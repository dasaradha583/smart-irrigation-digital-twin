"""The coupled digital twin (phase 4): one shared daily loop over both models.

Each simulated day runs the workflow's seven steps in a fixed order:

1. Read the day's weather.
2. Compute crop water use for each plot (ET0 x Kc).
3. Update soil moisture with rain and crop water use.
4. Decide irrigation. Each plot's request becomes demand at its junction.
5. Run the hydraulic simulation for the day with the active scenario.
   Demand is pressure-driven, so low pressure delivers less than requested.
6. Apply the water delivered at each junction to its plot.
7. Log everything.

Network state carries over between days: the tank's level and the pump's
on/off status at midnight become the next day's starting state.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
import wntr
from wntr.network import LinkStatus

from .farm.plot import Plot
from .network.build import (
    PLOT_JUNCTIONS,
    PUMP,
    REQUIRED_PRESSURE,
    RESERVOIR,
    TANK,
    build_farm_network,
)
from .network.demand import set_irrigation_demands
from .network.scenarios import LeakSpec, inject_leak, inject_pump_failure
from .network.simulate import HydraulicRun, run_hydraulics

DAY_S = 24 * 3600


# ---------------------------------------------------------------------------
# Scenario definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LeakEvent:
    """A leak that opens at ``start`` + ``start_hour`` and stays open until ``end`` (inclusive) or season end."""

    pipe: str
    hole_diameter_m: float
    start: dt.date
    end: dt.date | None = None
    start_hour: int = 0
    position: float = 0.5

    def window_on(self, day: dt.date) -> tuple[int, int | None] | None:
        """Active seconds within ``day``, as (start_s, end_s or None for "rest of day"), or None."""
        if day < self.start or (self.end is not None and day > self.end):
            return None
        start_s = self.start_hour * 3600 if day == self.start else 0
        return start_s, None


@dataclass(frozen=True)
class PumpOutage:
    """The pump is out of service from ``start`` + ``start_hour`` until the end of ``end`` (inclusive)."""

    start: dt.date
    end: dt.date
    start_hour: int = 0

    def window_on(self, day: dt.date) -> tuple[int, int | None] | None:
        if day < self.start or day > self.end:
            return None
        start_s = self.start_hour * 3600 if day == self.start else 0
        return start_s, None


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str = ""
    leaks: tuple[LeakEvent, ...] = ()
    outages: tuple[PumpOutage, ...] = ()
    rain_factor: float = 1.0       # drought: scale rainfall
    et0_factor: float = 1.0        # drought: scale evaporative demand
    sump_drop_m: float = 0.0       # drought: lower water level in the well/sump


@dataclass(frozen=True)
class Operation:
    """How the farm runs its network each day."""

    window_start_hour: int = 6
    window_hours: int = 12


#: A scheduler can rewrite the day's requests before they reach the network,
#: for example to prioritise plots when supply is short.
#: Signature: (date, requests_m3 by plot, plots, previous day's network log) -> requests_m3.
Scheduler = Callable[[dt.date, dict[str, float], Sequence[Plot], dict | None], dict[str, float]]


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class SeasonResult:
    scenario: Scenario
    plots: pd.DataFrame              # one row per plot per day
    network: pd.DataFrame            # one row per day
    pressure: pd.DataFrame           # hourly pressure at every node (m)
    flow: pd.DataFrame               # hourly flow in every link (L/s)
    alerts: pd.DataFrame             # one row per alert
    meta: dict = field(default_factory=dict)

    def plot_summary(self, ky: float | None = None) -> pd.DataFrame:
        """Seasonal totals per plot, including relative yield (FAO-33)."""
        df = self.plots[self.plots["kc"] > 0]  # in-season days only
        g = df.groupby("plot")
        out = pd.DataFrame({
            "requested_m3": g["request_m3"].sum(),
            "delivered_m3": g["delivered_m3"].sum(),
            "irrigation_net_mm": g["irrigation_net_mm"].sum(),
            "rain_mm": g["rain_mm"].sum(),
            "etc_mm": g["etc_mm"].sum(),
            "eta_mm": g["eta_mm"].sum(),
            "deep_percolation_mm": g["deep_percolation_mm"].sum(),
            "stress_days": g["stressed"].sum(),
            "min_ks": g["ks"].min(),
        })
        ky = ky if ky is not None else self.meta.get("ky", 1.25)
        out["relative_yield"] = np.clip(1.0 - ky * (1.0 - out["eta_mm"] / out["etc_mm"]), 0.0, 1.0)
        out["delivered_fraction"] = np.where(out["requested_m3"] > 0, out["delivered_m3"] / out["requested_m3"], 1.0)
        return out

    def summary(self) -> dict[str, float]:
        ps = self.plot_summary()
        net = self.network
        return {
            "requested_m3": float(ps["requested_m3"].sum()),
            "delivered_m3": float(ps["delivered_m3"].sum()),
            "delivered_pct": float(100 * ps["delivered_m3"].sum() / max(ps["requested_m3"].sum(), 1e-9)),
            "pumped_m3": float(net["pumped_m3"].sum()),
            "leak_m3": float(net["leak_m3"].sum()),
            "pump_energy_kwh": float(net["pump_energy_kwh"].sum()),
            "stress_days_total": int(ps["stress_days"].sum()),
            "mean_relative_yield": float(ps["relative_yield"].mean()),
            "min_relative_yield": float(ps["relative_yield"].min()),
            "days_low_pressure": int((net["min_plot_pressure_irrigating_m"] < REQUIRED_PRESSURE - 1e-6).sum()),
        }


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def _day_network(scenario: Scenario, day: dt.date, tank_level: float, pump_open: bool,
                 requests: dict[str, float], op: Operation) -> tuple[wntr.network.WaterNetworkModel, list[str]]:
    wn = build_farm_network()
    tank = wn.get_node(TANK["name"])
    tank.init_level = float(np.clip(tank_level, tank.min_level, tank.max_level))
    wn.get_link(PUMP["name"]).initial_status = LinkStatus.Open if pump_open else LinkStatus.Closed
    if scenario.sump_drop_m:
        wn.get_node(RESERVOIR["name"]).base_head = RESERVOIR["base_head"] - scenario.sump_drop_m
    set_irrigation_demands(wn, {PLOT_JUNCTIONS[p]: v for p, v in requests.items()},
                           op.window_start_hour, op.window_hours)
    leak_nodes = []
    for leak in scenario.leaks:
        win = leak.window_on(day)
        if win is None:
            continue
        spec = LeakSpec(leak.pipe, leak.hole_diameter_m, start_time_s=win[0], end_time_s=win[1],
                        position=leak.position)
        wn = inject_leak(wn, spec)
        leak_nodes.append(spec.junction_name)
    for outage in scenario.outages:
        win = outage.window_on(day)
        if win is not None:
            wn = inject_pump_failure(wn, PUMP["name"], start_time_s=win[0], end_time_s=win[1])
    return wn, leak_nodes


def _pump_energy_kwh(run: HydraulicRun) -> float:
    head = run.results.node["head"]
    flow = run.flow
    power_w = wntr.metrics.pump_power(flow, head, run.wn)[PUMP["name"]]
    return float(run._interval_volumes(power_w.to_frame()).sum().iloc[0] / 3.6e6)


def run_season(plots: Sequence[Plot], weather: pd.DataFrame, scenario: Scenario,
               start: dt.date, end: dt.date, operation: Operation | None = None,
               scheduler: Scheduler | None = None,
               progress: Callable[[dt.date], None] | None = None) -> SeasonResult:
    """Run the coupled twin from ``start`` to ``end`` (inclusive).

    ``plots`` are modified in place (their soil state advances). ``weather``
    needs columns ``date``, ``et0_mm`` and ``rain_mm``.
    """
    op = operation or Operation()
    wx = weather.set_index("date")
    missing = [d for d in pd.date_range(start, end).date if d not in wx.index]
    if missing:
        raise ValueError(f"weather is missing {len(missing)} days, first {missing[0]}")

    tank_level = build_farm_network().get_node(TANK["name"]).init_level
    pump_open = True
    plot_rows: list[dict] = []
    net_rows: list[dict] = []
    alerts: list[dict] = []
    pressures, flows = [], []
    prev_log: dict | None = None

    for day in pd.date_range(start, end).date:
        if progress:
            progress(day)
        # 1. weather (with the scenario's drought factors)
        et0 = float(wx.at[day, "et0_mm"]) * scenario.et0_factor
        rain = float(wx.at[day, "rain_mm"]) * scenario.rain_factor

        # 2-4. crop water use, soil balance, irrigation decision
        requests = {p.name: p.start_day(day, et0, rain) for p in plots}
        if scheduler is not None:
            requests = scheduler(day, dict(requests), plots, prev_log)

        # 5. hydraulics with the active scenario
        wn, leak_nodes = _day_network(scenario, day, tank_level, pump_open, requests, op)
        run = run_hydraulics(wn)
        delivery = run.plot_delivery()

        # 6. apply delivered water
        for p in plots:
            rec = p.finish_day(float(delivery.loc[p.name, "delivered_m3"]))
            row = asdict(rec)
            row["request_m3"] = requests[p.name]  # after any scheduler change
            plot_rows.append(row)

        # 7. log
        s = run.summary()
        stamp = pd.Timestamp(day)
        hourly_index = run.pressure.index % 3600 == 0
        p_hour = run.pressure.loc[hourly_index].iloc[:-1]
        p_hour.index = stamp + pd.to_timedelta(p_hour.index, unit="s")
        f_hour = run.flow.loc[hourly_index].iloc[:-1] * 1000.0
        f_hour.index = p_hour.index
        pressures.append(p_hour[[c for c in p_hour.columns if c not in leak_nodes]])
        flows.append(f_hour[[c for c in f_hour.columns if not c.endswith("_B")]])

        window = (run.pressure.index >= op.window_start_hour * 3600) & \
                 (run.pressure.index < (op.window_start_hour + op.window_hours) * 3600)
        plot_p = run.pressure.loc[window, list(PLOT_JUNCTIONS.values())]
        irrigating = sum(requests.values()) > 0
        log = {
            "date": day,
            "requested_m3": s["requested_m3"],
            "delivered_m3": s["delivered_m3"],
            "pumped_m3": s["pumped_m3"],
            "leak_m3": s["leak_m3"],
            "pump_energy_kwh": _pump_energy_kwh(run),
            "pump_hours": float((run.flow[PUMP["name"]].iloc[:-1] > 1e-6).sum() * wn.options.time.hydraulic_timestep / 3600),
            "tank_start_m": float(run.tank_level.iloc[0]),
            "tank_end_m": float(run.tank_level.iloc[-1]),
            "min_plot_pressure_irrigating_m": float(plot_p.min().min()) if irrigating else np.nan,
            "leak_active": bool(leak_nodes),
            "pump_outage": any(o.window_on(day) is not None for o in scenario.outages),
            "mass_balance_residual": float(run.mass_balance()["residual"].abs().max()),
        }
        net_rows.append(log)
        prev_log = log

        # alerts from what an operator could actually observe
        if irrigating and log["min_plot_pressure_irrigating_m"] < REQUIRED_PRESSURE - 1e-6:
            alerts.append({"date": day, "kind": "low pressure",
                           "detail": f"min plot pressure {log['min_plot_pressure_irrigating_m']:.1f} m"})
        for p in plots:
            req = requests[p.name]
            got = float(delivery.loc[p.name, "delivered_m3"])
            if req > 0 and got < 0.95 * req:
                alerts.append({"date": day, "kind": "delivery shortfall",
                               "detail": f"{p.name}: {got:.0f} of {req:.0f} m3"})
        for row in plot_rows[-len(plots):]:
            if row["stressed"]:
                alerts.append({"date": day, "kind": "crop stress",
                               "detail": f"{row['plot']}: Ks = {row['ks']:.2f}"})
        if log["tank_end_m"] <= TANK["min_level"] + 1e-3:
            alerts.append({"date": day, "kind": "tank empty", "detail": "tank at minimum level"})

        # carry network state to the next day
        tank_level = log["tank_end_m"]
        pump_open = bool(run.results.link["status"][PUMP["name"]].iloc[-1] == 1)

    return SeasonResult(
        scenario=scenario,
        plots=pd.DataFrame(plot_rows),
        network=pd.DataFrame(net_rows),
        pressure=pd.concat(pressures),
        flow=pd.concat(flows),
        alerts=pd.DataFrame(alerts, columns=["date", "kind", "detail"]),
        meta={"ky": plots[0].crop.ky, "operation": asdict(op), "start": start, "end": end},
    )
