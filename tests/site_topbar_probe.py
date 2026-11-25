"""Measure the public site's rendered top bar in headless Chromium.

Run as a script, not imported: the sync Playwright API leaves an event loop installed in its own
process, which would break the ``asyncio.run`` calls the suites make in theirs.

    python tests/site_topbar_probe.py <page-dir> <shot-dir>

``<page-dir>`` holds one ``<state>.html`` per bar state plus a ``static/`` link to the shipped
assets. Every page is measured at :data:`WIDTHS`; the two design widths are also photographed. One
JSON list is printed, one row per (page, width); a missing browser answers
``{"unavailable": "<reason>"}`` so a caller can skip instead of failing on tooling.

What one row reports, per width: the bar's and the cluster's height, the door's and the name's box,
whether the door is drawn and whether its label is drawn, how many lines each string on the bar
occupies, whether the cluster reaches the nav or the door reaches the search, the document's
horizontal overflow, the nav's own scroll box, and whether the name is truncated.
"""
from __future__ import annotations

import functools
import http.server
import json
import sys
import threading
from pathlib import Path

#: The widths the bar is measured at: the two design widths and the three the note names.
WIDTHS = (1440, 1024, 900, 620, 430)

#: The two widths the design note renders at, and therefore the two that are photographed.
SHOTS = ((1440, 900), (430, 844))

PROBE = """() => {
  const q = (s) => document.querySelector(s);
  const rect = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
            r: r.right, b: r.bottom, cx: r.x + r.width / 2, cy: r.y + r.height / 2}; };
  const drawn = (el) => !!el && getComputedStyle(el).display !== 'none' && el.getClientRects().length > 0;
  const lines = (el) => { if (!el || !drawn(el)) return 0;
    const range = document.createRange(); range.selectNodeContents(el);
    const tops = new Set([...range.getClientRects()].filter(r => r.width > 0.5)
      .map(r => Math.round(r.top)));
    return tops.size; };
  const over = (a, b) => !!a && !!b && a.x < b.r - 0.5 && b.x < a.r - 0.5 &&
    a.y < b.b - 0.5 && b.y < a.b - 0.5;

  const bar = q('.top .in');
  const nav = q('.top .nav');
  const search = q('.top .search');
  const who = q('.top .who a');
  const door = q('.top .btn-door');
  const lang = q('.top .langchip');
  const cluster = q('.top .cluster');
  const name = q('.top .who a .nm');
  const label = q('.top .btn-door .door-lbl');

  const sizes = [...document.querySelectorAll('.top *')]
    .filter(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
    .filter(e => drawn(e))
    .map(e => parseFloat(getComputedStyle(e).fontSize));

  const doorBox = rect(door);
  const nameBox = rect(name);
  return {
    doc: {scrollW: document.documentElement.scrollWidth,
          clientW: document.documentElement.clientWidth},
    barH: bar ? Math.round(bar.getBoundingClientRect().height) : null,
    clusterH: cluster ? Math.round(cluster.getBoundingClientRect().height) : null,
    boxes: {bar: rect(bar), cluster: rect(cluster), who: rect(who), door: doorBox,
            name: nameBox, lang: rect(lang), search: rect(search), nav: rect(nav),
            brand: rect(q('.top .brand')), brandSpan: rect(q('.top .brand > span'))},
    heights: {who: who ? Math.round(who.getBoundingClientRect().height) : null,
              door: doorBox ? doorBox.h : null,
              lang: lang ? Math.round(lang.getBoundingClientRect().height) : null,
              search: search ? Math.round(search.getBoundingClientRect().height) : null},
    // the three controls' centre lines: one value means one band
    centres: [who, door, lang].filter(drawn).map(el => el.getBoundingClientRect().top),
    lines: {
      brand: lines(q('.top .brand b')),
      brandSub: lines(q('.top .brand small')),
      nav: Math.max(0, ...[...document.querySelectorAll('.top .nav a')].map(lines)),
      door: lines(label),
      name: lines(name),
      search: lines(q('.top .search input')),
    },
    nameClipped: name ? name.scrollWidth > name.clientWidth + 1 : null,
    nameText: name ? name.textContent.trim() : null,
    clusterHitsNav: over(rect(cluster) || rect(who), nav),
    doorHitsSearch: over(doorBox, search),
    navScroll: nav ? {clientW: Math.round(nav.clientWidth), scrollW: Math.round(nav.scrollWidth)}
                   : null,
    minText: sizes.length ? Math.min(...sizes) : null,
    hasDoor: drawn(door),
    doorLabel: drawn(label),
    doorName: door ? (door.getAttribute('aria-label') || door.textContent.trim()) : null,
    doorTitle: door ? door.getAttribute('title') : null,
    doorRadius: door ? getComputedStyle(door).borderRadius : null,
    hasSignIn: !!(door && /sign/i.test(door.textContent) && !door.querySelector('.ico')),
    whoHref: who ? who.getAttribute('href') : null,
    topH: getComputedStyle(document.documentElement).getPropertyValue('--top-h').trim(),
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
    pages = sorted(path.stem for path in directory.glob("*.html"))
    server, base = _serve(directory)
    report: list[dict] = []
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch()
            for name in pages:
                for width in WIDTHS:
                    height = 844 if width <= 430 else 900
                    page = browser.new_page(viewport={"width": width, "height": height},
                                            device_scale_factor=2)
                    page.goto(f"{base}/{name}.html", wait_until="load")
                    page.wait_for_timeout(200)
                    measured = page.evaluate(PROBE)
                    measured["page"], measured["width"] = name, width
                    measured["height"] = height
                    if (width, height) in SHOTS:
                        page.screenshot(path=str(shots / f"{name}-fold-{width}x{height}.png"))
                        page.locator(".top").screenshot(
                            path=str(shots / f"{name}-bar-{width}x{height}.png"))
                    if width == 1440:
                        page.screenshot(path=str(shots / f"{name}-full-{width}x{height}.png"),
                                        full_page=True)
                    report.append(measured)
                    page.close()
            browser.close()
    finally:
        server.shutdown()
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
