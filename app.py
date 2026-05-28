"""Retail Site Intelligence — Streamlit entry point.

A locally-run analyst tool that walks through the daily workflow of a
retail real estate specialist at a premium sportswear brand (On).

Run:
    streamlit run app.py

Three top-level tabs follow user stories, not features:
    🔍 New Site   — 7-step funnel from address to deal memo
    📋 Pipeline   — Kanban board of in-flight sites
    🏢 Portfolio  — Lease calendar and alerts for active stores
"""
from __future__ import annotations

import streamlit as st

from modules import pipeline, portfolio, site_analysis
from utils.config import load_brand, load_weights

# ---------------------------------------------------------------------------
# Page config + brand styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Retail Site Intelligence",
    page_icon="🏃",
    layout="wide",
    initial_sidebar_state="expanded",
)

BRAND = load_brand()
ACCENT = BRAND["brand_colors"]["accent"]
PRIMARY = BRAND["brand_colors"]["primary"]

st.markdown(
    f"""
    <style>
        /* ---- Force main content area to 80% viewport width ---- */
        .main > div > .block-container,
        section.main > div > .block-container,
        [data-testid="stMain"] > div > .block-container,
        section[data-testid="stMain"] > div > .block-container {{
            max-width: 80vw !important;
            padding-left: 1rem !important;
            padding-right: 1rem !important;
        }}
        /* Folium iframe uses the explicit width/height passed to st_folium */
        .block-container {{ padding-top: 1.5rem; padding-bottom: 2rem; }}
        body, p, span, div, label {{ color: {PRIMARY}; }}
        h1, h2, h3, h4, h5 {{ color: {PRIMARY}; }}
        small, .caption {{ color: #333333; }}
        div[data-testid="stMetric"] {{
            background: #FFFFFF;
            padding: 12px 16px;
            border-radius: 8px;
            border-left: 4px solid {ACCENT};
            box-shadow: 0 1px 3px rgba(0,0,0,0.10);
        }}
        div[data-testid="stMetricLabel"] {{
            color: #333333 !important;
            font-size: 0.85rem;
            font-weight: 600;
        }}
        div[data-testid="stMetricValue"] {{ color: {PRIMARY} !important; }}
        div[data-testid="stMetricDelta"] {{ color: #2E7D32 !important; }}
        .stTabs [data-baseweb="tab"] {{
            font-weight: 600;
            font-size: 0.95rem;
            color: {PRIMARY};
        }}
        .stTabs [aria-selected="true"] {{
            border-bottom: 3px solid {ACCENT} !important;
            color: {PRIMARY} !important;
        }}
        section[data-testid="stSidebar"] {{ background: #F4F4F4; }}
        section[data-testid="stSidebar"] * {{ color: {PRIMARY}; }}
        /* Streamlit captions — bump contrast site-wide */
        div[data-testid="stCaptionContainer"], .stCaption {{ color: #333333 !important; }}
        small {{ color: #333333 !important; }}
        /* Step-nav buttons */
        .stepnav-button button {{
            font-size: 0.82rem !important;
            padding: 6px 4px !important;
        }}
        /* Hide Leaflet attribution strip — visual cleanliness for the demo */
        .leaflet-control-attribution {{ display: none !important; }}
        /* Make folium LayerControl more readable and prominent */
        .leaflet-control-layers {{
            font-size: 0.85rem !important;
            background: #FFFFFF !important;
            border: 1px solid #1A1A1A !important;
            box-shadow: 0 2px 6px rgba(0,0,0,0.18) !important;
        }}
        .leaflet-control-layers-expanded {{ padding: 8px 12px !important; }}
        .leaflet-control-layers label {{
            color: #1A1A1A !important;
            font-weight: 500 !important;
            margin: 3px 0 !important;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

col_title, col_tag = st.columns([5, 2])
with col_title:
    st.markdown(
        f"## Retail Site Intelligence · "
        f"<span style='color:{ACCENT}'>{BRAND['brand']}</span>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Site selection · pipeline tracking · portfolio management — "
        "for a premium sport-lifestyle retailer."
    )
with col_tag:
    if BRAND.get("premium_tier"):
        st.markdown(
            f"<div style='text-align:right;padding-top:14px;'>"
            f"<span style='background:{PRIMARY};color:#FFF;"
            f"padding:4px 10px;border-radius:12px;font-size:0.75rem;"
            f"font-weight:600;letter-spacing:0.05em;'>"
            f"PREMIUM TIER · NO OUTLETS</span></div>",
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "site" not in st.session_state:
    st.session_state.site = {"step": 1}
if "weights" not in st.session_state:
    st.session_state.weights = load_weights()

# ---------------------------------------------------------------------------
# Sidebar — score weights (Glass Box)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### ⚙️ Score weights")
    st.caption(
        "Weighted dimensions for the Site Score (Step 5). "
        "The tool is brand-neutral — these and `on_brand.json` drive behaviour."
    )

    new_weights: dict[str, float] = {}
    for key, val in st.session_state.weights.items():
        new_weights[key] = st.slider(
            key.replace("_", " ").title(),
            min_value=0.0,
            max_value=0.5,
            value=float(val),
            step=0.01,
            key=f"w_{key}",
        )
    total = sum(new_weights.values())
    if abs(total - 1.0) > 0.005:
        st.warning(f"Weights sum to {total:.0%} — must equal 100%.")
    else:
        st.session_state.weights = new_weights
        st.success(f"Sum: {total:.0%}")

    st.divider()
    # Strava heatmap setup was removed — Strava migrated to cookie-only
    # auth that's brittle to wire from a third-party app, and the OSM
    # running-infrastructure layer (parks + routes + cycleways) covers
    # the analyst story without external dependencies. `utils/strava.py`
    # is kept for future use; instructions remain in `.env.example`.

    st.markdown("##### Brand config")
    st.caption(
        f"**Brand:** {BRAND['brand']}  \n"
        f"**Direct competitors:** {len(BRAND.get('direct_competitors', []))}  \n"
        f"**Existing stores:** {len(BRAND.get('existing_stores', []))}  \n"
        f"**No outlets:** {'Yes' if BRAND.get('no_outlets') else 'No'}"
    )
    with st.expander("Edit `data/config/on_brand.json` to swap brands"):
        st.caption(
            "Brand config drives competitor logic, complementary adjacencies, "
            "luxury proxies, and the cannibalization check. Swap the file "
            "and the same scoring engine runs for any retailer."
        )

# ---------------------------------------------------------------------------
# Top-level tabs
# ---------------------------------------------------------------------------

tab_new, tab_pipeline, tab_portfolio = st.tabs([
    "🔍  New Site",
    "📋  Pipeline",
    "🏢  Portfolio",
])

with tab_new:
    site_analysis.render(BRAND, st.session_state.weights)
with tab_pipeline:
    pipeline.render(BRAND)
with tab_portfolio:
    portfolio.render(BRAND)
