"""Site Score aggregation — Glass Box methodology.

Every dimension is normalised to 0–100 BEFORE the weighted sum, so the
breakdown is directly comparable across dimensions. The dimension weights
live in `score_weights.json` and are user-editable from the sidebar.

Premium Environment Index (PEI) is a sub-score 0–10 derived from:
    • 5★ hotel count in radius
    • Luxury / premium-brand POI count in radius
    • Municipality Kaufkraft index (BFS)
PEI < 5 emits a warning that the site doesn't match On's premium positioning.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from utils.osm import POI, apply_cluster_negatives, apply_competitor_saturation


# ---------------------------------------------------------------------------
# Dimension normalisers (raw → 0-100)
# ---------------------------------------------------------------------------

def _norm_demographie(mun: dict[str, Any] | None) -> tuple[float, dict]:
    """Population (log) + Kaufkraft idx + age 18-45 share + growth."""
    if not mun:
        return 35.0, {"_note": "Municipality outside BFS seed — neutral baseline"}
    pop = float(mun.get("population", 0) or 0)
    pop_score = max(0.0, min(100.0, 25 * math.log10(max(pop, 1)) - 30))
    # 1k pop ≈ 45, 100k ≈ 95, 400k+ ≈ 100. Below 1k → 0.
    kaufkraft = float(mun.get("kaufkraft_index", 100) or 100)
    kk_score = max(0.0, min(100.0, (kaufkraft - 80) * 2.5))   # 80 → 0, 120 → 100
    age = float(mun.get("age_18_45_pct", 35) or 35)
    age_score = max(0.0, min(100.0, (age - 25) * 5))           # 25% → 0, 45% → 100
    growth = float(mun.get("growth_5y_pct", 0) or 0)
    growth_score = max(0.0, min(100.0, 50 + growth * 8))       # 0% → 50, +6% → 98
    final = 0.4 * pop_score + 0.3 * kk_score + 0.2 * age_score + 0.1 * growth_score
    return final, {
        "Population": (pop_score, f"{int(pop):,}"),
        "Kaufkraft idx": (kk_score, f"{kaufkraft:.0f}"),
        "Age 18-45 %": (age_score, f"{age:.1f}%"),
        "5y growth %": (growth_score, f"{growth:+.1f}%"),
    }


def _norm_accessibility(walk_score: int | None,
                         transit_score: int | None) -> tuple[float, dict]:
    if walk_score is None and transit_score is None:
        return 40.0, {"_note": "Step 4 not yet run"}
    w = float(walk_score or 0)
    t = float(transit_score or 0)
    final = 0.6 * w + 0.4 * t
    return final, {
        "Walk Score": (w, f"{int(w)}/100"),
        "Transit Score": (t, f"{int(t)}/100"),
    }


def _norm_tier_sum(pois: list[POI], category: str,
                    saturation_raw: float = 40.0) -> tuple[float, dict]:
    """Generic positive-tier normaliser: distance-weighted sum → 0-100 via
    log saturation curve.

    `saturation_raw` is the raw sum at which the dimension reaches ~95/100."""
    items = [p for p in pois if p.category == category]
    raw = sum(p.score for p in items)
    raw_pos = max(raw, 0.0)
    final = 100 * (1 - math.exp(-raw_pos / saturation_raw))
    return final, {
        "Count": (None, str(len(items))),
        "Raw weighted sum": (None, f"{raw:+.1f}"),
        "Saturation (95 ≈ raw {})".format(int(saturation_raw)): (final, ""),
    }


def _norm_konkurrenz(pois: list[POI]) -> tuple[float, dict]:
    items = [p for p in pois if p.category == "competitor"]
    n = len(items)
    raw = sum(p.score for p in items)  # already saturation-adjusted
    # Start at 100, decrement by abs(raw); raw < 0 since competitors are negative
    final = max(0.0, 100.0 + raw * 2.0)
    return final, {
        "Direct competitors in radius": (None, str(n)),
        "Saturation penalty": (None, f"{raw:+.1f}"),
    }


def _norm_premium_env(pei_0_10: float) -> tuple[float, dict]:
    return pei_0_10 * 10, {"PEI (0-10)": (pei_0_10 * 10, f"{pei_0_10:.1f}/10")}


def _norm_negativ(pois: list[POI], cluster_extras: dict[str, float]) -> tuple[float, dict]:
    items = [p for p in pois if p.category == "negative"]
    raw_per_poi = sum(p.score for p in items)
    extras = sum(cluster_extras.values())
    raw = raw_per_poi + extras
    # raw is negative; clamp to [-50, 0] then map to [0, 100]
    raw_clamped = max(raw, -50.0)
    final = 100 + raw_clamped * 2  # raw = 0 → 100; raw = -50 → 0
    return final, {
        "Negative POIs": (None, str(len(items))),
        "Per-POI penalty": (None, f"{raw_per_poi:+.1f}"),
        "Cluster penalty": (None, f"{extras:+.1f}"),
    }


# ---------------------------------------------------------------------------
# Premium Environment Index (0–10)
# ---------------------------------------------------------------------------

def compute_pei(pois: list[POI], mun: dict[str, Any] | None) -> tuple[float, dict]:
    """0–10 composite. Inputs:
        - 5★ hotels in radius
        - Premium / luxury POI count
        - Kaufkraft index
    """
    hotel_5 = sum(1 for p in pois if p.subcategory == "hotel_5star")
    premium_count = sum(1 for p in pois if p.category == "premium")
    kaufkraft = float((mun or {}).get("kaufkraft_index", 100) or 100)

    hotel_pts = min(hotel_5 * 1.5, 3.0)
    premium_pts = min(premium_count * 0.4, 3.5)
    kk_pts = max(0.0, min(3.5, (kaufkraft - 95) / 8.0))

    pei = round(hotel_pts + premium_pts + kk_pts, 1)
    return pei, {
        "5★ hotels in radius": (hotel_pts, f"{hotel_5}"),
        "Premium/luxury POIs":  (premium_pts, f"{premium_count}"),
        "Kaufkraft idx":        (kk_pts, f"{kaufkraft:.0f}"),
    }


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    total: int                                  # 0–100
    verdict: str                                # "🟢 Go" / "🟡 Vertiefte Prüfung" / "🔴 No-Go"
    pei: float                                  # 0–10
    pei_breakdown: dict[str, tuple[Any, str]]
    dimensions: dict[str, dict]                 # per-dim {raw, norm, weight, contribution, sub}
    cluster_extras: dict[str, float]
    saturation_n: int                           # # of direct competitors


def aggregate(
    pois: list[POI],
    weights: dict[str, float],
    municipality: dict[str, Any] | None,
    walk_score: int | None = None,
    transit_score: int | None = None,
) -> ScoreResult:
    # 1. Apply competitor saturation + cluster effects (mutates POIs)
    apply_competitor_saturation(pois)
    cluster_extras = apply_cluster_negatives(pois)
    n_competitors = sum(1 for p in pois if p.category == "competitor")

    # 2. PEI sub-score
    pei, pei_break = compute_pei(pois, municipality)

    # 3. Each dimension normalised 0-100
    dims: dict[str, dict] = {}

    norm, sub = _norm_demographie(municipality)
    dims["demographie"] = {"norm": norm, "sub": sub}

    norm, sub = _norm_accessibility(walk_score, transit_score)
    dims["accessibility"] = {"norm": norm, "sub": sub}

    norm, sub = _norm_tier_sum(pois, "sport", saturation_raw=45.0)
    dims["sportstaetten"] = {"norm": norm, "sub": sub}

    norm, sub = _norm_tier_sum(pois, "symbiose", saturation_raw=30.0)
    dims["symbiose"] = {"norm": norm, "sub": sub}

    norm, sub = _norm_konkurrenz(pois)
    dims["konkurrenz"] = {"norm": norm, "sub": sub}

    norm, sub = _norm_premium_env(pei)
    dims["premium_environment"] = {"norm": norm, "sub": sub}

    norm, sub = _norm_negativ(pois, cluster_extras)
    dims["negativ"] = {"norm": norm, "sub": sub}

    # 4. Weighted total
    total = 0.0
    for key, d in dims.items():
        w = float(weights.get(key, 0.0))
        contrib = d["norm"] * w
        d["weight"] = w
        d["contribution"] = contrib
        total += contrib

    total = int(round(max(0.0, min(100.0, total))))
    if total >= 75:
        verdict = "🟢 Go"
    elif total >= 50:
        verdict = "🟡 Vertiefte Prüfung"
    else:
        verdict = "🔴 No-Go"

    return ScoreResult(
        total=total,
        verdict=verdict,
        pei=pei,
        pei_breakdown=pei_break,
        dimensions=dims,
        cluster_extras=cluster_extras,
        saturation_n=n_competitors,
    )
