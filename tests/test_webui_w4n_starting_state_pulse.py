"""The pulse survives the STARTING (``LOADING``) state, and ends at the SETTLED one.

A real DCS start goes ``SHUTDOWN`` -> ``LOADING`` (most of the boot) -> ``RUNNING``, and NO server
control is gated on ``LOADING`` -- so the row rendered NOTHING for the whole boot and the old end
condition ("the status moved") spent the expectation one or two seconds in. Two changes: the expectation
now ends when the row reaches the action's SETTLED state (``RUNNING``/``PAUSED`` for a start/restart,
``SHUTDOWN``/``STOPPED`` for a shutdown/stop), and ``LOADING`` never ends it; and a pending control
whose own gate no longer matches the row STAYS on the row, busy and DISABLED, so there is a glyph to
pulse through the boot. Pins the pure rule, the row read (``actions_page.awaiting_action``) and the
console end to end. Harness reused from ``test_webui_write_pause.py``.

The glyph itself no longer DEPENDS on an expectation: since a launch can be issued from outside the
console, ``LOADING`` draws the launch control from the row's own state (``_booting_row_control``), so
a pending expectation only adds its busy sentence to the same single control.
"""
from __future__ import annotations

import re
import types

import pytest

from core import Status
from services.webservice.pages import actions as actions_page
from test_webui_busy_await_change import booting_server  # the non-flipping-startup double
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, sign_in, token_of,
)

#: the accessible signal of the CONTROL's pulse, and the animating class, asserted together (shared by
#: copy, not import: other pulse files stay as they are except where this change's semantics
#: supersede them).
BUSY = 'aria-busy="true"'

STARTUP = next(action.path for action in actions_page.SERVER_ACTIONS if action.key == "startup")
STARTUP_ROW = STARTUP + actions_page.CONFIRM_SUFFIX

_BY_KEY = {action.key: action for action in actions_page.SERVER_ACTIONS}


def loading_fleet(*, status=Status.LOADING):
    """ONE server — ``SRS-1`` on ``Host A`` — with the given status (``LOADING`` by default)."""
    a = booting_server("SRS-1", ["Host A"], status=status)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a)])


def form_for(html: str, path: str) -> str:
    """The ONE ``<form>`` that posts to *path* — a control, with its button and its own signals."""
    found = re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)
    assert found, f"the page rendered no control posting to {path}"
    return found[0]


# ============================================ 1. the pure rule — the settled state, not any change

def test_a_start_keeps_pulsing_through_loading_and_ends_at_running():
    """THE card's first change: ``SHUTDOWN`` -> ``LOADING`` -> ``RUNNING`` keeps the pulse to the end.

    The whole point is that ``LOADING`` — the state the person is waiting through — is not the finish.
    Proved through the real store read (``awaiting_change``), with the action key the row would read.
    """
    actions_page.remember_awaiting_change("server", "SRS-1", _BY_KEY["startup"], "SHUTDOWN", False)
 # the boot begins: the row reads LOADING. That is NOT the end — the pulse must survive it.
    assert actions_page.awaiting_change("server", "SRS-1", "startup", "LOADING", False) is True, \
        "the STARTING state must not end the pulse ()"
    assert actions_page.awaiting_change("server", "SRS-1", "startup", "LOADING", False) is True, \
        "it survives repeated LOADING renders — the boot is a minute, not a frame"
 # the boot finishes: RUNNING is settled, so one render ends it AND drops it (no re-arm)
    assert actions_page.awaiting_change("server", "SRS-1", "startup", "RUNNING", False) is False
    assert actions_page.awaiting_store() == {}, "the settled state drops the expectation"


def test_a_shutdown_ends_at_the_settled_down_state_not_at_loading():
    """The mirror: a shutdown/stop settles at ``SHUTDOWN``/``STOPPED`` and not before."""
    actions_page.remember_awaiting_change("server", "SRS-1", _BY_KEY["shutdown"], "RUNNING", False)
 # the row between the two states reads SHUTDOWN via the ``Status`` fold (``SHUTTING_DOWN`` folds
 # to SHUTDOWN), which IS settled for shutdown — but a LOADING row must still not end it
    assert actions_page.awaiting_change("server", "SRS-1", "shutdown", "LOADING", False) is True
    assert actions_page.awaiting_change("server", "SRS-1", "shutdown", "SHUTDOWN", False) is False
    assert actions_page.awaiting_store() == {}


def test_a_restart_submitted_in_a_settled_state_needs_the_row_to_leave_it_first():
    """A restart is submitted WHILE RUNNING, which is already its own settled state, so a bare
    ``state in settled`` test would spend it on the first render.

    The ``departed`` latch fixes that: only once the row has been seen in a NON-settled state does the
    return to RUNNING end it.
    """
    restart = _BY_KEY["restart"]
    actions_page.remember_awaiting_change("server", "SRS-1", restart, "RUNNING", False)
 # rendered at once, the row still reads RUNNING (the restart has not torn the mission down yet):
 # the pulse HOLDS — this is the action running, it is not the finish
    assert actions_page.awaiting_change("server", "SRS-1", "restart", "RUNNING", False) is True, \
        "a restart submitted in RUNNING still has work to do at submission"
 # the row leaves RUNNING for the boot
    assert actions_page.awaiting_change("server", "SRS-1", "restart", "LOADING", False) is True
 #...and returns to a settled state: ENDED (the latch), never re-armed
    assert actions_page.awaiting_change("server", "SRS-1", "restart", "RUNNING", False) is False
    assert actions_page.awaiting_store() == {}
    assert actions_page.awaiting_change("server", "SRS-1", "restart", "RUNNING", False) is False


def test_a_restart_of_a_paused_server_settles_back_at_paused():
    """The PAUSED half of the same rule: a restart of a PAUSED server settles at PAUSED again."""
    actions_page.remember_awaiting_change("server", "SRS-1", _BY_KEY["restart"], "PAUSED", False)
    assert actions_page.awaiting_change("server", "SRS-1", "restart", "PAUSED", False) is True, \
        "unchanged at submission: the restart has not touched it yet"
    assert actions_page.awaiting_change("server", "SRS-1", "restart", "LOADING", False) is True
    assert actions_page.awaiting_change("server", "SRS-1", "restart", "PAUSED", False) is False
    assert actions_page.awaiting_store() == {}


def test_the_ceiling_is_untouched_by_the_settled_rule():
    """``AWAIT_CHANGE_SECONDS`` still stops a start that never comes up — the rule did not move it."""
    actions_page.remember_awaiting_change("server", "SRS-1", _BY_KEY["startup"], "SHUTDOWN", False)
    stamped = actions_page.awaiting_store()["server:srs-1"]["at"]
    ceiling = actions_page.AWAIT_CHANGE_SECONDS
    assert actions_page.awaiting_change("server", "SRS-1", "startup", "LOADING", False,
                                       now=stamped + ceiling - 1) is True
    assert actions_page.awaiting_change("server", "SRS-1", "startup", "LOADING", False,
                                       now=stamped + ceiling) is False, "at the cap it stops"


# ============================================ 2. the row read — WHICH control is pending

def test_awaiting_action_names_the_pending_control_and_clears_at_settlement():
    """The ROW-level read adds: it names the key, so the row can keep that control on screen."""
    actions_page.remember_awaiting_change("server", "SRS-1", _BY_KEY["startup"], "SHUTDOWN", False)
    assert actions_page.awaiting_action("server", "SRS-1", "LOADING", False) == "startup", \
        "a LOADING row still knows a start is pending"
    assert actions_page.awaiting_action("server", "SRS-1", "RUNNING", False) is None, \
        "and the settled state clears it"
    assert actions_page.awaiting_action("server", "SRS-B", "LOADING", False) is None, \
        "no expectation, no pending control"


# 3. the console, end to end

@pytest.mark.asyncio
async def test_a_loading_row_keeps_the_pending_glyph_busy_and_disabled(config_dir, clean_registry):
    """A LOADING row shows the start's glyph — and the pending write draws no SECOND copy of it.

    ``LOADING`` is in no control's ``statuses``, so the glyph the row draws comes from the row's own
    state (``_booting_row_control``): a launch started from Discord, the scheduler or a node power
    operation has no expectation behind it at all, and the operator still sees the server starting.
    A press on THIS console then adds its expectation to the same control — one glyph, never two.
    """
    cluster = loading_fleet()
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
 # nothing recorded yet: the glyph comes from the STATE, busy and disabled
        before = form_for(page, STARTUP_ROW)
        assert BUSY in before, "a LOADING row must pulse the launch glyph without any press"
        assert "disabled" in before
        assert len(re.findall(rf'<form[^>]*action="{re.escape(STARTUP_ROW)}"', page)) == 1

 # the write happened: record the expectation exactly as the route does (submitted on SHUTDOWN)
        actions_page.remember_awaiting_change("server", "SRS-1", _BY_KEY["startup"], "SHUTDOWN", False)
        loading = await open_page(http)

    forms = re.findall(rf'<form[^>]*action="{re.escape(STARTUP_ROW)}".*?</form>', loading, re.S)
    assert len(forms) == 1, f"the row drew {len(forms)} launch glyphs"
    form = forms[0]
    assert BUSY in form, "and it must be the pulsing control"
    assert re.search(r'class="act-btn[^"]*\bbusy\b', form), "carrying the animating class"
    assert "disabled" in form, "it is a statement, not an invitation — disabled while transitional"
 # the row still says nothing false: the server has not come up
    assert "is running; this can take a moment" in form


@pytest.mark.asyncio
async def test_a_loading_row_with_nothing_pending_is_drawn_from_the_state_alone(config_dir,
                                                                              clean_registry):
    """With no write recorded, the glyph is still drawn — and the RENDER records nothing itself.

    The state is the authority, so a launch the console did not issue is visible; the store stays
    empty, which proves the pulse is read at render time rather than invented into the expectation.
    """
    cluster = loading_fleet()
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)

    assert form_for(page, STARTUP_ROW), "nothing pending: the state alone must draw the glyph"
    assert BUSY in page, "and it pulses"
    assert actions_page.awaiting_store() == {}, "the render recorded an expectation of its own"


@pytest.mark.asyncio
async def test_a_settled_row_ends_the_pulse_and_shows_the_offered_controls(config_dir, clean_registry):
    """Once the server reaches RUNNING the pending start is spent and the row shows its real controls."""
    cluster = loading_fleet(status=Status.RUNNING)
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        actions_page.remember_awaiting_change("server", "SRS-1", _BY_KEY["startup"], "SHUTDOWN", False)
        page = await open_page(http)

 # RUNNING settles the start: it is spent, so the row offers its OWN controls (restart/shutdown)
    assert BUSY not in page, "the settled state ends the pulse"
    assert f'action="{STARTUP_ROW}"' not in page, "and the pending startup is gone from the row"
    assert actions_page.awaiting_store() == {}
