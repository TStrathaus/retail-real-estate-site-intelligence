"""Matplotlib charts as PNG bytes — for embedding in ReportLab PDFs.

Streamlit uses Plotly for the live UI; PDFs use matplotlib because
exporting Plotly to PNG requires `kaleido` (headless Chrome bundle, 100+ MB
on Windows). matplotlib renders crisply via the Agg backend with zero
extra dependencies.

Every chart returns raw PNG bytes — wrap in `Image(BytesIO(bytes), ...)`
for ReportLab.
"""
from __future__ import annotations

import io
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ACCENT = "#00C853"
RED = "#D32F2F"
AMBER = "#F9A825"
PRIMARY = "#1A1A1A"
GREY = "#999999"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 8,
    "axes.titlesize": 9,
    "axes.titleweight": "bold",
    "axes.titlepad": 10,
    "axes.edgecolor": "#CCCCCC",
    "axes.labelcolor": PRIMARY,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": PRIMARY,
    "ytick.color": PRIMARY,
    "xtick.major.size": 0,
    "ytick.major.size": 0,
    "figure.facecolor": "white",
})


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight",
                 facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _swiss_fmt(x: float) -> str:
    """5'000'000 instead of 5,000,000 — matches CH analyst convention."""
    return f"{int(x):,}".replace(",", "'")


# ---------------------------------------------------------------------------
# Glass Box — score breakdown
# ---------------------------------------------------------------------------

def chart_score_breakdown(score_result, flood_penalty: int = 0,
                            sust_delta: int = 0) -> bytes:
    dims = score_result.dimensions
    labels = [k.replace("_", " ").title() for k in dims]
    contribs = [d["contribution"] for d in dims.values()]
    if flood_penalty:
        labels.append("Flood penalty")
        contribs.append(float(flood_penalty))
    if sust_delta:
        labels.append("Sustainability Δ")
        contribs.append(float(sust_delta))

    order = sorted(range(len(contribs)), key=lambda i: contribs[i])
    labels = [labels[i] for i in order]
    contribs = [contribs[i] for i in order]
    colors = [ACCENT if c >= 0 else RED for c in contribs]

    fig, ax = plt.subplots(figsize=(6.5, max(2.5, 0.42 * len(labels) + 0.8)))
    ax.barh(labels, contribs, color=colors, edgecolor="white", linewidth=0.5)
    ax.axvline(0, color=GREY, linewidth=0.6)

    x_min = min(0, min(contribs) * 1.4)
    x_max = max(contribs) * 1.4 if max(contribs) > 0 else 5
    ax.set_xlim(x_min, x_max)

    for i, c in enumerate(contribs):
        offset = (x_max - x_min) * 0.012
        ax.text(c + (offset if c >= 0 else -offset), i, f"{c:+.1f}",
                va="center", ha="left" if c >= 0 else "right",
                fontsize=7, color=PRIMARY)
    ax.set_xlabel("Contribution to Site Score (pts)")
    ax.set_title("Score breakdown · Glass Box")
    return _png(fig)


# ---------------------------------------------------------------------------
# Walk Score breakdown
# ---------------------------------------------------------------------------

def chart_walk_score(footfall: dict) -> bytes:
    cat_scores = footfall.get("category_scores", {})
    cat_counts = footfall.get("category_counts", {})
    if not cat_scores:
        # Empty placeholder
        fig, ax = plt.subplots(figsize=(5, 2))
        ax.text(0.5, 0.5, "Walk Score data unavailable",
                 ha="center", va="center", color=GREY, transform=ax.transAxes)
        ax.axis("off")
        return _png(fig)

    cats = list(cat_scores.keys())
    scores = [cat_scores[c] for c in cats]
    counts = [cat_counts.get(c, 0) for c in cats]
    order = sorted(range(len(cats)), key=lambda i: scores[i])
    cats = [cats[i].title() for i in order]
    scores = [scores[i] for i in order]
    counts = [counts[i] for i in order]

    fig, ax = plt.subplots(figsize=(6, 2.6))
    ax.barh(cats, scores, color=ACCENT, edgecolor="white", linewidth=0.5)
    for i, (s, c) in enumerate(zip(scores, counts)):
        ax.text(s + 1.5, i, f"{int(s)} · {c} POIs",
                va="center", ha="left", fontsize=7, color=PRIMARY)
    ax.set_xlim(0, 118)
    ax.set_xlabel("Sub-score (0–100, log-saturated)")
    ax.set_title(f"Walk Score breakdown · total {footfall.get('walk_score', 0)}/100")
    return _png(fig)


# ---------------------------------------------------------------------------
# Cashflow
# ---------------------------------------------------------------------------

def chart_cashflow(pf) -> bytes:
    years = pf.years
    fig, ax = plt.subplots(figsize=(6.5, 2.8))
    fcf_colors = [RED if v < 0 else ACCENT for v in pf.fcf]
    ax.bar(years, pf.fcf, color=fcf_colors, edgecolor="white", linewidth=0.5,
            label="FCF", zorder=2)
    ax2 = ax.twinx()
    ax2.plot(years, pf.cumulative, color=PRIMARY, linewidth=2,
              marker="o", markersize=5, label="Cumulative", zorder=3)
    ax.axhline(0, color=GREY, linewidth=0.6, linestyle="--")
    ax.set_xlabel("Year")
    ax.set_ylabel("FCF (CHF)")
    ax2.set_ylabel("Cumulative (CHF)")
    ax2.spines["top"].set_visible(False)
    ax.set_xticks(years)
    ax.set_title("5-yr cashflow · Base scenario")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: _swiss_fmt(x)))
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: _swiss_fmt(x)))

    # Annotate break-even if present
    if pf.breakeven_month and pf.payback_years:
        ax2.annotate(
            f"Break-even ≈ M{pf.breakeven_month}",
            xy=(pf.payback_years, 0),
            xytext=(pf.payback_years + 0.2, max(pf.cumulative) * 0.5),
            fontsize=7, color=PRIMARY,
            arrowprops=dict(arrowstyle="->", color=GREY, lw=0.6),
        )

    return _png(fig)


# ---------------------------------------------------------------------------
# Bear / Base / Bull
# ---------------------------------------------------------------------------

def chart_scenarios(scenarios: dict[str, Any]) -> bytes:
    fig, ax = plt.subplots(figsize=(6.5, 2.8))
    colors = {"Bear": RED, "Base": PRIMARY, "Bull": ACCENT}
    for name, pf in scenarios.items():
        ax.plot(pf.years, pf.cumulative, color=colors.get(name, GREY),
                 linewidth=2, marker="o", markersize=4, label=name)
    ax.axhline(0, color=GREY, linewidth=0.6, linestyle="--")
    ax.set_xlabel("Year")
    ax.set_ylabel("Cumulative cash (CHF)")
    ax.legend(loc="upper left", frameon=False)
    ax.set_title("Scenario comparison · Bear / Base / Bull")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: _swiss_fmt(x)))
    return _png(fig)


# ---------------------------------------------------------------------------
# Proximity — POI count per category
# ---------------------------------------------------------------------------

CATEGORY_COLOR = {
    "sport":      "#1565C0",
    "symbiose":   "#00C853",
    "premium":    "#C9A227",
    "competitor": "#D32F2F",
    "negative":   "#F57C00",
    "partner":    "#616161",
}


def chart_static_map(pois: list, lat: float, lon: float,
                       radius_m: int, *, title: str | None = None) -> bytes:
    """Static context map for PDF/PPT.

    Backdrop preference order:
      1. **contextily OSM tile basemap** (CartoDB Positron) — proper map
         with streets, building footprints, labels. Fetches Web-Mercator
         tiles at the right zoom level for the search radius.
      2. **OSMnx walking-network** — fallback if contextily fails (no
         network access, etc.).
    Overlay = POI scatter coloured by category + radius circle +
    candidate marker.
    """
    import math

    import matplotlib.patches as mpatches
    import numpy as np

    fig, ax = plt.subplots(figsize=(6.4, 6.0))

    use_contextily = False
    try:
        import contextily as ctx
        from pyproj import Transformer
        to_merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        use_contextily = True
    except Exception:
        to_merc = None

    if use_contextily:
        # Mercator-projected plot with real OSM tile basemap
        cx, cy = to_merc.transform(lon, lat)

        # Radius circle — project 64 vertices of a degree-space circle
        metres_per_deg_lat = 111_320.0
        metres_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))
        r_lat = radius_m / metres_per_deg_lat
        r_lon = radius_m / metres_per_deg_lon
        theta = np.linspace(0, 2 * math.pi, 96)
        c_lon = lon + r_lon * np.cos(theta)
        c_lat = lat + r_lat * np.sin(theta)
        c_x, c_y = to_merc.transform(c_lon, c_lat)
        ax.fill(c_x, c_y, color=ACCENT, alpha=0.06, zorder=2)
        ax.plot(c_x, c_y, color=ACCENT, linewidth=1.6,
                 linestyle="--", zorder=3)

        # POIs in mercator
        for p in pois:
            col = CATEGORY_COLOR.get(p.category, GREY)
            size = 30 + min(abs(p.base_score) * 4, 100)
            px, py = to_merc.transform(p.lon, p.lat)
            ax.scatter(px, py, s=size, c=col, alpha=0.85,
                        edgecolors="white", linewidth=0.5, zorder=4)

        # Candidate marker
        ax.scatter([cx], [cy], s=200, c=ACCENT, edgecolors="white",
                    linewidth=2.5, zorder=5)
        ax.scatter([cx], [cy], s=45, c=PRIMARY, zorder=6)

        # Fixed bounds = 1.25× radius in metres (mercator metres ≈ ground
        # metres for small areas at mid-latitudes)
        bound = radius_m * 1.25
        ax.set_xlim(cx - bound, cx + bound)
        ax.set_ylim(cy - bound, cy + bound)
        ax.set_aspect("equal")

        # Add the OSM tile basemap. Auto-zoom; CartoDB Positron is light
        # and analyst-readable.
        try:
            ctx.add_basemap(
                ax, source=ctx.providers.CartoDB.Positron,
                crs="EPSG:3857", attribution_size=5,
            )
        except Exception:
            ax.set_facecolor("#FAFAFA")
    else:
        # Fallback — OSMnx network in lat/lon
        try:
            import osmnx as ox
            from utils.config import CACHE_DIR
            ox.settings.use_cache = True
            ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
            G = ox.graph_from_point((lat, lon), dist=radius_m, network_type="walk")
            ox.plot.plot_graph(
                G, ax=ax, show=False, close=False,
                bgcolor="#FFFFFF", node_size=0,
                edge_color="#D6D6D6", edge_linewidth=0.6,
            )
        except Exception:
            ax.set_facecolor("#FAFAFA")

        metres_per_deg_lat = 111_320.0
        metres_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))
        r_lat = radius_m / metres_per_deg_lat
        r_lon = radius_m / metres_per_deg_lon
        theta = np.linspace(0, 2 * math.pi, 64)
        ax.plot(lon + r_lon * np.cos(theta), lat + r_lat * np.sin(theta),
                 color=ACCENT, linewidth=1.5, linestyle="--", zorder=3)
        for p in pois:
            col = CATEGORY_COLOR.get(p.category, GREY)
            size = 30 + min(abs(p.base_score) * 4, 100)
            ax.scatter(p.lon, p.lat, s=size, c=col, alpha=0.85,
                        edgecolors="white", linewidth=0.5, zorder=4)
        ax.scatter([lon], [lat], s=180, c=ACCENT, edgecolors="white",
                    linewidth=2.0, zorder=5)
        ax.scatter([lon], [lat], s=40, c=PRIMARY, zorder=6)

    # Legend
    legend_items = [
        ("Sport", "#1565C0"),
        ("Symbiose", ACCENT),
        ("Premium", "#C9A227"),
        ("Competitor", RED),
        ("Negative env.", "#F57C00"),
        ("Partner", "#616161"),
    ]
    handles = [mpatches.Patch(color=c, label=l) for l, c in legend_items]
    ax.legend(
        handles=handles, loc="upper right", fontsize=6.5,
        frameon=True, framealpha=0.92, edgecolor="#CCCCCC",
        handlelength=1.2, ncols=2, columnspacing=0.6, labelspacing=0.3,
    )

    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#DDDDDD")
    ax.set_title(
        title or f"Site context · {radius_m} m radius · {len(pois)} POIs"
    )
    fig.tight_layout()
    return _png(fig)


def chart_poi_categories(pois: list, radius_m: int) -> bytes:
    summary: dict[str, dict] = {c: {"count": 0, "score": 0.0}
                                  for c in CATEGORY_COLOR}
    for p in pois:
        if p.category in summary:
            summary[p.category]["count"] += 1
            summary[p.category]["score"] += p.score

    cats = list(summary.keys())
    counts = [summary[c]["count"] for c in cats]
    scores = [summary[c]["score"] for c in cats]
    colors = [CATEGORY_COLOR[c] for c in cats]
    labels = [c.title() for c in cats]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7, 2.4),
                                      gridspec_kw={"width_ratios": [1, 1]})

    ax1.bar(labels, counts, color=colors, edgecolor="white", linewidth=0.5)
    for i, c in enumerate(counts):
        ax1.text(i, c + max(counts) * 0.02 if counts else 1,
                  str(c), ha="center", fontsize=7)
    ax1.set_title(f"POI count by category · {radius_m} m radius")
    ax1.tick_params(axis="x", rotation=30)

    score_colors = [ACCENT if v >= 0 else RED for v in scores]
    ax2.bar(labels, scores, color=score_colors, edgecolor="white", linewidth=0.5)
    ax2.axhline(0, color=GREY, linewidth=0.6)
    ax2.set_title("Distance-weighted score sum")
    ax2.tick_params(axis="x", rotation=30)
    for i, c in enumerate(scores):
        offset = max(abs(min(scores)), abs(max(scores))) * 0.03 or 5
        ax2.text(i, c + (offset if c >= 0 else -offset),
                  f"{c:+.0f}", ha="center", fontsize=7)

    return _png(fig)
