"""Parse Open-Meteo archive responses into a tidy hourly table. Pure functions, no I/O."""
from __future__ import annotations

import pandas as pd

# API variable -> (column name carrying its unit, expected unit)
HOURLY_VARIABLES = {
    "temperature_2m": ("temperature_2m_c", "°C"),
    "precipitation": ("precipitation_mm", "mm"),
    "wind_speed_10m": ("wind_speed_10m_kmh", "km/h"),
}


class WeatherDataError(RuntimeError):
    """The API response breaks an assumption the pipeline relies on."""


def parse_hourly(raw: dict, apiary_id: str) -> pd.DataFrame:
    """One row per hour: apiary_id, ts_utc, the model grid point, one column per variable."""
    if raw.get("utc_offset_seconds") != 0:
        raise WeatherDataError(f"expected UTC (offset 0), got {raw.get('utc_offset_seconds')}")

    units = raw.get("hourly_units", {})
    for var, (_, unit) in HOURLY_VARIABLES.items():
        if units.get(var) != unit:
            raise WeatherDataError(f"{var}: expected unit {unit!r}, got {units.get(var)!r}")

    hourly = raw["hourly"]
    # Times carry no offset ("2025-03-29T00:00"). They are UTC only because we asked for GMT.
    ts = pd.DatetimeIndex(pd.to_datetime(hourly["time"], format="%Y-%m-%dT%H:%M")).tz_localize("UTC")
    expected = pd.date_range(ts[0], ts[-1], freq="h")
    if len(ts) != len(expected) or not (ts == expected).all():
        raise WeatherDataError("hourly timestamps are not continuous")

    df = pd.DataFrame({
        "apiary_id": apiary_id,
        "ts_utc": ts,
        "grid_lat": raw["latitude"],    # the model grid point, not the apiary location
        "grid_lon": raw["longitude"],
    })
    for var, (col, _) in HOURLY_VARIABLES.items():
        values = hourly[var]
        if len(values) != len(ts):
            raise WeatherDataError(f"{var}: {len(values)} values for {len(ts)} timestamps")
        df[col] = pd.Series(values, dtype="float64")   # None -> NaN
    return df
