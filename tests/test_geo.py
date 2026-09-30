import geopandas as gpd
from shapely.geometry import box

from hive_platform.ingest.geo import CRS_L93, make_tiles



def test_make_tiles_covers_boundary():
    # 12 km x 7 km rectangle in Lambert-93 -> 3 x 2 tiles of 5 km
    rect = gpd.GeoDataFrame(geometry=[box(500_000, 6_300_300, 512_000, 6_307_000)], crs=CRS_L93)
    tiles = make_tiles(rect.to_crs("EPSG:4326"), size_m=5000)

    assert len(tiles) == 6
    covered = tiles.to_crs(CRS_L93).union_all()
    assert covered.covers(rect.geometry.iloc[0].buffer(-1))
