"""Safe file writes, lineage stamping, and the run manifest (silver layer output)."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .config import SOURCE_NAME, GeoIngestConfig, RunStats


def write_json_atomic(path: Path, payload: dict) -> None:
    """Write to a temp file, then rename: a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def write_geoparquet_atomic(gdf: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    gdf.to_parquet(tmp, index=False)
    tmp.replace(path)


def add_lineage(gdf: gpd.GeoDataFrame, cfg: GeoIngestConfig, run_id: str) -> gpd.GeoDataFrame:
    out = gdf.copy()
    out["dept_code"] = cfg.dept_code
    out["rpg_year"] = cfg.rpg_year
    out["source"] = SOURCE_NAME
    out["run_id"] = run_id
    out["ingested_at_utc"] = pd.Timestamp.now(tz="UTC")
    return out


def write_manifest(cfg: GeoIngestConfig, run_id: str, started: datetime,
                   stats: RunStats, outputs: list[Path]) -> Path:
    config = {k: str(v) if isinstance(v, Path) else v for k, v in asdict(cfg).items()}
    manifest = {
        "run_id": run_id,
        "pipeline": "ingest.geo",
        "started_at_utc": started.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "stats": asdict(stats),
        "outputs": [str(p) for p in outputs],
    }
    path = cfg.manifest_dir / f"{started:%Y%m%dT%H%M%SZ}_{run_id}.json"
    write_json_atomic(path, manifest)
    return path
