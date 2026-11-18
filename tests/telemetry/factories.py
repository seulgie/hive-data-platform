"""A small synthetic world on disk (S3 + S4 + S5 outputs) for telemetry tests. No network."""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from hive_platform.ingest.geo.config import GeoIngestConfig
from hive_platform.synthetic.telemetry.config import TelemetryConfig

UTC = "UTC"
CROPS = ["apple", "kiwi", "plum", "cherry", "pear", "other_orchard"]


def fake_weather(apiary_ids: list[str], cfg: TelemetryConfig) -> pd.DataFrame:
    """Daily temperature cycle with a seasonal trend; rain 06:00-14:00 UTC every 5th day."""
    start, end = cfg.season_utc
    ts = pd.date_range(start, end, freq="h", inclusive="left")
    hours = np.arange(len(ts))
    temp = 14 + 6 * np.sin(2 * np.pi * hours / (24 * 365)) + 7 * np.sin(2 * np.pi * (hours - 9) / 24)
    rain = np.where(((ts.dayofyear % 5) == 0) & (ts.hour >= 6) & (ts.hour < 14), 1.5, 0.0)
    return pd.concat([pd.DataFrame({
        "apiary_id": a, "ts_utc": ts, "grid_lat": 44.0, "grid_lon": 1.0,
        "temperature_2m_c": temp.round(1), "precipitation_mm": rain, "wind_speed_10m_kmh": 10.0,
    }) for a in apiary_ids], ignore_index=True)


def build_world(root: Path, n_apiaries: int = 3, hives_per_apiary: int = 10,
                n_swaps: int = 2, seed: int = 42) -> TelemetryConfig:
    """Writes S3/S4/S5-shaped files under root and returns a config pointing at them."""
    cfg = TelemetryConfig(seed=seed)
    cfg.hive.apiary.geo = GeoIngestConfig(raw_root=root / "raw", lake_root=root / "lake")
    season_start, _ = cfg.season_utc

    apiary_ids = [f"AP-{i + 1:03d}" for i in range(n_apiaries)]
    apiaries = gpd.GeoDataFrame(
        {"apiary_id": apiary_ids, "crop_variety": [CROPS[i % len(CROPS)] for i in range(n_apiaries)],
         "lat": 44.0, "lon": 1.0, "n_hives": hives_per_apiary},
        geometry=gpd.points_from_xy([1.0] * n_apiaries, [44.0] * n_apiaries), crs="EPSG:4326")
    cfg.apiaries_path.parent.mkdir(parents=True, exist_ok=True)
    apiaries.to_parquet(cfg.apiaries_path)

    hives, assigns, faults = [], [], []
    for i, a in enumerate(apiary_ids):
        installed = season_start + pd.Timedelta(hours=8 + 24 * i)
        for k in range(1, hives_per_apiary + 1):
            hives.append((f"{a}-H{k:02d}", a, installed))
    hives = pd.DataFrame(hives, columns=["hive_id", "apiary_id", "installed_at_utc"])

    swapped = set(hives["hive_id"].iloc[:n_swaps])
    sensor = 0
    for h in hives.itertuples():
        sensor += 1
        if h.hive_id not in swapped:
            assigns.append((f"SN-{sensor:05d}", h.hive_id, h.installed_at_utc, pd.NaT, "install"))
            continue
        fail = h.installed_at_utc + pd.Timedelta(days=60)
        swap = fail + pd.Timedelta(days=2)
        assigns.append((f"SN-{sensor:05d}", h.hive_id, h.installed_at_utc, swap, "install"))
        faults.append(("DROPOUT", h.hive_id, f"SN-{sensor:05d}", fail, swap))
        sensor += 1
        assigns.append((f"SN-{sensor:05d}", h.hive_id, swap, pd.NaT, "swap"))
        faults.append(("SENSOR_SWAP", h.hive_id, f"SN-{sensor:05d}", swap, swap))

    assigns = pd.DataFrame(assigns, columns=["sensor_id", "hive_id", "valid_from_utc",
                                             "valid_to_utc", "reason"])
    assigns["valid_to_utc"] = pd.to_datetime(assigns["valid_to_utc"], utc=True)
    faults = pd.DataFrame(faults, columns=["fault_type", "hive_id", "sensor_id",
                                           "start_utc", "end_utc"])
    faults.insert(0, "event_id", [f"EV-{i + 1:05d}" for i in range(len(faults))])

    cfg.hives_dir.mkdir(parents=True, exist_ok=True)
    hives.to_parquet(cfg.hives_dir / "hives.parquet", index=False)
    assigns.to_parquet(cfg.hives_dir / "sensor_assignments.parquet", index=False)
    faults.to_parquet(cfg.hives_dir / "fault_log.parquet", index=False)

    cfg.weather_path.parent.mkdir(parents=True, exist_ok=True)
    fake_weather(apiary_ids, cfg).to_parquet(cfg.weather_path, index=False)
    return cfg
