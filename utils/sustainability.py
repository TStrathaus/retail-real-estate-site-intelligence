"""Nachhaltigkeits-Scoring — three empirically-grounded factors.

Source: Bechtiger 2024, "Empirische Analyse Nachhaltigkeitsmerkmale &
Wirtschaftlichkeit Schweizer Renditeliegenschaften", MAS Real Estate
thesis, UZH/CCRS. n = 288 Swiss income properties, ESI dataset.

Two factors are statistically significant and drive both score AND rent:
    • Accessibility (lift / wheelchair) — p < .001
    • Natural daylight — p = .021

ÖV-Güteklasse (VSS-Norm 640 290) is included as a location indicator —
positive in score, but Bechtiger found *no significant* rent effect, so
the rent adjustment is 0 and the UI carries a note.

Heating energy & air quality were tested and found NOT significant
(p > .1 on both Bewertung and Ertrag), so they are deliberately omitted.

ESI mapping (CCRS Economic Sustainability Indicator):
    Standort & Mobilität → ÖV (this module) + transit score (Step 4)
    Sicherheit           → Hochwasser/Naturgefahren (BAFU — see flood.py)
    Gesundheit           → Tageslicht (this module)
    Flexibilität         → Barrierefreiheit (this module)

Sustainability factors:
    • Score deltas are ADDITIVE outside the 100-pt base score
    • Rent deltas applied directly to annual rent in Step 6 Pro-Forma
"""
from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Empirical coefficients (Bechtiger 2024)
# ---------------------------------------------------------------------------

ACCESSIBILITY_OPTIONS: dict[str, dict] = {
    "wheelchair": {
        "label": "Wheelchair-accessible (lift + level entry)",
        "score_delta": 0,
        "rent_delta_pct": 0.0,
        "valuation_delta_pct": 0.0,
        "evidence": "baseline",
    },
    "non_wheelchair_lift": {
        "label": "Lift present but not wheelchair-accessible",
        "score_delta": -8,
        "rent_delta_pct": -7.8,
        "valuation_delta_pct": -11.0,
        "evidence": "p < .001 (Bechtiger 2024)",
    },
    "no_lift": {
        "label": "No elevator",
        "score_delta": -15,
        "rent_delta_pct": -13.0,
        "valuation_delta_pct": -16.9,
        "evidence": "p < .001 (Bechtiger 2024)",
    },
}

DAYLIGHT_OPTIONS: dict[str, dict] = {
    "natural": {
        "label": "Natural daylight (windows / skylights)",
        "score_delta": +5,
        "rent_delta_pct": +6.2,
        "evidence": "p = .021 (Bechtiger 2024)",
    },
    "artificial": {
        "label": "Artificial lighting only",
        "score_delta": 0,
        "rent_delta_pct": 0.0,
        "evidence": "baseline",
    },
}

OV_CLASS_OPTIONS: dict[str, dict] = {
    "A": {"label": "A — excellent (hub stations, ≥ 5-min headway)",
           "score_delta": +8, "rent_delta_pct": 0.0},
    "B": {"label": "B — very good (rail/tram, 5–10-min headway)",
           "score_delta": +8, "rent_delta_pct": 0.0},
    "C": {"label": "C — good (bus, ≤ 20-min headway)",
           "score_delta": +4, "rent_delta_pct": 0.0},
    "D": {"label": "D — moderate (occasional / distance)",
           "score_delta": 0, "rent_delta_pct": 0.0},
}
OV_NOT_SIGNIFICANT_NOTE = (
    "ÖV-Güteklasse adds to the score (location indicator) but Bechtiger "
    "2024 found no statistically significant rent effect, so rent uplift "
    "is 0%."
)

METHODOLOGY_NOTE = (
    "Sustainability scoring methodology: **ESI (CCRS/UZH)** + "
    "**Bechtiger 2024** empirical coefficients (n = 288 Swiss income "
    "properties, MAS Real Estate UZH)."
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SustainabilityInputs:
    accessibility: str = "wheelchair"
    daylight: str = "artificial"
    ov_class: str = "C"


@dataclass
class SustainabilityFactor:
    name: str
    label: str
    score_delta: int
    rent_delta_pct: float
    evidence: str


@dataclass
class SustainabilityResult:
    inputs: SustainabilityInputs
    factors: list[SustainabilityFactor]
    score_delta: int          # total additive score (outside base 100)
    rent_delta_pct: float     # applied to annual rent (Step 6)


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

def derive_ov_class_from_transit(transit_score: int | None,
                                   rail_stations: int | None) -> str:
    """Heuristic — Swisstopo ARE/VSS class would be authoritative. We
    derive a defensible default from the transit metrics computed in
    Step 4 so the analyst can override manually."""
    t = transit_score or 0
    r = rail_stations or 0
    if t >= 80 and r >= 1:
        return "A"
    if t >= 60 and r >= 1:
        return "B"
    if t >= 40:
        return "C"
    return "D"


def compute(inp: SustainabilityInputs) -> SustainabilityResult:
    acc = ACCESSIBILITY_OPTIONS[inp.accessibility]
    day = DAYLIGHT_OPTIONS[inp.daylight]
    ov = OV_CLASS_OPTIONS[inp.ov_class]

    factors = [
        SustainabilityFactor(
            name="Accessibility", label=acc["label"],
            score_delta=acc["score_delta"],
            rent_delta_pct=acc["rent_delta_pct"],
            evidence=acc["evidence"],
        ),
        SustainabilityFactor(
            name="Daylight", label=day["label"],
            score_delta=day["score_delta"],
            rent_delta_pct=day["rent_delta_pct"],
            evidence=day["evidence"],
        ),
        SustainabilityFactor(
            name="ÖV-Güteklasse",
            label=f"{ov['label']} · rent effect not significant",
            score_delta=ov["score_delta"],
            rent_delta_pct=0.0,
            evidence="positive but n.s. (Bechtiger 2024)",
        ),
    ]
    total_score = sum(f.score_delta for f in factors)
    total_rent = sum(f.rent_delta_pct for f in factors)
    return SustainabilityResult(
        inputs=inp, factors=factors,
        score_delta=total_score, rent_delta_pct=total_rent,
    )
