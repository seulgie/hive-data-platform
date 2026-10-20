"""raw_store.py: bronze cache behaviour (idempotency, resume after failure, no partial files)."""
import pytest
import requests

from hive_platform.ingest.geo import raw_store, spatial
from hive_platform.ingest.geo.config import DataQualityError, GeoIngestConfig, RunStats

from .factories import DEPT_L93, FakeSession, fake_world


def _cfg(tmp_path):
    return GeoIngestConfig(tile_size_m=3000, page_size=50, request_pause_s=0, raw_root=tmp_path)


def test_communes_are_fetched_once_then_read_from_cache(tmp_path):
    session = FakeSession(*fake_world())
    cfg = _cfg(tmp_path)

    first = raw_store.fetch_communes(session, cfg)
    second = raw_store.fetch_communes(session, cfg)

    assert first == second
    assert session.communes_calls == 1


def test_parse_communes_rejects_duplicate_insee_codes():
    communes, _ = fake_world()
    raw = {"features": [f for f in communes.iloc[[0, 0]].iterfeatures()]}
    with pytest.raises(DataQualityError, match="duplicate INSEE"):
        raw_store.parse_communes(raw)


def test_cache_resumes_after_network_failure_without_partial_files(tmp_path):
    communes, parcels = fake_world()
    cfg = _cfg(tmp_path)
    tiles = spatial.make_tiles(DEPT_L93, cfg.tile_size_m)

    # Run 1: the network dies on the 3rd RPG request
    failing = FakeSession(communes, parcels, fail_on_rpg_call=3)
    with pytest.raises(requests.ConnectionError):
        raw_store.cache_tiles(failing, tiles, cfg, RunStats())
    cached_after_failure = sorted(cfg.tiles_raw_dir.glob("tile_*.json"))
    assert len(cached_after_failure) >= 1
    assert not list(cfg.tiles_raw_dir.glob("*.tmp"))      # atomic writes: nothing half-written

    # Run 2: healthy network, only the missing tiles are fetched
    healthy = FakeSession(communes, parcels)
    stats = RunStats()
    raw_store.cache_tiles(healthy, tiles, cfg, stats)
    assert stats.tiles_from_cache == len(cached_after_failure)
    assert stats.tiles_fetched_now == len(tiles) - len(cached_after_failure)

    # Run 3: everything cached, zero requests
    idle = FakeSession(communes, parcels)
    raw_store.cache_tiles(idle, tiles, cfg, RunStats())
    assert idle.rpg_calls == 0


def test_load_raw_tiles_keeps_source_tile_id(tmp_path):
    communes, parcels = fake_world()
    cfg = _cfg(tmp_path)
    tiles = spatial.make_tiles(DEPT_L93, cfg.tile_size_m)
    raw_store.cache_tiles(FakeSession(communes, parcels), tiles, cfg, RunStats())

    gdf = raw_store.load_raw_tiles(cfg.tiles_raw_dir)

    assert set(gdf["source_tile_id"]) <= set(tiles["tile_id"])
    assert gdf.crs == "EPSG:4326"


def test_load_raw_tiles_fails_on_empty_cache(tmp_path):
    with pytest.raises(DataQualityError, match="no parcels"):
        raw_store.load_raw_tiles(tmp_path)

def test_tile_cache_is_not_shared_between_tile_sizes(tmp_path):
    # tile_id N means a different box for each tile size: the cache must not mix them
    communes, parcels = fake_world()
    small = GeoIngestConfig(tile_size_m=3000, page_size=50, request_pause_s=0, raw_root=tmp_path)
    large = GeoIngestConfig(tile_size_m=5000, page_size=50, request_pause_s=0, raw_root=tmp_path)

    raw_store.cache_tiles(FakeSession(communes, parcels),
                          spatial.make_tiles(DEPT_L93, small.tile_size_m), small, RunStats())
    stats = RunStats()
    raw_store.cache_tiles(FakeSession(communes, parcels),
                          spatial.make_tiles(DEPT_L93, large.tile_size_m), large, stats)

    assert stats.tiles_from_cache == 0
    assert stats.tiles_fetched_now == stats.tiles_total
    