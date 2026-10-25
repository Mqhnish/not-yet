"""Take the README screenshots from a running server.

    uvicorn notyet.app:app --port 8000 &
    python scripts/screenshots.py http://localhost:8000
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

DOCS = Path(__file__).resolve().parents[1] / "docs"


def run(url: str) -> None:
    DOCS.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        shots = [
            ("plan-light.png", {"viewport": {"width": 1360, "height": 1500}, "color_scheme": "light"}, True),
            ("plan-dark.png", {"viewport": {"width": 1360, "height": 1500}, "color_scheme": "dark"}, False),
            ("plan-mobile.png", {"viewport": {"width": 390, "height": 844}, "color_scheme": "light", "device_scale_factor": 2}, False),
        ]
        for name, opts, full in shots:
            page = browser.new_page(timezone_id="Asia/Singapore", **opts)
            page.goto(url)
            page.wait_for_selector(".chip")
            page.click("#go")
            page.wait_for_selector("#out:not(.hidden)", timeout=180_000)
            if name == "plan-mobile.png":
                page.locator(".hero").scroll_into_view_if_needed()
                page.screenshot(path=DOCS / name)
            else:
                page.screenshot(path=DOCS / name, full_page=full)
            print("wrote", DOCS / name)
            page.close()
        browser.close()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000")
