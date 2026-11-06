"""HTTP access shared by every ingest source: retries on transient errors, loud failures."""
from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,                      # waits 1, 2, 4, 8... seconds
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_json(session: requests.Session, url: str, params: dict, timeout: int = 120) -> dict:
    response = session.get(url, params=params, timeout=timeout)
    if not response.ok:
        log.error("HTTP %s from %s: %s", response.status_code, url, response.text[:500])
        response.raise_for_status()
    return response.json()
