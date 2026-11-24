"""The DCS update's row PULSE, and the cache re-read that ends it.

The control starts a write that takes minutes, so its glyph wears the console's busy idiom while the
action runs (``busy`` on the row's control record), and the pulse ends on the ONE fact the row can
read: the console's cached DCS check, refreshed by the route once the updater has returned. The three
pins that matter are the render (the glyph pulses and no neighbour does), the END (a refreshed check
spends it), and the ROUTE (a successful update really does refresh the cache) — plus the ceiling,
because a pulse that outlives the default two minutes is the whole point for this operation.
"""
from __future__ import annotations

import asyncio
import re
from datetime import timedelta

import pytest
from packaging.version import parse

from services.webservice import dcs_update
from services.webservice.pages import actions as actions_page
from services.webservice.pages import lists as lists_page
from services.webservice.pages import nodes as nodes_page
from test_webui_node_dcs_control import (  # noqa: F401 - fixtures/parsers imported on purpose
    BRANCH, DCS_FORM, DeafNode, LATEST, RESTART_FORM, SHUTDOWN_FORM,
    button_classes, form_markup, render, row_of, seed,
)
from test_webui_node_dcs_update import (  # noqa: F401 - fixtures imported on purpose
    CONFIRM, DCS_PATH, DcsNode, Fleet, NODE, app_with, config_dir, fleet,
)
from test_webui_write_pause import client, open_page, sign_in, token_of

#: the DCS control's declaration — the pulse is read off it, never re-spelled here
ACTION = next(a for a in actions_page.NODE_ACTIONS if a.key == actions_page.DCS_UPDATE_KEY)


class UpdatingNode(DcsNode):
    """A node that ANSWERS the availability check, and moves its facts when the updater runs."""

    async def is_dcs_update_available(self):
        return self.latest if parse(self.version) < parse(self.latest) else None


def start_pulse(node_name: str = NODE) -> None:
    """Record the expectation the route records on the press, against the cache as it stands."""
    actions_page.remember_awaiting_change(
        "node", node_name, ACTION, "", False,
        revision=actions_page._dcs_checked_stamp(node_name))


def _field(markup: str, name: str) -> str:
    """One hidden form value out of the rendered dialog."""
    match = re.search(r'name="' + name + r'" value="([^"]*)"', markup)
    assert match, f"the dialog carries no {name}"
    return match.group(1)


async def submit_update(http, version: str = LATEST):
    """POST the DCS update exactly as its dialog does: token, confirm token, version, warn time."""
    page = await open_page(http, "/nodes")
    dialog = await http.post(DCS_PATH + CONFIRM,
                             data={"node": NODE, "_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.text
    return await http.post(DCS_PATH, data={"node": NODE,
                                           "_csrf_token": _field(dialog.text, "_csrf_token"),
                                           "_confirm_token": _field(dialog.text, "_confirm_token"),
                                           "version": version, "warn_time": "60"})


# ------------------------------------------------------------------ 1. the glyph pulses
@pytest.mark.asyncio
async def test_the_glyph_pulses_while_the_update_it_started_is_pending(config_dir, fleet):
    """The DCS control wears ``busy`` + ``aria-busy``; its neighbours wear neither."""
    seed(NODE, available=True, latest=LATEST)
    start_pulse()
    html = await render(config_dir, fleet.declare(NODE).cluster(node=DeafNode(NODE, master=True)))
    row = row_of(html, NODE)
    print("\n[pending] " + " ".join(button_classes(row, DCS_FORM)))
    assert "busy" in button_classes(row, DCS_FORM)
    assert 'aria-busy="true"' in form_markup(row, DCS_FORM)
    for path in (RESTART_FORM, SHUTDOWN_FORM):
        assert "busy" not in button_classes(row, path), path
        assert 'aria-busy="true"' not in form_markup(row, path), path
    assert DeafNode.ASKED == [], "the render asked the node"


# ------------------------------------------------------------------ 2. the check ends it
@pytest.mark.asyncio
async def test_the_pulse_ends_when_the_check_has_been_refreshed(config_dir, fleet):
    """A record produced AFTER the press spends the expectation — and states the row is current.

    Both halves are asserted: the glyph is PULSING before the re-check, so "no busy" afterwards is
    the check spending it rather than a pulse that never lit.
    """
    seed(NODE, available=True, latest=LATEST)
    start_pulse()
    cluster = fleet.declare(NODE).cluster(node=DeafNode(NODE, master=True))
    before = await render(config_dir, cluster)
    assert "busy" in button_classes(row_of(before, NODE), DCS_FORM), "the pulse never lit"
    checked = dcs_update.entry(NODE)
    assert checked is not None and checked.checked_at is not None
    later = checked.checked_at + timedelta(seconds=1)
    dcs_update.record(NODE, has_dcs=True, branch=BRANCH, version=LATEST, latest=LATEST,
                      available=False, at=later)
    html = await render(config_dir, cluster)
    classes = button_classes(row_of(html, NODE), DCS_FORM)
    print("\n[spent] " + " ".join(classes))
    assert "busy" not in classes
    assert "busy" not in html
    assert "attention" not in classes, "the amber mark must be gone with the update"


# ------------------------------------------------------------------ 3. the route refreshes it
@pytest.mark.asyncio
async def test_a_successful_update_refreshes_the_cached_answer_the_row_reads(config_dir, fleet):
    """Through the REAL route and the REAL action: the cache ends on what the node installed.

    Without this the console keeps reporting the update it has just installed until the six-hour
    sweep — the row's chip and its amber mark both read this cache.
    """
    node = UpdatingNode(NODE, master=True)
    cluster = fleet.declare(NODE).cluster(node=node)
    seed(NODE, available=True, latest=LATEST)      # as the background poller left it
    app = app_with(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await submit_update(http)
        assert reply.status_code in (200, 303), reply.text
        # the re-read is FIRE-AND-FORGET: let the task it scheduled run against the double
        for _ in range(20):
            if dcs_update.entry(NODE) is not None and dcs_update.entry(NODE).version == LATEST:
                break
            await asyncio.sleep(0)
    cached = dcs_update.entry(NODE)
    assert cached is not None and cached.version == LATEST, cached
    assert cached.available is False, cached
    assert node.version == LATEST, "the updater never ran"


# ------------------------------------------------- 3c. the press is recorded, a failure drops it
@pytest.mark.asyncio
async def test_the_route_records_the_expectation_before_the_action_and_a_failure_drops_it(
        config_dir, fleet, monkeypatch):
    """The pulse's two halves on the route: recorded BEFORE the action, gone when it fails."""
    from core.actions import ActionResult

    seen: dict = {}

    async def failing(qualname, ctx, **params):
        seen.update(actions_page.awaiting_store())      # what the store held WHILE the action ran
        return ActionResult(success=False, message="the test refused it")

    monkeypatch.setattr(actions_page, "call_action", failing)
    cluster = fleet.declare(NODE).cluster(node=UpdatingNode(NODE, master=True))
    seed(NODE, available=True, latest=LATEST)
    app = app_with(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        await submit_update(http)
    assert "node:" + NODE.casefold() in seen, "the route did not record the press before the action"
    assert actions_page.awaiting_store() == {}, "a failed update left its glyph pulsing"


# ------------------------------------------------------------------ 3b. the stream it ends on
@pytest.mark.asyncio
async def test_the_nodes_stream_answers_the_rows_the_pulse_lives_in(config_dir, fleet):
    """The table the pulse is rendered in is a real stream target under the page's own capability."""
    app = app_with(config_dir, fleet.declare(NODE).cluster(node=DeafNode(NODE, master=True)))
    _, fragments = lists_page.stream_paths(nodes_page.PAGE)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(fragments)
    assert reply.status_code == 200, reply.text
    assert NODE in reply.text, "the frame carries no node rows"
    assert "act-btn" in reply.text, "the frame drew no control glyph (the strip's `icons` import)"


# ------------------------------------------------------------------ 4. how long it may last
def test_the_pulse_ceiling_is_the_update_s_own_not_the_default():
    """Minutes long: two minutes would stop the pulse mid-update, which is the bug it fixes."""
    assert ACTION.ceiling == actions_page.DCS_PULSE_SECONDS
    assert ACTION.ceiling > actions_page.AWAIT_CHANGE_SECONDS
    seed(NODE, available=True, latest=LATEST)
    start_pulse()
    stamp = actions_page._dcs_checked_stamp(NODE)
    stamped = actions_page.awaiting_store()["node:" + NODE.casefold()]["at"]
    assert actions_page.awaiting_change("node", NODE, ACTION.key, "", False,
                                        now=stamped + 130, revision=stamp) is True, \
        "the default two-minute ceiling ended the pulse mid-update"
    assert actions_page.awaiting_change("node", NODE, ACTION.key, "", False,
                                        now=stamped + ACTION.ceiling + 1, revision=stamp) is False
