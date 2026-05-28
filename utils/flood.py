"""BAFU flood-risk integration.

**Architecture** — the BAFU surface-runoff layer
(`ch.bafu.gefaehrdungskarte-oberflaechenabfluss`) is published as a
visual WMS raster only; the geo.admin.ch identify API returns "No
GeoTable" and the WMS GetFeatureInfo endpoint returns empty bodies
(confirmed on all four real On CH sites). There is no per-coordinate
programmatic query for this dataset.

So the runtime check uses two channels:

1. **Analyst-authoritative override table** — for the four real On CH
   locations an analyst already classified by hand.
   500 m match radius — wider than typical geocoding error for historic
   street addresses, narrow enough that the four sites stay distinct.

2. **WMS overlay** — added to every Folium map so the analyst sees the
   official hazard polygons visually. Layer source: `wms.geo.admin.ch`.

For unknown sites we return a "consult map" result with a deeplink to
map.geo.admin.ch — that's how the BAFU dataset is meant to be used.

Score impact applied POST-aggregation:
    HQ30 / "stark"     →  -20 pts  + 🔴 WARNING + insurance CHF 15'000/yr
    HQ100 / "mittel"   →  -10 pts  + 🟡 CAUTION + insurance CHF  8'000/yr
    HQ300 / "gering"   →   -5 pts  + ℹ️  INFO    + insurance CHF  3'000/yr
    none / unknown     →    0 pts  + 🟢 OK / ℹ️ review
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import streamlit as st


# ---------------------------------------------------------------------------
# Risk class table
# ---------------------------------------------------------------------------

@dataclass
class FloodRisk:
    risk: str          # "none" | "low" | "medium" | "high"
    hq_class: str      # "HQ30" | "HQ100" | "HQ300" | "none" | "unknown"
    label: str
    score_impact: int
    insurance_chf: int
    recommendation: str
    source: str        # "BAFU API" | "analyst override" | "fallback"
    raw: dict[str, Any] | None = None


_NONE = FloodRisk(
    risk="none", hq_class="none",
    label="🟢 Outside flood-hazard zone",
    score_impact=0, insurance_chf=0,
    recommendation="No specific flood-protection action required.",
    source="BAFU API",
)
_LOW = FloodRisk(
    risk="low", hq_class="HQ300",
    label="ℹ️ HQ300 zone (300-yr return period) — LOW",
    score_impact=-5, insurance_chf=3000,
    recommendation=("Standard elementary-damage insurance sufficient. "
                     "Document inventory and check basement-level pumps."),
    source="BAFU API",
)
_MEDIUM = FloodRisk(
    risk="medium", hq_class="HQ100",
    label="🟡 HQ100 zone (100-yr return period) — MEDIUM",
    score_impact=-10, insurance_chf=8000,
    recommendation=("Verify Rückstauklappen / pump systems. Business-"
                     "interruption insurance recommended. Avoid high-value "
                     "inventory in basement / Tiefparterre."),
    source="BAFU API",
)
_HIGH = FloodRisk(
    risk="high", hq_class="HQ30",
    label="🔴 HQ30 zone (30-yr return period) — VERY HIGH",
    score_impact=-20, insurance_chf=15000,
    recommendation=("Mobile flood barriers + business-interruption insurance "
                     "mandatory. Hochwasserschutzplan and evacuation procedure "
                     "before opening. Reconsider basement use entirely."),
    source="BAFU API",
)


# ---------------------------------------------------------------------------
# Analyst-authoritative overrides for known On CH locations
# Source: analyst-curated flood-risk overrides for the known sites
# ---------------------------------------------------------------------------

_KNOWN_SITES: list[tuple[float, float, FloodRisk]] = [
    # On Labs HQ + Flagship — Hardturmstrasse 183, 8005 Zürich
    (47.3934, 8.5044, FloodRisk(
        risk="low", hq_class="HQ300",
        label="🟡 Medium-Low (Hardturm — Limmat 400 m, surface runoff)",
        score_impact=-5, insurance_chf=3000,
        recommendation=("Modern building (2022) — likely Rückstauklappen in "
                         "place. Check basement pumps. No direct HQ risk."),
        source="analyst override (ANALYSE file)",
    )),
    # Flagship Limmatquai 28, 8001 Zürich
    (47.3731, 8.5430, FloodRisk(
        risk="high", hq_class="HQ100",
        label="🔴 HIGH (Limmatquai 28 — < 20 m from Limmat)",
        score_impact=-15, insurance_chf=12000,
        recommendation=("CRITICAL: Mobile flood barriers + business-interruption "
                         "insurance mandatory. Historic Münsterburg building, no "
                         "modern foundation protection. Combined Sihl+Limmat "
                         "risk (2005 reference scenario). Estimated EG flood "
                         "depth at HQ100: 20-60 cm."),
        source="analyst override (ANALYSE file)",
    )),
    # On HQ Office — Förrlibuckstrasse 190, 8005 Zürich
    (47.3893, 8.5102, FloodRisk(
        risk="none", hq_class="none",
        label="🟢 LOW (Förrlibuck — no direct watercourse)",
        score_impact=0, insurance_chf=0,
        recommendation=("Industrial quarter on elevated terrain. No acute "
                         "flood risk. Standard insurance + IT servers on "
                         "upper floors only."),
        source="analyst override (ANALYSE file)",
    )),
    # We Run Sihlcity — Kalanderplatz 1, 8045 Zürich
    (47.3626, 8.5241, FloodRisk(
        risk="high", hq_class="HQ30",
        label="🔴 VERY HIGH (Sihlcity — < 100 m from Sihl)",
        score_impact=-20, insurance_chf=15000,
        recommendation=("HIGHEST PRIORITY: Sihlcity built on historic Sihl "
                         "flood-plain (former Sihlpapier site). Tiefgarage "
                         "total-loss risk under HQ100 Sihl. Maximum insurance + "
                         "evacuation plan + lease force-majeure clause review."),
        source="analyst override (ANALYSE file)",
    )),
]

# Match radius for an override. Nominatim sometimes lands on a back-of-
# building centroid 100-400 m from the analyst-recorded street-frontage
# coordinate (Limmatquai 28 is a 370 m offset). 500 m comfortably covers
# this without colliding across the four On CH sites (closest pair is
# Hardturm ↔ Förrlibuck at ~700 m).
_OVERRIDE_RADIUS_M = 500.0


def _override_lookup(lat: float, lon: float) -> FloodRisk | None:
    # Cheap haversine — pulled in locally to avoid a circular import
    r = 6_371_000.0
    for klat, klon, risk in _KNOWN_SITES:
        p1, p2 = math.radians(lat), math.radians(klat)
        dphi = math.radians(klat - lat)
        dlam = math.radians(klon - lon)
        a = (math.sin(dphi / 2) ** 2
             + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2)
        d = 2 * r * math.asin(math.sqrt(a))
        if d <= _OVERRIDE_RADIUS_M:
            return risk
    return None


# ---------------------------------------------------------------------------
# BAFU layer
# ---------------------------------------------------------------------------

BAFU_LAYER = "ch.bafu.gefaehrdungskarte-oberflaechenabfluss"


def geo_admin_deeplink(lat: float, lon: float) -> str:
    """Direct link to map.geo.admin.ch with the BAFU layer + candidate marker."""
    return (
        f"https://map.geo.admin.ch/?lang=en&topic=ech&bgLayer=ch.swisstopo.pixelkarte-farbe"
        f"&layers={BAFU_LAYER}&E={lon}&N={lat}&zoom=10"
        f"&crosshair=marker&swisssearch={lat},{lon}"
    )


@st.cache_data(ttl=86400, show_spinner=False)
def check_flood_risk(lat: float, lon: float) -> FloodRisk:
    """Return a FloodRisk for the given coordinate.

    Override table first; otherwise return a 'review-required' result with
    a deeplink to map.geo.admin.ch. The WMS overlay on the Folium map is
    the primary analyst-facing source for sites without an override.
    """
    override = _override_lookup(lat, lon)
    if override is not None:
        return override

    return FloodRisk(
        risk="none", hq_class="unknown",
        label="ℹ️ Outside override dataset — see WMS overlay",
        score_impact=0, insurance_chf=0,
        recommendation=(
            "The BAFU surface-runoff layer is published as a raster only "
            "(no per-point API). Use the 🌊 overlay on the map to inspect "
            f"the hazard zone visually, or open map.geo.admin.ch in a new "
            f"tab for the full official map."
        ),
        source="WMS-only (no programmatic check available)",
    )


# ---------------------------------------------------------------------------
# Folium WMS layer (convenience wrapper — also wired in utils.geo)
# ---------------------------------------------------------------------------

def add_bafu_wms_layer(folium_map, show: bool = True) -> None:
    """Attach the BAFU surface-runoff hazard layer to a Folium map."""
    import folium
    folium.WmsTileLayer(
        url="https://wms.geo.admin.ch/",
        layers=BAFU_LAYER,
        fmt="image/png",
        transparent=True,
        name="🌊 BAFU flood hazard",
        overlay=True,
        opacity=0.55,
        show=show,
    ).add_to(folium_map)
