"""HTTP access to the two public APIs. Network only: no files, no GeoDataFrames."""
from __future__ import annotations

import json
import logging
import time

import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import mapping
from urllib3.util.retry import Retry

from .config import GEO_API, RPG_API, GeoIngestConfig

log = logging.getLogger(__name__)


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,                      # waits 1, 2, 4, 8... seconds
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_json(session: requests.Session, url: str, params: dict, timeout: int = 120) -> dict:
    response = session.get(url, params=params, timeout=timeout)
    if not response.ok:
        log.error("HTTP %s from %s: %s", response.status_code, url, response.text[:500])
        response.raise_for_status()
    return response.json()


def fetch_communes_geojson(session: requests.Session, dept_code: str) -> dict:
    return get_json(
        session,
        f"{GEO_API}/departements/{dept_code}/communes",
        {"format": "geojson", "geometry": "contour", "fields": "code,nom,population"},
        timeout=60,
    )


def fetch_rpg_tile(session: requests.Session, geom_wgs84, cfg: GeoIngestConfig) -> tuple[list, int]:
    """Fetch every page for one tile. Returns (features, pages)."""
    features, start, pages = [], 0, 0
    while True:
        params = {
            "annee": cfg.rpg_year,
            "geom": json.dumps(mapping(geom_wgs84)),
            "_limit": cfg.page_size,
            "_start": start,
        }
        batch = get_json(session, RPG_API, params).get("features", [])
        features.extend(batch)
        pages += 1
        if len(batch) < cfg.page_size:
            return features, pages
        start += cfg.page_size
        time.sleep(cfg.request_pause_s)
