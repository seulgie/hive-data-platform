"""quality.py: schema enforcement, dedupe invariant, validation gate."""
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from hive_platform.ingest.geo import quality, spatial
from hive_platform.ingest.geo.config import CRS_L93, CRS_WGS84, DataQualityError, RunStats

from .factories import parcel, square, to_wgs

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"


def test_cast_types_keeps_null_and_literal_na_distinct():
    gdf = to_wgs([parcel("1", square(500_000, 6_300_000), cat=None),
                  parcel("2", square(500_200, 6_300_000), cat="NA")])
    out = quality.cast_types(gdf)

    assert pd.isna(out.loc[0, "cat_cult_p"])
    assert out.loc[1, "cat_cult_p"] == "NA"
    assert out["id_parcel"].dtype == "string"
    assert out["surf_parc"].dtype == "float64"


def test_cast_types_fails_on_missing_column():
    gdf = to_wgs([parcel("1", square(500_000, 6_300_000))]).drop(columns="code_group")
    with pytest.raises(DataQualityError, match="missing source columns"):
        quality.cast_types(gdf)


def test_dedupe_keeps_one_copy_of_identical_duplicates():
    p = parcel("1", square(500_000, 6_300_000))
    gdf = quality.cast_types(to_wgs([p, p, parcel("2", square(500_200, 6_300_000))]))
    stats = RunStats()

    out = quality.dedupe_parcels(gdf, stats)

    assert sorted(out["id_parcel"]) == ["1", "2"]
    assert stats.duplicates_removed == 1


def test_dedupe_fails_when_copies_differ():
    # Same id, but the second copy is clipped to half -> keep-first would lose data
    full = parcel("1", square(500_000, 6_300_000, size=100))
    clipped = parcel("1", box(500_000, 6_300_000, 500_050, 6_300_100))
    gdf = quality.cast_types(to_wgs([full, clipped]))

    with pytest.raises(DataQualityError, match="differ between tiles"):
        quality.dedupe_parcels(gdf, RunStats())


def _clean_frame() -> gpd.GeoDataFrame:
    gdf = quality.cast_types(to_wgs([parcel("1", square(505_000, 6_305_000)),
                                     parcel("2", square(505_200, 6_305_000))]))
    return spatial.fix_geometries(gdf, RunStats())


def test_validate_accepts_clean_frame():
    quality.validate_parcels(_clean_frame())


@pytest.mark.parametrize("breakage, message", [
    (lambda g: g.assign(id_parcel=pd.array(["1", "1"], dtype="string")), "duplicate id_parcel"),
    (lambda g: g.assign(surf_parc=[-1.0, 1.0]), "negative surf_parc"),
    (lambda g: g.to_crs(CRS_L93), "unexpected CRS"),
])
def test_validate_rejects_broken_frames(breakage, message):
    with pytest.raises(DataQualityError, match=message):
        quality.validate_parcels(breakage(_clean_frame()))


@pytest.mark.skipif(not (SAMPLES / "rpg_82_2024_tile_sample.geojson").exists(), reason="fixture missing")
def test_real_rpg_sample_passes_transform_steps():
    raw = json.loads((SAMPLES / "rpg_82_2024_tile_sample.geojson").read_text(encoding="utf-8"))
    gdf = gpd.GeoDataFrame.from_features(raw["features"], crs=CRS_WGS84)

    out = spatial.fix_geometries(quality.dedupe_parcels(quality.cast_types(gdf), RunStats()), RunStats())
    quality.validate_parcels(out)

    assert (out["cat_cult_p"] == "NA").any()      # literal "NA" survives
    assert out["cat_cult_p"].isna().any()          # real nulls survive too
