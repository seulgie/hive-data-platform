"""Orchestration only: the order of steps, the run id, and the CLI.

    S3 apiary registry -> one request per apiary (whole season, UTC)
        -> raw cache (bronze, immutable, stale-checked on read)
        -> parse (UTC, units, continuity) -> trim to season -> validate
        -> silver weather_hourly + run manifest

Run:  python -m hive_platform.ingest.weather --seed 42
"""
from __future__ import annotations

import argparse
import json
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

import geopandas as gpd
import pandas as pd
import requests

from hive_platform.common.http import make_session
from hive_platform.common.io import write_json_atomic, write_parquet_atomic

from . import quality, raw_store
from .config import SOURCE_NAME, WeatherConfig, WeatherStats
from .parse import parse_hourly

log = logging.getLogger(__name__)


def run(cfg: WeatherConfig, session: requests.Session | None = None) -> WeatherStats:
    session = session or make_session()
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(UTC)
    stats = WeatherStats()

    apiaries = pd.DataFrame(gpd.read_parquet(cfg.registry_path).drop(columns="geometry"))
    stats.apiaries = len(apiaries)

    # Extract (bronze)
    raw_store.cache_apiaries(session, apiaries, cfg, stats)

    # Transform (bronze -> silver)
    frames = [parse_hourly(resp, apiary_id) for apiary_id, resp in raw_store.load_raw(apiaries, cfg)]
    weather = quality.trim_to_season(pd.concat(frames, ignore_index=True), cfg)
    stats.nulls = quality.validate_weather(weather, apiaries["apiary_id"].tolist(), cfg)

    cells = weather.groupby("apiary_id")[["grid_lat", "grid_lon"]].first()
    cell_sizes = cells.groupby(["grid_lat", "grid_lon"]).size()
    stats.grid_cells = len(cell_sizes)
    stats.apiaries_sharing_a_cell = int(cell_sizes[cell_sizes > 1].sum())
    stats.hours_per_apiary = int(weather.groupby("apiary_id").size().iloc[0])
    stats.rows = len(weather)

    weather = weather.assign(source=SOURCE_NAME, seed=cfg.seed, run_id=run_id,
                             ingested_at_utc=pd.Timestamp.now(tz="UTC"))

    # Load (silver) + manifest
    write_parquet_atomic(weather, cfg.out_path)
    config = asdict(cfg)
    config["apiary"] = {"seed": cfg.apiary.seed, "registry": str(cfg.registry_path)}
    write_json_atomic(cfg.manifest_dir / f"{started:%Y%m%dT%H%M%SZ}_{run_id}.json", {
        "run_id": run_id,
        "pipeline": "ingest.weather",
        "started_at_utc": started.isoformat(),
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "season_utc": [t.isoformat() for t in cfg.season_utc],
        "config": config,
        "stats": asdict(stats),
        "outputs": [str(cfg.out_path)],
    })
    log.info("done: %s", json.dumps(asdict(stats)))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest hourly weather for every apiary (S5).")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(WeatherConfig(seed=args.seed))


if __name__ == "__main__":
    main()
