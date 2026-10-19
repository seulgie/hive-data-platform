"""pipeline.py: end-to-end run with a fake API (order of steps, outputs, idempotency)."""
import json

import geopandas as gpd

from hive_platform.ingest import geo
from hive_platform.ingest.geo.config import GeoIngestConfig

from .factories import FakeSession, fake_world


def test_run_end_to_end_and_cache_is_idempotent(tmp_path):
    session = FakeSession(*fake_world())
    cfg = GeoIngestConfig(tile_size_m=3000, page_size=50, request_pause_s=0,
                          raw_root=tmp_path / "raw", lake_root=tmp_path / "lake")

    stats = geo.run(cfg, session=session)

    out = gpd.read_parquet(cfg.parcels_out_path)
    assert len(out) == stats.rows_final > 0
    assert out["id_parcel"].is_unique
    assert stats.duplicates_removed > 0            # parcels on tile edges come back twice
    assert stats.outside_dept_dropped > 0          # parcels east of x = 510 km
    manifests = list(cfg.manifest_dir.glob("*.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["stats"]["rows_final"] == stats.rows_final

    calls_after_first_run = session.rpg_calls
    stats2 = geo.run(cfg, session=session)
    assert session.rpg_calls == calls_after_first_run   # second run reads only the cache
    assert stats2.tiles_from_cache == stats2.tiles_total
    assert stats2.rows_final == stats.rows_final
