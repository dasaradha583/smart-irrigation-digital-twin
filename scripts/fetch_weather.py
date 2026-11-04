"""Download the study-season weather once and store it as a CSV (phase 3).

Run from the repository root, anywhere archive-api.open-meteo.com is reachable::

    python scripts/fetch_weather.py

Writes data/weather/anantapur_20231001_20240430.csv. Every other script
reads that file and never calls the API.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from irrigation_twin.farm.weather import ANANTAPUR, fetch_open_meteo, save_weather, weather_csv_path  # noqa: E402

#: A month either side of the cropping season (1 Nov 2023 - 19 Apr 2024).
START = dt.date(2023, 10, 1)
END = dt.date(2024, 4, 30)


def main() -> None:
    path = weather_csv_path(ROOT, ANANTAPUR, START, END)
    weather, elevation = fetch_open_meteo(ANANTAPUR, START, END)
    if weather.isna().any().any():
        missing = weather.columns[weather.isna().any()].tolist()
        raise SystemExit(f"Open-Meteo returned gaps in {missing}; not saving.")
    save_weather(weather, path, ANANTAPUR, elevation)
    print(f"saved {len(weather)} days to {path.relative_to(ROOT)} (elevation {elevation} m)")


if __name__ == "__main__":
    main()
