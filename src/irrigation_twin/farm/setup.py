"""The study farm: four maize plots on the network's plot junctions (phase 1 scope)."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..network.build import PLOT_JUNCTIONS
from .crop import LOAM, LOAMY_SAND, MAIZE_GRAIN, SANDY_LOAM, Crop, kc_mid_adjusted
from .plot import IrrigationPolicy, Plot

#: Staggered sowing, a common practice that also spreads the irrigation
#: peaks. Soils vary across the farm.
PLOT_LAYOUT = {
    "Plot_A": {"soil": SANDY_LOAM, "sowing": dt.date(2023, 11, 1)},
    "Plot_B": {"soil": SANDY_LOAM, "sowing": dt.date(2023, 11, 8)},
    "Plot_C": {"soil": LOAM, "sowing": dt.date(2023, 11, 15)},
    "Plot_D": {"soil": LOAMY_SAND, "sowing": dt.date(2023, 11, 22)},
}
PLOT_AREA_HA = 0.5

SEASON_START = min(p["sowing"] for p in PLOT_LAYOUT.values())
SEASON_END = max(p["sowing"] for p in PLOT_LAYOUT.values()) + dt.timedelta(days=MAIZE_GRAIN.season_length - 1)


def mid_season_climate(weather: pd.DataFrame, crop: Crop, sowing: dt.date) -> tuple[float, float]:
    """Mean u2 and RHmin over the plot's mid-season stage, for the Kc_mid adjustment."""
    start = sowing + dt.timedelta(days=crop.l_ini + crop.l_dev)
    end = start + dt.timedelta(days=crop.l_mid - 1)
    w = weather[(weather["date"] >= start) & (weather["date"] <= end)]
    if w.empty:
        return 2.0, 45.0  # FAO-56 standard climate: no adjustment
    return float(w["u2_ms"].mean()), float(w["rh_min"].mean())


def build_plots(weather: pd.DataFrame | None = None,
                policy: IrrigationPolicy | None = None) -> list[Plot]:
    """Create the farm's plots. With weather, Kc_mid is adjusted to the local climate (FAO-56 eq. 62)."""
    policy = policy or IrrigationPolicy()
    plots = []
    for name, cfg in PLOT_LAYOUT.items():
        kc_mid = None
        if weather is not None:
            u2, rh_min = mid_season_climate(weather, MAIZE_GRAIN, cfg["sowing"])
            kc_mid = kc_mid_adjusted(MAIZE_GRAIN, u2, rh_min)
        plots.append(Plot(name=name, junction=PLOT_JUNCTIONS[name], area_ha=PLOT_AREA_HA,
                          crop=MAIZE_GRAIN, soil=cfg["soil"], sowing_date=cfg["sowing"],
                          policy=policy, kc_mid=kc_mid))
    return plots
