"""Tab 1 — New Site analysis.

7-step funnel: Location → Market → Proximity → Footfall →
Site Score → Pro-Forma → Export. The funnel is sequential, but every
step's output is stored on `st.session_state.site` so the user can
revisit earlier steps without losing later work.

Sprint 1 + 2 complete: all seven steps wired. Flood risk (Innovation 7)
deducts from the Site Score post-aggregation and
auto-injects business-interruption insurance into the Pro-Forma.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

import os

from utils.bfs import lookup_municipality
from utils.export import deal_memo_pdf, site_report_pdf
from utils.financial import ProForma, ProFormaInputs, build_proforma, build_scenarios
from utils.flood import FloodRisk, check_flood_risk
from utils.geo import build_site_map, geocode, haversine_m, walking_isochrone
from utils.osm import (
    POI,
    CATEGORY_LABEL,
    add_pois_to_map,
    classify_pois,
    fetch_all_shops,
    fetch_osm_features,
    fetch_pedestrian_paths,
    fetch_running_infrastructure,
    fetch_transit_stops,
    summarize_by_category,
)
from utils.presentation_pptx import build_overview_deck
from utils.scoring import ScoreResult, aggregate
from utils.sustainability import (
    ACCESSIBILITY_OPTIONS,
    DAYLIGHT_OPTIONS,
    METHODOLOGY_NOTE,
    OV_CLASS_OPTIONS,
    OV_NOT_SIGNIFICANT_NOTE,
    SustainabilityInputs,
    SustainabilityResult,
    compute as compute_sustainability,
    derive_ov_class_from_transit,
)
from utils.walk import compute_walk_score, fetch_walk_amenities, transit_to_score_0_100

STEP_LABELS = [
    "1. Location",
    "2. Market",
    "3. Proximity",
    "4. Footfall",
    "5. Score",
    "6. Pro-Forma",
    "7. Export",
]


def render(brand: dict, weights: dict) -> None:
    site = st.session_state.site
    current = site.get("step", 1)

    _render_progress_bar(current)
    st.write("")

    {
        1: _step1_location,
        2: _step2_market,
        3: _step3_proximity,
        4: _step4_footfall,
        5: _step5_score,
        6: _step6_pro_forma,
        7: _step7_export,
    }[current](brand, weights)

    _render_nav(site, current)


# ---------------------------------------------------------------------------
# Progress bar + nav
# ---------------------------------------------------------------------------

def _render_progress_bar(current: int) -> None:
    """Free-jump step navigation — every step is always clickable.

    Only Step 6 (Pro-Forma) is hard-gated when the adjusted Site Score
    is below 50. Per user request: navigation does
    NOT require walking left-to-right; analyses run eagerly after
    geocoding so any step can be visited at any time.
    """
    site = st.session_state.site
    cols = st.columns(len(STEP_LABELS))
    for i, label in enumerate(STEP_LABELS, 1):
        done = i < current
        is_current = i == current
        complete = site.get(f"step{i}_complete", False)
        score = site.get("score")
        gate_proforma = (i == 6 and score is not None and score < 50)

        if gate_proforma:
            icon = "🔒"
        elif is_current:
            icon = "●"
        elif complete:
            icon = "✓"
        else:
            icon = str(i)

        btn_type = "primary" if is_current else "secondary"
        label_text = f"{icon}  {label.split('. ', 1)[-1]}"

        with cols[i - 1]:
            st.markdown("<div class='stepnav-button'>", unsafe_allow_html=True)
            clicked = st.button(
                label_text,
                key=f"nav_jump_{i}",
                use_container_width=True,
                type=btn_type,
                disabled=gate_proforma,
                help=("Pro-Forma is gated when Site Score < 50."
                      if gate_proforma else "Click to jump to this step."),
            )
            st.markdown("</div>", unsafe_allow_html=True)
            if clicked and not gate_proforma:
                site["step"] = i
                st.rerun()


def _render_nav(site: dict, current: int) -> None:
    st.divider()
    col_back, _spacer, col_next = st.columns([1, 4, 1])
    with col_back:
        if current > 1 and st.button("← Back", key=f"back_{current}"):
            site["step"] = current - 1
            st.rerun()
    with col_next:
        unlocked = site.get(f"step{current}_complete", False)
        # Pro-Forma gate: Adjusted Site Score >= 50 required
        if current == 5 and site.get("score") is not None and site["score"] < 50:
            st.button(
                "🔒 Pro-Forma locked (Score < 50)",
                disabled=True, key=f"next_locked_{current}",
                help="deep financial modelling only for "
                     "sites with adjusted Score ≥ 50 (after flood-risk penalty).",
            )
        elif current < 7 and unlocked:
            if st.button("Next →", type="primary", key=f"next_{current}"):
                site["step"] = current + 1
                st.rerun()


# ===========================================================================
# STEP 1 — LOCATION
# ===========================================================================

def _step1_location(brand: dict, _weights: dict) -> None:
    site = st.session_state.site
    st.subheader("Step 1 — Location input")
    st.caption("Geocode the candidate site. Flood-risk check runs automatically (BAFU).")

    # ----- Preset locations (walk-through demo) -----
    demo_locations = brand.get("demo_locations") or []
    if demo_locations:
        with st.container(border=True):
            cols = st.columns([4, 1])
            with cols[0]:
                st.markdown("**🎯 Walk-through demo** — pre-curated ZH locations "
                             "covering Go / Vertiefte Prüfung / No-Go.")
                preset_label = st.selectbox(
                    "Preset location",
                    options=["— select —"] + [d["label"] for d in demo_locations],
                    index=0,
                    label_visibility="collapsed",
                )
                if preset_label != "— select —":
                    chosen = next(
                        d for d in demo_locations if d["label"] == preset_label
                    )
                    st.caption(f"📋 {chosen['tagline']}")
            with cols[1]:
                load_preset = st.button(
                    "Load preset →",
                    use_container_width=True, type="primary",
                    disabled=(preset_label == "— select —"),
                )
            if load_preset and preset_label != "— select —":
                chosen = next(
                    d for d in demo_locations if d["label"] == preset_label
                )
                site["address_query"] = chosen["address"]
                # Auto-trigger geocode by clearing then re-running below
                site["_preset_address"] = chosen["address"]
                st.rerun()

    col_input, col_radius = st.columns([3, 1])
    with col_input:
        # If a preset was just loaded, prefer its address as the text-input default
        default = (site.pop("_preset_address", None)
                    or site.get("address_query") or "Limmatquai 28, 8001 Zürich")
        address = st.text_input(
            "Address, city, or postal code", value=default,
            help="Free-text — e.g. 'Bahnhofstrasse 50, Zürich' or 'Sihlcity'.",
        )
    with col_radius:
        radius_m = st.selectbox(
            "Search radius", options=[250, 500, 1000, 2000],
            index=[250, 500, 1000, 2000].index(site.get("radius_m", 500)),
            format_func=lambda x: f"{x} m",
        )

    col_btn, col_iso = st.columns([1, 5])
    with col_btn:
        do_geocode = st.button("🔍 Geocode site", type="primary",
                                use_container_width=True)
    with col_iso:
        show_iso = st.checkbox(
            "10-min walking isochrone (slower first run)",
            value=site.get("show_iso", False),
        )
        site["show_iso"] = show_iso

    if do_geocode:
        with st.spinner("Resolving address via Nominatim..."):
            result = geocode(address)
        if not result:
            st.error("Address not found. Try a more specific query.")
            return
        # Reset downstream state when location changes
        for k in ("market", "proximity", "footfall", "score", "score_result",
                   "flood_risk", "proforma", "scenarios",
                   "step2_complete", "step3_complete", "step4_complete",
                   "step5_complete", "step6_complete", "pois", "_poi_key"):
            site.pop(k, None)
        site.update({
            "address_query": address,
            "name": result.display_name.split(",")[0].strip(),
            "address": result.display_name,
            "lat": result.lat,
            "lon": result.lon,
            "city": result.city,
            "country": result.country,
            "postcode": result.postcode,
            "radius_m": radius_m,
            "step1_complete": True,
        })
        # Run flood check immediately — headline analyst signal
        with st.spinner("Checking BAFU flood-risk database..."):
            try:
                site["flood_risk"] = check_flood_risk(result.lat, result.lon)
            except Exception as e:
                st.warning(f"Flood check failed ({type(e).__name__}: {e}).")
        # Eagerly run all downstream analyses so every step is reachable
        _run_eager_analyses(site, brand)
        st.rerun()

    if site.get("step1_complete"):
        site["radius_m"] = radius_m

    if not site.get("step1_complete"):
        st.info("Enter an address and click **Geocode site** to continue.")
        return

    st.success(f"📍 **{site['address']}**")

    cm1, cm2, cm3, cm4 = st.columns(4)
    cm1.metric("Latitude", f"{site['lat']:.5f}")
    cm2.metric("Longitude", f"{site['lon']:.5f}")
    cm3.metric("City", site.get("city") or "—")
    cm4.metric("Postal code", site.get("postcode") or "—")

    # Flood-risk headline panel
    fr: FloodRisk | None = site.get("flood_risk")
    if fr:
        _render_flood_panel(fr, prefix="step1")

    # Sustainability inputs (Bechtiger 2024 / ESI)
    _render_sustainability_inputs(site)

    # Cannibalization preview
    existing = brand.get("existing_stores", [])
    if existing:
        nearest = min(
            existing,
            key=lambda s: haversine_m(site["lat"], site["lon"], s["lat"], s["lon"]),
        )
        d = haversine_m(site["lat"], site["lon"], nearest["lat"], nearest["lon"])
        if d < 2000:
            st.warning(
                f"⚠️ Nearest existing {brand['brand']} location: "
                f"**{nearest['name']}** at **{d/1000:.2f} km** — "
                f"full cannibalization check in Step 3."
            )

    # Map overlays — toggleable layers. Shown HERE (after geocoding metrics)
    # so the user sees them next to the map they affect.
    st.markdown("**🗺️ Map overlays** — toggle layers for the map below.")
    o1, o2, o3, o4 = st.columns(4)
    with o1:
        show_flood = st.checkbox(
            "🌊 BAFU flood",
            value=site.get("show_flood", False),
            help="Official BAFU surface-runoff hazard map (WMS).",
        )
    with o2:
        show_running = st.checkbox(
            "🏃 Running infrastructure",
            value=site.get("show_running", False),
            help=("OSM-derived running infrastructure (route=running/hiking, "
                  "leisure=track) — Strava's public tiles require "
                  "authentication since 2023, this is the public-data "
                  "equivalent."),
        )
    with o3:
        show_ped = st.checkbox(
            "🚶 Pedestrian streets",
            value=site.get("show_pedestrian", False),
            help="OSM pedestrian / footway / living-street network.",
        )
    with o4:
        show_shop_density = st.checkbox(
            "🔥 Shop density",
            value=site.get("show_shop_density", False),
            help=("Heatmap of every OSM shop within 1.5× radius — blends "
                  "smoothly past the search circle."),
        )
    site["show_flood"] = show_flood
    site["show_running"] = show_running
    site["show_pedestrian"] = show_ped
    site["show_shop_density"] = show_shop_density

    # Second row — POI display + hotels
    o5, o6, _, _ = st.columns(4)
    with o5:
        show_pois = st.checkbox(
            "🎯 POI categories (top 100)",
            value=site.get("show_pois_step1", False),
            help=("Render 🏃 Sport · 🤝 Symbiose · 💎 Premium · ⚔️ Competitor · "
                  "⚠️ Negative · 🏬 Partner POIs as colored dots. Capped at "
                  "100 by absolute score to keep the map responsive."),
            key="step1_pois_toggle",
        )
    with o6:
        show_hotels = st.checkbox(
            "🏨 Hotels (curated)",
            value=site.get("show_hotels", False),
            help=("Curated Zürich hotels (5★=green · 4★=orange · 3★=red). "
                  "OSM under-tags stars; we maintain the list in "
                  "on_brand.json."),
            key="step1_hotels_toggle",
        )
    site["show_pois_step1"] = show_pois
    site["show_hotels"] = show_hotels

    iso_geojson = None
    if show_iso:
        with st.spinner("Computing walking isochrone via OSMnx..."):
            try:
                iso_geojson = walking_isochrone(site["lat"], site["lon"], minutes=10)
            except Exception as e:
                st.warning(f"Isochrone unavailable ({type(e).__name__}: {e}).")

    ped_gdf = None
    if show_ped:
        with st.spinner("Loading pedestrian-street network from OSM..."):
            ped_gdf = fetch_pedestrian_paths(
                site["lat"], site["lon"], site["radius_m"],
            )

    run_gdf = None
    if show_running:
        with st.spinner("Loading OSM running infrastructure..."):
            run_gdf = fetch_running_infrastructure(
                site["lat"], site["lon"], int(site["radius_m"] * 1.5),
            )

    # Shop heatmap: fetch every OSM shop within 1.5× radius so the blob
    # extends smoothly past the search circle
    shop_pts = None
    if show_shop_density:
        with st.spinner("Loading shop-density data..."):
            shops_df = fetch_all_shops(
                site["lat"], site["lon"], int(site["radius_m"] * 1.5),
            )
        if shops_df is not None and len(shops_df) > 0:
            shop_pts = list(zip(shops_df["lat"], shops_df["lon"]))

    m = build_site_map(
        lat=site["lat"], lon=site["lon"], radius_m=site["radius_m"],
        isochrone_geojson=iso_geojson, existing_stores=existing,
        show_flood_layer=show_flood,
        pedestrian_paths=ped_gdf,
        shop_heatmap_points=shop_pts,
        known_competitors=brand.get("known_competitor_locations"),
        known_hotels=brand.get("known_hotels") if show_hotels else None,
        running_infrastructure=run_gdf,
    )
    if show_pois:
        pois_step1 = _ensure_pois(site, brand, silent=True)
        if pois_step1:
            add_pois_to_map(m, pois_step1, fine_grained=False)
    st_folium(m, width=1200, height=800,
                returned_objects=[], key="map_step1")
    st.caption(
        f"Green dot = candidate · dashed circle = {site['radius_m']} m radius · "
        f"black markers = existing {brand['brand']} locations · "
        f"red ⚔️ markers = curated competitor flagships. "
        f"Toggle layers via the top-right layer control on the map."
    )


def _run_eager_analyses(site: dict, brand: dict) -> None:
    """Run Steps 2 + 3 + 4 analyses eagerly after geocoding so every
    later step is reachable from the moment a location is selected.
    """
    lat, lon, radius = site["lat"], site["lon"], site["radius_m"]

    # Step 2 — BFS demographics + OSM hotels
    try:
        mun = lookup_municipality(site.get("city"))
        site["market"] = {"municipality": mun}
        # 5★/4★ hotels via OSM tags
        features = fetch_osm_features(lat, lon, radius)
        hotels = (features[features["tourism"].astype(str).str.lower() == "hotel"]
                   if "tourism" in features.columns else None)
        if hotels is None or len(hotels) == 0:
            site["market"]["hotels_total"] = 0
            site["market"]["hotels_5star"] = 0
            site["market"]["hotels_4star"] = 0
        else:
            site["market"]["hotels_total"] = len(hotels)
            site["market"]["hotels_5star"] = int(
                hotels["stars"].astype(str).str.startswith("5").sum()
            ) if "stars" in hotels.columns else 0
            site["market"]["hotels_4star"] = int(
                hotels["stars"].astype(str).str.startswith("4").sum()
            ) if "stars" in hotels.columns else 0
        site["step2_complete"] = True
    except Exception as e:
        st.warning(f"Step 2 eager fetch: {type(e).__name__}: {e}")

    # Step 3 — POI classification (uses cached OSM features)
    try:
        _ensure_pois(site, brand, silent=True)
        site["step3_complete"] = True
    except Exception as e:
        st.warning(f"Step 3 eager fetch: {type(e).__name__}: {e}")

    # Step 4 — Walk Score + transit
    try:
        ws = compute_walk_score(lat, lon, radius_m=1000)
        t_score = transit_to_score_0_100(ws)
        site["footfall"] = {
            "walk_score": ws.score, "transit_score": t_score,
            "category_scores": ws.category_scores,
            "category_counts": ws.category_counts,
            "transit_stops_250m": ws.transit_stops_250m,
            "transit_stops_500m": ws.transit_stops_500m,
            "rail_stations": ws.rail_stations,
        }
        site["step4_complete"] = True
    except Exception as e:
        st.warning(f"Step 4 eager fetch: {type(e).__name__}: {e}")


def _render_sustainability_inputs(site: dict) -> None:
    """Step 1 expander — accessibility / daylight / ÖV-class inputs.

    Default ÖV class is "C" until Step 4 runs; after Step 4 we auto-suggest
    based on the computed transit score (user can override at any time).
    """
    si = site.get("sustainability_inputs") or SustainabilityInputs()
    footfall = site.get("footfall") or {}
    suggested_ov = derive_ov_class_from_transit(
        footfall.get("transit_score"), footfall.get("rail_stations"),
    )
    # Bump default once Step 4 has run
    if "sustainability_inputs" not in site and footfall:
        si.ov_class = suggested_ov

    with st.expander(
        "🌱 Sustainability inputs · Bechtiger 2024 / ESI (CCRS/UZH)",
        expanded=False,
    ):
        st.caption(METHODOLOGY_NOTE)
        c1, c2, c3 = st.columns(3)
        with c1:
            acc_keys = list(ACCESSIBILITY_OPTIONS.keys())
            acc_idx = acc_keys.index(si.accessibility) \
                if si.accessibility in acc_keys else 0
            si.accessibility = st.selectbox(
                "Accessibility",
                acc_keys, index=acc_idx,
                format_func=lambda k: ACCESSIBILITY_OPTIONS[k]["label"],
                help="Bechtiger 2024 (p<.001): -8 pts / -7.8% rent if lift "
                     "not wheelchair-accessible; -15 pts / -13% if no lift.",
            )
        with c2:
            day_keys = list(DAYLIGHT_OPTIONS.keys())
            day_idx = day_keys.index(si.daylight) \
                if si.daylight in day_keys else 1
            si.daylight = st.selectbox(
                "Daylight",
                day_keys, index=day_idx,
                format_func=lambda k: DAYLIGHT_OPTIONS[k]["label"],
                help="Bechtiger 2024 (p=.021): +5 pts / +6.2% rent for "
                     "natural daylight.",
            )
        with c3:
            ov_keys = list(OV_CLASS_OPTIONS.keys())
            ov_idx = ov_keys.index(si.ov_class) \
                if si.ov_class in ov_keys else 2
            si.ov_class = st.selectbox(
                f"ÖV-Güteklasse (VSS 640 290) — suggested: {suggested_ov}",
                ov_keys, index=ov_idx,
                format_func=lambda k: OV_CLASS_OPTIONS[k]["label"],
                help=OV_NOT_SIGNIFICANT_NOTE,
            )

        site["sustainability_inputs"] = si

        # Live preview of impact
        result = compute_sustainability(si)
        c1, c2 = st.columns(2)
        c1.metric(
            "Sustainability score adjustment",
            f"{result.score_delta:+d} pts",
            help="Added on top of the 100-pt base Site Score.",
        )
        c2.metric(
            "Annual rent adjustment",
            f"{result.rent_delta_pct:+.1f}%",
            help="Applied to the annual rent in Step 6 Pro-Forma.",
        )


def _render_sustainability_panel(result: SustainabilityResult,
                                   base_score: int, esi_score: int) -> None:
    st.markdown("##### 🌱 Sustainability adjustment · Bechtiger 2024 / ESI")
    c1, c2, c3 = st.columns([2, 2, 3])
    c1.metric("Base score (after flood)", f"{base_score}/100")
    c2.metric(
        "Sustainability Δ", f"{result.score_delta:+d}",
        delta=f"{result.rent_delta_pct:+.1f}% rent",
        delta_color="normal" if result.score_delta >= 0 else "inverse",
        help="Additive to the 100-pt base score.",
    )
    esi_color = ("#00C853" if esi_score >= 75
                  else "#F9A825" if esi_score >= 50 else "#D32F2F")
    c3.markdown(
        f"<div style='padding:6px 0;'>"
        f"<div style='font-size:0.75rem;color:#222;font-weight:600;"
        f"letter-spacing:0.05em;'>ESI-ADJUSTED SCORE</div>"
        f"<div style='font-size:2.4rem;color:{esi_color};font-weight:700;"
        f"line-height:1.0;'>{esi_score}<span style='font-size:1.1rem;"
        f"color:#333;'> (base+Δ)</span></div></div>",
        unsafe_allow_html=True,
    )

    rows = []
    for f in result.factors:
        rows.append({
            "Factor": f.name,
            "Setting": f.label,
            "Score Δ": f"{f.score_delta:+d} pts",
            "Rent Δ": f"{f.rent_delta_pct:+.1f}%" if f.rent_delta_pct else "—",
            "Evidence": f.evidence,
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(METHODOLOGY_NOTE)


def _render_flood_panel(fr: FloodRisk, prefix: str = "") -> None:
    bg = {
        "none":   "#E8F5E9",
        "low":    "#FFF8E1",
        "medium": "#FFF3E0",
        "high":   "#FFEBEE",
    }.get(fr.risk, "#F5F5F5")
    border = {
        "none":   "#00C853",
        "low":    "#F9A825",
        "medium": "#F57C00",
        "high":   "#D32F2F",
    }.get(fr.risk, "#9E9E9E")
    insurance_note = (f"<br><i>Business-interruption insurance estimate: "
                       f"CHF {fr.insurance_chf:,}/yr".replace(",", " ") + "</i>"
                       if fr.insurance_chf else "")
    st.markdown(
        f"<div style='background:{bg};border-left:4px solid {border};"
        f"padding:10px 14px;border-radius:6px;margin:8px 0;'>"
        f"<div style='font-size:1.0rem;font-weight:600;color:#1A1A1A;'>"
        f"{fr.label}</div>"
        f"<div style='font-size:0.85rem;color:#1A1A1A;margin-top:4px;'>"
        f"{fr.recommendation}{insurance_note}<br>"
        f"<span style='font-size:0.75rem;color:#333;'>Score impact: "
        f"<b>{fr.score_impact:+d}</b> pts · source: {fr.source}</span>"
        f"</div></div>",
        unsafe_allow_html=True,
    )


# ===========================================================================
# STEP 2 — MARKET OVERVIEW
# ===========================================================================

def _step2_market(brand: dict, _weights: dict) -> None:
    site = st.session_state.site
    st.subheader("Step 2 — Market overview")
    st.caption("Population, purchasing power, age structure, premium-environment signal.")

    if not site.get("step1_complete"):
        st.warning("Complete Step 1 first.")
        return

    mun = lookup_municipality(site.get("city"))
    site["market"] = {"municipality": mun}

    if mun is None:
        st.info(
            f"ℹ️ **{site.get('city') or 'This city'}** is outside the BFS seed dataset "
            f"(24 largest Swiss municipalities). The Site Score will use a "
            f"neutral demographic baseline. To extend: append a row to "
            f"`data/bfs/municipalities.csv`."
        )
    else:
        st.caption(f"BFS data for **{mun['city']}** ({mun['canton']}). "
                    f"Source: {mun['source']}.")

    if mun:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Population", f"{int(mun['population']):,}".replace(",", " "))
        c2.metric("5-yr growth", f"{mun['growth_5y_pct']:+.1f}%",
                   delta=f"{mun['growth_5y_pct']:+.1f}%", delta_color="normal")
        c3.metric("Kaufkraft idx", f"{int(mun['kaufkraft_index'])}",
                   help="Cantonal purchasing-power index (CH avg = 100).")
        c4.metric("Age 18–45 share", f"{mun['age_18_45_pct']:.1f}%",
                   help="On core target demographic.")
        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Hotel beds", f"{int(mun['hotel_beds']):,}".replace(",", " "),
                   help="Tourism proxy (BFS Hotellerie).")
        c6.metric("Foreign share", f"{mun['foreign_share_pct']:.1f}%",
                   help="International exposure signal.")
        c7.metric("Canton", mun["canton"])
        c8.metric("Source year", mun["source"].split()[-1])
    else:
        c1, c2, c3, c4 = st.columns(4)
        for c, lbl in zip([c1, c2, c3, c4],
                           ["Population", "5-yr growth", "Kaufkraft idx",
                            "Age 18-45 share"]):
            c.metric(lbl, "—")

    st.divider()
    st.markdown("##### 🏨 Premium-environment signal (OSM live)")
    with st.spinner("Querying OSM for hotels in radius..."):
        try:
            features = fetch_osm_features(site["lat"], site["lon"], site["radius_m"])
            hotels = features[features["tourism"].astype(str).str.lower() == "hotel"] \
                if "tourism" in features.columns else pd.DataFrame()
            n_hotels = len(hotels)
            n_5star = int((hotels["stars"].astype(str).str.startswith("5")).sum()) \
                if "stars" in hotels.columns and len(hotels) > 0 else 0
            n_4star = int((hotels["stars"].astype(str).str.startswith("4")).sum()) \
                if "stars" in hotels.columns and len(hotels) > 0 else 0
        except Exception as e:
            st.warning(f"OSM unavailable: {e}")
            n_hotels = n_5star = n_4star = 0

    # Curated hotel data — merge with OSM live count (OSM under-tags stars=*)
    curated = brand.get("known_hotels", []) or []
    curated_in = []
    for h in curated:
        d = haversine_m(site["lat"], site["lon"], h["lat"], h["lon"])
        if d <= site["radius_m"]:
            curated_in.append({**h, "distance_m": d})
    curated_5 = sum(1 for h in curated_in if h.get("stars") == 5)
    curated_4 = sum(1 for h in curated_in if h.get("stars") == 4)
    n_5_combined = max(n_5star, curated_5)
    n_4_combined = max(n_4star, curated_4)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Hotels in radius (OSM)", n_hotels)
    c2.metric(
        "⭐ 5-star hotels", n_5_combined,
        delta=f"OSM {n_5star} · curated {curated_5}",
        delta_color="off",
        help="OSM under-tags `stars=*` in CH — curated list catches the "
              "11+ real Zürich 5★ flagships.",
    )
    c3.metric(
        "⭐ 4-star hotels", n_4_combined,
        delta=f"OSM {n_4star} · curated {curated_4}",
        delta_color="off",
    )
    c4.metric("Curated hotels in radius", len(curated_in))

    site["market"]["hotels_total"] = max(n_hotels, len(curated_in))
    site["market"]["hotels_5star"] = n_5_combined
    site["market"]["hotels_4star"] = n_4_combined
    site["market"]["curated_hotels_in_radius"] = curated_in

    if curated_in:
        with st.expander(f"📋 Curated hotels in radius ({len(curated_in)})"):
            df = pd.DataFrame([
                {"Name": h["name"], "Stars": f"{h.get('stars', '?')}★",
                 "Tier": h.get("tier", "—"),
                 "Address": h.get("address", ""),
                 "Distance": f"{h['distance_m']/1000:.2f} km"}
                for h in sorted(curated_in, key=lambda x: x["distance_m"])
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

    site["step2_complete"] = True


# ===========================================================================
# STEP 3 — PROXIMITY INTELLIGENCE
# ===========================================================================

def _step3_proximity(brand: dict, _weights: dict) -> None:
    site = st.session_state.site
    st.subheader("Step 3 — Proximity intelligence")
    st.caption(
        "Sports venues · symbiose partners · direct competitors · negative "
        "environment · premium signals. One OSM query, brand-aware classification."
    )

    if not site.get("step1_complete"):
        st.warning("Complete Step 1 first.")
        return

    pois = _ensure_pois(site, brand)
    if pois is None:
        return

    summary = summarize_by_category(pois)

    cats_ordered = ["sport", "symbiose", "premium", "competitor", "negative", "partner"]
    cols = st.columns(len(cats_ordered))
    for i, cat in enumerate(cats_ordered):
        s = summary[cat]
        with cols[i]:
            delta_color = "normal" if s["score_sum"] >= 0 else "inverse"
            st.metric(
                CATEGORY_LABEL[cat], f"{s['count']}",
                delta=f"{s['score_sum']:+.0f} pts",
                delta_color=delta_color,
                help=f"{s['count']} POIs in {site['radius_m']} m · "
                     f"distance-weighted raw sum.",
            )

    _render_cannibalization(site, brand)

    # Always-visible curated competitor section — shows what's on the
    # ground even when OSM under-tags mono-brand stores
    _render_curated_competitors(site, brand)

    st.markdown("##### POI breakdown")
    tabs = st.tabs([CATEGORY_LABEL[c] for c in cats_ordered])
    for tab, cat in zip(tabs, cats_ordered):
        with tab:
            _render_category_table(summary[cat]["items"])

    st.markdown("##### Map · color-coded POIs + analytical overlays")
    o1, o2, o3, o4 = st.columns(4)
    with o1:
        show_flood = st.checkbox(
            "🌊 BAFU flood", value=site.get("show_flood", False),
            key="step3_flood_toggle",
        )
    with o2:
        show_running = st.checkbox(
            "🏃 Running infrastructure", value=site.get("show_running", False),
            key="step3_running_toggle",
        )
    with o3:
        show_ped = st.checkbox(
            "🚶 Pedestrian streets", value=site.get("show_pedestrian", False),
            key="step3_ped_toggle",
        )
    with o4:
        show_shop_density = st.checkbox(
            "🔥 Shop density", value=site.get("show_shop_density", False),
            key="step3_density_toggle",
        )
    o5, o6, o7, o8 = st.columns(4)
    with o5:
        show_transit = st.checkbox(
            "🚆 Transit stops", value=site.get("show_transit", False),
            key="step3_transit_toggle",
            help="Rail · tram · bus stops from OSM, color-coded by mode.",
        )
    with o6:
        show_all_shops = st.checkbox(
            "🛍️ All shops (generic)", value=site.get("show_all_shops", False),
            key="step3_allshops_toggle",
            help=("Every OSM shop=* in radius, regardless of brand "
                  "classification — visual retail density."),
        )
    with o7:
        show_cannib = st.checkbox(
            "⚠️ Cannibalization overlap", value=site.get("show_cannib", False),
            key="step3_cannib_toggle",
            help=("Per existing-store catchment circle + connector line, "
                  "color-coded by overlap risk."),
        )
    with o8:
        fine_pois = st.checkbox(
            "🎯 Fine-grained POIs", value=site.get("show_fine_pois", False),
            key="step3_finepois_toggle",
            help=("Split symbiose into sport shops vs. other; separate "
                  "sport-facility group."),
        )

    o9, o10, o11, o12 = st.columns(4)
    with o9:
        show_competitor_heat = st.checkbox(
            "⚔️ Competitor heatmap",
            value=site.get("show_competitor_heat", False),
            key="step3_competitor_heat",
            help="Density heatmap of detected + curated competitors.",
        )
    with o10:
        show_walk_heat = st.checkbox(
            "🟢 Walk-amenity heatmap",
            value=site.get("show_walk_heat", False),
            key="step3_walk_heat",
        )
    with o11:
        show_sport_heat = st.checkbox(
            "🏋️ Sport heatmap",
            value=site.get("show_sport_heat", False),
            key="step3_sport_heat",
        )
    with o12:
        show_hotels_step3 = st.checkbox(
            "🏨 Hotels (curated)",
            value=site.get("show_hotels", False),
            key="step3_hotels_toggle",
        )
    o13, o14, _o15, _o16 = st.columns(4)
    with o13:
        show_pois = st.checkbox(
            "🔵 POI markers", value=site.get("show_pois_step3", True),
            key="step3_pois_toggle",
            help=("The classified POI category markers (sport / symbiose / "
                  "premium / competitor / negative / partner). Turn off to "
                  "isolate a single overlay layer."),
        )
    with o14:
        show_existing = st.checkbox(
            "🏪 Existing On stores", value=site.get("show_existing", True),
            key="step3_existing_toggle",
            help="Black markers for existing On locations (drives cannibalization).",
        )

    site["show_competitor_heat"] = show_competitor_heat
    site["show_walk_heat"] = show_walk_heat
    site["show_sport_heat"] = show_sport_heat
    site["show_hotels"] = show_hotels_step3
    site["show_pois_step3"] = show_pois
    site["show_existing"] = show_existing

    site["show_flood"] = show_flood
    site["show_running"] = show_running
    site["show_pedestrian"] = show_ped
    site["show_shop_density"] = show_shop_density
    site["show_transit"] = show_transit
    site["show_all_shops"] = show_all_shops
    site["show_cannib"] = show_cannib
    site["show_fine_pois"] = fine_pois

    ped_gdf = (fetch_pedestrian_paths(site["lat"], site["lon"], site["radius_m"])
                if show_ped else None)
    run_gdf = (fetch_running_infrastructure(
                site["lat"], site["lon"], int(site["radius_m"] * 1.5))
                if show_running else None)
    transit_df = (fetch_transit_stops(site["lat"], site["lon"], site["radius_m"])
                   if show_transit else None)
    all_shops_df = (fetch_all_shops(site["lat"], site["lon"], site["radius_m"])
                     if show_all_shops else None)

    # Shop density — extended-radius generic shops for the heatmap
    shop_pts = None
    if show_shop_density:
        shops_ext = fetch_all_shops(
            site["lat"], site["lon"], int(site["radius_m"] * 1.5),
        )
        if shops_ext is not None and len(shops_ext) > 0:
            shop_pts = list(zip(shops_ext["lat"], shops_ext["lon"]))

    # Optional density-heatmap point sources
    walk_heat_pts = None
    if show_walk_heat:
        from utils.walk import fetch_walk_amenities as _fwa
        wdf = _fwa(site["lat"], site["lon"], max(1000, site["radius_m"]))
        if wdf is not None and len(wdf) > 0:
            walk_heat_pts = list(zip(wdf["lat"], wdf["lon"]))
    sport_heat_pts = ([(p.lat, p.lon) for p in pois if p.category == "sport"]
                       if show_sport_heat else None)
    competitor_heat_pts = (
        [(p.lat, p.lon) for p in pois if p.category == "competitor"]
        if show_competitor_heat else None
    )

    # Existing-store markers show when either toggled directly OR when
    # cannibalization is on (the overlay needs the store locations).
    existing = (brand.get("existing_stores", [])
                 if (show_existing or show_cannib) else None)

    m = build_site_map(
        lat=site["lat"], lon=site["lon"], radius_m=site["radius_m"],
        existing_stores=existing,
        show_flood_layer=show_flood,
        pedestrian_paths=ped_gdf,
        shop_heatmap_points=shop_pts,
        transit_stops_df=transit_df,
        all_shops_df=all_shops_df,
        cannibalization=show_cannib,
        walk_heatmap_points=walk_heat_pts,
        sport_heatmap_points=sport_heat_pts,
        competitor_heatmap_points=competitor_heat_pts,
        known_competitors=(brand.get("known_competitor_locations")
                            if show_pois else None),
        known_hotels=brand.get("known_hotels") if show_hotels_step3 else None,
        running_infrastructure=run_gdf,
    )
    if show_pois:
        add_pois_to_map(m, pois, fine_grained=fine_pois, cap=300)
    st_folium(m, width=1200, height=800,
                returned_objects=[], key="map_step3")
    st.caption(
        "Marker size scales with score magnitude. "
        "Toggle categories and overlays via the layer control (top-right)."
    )

    site["step3_complete"] = True


def _ensure_pois(site: dict, brand: dict,
                  silent: bool = False) -> list[POI] | None:
    key = (site["lat"], site["lon"], site["radius_m"])
    if site.get("_poi_key") == key and "pois" in site:
        return site["pois"]
    spinner_ctx = st.spinner(f"Fetching OSM features within {site['radius_m']} m...") \
        if not silent else _NoOpCtx()
    with spinner_ctx:
        try:
            features = fetch_osm_features(site["lat"], site["lon"], site["radius_m"])
        except Exception as e:
            if not silent:
                st.error(f"OSM fetch failed: {type(e).__name__}: {e}")
            return None
    if features is None or len(features) == 0:
        if not silent:
            st.warning("No OSM features found in this radius. Try widening it.")
        site["pois"] = []
        site["_poi_key"] = key
        return []
    pois = classify_pois(features, brand,
                           candidate_lat=site["lat"], candidate_lon=site["lon"],
                           radius_m=site["radius_m"])
    site["pois"] = pois
    site["_poi_key"] = key
    return pois


class _NoOpCtx:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _render_category_table(items: list[POI]) -> None:
    if not items:
        st.caption("_No POIs of this type within the search radius._")
        return
    rows = []
    for p in sorted(items, key=lambda x: x.distance_m):
        rows.append({
            "Name": p.name,
            "Type": p.subcategory.replace("_", " ").title(),
            "Distance (m)": int(p.distance_m),
            "Bucket": p.bucket,
            "Base": f"{p.base_score:+.1f}",
            "Score contribution": f"{p.score:+.1f}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_curated_competitors(site: dict, brand: dict) -> None:
    """Always-visible competitor-presence block for Step 3.

    OSM under-tags mono-brand competitor stores in CH (verified May 2026 —
    Adidas Bahnhofstrasse 56 doesn't appear in OSM `shop=*`, nor does the
    closed Nike Sihlcity). We curate this from public sources in
    `on_brand.json → known_competitor_locations + multi_brand_known_locations`.

    See SCORING.md §4 for the rationale.
    """
    radius_m = site["radius_m"]
    lat, lon = site["lat"], site["lon"]

    curated_mono = brand.get("known_competitor_locations", []) or []
    curated_multi = brand.get("multi_brand_known_locations", []) or []

    # Mono-brand competitors within radius (active + closed for context)
    mono_in = []
    for e in curated_mono:
        d = haversine_m(lat, lon, e["lat"], e["lon"])
        if d <= radius_m * 2:   # show closed-store narrative within 2× radius too
            mono_in.append((d, e))
    mono_in.sort(key=lambda x: x[0])

    # Multi-brand specialty within radius (always active)
    multi_in = []
    for e in curated_multi:
        d = haversine_m(lat, lon, e["lat"], e["lon"])
        if d <= radius_m:
            multi_in.append((d, e))
    multi_in.sort(key=lambda x: x[0])

    if not mono_in and not multi_in:
        st.info(
            "ℹ️ **Curated competitor check:** no mono-brand competitor "
            "flagships or multi-brand specialty chains in radius — Zürich's "
            "mono-brand sport-retail footprint is thin (verified May 2026). "
            "See SCORING.md §4."
        )
        return

    st.markdown("##### ⚔️ Curated competitor presence · analyst-verified ground truth")

    if mono_in:
        active_mono = [(d, e) for d, e in mono_in if e.get("status") == "active"]
        closed_mono = [(d, e) for d, e in mono_in if e.get("status") == "closed"]
        if active_mono:
            rows = []
            for d, e in active_mono:
                rows.append({
                    "Brand": e.get("brand", "—"),
                    "Name": e["name"],
                    "Address": e.get("address", ""),
                    "Distance": f"{d/1000:.2f} km",
                    "Status": "🟢 active",
                })
            st.markdown("**Active mono-brand flagships in radius:**")
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                          hide_index=True)
        if closed_mono:
            st.markdown(
                f"**📜 Closed mono-brand flagships near this site** "
                f"(within 2× radius — analyst-context, no score impact):"
            )
            rows = []
            for d, e in closed_mono:
                rows.append({
                    "Brand": e.get("brand", "—"),
                    "Name": e["name"],
                    "Distance": f"{d/1000:.2f} km",
                    "Closed": e.get("as_of", "—"),
                    "Note": e.get("note", "")[:120],
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                          hide_index=True)
            st.caption(
                "*Closed flagships are kept for narrative — Zürich's mono-brand "
                "sport retail has been thinning (Nike, Puma both exited within "
                "the past 6 months). Score is unaffected.*"
            )

    if multi_in:
        st.markdown("**Multi-brand sneaker chains in radius** "
                     "(sell competitor products heavily — Foot Locker, Snipes, Titolo):")
        rows = []
        for d, e in multi_in:
            rows.append({
                "Name": e["name"],
                "Address": e.get("address", ""),
                "Distance (m)": f"{int(d):,}".replace(",", " "),
                "Score impact": "−4 × distance multiplier",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                      hide_index=True)

    st.caption(
        "Source: analyst-curated overrides in `data/config/on_brand.json` "
        "(researched May 2026 — see SCORING.md §4 for the full methodology)."
    )


def _render_cannibalization(site: dict, brand: dict) -> None:
    existing = brand.get("existing_stores", [])
    if not existing:
        return
    rows = []
    for s in existing:
        d = haversine_m(site["lat"], site["lon"], s["lat"], s["lon"])
        if d < 500:
            overlap, badge = "≈ 80% (high)", "🔴"
        elif d < 1000:
            overlap, badge = "≈ 50% (medium)", "🟠"
        elif d < 2000:
            overlap, badge = "≈ 20% (low)", "🟡"
        else:
            overlap, badge = "< 5% (none)", "🟢"
        rows.append({
            "Badge": badge, "Existing site": s["name"],
            "Distance": f"{d/1000:.2f} km",
            "Est. isochrone overlap": overlap,
        })
    rows.sort(key=lambda r: float(r["Distance"].split()[0]))
    critical = sum(1 for r in rows if r["Badge"] in ("🔴", "🟠"))
    if critical:
        st.warning(
            f"⚠️ **Cannibalization risk:** {critical} existing {brand['brand']} "
            f"location(s) within 1 km. Distance-based estimate."
        )
    else:
        st.info(
            f"✅ No cannibalization risk vs. existing {brand['brand']} "
            f"locations within 1 km."
        )
    with st.expander("Detailed cannibalization table"):
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ===========================================================================
# STEP 4 — FOOTFALL & ACCESSIBILITY
# ===========================================================================

def _step4_footfall(_brand: dict, _weights: dict) -> None:
    site = st.session_state.site
    st.subheader("Step 4 — Footfall & accessibility")
    st.caption("Amenity-richness Walk Score · ÖV transit accessibility.")

    if not site.get("step1_complete"):
        st.warning("Complete Step 1 first.")
        return

    with st.spinner("Computing Walk Score and transit access from OSM..."):
        try:
            ws = compute_walk_score(site["lat"], site["lon"], radius_m=1000)
        except Exception as e:
            st.error(f"Walk Score failed: {type(e).__name__}: {e}")
            return

    t_score = transit_to_score_0_100(ws)
    site["footfall"] = {
        "walk_score": ws.score, "transit_score": t_score,
        "category_scores": ws.category_scores,
        "category_counts": ws.category_counts,
        "transit_stops_250m": ws.transit_stops_250m,
        "transit_stops_500m": ws.transit_stops_500m,
        "rail_stations": ws.rail_stations,
    }

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Walk Score", f"{ws.score}/100", help=ws.method)
    c2.metric("Transit Score", f"{t_score}/100",
               help="Combined rail + bus/tram stop accessibility.")
    c3.metric("Transit stops ≤ 500 m", ws.transit_stops_500m)
    c4.metric("Rail stations in radius", ws.rail_stations)

    st.markdown("##### Walk Score breakdown by amenity category")
    cat_df = pd.DataFrame([
        {"Category": cat.title(),
         "POI count (≤ 1 km)": ws.category_counts[cat],
         "Sub-score": ws.category_scores[cat]}
        for cat in ws.category_scores
    ]).sort_values("Sub-score", ascending=True)
    fig = px.bar(
        cat_df, x="Sub-score", y="Category", orientation="h",
        text="POI count (≤ 1 km)",
        color="Sub-score", color_continuous_scale="Greens",
        range_x=[0, 100],
    )
    fig.update_layout(height=300, margin=dict(l=20, r=20, t=10, b=20),
                       showlegend=False, coloraxis_showscale=False)
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "ℹ️ The original Walk Score™ uses a Pandana walking-network model. "
        "This implementation uses amenity richness within walking distance — "
        "the analyst-facing interpretation is the same."
    )

    # Walk amenities as a map layer — the user can see WHERE the
    # walkability comes from, not just the aggregate number.
    st.markdown("##### 🗺️ Walk amenities · map view")
    st.caption("Every walk-amenity category from the Walk Score, plotted "
                "on the map. Toggle individual categories via the layer "
                "control (top-right).")
    with st.spinner("Loading walk amenities..."):
        walk_df = fetch_walk_amenities(site["lat"], site["lon"], 1000)

    walk_heat_pts = (list(zip(walk_df["lat"], walk_df["lon"]))
                       if walk_df is not None and len(walk_df) > 0 else None)
    m_walk = build_site_map(
        lat=site["lat"], lon=site["lon"], radius_m=1000,
        existing_stores=brand.get("existing_stores", []),
        show_flood_layer=False,
        walk_amenities_df=walk_df,
        walk_heatmap_points=walk_heat_pts,
    )
    st_folium(m_walk, width=1200, height=800,
                returned_objects=[], key="map_step4_walk")

    site["step4_complete"] = True


# ===========================================================================
# STEP 5 — SITE SCORE
# ===========================================================================

def _step5_score(brand: dict, weights: dict) -> None:
    site = st.session_state.site
    st.subheader("Step 5 — Site Score (0–100)")
    st.caption("Glass-Box aggregation · every dimension normalised 0–100 before weighting.")

    missing = []
    if not site.get("step2_complete"): missing.append("Step 2 (Market)")
    if not site.get("step3_complete"): missing.append("Step 3 (Proximity)")
    if not site.get("step4_complete"): missing.append("Step 4 (Footfall)")
    if missing:
        st.warning(
            "The Site Score aggregates Steps 2-4 outputs. Please complete: "
            + ", ".join(missing) + "."
        )
        return

    pois = list(site.get("pois", []))
    result: ScoreResult = aggregate(
        pois=pois, weights=weights,
        municipality=site.get("market", {}).get("municipality"),
        walk_score=site.get("footfall", {}).get("walk_score"),
        transit_score=site.get("footfall", {}).get("transit_score"),
    )

    # Apply flood penalty post-aggregation
    fr: FloodRisk | None = site.get("flood_risk")
    flood_penalty = fr.score_impact if fr else 0
    raw_total = result.total
    adjusted_total = max(0, min(100, raw_total + flood_penalty))

    # Sustainability adjustment (Bechtiger 2024 / ESI) — additive
    # outside the 100-pt base score.
    si = site.get("sustainability_inputs") or SustainabilityInputs()
    sust_result: SustainabilityResult = compute_sustainability(si)
    esi_total = adjusted_total + sust_result.score_delta

    site["score_raw"] = raw_total
    site["score"] = adjusted_total
    site["score_esi"] = esi_total
    site["score_result"] = result
    site["sustainability_result"] = sust_result

    # Headline
    col_score, col_verdict, col_pei = st.columns([2, 2, 2])
    color = ("#00C853" if adjusted_total >= 75
              else "#F9A825" if adjusted_total >= 50 else "#D32F2F")
    verdict = ("🟢 Go" if adjusted_total >= 75
                else "🟡 Vertiefte Prüfung" if adjusted_total >= 50
                else "🔴 No-Go")
    with col_score:
        st.markdown(
            f"<div style='text-align:center;padding:18px 0;'>"
            f"<div style='font-size:0.85rem;color:#222;font-weight:600;"
            f"letter-spacing:0.05em;'>SITE SCORE</div>"
            f"<div style='font-size:3.6rem;color:{color};font-weight:700;"
            f"line-height:1.0;'>{adjusted_total}<span style='font-size:1.5rem;"
            f"color:#333;'>/100</span></div>"
            + (f"<div style='font-size:0.8rem;color:#333;margin-top:6px;'>"
                f"raw {raw_total} {flood_penalty:+d} flood = "
                f"<b>{adjusted_total}</b></div>"
                if flood_penalty else "")
            + "</div>",
            unsafe_allow_html=True,
        )
    with col_verdict:
        st.markdown(
            f"<div style='text-align:center;padding:18px 0;'>"
            f"<div style='font-size:0.85rem;color:#222;font-weight:600;"
            f"letter-spacing:0.05em;'>VERDICT</div>"
            f"<div style='font-size:2rem;font-weight:700;margin-top:14px;'>"
            f"{verdict}</div></div>",
            unsafe_allow_html=True,
        )
    with col_pei:
        pei_color = ("#00C853" if result.pei >= 7
                      else "#F9A825" if result.pei >= 5 else "#D32F2F")
        st.markdown(
            f"<div style='text-align:center;padding:18px 0;'>"
            f"<div style='font-size:0.85rem;color:#222;font-weight:600;"
            f"letter-spacing:0.05em;'>PREMIUM ENV. INDEX</div>"
            f"<div style='font-size:3.6rem;color:{pei_color};font-weight:700;"
            f"line-height:1.0;'>{result.pei:.1f}<span style='font-size:1.5rem;"
            f"color:#333;'>/10</span></div></div>",
            unsafe_allow_html=True,
        )

    # Note: flood-risk verbal panel is intentionally NOT repeated here —
    # see Step 1 for the full flood-risk panel (recommendation, insurance,
    # source). The penalty stays visible in the score breakdown below for
    # transparency. The 🌊 map layer is still toggleable on every map.

    # Sustainability adjustment panel (Bechtiger 2024 / ESI)
    _render_sustainability_panel(sust_result, adjusted_total, esi_total)

    # Brand premium-threshold check
    min_pei = brand.get("min_premium_env_index", 5)
    if result.pei < min_pei:
        st.warning(
            f"⚠️ **Premium positioning mismatch:** PEI {result.pei:.1f} < "
            f"{min_pei} threshold. Surrounding environment may not match "
            f"{brand['brand']}'s premium positioning."
        )

    if result.saturation_n >= 4:
        st.warning(
            f"⚠️ **Market saturation:** {result.saturation_n} direct "
            f"competitors in radius — penalty floored at -15."
        )
    elif result.saturation_n == 1:
        st.info(
            "ℹ️ Exactly one direct competitor — penalty neutralised "
            "(cluster-formation signal)."
        )

    # Glass Box
    st.markdown("##### Score breakdown · Glass Box")
    dims = result.dimensions
    rows = [{
        "Dimension": k.replace("_", " ").title(),
        "Score (0-100)": round(d["norm"], 1),
        "Weight": d["weight"],
        "Contribution": round(d["contribution"], 1),
    } for k, d in dims.items()]

    if flood_penalty:
        rows.append({
            "Dimension": "Flood risk (post-agg)",
            "Score (0-100)": "—",
            "Weight": "—",
            "Contribution": float(flood_penalty),
        })

    df = pd.DataFrame(rows).sort_values("Contribution", ascending=True)

    fig = go.Figure()
    text_labels = []
    for _, r in df.iterrows():
        if r["Score (0-100)"] == "—":
            text_labels.append(f"{r['Contribution']:+.0f} pts (flood penalty)")
        else:
            text_labels.append(
                f"{r['Contribution']:.1f} pts "
                f"({r['Score (0-100)']:.0f}/100 × {r['Weight']:.0%})"
            )
    fig.add_trace(go.Bar(
        x=df["Contribution"], y=df["Dimension"], orientation="h",
        text=text_labels, textposition="outside",
        marker_color=["#00C853" if c >= 0 else "#D32F2F" for c in df["Contribution"]],
    ))
    fig.update_layout(
        height=420, margin=dict(l=20, r=160, t=20, b=20),
        xaxis_title="Contribution to Site Score (post-flood)",
        showlegend=False,
        xaxis=dict(range=[min(-25, df["Contribution"].min() * 1.3),
                           max(30, df["Contribution"].max() * 1.4)]),
    )
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Per-dimension sub-scores"):
        for key, d in dims.items():
            st.markdown(
                f"**{key.replace('_', ' ').title()}** · "
                f"{d['norm']:.1f}/100 × {d['weight']:.0%} = "
                f"**{d['contribution']:.1f}** pts"
            )
            for sub_key, sub_val in d["sub"].items():
                if sub_key == "_note":
                    st.caption(f"_{sub_val}_")
                    continue
                val, display = (sub_val if isinstance(sub_val, tuple)
                                else (sub_val, str(sub_val)))
                if val is None:
                    st.write(f"  • {sub_key}: {display}")
                else:
                    st.write(f"  • {sub_key}: {display} → {val:.1f}/100")
            st.write("")

    with st.expander("Premium Environment Index breakdown"):
        for k, (pts, display) in result.pei_breakdown.items():
            st.write(f"  • {k}: {display} → {pts:.1f} pts")
        st.write(f"  **PEI total: {result.pei:.1f}/10**")

    if result.cluster_extras:
        with st.expander("Cluster penalties applied"):
            for k, v in result.cluster_extras.items():
                st.write(f"  • {k.replace('_', ' ').title()}: {v:+.1f} pts")

    # -------------------------------------------------------------------
    # 🗺️ Site Overview Map — every analytical layer in one view
    # -------------------------------------------------------------------
    st.markdown("##### 🗺️ Site Overview Map · all analytical layers")
    st.caption(
        "Every layer in one place — toggle via the top-right layer control. "
        "Defaults show the layers the score depends on; the rest is one click away."
    )

    pois = site.get("pois", [])
    radius_m = site["radius_m"]
    lat, lon = site["lat"], site["lon"]

    # Fetch the supplementary overview layers (cached — cheap on revisit)
    with st.spinner("Loading overview-map layers from OSM..."):
        transit_df = fetch_transit_stops(lat, lon, radius_m)
        all_shops_df = fetch_all_shops(lat, lon, radius_m)
        ped_gdf = fetch_pedestrian_paths(lat, lon, radius_m)

    iso_geojson = None
    if site.get("show_iso"):
        try:
            iso_geojson = walking_isochrone(lat, lon, minutes=10)
        except Exception:
            iso_geojson = None

    shop_pts = [
        (p.lat, p.lon) for p in pois
        if p.category in ("symbiose", "premium", "competitor")
    ]

    walk_df = fetch_walk_amenities(lat, lon, max(1000, radius_m))
    run_gdf_ov = fetch_running_infrastructure(lat, lon, int(radius_m * 1.5))

    # Density-heatmap point sources — extended radius for smooth blending
    shops_ext = fetch_all_shops(lat, lon, int(radius_m * 1.5))
    shop_density_pts = (list(zip(shops_ext["lat"], shops_ext["lon"]))
                         if shops_ext is not None and len(shops_ext) > 0 else None)
    walk_density_pts = (list(zip(walk_df["lat"], walk_df["lon"]))
                         if walk_df is not None and len(walk_df) > 0 else None)
    sport_density_pts = [(p.lat, p.lon) for p in pois if p.category == "sport"]
    competitor_density_pts = [(p.lat, p.lon) for p in pois if p.category == "competitor"]

    overview_map = build_site_map(
        lat=lat, lon=lon, radius_m=radius_m,
        isochrone_geojson=iso_geojson,
        existing_stores=brand.get("existing_stores", []),
        show_flood_layer=True,
        pedestrian_paths=ped_gdf,
        shop_heatmap_points=shop_density_pts,
        transit_stops_df=transit_df,
        all_shops_df=all_shops_df,
        cannibalization=True,
        walk_amenities_df=walk_df,
        walk_heatmap_points=walk_density_pts,
        sport_heatmap_points=sport_density_pts,
        competitor_heatmap_points=competitor_density_pts,
        known_competitors=brand.get("known_competitor_locations"),
        known_hotels=brand.get("known_hotels"),
        running_infrastructure=run_gdf_ov,
    )
    add_pois_to_map(overview_map, pois, fine_grained=True, cap=400)
    st_folium(overview_map, width=1200, height=800,
                returned_objects=[], key="map_step5_overview")
    st.caption(
        "**Default ON:** candidate · radius · existing stores · cannibalization · "
        "sport facilities · sport shops · other symbiose · premium · "
        "competitors · negative env. · rail · tram · 🌊 BAFU flood · "
        "🏃 running infrastructure.  "
        "**Default OFF** (click in layer control): bus · all shops · pedestrian · "
        "shop-density heatmap · walk-density heatmap · sport-density heatmap · "
        "competitor-density heatmap · wholesale partners. "
    )

    site["step5_complete"] = True


# ===========================================================================
# STEP 6 — PRO-FORMA (DCF + scenarios + sensitivity)
# ===========================================================================

def _step6_pro_forma(brand: dict, _weights: dict) -> None:
    site = st.session_state.site
    st.subheader("Step 6 — Leasehold Pro-Forma · 5-yr cashflow")
    st.caption(
        "**Leasehold** model — On runs a DTC strategy on leased premises, "
        "no property purchase. Year-0 CAPEX = fit-out spend (typical "
        "CHF 4'000/m²), recovered over the lease via EBITDA − rent − "
        "insurance − OPEX. Industry KPIs (Occupancy Cost Ratio, Cash-on-"
        "Cash, Break-even Sales) shown below."
    )

    if site.get("score") is None:
        st.warning("Complete Steps 1–5 first.")
        return
    if site["score"] < 50:
        st.error("Site Score below 50 — Pro-Forma is gated.")
        return

    # Inputs (start from brand defaults, persist in session)
    default_inputs = ProFormaInputs.defaults(brand)
    inp = site.get("proforma_inputs") or default_inputs
    # Auto-inject flood insurance
    fr: FloodRisk | None = site.get("flood_risk")
    if fr and inp.insurance_yr_chf == 0 and fr.insurance_chf:
        inp.insurance_yr_chf = float(fr.insurance_chf)

    # Sustainability rent adjustment (Bechtiger 2024) — applied to the
    # effective rent used in the DCF, NOT mutated into the list rent input.
    sust: SustainabilityResult | None = site.get("sustainability_result")
    sust_rent_delta_pct = sust.rent_delta_pct if sust else 0.0
    if sust_rent_delta_pct:
        st.info(
            f"🌱 **Sustainability rent adjustment (Bechtiger 2024):** "
            f"**{sust_rent_delta_pct:+.1f}%** applied to the annual rent "
            f"below. Drivers: "
            + ", ".join(
                f"{f.name} {f.rent_delta_pct:+.1f}%" for f in sust.factors
                if f.rent_delta_pct
            )
            + "."
        )

    st.markdown("##### Lease & build-out")
    c1, c2, c3, c4 = st.columns(4)
    inp.area_sqm = c1.number_input(
        "Area (m²)", min_value=50.0, max_value=2000.0,
        value=float(inp.area_sqm), step=10.0,
    )
    inp.rent_per_sqm_yr = c2.number_input(
        "Rent (CHF/m²/yr)", min_value=100.0, max_value=8000.0,
        value=float(inp.rent_per_sqm_yr), step=50.0,
    )
    inp.lease_years = c3.number_input(
        "Lease term (yr)", min_value=1, max_value=20,
        value=int(inp.lease_years), step=1,
    )
    inp.rent_free_months = c4.number_input(
        "Rent-free (months)", min_value=0, max_value=24,
        value=int(inp.rent_free_months), step=1,
    )
    c5, c6, c7, c8 = st.columns(4)
    inp.capex_per_sqm = c5.number_input(
        "CAPEX (CHF/m²)", min_value=500.0, max_value=12000.0,
        value=float(inp.capex_per_sqm), step=100.0,
        help=f"On-typical: CHF {brand['typical_capex_per_sqm_chf']:,}/m²".replace(",", " "),
    )
    inp.insurance_yr_chf = c6.number_input(
        "Insurance (CHF/yr)", min_value=0.0, max_value=50000.0,
        value=float(inp.insurance_yr_chf), step=500.0,
        help=("Auto-injected from BAFU flood-risk class. "
              "Edit if you have a quote."),
    )
    inp.other_opex_pct_of_rev = c7.number_input(
        "Other OPEX (% rev)", min_value=0.0, max_value=20.0,
        value=float(inp.other_opex_pct_of_rev), step=0.5,
    )
    inp.discount_rate_pct = c8.number_input(
        "Discount rate (%)", min_value=2.0, max_value=20.0,
        value=float(inp.discount_rate_pct), step=0.5,
    )

    # Rent escalation — Swiss commercial leases typically index-link 1-3%
    c_esc, _, _ = st.columns([1, 2, 3])
    with c_esc:
        inp.rent_escalation_pct = st.number_input(
            "Rent escalation p.a. (%)",
            min_value=0.0, max_value=8.0,
            value=float(inp.rent_escalation_pct), step=0.25,
            help=("Annual rent uplift over the lease term — Swiss "
                  "commercial leases typically index-link to LIK at "
                  "1-3% per year."),
        )

    st.markdown("##### Revenue")
    c9, c10, c11 = st.columns(3)
    inp.revenue_y1 = c9.number_input(
        "Revenue Year 1 (CHF)", min_value=100_000.0, max_value=20_000_000.0,
        value=float(inp.revenue_y1), step=100_000.0,
        help="Use an analog store for benchmarking.",
    )
    inp.revenue_growth_pct = c10.number_input(
        "Revenue growth p.a. (%)", min_value=-10.0, max_value=30.0,
        value=float(inp.revenue_growth_pct), step=0.5,
    )
    inp.ebitda_margin_pct = c11.number_input(
        "EBITDA margin (%)", min_value=-20.0, max_value=50.0,
        value=float(inp.ebitda_margin_pct), step=0.5,
    )

    site["proforma_inputs"] = inp

    # Sensitivity sliders (live)
    st.markdown("##### 🎚️ Sensitivity")
    sc1, sc2 = st.columns(2)
    rent_delta = sc1.slider(
        "Rent Δ (%)", min_value=-30, max_value=30, value=0, step=1,
        help="Stress-test rent vs. plan. Live update.",
    )
    footfall_delta = sc2.slider(
        "Revenue Δ (%) — footfall proxy",
        min_value=-50, max_value=50, value=0, step=1,
    )

    from dataclasses import replace as _r
    # Effective rent = list rent × sensitivity stress × sustainability adjustment
    sust_factor = 1 + sust_rent_delta_pct / 100.0
    sens_factor = 1 + rent_delta / 100.0
    effective_rent = inp.rent_per_sqm_yr * sens_factor * sust_factor
    stressed = _r(
        inp,
        rent_per_sqm_yr=effective_rent,
        revenue_y1=inp.revenue_y1 * (1 + footfall_delta / 100.0),
    )
    if sust_rent_delta_pct:
        st.caption(
            f"Effective rent in DCF: CHF {effective_rent:.0f}/m²/yr  "
            f"(list {inp.rent_per_sqm_yr:.0f} × "
            f"sensitivity {sens_factor:.3f} × "
            f"sustainability {sust_factor:.3f})"
        )

    base_pf = build_proforma(stressed)
    site["proforma"] = base_pf

    # Headline metrics — DCF view
    st.markdown("##### Headline · DCF view")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("NPV", f"CHF {int(base_pf.npv):,}".replace(",", " "))
    m2.metric(
        "IRR", f"{base_pf.irr * 100:.1f}%" if base_pf.irr is not None else "n/a"
    )
    m3.metric(
        "Payback",
        f"{base_pf.payback_years:.1f} yr" if base_pf.payback_years else "> horizon",
    )
    m4.metric(
        "Break-even", f"≈ Month {base_pf.breakeven_month}"
        if base_pf.breakeven_month else "—",
    )

    # Leasehold KPIs — industry-standard retail
    st.markdown("##### Leasehold KPIs · retail-industry standard")
    k1, k2, k3, k4 = st.columns(4)
    # Use Year 1 for headline KPIs (most actionable)
    ocr_y1 = base_pf.occupancy_cost_ratio[1] if len(base_pf.occupancy_cost_ratio) > 1 else 0.0
    coc_y1 = base_pf.cash_on_cash[1] if len(base_pf.cash_on_cash) > 1 else 0.0
    be_y1 = base_pf.breakeven_sales[1] if len(base_pf.breakeven_sales) > 1 else 0.0

    ocr_health = "🟢" if ocr_y1 < 10 else ("🟡" if ocr_y1 < 15 else "🔴")
    k1.metric(
        "Occupancy Cost Ratio (Y1)",
        f"{ocr_y1:.1f}%",
        delta=ocr_health,
        delta_color="off",
        help=("(Rent + Insurance) / Revenue. Premium-retail benchmark: "
              "below 10% = healthy · 10-15% = stretched · above 15% = "
              "rent is too high."),
    )
    k2.metric(
        "Total rent over lease",
        f"CHF {int(base_pf.total_rent_paid):,}".replace(",", " "),
        help=(f"Cumulative rent across the {inp.horizon_years}-year "
              "horizon (after rent-free period + with escalation)."),
    )
    k3.metric(
        "Cash-on-Cash Return (Y1)",
        f"{coc_y1:.1f}%",
        help=("Year-1 FCF / Year-0 CAPEX. Premium-retail benchmark: "
              "above 20% indicates the fit-out spend pays back fast."),
    )
    k4.metric(
        "Break-even sales (Y1)",
        f"CHF {int(be_y1):,}".replace(",", " "),
        help=("Revenue level at which EBITDA exactly covers rent + "
              "insurance + other OPEX. Compare against your Y1 "
              "revenue assumption."),
    )

    if ocr_y1 >= 15:
        st.warning(
            f"⚠️ **Occupancy Cost Ratio {ocr_y1:.1f}% — above 15%**. "
            "Rent is consuming too much revenue for a premium retail "
            "P&L. Either rent must come down or revenue assumption "
            "needs to rise."
        )
    elif ocr_y1 >= 10:
        st.info(
            f"ℹ️ **Occupancy Cost Ratio {ocr_y1:.1f}% — in the 10-15% "
            "stretched band**. Sustainable but worth negotiating rent "
            "down at the next break-option."
        )

    # Cashflow table
    st.markdown("##### Cashflow")
    cf_rows = {
        "Year": [f"Y{y}" for y in base_pf.years],
        "Revenue": [round(v, 0) for v in base_pf.revenue],
        "EBITDA": [round(v, 0) for v in base_pf.ebitda],
        "Rent": [round(v, 0) for v in base_pf.rent],
        "Insurance": [round(v, 0) for v in base_pf.insurance],
        "Other OPEX": [round(v, 0) for v in base_pf.other_opex],
        "FCF": [round(v, 0) for v in base_pf.fcf],
        "Cumulative": [round(v, 0) for v in base_pf.cumulative],
    }
    cf_df = pd.DataFrame(cf_rows).set_index("Year").T
    cf_df = cf_df.applymap(lambda v: f"{int(v):,}".replace(",", " "))
    st.dataframe(cf_df, use_container_width=True)

    # Cashflow chart
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=base_pf.years, y=base_pf.fcf, name="FCF",
        marker_color=["#D32F2F" if v < 0 else "#00C853" for v in base_pf.fcf],
    ))
    fig.add_trace(go.Scatter(
        x=base_pf.years, y=base_pf.cumulative,
        name="Cumulative", mode="lines+markers",
        line=dict(color="#1A1A1A", width=2),
    ))
    fig.add_hline(y=0, line_dash="dot", line_color="#999")
    fig.update_layout(
        height=380, margin=dict(l=20, r=20, t=10, b=20),
        xaxis_title="Year", yaxis_title="CHF",
        legend=dict(orientation="h", y=1.05),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Scenarios
    st.markdown("##### Bear / Base / Bull")
    scenarios = build_scenarios(stressed)
    site["scenarios"] = scenarios

    sc_metrics = []
    for name, pf in scenarios.items():
        sc_metrics.append({
            "Scenario": name,
            "NPV (CHF)": int(pf.npv),
            "IRR": f"{pf.irr * 100:.1f}%" if pf.irr is not None else "n/a",
            "Payback (yr)": (round(pf.payback_years, 1)
                              if pf.payback_years else "—"),
            "Cumulative Y5 (CHF)": int(pf.cumulative[-1]),
        })
    sc_df = pd.DataFrame(sc_metrics)
    fmt_cols = ["NPV (CHF)", "Cumulative Y5 (CHF)"]
    for c in fmt_cols:
        sc_df[c] = sc_df[c].apply(lambda v: f"{int(v):,}".replace(",", " "))
    st.dataframe(sc_df, use_container_width=True, hide_index=True)

    sc_fig = go.Figure()
    colors = {"Bear": "#D32F2F", "Base": "#1A1A1A", "Bull": "#00C853"}
    for name, pf in scenarios.items():
        sc_fig.add_trace(go.Scatter(
            x=pf.years, y=pf.cumulative, name=name, mode="lines+markers",
            line=dict(color=colors[name], width=2),
        ))
    sc_fig.add_hline(y=0, line_dash="dot", line_color="#999")
    sc_fig.update_layout(
        height=360, margin=dict(l=20, r=20, t=10, b=20),
        xaxis_title="Year", yaxis_title="Cumulative cash (CHF)",
        legend=dict(orientation="h", y=1.05),
    )
    st.plotly_chart(sc_fig, use_container_width=True)

    site["step6_complete"] = True


# ===========================================================================
# STEP 7 — EXPORT
# ===========================================================================

def _step7_export(brand: dict, _weights: dict) -> None:
    site = st.session_state.site
    st.subheader("Step 7 — Export & save")
    st.caption("PDF Site Report (1-pager) · PDF Deal Memo (full) · "
                "PPT Product Overview · Pipeline add.")

    if not site.get("step5_complete"):
        st.warning("Complete Step 5 (Site Score) first to enable export.")
        return

    safe_name = (site.get("name") or "site").replace(" ", "_").replace("/", "-")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown("##### 📄 Site Report")
        st.caption("1-pager: score + Glass-Box chart + market + sustainability.")
        try:
            pdf_bytes = site_report_pdf(brand, site)
            st.download_button(
                "Download Site Report",
                data=pdf_bytes,
                file_name=f"SiteReport_{safe_name}_{timestamp}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"PDF generation failed: {type(e).__name__}: {e}")

    with c2:
        st.markdown("##### 📊 Deal Memo")
        st.caption("5-page deal memo with charts + static maps + top-POI tables.")
        if not site.get("step6_complete"):
            st.info("Run Step 6 (Pro-Forma) for the full memo.")
        else:
            try:
                pdf_bytes = deal_memo_pdf(brand, site)
                st.download_button(
                    "Download Deal Memo",
                    data=pdf_bytes,
                    file_name=f"DealMemo_{safe_name}_{timestamp}.pdf",
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"PDF generation failed: {type(e).__name__}: {e}")

    with c3:
        st.markdown("##### 📽 Product Overview")
        st.caption("6-slide PPT — cover, score, proximity, risk, pro-forma, methodology.")
        try:
            pptx_bytes = build_overview_deck(brand, site)
            st.download_button(
                "Download PPT Deck",
                data=pptx_bytes,
                file_name=f"ProductOverview_{safe_name}_{timestamp}.pptx",
                mime=("application/vnd.openxmlformats-officedocument."
                       "presentationml.presentation"),
                type="primary",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"PPT generation failed: {type(e).__name__}: {e}")

    with c4:
        st.markdown("##### ➕ Add to Pipeline")
        st.caption("Tracks this site through Screening → Live.")
        st.button(
            "Add to Pipeline (Sprint 3)",
            disabled=True,
            use_container_width=True,
            help="Pipeline persistence wires up in Sprint 3 (SQLite + Kanban).",
        )

    st.divider()
    st.markdown("##### Summary")
    fr: FloodRisk | None = site.get("flood_risk")
    result: ScoreResult | None = site.get("score_result")
    pf: ProForma | None = site.get("proforma")

    sust = site.get("sustainability_result")
    esi = site.get("score_esi", site.get("score", 0))
    summary_rows = [
        ("Site", site.get("name") or "—"),
        ("Address", site.get("address") or "—"),
        ("Coordinates", f"{site.get('lat', 0):.5f}, {site.get('lon', 0):.5f}"),
        ("Site Score (base, flood-adjusted)", f"{site.get('score', 0)}/100"),
    ]
    if sust:
        summary_rows.append((
            "Sustainability Δ (Bechtiger 2024)",
            f"{sust.score_delta:+d} pts · rent {sust.rent_delta_pct:+.1f}%",
        ))
        summary_rows.append(("Site Score (ESI-adjusted)", f"{esi} (base+Δ)"))
    summary_rows += [
        ("PEI", f"{result.pei:.1f}/10" if result else "—"),
        ("Flood risk", fr.label if fr else "—"),
    ]
    if pf:
        summary_rows.append(("Pro-Forma NPV",
                              f"CHF {int(pf.npv):,}".replace(",", " ")))
        if pf.irr is not None:
            summary_rows.append(("Pro-Forma IRR", f"{pf.irr * 100:.1f}%"))
        if pf.payback_years:
            summary_rows.append(("Payback", f"{pf.payback_years:.1f} yr"))
    st.dataframe(
        pd.DataFrame(summary_rows, columns=["Metric", "Value"]),
        use_container_width=True, hide_index=True,
    )
