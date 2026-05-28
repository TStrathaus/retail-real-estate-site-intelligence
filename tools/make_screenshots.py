"""Drive the running Streamlit app with Playwright and capture
analyst-ready screenshots for the product-overview PPT.

Prereqs:
  • Streamlit running on http://localhost:8765
  • `pip install playwright` + `python -m playwright install chromium`

Run:
  python tools/make_screenshots.py
Outputs PNGs to outputs/screenshots/.
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
OUT = Path("outputs/screenshots")
OUT.mkdir(parents=True, exist_ok=True)

VIEW_W, VIEW_H = 1600, 1100


def _wait(page: Page, ms: int) -> None:
    page.wait_for_timeout(ms)


def _shot(page: Page, name: str, full_page: bool = True) -> None:
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), full_page=full_page)
    print(f"  → {path.name}  ({path.stat().st_size // 1024} KB)")


def _click_button_with_text(page: Page, text: str, *, exact: bool = False) -> bool:
    """Click the first button whose visible text contains `text`."""
    try:
        if exact:
            sel = f'button:text-is("{text}")'
        else:
            sel = f'button:has-text("{text}")'
        page.locator(sel).first.click(timeout=8000)
        _wait(page, 800)
        return True
    except Exception as e:
        print(f"  ! button '{text}' click failed: {type(e).__name__}")
        return False


def _toggle_checkbox(page: Page, label_text: str, on: bool = True) -> bool:
    """Click the visible checkbox label — Streamlit toggles state on label click."""
    try:
        # Locate by text, then go up to the checkbox wrapper, click input
        lbl = page.locator(f'label:has-text("{label_text}")').first
        lbl.scroll_into_view_if_needed(timeout=4000)
        # Check current state, only toggle if mismatched
        cb = lbl.locator('input[type="checkbox"]')
        is_checked = cb.is_checked()
        if is_checked != on:
            lbl.click(timeout=4000)
            _wait(page, 500)
        return True
    except Exception as e:
        print(f"  ! toggle '{label_text}' failed: {type(e).__name__}")
        return False


def main() -> None:
    print(f"Launching Chromium · viewport {VIEW_W}×{VIEW_H} · target {APP_URL}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": VIEW_W, "height": VIEW_H},
            device_scale_factor=1.5,
        )
        page = context.new_page()
        page.set_default_timeout(45000)

        print("Loading app …")
        page.goto(APP_URL, wait_until="networkidle", timeout=60000)
        _wait(page, 3000)

        # Slide 2 — Step 1 starting state
        print("\n[1/8] Step 1 · starting state")
        _shot(page, "01_step1_starting", full_page=False)

        # Use Playwright's .press_sequentially() with React-native event
        # dispatch via evaluate — Streamlit's text-input commits when an
        # `input` event fires (the React-tracked event), not on .fill().
        print(f"\n[2/8] Setting address via React-native input event: {ADDRESS!r}")
        try:
            page.evaluate(
                """(addr) => {
                    const input = document.querySelector(
                        'input[aria-label="Address, city, or postal code"]'
                    );
                    if (!input) return false;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, "value"
                    ).set;
                    setter.call(input, addr);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.blur();
                    return true;
                }""",
                ADDRESS,
            )
            _wait(page, 4000)   # wait for Streamlit rerun
            try:
                addr_now = page.locator('input[aria-label="Address, city, or postal code"]').first.input_value()
                print(f"  address committed: {addr_now!r}")
            except Exception:
                pass

            geocode_btn = page.get_by_role("button", name="🔍 Geocode site").first
            geocode_btn.scroll_into_view_if_needed()
            _wait(page, 600)
            geocode_btn.click(timeout=8000)
            print("  Geocode clicked")
        except Exception as e:
            print(f"  ! address/geocode path failed: {type(e).__name__}: {e}")
        _wait(page, 4000)
        _shot(page, "_debug_after_geocode_click", full_page=False)

        # Eager analyses run after geocode. Wait up to 240s, polling
        # for spinners to clear AND iframe (map) to be present.
        print("Waiting up to 240s for Step 2-4 eager fetches …")
        spinner_sel = '[data-testid="stSpinner"]'
        for i in range(48):
            _wait(page, 5000)
            try:
                spinner_count = page.locator(spinner_sel).count()
                iframe_count = page.locator("iframe").count()
                if i % 4 == 0:
                    print(f"  +{(i+1)*5}s: spinners={spinner_count} iframes={iframe_count}")
                if iframe_count > 0 and spinner_count == 0:
                    _wait(page, 5000)
                    break
            except Exception:
                pass
        _wait(page, 6000)
        _shot(page, "_debug_after_eager", full_page=False)

        # Slide 3 — Step 1 with flood + hotels
        print("\n[3/8] Step 1 · with overlays on")
        _toggle_checkbox(page, "BAFU flood", on=True)
        _toggle_checkbox(page, "Hotels (curated)", on=True)
        _wait(page, 4500)
        _shot(page, "02_step1_location", full_page=True)

        # Slide 4 — Step 2 Market
        print("\n[4/8] Step 2 · Market")
        _click_button_with_text(page, "Market")
        _wait(page, 3000)
        _shot(page, "03_step2_market", full_page=True)

        # Slide 5 — Step 3 Proximity
        print("\n[5/8] Step 3 · Proximity")
        _click_button_with_text(page, "Proximity")
        _wait(page, 5000)
        # Turn on the analytical mix
        _toggle_checkbox(page, "BAFU flood", on=True)
        _toggle_checkbox(page, "Hotels (curated)", on=True)
        _toggle_checkbox(page, "Cannibalization overlap", on=True)
        _toggle_checkbox(page, "Fine-grained POIs", on=True)
        _wait(page, 4500)
        _shot(page, "04_step3_proximity", full_page=True)

        # Slide 6 — Step 4 Footfall
        print("\n[6/8] Step 4 · Footfall")
        _click_button_with_text(page, "Footfall")
        _wait(page, 5000)
        _shot(page, "05_step4_footfall", full_page=True)

        # Slide 7 — Step 5 Score
        print("\n[7/8] Step 5 · Score")
        _click_button_with_text(page, "Score")
        _wait(page, 8000)
        _shot(page, "06_step5_score", full_page=True)

        # Slide 8 — Step 6 Pro-Forma
        print("\n[8/8] Step 6 · Pro-Forma + Step 7 · Export")
        _click_button_with_text(page, "Pro-Forma")
        _wait(page, 5000)
        _shot(page, "07_step6_proforma", full_page=True)

        _click_button_with_text(page, "Export")
        _wait(page, 2500)
        _shot(page, "08_step7_export", full_page=True)

        browser.close()
        files = sorted(OUT.glob("*.png"))
        total_kb = sum(f.stat().st_size for f in files) // 1024
        print(f"\nDone. {len(files)} screenshots · {total_kb} KB total · {OUT}")


if __name__ == "__main__":
    main()
