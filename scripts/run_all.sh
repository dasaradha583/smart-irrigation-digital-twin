#!/usr/bin/env bash
# Produce every result in the project from scratch.
# Needs access to archive-api.open-meteo.com for the first step only.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! ls data/weather/*.csv >/dev/null 2>&1; then
  python scripts/fetch_weather.py
fi
pytest -q
python scripts/stretch_leak_detection.py
python scripts/run_season_analysis.py
python scripts/build_report.py
echo "Done. Report: docs/report.md   Dashboard: streamlit run app/dashboard.py"
