"""Measure the public site's rendered pages in headless Chromium.

Run as a script, not imported: the sync Playwright API leaves an event loop installed in its own
process, which would break the ``asyncio.run`` calls the rest of the suite makes in the main one.

    python tests/site_visual_probe.py <page-dir> <shot-dir>

``<page-dir>`` holds ``overview.html``, ``leaderboard.html`` and a ``static/`` link to the shipped
assets. The measurements for both widths are printed as one JSON object; a missing browser answers
``{"unavailable": "<reason>"}`` so the test can skip instead of failing on tooling.
"""
from __future__ import annotations

import functools
import http.server
import json
import sys
import threading
from pathlib import Path

#: The pages a caller may render, by the file name it writes them under.
PAGES = ("overview", "leaderboard")

#: The two widths the design note is measured at.
SIZES = ((1440, 900), (430, 844))

#: Values read off one page: resolved type sizes, the popover's placement, the native list's ground.
PROBE = """(name) => {
  const style = (selector) => {
    const node = document.querySelector(selector);
    return node ? getComputedStyle(node) : null;
  };
  const size = (selector) => { const s = style(selector); return s ? parseFloat(s.fontSize) : null; };
  const leafSizes = [...document.querySelectorAll('body *')]
    .filter((node) => node.children.length === 0 && node.textContent.trim() &&
                      getComputedStyle(node).display !== 'none')
    .map((node) => parseFloat(getComputedStyle(node).fontSize));
  const headline = document.querySelector('.hero h1');
  const headlineBox = headline ? headline.getBoundingClientRect() : null;
  const labelStarts = [...document.querySelectorAll('.ctlrow .ctl-lbl')]
    .map((node) => Math.round(node.getBoundingClientRect().left));
  const chipStarts = [...document.querySelectorAll('.ctlrow .seg')]
    .map((node) => Math.round(node.getBoundingClientRect().left));
  const option = [...document.querySelectorAll('.pickbar .pick option')].find((o) => !o.selected);
  const language = {open: false};
  const chip = document.querySelector('.langchip');
  const menu = document.querySelector('.langmenu');
  if (chip && menu) {
    chip.click();
    const box = menu.getBoundingClientRect();
    const pill = chip.getBoundingClientRect();
    const computed = getComputedStyle(menu);
    language.open = menu.matches(':popover-open');
    language.position = computed.position;
    language.top = box.top;
    language.right = box.right;
    language.height = box.height;
    language.pillTop = pill.top;
    language.pillBottom = pill.bottom;
    language.pillRight = pill.right;
  }
  return {
    page: name,
    hero: size('.hero h1'),
    heroWidth: headlineBox ? Math.round(headlineBox.width) : null,
    heroRight: headlineBox ? Math.round(headlineBox.right) : null,
    heroHeight: headlineBox ? Math.round(headlineBox.height) : null,
    body: parseFloat(getComputedStyle(document.body).fontSize),
    sectionHeading: size('.sect-head h2'),
    panelHeading: size('.panel > .hd h2'),
    factsSize: size('.liveband .facts span'),
    factsFamily: (style('.liveband .facts span') || {}).fontFamily || null,
    pickSize: size('.pickbar .pick'),
    pickFamily: (style('.pickbar .pick') || {}).fontFamily || null,
    optionBackground: option ? getComputedStyle(option).backgroundColor : null,
    optionColour: option ? getComputedStyle(option).color : null,
    barValues: document.querySelectorAll('.bar-val').length,
    bars: document.querySelectorAll('.chart .bar').length,
    smallestText: leafSizes.length ? Math.min(...leafSizes) : null,
    underThirteen: leafSizes.filter((value) => value < 13).length,
    labelStarts: labelStarts,
    chipStarts: chipStarts,
    activeControls: document.querySelectorAll('.ctlrow .seg a.on').length,
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    language: language
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
    except ImportError as error:  # the sheet's own contract still holds without a browser
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
            for name in PAGES:
                if not (directory / f"{name}.html").exists():
                    continue
                for width, height in SIZES:
                    page = browser.new_page(viewport={"width": width, "height": height},
                                            device_scale_factor=2)
                    page.goto(f"{base}/{name}.html", wait_until="load")
                    page.wait_for_timeout(250)
                    measured = page.evaluate(PROBE, name)
                    measured["width"], measured["height"] = width, height
                    page.screenshot(path=str(shots / f"{name}-fold-{width}x{height}.png"))
                    page.screenshot(path=str(shots / f"{name}-full-{width}x{height}.png"),
                                    full_page=True)
                    if name == "overview":
                        if measured["language"].get("open"):
                            page.screenshot(
                                path=str(shots / f"{name}-langopen-fold-{width}x{height}.png"))
                        page.keyboard.press("Escape")
                        page.locator(".liveband").screenshot(
                            path=str(shots / f"liveband-{width}.png"))
                    if name == "overview" and width == 1440:
                        # the picker submits on change, and its noscript button does so with scripting off
                        with page.expect_navigation():
                            page.select_option(".pickbar .pick", index=1)
                        measured["submittedOnChange"] = page.url
                        context = browser.new_context(java_script_enabled=False,
                                                      viewport={"width": width, "height": height})
                        plain = context.new_page()
                        plain.goto(f"{base}/{name}.html", wait_until="load")
                        plain.select_option(".pickbar .pick", index=1)
                        with plain.expect_navigation():
                            plain.click(".pickbar button[type=submit]")
                        measured["submittedWithoutScript"] = plain.url
                        context.close()
                    report.append(measured)
                    page.close()
            browser.close()
    finally:
        server.shutdown()
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
