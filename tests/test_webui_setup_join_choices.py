"""The joining screen's first screen: its two choices are headlines of equal weight, split visibly.

WHAT THESE PIN, AND WHAT THEY DO NOT. No browser, no bot, no PostgreSQL. The render assertions drive
the SHIPPED ``_join_document`` through the shipped ASGI app. The TYPOGRAPHY claim is pinned on the
ONE stylesheet rule BOTH headings share (``.join-head``): a later edit cannot give one option a
different size, weight or colour than the other without splitting that rule, and the rule is read
from the served stylesheet, not described. The rendered measurement the operator judges by eye lives
in the task's evidence, not here.

There is exactly ONE source of the option headings' look, so the two options cannot drift apart: the
assertion is not "they are both headings in the HTML" (which is not evidence that they LOOK alike)
but "both are the same element with the same class, and that class has exactly one definition".
"""
from __future__ import annotations

import re

import pytest

from conftest import asgi_client
from services.webservice.setup import tour
from services.webservice.setup.app import SETUP_PATH
from test_webui_setup_join import build, enter, seed_install

JOIN_NEW_ACTION = tour.JOIN_NEW_ACTION
JOIN_RENAME_ACTION = tour.JOIN_RENAME_ACTION

#: Each option's headline element — the same element and class for both, on purpose.
_HEAD_RE = re.compile(r'<h2 class="join-head">([^<]*)</h2>')
#: Each option's distinct block.
_BLOCK_RE = re.compile(r'<section class="join-choice">')


async def _first_screen(tmp_path) -> str:
    seed_install(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        return (await enter(client)).text


@pytest.mark.asyncio
async def test_the_two_choices_render_as_two_headline_blocks(tmp_path):
    """Both options are ``<h2 class="join-head">`` inside their own ``<section class="join-choice">``."""
    screen = await _first_screen(tmp_path)
    assert _HEAD_RE.findall(screen) == [
        "A new node",
        "An existing node whose hostname changed",
    ]
    assert len(_BLOCK_RE.findall(screen)) == 2


def test_one_stylesheet_rule_gives_both_headings_the_same_typography():
    """The look of a heading has EXACTLY ONE definition, so the two options cannot differ."""
    style = tour._the_style()
    rules = re.findall(r"\.join-head\{([^}]*)\}", style)
    assert len(rules) == 1, f"the option headings must share one rule, found {rules}"
    body = rules[0]
    assert "font-size" in body, body
    assert "font-weight" in body, body
    assert "color" in body, body
    # and that one rule is a HEADLINE, not the 12.5px muted footnote the choices used to carry
    assert "12.5px" not in body, body
    assert "var(--text-3)" not in body, body


def test_the_split_is_a_border_and_a_gap_not_spacing_alone():
    """The two blocks are distinct: each carries a border, and the pair is laid out with a gap."""
    style = tour._the_style()
    assert re.search(r"\.join-choice\{[^}]*border:", style), "the option block carries no border"
    assert re.search(r"\.join-choices\{[^}]*gap:", style), "the two blocks are not separated by a gap"


@pytest.mark.asyncio
async def test_neither_choice_keeps_the_footnote_style(tmp_path):
    """The old small, low-contrast footnote look is gone from BOTH options, not just the first."""
    screen = await _first_screen(tmp_path)
    choices = re.search(r'<div class="join-choices">.*?</form>', screen, re.S)
    assert choices, "the two choices are not wrapped in one block"
    inside = choices.group(0)
    assert "choice-legend" not in inside, "an option still carries the small grey footnote legend"
    assert 'class="help"' not in inside, "an option still carries the unclassed body ``help`` text"
    # both action values the form contract posts are still present and unchanged
    assert f'value="{JOIN_NEW_ACTION}"' in inside
    assert f'value="{JOIN_RENAME_ACTION}"' in inside


@pytest.mark.asyncio
async def test_the_first_screen_is_still_reachable_at_the_step_path(tmp_path):
    """The presentation change did not move the screen: both URLs still render the question."""
    screen = await _first_screen(tmp_path)
    assert "This node is not configured yet" in screen
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        assert "This node is not configured yet" in (await client.get(tour.STEP_PATH)).text
