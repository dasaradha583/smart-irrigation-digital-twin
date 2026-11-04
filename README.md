# Smart Irrigation Digital Twin

One digital twin of a small irrigated farm and the water network that feeds it, so a pipe leak or pump failure can be traced through to crop water stress.

The project merges two models: **AquaTwin**, a water distribution network simulated in [WNTR](https://usepa.github.io/WNTR/) with pressure-driven demand, and **FarmTwin**, a FAO-56 daily soil-water balance for four maize plots in Anantapur, India, driven by real weather. Each day they swap two quantities. Each plot's irrigation request becomes demand at its network junction, and the water the network can actually deliver at that pressure comes back as irrigation.

## Status

All phases and stretch goals are built and tested (`pytest`: 49 checks). The season results and the final report are generated from real weather by one command:

```bash
./scripts/run_all.sh
```

It downloads the 2023-24 weather from Open-Meteo once (cached in `data/weather/`), runs the tests, the leak-detection study, every season scenario and the optimiser, and then writes `docs/report.md`.

| Phase | Code | Write-up |
|---|---|---|
| 1. Scope and study | — | [docs/phase1_scope.md](docs/phase1_scope.md) |
| 2. Water network model | `src/irrigation_twin/network/` | [docs/phase2_water_network.md](docs/phase2_water_network.md) |
| 3. Farm model | `src/irrigation_twin/farm/` | `docs/report.md` §3 (generated) |
| 4. Coupling | `src/irrigation_twin/twin.py` | `docs/report.md` §4 (generated) |
| 5. Scenarios and analysis | `src/irrigation_twin/catalog.py`, `scripts/run_season_analysis.py` | `docs/report.md` §5 (generated) |
| 6. Dashboard | `app/dashboard.py` | `streamlit run app/dashboard.py` |
| 7. Validation and report | `tests/`, `scripts/build_report.py` | `docs/report.md` (generated) |
| Stretch: leak detection | `src/irrigation_twin/detection.py` | [docs/stretch_leak_detection.md](docs/stretch_leak_detection.md) |
| Stretch: optimiser, drought, water saved | `src/irrigation_twin/optimise.py` | `docs/report.md` §6 (generated) |

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
