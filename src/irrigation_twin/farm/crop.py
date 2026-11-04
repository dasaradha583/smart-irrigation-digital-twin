"""Crop and soil parameters, and the FAO-56 crop coefficient curve.

Sources:
- Crop coefficients: FAO-56 Table 12 (maize, grain).
- Stage lengths: FAO-56 Table 11, maize (grain), "India (dry, cool)", sown in October.
- Rooting depth and depletion fraction p: FAO-56 Table 22 (maize, grain).
- Soil water limits: FAO-56 Table 19, mid-points of the ranges.
- Yield response factor Ky: FAO Irrigation and Drainage Paper 33 (Doorenbos &
  Kassam, 1979), maize, whole season.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Crop:
    name: str
    kc_ini: float
    kc_mid: float
    kc_end: float
    l_ini: int
    l_dev: int
    l_mid: int
    l_late: int
    root_depth_min_m: float  # at sowing
    root_depth_max_m: float  # reached at the end of the development stage
    p_table: float           # depletion fraction for no stress at ETc = 5 mm/day
    height_m: float          # mean plant height in mid-season (for the Kc_mid adjustment)
    ky: float                # seasonal yield response factor

    @property
    def season_length(self) -> int:
        return self.l_ini + self.l_dev + self.l_mid + self.l_late


MAIZE_GRAIN = Crop(
    name="maize (grain)",
    kc_ini=0.30, kc_mid=1.20, kc_end=0.35,
    l_ini=30, l_dev=40, l_mid=50, l_late=30,
    root_depth_min_m=0.30, root_depth_max_m=1.00,
    p_table=0.55, height_m=2.0, ky=1.25,
)


@dataclass(frozen=True)
class Soil:
    name: str
    theta_fc: float  # m3/m3, field capacity
    theta_wp: float  # m3/m3, wilting point

    def taw_mm(self, root_depth_m: float) -> float:
        """FAO-56 eq. 82: total available water in the root zone (mm)."""
        return 1000.0 * (self.theta_fc - self.theta_wp) * root_depth_m


SANDY_LOAM = Soil("sandy loam", theta_fc=0.23, theta_wp=0.11)
LOAM = Soil("loam", theta_fc=0.25, theta_wp=0.12)
LOAMY_SAND = Soil("loamy sand", theta_fc=0.15, theta_wp=0.065)


def kc_mid_adjusted(crop: Crop, u2_mean: float, rh_min_mean: float) -> float:
    """FAO-56 eq. 62: adjust Kc_mid for a climate other than RHmin = 45 %, u2 = 2 m/s."""
    u2 = float(np.clip(u2_mean, 1.0, 6.0))
    rh = float(np.clip(rh_min_mean, 20.0, 80.0))
    return crop.kc_mid + (0.04 * (u2 - 2.0) - 0.004 * (rh - 45.0)) * (crop.height_m / 3.0) ** 0.3


def kc_on_day(crop: Crop, day: int, kc_mid: float | None = None) -> float:
    """Crop coefficient on day ``day`` after sowing (0-based), FAO-56 Fig. 25 / eq. 66.

    Constant through the initial stage, linear to Kc_mid through development,
    constant through mid-season, then linear to Kc_end. Returns 0 outside the season.
    """
    kc_mid = crop.kc_mid if kc_mid is None else kc_mid
    if day < 0 or day >= crop.season_length:
        return 0.0
    e_ini = crop.l_ini
    e_dev = e_ini + crop.l_dev
    e_mid = e_dev + crop.l_mid
    if day < e_ini:
        return crop.kc_ini
    if day < e_dev:
        return crop.kc_ini + (day - e_ini + 1) / crop.l_dev * (kc_mid - crop.kc_ini)
    if day < e_mid:
        return kc_mid
    return kc_mid + (day - e_mid + 1) / crop.l_late * (crop.kc_end - kc_mid)


def growth_stage(crop: Crop, day: int) -> str:
    if day < 0:
        return "not sown"
    if day >= crop.season_length:
        return "harvested"
    if day < crop.l_ini:
        return "initial"
    if day < crop.l_ini + crop.l_dev:
        return "development"
    if day < crop.l_ini + crop.l_dev + crop.l_mid:
        return "mid-season"
    return "late season"


def root_depth_on_day(crop: Crop, day: int) -> float:
    """Root depth (m): linear growth from sowing to the end of development, then constant.

    FAO-56 uses the maximum rooting depth. Growing roots keep early-season
    irrigations small, which suits a daily irrigation twin.
    """
    end_dev = crop.l_ini + crop.l_dev
    frac = float(np.clip(day / end_dev, 0.0, 1.0))
    return crop.root_depth_min_m + frac * (crop.root_depth_max_m - crop.root_depth_min_m)


def depletion_fraction(crop: Crop, etc_mm: float) -> float:
    """FAO-56 Table 22 footnote: p = p_table + 0.04 (5 - ETc), limited to 0.1-0.8."""
    return float(np.clip(crop.p_table + 0.04 * (5.0 - etc_mm), 0.1, 0.8))
