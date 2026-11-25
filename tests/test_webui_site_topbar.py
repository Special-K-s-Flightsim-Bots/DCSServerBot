"""The public site's top bar: one band, an identity chip and a brass console door.

WHAT THIS SUITE PINS
--------------------
* the cluster's markup: the identity chip is a link to the viewer's own page carrying the name, the
  console door is a ``.btn-door`` with one glyph and its words, and both sit in ``.cluster`` beside
  the language pill;
* the door is drawn exactly when the viewer reaches the console — a signed-in viewer without the
  capability gets no door, an anonymous visitor gets the sign-in chip in the name's slot;
* the glyph state reuses the ``Command Center`` msgid for its accessible name, so the bar adds no
  string and no catalog entry;
* the stylesheet: the phone block neither hides nor reorders the door, the label collapses to the
  glyph below 1180 with the words kept as ``aria-label``/``title``, and the new component rules read
  tokens only (the sheet's own literal pin covers them — this file names the rules so a deletion is
  caught);
* the RENDERED bar, in a real browser through the app: the four states at five widths, one 36px
  band, no wrapped string, no horizontal overflow, the cluster never reaching the nav, a long name
  truncated and a short one whole.

The rendered measurements run in their own process (:mod:`tests.site_topbar_probe`) because
Playwright's sync API leaves an event loop installed in the process that hosts it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from conftest import ensure_playwright_browser, link_or_copy
from test_webui_site_overview import (SERVERS_YAML, StubBus, build, bus,  # noqa: F401  (fixtures)
                                      config_dir, page_markup)
from test_webui_site_pages import door_config

REPO = Path(__file__).resolve().parent.parent
SITE_CSS = REPO / "services" / "webservice" / "static" / "site.css"
SITE_BASE = REPO / "services" / "webservice" / "templates" / "site_base.html"
PROBE = REPO / "tests" / "site_topbar_probe.py"

#: The site the bar is rendered for: a long enough title to squeeze the row, and the four nav
#: entries this build serves. ``door_config`` also enables ONE sign-in door, so the anonymous bar
#: draws the sign-in chip the design puts in the name's slot.
SITE = {"root": {"slug": "", "branding": {"title": "DCS Special K"},
                 "nav": ["overview", "leaderboard", "servers", "players"]}}


def bar_config() -> dict:
    """The site's own config with its sign-in door enabled, and this card's title."""
    config = door_config()
    config["sites"] = SITE
    return config


#: ``(page, roles, display name)``; ``roles`` None renders the page anonymously. The anonymous page
#: is rendered FIRST, because the shell resolves its identity from ``app.state`` once set.
STATES = (
    ("signed-out", None, ""),
    ("signed-in-long", ("Admin",), "ThirtyCharacterPilotName123456"),
    ("signed-in-short", ("Admin",), "|DTC| nash"),
    ("no-console", ("DCS",), "|DTC| nash"),
)

#: The widths the probe measures; the design's two, plus the three the note names.
WIDTHS = (1440, 1024, 900, 620, 430)


def render_states(config_dir: Path, destination: Path) -> None:
    """Write one ``<state>.html`` per bar state, from the REAL shell, into *destination*.

    The page is rendered through the app with a stub bus and a stubbed identity, so the document is
    the shipped template's output and the design width's own measurements are the bar's.
    """
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus
    from services.webservice.auth import Identity

    destination.mkdir(parents=True, exist_ok=True)
    previous = ServiceRegistry._singletons.get(ServiceBus)
    ServiceRegistry._singletons[ServiceBus] = StubBus()
    try:
        app, _ = build(config_dir, config=bar_config())
        for name, roles, display in STATES:
            if roles is not None:
                identity = Identity(subject="specialk", backend="local",
                                    roles=frozenset(roles), display_name=display)
                app.state.webui_auth = types.SimpleNamespace(
                    authenticate=lambda request, who=identity: who)
            (destination / f"{name}.html").write_text(page_markup(app, "/"), encoding="utf-8")
    finally:
        if previous is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = previous
    link_or_copy(REPO / "services" / "webservice" / "static", destination / "static")


def measure(directory: Path, shots: Path) -> dict[tuple[str, int], dict]:
    """``{(page, width): row}`` from the probe, or a skip when this interpreter has no browser."""
    ensure_playwright_browser(sys.executable)
    finished = subprocess.run([sys.executable, str(PROBE), str(directory), str(shots)],
                              capture_output=True, text=True, encoding="utf-8", timeout=300,
                              check=True)
    payload = json.loads(finished.stdout.strip().splitlines()[-1])
    if isinstance(payload, dict) and payload.get("unavailable"):
        pytest.skip(payload["unavailable"])
    return {(row["page"], row["width"]): row for row in payload}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """``{(page, width): measurement}`` plus the shot directory, from the real templates."""
    directory = tmp_path_factory.mktemp("site-topbar")
    config = directory / "config"
    config.mkdir()
    (config / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    pages = directory / "pages"
    render_states(config, pages)
    return measure(pages, directory / "shots"), directory / "shots"


# --------------------------------------------------------------------------- the cluster's markup

def _header(markup: str) -> str:
    """The top bar of a rendered page, without the page below it."""
    return markup.split("</header>", 1)[0]


def _cluster(markup: str) -> str:
    """The cluster's own markup: the last group in the bar, before its two closing tags."""
    header = _header(markup)
    cluster = header.split('<div class="cluster">', 1)[1]
    return cluster.rsplit("</div>", 2)[0]


@pytest.fixture
def headers(config_dir, bus):
    """The top bar of all four states, rendered through the app."""
    from services.webservice.auth import Identity

    app, _ = build(config_dir, config=bar_config())
    result = {"signed-out": _header(page_markup(app, "/"))}
    for name, roles, display in STATES[1:]:
        identity = Identity(subject="specialk", backend="local", roles=frozenset(roles),
                            display_name=display)
        app.state.webui_auth = types.SimpleNamespace(authenticate=lambda request, who=identity: who)
        result[name] = _header(page_markup(app, "/"))
    return result


def test_the_console_door_is_a_brass_pill_with_one_glyph_and_its_words(headers):
    """The door is a ``.btn-door``: a pill, one console glyph, and the ``Command Center`` label."""
    door = _cluster(headers["signed-in-short"]).split('class="btn btn-door"', 1)[1]
    door = door.split("</a>", 1)[0]
    assert 'class="btn btn-door"' in _cluster(headers["signed-in-short"])
    assert 'class="ico"' in door and "viewBox=\"0 0 24 24\"" in door, "the door carries no glyph"
    assert 'aria-hidden="true"' in door, "the glyph is not hidden from the accessible name"
    assert "Command Center" in door.split('class="door-lbl">', 1)[1], "the door lost its label"
    assert 'aria-label="Command Center"' in door, "the glyph state has no accessible name"
    assert 'title="Command Center"' in door, "the glyph state has no tooltip"
    assert "btn-sm" not in _header(headers["signed-in-short"]), "the door still wears the small box"


def test_the_identity_chip_is_a_link_to_the_viewers_own_page_carrying_the_name(headers):
    """The chip opens the viewer's statistics and carries the name in an inner ``.nm``."""
    chip = _cluster(headers["signed-in-short"]).split('<span class="who">', 1)[1]
    chip = chip.split("</a></span>", 1)[0]
    assert 'href="/me"' in chip, f"the chip does not open the viewer's own page: {chip}"
    assert 'title="My statistics"' in chip, "the chip lost its own existing title"
    assert '<span class="nm">|DTC| nash' in chip, f"the name is not a truncating span: {chip}"


def test_a_viewer_without_console_access_gets_no_door(headers):
    """``dashboard.view`` is the door's condition: the identity stays, the door does not."""
    cluster = _cluster(headers["no-console"])
    assert 'class="who"' in cluster, "the viewer without console access lost their chip"
    assert "btn-door" not in cluster, "a door is drawn for a viewer the console would refuse"
    assert 'class="langchip"' in cluster, "the language pill left the cluster"


def test_the_sign_in_chip_takes_the_names_slot_when_signed_out(headers):
    """Anonymous: no identity chip, and the sign-in control takes its place inside the cluster."""
    cluster = _cluster(headers["signed-out"])
    assert 'class="who"' not in cluster, "an anonymous visitor carries an identity chip"
    assert '<a class="btn btn-door" href=' in cluster, f"no sign-in chip in the cluster: {cluster}"
    assert "Sign in" in cluster
    assert "ico" not in cluster, "the sign-in chip wears the console glyph"


def test_the_glyph_state_reuses_the_command_center_msgid():
    """No new string: the accessible name is the label's own existing msgid."""
    source = SITE_BASE.read_text(encoding="utf-8")
    assert "aria-label=\"{{ _('Command Center') }}\"" in source
    assert "title=\"{{ _('Command Center') }}\"" in source
    assert "{% trans %}Command Center{% endtrans %}" in source
    # the strings the bar prints are the ones already in the catalogs, and no other
    bodies = set(re.findall(r"{% trans %}(.*?){% endtrans %}", source))
    assert bodies == {"Community record", "Site", "Command Center", "Sign in"}, bodies


# ------------------------------------------------------------------------------ the stylesheet

def _phone_block() -> str:
    return _media("@media (max-width: 620px) {", SITE_CSS.read_text(encoding="utf-8"), must=".top")


def _media(prefix: str, sheet: str, *, must: str = "") -> str:
    """The first ``@media`` block that starts with *prefix* and contains *must*, brace-matched.

    The sheet carries more than one block at the same width (the hero's mark, the rating chart's
    plot, the bar), so a caller names a string its block must contain.
    """
    start = 0
    while True:
        start = sheet.index(prefix, start)
        depth = 0
        end = len(sheet)
        for index, char in enumerate(sheet[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        else:
            raise AssertionError(f"{prefix!r} never closes")
        block = sheet[start:end]
        if must in block:
            return block
        start = end


def _block(prefix: str, sheet: str) -> str:
    """The rule whose selector starts with *prefix*, brace-matched, comments stripped."""
    start = sheet.index(prefix)
    depth = 0
    for index, char in enumerate(sheet[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return re.sub(r"/\*.*?\*/", "", sheet[start:index + 1], flags=re.S)
    raise AssertionError(f"the rule starting {prefix!r} never closes")


def test_the_phone_block_neither_hides_nor_reorders_the_door():
    """Below 620 the door is drawn: no ``display: none`` on the button, no order for the chip."""
    phone = _phone_block()
    assert not re.search(r"\.top \.btn\s*\{[^}]*display:\s*none", phone), \
        "the phone block hides the console door again"
    assert not re.search(r"\.top \.who\s*\{[^}]*order:", phone), \
        "the phone block reorders the identity chip out of the cluster"
    # the field is the one control that sheds something on a phone
    assert re.search(r"\.top \.search button\s*\{[^}]*display:\s*none", phone)


def test_the_door_collapses_to_its_glyph_below_1180_and_keeps_its_words():
    """The glyph replaces the label, only for a door that has one, and only below 1180."""
    sheet = SITE_CSS.read_text(encoding="utf-8")
    narrow = _media("@media (max-width: 1180px) {", sheet)
    assert re.search(r"\.top \.btn-door:has\(\.ico\)[^{]*\{[^}]*width:\s*var\(--chip-h\)", narrow), \
        "the glyph state does not collapse the door to its own box"
    assert re.search(r"\.top \.btn-door:has\(\.ico\) \.door-lbl\s*\{\s*display:\s*none", narrow), \
        "the label is not the part that collapses"
    # the words survive as attributes, which the markup pin covers; the sign-in chip has no glyph,
    # so the same block must not hide ITS label
    assert narrow.count(":has(.ico)") == 2, "a rule without the glyph guard would hide the sign-in chip"


def test_every_rule_the_cluster_adds_is_in_the_sheet_and_reads_a_token():
    """The rules the bar is built from exist, and the sheet's literal pin covers each of them."""
    sheet = SITE_CSS.read_text(encoding="utf-8")
    for selector in (".top .cluster", ".top .who a", ".top .who a .nm", ".top .langchip",
                     ".top .btn-door", ".top .btn-door .ico", ".top .search", ".brand > span",
                     ".brand b, .brand small"):
        assert selector in sheet, f"the sheet carries no rule for {selector}"
    cluster = _block(".top .cluster", sheet)
    assert "flex: 0 0 auto" in cluster and "white-space: nowrap" in cluster, cluster
    door = _block(".top .btn-door {", sheet)
    assert "height: var(--chip-h)" in door and "white-space: nowrap" in door, door
    assert "border-radius: var(--radius-pill)" in door, door
    for literal in re.findall(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(", door + cluster):
        raise AssertionError(f"a literal colour in the cluster's rules: {literal}")


def test_the_bar_height_tokens_follow_the_wrap():
    """The bar's height token steps with the rows the note designs: 64, then 116, then 162."""
    sheet = SITE_CSS.read_text(encoding="utf-8")
    assert re.search(r"--top-h-2:\s*116px", sheet) and re.search(r"--top-h-3:\s*162px", sheet)
    assert re.search(r"--top-h:\s*var\(--top-h-2\)", _media("@media (max-width: 900px) {", sheet)), \
        "the two-row band does not follow the wrap"
    assert re.search(r"--top-h:\s*var\(--top-h-3\)", _phone_block()), \
        "the three-row band does not follow the wrap"


# ----------------------------------------------------------------------------- the rendered bar

@pytest.mark.parametrize("state", [name for name, _roles, _shown in STATES])
def test_the_rendered_bar_is_one_band_of_thirty_six_pixel_controls(rendered, state):
    """The identity, the door and the language pill share one 36px band at every width."""
    measurements, _shots = rendered
    for width in WIDTHS:
        row = measurements[(state, width)]
        assert row["clusterH"] == 36, f"{state}@{width}: cluster {row['clusterH']}px"
        heights = {value for value in row["heights"].values() if value is not None}
        assert heights == {36}, f"{state}@{width}: controls {row['heights']}"
        assert len(set(row["centres"])) == 1 or max(row["centres"]) - min(row["centres"]) <= 1, \
            f"{state}@{width}: the controls sit on {sorted(row['centres'])}"


@pytest.mark.parametrize("state", [name for name, _roles, _shown in STATES])
def test_nothing_on_the_rendered_bar_wraps_or_overflows(rendered, state):
    """No string takes a second line, the row stays inside the viewport, and the cluster keeps off
    the nav at 1440, 1024, 900, 620 and 430."""
    measurements, _shots = rendered
    for width in WIDTHS:
        row = measurements[(state, width)]
        assert row["doc"]["scrollW"] == row["doc"]["clientW"], \
            f"{state}@{width}: the bar scrolls the page horizontally"
        for part, lines in row["lines"].items():
            assert lines <= 1, f"{state}@{width}: the {part} wraps to {lines} lines"
        assert not row["clusterHitsNav"], f"{state}@{width}: the cluster reaches the nav"
        assert not row["doorHitsSearch"], f"{state}@{width}: the door reaches the search"
        assert row["minText"] >= 13, f"{state}@{width}: {row['minText']}px painted on the bar"


def test_the_rendered_door_survives_the_phone_and_sheds_only_its_words(rendered):
    """At 430 the door is drawn, 36x36, and its words are still its accessible name."""
    measurements, _shots = rendered
    for width, label_drawn in ((1440, True), (1024, False), (620, False), (430, False)):
        row = measurements[("signed-in-long", width)]
        assert row["hasDoor"], f"no door at {width}"
        assert row["doorLabel"] is label_drawn, f"the label state at {width} is {row['doorLabel']}"
        assert row["doorName"] == "Command Center", row["doorName"]
        assert row["doorTitle"] == "Command Center", row["doorTitle"]
        assert row["heights"]["door"] == 36, row["heights"]
        if not label_drawn:
            box = row["boxes"]["door"]
            assert (box["w"], box["h"]) == (36, 36), f"the glyph state is {box['w']}x{box['h']}"


def test_the_rendered_name_truncates_a_long_tag_and_keeps_a_short_one_whole(rendered):
    """The chip is the name's container: a 30-character tag is ellipsised, ``|DTC| nash`` is not."""
    measurements, _shots = rendered
    for width in WIDTHS:
        long = measurements[("signed-in-long", width)]
        short = measurements[("signed-in-short", width)]
        assert long["nameClipped"] is True, f"the long name fits whole at {width}"
        assert short["nameClipped"] is False, f"the short name truncates at {width}"
        name = long["boxes"]["name"]
        assert name["w"] <= long["boxes"]["bar"]["w"], "the name is wider than the bar"
        assert name["r"] <= long["boxes"]["bar"]["r"] + 0.5, "the name overflows the bar's right end"


def test_the_rendered_bar_carries_no_door_without_console_access_and_no_chip_before_sign_in(
        rendered):
    """The door exists exactly when the viewer reaches the console; anonymous draws the sign-in chip."""
    measurements, _shots = rendered
    for width in WIDTHS:
        assert measurements[("no-console", width)]["hasDoor"] is False, \
            f"a door at {width} for a viewer the console refuses"
        signed_out = measurements[("signed-out", width)]
        assert signed_out["hasSignIn"] is True, f"no sign-in chip at {width}"
        assert signed_out["whoHref"] is None, f"an identity chip on the anonymous bar at {width}"
        for state in ("signed-in-long", "signed-in-short"):
            assert measurements[(state, width)]["whoHref"] == "/me", \
                f"{state}@{width}: the chip does not open the viewer's own page"
