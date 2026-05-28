"""5-year Pro-Forma for a **leased** retail location.

This is a **leasehold** cashflow model, not a property-purchase DCF. On
runs a DTC store-network strategy on leased premises — CAPEX is the
fit-out spend (typical CHF 4'000/m²), recovered over the lease term via
EBITDA - rent - insurance - other OPEX. There is no property purchase
or terminal value.

Inputs are kept analyst-friendly — every field has an On-default
benchmark from `on_brand.json`. Three pre-baked scenarios (Bear / Base /
Bull) and a sensitivity layer that mutates Base by ±% on rent and
footfall.

**Rental KPIs (industry-standard retail leasehold):**
  • Occupancy Cost Ratio = (Rent + Insurance) / Revenue — target <10% for premium retail
  • Total rent paid over lease term — gross lease commitment
  • Cash-on-Cash Return = annual FCF / CAPEX (after Year 0)
  • Break-Even Sales = (Rent + Insurance + Other OPEX) / EBITDA margin
  • Rent escalation = annual % increase (Swiss commercial leases typically 1-3%)

Outputs:
  • Year-by-year revenue, EBITDA, rent, insurance, FCF, cumulative
  • NPV at user-set discount rate
  • IRR (numpy-financial)
  • Payback period (linear interpolation)
  • Occupancy cost ratio per year
  • Total rent over lease horizon
  • Cash-on-cash return per year
  • Break-even sales per year
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy_financial as npf

# ---------------------------------------------------------------------------
# Input dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProFormaInputs:
    area_sqm: float
    rent_per_sqm_yr: float
    lease_years: int
    rent_free_months: int
    capex_per_sqm: float
    revenue_y1: float
    revenue_growth_pct: float    # % per year
    ebitda_margin_pct: float
    discount_rate_pct: float = 8.0
    insurance_yr_chf: float = 0.0   # auto-injected from flood risk
    other_opex_pct_of_rev: float = 0.0   # extras beyond rent + insurance
    horizon_years: int = 5
    rent_escalation_pct: float = 1.5   # Swiss commercial-lease average

    @classmethod
    def defaults(cls, brand: dict) -> "ProFormaInputs":
        area = float(brand.get("typical_store_sqm", 200))
        capex = float(brand.get("typical_capex_per_sqm_chf", 4000))
        return cls(
            area_sqm=area,
            rent_per_sqm_yr=2400.0,        # CHF 200/m²/month — Bahnhofstrasse-class
            lease_years=10,
            rent_free_months=3,
            capex_per_sqm=capex,
            revenue_y1=3_500_000.0,        # benchmark On flagship turnover
            revenue_growth_pct=8.0,
            ebitda_margin_pct=22.0,
            discount_rate_pct=8.0,
            rent_escalation_pct=1.5,
        )


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProForma:
    years: list[int]                # 0..horizon
    revenue: list[float]            # y1..yN (0 at year 0)
    ebitda: list[float]
    rent: list[float]
    insurance: list[float]
    other_opex: list[float]
    fcf: list[float]                # incl. -CAPEX at year 0
    cumulative: list[float]
    npv: float
    irr: float | None
    payback_years: float | None
    breakeven_month: int | None
    # Rental / leasehold KPIs
    occupancy_cost_ratio: list[float]   # (rent+insurance)/revenue per year
    total_rent_paid: float              # sum across horizon
    cash_on_cash: list[float]           # FCF[y] / CAPEX per year (y≥1)
    breakeven_sales: list[float]        # (rent+ins+opex) / ebitda_margin per year
    capex: float                        # year-0 fit-out
    inputs: ProFormaInputs = field(default_factory=lambda: ProFormaInputs.defaults({}))


def build_proforma(inp: ProFormaInputs) -> ProForma:
    H = max(1, int(inp.horizon_years))
    years = list(range(0, H + 1))

    capex = inp.area_sqm * inp.capex_per_sqm
    full_rent = inp.area_sqm * inp.rent_per_sqm_yr

    revenue = [0.0] + [
        inp.revenue_y1 * ((1 + inp.revenue_growth_pct / 100.0) ** i)
        for i in range(H)
    ]
    ebitda = [0.0] + [r * inp.ebitda_margin_pct / 100.0 for r in revenue[1:]]
    # Rent: rent-free in Y1, then annual escalation per Swiss-lease convention
    rent = [0.0]
    esc = 1 + inp.rent_escalation_pct / 100.0
    for i in range(H):
        base_rent = full_rent * (esc ** i)
        if i == 0:
            rent.append(base_rent * (1 - inp.rent_free_months / 12.0))
        else:
            rent.append(base_rent)
    insurance = [0.0] + [inp.insurance_yr_chf for _ in range(H)]
    other_opex = [0.0] + [
        r * inp.other_opex_pct_of_rev / 100.0 for r in revenue[1:]
    ]

    fcf = [-capex] + [
        e - r_ - ins - oth
        for e, r_, ins, oth in zip(ebitda[1:], rent[1:], insurance[1:], other_opex[1:])
    ]
    cumulative = list(np.cumsum(fcf))

    # ----- Leasehold KPIs -----
    occupancy_cost_ratio = [0.0] + [
        (r_ + ins) / rev * 100.0 if rev > 0 else 0.0
        for r_, ins, rev in zip(rent[1:], insurance[1:], revenue[1:])
    ]
    total_rent_paid = float(sum(rent))
    cash_on_cash = [0.0] + [
        f / capex * 100.0 if capex > 0 else 0.0
        for f in fcf[1:]
    ]
    margin = inp.ebitda_margin_pct / 100.0
    breakeven_sales = [0.0] + [
        (r_ + ins + oth) / margin if margin > 0 else 0.0
        for r_, ins, oth in zip(rent[1:], insurance[1:], other_opex[1:])
    ]

    # NPV (year 0 included)
    disc = 1 + inp.discount_rate_pct / 100.0
    npv = sum(c / (disc ** i) for i, c in enumerate(fcf))

    # IRR
    try:
        irr = npf.irr(fcf)
        if irr is None or np.isnan(irr) or np.isinf(irr):
            irr = None
        else:
            irr = float(irr)
    except Exception:
        irr = None

    # Payback (linear interp on cumulative crossing zero)
    payback_years = None
    breakeven_month = None
    for i in range(1, len(cumulative)):
        if cumulative[i] >= 0 and cumulative[i - 1] < 0:
            prev = cumulative[i - 1]
            cur = cumulative[i]
            frac = (-prev) / (cur - prev) if (cur - prev) > 0 else 0
            payback_years = (i - 1) + frac
            breakeven_month = int(round(payback_years * 12))
            break

    return ProForma(
        years=years, revenue=revenue, ebitda=ebitda, rent=rent,
        insurance=insurance, other_opex=other_opex,
        fcf=fcf, cumulative=cumulative,
        npv=float(npv), irr=irr,
        payback_years=payback_years, breakeven_month=breakeven_month,
        occupancy_cost_ratio=occupancy_cost_ratio,
        total_rent_paid=total_rent_paid,
        cash_on_cash=cash_on_cash,
        breakeven_sales=breakeven_sales,
        capex=float(capex),
        inputs=inp,
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def scenario_inputs(base: ProFormaInputs, kind: str) -> ProFormaInputs:
    """Return Bear / Base / Bull variants of the input set."""
    from dataclasses import replace
    if kind == "bear":
        return replace(
            base,
            revenue_y1=base.revenue_y1 * 0.7,
            revenue_growth_pct=base.revenue_growth_pct * 0.5,
            ebitda_margin_pct=max(0, base.ebitda_margin_pct - 4),
        )
    if kind == "bull":
        return replace(
            base,
            revenue_y1=base.revenue_y1 * 1.20,
            revenue_growth_pct=base.revenue_growth_pct * 1.3,
            ebitda_margin_pct=base.ebitda_margin_pct + 3,
        )
    return base


def build_scenarios(base: ProFormaInputs) -> dict[str, ProForma]:
    return {
        "Bear": build_proforma(scenario_inputs(base, "bear")),
        "Base": build_proforma(scenario_inputs(base, "base")),
        "Bull": build_proforma(scenario_inputs(base, "bull")),
    }


# ---------------------------------------------------------------------------
# Sensitivity (Innovation 6)
# ---------------------------------------------------------------------------

def apply_sensitivity(base: ProFormaInputs, rent_delta_pct: float,
                       footfall_delta_pct: float) -> ProFormaInputs:
    """Adjust base by ±% rent and ±% footfall (interpreted as revenue Y1)."""
    from dataclasses import replace
    return replace(base,
                    rent_per_sqm_yr=base.rent_per_sqm_yr * (1 + rent_delta_pct / 100.0),
                    revenue_y1=base.revenue_y1 * (1 + footfall_delta_pct / 100.0))
