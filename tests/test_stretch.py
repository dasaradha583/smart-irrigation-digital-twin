"""Checks for the stretch-goal modules and result storage."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from irrigation_twin.catalog import load_result, save_result
from irrigation_twin.detection import DayCase, alarms, evaluate, simulate_case
from irrigation_twin.farm import build_plots
from irrigation_twin.optimise import PriorityScheduler
from irrigation_twin.twin import Scenario, run_season


# ---- priority scheduler -------------------------------------------------------

def _plots_with_state(day):
    plots = build_plots()
    depletion = {"Plot_A": 80.0, "Plot_B": 40.0, "Plot_C": 70.0, "Plot_D": 30.0}
    reqs = {}
    for p in plots:
        p.depletion_mm = depletion[p.name]
        reqs[p.name] = p.start_day(day, 7.0, 0.0)
    return plots, reqs


def test_scheduler_passes_through_without_outage():
    day = dt.date(2024, 2, 1)
    plots, reqs = _plots_with_state(day)
    out = PriorityScheduler()(day, dict(reqs), plots, None, {"pump_outage": False, "tank_level_m": 3.0})
    assert out == reqs


def test_scheduler_rations_tank_water_during_outage():
    day = dt.date(2024, 2, 1)
    plots, reqs = _plots_with_state(day)
    sched = PriorityScheduler()
    out = sched(day, dict(reqs), plots, None, {"pump_outage": True, "tank_level_m": 2.0})
    capacity = (2.0 - sched.tank_min_level) * sched.tank_area_m2 * sched.margin
    assert sum(out.values()) <= capacity + 1e-9
    assert sum(out.values()) < sum(reqs.values())
    served = [k for k, v in out.items() if v > 0]
    assert served, "someone must get the tank water"
    # The most depleted plot (A, 80 mm in mid-season) is served first.
    assert out["Plot_A"] == pytest.approx(min(reqs["Plot_A"], capacity))


# ---- leak detection -----------------------------------------------------------

REQ = (80.0, 60.0, 0.0, 100.0)


def test_simulate_case_labels_and_signal():
    leak = simulate_case(DayCase(seed=11, requests_m3=REQ, tank_level=3.0, leak_pipe="P3",
                                 leak_hole_m=0.03, leak_start_hour=8))
    clean = simulate_case(DayCase(seed=11, requests_m3=REQ, tank_level=3.0, leak_pipe=None,
                                  leak_hole_m=0.0, leak_start_hour=0))
    assert len(leak) == 24 and len(clean) == 24
    assert leak.loc[leak.index <= 8, "label"].sum() == 0
    assert leak.loc[leak.index > 8, "label"].all()
    assert clean["label"].sum() == 0
    # The leak draws extra water: pump flow rises or the tank falls against the prediction.
    after = leak.index > 9
    signal = leak.loc[after, "r_q_pump"].mean() - 10 * leak.loc[after, "r_tank_slope"].mean()
    assert signal > clean.loc[after, "r_q_pump"].abs().mean() + 1.0


def test_alarm_persistence():
    prob = np.array([0.9, 0.1, 0.9, 0.9, 0.9, 0.2])
    assert alarms(prob, 0.5, 1).tolist() == [1, 0, 1, 1, 1, 0]
    assert alarms(prob, 0.5, 2).tolist() == [0, 0, 0, 1, 1, 0]


def test_evaluate_counts_delay_and_false_alarms():
    rows = []
    for h in range(24):  # leak day starting at hour 5, detected at hour 8
        rows.append({"seed": 1, "label": int(h > 5), "alarm": int(h >= 8), "leak_pipe": "P3",
                     "leak_hole_cm": 2.0, "leak_start_hour": 5})
    for h in range(24):  # leak-free day with one false alarm
        rows.append({"seed": 2, "label": 0, "alarm": int(h == 3), "leak_pipe": "none",
                     "leak_hole_cm": 0.0, "leak_start_hour": 0})
    df = pd.DataFrame(rows, index=list(range(24)) * 2)
    m = evaluate(df)
    assert m["event_detection_rate"] == 1.0
    assert m["median_delay_h"] == 3.0
    assert m["false_alarm_day_rate"] == 1.0


# ---- result storage -------------------------------------------------------------

def test_save_and_load_roundtrip(tmp_path):
    start = dt.date(2024, 1, 20)
    end = start + dt.timedelta(days=1)
    wx = pd.DataFrame({"date": pd.date_range(start, end).date, "et0_mm": 7.0, "rain_mm": 0.0})
    plots = build_plots()
    for p in plots:
        p.depletion_mm = 60.0
    res = run_season(plots, wx, Scenario("baseline", "test"), start, end)
    save_result(res, tmp_path / "baseline")
    back = load_result(tmp_path / "baseline")
    assert len(back["plots"]) == len(res.plots)
    assert len(back["pressure"]) == 48
    assert back["meta"]["summary"]["delivered_m3"] == pytest.approx(res.summary()["delivered_m3"])
