"""End-to-end smoke test for the Sprint 1+2 pipeline.

Runs the full pipeline (geocode → OSM → classify → walk-score → aggregate →
flood-risk → pro-forma → PDFs) for the real On Flagship at Limmatquai 28
and prints diagnostics.
"""
from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import streamlit as st  # noqa: F401

st.cache_data = lambda *a, **kw: (
    (lambda f: f) if (a and callable(a[0])) else (lambda f: f)
)

from utils.bfs import lookup_municipality
from utils.config import load_brand, load_weights
from utils.export import deal_memo_pdf, site_report_pdf
from utils.financial import ProFormaInputs, build_proforma, build_scenarios
from utils.flood import check_flood_risk
from utils.geo import geocode
from utils.osm import classify_pois, fetch_osm_features, summarize_by_category
from utils.scoring import aggregate
from utils.sustainability import (
    SustainabilityInputs,
    compute as compute_sustainability,
    derive_ov_class_from_transit,
)
from utils.walk import compute_walk_score, transit_to_score_0_100


def main() -> int:
    brand = load_brand()
    weights = load_weights()

    r = geocode("Limmatquai 28, 8001 Zurich")
    if r is None:
        print("FAIL: geocoding returned None")
        return 1
    print(f"GEOCODE  : ({r.lat:.4f}, {r.lon:.4f})  city={r.city}")

    flood = check_flood_risk(r.lat, r.lon)
    print(f"FLOOD    : {flood.label}  impact={flood.score_impact:+d}pts  "
          f"insurance=CHF {flood.insurance_chf:,}  source={flood.source}")

    mun = lookup_municipality(r.city)
    print(f"BFS      : {mun['city'] if mun else 'not found'}")

    features = fetch_osm_features(r.lat, r.lon, 500)
    print(f"OSM FETCH: {len(features)} rows")

    pois = classify_pois(features, brand,
                          candidate_lat=r.lat, candidate_lon=r.lon, radius_m=500)
    print(f"CLASSIFY : {len(pois)} POIs scored")
    summary = summarize_by_category(pois)
    for cat, s in summary.items():
        print(f"  {cat:11s}: count={s['count']:3d}  sum={s['score_sum']:+7.1f}")

    ws = compute_walk_score(r.lat, r.lon, 1000)
    t_score = transit_to_score_0_100(ws)
    print(f"WALK/TRANS: walk={ws.score}/100  transit={t_score}/100")

    result = aggregate(
        pois, weights, mun,
        walk_score=ws.score, transit_score=t_score,
    )
    adjusted = max(0, min(100, result.total + flood.score_impact))
    print(f"\nSITE SCORE: raw={result.total}/100  flood={flood.score_impact:+d}  "
          f"adjusted={adjusted}/100  verdict={result.verdict}  PEI={result.pei}/10")

    # Sustainability (Bechtiger 2024) — try worst-case: no lift, artificial light
    suggested = derive_ov_class_from_transit(t_score, ws.rail_stations)
    print(f"\nSUSTAIN  : ÖV-class suggested by Step 4 transit metrics: {suggested}")
    si = SustainabilityInputs(
        accessibility="no_lift", daylight="artificial", ov_class=suggested,
    )
    sust = compute_sustainability(si)
    esi = adjusted + sust.score_delta
    print(f"  worst-case (no lift, artificial): "
          f"score Δ {sust.score_delta:+d}, rent Δ {sust.rent_delta_pct:+.1f}%, "
          f"ESI={esi}")
    # And best-case: wheelchair + natural daylight
    si2 = SustainabilityInputs(
        accessibility="wheelchair", daylight="natural", ov_class=suggested,
    )
    sust2 = compute_sustainability(si2)
    esi2 = adjusted + sust2.score_delta
    print(f"  best-case  (wheelchair, natural):  "
          f"score Δ {sust2.score_delta:+d}, rent Δ {sust2.rent_delta_pct:+.1f}%, "
          f"ESI={esi2}")
    for k, d in result.dimensions.items():
        print(f"  {k:22s}: norm={d['norm']:5.1f}  w={d['weight']:.0%}  "
              f"contrib={d['contribution']:5.1f}")

    # --- Pro-Forma ---
    inp = ProFormaInputs.defaults(brand)
    inp.insurance_yr_chf = float(flood.insurance_chf)
    pf = build_proforma(inp)
    print(f"\nPROFORMA : NPV=CHF {int(pf.npv):,}  "
          f"IRR={pf.irr * 100:.1f}%" if pf.irr else "  IRR=n/a")
    print(f"  payback={pf.payback_years:.1f}y  break-even=month {pf.breakeven_month}")
    # Rental KPIs
    print(f"LEASEHOLD KPIs:")
    print(f"  Occupancy Cost Ratio Y1: {pf.occupancy_cost_ratio[1]:.1f}%")
    print(f"  Total rent over horizon: CHF {int(pf.total_rent_paid):,}")
    print(f"  Cash-on-Cash Y1: {pf.cash_on_cash[1]:.1f}%")
    print(f"  Break-even sales Y1: CHF {int(pf.breakeven_sales[1]):,}")
    print(f"  CAPEX (Y0 fit-out): CHF {int(pf.capex):,}")

    scenarios = build_scenarios(inp)
    for name, p in scenarios.items():
        print(f"  {name:5s}: NPV={int(p.npv):,}  "
              f"IRR={p.irr * 100:.1f}%" if p.irr else f"  {name}: NPV={int(p.npv):,}")

    # --- PDFs ---
    site = {
        "name": "Limmatquai 28", "address": r.display_name,
        "lat": r.lat, "lon": r.lon, "radius_m": 500,
        "score": adjusted, "score_raw": result.total, "score_esi": esi2,
        "score_result": result, "flood_risk": flood,
        "sustainability_result": sust2,
        "market": {"municipality": mun, "hotels_5star": 0, "hotels_4star": 0},
        "pois": pois, "proforma": pf,
    }
    sr = site_report_pdf(brand, site)
    dm = deal_memo_pdf(brand, site)
    print(f"\nPDFs     : site_report={len(sr):,}B  deal_memo={len(dm):,}B")

    return 0


if __name__ == "__main__":
    sys.exit(main())
