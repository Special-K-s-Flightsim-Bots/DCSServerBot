"""The pulse lasts until the GLYPH CHANGES, not until the action call returns.

A control is busy while (1) it is the control that was pressed -- this session issued a write
through it -- and (2) the observable it moves has not moved yet, bounded by
``services.webservice.pages.actions.AWAIT_CHANGE_SECONDS``. The expectation record names which
observable its action moves (the row status here). A failed write drops the expectation immediately.
The seam's in-flight half marks the ROW, not a glyph (see ``test_webui_busy_control.py``)."""
from __future__ import annotations

import asyncio
import re
import types

import pytest

from core import Status
from core.actions import in_flight_targets, reset_in_flight
from services.webservice.pages import actions as actions_page
from test_webui_write_pause import (  # noqa: F401 - fixtures are imported on purpose
    PASSWORD, Cluster, build, clean_registry, client, config_dir, instance, open_page, sign_in,
    token_of,
)

#: the two halves of the signal, asserted separately so a regression names which one broke (shared
#: kept in sync by copy, not import, so the other pulse files are left untouched).
BUSY_ATTRIBUTE = 'aria-busy="true"'

#: the Startup control's own path, read off the declaration (never a typed literal): index 0 is
#: 'startup' today, and a lookup by key cannot break if the tuple is reordered.
STARTUP_PATH = next(action.path for action in actions_page.SERVER_ACTIONS if action.key == "startup")


def booting_server(name, managed_by=None, *, status=Status.SHUTDOWN, node_name="dcs-a-01",
                   instance_name="Host-A-Instance", startup=None):
    """A server double whose ``startup`` does NOT flip the status -- a boot in progress.

    That is the honest shape where the glyph has not swapped when the next render happens.
    ``startup`` is injectable so a test can also make the start FAIL.
    """
    mission = types.SimpleNamespace(pause=None, unpause=None)

    async def _noop_startup(modify_mission=True, use_orig=True, preset=None):  # noqa: ARG001
        return None

    srv = types.SimpleNamespace(
        name=name, display_name=name, status=status,
        node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=instance_name), port=10309,
        current_mission=mission, players={}, is_remote=False,
        locals={"managed_by": list(managed_by)} if managed_by else {},
        startup=startup or _noop_startup,
    )
    return srv


def boot_fleet(*, status=Status.SHUTDOWN, startup=None):
    """A one-server fleet: SRS-1 (hosted by ``Host A``), SHUTDOWN, with a non-flipping startup."""
    a = booting_server("SRS-1", ["Host A"], status=status, startup=startup)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a)])


async def start(http, name="SRS-1"):
    """POST the Startup control the SHUTDOWN row offers, through the session's own token.

    The row's control posts to its DIALOG since (Startup carries a maintenance option), so the
    OFFERED path is ``STARTUP_PATH + /confirm`` while the action route itself is still ``STARTUP_PATH``
    — which is what this POST targets (the action's default option applies when the body names none).
    """
    html = await open_page(http)
    assert f'action="{STARTUP_PATH}{actions_page.CONFIRM_SUFFIX}"' in html, \
        "a SHUTDOWN row must offer Startup (through its dialog, )"
    return await http.post(STARTUP_PATH, data={"server": name, "_csrf_token": token_of(html)})


# 1. the pulse outlives the call

@pytest.mark.asyncio
async def test_a_submitted_write_pulses_until_the_glyph_changes(config_dir, clean_registry):
    """After a successful start whose status has NOT moved, the row is busy.

    Proved with the seam's in-flight set EMPTY -- the action has returned -- which is exactly why the
    pulse must anchor on the status.
    """
    cluster = boot_fleet()
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")

        before = await open_page(http)
        assert BUSY_ATTRIBUTE not in before, "the pre-flight render must be clean"

        reply = await start(http)
        assert reply.status_code == 303, reply.text

 # the action RETURNED (so nothing is in flight) and the status is UNCHANGED (a boot in
 # progress): the seam's set is empty, yet the row must still say "still working".
        assert in_flight_targets() == frozenset(), "the seam holds nothing: the call has returned"
        assert cluster.servers[0].status == Status.SHUTDOWN, "the double does not flip on startup"

        flying = await open_page(http)
        assert BUSY_ATTRIBUTE in flying, "the actor's own write must pulse until the glyph changes"
        assert re.search(r'class="act-btn[^"]*\bbusy\b', flying), \
            "the strip button must carry the animating class"
        assert "an action on SRS-1 is running; this can take a moment" in flying

    assert reset_in_flight() is None


@pytest.mark.asyncio
async def test_the_pulse_stops_when_the_glyph_changes(config_dir, clean_registry):
    """The expectation is spent the moment the row's status differs — the glyph swap ends it."""
    cluster = boot_fleet()
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        assert (await start(http)).status_code == 303

        flying = await open_page(http)
        assert BUSY_ATTRIBUTE in flying, "still SHUTDOWN -> still pulsing"

 # the DCS broadcast moves the server on: the glyph changes, so the pulse must go
        cluster.servers[0].status = Status.RUNNING
        after = await open_page(http)
        assert BUSY_ATTRIBUTE not in after, "the status changed, so the expectation is spent"
        assert "an action on SRS-1 is running; this can take a moment" not in after


@pytest.mark.asyncio
async def test_the_pulse_stops_at_the_cap(config_dir, clean_registry, monkeypatch):
    """A start that never comes up must not pulse forever : at the cap it stops.

    The clock is advanced past:data:`AWAIT_CHANGE_SECONDS` AFTER the write is recorded, so the
    recorded stamp stays real and only the READ sees a later moment — the boundary is exercised
    through the real render path, not a hand-built control dict.
    """
    cluster = boot_fleet()
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        assert (await start(http)).status_code == 303
        assert BUSY_ATTRIBUTE in await open_page(http), "inside the window -> pulsing"

 # jump the clock well past the ceiling
        monkeypatch.setattr(actions_page.time, "monotonic", lambda: 1_000_000_000.0)
        capped = await open_page(http)
        assert BUSY_ATTRIBUTE not in capped, "at the cap the pulse stops — it does not run forever"
        assert cluster.servers[0].status == Status.SHUTDOWN, "the row still shows the truth"


@pytest.mark.asyncio
async def test_a_failed_write_drops_the_expectation_immediately(config_dir, clean_registry):
    """A FAILED start must not pulse for two minutes while the notice says it failed ."""
    async def exploding_startup(modify_mission=True, use_orig=True, preset=None):  # noqa: ARG001
        raise TimeoutError("dcs never came up")

    cluster = boot_fleet(startup=exploding_startup)
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        assert (await start(http)).status_code == 303, "a typed refusal is a 303 + a notice"

        after = await open_page(http)
        assert "Timeout while starting up" in after, "the notice says the start failed"
        assert BUSY_ATTRIBUTE not in after, "and the row must NOT be pulsing"
        assert "is running; this can take a moment" not in after

    assert reset_in_flight() is None


# ------------------------------------------------- 2. the store is process state

@pytest.mark.asyncio
async def test_a_render_right_after_the_press_sees_the_pulse_with_no_cookie_round_trip(
        config_dir, clean_registry):
    """The fresh render sees the expectation at the moment it renders.

    The action is made to BLOCK, so the two requests overlap exactly as they do in the browser: the store
    is PROCESS-side and written BEFORE the action runs, so the render finds it immediately. A session
    store could only have seen it after the POST's ``Set-Cookie``.
    """
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_startup(modify_mission=True, use_orig=True, preset=None):  # noqa: ARG001
        entered.set()
        await release.wait()

    cluster = boot_fleet(startup=blocking_startup)
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        before = await open_page(http)
        assert BUSY_ATTRIBUTE not in before, "the pre-flight render must be clean"

 # the POST the interceptor fires, in the background...
        posted = asyncio.create_task(http.post(
            STARTUP_PATH, data={"server": "SRS-1", "_csrf_token": token_of(before)}))
 #...and the render the at-once navigation lands on, while the action is still running
        await asyncio.wait_for(entered.wait(), timeout=5)
        flying = await open_page(http)
        assert BUSY_ATTRIBUTE in flying, \
            "the render taken while the action runs must already show the pulse"
        assert "an action on SRS-1 is running; this can take a moment" in flying

        release.set()
        assert (await posted).status_code == 303

    assert reset_in_flight() is None


@pytest.mark.asyncio
async def test_a_second_viewer_of_the_same_row_sees_the_pending_signal(config_dir, clean_registry):
    """The store is PROCESS state , so the signal is a property of the ROW, not a session.

    This is the honest reading of a process-side store, and the reason the non-disclosure rule
    matters: the signal is only ever consulted for a row the caller is ALREADY rendering
    (``server_controls`` walks the caller's scoped source), so a second viewer of the same row sees
    that something is running on it — and a viewer who cannot see the row learns nothing at all
    (pinned in ``tests/test_webui_busy_pulse_scope``).
    """
    cluster = boot_fleet()
    app = build(config_dir, cluster)

    async with client(app) as actor:
        await sign_in(actor, "mod1")
        assert (await start(actor)).status_code == 303
        assert BUSY_ATTRIBUTE in await open_page(actor), "the actor sees the expectation"

    async with client(app) as other:
        await sign_in(other, "specialk")           # Admin: same row, its OWN session
        page = await open_page(other)
    assert BUSY_ATTRIBUTE in page, \
        "the expectation is process-side : a second viewer of the SAME row sees it"
    assert 'value="SRS-1"' in page, "the row is still the same server"


def test_awaiting_change_is_bounded_and_spent_by_the_status():
    """The pure rule, pinned without a browser: unchanged status pulses; changed status does not.

    Fast and direct, so a regression in the comparison is named here rather than only end to end. The
    control the record names is part of the rule since.
    """
    startup = next(action for action in actions_page.SERVER_ACTIONS if action.key == "startup")

    actions_page.remember_awaiting_change("server", "SRS-1", startup, "SHUTDOWN", False)
 # the key is the guard's own key: a padded, odd-cased name finds the same entry
    assert actions_page.awaiting_change("server", "  srs-1  ", "startup",
                                        "SHUTDOWN", False) is True
 # the row moved on: the glyph swapped, so the expectation is spent — AND DROPPED, so
 # it can never re-arm when the row returns to SHUTDOWN inside the window
    assert actions_page.awaiting_change("server", "SRS-1", "startup", "RUNNING", False) is False
    assert actions_page.awaiting_store() == {}, "a spent expectation must not linger"
 # no entry at all: not awaiting
    assert actions_page.awaiting_change("server", "SRS-B", "startup", "SHUTDOWN", False) is False

 # a fresh press re-arms from the NEW baseline; forget drops it (a failed/refused write)
    actions_page.remember_awaiting_change("server", "SRS-1", startup, "SHUTDOWN", False)
    actions_page.forget_awaiting_change("server", "SRS-1")
    assert actions_page.awaiting_change("server", "SRS-1", "startup", "SHUTDOWN", False) is False


def test_a_second_press_replaces_the_entry_and_never_stacks():
    """One expectation per TARGET : the newest press owns it, and the store stays bounded.

    Also pins the cap: a process store outlives every session, so the oldest entry is dropped rather
    than the mapping growing without limit.
    """
    by_key = {action.key: action for action in actions_page.SERVER_ACTIONS}
    actions_page.remember_awaiting_change("server", "SRS-1", by_key["startup"], "SHUTDOWN", False)
    actions_page.remember_awaiting_change("server", "SRS-1", by_key["pause"], "SHUTDOWN", False)
    store = actions_page.awaiting_store()
    assert list(store) == ["server:srs-1"], "a repeat press replaces its entry, never stacks"
    assert store["server:srs-1"]["action"] == "pause"

    for index in range(actions_page.AWAIT_MAX_PENDING + 4):
        actions_page.remember_awaiting_change("server", f"SRS-{index}", by_key["startup"],
                                              "SHUTDOWN", False)
    assert len(actions_page.awaiting_store()) == actions_page.AWAIT_MAX_PENDING, \
        "the store is bounded by count (a process store outlives every session)"
