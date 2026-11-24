"""Measure the hero's mark in headless Chromium.

Run as a script, not imported: the sync Playwright API leaves an event loop installed in its own
process, which would break the ``asyncio.run`` calls the rest of the suite makes in the main one.

    python tests/site_hero_probe.py <page-dir> <shot-dir>

``<page-dir>`` holds the rendered ``hero-*.html`` documents and a ``static/`` link to the shipped
assets. Every document is measured at the three widths the size ladder names; the rows are printed
as one JSON array. A missing browser answers ``{"unavailable": "<reason>"}`` so the test can skip
instead of failing on tooling.
"""
from __future__ import annotations

import functools
import http.server
import json
import sys
import threading
from pathlib import Path

#: The three widths the ladder is stated at.
SIZES = ((1440, 900), (900, 900), (430, 844))

#: Values read off one hero: the mark's box and the hero's own height.
PROBE = """() => {
  const hero = document.querySelector('.hero');
  const inner = document.querySelector('.hero .in');
  const mark = document.querySelector('.hero-mark img');
  const box = mark ? mark.getBoundingClientRect() : null;
  const heroBox = hero ? hero.getBoundingClientRect() : null;
  const rect = (selector) => {
    const node = document.querySelector(selector);
    if (!node) { return null; }
    const r = node.getBoundingClientRect();
    return {top: Math.round(r.top * 100) / 100, height: Math.round(r.height * 100) / 100};
  };
  return {
    heroHeight: heroBox ? Math.round(heroBox.height * 100) / 100 : null,
    innerHeight: inner ? Math.round(inner.getBoundingClientRect().height * 100) / 100 : null,
    eyebrow: rect('.hero .eyebrow'),
    headline: rect('.hero h1'),
    foot: rect('.hero-foot'),
    markWidth: box ? Math.round(box.width * 100) / 100 : null,
    markHeight: box ? Math.round(box.height * 100) / 100 : null,
    markTop: box ? Math.round(box.top * 100) / 100 : null,
    markLeft: box ? Math.round(box.left * 100) / 100 : null,
    markRight: box ? Math.round(box.right * 100) / 100 : null,
    innerClass: inner ? inner.className : null,
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth
  };
}"""


def _serve(directory: Path):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Quiet, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def main(argv: list[str]) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        print(json.dumps({"unavailable": f"playwright: {error}"}))
        return 0

    directory = Path(argv[1])
    shots = Path(argv[2])
    shots.mkdir(parents=True, exist_ok=True)
    server, base = _serve(directory)
    report: list[dict] = []
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch()
            for path in sorted(directory.glob("hero-*.html")):
                for width, height in SIZES:
                    page = browser.new_page(viewport={"width": width, "height": height},
                                            device_scale_factor=2)
                    page.goto(f"{base}/{path.name}", wait_until="load")
                    page.wait_for_timeout(200)
                    measured = page.evaluate(PROBE)
                    measured["page"] = path.stem
                    measured["width"], measured["height"] = width, height
                    page.screenshot(path=str(shots / f"{path.stem}-{width}x{height}.png"))
                    report.append(measured)
                    page.close()
            browser.close()
    finally:
        server.shutdown()
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
