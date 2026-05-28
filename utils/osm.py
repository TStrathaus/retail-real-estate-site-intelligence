"""OSM POI fetching, classification, and proximity scoring.

A single OSMnx query covers every tag tier (sport, symbiose, competitor,
negative, premium). Each POI is then classified by name match against the
brand config first (e.g. shop=clothes named "Lululemon" → symbiose, not
generic clothes), with a fallback to pure-tag classification.

Distance weighting:
    ≤ 250 m  →  3.0×
    ≤ 500 m  →  2.0×
    ≤ 1 km   →  1.0×
    > 1 km   →  0.5×

Competitor saturation:
    1 direct competitor in radius   →  0 (cluster forms, neutral)
    2-3 direct competitors          →  -5 each (distance-weighted)
    4+ direct competitors           →  -15 total (market saturated)

The returned `POI` objects are consumed by both the map renderer and the
scoring engine; the per-tier aggregation in `scoring.py` reads
`p.category` + `p.score` and applies the dimension weights.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import streamlit as st

from utils.config import CACHE_DIR
from utils.geo import haversine_m

# ---------------------------------------------------------------------------
# Tag tables
# ---------------------------------------------------------------------------

# Base score by OSM tag for SPORTS venues
SPORT_TAG_SCORES: dict[tuple[str, str], tuple[str, float]] = {
    ("leisure", "fitness_centre"):   ("fitness_centre", 15.0),
    ("leisure", "sports_centre"):    ("sports_centre", 10.0),
    ("leisure", "track"):            ("running_track", 12.0),
    ("leisure", "stadium"):          ("stadium", 8.0),
    ("leisure", "swimming_pool"):    ("swimming_pool", 6.0),
    ("leisure", "sports_hall"):      ("sports_hall", 7.0),
    ("leisure", "fitness_station"):  ("outdoor_fitness", 5.0),
    ("leisure", "golf_course"):      ("golf_course", 3.0),
    ("leisure", "pitch"):            ("pitch", 3.0),
    ("sport",   "tennis"):           ("tennis", 12.0),
    ("sport",   "padel"):            ("padel", 10.0),
    ("sport",   "yoga"):             ("yoga", 6.0),
    ("sport",   "pilates"):          ("pilates", 6.0),
    ("sport",   "running"):          ("running_track", 12.0),
    ("sport",   "climbing"):         ("climbing", 6.0),
    ("sport",   "crossfit"):         ("crossfit", 8.0),
    ("route",   "hiking"):           ("trailhead", 8.0),
}

# Base score by OSM tag for SYMBIOSE / HEALTH partners
SYMBIOSE_TAG_SCORES: dict[tuple[str, str], tuple[str, float]] = {
    ("amenity", "juice_bar"):        ("juice_bar", 5.0),
    ("shop",    "health_food"):      ("health_food", 5.0),
    ("amenity", "physiotherapist"):  ("physiotherapist", 4.0),
    ("healthcare", "physiotherapist"): ("physiotherapist", 4.0),
    ("shop",    "bicycle"):          ("bicycle", 3.0),
    ("shop",    "outdoor"):          ("outdoor_shop", 5.0),
    ("shop",    "sports"):           ("sports_shop", 4.0),
}

# Base score by OSM tag for NEGATIVE per-POI effects
NEGATIVE_TAG_SCORES: dict[tuple[str, str], tuple[str, float]] = {
    ("amenity", "fast_food"):        ("fast_food", -1.5),
    ("amenity", "bar"):              ("bar", -1.5),
    ("amenity", "nightclub"):        ("nightclub", -2.5),
    ("amenity", "casino"):           ("casino", -3.0),
    ("shop",    "pawnbroker"):       ("pawnbroker", -3.0),
    ("shop",    "vape"):             ("vape", -2.0),
    ("amenity", "betting"):          ("betting", -3.0),
}

# Premium proxies (used to compute the Premium Environment Index)
PREMIUM_TAG_SCORES: dict[tuple[str, str], tuple[str, float]] = {
    ("shop",    "jewelry"):          ("jewelry", 4.0),
    ("shop",    "watches"):          ("watches", 5.0),
    ("shop",    "perfumery"):        ("perfumery", 3.0),
}

# Cheap-fashion / discounter signals (negative when DOMINANT — count-based)
DISCOUNTER_NAMES = {
    "aldi", "lidl", "primark", "kik", "tedi", "action", "h&m", "c&a",
    "deichmann", "takko", "ernsting's", "norma", "denner",
}
FASTFOOD_CHAIN_NAMES = {
    "mcdonald's", "burger king", "kfc", "subway", "domino's pizza",
    "starbucks coffee",
}


# ---------------------------------------------------------------------------
# Distance multiplier
# ---------------------------------------------------------------------------

def distance_multiplier(distance_m: float) -> float:
    if distance_m <= 250:
        return 3.0
    if distance_m <= 500:
        return 2.0
    if distance_m <= 1000:
        return 1.0
    return 0.5


def distance_bucket(distance_m: float) -> str:
    if distance_m <= 250:
        return "≤250 m"
    if distance_m <= 500:
        return "≤500 m"
    if distance_m <= 1000:
        return "≤1 km"
    return ">1 km"


# ---------------------------------------------------------------------------
# POI dataclass
# ---------------------------------------------------------------------------

@dataclass
class POI:
    name: str
    category: str          # sport | symbiose | competitor | negative | premium | partner
    subcategory: str
    lat: float
    lon: float
    distance_m: float
    base_score: float      # raw tier score before distance weighting
    score: float           # = base_score × distance_multiplier (signed)
    osm_tags: dict[str, Any] = field(default_factory=dict)

    @property
    def bucket(self) -> str:
        return distance_bucket(self.distance_m)


# ---------------------------------------------------------------------------
# OSM fetch (cached)
# ---------------------------------------------------------------------------

OSM_TAGS: dict[str, list[str]] = {
    "leisure": [
        "fitness_centre", "stadium", "track", "swimming_pool",
        "sports_centre", "sports_hall", "fitness_station",
        "golf_course", "pitch",
    ],
    "sport": [
        "tennis", "padel", "yoga", "pilates", "running",
        "climbing", "crossfit", "swimming",
    ],
    "shop": [
        "outdoor", "sports", "bicycle", "health_food", "clothes",
        "jewelry", "watches", "electronics", "shoes", "perfumery",
        "supermarket", "convenience",
    ],
    "amenity": [
        "juice_bar", "physiotherapist", "fast_food", "bar",
        "nightclub", "cafe", "restaurant", "casino", "betting",
        "pharmacy", "bank",
    ],
    "healthcare": ["physiotherapist"],
    "tourism": ["hotel"],
    "route": ["hiking"],
}


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_running_infrastructure(lat: float, lon: float, radius_m: int):
    """OSM-derived running infrastructure (the Strava-heatmap replacement).

    Strava's `tiles/run/hot/{z}/{x}/{y}.png` endpoint requires an
    authenticated session as of 2023 — the unauthenticated request
    returns a blank PNG. There is no free public running-heatmap tile
    service. We build the public-data equivalent from OSM:

      • `route=running|hiking|foot` — explicit running / hiking relations
      • `leisure=park|nature_reserve` — green areas where runners run
        (Zürichberg, Platzspitz, Bürkliplatz, etc.)
      • `leisure=track` + `sport=running` — actual running tracks
      • `highway=cycleway` — frequently used by runners

    Returned as a GeoDataFrame ready for Folium GeoJson rendering.
    Tags are split between green-area POLYGONS and running-route LINES;
    the renderer styles them differently.
    """
    try:
        import osmnx as ox
    except ImportError:
        return None
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    tags = {
        "route":   ["running", "hiking", "foot"],
        "leisure": ["track", "park", "nature_reserve"],
        "sport":   ["running"],
        "highway": ["cycleway"],
    }
    try:
        gdf = ox.features_from_point((lat, lon), tags=tags, dist=radius_m)
    except Exception:
        return None
    if gdf is None or len(gdf) == 0:
        return None
    return gdf


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_transit_stops(lat: float, lon: float, radius_m: int):
    """Return transit stops as a DataFrame with a `mode` column.

    Modes: rail (heavy rail / station / halt) · tram · bus · other.
    Used both for the Walk-Score transit count *and* the map layer."""
    try:
        import osmnx as ox
    except ImportError:
        return None
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    tags = {
        "railway": ["station", "halt", "tram_stop"],
        "highway": ["bus_stop"],
        "amenity": ["bus_station"],
        "public_transport": ["station", "stop_position", "platform"],
    }
    try:
        gdf = ox.features_from_point((lat, lon), tags=tags, dist=radius_m)
    except Exception:
        return None
    if gdf is None or len(gdf) == 0:
        return None
    proj = gdf.to_crs(epsg=3857)
    cent = proj.geometry.centroid.to_crs(epsg=4326)
    df = pd.DataFrame(gdf.drop(columns="geometry"))
    df["lat"] = cent.y.values
    df["lon"] = cent.x.values

    def _mode(row):
        rw = str(row.get("railway", "")).lower()
        am = str(row.get("amenity", "")).lower()
        hw = str(row.get("highway", "")).lower()
        if rw in ("station", "halt"):
            return "rail"
        if rw == "tram_stop":
            return "tram"
        if hw == "bus_stop" or am == "bus_station":
            return "bus"
        pt = str(row.get("public_transport", "")).lower()
        if pt == "station":
            return "rail"
        return "stop"

    df["mode"] = df.apply(_mode, axis=1)
    df["distance_m"] = df.apply(
        lambda r: haversine_m(lat, lon, r["lat"], r["lon"]), axis=1
    )
    if "name" not in df.columns:
        df["name"] = "(unnamed)"
    df["name"] = df["name"].fillna("(unnamed)")
    return df[["name", "lat", "lon", "mode", "distance_m"]]


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_all_shops(lat: float, lon: float, radius_m: int):
    """Return ALL OSM shops (regardless of brand classification) — for
    the visual 'retail density' overview layer. Subcategorisation comes
    from `classify_pois` for the brand-aware sport-shops layer.
    """
    try:
        import osmnx as ox
    except ImportError:
        return None
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    try:
        gdf = ox.features_from_point(
            (lat, lon), tags={"shop": True}, dist=radius_m,
        )
    except Exception:
        return None
    if gdf is None or len(gdf) == 0:
        return None
    proj = gdf.to_crs(epsg=3857)
    cent = proj.geometry.centroid.to_crs(epsg=4326)
    df = pd.DataFrame(gdf.drop(columns="geometry"))
    df["lat"] = cent.y.values
    df["lon"] = cent.x.values
    if "name" not in df.columns:
        df["name"] = "Shop"
    df["name"] = df["name"].fillna("Shop")
    if "shop" not in df.columns:
        df["shop"] = "unknown"
    df["shop"] = df["shop"].fillna("unknown").astype(str)
    df["distance_m"] = df.apply(
        lambda r: haversine_m(lat, lon, r["lat"], r["lon"]), axis=1
    )
    return df[["name", "shop", "lat", "lon", "distance_m"]]


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_pedestrian_paths(lat: float, lon: float, radius_m: int):
    """Pedestrian infrastructure for the map overlay.

    OSM tagging convention for Swiss "Fussgängerzonen" where trams /
    taxis are still allowed (Bahnhofstrasse, Limmatquai, parts of
    Niederdorf):
      • `highway=pedestrian`         — canonical pedestrian street
        (the tram line that runs along it is mapped as a separate
        `railway=tram` way; trams + taxis allowed via `psv=yes` /
        `taxi=yes` / `motor_vehicle=destination` tags)
      • `highway=living_street`      — Swiss "Begegnungszone" / 20 km/h
        shared-space zone
      • `highway=footway`            — explicit footpaths
      • `highway=path`               — multi-use paths
      • `highway=cycleway`           — also used by runners / pedestrians
      • `area:highway=pedestrian`    — pedestrian SQUARES & plazas
        (mapped as polygons rather than lines)
      • `pedestrian=*` or `place=square`  — fallback for plaza geometry

    Returns a GeoDataFrame with both lines and polygons; the renderer
    in `geo.build_site_map` styles them differently (orange lines for
    streets, light-orange fills for plazas).
    """
    try:
        import osmnx as ox
    except ImportError:
        return None
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    tags = {
        "highway": ["pedestrian", "living_street", "footway", "path", "cycleway"],
        "area:highway": ["pedestrian"],
        "place": ["square"],
    }
    try:
        gdf = ox.features_from_point((lat, lon), tags=tags, dist=radius_m)
    except Exception:
        return None
    if gdf is None or len(gdf) == 0:
        return None
    keep = gdf.geometry.geom_type.isin([
        "LineString", "MultiLineString", "Polygon", "MultiPolygon",
    ])
    return gdf[keep]


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_osm_features(lat: float, lon: float, radius_m: int) -> pd.DataFrame:
    """Single OSMnx query returning all relevant POIs as a flat DataFrame.

    Geometries are reduced to centroid (lat, lon). Caching is by
    (lat, lon, radius_m); typical Zurich call returns 200–600 rows.
    """
    try:
        import osmnx as ox
    except ImportError as e:
        raise RuntimeError(f"osmnx not installed: {e}") from e

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    ox.settings.log_console = False

    gdf = ox.features_from_point((lat, lon), tags=OSM_TAGS, dist=radius_m)
    if gdf is None or len(gdf) == 0:
        return pd.DataFrame()

    # Reduce geometry to centroid
    geom_proj = gdf.to_crs(epsg=3857)
    centroids = geom_proj.geometry.centroid.to_crs(epsg=4326)
    gdf = gdf.copy()
    gdf["lat"] = centroids.y.values
    gdf["lon"] = centroids.x.values

    # Drop the geometry column for plain DataFrame
    df = pd.DataFrame(gdf.drop(columns="geometry"))
    df["distance_m"] = df.apply(
        lambda r: haversine_m(lat, lon, r["lat"], r["lon"]), axis=1
    )
    return df


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _names_to_check(row: pd.Series) -> list[str]:
    out: list[str] = []
    for col in ("name", "brand", "operator"):
        v = row.get(col)
        if isinstance(v, str) and v.strip():
            out.append(v.lower().strip())
    return out


def _matches_any(names: list[str], needles: list[str]) -> str | None:
    for needle in needles:
        nl = needle.lower()
        if any(nl in n for n in names):
            return needle
    return None


def _competitor_base(competitor_name: str) -> float:
    """Per-competitor base penalty."""
    n = competitor_name.lower()
    if n == "hoka":
        return -8.0  # running-direct
    if n in ("nike", "adidas"):
        return -5.0
    if n == "new balance":
        return -4.0
    if n == "asics":
        return -5.0
    return -4.0


def _adjacency_base(adjacency_name: str) -> float:
    """Per-symbiose base bonus."""
    n = adjacency_name.lower()
    if n == "lululemon":
        return 12.0
    if n == "arc'teryx":
        return 10.0
    if n == "patagonia":
        return 8.0
    if n in ("cos", "&other stories", "sweaty betty"):
        return 7.0
    if n == "apple store":
        return 6.0
    if n == "aesop":
        return 5.0
    return 6.0


def _classify_row(row: pd.Series, brand: dict) -> tuple[str | None, str, float]:
    """Return (category, subcategory, base_score) for an OSM row, or
    (None, "", 0) if the row contributes nothing.

    Classification priority (first match wins):
      1.  Direct competitor (mono-brand store: Nike, Adidas, Hoka, …)
      2.  Multi-brand specialty (Foot Locker, Snipes, JD Sports, Titolo) → competitor-leaning
      3.  Mass-market shoe (Dosenbach, Deichmann, Day, …) → negative environment
      4.  Luxury shoe brand (Salvatore Ferragamo, Tod's, Bally) → premium adjacency
      5.  Value sport retailer (Decathlon, Sport 2000) → mild symbiose (different price tier)
      6.  Premium running specialist (Run Store, Q36.5) → symbiose
      7.  Wholesale partner (Ochsner Sport, Athlete's Foot) → partner (distance-graded later)
      8.  Luxury adjacency (Hermès, Louis Vuitton, …) → premium
      9.  Complementary adjacency (Lululemon, Arc'teryx, …) → symbiose
     10.  Negative-environment brand (Aldi, Primark, McDonald's, …) → negative
     11.  Discounter (hardcoded fallback) → negative
     12.  Fall through to OSM tag-based classification
    See SCORING.md for the full analyst rationale.
    """

    names = _names_to_check(row)

    if names:
        # 1. Direct competitor (mono-brand)
        hit = _matches_any(names, brand.get("direct_competitors", []))
        if hit:
            return ("competitor", hit, _competitor_base(hit))

        # 2. Multi-brand specialty (Foot Locker, Snipes, JD Sports, Titolo)
        hit = _matches_any(names, brand.get("multi_brand_specialty_retailers", []))
        if hit:
            return ("competitor", f"{hit} (multi-brand)", -4.0)

        # 3. Mass-market shoe chains — negative for premium positioning
        hit = _matches_any(names, brand.get("mass_market_shoe_brands", []))
        if hit:
            return ("negative", f"{hit} (mass-market shoe)", -3.0)

        # 4. Luxury shoe brands — premium adjacency
        hit = _matches_any(names, brand.get("luxury_shoe_brands", []))
        if hit:
            return ("premium", f"{hit} (luxury shoe)", 5.0)

        # 5. Value sport retailers (Decathlon) — different price tier, mild positive
        hit = _matches_any(names, brand.get("value_sport_retailers", []))
        if hit:
            return ("symbiose", f"{hit} (value sport)", 2.0)

        # 6. Premium running specialists (Run Store, Q36.5) — same customer
        hit = _matches_any(names, brand.get("premium_running_specialists", []))
        if hit:
            return ("symbiose", f"{hit} (running specialist)", 4.0)

        # 7. Wholesale partner — distance-graded in classify_pois (marker=0.01)
        hit = _matches_any(names, brand.get("wholesale_partners", []))
        if hit:
            return ("partner", hit, 0.01)

        # 8. Luxury adjacency
        hit = _matches_any(names, brand.get("luxury_adjacencies", []))
        if hit:
            return ("premium", hit, 5.0)

        # 9. Complementary adjacency
        hit = _matches_any(names, brand.get("complementary_adjacencies", []))
        if hit:
            return ("symbiose", hit, _adjacency_base(hit))

        # 10. Negative-environment brand
        hit = _matches_any(names, brand.get("negative_environments", []))
        if hit:
            return ("negative", hit, -4.0)

        # 11. Discounter / cheap fashion by hardcoded list
        for n in names:
            for bad in DISCOUNTER_NAMES:
                if bad in n:
                    return ("negative", bad.title(), -3.0)

    # 2. Tag-based classification

    for (k, v_expected), (sub, score) in SPORT_TAG_SCORES.items():
        if str(row.get(k, "")).lower() == v_expected:
            return ("sport", sub, score)

    for (k, v_expected), (sub, score) in SYMBIOSE_TAG_SCORES.items():
        if str(row.get(k, "")).lower() == v_expected:
            return ("symbiose", sub, score)

    for (k, v_expected), (sub, score) in PREMIUM_TAG_SCORES.items():
        if str(row.get(k, "")).lower() == v_expected:
            return ("premium", sub, score)

    for (k, v_expected), (sub, score) in NEGATIVE_TAG_SCORES.items():
        if str(row.get(k, "")).lower() == v_expected:
            return ("negative", sub, score)

    # 5★ hotels (premium signal)
    if str(row.get("tourism", "")).lower() == "hotel":
        stars = str(row.get("stars", ""))
        if stars.startswith("5"):
            return ("premium", "hotel_5star", 6.0)
        if stars.startswith("4"):
            return ("premium", "hotel_4star", 2.0)

    return (None, "", 0.0)


# ---------------------------------------------------------------------------
# Classify + score the whole feature set
# ---------------------------------------------------------------------------

def classify_pois(df: pd.DataFrame, brand: dict,
                    candidate_lat: float | None = None,
                    candidate_lon: float | None = None,
                    radius_m: float | None = None) -> list[POI]:
    """Classify the OSM dataframe + merge analyst-curated known
    competitors / multi-brand specialty locations.

    `candidate_lat/lon/radius_m` are needed to compute distance to
    curated entries. If omitted they're inferred from the dataframe."""
    if df is None or len(df) == 0:
        return []

    pois: list[POI] = []
    for _, row in df.iterrows():
        cat, sub, base = _classify_row(row, brand)
        if cat is None or base == 0:
            continue
        name = (row.get("name") or row.get("brand") or row.get("operator")
                or sub.replace("_", " ").title())
        if not isinstance(name, str):
            name = sub.replace("_", " ").title()
        dist = float(row["distance_m"])
        # Wholesale partners are scored by distance class directly, NOT
        # multiplied by the standard proximity weight (analyst rule —
        # see SCORING.md "Wholesale partner distance grading").
        if cat == "partner":
            if dist < 250:
                base = -2.0
                signed_score = -2.0
            elif dist <= 1000:
                base = 0.0
                signed_score = 0.0
            else:
                base = 1.0
                signed_score = 1.0
        else:
            signed_score = base * distance_multiplier(dist)
        pois.append(POI(
            name=name.strip(),
            category=cat,
            subcategory=sub,
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            distance_m=dist,
            base_score=base,
            score=signed_score,
            osm_tags={k: row.get(k) for k in ("leisure", "sport", "shop",
                                                "amenity", "tourism", "stars")
                       if pd.notna(row.get(k))},
        ))

    # Infer candidate lat/lon if not passed (closest POI is within ~50 m
    # of the centre by definition)
    if candidate_lat is None or candidate_lon is None:
        closest = df.loc[df["distance_m"].idxmin()]
        candidate_lat = float(closest["lat"])
        candidate_lon = float(closest["lon"])
    if radius_m is None:
        radius_m = float(df["distance_m"].max())

    pois.extend(_pois_from_curated_locations(
        brand, candidate_lat, candidate_lon, radius_m,
    ))
    return pois


def _pois_from_curated_locations(brand: dict, cand_lat: float,
                                   cand_lon: float, radius_m: float) -> list[POI]:
    """Merge curated `known_competitor_locations` and
    `multi_brand_known_locations` from `on_brand.json` into the POI list.
    Used because OSM under-tags mono-brand competitor stores in Zurich.
    """
    out: list[POI] = []
    for entry in brand.get("known_competitor_locations", []) or []:
        if entry.get("status") != "active":
            continue
        d = haversine_m(cand_lat, cand_lon, entry["lat"], entry["lon"])
        if d > radius_m:
            continue
        base = _competitor_base(entry.get("brand", "")) or -5.0
        out.append(POI(
            name=f"{entry['name']} (curated)",
            category="competitor",
            subcategory=entry.get("brand", "unknown").lower(),
            lat=entry["lat"], lon=entry["lon"],
            distance_m=d, base_score=base,
            score=base * distance_multiplier(d),
            osm_tags={"_source": "curated", "_brand": entry.get("brand", "")},
        ))

    for entry in brand.get("multi_brand_known_locations", []) or []:
        d = haversine_m(cand_lat, cand_lon, entry["lat"], entry["lon"])
        if d > radius_m:
            continue
        out.append(POI(
            name=f"{entry['name']} (curated)",
            category="competitor",
            subcategory="multi_brand_specialty",
            lat=entry["lat"], lon=entry["lon"],
            distance_m=d, base_score=-4.0,
            score=-4.0 * distance_multiplier(d),
            osm_tags={"_source": "curated"},
        ))

    return out


# ---------------------------------------------------------------------------
# Saturation logic
# ---------------------------------------------------------------------------

def apply_competitor_saturation(pois: list[POI]) -> None:
    """Mutates POI scores in place to apply saturation rules."""
    competitors = [p for p in pois if p.category == "competitor"]
    n = len(competitors)
    if n == 0:
        return
    if n == 1:
        # First competitor in radius = cluster signal, neutralise penalty
        competitors[0].score = 0.0
        competitors[0].base_score = 0.0
        return
    if n <= 3:
        # Keep per-POI penalty (already applied) — no change
        return
    # 4+ → enforce total penalty floor of -15
    total_existing = sum(p.score for p in competitors)
    if total_existing > -15.0:
        # Spread -15 across them, distance-weighted
        weights = [distance_multiplier(p.distance_m) for p in competitors]
        wsum = sum(weights) or 1.0
        for p, w in zip(competitors, weights):
            p.score = -15.0 * (w / wsum)
            p.base_score = -5.0  # marker for saturation


def apply_cluster_negatives(pois: list[POI]) -> dict[str, float]:
    """Detect negative-environment clusters (fast-food, bars, discounters).
    Returns extra cluster penalties keyed by cluster type for the score
    breakdown — these are summed into the 'negative' dimension separately
    from the per-POI negatives.
    """
    extras: dict[str, float] = {}
    ff = [p for p in pois if p.subcategory == "fast_food" and p.distance_m <= 500]
    if len(ff) >= 3:
        extras["fast_food_cluster"] = -8.0
    bars = [p for p in pois
             if p.subcategory in ("bar", "nightclub") and p.distance_m <= 500]
    if len(bars) >= 4:
        extras["nightlife_cluster"] = -8.0
    disc = [p for p in pois
             if p.category == "negative" and p.distance_m <= 500
             and p.subcategory not in ("fast_food", "bar", "nightclub")]
    if len(disc) >= 3:
        extras["discounter_cluster"] = -10.0
    return extras


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def summarize_by_category(pois: list[POI]) -> dict[str, dict]:
    """Per-category counts and score sums (for Step 3 Glass-Box panels)."""
    cats = ("sport", "symbiose", "competitor", "negative", "premium", "partner")
    out: dict[str, dict] = {}
    for c in cats:
        items = [p for p in pois if p.category == c]
        out[c] = {
            "count": len(items),
            "score_sum": sum(p.score for p in items),
            "items": items,
        }
    return out


# ---------------------------------------------------------------------------
# Map markers
# ---------------------------------------------------------------------------

CATEGORY_COLOR = {
    "sport":      "#1565C0",   # blue
    "symbiose":   "#00C853",   # On accent green
    "premium":    "#C9A227",   # gold
    "competitor": "#D32F2F",   # red
    "negative":   "#F57C00",   # orange
    "partner":    "#616161",   # gray
}

CATEGORY_LABEL = {
    "sport":      "🏃 Sport venue",
    "symbiose":   "🤝 Symbiose partner",
    "premium":    "💎 Premium signal",
    "competitor": "⚔️ Direct competitor",
    "negative":   "⚠️ Negative environment",
    "partner":    "🏬 Wholesale partner",
}


SPORT_SHOP_SUBCATS = {
    "outdoor_shop", "sports_shop", "bicycle", "sports_centre",
}


def add_pois_to_map(folium_map, pois: list[POI], visible: set[str] | None = None,
                     *, fine_grained: bool = False, cap: int | None = None) -> None:
    """Add CircleMarker per POI directly to the map (no FeatureGroup).

    Folium FeatureGroups + LayerControl were removed at a deliberate UX
    request — toggling is done with Streamlit checkboxes outside the
    map. Categories are still color-coded; the tooltip carries the
    category name for clarity.

    `cap` (optional) limits to the top-N POIs by |score| — useful on
    the Step 1 map where we want a quick visual, not 250+ markers.
    """
    import folium

    if cap and len(pois) > cap:
        pois = sorted(pois, key=lambda p: abs(p.score), reverse=True)[:cap]

    if fine_grained:
        def _color(p: POI) -> str:
            if p.category == "symbiose" and p.subcategory in SPORT_SHOP_SUBCATS:
                return "#0288D1"   # sport shops distinct from other symbiose
            if p.category == "sport":
                return "#1565C0"
            return CATEGORY_COLOR.get(p.category, "#9E9E9E")
    else:
        def _color(p: POI) -> str:
            return CATEGORY_COLOR.get(p.category, "#9E9E9E")

    for p in pois:
        color = _color(p)
        radius = 4 + min(abs(p.base_score) / 4.0, 4.0)
        folium.CircleMarker(
            location=[p.lat, p.lon], radius=radius, color=color,
            weight=1.2, fill=True, fill_color=color, fill_opacity=0.75,
            popup=folium.Popup(
                f"<b>{p.name}</b><br>"
                f"<i>{p.subcategory.replace('_', ' ').title()}</i><br>"
                f"Distance: {p.distance_m:.0f} m ({p.bucket})<br>"
                f"Score: <b>{p.score:+.1f}</b>",
                max_width=240,
            ),
            tooltip=f"{CATEGORY_LABEL.get(p.category, p.category)} · {p.name}",
        ).add_to(folium_map)
