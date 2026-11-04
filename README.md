# Smart Irrigation Digital Twin

One digital twin of a small irrigated farm and the water network that feeds it, so a pipe leak or pump failure can be traced through to crop water stress.

The project merges two models: **AquaTwin**, a water distribution network simulated in [WNTR](https://usepa.github.io/WNTR/) with pressure-driven demand, and **FarmTwin**, a FAO-56 daily soil-water balance for four maize plots in Anantapur, India, driven by real weather. Each day they swap two quantities. Each plot's irrigation request becomes demand at its network junction, and the water the network can actually deliver at that pressure comes back as irrigation.

**Final report:** [docs/report.md](docs/report.md)

## Status

| Phase | Status | Write-up |
|---|---|---|
| 1. Scope and study | Done | [docs/phase1_scope.md](docs/phase1_scope.md) |
| 2. Water network model | Done | [docs/phase2_water_network.md](docs/phase2_water_network.md) |
| 3. Farm model | Done | [docs/report.md](docs/report.md) §3 |
| 4. Coupling | Done | [docs/report.md](docs/report.md) §4 |
| 5. Scenarios and analysis | Done | [docs/report.md](docs/report.md) §5 |
| 6. Dashboard (Streamlit) | Done | `app/dashboard.py` |
| 7. Validation and report | Done | [docs/report.md](docs/report.md) |
| Stretch: leak detection | Done | [docs/stretch_leak_detection.md](docs/stretch_leak_detection.md) |
| Stretch: schedule optimiser, drought, water saved | Done | [docs/report.md](docs/report.md) §6 |

## Quick start

```bash
pip install -r requirements.txt
```

Download the season's weather once (needs access to `archive-api.open-meteo.com`):

```bash
python scripts/fetch_weather.py
```

Run every season scenario, the optimiser grid and all figures (about 15 minutes on 4 cores):

```bash
python scripts/run_season_analysis.py
```

Leak-detection stretch goal (about 2 minutes):

```bash
python scripts/stretch_leak_detection.py
```

Open the dashboard:

```bash
streamlit run app/dashboard.py
```

Run the validation tests:

```bash
pytest
```

## Usage from Python

```python
import datetime as dt
from pathlib import Path
from irrigation_twin.farm import SEASON_END, SEASON_START, build_plots
from irrigation_twin.farm.weather import ANANTAPUR, add_et0, load_weather
from irrigation_twin.twin import LeakEvent, Scenario, run_season

weather, elevation = load_weather(Path("data/weather/anantapur_20231001_20240430.csv"))
weather = add_et0(weather, ANANTAPUR, elevation)

leak = Scenario("my leak", leaks=(LeakEvent("P6", 0.03, dt.date(2024, 2, 1)),))
result = run_season(build_plots(weather), weather, leak, SEASON_START, SEASON_END)
print(result.summary())          # water, energy, stress days, relative yield
print(result.plot_summary())     # per plot
```

## Layout

```
src/irrigation_twin/
  network/       WNTR network, demand, leak/pump-failure injection, hydraulics (phase 2)
  farm/          FAO-56 ET0, crop and soil, plot water balance, weather (phase 3)
  twin.py        the coupled daily loop and scenarios (phase 4)
  catalog.py     standard season scenarios, result storage (phase 5)
  detection.py   leak detector (stretch)
  optimise.py    schedule optimiser (stretch)
app/dashboard.py Streamlit dashboard (phase 6)
scripts/         one script per deliverable
tests/           validation checks
docs/            scope, phase write-ups, final report
data/weather/    cached weather CSV
outputs/         generated tables, figures and season logs
```
