"""Smart Irrigation Digital Twin: Streamlit dashboard (phase 6).

Run from the repository root::

    streamlit run app/dashboard.py

Reads the season results written by scripts/run_season_analysis.py
(outputs/season/<scenario>/). Set TWIN_OUTPUTS to read another folder.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from irrigation_twin.catalog import SCENARIOS, load_result  # noqa: E402
from irrigation_twin.farm.setup import PLOT_LAYOUT  # noqa: E402
from irrigation_twin.network.build import (  # noqa: E402
    JUNCTIONS,
    PIPES,
    PLOT_JUNCTIONS,
    REQUIRED_PRESSURE,
    RESERVOIR,
    TANK,
)

OUTPUTS = Path(os.environ.get("TWIN_OUTPUTS", ROOT / "outputs"))
PLOT_COLORS = {"Plot_A": "#1f77b4", "Plot_B": "#ff7f0e", "Plot_C": "#2ca02c", "Plot_D": "#d62728"}
SCENARIO_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]

st.set_page_config(page_title="Irrigation Digital Twin", page_icon="💧", layout="wide")


@st.cache_data
def available_scenarios() -> list[str]:
    folder = OUTPUTS / "season"
    found = [p.name for p in folder.iterdir() if (p / "meta.json").exists()] if folder.exists() else []
    order = list(SCENARIOS)
    return sorted(found, key=lambda n: order.index(n) if n in order else len(order))


@st.cache_data
def load(name: str) -> dict:
    return load_result(OUTPUTS / "season" / name)


def label(name: str) -> str:
    return name.replace("_", " ").capitalize()


scenarios = available_scenarios()
if not scenarios:
    st.title("Smart Irrigation Digital Twin")
    st.error(f"No season results found in `{OUTPUTS / 'season'}`. Run "
             "`python scripts/fetch_weather.py` and then `python scripts/run_season_analysis.py`.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("💧 Irrigation twin")
scenario = st.sidebar.selectbox("Scenario", scenarios, format_func=label)
compare = st.sidebar.selectbox("Compare with", [s for s in scenarios if s != scenario] or scenarios,
                               format_func=label)
data = load(scenario)
ref = load(compare)
st.sidebar.caption(data["meta"]["description"])

dates = sorted(data["network"]["date"].dt.date.unique())
default_day = next((d for d in dates if str(d) == "2024-02-05"), dates[len(dates) // 2])
day = st.sidebar.select_slider("Day", options=dates, value=default_day, format_func=lambda d: d.strftime("%d %b %Y"))
hour = st.sidebar.slider("Hour (network view)", 0, 23, 9)
st.sidebar.markdown("---")
st.sidebar.caption("Anantapur, Andhra Pradesh · maize, Rabi 2023-24 · four 0.5 ha plots · "
                   "WNTR pressure-driven hydraulics · FAO-56 soil water balance")

# ---------------------------------------------------------------------------
# Header KPIs
# ---------------------------------------------------------------------------
st.title(f"{label(scenario)}")
s, r = data["meta"]["summary"], ref["meta"]["summary"]
vs = f"vs {label(compare).lower()}"
k = st.columns(3) + st.columns(3)
k[0].metric("Water delivered to plots", f"{s['delivered_m3']:,.0f} m³", f"{s['delivered_m3'] - r['delivered_m3']:+,.0f} {vs}")
k[1].metric("Lost to leaks", f"{s['leak_m3']:,.0f} m³", f"{s['leak_m3'] - r['leak_m3']:+,.0f} {vs}", delta_color="inverse")
k[2].metric("Pump energy", f"{s['pump_energy_kwh']:,.0f} kWh", f"{s['pump_energy_kwh'] - r['pump_energy_kwh']:+,.0f} {vs}", delta_color="inverse")
k[3].metric("Crop stress (plot-days)", f"{s['stress_days_total']}", f"{s['stress_days_total'] - r['stress_days_total']:+d} {vs}", delta_color="inverse")
k[4].metric("Mean relative yield", f"{100 * s['mean_relative_yield']:.1f} %", f"{100 * (s['mean_relative_yield'] - r['mean_relative_yield']):+.1f} pts {vs}")
k[5].metric("Low-pressure days", f"{s['days_low_pressure']}", f"{s['days_low_pressure'] - r['days_low_pressure']:+d} {vs}", delta_color="inverse")

tab_net, tab_plots, tab_water, tab_compare, tab_alerts, tab_stretch = st.tabs(
    ["Network", "Plots", "Water & network", "Scenario comparison", "Alerts", "Leak detection & optimiser"])

plots_df = data["plots"]
net = data["network"].set_index(data["network"]["date"].dt.date)
today_plots = plots_df[plots_df["date"].dt.date == day].set_index("plot")

# ---------------------------------------------------------------------------
# Network view
# ---------------------------------------------------------------------------
with tab_net:
    ts = pd.Timestamp(day) + pd.Timedelta(hours=hour)
    pressure = data["pressure"].loc[ts] if ts in data["pressure"].index else None
    flow = data["flow"].loc[ts] if ts in data["flow"].index else None

    coords = {n: j["coordinates"] for n, j in JUNCTIONS.items()}
    coords[RESERVOIR["name"]] = RESERVOIR["coordinates"]
    coords[TANK["name"]] = TANK["coordinates"]
    fig = go.Figure()
    for name, p in {**PIPES, "PU1": {"start": "R1", "end": "J1"}}.items():
        (x0, y0), (x1, y1) = coords[p["start"]], coords[p["end"]]
        q = float(flow[name]) if flow is not None and name in flow else 0.0
        fig.add_trace(go.Scatter(x=[x0, x1], y=[y0, y1], mode="lines",
                                 line=dict(width=2 + min(abs(q), 20) * 0.6, color="#4c78a8" if name != "PU1" else "#888"),
                                 hoverinfo="text", text=f"{name}: {q:.2f} L/s", showlegend=False))
        fig.add_annotation(x=(x0 + x1) / 2, y=(y0 + y1) / 2, text=f"{name}<br>{q:.1f} L/s", showarrow=False,
                           font=dict(size=10, color="#555"), bgcolor="rgba(255,255,255,0.7)")
    names = list(JUNCTIONS)
    pvals = [float(pressure[n]) if pressure is not None and n in pressure else float("nan") for n in names]
    fig.add_trace(go.Scatter(
        x=[coords[n][0] for n in names], y=[coords[n][1] for n in names], mode="markers+text",
        marker=dict(size=[26 if n in PLOT_JUNCTIONS.values() else 18 for n in names], color=pvals,
                    colorscale="RdYlGn", cmin=0, cmax=30, showscale=True,
                    colorbar=dict(title="Pressure (m)"), line=dict(width=1, color="#333")),
        text=names, textposition="top center", hoverinfo="text",
        hovertext=[f"{n}: {v:.1f} m" for n, v in zip(names, pvals)], showlegend=False))
    # The reservoir and tank are drawn apart: their value is a water level, not a pressure.
    level = float(pressure[TANK["name"]]) if pressure is not None else float("nan")
    for n, sym, txt in [(RESERVOIR["name"], "diamond", "R1 sump"), (TANK["name"], "square", f"T1 tank<br>level {level:.2f} m")]:
        fig.add_trace(go.Scatter(x=[coords[n][0]], y=[coords[n][1]], mode="markers+text", text=[txt],
                                 textposition="bottom center", marker=dict(size=20, color="#9ecae1", symbol=sym,
                                                                          line=dict(width=1, color="#333")),
                                 hoverinfo="text", hovertext=[txt.replace("<br>", ", ")], showlegend=False))
    for plot, j in PLOT_JUNCTIONS.items():
        x, y = coords[j]
        rec = today_plots.loc[plot] if plot in today_plots.index else None
        status = "" if rec is None else f"<br>θ={rec['soil_moisture']:.3f} · Ks={rec['ks']:.2f}"
        fig.add_annotation(x=x, y=y - 60, text=f"<b>{plot.replace('_', ' ')}</b>{status}", showarrow=False,
                           font=dict(size=11, color=PLOT_COLORS[plot]))
    fig.update_layout(height=520, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
                      title=f"{ts:%d %b %Y, %H:00} · required pressure {REQUIRED_PRESSURE:.0f} m",
                      xaxis=dict(visible=False), yaxis=dict(visible=False, scaleanchor="x"))
    st.plotly_chart(fig, use_container_width=True)
    row = net.loc[day]
    c = st.columns(5)
    c[0].metric("Requested today", f"{row['requested_m3']:.0f} m³")
    c[1].metric("Delivered today", f"{row['delivered_m3']:.0f} m³")
    c[2].metric("Leak today", f"{row['leak_m3']:.0f} m³")
    c[3].metric("Tank start → end", f"{row['tank_start_m']:.2f} → {row['tank_end_m']:.2f} m")
    c[4].metric("Pump running", f"{row['pump_hours']:.1f} h" + (" (outage)" if row["pump_outage"] else ""))

# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------
with tab_plots:
    cols = st.columns(4)
    for col, plot in zip(cols, PLOT_JUNCTIONS):
        rec = today_plots.loc[plot]
        cfg = PLOT_LAYOUT[plot]
        state = "🔴 stressed" if rec["stressed"] else ("🟡 irrigating" if rec["request_m3"] > 0 else "🟢 OK")
        col.markdown(f"**{plot.replace('_', ' ')}** · {cfg['soil'].name} · sown {cfg['sowing']:%d %b}")
        col.metric("Soil moisture", f"{rec['soil_moisture']:.3f} m³/m³", state, delta_color="off")
        col.caption(f"Stage: {rec['stage']} · Kc {rec['kc']:.2f} · Ks {rec['ks']:.2f} · "
                    f"depletion {rec['depletion_end_mm']:.0f} of {rec['taw_mm']:.0f} mm")

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        subplot_titles=[p.replace("_", " ") for p in PLOT_JUNCTIONS])
    for i, plot in enumerate(PLOT_JUNCTIONS, start=1):
        soil = PLOT_LAYOUT[plot]["soil"]
        for src, name, dash in [(data["plots"], label(scenario), "solid"), (ref["plots"], label(compare), "dot")]:
            d = src[(src["plot"] == plot) & (src["kc"] > 0)]
            fig.add_trace(go.Scatter(x=d["date"], y=d["soil_moisture"], name=name, legendgroup=name,
                                     showlegend=i == 1, line=dict(color=PLOT_COLORS[plot], dash=dash)), row=i, col=1)
        d = data["plots"][(data["plots"]["plot"] == plot) & (data["plots"]["kc"] > 0)]
        stress = soil.theta_fc - d["raw_mm"] / (1000 * d["root_depth_m"])
        fig.add_trace(go.Scatter(x=d["date"], y=stress, name="stress threshold", legendgroup="stress",
                                 showlegend=i == 1, line=dict(color="black", width=1, dash="dash")), row=i, col=1)
        fig.add_vline(x=pd.Timestamp(day), line=dict(color="grey", width=1), row=i, col=1)
    fig.update_layout(height=820, margin=dict(l=10, r=10, t=40, b=10), hovermode="x unified")
    fig.update_yaxes(title_text="θ (m³/m³)")
    st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Water and network time series
# ---------------------------------------------------------------------------
with tab_water:
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        subplot_titles=["Water requested vs delivered (m³/day)", "Pumped and leaked (m³/day)",
                                        "Tank level at midnight (m)", "Lowest plot pressure while irrigating (m)"])
    n = data["network"]
    fig.add_trace(go.Bar(x=n["date"], y=n["requested_m3"], name="requested", marker_color="#bbb"), row=1, col=1)
    fig.add_trace(go.Bar(x=n["date"], y=n["delivered_m3"], name="delivered", marker_color="#1f77b4"), row=1, col=1)
    fig.add_trace(go.Scatter(x=n["date"], y=n["pumped_m3"], name="pumped", line=dict(color="#555")), row=2, col=1)
    fig.add_trace(go.Scatter(x=n["date"], y=n["leak_m3"], name="leak", line=dict(color="#d62728")), row=2, col=1)
    for src, name, dash in [(n, label(scenario), "solid"), (ref["network"], label(compare), "dot")]:
        fig.add_trace(go.Scatter(x=src["date"], y=src["tank_end_m"], name=f"tank · {name}",
                                 line=dict(color="#2ca02c", dash=dash)), row=3, col=1)
        fig.add_trace(go.Scatter(x=src["date"], y=src["min_plot_pressure_irrigating_m"], mode="markers",
                                 name=f"pressure · {name}", marker=dict(size=4, color="#9467bd",
                                                                          symbol="circle" if dash == "solid" else "x")),
                      row=4, col=1)
    fig.add_hline(y=REQUIRED_PRESSURE, line=dict(color="grey", dash="dash"), row=4, col=1)
    fig.update_layout(height=860, barmode="overlay", margin=dict(l=10, r=10, t=40, b=10), hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader(f"Hourly pressures on {day:%d %b %Y}")
    p_day = data["pressure"][data["pressure"].index.date == day]
    fig = go.Figure()
    for plot, j in PLOT_JUNCTIONS.items():
        fig.add_trace(go.Scatter(x=p_day.index.hour, y=p_day[j], name=f"{j} ({plot.replace('_', ' ')})",
                                 line=dict(color=PLOT_COLORS[plot])))
    fig.add_hline(y=REQUIRED_PRESSURE, line=dict(color="grey", dash="dash"))
    fig.update_layout(height=320, xaxis_title="hour", yaxis_title="pressure (m)", margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Scenario comparison
# ---------------------------------------------------------------------------
with tab_compare:
    rows = []
    for name in scenarios:
        m = load(name)["meta"]["summary"]
        rows.append({"Scenario": label(name), "Delivered (m³)": m["delivered_m3"], "Leaked (m³)": m["leak_m3"],
                     "Pumped (m³)": m["pumped_m3"], "Energy (kWh)": m["pump_energy_kwh"],
                     "Stress plot-days": m["stress_days_total"], "Mean yield (%)": 100 * m["mean_relative_yield"],
                     "Worst plot yield (%)": 100 * m["min_relative_yield"], "Low-pressure days": m["days_low_pressure"]})
    table = pd.DataFrame(rows).set_index("Scenario")
    st.dataframe(table.style.format("{:,.1f}"), use_container_width=True)
    by_plot = []
    for name in scenarios:
        d = load(name)["plots"]
        d = d[d["kc"] > 0].groupby("plot").agg(eta=("eta_mm", "sum"), etc=("etc_mm", "sum"),
                                                 stress=("stressed", "sum"))
        d["yield"] = (1 - 1.25 * (1 - d["eta"] / d["etc"])).clip(0, 1) * 100
        d["scenario"] = label(name)
        by_plot.append(d.reset_index())
    by_plot = pd.concat(by_plot)
    fig = go.Figure()
    for plot in PLOT_JUNCTIONS:
        d = by_plot[by_plot["plot"] == plot]
        fig.add_trace(go.Bar(x=d["scenario"], y=d["yield"], name=plot.replace("_", " "), marker_color=PLOT_COLORS[plot]))
    fig.update_layout(barmode="group", height=380, yaxis_title="Relative yield (%)", yaxis_range=[0, 105],
                      margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Relative yield from FAO-33: 1 − Ya/Ym = Ky (1 − ΣETa/ΣETc), with Ky = 1.25 for maize.")

# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
with tab_alerts:
    a = data["alerts"]
    if a.empty:
        st.success("No alerts in this scenario.")
    else:
        counts = a["kind"].value_counts()
        c = st.columns(len(counts))
        for col, (kind, cnt) in zip(c, counts.items()):
            col.metric(kind.capitalize(), int(cnt))
        kinds = st.multiselect("Show", list(counts.index), default=list(counts.index))
        st.dataframe(a[a["kind"].isin(kinds)].assign(date=lambda x: x["date"].dt.date),
                     use_container_width=True, hide_index=True, height=420)

# ---------------------------------------------------------------------------
# Stretch goals
# ---------------------------------------------------------------------------
with tab_stretch:
    left, right = st.columns(2)
    det = OUTPUTS / "leak_detection" / "metrics.json"
    with left:
        st.subheader("Leak detection")
        if det.exists():
            m = json.loads(det.read_text())
            rf, rule = m["random_forest"], m["threshold_rule"]
            st.caption("Tested on leaks in pipes P3 and P7 and hole sizes never seen in training.")
            st.dataframe(pd.DataFrame({
                "Random forest": [rf["hourly_precision"], rf["hourly_recall"], rf["event_detection_rate"],
                                  rf["median_delay_h"], rf["false_alarm_day_rate"]],
                "Threshold rule": [rule["hourly_precision"], rule["hourly_recall"], rule["event_detection_rate"],
                                   rule["median_delay_h"], rule["false_alarm_day_rate"]],
            }, index=["Hourly precision", "Hourly recall", "Leak days detected", "Median delay (h)",
                      "False-alarm day rate"]).style.format("{:.3f}"), use_container_width=True)
            img = OUTPUTS / "leak_detection" / "detection_by_size.png"
            if img.exists():
                st.image(str(img))
        else:
            st.info("Run `python scripts/stretch_leak_detection.py`.")
    with right:
        st.subheader("Schedule optimiser")
        grid = OUTPUTS / "optimiser" / "grid_drought_pump_failure.csv"
        if grid.exists():
            g = pd.read_csv(grid)
            st.caption("Drought plus pump failure: schedule settings ranked by mean relative yield.")
            st.dataframe(g[["candidate", "mean_relative_yield", "min_relative_yield", "delivered_m3",
                            "stress_days_total"]].style.format(
                {"mean_relative_yield": "{:.3f}", "min_relative_yield": "{:.3f}", "delivered_m3": "{:,.0f}"}),
                use_container_width=True, hide_index=True)
            sweep = OUTPUTS / "optimiser" / "water_saving_sweep_baseline.csv"
            if sweep.exists():
                st.caption("Normal year: water saved against the default trigger (0.8 × RAW).")
                st.dataframe(pd.read_csv(sweep)[["trigger_fraction", "delivered_m3", "water_saved_m3",
                                                 "water_saved_pct", "mean_relative_yield", "yield_change_pct"]]
                             .style.format("{:.2f}"), use_container_width=True, hide_index=True)
        else:
            st.info("Run `python scripts/run_season_analysis.py`.")
