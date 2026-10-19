"""Shared test data and a fake HTTP session. No network anywhere in the geo tests."""
from __future__ import annotations

import json

import geopandas as gpd
import requests
from shapely.geometry import Polygon, box, shape

from hive_platform.ingest.geo.config import CRS_L93, CRS_WGS84

# A simple "department": a 10 km x 10 km square in Lambert-93
DEPT_L93 = box(500_000, 6_300_000, 510_000, 6_310_000)


def dept_wgs84():
    return gpd.GeoSeries([DEPT_L93], crs=CRS_L93).to_crs(CRS_WGS84).iloc[0]


def square(x, y, size=100) -> Polygon:
    return box(x, y, x + size, y + size)


def parcel(id_parcel, geom_l93, code_group="20", cat="CP", surf=1.0) -> dict:
    """One RPG-like record with geometry given in Lambert-93 (converted by to_wgs)."""
    return {"id_parcel": id_parcel, "surf_parc": surf, "code_cultu": "VRG", "code_group": code_group,
            "culture_d1": None, "culture_d2": None, "cat_cult_p": cat, "geometry": geom_l93}


def to_wgs(records: list[dict]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(records, crs=CRS_L93).to_crs(CRS_WGS84)


def fake_world() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Two communes forming DEPT_L93, and a parcel grid that spills east of it."""
    communes = gpd.GeoDataFrame(
        {"code": ["82001", "82002"], "nom": ["West", "East"], "population": [10, 20]},
        geometry=[box(500_000, 6_300_000, 505_000, 6_310_000), box(505_000, 6_300_000, 510_000, 6_310_000)],
        crs=CRS_L93,
    ).to_crs(CRS_WGS84)
    records = [parcel(f"p{i}_{j}", square(500_000 + i * 400, 6_300_000 + j * 400, size=300))
               for i in range(26) for j in range(25)]
    return communes, to_wgs(records)


class FakeResponse:
    def __init__(self, payload: dict | None = None, status_code: int = 200):
        self._payload = payload or {}
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = "" if self.ok else "server error"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    """Serves communes and RPG queries from in-memory data, like the real APIs.

    fail_on_rpg_call=N raises a ConnectionError on the N-th RPG request (1-based),
    to simulate a network failure in the middle of a run.
    """

    def __init__(self, communes: gpd.GeoDataFrame, parcels: gpd.GeoDataFrame,
                 fail_on_rpg_call: int | None = None):
        self.communes_json = json.loads(communes.to_json(drop_id=True))
        self.parcels = parcels
        self.features = json.loads(parcels.to_json(drop_id=True))["features"]
        self.fail_on_rpg_call = fail_on_rpg_call
        self.rpg_calls = 0
        self.communes_calls = 0

    def get(self, url, params=None, timeout=None):
        if "communes" in url:
            self.communes_calls += 1
            return FakeResponse(self.communes_json)
        self.rpg_calls += 1
        if self.rpg_calls == self.fail_on_rpg_call:
            raise requests.ConnectionError("simulated network failure")
        query = shape(json.loads(params["geom"]))
        hits = self.parcels.sindex.query(query, predicate="intersects")
        page = [self.features[i] for i in sorted(hits)][params["_start"]: params["_start"] + params["_limit"]]
        return FakeResponse({"type": "FeatureCollection", "features": page})
