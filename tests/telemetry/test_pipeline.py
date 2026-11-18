"""generate.py and pipeline.py: a small world end to end (3 apiaries, 30 hives, 2 swaps)."""
import json

import pandas as pd
import pytest

from hive_platform.synthetic.hives import resolve_hive
from hive_platform.synthetic.telemetry import pipeline
from hive_platform.synthetic.telemetry.pipeline import TelemetryError

from .factories import build_world


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    cfg = build_world(root)
    stats = pipeline.run(cfg)
    readings = pd.read_parquet(cfg.readings_dir)
    faults = pd.read_parquet(cfg.out_dir / "fault_log.parquet")
    assigns = pd.read_parquet(cfg.hives_dir / "sensor_assignments.parquet")
    return cfg, stats, readings, faults, assigns


def test_layout_and_stats(world):
    cfg, stats, readings, _, _ = world
    assert sorted(p.name for p in cfg.out_dir.iterdir()) == [
        "fault_log.parquet", "manifest.json", "readings"]
    assert stats.months == [f"2025-{m:02d}" for m in range(3, 11)]
    assert stats.rows == len(readings)
    assert 0.99 < stats.completeness < 1.0                       # 0.5% packet loss
    manifest = json.loads((cfg.out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stats"]["rows"] == stats.rows


def test_every_reading_belongs_to_a_mounted_sensor(world):
    _, _, readings, _, assigns = world
    resolved = resolve_hive(readings[["sensor_id", "ts_utc"]], assigns)
    assert resolved["hive_id"].notna().all()
    assert not readings.duplicated(["sensor_id", "ts_utc"]).any()


def test_no_readings_while_a_sensor_is_dead(world):
    _, _, readings, faults, _ = world
    for d in faults[faults["fault_type"] == "DROPOUT"].itertuples():
        r = readings[readings["sensor_id"] == d.sensor_id]
        assert not ((r["ts_utc"] >= d.start_utc) & (r["ts_utc"] < d.end_utc)).any()


def test_new_sensor_starts_with_a_full_battery(world):
    _, _, readings, faults, _ = world
    swap = faults[faults["fault_type"] == "SENSOR_SWAP"].iloc[0]
    first = readings[readings["sensor_id"] == swap.sensor_id].sort_values("ts_utc").iloc[0]
    assert first["battery_v"] == pytest.approx(4.15, abs=0.05)


def test_fault_log_combines_s4_and_s6_events(world):
    _, _, _, faults, _ = world
    counts = faults["fault_type"].value_counts().to_dict()
    assert counts == {"DROPOUT": 2, "SENSOR_SWAP": 2, "SWARM": 2, "DRIFT": 1}  # 5%, 3% of 30
    assert faults["event_id"].is_unique
    assert faults.loc[faults["fault_type"] == "SWARM", "magnitude"].between(1.5, 3.0).all()


def test_injected_swarm_is_visible_as_a_one_hour_drop(world):
    _, _, readings, faults, _ = world
    ev = faults[faults["fault_type"] == "SWARM"].iloc[0]
    w = readings[readings["sensor_id"] == ev.sensor_id].set_index("ts_utc")["weight_kg"]
    one_hour_change = (w - w.shift(4)).loc[ev.start_utc:ev.end_utc + pd.Timedelta(minutes=15)]
    assert one_hour_change.min() < -0.8 * ev.magnitude


def test_same_seed_same_data_and_rerun_replaces_the_export(world, tmp_path):
    cfg, _, readings, _, _ = world
    pipeline.run(cfg)                                             # rerun in place
    again = pd.read_parquet(cfg.readings_dir)
    pd.testing.assert_frame_equal(readings, again)
    assert not cfg.out_dir.with_name(cfg.out_dir.name + ".tmp").exists()


def test_validate_batch_rejects_a_reading_from_a_dead_sensor(world):
    _, _, readings, faults, assigns = world
    d = faults[faults["fault_type"] == "DROPOUT"].iloc[0]
    bad = readings.iloc[[0]].assign(sensor_id=d.sensor_id, ts_utc=d.start_utc)
    batch = pd.concat([readings.iloc[:100], bad], ignore_index=True)
    dropouts = faults.loc[faults["fault_type"] == "DROPOUT", ["sensor_id", "start_utc", "end_utc"]]
    with pytest.raises(TelemetryError, match="dead sensor"):
        pipeline.validate_batch(batch.astype({"sensor_id": "string"}), assigns, dropouts)
