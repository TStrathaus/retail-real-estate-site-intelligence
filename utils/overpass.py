"""Resilient Overpass access for every OSMnx call in the tool.

The public Overpass endpoint (overpass-api.de) regularly refuses or
throttles shared cloud IPs (Streamlit Community Cloud) and is often at
capacity. OSMnx 2.x talks to exactly one endpoint and, on 429/504, sleeps
55 s and retries forever — a hosted session then hangs or dies.

`configure_osmnx()` replaces OSMnx's internal Overpass request with one
that:
  • answers from the local cache first (seeded with the demo presets),
  • tries a list of public mirrors, each with a bounded client timeout,
  • stores every response under the PRIMARY endpoint's cache key, so a
    result fetched from any mirror is reused regardless of which mirror
    answered,
  • raises `OverpassUnavailable` with a readable message when all fail.

Override the mirror list with `OVERPASS_URLS` (comma-separated) in the
environment or Streamlit secrets.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from collections import OrderedDict
from typing import Any

import requests

from utils.config import CACHE_DIR, ROOT

log = logging.getLogger(__name__)

DEFAULT_ENDPOINTS = [
    "https://overpass-api.de/api",
    "https://maps.mail.ru/osm/tools/overpass/api",
    "https://overpass.private.coffee/api",
    "https://overpass.kumi.systems/api",
]
# Primary endpoint — cache keys are always computed against this URL.
PRIMARY = DEFAULT_ENDPOINTS[0]

OSMNX_CACHE = CACHE_DIR / "osmnx"
# Committed cache for the demo presets (see tools/warm_osm_cache.py).
SEED_DIR = ROOT / "data" / "osm_seed"

REQUEST_TIMEOUT_S = 60      # per endpoint, client side
TOTAL_BUDGET_S = 150        # across all endpoints and rounds
ROUNDS = 2

USER_AGENT = "retail-site-intelligence (contact: t.strathaus@mail.ch)"

_configured = False


class OverpassUnavailable(RuntimeError):
    """Every Overpass endpoint failed for this query."""


def _endpoints() -> list[str]:
    raw = os.getenv("OVERPASS_URLS", "")
    urls = [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]
    return urls or DEFAULT_ENDPOINTS


def _cache_key(data: OrderedDict[str, Any]) -> str:
    url = PRIMARY + "/interpreter"
    return str(requests.Request("GET", url, params=data).prepare().url)


def _seed_cache() -> None:
    """Copy committed demo-preset responses into the runtime cache."""
    if not SEED_DIR.is_dir():
        return
    OSMNX_CACHE.mkdir(parents=True, exist_ok=True)
    for f in SEED_DIR.glob("*.json"):
        target = OSMNX_CACHE / f.name
        if not target.exists():
            shutil.copyfile(f, target)


def _overpass_request(data: OrderedDict[str, Any]) -> dict[str, Any]:
    from osmnx import _http

    key = _cache_key(data)
    cached = _http._retrieve_from_cache(key)
    if isinstance(cached, dict):
        return cached

    headers = _http._get_http_headers()
    deadline = time.monotonic() + TOTAL_BUDGET_S
    errors: list[str] = []
    for round_no in range(ROUNDS):
        for base in _endpoints():
            remaining = deadline - time.monotonic()
            if remaining <= 5:
                break
            host = base.split("/")[2]
            try:
                resp = requests.post(
                    base + "/interpreter", data=data, headers=headers,
                    timeout=min(REQUEST_TIMEOUT_S, remaining),
                )
            except requests.RequestException as e:
                errors.append(f"{host}: {type(e).__name__}")
                continue
            if not resp.ok:
                errors.append(f"{host}: HTTP {resp.status_code}")
                continue
            try:
                body = resp.json()
            except ValueError:
                errors.append(f"{host}: invalid JSON")
                continue
            remark = str(body.get("remark", "")) if isinstance(body, dict) else ""
            if not isinstance(body, dict) or "runtime error" in remark.lower():
                errors.append(f"{host}: {remark or 'unexpected response'}")
                continue
            _http._save_to_cache(key, body, True)
            return body
        if round_no < ROUNDS - 1 and deadline - time.monotonic() > 15:
            time.sleep(5)

    log.warning("All Overpass endpoints failed: %s", "; ".join(errors))
    raise OverpassUnavailable(
        "OpenStreetMap data source (Overpass API) is busy right now — "
        "please retry in a minute. (" + "; ".join(errors[-4:]) + ")"
    )


def configure_osmnx():
    """Apply cache + Overpass-fallback settings; return the osmnx module.

    Idempotent; call at the top of every function that uses OSMnx.
    """
    global _configured
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(OSMNX_CACHE)
    ox.settings.log_console = False
    if not _configured:
        ox.settings.http_user_agent = os.getenv("NOMINATIM_USER_AGENT", USER_AGENT)
        ox.settings.overpass_rate_limit = False
        try:
            from osmnx import _overpass
            _overpass._overpass_request = _overpass_request
        except (ImportError, AttributeError):
            log.warning("OSMnx internals changed — Overpass fallback inactive")
        _seed_cache()
        _configured = True
    return ox
