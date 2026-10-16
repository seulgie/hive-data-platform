"""Tests for hive_platform.ingest.geo. No network: the APIs are replaced by a fake session."""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon, box, shape

from hive_platform.ingest import geo
from hive_platform.ingest.geo import (
    CRS_L93,
    CRS_WGS84,
    DataQualityError,
    GeoIngestConfig,
    RunStats,
)

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parcel(id_parcel, geom_l93, code_group="20", cat="CP", surf=1.0) -> dict:
    """One RPG-like record with geometry given in Lambert-93 (converted later)."""
    return {"id_parcel": id_parcel, "surf_parc": surf, "code_cultu": "VRG", "code_group": code_group,
            "culture_d1": None, "culture_d2": None, "cat_cult_p": cat, "geometry": geom_l93}


def to_wgs(records: list[dict]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(records, crs=CRS_L93).to_crs(CRS_WGS84)


def square(x, y, size=100) -> Polygon:
    return box(x, y, x + size, y + size)


# A simple "department": a 10 km x 10 km square in Lambert-93
DEPT_L93 = box(500_000, 6_300_000, 510_000, 6_310_000)


def dept_wgs84():
    return gpd.GeoSeries([DEPT_L93], crs=CRS_L93).to_crs(CRS_WGS84).iloc[0]


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def test_make_tiles_covers_boundary_and_computes_inside_ratio():
    boundary = box(500_000, 6_300_000, 512_000, 6_307_000)  # 12 km x 7 km -> 3 x 2 tiles
    tiles = geo.make_tiles(boundary, size_m=5000)

    assert len(tiles) == 6
    assert tiles.union_all().covers(boundary)
    assert tiles["inside_ratio"].between(0, 1).all()
    assert tiles["inside_ratio"].max() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

def test_cast_types_keeps_null_and_literal_na_distinct():
    gdf = to_wgs([parcel("1", square(500_000, 6_300_000), cat=None),
                  parcel("2", square(500_200, 6_300_000), cat="NA")])
    out = geo.cast_types(gdf)

    assert pd.isna(out.loc[0, "cat_cult_p"])
    assert out.loc[1, "cat_cult_p"] == "NA"
    assert out["id_parcel"].dtype == "string"
    assert out["surf_parc"].dtype == "float64"


def test_cast_types_fails_on_missing_column():
    gdf = to_wgs([parcel("1", square(500_000, 6_300_000))]).drop(columns="code_group")
    with pytest.raises(DataQualityError, match="missing source columns"):
        geo.cast_types(gdf)


# ---------------------------------------------------------------------------
# Dedupe + invariant
# ---------------------------------------------------------------------------

def test_dedupe_keeps_one_copy_of_identical_duplicates():
    p = parcel("1", square(500_000, 6_300_000))
    gdf = geo.cast_types(to_wgs([p, p, parcel("2", square(500_200, 6_300_000))]))
    stats = RunStats()

    out = geo.dedupe_parcels(gdf, stats)

    assert sorted(out["id_parcel"]) == ["1", "2"]
    assert stats.duplicates_removed == 1


def test_dedupe_fails_when_copies_differ():
    # Same id, but the second copy is clipped to half -> keep-first would lose data
    full = parcel("1", square(500_000, 6_300_000, size=100))
    clipped = parcel("1", box(500_000, 6_300_000, 500_050, 6_300_100))
    gdf = geo.cast_types(to_wgs([full, clipped]))

    with pytest.raises(DataQualityError, match="differ between tiles"):
        geo.dedupe_parcels(gdf, RunStats())


# ---------------------------------------------------------------------------
# Geometry repair
# ---------------------------------------------------------------------------

def test_fix_geometries_repairs_bowtie_and_returns_only_multipolygons():
    bowtie = Polygon([(500_000, 6_300_000), (500_100, 6_300_100),
                      (500_100, 6_300_000), (500_000, 6_300_100)])
    gdf = geo.cast_types(to_wgs([parcel("1", bowtie), parcel("2", square(500_200, 6_300_000))]))
    assert not gdf.geometry.is_valid.all()
    stats = RunStats()

    out = geo.fix_geometries(gdf, stats)

    assert out.geometry.is_valid.all()
    assert set(out.geom_type) == {"MultiPolygon"}
    assert stats.invalid_fixed == 1
    assert out.crs == gdf.crs


def test_polygonal_part_drops_lines_from_collection():
    collection = GeometryCollection([square(0, 0), LineString([(0, 0), (5, 5)])])
    result = geo._polygonal_part(collection)

    assert isinstance(result, MultiPolygon)
    assert result.area == pytest.approx(100 * 100)
    assert geo._polygonal_part(LineString([(0, 0), (1, 1)])) is None


# ---------------------------------------------------------------------------
# Department membership
# ---------------------------------------------------------------------------

def test_assign_department_uses_representative_point():
    records = [
        parcel("inside", square(505_000, 6_305_000)),
        # 80% inside, crosses the east edge -> kept, flagged
        parcel("mostly_in", box(509_920, 6_305_000, 510_020, 6_305_100)),
        # 20% inside -> dropped (belongs to the neighbouring department)
        parcel("mostly_out", box(509_980, 6_306_000, 510_080, 6_306_100)),
        parcel("outside", square(520_000, 6_305_000)),
    ]
    gdf = geo.fix_geometries(geo.cast_types(to_wgs(records)), RunStats())
    stats = RunStats()

    out = geo.assign_department(gdf, DEPT_L93, stats)

    assert sorted(out["id_parcel"]) == ["inside", "mostly_in"]
    flags = dict(zip(out["id_parcel"], out["crosses_dept_boundary"]))
    assert flags == {"inside": False, "mostly_in": True}
    assert stats.outside_dept_dropped == 2


# ---------------------------------------------------------------------------
# Validation gate
# ---------------------------------------------------------------------------

def _clean_frame() -> gpd.GeoDataFrame:
    gdf = geo.cast_types(to_wgs([parcel("1", square(505_000, 6_305_000)),
                                 parcel("2", square(505_200, 6_305_000))]))
    return geo.fix_geometries(gdf, RunStats())


def test_validate_accepts_clean_frame():
    geo.validate_parcels(_clean_frame())


@pytest.mark.parametrize("breakage, message", [
    (lambda g: g.assign(id_parcel=pd.array(["1", "1"], dtype="string")), "duplicate id_parcel"),
    (lambda g: g.assign(surf_parc=[-1.0, 1.0]), "negative surf_parc"),
    (lambda g: g.to_crs(CRS_L93), "unexpected CRS"),
])
def test_validate_rejects_broken_frames(breakage, message):
    with pytest.raises(DataQualityError, match=message):
        geo.validate_parcels(breakage(_clean_frame()))


# ---------------------------------------------------------------------------
# Real fixtures saved by notebook 01
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not (SAMPLES / "rpg_82_2024_tile_sample.geojson").exists(), reason="fixture missing")
def test_real_rpg_sample_passes_transform_steps():
    raw = json.loads((SAMPLES / "rpg_82_2024_tile_sample.geojson").read_text(encoding="utf-8"))
    gdf = gpd.GeoDataFrame.from_features(raw["features"], crs=CRS_WGS84)

    out = geo.fix_geometries(geo.dedupe_parcels(geo.cast_types(gdf), RunStats()), RunStats())
    geo.validate_parcels(out)

    assert (out["cat_cult_p"] == "NA").any()      # literal "NA" survives
    assert out["cat_cult_p"].isna().any()          # real nulls survive too


# ---------------------------------------------------------------------------
# HTTP: pagination + idempotent cache + end-to-end run
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.ok, self.status_code, self.text = True, 200, ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    """Serves communes and RPG queries from in-memory data, like the real APIs."""

    def __init__(self, communes: gpd.GeoDataFrame, parcels: gpd.GeoDataFrame):
        self.communes_json = json.loads(communes.to_json(drop_id=True))
        self.parcels = parcels
        self.features = json.loads(parcels.to_json(drop_id=True))["features"]
        self.rpg_calls = 0

    def get(self, url, params=None, timeout=None):
        if "communes" in url:
            return FakeResponse(self.communes_json)
        self.rpg_calls += 1
        query = shape(json.loads(params["geom"]))
        hits = self.parcels.sindex.query(query, predicate="intersects")
        page = [self.features[i] for i in sorted(hits)][params["_start"]: params["_start"] + params["_limit"]]
        return FakeResponse({"type": "FeatureCollection", "features": page})


def _fake_world():
    communes = gpd.GeoDataFrame(
        {"code": ["82001", "82002"], "nom": ["West", "East"], "population": [10, 20]},
        geometry=[box(500_000, 6_300_000, 505_000, 6_310_000), box(505_000, 6_300_000, 510_000, 6_310_000)],
        crs=CRS_L93,
    ).to_crs(CRS_WGS84)
    records = [parcel(f"p{i}_{j}", square(500_000 + i * 400, 6_300_000 + j * 400, size=300))
               for i in range(26) for j in range(25)]          # some parcels lie east of the dept
    return communes, to_wgs(records)


def test_fetch_rpg_tile_paginates(tmp_path):
    communes, parcels = _fake_world()
    session = FakeSession(communes, parcels)
    cfg = GeoIngestConfig(page_size=100, request_pause_s=0, raw_root=tmp_path)

    features, pages = geo.fetch_rpg_tile(session, dept_wgs84(), cfg)

    assert pages == len(features) // 100 + 1
    assert len({f["properties"]["id_parcel"] for f in features}) == len(features)


def test_run_end_to_end_and_cache_is_idempotent(tmp_path):
    communes, parcels = _fake_world()
    session = FakeSession(communes, parcels)
    cfg = GeoIngestConfig(tile_size_m=3000, page_size=50, request_pause_s=0,
                          raw_root=tmp_path / "raw", lake_root=tmp_path / "lake")

    stats = geo.run(cfg, session=session)

    out = gpd.read_parquet(cfg.parcels_out_path)
    assert len(out) == stats.rows_final > 0
    assert out["id_parcel"].is_unique
    assert stats.duplicates_removed > 0            # 3 km tiles over 400 m-spaced parcels -> overlaps
    assert stats.outside_dept_dropped > 0          # parcels east of x = 510 km
    manifests = list(cfg.manifest_dir.glob("*.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["stats"]["rows_final"] == stats.rows_final

    calls_after_first_run = session.rpg_calls
    stats2 = geo.run(cfg, session=session)
    assert session.rpg_calls == calls_after_first_run   # second run reads only the cache
    assert stats2.tiles_from_cache == stats2.tiles_total
    assert stats2.rows_final == stats.rows_final