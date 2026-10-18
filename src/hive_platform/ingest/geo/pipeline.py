"""Orchestration only: the order of steps, the run id, and the CLI. No step logic lives here.

    API -> raw cache (bronze, immutable)
        -> parse + cast types
        -> dedupe on id_parcel (+ invariant: duplicates must be identical)
        -> make_valid + keep polygon parts
        -> department membership (representative point)
        -> validate
        -> GeoParquet (silver) + run manifest

Design decisions come from notebooks/01 and notebooks/02.
Run:  python -m hive_platform.ingest.geo --dept 82 --year 2024
"""
from __future__ import annotations

import argparse
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

import requests

from . import quality, raw_store, spatial, storage
from .client import make_session
from .config import GeoIngestConfig, RunStats

log = logging.getLogger(__name__)


def run(cfg: GeoIngestConfig, session: requests.Session | None = None) -> RunStats:
    session = session or make_session()
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(timezone.utc)
    stats = RunStats()

    # S1
    communes = raw_store.parse_communes(raw_store.fetch_communes(session, cfg))
    boundary_l93 = spatial.build_boundary(communes)
    log.info("communes: %d, department area: %.0f km2", len(communes), boundary_l93.area / 1e6)

    # S2: extract (bronze)
    tiles = spatial.make_tiles(boundary_l93, cfg.tile_size_m)
    raw_store.cache_tiles(session, tiles, cfg, stats)

    # S2: transform (bronze -> silver)
    parcels = raw_store.load_raw_tiles(cfg.tiles_raw_dir)
    stats.rows_raw = len(parcels)
    parcels = quality.cast_types(parcels)
    parcels = quality.dedupe_parcels(parcels, stats)
    parcels = spatial.fix_geometries(parcels, stats)
    parcels = spatial.assign_department(parcels, boundary_l93, stats)
    parcels = storage.add_lineage(parcels, cfg, run_id)
    quality.validate_parcels(parcels)
    stats.rows_final = len(parcels)

    # Load (silver) + manifest
    storage.write_geoparquet_atomic(communes, cfg.communes_out_path)
    storage.write_geoparquet_atomic(parcels, cfg.parcels_out_path)
    manifest = storage.write_manifest(cfg, run_id, started, stats,
                                      [cfg.communes_out_path, cfg.parcels_out_path])
    log.info("done: %s", json.dumps(asdict(stats)))
    log.info("manifest: %s", manifest)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest communes + RPG parcels for one department.")
    parser.add_argument("--dept", default="82")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--tile-size", type=int, default=5000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(GeoIngestConfig(dept_code=args.dept, rpg_year=args.year, tile_size_m=args.tile_size))


if __name__ == "__main__":
    main()
