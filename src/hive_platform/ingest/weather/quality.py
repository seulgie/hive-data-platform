"""Quality rules for the hourly weather table. Every rule that can stop a run lives here."""
from __future__ import annotations

import pandas as pd

from .config import WeatherConfig
from .parse import HOURLY_VARIABLES, WeatherDataError

# Plausible physical ranges for Tarn-et-Garonne, March to October
VALID_RANGES = {
    "temperature_2m_c": (-20.0, 50.0),
    "precipitation_mm": (0.0, 150.0),
    "wind_speed_10m_kmh": (0.0, 200.0),
}


def trim_to_season(df: pd.DataFrame, cfg: WeatherConfig) -> pd.DataFrame:
    """The request covers whole UTC days; keep only the season's [start, end) hours."""
    start, end = cfg.season_utc
    return df[(df["ts_utc"] >= start) & (df["ts_utc"] < end)].reset_index(drop=True)


def validate_weather(df: pd.DataFrame, apiary_ids: list[str], cfg: WeatherConfig) -> dict:
    """Checks the whole table; returns null counts per variable. All problems reported together."""
    problems = []
    start, end = cfg.season_utc
    expected_hours = int((end - start) / pd.Timedelta(hours=1))

    if df.duplicated(["apiary_id", "ts_utc"]).any():
        problems.append("duplicate (apiary_id, ts_utc)")
    missing = sorted(set(apiary_ids) - set(df["apiary_id"]))
    if missing:
        problems.append(f"no weather for {missing[:5]}")
    hours = df.groupby("apiary_id")["ts_utc"].nunique()
    short = hours[hours != expected_hours]
    if len(short):
        problems.append(f"{len(short)} apiaries without {expected_hours} hours, e.g. "
                        f"{short.index[0]} has {short.iloc[0]}")

    nulls = {}
    for col, _ in HOURLY_VARIABLES.values():
        nulls[col] = int(df[col].isna().sum())
        if nulls[col] > cfg.max_null_share * len(df):
            problems.append(f"{col}: {nulls[col]} nulls")
        lo, hi = VALID_RANGES[col]
        out_of_range = df[col].notna() & ~df[col].between(lo, hi)
        if out_of_range.any():
            problems.append(f"{col}: {int(out_of_range.sum())} values outside [{lo}, {hi}]")

    if problems:
        raise WeatherDataError("weather validation failed: " + "; ".join(problems))
    return nulls
