"""Drive the Site settings page in headless Chromium and report what the page did.

Run as a script, not imported: Playwright's sync API leaves an event loop installed in its own
process, which would break the ``asyncio.run`` calls the suite makes in the main one.

    python tests/site_settings_probe.py <base-url> <user> <password> <page> <phase> <site-key>

``<page>`` is the settings page's address under the console's mount and ``<phase>`` is what the run
does once it is signed in and looking at that page:

* ``add``    — count the rows of the three generated lists, press each ``+ Add``, click one preset
               tile, then fill the added footer link and press Save;
* ``bind``   — type into ``community_name`` and read the preview's own hero back, reporting every
               request the page made while typing;
* ``identity`` — read the card's header line, then press its switch off and on again, reporting the
               body ``new FormData(form)`` carries in each state and whether the card stayed open;
* ``character`` — press PvP, read the fourteen switches, the chip and the sentence back; move one
               switch by hand and read the divergence line; press PvP again and read it cleared.
* ``remove`` — press the trashcan of the footer link whose ``href`` is ``/imprint`` and press Save.
* ``twocard`` — type into, then edit the palette of, ONE card on a page that draws two, reporting
               every card's own frame before and after each edit;
* ``palettepaint`` — press every preset tile, then type two declarations into the expert sheet,
               reading the FRAME's computed palette back after each step.

One JSON object is printed on stdout. A machine without a browser answers ``{"unavailable": ...}``
so the test can skip instead of failing on tooling.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time

#: the label and target the ``add`` phase writes into the row it added.
LINK_LABEL = "Imprint"
LINK_HREF = "/imprint"

#: the community name the ``bind`` phase types: the design's own example.
TYPED_NAME = "5th Guards"

#: the bar's name the ``twocard`` phase types into ``branding.title``.
TYPED_BAR = "Foothold Wing Bar"

#: the community name the ``twocard`` phase types into the SECOND card: nothing the fixture's own
#: site file already renders, so a frame that did not follow reads its old name back.
TYPED_SECOND = "Night Wings"

#: the ONE list the page still generates — it is the only block that takes new entries.
LISTS = {
    "footer_links": '[data-cfg-seq="sites.{site}.branding.footer_links"]',
}

#: the two CLOSED sets, by the leaf they draw. They list every id of the vocabulary always, so they
#: carry no Add and no remove: the block is the set.
CLOSED_SETS = {
    "nav": '[data-sc-set="nav"]',
    "tiles": '[data-sc-set="tiles"]',
}


def _sign_in(page, base: str, user: str, password: str) -> None:
    page.goto(f"{base}/auth/login", wait_until="load")
    page.fill('input[name="username"]', user)
    page.fill('input[name="password"]', password)
    with page.expect_navigation():
        page.click('button[type="submit"]')


def _card(page, site: str):
    """The card whose hidden site field names *site*."""
    return page.locator(f'form.sc-form:has(input[value="{site}"])').first


def _add_counts(page, site: str) -> dict:
    counts = {}
    for name, template in LISTS.items():
        container = page.locator(template.format(site=site)).first
        before = container.locator("[data-cfg-row]").count()
        container.locator("[data-cfg-add]").first.click()
        page.wait_for_timeout(80)
        counts[name] = [before, container.locator("[data-cfg-row]").count()]
    return counts


def _closed_set_controls(page, site: str) -> dict:
    """The controls each CLOSED set must NOT carry, counted inside ONE card."""
    card = _card(page, site)
    return {name: {"add": card.locator(f"{template} [data-cfg-add]").count(),
                   "remove": card.locator(f"{template} [data-cfg-remove]").count(),
                   "rows": card.locator(f"{template} [data-reorder-row]").count()}
            for name, template in CLOSED_SETS.items()}


def _preset_click(page, site: str) -> dict:
    """Click ONE preset tile and report everything a blank page would show up in.

    The page records its OWN ``submit`` events (a capture-phase listener on ``document``), so the pin
    can say "no submit fired" rather than inferring it from a request log that a stray coordinate
    could pollute.
    """
    card = _card(page, site)
    page.evaluate("""() => {
        window.__submits = [];
        document.addEventListener('submit', (event) => window.__submits.push(
            event.submitter ? event.submitter.tagName + '.' + (event.submitter.className || '')
                            : 'no-submitter'), true);
    }""")
    # bring the tile into view FIRST: the scroll that reveals it is not part of the press, and the
    # window's own scroll position is what the press must leave alone (a pane may scroll; the
    # document must not, or the viewport lands on the empty area below the page's content).
    tile = card.locator("label.sc-tile", has_text="Brass").first
    tile.scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    before = {
        "url": page.url,
        "text": len(page.inner_text("body")),
        "html": len(page.content()),
        "scrollY": page.evaluate("() => window.scrollY"),
    }
    tile.click()
    page.wait_for_timeout(400)
    checked = page.eval_on_selector_all(
        f'input[name="f.sites.{site}.theme.preset"]:checked', "nodes => nodes.map(n => n.value)")
    submits = page.evaluate("() => window.__submits")
    after = {
        "url": page.url,
        "text": len(page.inner_text("body")),
        "html": len(page.content()),
        "scrollY": page.evaluate("() => window.scrollY"),
    }
    # back to the shipped palette: this run's save must not carry a palette nobody asked for
    page.locator("label.sc-tile").first.click()
    page.wait_for_timeout(80)
    return {"before": before, "after": after, "checked": checked, "submits": submits}


def _character(page, site: str) -> dict:
    """Press one character pill and read the two lists, the chip, the sentence and the requests back.

    A press is a CLIENT-SIDE rewrite of controls the server already reads, so the pin is twofold: the
    fourteen switches and the chip must follow the press, and NOT ONE request may leave the page. A
    switch moved by hand afterwards must be shown as a divergence, and pressing again must clear it.
    """
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    requests: list[str] = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    pressed = {"section": "input[data-sc-section]:checked",
               "metric": "input[data-sc-metric]:checked"}

    def state() -> dict:
        return {
            "sections": card.locator(pressed["section"]).evaluate_all(
                "nodes => nodes.map((n) => n.getAttribute('data-sc-section'))"),
            "metrics": card.locator(pressed["metric"]).evaluate_all(
                "nodes => nodes.map((n) => n.getAttribute('data-sc-metric'))"),
            "chip": card.locator("[data-sc-character-chip]").first.text_content(),
            "count": card.locator("[data-sc-character-count]").first.text_content(),
            "what": card.locator("[data-sc-character-what]").first.text_content(),
            "whySections": card.locator("[data-sc-why-sections]").first.text_content(),
            "whyMetrics": card.locator("[data-sc-why-metrics]").first.text_content(),
            "changedHidden": card.locator("[data-sc-character-changed]").first.is_hidden(),
            "changed": card.locator("[data-sc-character-changed]").first.text_content(),
        }

    pill = card.locator('label.sc-pill:has(input[data-sc-character="pvp"])').first
    pill.scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    pill.click()
    page.wait_for_timeout(300)
    report = {"pressed": state(), "requests": requests}

    # a hand edit after the press: turn one withdrawn section back on
    box = card.locator('input[data-sc-section="season"]').first
    box.click()
    page.wait_for_timeout(250)
    report["edited"] = state()

    # pressing the SAME character again clears the line (the press may not be a no-op listener)
    card.locator('label.sc-pill:has(input[data-sc-character="pvp"])').first.click()
    page.wait_for_timeout(250)
    report["again"] = state()
    report["requests"] = requests
    return report


def _row_ids(card, selector: str) -> list:
    """The block's rows' ids, in DOM order, inside ONE card."""
    return card.locator(f"{selector} [data-reorder-row]").evaluate_all(
        "nodes => nodes.map((n) => n.getAttribute('data-reorder-id'))")


def _ord_numbers(card, selector: str) -> dict:
    """What each row's order cell carries: its own text, and the content the sheet asks it to draw.

    The number is a CSS counter, so the element holds no text and the counter expression is the
    sheet's own — which is why the pin also reads the PAINTED cell (``_ord_shot``).
    """
    return card.locator(f"{selector} [data-reorder-row] .ord").evaluate_all(
        "nodes => nodes.map((n) => ({text: n.textContent, "
        "content: getComputedStyle(n, '::before').content}))")


def _ord_shot(card, selector: str, rid: str) -> str:
    """A digest of the PIXELS of one row's order cell — the only way to read a counter's number."""
    cell = card.locator(f'{selector} [data-reorder-row][data-reorder-id="{rid}"] .ord').first
    return hashlib.sha256(cell.screenshot()).hexdigest()[:16]


def _drag_row(page, card, selector: str, source: int, target: int) -> None:
    """Drag the row at *source* so it lands above the row at *target* — pointer events on its grip."""
    rows = card.locator(f"{selector} [data-reorder-row]")
    grip = rows.nth(source).locator("[data-reorder-handle]").first
    grip.scroll_into_view_if_needed()
    page.wait_for_timeout(80)
    start = grip.bounding_box()
    landing = rows.nth(target).bounding_box()
    page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
    page.mouse.down()
    page.mouse.move(start["x"] + start["width"] / 2, landing["y"] + 2, steps=10)
    page.wait_for_timeout(80)
    page.mouse.up()
    page.wait_for_timeout(250)


def _reorder(page, site: str) -> dict:
    """Drag row 3 above row 1, then Alt+Down on the focused grip — then Save and read the order back.

    A LOCAL reorder posts nothing: the rows live in the card's form, so the DOM order after the move
    IS the payload. The pin is the order before, after the drag and after the key press, the pixels of
    one row's order cell in each state (the number is a CSS counter), the values the form would post,
    and every request the page made WHILE the rows moved.
    """
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(400)
    selector = CLOSED_SETS["nav"]
    # the LOCAL list reports every move: the container's bubbling event is how the page's own script
    # would repaint anything that draws the order
    page.evaluate("() => { window.__moves = []; "
                  "document.addEventListener('reorder-moved', "
                  "(event) => window.__moves.push(event.detail.ids), false); }")
    requests: list = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    page.wait_for_timeout(200)
    # everything the page loaded to render itself is not a request the MOVE made
    baseline = len(requests)

    def state() -> dict:
        return {
            "ids": _row_ids(card, selector),
            "ord": _ord_numbers(card, selector),
            "ord_squadrons": _ord_shot(card, selector, "squadrons"),
            "posted": card.locator(f'input[name="f.sites.{site}.nav"]:checked').evaluate_all(
                "nodes => nodes.map((n) => n.value)"),
            "status": card.locator("[data-reorder-status]").count(),
            "dragging": page.locator(".reorder-dragging").count(),
        }

    report = {"before": state()}
    _drag_row(page, card, selector, 2, 0)
    report["after_drag"] = state()

    grip = card.locator(f"{selector} [data-reorder-row]").first.locator("[data-reorder-handle]").first
    grip.focus()
    page.keyboard.press("Alt+ArrowDown")
    page.wait_for_timeout(250)
    report["after_key"] = state()
    report["requests"] = requests[baseline:]
    report["moves"] = page.evaluate("() => window.__moves")
    report["saved"] = _save(page)
    page.wait_for_timeout(300)
    reloaded = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    report["after_save"] = {
        "ids": _row_ids(reloaded, selector),
        "posted": reloaded.locator(f'input[name="f.sites.{site}.nav"]:checked').evaluate_all(
            "nodes => nodes.map((n) => n.value)"),
    }
    report["tiles_parked"] = reloaded.locator(f'{CLOSED_SETS["tiles"]} [data-reorder-row]').evaluate_all(
        "nodes => nodes.filter((n) => n.classList.contains('parked'))"
        ".map((n) => n.getAttribute('data-reorder-id'))")
    return report


def _draft(page, site: str) -> dict:
    """Uncheck one nav entry and read the frame back: the DEMO follows the form within about a second.

    A nav entry is the discriminating field: the local paint can only HIDE an anchor, so an anchor the
    server never rendered is the proof that the box was re-read from the draft. The elapsed time is
    measured from the press to the moment the anchor is gone from the frame's own document.
    """
    page.evaluate("() => document.querySelectorAll('details.sc-card').forEach((d) => { d.open = true; })")
    page.wait_for_timeout(400)
    card = _card(page, site)
    frame = page.frame_locator(f'form.sc-form:has(input[value=\"{site}\"]) iframe[data-sc-preview]')
    selector = '.top .nav a[href$="/rankings"]'
    before = frame.locator(selector).count()
    box = card.locator(f'input[name="f.sites.{site}.nav"][value="leaderboard"]').first
    box.scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    started = time.perf_counter()
    box.uncheck()
    elapsed = None
    deadline = started + 3.0
    while time.perf_counter() < deadline:
        if frame.locator(selector).count() == 0:
            elapsed = int((time.perf_counter() - started) * 1000)
            break
        page.wait_for_timeout(50)
    after = frame.locator(selector).count()
    return {"before": before, "after": after, "elapsed_ms": elapsed, "requests": requests,
            "checked_nav": card.locator(f'input[name="f.sites.{site}.nav"]:checked').evaluate_all(
                "nodes => nodes.map((n) => n.value)")}


def _switch(page, site: str) -> dict:
    """Flip a SWITCHED-OFF site on, then off: the box mounts the frame, then states the off-state.

    The reported symptom is the first half — a switch that changes nothing in the box — so the pin
    reads the panel: no frame and the off-state's own sentence before, one frame carrying the site's
    document after, and the off-state back again when the switch is flipped away. Every request is
    reported so the caller can prove no console door but the draft was touched.
    """
    page.evaluate("() => document.querySelectorAll('details.sc-card').forEach((d) => { d.open = true; })")
    page.wait_for_timeout(300)
    # THE SWITCH LIVES IN THE CARD'S SUMMARY and reaches the form through `form="sc-save-…"`, so the
    # card is the <details>, not the form: locating the switch inside the form finds nothing.
    card = page.locator(
        f'details.sc-card:has(form.sc-form:has(input[value="{site}"]))').first
    panel = card.locator('aside.sc-site').first
    frame = panel.locator('iframe[data-sc-preview]')
    requests: list[str] = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    toggle = card.locator('input[role="switch"][name$=".enabled"]').first

    off_before = {"frames": frame.count(), "off": panel.locator('.sc-pv-box.sc-pv-off').count(),
                  "words": panel.locator('.sc-pv-box').first.text_content()}

    started = time.perf_counter()
    toggle.check()
    on_elapsed = None
    deadline = started + 5.0
    while time.perf_counter() < deadline:
        if frame.count() == 1:
            on_elapsed = int((time.perf_counter() - started) * 1000)
            break
        page.wait_for_timeout(50)
    page.wait_for_timeout(300)
    title = None
    hero = None
    if frame.count():
        inner = page.frame_locator(f'form.sc-form:has(input[value="{site}"]) iframe[data-sc-preview]')
        title = inner.locator("title").first.text_content()
        hero = inner.locator(".hero h1 em").first.text_content()
    after_on = {"frames": frame.count(), "off": panel.locator('.sc-pv-box.sc-pv-off').count(),
                "title": title, "hero": hero, "elapsed_ms": on_elapsed}

    toggle.uncheck()
    deadline = time.perf_counter() + 5.0
    while time.perf_counter() < deadline:
        if frame.count() == 0 and panel.locator('.sc-pv-box.sc-pv-off').count() == 1:
            break
        page.wait_for_timeout(50)
    after_off = {"frames": frame.count(), "off": panel.locator('.sc-pv-box.sc-pv-off').count(),
                 "words": panel.locator('.sc-pv-box').first.text_content()}
    return {"off_before": off_before, "on": after_on, "off_after": after_off,
            "requests": requests}


def _bind(page, site: str) -> dict:
    """Type into ``community_name`` and read the site's own document back.

    The card's rows bind on ``input``: the preview must follow the keystrokes, and — because nothing
    leaves the page until Save — NOT ONE request may be made while typing. Every request the page
    starts after the typing begins is therefore recorded and reported, not inferred from a log.
    """
    # the box lives inside the card's <details>: open every card so the frames are rendered
    page.evaluate("() => document.querySelectorAll('details.sc-card').forEach((d) => { d.open = true; })")
    page.wait_for_timeout(400)
    frame = page.frame_locator(f'form.sc-form:has(input[value="{site}"]) iframe[data-sc-preview]')
    # the DOM's OWN text, not the rendered case: the site's sheet uppercases the hero, and the pin is
    # about what the binding wrote into the document
    before = {
        "hero": frame.locator(".hero h1 em").first.text_content(),
        "title": frame.locator(".brand b").first.text_content(),
        "copyright": frame.locator(".foot-text").first.text_content(),
    }
    requests: list[str] = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    card = _card(page, site)
    card.locator(f'input[name="f.sites.{site}.branding.community_name"]').first.fill(TYPED_NAME)
    page.wait_for_timeout(400)
    after = {
        "hero": frame.locator(".hero h1 em").first.text_content(),
        "title": frame.locator(".brand b").first.text_content(),
        "copyright": frame.locator(".foot-text").first.text_content(),
    }
    # the footer's links are rebuilt from the card's own rows: add one if the site has none, fill it,
    # and read the anchors the preview document now carries
    container = card.locator(f'[data-cfg-seq="sites.{site}.branding.footer_links"]')
    if not container.locator("[data-cfg-row]").count():
        container.locator("[data-cfg-add]").first.click()
        page.wait_for_timeout(150)
    rows = container.locator("[data-cfg-row]")
    rows.last.locator('input[name$=".label"]').fill(LINK_LABEL)
    rows.last.locator('input[name$=".href"]').fill(LINK_HREF)
    page.wait_for_timeout(400)
    links = frame.locator(".foot .in a").all_text_contents()
    hrefs = frame.locator(".foot .in a").evaluate_all("nodes => nodes.map((n) => n.getAttribute('href'))")
    return {"before": before, "after": after, "requests": requests, "links": links,
            "hrefs": hrefs}


def _identity(page, site: str) -> dict:
    """THE HEADER LINE, driven: where the two controls are, and the payload the save form would carry.

    The form is a SIBLING of the card's `<summary>`, so the moved controls reach it only by
    ``form="sc-save-<token>"``. The pin reads ``new FormData(form)`` — the exact body the browser posts
    and ``static/submit.js`` sends — with the box ticked and unticked, and reports the card's own open
    state across both presses (a press inside the disclosure must not toggle the card).
    """
    form = _card(page, site)
    card = page.locator(f'details.sc-card:has(form.sc-form:has(input[value="{site}"]))').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(200)
    summary = card.locator("summary.sc-sum").first
    slug = f"f.sites.{site}.slug"
    enabled = f"f.sites.{site}.enabled"
    field = summary.locator(f'input[name="{slug}"]').first
    switch = summary.locator('.sc-idsw input[type="checkbox"]').first

    def payload() -> dict:
        return form.evaluate("""(f, names) => {
          const data = new FormData(f);
          const out = {};
          for (const name of names) out[name] = data.getAll(name);
          return out;
        }""", [slug, enabled])

    def words() -> list:
        return summary.locator(".sc-idsw .wd span").evaluate_all(
            "nodes => nodes.filter((n) => getComputedStyle(n).display !== 'none')"
            ".map((n) => n.textContent.trim())")

    def track() -> str:
        return summary.locator(".sc-idsw .bx").first.evaluate(
            "(n) => getComputedStyle(n).backgroundColor")

    report = {
        "name": summary.locator(".k.mono").first.inner_text().strip(),
        "open": card.evaluate("(d) => d.open"),
        "form_id": card.locator("form.sc-form").first.get_attribute("id"),
        "field_form": field.get_attribute("form"),
        "switch_form": switch.get_attribute("form"),
        "in_summary": {
            "slug": summary.locator(f'input[name="{slug}"]').count(),
            "switch": summary.locator(f'input[role="switch"][name="{enabled}"]').count(),
            "hidden": summary.locator(f'input[type="hidden"][name="{enabled}"]').count(),
        },
        "in_card": {
            "slug": card.locator(f'input[name="{slug}"]').count(),
            "switch": card.locator(f'input[role="switch"][name="{enabled}"]').count(),
            "hidden": card.locator(f'input[type="hidden"][name="{enabled}"]').count(),
        },
        "checked": switch.is_checked(),
        "ticked": payload(),
        "ticked_words": words(),
        "ticked_track": track(),
    }
    switch.click()
    page.wait_for_timeout(150)
    report["unticked_checked"] = switch.is_checked()
    report["open_after_untick"] = card.evaluate("(d) => d.open")
    report["unticked"] = payload()
    report["unticked_words"] = words()
    report["unticked_track"] = track()
    switch.click()
    page.wait_for_timeout(150)
    report["reticked_checked"] = switch.is_checked()
    report["open_after_retick"] = card.evaluate("(d) => d.open")
    report["reticked"] = payload()
    report["reticked_words"] = words()
    report["reticked_track"] = track()
    return report


def _expert(page, site: str) -> dict:
    """§D: read the sheet back, force a refusal on line 12, and pin the caret against the verdicts.

    The page's own editor is the subject, so every number below is read from the CodeMirror instance
    the page built. The verdicts are the SERVER's (they arrive from the validate route while the
    operator types), which is why the caret pin types a whole sequence with timing: a check must never
    move the caret, or the rest of the line lands on the wrong row.

    The sheet is drawn by Expert Mode, and this card stores none, so the box starts UNTICKED and the
    probe ticks it first — that press is the only way the editor is on screen at all.
    """
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(700)
    toggle = card.locator("[data-sc-expert-toggle]").first
    if toggle.count() and not toggle.is_checked():
        toggle.click()
        page.wait_for_timeout(400)
    block = card.locator("[data-sc-expert]").first
    if not block.count():
        return {"shown": False}

    def now() -> dict:
        return block.evaluate("""(b) => {
          const host = b.querySelector('.sc-sheet');
          const cm = b.querySelector('.CodeMirror') ? b.querySelector('.CodeMirror').CodeMirror : null;
          return {
            themeScope: host ? host.classList.contains('cfg-editor') : false,
            height: cm ? cm.getWrapperElement().getBoundingClientRect().height : 0,
            sheetHeight: host ? Math.round(host.getBoundingClientRect().height) : 0,
            gutter: cm ? cm.getWrapperElement().querySelector('.CodeMirror-gutters')
                          .getBoundingClientRect().width : 0,
            text: cm ? cm.getValue() : (b.querySelector('[data-sc-x-text]') || {}).value,
            lines: cm ? cm.lineCount() : 0,
            caret: cm ? {line: cm.getCursor().line, ch: cm.getCursor().ch} : null,
            /* measured against the CARD, not the viewport: the page scrolls in the shell's own pane,
             * so a viewport-relative number moves whenever a click scrolls the card into view */
            sheetTop: host ? Math.round(host.getBoundingClientRect().top
                                        - b.closest('details').getBoundingClientRect().top) : 0,
            verdictTop: Math.round(b.querySelector('[data-sc-x-verdict]').getBoundingClientRect().top
                                   - b.closest('details').getBoundingClientRect().top),
            badge: b.querySelector('[data-sc-x-badge]').textContent.trim(),
            when: b.querySelector('[data-sc-x-when]').textContent.trim(),
            count: b.querySelector('[data-sc-x-count]').textContent.trim(),
            loadDisabled: b.querySelector('[data-sc-x-load]').disabled,
            verdict: b.querySelector('[data-sc-x-verdict]').textContent.trim(),
            verdictState: b.querySelector('[data-sc-x-verdict]').getAttribute('data-state'),
          };
        }""")

    report: dict = {"shown": True, "initial": now()}
    # the mode's own class names, over this sheet and over the four shapes a refusal names
    report["mode"] = block.evaluate("""(b) => {
      const texts = [
        b.querySelector('.CodeMirror').CodeMirror.getValue(),
        '@import url(x.css);',
        'a:hover { color: red; }',
        '/* note */',
        '--fg-3: #b6a992 !important;',
      ];
      const host = document.createElement('div');
      document.body.appendChild(host);
      const seen = new Set();
      texts.forEach((text) => {
        host.innerHTML = '';
        const cm = window.CodeMirror(host, {value: text, mode: 'css'});
        cm.getWrapperElement().querySelectorAll('span[class]').forEach((node) => {
          node.className.split(/\\s+/).forEach((cls) => {
            if (cls.indexOf('cm-') === 0) { seen.add(cls.slice(3)); }
          });
        });
      });
      host.remove();
      return [...seen].sort();
    }""")

    # CHECK on the untouched copy: the shipped sheet's transcription must be ACCEPTED
    block.locator('[data-sc-x-check]').first.click()
    page.wait_for_timeout(900)
    report["checked"] = now()

    # one keystroke: the badge flips, the restore control comes alive
    editor_js = """(b) => { const cm = b.querySelector('.CodeMirror').CodeMirror;
      cm.focus(); cm.setCursor({line: 11, ch: cm.getLine(11).length}); return cm.getCursor(); }"""
    block.evaluate(editor_js)
    page.keyboard.type(" ", delay=40)
    page.wait_for_timeout(500)
    report["typed"] = now()

    # THE CARET PIN: a whole sequence of keystrokes while verdicts come and go
    typed = "!important"
    page.keyboard.type(typed, delay=90)
    page.wait_for_timeout(900)
    report["after_type"] = now()
    page.keyboard.type(" x", delay=90)
    page.wait_for_timeout(900)
    report["after_more"] = now()

    # the marks the refusal put on line 12, read from the DOM (a screenshot is not evidence)
    report["marks"] = block.evaluate("""(b) => {
      const cm = b.querySelector('.CodeMirror').CodeMirror;
      const rows = [...cm.getWrapperElement().querySelectorAll('.CodeMirror-code > div')];
      const read = (index) => {
        const row = rows[index];
        if (!row) { return null; }
        const gutter = row.querySelector('.CodeMirror-gutter-wrapper');
        const wash = row.querySelector('.CodeMirror-linebackground');
        const gutbg = row.querySelector('.CodeMirror-gutter-background');
        return {
          gutterClass: gutter ? gutter.className : '',
          gutterBackground: gutbg ? getComputedStyle(gutbg).backgroundColor : '',
          washClass: wash ? wash.className : '',
          washBackground: wash ? getComputedStyle(wash).backgroundColor : '',
          washShadow: wash ? getComputedStyle(wash).boxShadow : '',
        };
      };
      return {line12: read(11), line11: read(10)};
    }""")

    # the restore control: it asks ONCE, then puts the transcription back
    asked: list = []
    page.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.accept()))
    block.locator('[data-sc-x-load]').first.click()
    page.wait_for_timeout(900)
    report["restored"] = now()
    report["asked"] = asked
    return report


def _nojs(browser, base: str, user: str, password: str, page_path: str, site: str) -> dict:
    """PIN 6: with scripts off the plain textarea posts, and the save still refuses an invalid sheet."""
    context = browser.new_context(java_script_enabled=False, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors: list = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _sign_in(page, base, user, password)
    page.goto(base + page_path, wait_until="load")
    card = page.locator(f'form.sc-form:has(input[value="{site}"])').first
    field = card.locator(f'textarea[name="f.sites.{site}.theme.expert"]').first
    report = {"found": field.count() > 0, "value": field.input_value() if field.count() else None,
              "editor": page.locator(f'form.sc-form:has(input[value="{site}"]) .CodeMirror').count(),
              # the mark's picker is markup-only: with scripts off the label still opens the input
              # and the submit still posts it, so the no-script path is the same control. Scoped to
              # the mark's own slot: the palette block draws a picker of the same anatomy for the
              # site's uploaded sheet.
              "picker": card.locator("[data-sc-mark] label.sc-av-pick").all_inner_texts(),
              "picker_input": card.locator("[data-sc-mark] input.sc-av-file").count(),
              "errors": errors}
    if not report["found"]:
        context.close()
        return report
    field.scroll_into_view_if_needed()
    field.fill('a.component { color: red; }')
    with page.expect_navigation():
        page.locator("form.sc-form .sc-actions button[type='submit']").first.click()
    page.wait_for_timeout(300)
    report["notices"] = page.locator(".notice-msg").all_inner_texts()
    context.close()
    return report


def _png(width: int, height: int, colour=(200, 30, 40, 255)) -> bytes:
    """A PNG of one flat colour, written with the stdlib so the probe needs no image library."""
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(colour) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _picker(card) -> dict:
    """What the mark's picker IS: the input's own box, its styles, and the control beside it."""
    return card.evaluate("""(c) => {
      const input = c.querySelector('input.sc-av-file');
      if (!input) { return {found: false}; }
      const style = getComputedStyle(input);
      const box = input.getBoundingClientRect();
      const label = input.id ? c.querySelector(`label[for="${input.id}"]`) : null;
      const status = c.querySelector('[data-sc-file-status]');
      return {
        found: true,
        display: style.display,
        visibility: style.visibility,
        clip: style.clip + '|' + (style.clipPath || ''),
        opacity: style.opacity,
        width: Math.round(box.width),
        height: Math.round(box.height),
        focusable: (input.tabIndex >= 0) || true,
        id: input.id || '',
        label: label ? (() => {
          const box = label.getBoundingClientRect();
          const style = getComputedStyle(label);
          return {tag: label.tagName, cls: label.className, text: label.textContent.trim(),
                  width: Math.round(box.width), height: Math.round(box.height),
                  display: style.display, padding: style.padding, border: style.borderTopWidth,
                  cursor: style.cursor};
        })() : null,
        statusText: status ? status.textContent.trim() : null,
        actsText: (c.querySelector('[data-sc-mark-acts]') || {}).innerText || '',
      };
    }""")


def _preview_mark(page, site: str) -> dict:
    """The preview document's own mark: its address and the file's own pixels."""
    frame = page.frame_locator(f'form.sc-form:has(input[value="{site}"]) iframe[data-sc-preview]')
    img = frame.locator(".brand img").first
    if not img.count():
        return {"found": False}
    return {"found": True,
            "src": img.get_attribute("src"),
            "naturalWidth": img.evaluate("(n) => n.naturalWidth"),
            "naturalHeight": img.evaluate("(n) => n.naturalHeight"),
            "complete": img.evaluate("(n) => n.complete")}


#: the two colours the ``mark`` phase uploads: the second REPLACES the first, so "the box shows the
#: file just written" is readable as a pixel, not inferred from an address that never changed.
MARK_FIRST = (200, 30, 40, 255)
MARK_SECOND = (30, 90, 200, 255)


def _preview_pixel(page) -> dict:
    """The centre pixel of the preview's own mark, read from the frame that draws it."""
    for frame in page.frames:
        if "/chrome-preview" not in frame.url:
            continue
        return frame.evaluate("""() => {
          const img = document.querySelector('.brand img');
          if (!img) { return {found: false}; }
          const box = document.createElement('canvas');
          box.width = img.naturalWidth || 1;
          box.height = img.naturalHeight || 1;
          const ctx = box.getContext('2d');
          try { ctx.drawImage(img, 0, 0); } catch (error) { return {found: true, error: String(error)}; }
          const data = ctx.getImageData(Math.floor(box.width / 2), Math.floor(box.height / 2), 1, 1).data;
          return {found: true, src: img.getAttribute('src'), naturalWidth: img.naturalWidth,
                  pixel: [data[0], data[1], data[2], data[3]]};
        }""")
    return {"found": False}


def _upload_mark(page, site: str, card, colour) -> None:
    """Choose one PNG and press the upload's own submit, waiting for the page it lands on."""
    card.locator("input.sc-av-file").first.set_input_files(_write_png(colour))
    page.wait_for_timeout(200)
    with page.expect_navigation(timeout=30000):
        card.locator('[data-sc-mark-acts] button[type="submit"]').first.click()
    page.wait_for_timeout(800)


def _mark(page, site: str) -> dict:
    """§E: read the picker, upload a mark, then REPLACE it and read the SAME render's preview back.

    Nothing here presses F5: each upload submits the mark's own form, the route answers a 303 to the
    page, and the facts below are read from the document THAT navigation produced. The second upload
    is the one that matters — the preview's address and the mark's address are unchanged by it, so a
    preview that still shows the first file is the defect.
    """
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(500)
    report: dict = {"picker": _picker(card), "before": _preview_mark(page, site),
                    "before_pixel": _preview_pixel(page),
                    "iframe": card.locator("iframe[data-sc-preview]").first.get_attribute("src")}

    card.locator("input.sc-av-file").first.set_input_files(_write_png(MARK_FIRST))
    page.wait_for_timeout(200)
    report["picker_after_choose"] = _picker(card)
    _upload_mark(page, site, card, MARK_FIRST)
    report["first_notices"] = page.locator(".notice-msg").all_inner_texts()
    reloaded = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    reloaded.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(800)
    report["first"] = _preview_mark(page, site)
    report["first_pixel"] = _preview_pixel(page)

    _upload_mark(page, site, reloaded, MARK_SECOND)
    reloaded = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    reloaded.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(800)
    report["second_notices"] = page.locator(".notice-msg").all_inner_texts()
    report["second"] = _preview_mark(page, site)
    report["second_pixel"] = _preview_pixel(page)
    report["after_iframe"] = reloaded.locator("iframe[data-sc-preview]").first.get_attribute("src")
    report["state"] = reloaded.locator("[data-sc-mark]").first.get_attribute("data-sc-mark")
    report["previews"] = reloaded.locator("[data-sc-mark-previews] img").evaluate_all(
        "nodes => nodes.map((n) => ({src: n.getAttribute('src'), w: n.naturalWidth}))")
    report["expected"] = {"first": list(MARK_FIRST), "second": list(MARK_SECOND)}
    return report


def _write_png(colour) -> str:
    """One temp PNG of *colour*, written for the file input."""
    import tempfile

    handle, path = tempfile.mkstemp(suffix=".png")
    with open(handle, "wb") as sink:
        sink.write(_png(128, 128, colour))
    return path


#: the boxes a press must leave alone: the mark's slot, the card that holds it, and the document.
_PRESS_BOXES = """(c) => {
  const slot = c.querySelector('[data-sc-mark]');
  const acts = c.querySelector('[data-sc-mark-acts]');
  const input = c.querySelector('input.sc-av-file');
  const pane = document.querySelector('.wrap');
  const r = (n) => n ? +n.getBoundingClientRect().height.toFixed(3) : null;
  return {
    scrollHeight: document.documentElement.scrollHeight,
    clientHeight: document.documentElement.clientHeight,
    scrollY: window.scrollY,
    slot: r(slot),
    card: r(c),
    cardScrollHeight: c.scrollHeight,
    cardClientHeight: c.clientHeight,
    paneScrollTop: pane ? pane.scrollTop : null,
    inputTop: input ? +input.getBoundingClientRect().top.toFixed(3) : null,
    actsTop: acts ? +acts.getBoundingClientRect().top.toFixed(3) : null,
    active: document.activeElement
      ? document.activeElement.tagName + '.' + document.activeElement.className : '',
  };
}"""


def _press(page, site: str) -> dict:
    """Press the mark's trigger and measure the three states — before, during and after the press.

    The trigger is a `label[for]` bound to the clipped file input, so the press is where the two can
    part company: opening the picker focuses the input, and a focused box is scrolled into view by
    every scroll container between it and the document. Each state therefore reports the document's
    own `scrollHeight`/`clientHeight`, the slot's and the card's heights, the scrolling pane's
    position, and where the input and its row actually sit.

    The native picker is opened for real (Chromium dispatches a file chooser the probe holds open),
    and the keyboard path is the same control: Shift+Tab away and Tab back must land on the input,
    and Enter must open the picker too.
    """
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(400)
    report: dict = {"picker": _picker(card)}
    # the press is measured with the trigger ON SCREEN: the scroll that reveals it is not the press
    page.locator("label.sc-av-pick").first.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    report["before"] = card.evaluate(_PRESS_BOXES)

    chooser: dict = {}
    page.on("filechooser", lambda held: chooser.setdefault("held", held))
    page.locator("label.sc-av-pick").first.click()
    page.wait_for_timeout(500)
    report["during"] = card.evaluate(_PRESS_BOXES)
    report["chooser"] = "held" in chooser
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    report["after"] = card.evaluate(_PRESS_BOXES)

    # THE KEYBOARD PATH: leave the input, then Tab back onto it, then open the picker with Enter
    page.keyboard.press("Shift+Tab")
    page.wait_for_timeout(120)
    page.keyboard.press("Tab")
    page.wait_for_timeout(200)
    report["tab_lands_on_input"] = page.evaluate(
        "() => document.activeElement === document.querySelector('input.sc-av-file')")
    keyboard: dict = {}
    page.on("filechooser", lambda held: keyboard.setdefault("held", held))
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    report["keyboard"] = "held" in keyboard
    report["during_keyboard"] = card.evaluate(_PRESS_BOXES)
    return report


def _nojspick(browser, base: str, user: str, password: str, page_path: str, site: str) -> dict:
    """The no-JavaScript mark path: the label opens the picker and the submit posts the file.

    Nothing on this path is scripted — the label is the browser's own, the input is the form's own
    field, and the submit is the card's button — so a navigation that says the mark was written is
    the whole proof that a browser without scripts can set it.
    """
    context = browser.new_context(java_script_enabled=False, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors: list = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _sign_in(page, base, user, password)
    page.goto(base + page_path, wait_until="load")
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    report: dict = {"label": card.locator("[data-sc-mark] label.sc-av-pick").all_inner_texts(),
                    "state_before": card.locator("[data-sc-mark]").first.get_attribute("data-sc-mark")}
    with page.expect_file_chooser() as picked:
        card.locator("label.sc-av-pick").first.click()
    picked.value.set_files(_write_png((10, 120, 200, 255)))
    page.wait_for_timeout(200)
    report["chosen"] = card.locator("[data-sc-file-status]").first.inner_text()
    with page.expect_navigation(timeout=30000):
        card.locator('[data-sc-mark-acts] button[type="submit"]').first.click()
    page.wait_for_timeout(300)
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    report["notices"] = page.locator(".notice-msg").all_inner_texts()
    report["state_after"] = card.locator("[data-sc-mark]").first.get_attribute("data-sc-mark")
    report["errors"] = errors
    context.close()
    return report


def _preset(page, site: str) -> dict:
    """§Style: press a tile, read what the page shows BEFORE any save, then save and read it back."""
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(400)
    gallery = card.locator(".sc-gallery").first
    gallery.scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    requests: list = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    page.wait_for_timeout(200)
    baseline = len(requests)

    def state() -> dict:
        return gallery.evaluate("""(g) => {
          const tiles = [...g.querySelectorAll('label.sc-tile')];
          return {
            on: tiles.map((t, i) => t.classList.contains('on') ? i : null)
                    .filter((i) => i !== null),
            badges: tiles.map((t, i) => {
                      const badge = t.querySelector('[data-sc-preset-badge]');
                      return badge && !badge.hidden && badge.offsetParent !== null ? i : null;
                    }).filter((i) => i !== null),
            checked: tiles.map((t, i) => t.querySelector('input').checked ? i : null)
                          .filter((i) => i !== null),
            badgeTexts: [...g.querySelectorAll('.st-badge')]
                          .filter((b) => !b.hidden).map((b) => b.textContent.trim()),
            scrollY: window.scrollY,
            url: location.href,
          };
        }""")

    report = {"before": state()}
    tile = gallery.locator("label.sc-tile", has_text="Slate").first
    tile.click()
    page.wait_for_timeout(300)
    report["pressed"] = state()
    report["requests"] = requests[baseline:]
    report["saved"] = _save(page)
    page.wait_for_timeout(300)
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(400)
    report["after_save"] = card.locator(".sc-gallery").first.evaluate("""(g) => {
      const tiles = [...g.querySelectorAll('label.sc-tile')];
      return {on: tiles.map((t, i) => t.classList.contains('on') ? i : null)
                       .filter((i) => i !== null),
              badges: tiles.map((t, i) => {
                        const badge = t.querySelector('[data-sc-preset-badge]');
                        return badge && !badge.hidden && badge.offsetParent !== null ? i : null;
                      }).filter((i) => i !== null),
              badgeTexts: [...g.querySelectorAll('.st-badge')]
                            .filter((b) => !b.hidden).map((b) => b.textContent.trim())};
    }""")
    report["sheet"] = page.evaluate("""async () => {
      const reply = await fetch('/theme.css');
      return {status: reply.status, head: (await reply.text()).slice(0, 120)};
    }""")
    return report


def _wizard_card(page, site: str):
    """The card's palette wizard, opened through the gallery's door, with its six step rows drawn.

    The block draws ONE panel at a time: the gallery at rest and the wizard behind `<button
    data-pal-start>`, so a probe that wants the wizard must press that door — it is the only thing on
    the page that shows the wizard.
    """
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(700)
    door = card.locator("[data-pal-start]").first
    if door.count():
        door.click()
        page.wait_for_timeout(400)
    return card, card.locator("[data-sc-wizard]").first


#: the six step rows as the SCREEN prints them: the number, the name, the token names, every hex,
#: the measured ratio and the pair it measures
STEPS_JS = """(b) => [...b.querySelectorAll('[data-sc-wiz-step]')].map((row) => ({
  id: row.getAttribute('data-sc-wiz-step'),
  name: row.querySelector('.nm').textContent.replace(/[▾▴]/g, '').replace(/\\s+/g, ' ').trim(),
  tokens: row.querySelector('.tk').textContent.trim(),
  hexes: row.querySelector('[data-sc-wiz-hex]').textContent.trim(),
  ratio: row.querySelector('[data-sc-wiz-ratio]').textContent.trim(),
  state: row.querySelector('[data-sc-wiz-ratio]').getAttribute('data-state'),
  pair: row.querySelector('[data-sc-wiz-pair]').textContent.trim(),
  swatch: getComputedStyle(row.querySelector('.sw')).backgroundColor,
  swatchDisabled: row.querySelector('.sw').disabled,
  swatchLabel: row.querySelector('.sw').getAttribute('aria-label'),
}))"""


def _steps(wiz) -> list[dict]:
    """The six rows as the screen prints them, through the same reader the layout pin uses."""
    return wiz.evaluate(STEPS_JS)


def _choose_one_colour(page, wiz, hex_value: str, scheme: str = "dark") -> None:
    """The third starting point: the tile, its hex, and the scheme pill."""
    wiz.locator('.sc-start:has(input[value="one"])').first.click()
    wiz.locator("[data-sc-wiz-hex]").first.fill(hex_value)
    wiz.locator(f'[data-sc-wiz-scheme][value="{scheme}"]').first.click()
    page.wait_for_timeout(60)


def _frame_tokens(card, names) -> dict:
    """The preview DOCUMENT's own root, read with getComputedStyle: the palette it is wearing."""
    frame = card.locator("iframe[data-sc-preview]").first
    return frame.evaluate("""(f, names) => {
      const doc = f.contentDocument;
      const cs = doc.defaultView.getComputedStyle(doc.documentElement);
      const out = {theme: doc.documentElement.getAttribute('data-theme')};
      names.forEach((name) => { out[name] = cs.getPropertyValue(name).trim(); });
      out.inline = doc.documentElement.getAttribute('style') || '';
      return out;
    }""", names)


#: the ten tokens a preview is read back through: the six steps' own values
PREVIEW_TOKENS = ["--ground", "--panel", "--line-2", "--brass", "--fg", "--fg-3"]

#: every declaration the six steps print, in the order the rows name them — the whole palette a site
#: is wearing, so a read can be compared with the document token for token
STEP_TOKENS = ["--ground", "--stage", "--panel", "--panel-2", "--band", "--line", "--line-2",
               "--brass", "--brass-hi", "--brass-dim", "--fg", "--fg-2", "--fg-3"]


def _sheet_state(card) -> dict:
    """The expert block's own state, read from the instance the page built."""
    return card.evaluate("""(c) => {
      const b = c.querySelector('[data-sc-expert]');
      const cm = b.querySelector('.CodeMirror');
      return {
        text: cm ? cm.CodeMirror.getValue() : b.querySelector('[data-sc-x-text]').value,
        badge: b.querySelector('[data-sc-x-badge]').textContent.trim(),
        when: b.querySelector('[data-sc-x-when]').textContent.trim(),
        count: b.querySelector('[data-sc-x-count]').textContent.trim(),
        verdict: b.querySelector('[data-sc-x-verdict]').textContent.trim(),
        verdictState: b.querySelector('[data-sc-x-verdict]').getAttribute('data-state'),
      };
    }""")


def _wizard(page, site: str) -> dict:
    """§2 (pins 1, 3, 4 and 5): derive from one colour, read the screen and the preview, hand off.

    Every number is read off the page: the six rows' hexes and ratios, the moves the rule printed,
    the preview DOCUMENT's computed tokens, the sheet the handoff left in the editor, and every
    request the page made between the derive press and the handoff.
    """
    card, wiz = _wizard_card(page, site)
    if not wiz.count():
        return {"shown": False}
    requests: list[str] = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    report: dict = {"shown": True}
    wizard_errors: list[str] = []
    page.on("pageerror", lambda error: wizard_errors.append(str(error)))
    # the derivation itself, straight out of the module the page loaded
    report["derive"] = page.evaluate("""() => {
      const r = window.SC_PALETTE.derive('#4a7fd1', 'dark');
      return {values: r.values, checks: r.checks.map((c) => [c.token, c.against, c.ratio, c.min]),
              moves: r.moves.map((m) => [m.token, m.from, m.to, m.reached]),
              gated: r.gated, text: window.SC_PALETTE.sheetText(r.values)};
    }""")
    # the four shipped accents, through both ladders — the note's own table, computed
    report["derive_extra"] = page.evaluate("""() => {
      const cases = [['#d9a04a', 'dark'], ['#1e5fae', 'light'], ['#33d1e6', 'dark'],
                     ['#7a4e1b', 'light']];
      const out = {};
      cases.forEach(([base, scheme]) => { out[base] = window.SC_PALETTE.derive(base, scheme).values; });
      return out;
    }""")
    report["step1"] = wiz.evaluate("""(b) => ({
      where: b.querySelector('[data-sc-wiz-where]').textContent.trim(),
      tiles: [...b.querySelectorAll('[data-sc-wiz-start]')].map((t) => ({
        value: t.querySelector('input').value, on: t.classList.contains('on'),
        name: t.querySelector('.sc-tile-name').textContent.trim(),
        key: t.querySelector('.start-key').textContent.trim(),
        say: t.querySelector('.start-say').textContent.trim(),
      })),
      schemes: [...b.querySelectorAll('[data-sc-wiz-scheme]')].map((p) => [p.value,
                                                                          p.classList.contains('on')]),
      constants: (b.querySelector('[data-sc-wiz-consts]') || {}).textContent.trim(),
    })""")
    _choose_one_colour(page, wiz, "#4a7fd1")
    report["before"] = _sheet_state(card)
    report["before_preview"] = _frame_tokens(card, PREVIEW_TOKENS)
    requests.clear()
    wiz.locator("[data-sc-wiz-derive]").first.click()
    page.wait_for_timeout(300)
    report["step2"] = wiz.evaluate("""(b) => ({
      where: b.querySelector('[data-sc-wiz-where]').textContent.trim(),
      shown: !b.querySelector('[data-sc-wiz-body="2"]').hidden,
      row: getComputedStyle(b.querySelector('[data-sc-wiz-body="2"]')).display,
    })""")
    report["steps"] = wiz.evaluate(STEPS_JS)
    report["moves"] = wiz.evaluate(
        "(b) => [...b.querySelectorAll('[data-sc-wiz-moves] p')].map((p) => p.textContent.trim())")
    report["warn"] = wiz.evaluate("""(b) => ({
      hidden: b.querySelector('[data-sc-wiz-warn]').hidden,
      say: b.querySelector('[data-sc-wiz-warn-say]').textContent.trim(),
    })""")
    report["constants"] = wiz.evaluate(
        "(b) => b.querySelector('[data-sc-wiz-consts]').textContent.trim()")
    report["useDisabled"] = wiz.locator("[data-sc-wiz-use]").first.is_disabled()
    report["after_preview"] = _frame_tokens(card, PREVIEW_TOKENS)
    report["requests_before_handoff"] = list(requests)
    report["errors"] = wizard_errors
    if not report["step2"]["shown"]:
        return report

    wiz.locator("[data-sc-wiz-use]").first.click()
    page.wait_for_timeout(400)
    report["requests_after_handoff"] = list(requests)
    report["handoff"] = {
        "wizardHidden": wiz.evaluate("(b) => b.hidden"),
        "stripHidden": card.locator("[data-sc-handed]").first.evaluate("(n) => n.hidden"),
        "strip": card.locator("[data-sc-handed]").first.inner_text(),
        "sheet": _sheet_state(card),
        "head": card.evaluate("""(c) => ({
          words: c.querySelector('[data-sc-pal-words]').textContent.trim(),
          key: c.querySelector('[data-sc-pal-key]').textContent.trim(),
          value: c.querySelector('[data-sc-pal-value]').textContent.trim(),
        })"""),
        # the handoff's destination is the sheet, so the block shows it: the box ticked, the sheet
        # drawn — and the carrier written by the handoff alone
        "toggle": card.evaluate("(c) => { const b = c.querySelector('[data-sc-expert-toggle]');"
                                " return b ? b.checked : null; }"),
        "sheetDrawn": card.evaluate("(c) => { const b = c.querySelector('[data-sc-expert]');"
                                    " return b ? !b.hidden && getComputedStyle(b).display !== 'none'"
                                    " : false; }"),
        "galleryDrawn": card.evaluate(
            "(c) => { const r = c.querySelector('[data-sc-pal-panel=\"rest\"]');"
            " return r ? !r.hidden : null; }"),
        "preset": card.evaluate("""(c) => {
          const checked = c.querySelector('.sc-gallery input[type="radio"]:checked');
          return {
            checked: checked ? checked.value : null,
            on: [...c.querySelectorAll('[data-sc-preset-tile]')]
              .map((t) => t.classList.contains('on')),
            badges: [...c.querySelectorAll('[data-sc-preset-badge]')]
              .map((b) => (b.hidden ? null : b.textContent.trim())),
          };
        }"""),
    }
    # back to the wizard: the six steps are still there, with no re-derivation
    report["again"] = None
    again = card.locator("[data-sc-wiz-again]").first
    if again.count():
        again.click()
        page.wait_for_timeout(200)
        report["again"] = {"wizardHidden": wiz.evaluate("(b) => b.hidden"),
                           "stripHidden": card.locator("[data-sc-handed]").first.evaluate(
                               "(n) => n.hidden"),
                           "steps": wiz.evaluate(STEPS_JS)}
        wiz.locator("[data-sc-wiz-use]").first.click()
        page.wait_for_timeout(300)
    report["saved"] = _save(page)
    report["stored"] = _sheet_state(card)
    # the reloaded card opens the wizard FROM the sheet it now stores, not from a preset
    payload = card.locator("[data-sc-wizard]").first.get_attribute("data-sc-wizard")
    report["reloaded"] = json.loads(payload) if payload else None
    report["sheet"] = page.evaluate("""async () => {
      const reply = await fetch('/theme.css');
      return {status: reply.status, text: await reply.text()};
    }""")
    # the PUBLIC site, wearing it: the saved palette is what a visitor's page is painted with
    origin = page.evaluate("() => location.origin")
    page.goto(origin + "/", wait_until="load")
    page.wait_for_timeout(200)
    report["public"] = page.evaluate("""() => {
      const body = getComputedStyle(document.body);
      const root = getComputedStyle(document.documentElement);
      return {
        ground: body.backgroundColor,
        accent: root.getPropertyValue('--brass').trim(),
        text: root.getPropertyValue('--fg').trim(),
        theme: document.documentElement.getAttribute('data-theme'),
      };
    }""")
    return report


def _wizard_adjust(page, site: str) -> dict:
    """§2.4 (pin 2): a base that cannot be the accent — the move, the warning and the closed handoff."""
    card, wiz = _wizard_card(page, site)
    if not wiz.count():
        return {"shown": False}
    report: dict = {"shown": True}
    _choose_one_colour(page, wiz, "#1f3a5f")
    wiz.locator("[data-sc-wiz-derive]").first.click()
    page.wait_for_timeout(300)
    report["warn"] = wiz.evaluate("""(b) => ({
      hidden: b.querySelector('[data-sc-wiz-warn]').hidden,
      say: b.querySelector('[data-sc-wiz-warn-say]').textContent.trim(),
      use: b.querySelector('[data-sc-wiz-accept]').textContent.trim(),
    })""")
    report["useDisabled"] = wiz.locator("[data-sc-wiz-use]").first.is_disabled()
    report["steps"] = wiz.evaluate(STEPS_JS)
    report["moves"] = wiz.evaluate(
        "(b) => [...b.querySelectorAll('[data-sc-wiz-moves] p')].map((p) => p.textContent.trim())")
    before = _sheet_state(card)
    wiz.locator("[data-sc-wiz-accept]").first.click()
    page.wait_for_timeout(200)
    report["accepted"] = {
        "useDisabled": wiz.locator("[data-sc-wiz-use]").first.is_disabled(),
        "warnHidden": wiz.evaluate("(b) => b.querySelector('[data-sc-wiz-warn]').hidden"),
        "steps": wiz.evaluate(STEPS_JS),
        "before_text": before["text"],
    }
    report["sheet_untouched"] = _sheet_state(card)["text"] == before["text"]
    return report


def _wizard_now(page, site: str) -> dict:
    """§2.2: the second starting point reads the colours the page is actually rendering.

    The palette is read by the wizard out of the preview DOCUMENT's own root, so the six steps must
    open on the values the site wears — not on a preset's numbers typed into the page.
    """
    card, wiz = _wizard_card(page, site)
    if not wiz.count():
        return {"shown": False}
    report: dict = {"shown": True}
    report["preview"] = _frame_tokens(card, STEP_TOKENS)
    wiz.locator('.sc-start:has(input[value="now"])').first.click()
    page.wait_for_timeout(60)
    wiz.locator("[data-sc-wiz-derive]").first.click()
    page.wait_for_timeout(300)
    report["steps"] = wiz.evaluate(STEPS_JS)
    report["moves"] = wiz.evaluate(
        "(b) => [...b.querySelectorAll('[data-sc-wiz-moves] p')].map((p) => p.textContent.trim())")
    report["useDisabled"] = wiz.locator("[data-sc-wiz-use]").first.is_disabled()
    report["stored"] = _reported_strip(wiz)
    report["sheet"] = card.evaluate("""(c) => {
      const b = c.querySelector('[data-sc-expert]');
      return {posted: b.querySelector('[data-sc-x-text]').value,
              badge: b.querySelector('[data-sc-x-badge]').textContent.trim()};
    }""")
    return report


def _reported_strip(wiz) -> dict:
    """The strip a READ palette puts above the six steps: whether it is drawn, and what it says."""
    return wiz.evaluate("""(b) => {
      const node = b.querySelector('[data-sc-wiz-stored]');
      return {present: Boolean(node), hidden: node ? node.hidden : null,
              text: node ? node.textContent.trim() : ''};
    }""")


def _wizard_stored(page, site: str) -> dict:
    """§5/§9: a site whose palette IS a sheet opens the wizard ON that sheet, and reading moves nothing.

    The evidence is the payload's own `start`, the tile it pressed, the six rows' hexes as printed,
    the strip that says they are measured as they stand — and the sheet's text before and after the
    derivation, which must be the same bytes: a read is a report, never a rewrite.
    """
    card, wiz = _wizard_card(page, site)
    if not wiz.count():
        return {"shown": False}
    requests: list[str] = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    page.wait_for_timeout(200)
    baseline = len(requests)
    payload = json.loads(wiz.get_attribute("data-sc-wizard") or "{}")
    report: dict = {"shown": True, "start": payload.get("start"),
                    "tiles": [{"id": tile.get("id"), "on": tile.get("on")}
                              for tile in payload.get("tiles") or []],
                    "step1_tiles": wiz.evaluate("""(b) => [...b.querySelectorAll('[data-sc-wiz-start]')]
                      .map((t) => ({value: t.querySelector('input').value,
                                    on: t.classList.contains('on')}))"""),
                    "pressed": wiz.evaluate(
                        "(b) => (b.querySelector('.sc-start.on') || {}).textContent || ''"),
                    "toggle": card.evaluate("""(c) => {
                      const box = c.querySelector('[data-sc-expert-toggle]');
                      if (!box) { return null; }
                      return {name: box.getAttribute('name'), checked: box.checked, type: box.type,
                              role: box.getAttribute('role'), tag: box.tagName,
                              label: box.closest('label').textContent.trim(),
                              inHead: Boolean(box.closest('.sc-sechd'))};
                    }"""),
                    "sheet_before": _sheet_state(card)["text"],
                    "carrier_before": card.evaluate(
                        "(c) => c.querySelector('[data-sc-x-text]').value"),
                    "header_before": card.evaluate("""(c) => ({
                      words: c.querySelector('[data-sc-pal-words]').textContent.trim(),
                      key: c.querySelector('[data-sc-pal-key]').textContent.trim(),
                      value: c.querySelector('[data-sc-pal-value]').textContent.trim(),
                    })""")}
    wiz.locator("[data-sc-wiz-derive]").first.click()
    page.wait_for_timeout(400)
    report["steps"] = wiz.evaluate(STEPS_JS)
    report["moves"] = wiz.evaluate(
        "(b) => [...b.querySelectorAll('[data-sc-wiz-moves] p')].map((p) => p.textContent.trim())")
    report["stored"] = _reported_strip(wiz)
    report["useDisabled"] = wiz.locator("[data-sc-wiz-use]").first.is_disabled()
    report["sheet_after"] = _sheet_state(card)["text"]
    report["carrier_after"] = card.evaluate(
        "(c) => c.querySelector('[data-sc-x-text]').value")
    report["preview"] = _frame_tokens(card, PREVIEW_TOKENS)
    report["requests"] = requests[baseline:]
    return report


def _token_values(wiz, step_id: str) -> list[dict]:
    """One open row's declarations, as the page prints them: the value, the hex, the name and its line."""
    return wiz.evaluate("""(b, sid) => {
      const row = b.querySelector('[data-sc-wiz-step="' + sid + '"]');
      const P = window.SC_PALETTE;
      return [...row.querySelectorAll('[data-sc-wiz-tok]')].map((t) => {
        const value = t.querySelector('[data-sc-wiz-tokhex]').value;
        const rgb = P.hexRgb(value);
        return {
          name: t.querySelector('.sc-toknm').textContent.trim(),
          pick: t.querySelector('[data-sc-wiz-tokpick]').value,
          hex: value,
          light: rgb ? P.oklch(rgb)[0] : null,
          said: t.querySelector('[data-sc-wiz-toksaid]').textContent.trim(),
          pickDisabled: t.querySelector('[data-sc-wiz-tokpick]').disabled,
          hexDisabled: t.querySelector('[data-sc-wiz-tokhex]').disabled,
        };
      });
    }""", step_id)


def _set_token(wiz, step_id: str, name: str, value: str) -> None:
    """Press a declaration's colour input, the way the picker's `input` event does."""
    wiz.evaluate("""(b, [sid, name, value]) => {
      const row = b.querySelector('[data-sc-wiz-step="' + sid + '"]');
      const field = row.querySelector('[data-sc-wiz-tokpick="' + name + '"]');
      field.value = value;
      field.dispatchEvent(new Event('input', {bubbles: true}));
    }""", [step_id, name, value])


def _set_token_hex(wiz, step_id: str, name: str, value: str) -> None:
    """Type into a declaration's hex field, the way the field's `input` event does."""
    wiz.evaluate("""(b, [sid, name, value]) => {
      const row = b.querySelector('[data-sc-wiz-step="' + sid + '"]');
      const field = row.querySelector('[data-sc-wiz-tokhex="' + name + '"]');
      field.value = value;
      field.dispatchEvent(new Event('input', {bubbles: true}));
    }""", [step_id, name, value])


def _step_state(wiz, step_id: str) -> dict:
    """One row as the screen prints it: the swatch, every hex, the chip and the pair's own line."""
    return wiz.evaluate("""(b, sid) => {
      const row = b.querySelector('[data-sc-wiz-step="' + sid + '"]');
      const chip = row.querySelector('[data-sc-wiz-ratio]');
      const sw = row.querySelector('.sw');
      const pair = row.querySelector('[data-sc-wiz-tokpair]');
      return {
        open: row.classList.contains('is-open'),
        toksHidden: row.querySelector('[data-sc-wiz-toks]').hidden,
        swatch: sw.value,
        swatchBg: getComputedStyle(sw).backgroundColor,
        swatchDisabled: sw.disabled,
        swatchLabel: sw.getAttribute('aria-label'),
        hexes: row.querySelector('[data-sc-wiz-hex]').textContent.trim(),
        ratio: chip.textContent.trim(),
        state: chip.getAttribute('data-state'),
        pair: row.querySelector('[data-sc-wiz-pair]').textContent.trim(),
        line: pair ? pair.textContent.trim() : '',
      };
    }""", step_id)


def _measure(wiz) -> dict:
    """The block's boxes as the screen draws them: the block, the steps list, the strip and each row."""
    return wiz.evaluate("""(b) => {
      const box = (n) => (n ? Math.round(n.getBoundingClientRect().height) : null);
      const warn = b.querySelector('[data-sc-wiz-warn]');
      return {
        block: box(b), steps: box(b.querySelector('.sc-steps')),
        strip: warn && !warn.hidden ? box(warn) : null,
        rows: [...b.querySelectorAll('[data-sc-wiz-step]')].map((r) => ({
          id: r.getAttribute('data-sc-wiz-step'), h: box(r),
        })),
      };
    }""")


def _warn_state(wiz) -> dict:
    """The strip above the steps: whether it is drawn, its sentence, its two answers and the foot."""
    return wiz.evaluate("""(b) => {
      const w = b.querySelector('[data-sc-wiz-warn]');
      const accept = b.querySelector('[data-sc-wiz-accept]');
      const keep = b.querySelector('[data-sc-wiz-keep]');
      const foot = b.querySelector('[data-sc-wiz-foot]');
      return {
        hidden: w.hidden,
        say: b.querySelector('[data-sc-wiz-warn-say]').textContent.trim(),
        use: accept.textContent.trim(),
        useHidden: accept.hidden,
        keep: keep.textContent.trim(),
        keepHidden: keep.hidden,
        foot: foot ? foot.textContent.trim() : '',
      };
    }""")


def _palette_edit(page, site: str) -> dict:
    """§3 (pins 1, 2 and 3): a step's row edited, its followers moved, the frame repainted, AA stated."""
    card, wiz = _wizard_card(page, site)
    if not wiz.count():
        return {"shown": False}
    requests: list[str] = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    report: dict = {"shown": True}
    _choose_one_colour(page, wiz, "#4a7fd1")
    wiz.locator("[data-sc-wiz-derive]").first.click()
    page.wait_for_timeout(300)
    report["frames"] = card.locator("iframe[data-sc-preview]").count()
    wiz.locator('[data-sc-wiz-more="accent"]').first.click()
    page.wait_for_timeout(80)
    report["open"] = _step_state(wiz, "accent")
    report["before"] = _token_values(wiz, "accent")
    report["before_frame"] = _frame_tokens(card, ["--brass", "--brass-hi", "--brass-dim"])
    requests.clear()
    _set_token(wiz, "accent", "--brass", "#6e9ff4")
    page.wait_for_timeout(120)
    report["after"] = _token_values(wiz, "accent")
    report["after_step"] = _step_state(wiz, "accent")
    report["after_frame"] = _frame_tokens(card, ["--brass", "--brass-hi", "--brass-dim"])
    report["after_requests"] = list(requests)
    report["measure_after"] = _measure(wiz)
    # the second opened step: the ground's own edit, its follower and the pair it moves
    wiz.locator('[data-sc-wiz-more="ground"]').first.click()
    page.wait_for_timeout(80)
    _set_token(wiz, "ground", "--ground", "#121a2e")
    page.wait_for_timeout(120)
    report["ground"] = _token_values(wiz, "ground")
    report["ground_step"] = _step_state(wiz, "ground")
    report["ground_frame"] = _frame_tokens(card, ["--ground", "--stage", "--brass"])
    # step 06's closed swatch follows --fg; its opener still opens the row and --fg stays editable
    wiz.locator('[data-sc-wiz-more="steps"]').first.click()
    page.wait_for_timeout(80)
    report["steps"] = _step_state(wiz, "steps")
    report["steps_tokens"] = _token_values(wiz, "steps")
    wiz.locator('[data-sc-wiz-more="text"]').first.click()
    page.wait_for_timeout(80)
    report["text"] = _token_values(wiz, "text")
    # the two directions of the same value: the hex field writes the picker, a malformed hex does not
    _set_token_hex(wiz, "accent", "--brass", "#123456")
    page.wait_for_timeout(100)
    report["hex_to_pick"] = _token_values(wiz, "accent")
    _set_token_hex(wiz, "accent", "--brass", "zzzz")
    page.wait_for_timeout(100)
    report["malformed"] = _token_values(wiz, "accent")
    report["malformed_step"] = _step_state(wiz, "accent")
    # the AA case is the design's own ground: put the ground back to the derivation's #0b1016
    _set_token(wiz, "ground", "--ground", "#0b1016")
    page.wait_for_timeout(100)
    # an edit under AA: the value stays, the strip names the nearest and both answers
    _set_token(wiz, "accent", "--brass", "#31508f")
    page.wait_for_timeout(120)
    report["aa"] = _warn_state(wiz)
    report["aa_tokens"] = _token_values(wiz, "accent")
    report["aa_step"] = _step_state(wiz, "accent")
    report["aa_steps"] = _steps(wiz)
    report["aa_useDisabled"] = wiz.locator("[data-sc-wiz-use]").first.is_disabled()
    report["aa_sheet"] = _sheet_state(card)["text"]
    report["measure_aa"] = _measure(wiz)
    # the answer: Use the nearest — the pair passes and the handoff opens
    wiz.locator("[data-sc-wiz-accept]").first.click()
    page.wait_for_timeout(120)
    report["used"] = _warn_state(wiz)
    report["used_step"] = _step_state(wiz, "accent")
    report["used_useDisabled"] = wiz.locator("[data-sc-wiz-use]").first.is_disabled()
    report["used_sheet"] = _sheet_state(card)["text"]
    report["used_frame"] = _frame_tokens(card, ["--brass", "--brass-hi", "--brass-dim"])
    # the handoff: the nearest's sheet, then the card's Save — the only write
    wiz.locator("[data-sc-wiz-use]").first.click()
    page.wait_for_timeout(300)
    report["handoff"] = _sheet_state(card)["text"]
    report["saved"] = _save(page)
    return report


def _palette_keep(page, site: str) -> dict:
    """§3.3: Keep the operator's value — the row keeps its red chip and the verdict refuses it."""
    card, wiz = _wizard_card(page, site)
    if not wiz.count():
        return {"shown": False}
    report: dict = {"shown": True}
    _choose_one_colour(page, wiz, "#4a7fd1")
    wiz.locator("[data-sc-wiz-derive]").first.click()
    page.wait_for_timeout(300)
    wiz.locator('[data-sc-wiz-more="accent"]').first.click()
    page.wait_for_timeout(80)
    _set_token(wiz, "accent", "--brass", "#31508f")
    page.wait_for_timeout(120)
    report["warn"] = _warn_state(wiz)
    report["step"] = _step_state(wiz, "accent")
    report["useDisabled"] = wiz.locator("[data-sc-wiz-use]").first.is_disabled()
    report["sheet"] = _sheet_state(card)["text"]
    wiz.locator("[data-sc-wiz-keep]").first.click()
    page.wait_for_timeout(120)
    report["kept"] = _warn_state(wiz)
    report["kept_step"] = _step_state(wiz, "accent")
    report["kept_useDisabled"] = wiz.locator("[data-sc-wiz-use]").first.is_disabled()
    report["kept_sheet"] = _sheet_state(card)["text"]
    report["kept_handoff"] = _sheet_state(card)["text"]
    return report


#: the two declarations typed into the expert sheet, and the line the grammar does not describe
TYPED_GROUND = "#102030"
TYPED_BRASS = "#abcdef"
TYPED_JUNK = "not a declaration"


def _palette_paint(page, site: str) -> dict:
    """§5 driven: the palette block's own controls repaint the card's own frame, before Save.

    Every value is read back with ``getComputedStyle`` off the frame's own ``documentElement``, so
    what is answered is the palette the preview is WEARING — not the attributes a script wrote.
    """
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(900)
    frame = card.locator("iframe[data-sc-preview]").first
    frame.evaluate("""(f) => new Promise((resolve) => {
      const ready = () => f.contentDocument && f.contentDocument.documentElement &&
        getComputedStyle(f.contentDocument.documentElement).getPropertyValue('--ground').trim();
      if (ready()) { resolve(true); return; }
      f.addEventListener('load', () => resolve(true), {once: true});
      setTimeout(() => resolve(Boolean(ready())), 1500);
    })""")
    requests: list[str] = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    section = card.locator('section[data-sc-block="palette"]').first
    report: dict = {"frames": card.locator("iframe[data-sc-preview]").count(),
                    "before": _frame_tokens(card, ["--ground", "--panel", "--brass", "--fg"]),
                    "presets": []}
    tiles = section.locator("[data-sc-preset-tile]")
    for index in range(tiles.count()):
        tile = tiles.nth(index)
        radio = tile.locator('input[type="radio"]').first
        payload = json.loads(radio.get_attribute("data-sc-preset-sheet") or "{}")
        # the tile is the label the operator presses: the radio under it is drawn as the card
        tile.scroll_into_view_if_needed()
        tile.click()
        page.wait_for_timeout(150)
        report["presets"].append({
            "value": radio.get_attribute("value") or "", "payload": payload,
            "after": _frame_tokens(card, ["--ground", "--panel", "--brass", "--fg"])})
    # nothing the presses did reached the server: Save is still the only write
    report["press_requests"] = list(requests)
    report["sheet"] = _type_sheet(page, card)
    return report


def _type_sheet(page, card) -> dict:
    """Two declarations typed into the editor the page built, then a line it does not parse."""
    toggle = card.locator("[data-sc-expert-toggle]").first
    if not toggle.count():
        return {"shown": False}
    toggle.check(force=True)
    page.wait_for_timeout(200)
    editor = card.locator(".CodeMirror").first
    if not editor.count():
        return {"shown": False}
    editor.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type(f"--ground: {TYPED_GROUND};\n--brass: {TYPED_BRASS};")
    page.wait_for_timeout(300)
    typed = _frame_tokens(card, ["--ground", "--brass"])
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type(TYPED_JUNK)
    page.wait_for_timeout(300)
    return {"shown": True, "typed": typed, "after_junk": _frame_tokens(card, ["--ground", "--brass"]),
            "text": _sheet_state(card)["text"]}


def _expert_mode(page, site: str) -> dict:
    """§4 driven: the box is a view control. Unticking hides the sheet, writes nothing, and saving
    leaves the stored sheet exactly as it was.

    Every state is read from the page: the box's own attributes, whether the sheet block is drawn, the
    strip that stands where the sheet would be, the head's say-line, and the CARRIER's text — which is
    the only thing a save posts. The save then reloads the page, so "unticking cleared nothing" is
    proven against the file (by the caller) and against the card that comes back.
    """
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(700)

    def state() -> dict:
        return card.evaluate("""(c) => {
          const box = c.querySelector('[data-sc-expert-toggle]');
          const block = c.querySelector('[data-sc-expert]');
          const carrier = c.querySelector('[data-sc-x-text]');
          const stored = c.querySelector('[data-sc-expert-stored]');
          const none = c.querySelector('[data-sc-expert-none]');
          const say = c.querySelector('[data-sc-pal-say]');
          const visible = (node) => Boolean(node) && !node.hidden
            && getComputedStyle(node).display !== 'none';
          const sheet = c.querySelector('[data-sc-expert] .sc-sheet');
          const editor = c.querySelector('[data-sc-expert] .CodeMirror');
          const card = c.closest('details');
          return {
            checked: box ? box.checked : null,
            sheetDrawn: visible(block),
            stripDrawn: visible(stored),
            stripText: stored ? stored.textContent.trim() : '',
            noneDrawn: visible(none),
            say: say ? say.textContent.trim() : '',
            carrier: carrier ? carrier.value : '',
            count: c.querySelector('[data-sc-x-count]').textContent.trim(),
            badge: c.querySelector('[data-sc-x-badge]').textContent.trim(),
            sheetW: sheet ? Math.round(sheet.getBoundingClientRect().width) : 0,
            cardW: card ? Math.round(card.getBoundingClientRect().width) : 0,
            editorH: editor ? Math.round(editor.getBoundingClientRect().height) : 0,
            gutter: editor ? Math.round(editor.querySelector('.CodeMirror-gutters')
              .getBoundingClientRect().width) : 0,
          };
        }""")

    toggle = card.locator("[data-sc-expert-toggle]").first
    report: dict = {"toggle": card.evaluate("""(c) => {
      const box = c.querySelector('[data-sc-expert-toggle]');
      if (!box) { return null; }
      const block = box.closest('[data-sc-block]');
      return {name: box.getAttribute('name'), type: box.type, role: box.getAttribute('role'),
              tag: box.tagName, label: box.closest('label').textContent.trim(),
              inHead: Boolean(box.closest('.sc-sechd')),
              block: block ? block.getAttribute('data-sc-block') : null,
              inPalette: Boolean(box.closest('[data-sc-block="palette"]')),
              sectionId: block ? block.getAttribute('id') : null};
    }"""), "opened": state()}

    # UNTICK: the sheet goes off screen and the carrier keeps the sheet
    toggle.click()
    page.wait_for_timeout(400)
    report["closed"] = state()

    # TICK again: the sheet comes back with the stored text, untouched
    toggle.click()
    page.wait_for_timeout(400)
    report["reopened"] = state()
    editor = card.locator("[data-sc-expert] .CodeMirror").count()
    report["editor"] = editor
    if editor:
        report["reopened"]["editorText"] = card.evaluate(
            "(c) => c.querySelector('[data-sc-expert] .CodeMirror').CodeMirror.getValue()")

    # UNTICKED AT SAVE — the pin this block exists for
    toggle.click()
    page.wait_for_timeout(300)
    report["saved"] = _save(page)

    again = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    again.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(800)
    card = again
    report["reloaded"] = state()
    report["header"] = card.evaluate("""(c) => ({
      words: c.querySelector('[data-sc-pal-words]').textContent.trim(),
      key: c.querySelector('[data-sc-pal-key]').textContent.trim(),
      value: c.querySelector('[data-sc-pal-value]').textContent.trim(),
    })""")
    return report


def _expert_empty(page, site: str, site_file: str | None = None) -> dict:
    """PIN 1 — a preset site's sheet is SHOWN, not stored; a Save keeps it absent until an edit.

    The card stores no ``theme.expert``. Opening Expert Mode draws the shipped transcription in the
    editor while the CARRIER stays empty, so the bar must say the transcription is shown and not
    stored. The first Save posts the empty carrier and succeeds; the reloaded card states the preset
    in words. One keystroke fills the carrier and the second Save stores the sheet.
    """
    card = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(700)

    def state() -> dict:
        return card.evaluate("""(c) => {
          const box = c.querySelector('[data-sc-expert-toggle]');
          const block = c.querySelector('[data-sc-expert]');
          const carrier = c.querySelector('[data-sc-x-text]');
          const stored = c.querySelector('[data-sc-expert-stored]');
          const none = c.querySelector('[data-sc-expert-none]');
          const say = c.querySelector('[data-sc-pal-say]');
          const visible = (node) => Boolean(node) && !node.hidden
            && getComputedStyle(node).display !== 'none';
          const cm = block && block.querySelector('.CodeMirror');
          return {
            checked: box ? box.checked : null,
            sheetDrawn: visible(block),
            storedDrawn: visible(stored),
            noneDrawn: visible(none),
            noneText: none ? none.textContent.trim() : '',
            say: say ? say.textContent.trim() : '',
            carrier: carrier ? carrier.value : '',
            text: cm ? cm.CodeMirror.getValue() : (carrier ? carrier.value : ''),
            badge: block.querySelector('[data-sc-x-badge]').textContent.trim(),
            when: block.querySelector('[data-sc-x-when]').textContent.trim(),
            count: block.querySelector('[data-sc-x-count]').textContent.trim(),
            loadDisabled: block.querySelector('[data-sc-x-load]').disabled,
          };
        }""")

    toggle = card.locator("[data-sc-expert-toggle]").first
    report: dict = {}
    if not toggle.is_checked():
        toggle.click()
        page.wait_for_timeout(400)
    report["opened"] = state()

    logs = []
    page.on("console", lambda message: logs.append(message.text))
    page.on("request", lambda request: logs.append(
        f"REQ {request.method} {request.url.rsplit('/', 1)[-1]} {len(request.post_data or '')}"))

    # SAVE WITHOUT AN EDIT: the empty carrier posts, the save succeeds and stores no sheet
    import sys as _sys
    print("DEBUG_BEFORE_SAVE", repr(state()["carrier"][:40]), "|", state()["badge"], file=_sys.stderr)
    report["saved"] = _save(page)
    # the bytes the FIRST Save left, read here because the second Save runs before the caller can
    # read the file: "the untouched Save kept the sheet absent" is asserted against these bytes.
    if site_file:
        from pathlib import Path as _Path
        report["saved_bytes"] = _Path(site_file).read_text(encoding="utf-8")
    print("DEBUG_SAVE", report["saved"], file=_sys.stderr)
    report["logs"] = logs

    again = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    again.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(800)
    card = again
    report["reloaded"] = state()
    report["header"] = card.evaluate("""(c) => ({
      words: c.querySelector('[data-sc-pal-words]').textContent.trim(),
      key: c.querySelector('[data-sc-pal-key]').textContent.trim(),
      value: c.querySelector('[data-sc-pal-value]').textContent.trim(),
    })""")

    # ONE KEYSTROKE: the carrier fills, the badge stops saying "not stored"
    toggle = card.locator("[data-sc-expert-toggle]").first
    if not toggle.is_checked():
        toggle.click()
        page.wait_for_timeout(400)
    card.evaluate("""(c) => {
      const cm = c.querySelector('[data-sc-expert] .CodeMirror').CodeMirror;
      const line = cm.lineCount() - 1;
      cm.focus();
      cm.setCursor({line: line, ch: cm.getLine(line).length});
    }""")
    page.keyboard.type(" ", delay=40)
    page.wait_for_timeout(500)
    report["edited"] = state()

    # SAVE THE EDIT: the sheet is stored and the reloaded card opens on it
    report["saved_edit"] = _save(page)
    again = page.locator(f'details.sc-card:has(input[name="_site"][value="{site}"])').first
    again.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(800)
    card = again
    report["reloaded_edit"] = state()
    report["header_edit"] = card.evaluate("""(c) => ({
      words: c.querySelector('[data-sc-pal-words]').textContent.trim(),
      key: c.querySelector('[data-sc-pal-key]').textContent.trim(),
      value: c.querySelector('[data-sc-pal-value]').textContent.trim(),
    })""")
    return report


def _wizard_layout(page, site: str, shots: dict) -> dict:
    """§1–§2's arrangement: the gallery's tiles across, the starting points as ROWS, one width for the
    six steps, and no horizontal overflow at either viewport.

    Every box is read inside ONE card — the page draws a card per site, and a selector that spans the
    page would count another site's tiles. The gallery is measured FIRST, because it is the panel at
    rest and a hidden panel measures zero; step 1 and step 2 are then measured separately, because
    each step hides the other's body.
    """
    card = _card(page, site)
    card.evaluate("(f) => { const d = f.closest('details'); if (d) { d.open = true; } }")
    page.wait_for_timeout(700)
    door = card.locator("[data-pal-start]").first
    if not door.count():
        return {"shown": False}
    report: dict = {"shown": True}

    def boxes(selector: str) -> dict:
        return card.evaluate("""(c, selector) => {
          const box = (node) => {
            if (!node) { return null; }
            const r = node.getBoundingClientRect();
            return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width),
                    h: Math.round(r.height)};
          };
          const nodes = [...c.querySelectorAll(selector)].map(box);
          const doc = document.documentElement;
          const wizard = c.querySelector('[data-sc-wizard]');
          return {
            viewport: window.innerWidth,
            wizard: box(wizard),
            nodes: nodes,
            perRow: nodes.length ? nodes.filter((n) => n && Math.abs(n.y - nodes[0].y) < 4).length : 0,
            overflow: doc.scrollWidth - doc.clientWidth,
            bodyOverflow: document.body.scrollWidth - document.body.clientWidth,
          };
        }""", selector)

    def state() -> dict:
        return {"tiles": boxes("[data-sc-wiz-start]"), "steps": boxes("[data-sc-wiz-step]")}

    # the panel at rest: the presets as tiles, and the door beside them
    report["rest"] = boxes("[data-sc-preset-tile]")
    report["door"] = card.evaluate("""(c) => {
      const node = c.querySelector('[data-pal-start]');
      if (!node) { return null; }
      const r = node.getBoundingClientRect();
      const tile = c.querySelector('[data-sc-preset-tile]');
      const tr = tile ? tile.getBoundingClientRect() : null;
      return {tag: node.tagName, w: Math.round(r.width), h: Math.round(r.height),
              tileW: tr ? Math.round(tr.width) : null,
              inGallery: Boolean(node.closest('.sc-palgallery')),
              radios: node.querySelectorAll('input').length};
    }""")
    page.screenshot(path=shots["rest"])

    door.click()
    page.wait_for_timeout(400)
    wiz = card.locator("[data-sc-wizard]").first
    report["wide"] = state()
    report["opened"] = card.evaluate("""(c) => {
      const rest = c.querySelector('[data-sc-pal-panel="rest"]');
      const node = c.querySelector('[data-sc-wizard]');
      const block = c.querySelector('[data-sc-block="palette"]');
      return {
        galleryHidden: rest ? rest.hidden : null,
        wizardHidden: node ? node.hidden : null,
        insideBlock: Boolean(node && block && block.contains(node)),
        palettes: c.querySelectorAll('[data-sc-block="palette"]').length,
        wizards: c.querySelectorAll('[data-sc-wizard]').length,
        previews: c.querySelectorAll('iframe[data-sc-preview]').length,
      };
    }""")
    page.screenshot(path=shots["wide"])
    wiz.locator("[data-sc-wiz-derive]").first.click()
    page.wait_for_timeout(300)
    report["wide_steps"] = state()
    page.screenshot(path=shots["wide_steps"])
    # §11: the box is the PAGE's, so the wizard draws no control of its own and nothing is lifted —
    # the one preview stands beside the block whether the wizard is open or not
    report["lifted"] = None
    show = wiz.locator("[data-sc-wiz-show]").first
    if show.count():
        show.click()
        page.wait_for_timeout(200)
        report["lifted"] = card.evaluate("""(c) => {
          const pv = c.querySelector('.sc-brand-pv');
          const frame = pv && pv.querySelector('iframe');
          const r = pv ? pv.getBoundingClientRect() : null;
          return {lift: pv ? pv.classList.contains('sc-pv-lift') : false,
                  position: pv ? getComputedStyle(pv).position : '',
                  box: r ? {x: Math.round(r.x), w: Math.round(r.width), h: Math.round(r.height)} : null,
                  frameTop: frame ? Math.round(frame.getBoundingClientRect().top) : 0};
        }""")
        page.screenshot(path=shots["lifted"])
        show.click()
        page.wait_for_timeout(150)
    # the page's own preview: the wizard has no box of its own, so this is what replaces it
    report["panel"] = card.evaluate("""(c) => {
      const panel = c.querySelector('.sc-site');
      const frame = panel && panel.querySelector('iframe[data-sc-preview]');
      const r = panel ? panel.getBoundingClientRect() : null;
      return {present: Boolean(panel),
              panels: c.querySelectorAll('.sc-site').length,
              frames: c.querySelectorAll('iframe[data-sc-preview]').length,
              box: r ? {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width),
                        h: Math.round(r.height)} : null,
              frameBox: frame ? {w: Math.round(frame.getBoundingClientRect().width),
                                 h: Math.round(frame.getBoundingClientRect().height)} : null};
    }""")

    page.set_viewport_size({"width": 430, "height": 844})
    page.wait_for_timeout(250)
    wiz.locator("[data-sc-wiz-back]").first.click()
    page.wait_for_timeout(200)
    report["narrow"] = state()
    page.screenshot(path=shots["narrow"])
    wiz.locator("[data-sc-wiz-derive]").first.click()
    page.wait_for_timeout(250)
    report["narrow_steps"] = state()
    page.screenshot(path=shots["narrow_steps"])
    page.set_viewport_size({"width": 1440, "height": 900})
    return report


def _identity_layout(page, site: str, shots: dict) -> dict:
    """THE HEADER LINE on the REAL page: 1440, 430, and the ladder of widths between them.

    Every number is the browser's: the header's box, whether the name and the controls share a row,
    the controls' right edge against the header's own padding edge, the address field's width, the
    visible character chips, and the overflow of the header, of the card and of the document. The two
    renders are the card at the two widths the design measures.
    """
    card = page.locator(f'details.sc-card:has(form.sc-form:has(input[value="{site}"]))').first
    card.evaluate("(d) => { d.open = true; }")
    page.wait_for_timeout(250)

    def measure() -> dict:
        return page.evaluate("""(site) => {
          const card = [...document.querySelectorAll('details.sc-card')]
            .find((d) => d.querySelector('form.sc-form input[value="' + site + '"]'));
          const sum = card.querySelector('summary.sc-sum');
          const name = sum.querySelector('.k.mono');
          const r = sum.querySelector('.r');
          const addr = sum.querySelector('.sc-idaddr');
          const field = addr ? addr.querySelector('input') : null;
          const sw = sum.querySelector('.sc-idsw');
          const chips = [...sum.querySelectorAll('.st-chip')];
          const sr = sum.getBoundingClientRect(), rr = r.getBoundingClientRect();
          const nr = name.getBoundingClientRect();
          const cs = getComputedStyle(sum);
          const padRight = parseFloat(cs.paddingRight) + parseFloat(cs.borderRightWidth);
          const cr = card.getBoundingClientRect();
          const offenders = [...card.querySelectorAll('*')]
            .filter((n) => n.getBoundingClientRect().right > cr.right + 1)
            .slice(0, 5)
            .map((n) => n.tagName.toLowerCase()
                        + (n.getAttribute('class') ? '.' + n.getAttribute('class').split(' ')[0] : ''));
          const headerOffenders = [...sum.querySelectorAll('*')]
            .filter((n) => n.getBoundingClientRect().right > sr.right + 1)
            .map((n) => n.tagName.toLowerCase()
                        + (n.getAttribute('class') ? '.' + n.getAttribute('class').split(' ')[0] : ''));
          return {
            card: Math.round(card.getBoundingClientRect().width),
            header: [Math.round(sr.width), Math.round(sr.height)],
            nameWidth: Math.round(nr.width),
            oneRow: Math.abs(nr.top - rr.top) < 12,
            controlsRight: Math.round(rr.right),
            headerEdge: Math.round(sr.right - padRight),
            fieldWidth: field ? Math.round(field.getBoundingClientRect().width) : null,
            frameWidth: addr ? Math.round(addr.getBoundingClientRect().width) : null,
            switchWidth: sw ? Math.round(sw.getBoundingClientRect().width) : null,
            chipsShown: chips.filter((c) => getComputedStyle(c).display !== 'none').length,
            headerOverflow: sum.scrollWidth - sum.clientWidth,
            cardOverflow: card.scrollWidth - card.clientWidth,
            pageOverflow: document.documentElement.scrollWidth
                          - document.documentElement.clientWidth,
            offenders: offenders,
            headerOffenders: headerOffenders,
            parts: Object.fromEntries(
              [['r', r], ['addr', addr], ['flag', sum.querySelector('.st-flag')],
               ['sw', sw], ['name', name]]
                .filter(([, n]) => n)
                .map(([k, n]) => [k, [Math.round(n.getBoundingClientRect().width),
                                      Math.round(n.getBoundingClientRect().right)]])),
          };
        }""", site)

    def settle() -> None:
        page.evaluate("""(site) => {
          const card = [...document.querySelectorAll('details.sc-card')]
            .find((d) => d.querySelector('form.sc-form input[value="' + site + '"]'));
          card.scrollIntoView({block: 'start'});
        }""", site)
        page.wait_for_timeout(250)

    ladder = {}
    for width in (1440, 1050, 884, 700, 620, 560, 430):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(350)
        settle()
        ladder[str(width)] = measure()

    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(300)
    settle()
    card.locator("summary.sc-sum").first.screenshot(path=shots["wide"])
    page.screenshot(path=shots["wide_page"])
    page.set_viewport_size({"width": 430, "height": 844})
    page.wait_for_timeout(400)
    settle()
    card.locator("summary.sc-sum").first.screenshot(path=shots["narrow"])
    page.screenshot(path=shots["narrow_page"])
    page.set_viewport_size({"width": 1440, "height": 900})
    return {"ladder": ladder, "shots": shots}


def _page(page, site: str) -> dict:
    """§11 driven: ONE frame that every block writes into, a panel that holds its place, the phone.

    Every number is read off the live page: the frames the document actually carries, the site's own
    document's `.brand b` / `.hero h1 em` / `.top .nav` before and after the page's controls move
    them, the panel's own `getBoundingClientRect().top` across a scripted scroll of the console's
    scrolling pane, and the same measures at 430 × 844 with the strip's own document read back.
    """
    card = _card(page, site)
    card.evaluate("(f) => { const d = f.closest('details'); if (d) { d.open = true; } }")
    page.wait_for_timeout(800)
    report: dict = {}

    report["frames"] = page.eval_on_selector_all(
        "iframe", "nodes => nodes.map((n) => n.getAttribute('src'))")
    report["frames_in_blocks"] = page.eval_on_selector_all(
        "[data-sc-block] iframe", "nodes => nodes.length")
    report["cards"] = page.locator("details.sc-card").count()
    report["wizard_slots"] = page.locator("[data-sc-wiz-show]").count()

    frame = page.locator("form.sc-form iframe[data-sc-preview]").first

    def read(selector: str):
        return frame.evaluate("(f, sel) => { const n = f.contentDocument.querySelector(sel);"
                              " return n ? n.textContent.trim() : null; }", selector)

    def every_headline() -> list:
        """`.brand b` in EVERY frame on the page: the pin says only ONE frame may change."""
        return page.eval_on_selector_all(
            "iframe", "nodes => nodes.map((f) => { const n = f.contentDocument"
                      " && f.contentDocument.querySelector('.brand b');"
                      " return n ? n.textContent.trim() : null; })")

    report["heads_before"] = every_headline()
    report["before"] = {"brand": read(".brand b"), "hero": read(".hero h1 em"),
                        "foot": read(".foot-text")}
    card.locator(f'input[name="f.sites.{site}.branding.title"]').first.fill("Foothold Wing Bar")
    card.locator(f'input[name="f.sites.{site}.branding.community_name"]').first.fill(TYPED_NAME)
    page.wait_for_timeout(400)
    report["heads_after"] = every_headline()
    report["after"] = {"brand": read(".brand b"), "hero": read(".hero h1 em"),
                       "foot": read(".foot-text")}
    report["frames_after_typing"] = page.locator("iframe").count()

    def nav_hrefs() -> list:
        return frame.evaluate("(f) => [...f.contentDocument.querySelectorAll('.top .nav a')]"
                              ".map((a) => a.getAttribute('href'))")

    report["nav_before"] = nav_hrefs()
    report["nav_rows_before"] = card.locator(
        '[data-sc-set="nav"] [data-reorder-row]').evaluate_all(
        "nodes => nodes.map((n) => n.getAttribute('data-reorder-id'))")
    grip = card.locator('[data-sc-set="nav"] [data-reorder-row]').first.locator(
        "[data-reorder-handle]").first
    grip.focus()
    page.keyboard.press("Alt+ArrowDown")
    page.wait_for_timeout(400)
    report["nav_after"] = nav_hrefs()
    report["nav_rows_after"] = card.locator(
        '[data-sc-set="nav"] [data-reorder-row]').evaluate_all(
        "nodes => nodes.map((n) => n.getAttribute('data-reorder-id'))")

    def panel_box() -> dict:
        return page.evaluate("""() => {
          const panel = document.querySelector('form.sc-form .sc-site');
          const r = panel.getBoundingClientRect();
          const wrap = document.querySelector('.wrap');
          return {top: Math.round(r.top), height: Math.round(r.height),
                  width: Math.round(r.width), scrollTop: Math.round(wrap.scrollTop),
                  innerWidth: window.innerWidth};
        }""")

    page.evaluate("() => { document.querySelector('.wrap').scrollTop = 0; }")
    page.wait_for_timeout(200)
    report["rest"] = panel_box()
    page.evaluate("() => { document.querySelector('.wrap').scrollTop += 900; }")
    page.wait_for_timeout(300)
    report["scrolled"] = panel_box()
    page.evaluate("() => { document.querySelector('.wrap').scrollTop += 900; }")
    page.wait_for_timeout(300)
    report["scrolled2"] = panel_box()

    # the phone: the box goes above the blocks, 210 px tall, and stays put
    page.set_viewport_size({"width": 430, "height": 844})
    page.wait_for_timeout(600)
    page.evaluate("() => { document.querySelector('.wrap').scrollTop = 0; }")
    page.wait_for_timeout(200)
    report["phone"] = page.evaluate("""() => {
      const panel = document.querySelector('form.sc-form .sc-site');
      const pageEl = panel.closest('.sc-page');
      const frame = panel.querySelector('iframe');
      const blocks = [...pageEl.querySelectorAll('[data-sc-block]')];
      const r = panel.getBoundingClientRect();
      const first = blocks.length ? blocks[0].getBoundingClientRect() : null;
      const doc = frame.contentDocument;
      const shown = (sel) => { const n = doc.querySelector(sel);
        return n ? getComputedStyle(n).display : 'absent'; };
      return {top: Math.round(r.top), height: Math.round(r.height),
              width: Math.round(r.width), page: getComputedStyle(pageEl).display,
              firstBlockTop: first ? Math.round(first.top) : null,
              frameHeight: Math.round(frame.getBoundingClientRect().height),
              caption: getComputedStyle(panel.querySelector('.sc-pv-cap')).display,
              nav: shown('.top .nav'), foot: shown('.foot'),
              eyebrow: shown('.hero .eyebrow'), bar: shown('.top'),
              hero: (doc.querySelector('.hero h1 em') || {}).textContent};
    }""")
    page.evaluate("() => { document.querySelector('.wrap').scrollTop += 852; }")
    page.wait_for_timeout(300)
    report["phone_scrolled"] = page.evaluate("""() => {
      const panel = document.querySelector('form.sc-form .sc-site');
      const r = panel.getBoundingClientRect();
      return {top: Math.round(r.top), height: Math.round(r.height),
              scrollTop: Math.round(document.querySelector('.wrap').scrollTop)};
    }""")
    page.evaluate("() => { document.querySelector('.wrap').scrollTop += 852; }")
    page.wait_for_timeout(300)
    report["phone_scrolled2"] = page.evaluate("""() => {
      const panel = document.querySelector('form.sc-form .sc-site');
      const r = panel.getBoundingClientRect();
      return {top: Math.round(r.top), height: Math.round(r.height),
              scrollTop: Math.round(document.querySelector('.wrap').scrollTop)};
    }""")
    return report


#: EVERY card's own frame, in the page's card order: the bar's name, the hero's name and the palette
#: the document's root is wearing. Read per FORM, so a frame that follows the wrong card is a frame
#: whose row differs from its own card's.
CARDS_JS = """() => [...document.querySelectorAll('form.sc-form')].map((form) => {
  const key = (form.querySelector('input[name="_site"]') || {}).value || null;
  const frame = form.querySelector('iframe[data-sc-preview]');
  const doc = frame && frame.contentDocument;
  if (!doc || !doc.documentElement) { return {key: key, brand: null, hero: null, brass: null}; }
  const brand = doc.querySelector('.brand b');
  const hero = doc.querySelector('.hero h1 em');
  const cs = doc.defaultView.getComputedStyle(doc.documentElement);
  return {
    key: key,
    brand: brand ? brand.textContent.trim() : null,
    hero: hero ? hero.textContent.trim() : null,
    brass: cs.getPropertyValue('--brass').trim(),
  };
})"""


def _twocard(page, site: str) -> dict:
    """§1 with TWO cards on ONE page: the card being edited follows, the other card does not.

    Every frame on the page is read before and after each edit, indexed by the card that owns it —
    so a global frame resolution shows up as card 1 changing while card 2 is the one typed into.
    Both halves are driven: a text leaf, and a palette edit inside the same card's wizard.
    """
    page.evaluate("() => document.querySelectorAll('details.sc-card').forEach((d) => { d.open = true; })")
    page.wait_for_timeout(800)
    report: dict = {"cards": page.locator("form.sc-form").count(),
                    "frames": page.locator("iframe[data-sc-preview]").count()}
    requests: list[str] = []
    page.on("request", lambda request: requests.append(f"{request.method} {request.url}"))
    report["before"] = page.evaluate(CARDS_JS)

    card = _card(page, site)
    card.locator(f'input[name="f.sites.{site}.branding.title"]').first.fill(TYPED_BAR)
    card.locator(f'input[name="f.sites.{site}.branding.community_name"]').first.fill(TYPED_SECOND)
    page.wait_for_timeout(400)
    report["typed"] = page.evaluate(CARDS_JS)

    card, wiz = _wizard_card(page, site)
    report["palette_shown"] = bool(wiz.count())
    if report["palette_shown"]:
        _choose_one_colour(page, wiz, "#4a7fd1")
        wiz.locator("[data-sc-wiz-derive]").first.click()
        page.wait_for_timeout(300)
        wiz.locator('[data-sc-wiz-more="accent"]').first.click()
        page.wait_for_timeout(80)
        report["palette_before"] = page.evaluate(CARDS_JS)
        _set_token(wiz, "accent", "--brass", "#6e9ff4")
        page.wait_for_timeout(150)
        report["palette_after"] = page.evaluate(CARDS_JS)
    report["requests"] = requests
    return report


def _save(page) -> dict:
    posts = []
    bodies = []
    def on_response(reply):
        if reply.request.method == "POST":
            posts.append(f"{reply.status} {reply.request.url}")

    def on_request(request):
        if request.method == "POST":
            bodies.append((request.url, request.is_navigation_request(), request.post_data or ""))

    page.on("response", on_response)
    page.on("request", on_request)
    try:
        with page.expect_navigation(timeout=30000):
            page.locator("form.sc-form .sc-actions button[type='submit']").first.click()
        page.wait_for_timeout(300)
    finally:
        page.remove_listener("response", on_response)
        page.remove_listener("request", on_request)
    notices = page.locator(".notice-msg").all_inner_texts()
    return {"url": page.url, "notices": notices, "posts": posts, "bodies": bodies,
            "text": len(page.inner_text("body"))}


def main(argv: list[str]) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        print(json.dumps({"unavailable": f"playwright: {error}"}))
        return 0

    base, user, password, page_path, phase, site = argv[1:7]
    report: dict = {"phase": phase}
    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        _sign_in(page, base, user, password)
        page.goto(base + page_path, wait_until="load")
        report["scripts"] = page.eval_on_selector_all(
            "script[src]", "nodes => nodes.map(n => n.getAttribute('src'))")
        report["cards"] = page.locator("details.sc-card").count()

        if phase == "add":
            report["add"] = _add_counts(page, site)
            report["closed"] = _closed_set_controls(page, site)
            report["preset"] = _preset_click(page, site)
            card = _card(page, site)
            rows = card.locator(f'[data-cfg-seq="sites.{site}.branding.footer_links"] [data-cfg-row]')
            if not rows.count():
                # the Add control did nothing: report THAT rather than failing on a missing row, so
                # the caller asserts the counts it was given
                report["saved"] = None
                report["rows_after_save"] = 0
            else:
                row = rows.last
                row.locator('input[name$=".label"]').fill(LINK_LABEL)
                row.locator('input[name$=".href"]').fill(LINK_HREF)
                report["saved"] = _save(page)
                report["rows_after_save"] = page.locator(
                    f'[data-cfg-seq="sites.{site}.branding.footer_links"] [data-cfg-row]').count()
        elif phase == "bind":
            report["bind"] = _bind(page, site)
        elif phase == "draft":
            report["draft"] = _draft(page, site)
        elif phase == "switch":
            report["switch"] = _switch(page, site)
        elif phase == "identity":
            report["identity"] = _identity(page, site)
        elif phase == "identitylayout":
            report["identity"] = _identity_layout(page, site, {
                "wide": argv[7], "wide_page": argv[8],
                "narrow": argv[9], "narrow_page": argv[10]})
        elif phase == "character":
            report["character"] = _character(page, site)
        elif phase == "reorder":
            report["reorder"] = _reorder(page, site)
        elif phase == "expert":
            report["expert"] = _expert(page, site)
        elif phase == "nojs":
            report["nojs"] = _nojs(browser, base, user, password, page_path, site)
        elif phase == "mark":
            report["mark"] = _mark(page, site)
        elif phase == "press":
            report["press"] = _press(page, site)
        elif phase == "nojspick":
            report["nojspick"] = _nojspick(browser, base, user, password, page_path, site)
        elif phase == "preset":
            report["preset"] = _preset(page, site)
        elif phase == "wizard":
            report["wizard"] = _wizard(page, site)
        elif phase == "wizardadjust":
            report["wizard"] = _wizard_adjust(page, site)
        elif phase == "wizardnow":
            report["wizard"] = _wizard_now(page, site)
        elif phase == "paletteedit":
            report["wizard"] = _palette_edit(page, site)
        elif phase == "palettekeep":
            report["wizard"] = _palette_keep(page, site)
        elif phase == "palettepaint":
            report["paint"] = _palette_paint(page, site)
        elif phase == "stored":
            report["wizard"] = _wizard_stored(page, site)
        elif phase == "expertmode":
            report["expert"] = _expert_mode(page, site)
        elif phase == "expertempty":
            report["expert"] = _expert_empty(page, site, argv[7] if len(argv) > 7 else None)
        elif phase == "wizardlayout":
            shots = os.path.join(tempfile.gettempdir(), "dcssb-wizard")
            os.makedirs(shots, exist_ok=True)
            report["wizard"] = _wizard_layout(page, site, {
                "rest": os.path.join(shots, "wiz-rest.png"),
                "wide": os.path.join(shots, "wiz-1440.png"),
                "wide_steps": os.path.join(shots, "wiz-1440-steps.png"),
                "lifted": os.path.join(shots, "wiz-lifted.png"),
                "narrow": os.path.join(shots, "wiz-430.png"),
                "narrow_steps": os.path.join(shots, "wiz-430-steps.png")})
        elif phase == "page":
            report["page"] = _page(page, site)
        elif phase == "twocard":
            report["twocard"] = _twocard(page, site)
        elif phase == "remove":
            card = _card(page, site)
            rows = card.locator(f'[data-cfg-seq="sites.{site}.branding.footer_links"] [data-cfg-row]')
            target = None
            for index in range(rows.count()):
                candidate = rows.nth(index)
                if candidate.locator('input[value="' + LINK_HREF + '"]').count():
                    target = candidate
                    break
            if target is None:
                report["removed"] = {"found": False,
                                     "rows": rows.count()}
            else:
                target.locator("[data-cfg-remove]").first.click()
                page.wait_for_timeout(80)
                report["removed"] = {"found": True, "after_click": rows.count(),
                                     "saved": _save(page)}
                report["rows_after_save"] = page.locator(
                    f'[data-cfg-seq="sites.{site}.branding.footer_links"] [data-cfg-row]').count()
        report["errors"] = errors
        browser.close()
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
