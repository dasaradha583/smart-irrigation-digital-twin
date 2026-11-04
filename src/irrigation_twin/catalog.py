"""Standard scenarios for the 2023-24 Rabi season, and saving/loading their results."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from .twin import LeakEvent, PumpOutage, Scenario, SeasonResult

#: Leak and outage dates fall in the peak-demand period, when every plot is
#: in or near mid-season (mid-January to February).
LEAK_START = dt.date(2024, 1, 25)
LEAK = dict(pipe="P3", hole_diameter_m=0.04, start=LEAK_START, start_hour=6)
OUTAGE = PumpOutage(dt.date(2024, 2, 1), dt.date(2024, 2, 10))
BURST_START = dt.date(2024, 2, 5)
BURST = LeakEvent("P6", 0.05, BURST_START, end=dt.date(2024, 2, 19), start_hour=6)

SCENARIOS: dict[str, Scenario] = {
    "baseline": Scenario(
        "baseline", "Normal operation all season."),
    "leak": Scenario(
        "leak", "4 cm hole in pipe P3 from 25 Jan, 06:00, never found or repaired.",
        leaks=(LeakEvent(**LEAK),)),
    "leak_repaired": Scenario(
        "leak_repaired", "Same leak, caught by the detector within hours and repaired the next day.",
        leaks=(LeakEvent(**LEAK, end=LEAK_START + dt.timedelta(days=1)),)),
    "pipe_burst": Scenario(
        "pipe_burst", "5 cm break in pipe P6 (the only supply to Plot D) on 5 Feb, 06:00. Not found "
                      "until the crop wilts; repaired on 19 Feb.",
        leaks=(BURST,)),
    "pump_failure": Scenario(
        "pump_failure", "Pump out of service 1-10 Feb while waiting for a spare part.",
        outages=(OUTAGE,)),
    "drought": Scenario(
        "drought", "Failed rains (rain x 0), 10 % higher evaporative demand, and the well "
                   "water level 6 m lower.",
        rain_factor=0.0, et0_factor=1.1, sump_drop_m=6.0),
    "drought_pump_failure": Scenario(
        "drought_pump_failure", "Drought plus the 1-10 Feb pump outage: the limited-supply "
                                "case for the schedule optimiser.",
        outages=(OUTAGE,), rain_factor=0.0, et0_factor=1.1, sump_drop_m=6.0),
}

TABLES = ("plots", "network", "pressure", "flow", "alerts")


def save_result(result: SeasonResult, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    result.plots.to_csv(folder / "plots.csv", index=False, float_format="%.5g")
    result.network.to_csv(folder / "network.csv", index=False, float_format="%.6g")
    result.pressure.round(3).to_csv(folder / "pressure.csv", index_label="time")
    result.flow.round(3).to_csv(folder / "flow.csv", index_label="time")
    result.alerts.to_csv(folder / "alerts.csv", index=False)
    meta = {"scenario": result.scenario.name, "description": result.scenario.description,
            "summary": result.summary(), **{k: str(v) for k, v in result.meta.items()}}
    (folder / "meta.json").write_text(json.dumps(meta, indent=2, default=float))


def load_result(folder: Path) -> dict:
    """Load a saved season as plain DataFrames (for the dashboard and report)."""
    out = {
        "plots": pd.read_csv(folder / "plots.csv", parse_dates=["date"]),
        "network": pd.read_csv(folder / "network.csv", parse_dates=["date"]),
        "pressure": pd.read_csv(folder / "pressure.csv", index_col="time", parse_dates=["time"]),
        "flow": pd.read_csv(folder / "flow.csv", index_col="time", parse_dates=["time"]),
        "alerts": pd.read_csv(folder / "alerts.csv", parse_dates=["date"]),
        "meta": json.loads((folder / "meta.json").read_text()),
    }
    return out
