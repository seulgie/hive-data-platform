"""HTTP access to the two public APIs. Network only: no files, no GeoDataFrames."""
from __future__ import annotations

import json
import time

import requests
from shapely.geometry import mapping

from hive_platform.common.http import get_json, make_session

from .config import GEO_API, RPG_API, GeoIngestConfig



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


__all__ = ["fetch_communes_geojson", "fetch_rpg_tile", "get_json", "make_session"]
