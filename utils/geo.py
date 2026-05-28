"""Geocoding, walking isochrones, and Folium map helpers.

Nominatim is used for forward geocoding (free, no key — but requires a
contact-identifying User-Agent per their usage policy). OSMnx provides the
walking network for the 10-minute isochrone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Any

import requests
import streamlit as st
from dotenv import load_dotenv

from utils.config import CACHE_DIR

load_dotenv()
USER_AGENT = os.getenv(
    "NOMINATIM_USER_AGENT",
    "retail-site-intelligence (contact: t.strathaus@mail.ch)",
)


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

@dataclass
class GeocodeResult:
    lat: float
    lon: float
    display_name: str
    city: str | None
    country: str | None
    postcode: str | None
    raw: dict[str, Any]


@st.cache_data(ttl=86400, show_spinner=False)
def geocode(query: str) -> GeocodeResult | None:
    """Forward-geocode an address string via Nominatim."""
    if not query or not query.strip():
        return None
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "addressdetails": 1,
    }
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en,de"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    row = rows[0]
    addr = row.get("address", {})
    return GeocodeResult(
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        display_name=row.get("display_name", query),
        city=(addr.get("city") or addr.get("town") or addr.get("village")
              or addr.get("municipality")),
        country=addr.get("country"),
        postcode=addr.get("postcode"),
        raw=row,
    )


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    r = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlam = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlam / 2) ** 2
    return 2 * r * asin(sqrt(a))


# ---------------------------------------------------------------------------
# Walking isochrone (OSMnx)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def walking_isochrone(lat: float, lon: float, minutes: int = 10,
                       walking_kmh: float = 4.8) -> dict | None:
    """Return a GeoJSON-compatible mapping of the area reachable within
    `minutes` minutes of walking from (lat, lon).

    Implementation: build a walking graph 1.2× the theoretical maximum
    distance, assign per-edge travel time, run an ego-graph from the nearest
    node bounded by walk time, then take the convex hull of reachable nodes.

    Cached for 24h per coordinate/minute combo.
    """
    try:
        import networkx as nx
        import osmnx as ox
        import geopandas as gpd
        from shapely.geometry import Point, mapping
    except ImportError:
        return None

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    ox.settings.log_console = False

    max_dist = int(minutes * (walking_kmh / 60) * 1000 * 1.4)  # 40% safety margin
    try:
        G = ox.graph_from_point((lat, lon), dist=max_dist, network_type="walk")
    except Exception:
        return None
    if G is None or G.number_of_nodes() == 0:
        return None

    walking_m_per_min = walking_kmh * 1000 / 60
    for _u, _v, data in G.edges(data=True):
        length = data.get("length", 0) or 0
        data["time"] = length / walking_m_per_min

    # OSMnx 2.x: nearest_nodes on an unprojected graph requires scikit-
    # learn (BallTree). Project first so the planar KDTree path is used —
    # works whether scikit-learn is installed or not, and is faster.
    try:
        G_proj = ox.project_graph(G)
        proj = G_proj.graph.get("crs")
        # Project the query point into the same CRS
        import geopandas as gpd
        from shapely.geometry import Point as _Pt
        q = gpd.GeoSeries([_Pt(lon, lat)], crs="EPSG:4326").to_crs(proj)
        cx, cy = float(q.x.iloc[0]), float(q.y.iloc[0])
        center = ox.distance.nearest_nodes(G_proj, cx, cy)
    except Exception:
        center = ox.distance.nearest_nodes(G, lon, lat)
    sub = nx.ego_graph(G, center, radius=minutes, distance="time")
    if sub.number_of_nodes() < 3:
        return None

    pts = [Point(d["x"], d["y"]) for _, d in sub.nodes(data=True)]
    gdf = gpd.GeoDataFrame(geometry=pts, crs="EPSG:4326")
    poly = gdf.union_all().convex_hull
    return mapping(poly)


# ---------------------------------------------------------------------------
# Folium map
# ---------------------------------------------------------------------------

def build_site_map(lat: float, lon: float, radius_m: int = 500,
                    isochrone_geojson: dict | None = None,
                    existing_stores: list[dict] | None = None,
                    show_flood_layer: bool = False,
                    pedestrian_paths=None,
                    shop_heatmap_points: list[tuple[float, float]] | None = None,
                    transit_stops_df=None,
                    all_shops_df=None,
                    cannibalization: bool = False,
                    walk_amenities_df=None,
                    walk_heatmap_points: list[tuple[float, float]] | None = None,
                    sport_heatmap_points: list[tuple[float, float]] | None = None,
                    competitor_heatmap_points: list[tuple[float, float]] | None = None,
                    known_competitors: list[dict] | None = None,
                    known_hotels: list[dict] | None = None,
                    running_infrastructure=None,
                    strava_tile_url: str | None = None):
    """Construct a Folium map centred on (lat, lon) with brand styling.

    Overlay layers (all togglable via the LayerControl widget):
      • Search-radius circle + candidate pin (always on)
      • 10-min walking isochrone (optional, expensive)
      • Existing brand stores (always on if provided)
      • BAFU flood-hazard WMS (toggle)
      • Strava running heatmap (toggle, public tile layer)
      • OSM pedestrian streets (toggle, GeoJSON)
      • Shop-density heatmap (toggle, folium.plugins.HeatMap)
    """
    import folium

    m = folium.Map(
        location=[lat, lon],
        zoom_start=16 if radius_m <= 500 else 15,
        tiles="CartoDB positron",
        control_scale=True,
    )

    # Candidate site pin
    folium.CircleMarker(
        location=[lat, lon],
        radius=9,
        color="#00C853",
        weight=3,
        fill=True,
        fill_color="#00C853",
        fill_opacity=1.0,
        popup=folium.Popup("<b>Candidate site</b>", max_width=200),
    ).add_to(m)

    # Search radius
    folium.Circle(
        location=[lat, lon],
        radius=radius_m,
        color="#00C853",
        weight=2,
        dash_array="6,6",
        fill=True,
        fill_color="#00C853",
        fill_opacity=0.05,
        popup=f"{radius_m} m radius",
    ).add_to(m)

    # Isochrone
    if isochrone_geojson is not None:
        folium.GeoJson(
            isochrone_geojson,
            name="10-min walk",
            style_function=lambda _x: {
                "fillColor": "#1A1A1A",
                "color": "#1A1A1A",
                "weight": 1,
                "fillOpacity": 0.10,
                "dashArray": "3,3",
            },
        ).add_to(m)

    # Existing brand stores — black shopping-bag markers, added DIRECTLY
    # (no FeatureGroup — layer visibility is controlled by the caller
    # passing or not passing `existing_stores`).
    if existing_stores:
        for s in existing_stores:
            dist_m = haversine_m(lat, lon, s["lat"], s["lon"])
            folium.Marker(
                location=[s["lat"], s["lon"]],
                icon=folium.Icon(color="black", icon="shopping-bag", prefix="fa"),
                popup=folium.Popup(
                    f"<b>{s['name']}</b><br>"
                    f"{s.get('address', '')}<br>"
                    f"<i>Distance: {dist_m/1000:.2f} km</i>",
                    max_width=260,
                ),
            ).add_to(m)

    # Cannibalization overlay — per existing-store catchment circle +
    # connector line. Renders only when `cannibalization=True` AND
    # existing_stores were provided.
    if cannibalization and existing_stores:
        for s in existing_stores:
            d = haversine_m(lat, lon, s["lat"], s["lon"])
            if d > 3500:
                continue
            if d < 500:
                color, overlap, badge = "#D32F2F", "≈ 80%", "HIGH"
            elif d < 1000:
                color, overlap, badge = "#F57C00", "≈ 50%", "MEDIUM"
            elif d < 2000:
                color, overlap, badge = "#F9A825", "≈ 20%", "LOW"
            else:
                color, overlap, badge = "#00C853", "< 5%", "NONE"
            folium.Circle(
                location=[s["lat"], s["lon"]],
                radius=700,
                color=color, weight=2, fill=True, fill_color=color,
                fill_opacity=0.10, dash_array="5,5",
                popup=folium.Popup(
                    f"<b>{s['name']}</b><br>"
                    f"Distance from candidate: {d/1000:.2f} km<br>"
                    f"Estimated isochrone overlap: {overlap} ({badge})",
                    max_width=280,
                ),
            ).add_to(m)
            folium.PolyLine(
                locations=[[lat, lon], [s["lat"], s["lon"]]],
                color=color, weight=2.5, opacity=0.75, dash_array="3,8",
                tooltip=f"{d/1000:.2f} km · {overlap} overlap",
            ).add_to(m)

    # BAFU flood-risk WMS layer (Innovation 7)
    if show_flood_layer:
        folium.WmsTileLayer(
            url="https://wms.geo.admin.ch/",
            layers="ch.bafu.gefaehrdungskarte-oberflaechenabfluss",
            fmt="image/png",
            transparent=True,
            name="🌊 BAFU flood hazard",
            overlay=True,
            opacity=0.55,
        ).add_to(m)

    # Running infrastructure — parks (light-green polygons) + routes/
    # cycleways (orange lines). Added directly to map.
    if running_infrastructure is not None and len(running_infrastructure) > 0:
        try:
            gdf = running_infrastructure
            parks = gdf[
                gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
                & (gdf.get("leisure", "").astype(str)
                    .isin(["park", "nature_reserve"]))
            ] if "leisure" in gdf.columns else gdf.iloc[0:0]
            if len(parks) > 0:
                folium.GeoJson(
                    parks.__geo_interface__,
                    style_function=lambda _f: {
                        "color": "#2E7D32", "weight": 0.5,
                        "fillColor": "#A5D6A7", "fillOpacity": 0.45,
                    },
                ).add_to(m)
            routes = gdf[
                gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])
            ]
            if len(routes) > 0:
                folium.GeoJson(
                    routes.__geo_interface__,
                    style_function=lambda _f: {
                        "color": "#E65100", "weight": 3.5, "opacity": 0.80,
                    },
                ).add_to(m)
        except Exception:
            pass

    # Pedestrian infrastructure — lines (streets) + polygons (plazas)
    # Includes Fussgängerzonen where trams + taxis are still allowed
    # (Bahnhofstrasse, Limmatquai, Marktplatz, etc.)
    if pedestrian_paths is not None and len(pedestrian_paths) > 0:
        try:
            gdf = pedestrian_paths
            lines = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
            polys = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
            if len(polys) > 0:
                folium.GeoJson(
                    polys.__geo_interface__,
                    style_function=lambda _f: {
                        "color": "#E65100", "weight": 1.0,
                        "fillColor": "#FFB74D", "fillOpacity": 0.30,
                    },
                ).add_to(m)
            if len(lines) > 0:
                folium.GeoJson(
                    lines.__geo_interface__,
                    style_function=lambda _f: {
                        "color": "#FF6F00", "weight": 3.5, "opacity": 0.75,
                    },
                ).add_to(m)
        except Exception:
            pass

    # ---------- Density heatmaps — direct to map, no FeatureGroup ----------
    from folium.plugins import HeatMap

    def _heatmap_direct(points, gradient, *,
                         radius_px=24, blur_px=30, min_opacity=0.40):
        if not points:
            return
        try:
            HeatMap(
                points,
                radius=radius_px, blur=blur_px,
                min_opacity=min_opacity, gradient=gradient,
            ).add_to(m)
        except Exception:
            pass

    # Shop density
    _heatmap_direct(
        shop_heatmap_points,
        gradient={0.05: "#FFEB3B", 0.20: "#FB8C00", 0.50: "#D32F2F"},
    )
    # Walk-amenities density (Walk Score visual)
    _heatmap_direct(
        walk_heatmap_points,
        gradient={0.05: "#C8E6C9", 0.25: "#66BB6A", 0.55: "#1B5E20"},
    )
    # Sport-facility density
    _heatmap_direct(
        sport_heatmap_points,
        gradient={0.05: "#BBDEFB", 0.25: "#42A5F5", 0.55: "#0D47A1"},
    )

    # Competitor density — per user request: NOT a heatmap. Render each
    # competitor as a red 200 m radius circle + a centred red dot so the
    # catchment is visually explicit instead of blurred.
    if competitor_heatmap_points:
        for ll in competitor_heatmap_points:
            try:
                pt_lat, pt_lon = ll[0], ll[1]
            except Exception:
                continue
            folium.Circle(
                location=[pt_lat, pt_lon],
                radius=200,
                color="#D32F2F", weight=2,
                fill=True, fill_color="#D32F2F", fill_opacity=0.18,
            ).add_to(m)
            folium.CircleMarker(
                location=[pt_lat, pt_lon], radius=5,
                color="#D32F2F", weight=1.5,
                fill=True, fill_color="#D32F2F", fill_opacity=1.0,
            ).add_to(m)

    # Transit stops — color-coded by mode, direct to map
    if transit_stops_df is not None and len(transit_stops_df) > 0:
        try:
            mode_colors = {
                "rail":  "#1A237E", "tram":  "#0277BD",
                "bus":   "#37474F", "stop":  "#90A4AE", "other": "#90A4AE",
            }
            mode_size = {"rail": 7, "tram": 5, "bus": 4, "stop": 3, "other": 3}
            for _, r in transit_stops_df.iterrows():
                mode = r["mode"]
                folium.CircleMarker(
                    location=[r["lat"], r["lon"]],
                    radius=mode_size.get(mode, 3),
                    color=mode_colors.get(mode, "#90A4AE"),
                    fill=True, fill_color=mode_colors.get(mode, "#90A4AE"),
                    fill_opacity=0.85, weight=1,
                    popup=folium.Popup(
                        f"<b>{r['name']}</b><br>"
                        f"Mode: {mode}<br>"
                        f"Distance: {r['distance_m']:.0f} m",
                        max_width=200,
                    ),
                ).add_to(m)
        except Exception:
            pass

    # Walk amenities — categorised, direct to map (no FeatureGroup)
    if walk_amenities_df is not None and len(walk_amenities_df) > 0:
        try:
            cat_colors = {
                "food":      "#E65100",
                "grocery":   "#558B2F",
                "services":  "#1565C0",
                "education": "#6A1B9A",
                "leisure":   "#00838F",
                "retail":    "#C2185B",
            }
            for _, r in walk_amenities_df.iterrows():
                cat = r["category"]
                color = cat_colors.get(cat, "#9E9E9E")
                folium.CircleMarker(
                    location=[r["lat"], r["lon"]],
                    radius=3.5, color=color,
                    fill=True, fill_color=color,
                    fill_opacity=0.75, weight=0.5,
                    popup=folium.Popup(
                        f"<b>{r['name']}</b><br>"
                        f"<i>Walk-amenity: {cat}</i><br>"
                        f"Distance: {r['distance_m']:.0f} m",
                        max_width=220,
                    ),
                ).add_to(m)
        except Exception:
            pass

    # Known hotels — combined into ONE layer with star-graded color dots
    # Color gradient: 3★ → red · 4★ → orange · 5★ → green
    if known_hotels:
        try:
            in_radius = [
                h for h in known_hotels
                if haversine_m(lat, lon, h["lat"], h["lon"]) <= radius_m * 1.5
            ]
            if in_radius:
                star_colors = {
                    5: "#2E7D32",   # green — 5-star
                    4: "#F57C00",   # orange — 4-star
                    3: "#D32F2F",   # red — 3-star
                    2: "#B71C1C",   # dark red — 2-star
                    1: "#7F0000",   # very dark red
                }
                for h in in_radius:
                    d = haversine_m(lat, lon, h["lat"], h["lon"])
                    stars = h.get("stars", 3)
                    color = star_colors.get(stars, "#9E9E9E")
                    radius_px = 6 + (stars or 3)   # 5★ slightly larger
                    folium.CircleMarker(
                        location=[h["lat"], h["lon"]],
                        radius=radius_px,
                        color=color,
                        fill=True, fill_color=color, fill_opacity=0.85,
                        weight=2,
                        popup=folium.Popup(
                            f"<b>{h['name']}</b><br>"
                            f"<span style='color:{color};font-weight:600;'>"
                            f"{stars}★</span> · {h.get('tier', '—')}<br>"
                            f"{h.get('address', '')}<br>"
                            f"Distance: {d/1000:.2f} km",
                            max_width=280,
                        ),
                        tooltip=f"{h['name']} ({stars}★)",
                    ).add_to(m)
        except Exception:
            pass

    # Strava auth-tile fallback — only renders if user has provided an
    # authenticated tile URL via STRAVA_TILE_URL env var (cookies expire
    # ~every 2 weeks per Strava's CDN policy)
    if strava_tile_url:
        try:
            folium.TileLayer(
                tiles=strava_tile_url,
                attr="© Strava",
                name="🏃 Strava Global Heatmap (user-authenticated)",
                overlay=True,
                opacity=0.75,
                show=True,
            ).add_to(m)
        except Exception:
            pass

    # Known competitor curated overrides — large red ✕ markers
    if known_competitors:
        try:
            for entry in known_competitors:
                if entry.get("status") != "active":
                    continue
                d = haversine_m(lat, lon, entry["lat"], entry["lon"])
                folium.Marker(
                    location=[entry["lat"], entry["lon"]],
                    icon=folium.Icon(color="red", icon="times", prefix="fa"),
                    popup=folium.Popup(
                        f"<b>{entry['name']}</b><br>"
                        f"<i>Brand: {entry.get('brand', '—')}</i><br>"
                        f"{entry.get('address', '')}<br>"
                        f"Distance: {d/1000:.2f} km<br>"
                        f"Status: {entry.get('status', '—')} "
                        f"(as of {entry.get('as_of', '—')})<br>"
                        f"<small>Curated override — see SCORING.md §4</small>",
                        max_width=280,
                    ),
                ).add_to(m)
        except Exception:
            pass

    # All shops — generic retail (any shop=*), direct to map
    if all_shops_df is not None and len(all_shops_df) > 0:
        try:
            for _, r in all_shops_df.iterrows():
                folium.CircleMarker(
                    location=[r["lat"], r["lon"]],
                    radius=3,
                    color="#7B1FA2",
                    fill=True, fill_color="#CE93D8",
                    fill_opacity=0.7, weight=0.5,
                    popup=folium.Popup(
                        f"<b>{r['name']}</b><br>"
                        f"<i>shop={r['shop']}</i><br>"
                        f"Distance: {r['distance_m']:.0f} m",
                        max_width=200,
                    ),
                ).add_to(m)
        except Exception:
            pass

    # NOTE: NO folium.LayerControl(). All layer toggling happens via
    # Streamlit checkboxes outside the map — caller decides which data
    # to pass; only enabled layers are rendered. Per the UX design.
    return m
