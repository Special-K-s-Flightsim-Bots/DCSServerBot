"""The pulse belongs to the CONTROL YOU PRESSED, and ends when THAT action's state moves.

Two defects on the tree carrying the flag pair: the pulse was stamped on the ROW, so pressing
*Maintenance* blinked *Startup* too; and the expectation waited on the status even for a flag action
that never moves it, so it ran to ``AWAIT_CHANGE_SECONDS`` and never stopped. The expectation record
now carries the submitted action's KEY and the observable its action moves (``WriteAction.observable``);
the seam's in-flight half marks the ROW, never a glyph. Driven through the real app, the real local
login backend, the real flag actions and the real read model."""
from __future__ import annotations

import re
import types
from typing import Any

import pytest

from core import Status
from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page
from test_webui_busy_control import in_flight
from test_webui_server_maintenance import clean_scheduler_registry  # noqa: F401 - fixture by name
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, recording_bot, sign_in,
    token_of,
)

SERVERS = servers_page.SERVERS_PATH

#: the accessible signal of the CONTROL's pulse — asserted on the control that was pressed and
#: asserted ABSENT from its siblings (the whole point of this card).
BUSY = 'aria-busy="true"'
#: the ROW-level marker the seam's in-flight half renders (the other half, kept off the glyphs).
ROW_MARKER = 'data-busy="true"'

STARTUP = next(action.path for action in actions_page.SERVER_ACTIONS if action.key == "startup")
#: the path the SHUTDOWN ROW's Startup control actually posts to: the control carries a
#: maintenance option, so — like every node control — it posts to its DIALOG and the option is chosen
#: there. The action route (``STARTUP``) is still the target of the dialog's OWN form, so the tests
#: POST there once the row has offered the control.
STARTUP_ROW = STARTUP + actions_page.CONFIRM_SUFFIX
MAINTENANCE = next(action.path for action in actions_page.SERVER_ACTIONS
                   if action.key == "maintenance")
END_MAINTENANCE = next(action.path for action in actions_page.SERVER_ACTIONS
                       if action.key == "end_maintenance")


def form_for(html: str, path: str) -> str:
    """The ONE ``<form>`` that posts to *path* — a control, with its button and its own signals."""
    found = re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)
    assert found, f"the page rendered no control posting to {path}"
    return found[0]


# ------------------------------------------------------------------------------- test doubles

class PulseServer:
    """A server double carrying the status, the flag, the four restart triggers and a ``startup``.

    ``startup`` deliberately does NOT flip the status (a boot in progress) and counts its calls, so a
    test can assert the flag pair never runs it.
    """

    def __init__(self, name, node_name="dcs-a-01", managed_by=(), *, status=Status.SHUTDOWN,
                 maintenance=False):
        self.name = name
        self.display_name = name
        self.status = status
        self.maintenance = maintenance
        self.restart_pending = False
        self.on_empty: set = set()
        self.on_mission_end: set = set()
        self.on_coalition_win: set = set()
        self.node = types.SimpleNamespace(name=node_name)
        self.instance = types.SimpleNamespace(name=f"{node_name}-Instance")
        self.port = 10309
        self.current_mission = None
        self.is_remote = False
        self.locals = {"managed_by": list(managed_by)} if managed_by else {}
        self.players: dict[str, Any] = {}
        self.calls: list[str] = []

    async def startup(self, modify_mission=True, use_orig=True, preset=None):  # noqa: ARG002
        self.calls.append("startup")

    def get_active_players(self):
        return list(self.players.values())


def pulse_fleet(*, status=Status.SHUTDOWN, maintenance=False):
    """ONE server — ``SRS-1`` on ``Host A`` — so a control-level assertion names its own row."""
    a = PulseServer("SRS-1", "dcs-a-01", ["Host A"], status=status, maintenance=maintenance)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a)])


def build_console(config_dir, cluster):
    """An install with BOTH plugins: the power controls (mission) and the flag pair (scheduler)."""
    return build(config_dir, cluster, plugins=("mission", "scheduler"))


async def post(http, page, path, name="SRS-1", *, offered=None):
    """POST ONE rendered control, with the session's own token, the target in the BODY.

    ``offered`` is the path the ROW actually renders — a control with a dialog (: Startup now
    carries a maintenance option) posts to ``path + /confirm`` while the dialog's own form still POSTs
    to *path*. Defaults to *path* for the controls that post directly.
    """
    assert f'action="{offered or path}"' in page, f"the page offered no control for {path}"
    return await http.post(path, data={"server": name, "_csrf_token": token_of(page)})


# ============================================================ (a) the pulse is the pressed control

@pytest.mark.asyncio
async def test_pressing_startup_leaves_the_flag_control_dead(config_dir, clean_scheduler_registry):
    """*Startup* must not blink *Maintenance*.

    A SHUTDOWN row offers BOTH controls, so the sibling is on screen and a row-stamped pulse would light
    it. The double's startup does not flip the status, so the expectation is still pending on the next
    render -- this measures the pulse, not the in-flight flicker.
    """
    cluster = pulse_fleet()
    app = build_console(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, SERVERS)
        assert BUSY not in page and ROW_MARKER not in page, "the pre-flight render must be clean"

        reply = await post(http, page, STARTUP, offered=STARTUP_ROW)
        assert reply.status_code == 303, reply.text
        assert cluster.servers[0].calls == ["startup"]

        after = await open_page(http)

    assert cluster.servers[0].status == Status.SHUTDOWN, "the double does not flip on startup"
    assert BUSY in form_for(after, STARTUP_ROW), \
        "the pressed control pulses while its status has not moved"
    assert BUSY not in form_for(after, MAINTENANCE), \
        "the sibling flag control must NOT pulse for an action it did not run"
    assert "an action on SRS-1 is running; this can take a moment" in form_for(after, STARTUP_ROW)


@pytest.mark.asyncio
async def test_pressing_maintenance_leaves_the_startup_glyph_dead(config_dir, clean_scheduler_registry):
    """The mirrored direction, on a SHUTDOWN row: the flag control must not blink *Startup*.

    The flag pair applies in EVERY status (a flag is not a power state), so this is the same row as
    the test above with the OTHER control pressed — the two together pin the scope in both directions.
    """
    cluster = pulse_fleet(maintenance=True)      # the row offers End maintenance
    app = build_console(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, SERVERS)
        assert f'action="{END_MAINTENANCE}"' in page

        reply = await post(http, page, END_MAINTENANCE)
        assert reply.status_code == 303, reply.text
        after = await open_page(http)

    assert cluster.servers[0].maintenance is False, "the flag cleared"
    assert cluster.servers[0].status == Status.SHUTDOWN, "the status never moved"
    assert cluster.servers[0].calls == [], "a flag is not a power operation"
    assert BUSY not in form_for(after, STARTUP_ROW), \
        "the sibling power control must NOT pulse for a flag write"
 # and the flag's own expectation is already spent (Defect 2): nothing on the row is pulsing
    assert BUSY not in after and ROW_MARKER not in after


# ======================================================= (b) the flag expectation ends on the flag

@pytest.mark.asyncio
async def test_pressing_maintenance_ends_its_pulse_at_the_flag_swap(config_dir,
                                                                   clean_scheduler_registry):
    """End to end: the flag toggle, with the server RUNNING throughout.

    The status NEVER moves, so a status-keyed expectation would pulse for the full window and never stop.
    The flag lands before the response returns, so the very next render sees it moved and the expectation
    is spent: no control pulses and the row carries no marker.
    """
    cluster = pulse_fleet(status=Status.RUNNING)
    app = build_console(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, SERVERS)
        assert f'action="{MAINTENANCE}"' in page
        assert BUSY not in page

        set_reply = await post(http, page, MAINTENANCE)
        assert set_reply.status_code == 303, set_reply.text
        after_set = await open_page(http)

        assert cluster.servers[0].maintenance is True
        assert cluster.servers[0].status == Status.RUNNING, "the status never moved"
        assert f'action="{END_MAINTENANCE}"' in after_set, "the glyph swapped: the pulse must end here"
        assert BUSY not in after_set, \
            "the flag moved, so the expectation is spent — no 120-second window"
        assert ROW_MARKER not in after_set, "the write returned: nothing is in flight either"

 # THE SECOND PRESS — the one reported as blinking forever
        clear_reply = await post(http, after_set, END_MAINTENANCE)
        assert clear_reply.status_code == 303, clear_reply.text
        final = await open_page(http)

    assert cluster.servers[0].maintenance is False, "the flag cleared"
    assert cluster.servers[0].status == Status.RUNNING, "the status still never moved"
    assert f'action="{MAINTENANCE}"' in final, "the glyph swapped back"
    assert BUSY not in final, "the cleared flag spends the expectation too — it does not re-arm"
    assert ROW_MARKER not in final


def test_a_flag_expectation_ends_on_the_flag_even_though_the_status_never_moves():
    """The pure rule, pinned without a browser: the observable is the FLAG for the flag pair.

    Fast and direct, so a regression in WHICH value is compared is named here rather than only end to
    end — and the sibling half: the OTHER control of the pair does not pulse for a write it did not run.
    """
    by_key = {action.key: action for action in actions_page.SERVER_ACTIONS}
    set_flag, clear_flag = by_key["maintenance"], by_key["end_maintenance"]

    actions_page.remember_awaiting_change("server", "SRS-1", set_flag, "RUNNING", False)
 # the flag has not moved yet: the expectation holds, though the status never will
    assert actions_page.awaiting_change("server", "SRS-1", "maintenance",
                                       "RUNNING", False) is True
 # the flag moved (the status did NOT): the glyph swapped, so the expectation is spent
    assert actions_page.awaiting_change("server", "SRS-1", "maintenance",
                                       "RUNNING", True) is False
 # the SIBLING control never pulses: its key is not the one the record carries
    assert actions_page.awaiting_change("server", "SRS-1", "end_maintenance",
                                       "RUNNING", False) is False
    assert actions_page.awaiting_change("server", "SRS-1", "startup",
                                       "RUNNING", False) is False

 # the other half of the pair, the same way round
    actions_page.remember_awaiting_change("server", "SRS-1", clear_flag, "RUNNING", True)
    assert actions_page.awaiting_change("server", "SRS-1", "end_maintenance",
                                       "RUNNING", True) is True
    assert actions_page.awaiting_change("server", "SRS-1", "end_maintenance",
                                       "RUNNING", False) is False


def test_the_pair_declares_the_flag_as_its_observable_and_the_power_actions_do_not():
    """The DECLARATION, asserted where the decision lives: one field, read by the record and the read.

    Without this, Defect 2 could come back by a pair of controls that quietly fall back to the default
    (the status) — a change no rendering test would catch on a server whose status happens to move.
    """
    by_key = {action.key: action for action in actions_page.SERVER_ACTIONS}
    for key in ("maintenance", "end_maintenance"):
        assert by_key[key].observable == actions_page.AWAIT_OBSERVABLE_MAINTENANCE, key
    for key in ("startup", "start", "restart", "shutdown", "stop", "pause", "unpause"):
        assert by_key[key].observable == actions_page.AWAIT_OBSERVABLE_STATUS, key


def test_a_sibling_control_never_pulses_for_an_action_it_did_not_run():
    """Defect 1 as a pure pin: the record names the key, and only that key answers ``True``."""
    startup = next(action for action in actions_page.SERVER_ACTIONS if action.key == "startup")
    actions_page.remember_awaiting_change("server", "SRS-1", startup, "SHUTDOWN", False)
    assert actions_page.awaiting_change("server", "SRS-1", "startup",
                                       "SHUTDOWN", False) is True
    for sibling in ("maintenance", "end_maintenance", "pause", "unpause", "restart", "shutdown"):
        assert actions_page.awaiting_change("server", "SRS-1", sibling,
                                           "SHUTDOWN", False) is False, sibling


# ====================================================== (c) the power expectation is unchanged

@pytest.mark.asyncio
async def test_the_power_expectation_still_runs_until_the_status_moves(config_dir,
                                                                      clean_scheduler_registry):
    """A Startup pulses while the row still reads SHUTDOWN, and ends the moment the status moves -- the
    glyph swap."""
    cluster = pulse_fleet()
    app = build_console(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, SERVERS)
        assert (await post(http, page, STARTUP, offered=STARTUP_ROW)).status_code == 303

        flying = await open_page(http)
        assert BUSY in form_for(flying, STARTUP_ROW), "still SHUTDOWN -> still pulsing"

        cluster.servers[0].status = Status.RUNNING      # the DCS broadcast moves the row on
        after = await open_page(http)

    assert BUSY not in after, "the status moved, so the expectation is spent"
    assert "an action on SRS-1 is running; this can take a moment" not in after


def test_the_power_expectation_still_respects_the_ceiling():
    """``AWAIT_CHANGE_SECONDS`` is untouched: at the cap the pulse stops, inside it it holds.

    The clock is injected at the READ, so the recorded stamp stays real and only the comparison sees a
    later moment.
    """
    startup = next(action for action in actions_page.SERVER_ACTIONS if action.key == "startup")
    actions_page.remember_awaiting_change("server", "SRS-1", startup, "SHUTDOWN", False)
    stamped = actions_page.awaiting_store()["server:srs-1"]["at"]

    ceiling = actions_page.AWAIT_CHANGE_SECONDS
    assert actions_page.awaiting_change("server", "SRS-1", "startup", "SHUTDOWN", False,
                                        now=stamped + ceiling - 1) is True
    assert actions_page.awaiting_change("server", "SRS-1", "startup", "SHUTDOWN", False,
                                        now=stamped + ceiling) is False, "at the cap it stops"
    assert actions_page.awaiting_change("server", "SRS-1", "startup", "SHUTDOWN", False,
                                        now=stamped + ceiling * 10) is False


# ======================================================== the seam's half belongs to the ROW

@pytest.mark.asyncio
async def test_an_in_flight_action_marks_the_row_and_lights_no_glyph(config_dir, clean_registry):
    """The seam cannot name the action, so it marks the row.

    A target in flight through the REAL seam must produce the row marker and NOTHING on a control. That is
    the honest home for a fact whose only authority keys the target.
    """
    cluster = pulse_fleet(status=Status.RUNNING)
    app = build_console(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        rest = await open_page(http, SERVERS)
        assert ROW_MARKER not in rest and BUSY not in rest

        async with in_flight("SRS-1"):
            flying = await open_page(http, SERVERS)
        ended = await open_page(http, SERVERS)

    assert ROW_MARKER in flying, "the seam's half marks the ROW"
    assert re.search(r'class="act-strip[^"]*\bbusy\b', flying), \
        "the marker is a class on the strip container"
    assert BUSY not in flying, "no glyph claims the action: the seam cannot say which one is running"
    assert "is running; this can take a moment" not in flying, "and no control edits its wording"
    assert ROW_MARKER not in ended, "the guard cleared, so the marker is gone"


@pytest.mark.asyncio
async def test_the_row_marker_does_not_disclose_a_server_outside_the_scope(config_dir,
                                                                           clean_registry):
    """The scope rule, unchanged: a marker is a property of a row the caller ALREADY has.

    ``hoster-b`` manages Host B; SRS-1 is not in their view at all, so nothing about it — marker or
    glyph — can be rendered for them, whether it is mid-action or not.
    """
    a = PulseServer("SRS-1", "dcs-a-01", ["Host A"], status=Status.RUNNING)
    b = PulseServer("SRS-B", "dcs-b-01", ["Host B"], status=Status.RUNNING)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True, instances={})
    cluster = Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                      instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])
    app = build_console(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "hoster-b")
        async with in_flight("SRS-1"):
            page = await open_page(http, SERVERS)
        async with in_flight("SRS-B"):
            own = await open_page(http, SERVERS)

    assert "SRS-1" not in page, "the out-of-scope row is not rendered at all"
    assert ROW_MARKER not in page and BUSY not in page
    assert ROW_MARKER in own, "their OWN busy server shows the row marker"
    assert "SRS-1" not in own
