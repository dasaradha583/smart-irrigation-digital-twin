"""Phase 4 coupling checks (validation plan, check 3).

Short runs start in mid-season with the soil already near the irrigation
trigger, so every plot asks for water within a few days. The weather is a
constant test input, not real data.
"""

import datetime as dt

import pandas as pd
import pytest

from irrigation_twin.farm import build_plots
from irrigation_twin.twin import LeakEvent, PumpOutage, Scenario, run_season

START = dt.date(2024, 1, 20)   # every plot is in mid-season
DAYS = 6
END = START + dt.timedelta(days=DAYS - 1)


def _weather():
    return pd.DataFrame({"date": pd.date_range(START, END).date, "et0_mm": 7.0, "rain_mm": 0.0})


def _plots():
    plots = build_plots()
    for p in plots:
        p.depletion_mm = 60.0  # close to the trigger for a 1 m root zone
    return plots


def _run(scenario):
    return run_season(_plots(), _weather(), scenario, START, END)


@pytest.fixture(scope="module")
def baseline():
    return _run(Scenario("baseline"))


def test_no_leak_delivers_everything(baseline):
    df = baseline.plots
    assert df["request_m3"].sum() > 0
    assert (df["delivered_m3"] - df["request_m3"]).abs().max() < 1e-6
    assert baseline.alerts.empty or "delivery shortfall" not in set(baseline.alerts["kind"])


def test_network_state_carries_over(baseline):
    net = baseline.network
    assert (net["tank_start_m"].iloc[1:].values == pytest.approx(net["tank_end_m"].iloc[:-1].values, abs=1e-9))


def test_mass_balance_every_day(baseline):
    assert baseline.network["mass_balance_residual"].max() < 1e-8


def test_larger_leak_never_delivers_more():
    delivered = []
    for hole in [0.02, 0.035, 0.05]:
        r = _run(Scenario(f"leak {hole}", leaks=(LeakEvent("P3", hole, START),)))
        delivered.append(r.plots["delivered_m3"].sum())
    assert delivered[0] >= delivered[1] >= delivered[2]
    assert delivered[2] < delivered[0]


def test_leak_lowers_soil_moisture_downstream(baseline):
    """The core chain: leak -> low pressure -> less water -> drier soil."""
    leak = _run(Scenario("leak", leaks=(LeakEvent("P3", 0.05, START),)))
    sm_base = baseline.plots.groupby("plot")["soil_moisture"].last()
    sm_leak = leak.plots.groupby("plot")["soil_moisture"].last()
    assert sm_leak["Plot_B"] < sm_base["Plot_B"] - 1e-3
    assert "low pressure" in set(leak.alerts["kind"])


def test_pump_outage_stops_pump_and_recovers():
    outage_end = START + dt.timedelta(days=2)
    r = _run(Scenario("outage", outages=(PumpOutage(START, outage_end),)))
    net = r.network.set_index("date")
    assert net.loc[START:outage_end, "pumped_m3"].max() == 0.0
    assert net.loc[outage_end + dt.timedelta(days=1):, "pumped_m3"].sum() > 0.0
    assert r.plots["delivered_m3"].sum() < r.plots["request_m3"].sum()


def test_drought_sump_drop_reduces_supply(baseline):
    r = _run(Scenario("dry sump", sump_drop_m=6.0))
    assert r.plots["delivered_m3"].sum() < baseline.plots["delivered_m3"].sum()


def test_missing_weather_rejected():
    with pytest.raises(ValueError):
        run_season(_plots(), _weather().iloc[:-1], Scenario("x"), START, END)
