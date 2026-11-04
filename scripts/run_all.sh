#!/usr/bin/env bash
# Produce every result in the project from scratch.
# Needs access to archive-api.open-meteo.com for the first step only.
# Set PYTHON to choose the interpreter (default: python3).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"

if ! ls data/weather/*.csv >/dev/null 2>&1; then
  "$PY" scripts/fetch_weather.py
fi
"$PY" -m pytest -q
"$PY" scripts/stretch_leak_detection.py
"$PY" scripts/run_season_analysis.py
"$PY" scripts/build_report.py
echo "Done. Report: docs/report.md   Dashboard: $PY -m streamlit run app/dashboard.py"
