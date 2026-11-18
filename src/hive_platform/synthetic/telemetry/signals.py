"""Signals of a healthy hive, driven by real weather. Pure functions on numpy arrays.

The goal is not biological fidelity: it is KPIs that react like real ones.
- weight: foragers leave on good days (daytime dip), bring nectar back (cumulative gain,
  large during the crop's bloom), the colony consumes a little every day.
  No foraging when it rains or is cold, so rainy days have no daily dip.
- internal temperature: regulated around 34.5 C, weakly coupled to the outside.
- humidity: 50-65 %, higher when it rains.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from hive_platform.common.season import LOCAL_TZ

# Bloom windows (assumption, local month-day, [start, end)) per crop_variety from S3
BLOOM_WINDOWS = {
    "peach": ("03-10", "04-05"),
    "plum": ("03-15", "04-10"),
    "cherry": ("03-20", "04-15"),
    "pear": ("04-01", "04-25"),
    "apple": ("04-10", "05-10"),
    "kiwi": ("05-15", "06-15"),
    "berry": ("04-15", "06-01"),
    "other_orchard": ("04-01", "05-01"),
}
FORAGE_HOURS_LOCAL = (8, 19)      # bees fly between 08:00 and 19:00 local
MIN_FORAGE_TEMP_C = 12.0
RAIN_THRESHOLD_MM = 0.2           # per hour
BROOD_TEMP_C = 34.5


@dataclass
class HiveParams:
    base_weight_kg: float
    forager_mass_kg: float        # weight that leaves the hive at the peak of a flying day
    bloom_gain_kg_day: float      # net nectar gain on a good day during bloom
    background_gain_kg_day: float # same, outside bloom (other flora)
    consumption_kg_day: float
    temp_coupling: float          # share of (outside - 20 C) leaking into the brood nest


def draw_params(rng: np.random.Generator) -> HiveParams:
    return HiveParams(
        base_weight_kg=rng.uniform(35, 55),
        forager_mass_kg=rng.uniform(0.4, 0.9),
        bloom_gain_kg_day=rng.uniform(0.6, 1.2),
        background_gain_kg_day=rng.uniform(0.15, 0.3),
        consumption_kg_day=rng.uniform(0.15, 0.25),
        temp_coupling=rng.uniform(0.04, 0.1),
    )


def time_grid(start: pd.Timestamp, end: pd.Timestamp, interval_min: int) -> pd.DatetimeIndex:
    """Reading times in [start, end), aligned on the interval (e.g. :00, :15, :30, :45)."""
    first = start.ceil(f"{interval_min}min")
    return pd.date_range(first, end, freq=f"{interval_min}min", inclusive="left")


def weather_at(ts: pd.DatetimeIndex, weather: pd.DataFrame) -> pd.DataFrame:
    """Hourly weather of one apiary, read at each reading time (value of the current hour)."""
    hourly = weather.set_index("ts_utc")[["temperature_2m_c", "precipitation_mm"]]
    return hourly.reindex(ts.floor("h")).set_axis(ts)


def local_hour(ts: pd.DatetimeIndex) -> np.ndarray:
    local = ts.tz_convert(LOCAL_TZ)
    return (local.hour + local.minute / 60).to_numpy(dtype=float)


def forage_activity(ts: pd.DatetimeIndex, wx: pd.DataFrame) -> np.ndarray:
    """0..1 share of foragers outside: a daytime arch, zero when raining or cold."""
    h = local_hour(ts)
    lo, hi = FORAGE_HOURS_LOCAL
    arch = np.where((h >= lo) & (h < hi), np.sin(np.pi * (h - lo) / (hi - lo)), 0.0)
    flying = ((wx["temperature_2m_c"] >= MIN_FORAGE_TEMP_C)
              & (wx["precipitation_mm"] < RAIN_THRESHOLD_MM)).to_numpy()
    return arch * flying


def in_bloom(ts: pd.DatetimeIndex, crop_variety: str, year: int) -> np.ndarray:
    start, end = BLOOM_WINDOWS[crop_variety]
    local = ts.tz_convert(LOCAL_TZ)
    lo = pd.Timestamp(f"{year}-{start}", tz=LOCAL_TZ)
    hi = pd.Timestamp(f"{year}-{end}", tz=LOCAL_TZ)
    return np.asarray((local >= lo) & (local < hi))


def full_day_activity(interval_min: int) -> float:
    """Sum of forage_activity over one fully flyable day: a full day brings the daily gain."""
    lo, hi = FORAGE_HOURS_LOCAL
    n = (hi - lo) * 60 // interval_min
    return float(np.sin(np.pi * np.arange(n) / n).sum())


def healthy_signals(ts: pd.DatetimeIndex, wx: pd.DataFrame, crop_variety: str, year: int,
                    params: HiveParams, interval_min: int,
                    rng: np.random.Generator) -> pd.DataFrame:
    steps_per_day = 24 * 60 // interval_min
    activity = forage_activity(ts, wx)
    daily_gain = np.where(in_bloom(ts, crop_variety, year),
                          params.bloom_gain_kg_day, params.background_gain_kg_day)
    gain = daily_gain * activity / full_day_activity(interval_min)
    consumption = params.consumption_kg_day / steps_per_day

    weight = (params.base_weight_kg
              + np.cumsum(gain - consumption)            # stock of honey and bees
              - params.forager_mass_kg * activity        # foragers outside the hive
              + rng.normal(0, 0.03, len(ts)))            # load-cell noise

    outside = wx["temperature_2m_c"].to_numpy()
    temp = BROOD_TEMP_C + params.temp_coupling * (outside - 20) + rng.normal(0, 0.25, len(ts))

    raining = (wx["precipitation_mm"] >= RAIN_THRESHOLD_MM).to_numpy()
    humidity = np.clip(57 + 6 * raining + rng.normal(0, 2, len(ts)), 35, 90)

    return pd.DataFrame({"weight_kg": weight, "temp_internal_c": temp,
                         "humidity_pct": humidity}, index=ts)
