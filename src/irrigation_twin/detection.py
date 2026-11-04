"""Stretch goal: detect leaks from pressure and flow anomalies.

Model-based residual approach: each hour, compare what the sensors measure
with what the twin predicts for a leak-free network, and classify the
residuals.

- "Measured": a simulation with the leak (if any), the plots' *actual* water
  use (the nominal schedule plus +/-5 % random error, because real use never
  matches the plan exactly), and sensor noise.
- "Predicted": the twin's simulation of the same day with the nominal
  schedule and no leak.

Sensors (a plausible farm setup): pressure at J2 and at the four plot
junctions (+/-0.05 m noise), a pump flow meter (+/-1 % noise), and a tank
level gauge (+/-0.01 m noise).

Per the validation plan, the detector is tested on leaks it never saw in
training: different pipes and different hole sizes. It is scored on
precision, recall and detection delay, not accuracy alone.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from .network.build import PLOT_JUNCTIONS, PUMP, TANK, build_farm_network
from .network.demand import set_irrigation_demands
from .network.scenarios import LeakSpec, inject_leak
from .network.simulate import run_hydraulics

PRESSURE_SENSORS = ["J2", *PLOT_JUNCTIONS.values()]
PRESSURE_NOISE_M = 0.05
FLOW_NOISE_FRAC = 0.01
LEVEL_NOISE_M = 0.01
DEMAND_ERROR_FRAC = 0.05
DETECTION_TIMESTEP_S = 300  # 5-minute hydraulics are enough for hourly sensor data

TRAIN_PIPES = ("P1", "P2", "P4", "P5", "P6")
TEST_PIPES = ("P3", "P7")
TRAIN_HOLES_M = (0.010, 0.020, 0.030)
TEST_HOLES_M = (0.008, 0.015, 0.025, 0.040)


@dataclass(frozen=True)
class DayCase:
    seed: int
    requests_m3: tuple[float, ...]
    tank_level: float
    leak_pipe: str | None
    leak_hole_m: float
    leak_start_hour: int


def _network(requests, tank_level):
    wn = build_farm_network()
    wn.options.time.hydraulic_timestep = DETECTION_TIMESTEP_S
    wn.options.time.report_timestep = DETECTION_TIMESTEP_S
    wn.get_node(TANK["name"]).init_level = tank_level
    set_irrigation_demands(wn, dict(zip(PLOT_JUNCTIONS.values(), requests)), 6, 12)
    return wn


def _hourly_sensors(run) -> pd.DataFrame:
    idx = run.pressure.index % 3600 == 0
    p = run.pressure.loc[idx, PRESSURE_SENSORS].iloc[:-1]
    out = p.add_prefix("p_")
    out["q_pump"] = run.flow.loc[idx, PUMP["name"]].iloc[:-1].values * 1000.0
    out["tank"] = run.tank_level.loc[idx].iloc[:-1].values
    out.index = (out.index // 3600).astype(int)
    return out


def simulate_case(case: DayCase) -> pd.DataFrame:
    """Hourly residual features and labels for one simulated day."""
    rng = np.random.default_rng(case.seed)
    nominal = np.array(case.requests_m3)
    actual = nominal * (1.0 + rng.normal(0.0, DEMAND_ERROR_FRAC, nominal.size))

    truth_wn = _network(actual, case.tank_level)
    if case.leak_pipe is not None:
        truth_wn = inject_leak(truth_wn, LeakSpec(case.leak_pipe, case.leak_hole_m,
                                                  start_time_s=case.leak_start_hour * 3600))
    measured = _hourly_sensors(run_hydraulics(truth_wn))
    predicted = _hourly_sensors(run_hydraulics(_network(nominal, case.tank_level)))

    noise = pd.DataFrame(0.0, index=measured.index, columns=measured.columns)
    for c in measured.columns:
        if c.startswith("p_"):
            noise[c] = rng.normal(0.0, PRESSURE_NOISE_M, len(noise))
        elif c == "q_pump":
            noise[c] = rng.normal(0.0, FLOW_NOISE_FRAC, len(noise)) * measured[c].abs()
        else:
            noise[c] = rng.normal(0.0, LEVEL_NOISE_M, len(noise))
    resid = (measured + noise) - predicted

    feats = resid.add_prefix("r_")
    # Short rolling means suppress noise. The tank residual trend captures
    # water going missing even when the pump hides the pressure drop.
    feats["r_p_mean"] = resid[[f"p_{s}" for s in PRESSURE_SENSORS]].mean(axis=1)
    feats["r_p_mean_3h"] = feats["r_p_mean"].rolling(3, min_periods=1).mean()
    feats["r_q_pump_3h"] = feats["r_q_pump"].rolling(3, min_periods=1).mean()
    feats["r_tank_slope"] = resid["tank"].diff().fillna(0.0)
    feats["hour"] = feats.index
    feats["irrigating"] = ((feats.index >= 6) & (feats.index < 18)).astype(int)
    feats["label"] = 0
    if case.leak_pipe is not None:
        # sensors read on the hour; the reading at hour h reflects the leak once h > start
        feats.loc[feats.index > case.leak_start_hour, "label"] = 1
    feats["seed"] = case.seed
    feats["leak_pipe"] = case.leak_pipe or "none"
    feats["leak_hole_cm"] = case.leak_hole_m * 100
    feats["leak_start_hour"] = case.leak_start_hour
    return feats


def make_cases(n: int, pipes, holes, seed: int, leak_fraction: float = 0.6) -> list[DayCase]:
    rng = np.random.default_rng(seed)
    cases = []
    for i in range(n):
        requests = tuple(float(rng.uniform(30, 125)) if rng.random() < 0.6 else 0.0
                         for _ in PLOT_JUNCTIONS)
        leak = rng.random() < leak_fraction
        cases.append(DayCase(
            seed=int(seed * 100_000 + i),
            requests_m3=requests,
            tank_level=float(rng.uniform(1.5, 4.5)),
            leak_pipe=str(rng.choice(pipes)) if leak else None,
            leak_hole_m=float(rng.choice(holes)) if leak else 0.0,
            leak_start_hour=int(rng.integers(1, 21)) if leak else 0,
        ))
    return cases



def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("r_") or c in ("hour", "irrigating")]


def train_detector(train: pd.DataFrame, seed: int = 0) -> RandomForestClassifier:
    clf = RandomForestClassifier(n_estimators=300, min_samples_leaf=3, class_weight="balanced",
                                 random_state=seed, n_jobs=-1)
    clf.fit(train[feature_columns(train)], train["label"])
    return clf


def alarms(prob: np.ndarray, threshold: float = 0.5, persistence: int = 2) -> np.ndarray:
    """Raise an alarm only after ``persistence`` consecutive positive hours."""
    pos = prob >= threshold
    out = np.zeros_like(pos)
    run = 0
    for i, p in enumerate(pos):
        run = run + 1 if p else 0
        out[i] = run >= persistence
    return out


def evaluate(test: pd.DataFrame, alarm_col: str = "alarm") -> dict:
    """Hourly precision/recall, event detection rate, delay and false-alarm days."""
    tp = int(((test[alarm_col] == 1) & (test["label"] == 1)).sum())
    fp = int(((test[alarm_col] == 1) & (test["label"] == 0)).sum())
    fn = int(((test[alarm_col] == 0) & (test["label"] == 1)).sum())
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")

    events = []
    false_alarm_days = 0
    no_leak_days = 0
    for _seed, day in test.groupby("seed"):
        if day["leak_pipe"].iloc[0] == "none":
            no_leak_days += 1
            false_alarm_days += int(day[alarm_col].any())
            continue
        start = int(day["leak_start_hour"].iloc[0])
        hits = day.index[(day[alarm_col] == 1) & (day.index > start)]
        pre = bool(day.loc[day.index <= start, alarm_col].any())
        events.append({"pipe": day["leak_pipe"].iloc[0], "hole_cm": day["leak_hole_cm"].iloc[0],
                       "detected": len(hits) > 0, "delay_h": (int(hits[0]) - start) if len(hits) else np.nan,
                       "false_alarm_before_leak": pre,
                       "hours_observed": int((day.index > start).sum())})
    ev = pd.DataFrame(events)
    return {
        "hourly_precision": precision,
        "hourly_recall": recall,
        "event_detection_rate": float(ev["detected"].mean()) if len(ev) else float("nan"),
        "median_delay_h": float(ev["delay_h"].median()) if len(ev) else float("nan"),
        "false_alarm_day_rate": false_alarm_days / no_leak_days if no_leak_days else float("nan"),
        "n_leak_days": int(len(ev)),
        "n_no_leak_days": int(no_leak_days),
        "events": ev,
    }
