"""parse.py: tests on the real Open-Meteo response saved by scripts/profile_openmeteo.py."""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from hive_platform.ingest.weather.parse import WeatherDataError, parse_hourly

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "samples" / "openmeteo_archive_sample.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parses_48_continuous_utc_hours_across_the_dst_change(raw):
    df = parse_hourly(raw, "AP-001")

    assert len(df) == 48
    assert str(df["ts_utc"].dt.tz) == "UTC"
    assert df["ts_utc"].iloc[0] == pd.Timestamp("2025-03-29 00:00", tz="UTC")
    assert df["ts_utc"].iloc[-1] == pd.Timestamp("2025-03-30 23:00", tz="UTC")
    assert list(df.columns) == ["apiary_id", "ts_utc", "grid_lat", "grid_lon",
                                "temperature_2m_c", "precipitation_mm", "wind_speed_10m_kmh"]


def test_rejects_a_response_not_in_utc(raw):
    raw["utc_offset_seconds"] = 3600
    with pytest.raises(WeatherDataError, match="UTC"):
        parse_hourly(raw, "AP-001")


def test_rejects_an_unexpected_unit(raw):
    raw["hourly_units"]["wind_speed_10m"] = "m/s"
    with pytest.raises(WeatherDataError, match="wind_speed_10m"):
        parse_hourly(raw, "AP-001")


def test_rejects_a_missing_hour(raw):
    broken = copy.deepcopy(raw)
    for key in broken["hourly"]:
        del broken["hourly"][key][10]          # drop the same hour from every series
    with pytest.raises(WeatherDataError, match="continuous"):
        parse_hourly(broken, "AP-001")


def test_null_values_become_nan(raw):
    raw["hourly"]["precipitation"][5] = None
    df = parse_hourly(raw, "AP-001")
    assert df["precipitation_mm"].isna().sum() == 1
    