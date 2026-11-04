"""Scenario injection: pipe leaks and pump failures.

Each function returns a modified copy and leaves the input network as it was,
so one baseline network can be reused for every scenario.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass

import wntr
from wntr.network import LinkStatus
from wntr.network.controls import (
    AndCondition,
    ControlAction,
    OrCondition,
    Rule,
    SimTimeCondition,
)

LEAK_DISCHARGE_COEFF = 0.75


@dataclass(frozen=True)
class LeakSpec:
    """A leak at a point along a pipe.

    ``hole_diameter_m`` is the diameter of an equivalent circular orifice.
    Leak flow follows the orifice equation
    ``Q = Cd * A * sqrt(2 g p)``, so it rises with the local pressure.
    """

    pipe: str
    hole_diameter_m: float
    start_time_s: int = 0
    end_time_s: int | None = None
    position: float = 0.5  # fraction of the pipe length from its start node

    @property
    def area_m2(self) -> float:
        return math.pi * (self.hole_diameter_m / 2.0) ** 2

    @property
    def junction_name(self) -> str:
        return f"{self.pipe}_leak"


def inject_leak(wn: wntr.network.WaterNetworkModel, leak: LeakSpec) -> wntr.network.WaterNetworkModel:
    """Return a copy of ``wn`` with a leak partway along ``leak.pipe``.

    The pipe is split at ``leak.position`` and the leak is attached to the new
    junction. Its flow appears in ``results.node["leak_demand"]`` under
    ``leak.junction_name``. Leaks need ``wntr.sim.WNTRSimulator``, which
    :func:`irrigation_twin.network.simulate.run_hydraulics` uses.
    """
    if leak.hole_diameter_m <= 0:
        raise ValueError("leak hole diameter must be positive")
    if not 0.0 < leak.position < 1.0:
        raise ValueError("leak position must be strictly between 0 and 1")
    if leak.pipe not in wn.pipe_name_list:
        raise KeyError(f"unknown pipe {leak.pipe!r}")

    leaky = wntr.morph.split_pipe(wn, leak.pipe, f"{leak.pipe}_B", leak.junction_name,
                                  split_at_point=leak.position, return_copy=True)
    node = leaky.get_node(leak.junction_name)
    node.add_leak(leaky, area=leak.area_m2, discharge_coeff=LEAK_DISCHARGE_COEFF,
                  start_time=leak.start_time_s, end_time=leak.end_time_s)
    return leaky


def inject_pump_failure(wn: wntr.network.WaterNetworkModel, pump: str = "PU1",
                        start_time_s: int = 0,
                        end_time_s: int | None = None) -> wntr.network.WaterNetworkModel:
    """Return a copy of ``wn`` with ``pump`` forced off from ``start_time_s``.

    If ``end_time_s`` is given, the pump's normal tank-level controls take
    over again after the outage. Otherwise the pump stays off until the end
    of the run.

    The pump's existing controls are suspended during the outage instead of
    being left to compete with it. When a "switch on at low level" control
    and an outage rule fight over the same pump, WNTRSimulator stops
    updating the tank level correctly and the tank appears to supply water it
    does not hold.
    """
    if end_time_s is not None and end_time_s <= start_time_s:
        raise ValueError("pump outage must end after it starts")
    failed = copy.deepcopy(wn)
    link = failed.get_link(pump)

    in_outage = SimTimeCondition(failed, ">=", start_time_s)
    if end_time_s is not None:
        in_outage = AndCondition(in_outage, SimTimeCondition(failed, "<", end_time_s))

    for name, control in list(failed.controls()):
        if not any(action.target()[0] is link for action in control.actions()):
            continue
        failed.remove_control(name)
        if end_time_s is None and start_time_s <= 0:
            continue  # the pump never runs again, so the control is dead
        outside = OrCondition(SimTimeCondition(failed, "<", start_time_s),
                              SimTimeCondition(failed, ">=", end_time_s)) if end_time_s is not None \
            else SimTimeCondition(failed, "<", start_time_s)
        failed.add_control(name, Rule(AndCondition(control.condition, outside),
                                      list(control.actions()), priority=control.priority))

    failed.add_control(f"{pump}_outage",
                       Rule(in_outage, [ControlAction(link, "status", LinkStatus.Closed)], priority=6))
    if start_time_s <= 0:
        link.initial_status = LinkStatus.Closed
    return failed
