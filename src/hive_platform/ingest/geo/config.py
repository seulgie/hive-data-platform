"""Settings and shared definitions for the geo ingest. No logic, no I/O."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CRS_WGS84 = "EPSG:4326"  # storage + API exchange
CRS_L93 = "EPSG:2154"    # metric CRS for France: areas, distances, spatial predicates
GEO_API = "https://geo.api.gouv.fr"
RPG_API = "https://apicarto.ign.fr/api/rpg/v2"
SOURCE_NAME = "apicarto_rpg_v2"


class DataQualityError(RuntimeError):
    """Raised when data breaks an invariant. The run stops and nothing is written."""


@dataclass
class GeoIngestConfig:
    dept_code: str = "82"
    rpg_year: int = 2024
    tile_size_m: int = 5000
    page_size: int = 1000
    request_pause_s: float = 0.2  # be polite to a free public API
    raw_root: Path = Path("data/raw")
    lake_root: Path = Path("data/lake")

    @property
    def communes_raw_path(self) -> Path:
        return self.raw_root / "geo" / f"communes_{self.dept_code}.geojson"

    @property
    def tiles_raw_dir(self) -> Path:
        # Every input that changes a tile's content must be part of the cache key:
        # tile_0005 is a different box for each tile size.
        return (self.raw_root / "rpg" / f"rpg_year={self.rpg_year}"
                / f"dept_code={self.dept_code}" / f"tile_size_m={self.tile_size_m}")

    @property
    def communes_out_path(self) -> Path:
        return self.lake_root / "silver" / "communes" / f"dept_code={self.dept_code}" / "communes.parquet"

    @property
    def parcels_out_path(self) -> Path:
        return (self.lake_root / "silver" / "rpg_parcels"
                / f"rpg_year={self.rpg_year}" / f"dept_code={self.dept_code}" / "parcels.parquet")

    @property
    def manifest_dir(self) -> Path:
        return self.lake_root / "manifests" / "geo"


@dataclass
class RunStats:
    """Row counts at every step. Written to the manifest so any number can be traced back."""
    tiles_total: int = 0
    tiles_fetched_now: int = 0
    tiles_from_cache: int = 0
    rows_raw: int = 0
    duplicates_removed: int = 0
    invalid_fixed: int = 0
    collections_split: int = 0
    empty_dropped: int = 0
    outside_dept_dropped: int = 0
    crossing_boundary: int = 0
    rows_final: int = 0
