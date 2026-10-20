"""spatial.py: tiling, geometry repair, department membership."""
import pytest
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon, box

from hive_platform.ingest.geo import quality, spatial
from hive_platform.ingest.geo.config import RunStats

from .factories import DEPT_L93, parcel, square, to_wgs


def test_make_tiles_covers_boundary_and_computes_inside_ratio():
    boundary = box(500_000, 6_300_000, 512_000, 6_307_000)  # 12 km x 7 km -> 3 x 2 tiles
    tiles = spatial.make_tiles(boundary, size_m=5000)

    assert len(tiles) == 6
    assert tiles.union_all().covers(boundary)
    assert tiles["inside_ratio"].between(0, 1).all()
    assert tiles["inside_ratio"].max() == pytest.approx(1.0)


def test_fix_geometries_repairs_bowtie_and_returns_only_multipolygons():
    bowtie = Polygon([(500_000, 6_300_000), (500_100, 6_300_100),
                      (500_100, 6_300_000), (500_000, 6_300_100)])
    gdf = quality.cast_types(to_wgs([parcel("1", bowtie), parcel("2", square(500_200, 6_300_000))]))
    assert not gdf.geometry.is_valid.all()
    stats = RunStats()

    out = spatial.fix_geometries(gdf, stats)

    assert out.geometry.is_valid.all()
    assert set(out.geom_type) == {"MultiPolygon"}
    assert stats.invalid_fixed == 1
    assert out.crs == gdf.crs


def test_polygonal_part_drops_lines_from_collection():
    collection = GeometryCollection([square(0, 0), LineString([(0, 0), (5, 5)])])
    result = spatial._polygonal_part(collection)

    assert isinstance(result, MultiPolygon)
    assert result.area == pytest.approx(100 * 100)
    assert spatial._polygonal_part(LineString([(0, 0), (1, 1)])) is None


def test_assign_department_uses_representative_point():
    records = [
        parcel("inside", square(505_000, 6_305_000)),
        # 80% inside, crosses the east edge -> kept, flagged
        parcel("mostly_in", box(509_920, 6_305_000, 510_020, 6_305_100)),
        # 20% inside -> dropped (belongs to the neighbouring department)
        parcel("mostly_out", box(509_980, 6_306_000, 510_080, 6_306_100)),
        parcel("outside", square(520_000, 6_305_000)),
    ]
    gdf = spatial.fix_geometries(quality.cast_types(to_wgs(records)), RunStats())
    stats = RunStats()

    out = spatial.assign_department(gdf, DEPT_L93, stats)

    assert sorted(out["id_parcel"]) == ["inside", "mostly_in"]
    flags = dict(zip(out["id_parcel"], out["crosses_dept_boundary"]))
    assert flags == {"inside": False, "mostly_in": True}
    assert stats.outside_dept_dropped == 2

