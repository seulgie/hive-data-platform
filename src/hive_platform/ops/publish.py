"""Publish a freshly built DuckDB warehouse: write-audit-publish for the whole file.

dbt builds into a separate file (data/lake/_build/warehouse.duckdb). Only after `dbt build`
and its tests have passed does this step check the file and swap it in place of the live
warehouse with one atomic rename. Readers (the map board) therefore see either the old
warehouse or the new one, never a half-built one, and a failed run leaves the old one
untouched.

Run from the repo root:  python -m hive_platform.ops.publish
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)

BUILD_PATH = Path("data/lake/_build/warehouse.duckdb")
LIVE_PATH = Path("data/lake/warehouse.duckdb")

# What the board reads. If one of these is missing or empty, the build is not publishable.
REQUIRED_TABLES = (
    "marts.dim_apiary",
    "marts.fct_hive_status_daily",
    "marts.fct_apiary_visit_priority_daily",
    "evaluation.eval_alert_rules",
)


class PublishError(RuntimeError):
    """The build cannot be published; the live warehouse was left as it was."""


def wal_path(db: Path) -> Path:
    # DuckDB's write-ahead log. It exists while a writer has the file open, or after a
    # writer crashed before its last checkpoint.
    return db.with_name(db.name + ".wal")


def audit(build: Path) -> dict[str, int]:
    """Check the build is complete and holds every table the board needs. Returns row counts."""
    if not build.exists():
        raise PublishError(f"no build to publish at {build}")
    if wal_path(build).exists():
        raise PublishError(f"{wal_path(build)} exists: the build is still open or was not "
                           "closed cleanly")
    counts: dict[str, int] = {}
    with duckdb.connect(str(build), read_only=True) as con:
        for table in REQUIRED_TABLES:
            try:
                counts[table] = con.execute(f"select count(*) from {table}").fetchone()[0]
            except duckdb.CatalogException as exc:
                raise PublishError(f"{table} is missing from the build") from exc
    empty = [t for t, n in counts.items() if n == 0]
    if empty:
        raise PublishError(f"empty tables in the build: {empty}")
    return counts


def publish(build: Path = BUILD_PATH, live: Path = LIVE_PATH) -> dict[str, int]:
    counts = audit(build)
    if wal_path(live).exists():
        # A WAL next to the live file belongs to that file. Swapping the file under it
        # could replay old changes onto the new warehouse, so stop instead.
        raise PublishError(f"{wal_path(live)} exists: a writer has the live warehouse open")
    live.parent.mkdir(parents=True, exist_ok=True)
    os.replace(build, live)  # atomic on one filesystem: _build/ sits inside data/lake/
    log.info("published %s -> %s %s", build, live, counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Swap a built warehouse in as the live one.")
    parser.add_argument("--build", type=Path, default=BUILD_PATH)
    parser.add_argument("--live", type=Path, default=LIVE_PATH)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    publish(args.build, args.live)


if __name__ == "__main__":
    main()
