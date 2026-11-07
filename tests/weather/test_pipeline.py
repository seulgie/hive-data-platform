"""pipeline.py: end-to-end run on a fake registry and a fake API."""
import json

import pandas as pd

from hive_platform.ingest.geo.config import GeoIngestConfig
from hive_platform.ingest.weather import pipeline
from hive_platform.ingest.weather.config import WeatherConfig
from hive_platform.synthetic.apiaries import ApiaryConfig

from .factories import FakeOpenMeteo, fake_registry, write_registry


def test_run_end_to_end_and_second_run_uses_only_the_cache(tmp_path):
    geo = GeoIngestConfig(raw_root=tmp_path / "raw", lake_root=tmp_path / "lake")
    cfg = WeatherConfig(request_pause_s=0, apiary=ApiaryConfig(geo=geo))
    registry = fake_registry(6)
    write_registry(cfg.registry_path, registry)
    session = FakeOpenMeteo()

    stats = pipeline.run(cfg, session=session)

    out = pd.read_parquet(cfg.out_path)
    assert len(out) == stats.rows == 6 * 245 * 24
    assert str(out["ts_utc"].dt.tz) == "UTC"
    assert (stats.grid_cells, stats.apiaries_sharing_a_cell) == (3, 6)   # pairs share a cell
    manifest = json.loads(next(cfg.manifest_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert manifest["stats"]["rows"] == stats.rows

    stats2 = pipeline.run(cfg, session=session)
    assert len(session.calls) == 6                     # nothing fetched the second time
    assert stats2.from_cache == 6
