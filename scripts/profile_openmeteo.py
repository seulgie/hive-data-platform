"""Profile the Open-Meteo archive API on one apiary before building ingest/weather.

Fetches 48 hours of hourly weather for the first apiary of the registry, across the
2025-03-30 summer-time change, prints the response shape, and saves it as a test fixture.

Usage:  python scripts/profile_openmeteo.py
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd

from hive_platform.ingest.geo.client import get_json, make_session

ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
REGISTRY = Path("data/raw/synthetic/apiaries/dept_code=82/seed=42/apiaries.parquet")
FIXTURE = Path("data/samples/openmeteo_archive_sample.json")
HOURLY = ["temperature_2m", "precipitation", "wind_speed_10m"]


def main() -> None:
    apiary = gpd.read_parquet(REGISTRY).sort_values("apiary_id").iloc[0]
    params = {
        "latitude": apiary["lat"],
        "longitude": apiary["lon"],
        "start_date": "2025-03-29",
        "end_date": "2025-03-30",   # Paris switches to summer time on 30 March
        "hourly": ",".join(HOURLY),
        "timezone": "GMT",          # ask for UTC: everything is stored in UTC
    }
    data = get_json(make_session(), ARCHIVE_API, params)
    hourly = data["hourly"]

    print("apiary     :", apiary["apiary_id"], apiary["commune_name"], apiary["lat"], apiary["lon"])
    print("grid point :", data["latitude"], data["longitude"], "| elevation:", data["elevation"])
    print("keys       :", list(data))
    print("timezone   :", data["timezone"], "| utc_offset_seconds:", data["utc_offset_seconds"])
    print("units      :", data["hourly_units"])
    print("hours      :", len(hourly["time"]), "|", hourly["time"][0], "->", hourly["time"][-1])
    for var in HOURLY:
        values = [v for v in hourly[var] if v is not None]
        nulls = len(hourly[var]) - len(values)
        print(f"{var:15}: nulls={nulls}, min={min(values, default=None)}, max={max(values, default=None)}")

    FIXTURE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print("saved      :", FIXTURE)


if __name__ == "__main__":
    main()