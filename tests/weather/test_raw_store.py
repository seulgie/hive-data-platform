"""raw_store.py: cache per apiary, resume after failure, stale cache detection."""
import pytest
import requests

from hive_platform.ingest.geo.config import GeoIngestConfig
from hive_platform.ingest.weather import raw_store
from hive_platform.ingest.weather.config import WeatherConfig, WeatherStats
from hive_platform.ingest.weather.parse import WeatherDataError
from hive_platform.synthetic.apiaries import ApiaryConfig

from .factories import FakeOpenMeteo, fake_registry


def _cfg(tmp_path) -> WeatherConfig:
    geo = GeoIngestConfig(raw_root=tmp_path / "raw", lake_root=tmp_path / "lake")
    return WeatherConfig(request_pause_s=0, apiary=ApiaryConfig(geo=geo))


def test_request_dates_cover_the_season_in_utc(tmp_path):
    # Season starts 2025-03-01 00:00 Paris = 2025-02-28 23:00 UTC
    assert _cfg(tmp_path).request_dates == ("2025-02-28", "2025-10-31")


def test_each_apiary_is_fetched_once_then_read_from_cache(tmp_path):
    cfg, registry = _cfg(tmp_path), fake_registry()
    session = FakeOpenMeteo()

    raw_store.cache_apiaries(session, registry, cfg, WeatherStats())
    stats = WeatherStats()
    raw_store.cache_apiaries(session, registry, cfg, stats)

    assert len(session.calls) == len(registry)
    assert stats.from_cache == len(registry) and stats.fetched_now == 0
    assert [a for a, _ in raw_store.load_raw(registry, cfg)] == registry["apiary_id"].tolist()


def test_resume_after_network_failure_without_partial_files(tmp_path):
    cfg, registry = _cfg(tmp_path), fake_registry()

    with pytest.raises(requests.ConnectionError):
        raw_store.cache_apiaries(FakeOpenMeteo(fail_on_call=4), registry, cfg, WeatherStats())
    assert len(list(cfg.raw_dir.glob("*.json"))) == 3
    assert not list(cfg.raw_dir.glob("*.tmp"))

    healthy, stats = FakeOpenMeteo(), WeatherStats()
    raw_store.cache_apiaries(healthy, registry, cfg, stats)
    assert (stats.from_cache, stats.fetched_now) == (3, 3)


def test_stale_cache_is_detected_when_an_apiary_moves(tmp_path):
    cfg, registry = _cfg(tmp_path), fake_registry()
    raw_store.cache_apiaries(FakeOpenMeteo(), registry, cfg, WeatherStats())

    moved = registry.copy()
    moved.loc[0, "lat"] += 0.05          # the registry changed after the cache was filled
    with pytest.raises(WeatherDataError, match="delete"):
        raw_store.load_raw(moved, cfg)
