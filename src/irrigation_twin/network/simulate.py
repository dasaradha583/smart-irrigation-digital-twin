"""Run the hydraulic simulation and pull out the numbers the twin uses."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass

import pandas as pd
import wntr

from .build import PLOT_JUNCTIONS, PUMP, RESERVOIR, TANK


@dataclass
class HydraulicRun:
    """One day of hydraulic results, in SI units (m, m^3/s)."""

    wn: wntr.network.WaterNetworkModel
    results: wntr.sim.SimulationResults

    # ---- raw time series (index: seconds since start) ----
    @property
    def pressure(self) -> pd.DataFrame:
        return self.results.node["pressure"]

    @property
    def delivered(self) -> pd.DataFrame:
        """Flow actually delivered at each node (m^3/s), leaks excluded."""
        return self.results.node["demand"]

    @property
    def requested(self) -> pd.DataFrame:
        """Flow requested at each junction (m^3/s), before pressure limits."""
        return wntr.metrics.expected_demand(self.wn)

    @property
    def leak(self) -> pd.DataFrame:
        return self.results.node["leak_demand"]

    @property
    def flow(self) -> pd.DataFrame:
        return self.results.link["flowrate"]

    @property
    def tank_level(self) -> pd.Series:
        return self.results.node["pressure"][TANK["name"]]

    # ---- daily totals ----
    def _interval_volumes(self, rates: pd.DataFrame) -> pd.DataFrame:
        """Convert rates at each report time into volumes for the step that starts there.

        Result row t holds the state for [t, next report time). The last row,
        at the end of the run, starts no interval and is dropped. Step lengths
        come from the result index, so uneven reporting is handled too.
        """
        times = rates.index.to_series()
        steps = (times.shift(-1) - times).iloc[:-1]
        return rates.iloc[:-1].mul(steps.values, axis=0)

    def plot_delivery(self) -> pd.DataFrame:
        """Requested vs delivered daily volume (m^3) per farm plot."""
        junctions = list(PLOT_JUNCTIONS.values())
        req = self._interval_volumes(self.requested[junctions]).sum()
        got = self._interval_volumes(self.delivered[junctions]).sum()
        table = pd.DataFrame({
            "junction": junctions,
            "requested_m3": req.values,
            "delivered_m3": got.values,
        }, index=list(PLOT_JUNCTIONS.keys()))
        table["delivered_fraction"] = (table["delivered_m3"] / table["requested_m3"]).where(
            table["requested_m3"] > 0, 1.0)
        table.index.name = "plot"
        return table

    def mass_balance(self) -> pd.DataFrame:
        """Hourly water balance (m^3/s). ``residual`` should be ~0.

        Source supply = junction deliveries + leak flow + net flow into the tank.
        WNTR reports a reservoir's outflow as a negative demand and a tank's
        net inflow as a positive demand.
        """
        demand = self.delivered
        junctions = self.wn.junction_name_list
        supply = -demand[RESERVOIR["name"]]
        consumed = demand[junctions].sum(axis=1)
        leaked = self.leak[junctions].sum(axis=1)
        tank_in = demand[TANK["name"]]
        out = pd.DataFrame({
            "supply": supply,
            "consumed": consumed,
            "leaked": leaked,
            "tank_inflow": tank_in,
        })
        out["residual"] = out["supply"] - out["consumed"] - out["leaked"] - out["tank_inflow"]
        return out

    def tank_storage_error_m3(self) -> float:
        """Largest single-step gap between tank volume change and net inflow (m^3).

        Junction flows can balance perfectly while the tank level is wrong.
        WNTR integrates the tank explicitly, so in the step where the tank
        reaches a level limit, or a level control switches the pump, it can
        overshoot by up to (flow x time step). This returns the worst such gap.
        """
        tank = self.wn.get_node(TANK["name"])
        area = math.pi * (tank.diameter / 2.0) ** 2
        stored = self.tank_level.diff().iloc[1:].values * area
        inflow = self._interval_volumes(self.delivered[[TANK["name"]]])[TANK["name"]].values
        return float(abs(stored - inflow).max())

    def summary(self) -> dict[str, float]:
        """Headline numbers for the day."""
        delivery = self.plot_delivery()
        leak_vol = self._interval_volumes(self.leak[self.wn.junction_name_list]).sum().sum()
        pump_vol = self._interval_volumes(self.flow[[PUMP["name"]]]).sum().sum()
        plot_pressure = self.pressure[list(PLOT_JUNCTIONS.values())]
        return {
            "requested_m3": float(delivery["requested_m3"].sum()),
            "delivered_m3": float(delivery["delivered_m3"].sum()),
            "leak_m3": float(leak_vol),
            "pumped_m3": float(pump_vol),
            "min_plot_pressure_m": float(plot_pressure.min().min()),
            "final_tank_level_m": float(self.tank_level.iloc[-1]),
        }


def run_hydraulics(wn: wntr.network.WaterNetworkModel) -> HydraulicRun:
    """Simulate ``wn`` with WNTR's own solver.

    The twin uses WNTRSimulator, not EpanetSimulator, because only
    WNTRSimulator supports leaks together with pressure-driven demand.

    A simulation advances the model's internal clock and tank levels, so the
    run works on a reset copy. That way ``wn`` can be simulated again, or
    copied into other scenarios, without the next run starting at the end of
    the day.
    """
    wn = copy.deepcopy(wn)
    wn.reset_initial_values()
    sim = wntr.sim.WNTRSimulator(wn)
    results = sim.run_sim()
    return HydraulicRun(wn=wn, results=results)
