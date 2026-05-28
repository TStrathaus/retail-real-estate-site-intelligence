"""Footfall & accessibility scoring.

Walk Score (proper Pandana implementation) requires building a walking
graph and computing aggregations per node — expensive and Pandana is
notoriously hard to install on Windows. For a demo tool the pragmatic
equivalent is an **amenity-richness Walk Score**: count of the 6 canonical
walkable-amenity categories within 250/500/1000 m, log-scaled and
distance-weighted. This produces a 0–100 number that tracks the real
Walk Score concept well for analyst storytelling.

Transit (ÖV) score is a count-based aggregation of OSM
`public_transport=stop_position|platform` + `railway=station|tram_stop`
within the search radius — simple and accurate for Swiss urban contexts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st

from utils.config import CACHE_DIR
from utils.geo import haversine_m
from utils.osm import distance_multiplier

WALK_AMENITY_TAGS: dict[str, list[str]] = {
    "amenity": [
        "restaurant", "cafe", "fast_food", "bar", "pub",  # food & drink
        "pharmacy", "bank", "atm",                        # services
        "school", "kindergarten", "library",              # education
        "post_office", "marketplace",
    ],
    "shop": [
        "supermarket", "convenience", "bakery", "butcher",
        "greengrocer", "clothes", "books", "florist",
    ],
    "leisure": ["park", "garden", "playground"],
    "tourism": ["museum", "gallery"],
}

# Category weights — what the original Walk Score concept emphasises
WALK_CATEGORY_WEIGHTS: dict[str, float] = {
    "food":      0.25,   # restaurant, cafe, fast_food, bar
    "grocery":   0.25,   # supermarket, convenience, bakery, greengrocer
    "services":  0.15,   # pharmacy, bank, post_office
    "education": 0.10,   # school, kindergarten, library
    "leisure":   0.15,   # park, garden, museum
    "retail":    0.10,   # clothes, books, florist
}

TRANSIT_TAGS: dict[str, list[str]] = {
    "public_transport": ["stop_position", "platform", "station"],
    "railway": ["station", "tram_stop", "halt"],
    "amenity": ["bus_station"],
}


def _category_of(row: pd.Series) -> str | None:
    amenity = str(row.get("amenity", "")).lower()
    shop = str(row.get("shop", "")).lower()
    leisure = str(row.get("leisure", "")).lower()
    tourism = str(row.get("tourism", "")).lower()

    if amenity in {"restaurant", "cafe", "fast_food", "bar", "pub"}:
        return "food"
    if shop in {"supermarket", "convenience", "bakery", "butcher", "greengrocer"}:
        return "grocery"
    if amenity in {"pharmacy", "bank", "atm", "post_office"}:
        return "services"
    if amenity in {"school", "kindergarten", "library"}:
        return "education"
    if leisure in {"park", "garden", "playground"} or tourism in {"museum", "gallery"}:
        return "leisure"
    if shop in {"clothes", "books", "florist", "marketplace"}:
        return "retail"
    return None


@dataclass
class WalkScoreResult:
    score: int                              # 0–100
    category_counts: dict[str, int]         # category → POI count in 1 km
    category_scores: dict[str, float]       # category → weighted 0–100
    transit_stops_250m: int
    transit_stops_500m: int
    rail_stations: int
    radius_m: int
    method: str = "Amenity-richness Walk Score (Pandana-free)"


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_walk_amenities(lat: float, lon: float, radius_m: int = 1000):
    """Categorised walk-amenity POIs for the map layer.

    Returns a DataFrame with `name`, `lat`, `lon`, `distance_m`, `category`
    (one of food / grocery / services / education / leisure / retail).
    """
    try:
        import osmnx as ox
    except ImportError:
        return None
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    try:
        gdf = ox.features_from_point((lat, lon), tags=WALK_AMENITY_TAGS, dist=radius_m)
    except Exception:
        return None
    if gdf is None or len(gdf) == 0:
        return None
    proj = gdf.to_crs(epsg=3857)
    cent = proj.geometry.centroid.to_crs(epsg=4326)
    df = pd.DataFrame(gdf.drop(columns="geometry"))
    df["lat"] = cent.y.values
    df["lon"] = cent.x.values
    df["distance_m"] = df.apply(
        lambda r: haversine_m(lat, lon, r["lat"], r["lon"]), axis=1
    )
    if "name" not in df.columns:
        df["name"] = "(unnamed)"
    df["name"] = df["name"].fillna("(unnamed)")
    df["category"] = df.apply(_category_of, axis=1)
    df = df[df["category"].notna()]
    return df[["name", "lat", "lon", "distance_m", "category"]]


@st.cache_data(ttl=86400, show_spinner=False)
def compute_walk_score(lat: float, lon: float, radius_m: int = 1000) -> WalkScoreResult:
    try:
        import osmnx as ox
    except ImportError as e:
        raise RuntimeError(f"osmnx not installed: {e}") from e

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    ox.settings.log_console = False

    gdf = ox.features_from_point((lat, lon), tags=WALK_AMENITY_TAGS, dist=radius_m)
    cat_counts: dict[str, int] = {k: 0 for k in WALK_CATEGORY_WEIGHTS}
    cat_distance_scores: dict[str, float] = {k: 0.0 for k in WALK_CATEGORY_WEIGHTS}

    if gdf is not None and len(gdf) > 0:
        proj = gdf.to_crs(epsg=3857)
        cent = proj.geometry.centroid.to_crs(epsg=4326)
        df = pd.DataFrame(gdf.drop(columns="geometry"))
        df["lat"] = cent.y.values
        df["lon"] = cent.x.values
        df["distance_m"] = df.apply(
            lambda r: haversine_m(lat, lon, r["lat"], r["lon"]), axis=1
        )

        for _, row in df.iterrows():
            cat = _category_of(row)
            if cat is None:
                continue
            cat_counts[cat] += 1
            cat_distance_scores[cat] += distance_multiplier(row["distance_m"])

    # Saturation curve — tuned May 2026 with empirical Zurich data.
    # The old `100 * (1 - exp(-s/6))` saturated above s≈30, but central
    # Altstadt has s=600-1000+ per category (Limmatquai food=943, retail=603),
    # so every Zurich location scored 100/100.
    # Log scaling `13 * ln(1+s)` differentiates realistically:
    #     s=5    →  23
    #     s=20   →  40
    #     s=50   →  51
    #     s=100  →  60
    #     s=200  →  69
    #     s=500  →  81
    #     s=1000 →  90
    #     s>2200 → 100 (cap)
    cat_norm: dict[str, float] = {}
    for cat in WALK_CATEGORY_WEIGHTS:
        s = cat_distance_scores[cat]
        norm = min(100.0, 13.0 * math.log(1 + s))
        cat_norm[cat] = round(norm, 1)

    total = sum(cat_norm[c] * w for c, w in WALK_CATEGORY_WEIGHTS.items())
    score = int(round(total))

    # Transit
    transit = ox.features_from_point((lat, lon), tags=TRANSIT_TAGS, dist=radius_m)
    n_250 = n_500 = n_rail = 0
    if transit is not None and len(transit) > 0:
        proj = transit.to_crs(epsg=3857)
        cent = proj.geometry.centroid.to_crs(epsg=4326)
        tdf = pd.DataFrame(transit.drop(columns="geometry"))
        tdf["lat"] = cent.y.values
        tdf["lon"] = cent.x.values
        tdf["distance_m"] = tdf.apply(
            lambda r: haversine_m(lat, lon, r["lat"], r["lon"]), axis=1
        )
        n_250 = int((tdf["distance_m"] <= 250).sum())
        n_500 = int((tdf["distance_m"] <= 500).sum())
        # Rail stations specifically
        if "railway" in tdf.columns:
            n_rail = int(tdf["railway"].astype(str).str.lower().isin(
                ["station", "halt", "tram_stop"]
            ).sum())

    return WalkScoreResult(
        score=score,
        category_counts=cat_counts,
        category_scores=cat_norm,
        transit_stops_250m=n_250,
        transit_stops_500m=n_500,
        rail_stations=n_rail,
        radius_m=radius_m,
    )


def transit_to_score_0_100(t: WalkScoreResult) -> int:
    """Combine transit metrics into a 0-100 ÖV score."""
    # Heuristic: rail station within radius = 40 base; per-stop within 500m = 4; cap 100.
    s = 0
    if t.rail_stations >= 1:
        s += 40
    s += min(t.transit_stops_500m * 4, 60)
    return min(int(s), 100)
