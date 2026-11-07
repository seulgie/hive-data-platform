"""Bronze layer: one immutable JSON file per apiary, holding the request and the response.

Reruns skip apiaries already cached. On read, the cached request must still match the
registry and the config; a mismatch means the cache is stale and the run stops.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import pandas as pd
import requests

from hive_platform.common.io import write_json_atomic

from .client import fetch_archive
from .config import WeatherConfig, WeatherStats
from .parse import WeatherDataError

log = logging.getLogger(__name__)


def cache_apiaries(session: requests.Session, apiaries: pd.DataFrame,
                   cfg: WeatherConfig, stats: WeatherStats) -> None:
    start, end = cfg.request_dates
    for a in apiaries.sort_values("apiary_id").itertuples():
        path = cfg.raw_dir / f"{a.apiary_id}.json"
        if path.exists():
            stats.from_cache += 1
            continue
        response = fetch_archive(session, a.lat, a.lon, start, end, cfg.variables)
        write_json_atomic(path, {
            "apiary_id": a.apiary_id,
            "request": {"lat": a.lat, "lon": a.lon, "start_date": start, "end_date": end,
                        "variables": list(cfg.variables)},
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "response": response,
        })
        stats.fetched_now += 1
        log.info("%s: %d hours", a.apiary_id, len(response["hourly"]["time"]))
        time.sleep(cfg.request_pause_s)


def load_raw(apiaries: pd.DataFrame, cfg: WeatherConfig) -> list[tuple[str, dict]]:
    """(apiary_id, response) for every apiary, after checking the cache is not stale."""
    start, end = cfg.request_dates
    out = []
    for a in apiaries.sort_values("apiary_id").itertuples():
        path = cfg.raw_dir / f"{a.apiary_id}.json"
        if not path.exists():
            raise WeatherDataError(f"{a.apiary_id}: no cached response in {cfg.raw_dir}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = {"lat": a.lat, "lon": a.lon, "start_date": start, "end_date": end,
                    "variables": list(cfg.variables)}
        if payload["request"] != expected:
            raise WeatherDataError(
                f"{a.apiary_id}: cached request {payload['request']} does not match {expected}. "
                f"The registry or the config changed: delete {cfg.raw_dir} and rerun.")
        out.append((a.apiary_id, payload["response"]))
    return out
