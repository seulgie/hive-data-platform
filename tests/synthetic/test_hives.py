"""Tests for hive_platform.synthetic.hives (S4 registry + SCD2 sensor history)."""
from __future__ import annotations

import json

import geopandas as gpd
import pandas as pd
import pytest

from hive_platform.ingest.geo.config import GeoIngestConfig
from hive_platform.synthetic import hives as hv
from hive_platform.synthetic.apiaries import ApiaryConfig
from hive_platform.synthetic.hives import HiveConfig, HiveRegistryError

UTC = "UTC"


def fake_apiaries(n=40) -> pd.DataFrame:
    return pd.DataFrame({"apiary_id": [f"AP-{i:03d}" for i in range(1, n + 1)],
                         "n_hives": [8 + (i % 9) for i in range(n)]})   # 8..16


@pytest.fixture(scope="module")
def world():
    cfg = HiveConfig()
    apiaries = fake_apiaries()
    hives, assign, faults, stats = hv.generate(apiaries, cfg)
    return cfg, apiaries, hives, assign, faults, stats


# ---------------------------------------------------------------------------
# Season and installation
# ---------------------------------------------------------------------------

def test_season_is_local_but_stored_in_utc():
    cfg = HiveConfig()
    # Paris is UTC+1 on both dates (summer time ends on 26 Oct 2025)
    assert cfg.season_start_utc == pd.Timestamp("2025-02-28 23:00", tz=UTC)
    assert cfg.season_end_utc == pd.Timestamp("2025-10-31 23:00", tz=UTC)


def test_hives_expand_from_apiaries(world):
    _, apiaries, hives, *_ = world
    assert len(hives) == apiaries["n_hives"].sum()
    assert hives["hive_id"].is_unique
    first = hives[hives["apiary_id"] == "AP-001"]["hive_id"].tolist()
    assert first == [f"AP-001-H{k:02d}" for k in range(1, 9)]


def test_installation_window_and_working_hours(world):
    cfg, _, hives, *_ = world
    local = hives["installed_at_utc"].dt.tz_convert(hv.LOCAL_TZ)
    assert (local.dt.day <= cfg.install_window_days).all() and (local.dt.month == 3).all()
    assert local.dt.hour.between(7, 11).all()
    # every hive of an apiary is installed at the same moment
    assert hives.groupby("apiary_id")["installed_at_utc"].nunique().eq(1).all()


# ---------------------------------------------------------------------------
# Sensor history (SCD2) and faults
# ---------------------------------------------------------------------------

def test_swap_count_and_chained_intervals(world):
    cfg, _, hives, assign, _, stats = world
    assert stats.swaps == round(cfg.swap_rate * len(hives))

    swapped = assign[assign["reason"] == "swap"]["hive_id"]
    for hive_id in swapped:
        a = assign[assign["hive_id"] == hive_id].sort_values("valid_from_utc")
        assert len(a) == 2
        assert a["valid_to_utc"].iloc[0] == a["valid_from_utc"].iloc[1]   # no gap, no overlap
        assert pd.isna(a["valid_to_utc"].iloc[1])                          # still mounted


def test_dropout_ends_exactly_at_the_swap(world):
    _, _, hives, _, faults, _ = world
    drop = faults[faults["fault_type"] == "DROPOUT"].set_index("hive_id")
    swap = faults[faults["fault_type"] == "SENSOR_SWAP"].set_index("hive_id")
    assert (drop["end_utc"] == swap.loc[drop.index, "start_utc"]).all()

    installed = hives.set_index("hive_id")["installed_at_utc"]
    delay = drop["end_utc"] - drop["start_utc"]
    assert (delay > pd.Timedelta(0)).all() and (delay < pd.Timedelta(days=6)).all()
    swap_local = drop["end_utc"].dt.tz_convert(hv.LOCAL_TZ)
    assert swap_local.dt.hour.between(9, 16).all()           # swaps happen in working hours
    assert (drop["start_utc"] >= installed.loc[drop.index] + pd.Timedelta(days=7)).all()


def test_generate_is_deterministic(world):
    _, apiaries, _, assign, faults, _ = world
    _, a2, f2, _ = hv.generate(apiaries, HiveConfig())
    pd.testing.assert_frame_equal(assign, a2)
    pd.testing.assert_frame_equal(faults, f2)
    _, a3, _, _ = hv.generate(apiaries, HiveConfig(seed=7))
    assert not assign["valid_to_utc"].equals(a3["valid_to_utc"])


def test_validate_rejects_overlap_and_gap(world):
    cfg, apiaries, hives, assign, faults, _ = world
    hive_id = assign.loc[assign["reason"] == "swap", "hive_id"].iloc[0]
    second = assign.index[(assign["hive_id"] == hive_id) & (assign["reason"] == "swap")][0]

    for shift, label in [(pd.Timedelta(hours=-1), "overlap"), (pd.Timedelta(hours=1), "gap")]:
        broken = assign.copy()
        broken.loc[second, "valid_from_utc"] += shift
        with pytest.raises(HiveRegistryError, match="gap or overlap"):
            hv.validate_registry(apiaries, hives, broken, faults, cfg)


# ---------------------------------------------------------------------------
# resolve_hive: the point-in-time join telemetry will use
# ---------------------------------------------------------------------------

def test_resolve_hive_uses_half_open_intervals():
    t0 = pd.Timestamp("2025-03-05 08:00", tz=UTC)
    swap = pd.Timestamp("2025-06-10 14:00", tz=UTC)
    assign = pd.DataFrame({
        "sensor_id": ["SN-1", "SN-2"], "hive_id": ["H1", "H1"],
        "valid_from_utc": [t0, swap], "valid_to_utc": [swap, pd.NaT],
    })
    assign["valid_to_utc"] = pd.to_datetime(assign["valid_to_utc"], utc=True)
    readings = pd.DataFrame({
        "sensor_id": ["SN-1", "SN-1", "SN-1", "SN-2", "SN-9", "SN-1"],
        "ts_utc": [t0 - pd.Timedelta(minutes=15),   # before installation
                   t0,                              # first instant: included
                   swap,                            # old sensor at the swap: excluded
                   swap,                            # new sensor at the swap: included
                   swap,                            # unknown sensor
                   swap - pd.Timedelta(minutes=15)],
        "value": range(6),
    })

    out = hv.resolve_hive(readings, assign)

    assert out["hive_id"].isna().tolist() == [True, False, True, False, True, False]
    assert (out["hive_id"].dropna() == "H1").all()
    assert out["value"].tolist() == list(range(6))     # order and columns preserved


# ---------------------------------------------------------------------------
# run(): files on disk
# ---------------------------------------------------------------------------

def test_run_reads_apiaries_and_writes_registry(tmp_path):
    geo = GeoIngestConfig(raw_root=tmp_path / "raw", lake_root=tmp_path / "lake")
    cfg = HiveConfig(seed=5, apiary=ApiaryConfig(geo=geo))
    apiaries = fake_apiaries(5)
    cfg.apiary.out_dir.mkdir(parents=True)
    gpd.GeoDataFrame(apiaries, geometry=gpd.points_from_xy([1.1] * 5, [44.0] * 5),
                     crs="EPSG:4326").to_parquet(cfg.apiary.out_dir / "apiaries.parquet")

    stats = hv.run(cfg)

    assert "seed=5" in str(cfg.out_dir) and "seed=5" in str(cfg.apiary.out_dir)
    hives = pd.read_parquet(cfg.out_dir / "hives.parquet")
    assert len(hives) == stats.hives == apiaries["n_hives"].sum()
    assert str(hives["installed_at_utc"].dt.tz) == "UTC"
    for name in ("sensor_assignments", "fault_log"):
        assert (cfg.out_dir / f"{name}.parquet").exists()
    manifest = json.loads((cfg.out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stats"]["swaps"] == stats.swaps
