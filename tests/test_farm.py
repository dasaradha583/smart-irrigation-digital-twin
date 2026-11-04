"""Phase 3 checks: FAO-56 equations against the published worked examples, and
closure of the soil water balance (validation plan, check 2).

The weather series here are constant test inputs, not real data.
"""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from irrigation_twin.farm import MAIZE_GRAIN, SANDY_LOAM, IrrigationPolicy, Plot, build_plots
from irrigation_twin.farm.crop import depletion_fraction, kc_mid_adjusted, kc_on_day, root_depth_on_day
from irrigation_twin.farm.et0 import (
    atmospheric_pressure,
    et0_penman_monteith,
    extraterrestrial_radiation,
    mean_saturation_vapour_pressure,
    psychrometric_constant,
    solar_radiation_from_sunshine,
    wind_speed_at_2m,
)
from irrigation_twin.farm.weather import ANANTAPUR, add_et0, load_weather, parse_open_meteo, save_weather


# ---- FAO-56 worked examples --------------------------------------------------

def test_fao56_example2_psychrometric_constant():
    assert psychrometric_constant(atmospheric_pressure(1800)) == pytest.approx(0.054, abs=5e-4)


def test_fao56_example3_saturation_vapour_pressure():
    assert mean_saturation_vapour_pressure(24.5, 15.0) == pytest.approx(2.39, abs=0.01)


def test_fao56_example8_extraterrestrial_radiation():
    # 3 September at 20 deg S
    assert extraterrestrial_radiation(246, -20.0) == pytest.approx(32.2, abs=0.05)


def test_fao56_example10_solar_radiation():
    # Rio de Janeiro (22 deg 54' S), 15 May, n = 7.1 h
    assert solar_radiation_from_sunshine(7.1, 135, -22.9) == pytest.approx(14.5, abs=0.05)


def test_fao56_example18_daily_et0():
    # Brussels, 6 July: published ET0 = 3.9 mm/day
    rs = solar_radiation_from_sunshine(9.25, 187, 50.8)
    assert rs == pytest.approx(22.07, abs=0.01)
    u2 = wind_speed_at_2m(10 / 3.6, 10.0)
    assert u2 == pytest.approx(2.078, abs=0.001)
    et0 = et0_penman_monteith(21.5, 12.3, 84, 63, u2, rs, 187, 50.8, 100)
    assert et0 == pytest.approx(3.9, abs=0.05)


# ---- crop coefficients ------------------------------------------------------

def test_kc_curve_shape():
    c = MAIZE_GRAIN
    assert kc_on_day(c, 0) == c.kc_ini
    assert kc_on_day(c, c.l_ini - 1) == c.kc_ini
    assert kc_on_day(c, c.l_ini + c.l_dev - 1) == pytest.approx(c.kc_mid)
    assert kc_on_day(c, c.l_ini + c.l_dev + c.l_mid - 1) == pytest.approx(c.kc_mid)
    assert kc_on_day(c, c.season_length - 1) == pytest.approx(c.kc_end)
    assert kc_on_day(c, c.season_length) == 0.0
    assert kc_on_day(c, -1) == 0.0
    curve = [kc_on_day(c, d) for d in range(c.season_length)]
    rising = curve[c.l_ini - 1: c.l_ini + c.l_dev]
    assert all(b >= a for a, b in zip(rising, rising[1:]))


def test_kc_mid_adjustment():
    # Standard climate (u2 = 2 m/s, RHmin = 45 %): no change
    assert kc_mid_adjusted(MAIZE_GRAIN, 2.0, 45.0) == pytest.approx(MAIZE_GRAIN.kc_mid)
    # Drier and windier than standard: Kc_mid goes up
    assert kc_mid_adjusted(MAIZE_GRAIN, 3.0, 25.0) > MAIZE_GRAIN.kc_mid


def test_root_depth_and_p():
    assert root_depth_on_day(MAIZE_GRAIN, 0) == MAIZE_GRAIN.root_depth_min_m
    assert root_depth_on_day(MAIZE_GRAIN, 200) == MAIZE_GRAIN.root_depth_max_m
    assert depletion_fraction(MAIZE_GRAIN, 5.0) == pytest.approx(MAIZE_GRAIN.p_table)
    assert depletion_fraction(MAIZE_GRAIN, 50.0) == 0.1


# ---- water balance ------------------------------------------------------------

def _plot(policy=None):
    return Plot("T", "J3", 0.5, MAIZE_GRAIN, SANDY_LOAM, dt.date(2023, 11, 1),
                policy=policy or IrrigationPolicy())


def _run(plot, days, et0, rain, deliver_fraction=1.0):
    rows = []
    for i in range(days):
        d = plot.sowing_date + dt.timedelta(days=i)
        req = plot.start_day(d, et0[i], rain[i])
        rows.append(plot.finish_day(req * deliver_fraction))
    return pd.DataFrame([r.__dict__ for r in rows])


def test_water_balance_closes_every_day():
    rng = np.random.default_rng(0)
    n = MAIZE_GRAIN.season_length
    et0 = rng.uniform(3, 8, n)
    rain = np.where(rng.random(n) < 0.15, rng.uniform(1, 40, n), 0.0)
    df = _run(_plot(), n, et0, rain, deliver_fraction=0.7)
    storage_change = df["depletion_start_mm"] - df["depletion_end_mm"]
    inputs = df["rain_mm"] + df["irrigation_net_mm"] - df["eta_mm"] - df["deep_percolation_mm"]
    assert np.allclose(storage_change, inputs, atol=1e-9)


def test_dry_spell_lowers_soil_moisture():
    """Validation plan, check 3: zero rain and no irrigation dries the soil."""
    n = 60
    df = _run(_plot(), n, np.full(n, 6.0), np.zeros(n), deliver_fraction=0.0)
    assert df["soil_moisture"].is_monotonic_decreasing
    assert df["soil_moisture"].iloc[-1] < df["soil_moisture"].iloc[0]
    assert df["stressed"].any()
    assert (df["soil_moisture"] >= SANDY_LOAM.theta_wp - 1e-9).all()


def test_full_irrigation_avoids_stress():
    n = MAIZE_GRAIN.season_length
    df = _run(_plot(), n, np.full(n, 7.0), np.zeros(n), deliver_fraction=1.0)
    assert not df["stressed"].any()
    assert df["eta_mm"].sum() == pytest.approx(df["etc_mm"].sum())


def test_request_capped_and_triggered():
    policy = IrrigationPolicy(trigger_fraction=0.8, max_application_mm=20.0, efficiency=0.8)
    plot = _plot(policy)
    plot.depletion_mm = 30.0  # well past the trigger at sowing (RAW about 24 mm)
    req = plot.start_day(plot.sowing_date, 5.0, 0.0)
    assert req == pytest.approx(plot.mm_to_m3(20.0 / 0.8))
    rec = plot.finish_day(req)
    assert rec.irrigation_net_mm == pytest.approx(20.0)


def test_no_request_before_sowing():
    plot = _plot()
    assert plot.start_day(plot.sowing_date - dt.timedelta(days=1), 6.0, 0.0) == 0.0
    plot.finish_day(0.0)


def test_day_protocol_enforced():
    plot = _plot()
    plot.start_day(plot.sowing_date, 5.0, 0.0)
    with pytest.raises(RuntimeError):
        plot.start_day(plot.sowing_date, 5.0, 0.0)


# ---- weather handling ---------------------------------------------------------

def _payload():
    days = ["2023-11-01", "2023-11-02"]
    hours = [f"{d}T{h:02d}:00" for d in days for h in range(24)]
    return {
        "elevation": 350.0,
        "daily": {"time": days, "temperature_2m_max": [30.0, 31.0], "temperature_2m_min": [20.0, 19.0],
                  "precipitation_sum": [0.0, 5.0], "shortwave_radiation_sum": [20.0, 18.0],
                  "et0_fao_evapotranspiration": [4.5, 4.2]},
        "hourly": {"time": hours,
                   "relative_humidity_2m": [40 + (h % 24) for h in range(48)],
                   "wind_speed_10m": [3.0] * 48},
    }


def test_parse_open_meteo_payload():
    w = parse_open_meteo(_payload())
    assert list(w["date"]) == [dt.date(2023, 11, 1), dt.date(2023, 11, 2)]
    assert w["rh_max"].tolist() == [63, 63] and w["rh_min"].tolist() == [40, 40]
    assert w["u2_ms"].iloc[0] == pytest.approx(wind_speed_at_2m(3.0, 10.0))
    assert w["rain_mm"].tolist() == [0.0, 5.0]


def test_weather_csv_roundtrip(tmp_path):
    w = add_et0(parse_open_meteo(_payload()), ANANTAPUR, 350.0)
    path = tmp_path / "w.csv"
    save_weather(w, path, ANANTAPUR, 350.0)
    back, elev = load_weather(path)
    assert elev == 350.0
    assert back["date"].tolist() == w["date"].tolist()
    assert np.allclose(back["et0_mm"], w["et0_mm"], atol=1e-3)


def test_build_plots_matches_network():
    plots = build_plots()
    assert len(plots) == 4
    assert {p.junction for p in plots} == {"J3", "J4", "J6", "J7"}
