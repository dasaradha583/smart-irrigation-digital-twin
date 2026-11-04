"""Build the farm water-distribution network as a WNTR model.

Layout (coordinates in metres, north is up)::

    R1 --PU1--> J1 ---P1--- J2 ---P2--- J3 ---P3--- J4
    (sump)    (pump         |  \\         |           (Plot B)
               outlet)      |   PT       P7
                            |    \\       |
                            P4    T1     |
                            |  (tower)   |
                            J5 ---P5--- J6 ---P6--- J7
                                       (Plot C)    (Plot D)

    Plot A is supplied from J3.

A sump/well (reservoir R1) feeds a single pump PU1. The pump fills an elevated
tank T1 that floats on the system at J2, and the network supplies four farm
plots. Pipe P7 closes a loop between the two branches, so water can reach a
plot by a second route when one pipe leaks.

Every number in this module is a project assumption. Each one is listed in
``NETWORK_PARAMETERS`` so the report can include the full parameter table.
All values are SI, matching WNTR: metres, m^3/s, seconds.
"""

from __future__ import annotations

from dataclasses import dataclass

import wntr

# --------------------------------------------------------------------------
# Network data
# --------------------------------------------------------------------------

#: Ground elevation of the farm (m). The farm is nearly flat.
JUNCTIONS: dict[str, dict] = {
    # name: elevation (m), coordinates (m)
    "J1": {"elevation": 10.0, "coordinates": (50.0, 0.0)},
    "J2": {"elevation": 10.5, "coordinates": (300.0, 0.0)},
    "J3": {"elevation": 11.0, "coordinates": (600.0, 0.0)},
    "J4": {"elevation": 12.0, "coordinates": (900.0, 0.0)},
    "J5": {"elevation": 10.5, "coordinates": (300.0, -300.0)},
    "J6": {"elevation": 11.5, "coordinates": (600.0, -300.0)},
    "J7": {"elevation": 12.5, "coordinates": (900.0, -300.0)},
}

#: Pipes: start node, end node, length (m), internal diameter (m),
#: Hazen-Williams C. PVC pipe, C = 140.
PIPES: dict[str, dict] = {
    "P1": {"start": "J1", "end": "J2", "length": 250.0, "diameter": 0.150, "roughness": 140.0},
    "P2": {"start": "J2", "end": "J3", "length": 300.0, "diameter": 0.125, "roughness": 140.0},
    "P3": {"start": "J3", "end": "J4", "length": 300.0, "diameter": 0.100, "roughness": 140.0},
    "P4": {"start": "J2", "end": "J5", "length": 300.0, "diameter": 0.125, "roughness": 140.0},
    "P5": {"start": "J5", "end": "J6", "length": 300.0, "diameter": 0.100, "roughness": 140.0},
    "P6": {"start": "J6", "end": "J7", "length": 300.0, "diameter": 0.100, "roughness": 140.0},
    "P7": {"start": "J3", "end": "J6", "length": 300.0, "diameter": 0.080, "roughness": 140.0},
    "PT": {"start": "J2", "end": "T1", "length": 100.0, "diameter": 0.150, "roughness": 140.0},
}

#: Sump/well: water surface fixed at 8 m.
RESERVOIR = {"name": "R1", "base_head": 8.0, "coordinates": (0.0, 0.0)}

#: Elevated tank on a 30 m tower: 6 m diameter, 0.5-5.0 m operating range
#: (about 127 m^3 when full).
TANK = {
    "name": "T1",
    "elevation": 30.0,
    "init_level": 3.0,
    "min_level": 0.5,
    "max_level": 5.0,
    "diameter": 6.0,
    "coordinates": (400.0, -100.0),
}

#: Single-point pump curve: 25 m head at 15 L/s. WNTR extends this to
#: shutoff head 1.33 x 25 m and maximum flow 2 x 15 L/s.
PUMP = {
    "name": "PU1",
    "start": "R1",
    "end": "J1",
    "curve_name": "PU1_CURVE",
    "design_flow": 0.015,
    "design_head": 25.0,
}

#: Pump control: switch on below the low tank level, off above the high level.
PUMP_ON_LEVEL = 2.0
PUMP_OFF_LEVEL = 4.5

#: Farm plots and the junction that supplies each one.
PLOT_JUNCTIONS: dict[str, str] = {
    "Plot_A": "J3",
    "Plot_B": "J4",
    "Plot_C": "J6",
    "Plot_D": "J7",
}

#: Pressure-driven demand settings. Above REQUIRED_PRESSURE a plot gets its
#: full request; below MINIMUM_PRESSURE it gets nothing; in between, delivery
#: follows WNTR's Wagner curve. 15 m (about 1.5 bar) suits sprinklers.
REQUIRED_PRESSURE = 15.0
MINIMUM_PRESSURE = 0.0
PRESSURE_EXPONENT = 0.5

#: Simulation clock: one day. Demand patterns change hourly. The hydraulics
#: run and report every minute so the solver catches the moment the tank
#: hits its minimum or maximum level. Hourly steps let the tank overshoot and
#: supply more water than it holds.
DURATION_S = 24 * 3600
PATTERN_TIMESTEP_S = 3600
HYDRAULIC_TIMESTEP_S = 60


@dataclass(frozen=True)
class Parameter:
    """One documented model parameter, for the report appendix."""

    name: str
    value: str
    unit: str
    source: str


NETWORK_PARAMETERS: list[Parameter] = [
    Parameter("Pipe material / Hazen-Williams C", "PVC / 140", "-", "Assumption; typical new PVC"),
    Parameter("Pipe diameters", "80-150", "mm", "Assumption; sized for < 1.5 m/s at peak demand"),
    Parameter("Pipe lengths", "100-300", "m", "Assumption; plot layout on a ~1 km x 0.3 km farm"),
    Parameter("Junction elevations", "10.0-12.5", "m", "Assumption; nearly flat farm"),
    Parameter("Sump water level (R1 head)", f"{RESERVOIR['base_head']}", "m", "Assumption"),
    Parameter("Pump design point", f"{PUMP['design_flow'] * 1000:.0f} L/s @ {PUMP['design_head']} m", "-",
              "Assumption; single-point curve"),
    Parameter("Pump on / off tank level", f"{PUMP_ON_LEVEL} / {PUMP_OFF_LEVEL}", "m", "Assumption"),
    Parameter("Tank base elevation", f"{TANK['elevation']}", "m", "Assumption; tower tank"),
    Parameter("Tank diameter", f"{TANK['diameter']}", "m", "Assumption"),
    Parameter("Tank min / init / max level",
              f"{TANK['min_level']} / {TANK['init_level']} / {TANK['max_level']}", "m", "Assumption"),
    Parameter("Required pressure (full delivery)", f"{REQUIRED_PRESSURE}", "m", "Assumption; sprinkler operating pressure"),
    Parameter("Minimum pressure (zero delivery)", f"{MINIMUM_PRESSURE}", "m", "WNTR default"),
    Parameter("Pressure exponent", f"{PRESSURE_EXPONENT}", "-", "WNTR default (orifice behaviour)"),
    Parameter("Hydraulic / pattern time step", f"{HYDRAULIC_TIMESTEP_S} / {PATTERN_TIMESTEP_S}", "s",
              "Project choice; 1 min keeps tank-limit error under 1 m^3 per event"),
    Parameter("Leak discharge coefficient", "0.75", "-", "WNTR default"),
]


def build_farm_network(demand_model: str = "PDD") -> wntr.network.WaterNetworkModel:
    """Return the farm water network with no irrigation demand yet.

    Parameters
    ----------
    demand_model:
        ``"PDD"`` (pressure-driven, the default the twin needs) or ``"DD"``
        (demand-driven, used only to cross-check against EPANET).
    """
    wn = wntr.network.WaterNetworkModel()
    wn.name = "farm_irrigation_network"

    wn.add_reservoir(RESERVOIR["name"], base_head=RESERVOIR["base_head"],
                     coordinates=RESERVOIR["coordinates"])

    for name, j in JUNCTIONS.items():
        wn.add_junction(name, base_demand=0.0, elevation=j["elevation"],
                        coordinates=j["coordinates"])

    wn.add_tank(TANK["name"], elevation=TANK["elevation"], init_level=TANK["init_level"],
                min_level=TANK["min_level"], max_level=TANK["max_level"],
                diameter=TANK["diameter"], coordinates=TANK["coordinates"])

    for name, p in PIPES.items():
        wn.add_pipe(name, p["start"], p["end"], length=p["length"],
                    diameter=p["diameter"], roughness=p["roughness"], minor_loss=0.0)

    wn.add_curve(PUMP["curve_name"], "HEAD", [(PUMP["design_flow"], PUMP["design_head"])])
    wn.add_pump(PUMP["name"], PUMP["start"], PUMP["end"], pump_type="HEAD",
                pump_parameter=PUMP["curve_name"])
    _add_pump_level_controls(wn)

    opts = wn.options
    opts.time.duration = DURATION_S
    opts.time.hydraulic_timestep = HYDRAULIC_TIMESTEP_S
    opts.time.pattern_timestep = PATTERN_TIMESTEP_S
    opts.time.report_timestep = HYDRAULIC_TIMESTEP_S
    opts.hydraulic.headloss = "H-W"
    opts.hydraulic.demand_model = demand_model
    opts.hydraulic.required_pressure = REQUIRED_PRESSURE
    opts.hydraulic.minimum_pressure = MINIMUM_PRESSURE
    opts.hydraulic.pressure_exponent = PRESSURE_EXPONENT
    return wn


def _add_pump_level_controls(wn: wntr.network.WaterNetworkModel) -> None:
    """Switch the pump on and off from the tank level."""
    from wntr.network.controls import Control, ControlAction, ValueCondition
    from wntr.network import LinkStatus

    pump = wn.get_link(PUMP["name"])
    tank = wn.get_node(TANK["name"])

    on = Control(ValueCondition(tank, "level", "<", PUMP_ON_LEVEL),
                 ControlAction(pump, "status", LinkStatus.Open))
    off = Control(ValueCondition(tank, "level", ">", PUMP_OFF_LEVEL),
                  ControlAction(pump, "status", LinkStatus.Closed))
    wn.add_control("PU1_on_low_tank", on)
    wn.add_control("PU1_off_high_tank", off)
