"""One hive, one season: healthy signals + its faults, split across the sensors it carried.

The hive's physical state (weight, brood temperature) is continuous over the season;
sensors only observe it while mounted and alive. A swapped hive therefore produces
readings under two sensor_ids, joined back to the hive with resolve_hive (SCD2).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import faults as flt
from . import signals as sig
from .config import TelemetryConfig


def generate_hive(hive: pd.Series, assignments: pd.DataFrame, hive_faults: pd.DataFrame,
                  weather: pd.DataFrame, crop_variety: str, cfg: TelemetryConfig,
                  rng: np.random.Generator) -> tuple[pd.DataFrame, int]:
    """Readings (sensor_id, ts_utc, weight_kg, temp_internal_c, humidity_pct, battery_v),
    and the number of readings expected while a live sensor was mounted (before packet loss)."""
    _, season_end = cfg.season_utc
    ts = sig.time_grid(hive["installed_at_utc"], season_end, cfg.interval_min)
    wx = sig.weather_at(ts, weather)
    params = sig.draw_params(rng)
    s = sig.healthy_signals(ts, wx, crop_variety, cfg.season_year, params, cfg.interval_min, rng)
    weight = s["weight_kg"].to_numpy()
    temp = s["temp_internal_c"].to_numpy()

    for f in hive_faults.itertuples():
        if f.fault_type == "SWARM":
            weight = flt.apply_swarm(ts, weight, f.start_utc, f.magnitude)
        elif f.fault_type == "DRIFT":
            temp = flt.apply_drift(ts, temp, f.start_utc, f.magnitude, cfg.drift_ramp_days)

    frames = []
    for a in assignments.sort_values("valid_from_utc").itertuples():
        end = a.valid_to_utc if pd.notna(a.valid_to_utc) else season_end
        mounted = (ts >= a.valid_from_utc) & (ts < end)
        for d in hive_faults[(hive_faults["fault_type"] == "DROPOUT")
                             & (hive_faults["sensor_id"] == a.sensor_id)].itertuples():
            mounted &= ~((ts >= d.start_utc) & (ts < d.end_utc))   # dead sensor: no data
        idx = np.flatnonzero(mounted)
        if not len(idx):
            continue
        # Battery: 4.15 V when mounted, -0.25 V over a full season, sensor-specific
        age = (ts[idx] - a.valid_from_utc) / (season_end - ts[0])
        battery = 4.15 - 0.25 * np.asarray(age, dtype=float) + rng.normal(0, 0.01, len(idx))
        frames.append(pd.DataFrame({
            "sensor_id": a.sensor_id,
            "ts_utc": ts[idx],
            "weight_kg": weight[idx],
            "temp_internal_c": temp[idx],
            "humidity_pct": s["humidity_pct"].to_numpy()[idx],
            "battery_v": battery,
        }))

    out = pd.concat(frames, ignore_index=True)
    expected = len(out)
    keep = rng.random(len(out)) >= cfg.packet_loss_rate          # random transmission losses
    out = out[keep].reset_index(drop=True)
    for col in ("weight_kg", "temp_internal_c", "humidity_pct", "battery_v"):
        out[col] = out[col].round(3).astype("float32")
    return out, expected
