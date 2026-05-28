"""Generate sample PDFs to outputs/ for visual inspection. Also exercises
the isochrone path so the scikit-learn fix can be verified."""
from __future__ import annotations

import pathlib
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
from utils.geo import geocode, walking_isochrone
from utils.presentation_pptx import build_overview_deck
from utils.osm import classify_pois, fetch_osm_features
from utils.scoring import aggregate
from utils.sustainability import SustainabilityInputs, compute as compute_sust
from utils.walk import compute_walk_score, transit_to_score_0_100


def main() -> int:
    brand = load_brand()
    weights = load_weights()

    # --- isochrone smoke ---
    iso = walking_isochrone(47.3731, 8.5430, minutes=10)
    if iso is None:
        print("ISOCHRONE: None (build failed)")
    else:
        coords = iso.get("coordinates")
        ring_len = (len(coords[0]) if coords and isinstance(coords[0], list)
                    else 0)
        print(f"ISOCHRONE: ok, type={iso.get('type')}, "
              f"polygon nodes={ring_len}")

    # --- full pipeline + PDF ---
    r = geocode("Limmatquai 28, 8001 Zurich")
    flood = check_flood_risk(r.lat, r.lon)
    mun = lookup_municipality(r.city)
    features = fetch_osm_features(r.lat, r.lon, 500)
    pois = classify_pois(features, brand,
                          candidate_lat=r.lat, candidate_lon=r.lon, radius_m=500)
    ws = compute_walk_score(r.lat, r.lon, 1000)
    t = transit_to_score_0_100(ws)
    result = aggregate(pois, weights, mun, walk_score=ws.score, transit_score=t)
    adjusted = max(0, min(100, result.total + flood.score_impact))

    # Sustainability: worst-case for a stressed example
    si = SustainabilityInputs(
        accessibility="no_lift", daylight="artificial", ov_class="A",
    )
    sust = compute_sust(si)
    esi = adjusted + sust.score_delta

    inp = ProFormaInputs.defaults(brand)
    inp.insurance_yr_chf = float(flood.insurance_chf)
    pf = build_proforma(inp)
    sc = build_scenarios(inp)

    ff = {
        "walk_score": ws.score, "transit_score": t,
        "category_scores": ws.category_scores,
        "category_counts": ws.category_counts,
        "transit_stops_500m": ws.transit_stops_500m,
        "transit_stops_250m": ws.transit_stops_250m,
        "rail_stations": ws.rail_stations,
    }
    site = dict(
        name="Limmatquai 28", address=r.display_name,
        lat=r.lat, lon=r.lon, radius_m=500,
        score=adjusted, score_raw=result.total, score_esi=esi,
        score_result=result, flood_risk=flood, sustainability_result=sust,
        market={
            "municipality": mun, "hotels_5star": 0,
            "hotels_4star": 0, "hotels_total": 0,
        },
        pois=pois, proforma=pf, scenarios=sc, footfall=ff,
    )

    out_dir = pathlib.Path("outputs")
    out_dir.mkdir(exist_ok=True)
    sr_path = out_dir / "sample_SiteReport_Limmatquai28.pdf"
    dm_path = out_dir / "sample_DealMemo_Limmatquai28.pdf"
    pptx_path = out_dir / "sample_ProductOverview_Limmatquai28.pptx"
    sr_path.write_bytes(site_report_pdf(brand, site))
    dm_path.write_bytes(deal_memo_pdf(brand, site))
    pptx_path.write_bytes(build_overview_deck(brand, site))
    print(f"Wrote {sr_path}  ({sr_path.stat().st_size:,} B)")
    print(f"Wrote {dm_path}  ({dm_path.stat().st_size:,} B)")
    print(f"Wrote {pptx_path}  ({pptx_path.stat().st_size:,} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
