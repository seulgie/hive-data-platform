"""Atomic file writes: write to a temp file, then rename. A crash never leaves a half file."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    """Works for DataFrame and GeoDataFrame (GeoParquet)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


write_geoparquet_atomic = write_parquet_atomic  # same operation; name kept for the geo code
