"""§11 on the Site settings page, DRIVEN in a real browser: one frame, and a panel that holds.

The three pins here cannot be read off markup or a stylesheet:

* the site's own document must FOLLOW the page's controls — typing a bar's name changes `.brand b`
  inside the ONE frame, typing a community name changes `.hero h1 em`, and a dragged nav row moves
  the frame's `.top .nav` before Save;
* the panel must HOLD its place: `getBoundingClientRect().top` is the same number before and after a
  scripted scroll of the console's own pane (a sticky box that is confined to a card, or to a grid
  row, moves — and only a real layout says which);
* at the phone the box is the 210 px strip ABOVE the blocks, and that strip is the preview
  document's own sheet drawing the bar, the name and the sentence only.

The page is served by the real application on a real loopback port and driven by
``tests/site_settings_probe.py``'s ``page`` phase in its own process.
"""
from __future__ import annotations

import pytest

from services.webservice import site_pages
from test_webui_site_settings_page import (  # noqa: F401 - fixtures imported on purpose
    ADMIN, SITE_KEY, _probe, live_config, served,
)

#: the community name the probe types into ``community_name`` (the design's own example).
TYPED_NAME = "5th Guards"
#: the bar's name the probe types into ``branding.title``.
TYPED_BAR = "Foothold Wing Bar"


def _address(path: str) -> str:
    """A nav page's address on the ROOT site: ``""`` is the site's own root."""
    return "/" + path.lstrip("/")


#: the nav page ids in the site's shipped order, with the address the root site serves each at.
NAV_ORDER = [("overview", site_pages.NAV_PATHS["overview"]),
             ("leaderboard", site_pages.NAV_PATHS["leaderboard"]),
             ("players", site_pages.NAV_PATHS["players"])]


#: the probe's answer, kept for this process: one browser run answers every pin below, and the
#: numbers are the data the pins read (the server behind them is not needed once they are read).
_PROBE_CACHE: dict = {}


@pytest.fixture
def report(served):
    """One driven run of the page, reused by every pin in this process (each run costs a browser)."""
    if "page" not in _PROBE_CACHE:
        _PROBE_CACHE["page"] = _probe(served, "page")["page"]
    return _PROBE_CACHE["page"]


def test_one_frame_per_card_and_no_block_draws_one(report, served):
    """PIN 1, live: ONE frame per site card — its `src` the chrome-preview route, and none in a block."""
    assert len(report["frames"]) == report["cards"] >= 1, (
        f"the page renders {report['frames']} for {report['cards']} card(s)")
    for src in report["frames"]:
        assert site_pages.CHROME_PREVIEW_PATH in (src or ""), src
    assert report["frames_in_blocks"] == 0, "a block draws a frame of its own"
    assert report["wizard_slots"] == 0, "the retired Show the site slot is still on the page"
    assert report["frames_after_typing"] == len(report["frames"]), (
        "typing unmounted or duplicated a frame — each is mounted once and never unmounted")


def test_typing_writes_the_one_frame(report):
    """PIN 2, live: the values land in the CARD's frame, and in no other card's frame."""
    before, after = report["before"], report["after"]
    assert before["brand"] != TYPED_BAR and after["brand"] == TYPED_BAR, (
        f"typing the bar's name left '.brand b' at {after['brand']!r}")
    assert before["hero"] != TYPED_NAME and after["hero"] == TYPED_NAME, (
        f"typing the community name left '.hero h1 em' at {after['hero']!r}")
    assert after["foot"] == before["foot"], "a typed bar's name moved the footer's holder"
    heads_before, heads_after = report["heads_before"], report["heads_after"]
    assert heads_after[0] == TYPED_BAR, heads_after
    assert heads_after[1:] == heads_before[1:], (
        f"typing in one card changed another card's frame: {heads_before} -> {heads_after}")


#: the SECOND card the fixture's site file draws — the card a global frame never wrote into.
SECOND_KEY = "group1"
#: the community name typed into that card: nothing its site file already renders.
TYPED_SECOND = "Night Wings"


def test_typing_in_the_second_card_writes_that_card_s_own_frame(served):
    """PIN 2, live, with TWO cards: the card being edited follows and the other card does not.

    A page with one card cannot tell a per-card frame from a single global one — the first card IS
    the page's frame. The second card is typed into here, and BOTH frames are read before and after,
    indexed by the card that owns each one.
    """
    two = _probe(served, "twocard", site=SECOND_KEY)["twocard"]
    assert two["cards"] == 2, f"the fixture draws {two['cards']} cards; this pin needs two"
    assert two["frames"] == 2, f"the fixture draws {two['frames']} frames for two cards"
    before, typed = two["before"], two["typed"]
    assert [row["key"] for row in before] == [SITE_KEY, SECOND_KEY], before
    assert before[1]["hero"] != TYPED_SECOND, (
        f"the second card already showed {before[1]['hero']!r}")
    assert typed[1]["hero"] == TYPED_SECOND, (
        f"typing in the second card left its frame at {typed[1]['hero']!r}")
    assert typed[1]["brand"] == TYPED_BAR, (
        f"typing the bar's name left the second frame's bar at {typed[1]['brand']!r}")
    assert typed[0] == before[0], (
        f"typing in the second card wrote the FIRST card's frame: {before[0]} -> {typed[0]}")


def test_a_palette_edit_in_the_second_card_repaints_that_card_s_frame_only(served):
    """PIN 1, live, with TWO cards: the wizard repaints ITS card's document, never the first's.

    The wizard resolves its frame from its own form; a page-wide lookup would set the candidate
    palette on the first card's document while the operator worked in the second.
    """
    two = _probe(served, "twocard", site=SECOND_KEY)["twocard"]
    assert two["palette_shown"], "the second card draws no palette wizard"
    before, after = two["palette_before"], two["palette_after"]
    assert [row["key"] for row in before] == [SITE_KEY, SECOND_KEY], before
    assert after[1]["brass"].lower() == "#6e9ff4", (
        f"the palette edit did not repaint the second card's frame: {before[1]['brass']!r} -> "
        f"{after[1]['brass']!r}")
    assert after[0]["brass"] == before[0]["brass"], (
        f"the palette edit repainted the FIRST card's frame: {before[0]['brass']!r} -> "
        f"{after[0]['brass']!r}")


def test_a_nav_reorder_writes_the_bar_s_nav(report, served):
    """PIN 2, live: one move DOWN in Menu & Tiles reorders `.top .nav` before Save."""
    rows_before, rows_after = report["nav_rows_before"], report["nav_rows_after"]
    assert rows_before[:2] == ["overview", "leaderboard"], rows_before
    assert rows_after[:2] == ["leaderboard", "overview"], (
        f"the page's own rows did not move: {rows_after}")
    moved = [ident for ident in rows_after if ident in dict(NAV_ORDER)]
    assert report["nav_after"] == [_address(site_pages.NAV_PATHS[ident]) for ident in moved], (
        f"the frame's nav reads {report['nav_after']} after the rows read {rows_after}")


def test_the_panel_holds_its_place_while_the_column_scrolls(report):
    """PIN 4: once pinned, `getBoundingClientRect().top` is the SAME number over 900 px of column."""
    pinned, later = report["scrolled"], report["scrolled2"]
    assert pinned["innerWidth"] >= 1360, f"this run measured a {pinned['innerWidth']} px window"
    assert later["scrollTop"] - pinned["scrollTop"] >= 900, (
        f"the column did not scroll: {pinned['scrollTop']} -> {later['scrollTop']}")
    assert pinned["top"] == later["top"], (
        f"the panel moved {later['top'] - pinned['top']} px while the column scrolled")
    assert report["rest"]["top"] > pinned["top"], (
        "the panel never reached its pinned edge, so this run measured nothing")
    assert pinned["width"] == 640, f"the box is {pinned['width']} px wide beside the column"


def test_the_phone_is_the_strip_above_the_blocks(report):
    """PIN 5: at 430 the box is the 210 px strip, ABOVE the first block, and it still holds."""
    phone = report["phone"]
    assert phone["page"] == "flex", (
        f"the phone's page is a {phone['page']} — a sticky child of a grid cannot travel")
    assert phone["frameHeight"] == 210, f"the strip is {phone['frameHeight']} px tall"
    assert phone["firstBlockTop"] is not None and phone["top"] < phone["firstBlockTop"], (
        f"the strip is not above the blocks: {phone['top']} vs {phone['firstBlockTop']}")
    assert phone["caption"] == "none", "the caption still costs a quarter of the screen"
    # the strip is the preview document's own sheet: the bar, the name and the sentence, and none of
    # the bands the blocks above it already draw
    assert phone["bar"] != "none", "the strip dropped the bar it exists for"
    assert phone["nav"] == "none", "the strip still draws the menu row"
    assert phone["foot"] == "none", "the strip still draws the footer"
    assert phone["eyebrow"] == "none", "the strip still draws the eyebrow"
    assert phone["hero"] == TYPED_NAME, f"the strip's headline reads {phone['hero']!r}"
    pinned, later = report["phone_scrolled"], report["phone_scrolled2"]
    assert later["scrollTop"] - pinned["scrollTop"] >= 852, (
        f"the phone did not scroll: {pinned['scrollTop']} -> {later['scrollTop']}")
    assert pinned["top"] == later["top"], (
        f"the strip moved {later['top'] - pinned['top']} px while the page scrolled")
    assert later["height"] == pinned["height"] == phone["height"], (
        "the strip changed height while it was pinned")
