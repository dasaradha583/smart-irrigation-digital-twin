"""Stretch goal: irrigation scheduling under limited supply.

Two parts:

1. :class:`PriorityScheduler`: a daily rule for pump outages. With the pump
   down, the only water is what is left in the tank. The rule gives it to
   the plots closest to stress, weighted by how sensitive their growth
   stage is, instead of splitting it across every plot.

2. :func:`grid_search`: tries combinations of schedule settings (trigger
   level, daily application cap, irrigation window, with or without the
   priority rule) for a given scenario. It ranks them by seasonal relative
   yield (FAO-33), then by water delivered.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import pandas as pd

from .farm.plot import IrrigationPolicy, Plot
from .farm.setup import build_plots
from .twin import Operation, Scenario, run_season

#: Relative sensitivity to water stress by growth stage (maize is most
#: sensitive around flowering, i.e. mid-season; FAO-33 stage Ky values).
STAGE_WEIGHT = {"initial": 0.4, "development": 0.4, "mid-season": 1.5, "late season": 0.5}


@dataclass
class PriorityScheduler:
    """Allocate an estimated daily capacity to the most urgent plots.

    It rations only on days the pump is out of service. Supply is then a
    known volume, the water in the tank above its minimum level, which the
    operator reads from the tank gauge.

    It does not ration when supply is limited by pressure (a leak, a low
    well). An earlier version estimated capacity from yesterday's deliveries
    in those cases. Because a leak keeps pressure low whatever the demand,
    the estimate ratcheted down every day and starved the crop. The remedy
    for pressure limits is the irrigation window and trigger settings, which
    :func:`grid_search` tunes.
    """

    margin: float = 0.95
    tank_area_m2: float = 3.14159265 * 3.0 ** 2
    tank_min_level: float = 0.5

    def __call__(self, day: dt.date, requests: dict[str, float], plots: Sequence[Plot],
                 prev: dict | None, today: dict) -> dict[str, float]:
        total = sum(requests.values())
        if total == 0:
            return requests
        if not today["pump_outage"]:
            return requests
        capacity = max(today["tank_level_m"] - self.tank_min_level, 0.0) * self.tank_area_m2 * self.margin
        if total <= capacity:
            return requests

        by_name = {p.name: p for p in plots}

        def urgency(name: str) -> float:
            p = by_name[name]
            rec = p._today
            if rec is None or rec.taw_mm <= 0:
                return 0.0
            # how far past the trigger, as a share of the stress buffer, times stage weight
            return (rec.depletion_before_irrigation_mm / max(rec.raw_mm, 1e-6)) * STAGE_WEIGHT.get(rec.stage, 0.5)

        out = {name: 0.0 for name in requests}
        remaining = capacity
        for name in sorted((n for n, v in requests.items() if v > 0), key=urgency, reverse=True):
            if remaining <= 0:
                break
            give = min(requests[name], remaining)
            out[name] = give
            remaining -= give
        return out


@dataclass(frozen=True)
class Candidate:
    trigger_fraction: float
    max_application_mm: float
    window_hours: int
    priority: bool

    @property
    def label(self) -> str:
        return (f"trigger {self.trigger_fraction:.1f} RAW, cap {self.max_application_mm:.0f} mm, "
                f"{self.window_hours} h window, {'priority' if self.priority else 'independent'}")


def _evaluate(args) -> dict:
    cand, scenario, weather, start, end = args
    plots = build_plots(weather, IrrigationPolicy(trigger_fraction=cand.trigger_fraction,
                                                  max_application_mm=cand.max_application_mm))
    op = Operation(window_start_hour=max(0, 12 - cand.window_hours // 2), window_hours=cand.window_hours)
    res = run_season(plots, weather, scenario, start, end, operation=op,
                     scheduler=PriorityScheduler() if cand.priority else None)
    s = res.summary()
    return {"candidate": cand.label, **cand.__dict__, **s}


def grid_search(scenario: Scenario, weather: pd.DataFrame, start: dt.date, end: dt.date,
                triggers=(0.6, 0.8, 1.0), caps=(20.0,), windows=(12, 18), priority=(False, True),
                max_workers: int | None = None) -> pd.DataFrame:
    """Evaluate every combination; return them best first."""
    cands = [Candidate(t, c, w, p) for t, c, w, p in itertools.product(triggers, caps, windows, priority)]
    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        rows = list(ex.map(_evaluate, [(c, scenario, weather, start, end) for c in cands]))
    df = pd.DataFrame(rows)
    return df.sort_values(["mean_relative_yield", "min_relative_yield", "delivered_m3"],
                          ascending=[False, False, True]).reset_index(drop=True)
