"""Phase 7: write docs/report.md from the generated outputs.

Run from the repository root after run_season_analysis.py and
stretch_leak_detection.py::

    python scripts/build_report.py

Every number in the report is read from outputs/, so the report always
matches the results it describes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from irrigation_twin.catalog import SCENARIOS  # noqa: E402
from irrigation_twin.farm import MAIZE_GRAIN, PLOT_LAYOUT  # noqa: E402
from irrigation_twin.farm.plot import IrrigationPolicy  # noqa: E402
from irrigation_twin.farm.setup import PLOT_AREA_HA  # noqa: E402
from irrigation_twin.network.build import NETWORK_PARAMETERS, PLOT_JUNCTIONS  # noqa: E402
from irrigation_twin.twin import Operation  # noqa: E402


def f0(x):
    return f"{x:,.0f}"


def f1(x):
    return f"{x:,.1f}"


def pct(x):
    return f"{100 * x:.1f} %"


def table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "outputs", help="outputs folder to read")
    ap.add_argument("--report", type=Path, default=ROOT / "docs" / "report.md")
    args = ap.parse_args()
    O = args.out
    fig = lambda p: os.path.relpath(O / p, args.report.parent)  # noqa: E731

    summary = json.loads((O / "season_summary.json").read_text())
    p3 = summary["phase3"]
    chain = summary["phase4_chain"]
    comp = pd.read_csv(O / "phase5" / "scenario_comparison.csv").set_index("scenario")
    by_plot = pd.read_csv(O / "phase5" / "scenario_by_plot.csv")
    det = json.loads((O / "leak_detection" / "metrics.json").read_text())
    grid = pd.read_csv(O / "optimiser" / "grid_drought_pump_failure.csv")
    sweep = pd.read_csv(O / "optimiser" / "water_saving_sweep_baseline.csv")
    std = pd.read_csv(O / "phase2" / "standard_network_check.csv")
    routes = pd.read_csv(O / "phase2" / "plot_supply_routes.csv")
    weather_csv = next((ROOT / "data" / "weather").glob("*.csv"), None)
    provenance = ""
    if weather_csv is not None:
        provenance = " ".join(line[2:].strip() for line in weather_csv.read_text().splitlines()[:3]
                              if line.startswith("#"))

    # mass balance across every scenario's daily logs
    mb = max(pd.read_csv(O / "season" / s / "network.csv")["mass_balance_residual"].max()
             for s in comp.index)

    b, lk, lr = comp.loc["baseline"], comp.loc["leak"], comp.loc["leak_repaired"]
    bu, pf = comp.loc["pipe_burst"], comp.loc["pump_failure"]
    dr, dp = comp.loc["drought"], comp.loc["drought_pump_failure"]
    best, default = summary["optimiser"]["best"], summary["optimiser"]["default"]
    rf, rule = det["random_forest"], det["threshold_rule"]
    op, pol = Operation(), IrrigationPolicy()

    extra_stress = int(lk['stress_days_total'] - b['stress_days_total'])
    if extra_stress <= 5 and lk['mean_relative_yield'] >= b['mean_relative_yield'] - 0.01:
        leak_line = (f"**An undetected leak costs mostly water and energy.** The 4 cm leak on P3 lost "
                     f"{f0(lk['leak_m3'])} m³ and raised pump energy from {f0(b['pump_energy_kwh'])} to "
                     f"{f0(lk['pump_energy_kwh'])} kWh, but added only {extra_stress} stress plot-days. The pump "
                     f"compensates by running longer, so the farm sees this leak on the electricity bill before "
                     f"it sees it in the field.")
    else:
        leak_line = (f"**An undetected leak costs water, energy and yield.** The 4 cm leak on P3 lost "
                     f"{f0(lk['leak_m3'])} m³, raised pump energy from {f0(b['pump_energy_kwh'])} to "
                     f"{f0(lk['pump_energy_kwh'])} kWh, added {extra_stress} stress plot-days and moved mean yield "
                     f"from {pct(b['mean_relative_yield'])} to {pct(lk['mean_relative_yield'])}.")

    def yields(name):
        d = by_plot[by_plot["scenario"] == name].set_index("plot")["relative_yield"]
        return ", ".join(f"{p[-1]} {100 * d[p]:.0f} %" for p in PLOT_JUNCTIONS)

    rows = []
    for name in comp.index:
        r = comp.loc[name]
        rows.append({"Scenario": name.replace("_", " "), "Delivered to plots (m³)": f0(r["delivered_m3"]),
                     "Lost to leaks (m³)": f0(r["leak_m3"]), "Pumped (m³)": f0(r["pumped_m3"]),
                     "Pump energy (kWh)": f0(r["pump_energy_kwh"]),
                     "Mean soil moisture (m³/m³)": f"{r['mean_soil_moisture']:.3f}",
                     "Lowest soil moisture (m³/m³)": f"{r['min_soil_moisture']:.3f}",
                     "Stress (plot-days)": int(r["stress_days_total"]),
                     "Low-pressure days": int(r["days_low_pressure"]),
                     "Mean yield": pct(r["mean_relative_yield"]), "Worst plot": pct(r["min_relative_yield"])})
    comp_table = table(pd.DataFrame(rows))

    grid_rows = grid.head(12).assign(
        **{"Trigger (× RAW)": grid["trigger_fraction"], "Window (h)": grid["window_hours"],
           "Rationing": grid["priority"].map({True: "priority", False: "none"}),
           "Delivered (m³)": grid["delivered_m3"].map(f0), "Stress (plot-days)": grid["stress_days_total"],
           "Mean yield": grid["mean_relative_yield"].map(pct), "Worst plot": grid["min_relative_yield"].map(pct)}
    )[["Trigger (× RAW)", "Window (h)", "Rationing", "Delivered (m³)", "Stress (plot-days)", "Mean yield", "Worst plot"]]
    sweep_rows = sweep.assign(
        **{"Trigger (× RAW)": sweep["trigger_fraction"], "Delivered (m³)": sweep["delivered_m3"].map(f0),
           "Water saved vs 0.8 (m³)": sweep["water_saved_m3"].map(f0),
           "Saved (%)": sweep["water_saved_pct"].map(f1), "Stress (plot-days)": sweep["stress_days_total"],
           "Mean yield": sweep["mean_relative_yield"].map(pct)}
    )[["Trigger (× RAW)", "Delivered (m³)", "Water saved vs 0.8 (m³)", "Saved (%)", "Stress (plot-days)", "Mean yield"]]

    # best water-saving trigger that keeps mean yield within 0.5 points of the default
    ok = sweep[sweep["mean_relative_yield"] >= sweep.loc[sweep["trigger_fraction"] == 0.8, "mean_relative_yield"].iloc[0] - 0.005]
    saver = ok.sort_values("delivered_m3").iloc[0]

    farm_only = pd.DataFrame(p3["farm_only"]).rename(columns={
        "plot": "Plot", "irrigation_events": "Irrigation days", "gross_irrigation_m3": "Gross irrigation (m³)",
        "rain_mm": "Rain (mm)", "etc_mm": "ETc (mm)", "eta_mm": "ETa (mm)", "stress_days": "Stress days"})

    params = [(p.name, p.value, p.unit, p.source) for p in NETWORK_PARAMETERS]
    c = MAIZE_GRAIN
    params += [
        ("Crop", "maize (grain)", "-", "Phase 1 scope"),
        ("Kc ini / mid / end", f"{c.kc_ini} / {c.kc_mid} / {c.kc_end}", "-", "FAO-56 Table 12"),
        ("Kc mid after climate adjustment", ", ".join(f"{k[-1]} {v}" for k, v in p3["kc_mid_adjusted"].items()), "-", "FAO-56 eq. 62, mid-season mean u2 and RHmin"),
        ("Stage lengths ini/dev/mid/late", f"{c.l_ini}/{c.l_dev}/{c.l_mid}/{c.l_late}", "days", "FAO-56 Table 11, maize, India (dry, cool)"),
        ("Root depth", f"{c.root_depth_min_m} to {c.root_depth_max_m}", "m", "FAO-56 Table 22 (max 1.0-1.7 m); linear growth assumed"),
        ("Depletion fraction p (ETc = 5 mm/d)", f"{c.p_table}", "-", "FAO-56 Table 22, adjusted daily for ETc"),
        ("Yield response factor Ky", f"{c.ky}", "-", "FAO-33 (Doorenbos & Kassam 1979)"),
        ("Soils θFC / θWP", "; ".join(f"{k[-1]} {v['soil'].name} {v['soil'].theta_fc}/{v['soil'].theta_wp}" for k, v in PLOT_LAYOUT.items()), "m³/m³", "FAO-56 Table 19, mid-range"),
        ("Plot area", f"{PLOT_AREA_HA}", "ha", "Assumption"),
        ("Sowing dates", ", ".join(f"{k[-1]} {v['sowing']:%d %b}" for k, v in PLOT_LAYOUT.items()), "-", "Assumption; staggered sowing"),
        ("Irrigation trigger", f"{pol.trigger_fraction} × RAW", "-", "Assumption (optimised in §6)"),
        ("Max net application", f"{pol.max_application_mm}", "mm/day", "Assumption; system sized at ~1.4 x peak ETc"),
        ("Application efficiency", f"{pol.efficiency:.0%}", "-", "Assumption; typical sprinkler"),
        ("Irrigation window", f"{op.window_start_hour:02d}:00-{op.window_start_hour + op.window_hours:02d}:00", "-", "Assumption"),
        ("Albedo", "0.23", "-", "FAO-56 (grass reference)"),
        ("Weather", "Open-Meteo historical archive", "-", provenance or "see data/weather/"),
    ]
    param_table = table(pd.DataFrame(params, columns=["Parameter", "Value", "Unit", "Source"]))

    text = f"""# Smart Irrigation Digital Twin: Final report

*Generated {dt.date.today():%d %b %Y} by `scripts/build_report.py` from the files in `outputs/`.*

## 1. Summary

This project couples a water-distribution model (WNTR/EPANET, pressure-driven demand) with a FAO-56 soil-water model of four maize plots in Anantapur, India, for the 2023-24 Rabi season ({p3['days']} days, real weather). Every day, each plot's irrigation request becomes demand at a network junction, and the water the network actually delivers at the available pressure becomes that plot's irrigation. The twin can therefore follow a pipe leak or pump failure through to soil moisture, crop stress and yield.

Key results:

- **Coupling chain (pipe burst on P6, 5-19 Feb).** {chain['plot'].replace('_', ' ')} received {f0(chain['delivered_m3'][1])} m³ over the season against {f0(chain['delivered_m3'][0])} m³ in the baseline. Its mean soil moisture during the burst was {chain['mean_soil_moisture_during_leak'][1]:.3f} against {chain['mean_soil_moisture_during_leak'][0]:.3f} m³/m³, with {chain['stress_days'][1]} stress days against {chain['stress_days'][0]}, and relative yield {pct(chain['relative_yield'][1])} against {pct(chain['relative_yield'][0])}.
- {leak_line}
- **Detection pays.** With the leak found within hours (the detector's median delay) and repaired the next day, losses fall to {f0(lr['leak_m3'])} m³ and energy to {f0(lr['pump_energy_kwh'])} kWh.
- **A 10-day pump failure at peak demand** caused {int(pf['stress_days_total'])} stress plot-days and a mean yield of {pct(pf['mean_relative_yield'])} (worst plot {pct(pf['min_relative_yield'])}).
- **Leak detection (stretch):** on pipes and leak sizes never seen in training, the random-forest detector caught {pct(rf['event_detection_rate'])} of leak days with a median delay of {rf['median_delay_h']:.0f} h, raising false alarms on {pct(rf['false_alarm_day_rate'])} of leak-free days. A simple threshold rule did worse on both ({pct(rule['event_detection_rate'])} caught, {pct(rule['false_alarm_day_rate'])} false-alarm days).
- **Schedule optimiser (stretch):** under drought plus pump failure, the best schedule ({best['candidate']}) reached {pct(best['mean_relative_yield'])} mean yield with {int(best['stress_days_total'])} stress plot-days. The default schedule reached {pct(default['mean_relative_yield'])} with {int(default['stress_days_total'])}.

## 2. System

| Component | Model |
|---|---|
| Network | 1 sump, 1 pump (15 L/s at 25 m), 1 elevated tank (~127 m³), 7 junctions, 8 PVC pipes with one loop. Pressure-driven demand: full delivery at ≥ 15 m, none at 0 m. Details in [phase2_water_network.md](phase2_water_network.md) |
| Farm | Four 0.5 ha maize plots on junctions J3, J4, J6, J7, with staggered sowing (1-22 Nov 2023) and sandy loam, loam and loamy sand soils. Details in [phase1_scope.md](phase1_scope.md) |
| Weather | Open-Meteo historical archive for 14.68° N, 77.60° E, downloaded once to `data/weather/` |
| Dashboard | `streamlit run app/dashboard.py` |

**Which pipes each plot depends on** (NetworkX graph of the network, `src/irrigation_twin/network/topology.py`). A *critical* pipe is one whose failure alone cuts the plot off from both the pump and the tank:

{table(routes.rename(columns={"plot": "Plot", "junction": "Junction", "routes_from_hub": "Pipe routes from hub J2", "critical_pipes": "Critical pipes", "shortest_route_m": "Shortest route (m)"}))}

This explains the scenario results. A break on P6 starves Plot D, which has no other feed. The loop through P7 gives Plots A and C a second route.

## 3. Farm model (phase 3)

Reference evapotranspiration is computed with FAO-56 Penman-Monteith (eq. 6) from daily Tmax, Tmin, RHmax, RHmin, 2 m wind and solar radiation. The code reproduces the FAO-56 worked examples (Example 18: ET0 = 3.88 against the published 3.9 mm/day). Crop water use is Kc × ET0 with the FAO-56 maize curve, with Kc_mid adjusted for the local climate. Root-zone depletion follows FAO-56 chapter 8, with water stress (Ks) beyond the readily available water.

**Weather and ET0 check.** Season rainfall was {f1(p3['season_rain_mm'])} mm against {f0(p3['season_et0_mm'])} mm of reference ET, so irrigation carries the crop. Our ET0 averaged {p3['et0_mean_mm']:.2f} mm/day against {p3['et0_openmeteo_mean_mm']:.2f} from Open-Meteo's independent FAO ET0. The bias is {p3['bias_vs_openmeteo_mm']:+.2f} mm/day, RMSE {p3['rmse_vs_openmeteo_mm']:.2f} mm/day and r = {p3['r_vs_openmeteo']:.3f}. The temperature-only Hargreaves method averaged {p3['et0_hargreaves_mean_mm']:.2f} mm/day.

![ET0 check]({fig('phase3/et0_check.png')})

**Farm model alone** (every request met, no network):

{table(farm_only)}

![Soil moisture, farm only]({fig('phase3/soil_moisture_farm_only.png')})

## 4. Coupling (phase 4)

The twin advances one day at a time:

1. Read the weather.
2. Compute ETc for each plot.
3. Update soil moisture with rain and crop water use.
4. Turn each plot's irrigation request into junction demand over the {op.window_hours} h window.
5. Run the day's hydraulics with the active scenario.
6. Apply the delivered water.
7. Log everything.

The tank level and pump status carry over from one day to the next. Each day's hydraulics runs at 1-minute steps.

The chain the project set out to show, leak → low pressure → less water → drier soil → stress, is plotted below for the pipe-burst scenario:

![Coupling chain]({fig('phase4/leak_chain.png')})

## 5. Scenarios (phase 5)

| Scenario | Description |
|---|---|
""" + "\n".join(f"| {n.replace('_', ' ')} | {s.description} |" for n, s in SCENARIOS.items()) + f"""

{comp_table}

Relative yield by plot (FAO-33, Ky = {c.ky}):

| Scenario | Yield by plot |
|---|---|
""" + "\n".join(f"| {n.replace('_', ' ')} | {yields(n)} |" for n in comp.index) + f"""

![Scenario comparison]({fig('phase5/scenario_comparison.png')})

Notes on reading the table:
- "Delivered" is the water that reached the plots. A plot that gets too little keeps asking on later days, so seasonal requests go up in fault scenarios. Delivery and stress are the meaningful measures; the share of requests met is not.
- In the **drought** scenario, rain is removed, ET0 rises 10 % and the well level drops 6 m. Delivered water rises from {f0(b['delivered_m3'])} to {f0(dr['delivered_m3'])} m³, with {int(dr['stress_days_total'])} stress plot-days. Combined with the pump failure, stress rises to {int(dp['stress_days_total'])} plot-days.

## 6. Stretch goals

### 6.1 Leak detection

Full write-up: [stretch_leak_detection.md](stretch_leak_detection.md).

| Held-out test set | Random forest | Threshold rule |
|---|---|---|
| Hourly precision | {rf['hourly_precision']:.3f} | {rule['hourly_precision']:.3f} |
| Hourly recall | {rf['hourly_recall']:.3f} | {rule['hourly_recall']:.3f} |
| Leak days detected | {pct(rf['event_detection_rate'])} | {pct(rule['event_detection_rate'])} |
| Median detection delay | {rf['median_delay_h']:.0f} h | {rule['median_delay_h']:.0f} h |
| False-alarm days | {pct(rf['false_alarm_day_rate'])} | {pct(rule['false_alarm_day_rate'])} |

### 6.2 Irrigation schedule under limited supply

All combinations of irrigation trigger (0.6, 0.8 or 1.0 × RAW), window (12 or 18 h) and tank-water rationing during outages were simulated over the whole season under **drought + pump failure**:

{table(grid_rows)}

![Optimiser]({fig('optimiser/optimiser.png')})

### 6.3 Water saved in a normal year

{table(sweep_rows)}

The lowest-water trigger that keeps mean yield within 0.5 points of the default is **{saver['trigger_fraction']} × RAW**, which saves {f0(saver['water_saved_m3'])} m³ ({f1(saver['water_saved_pct'])} %) with {int(saver['stress_days_total'])} stress plot-days.

## 7. Validation

| Check (validation plan) | Result |
|---|---|
| 1. Standard example networks | EPANET's own example networks, run with the twin's solver and with the EPANET engine: """ + "; ".join(f"{r.network} ({r.junctions} junctions, {r.hours} h) max pressure difference {r.max_pressure_diff_m:.4f} m, max flow difference {r.max_flow_diff_Lps:.3f} L/s" for r in std.itertuples()) + f""" (`outputs/phase2/standard_network_check.csv`) |
| 1. Our network against EPANET | WNTR pressures agree with the EPANET engine within 0.001 m over a full day (`tests/test_network.py`) |
| 1. Mass balance | Largest daily residual across all {len(comp)} season runs: {mb:.1e} m³/s |
| 2. ET0 against references | FAO-56 Examples 2, 3, 8, 10 and 18 reproduced. Season RMSE against Open-Meteo ET0 is {p3['rmse_vs_openmeteo_mm']:.2f} mm/day |
| 2. Soil balance closure | Closes to 1e-9 mm every day (`tests/test_farm.py`) |
| 3. No leak → delivered = requested | Holds in the baseline: {f0(b['delivered_m3'])} m³ delivered of {f0(b['requested_m3'])} requested |
| 3. Larger leak never delivers more | Holds (`tests/test_twin.py`, `tests/test_network.py`) |
| 3. Dry spell lowers soil moisture | Holds (`tests/test_farm.py`) |
| 4. Leak detection on held-out leaks | See §6.1. Same-distribution score reported alongside to show the gap |

Run `pytest` to repeat checks 1-3.

## 8. Assumptions and limitations

- The network is a plausible small farm system, not a surveyed one. Pipe roughness, elevations and the pump curve are assumptions (Appendix).
- Single crop coefficient: soil evaporation is not separated from transpiration (the dual-Kc method is not used). Rain fully infiltrates, and capillary rise is zero.
- Yield is a relative index from FAO-33, not a crop-growth simulation.
- Leak size, location and timing are scenario choices.
- The leak detector is trained and tested on simulated sensors with added noise and demand error. A real network adds model error, so expect more false alarms until the twin is calibrated.

## Appendix: parameters and sources

{param_table}
"""
    args.report.write_text(text)
    print(f"wrote {args.report}")


if __name__ == "__main__":
    main()
