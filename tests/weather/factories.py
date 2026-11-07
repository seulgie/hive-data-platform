"""A fake Open-Meteo archive API and a fake apiary registry. No network."""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from hive_platform.ingest.weather.config import ARCHIVE_API


def fake_registry(n: int = 6) -> pd.DataFrame:
    """Apiaries 2 km apart in pairs, so pairs share a 0.1 degree grid cell."""
    lats = [44.01 + (i // 2) * 0.1 + (i % 2) * 0.018 for i in range(n)]
    lons = [1.01 + (i // 2) * 0.1 for i in range(n)]
    return pd.DataFrame({"apiary_id": [f"AP-{i + 1:03d}" for i in range(n)],
                         "lat": lats, "lon": lons})


def write_registry(path: Path, registry: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(registry, geometry=gpd.points_from_xy(registry["lon"], registry["lat"]),
                     crs="EPSG:4326").to_parquet(path)


def fake_response(lat: float, lon: float, start_date: str, end_date: str) -> dict:
    """Same shape as the real API: snapped grid point, naive UTC times, units."""
    times = pd.date_range(start_date, pd.Timestamp(end_date) + pd.Timedelta(hours=23), freq="h")
    hours = np.arange(len(times))
    return {
        "latitude": round(lat, 1), "longitude": round(lon, 1),   # snap to a 0.1 degree grid
        "generationtime_ms": 0.1, "utc_offset_seconds": 0, "timezone": "GMT",
        "timezone_abbreviation": "GMT", "elevation": 80.0,
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C",
                         "precipitation": "mm", "wind_speed_10m": "km/h"},
        "hourly": {
            "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
            "temperature_2m": list(np.round(15 + 8 * np.sin(hours * 2 * np.pi / 24), 1)),
            "precipitation": [0.5 if h % 50 == 0 else 0.0 for h in hours],
            "wind_speed_10m": [10.0] * len(times),
        },
    }


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload, self.ok, self.status_code, self.text = payload, True, 200, ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeOpenMeteo:
    """fail_on_call=N raises a ConnectionError on the N-th request (1-based)."""

    def __init__(self, fail_on_call: int | None = None):
        self.calls: list[dict] = []
        self.fail_on_call = fail_on_call

    def get(self, url, params=None, timeout=None):
        assert url == ARCHIVE_API
        self.calls.append(params)
        if len(self.calls) == self.fail_on_call:
            raise requests.ConnectionError("simulated network failure")
        return FakeResponse(fake_response(params["latitude"], params["longitude"],
                                          params["start_date"], params["end_date"]))
