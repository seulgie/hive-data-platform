"""Settings for the weather ingest (S5). No logic, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from hive_platform.common.season import SEASON_END_LOCAL, SEASON_START_LOCAL, season_utc
from hive_platform.synthetic.apiaries import ApiaryConfig

from .parse import HOURLY_VARIABLES

ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
SOURCE_NAME = "open_meteo_archive"


@dataclass
class WeatherConfig:
    seed: int = 42                          # selects the apiary registry (S3) to fetch for
    season_start_local: str = SEASON_START_LOCAL
    season_end_local: str = SEASON_END_LOCAL
    variables: tuple[str, ...] = tuple(HOURLY_VARIABLES)
    request_pause_s: float = 0.5            # free API: be polite
    max_null_share: float = 0.01            # more missing values than this stops the run
    apiary: ApiaryConfig = field(default_factory=ApiaryConfig)

    def __post_init__(self):
        self.apiary.seed = self.seed

    @property
    def season_utc(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return season_utc(self.season_start_local, self.season_end_local)

    @property
    def request_dates(self) -> tuple[str, str]:
        """UTC calendar days covering the season. The API's end_date is inclusive."""
        start, end = self.season_utc
        return start.date().isoformat(), (end - pd.Timedelta(hours=1)).date().isoformat()

    @property
    def registry_path(self) -> Path:
        return self.apiary.out_dir / "apiaries.parquet"

    @property
    def raw_dir(self) -> Path:
        # Cache key: which apiaries (seed) and which dates. Variables are checked on read.
        start, end = self.request_dates
        geo = self.apiary.geo
        return (geo.raw_root / "weather" / SOURCE_NAME / f"dept_code={geo.dept_code}"
                / f"seed={self.seed}" / f"{start}_{end}")

    @property
    def out_path(self) -> Path:
        geo = self.apiary.geo
        return (geo.lake_root / "silver" / "weather_hourly" / f"dept_code={geo.dept_code}"
                / f"seed={self.seed}" / "weather_hourly.parquet")

    @property
    def manifest_dir(self) -> Path:
        return self.apiary.geo.lake_root / "manifests" / "weather"


@dataclass
class WeatherStats:
    apiaries: int = 0
    fetched_now: int = 0
    from_cache: int = 0
    grid_cells: int = 0
    apiaries_sharing_a_cell: int = 0
    hours_per_apiary: int = 0
    rows: int = 0
    nulls: dict = field(default_factory=dict)
