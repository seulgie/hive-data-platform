"""Ingest S1 (communes + department boundary) and S2 (RPG parcels) for one department.

Pipeline (each step is a pure function, except fetch and write):

    API -> raw cache (bronze, immutable)
        -> parse + cast types
        -> dedupe on id_parcel (+ invariant: duplicates must be identical)
        -> make_valid + keep polygon parts
        -> department membership (representative point)
        -> validate
        -> GeoParquet (silver) + run manifest

Design decisions come from notebooks/01 and notebooks/02:
- Pagination is mandatory (tiles return up to ~1,100 parcels, page size is 1,000).
- The API returns full, unclipped parcels, so dedupe on id_parcel is safe (checked at runtime).
- A parcel belongs to the department if its representative point lies inside it.
- No business filter here (e.g. orchards only): the silver table keeps every parcel.

Run:  python -m hive_platform.ingest.geo --dept 82 --year 2024
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely
from requests.adapters import HTTPAdapter
from shapely.geometry import MultiPolygon, Polygon, box, mapping
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

CRS_WGS84 = "EPSG:4326"  # storage + API exchange
CRS_L93 = "EPSG:2154"    # metric CRS for France: areas, distances, spatial predicates
GEO_API = "https://geo.api.gouv.fr"
RPG_API = "https://apicarto.ign.fr/api/rpg/v2"
SOURCE_NAME = "apicarto_rpg_v2"

RPG_COLUMNS = ["id_parcel", "surf_parc", "code_cultu", "code_group",
               "culture_d1", "culture_d2", "cat_cult_p"]
STRING_COLUMNS = ["id_parcel", "code_cultu", "code_group", "culture_d1", "culture_d2", "cat_cult_p"]


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
        return self.raw_root / "rpg" / f"rpg_year={self.rpg_year}" / f"dept_code={self.dept_code}"

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


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,                      # waits 1, 2, 4, 8... seconds
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_json(session: requests.Session, url: str, params: dict, timeout: int = 120) -> dict:
    response = session.get(url, params=params, timeout=timeout)
    if not response.ok:
        log.error("HTTP %s from %s: %s", response.status_code, url, response.text[:500])
        response.raise_for_status()
    return response.json()


def write_json_atomic(path: Path, payload: dict) -> None:
    """Write to a temp file, then rename: a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# S1: communes and department boundary
# ---------------------------------------------------------------------------

def fetch_communes(session: requests.Session, cfg: GeoIngestConfig) -> dict:
    """Return the raw communes GeoJSON, from cache if available."""
    path = cfg.communes_raw_path
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    raw = get_json(
        session,
        f"{GEO_API}/departements/{cfg.dept_code}/communes",
        {"format": "geojson", "geometry": "contour", "fields": "code,nom,population"},
        timeout=60,
    )
    write_json_atomic(path, raw)
    return raw


def parse_communes(raw: dict) -> gpd.GeoDataFrame:
    communes = gpd.GeoDataFrame.from_features(raw["features"], crs=CRS_WGS84)
    communes = communes.rename(columns={"code": "insee_code", "nom": "commune_name"})
    communes["insee_code"] = communes["insee_code"].astype("string")
    if communes["insee_code"].duplicated().any():
        raise DataQualityError("duplicate INSEE codes in communes")
    return communes


def build_boundary(communes: gpd.GeoDataFrame) -> shapely.Geometry:
    """Dissolve communes into one department polygon, in Lambert-93."""
    boundary = communes.to_crs(CRS_L93).union_all()
    shapely.prepare(boundary)  # speeds up the many predicates run against it later
    return boundary


# ---------------------------------------------------------------------------
# S2: tiles and raw RPG cache
# ---------------------------------------------------------------------------

def make_tiles(boundary_l93: shapely.Geometry, size_m: int) -> gpd.GeoDataFrame:
    """Square grid in Lambert-93 covering the boundary. Keeps tiles that touch it."""
    minx, miny, maxx, maxy = boundary_l93.bounds
    cells = [
        box(x, y, x + size_m, y + size_m)
        for x in np.arange(minx, maxx, size_m)
        for y in np.arange(miny, maxy, size_m)
    ]
    grid = gpd.GeoDataFrame(geometry=cells, crs=CRS_L93)
    grid = grid[grid.intersects(boundary_l93)].reset_index(drop=True)
    grid["tile_id"] = grid.index.astype(int)
    grid["inside_ratio"] = grid.intersection(boundary_l93).area / grid.area
    return grid


def fetch_rpg_tile(session: requests.Session, geom_wgs84, cfg: GeoIngestConfig) -> tuple[list, int]:
    """Fetch every page for one tile. Returns (features, pages)."""
    features, start, pages = [], 0, 0
    while True:
        params = {
            "annee": cfg.rpg_year,
            "geom": json.dumps(mapping(geom_wgs84)),
            "_limit": cfg.page_size,
            "_start": start,
        }
        batch = get_json(session, RPG_API, params).get("features", [])
        features.extend(batch)
        pages += 1
        if len(batch) < cfg.page_size:
            return features, pages
        start += cfg.page_size
        time.sleep(cfg.request_pause_s)


def cache_tiles(session: requests.Session, tiles_l93: gpd.GeoDataFrame,
                cfg: GeoIngestConfig, stats: RunStats) -> None:
    """Bronze layer: one immutable JSON file per tile. Reruns skip finished tiles."""
    tiles_wgs = tiles_l93.to_crs(CRS_WGS84)
    stats.tiles_total = len(tiles_wgs)
    for row in tiles_wgs.itertuples():
        path = cfg.tiles_raw_dir / f"tile_{row.tile_id:04d}.json"
        if path.exists():
            stats.tiles_from_cache += 1
            continue
        t0 = time.perf_counter()
        features, pages = fetch_rpg_tile(session, row.geometry, cfg)
        write_json_atomic(path, {
            "tile_id": int(row.tile_id),
            "tile_bounds_wgs84": list(row.geometry.bounds),
            "rpg_year": cfg.rpg_year,
            "pages": pages,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "features": features,
        })
        stats.tiles_fetched_now += 1
        log.info("tile %d/%d: %d parcels, %d page(s), %.1fs",
                 row.tile_id + 1, stats.tiles_total, len(features), pages, time.perf_counter() - t0)
        time.sleep(cfg.request_pause_s)


def load_raw_tiles(tiles_dir: Path) -> gpd.GeoDataFrame:
    """Read every cached tile into one GeoDataFrame, with the source tile_id kept for lineage."""
    frames = []
    for path in sorted(tiles_dir.glob("tile_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not payload["features"]:
            continue
        gdf = gpd.GeoDataFrame.from_features(payload["features"], crs=CRS_WGS84)
        gdf["source_tile_id"] = payload["tile_id"]
        frames.append(gdf)
    if not frames:
        raise DataQualityError(f"no parcels found in {tiles_dir}")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=CRS_WGS84)


# ---------------------------------------------------------------------------
# Transform steps (pure functions: GeoDataFrame in, GeoDataFrame out)
# ---------------------------------------------------------------------------

def cast_types(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Enforce the schema. Pandas 'string' dtype keeps null and the literal "NA" distinct."""
    missing = [c for c in RPG_COLUMNS if c not in gdf.columns]
    if missing:
        raise DataQualityError(f"missing source columns: {missing}")
    out = gdf.copy()
    for col in STRING_COLUMNS:
        out[col] = out[col].astype("string")
    out["surf_parc"] = pd.to_numeric(out["surf_parc"], errors="raise").astype("float64")
    return out


def dedupe_parcels(gdf: gpd.GeoDataFrame, stats: RunStats) -> gpd.GeoDataFrame:
    """Drop copies of parcels returned by several tiles.

    Invariant (from notebook 02, check 3): all copies must be identical. If the API ever
    started clipping parcels to the query box, keep-first would silently drop parcel
    pieces, so we fail instead.
    """
    is_dup = gdf["id_parcel"].duplicated(keep=False)
    if is_dup.any():
        dups = gdf[is_dup]
        first = dups.drop_duplicates("id_parcel", keep="first").set_index("id_parcel")
        later = dups[dups["id_parcel"].duplicated(keep="first")]
        ref = first.loc[later["id_parcel"]]

        attrs = [c for c in RPG_COLUMNS if c != "id_parcel"]
        same_attrs = (later[attrs].fillna("<null>").to_numpy()
                      == ref[attrs].fillna("<null>").to_numpy()).all(axis=1)
        same_geom = later.geometry.reset_index(drop=True).geom_equals_exact(
            ref.geometry.reset_index(drop=True), tolerance=1e-7)  # ~1 cm in degrees
        bad = ~(same_attrs & same_geom.to_numpy())
        if bad.any():
            sample = later.loc[bad, "id_parcel"].head(5).tolist()
            raise DataQualityError(f"{bad.sum()} duplicated parcels differ between tiles, e.g. {sample}")

    out = gdf.drop_duplicates("id_parcel", keep="first").reset_index(drop=True)
    stats.duplicates_removed = len(gdf) - len(out)
    return out


def _polygonal_part(geom) -> MultiPolygon | None:
    """Keep only polygon parts and return them as a MultiPolygon (None if nothing is left)."""
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if isinstance(geom, MultiPolygon):
        return geom
    # GeometryCollection (or other): collect polygons from all parts, drop lines and points
    polys = [p for part in shapely.get_parts(geom)
             for p in (part.geoms if isinstance(part, MultiPolygon) else [part])
             if isinstance(p, Polygon) and not p.is_empty]
    return MultiPolygon(polys) if polys else None


def fix_geometries(gdf: gpd.GeoDataFrame, stats: RunStats) -> gpd.GeoDataFrame:
    """make_valid, then normalise every geometry to MultiPolygon (one type for the whole column)."""
    out = gdf.copy()
    invalid = ~out.geometry.is_valid
    stats.invalid_fixed = int(invalid.sum())
    out.loc[invalid, "geometry"] = out.loc[invalid, "geometry"].make_valid()

    stats.collections_split = int((out.geom_type == "GeometryCollection").sum())
    out = out.set_geometry(
        gpd.GeoSeries([_polygonal_part(g) for g in out.geometry], index=out.index, crs=out.crs)
    )
    empty = out.geometry.isna()
    stats.empty_dropped = int(empty.sum())
    return out[~empty].reset_index(drop=True)


def assign_department(gdf: gpd.GeoDataFrame, boundary_l93: shapely.Geometry,
                      stats: RunStats) -> gpd.GeoDataFrame:
    """Keep parcels whose representative point lies in the department.

    representative_point() is always inside the polygon (a centroid may not be), so every
    parcel belongs to exactly one department, even when neighbouring departments are added.
    Spatial predicates run in Lambert-93, on geometries that are already valid.
    """
    l93 = gdf.geometry.to_crs(CRS_L93)
    inside = l93.representative_point().within(boundary_l93).to_numpy()
    crosses = l93.intersects(boundary_l93.boundary).to_numpy()

    out = gdf[inside].copy()
    out["crosses_dept_boundary"] = crosses[inside]
    stats.outside_dept_dropped = int((~inside).sum())
    stats.crossing_boundary = int(out["crosses_dept_boundary"].sum())
    return out.reset_index(drop=True)


def add_lineage(gdf: gpd.GeoDataFrame, cfg: GeoIngestConfig, run_id: str) -> gpd.GeoDataFrame:
    out = gdf.copy()
    out["dept_code"] = cfg.dept_code
    out["rpg_year"] = cfg.rpg_year
    out["source"] = SOURCE_NAME
    out["run_id"] = run_id
    out["ingested_at_utc"] = pd.Timestamp.now(tz="UTC")
    return out


def validate_parcels(gdf: gpd.GeoDataFrame) -> None:
    """Last gate before writing. Every check that fails is reported together."""
    problems = []
    if gdf.empty:
        problems.append("no rows")
    if gdf.crs is None or gdf.crs.to_string() != CRS_WGS84:
        problems.append(f"unexpected CRS {gdf.crs}")
    if gdf["id_parcel"].isna().any():
        problems.append("null id_parcel")
    if gdf["id_parcel"].duplicated().any():
        problems.append(f"{gdf['id_parcel'].duplicated().sum()} duplicate id_parcel")
    if gdf.geometry.isna().any():
        problems.append("null geometry")
    if not gdf.geometry.is_valid.all():
        problems.append(f"{(~gdf.geometry.is_valid).sum()} invalid geometries")
    types = set(gdf.geom_type.unique())
    if types - {"MultiPolygon"}:
        problems.append(f"unexpected geometry types {types}")
    if (gdf["surf_parc"] < 0).any():
        problems.append("negative surf_parc")
    if problems:
        raise DataQualityError("validation failed: " + "; ".join(problems))


# ---------------------------------------------------------------------------
# Write (silver) + manifest
# ---------------------------------------------------------------------------

def write_geoparquet_atomic(gdf: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    gdf.to_parquet(tmp, index=False)
    tmp.replace(path)


def write_manifest(cfg: GeoIngestConfig, run_id: str, started: datetime,
                   stats: RunStats, outputs: list[Path]) -> Path:
    config = {k: str(v) if isinstance(v, Path) else v for k, v in asdict(cfg).items()}
    manifest = {
        "run_id": run_id,
        "pipeline": "ingest.geo",
        "started_at_utc": started.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "stats": asdict(stats),
        "outputs": [str(p) for p in outputs],
    }
    path = cfg.manifest_dir / f"{started:%Y%m%dT%H%M%SZ}_{run_id}.json"
    write_json_atomic(path, manifest)
    return path


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(cfg: GeoIngestConfig, session: requests.Session | None = None) -> RunStats:
    session = session or make_session()
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(timezone.utc)
    stats = RunStats()

    # S1
    communes = parse_communes(fetch_communes(session, cfg))
    boundary_l93 = build_boundary(communes)
    log.info("communes: %d, department area: %.0f km2", len(communes), boundary_l93.area / 1e6)

    # S2: extract (bronze)
    tiles = make_tiles(boundary_l93, cfg.tile_size_m)
    cache_tiles(session, tiles, cfg, stats)

    # S2: transform (bronze -> silver)
    parcels = load_raw_tiles(cfg.tiles_raw_dir)
    stats.rows_raw = len(parcels)
    parcels = cast_types(parcels)
    parcels = dedupe_parcels(parcels, stats)
    parcels = fix_geometries(parcels, stats)
    parcels = assign_department(parcels, boundary_l93, stats)
    parcels = add_lineage(parcels, cfg, run_id)
    validate_parcels(parcels)
    stats.rows_final = len(parcels)

    # Load (silver) + manifest
    write_geoparquet_atomic(communes, cfg.communes_out_path)
    write_geoparquet_atomic(parcels, cfg.parcels_out_path)
    manifest = write_manifest(cfg, run_id, started, stats,
                              [cfg.communes_out_path, cfg.parcels_out_path])
    log.info("done: %s", json.dumps(asdict(stats)))
    log.info("manifest: %s", manifest)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest communes + RPG parcels for one department.")
    parser.add_argument("--dept", default="82")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--tile-size", type=int, default=5000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(GeoIngestConfig(dept_code=args.dept, rpg_year=args.year, tile_size_m=args.tile_size))


if __name__ == "__main__":
    main()