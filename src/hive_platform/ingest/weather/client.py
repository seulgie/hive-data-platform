"""HTTP calls to the Open-Meteo archive API. Network only: no files, no DataFrames."""
from __future__ import annotations

import requests

from hive_platform.common.http import get_json

from .config import ARCHIVE_API


def fetch_archive(session: requests.Session, lat: float, lon: float,
                  start_date: str, end_date: str, variables: tuple[str, ...]) -> dict:
    """Hourly history for one point. Asks for UTC ("GMT"): everything is stored in UTC."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(variables),
        "timezone": "GMT",
    }
    return get_json(session, ARCHIVE_API, params)
