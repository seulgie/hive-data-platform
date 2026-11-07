"""Calls the real Open-Meteo API. Skipped by default; run with:  pytest -m network"""
import pytest

from hive_platform.common.http import make_session
from hive_platform.ingest.weather.client import fetch_archive
from hive_platform.ingest.weather.parse import HOURLY_VARIABLES, parse_hourly


@pytest.mark.network
def test_real_api_still_matches_our_parser():
    raw = fetch_archive(make_session(), 44.10, 1.10, "2025-06-01", "2025-06-01",
                        tuple(HOURLY_VARIABLES))
    df = parse_hourly(raw, "AP-NET")
    assert len(df) == 24
