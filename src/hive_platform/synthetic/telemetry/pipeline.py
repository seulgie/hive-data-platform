"""Orchestration: inputs -> faults -> every hive in batches -> validated monthly files.

    S3 apiaries (crop) + S4 hives, sensor history, DROPOUTs + S5 weather
        -> draw SWARM and DRIFT events
        -> per hive: healthy signals + faults, split by sensor (one RNG stream per hive)
        -> validate each batch (SCD2 join, no data while dead, ranges, uniqueness)
        -> readings/month=YYYY-MM/part-NNN.parquet + fault_log.parquet + manifest.json

Output is written to a temporary folder and swapped in at the end: a failed run never
leaves a half-written export. Run:  python -m hive_platform.synthetic.telemetry --seed 42
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from hive_platform.common.io import write_json_atomic
from hive_platform.synthetic.hives import resolve_hive

from . import faults as flt
from .config import GENERATOR_VERSION, SOURCE_NAME, TelemetryConfig, TelemetryStats
from .generate import generate_hive

log = logging.getLogger(__name__)

VALID_RANGES = {
    "weight_kg": (5.0, 150.0),
    "temp_internal_c": (10.0, 45.0),
    "humidity_pct": (0.0, 100.0),
    "battery_v": (3.0, 4.3),
}


class TelemetryError(RuntimeError):
    """Generated telemetry breaks one of its invariants."""


def make_rngs(seed: int, n_hives: int) -> tuple[np.random.Generator, list[np.random.Generator]]:
    """One stream for drawing faults, one independent stream per hive (sorted by hive_id).

    A hive's readings depend only on its own stream: one hive can be regenerated or tested
    alone, and a parallel engine would produce the same data.
    """
    root = np.random.SeedSequence(seed, spawn_key=(6,))
    faults_seq, hives_seq = root.spawn(2)
    return (np.random.default_rng(faults_seq),
            [np.random.default_rng(s) for s in hives_seq.spawn(n_hives)])


def load_inputs(cfg: TelemetryConfig) -> dict[str, pd.DataFrame]:
    apiaries = pd.read_parquet(cfg.apiaries_path, columns=["apiary_id", "crop_variety"])
    return {
        "apiaries": apiaries,
        "hives": pd.read_parquet(cfg.hives_dir / "hives.parquet"),
        "assignments": pd.read_parquet(cfg.hives_dir / "sensor_assignments.parquet"),
        "s4_faults": pd.read_parquet(cfg.hives_dir / "fault_log.parquet"),
        "weather": pd.read_parquet(cfg.weather_path,
                                   columns=["apiary_id", "ts_utc", "temperature_2m_c",
                                            "precipitation_mm"]),
    }


def validate_batch(batch: pd.DataFrame, assignments: pd.DataFrame,
                   dropouts: pd.DataFrame) -> None:
    problems = []
    if batch.duplicated(["sensor_id", "ts_utc"]).any():
        problems.append("duplicate (sensor_id, ts_utc)")
    unresolved = resolve_hive(batch[["sensor_id", "ts_utc"]], assignments)["hive_id"].isna()
    if unresolved.any():
        problems.append(f"{int(unresolved.sum())} readings outside any sensor assignment")
    d = batch[["sensor_id", "ts_utc"]].merge(dropouts, on="sensor_id")
    if ((d["ts_utc"] >= d["start_utc"]) & (d["ts_utc"] < d["end_utc"])).any():
        problems.append("readings from a dead sensor (inside a DROPOUT)")
    for col, (lo, hi) in VALID_RANGES.items():
        bad = ~batch[col].between(lo, hi)
        if bad.any():
            problems.append(f"{col}: {int(bad.sum())} values outside [{lo}, {hi}]")
    if problems:
        raise TelemetryError("telemetry validation failed: " + "; ".join(problems))


def write_batch(batch: pd.DataFrame, out_dir: Path, part: int, months: set) -> None:
    """Partition by UTC month: readings/month=2025-04/part-003.parquet.

    Readings live in their own folder so the whole dataset can be read as one table
    (fault_log and manifest sit next to it, not inside it)."""
    month = batch["ts_utc"].dt.strftime("%Y-%m")
    for m, chunk in batch.groupby(month):
        path = out_dir / "readings" / f"month={m}" / f"part-{part:03d}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        chunk.to_parquet(path, index=False)
        months.add(m)


def run(cfg: TelemetryConfig) -> TelemetryStats:
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(UTC)
    inp = load_inputs(cfg)
    hives = inp["hives"].sort_values("hive_id").reset_index(drop=True)
    crop_of = inp["apiaries"].set_index("apiary_id")["crop_variety"]
    weather_of = {a: g for a, g in inp["weather"].groupby("apiary_id")}

    faults_rng, hive_rngs = make_rngs(cfg.seed, len(hives))
    new_faults = flt.draw_faults(hives, inp["assignments"], cfg, faults_rng)
    fault_log = flt.combine_fault_logs(inp["s4_faults"], new_faults)
    faults_of = {h: g for h, g in fault_log.groupby("hive_id")}
    empty_faults = fault_log.iloc[0:0]
    dropouts = fault_log.loc[fault_log["fault_type"] == "DROPOUT",
                             ["sensor_id", "start_utc", "end_utc"]]
    assign_of = {h: g for h, g in inp["assignments"].groupby("hive_id")}

    tmp_dir = cfg.out_dir.with_name(cfg.out_dir.name + ".tmp")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    stats = TelemetryStats(hives=len(hives))
    months: set = set()
    sensors: set = set()
    batch_frames, part = [], 0

    for i, hive in hives.iterrows():
        readings, expected = generate_hive(
            hive, assign_of[hive["hive_id"]], faults_of.get(hive["hive_id"], empty_faults),
            weather_of[hive["apiary_id"]], crop_of[hive["apiary_id"]], cfg, hive_rngs[i])
        batch_frames.append(readings)
        stats.expected_readings += expected
        last = i == len(hives) - 1
        if len(batch_frames) == cfg.hives_per_batch or last:
            batch = pd.concat(batch_frames, ignore_index=True)
            batch["sensor_id"] = batch["sensor_id"].astype("string")
            validate_batch(batch, inp["assignments"], dropouts)
            write_batch(batch, tmp_dir, part, months)
            stats.rows += len(batch)
            sensors.update(batch["sensor_id"].unique())
            log.info("batch %d: hives up to %s, %d rows", part, hive["hive_id"], len(batch))
            batch_frames, part = [], part + 1

    stats.sensors_reporting = len(sensors)
    stats.months = sorted(months)
    stats.completeness = round(stats.rows / stats.expected_readings, 4)
    stats.faults = fault_log["fault_type"].value_counts().sort_index().to_dict()
    if not 0.98 <= stats.completeness <= 1.0:
        raise TelemetryError(f"completeness {stats.completeness} outside [0.98, 1.0]")

    fault_log.assign(source=SOURCE_NAME, seed=cfg.seed, run_id=run_id).to_parquet(
        tmp_dir / "fault_log.parquet", index=False)
    write_json_atomic(tmp_dir / "manifest.json", {
        "run_id": run_id,
        "generator": "synthetic.telemetry",
        "generator_version": GENERATOR_VERSION,
        "generated_at_utc": started.isoformat(),
        "inputs": {"apiaries": str(cfg.apiaries_path), "hives": str(cfg.hives_dir),
                   "weather": str(cfg.weather_path)},
        "config": {k: v for k, v in asdict(cfg).items() if k != "hive"},
        "stats": asdict(stats),
    })

    # Swap the finished export in place of the previous one
    shutil.rmtree(cfg.out_dir, ignore_errors=True)
    tmp_dir.rename(cfg.out_dir)
    log.info("telemetry: %s", json.dumps(asdict(stats)))
    log.info("written to %s", cfg.out_dir)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic hive telemetry (S6).")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(TelemetryConfig(seed=args.seed))


if __name__ == "__main__":
    main()
