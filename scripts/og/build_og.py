"""Rasterize the social/OG card to PNG, deterministically.

Renders scripts/og/card.html in headless Chromium at 1200x630 (the standard
OpenGraph/Twitter size) and writes the same PNG to two places: the docs site
asset and the repo asset. No network, no system image tools; uses the Playwright
that the `[ui]` extra already installs.

    pip install -e ".[ui]"        # provides playwright
    playwright install chromium   # one-time
    python scripts/og/build_og.py
"""

from __future__ import annotations

import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
CARD = ROOT / "scripts" / "og" / "card.html"
# docs/public serves files as-is at a stable URL (/llmprof/og.png) for the meta
# tags; src/assets images get hashed by Astro, so they cannot be the OG target.
OUTPUTS = [
    ROOT / "docs" / "public" / "og.png",
    ROOT / "assets" / "og.png",
]
WIDTH, HEIGHT = 1200, 630


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # native 1200x630 so the PNG dimensions match the og:image:width/height meta
        page = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1
        ).new_page()
        page.goto(CARD.as_uri(), wait_until="networkidle")
        png = page.screenshot(clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
        browser.close()

    for out in OUTPUTS:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png)
        print(f"wrote {out.relative_to(ROOT)} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
