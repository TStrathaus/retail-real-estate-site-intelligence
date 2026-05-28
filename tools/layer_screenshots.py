"""Capture one JPG per Proximity-tab (Step 3) overlay layer, in isolation,
for a fixed demo location. Each shot = base map (candidate · radius · POI
markers) + exactly one overlay enabled.

Prereqs:
  • Streamlit running on http://localhost:8765
  • Playwright + Chromium installed

Run:
  python tools/layer_screenshots.py
Outputs JPGs to outputs/proximity_layers/.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.sync_api import Page, sync_playwright

APP_URL = "http://localhost:8765"
ADDRESS = "Niederdorfstrasse 21, 8001 Zürich"
OUT = Path("outputs/proximity_layers")
OUT.mkdir(parents=True, exist_ok=True)

VIEW_W, VIEW_H = 1500, 1100

# Every Step 3 overlay toggle — ALL must be reset OFF between captures so
# exactly one layer is active at a time. POI markers + existing stores are
# now toggleable (previously always-on), which is what was leaking into
# every screenshot.
ALL_TOGGLES = [
    "BAFU flood", "Running infrastructure", "Pedestrian streets",
    "Shop density", "Transit stops", "All shops (generic)",
    "Cannibalization overlap", "Fine-grained POIs", "Competitor heatmap",
    "Walk-amenity heatmap", "Sport heatmap", "Hotels (curated)",
    "POI markers", "Existing On stores",
]

# (toggle-to-enable, filename-slug) — one capture each, all others OFF.
LAYERS = [
    ("BAFU flood",              "01_bafu_flood"),
    ("Running infrastructure",  "02_running_infrastructure"),
    ("Pedestrian streets",      "03_pedestrian_streets"),
    ("Shop density",            "04_shop_density"),
    ("Transit stops",           "05_transit_stops"),
    ("All shops (generic)",     "06_all_shops"),
    ("Cannibalization overlap", "07_cannibalization"),
    ("POI markers",             "08_poi_markers"),
    ("Competitor heatmap",      "09_competitor_circles"),
    ("Walk-amenity heatmap",    "10_walk_amenity_heatmap"),
    ("Sport heatmap",           "11_sport_heatmap"),
    ("Hotels (curated)",        "12_hotels"),
]


def _wait(page: Page, ms: int) -> None:
    page.wait_for_timeout(ms)


def _set_checkbox(page: Page, label_text: str, on: bool) -> bool:
    try:
        lbl = page.locator(f'label:has-text("{label_text}")').first
        lbl.scroll_into_view_if_needed(timeout=4000)
        cb = lbl.locator('input[type="checkbox"]')
        if cb.is_checked() != on:
            lbl.click(timeout=4000)
            _wait(page, 400)
        return True
    except Exception as e:
        print(f"  ! checkbox '{label_text}' ({'on' if on else 'off'}) failed: "
              f"{type(e).__name__}")
        return False


def _wait_map_ready(page: Page, max_s: int = 60) -> None:
    """Wait until spinners clear and the folium iframe is present."""
    for _ in range(max_s // 2):
        _wait(page, 2000)
        try:
            spinners = page.locator('[data-testid="stSpinner"]').count()
            iframes = page.locator("iframe").count()
            if iframes > 0 and spinners == 0:
                _wait(page, 1500)
                return
        except Exception:
            pass
    _wait(page, 2000)


def _shot_map(page: Page, slug: str) -> None:
    """Screenshot just the Step 3 folium map iframe as JPG."""
    path = OUT / f"proximity_{slug}.jpg"
    try:
        iframe = page.locator('iframe[title^="streamlit_folium"]').first
        iframe.scroll_into_view_if_needed(timeout=5000)
        _wait(page, 800)
        iframe.screenshot(path=str(path), type="jpeg", quality=88)
    except Exception:
        # Fallback — full-page JPG
        page.screenshot(path=str(path), type="jpeg", quality=88, full_page=False)
    print(f"  → {path.name}  ({path.stat().st_size // 1024} KB)")


def main() -> None:
    print(f"Launching Chromium · {APP_URL} · {ADDRESS}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": VIEW_W, "height": VIEW_H},
            device_scale_factor=1.5,
        )
        page = ctx.new_page()
        page.set_default_timeout(45000)

        page.goto(APP_URL, wait_until="networkidle", timeout=60000)
        _wait(page, 3000)

        # --- Geocode via React-native input event (Streamlit-safe) ---
        print(f"Geocoding {ADDRESS} …")
        page.evaluate(
            """(addr) => {
                const input = document.querySelector(
                    'input[aria-label="Address, city, or postal code"]');
                if (!input) return false;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, "value").set;
                setter.call(input, addr);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.blur();
                return true;
            }""",
            ADDRESS,
        )
        _wait(page, 4000)
        page.get_by_role("button", name="🔍 Geocode site").first.click(timeout=8000)
        print("Waiting for eager analyses …")
        _wait_map_ready(page, max_s=120)
        _wait(page, 4000)

        # --- Go to Proximity (Step 3) ---
        print("Opening Proximity tab …")
        page.get_by_role("button", name="Proximity").first.click(timeout=8000)
        _wait_map_ready(page, max_s=90)
        _wait(page, 3000)

        # Ensure EVERY overlay (incl. POI markers + existing stores) is OFF
        print("Resetting all overlays to OFF …")
        for label in ALL_TOGGLES:
            _set_checkbox(page, label, on=False)
        _wait_map_ready(page, max_s=60)

        # Baseline (truly clean — candidate + radius circle only)
        print("\n[baseline] candidate + radius only")
        _shot_map(page, "00_baseline")

        # One layer at a time — only the named toggle is ON for each shot
        for i, (label, slug) in enumerate(LAYERS, 1):
            print(f"\n[{i}/{len(LAYERS)}] {label}")
            _set_checkbox(page, label, on=True)
            _wait_map_ready(page, max_s=60)
            _wait(page, 1500)
            _shot_map(page, slug)
            _set_checkbox(page, label, on=False)   # back to clean baseline
            _wait(page, 1500)

        browser.close()
        files = sorted(OUT.glob("*.jpg"))
        total_kb = sum(f.stat().st_size for f in files) // 1024
        print(f"\nDone. {len(files)} JPGs · {total_kb} KB · {OUT}")


if __name__ == "__main__":
    main()
