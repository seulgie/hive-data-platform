"""Faults injected into healthy signals, and their ground-truth rows (S7 fault log).

v1 faults:
- DROPOUT     (from S4) the sensor is dead: no readings in [start, end).
- SWARM       half the colony leaves: weight drops 1.5-3 kg within one hour, May-June daytime.
- DRIFT       the temperature sensor drifts: offset ramps to +3-5 C over 21 days, then stays.
- packet loss 0.5% of readings missing at random. Normal noise, not a fault: not logged.

SWARM and DRIFT are drawn among hives without a sensor swap, so every injected event is
observable by exactly one sensor. The telemetry never carries labels: only this log does.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from hive_platform.common.season import LOCAL_TZ

from .config import TelemetryConfig

FAULT_COLUMNS = ["event_id", "fault_type", "hive_id", "sensor_id",
                 "start_utc", "end_utc", "magnitude"]


def _local_window(year: int, window: tuple[str, str]) -> tuple[pd.Timestamp, pd.Timestamp]:
    lo, hi = window
    return (pd.Timestamp(f"{year}-{lo}", tz=LOCAL_TZ), pd.Timestamp(f"{year}-{hi}", tz=LOCAL_TZ))


def draw_faults(hives: pd.DataFrame, assignments: pd.DataFrame, cfg: TelemetryConfig,
                rng: np.random.Generator) -> pd.DataFrame:
    """New SWARM and DRIFT events, one per selected hive. Deterministic for a given rng."""
    swapped = set(assignments.loc[assignments["reason"] == "swap", "hive_id"])
    eligible = sorted(set(hives["hive_id"]) - swapped)
    sensor_of = assignments.set_index("hive_id")["sensor_id"]  # one sensor per eligible hive
    _, season_end = cfg.season_utc
    year = cfg.season_year
    rows = []

    n_swarm = round(cfg.swarm_rate * len(hives))
    lo, hi = _local_window(year, cfg.swarm_window_local)
    h_lo, h_hi = cfg.swarm_hours_local
    for hive_id in sorted(rng.choice(eligible, size=n_swarm, replace=False)):
        day = lo.normalize() + pd.Timedelta(days=int(rng.integers(0, (hi - lo).days)))
        start = (day + pd.Timedelta(minutes=int(rng.integers(h_lo * 60, h_hi * 60)))
                 ).floor(f"{cfg.interval_min}min").tz_convert("UTC")
        rows.append(("SWARM", hive_id, sensor_of[hive_id], start, start + pd.Timedelta(hours=1),
                     round(float(rng.uniform(*cfg.swarm_drop_kg)), 2)))

    n_drift = round(cfg.drift_rate * len(hives))
    lo, hi = _local_window(year, cfg.drift_window_local)
    for hive_id in sorted(rng.choice(eligible, size=n_drift, replace=False)):
        start = (lo + pd.Timedelta(seconds=int(rng.integers(0, (hi - lo).total_seconds())))
                 ).floor(f"{cfg.interval_min}min").tz_convert("UTC")
        rows.append(("DRIFT", hive_id, sensor_of[hive_id], start, season_end,
                     round(float(rng.uniform(*cfg.drift_offset_c)), 2)))

    out = pd.DataFrame(rows, columns=FAULT_COLUMNS[1:])
    out["start_utc"] = pd.to_datetime(out["start_utc"], utc=True)
    out["end_utc"] = pd.to_datetime(out["end_utc"], utc=True)
    return out


def combine_fault_logs(s4_faults: pd.DataFrame, new_faults: pd.DataFrame) -> pd.DataFrame:
    """S4 events (DROPOUT, SENSOR_SWAP) + S6 events, renumbered in time order."""
    old = s4_faults[FAULT_COLUMNS[1:-1]].assign(magnitude=np.nan)
    log = pd.concat([old, new_faults], ignore_index=True)
    log = log.sort_values(["start_utc", "fault_type", "hive_id"]).reset_index(drop=True)
    log.insert(0, "event_id", [f"EV-{i + 1:05d}" for i in range(len(log))])
    return log


def apply_swarm(ts: pd.DatetimeIndex, weight: np.ndarray, start: pd.Timestamp,
                drop_kg: float) -> np.ndarray:
    """Weight falls linearly by drop_kg over one hour from start, and stays lower."""
    elapsed_h = (ts - start) / pd.Timedelta(hours=1)
    return weight - drop_kg * np.clip(np.asarray(elapsed_h, dtype=float), 0.0, 1.0)


def apply_drift(ts: pd.DatetimeIndex, temp: np.ndarray, start: pd.Timestamp,
                offset_c: float, ramp_days: int) -> np.ndarray:
    """Temperature offset ramps from 0 to offset_c over ramp_days from start, then stays."""
    elapsed_d = (ts - start) / pd.Timedelta(days=1)
    return temp + offset_c * np.clip(np.asarray(elapsed_d, dtype=float) / ramp_days, 0.0, 1.0)
