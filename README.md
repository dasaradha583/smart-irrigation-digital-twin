# Smart Irrigation Digital Twin

One digital twin of a small irrigated farm and the water network that feeds it, so a pipe leak or pump failure can be traced through to crop water stress.

The project merges two models: **AquaTwin**, a water distribution network simulated in [WNTR](https://usepa.github.io/WNTR/), and **FarmTwin**, a daily soil-moisture balance for each farm plot driven by real weather. Each day they swap two quantities. Each plot's irrigation request becomes demand at a network junction, and the water the network can actually deliver at that pressure comes back as irrigation.

## Status

| Phase | Status |
|---|---|
| 1. Scope and study | — |
| 2. Water network model | **Done**: see [docs/phase2_water_network.md](docs/phase2_water_network.md) |
| 3. Farm model | Next |
| 4. Coupling | |
| 5. Scenarios and analysis | |
| 6. Dashboard (Streamlit) | |
| 7. Validation and report | |

## Quick start

```bash
pip install -r requirements.txt
```

```bash
python scripts/phase2_network.py
```

```bash
pytest
```

## Usage

```python
from irrigation_twin.network import (
    PLOT_JUNCTIONS, LeakSpec, build_farm_network,
    inject_leak, inject_pump_failure, run_hydraulics, set_irrigation_demands,
)

wn = build_farm_network()                                     # pressure-driven demand
set_irrigation_demands(wn, {p: 60.0 for p in PLOT_JUNCTIONS}) # m^3/day per plot

baseline = run_hydraulics(wn)
leak     = run_hydraulics(inject_leak(wn, LeakSpec("P3", hole_diameter_m=0.03)))
outage   = run_hydraulics(inject_pump_failure(wn))

print(leak.plot_delivery())   # requested vs delivered m^3 per plot
print(leak.summary())         # delivered, leaked, pumped, min pressure, tank level
```

## Layout

```
src/irrigation_twin/network/   water network model (phase 2)
  build.py       network layout and every parameter with its source
  demand.py      daily plot requests -> junction demands
  scenarios.py   leak and pump-failure injection
  simulate.py    run WNTR, delivery table, mass balance
scripts/         one script per phase deliverable
tests/           validation checks
docs/            phase write-ups
outputs/         generated tables and figures
```
