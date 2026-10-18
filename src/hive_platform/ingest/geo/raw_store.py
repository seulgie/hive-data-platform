"""Bronze layer: fetch-if-missing, keep raw API responses on disk, read them back.

Raw files are immutable: once a tile is cached it is never fetched again, so a rerun
resumes where the previous one stopped and transforms can be replayed without the API.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from .client import fetch_communes_geojson, fetch_rpg_tile
from .config import CRS_WGS84, DataQualityError, GeoIngestConfig, RunStats
from .storage import write_json_atomic

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# S1: communes
# ---------------------------------------------------------------------------

def fetch_communes(session: requests.Session, cfg: GeoIngestConfig) -> dict:
    """Return the raw communes GeoJSON, from cache if available."""
    path = cfg.communes_raw_path
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    raw = fetch_communes_geojson(session, cfg.dept_code)
    write_json_atomic(path, raw)
    return raw


def parse_communes(raw: dict) -> gpd.GeoDataFrame:
    communes = gpd.GeoDataFrame.from_features(raw["features"], crs=CRS_WGS84)
    communes = communes.rename(columns={"code": "insee_code", "nom": "commune_name"})
    communes["insee_code"] = communes["insee_code"].astype("string")
    if communes["insee_code"].duplicated().any():
        raise DataQualityError("duplicate INSEE codes in communes")
    return communes


# ---------------------------------------------------------------------------
# S2: RPG tiles
# ---------------------------------------------------------------------------

def cache_tiles(session: requests.Session, tiles_l93: gpd.GeoDataFrame,
                cfg: GeoIngestConfig, stats: RunStats) -> None:
    """One immutable JSON file per tile. Reruns skip finished tiles."""
    tiles_wgs = tiles_l93.to_crs(CRS_WGS84)
    stats.tiles_total = len(tiles_wgs)
    for row in tiles_wgs.itertuples():
        path = cfg.tiles_raw_dir / f"tile_{row.tile_id:04d}.json"
        if path.exists():
            stats.tiles_from_cache += 1
            continue
        t0 = time.perf_counter()
        features, pages = fetch_rpg_tile(session, row.geometry, cfg)
        write_json_atomic(path, {
            "tile_id": int(row.tile_id),
            "tile_bounds_wgs84": list(row.geometry.bounds),
            "rpg_year": cfg.rpg_year,
            "pages": pages,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "features": features,
        })
        stats.tiles_fetched_now += 1
        log.info("tile %d/%d: %d parcels, %d page(s), %.1fs",
                 row.tile_id + 1, stats.tiles_total, len(features), pages, time.perf_counter() - t0)
        time.sleep(cfg.request_pause_s)


def load_raw_tiles(tiles_dir: Path) -> gpd.GeoDataFrame:
    """Read every cached tile into one GeoDataFrame, with the source tile_id kept for lineage."""
    frames = []
    for path in sorted(tiles_dir.glob("tile_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not payload["features"]:
            continue
        gdf = gpd.GeoDataFrame.from_features(payload["features"], crs=CRS_WGS84)
        gdf["source_tile_id"] = payload["tile_id"]
        frames.append(gdf)
    if not frames:
        raise DataQualityError(f"no parcels found in {tiles_dir}")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=CRS_WGS84)
