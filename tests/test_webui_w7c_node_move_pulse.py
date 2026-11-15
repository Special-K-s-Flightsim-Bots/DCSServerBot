"""A node power action pulses the rows of the SERVERS it starts/stops.

A node power action starts or stops its servers INSIDE the bot's own action, so no per-server write is
submitted from the browser and their rows carried no expectation. The fix
(``pages/actions.node_moved_names``) seeds the SAME store a per-server write writes, keyed by the
SERVER target and describing each affected server through the ``SERVER_ACTIONS`` record (``shutdown``
for *offline*, ``startup`` for *online*); their rows pulse and END by the rules already in force (the
settled state reached, a failure, or the ceiling). Pins: an accepted node action makes the affected
rows render ``aria-busy`` while the seam's in-flight set is EMPTY (and an unaffected node's rows do
not); the end conditions; a server already settled is NOT seeded and a node with a power-off RECORD
seeds only the servers it stopped; a refused write seeds nothing; a second action REPLACES rather than
stacks. Driven through the real app and the real admin node actions."""
from __future__ import annotations

import asyncio
import re
import types
from typing import Any

import pytest

from core import Status
from core.actions import in_flight_targets, reset_in_flight
from core.data.maintenance import record_power_off, reset_power_records
from services.webservice.pages import actions as actions_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import servers as servers_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, recording_bot, sign_in,
    token_of,
)

NODES = nodes_page.NODES_PATH
SERVERS = servers_page.SERVERS_PATH
ONLINE = "/actions/node/online"
OFFLINE = "/actions/node/offline"
RESTART = "/actions/node/restart"
CONFIRM = actions_page.CONFIRM_SUFFIX
NODE_A = "dcs-a-01"
NODE_B = "dcs-b-01"

#: the accessible signal of a control's pulse (the SAME attribute ``test_webui_busy_await_change``
#: asserts), read out of ONE rendered control's own form — never from the page at large.
BUSY = 'aria-busy="true"'

STARTUP_PATH = next(a.path for a in actions_page.SERVER_ACTIONS if a.key == "startup")
SHUTDOWN_PATH = next(a.path for a in actions_page.SERVER_ACTIONS if a.key == "shutdown")


class MoveServer:
    """A server double whose ``shutdown``/``startup`` do NOT flip the status — an operation in flight.

    That is the honest shape of the defect: a node ``online`` returns while the DCS servers are still
    booting (and a node ``offline``'s popup chain is still running), so the glyph has not swapped when
    the next render happens. ``calls`` records what the node action reached; the status is moved by
    the TESTS to exercise the settled-state end condition.
    """

    def __init__(self, name, node_name, managed_by=(), *, status=Status.RUNNING, maintenance=False):
        self.name = name
        self.display_name = name
        self.status = status
        self.maintenance = maintenance
        self.node = types.SimpleNamespace(name=node_name)
        self.instance = types.SimpleNamespace(name=f"{node_name}-Instance")
        self.port = 10309
        self.current_mission = None
        self.is_remote = False
        self.locals = {"managed_by": list(managed_by)} if managed_by else {}
        self.players: dict[str, Any] = {}
        self.calls: list[str] = []

    def get_active_players(self):
        return list(self.players.values())

    def is_populated(self):
        return bool(self.players)

    async def sendPopupMessage(self, *args, **kwargs):
        self.calls.append("popup")

    async def shutdown(self):
        self.calls.append("shutdown")

    async def startup(self, *args, **kwargs):
        self.calls.append("startup")


class FakeNode:
    """The ``Node`` double: an object in the caller's view is the heartbeat's "reachable" verdict."""

    def __init__(self, name, *, master=False):
        self.name = name
        self.master = master
        self.instances: dict = {}


@pytest.fixture(autouse=True)
def clean_power_records():
    """The power-off RECORD is MODULE state and the seeding reads it: reset it around every test."""
    reset_power_records()
    try:
        yield
    finally:
        reset_power_records()


@pytest.fixture
def clean_node_registry(clean_registry):
    """``clean_registry`` drops ``plugins.mission.actions``; the node actions live in ``admin``.

    Without dropping ``plugins.admin.actions`` from the module cache the SECOND install in one process
    re-imports a cached module (a no-op), the ``@action`` decorators never run, the registry stays
    EMPTY and the node action is never available — a green-looking POST (303 with a typed refusal)
    that seeds nothing. The node suites use the same fixture for the same reason.
    """
    import sys

    sys.modules.pop("plugins.admin.actions", None)
    try:
        yield
    finally:
        sys.modules.pop("plugins.admin.actions", None)


def fleet(*, status=Status.RUNNING, maintenance=False, down_b=False):
    """Three servers on ``dcs-a-01`` (all Host A) plus one on ``dcs-b-01`` — the unaffected neighbour.

    Every server is managed by ``Host A`` or ``Host B`` so a scoped caller has ONE side of the view,
    and both nodes are heartbeating (there is a node object for each) so the power gate is the node's
    own state and not the heartbeat. ``down_b`` slots the fourth server's status.
    """
    a1 = MoveServer("SRS-1", NODE_A, ["Host A"], status=status, maintenance=maintenance)
    a2 = MoveServer("SRS-2", NODE_A, ["Host A"], status=status, maintenance=maintenance)
    a3 = MoveServer("SRS-3", NODE_A, ["Host A"], status=status, maintenance=maintenance)
    b = MoveServer("SRS-B", NODE_B, ["Host B"], status=status)
    nodes = {NODE_A: FakeNode(NODE_A, master=True), NODE_B: FakeNode(NODE_B)}
    return Cluster([a1, a2, a3, b], nodes=nodes, node=nodes[NODE_A],
                   instances=[instance("Host-A-Instance", a1), instance("Host-B-Instance", b)])


def form_for(html: str, path: str) -> str:
    """The ONE ``<form>`` that posts to *path* — a control, with its button and its own signals."""
    found = re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)
    assert found, f"the page rendered no control posting to {path}"
    return found[0]


def busy_forms(html: str, path: str) -> int:
    """How many controls posting to *path* render ``aria-busy`` — i.e. how many ROWS are pulsing.

    The three servers of one node share ONE control path (``/actions/server/startup``), so counting the
    busy forms is what tells "all three pulse" from "one pulses": each server row is its own ``<form>``.
    """
    forms = re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)
    return sum(1 for form in forms if BUSY in form)


async def post_online(http, node=NODE_A):
    """POST the *online* half: no dialog, no confirm token — the route takes the node + CSRF only."""
    page = await open_page(http, NODES)
    return await http.post(ONLINE, data={"node": node, "_csrf_token": token_of(page)})


async def post_offline(http, node=NODE_A):
    """POST the *offline* half through its dialog (it requires the one-shot confirm token)."""
    page = await open_page(http, NODES)
    dialog = await http.post(OFFLINE + CONFIRM, data={"node": node, "_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.status_code
    confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
    return await http.post(OFFLINE, data={"node": node, "_csrf_token": token_of(page),
                                          "maintenance": ["1"], "_confirm_token": confirm})


def store_keys() -> set[str]:
    return set(actions_page.awaiting_store())


# (a) the rows pulse, the seam is empty

@pytest.mark.asyncio
async def test_an_online_node_action_pulses_the_rows_of_the_servers_it_starts(
        config_dir, clean_node_registry, recording_bot):
    """*Bring servers online* seeds the three servers it will start; their rows pulse."""
    cluster = fleet(status=Status.SHUTDOWN)
    app = build(config_dir, cluster, plugins=("mission", "admin"))

    async with client(app) as http:
        await sign_in(http, "specialk")          # Admin: the only role that may run a node write
        before = await open_page(http, SERVERS)
        assert BUSY not in before, "the pre-flight render must be clean"

        reply = await post_online(http)
        assert reply.status_code == 303, reply.text
        await asyncio.sleep(0.05)

 # the node action RETURNED: the seam's own guard holds nothing, yet the rows must pulse
        assert in_flight_targets() == frozenset(), "the call has returned: nothing is in flight"
        assert cluster.servers[0].status == Status.SHUTDOWN, "the doubles do not flip on startup"

 # the prediction seeded EXACTLY the node's three servers — and NOT the neighbour's
        assert store_keys() == {"server:srs-1", "server:srs-2", "server:srs-3"}, store_keys()
        assert all(entry["action"] == "startup" for entry in actions_page.awaiting_store().values())

        flying = await open_page(http, SERVERS)
        assert busy_forms(flying, STARTUP_PATH + CONFIRM) == 3, \
            "all THREE servers the operation starts must pulse their Startup glyph"
        assert "SRS-B" in flying and "server:srs-b" not in store_keys(), \
            "the unaffected node's server is never seeded"


@pytest.mark.asyncio
async def test_an_offline_node_action_pulses_the_rows_of_the_servers_it_stops(
        config_dir, clean_node_registry, recording_bot):
    """*Take servers offline* seeds the in-service servers it will stop; their rows pulse (Shutdown)."""
    cluster = fleet(status=Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission", "admin"))

    async with client(app) as http:
        await sign_in(http, "specialk")
        assert BUSY not in await open_page(http, SERVERS)

        reply = await post_offline(http)
        assert reply.status_code == 303, reply.text
        await asyncio.sleep(0.05)

        assert in_flight_targets() == frozenset()
        assert store_keys() == {"server:srs-1", "server:srs-2", "server:srs-3"}, store_keys()
        assert all(entry["action"] == "shutdown" for entry in actions_page.awaiting_store().values())

        flying = await open_page(http, SERVERS)
        assert busy_forms(flying, SHUTDOWN_PATH + CONFIRM) == 3, \
            "all THREE RUNNING servers' Shutdown glyphs pulse while the node powers them down"


@pytest.mark.asyncio
async def test_a_dashboard_render_shows_the_same_pulses(config_dir, clean_node_registry, recording_bot):
    """The dashboard renders the SAME strip: one seeding lights BOTH surfaces (servers + dashboard)."""
    cluster = fleet(status=Status.SHUTDOWN)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        assert (await post_online(http)).status_code == 303
        await asyncio.sleep(0.05)
        for page_path in (SERVERS, "/"):
            html = await open_page(http, page_path)
            assert BUSY in form_for(html, STARTUP_PATH + CONFIRM), page_path


# ============================================ (b) the end conditions, per server row

@pytest.mark.asyncio
async def test_a_start_pulse_ends_when_the_row_reaches_a_settled_state(config_dir, clean_node_registry,
                                                                      recording_bot):
    """A START ends at ``RUNNING``/``PAUSED`` — and only for the row that reached it."""
    cluster = fleet(status=Status.SHUTDOWN)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        assert (await post_online(http)).status_code == 303
        await asyncio.sleep(0.05)
        assert store_keys() == {"server:srs-1", "server:srs-2", "server:srs-3"}

        cluster.servers[0].status = Status.RUNNING      # SRS-1 settles
        cluster.servers[1].status = Status.PAUSED       # SRS-2 settles at the other word
        await open_page(http, SERVERS)                  # a render spends the settled entries
        assert store_keys() == {"server:srs-3"}, \
            "RUNNING and PAUSED are both settled for a start; SRS-3 has not booted yet"


@pytest.mark.asyncio
async def test_a_stop_pulse_ends_at_shutdown_or_stopped(config_dir, clean_node_registry, recording_bot):
    """A STOP ends at ``SHUTDOWN``/``STOPPED``, and a server still up keeps pulsing."""
    cluster = fleet(status=Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        assert (await post_offline(http)).status_code == 303
        await asyncio.sleep(0.05)
        assert store_keys() == {"server:srs-1", "server:srs-2", "server:srs-3"}

        cluster.servers[0].status = Status.SHUTDOWN
        cluster.servers[1].status = Status.STOPPED
        await open_page(http, SERVERS)
        assert store_keys() == {"server:srs-3"}, \
            "SHUTDOWN and STOPPED are both settled for a stop; SRS-3 is still up"


# ============================================ (c) the negatives

@pytest.mark.asyncio
async def test_a_server_already_in_the_settled_state_is_not_seeded(config_dir, clean_node_registry,
                                                                  recording_bot):
    """*Bring online* does not seed a server already up (skipped) nor one still flagged (not started)."""
    cluster = fleet(status=Status.SHUTDOWN)
    cluster.servers[0].status = Status.RUNNING       # already up: nothing to start
    cluster.servers[1].maintenance = True            # still flagged: the operation will not start it
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        assert (await post_online(http)).status_code == 303
        await asyncio.sleep(0.05)
    assert store_keys() == {"server:srs-3"}, \
        "only the down, unflagged server is pending — the up one and the flagged one are not"


@pytest.mark.asyncio
async def test_online_with_a_record_seeds_only_the_servers_it_stopped(config_dir, clean_node_registry,
                                                                     recording_bot):
    """The power-off RECORD decides which servers *online* starts — and therefore which rows pulse."""
    record_power_off(NODE_A, when=__import__("datetime").datetime.now(), actor="probe",
                     stopped=["SRS-1", "SRS-3"], flagged=["SRS-1", "SRS-3"])
    cluster = fleet(status=Status.SHUTDOWN)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        assert (await post_online(http)).status_code == 303
        await asyncio.sleep(0.05)
    assert store_keys() == {"server:srs-1", "server:srs-3"}, \
        "SRS-2 was not stopped by the recorded power-off, so the operation does not start it"


# ============================================ (d) refusals, replacement, and the scope

@pytest.mark.asyncio
async def test_a_refused_write_seeds_nothing(config_dir, clean_node_registry, recording_bot):
    """A node the action cannot reach is a typed refusal: no server row may pulse for it."""
    cluster = fleet(status=Status.SHUTDOWN)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_online(http, node="NOPE-9")
        assert reply.status_code == 303, reply.text
        after = await open_page(http, NODES)
        assert "offline or unknown" in after, "the typed refusal is rendered inline"
    assert store_keys() == set(), "a refused write must not seed anything"


@pytest.mark.asyncio
async def test_a_second_node_action_replaces_and_never_stacks(config_dir, clean_node_registry,
                                                             recording_bot):
    """Two node actions on the SAME node leave one entry per server (the newest press owns it)."""
    cluster = fleet(status=Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        assert (await post_offline(http)).status_code == 303
        await asyncio.sleep(0.05)
        assert (await post_offline(http)).status_code == 303
        await asyncio.sleep(0.05)
    assert store_keys() == {"server:srs-1", "server:srs-2", "server:srs-3"}, \
        "a repeat action replaces its entries, never stacks them"
    assert actions_page.awaiting_store()["server:srs-1"]["action"] == "shutdown"


@pytest.mark.asyncio
async def test_the_lifecycle_trio_seeds_nothing(config_dir, clean_node_registry, recording_bot):
    """Only the POWER pair moves servers as its operation; restart/shutdown/upgrade seed no server."""
    cluster = fleet(status=Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, NODES)
        dialog = await http.post(RESTART + CONFIRM, data={"node": NODE_A, "_csrf_token": token_of(page)})
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
        reply = await http.post(RESTART, data={"node": NODE_A, "_csrf_token": token_of(page),
                                               "_confirm_token": confirm})
        assert reply.status_code == 303, reply.text
        await asyncio.sleep(0.05)
    assert store_keys() == set(), "a node lifecycle write moves no per-server expectation"


@pytest.mark.asyncio
async def test_a_caller_who_cannot_reach_the_node_seeds_nothing(config_dir, clean_node_registry,
                                                               recording_bot):
    """Node writes are Admin-only: a manager's crafted POST is refused and seeds no server row."""
    cluster = fleet(status=Status.SHUTDOWN)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "hoster-b")              # manages Host B only — not a node operator
        nodes_page_html = await http.get(NODES)
        token = token_of(nodes_page_html.text) if nodes_page_html.status_code == 200 else "t" * 43
        reply = await http.post(ONLINE, data={"node": NODE_A, "_csrf_token": token})
        assert reply.status_code == 403, reply.status_code
    assert cluster.servers[0].calls == [], "nothing may run for a refused caller"
    assert store_keys() == set(), "and nothing may be seeded for a view that does not carry the node"


# ============================================ (e) the ceiling still bounds a no-op

@pytest.mark.asyncio
async def test_the_ceiling_bounds_a_pulse_the_operation_never_finishes(config_dir, clean_node_registry,
                                                                     recording_bot, monkeypatch):
    """A boot that never comes up must not pulse forever: at the cap the seeded expectation stops."""
    cluster = fleet(status=Status.SHUTDOWN)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        assert (await post_online(http)).status_code == 303
        await asyncio.sleep(0.05)
        assert BUSY in form_for(await open_page(http, SERVERS), STARTUP_PATH + CONFIRM), \
            "inside the window it pulses"

        monkeypatch.setattr(actions_page.time, "monotonic", lambda: 1_000_000_000.0)
        capped = await open_page(http, SERVERS)
        assert BUSY not in capped, "at the cap the pulse stops — the seeding does not run forever"

    assert reset_in_flight() is None
