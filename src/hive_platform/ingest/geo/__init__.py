"""Geo ingest: communes + RPG parcels for one department.

Module responsibilities:
    config     settings, paths, shared types (no logic)
    client     HTTP calls to the public APIs (network only)
    raw_store  bronze layer: fetch-if-missing cache on disk, read back
    spatial    geometry: boundary, tiles, repair, department membership
    quality    schema, dedupe invariant, validation gate
    storage    atomic writes, lineage columns, run manifest
    pipeline   order of steps + CLI
"""
from .config import DataQualityError, GeoIngestConfig, RunStats
from .pipeline import main, run

__all__ = ["DataQualityError", "GeoIngestConfig", "RunStats", "main", "run"]
