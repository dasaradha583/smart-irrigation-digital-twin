"""Turn daily irrigation requests into junction demands.

The farm model (phase 3) decides how much water each plot needs per day, as
a volume in m^3. This module spreads that volume evenly over the day's
irrigation window and sets it as the demand at the plot's junction. Phase 4
calls ``set_irrigation_demands`` once per simulated day.
"""

from __future__ import annotations

from collections.abc import Mapping

import wntr

from .build import DURATION_S, PATTERN_TIMESTEP_S, PLOT_JUNCTIONS

IRRIGATION_PATTERN = "IRRIGATION"

#: Default irrigation window: 06:00 to 12:00 (assumption, stated in the report).
DEFAULT_START_HOUR = 6
DEFAULT_DURATION_HOURS = 6


def irrigation_pattern(start_hour: int = DEFAULT_START_HOUR,
                       duration_hours: int = DEFAULT_DURATION_HOURS) -> list[float]:
    """Hourly multipliers: 1.0 inside the irrigation window, 0.0 outside."""
    steps = DURATION_S // PATTERN_TIMESTEP_S
    if not 0 <= start_hour < steps or duration_hours <= 0 or start_hour + duration_hours > steps:
        raise ValueError("irrigation window must fit inside one simulated day")
    return [1.0 if start_hour <= h < start_hour + duration_hours else 0.0 for h in range(steps)]


def set_irrigation_demands(wn: wntr.network.WaterNetworkModel,
                           daily_volume_m3: Mapping[str, float],
                           start_hour: int = DEFAULT_START_HOUR,
                           duration_hours: int = DEFAULT_DURATION_HOURS) -> None:
    """Set each plot's daily request as demand at its junction, in place.

    Parameters
    ----------
    wn:
        Network from :func:`build_farm_network`.
    daily_volume_m3:
        Requested water per plot for the day, keyed by plot name
        (``"Plot_A"``) or junction name (``"J3"``).
    start_hour, duration_hours:
        Irrigation window. The volume is spread evenly over it.
    """
    pattern = irrigation_pattern(start_hour, duration_hours)
    if IRRIGATION_PATTERN in wn.pattern_name_list:
        wn.get_pattern(IRRIGATION_PATTERN).multipliers = pattern
    else:
        wn.add_pattern(IRRIGATION_PATTERN, pattern)

    window_s = duration_hours * 3600
    for key, volume in daily_volume_m3.items():
        if volume < 0:
            raise ValueError(f"negative irrigation request for {key}: {volume}")
        junction_name = PLOT_JUNCTIONS.get(key, key)
        junction = wn.get_node(junction_name)
        junction.demand_timeseries_list.clear()
        junction.add_demand(base=volume / window_s, pattern_name=IRRIGATION_PATTERN,
                            category="irrigation")
