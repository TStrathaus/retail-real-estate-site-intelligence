# Scoring Logic — Analyst Reference

This document explains **how each kind of retail neighbour is treated by the Site
Score**, and why. The scoring rules are tuned for **On's premium DTC positioning**;
the same engine generalises to other retailers by editing `data/config/on_brand.json`.


---

## 1. The 12-step classification cascade

Every POI in the OSM result set is classified by **first matching name, then OSM tag**.
The cascade is in priority order — the first rule that matches wins:

| # | Rule | Category | Base score | Source list |
|---|---|---|---|---|
| 1 | Mono-brand competitor store (Nike, Adidas, …) | `competitor` | −5 to −8 | `direct_competitors` |
| 2 | Multi-brand specialty retailer (Foot Locker, Snipes, JD Sports, Titolo) | `competitor` | **−4** | `multi_brand_specialty_retailers` |
| 3 | Mass-market shoe chain (Dosenbach, Day, Deichmann, …) | `negative` | **−3** | `mass_market_shoe_brands` |
| 4 | Luxury shoe brand (Ferragamo, Tod's, Bally, …) | `premium` | **+5** | `luxury_shoe_brands` |
| 5 | Value sport retailer (Decathlon, Sport 2000) | `symbiose` | **+2** | `value_sport_retailers` |
| 6 | Premium running specialist (Run Store, Q36.5, …) | `symbiose` | **+4** | `premium_running_specialists` |
| 7 | Wholesale partner (Ochsner Sport, Athlete's Foot, …) | `partner` | **distance-graded** | `wholesale_partners` |
| 8 | Luxury adjacency (Hermès, Louis Vuitton, …) | `premium` | **+5** | `luxury_adjacencies` |
| 9 | Complementary adjacency (Lululemon, Arc'teryx, Mammut, …) | `symbiose` | **+6 to +12** | `complementary_adjacencies` |
| 10 | Negative-environment brand (Aldi, Primark, McDonald's, …) | `negative` | **−4** | `negative_environments` |
| 11 | Discounter fallback (Lidl, KiK, Tedi, …) | `negative` | **−3** | hardcoded `DISCOUNTER_NAMES` |
| 12 | OSM tag fallback (`leisure=fitness_centre` → +15, `shop=jewelry` → +4, …) | per-tag | per-tag | `*_TAG_SCORES` |

After scoring, each POI is **distance-weighted**: ≤ 250 m × 3.0 · ≤ 500 m × 2.0 · ≤ 1 km × 1.0 · > 1 km × 0.5.
The single exception: **wholesale partners** are NOT distance-multiplied — see §3.

---

## 2. Why these rules — the analyst rationale

### Mono-brand competitor stores (−5 to −8)
Direct fight for the same customer. The per-POI penalty is moderated by
**cluster effects**:

| Competitors in radius | Effect |
|---|---|
| 0 | 0 (neutral — but flag "uncontested" in PEI) |
| 1 | **0** — first competitor signals cluster formation, neutral |
| 2-3 | per-POI penalty applies |
| 4+ | floored at **−15** total (saturation) |

### Multi-brand specialty retailers (−4)
Foot Locker, Snipes, JD Sports, Titolo. They **sell competitor products heavily** (Nike Air,
Adidas Originals, Jordan, Yeezy) and target the same shoe-buyer as On. Treated as competitor
but penalised less harshly than a mono-brand flagship.

### Mass-market shoe chains (−3)
Dosenbach, Deichmann, Bata, Day, Skechers, Schuh-Frenkel, Walder. **Wrong price tier** for
On's premium positioning, and they often carry off-brand or competitor performance products
at value prices. Negative for brand environment, not for competition.

### Luxury shoe brands (+5)
Salvatore Ferragamo, Tod's, Bally, Berluti, Christian Louboutin, Gucci, Prada. **Same
shopping-trip customer**: a premium-lifestyle buyer who walks the Bahnhofstrasse axis is
equally likely to buy an On cloud and a Bally loafer.

### Value sport retailers (+2)
Decathlon, Sport 2000. "andere Preisklasse — kein Overlap". Different price
ceiling, but their existence proves **footfall** for the sport-curious shopper.

### Premium running specialists (+4)
Run Store Zurich, Q36.5, 11Teamsports, Pomp it Up. **Same target customer**, even if they
don't carry On. Indicates a running-community-rich neighbourhood.

### Complementary adjacencies (+6 to +12)
Lululemon (+12), Arc'teryx (+10), Patagonia (+8), COS / Sweaty Betty (+7), Apple Store (+6),
Mammut / Fjällräven / Jack Wolfskin (≈+8). **Aspirational-active-lifestyle cluster** — On's
ideal neighbourhood.

### Wholesale partners — distance-graded (§3)
See next section.

### Luxury adjacencies (+5)
Hermès, Louis Vuitton, Loewe, Bottega Veneta, Prada, Chanel. **Premium-environment proxy**
for the Premium Environment Index (PEI ≥ 5 required per `min_premium_env_index`).

### Negative-environment brands (−4)
Aldi, Lidl, Primark, McDonald's, KFC, Burger King, Tedi, Action. **Discount / fast-food
ecosystem** — wrong brand environment for On Premium.

---

## 3. Wholesale partner distance grading

This is the analytically interesting rule. **A retailer selling On products** (Ochsner
Sport, Athlete's Foot, Och Sport, SportXX, Kineo) signals **proven demand** — but also
risks **cannibalising a flagship** if too close.

| Distance from candidate flagship | Score | Rationale |
|---|---|---|
| **< 250 m** | **−2** | DTC strategy says a wholesale partner across the street **dilutes the flagship**. The Och Sport on Bahnhofstrasse already serves the running-shoe customer; a new On flagship 100 m away forces internal competition. |
| **250 m – 1 km** | **0** | Healthy coexistence range. The flagship anchors the brand experience, the partner serves the impulse / multi-brand shopper. |
| **> 1 km** | **+1** | Presence proves the market without overlapping catchments. |

Wholesale partners are **not** further multiplied by the standard distance weight — the
distance class IS the score. (Other categories use base × proximity multiplier.)

This rule reflects On's actual DTC discipline: keep flagships distinct, manage proximity
to wholesale.

---

## 4. Curated known-competitor overrides

OSM is **unreliable for mono-brand competitor stores** — for example, the active Adidas
Flagship at Bahnhofstrasse 56 doesn't appear in the OSM `shop=*` extract for Zurich, and
the now-closed Nike Sihlcity store is also missing.

`on_brand.json` therefore includes an analyst-curated list:

- `known_competitor_locations` — mono-brand stores (Adidas, Nike, Puma) with
  `status: "active"` / `"closed"` and `as_of` date. Only `active` entries contribute to
  the score; closed entries are kept for analyst context and presentation narrative.
- `multi_brand_known_locations` — Foot Locker (3×), Snipes (3×), Titolo. Always active.

Both lists are merged into the POI set by `classify_pois` when the candidate is within
the search radius. They render on the map with a `(curated)` suffix in the popup, so the
analyst can tell OSM detections from manual entries.

**Zurich-specific findings (May 2026):**

| Brand | Status | Note |
|---|---|---|
| Adidas Bahnhofstrasse 56 | Active | The only active mono-brand competitor flagship in Zürich city |
| Nike Sihlcity | Closed March 2026 | Material analyst signal — Sihlcity has shed competitor flagships |
| Puma Sihlcity | Closed November 2025 | Reinforces the Sihlcity signal |
| Hoka / NB / Asics / Brooks / Saucony / Salomon | No mono-brand in city | All wholesale-only via SportX, Och Sport, Run Store etc. |

---

## 5. Cluster penalties

Additional penalties when negative environment is **dominant**, not just present:

| Cluster | Trigger | Penalty |
|---|---|---|
| Fast-food cluster | ≥ 3 `amenity=fast_food` within 500 m | **−8** |
| Nightlife cluster | ≥ 4 `amenity=bar`/`nightclub` within 500 m | **−8** |
| Discounter cluster | ≥ 3 negative-brand POIs within 500 m | **−10** |

Cluster penalties are **additive to** the per-POI negatives — Langstrasse hits all three
simultaneously.

---

## 6. Where each category contributes to the Site Score

Dimension weights (sidebar-editable):

| Dimension | Weight | What goes in |
|---|---|---|
| Demographie | 20 % | BFS population / Kaufkraft / age / growth |
| Accessibility | 15 % | Walk Score + transit |
| Sportstätten | 20 % | Sport facilities (`category=sport`) raw sum, log-saturated |
| Symbiose | 15 % | All `symbiose` POIs (incl. value sport, running specialists) |
| Konkurrenz | 15 % | All `competitor` POIs (incl. multi-brand specialty), with saturation |
| Premium Environment | 10 % | PEI 0-10 = hotels(5★) + premium POIs + Kaufkraft |
| Negativ | 5 % | All `negative` POIs (incl. mass-market shoe, fast-food) + cluster penalties |

Each dimension is normalised 0–100 BEFORE the weighted sum (Glass Box). After the weighted
sum:

- **Flood penalty**: −5 / −10 / −20 by BAFU class, post-aggregation
- **Sustainability Δ** (Bechtiger 2024 / ESI): accessibility, daylight, ÖV class — additive
  outside the 100-pt base score

---

---

## 6.1 Walk Score saturation tuning (May 2026)

The amenity-richness Walk Score (`utils/walk.py`) per-category formula
was retuned with empirical Zurich data:

- **Old:** `100 · (1 − exp(−s / 6))` — saturates at 100 above s ≈ 30
- **New:** `min(100, 13 · ln(1 + s))` — log scaling

The retune was necessary because central Altstadt has category sums in
the **600-1000 range** (Limmatquai food = 943, Bahnhofstrasse food = 1086).
The old curve hit 100 for every Zurich location.

Reference values after retune (1 km radius):

| Location | Walk | Story |
|---|---|---|
| Bahnhofstrasse 25 | **72** | premium-retail axis |
| Niederdorfstrasse 21 | **71** | Altstadt pedestrian zone |
| Limmatquai 28 | **71** | On's existing flagship |
| Hardturmstrasse 183 | **51** | On Labs HQ, Zürich West |
| Rosengartenstrasse 21 | **30** | Wipkingen periphery |

Transit Score is unchanged — central Zurich legitimately maxes out at
100 (Hauptbahnhof + dense tram network).

---

## 7. Leasehold Pro-Forma (Step 6 — *not* a property purchase)

On runs a **DTC strategy on leased premises** — the tool models a
leasehold cashflow, not a real-estate purchase. Year-0 CAPEX is fit-out
spend (typical CHF 4'000/m²), recovered through EBITDA over the lease
term.

Industry-standard KPIs surfaced alongside NPV/IRR:

| KPI | Formula | Benchmark |
|---|---|---|
| **Occupancy Cost Ratio** | (Rent + Insurance) / Revenue | <10% healthy · 10-15% stretched · >15% rent too high |
| **Cash-on-Cash Return (per year)** | FCF[y] / Year-0 CAPEX | >20% Y1 = fit-out pays back fast |
| **Total rent over lease horizon** | Σ rent[y] | Gross lease commitment |
| **Break-even sales (per year)** | (Rent + Insurance + OPEX) / EBITDA margin | Y1 revenue assumption should exceed this with comfortable margin |
| **Rent escalation** | Annual % uplift applied to rent[y] | Swiss commercial leases typically 1-3% (index-linked LIK) |

OCR ≥15% triggers a red warning ("rent too high for a premium retail
P&L"). OCR in the 10-15% band triggers a softer "stretched" info.

---

*Last updated 2026-05-27.*
