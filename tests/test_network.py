"""Phase 2 checks for the water network model.

These follow the validation plan: an independent hydraulic check against
EPANET, mass balance, and the coupling sanity rules (no leak means full
delivery; a bigger leak never delivers more).
"""

import copy

import pytest
import wntr

from irrigation_twin.network import (
    PLOT_JUNCTIONS,
    LeakSpec,
    build_farm_network,
    inject_leak,
    inject_pump_failure,
    irrigation_pattern,
    run_hydraulics,
    set_irrigation_demands,
)
from irrigation_twin.network.build import HYDRAULIC_TIMESTEP_S, REQUIRED_PRESSURE

DAILY_REQUEST_M3 = 60.0


@pytest.fixture
def network():
    wn = build_farm_network()
    set_irrigation_demands(wn, {plot: DAILY_REQUEST_M3 for plot in PLOT_JUNCTIONS})
    return wn


@pytest.fixture
def baseline(network):
    return run_hydraulics(network)


# ---- structure -----------------------------------------------------------

def test_network_matches_scope(network):
    """Scope: 1 tank, 1 pump, 5-8 junctions, 3-5 plots."""
    assert network.num_tanks == 1
    assert network.num_pumps == 1
    assert 5 <= network.num_junctions <= 8
    assert 3 <= len(PLOT_JUNCTIONS) <= 5
    for junction in PLOT_JUNCTIONS.values():
        assert junction in network.junction_name_list


def test_irrigation_pattern_window():
    pattern = irrigation_pattern(6, 6)
    assert len(pattern) == 24
    assert sum(pattern) == 6
    assert pattern[5] == 0 and pattern[6] == 1 and pattern[11] == 1 and pattern[12] == 0
    with pytest.raises(ValueError):
        irrigation_pattern(20, 6)


def test_requested_volume_matches_input(baseline):
    delivery = baseline.plot_delivery()
    assert delivery["requested_m3"].tolist() == pytest.approx([DAILY_REQUEST_M3] * len(PLOT_JUNCTIONS))


def test_rerunning_same_model_gives_same_result(network):
    """A run must not change the model it was given."""
    first = run_hydraulics(network).summary()
    second = run_hydraulics(network).summary()
    assert first == pytest.approx(second)


# ---- validation check 1: hydraulics ----------------------------------------

def test_wntr_matches_epanet_demand_driven(tmp_path):
    """Independent check: WNTR's solver agrees with the EPANET engine."""
    wn = build_farm_network(demand_model="DD")
    set_irrigation_demands(wn, {plot: DAILY_REQUEST_M3 for plot in PLOT_JUNCTIONS})
    epanet = wntr.sim.EpanetSimulator(copy.deepcopy(wn)).run_sim(file_prefix=str(tmp_path / "epanet"))
    ours = run_hydraulics(wn).results

    p_epa, p_ours = epanet.node["pressure"], ours.node["pressure"]
    common = p_epa.index.intersection(p_ours.index)
    assert len(common) == len(p_epa)
    assert (p_epa.loc[common] - p_ours.loc[common]).abs().max().max() < 0.01  # m
    q_epa, q_ours = epanet.link["flowrate"], ours.link["flowrate"]
    assert (q_epa.loc[common] - q_ours.loc[common]).abs().max().max() < 1e-4  # m^3/s


@pytest.mark.parametrize("scenario", ["baseline", "leak", "pump_failure"])
def test_mass_balance_closes(network, scenario):
    """Source supply = deliveries + leak + tank inflow, at every step."""
    wn = {
        "baseline": network,
        "leak": inject_leak(network, LeakSpec("P3", 0.03)),
        "pump_failure": inject_pump_failure(network),
    }[scenario]
    run = run_hydraulics(wn)
    assert run.mass_balance()["residual"].abs().max() < 1e-8
    # Tank storage error is bounded by one step of flow at a level limit.
    max_step_volume = 0.03 * HYDRAULIC_TIMESTEP_S  # pump maximum flow x step
    assert run.tank_storage_error_m3() < max_step_volume


# ---- validation check 3: coupling sanity -----------------------------------

def test_no_leak_delivers_full_request(baseline):
    delivery = baseline.plot_delivery()
    assert delivery["delivered_fraction"].min() > 0.999
    assert baseline.summary()["leak_m3"] == 0.0


def test_baseline_pressure_above_required(baseline):
    plot_pressure = baseline.pressure[list(PLOT_JUNCTIONS.values())]
    assert plot_pressure.min().min() >= REQUIRED_PRESSURE


def test_larger_leak_never_delivers_more(network):
    delivered = []
    leaked = []
    for hole in [0.0, 0.01, 0.02, 0.03, 0.04]:
        wn = network if hole == 0 else inject_leak(network, LeakSpec("P3", hole))
        summary = run_hydraulics(wn).summary()
        delivered.append(summary["delivered_m3"])
        leaked.append(summary["leak_m3"])
    assert all(b <= a + 1e-6 for a, b in zip(delivered, delivered[1:]))
    assert all(b > a for a, b in zip(leaked, leaked[1:]))
    assert delivered[-1] < delivered[0]  # the big leak visibly hurts the farm


def test_leak_hurts_downstream_plot_most(network):
    """A leak on P3 (J3 -> J4) hits Plot B, at the end of that pipe, hardest."""
    delivery = run_hydraulics(inject_leak(network, LeakSpec("P3", 0.04))).plot_delivery()
    assert delivery["delivered_fraction"].idxmin() == "Plot_B"


def test_leak_injection_does_not_modify_input(network):
    pipes_before = sorted(network.pipe_name_list)
    inject_leak(network, LeakSpec("P2", 0.02))
    assert sorted(network.pipe_name_list) == pipes_before


def test_leak_spec_validation(network):
    with pytest.raises(ValueError):
        inject_leak(network, LeakSpec("P2", 0.0))
    with pytest.raises(ValueError):
        inject_leak(network, LeakSpec("P2", 0.02, position=1.0))
    with pytest.raises(KeyError):
        inject_leak(network, LeakSpec("NOPE", 0.02))


def test_leak_starting_midday(network):
    leak = LeakSpec("P2", 0.03, start_time_s=9 * 3600)
    run = run_hydraulics(inject_leak(network, leak))
    leak_flow = run.leak[leak.junction_name]
    assert leak_flow.loc[: 9 * 3600 - 1].abs().max() == 0.0
    assert leak_flow.loc[9 * 3600 + 60:].min() > 0.0


# ---- pump failure ---------------------------------------------------------

def test_pump_failure_limited_to_tank_storage(network):
    """With the pump off all day, the plots can only get what the tank holds."""
    run = run_hydraulics(inject_pump_failure(network))
    summary = run.summary()
    tank = run.wn.get_node("T1")
    usable_m3 = (tank.init_level - tank.min_level) * 3.141592653589793 * (tank.diameter / 2) ** 2
    assert summary["pumped_m3"] == 0.0
    assert summary["delivered_m3"] <= usable_m3 + 0.03 * HYDRAULIC_TIMESTEP_S
    assert summary["delivered_m3"] < 0.5 * summary["requested_m3"]


def test_timed_pump_outage_recovers(network):
    run = run_hydraulics(inject_pump_failure(network, start_time_s=6 * 3600, end_time_s=10 * 3600))
    pump_flow = run.flow["PU1"]
    assert pump_flow.loc[6 * 3600 + 60: 10 * 3600 - 60].abs().max() == 0.0
    assert pump_flow.loc[10 * 3600:].max() > 0.0
    baseline = run_hydraulics(network).summary()["delivered_m3"]
    assert run.summary()["delivered_m3"] < baseline


def test_pump_outage_rejects_bad_window(network):
    with pytest.raises(ValueError):
        inject_pump_failure(network, start_time_s=5, end_time_s=5)
