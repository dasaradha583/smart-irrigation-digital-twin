"""Daily reference evapotranspiration, FAO-56 Penman-Monteith.

Equation numbers refer to Allen, Pereira, Raes & Smith (1998), *Crop
evapotranspiration: Guidelines for computing crop water requirements*,
FAO Irrigation and Drainage Paper 56. Every function accepts scalars or
NumPy/pandas arrays.
"""

from __future__ import annotations

import numpy as np

SOLAR_CONSTANT = 0.0820  # MJ m-2 min-1
STEFAN_BOLTZMANN = 4.903e-9  # MJ K-4 m-2 day-1
ALBEDO = 0.23  # hypothetical grass reference crop


def atmospheric_pressure(elevation_m):
    """Eq. 7: atmospheric pressure (kPa) at elevation z (m)."""
    return 101.3 * ((293.0 - 0.0065 * np.asarray(elevation_m)) / 293.0) ** 5.26


def psychrometric_constant(pressure_kpa):
    """Eq. 8: gamma (kPa/degC)."""
    return 0.665e-3 * np.asarray(pressure_kpa)


def saturation_vapour_pressure(t_c):
    """Eq. 11: e0(T) (kPa)."""
    t_c = np.asarray(t_c, dtype=float)
    return 0.6108 * np.exp(17.27 * t_c / (t_c + 237.3))


def slope_vapour_pressure_curve(t_c):
    """Eq. 13: Delta (kPa/degC) at air temperature T."""
    t_c = np.asarray(t_c, dtype=float)
    return 4098.0 * saturation_vapour_pressure(t_c) / (t_c + 237.3) ** 2


def mean_saturation_vapour_pressure(tmax_c, tmin_c):
    """Eq. 12: es (kPa)."""
    return (saturation_vapour_pressure(tmax_c) + saturation_vapour_pressure(tmin_c)) / 2.0


def actual_vapour_pressure(tmax_c, tmin_c, rh_max, rh_min):
    """Eq. 17: ea (kPa) from RHmax and RHmin (%)."""
    return (saturation_vapour_pressure(tmin_c) * np.asarray(rh_max) / 100.0
            + saturation_vapour_pressure(tmax_c) * np.asarray(rh_min) / 100.0) / 2.0


def wind_speed_at_2m(u_z, z_m=10.0):
    """Eq. 47: convert wind speed measured at height z (m) to 2 m."""
    return np.asarray(u_z) * 4.87 / np.log(67.8 * z_m - 5.42)


def extraterrestrial_radiation(day_of_year, latitude_deg):
    """Eq. 21-25: Ra (MJ m-2 day-1)."""
    j = np.asarray(day_of_year, dtype=float)
    phi = np.deg2rad(latitude_deg)
    dr = 1.0 + 0.033 * np.cos(2.0 * np.pi * j / 365.0)                # eq. 23
    delta = 0.409 * np.sin(2.0 * np.pi * j / 365.0 - 1.39)            # eq. 24
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(delta), -1.0, 1.0))  # eq. 25
    return (24.0 * 60.0 / np.pi) * SOLAR_CONSTANT * dr * (
        ws * np.sin(phi) * np.sin(delta) + np.cos(phi) * np.cos(delta) * np.sin(ws))


def daylight_hours(day_of_year, latitude_deg):
    """Eq. 34: N (hours)."""
    j = np.asarray(day_of_year, dtype=float)
    phi = np.deg2rad(latitude_deg)
    delta = 0.409 * np.sin(2.0 * np.pi * j / 365.0 - 1.39)
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(delta), -1.0, 1.0))
    return 24.0 / np.pi * ws


def solar_radiation_from_sunshine(sunshine_hours, day_of_year, latitude_deg, a_s=0.25, b_s=0.50):
    """Eq. 35 (Angstrom): Rs (MJ m-2 day-1) from measured sunshine duration n."""
    n_max = daylight_hours(day_of_year, latitude_deg)
    return (a_s + b_s * np.asarray(sunshine_hours) / n_max) * extraterrestrial_radiation(day_of_year, latitude_deg)


def clear_sky_radiation(ra, elevation_m):
    """Eq. 37: Rso (MJ m-2 day-1)."""
    return (0.75 + 2e-5 * np.asarray(elevation_m)) * np.asarray(ra)


def net_longwave_radiation(tmax_c, tmin_c, ea_kpa, rs, rso):
    """Eq. 39: Rnl (MJ m-2 day-1)."""
    tmax_k4 = (np.asarray(tmax_c) + 273.16) ** 4
    tmin_k4 = (np.asarray(tmin_c) + 273.16) ** 4
    ratio = np.clip(np.asarray(rs) / np.asarray(rso), 0.25, 1.0)  # Rs/Rso is limited to <= 1.0
    return (STEFAN_BOLTZMANN * (tmax_k4 + tmin_k4) / 2.0
            * (0.34 - 0.14 * np.sqrt(np.asarray(ea_kpa)))
            * (1.35 * ratio - 0.35))


def et0_penman_monteith(tmax_c, tmin_c, rh_max, rh_min, u2_ms, rs_mj, day_of_year,
                        latitude_deg, elevation_m):
    """Eq. 6: daily grass-reference ET0 (mm/day).

    Soil heat flux G is taken as 0 for daily steps (eq. 42).
    """
    tmean = (np.asarray(tmax_c) + np.asarray(tmin_c)) / 2.0
    gamma = psychrometric_constant(atmospheric_pressure(elevation_m))
    delta = slope_vapour_pressure_curve(tmean)
    es = mean_saturation_vapour_pressure(tmax_c, tmin_c)
    ea = actual_vapour_pressure(tmax_c, tmin_c, rh_max, rh_min)
    ra = extraterrestrial_radiation(day_of_year, latitude_deg)
    rso = clear_sky_radiation(ra, elevation_m)
    rns = (1.0 - ALBEDO) * np.asarray(rs_mj)                          # eq. 38
    rnl = net_longwave_radiation(tmax_c, tmin_c, ea, rs_mj, rso)
    rn = rns - rnl                                                    # eq. 40
    g = 0.0
    u2 = np.asarray(u2_ms)
    numerator = 0.408 * delta * (rn - g) + gamma * (900.0 / (tmean + 273.0)) * u2 * (es - ea)
    denominator = delta + gamma * (1.0 + 0.34 * u2)
    return np.maximum(numerator / denominator, 0.0)


def et0_hargreaves(tmax_c, tmin_c, day_of_year, latitude_deg):
    """Eq. 52: temperature-only ET0 (mm/day), used only as a cross-check."""
    tmean = (np.asarray(tmax_c) + np.asarray(tmin_c)) / 2.0
    ra_mm = 0.408 * extraterrestrial_radiation(day_of_year, latitude_deg)
    return 0.0023 * (tmean + 17.8) * np.sqrt(np.maximum(np.asarray(tmax_c) - np.asarray(tmin_c), 0.0)) * ra_mm
