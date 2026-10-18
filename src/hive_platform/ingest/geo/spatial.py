"""Geometry operations: boundary, tiling, repair, department membership.

Pure functions. Metric work (areas, predicates) happens in Lambert-93.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon, box

from .config import CRS_L93, RunStats


def build_boundary(communes: gpd.GeoDataFrame) -> shapely.Geometry:
    """Dissolve communes into one department polygon, in Lambert-93."""
    boundary = communes.to_crs(CRS_L93).union_all()
    shapely.prepare(boundary)  # speeds up the many predicates run against it later
    return boundary


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
