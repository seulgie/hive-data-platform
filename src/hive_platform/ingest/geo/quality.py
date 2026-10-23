"""Record-level data quality: schema enforcement, dedupe invariant, final validation gate.

Pure functions. Every rule that can stop a run lives here.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd
import shapely

from .config import CRS_WGS84, DataQualityError, RunStats

RPG_COLUMNS = ["id_parcel", "surf_parc", "code_cultu", "code_group",
               "culture_d1", "culture_d2", "cat_cult_p"]
STRING_COLUMNS = ["id_parcel", "code_cultu", "code_group", "culture_d1", "culture_d2", "cat_cult_p"]


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
    """Collapse rows sharing an id_parcel into one row per parcel.

    Two kinds of duplicates exist in the API responses:
    - identical copies: a parcel on a tile edge is returned by both tiles -> keep one;
    - split records: the source stores several geometries for one parcel. First seen on
      id_parcel 11491338 (one tile, a 6.44 ha body plus a 3 m2 sliver, same attributes)
      -> the parcel's geometry is the union of all its rows. This also rebuilds a parcel
      if copies were ever clipped at a tile edge.
    Attributes must be identical across rows: there is no safe way to choose between
    conflicting values, so the run fails instead.
    """
    is_dup = gdf["id_parcel"].duplicated(keep=False)
    out = gdf.drop_duplicates("id_parcel", keep="first").reset_index(drop=True)
    stats.duplicates_removed = len(gdf) - len(out)
    if not is_dup.any():
        return out

    dups = gdf[is_dup]
    attrs = [c for c in RPG_COLUMNS if c != "id_parcel"]
    attr_key = dups[attrs].astype("string").fillna("<null>").agg("|".join, axis=1)
    conflicting = attr_key.groupby(dups["id_parcel"]).nunique()
    conflicting = conflicting[conflicting > 1]
    if len(conflicting):
        sample = conflicting.index[:5].tolist()
        raise DataQualityError(
            f"{len(conflicting)} parcels have rows with different attributes, e.g. {sample}")

    first_geom = dups.drop_duplicates("id_parcel").set_index("id_parcel").geometry
    ref = gpd.GeoSeries(first_geom.loc[dups["id_parcel"]].to_numpy(), index=dups.index, crs=dups.crs)
    same_geom = dups.geometry.geom_equals_exact(ref, tolerance=1e-7)  # ~1 cm in degrees
    to_merge = dups.loc[~same_geom, "id_parcel"].unique()

    if len(to_merge):
        # make_valid first: union on invalid geometries can raise a GEOS topology error
        merged = (dups[dups["id_parcel"].isin(to_merge)]
                  .geometry.make_valid()
                  .groupby(dups["id_parcel"])
                  .agg(lambda geoms: shapely.union_all(geoms.to_numpy())))
        rows = out["id_parcel"].isin(to_merge)
        out.loc[rows, "geometry"] = gpd.GeoSeries(
            merged.loc[out.loc[rows, "id_parcel"]].to_numpy(), index=out.index[rows], crs=out.crs)
    stats.split_records_merged = len(to_merge)
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
