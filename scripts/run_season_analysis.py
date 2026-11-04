"""Phases 3-5 and the optimiser stretch goal, from the cached weather CSV.

Run from the repository root (after scripts/fetch_weather.py)::

    python scripts/run_season_analysis.py

Outputs:
    outputs/phase3/      ET0 check and farm-only soil-moisture curves
    outputs/phase4/      the coupling chain: leak -> pressure -> delivery -> soil
    outputs/season/<s>/  full daily logs for each standard scenario (read by the dashboard)
    outputs/phase5/      scenario comparison tables and charts
    outputs/optimiser/   schedule grid search under limited supply, water-saving sweep
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from irrigation_twin.catalog import SCENARIOS, save_result  # noqa: E402
from irrigation_twin.farm import SEASON_END, SEASON_START, build_plots  # noqa: E402
from irrigation_twin.farm.weather import ANANTAPUR, add_et0, load_weather, weather_csv_path  # noqa: E402
from irrigation_twin.network.build import REQUIRED_PRESSURE  # noqa: E402
from irrigation_twin.optimise import Candidate, _evaluate  # noqa: E402
from irrigation_twin.twin import run_season  # noqa: E402

WEATHER_START, WEATHER_END = dt.date(2023, 10, 1), dt.date(2024, 4, 30)
OUT = ROOT / "outputs"
WEATHER_OVERRIDE: Path | None = None
PLOT_COLORS = {"Plot_A": "tab:blue", "Plot_B": "tab:orange", "Plot_C": "tab:green", "Plot_D": "tab:red"}


def load() -> pd.DataFrame:
    path = WEATHER_OVERRIDE or weather_csv_path(ROOT, ANANTAPUR, WEATHER_START, WEATHER_END)
    if not path.exists():
        raise SystemExit(f"{path} not found. Run scripts/fetch_weather.py first "
                         "where archive-api.open-meteo.com is reachable.")
    weather, elevation = load_weather(path)
    return add_et0(weather, ANANTAPUR, elevation)


# ---------------------------------------------------------------------------
# Phase 3: weather, ET0 check, farm-only run
# ---------------------------------------------------------------------------

def phase3(weather: pd.DataFrame) -> dict:
    out = OUT / "phase3"
    out.mkdir(parents=True, exist_ok=True)
    season = weather[(weather["date"] >= SEASON_START) & (weather["date"] <= SEASON_END)]

    diff = season["et0_mm"] - season["et0_openmeteo_mm"]
    check = {
        "days": int(len(season)),
        "et0_mean_mm": float(season["et0_mm"].mean()),
        "et0_openmeteo_mean_mm": float(season["et0_openmeteo_mm"].mean()),
        "et0_hargreaves_mean_mm": float(season["et0_hargreaves_mm"].mean()),
        "bias_vs_openmeteo_mm": float(diff.mean()),
        "rmse_vs_openmeteo_mm": float(np.sqrt((diff ** 2).mean())),
        "r_vs_openmeteo": float(np.corrcoef(season["et0_mm"], season["et0_openmeteo_mm"])[0, 1]),
        "season_rain_mm": float(season["rain_mm"].sum()),
        "season_et0_mm": float(season["et0_mm"].sum()),
    }
    (out / "et0_check.json").write_text(json.dumps(check, indent=2))

    fig, ax = plt.subplots(1, 2, figsize=(11, 4), gridspec_kw={"width_ratios": [2, 1]})
    d = pd.to_datetime(season["date"])
    ax[0].plot(d, season["et0_mm"], label="Ours (FAO-56 Penman-Monteith)", lw=1.5)
    ax[0].plot(d, season["et0_openmeteo_mm"], label="Open-Meteo FAO ET0", lw=1, ls="--")
    ax[0].plot(d, season["et0_hargreaves_mm"], label="Hargreaves (temperature only)", lw=1, alpha=0.7)
    ax[0].set_ylabel("ET0 (mm/day)")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3)
    ax[1].scatter(season["et0_openmeteo_mm"], season["et0_mm"], s=8)
    lim = [season[["et0_mm", "et0_openmeteo_mm"]].min().min() - 0.2, season[["et0_mm", "et0_openmeteo_mm"]].max().max() + 0.2]
    ax[1].plot(lim, lim, color="grey", lw=1)
    ax[1].set_xlabel("Open-Meteo ET0 (mm/day)")
    ax[1].set_ylabel("Our ET0 (mm/day)")
    ax[1].set_title(f"RMSE {check['rmse_vs_openmeteo_mm']:.2f} mm, r = {check['r_vs_openmeteo']:.3f}", fontsize=9)
    ax[1].grid(alpha=0.3)
    fig.suptitle("Reference evapotranspiration, Anantapur, Rabi 2023-24")
    fig.tight_layout()
    fig.savefig(out / "et0_check.png", dpi=150)
    plt.close(fig)

    # Farm only: every request delivered in full, no network involved.
    plots = build_plots(weather)
    rows = []
    wx = weather.set_index("date")
    for day in pd.date_range(SEASON_START, SEASON_END).date:
        for p in plots:
            req = p.start_day(day, float(wx.at[day, "et0_mm"]), float(wx.at[day, "rain_mm"]))
            rows.append(p.finish_day(req).__dict__)
    farm = pd.DataFrame(rows)
    farm.to_csv(out / "farm_only_daily.csv", index=False, float_format="%.5g")
    plot_soil_moisture(farm, plots, out / "soil_moisture_farm_only.png",
                       "Farm model alone: soil moisture with every irrigation request met")
    summary = farm[farm["kc"] > 0].groupby("plot").agg(
        irrigation_events=("request_m3", lambda s: int((s > 0).sum())),
        gross_irrigation_m3=("request_m3", "sum"), rain_mm=("rain_mm", "sum"),
        etc_mm=("etc_mm", "sum"), eta_mm=("eta_mm", "sum"), stress_days=("stressed", "sum"))
    summary.round(1).to_csv(out / "farm_only_summary.csv")
    check["farm_only"] = summary.round(1).reset_index().to_dict(orient="records")
    check["kc_mid_adjusted"] = {p.name: round(p.kc_mid, 3) for p in plots}
    return check


def plot_soil_moisture(df: pd.DataFrame, plots, path: Path, title: str) -> None:
    fig, axes = plt.subplots(len(plots), 1, figsize=(10, 2.2 * len(plots)), sharex=True)
    for ax, p in zip(axes, plots):
        d = df[(df["plot"] == p.name) & (df["kc"] > 0)]
        t = pd.to_datetime(d["date"])
        ax.plot(t, d["soil_moisture"], color=PLOT_COLORS[p.name], lw=1.5, label="soil moisture")
        trigger = p.soil.theta_fc - p.policy.trigger_fraction * d["raw_mm"] / (1000 * d["root_depth_m"])
        stress = p.soil.theta_fc - d["raw_mm"] / (1000 * d["root_depth_m"])
        ax.plot(t, trigger, color="grey", ls="--", lw=1, label="irrigation trigger")
        ax.plot(t, stress, color="black", ls=":", lw=1, label="stress threshold")
        ax.axhline(p.soil.theta_fc, color="tab:blue", alpha=0.3, lw=1)
        ax.axhline(p.soil.theta_wp, color="tab:red", alpha=0.3, lw=1)
        ax.set_ylabel("m³/m³")
        ax.set_title(f"{p.name} ({p.soil.name}, sown {p.sowing_date:%d %b})", fontsize=9, loc="left")
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8, loc="lower right", ncol=3)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Scenario runs (phases 4-5)
# ---------------------------------------------------------------------------

def _run_scenario(args):
    name, weather, out = args
    res = run_season(build_plots(weather), weather, SCENARIOS[name], SEASON_START, SEASON_END)
    save_result(res, out / "season" / name)
    return name, res


def phase4_chain(results: dict, out: Path, leak_name: str = "pipe_burst") -> dict:
    """Show the chain leak -> low pressure -> less water -> drier soil for the plot it hits hardest."""
    from irrigation_twin.catalog import SCENARIOS as S
    from irrigation_twin.network.build import PLOT_JUNCTIONS

    out.mkdir(parents=True, exist_ok=True)
    base, leak = results["baseline"], results[leak_name]
    ev = S[leak_name].leaks[0]
    start = pd.Timestamp(ev.start)
    stop = pd.Timestamp(ev.end) if ev.end else pd.Timestamp(SEASON_END)

    def mean_sm(res):
        d = res.plots.assign(date=pd.to_datetime(res.plots["date"]))
        d = d[(d["date"] >= start) & (d["date"] <= stop + pd.Timedelta(days=7))]
        return d.groupby("plot")["soil_moisture"].mean()

    worst = (mean_sm(base) - mean_sm(leak)).idxmax()
    j = PLOT_JUNCTIONS[worst]
    lo, hi = start - pd.Timedelta(days=12), stop + pd.Timedelta(days=21)
    leak_label = f"{leak_name.replace('_', ' ')} ({ev.pipe}, {ev.hole_diameter_m * 100:.0f} cm)"

    fig, axes = plt.subplots(4, 1, figsize=(10, 10), sharex=True)
    for res, lbl, color in [(base, "baseline", "tab:blue"), (leak, leak_label, "tab:orange")]:
        p = res.pressure[j]
        p = p[(p.index >= lo) & (p.index < hi)]
        axes[0].plot(p.index, p.values, color=color, lw=0.8, label=lbl)
        d = res.plots[res.plots["plot"] == worst].copy()
        d["date"] = pd.to_datetime(d["date"])
        d = d[(d["date"] >= lo) & (d["date"] < hi)]
        shift = pd.Timedelta(hours=-5) if color == "tab:blue" else pd.Timedelta(hours=5)
        axes[1].bar(d["date"] + shift, d["delivered_m3"], width=0.4, color=color, label=lbl)
        axes[2].plot(d["date"], d["soil_moisture"], color=color, label=lbl)
        axes[3].plot(d["date"], d["ks"], color=color, label=lbl)
    axes[0].axhline(REQUIRED_PRESSURE, color="grey", ls="--", lw=1)
    axes[0].set_ylabel(f"Pressure at {j} (m)")
    axes[1].set_ylabel("Water delivered (m³/day)")
    axes[2].set_ylabel("Soil moisture (m³/m³)")
    axes[3].set_ylabel("Water stress Ks")
    axes[3].set_ylim(0, 1.05)
    for ax in axes:
        ax.axvspan(start + pd.Timedelta(hours=ev.start_hour), stop + pd.Timedelta(days=1), color="red", alpha=0.07)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8, loc="lower left")
    fig.suptitle(f"Coupling chain for {worst}: leak → low pressure → less water → drier soil → stress\n"
                 "(shaded: leak open)")
    fig.tight_layout()
    fig.savefig(out / "leak_chain.png", dpi=150)
    plt.close(fig)
    bs, ls = base.plot_summary().loc[worst], leak.plot_summary().loc[worst]
    sm = (mean_sm(base)[worst], mean_sm(leak)[worst])
    return {"scenario": leak_name, "plot": worst, "junction": j,
            "mean_soil_moisture_during_leak": [float(sm[0]), float(sm[1])],
            "delivered_m3": [float(bs["delivered_m3"]), float(ls["delivered_m3"])],
            "stress_days": [int(bs["stress_days"]), int(ls["stress_days"])],
            "relative_yield": [float(bs["relative_yield"]), float(ls["relative_yield"])]}


def phase5(results: dict) -> pd.DataFrame:
    out = OUT / "phase5"
    out.mkdir(parents=True, exist_ok=True)
    rows, per_plot = [], []
    for name, res in results.items():
        rows.append({"scenario": name, **res.summary()})
        ps = res.plot_summary().reset_index()
        ps.insert(0, "scenario", name)
        per_plot.append(ps)
    comp = pd.DataFrame(rows).set_index("scenario")
    comp.round(3).to_csv(out / "scenario_comparison.csv")
    pd.concat(per_plot).round(3).to_csv(out / "scenario_by_plot.csv", index=False)

    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    names = list(results)
    x = np.arange(len(names))
    axes[0].bar(x, comp["delivered_m3"], label="delivered to plots")
    axes[0].bar(x, comp["leak_m3"], bottom=comp["delivered_m3"], label="lost to leak", color="tab:red")
    axes[0].set_ylabel("Water (m³, season)")
    axes[0].legend(fontsize=8)
    axes[1].bar(x, comp["stress_days_total"], color="tab:orange")
    axes[1].set_ylabel("Plot-days of crop stress")
    by_plot = pd.concat(per_plot)
    for i, p in enumerate(PLOT_COLORS):
        vals = by_plot[by_plot["plot"] == p].set_index("scenario").loc[names, "relative_yield"]
        axes[2].bar(x + (i - 1.5) * 0.2, vals * 100, 0.2, label=p, color=PLOT_COLORS[p])
    axes[2].set_ylabel("Relative yield (%)")
    axes[2].set_ylim(0, 105)
    axes[2].legend(fontsize=7, ncol=2)
    for ax in axes:
        ax.set_xticks(x, [n.replace("_", "\n") for n in names], fontsize=8)
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle("Scenario comparison, Rabi 2023-24")
    fig.tight_layout()
    fig.savefig(out / "scenario_comparison.png", dpi=150)
    plt.close(fig)
    return comp


# ---------------------------------------------------------------------------
# Optimiser (stretch)
# ---------------------------------------------------------------------------

GRID = [Candidate(t, 20.0, w, p) for t in (0.6, 0.8, 1.0) for w in (12, 18) for p in (False, True)]
SAVING_SWEEP = [Candidate(t, 20.0, 12, False) for t in (0.6, 0.8, 1.0, 1.2)]


def optimiser_outputs(grid: pd.DataFrame, sweep: pd.DataFrame) -> dict:
    out = OUT / "optimiser"
    out.mkdir(parents=True, exist_ok=True)
    keep = ["candidate", "trigger_fraction", "window_hours", "priority", "delivered_m3", "pumped_m3",
            "pump_energy_kwh", "stress_days_total", "mean_relative_yield", "min_relative_yield"]
    grid = grid.sort_values(["mean_relative_yield", "min_relative_yield", "delivered_m3"],
                            ascending=[False, False, True]).reset_index(drop=True)
    grid[keep].round(4).to_csv(out / "grid_drought_pump_failure.csv", index=False)
    sweep = sweep.sort_values("trigger_fraction").reset_index(drop=True)
    sweep[keep].round(4).to_csv(out / "water_saving_sweep_baseline.csv", index=False)

    default = grid[(grid["trigger_fraction"] == 0.8) & (grid["window_hours"] == 12) & (~grid["priority"])].iloc[0]
    best = grid.iloc[0]
    ref = sweep[sweep["trigger_fraction"] == 0.8].iloc[0]
    sweep["water_saved_m3"] = ref["delivered_m3"] - sweep["delivered_m3"]
    sweep["water_saved_pct"] = 100 * sweep["water_saved_m3"] / ref["delivered_m3"]
    sweep["yield_change_pct"] = 100 * (sweep["mean_relative_yield"] - ref["mean_relative_yield"])
    sweep[keep + ["water_saved_m3", "water_saved_pct", "yield_change_pct"]].round(4).to_csv(
        out / "water_saving_sweep_baseline.csv", index=False)

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
    labels = [c.replace(", cap 20 mm", "").replace(" RAW", "") for c in grid["candidate"]]
    colors = ["tab:green" if i == 0 else ("tab:grey" if lbl != default["candidate"] else "tab:blue")
              for i, lbl in enumerate(grid["candidate"])]
    ax[0].barh(range(len(grid)), grid["mean_relative_yield"] * 100, color=colors)
    ax[0].set_yticks(range(len(grid)), [lbl.replace("trigger ", "t=") for lbl in labels], fontsize=7)
    ax[0].invert_yaxis()
    ax[0].set_xlabel("Mean relative yield (%)")
    lo = max(0, grid["mean_relative_yield"].min() * 100 - 5)
    ax[0].set_xlim(lo, 100)
    ax[0].set_title("Drought + pump failure: schedule options\n(green = best, blue = default)", fontsize=9)
    ax[1].plot(sweep["delivered_m3"], sweep["mean_relative_yield"] * 100, "o-")
    for _, r in sweep.iterrows():
        ax[1].annotate(f"trigger {r['trigger_fraction']:.1f}", (r["delivered_m3"], r["mean_relative_yield"] * 100),
                       textcoords="offset points", xytext=(5, -10), fontsize=8)
    ax[1].set_xlabel("Water delivered, season (m³)")
    ax[1].set_ylabel("Mean relative yield (%)")
    ax[1].set_title("Normal year: water use vs yield by irrigation trigger", fontsize=9)
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "optimiser.png", dpi=150)
    plt.close(fig)
    return {"best": best[keep].to_dict(), "default": default[keep].to_dict(),
            "water_saving_sweep": sweep[keep + ["water_saved_m3", "water_saved_pct", "yield_change_pct"]]
            .round(4).to_dict(orient="records")}


# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    global OUT, WEATHER_OVERRIDE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weather", type=Path, help="weather CSV (default: the cached Open-Meteo file)")
    ap.add_argument("--out", type=Path, help="output folder (default: outputs/)")
    args = ap.parse_args()
    if args.weather:
        WEATHER_OVERRIDE = args.weather.resolve()
    if args.out:
        OUT = args.out.resolve()
    weather = load()
    summary = {"phase3": phase3(weather)}
    print("phase 3 done:", {k: v for k, v in summary["phase3"].items() if not isinstance(v, (list, dict))})

    from irrigation_twin.catalog import SCENARIOS as S
    jobs_s = [(name, weather, OUT) for name in S]
    jobs_g = [(c, S["drought_pump_failure"], weather, SEASON_START, SEASON_END) for c in GRID]
    jobs_w = [(c, S["baseline"], weather, SEASON_START, SEASON_END) for c in SAVING_SWEEP]
    with ProcessPoolExecutor() as ex:
        fut_s = [ex.submit(_run_scenario, j) for j in jobs_s]
        fut_g = [ex.submit(_evaluate, j) for j in jobs_g]
        fut_w = [ex.submit(_evaluate, j) for j in jobs_w]
        results = dict(f.result() for f in fut_s)
        grid = pd.DataFrame([f.result() for f in fut_g])
        sweep = pd.DataFrame([f.result() for f in fut_w])
    results = {name: results[name] for name in S}

    summary["phase4_chain"] = phase4_chain(results, OUT / "phase4")
    comp = phase5(results)
    summary["phase5"] = comp.round(3).reset_index().to_dict(orient="records")
    summary["optimiser"] = optimiser_outputs(grid, sweep)
    (OUT / "season_summary.json").write_text(json.dumps(summary, indent=2, default=float))

    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(comp.round(2))
        print("chain:", summary["phase4_chain"])
        print("optimiser best:", summary["optimiser"]["best"])
        print("optimiser default:", summary["optimiser"]["default"])


if __name__ == "__main__":
    main()
