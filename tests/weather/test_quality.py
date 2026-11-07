"""quality.py: trimming to the season and the validation gate."""
import pandas as pd
import pytest

from hive_platform.ingest.weather import quality
from hive_platform.ingest.weather.config import WeatherConfig
from hive_platform.ingest.weather.parse import WeatherDataError, parse_hourly

from .factories import fake_response


@pytest.fixture
def season_table():
    cfg = WeatherConfig()
    start, end = cfg.request_dates
    frames = [parse_hourly(fake_response(44.0 + i / 10, 1.0, start, end), f"AP-00{i}")
              for i in (1, 2)]
    return cfg, quality.trim_to_season(pd.concat(frames, ignore_index=True), cfg)


def test_trim_keeps_exactly_the_season_hours(season_table):
    cfg, df = season_table
    start, end = cfg.season_utc
    per_apiary = df.groupby("apiary_id")["ts_utc"]
    assert (per_apiary.min() == start).all()
    assert (per_apiary.max() == end - pd.Timedelta(hours=1)).all()
    assert (per_apiary.size() == 245 * 24).all()      # 1 March - 31 October, in hours


def test_validate_accepts_a_clean_table(season_table):
    cfg, df = season_table
    nulls = quality.validate_weather(df, ["AP-001", "AP-002"], cfg)
    assert nulls == {"temperature_2m_c": 0, "precipitation_mm": 0, "wind_speed_10m_kmh": 0}


@pytest.mark.parametrize("breakage, message", [
    (lambda df: df.drop(index=100), "without 5880 hours"),
    (lambda df: pd.concat([df, df.iloc[[0]]]), "duplicate"),
    (lambda df: df.assign(precipitation_mm=-1.0), "precipitation_mm"),
    (lambda df: df.assign(temperature_2m_c=float("nan")), "nulls"),
])
def test_validate_rejects_broken_tables(season_table, breakage, message):
    cfg, df = season_table
    with pytest.raises(WeatherDataError, match=message):
        quality.validate_weather(breakage(df), ["AP-001", "AP-002"], cfg)


def test_validate_rejects_a_missing_apiary(season_table):
    cfg, df = season_table
    with pytest.raises(WeatherDataError, match="AP-003"):
        quality.validate_weather(df, ["AP-001", "AP-002", "AP-003"], cfg)
