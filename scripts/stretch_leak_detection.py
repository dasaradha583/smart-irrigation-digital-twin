"""Stretch goal: train and evaluate the leak detector on held-out leaks.

Run from the repository root::

    python scripts/stretch_leak_detection.py

Writes outputs/leak_detection/.
"""

from __future__ import annotations

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

from irrigation_twin.detection import (  # noqa: E402
    TEST_HOLES_M,
    TEST_PIPES,
    TRAIN_HOLES_M,
    TRAIN_PIPES,
    alarms,
    evaluate,
    feature_columns,
    make_cases,
    simulate_case,
    train_detector,
)

OUT = ROOT / "outputs" / "leak_detection"


def build(cases):
    with ProcessPoolExecutor() as ex:
        return pd.concat(list(ex.map(simulate_case, cases, chunksize=8)))


def threshold_baseline(df: pd.DataFrame, train: pd.DataFrame) -> np.ndarray:
    """Simple rule for comparison: pump-flow residual above 4 sigma of leak-free training hours."""
    sigma = train.loc[train["label"] == 0, "r_q_pump_3h"].std()
    tank_sigma = train.loc[train["label"] == 0, "r_tank_slope"].std()
    score = (df["r_q_pump_3h"] > 4 * sigma) | (df["r_tank_slope"] < -4 * tank_sigma)
    return score.to_numpy()


def per_day_alarms(df: pd.DataFrame, prob: np.ndarray, **kw) -> np.ndarray:
    out = np.zeros(len(df), dtype=int)
    pos = 0
    for _seed, day in df.groupby("seed", sort=False):
        n = len(day)
        out[pos:pos + n] = alarms(prob[pos:pos + n], **kw)
        pos += n
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    train_cases = make_cases(400, TRAIN_PIPES, TRAIN_HOLES_M, seed=1)
    test_cases = make_cases(250, TEST_PIPES, TEST_HOLES_M, seed=2)
    print(f"simulating {len(train_cases)} training days and {len(test_cases)} held-out test days ...")
    train = build(train_cases)
    test = build(test_cases)

    clf = train_detector(train)

    # Calibrate the alarm threshold and persistence on a validation set drawn
    # like the training data (training pipes and sizes). The held-out test
    # set is never used for tuning. Target: false alarms on at most 5 % of
    # leak-free days, then the highest detection rate.
    val = build(make_cases(200, TRAIN_PIPES, TRAIN_HOLES_M, seed=4))
    val["prob"] = clf.predict_proba(val[feature_columns(val)])[:, 1]
    rule_val = threshold_baseline(val, train).astype(float)
    best, best_rule = None, None
    for persistence in (1, 2, 3):
        for threshold in np.arange(0.3, 0.96, 0.05):
            m = evaluate(val.assign(alarm=per_day_alarms(val, val["prob"].to_numpy(), threshold=threshold,
                                                         persistence=persistence)))
            key = (m["false_alarm_day_rate"] <= 0.05, m["event_detection_rate"], -m["median_delay_h"])
            if best is None or key > best[0]:
                best = (key, float(threshold), persistence)
        m = evaluate(val.assign(alarm=per_day_alarms(val, rule_val, threshold=0.5, persistence=persistence)))
        key = (m["false_alarm_day_rate"] <= 0.05, m["event_detection_rate"], -m["median_delay_h"])
        if best_rule is None or key > best_rule[0]:
            best_rule = (key, persistence)
    _, threshold, persistence = best
    rule_persistence = best_rule[1]
    print(f"calibrated on validation set: threshold={threshold:.2f}, persistence={persistence} h; "
          f"rule persistence={rule_persistence} h")

    test = test.copy()
    test["prob"] = clf.predict_proba(test[feature_columns(test)])[:, 1]
    test["alarm"] = per_day_alarms(test, test["prob"].to_numpy(), threshold=threshold, persistence=persistence)
    test["alarm_rule"] = per_day_alarms(test, threshold_baseline(test, train).astype(float),
                                        threshold=0.5, persistence=rule_persistence)

    results = {}
    for name, col in [("random_forest", "alarm"), ("threshold_rule", "alarm_rule")]:
        m = evaluate(test, col)
        events = m.pop("events")
        by_size = events.groupby("hole_cm").agg(detection_rate=("detected", "mean"),
                                                median_delay_h=("delay_h", "median"),
                                                n=("detected", "size"))
        by_pipe = events.groupby("pipe").agg(detection_rate=("detected", "mean"),
                                             median_delay_h=("delay_h", "median"),
                                             n=("detected", "size"))
        m["by_hole_cm"] = by_size.round(3).reset_index().to_dict(orient="records")
        m["by_pipe"] = by_pipe.round(3).reset_index().to_dict(orient="records")
        results[name] = m
        by_size.to_csv(OUT / f"{name}_by_hole_size.csv")

    # In-distribution score, to show why held-out testing matters.
    in_dist_cases = make_cases(150, TRAIN_PIPES, TRAIN_HOLES_M, seed=3)
    in_dist = build(in_dist_cases)
    in_dist["prob"] = clf.predict_proba(in_dist[feature_columns(in_dist)])[:, 1]
    in_dist["alarm"] = per_day_alarms(in_dist, in_dist["prob"].to_numpy(), threshold=threshold,
                                      persistence=persistence)
    m = evaluate(in_dist)
    m.pop("events")
    results["random_forest_same_distribution"] = m

    importances = pd.Series(clf.feature_importances_, index=feature_columns(train)).sort_values(ascending=False)
    importances.round(4).to_csv(OUT / "feature_importance.csv", header=["importance"])
    results["calibration"] = {"threshold": threshold, "persistence_h": persistence,
                              "rule_persistence_h": rule_persistence,
                              "target": "false alarms on <= 5% of leak-free validation days"}
    (OUT / "metrics.json").write_text(json.dumps(results, indent=2, default=float))

    fig, ax = plt.subplots(figsize=(7, 4))
    rf = pd.read_csv(OUT / "random_forest_by_hole_size.csv")
    rule = pd.read_csv(OUT / "threshold_rule_by_hole_size.csv")
    w = 0.35
    x = np.arange(len(rf))
    ax.bar(x - w / 2, rf["detection_rate"] * 100, w, label="Random forest")
    ax.bar(x + w / 2, rule["detection_rate"] * 100, w, label="Threshold rule")
    ax.set_xticks(x, [f"{h:g} cm" for h in rf["hole_cm"]])
    ax.set_ylabel("Leak days detected (%)")
    ax.set_xlabel("Held-out leak hole size (pipes P3, P7 never seen in training)")
    ax.set_ylim(0, 105)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "detection_by_size.png", dpi=150)
    plt.close(fig)

    for name, m in results.items():
        print(f"\n{name}")
        for k, v in m.items():
            if not isinstance(v, (list, str)):
                print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")
    print(f"\nOutputs written to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
