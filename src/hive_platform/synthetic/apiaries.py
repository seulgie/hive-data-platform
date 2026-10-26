"""S3: synthetic apiary registry, placed on real orchard parcels.

In a real company this table lives in the field-ops app database. Here a simulator plays
that system: it reads the real silver RPG parcels and writes a registry "export" to
data/raw/synthetic/, as if an operational system had dropped it there. Replacing this
module with a real export later must not change anything downstream.

Business assumptions (decided after notebook 03):
- 40 apiaries, 3 beekeepers, 8-16 hives per apiary (~400 hives).
- Candidate sites: orchard parcels (code_group "20") of at least 1 ha.
- Sites drawn with probability proportional to parcel area, at least 1.5 km apart
  (main foraging radius), so they cluster where orchards are.
- Beekeeper bases: area-weighted k-means (k = 3) of the candidate parcels.
- crop_variety: from the RPG code when it is specific (PRU -> plum, ...); for VRG,
  which hides apple and kiwi, imputed as apple 72% / kiwi 15% / other 13%.
  crop_variety_source tells which is which.

Run:  python -m hive_platform.synthetic.apiaries --seed 42
"""
from __future__ import annotations

import argparse
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from hive_platform.ingest.geo.config import CRS_L93, CRS_WGS84, GeoIngestConfig
from hive_platform.ingest.geo.storage import write_geoparquet_atomic, write_json_atomic

log = logging.getLogger(__name__)

SOURCE_NAME = "synthetic_apiary_registry"
GENERATOR_VERSION = "1"

# RPG codes specific enough to give the crop directly. Anything else in group 20 that is
# not VRG falls back to "other_orchard" (still sourced from the RPG code).
CODE_TO_VARIETY = {"PRU": "plum", "CBT": "cherry", "PVT": "peach", "PWT": "pear", "PFR": "berry"}
VRG_CODE = "VRG"


class RegistryError(RuntimeError):
    """The generated registry breaks one of its own invariants."""


@dataclass
class ApiaryConfig:
    seed: int = 42
    n_apiaries: int = 40
    n_beekeepers: int = 3
    orchard_group: str = "20"
    min_parcel_ha: float = 1.0
    min_spacing_m: float = 1500.0
    hives_min: int = 8
    hives_max: int = 16
    vrg_variety_weights: dict = field(
        default_factory=lambda: {"apple": 0.72, "kiwi": 0.15, "other_orchard": 0.13})
    kmeans_iterations: int = 100
    geo: GeoIngestConfig = field(default_factory=GeoIngestConfig)  # where the silver input lives

    @property
    def out_dir(self) -> Path:
        # The seed is part of the path: a different seed is a different registry (cache-key rule)
        return (self.geo.raw_root / "synthetic" / "apiaries"
                / f"dept_code={self.geo.dept_code}" / f"seed={self.seed}")


@dataclass
class RegistryStats:
    parcels_in: int = 0
    candidates: int = 0
    candidate_area_ha: float = 0.0
    apiaries: int = 0
    hives: int = 0
    apiaries_per_beekeeper: dict = field(default_factory=dict)
    varieties: dict = field(default_factory=dict)
    min_spacing_observed_m: float = 0.0


# ---------------------------------------------------------------------------
# Random streams
# ---------------------------------------------------------------------------

def make_rngs(seed: int) -> dict[str, np.random.Generator]:
    """One independent stream per step: changing one step never shifts the others' draws."""
    names = ["sites", "bases", "varieties", "hives"]
    children = np.random.SeedSequence(seed).spawn(len(names))
    return {name: np.random.default_rng(child) for name, child in zip(names, children)}


# ---------------------------------------------------------------------------
# Steps (pure functions)
# ---------------------------------------------------------------------------

def select_candidates(parcels: gpd.GeoDataFrame, cfg: ApiaryConfig) -> gpd.GeoDataFrame:
    """Orchard parcels large enough for a pollination contract, with a site point in L93."""
    mask = (parcels["code_group"] == cfg.orchard_group) & (parcels["surf_parc"] >= cfg.min_parcel_ha)
    cand = parcels.loc[mask, ["id_parcel", "code_cultu", "surf_parc", "geometry"]].to_crs(CRS_L93)
    cand = cand.sort_values("id_parcel").reset_index(drop=True)   # stable order -> reproducible draws
    cand["site"] = cand.representative_point()                     # always inside the parcel
    return cand


def sample_sites(cand: gpd.GeoDataFrame, cfg: ApiaryConfig,
                 rng: np.random.Generator) -> gpd.GeoDataFrame:
    """Draw sites with probability proportional to area, keeping a minimum spacing.

    Weighted random order (Efraimidis-Spirakis keys u^(1/w)), then greedy acceptance:
    a parcel is kept if its site is at least min_spacing_m from every site kept so far.
    """
    keys = rng.random(len(cand)) ** (1.0 / cand["surf_parc"].to_numpy())
    order = np.argsort(-keys)
    xy = np.column_stack([cand["site"].x.to_numpy(), cand["site"].y.to_numpy()])

    chosen: list[int] = []
    for i in order:
        if all(np.hypot(*(xy[i] - xy[j])) >= cfg.min_spacing_m for j in chosen):
            chosen.append(int(i))
            if len(chosen) == cfg.n_apiaries:
                return cand.iloc[chosen].reset_index(drop=True)
    raise RegistryError(
        f"only {len(chosen)} sites fit with {cfg.min_spacing_m:.0f} m spacing; "
        f"need {cfg.n_apiaries} (lower the spacing or the min parcel size)")


def weighted_kmeans(xy: np.ndarray, weights: np.ndarray, k: int,
                    rng: np.random.Generator, iterations: int) -> np.ndarray:
    """Lloyd's k-means with sample weights and weighted k-means++ initialisation."""
    centers = [xy[rng.choice(len(xy), p=weights / weights.sum())]]
    for _ in range(1, k):
        d2 = np.min([((xy - c) ** 2).sum(axis=1) for c in centers], axis=0)
        p = weights * d2
        centers.append(xy[rng.choice(len(xy), p=p / p.sum())])
    centers = np.array(centers)

    for _ in range(iterations):
        labels = np.argmin(((xy[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2), axis=1)
        new = np.array([
            np.average(xy[labels == j], axis=0, weights=weights[labels == j])
            if (labels == j).any() else centers[j]
            for j in range(k)
        ])
        if np.allclose(new, centers):
            break
        centers = new
    return centers


def place_beekeeper_bases(cand: gpd.GeoDataFrame, cfg: ApiaryConfig,
                          rng: np.random.Generator) -> gpd.GeoDataFrame:
    """Bases at the area-weighted centres of the candidate orchards (L93)."""
    xy = np.column_stack([cand["site"].x.to_numpy(), cand["site"].y.to_numpy()])
    centers = weighted_kmeans(xy, cand["surf_parc"].to_numpy(), cfg.n_beekeepers,
                              rng, cfg.kmeans_iterations)
    centers = centers[np.lexsort((centers[:, 1], centers[:, 0]))]  # west to east -> stable ids
    return gpd.GeoDataFrame(
        {"beekeeper_id": [f"BK-{i + 1}" for i in range(len(centers))]},
        geometry=gpd.points_from_xy(centers[:, 0], centers[:, 1]),
        crs=CRS_L93,
    )


def assign_beekeepers(sites: gpd.GeoDataFrame, bases: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Each apiary goes to the nearest base."""
    out = sites.copy()
    dist = np.column_stack([out["site"].distance(b) for b in bases.geometry])
    nearest = dist.argmin(axis=1)
    out["beekeeper_id"] = bases["beekeeper_id"].to_numpy()[nearest]
    out["distance_to_base_km"] = (dist[np.arange(len(out)), nearest] / 1000).round(2)
    return out


def assign_crop_variety(sites: gpd.GeoDataFrame, cfg: ApiaryConfig,
                        rng: np.random.Generator) -> gpd.GeoDataFrame:
    """Specific RPG codes map directly; VRG is imputed from the configured weights."""
    out = sites.copy()
    is_vrg = (out["code_cultu"] == VRG_CODE).to_numpy()
    names = list(cfg.vrg_variety_weights)
    probs = np.array([cfg.vrg_variety_weights[n] for n in names], dtype=float)
    imputed = rng.choice(names, size=int(is_vrg.sum()), p=probs / probs.sum())

    variety = out["code_cultu"].map(CODE_TO_VARIETY).fillna("other_orchard").to_numpy(dtype=object)
    variety[is_vrg] = imputed
    out["crop_variety"] = variety
    out["crop_variety_source"] = np.where(is_vrg, "imputed", "rpg_code")
    return out


def assign_hives(sites: gpd.GeoDataFrame, cfg: ApiaryConfig,
                 rng: np.random.Generator) -> gpd.GeoDataFrame:
    out = sites.copy()
    out["n_hives"] = rng.integers(cfg.hives_min, cfg.hives_max + 1, size=len(out))
    return out


def commune_of(points_l93: gpd.GeoSeries, communes: gpd.GeoDataFrame) -> pd.DataFrame:
    """INSEE code and name of the commune containing each point (row order preserved)."""
    pts = gpd.GeoDataFrame(geometry=points_l93.reset_index(drop=True), crs=CRS_L93)
    joined = gpd.sjoin(pts, communes.to_crs(CRS_L93)[["insee_code", "commune_name", "geometry"]],
                       how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")].sort_index()
    return joined[["insee_code", "commune_name"]].reset_index(drop=True)


def add_commune(sites: gpd.GeoDataFrame, communes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Commune of each site, for readable dashboards."""
    names = commune_of(sites["site"], communes)
    out = sites.copy()
    out["insee_code"] = names["insee_code"].to_numpy()
    out["commune_name"] = names["commune_name"].to_numpy()
    return out


def finalize(sites: gpd.GeoDataFrame, cfg: ApiaryConfig, run_id: str) -> gpd.GeoDataFrame:
    """Final registry schema: one row per apiary, point geometry in WGS84."""
    # Stable ids: ordered by beekeeper, then west to east
    out = (sites.assign(_x=sites["site"].x)
           .sort_values(["beekeeper_id", "_x"]).drop(columns="_x").reset_index(drop=True))
    points = gpd.GeoSeries(out["site"], crs=CRS_L93).to_crs(CRS_WGS84)
    registry = gpd.GeoDataFrame({
        "apiary_id": [f"AP-{i + 1:03d}" for i in range(len(out))],
        "beekeeper_id": out["beekeeper_id"].to_numpy(),
        "id_parcel": out["id_parcel"].astype("string").to_numpy(),
        "code_cultu": out["code_cultu"].astype("string").to_numpy(),
        "crop_variety": out["crop_variety"].to_numpy(),
        "crop_variety_source": out["crop_variety_source"].to_numpy(),
        "parcel_ha": out["surf_parc"].to_numpy(),
        "n_hives": out["n_hives"].astype("int64").to_numpy(),
        "insee_code": out["insee_code"].to_numpy(),
        "commune_name": out["commune_name"].to_numpy(),
        "distance_to_base_km": out["distance_to_base_km"].to_numpy(),
        "lon": points.x.round(6).to_numpy(),
        "lat": points.y.round(6).to_numpy(),
        "source": SOURCE_NAME,
        "seed": cfg.seed,
        "run_id": run_id,
    }, geometry=points.to_numpy(), crs=CRS_WGS84)
    return registry


def validate_registry(registry: gpd.GeoDataFrame, parcels: gpd.GeoDataFrame,
                      cfg: ApiaryConfig) -> float:
    """Invariants of the generated table. Returns the smallest observed spacing (m)."""
    problems = []
    if len(registry) != cfg.n_apiaries:
        problems.append(f"{len(registry)} apiaries, expected {cfg.n_apiaries}")
    for col in ("apiary_id", "id_parcel"):
        if not registry[col].is_unique:
            problems.append(f"duplicate {col}")
    if not registry["n_hives"].between(cfg.hives_min, cfg.hives_max).all():
        problems.append("n_hives out of range")
    if set(registry["beekeeper_id"]) - {f"BK-{i + 1}" for i in range(cfg.n_beekeepers)}:
        problems.append("unknown beekeeper_id")
    if registry["commune_name"].isna().any():
        problems.append("site outside every commune")

    pts = registry.to_crs(CRS_L93).geometry
    xy = np.column_stack([pts.x, pts.y])
    d = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    np.fill_diagonal(d, np.inf)
    min_spacing = float(d.min()) if len(xy) > 1 else float("inf")
    if min_spacing < cfg.min_spacing_m - 1e-6:
        problems.append(f"sites only {min_spacing:.0f} m apart")

    host = parcels.set_index("id_parcel").loc[registry["id_parcel"]].to_crs(CRS_L93).geometry
    if not all(h.contains(p) for h, p in zip(host, pts)):
        problems.append("site outside its parcel")

    if problems:
        raise RegistryError("registry validation failed: " + "; ".join(problems))
    return min_spacing


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def generate(parcels: gpd.GeoDataFrame, communes: gpd.GeoDataFrame, cfg: ApiaryConfig,
             run_id: str = "test") -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, RegistryStats]:
    """Pure core: inputs in, (registry, bases, stats) out. No file I/O."""
    rngs = make_rngs(cfg.seed)
    stats = RegistryStats(parcels_in=len(parcels))

    cand = select_candidates(parcels, cfg)
    stats.candidates = len(cand)
    stats.candidate_area_ha = round(float(cand["surf_parc"].sum()), 1)

    sites = sample_sites(cand, cfg, rngs["sites"])
    bases = place_beekeeper_bases(cand, cfg, rngs["bases"])
    sites = assign_beekeepers(sites, bases)
    sites = assign_crop_variety(sites, cfg, rngs["varieties"])
    sites = assign_hives(sites, cfg, rngs["hives"])
    sites = add_commune(sites, communes)
    registry = finalize(sites, cfg, run_id)

    stats.min_spacing_observed_m = round(validate_registry(registry, parcels, cfg), 1)
    stats.apiaries = len(registry)
    stats.hives = int(registry["n_hives"].sum())
    stats.apiaries_per_beekeeper = registry["beekeeper_id"].value_counts().sort_index().to_dict()
    stats.varieties = registry["crop_variety"].value_counts().to_dict()

    names = commune_of(bases.geometry, communes)
    bases = bases.assign(insee_code=names["insee_code"].to_numpy(),
                         commune_name=names["commune_name"].to_numpy()).to_crs(CRS_WGS84)
    return registry, bases, stats


def run(cfg: ApiaryConfig) -> RegistryStats:
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(timezone.utc)
    parcels = gpd.read_parquet(cfg.geo.parcels_out_path)
    communes = gpd.read_parquet(cfg.geo.communes_out_path)

    registry, bases, stats = generate(parcels, communes, cfg, run_id)

    write_geoparquet_atomic(registry, cfg.out_dir / "apiaries.parquet")
    write_geoparquet_atomic(bases, cfg.out_dir / "beekeeper_bases.parquet")
    config = asdict(cfg)
    config["geo"] = {k: str(v) for k, v in config["geo"].items()}
    write_json_atomic(cfg.out_dir / "manifest.json", {
        "run_id": run_id,
        "generator": "synthetic.apiaries",
        "generator_version": GENERATOR_VERSION,
        "generated_at_utc": started.isoformat(),
        "input": str(cfg.geo.parcels_out_path),
        "config": config,
        "stats": asdict(stats),
    })
    log.info("registry: %s", json.dumps(asdict(stats), ensure_ascii=False))
    log.info("written to %s", cfg.out_dir)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic apiary registry (S3).")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dept", default="82")
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(ApiaryConfig(seed=args.seed, geo=GeoIngestConfig(dept_code=args.dept, rpg_year=args.year)))


if __name__ == "__main__":
    main()
