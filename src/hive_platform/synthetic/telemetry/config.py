"""Settings for the telemetry simulator (S6). No logic, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from hive_platform.common.season import SEASON_END_LOCAL, SEASON_START_LOCAL, season_utc
from hive_platform.ingest.weather.config import WeatherConfig
from hive_platform.synthetic.hives import HiveConfig

SOURCE_NAME = "synthetic_iot_platform"
GENERATOR_VERSION = "1"


@dataclass
class TelemetryConfig:
    seed: int = 42                         # same seed as S3/S4/S5: one synthetic world
    interval_min: int = 15
    season_start_local: str = SEASON_START_LOCAL
    season_end_local: str = SEASON_END_LOCAL

    # Faults injected in v1 (DROPOUT comes from the S4 fault log)
    swarm_rate: float = 0.05               # share of hives that swarm once
    swarm_window_local: tuple[str, str] = ("05-01", "07-01")   # [start, end) month-day
    swarm_hours_local: tuple[int, int] = (11, 15)
    swarm_drop_kg: tuple[float, float] = (1.5, 3.0)
    drift_rate: float = 0.03               # share of hives whose temperature sensor drifts
    drift_window_local: tuple[str, str] = ("04-01", "09-01")
    drift_ramp_days: int = 21
    drift_offset_c: tuple[float, float] = (3.0, 5.0)
    packet_loss_rate: float = 0.005        # random missing readings, not logged as faults

    hives_per_batch: int = 64              # memory bound: hives generated before each write
    hive: HiveConfig = field(default_factory=HiveConfig)

    def __post_init__(self):
        self.hive.seed = self.seed
        self.hive.apiary.seed = self.seed

    @property
    def season_utc(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return season_utc(self.season_start_local, self.season_end_local)

    @property
    def season_year(self) -> int:
        return pd.Timestamp(self.season_start_local).year

    # --- inputs ---
    @property
    def apiaries_path(self) -> Path:
        return self.hive.apiary.out_dir / "apiaries.parquet"

    @property
    def hives_dir(self) -> Path:
        return self.hive.out_dir

    @property
    def weather_path(self) -> Path:
        return WeatherConfig(seed=self.seed, apiary=self.hive.apiary).out_path

    # --- outputs ("export" of a simulated IoT platform) ---
    @property
    def out_dir(self) -> Path:
        geo = self.hive.apiary.geo
        return (geo.raw_root / "synthetic" / "telemetry"
                / f"dept_code={geo.dept_code}" / f"seed={self.seed}")

    @property
    def readings_dir(self) -> Path:
        return self.out_dir / "readings"


@dataclass
class TelemetryStats:
    hives: int = 0
    sensors_reporting: int = 0
    rows: int = 0
    months: list = field(default_factory=list)
    expected_readings: int = 0
    completeness: float = 0.0              # rows / expected, outside DROPOUT intervals
    faults: dict = field(default_factory=dict)
