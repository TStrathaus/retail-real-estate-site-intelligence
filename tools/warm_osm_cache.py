"""Pre-fetch OSM data for the demo presets into the committed seed cache.

The hosted app (Streamlit Community Cloud) starts with an empty OSMnx
cache and depends on public Overpass servers that are often busy. This
script runs every Overpass query the default UI flow issues for each
`demo_locations` preset (500 m radius) and copies the responses into
`data/osm_seed/`, which `utils.overpass.configure_osmnx()` seeds into the
runtime cache on startup.

    python tools/warm_osm_cache.py            # default radius 500 m
    python tools/warm_osm_cache.py 500 1000   # several radii
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import utils.overpass as op  # noqa: E402
from utils.config import load_brand  # noqa: E402

op.SEED_DIR.mkdir(parents=True, exist_ok=True)
_inner = op._overpass_request


def _recording_request(data):
    """Run the normal request, then copy its cache file into the seed dir."""
    from osmnx import _http
    result = _inner(data)
    src = _http._resolve_cache_filepath(op._cache_key(data))
    if src.exists():
        shutil.copyfile(src, op.SEED_DIR / src.name)
    return result


op._overpass_request = _recording_request
ox = op.configure_osmnx()
from osmnx import _overpass  # noqa: E402
_overpass._overpass_request = _recording_request

from utils.geo import geocode  # noqa: E402
from utils.osm import (  # noqa: E402
    fetch_all_shops, fetch_osm_features, fetch_pedestrian_paths,
    fetch_running_infrastructure, fetch_transit_stops,
)
from utils.walk import compute_walk_score, fetch_walk_amenities  # noqa: E402


def warm(address: str, radius: int) -> None:
    g = geocode(address)
    if g is None:
        print(f"  ! geocode failed: {address}")
        return
    lat, lon = g.lat, g.lon
    calls = [
        ("features", lambda: fetch_osm_features(lat, lon, radius)),
        ("walk score", lambda: compute_walk_score(lat, lon, radius_m=1000)),
        ("walk amenities", lambda: fetch_walk_amenities(lat, lon, max(1000, radius))),
        ("transit", lambda: fetch_transit_stops(lat, lon, radius)),
        ("shops", lambda: fetch_all_shops(lat, lon, radius)),
        ("shops 1.5x", lambda: fetch_all_shops(lat, lon, int(radius * 1.5))),
        ("pedestrian", lambda: fetch_pedestrian_paths(lat, lon, radius)),
        ("running", lambda: fetch_running_infrastructure(lat, lon, radius)),
        ("running 1.5x", lambda: fetch_running_infrastructure(lat, lon, int(radius * 1.5))),
    ]
    for name, fn in calls:
        try:
            fn()
        except Exception as e:  # keep going — partial seed is still useful
            print(f"  ! {name}: {type(e).__name__}: {e}")


def main() -> None:
    radii = [int(a) for a in sys.argv[1:]] or [500]
    presets = load_brand().get("demo_locations") or []
    for i, d in enumerate(presets, 1):
        for r in radii:
            print(f"[{i}/{len(presets)}] {d['address']} @ {r} m", flush=True)
            warm(d["address"], r)
    files = list(op.SEED_DIR.glob("*.json"))
    size_mb = sum(f.stat().st_size for f in files) / 1e6
    print(f"Seed cache: {len(files)} files, {size_mb:.1f} MB in {op.SEED_DIR}")


if __name__ == "__main__":
    main()
