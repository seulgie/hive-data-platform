"""Record-level data quality: schema enforcement, dedupe invariant, final validation gate.

Pure functions. Every rule that can stop a run lives here.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd

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
