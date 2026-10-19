"""client.py: HTTP behaviour (errors, retries config, pagination)."""
import pytest
import requests

from hive_platform.ingest.geo import client
from hive_platform.ingest.geo.config import GeoIngestConfig

from .factories import FakeResponse, FakeSession, dept_wgs84, fake_world


def test_make_session_retries_transient_http_errors():
    retry = client.make_session().get_adapter("https://example.org").max_retries

    assert retry.total == 5
    assert {429, 500, 502, 503, 504} <= set(retry.status_forcelist)


def test_get_json_raises_on_http_error():
    class ErrorSession:
        def get(self, url, params=None, timeout=None):
            return FakeResponse(status_code=503)

    with pytest.raises(requests.HTTPError):
        client.get_json(ErrorSession(), "https://example.org", {})


def test_fetch_rpg_tile_paginates(tmp_path):
    session = FakeSession(*fake_world())
    cfg = GeoIngestConfig(page_size=100, request_pause_s=0, raw_root=tmp_path)

    features, pages = client.fetch_rpg_tile(session, dept_wgs84(), cfg)

    assert pages == len(features) // 100 + 1
    assert len({f["properties"]["id_parcel"] for f in features}) == len(features)
