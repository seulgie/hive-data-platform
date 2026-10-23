"""Inspect every cached copy of one RPG parcel. Reads the raw tile cache only (no API calls).

Usage:  python scripts/inspect_parcel.py 11491338 [--dept 82] [--year 2024] [--tile-size 5000]
"""
from __future__ import annotations

import argparse

import pandas as pd

from hive_platform.ingest.geo import raw_store
from hive_platform.ingest.geo.config import CRS_L93, GeoIngestConfig
from hive_platform.ingest.geo.quality import RPG_COLUMNS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("id_parcel")
    parser.add_argument("--dept", default="82")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--tile-size", type=int, default=5000)
    args = parser.parse_args()
    cfg = GeoIngestConfig(dept_code=args.dept, rpg_year=args.year, tile_size_m=args.tile_size)

    raw = raw_store.load_raw_tiles(cfg.tiles_raw_dir)
    copies = raw[raw["id_parcel"].astype(str) == args.id_parcel].reset_index(drop=True)
    print(f"{len(copies)} copies of id_parcel={args.id_parcel}\n")
    if copies.empty:
        return

    l93 = copies.to_crs(CRS_L93)
    pd.set_option("display.width", 200)
    print("--- attributes per copy ---")
    print(copies[["source_tile_id", *RPG_COLUMNS[1:]]].to_string())

    print("\n--- geometry per copy (metres, Lambert-93) ---")
    print(pd.DataFrame({
        "source_tile_id": copies["source_tile_id"],
        "geom_type": copies.geom_type,
        "n_parts": [len(getattr(g, "geoms", [g])) for g in copies.geometry],
        "n_vertices": copies.geometry.count_coordinates(),
        "valid": copies.is_valid,
        "area_m2": l93.area.round(2),
        "bounds_l93": [tuple(round(v) for v in g.bounds) for g in l93.geometry],
    }).to_string())

    print("\n--- each copy vs copy 0 ---")
    ref, ref93 = copies.geometry.iloc[0], l93.geometry.iloc[0]
    for i in range(1, len(copies)):
        g, g93 = copies.geometry.iloc[i], l93.geometry.iloc[i]
        same_attrs = (copies.loc[i, RPG_COLUMNS[1:]].fillna("<null>")
                      == copies.loc[0, RPG_COLUMNS[1:]].fillna("<null>"))
        print(f"copy {i} (tile {copies.loc[i, 'source_tile_id']}):")
        print("  attributes differ on      :", list(same_attrs[~same_attrs].index) or "none")
        print("  exact, same vertex order  :", g.equals_exact(ref, 1e-7))
        print("  exact after normalize()   :", g.normalize().equals_exact(ref.normalize(), 1e-7))
        print("  topologically equal       :", g.equals(ref) if g.is_valid and ref.is_valid else "n/a (invalid)")
        print(f"  symmetric difference (m2) : {g93.symmetric_difference(ref93).area:.4f}")
        print(f"  Hausdorff distance (m)    : {g93.hausdorff_distance(ref93):.4f}")
        print(f"  distance between copies(m): {g93.distance(ref93):.1f}")


if __name__ == "__main__":
    main()
