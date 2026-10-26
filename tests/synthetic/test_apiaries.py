"""Tests for hive_platform.synthetic.apiaries, on a small fake department (no files needed)."""
from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from hive_platform.ingest.geo.config import CRS_L93, CRS_WGS84, GeoIngestConfig
from hive_platform.synthetic import apiaries as ap
from hive_platform.synthetic.apiaries import ApiaryConfig, RegistryError

X0, Y0 = 500_000, 6_300_000          # Lambert-93 origin of the fake department (40 km x 30 km)


def fake_communes() -> gpd.GeoDataFrame:
    cells = [box(X0 + i * 20_000, Y0 + j * 15_000, X0 + (i + 1) * 20_000, Y0 + (j + 1) * 15_000)
             for i in range(2) for j in range(2)]
    return gpd.GeoDataFrame({"insee_code": ["82001", "82002", "82003", "82004"],
                             "commune_name": ["SW", "NW", "SE", "NE"]},
                            geometry=cells, crs=CRS_L93).to_crs(CRS_WGS84)


def fake_parcels() -> gpd.GeoDataFrame:
    """A parcel every 1 km. West half: large orchards; east half: small orchards.
    Every 4th position is wheat, every 7th a sub-hectare orchard, VRG and PRU alternate."""
    rows, k = [], 0
    for i in range(40):
        for j in range(30):
            x, y = X0 + i * 1000 + 300, Y0 + j * 1000 + 300
            ha = 5.0 if i < 20 else 1.2
            group, code = "20", ("VRG" if k % 2 == 0 else "PRU")
            if k % 4 == 3:
                group, code = "1", "BTH"
            if k % 7 == 6:
                ha = 0.4
            side = (ha * 10_000) ** 0.5
            rows.append({"id_parcel": f"P{k:05d}", "code_cultu": code, "code_group": group,
                         "surf_parc": ha, "geometry": box(x, y, x + side, y + side)})
            k += 1
    return gpd.GeoDataFrame(rows, crs=CRS_L93).to_crs(CRS_WGS84)


@pytest.fixture(scope="module")
def parcels():
    return fake_parcels()


@pytest.fixture(scope="module")
def communes():
    return fake_communes()


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def test_select_candidates_keeps_large_orchards_only(parcels):
    cand = ap.select_candidates(parcels, ApiaryConfig())

    assert (cand["surf_parc"] >= 1.0).all()
    assert set(cand["code_cultu"]) <= {"VRG", "PRU"}
    assert cand.crs == CRS_L93
    assert all(g.contains(p) for g, p in zip(cand.geometry, cand["site"]))


# ---------------------------------------------------------------------------
# Site sampling
# ---------------------------------------------------------------------------

def test_sample_sites_respects_count_and_spacing(parcels):
    cfg = ApiaryConfig()
    cand = ap.select_candidates(parcels, cfg)
    sites = ap.sample_sites(cand, cfg, np.random.default_rng(1))

    assert len(sites) == cfg.n_apiaries
    xy = np.column_stack([sites["site"].x, sites["site"].y])
    d = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    np.fill_diagonal(d, np.inf)
    assert d.min() >= cfg.min_spacing_m


def test_sample_sites_prefers_large_parcels(parcels):
    # Without spacing, large (5 ha) parcels carry ~80% of candidate area
    cfg = ApiaryConfig(n_apiaries=20, min_spacing_m=0)
    cand = ap.select_candidates(parcels, cfg)
    west_share = np.mean([
        (ap.sample_sites(cand, cfg, np.random.default_rng(s))["surf_parc"] == 5.0).mean()
        for s in range(30)
    ])
    assert 0.7 < west_share < 0.9


def test_sample_sites_fails_when_spacing_cannot_be_met(parcels):
    cfg = ApiaryConfig(min_spacing_m=50_000)
    cand = ap.select_candidates(parcels, cfg)
    with pytest.raises(RegistryError, match="sites fit"):
        ap.sample_sites(cand, cfg, np.random.default_rng(0))


# ---------------------------------------------------------------------------
# Beekeeper bases
# ---------------------------------------------------------------------------

def test_weighted_kmeans_finds_obvious_clusters():
    rng = np.random.default_rng(0)
    xy = np.vstack([rng.normal([0, 0], 100, (200, 2)), rng.normal([10_000, 0], 100, (200, 2))])
    centers = ap.weighted_kmeans(xy, np.ones(len(xy)), k=2, rng=rng, iterations=100)
    centers = centers[np.argsort(centers[:, 0])]

    assert np.allclose(centers, [[0, 0], [10_000, 0]], atol=50)


def test_weighted_kmeans_is_pulled_by_weights():
    xy = np.array([[0.0, 0.0], [1000.0, 0.0]])
    center = ap.weighted_kmeans(xy, np.array([1.0, 3.0]), k=1,
                                rng=np.random.default_rng(0), iterations=10)
    assert center[0, 0] == pytest.approx(750.0)


# ---------------------------------------------------------------------------
# Crop variety
# ---------------------------------------------------------------------------

def test_crop_variety_maps_specific_codes_and_imputes_vrg():
    sites = pd.DataFrame({"code_cultu": ["PRU", "CBT", "AGR"] + ["VRG"] * 20_000})
    cfg = ApiaryConfig()
    out = ap.assign_crop_variety(sites, cfg, np.random.default_rng(0))

    assert out["crop_variety"].iloc[:3].tolist() == ["plum", "cherry", "other_orchard"]
    assert (out["crop_variety_source"].iloc[:3] == "rpg_code").all()
    vrg = out.iloc[3:]
    assert (vrg["crop_variety_source"] == "imputed").all()
    shares = vrg["crop_variety"].value_counts(normalize=True)
    for name, weight in cfg.vrg_variety_weights.items():
        assert shares[name] == pytest.approx(weight, abs=0.01)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_generate_builds_a_valid_registry(parcels, communes):
    cfg = ApiaryConfig()
    registry, bases, stats = ap.generate(parcels, communes, cfg)

    assert len(registry) == cfg.n_apiaries
    assert registry["apiary_id"].tolist() == [f"AP-{i:03d}" for i in range(1, 41)]
    assert registry["n_hives"].between(cfg.hives_min, cfg.hives_max).all()
    assert stats.hives == registry["n_hives"].sum()
    assert sum(stats.apiaries_per_beekeeper.values()) == cfg.n_apiaries
    assert len(bases) == cfg.n_beekeepers and bases["commune_name"].notna().all()
    assert registry.crs == CRS_WGS84
    assert stats.min_spacing_observed_m >= cfg.min_spacing_m


def test_generate_is_deterministic_per_seed(parcels, communes):
    a, _, _ = ap.generate(parcels, communes, ApiaryConfig(seed=7))
    b, _, _ = ap.generate(parcels, communes, ApiaryConfig(seed=7))
    c, _, _ = ap.generate(parcels, communes, ApiaryConfig(seed=8))

    pd.testing.assert_frame_equal(pd.DataFrame(a.drop(columns="geometry")),
                                  pd.DataFrame(b.drop(columns="geometry")))
    assert set(a["id_parcel"]) != set(c["id_parcel"])


def test_validate_registry_catches_broken_tables(parcels, communes):
    cfg = ApiaryConfig()
    registry, _, _ = ap.generate(parcels, communes, cfg)

    too_close = registry.copy()
    too_close.loc[1, "geometry"] = too_close.loc[0, "geometry"]
    with pytest.raises(RegistryError, match="apart"):
        ap.validate_registry(too_close, parcels, cfg)

    bad_hives = registry.assign(n_hives=99)
    with pytest.raises(RegistryError, match="n_hives"):
        ap.validate_registry(bad_hives, parcels, cfg)


def test_run_writes_registry_bases_and_manifest(tmp_path, parcels, communes):
    geo_cfg = GeoIngestConfig(raw_root=tmp_path / "raw", lake_root=tmp_path / "lake")
    geo_cfg.parcels_out_path.parent.mkdir(parents=True)
    geo_cfg.communes_out_path.parent.mkdir(parents=True)
    parcels.to_parquet(geo_cfg.parcels_out_path)
    communes.to_parquet(geo_cfg.communes_out_path)
    cfg = ApiaryConfig(seed=3, geo=geo_cfg)

    stats = ap.run(cfg)

    assert "seed=3" in str(cfg.out_dir)
    registry = gpd.read_parquet(cfg.out_dir / "apiaries.parquet")
    assert len(registry) == stats.apiaries == 40
    assert len(gpd.read_parquet(cfg.out_dir / "beekeeper_bases.parquet")) == 3
    manifest = json.loads((cfg.out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["config"]["seed"] == 3
    assert manifest["stats"]["hives"] == stats.hives
