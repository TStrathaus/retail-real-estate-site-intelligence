"""PDF export — Site Report (1-pager) and Deal Memo (multi-page).

Built on ReportLab. Charts are rendered via matplotlib (`utils.charts`)
to PNG bytes and embedded as `Image` flowables — no headless browser
needed. Both PDFs share a header band with the brand accent colour;
tables use a quiet zebra-stripe style.

Site Report:  1-page executive summary (score · market · sustainability)
Deal Memo:    4-5 pages with all sub-analyses (proximity tables, walk-
              score chart, full cashflow + scenarios, flood detail).
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle, KeepTogether,
)

from utils import charts


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _styles(brand: dict) -> dict[str, Any]:
    primary = colors.HexColor(brand["brand_colors"]["primary"])
    accent = colors.HexColor(brand["brand_colors"]["accent"])
    base = getSampleStyleSheet()
    return {
        "primary": primary,
        "accent": accent,
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontSize=18, leading=22,
            textColor=primary, spaceAfter=4, alignment=0,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontSize=10, leading=13,
            textColor=colors.HexColor("#666666"), spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontSize=11.5, leading=14,
            textColor=primary, spaceBefore=10, spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"], fontSize=10, leading=12,
            textColor=primary, spaceBefore=6, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontSize=8.5, leading=11.5,
            textColor=colors.HexColor("#1A1A1A"),
        ),
        "caption": ParagraphStyle(
            "caption", parent=base["Normal"], fontSize=7.5, leading=9,
            textColor=colors.HexColor("#777777"),
        ),
    }


def _zebra_table(data: list[list[Any]], col_widths: list[float],
                  primary: colors.Color, *, small: bool = False) -> Table:
    t = Table(data, colWidths=col_widths, repeatRows=1)
    fs = 7.5 if small else 8.5
    style = TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), primary),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), fs),
        ("BOTTOMPADDING",(0, 0), (-1, 0), 5),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 1), (-1, -1), 3),
        ("ALIGN",        (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN",        (0, 0), (0, -1), "LEFT"),
        ("GRID",         (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
    ])
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.add("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F5F5F5"))
    t.setStyle(style)
    return t


def _png_image(png_bytes: bytes, width_mm: float) -> Image:
    """ReportLab Image from PNG bytes, scaled to the given width with auto height."""
    from PIL import Image as PILImage
    pil = PILImage.open(BytesIO(png_bytes))
    w_px, h_px = pil.size
    aspect = h_px / w_px
    width = width_mm * mm
    height = width * aspect
    return Image(BytesIO(png_bytes), width=width, height=height)


# ---------------------------------------------------------------------------
# Header / score band
# ---------------------------------------------------------------------------

def _header(brand: dict, site: dict, s: dict, doc_title: str) -> list:
    flow = []
    flow.append(Paragraph(
        f"<b>{brand['brand']}</b>  ·  Retail Site Intelligence  ·  {doc_title}",
        s["subtitle"],
    ))
    flow.append(Paragraph(
        site.get("name") or site.get("address", "Site").split(",")[0],
        s["title"],
    ))
    flow.append(Paragraph(
        f"{site.get('address', '')}  ·  "
        f"({site.get('lat', 0):.5f}, {site.get('lon', 0):.5f})  ·  "
        f"radius {site.get('radius_m', 0)} m",
        s["caption"],
    ))
    flow.append(Spacer(1, 4 * mm))
    return flow


def _score_band(brand: dict, site: dict, s: dict) -> list:
    score = site.get("score") or 0
    raw = site.get("score_raw") or score
    esi = site.get("score_esi") or score
    result = site.get("score_result")
    verdict = ("🟢 Go" if esi >= 75
                else "🟡 Vertiefte Prüfung" if esi >= 50
                else "🔴 No-Go")
    pei = result.pei if result else 0
    flood = site.get("flood_risk")
    flood_penalty = flood.score_impact if flood else 0
    sust = site.get("sustainability_result")
    sust_delta = sust.score_delta if sust else 0

    rows = [
        ["Raw", "Flood Δ", "Base (flood-adj.)", "Sust. Δ",
         "ESI-adjusted", "Verdict", "PEI"],
        [
            f"{raw}", f"{flood_penalty:+d}", f"{int(score)}",
            f"{sust_delta:+d}", f"{esi}", verdict, f"{pei:.1f}",
        ],
    ]
    col_widths = [14 * mm, 18 * mm, 30 * mm, 18 * mm, 26 * mm, 40 * mm, 14 * mm]
    t = _zebra_table(rows, col_widths, s["primary"])
    flow = [t, Spacer(1, 2 * mm)]
    if flood:
        flow.append(Paragraph(
            f"<b>Flood-risk:</b> {flood.label} · "
            f"insurance estimate CHF {flood.insurance_chf:,}/yr"
            .replace(",", " "),
            s["body"],
        ))
    flow.append(Spacer(1, 3 * mm))
    return flow


# ---------------------------------------------------------------------------
# Sub-tables
# ---------------------------------------------------------------------------

def _dimensions_table(site: dict, s: dict) -> list:
    result = site.get("score_result")
    if not result:
        return []
    rows = [["Dimension", "Score (0-100)", "Weight", "Contribution"]]
    for key, d in result.dimensions.items():
        rows.append([
            key.replace("_", " ").title(),
            f"{d['norm']:.1f}",
            f"{d['weight']:.0%}",
            f"{d['contribution']:+.1f}",
        ])
    return [
        Paragraph("Score breakdown · per dimension", s["h2"]),
        _zebra_table(rows, [60 * mm, 35 * mm, 25 * mm, 35 * mm], s["primary"]),
    ]


def _market_table(brand: dict, site: dict, s: dict) -> list:
    mun = (site.get("market") or {}).get("municipality")
    if not mun:
        return []
    market = site.get("market") or {}
    rows = [
        ["Population", f"{int(mun['population']):,}".replace(",", " ")],
        ["5-yr growth", f"{mun['growth_5y_pct']:+.1f}%"],
        ["Kaufkraft index", f"{int(mun['kaufkraft_index'])}"],
        ["Age 18–45 share", f"{mun['age_18_45_pct']:.1f}%"],
        ["Foreign share", f"{mun['foreign_share_pct']:.1f}%"],
        ["Hotel beds", f"{int(mun['hotel_beds']):,}".replace(",", " ")],
        ["Hotels in radius", str(market.get("hotels_total", 0))],
        ["5★ hotels in radius", str(market.get("hotels_5star", 0))],
        ["4★ hotels in radius", str(market.get("hotels_4star", 0))],
    ]
    rows_with_header = [["Market signal", "Value"]] + rows
    return [
        Paragraph(f"Market · {mun['city']} ({mun['canton']})", s["h2"]),
        _zebra_table(rows_with_header, [60 * mm, 35 * mm], s["primary"]),
    ]


def _sustainability_table(site: dict, s: dict) -> list:
    sust = site.get("sustainability_result")
    if not sust:
        return []
    rows = [["Factor", "Setting", "Score Δ", "Rent Δ", "Evidence"]]
    for f in sust.factors:
        rows.append([
            f.name, f.label, f"{f.score_delta:+d}",
            f"{f.rent_delta_pct:+.1f}%" if f.rent_delta_pct else "—",
            f.evidence,
        ])
    rows.append([
        "Total", "", f"{sust.score_delta:+d}",
        f"{sust.rent_delta_pct:+.1f}%", "",
    ])
    return [
        Paragraph("Sustainability · Bechtiger 2024 / ESI", s["h2"]),
        _zebra_table(
            rows, [28 * mm, 65 * mm, 18 * mm, 18 * mm, 35 * mm], s["primary"],
            small=True,
        ),
        Spacer(1, 1.5 * mm),
        Paragraph(
            "Methodology: ESI (CCRS/UZH) + Bechtiger 2024 (n=288 Swiss income "
            "properties, MAS Real Estate UZH). Accessibility p&lt;.001, "
            "Daylight p=.021. ÖV-class is a positive location indicator but "
            "not statistically significant for rent (Bechtiger 2024).",
            s["caption"],
        ),
    ]


def _proximity_summary_table(site: dict, s: dict) -> list:
    pois = site.get("pois") or []
    if not pois:
        return []
    summary: dict[str, dict] = {}
    for p in pois:
        d = summary.setdefault(p.category, {"count": 0, "score_sum": 0.0})
        d["count"] += 1
        d["score_sum"] += p.score
    rows = [["Category", "POIs", "Distance-weighted sum"]]
    for cat in ["sport", "symbiose", "premium", "competitor",
                 "negative", "partner"]:
        d = summary.get(cat, {"count": 0, "score_sum": 0})
        rows.append([
            cat.replace("_", " ").title(),
            str(d["count"]), f"{d['score_sum']:+.1f}",
        ])
    return [
        Paragraph(
            f"Proximity intelligence · radius {site.get('radius_m', 0)} m",
            s["h2"],
        ),
        _zebra_table(rows, [55 * mm, 30 * mm, 50 * mm], s["primary"]),
    ]


def _proximity_top_pois(site: dict, s: dict, limit: int = 6) -> list:
    """Top N POIs per category sorted by score magnitude — the substance
    behind the summary table."""
    pois = site.get("pois") or []
    if not pois:
        return []
    flow = [Paragraph(
        f"Top POIs per category (up to {limit}, sorted by score magnitude)",
        s["h3"],
    )]
    cat_order = ["sport", "symbiose", "premium", "competitor", "negative"]
    for cat in cat_order:
        items = sorted(
            [p for p in pois if p.category == cat],
            key=lambda p: abs(p.score), reverse=True,
        )[:limit]
        if not items:
            continue
        rows = [["Name", "Type", "Dist (m)", "Bucket", "Score"]]
        for p in items:
            rows.append([
                p.name[:38],
                p.subcategory.replace("_", " ").title()[:22],
                str(int(p.distance_m)),
                p.bucket,
                f"{p.score:+.1f}",
            ])
        flow.append(Paragraph(
            f"<b>{cat.title()}</b> ({sum(1 for p in pois if p.category == cat)} total)",
            s["body"],
        ))
        flow.append(_zebra_table(
            rows,
            [60 * mm, 38 * mm, 18 * mm, 20 * mm, 18 * mm],
            s["primary"], small=True,
        ))
        flow.append(Spacer(1, 1.5 * mm))
    return flow


def _cannibalization_table(brand: dict, site: dict, s: dict) -> list:
    existing = brand.get("existing_stores") or []
    if not existing:
        return []
    from utils.geo import haversine_m
    rows = [["Existing site", "Address", "Distance", "Est. overlap"]]
    items = []
    for store in existing:
        d = haversine_m(site["lat"], site["lon"],
                         store["lat"], store["lon"])
        if d < 500:
            overlap = "≈ 80%"
        elif d < 1000:
            overlap = "≈ 50%"
        elif d < 2000:
            overlap = "≈ 20%"
        else:
            overlap = "< 5%"
        items.append((d, store, overlap))
    items.sort(key=lambda x: x[0])
    for d, store, overlap in items:
        rows.append([
            store["name"],
            store.get("address", "")[:30],
            f"{d/1000:.2f} km",
            overlap,
        ])
    return [
        Paragraph(f"Cannibalization · vs. {len(existing)} existing "
                   f"{brand['brand']} locations", s["h2"]),
        _zebra_table(
            rows, [55 * mm, 60 * mm, 22 * mm, 25 * mm], s["primary"], small=True,
        ),
    ]


def _footfall_table(site: dict, s: dict) -> list:
    ff = site.get("footfall")
    if not ff:
        return []
    rows = [["Metric", "Value"]]
    rows.append(["Walk Score", f"{ff.get('walk_score', 0)}/100"])
    rows.append(["Transit Score", f"{ff.get('transit_score', 0)}/100"])
    rows.append(["Transit stops ≤ 500 m", str(ff.get("transit_stops_500m", 0))])
    rows.append(["Transit stops ≤ 250 m", str(ff.get("transit_stops_250m", 0))])
    rows.append(["Rail stations in radius", str(ff.get("rail_stations", 0))])
    return [
        Paragraph("Footfall & accessibility", s["h2"]),
        _zebra_table(rows, [60 * mm, 35 * mm], s["primary"]),
    ]


def _proforma_table(site: dict, s: dict) -> list:
    pf = site.get("proforma")
    if not pf:
        return []
    fmt = lambda v: f"{int(v):,}".replace(",", " ")
    rows = [["Year"] + [f"Y{y}" for y in pf.years]]
    rows.append(["Revenue"] + [fmt(v) for v in pf.revenue])
    rows.append(["EBITDA"] + [fmt(v) for v in pf.ebitda])
    rows.append(["Rent"] + [fmt(v) for v in pf.rent])
    rows.append(["Insurance"] + [fmt(v) for v in pf.insurance])
    rows.append(["FCF"] + [fmt(v) for v in pf.fcf])
    rows.append(["Cumulative"] + [fmt(v) for v in pf.cumulative])
    col_w = [25 * mm] + [22 * mm] * len(pf.years)

    npv_row = f"<b>NPV</b>: CHF {fmt(pf.npv)}"
    irr_row = (f"<b>IRR</b>: {pf.irr * 100:.1f}%"
                if pf.irr is not None else "<b>IRR</b>: n/a")
    pay_row = (f"<b>Payback</b>: {pf.payback_years:.1f} yr "
                f"(≈ M{pf.breakeven_month})"
                if pf.payback_years is not None
                else "<b>Payback</b>: > horizon")
    inp = pf.inputs
    inputs_para = Paragraph(
        f"<i>Inputs:</i> area {inp.area_sqm:.0f} m² · "
        f"rent CHF {inp.rent_per_sqm_yr:.0f}/m²·yr · "
        f"lease {inp.lease_years} yr · rent-free {inp.rent_free_months} mo · "
        f"CAPEX CHF {inp.capex_per_sqm:.0f}/m² · "
        f"discount {inp.discount_rate_pct:.1f}%",
        s["caption"],
    )
    return [
        Paragraph("Pro-Forma · Base scenario", s["h2"]),
        inputs_para,
        Spacer(1, 1.5 * mm),
        _zebra_table(rows, col_w, s["primary"], small=True),
        Spacer(1, 2 * mm),
        Paragraph(f"{npv_row}  ·  {irr_row}  ·  {pay_row}", s["body"]),
    ]


def _scenarios_table(site: dict, s: dict) -> list:
    scenarios = site.get("scenarios")
    if not scenarios:
        return []
    fmt = lambda v: f"{int(v):,}".replace(",", " ")
    rows = [["Scenario", "NPV (CHF)", "IRR", "Payback (yr)",
              "Cumulative Y5 (CHF)"]]
    for name, pf in scenarios.items():
        rows.append([
            name,
            fmt(pf.npv),
            f"{pf.irr * 100:.1f}%" if pf.irr is not None else "n/a",
            f"{pf.payback_years:.1f}" if pf.payback_years else "—",
            fmt(pf.cumulative[-1]),
        ])
    return [
        Paragraph("Scenario comparison · Bear / Base / Bull", s["h2"]),
        _zebra_table(rows,
                      [25 * mm, 35 * mm, 18 * mm, 25 * mm, 40 * mm],
                      s["primary"]),
    ]


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

def _footer(s: dict) -> list:
    return [
        Spacer(1, 5 * mm),
        Paragraph(
            f"Generated by Retail Site Intelligence  ·  "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  ·  "
            f"sources: OSM/Nominatim · BFS · BAFU · ESI (CCRS) · "
            f"Bechtiger 2024 (MAS Real Estate UZH).",
            s["caption"],
        ),
    ]


# ---------------------------------------------------------------------------
# Chart wrappers
# ---------------------------------------------------------------------------

def _score_chart(brand: dict, site: dict, s: dict, width_mm: float = 175) -> list:
    result = site.get("score_result")
    if not result:
        return []
    flood = site.get("flood_risk")
    sust = site.get("sustainability_result")
    try:
        png = charts.chart_score_breakdown(
            result,
            flood_penalty=flood.score_impact if flood else 0,
            sust_delta=sust.score_delta if sust else 0,
        )
        return [_png_image(png, width_mm), Spacer(1, 2 * mm)]
    except Exception as e:
        return [Paragraph(f"<i>Chart render failed: {e}</i>", s["caption"])]


def _walk_chart(site: dict, s: dict, width_mm: float = 165) -> list:
    ff = site.get("footfall")
    if not ff:
        return []
    try:
        png = charts.chart_walk_score(ff)
        return [_png_image(png, width_mm), Spacer(1, 2 * mm)]
    except Exception as e:
        return [Paragraph(f"<i>Chart render failed: {e}</i>", s["caption"])]


def _cashflow_chart(site: dict, s: dict, width_mm: float = 175) -> list:
    pf = site.get("proforma")
    if not pf:
        return []
    try:
        png = charts.chart_cashflow(pf)
        return [_png_image(png, width_mm), Spacer(1, 2 * mm)]
    except Exception as e:
        return [Paragraph(f"<i>Chart render failed: {e}</i>", s["caption"])]


def _scenarios_chart(site: dict, s: dict, width_mm: float = 175) -> list:
    sc = site.get("scenarios")
    if not sc:
        return []
    try:
        png = charts.chart_scenarios(sc)
        return [_png_image(png, width_mm), Spacer(1, 2 * mm)]
    except Exception as e:
        return [Paragraph(f"<i>Chart render failed: {e}</i>", s["caption"])]


def _poi_chart(site: dict, s: dict, width_mm: float = 175) -> list:
    pois = site.get("pois")
    if not pois:
        return []
    try:
        png = charts.chart_poi_categories(pois, site.get("radius_m", 0))
        return [_png_image(png, width_mm), Spacer(1, 2 * mm)]
    except Exception as e:
        return [Paragraph(f"<i>Chart render failed: {e}</i>", s["caption"])]


def _static_map(site: dict, s: dict, width_mm: float = 165) -> list:
    """OSMnx street network + POI scatter — embeddable in PDF as static map."""
    pois = site.get("pois")
    if not pois or site.get("lat") is None:
        return []
    try:
        png = charts.chart_static_map(
            pois, site["lat"], site["lon"], site.get("radius_m", 500),
        )
        return [_png_image(png, width_mm), Spacer(1, 2 * mm)]
    except Exception as e:
        return [Paragraph(f"<i>Static map failed: {e}</i>", s["caption"])]


# ---------------------------------------------------------------------------
# Public — Site Report (1-pager)
# ---------------------------------------------------------------------------

def site_report_pdf(brand: dict, site: dict) -> bytes:
    """Compact analyst summary: score band + Glass-Box chart + market +
    sustainability — fits one A4 page in most cases."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=12 * mm, bottomMargin=12 * mm,
        leftMargin=15 * mm, rightMargin=15 * mm,
        title=f"Site Report — {site.get('name', 'Candidate')}",
    )
    s = _styles(brand)
    flow = []
    flow += _header(brand, site, s, "Site Report")
    flow += _score_band(brand, site, s)
    flow += _score_chart(brand, site, s, width_mm=170)
    flow += _static_map(site, s, width_mm=150)
    flow += _market_table(brand, site, s)
    flow += [Spacer(1, 3 * mm)]
    flow += _sustainability_table(site, s)
    flow += _footer(s)
    doc.build(flow)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public — Deal Memo (multi-page)
# ---------------------------------------------------------------------------

def deal_memo_pdf(brand: dict, site: dict) -> bytes:
    """Full deal memo: every sub-analysis with chart + table.

    Page layout:
      1. Header · Score band · Glass-Box chart · Verdict statement
      2. Market · Dimension breakdown · Sustainability
      3. Proximity distribution chart · per-category top POIs · cannibalization
      4. Walk Score chart · footfall + transit detail
      5. Pro-Forma table · cashflow chart · scenarios chart + table
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=12 * mm, bottomMargin=12 * mm,
        leftMargin=15 * mm, rightMargin=15 * mm,
        title=f"Deal Memo — {site.get('name', 'Candidate')}",
    )
    s = _styles(brand)
    flow = []

    # Page 1 — Executive summary
    flow += _header(brand, site, s, "Deal Memo")
    flow += _score_band(brand, site, s)
    flow += _score_chart(brand, site, s, width_mm=170)
    flow += [Spacer(1, 2 * mm)]
    flow += _verdict_paragraph(site, s)

    # Page 2 — Market + dimensions + sustainability
    flow += [PageBreak()]
    flow += _market_table(brand, site, s)
    flow += [Spacer(1, 3 * mm)]
    flow += _dimensions_table(site, s)
    flow += [Spacer(1, 3 * mm)]
    flow += _sustainability_table(site, s)

    # Page 3 — Proximity
    flow += [PageBreak()]
    flow += _proximity_summary_table(site, s)
    flow += [Spacer(1, 2 * mm)]
    flow += _static_map(site, s, width_mm=165)
    flow += [Spacer(1, 2 * mm)]
    flow += _poi_chart(site, s, width_mm=170)
    flow += [PageBreak()]
    flow += _proximity_top_pois(site, s, limit=6)
    flow += [Spacer(1, 3 * mm)]
    flow += _cannibalization_table(brand, site, s)

    # Page 4 — Footfall
    flow += [PageBreak()]
    flow += _footfall_table(site, s)
    flow += [Spacer(1, 3 * mm)]
    flow += _walk_chart(site, s, width_mm=165)

    # Page 5 — Pro-Forma
    flow += [PageBreak()]
    flow += _proforma_table(site, s)
    flow += [Spacer(1, 3 * mm)]
    flow += _cashflow_chart(site, s, width_mm=170)
    flow += [Spacer(1, 3 * mm)]
    flow += _scenarios_table(site, s)
    flow += [Spacer(1, 2 * mm)]
    flow += _scenarios_chart(site, s, width_mm=170)

    flow += _footer(s)
    doc.build(flow)
    return buf.getvalue()


def _verdict_paragraph(site: dict, s: dict) -> list:
    esi = site.get("score_esi") or site.get("score") or 0
    flood = site.get("flood_risk")
    if esi >= 75:
        verdict_text = "🟢 <b>Go</b> — recommend progression to LOI."
        recommendation = (
            "Site Score above 75 indicates a strong match against the "
            "brand's positioning criteria. Premium environment, demographics, "
            "and accessibility all support a flagship-tier placement."
        )
    elif esi >= 50:
        verdict_text = "🟡 <b>Vertiefte Prüfung</b> — additional due diligence required."
        recommendation = (
            "Site Score 50-74 indicates a defensible candidate with material "
            "weaknesses. Investigate the lowest-scoring dimensions and the "
            "sensitivity range before progressing."
        )
    else:
        verdict_text = "🔴 <b>No-Go</b> — do not progress."
        recommendation = (
            "Site Score below 50 indicates structural misalignment with the "
            "brand's positioning. The financial model is gated and the deal "
            "should not advance without re-scoping the asset."
        )
    flood_note = ""
    if flood and flood.score_impact <= -10:
        flood_note = (
            "<br/><b>Flood-risk note:</b> this candidate carries a material "
            f"natural-hazard exposure ({flood.label}). "
            f"Business-interruption insurance and protection-plan costs "
            f"are auto-included in the Pro-Forma; the rent floor for a "
            f"viable lease structure is correspondingly higher."
        )
    return [
        Paragraph("Recommendation", s["h2"]),
        Paragraph(verdict_text, s["body"]),
        Paragraph(recommendation + flood_note, s["body"]),
    ]
