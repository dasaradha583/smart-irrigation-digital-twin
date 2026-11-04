"""Historical daily weather from the Open-Meteo archive, cached as a local CSV.

The twin needs, for each day: Tmax and Tmin (degC), RHmax and RHmin (%), mean
wind speed at 2 m (m/s), solar radiation Rs (MJ m-2 day-1) and rainfall (mm).
Open-Meteo's daily endpoint has no mean wind or humidity extremes, so those
come from its hourly series. Open-Meteo's own FAO-56 ET0 is kept as
``et0_openmeteo_mm`` to cross-check our Penman-Monteith calculation.

Following the project's risk plan, the data is downloaded once and stored in
``data/weather/``. Every later run reads the CSV, never the API.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .et0 import et0_hargreaves, et0_penman_monteith, wind_speed_at_2m

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


@dataclass(frozen=True)
class Site:
    name: str
    latitude: float
    longitude: float
    timezone: str


#: Study area (phase 1 scope): Anantapur, Andhra Pradesh, India. Semi-arid,
#: with about 550 mm of rain a year, mostly from June to October.
ANANTAPUR = Site("Anantapur", latitude=14.68, longitude=77.60, timezone="Asia/Kolkata")

DAILY_VARS = ["temperature_2m_max", "temperature_2m_min", "precipitation_sum",
              "shortwave_radiation_sum", "et0_fao_evapotranspiration"]
HOURLY_VARS = ["relative_humidity_2m", "wind_speed_10m"]


def fetch_open_meteo(site: Site, start: dt.date, end: dt.date, timeout: float = 60.0) -> tuple[pd.DataFrame, float]:
    """Download daily weather for ``site``. Returns (DataFrame, elevation in m)."""
    params = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(DAILY_VARS),
        "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "ms",
        "timezone": site.timezone,
    }
    url = f"{ARCHIVE_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "irrigation-twin/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    return parse_open_meteo(payload), float(payload["elevation"])


def parse_open_meteo(payload: dict) -> pd.DataFrame:
    """Turn an Open-Meteo archive response into the twin's daily table."""
    daily = pd.DataFrame(payload["daily"])
    daily["date"] = pd.to_datetime(daily.pop("time")).dt.date
    hourly = pd.DataFrame(payload["hourly"])
    hourly["date"] = pd.to_datetime(hourly.pop("time")).dt.date
    agg = hourly.groupby("date").agg(
        rh_max=("relative_humidity_2m", "max"),
        rh_min=("relative_humidity_2m", "min"),
        u10_mean=("wind_speed_10m", "mean"),
    ).reset_index()
    out = daily.merge(agg, on="date", how="left").rename(columns={
        "temperature_2m_max": "tmax_c",
        "temperature_2m_min": "tmin_c",
        "precipitation_sum": "rain_mm",
        "shortwave_radiation_sum": "rs_mj",
        "et0_fao_evapotranspiration": "et0_openmeteo_mm",
    })
    out["u2_ms"] = wind_speed_at_2m(out.pop("u10_mean"), 10.0)
    cols = ["date", "tmax_c", "tmin_c", "rh_max", "rh_min", "u2_ms", "rs_mj", "rain_mm", "et0_openmeteo_mm"]
    return out[cols]


def add_et0(weather: pd.DataFrame, site: Site, elevation_m: float) -> pd.DataFrame:
    """Add ``et0_mm`` (FAO-56 Penman-Monteith) and ``et0_hargreaves_mm`` columns."""
    w = weather.copy()
    doy = pd.to_datetime(w["date"]).dt.dayofyear.to_numpy()
    w["et0_mm"] = et0_penman_monteith(w["tmax_c"], w["tmin_c"], w["rh_max"], w["rh_min"],
                                      w["u2_ms"], w["rs_mj"], doy, site.latitude, elevation_m)
    w["et0_hargreaves_mm"] = et0_hargreaves(w["tmax_c"], w["tmin_c"], doy, site.latitude)
    return w


def weather_csv_path(root: Path, site: Site, start: dt.date, end: dt.date) -> Path:
    return root / "data" / "weather" / f"{site.name.lower()}_{start:%Y%m%d}_{end:%Y%m%d}.csv"


def save_weather(weather: pd.DataFrame, path: Path, site: Site, elevation_m: float) -> None:
    """Write the CSV with a commented header that records provenance."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# source: Open-Meteo historical weather API ({ARCHIVE_URL})\n"
              f"# site: {site.name} lat={site.latitude} lon={site.longitude} elevation_m={elevation_m}\n"
              f"# downloaded: {dt.date.today().isoformat()}\n")
    with path.open("w") as f:
        f.write(header)
        weather.to_csv(f, index=False, float_format="%.3f")


def load_weather(path: Path) -> tuple[pd.DataFrame, float]:
    """Read a cached weather CSV. Returns (DataFrame, elevation in m)."""
    elevation = None
    with path.open() as f:
        for line in f:
            if not line.startswith("#"):
                break
            if "elevation_m=" in line:
                elevation = float(line.rsplit("elevation_m=", 1)[1])
    if elevation is None:
        raise ValueError(f"{path} has no elevation_m header line")
    w = pd.read_csv(path, comment="#")
    w["date"] = pd.to_datetime(w["date"]).dt.date
    return w, elevation
