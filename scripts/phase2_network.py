"""Phase 2 deliverable: baseline pressures and flows, plus leak and pump-failure demos.

Run from the repository root::

    python scripts/phase2_network.py

Writes everything to ``outputs/phase2/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import wntr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from irrigation_twin.network import (  # noqa: E402
    NETWORK_PARAMETERS,
    PLOT_JUNCTIONS,
    LeakSpec,
    build_farm_network,
    inject_leak,
    inject_pump_failure,
    run_hydraulics,
    set_irrigation_demands,
)
from irrigation_twin.network.build import REQUIRED_PRESSURE  # noqa: E402
from irrigation_twin.network.topology import plot_supply_table  # noqa: E402

OUT = ROOT / "outputs" / "phase2"

#: Placeholder request until the farm model (phase 3) supplies real ones:
#: 60 m^3/day per plot, i.e. 6 mm over a 1 ha plot.
DAILY_REQUEST_M3 = 60.0

LEAK = LeakSpec(pipe="P3", hole_diameter_m=0.03)


def hourly(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """Keep the on-the-hour rows and label them with the hour."""
    out = df.loc[df.index % 3600 == 0].copy()
    out.index = (out.index // 3600).astype(int)
    out.index.name = "hour"
    return out


def plot_network(wn: wntr.network.WaterNetworkModel, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    wntr.graphics.plot_network(wn, ax=ax, node_size=60, link_width=2, node_labels=False,
                               link_labels=True, title="Farm water network")
    kinds = {"R1": "R1 (sump)", "T1": "T1 (tank)", "J1": "J1 (pump outlet)"}
    for name, node in wn.nodes():
        x, y = node.coordinates
        offset = (-6, -18) if name == "R1" else (-6, 8)  # R1 sits 50 m from J1
        ax.annotate(kinds.get(name, name), (x, y), xytext=offset, textcoords="offset points", fontsize=9)
    for plot, junction in PLOT_JUNCTIONS.items():
        x, y = wn.get_node(junction).coordinates
        ax.annotate(plot.replace("_", " "), (x, y), xytext=(8, -16), textcoords="offset points",
                    color="tab:green", fontsize=9, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_scenarios(runs: dict, path: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(9, 9), sharex=True)
    for name, run in runs.items():
        t = run.pressure.index / 3600
        axes[0].plot(t, run.pressure["J4"], label=name)
        axes[1].plot(t, run.delivered[list(PLOT_JUNCTIONS.values())].sum(axis=1) * 1000, label=name)
        axes[2].plot(t, run.tank_level, label=name)
    axes[0].axhline(REQUIRED_PRESSURE, color="grey", ls="--", lw=1)
    axes[0].set_ylabel("Pressure at J4 / Plot B (m)")
    axes[1].set_ylabel("Water delivered to plots (L/s)")
    axes[2].set_ylabel("Tank level (m)")
    axes[2].set_xlabel("Hour of day")
    axes[0].legend(loc="lower left")
    for ax in axes:
        ax.grid(alpha=0.3)
    fig.suptitle("Baseline vs leak vs pump failure (irrigation 06:00-12:00)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def standard_network_check() -> pd.DataFrame:
    """Run EPANET's Net1 and Net3 with the EPANET engine and with WNTRSimulator; compare."""
    import os
    import tempfile

    rows = []
    folder = os.path.join(os.path.dirname(wntr.__file__), "library", "networks")
    with tempfile.TemporaryDirectory() as tmp:
        for net in ("Net1", "Net3"):
            path = os.path.join(folder, f"{net}.inp")
            e = wntr.sim.EpanetSimulator(wntr.network.WaterNetworkModel(path)).run_sim(
                file_prefix=os.path.join(tmp, net))
            wn = wntr.network.WaterNetworkModel(path)
            w = wntr.sim.WNTRSimulator(wn).run_sim()
            c = e.node["pressure"].index.intersection(w.node["pressure"].index)
            j = wn.junction_name_list
            dp = (e.node["pressure"].loc[c, j] - w.node["pressure"].loc[c, j]).abs()
            dq = (e.link["flowrate"].loc[c] - w.link["flowrate"].loc[c]).abs() * 1000
            rows.append({"network": net, "junctions": len(j), "links": wn.num_links,
                         "hours": int(c[-1] // 3600), "time_steps_compared": len(c),
                         "max_pressure_diff_m": round(float(dp.max().max()), 5),
                         "mean_pressure_diff_m": round(float(dp.mean().mean()), 6),
                         "max_flow_diff_Lps": round(float(dq.max().max()), 4)})
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    wn = build_farm_network()
    set_irrigation_demands(wn, {plot: DAILY_REQUEST_M3 for plot in PLOT_JUNCTIONS})
    wntr.network.write_inpfile(wn, str(OUT / "farm_network.inp"))

    runs = {
        "baseline": run_hydraulics(wn),
        f"leak {LEAK.pipe} {LEAK.hole_diameter_m * 100:.0f} cm": run_hydraulics(inject_leak(wn, LEAK)),
        "pump failure": run_hydraulics(inject_pump_failure(wn)),
    }
    base = runs["baseline"]

    # Baseline pressures and flows (the phase 2 deliverable).
    hourly(base.pressure).round(2).to_csv(OUT / "baseline_pressure_m.csv")
    hourly(base.flow * 1000).round(3).to_csv(OUT / "baseline_flow_Lps.csv")

    # Scenario comparison.
    rows = []
    for name, run in runs.items():
        s = run.summary()
        d = run.plot_delivery()
        rows.append({"scenario": name, **{k: round(v, 2) for k, v in s.items()},
                     **{f"{p}_delivered_pct": round(100 * f, 1) for p, f in d["delivered_fraction"].items()}})
    scenarios = pd.DataFrame(rows).set_index("scenario")
    scenarios.to_csv(OUT / "scenario_summary.csv")

    # Leak size sweep on the same pipe.
    sweep = []
    for hole_cm in [0, 1, 2, 3, 4, 5]:
        w = wn if hole_cm == 0 else inject_leak(wn, LeakSpec(LEAK.pipe, hole_cm / 100))
        r = run_hydraulics(w)
        s = r.summary()
        sweep.append({"hole_diameter_cm": hole_cm, "leak_m3": round(s["leak_m3"], 1),
                      "pumped_m3": round(s["pumped_m3"], 1), "delivered_m3": round(s["delivered_m3"], 1),
                      "min_plot_pressure_m": round(s["min_plot_pressure_m"], 2),
                      "plot_B_delivered_pct": round(100 * r.plot_delivery().loc["Plot_B", "delivered_fraction"], 1)})
    sweep_df = pd.DataFrame(sweep).set_index("hole_diameter_cm")
    sweep_df.to_csv(OUT / "leak_size_sweep.csv")

    pd.DataFrame([p.__dict__ for p in NETWORK_PARAMETERS]).to_csv(OUT / "network_parameters.csv", index=False)

    # NetworkX: which pipes each plot depends on
    routes = plot_supply_table(wn)
    routes.to_csv(OUT / "plot_supply_routes.csv")

    # Validation check 1 on standard EPANET example networks
    check = standard_network_check()
    check.to_csv(OUT / "standard_network_check.csv", index=False)

    plot_network(wn, OUT / "network.png")
    plot_scenarios(runs, OUT / "scenarios.png")

    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print("Scenario summary\n", scenarios.T, "\n")
        print(f"Leak size sweep on {LEAK.pipe}\n", sweep_df, "\n")
        print("Supply routes (NetworkX)\n", routes, "\n")
        print("Standard EPANET networks, WNTRSimulator vs EPANET engine\n", check.to_string(index=False), "\n")
        print("Baseline mass-balance residual (max, m^3/s):",
              f"{base.mass_balance()['residual'].abs().max():.2e}")
    print(f"Outputs written to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
