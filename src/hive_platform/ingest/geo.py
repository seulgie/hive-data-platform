"""S1 + S2: Tarn-et-Garonne boundary, communes, and RPG parcels (real data)."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import box, mapping
from urllib3.util.retry import Retry

DEPT_CODE = "82"
RPG_YEAR = 2024  # latest published vintage; orchards are permanent crops -> valid for season 2025
CRS_WGS84 = "EPSG:4326"
CRS_L93 = "EPSG:2154"
GEO_API = "https://geo.api.gouv.fr"
RPG_API = "https://apicarto.ign.fr/api/rpg/v2"
RAW_DIR = Path("data/raw/geo")

log = logging.getLogger(__name__)


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


# ----------------- S1: communes + department boundary -------------------

# ----------------- S2: RPG parcels via tiled queries -------------------


# ----------------- main -------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()

    communes = fetch_communes(session)
    communes.to_parquet(RAW_DIR / f"communes_{DEPT_CODE}.parquet")
    boundary = dissolve_boundary(communes)
    boundary.to_parquet(RAW_DIR / f"dept_{DEPT_CODE}.parquet")
    log.info("communes: %d", len(communes))

    rpg = fetch_rpg(session, boundary, RPG_YEAR, RAW_DIR / f"rpg_{RPG_YEAR}_tiles")
    rpg.to_parquet(RAW_DIR / f"rpg_{DEPT_CODE}_{RPG_YEAR}.parquet")
    log.info("RPG parcels: %d", len(rpg))
    print(rpg["code_group"].value_counts(().head(20)))")



if __name__ == "__main__":
    main()

