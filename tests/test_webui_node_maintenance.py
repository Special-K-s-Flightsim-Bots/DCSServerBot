"""The node row's power pair: *Take servers offline* / *Bring servers online* (Admin-only).

The pair used to be a FLAG control wearing power words -- ``online`` cleared every maintenance flag on
the node (including a hand-set one) and started every server. It is now a POWER pair, and the flag's
own controls live on the SERVER row (``test_webui_server_maintenance.py``); the ACTION-level matrix is
``test_admin_node_power.py``. THIS file pins what the console shows: the discovery wiring; the role
matrix (Admin alone); the power state matrix (a node's own power-off RECORD decides which half is
offered, the servers' statuses are irrelevant, and the flag is not a term of the gate); the option
(``offline``'s ``maintenance`` parameter, default ON, off-companion first; ``online`` carries none);
the copy; the one-shot confirm for ``offline`` and the tokenless ``online``; and the audit. Driven
through the real app and a stub cluster."""
from __future__ import annotations

import asyncio
import re
import types
from typing import Any

import pytest

from conftest import permissions
from core import Status
from core.actions import ActionContext, action_available, call_action, discover_actions
from core.data.maintenance import record_power_off, reset_power_records
from services.webservice.pages import actions as actions_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.scope import Scope
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    BOT_YAML, Cluster, build, clean_registry, client, config_dir, instance, open_page,
    recording_bot, sign_in, token_of,
)

NODES = nodes_page.NODES_PATH
DASHBOARD = "/"
OFFLINE = "/actions/node/offline"
ONLINE = "/actions/node/online"
CONFIRM = actions_page.CONFIRM_SUFFIX
OFFLINE_DIALOG = OFFLINE + CONFIRM
ONLINE_DIALOG = ONLINE + CONFIRM
PAIR = (OFFLINE, ONLINE)
NODE_NAME = "dcs-a-01"
AGENT = "dcs-b-01"
EMPTY_NODE = "dcs-c-01"

#: the SAME rule the engine applies, written here so the fleet builder can produce both
#: sides of the power gate without importing the engine.
IN_SERVICE = (Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.LOADING, Status.SHUTTING_DOWN)
DOWN = (Status.SHUTDOWN, Status.UNREGISTERED)


# ------------------------------------------------------------------------------- test doubles

class FakeNode:
    """The ``Node`` double: the lifecycle calls the row also offers, counted (unused by the pair)."""

    def __init__(self, name, *, master=False):
        self.name = name
        self.master = master
        self.instances = {}
        self.calls: list[str] = []

    async def restart(self):
        self.calls.append("restart")

    async def shutdown(self, rc=-2):
        self.calls.append("shutdown")

    async def upgrade(self):
        self.calls.append("upgrade")


class MaintenanceServer:
    """A server double shaped where the scope, the read models and the pair's own reads look.

    ``status`` MOVES when ``shutdown``/``startup`` is called, exactly as the real objects' do — the
    power gate and the online half's "is it still down?" test both depend on it. ``calls`` records the
    two process operations; ``fails`` makes ``shutdown`` raise (the engine's per-server failure path).
    """

    def __init__(self, name, node_name, managed_by=(), *, status=Status.RUNNING, maintenance=False,
                 players=0):
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
        self.players = {f"ucid{i}": types.SimpleNamespace(ucid=f"ucid{i}", active=True)
                        for i in range(players)}
        self.calls: list[str] = []

    def get_active_players(self):
        return list(self.players.values())

    def is_populated(self):
        return bool(self.players)

    async def sendPopupMessage(self, *args, **kwargs):
        self.calls.append("popup")

    async def shutdown(self):
        self.calls.append("shutdown")
        self.status = Status.SHUTDOWN

    async def startup(self):
        self.calls.append("startup")
        self.status = Status.RUNNING


class StubbornServer(MaintenanceServer):
    """A server whose maintenance flag cannot be SET — the engine's per-server failure path."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._maintenance = self.maintenance

    @property
    def maintenance(self):
        return self._maintenance

    @maintenance.setter
    def maintenance(self, value):
        if value:
            raise RuntimeError("this flag refuses to be written")
        self._maintenance = value


@pytest.fixture(autouse=True)
def clean_power_records():
    """The record registry is MODULE state, and the power gate reads it: reset it around every test."""
    reset_power_records()
    try:
        yield
    finally:
        reset_power_records()


def fleet(*, status=Status.RUNNING, maintenance=False, players=0, empty_node=False):
    """Two nodes carrying one server each (one per hoster), plus an empty node on request.

    Both servers live on nodes that ARE heartbeating (there is a node object for each), so the power
    gate — and not the heartbeat — decides which control is offered.
    """
    a = MaintenanceServer("SRS-1", NODE_NAME, ["Host A"], status=status, maintenance=maintenance,
                          players=players)
    b = MaintenanceServer("SRS-B", AGENT, ["Host B"])
    nodes = {NODE_NAME: FakeNode(NODE_NAME, master=True), AGENT: FakeNode(AGENT)}
    if empty_node:
        nodes[EMPTY_NODE] = FakeNode(EMPTY_NODE)
    return Cluster([a, b], nodes=nodes, node=nodes[NODE_NAME],
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


@pytest.fixture
def clean_node_registry(clean_registry):
    """``clean_registry`` drops ``plugins.mission.actions``; the node actions live in ``admin``."""
    import sys

    sys.modules.pop("plugins.admin.actions", None)
    try:
        yield
    finally:
        sys.modules.pop("plugins.admin.actions", None)


def row_for(html: str, node: str) -> str:
    """The ``<tr>`` that belongs to *node* — ONE row, never a span from the header row down."""
    for row in re.findall(r"<tr>.*?</tr>", html, re.S):
        if node in row:
            return row
    raise AssertionError(f"no row for {node}")


def dialog_token(html: str) -> str:
    match = re.search(r'name="_confirm_token" value="([^"]+)"', html)
    assert match, "the dialog carries no confirm token"
    return match.group(1)


async def both_screens(http) -> dict[str, str]:
    """The standalone /nodes page and the dashboard's Nodes tab — two screens of one component."""
    return {"nodes": await open_page(http, NODES),
            "dashboard": await open_page(http, DASHBOARD + "?tab=nodes")}


async def open_dialog(http, path: str, node: str, token: str, *, expect_token: bool = True):
    """Open one node control's dialog and return (response, its one-shot token)."""
    reply = await http.post(path + CONFIRM, data={"node": node, "_csrf_token": token})
    assert reply.status_code == 200, f"{path}: {reply.status_code}"
    if not expect_token:
        return reply, ""
    return reply, dialog_token(reply.text)


def posted(*pairs: tuple[str, str]) -> dict[str, Any]:
    """A form body that can carry REPEATED fields, in order (the checkbox and its companion)."""
    body: dict[str, Any] = {}
    for name, value in pairs:
        if name not in body:
            body[name] = value
        elif isinstance(body[name], list):
            body[name].append(value)
        else:
            body[name] = [body[name], value]
    return body


def browser_form(node: str, token: str, confirm: str | None, option: str | None,
                 checked: bool = False) -> dict[str, Any]:
    """What a BROWSER sends for one node dialog's form — in the order the markup renders it."""
    pairs: list[tuple[str, str]] = [("node", node), ("_csrf_token", token)]
    if option is not None:
        pairs.append((option, "0"))
        if checked:
            pairs.append((option, "1"))
    if confirm is not None:
        pairs.append(("_confirm_token", confirm))
    return posted(*pairs)


# ------------------------------------------------------------------ 1. the discovery wiring

@pytest.mark.asyncio
async def test_the_web_shell_discovers_the_power_actions_in_its_own_process(config_dir,
                                                                           clean_node_registry):
    """``action_available`` for both halves, through the shell's own install."""
    for qualname in ("take_node_offline", "bring_node_online"):
        assert action_available(qualname) is False, "the registry must start EMPTY for this test"
    build(config_dir, fleet(), plugins=("mission", "admin"))
    for qualname in ("take_node_offline", "bring_node_online"):
        assert action_available(qualname) is True, qualname


@pytest.mark.asyncio
async def test_an_install_without_the_admin_plugin_offers_no_power_control(config_dir,
                                                                          clean_node_registry):
    """The control for the discovery pin above: with no ``admin`` plugin there is nothing to find."""
    build(config_dir, fleet(), plugins=("mission",))
    assert action_available("take_node_offline") is False
    assert action_available("bring_node_online") is False


# ---------------------------------------------------------------------- 2. the role matrix

@pytest.mark.asyncio
@pytest.mark.parametrize("who, sees_pair", [
    ("specialk", True),      # Admin — the only role that may take a node's servers out of service
    ("mod1", False),         # DCS Admin
    ("hoster", False),       # a MANAGER (scope Host A) — node writes are NOT scope-granted
])
async def test_the_role_matrix_on_both_screens(config_dir, clean_node_registry, who, sees_pair):
    """The pair, in the rendered markup — ABSENT for a role that may not use it."""
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, who)
        pages = await both_screens(http)
    for where, html in pages.items():
        assert (OFFLINE_DIALOG in html) is sees_pair, f"{who} on {where}: Take servers offline"
        assert ONLINE_DIALOG not in html, f"{who} on {where}: nothing is down on that node"
        if not sees_pair:
            assert "act-strip" not in html, f"{who} on {where}: an icon strip is rendered"


@pytest.mark.asyncio
async def test_a_crafted_post_for_the_pair_is_refused_by_the_gate(config_dir, clean_node_registry,
                                                                 recording_bot):
    """A role without the capability cannot reach either half, dialog or action."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "mod1")                     # DCS Admin: not a node operator
        page = await open_page(http, NODES)
        token = token_of(page)
        for path in (OFFLINE, ONLINE, OFFLINE_DIALOG, ONLINE_DIALOG):
            reply = await http.post(path, data={"node": NODE_NAME, "_csrf_token": token})
            assert reply.status_code == 403, path
            assert "Not authorized" in reply.json()["detail"], path
    assert cluster.servers[0].calls == []
    assert cluster.servers[0].maintenance is False
    assert recording_bot.calls == []


# ------------------------------------------------------------------ 3. the power state matrix

@pytest.mark.asyncio
async def test_servers_up_offer_take_offline_and_never_bring_online(config_dir, clean_node_registry):
    """Something to stop: the ordinary case, and only one half of the pair."""
    app = build(config_dir, fleet(status=Status.RUNNING), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = await both_screens(http)
    for where, html in pages.items():
        assert OFFLINE_DIALOG in row_for(html, NODE_NAME), where
        assert ONLINE_DIALOG not in row_for(html, NODE_NAME), where


@pytest.mark.asyncio
async def test_an_online_node_with_every_server_stopped_offers_take_offline_only(
        config_dir, clean_node_registry):
    """An ONLINE node with NOTHING up still offers *Take servers offline* and never *Bring servers online*.

    The node is heartbeating and no power-off is recorded, so it is IN SERVICE: the operation is
    available whether or not anything runs. The SERVERS' statuses must not decide.
    """
    app = build(config_dir, fleet(status=Status.SHUTDOWN), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = await both_screens(http)
    for where, html in pages.items():
        row = row_for(html, NODE_NAME)
        assert OFFLINE_DIALOG in row, where
        assert ONLINE_DIALOG not in row, where


@pytest.mark.asyncio
async def test_an_online_node_with_servers_down_and_flagged_still_offers_take_offline(
        config_dir, clean_node_registry):
    """All stopped and all flagged, no record: the NODE's state (online, in service) is the gate, so *Take
    servers offline* is offered even though nothing is up.

    The maintenance flag belongs to the SERVER row and no longer reaches this one.
    """
    app = build(config_dir, fleet(status=Status.SHUTDOWN, maintenance=True),
                plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    row = row_for(html, NODE_NAME)
    assert OFFLINE_DIALOG in row
    assert ONLINE_DIALOG not in row
    assert 'value="dcs-a-01"' in row, \
        "the trio is still offered — the row is not empty, the pair is simply gated on the node"


@pytest.mark.asyncio
async def test_a_power_off_record_offers_bring_online_only(config_dir, clean_node_registry):
    """The RECORD is the one gate of the online half, read from the engine's registry.

    A recorded power-off whose servers all came up again shows *Bring servers online* and NOT *Take
    servers offline*: a node with an unreverted power-off is not in service. The record is per node.
    """
    record_power_off(NODE_NAME, when=__import__("datetime").datetime.now(), actor="probe",
                     stopped=["SRS-1"], flagged=["SRS-1"])
    app = build(config_dir, fleet(status=Status.RUNNING), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    row = row_for(html, NODE_NAME)
    assert ONLINE_DIALOG in row
    assert OFFLINE_DIALOG not in row
    assert OFFLINE_DIALOG in row_for(html, AGENT), "the record is per node, not per install"


@pytest.mark.asyncio
async def test_the_flag_state_does_not_gate_the_pair_any_more(config_dir, clean_node_registry):
    """A node whose RUNNING servers are all flagged still offers OFFLINE.

    The gate is the PROCESSES, not the flag state, so a flagged-but-running node offers the power-off;
    the operation records those servers as ``already_flagged`` and leaves their flags alone.
    """
    app = build(config_dir, fleet(status=Status.RUNNING, maintenance=True),
                plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = await both_screens(http)
    for where, html in pages.items():
        row = row_for(html, NODE_NAME)
        assert OFFLINE_DIALOG in row, where
        assert ONLINE_DIALOG not in row, where


@pytest.mark.asyncio
async def test_an_online_tag_is_never_offered_the_online_half_without_a_record(
        config_dir, clean_node_registry):
    """The tag and the control never contradict each other.

    An ONLINE tag means the node is up, and the ONLY way it may carry *Bring servers online* is a
    power-off on record -- never "some server is down".
    """
    app = build(config_dir, fleet(status=Status.SHUTDOWN), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    row = row_for(html, NODE_NAME)
    assert '<span class="tag">online</span>' in row, "the heartbeat tag is the node's"
    assert ONLINE_DIALOG not in row, "an ONLINE row must not offer to bring the node online"
    assert OFFLINE_DIALOG in row, "...it offers the operation its own state names"


@pytest.mark.asyncio
async def test_the_online_half_is_offered_iff_a_record_exists(config_dir, clean_node_registry):
    """The record is the ONE thing that puts *Bring servers online* on a row.

    Driven twice over the same fleet -- every server down and unflagged: with no record the offline half
    only (keeping the record in memory means a bot restart offers no way back), with one, the online
    half only.
    """
    app = build(config_dir, fleet(status=Status.SHUTDOWN), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        before = await open_page(http, NODES)
        record_power_off(NODE_NAME, when=__import__("datetime").datetime.now(), actor="probe",
                         stopped=["SRS-1"], flagged=["SRS-1"])
        after = await open_page(http, NODES)
    assert ONLINE_DIALOG not in row_for(before, NODE_NAME), "no record, no way back to offer"
    assert OFFLINE_DIALOG in row_for(before, NODE_NAME)
    assert ONLINE_DIALOG in row_for(after, NODE_NAME)
    assert OFFLINE_DIALOG not in row_for(after, NODE_NAME)
 #... and the reason is written down, not implied by the missing arm
    doc = " ".join((actions_page.node_power_states.__doc__ or "").split())
    assert "After a bot restart" in doc
    assert "deliberate" in doc.lower()


@pytest.mark.asyncio
async def test_a_node_with_no_server_is_offered_neither_half(config_dir, clean_node_registry):
    """Nothing to take out of service and nothing to bring back: no control (never a no-op button)."""
    app = build(config_dir, fleet(empty_node=True), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    row = row_for(html, EMPTY_NODE)
    assert 'value="dcs-c-01"' in row, "the trio is still offered on that node"
    assert OFFLINE_DIALOG not in row and ONLINE_DIALOG not in row
 #... and the neighbouring node, which carries a RUNNING server, keeps its half (not vacuous)
    assert OFFLINE_DIALOG in row_for(html, NODE_NAME)


def test_node_power_states_follows_the_nodes_own_state_not_the_servers():
    """``node_power_states`` against its four rules, one clause at a time.

    (1) reachable + servers in view + no record -> the power-off half; (2) a record -> the power-on
    half; (3) no server in the caller's view -> neither; (4) an unreachable node -> neither. The
    maintenance FLAG is not a term of this function -- it adds its own state through ``node_states``.
    """
    def source(servers, node=object()):
        return types.SimpleNamespace(servers=list(servers),
                                     nodes={NODE_NAME: FakeNode(NODE_NAME) if node else None})

 # (1) every status the engine calls in service, and every one it does not: the SAME answer
    for status in IN_SERVICE + DOWN:
        for maintenance in (False, True):
            srv = MaintenanceServer("SRS-1", NODE_NAME, status=status, maintenance=maintenance)
            assert actions_page.node_power_states(source([srv]), NODE_NAME) == \
                {actions_page.NODE_POWER_OFF}, f"{status.value}/flag={maintenance}"
 #... and a node with NO server in the view is in neither state
    assert actions_page.node_power_states(source([]), NODE_NAME) == frozenset(), "no server, no state"
 # (2) the record is the ONE thing that flips the half
    record_power_off(NODE_NAME, when=__import__("datetime").datetime.now(), actor="probe",
                     stopped=["SRS-1"], flagged=["SRS-1"])
    for status in IN_SERVICE + DOWN:
        srv = MaintenanceServer("SRS-1", NODE_NAME, status=status)
        assert actions_page.node_power_states(source([srv]), NODE_NAME) == \
            {actions_page.NODE_POWER_ON}, f"{status.value} with a record"
 # (3) the heartbeat is the node's own state: an unreachable node has no power state at all
    assert actions_page.node_power_states(source([MaintenanceServer("SRS-1", NODE_NAME)],
                                                node=False), NODE_NAME) == frozenset()
 # the flag state is still in the vocabulary, and it is a DIFFERENT word
    RUN = [MaintenanceServer("SRS-1", NODE_NAME, status=Status.RUNNING, maintenance=True)]
    assert actions_page.node_states(source(RUN), NODE_NAME) == {actions_page.NODE_POWER_ON,
                                                                actions_page.NODE_MAINTENANCE}


# ---------------------------------------------------------------------- 4. the option flags

@pytest.mark.asyncio
async def test_offline_offers_the_maintenance_option_defaulted_on(config_dir, clean_node_registry,
                                                                 recording_bot):
    """The option is the ACTION's ``maintenance`` parameter, ON by default, and it says why."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        dialog, confirm = await open_dialog(http, OFFLINE, NODE_NAME, token)
        reply = await http.post(OFFLINE, data=browser_form(NODE_NAME, token, confirm, "maintenance",
                                                           checked=True))
        await asyncio.sleep(0.05)
    assert dialog.status_code == 200
    assert 'name="maintenance" value="0"' in dialog.text
    assert 'name="maintenance" value="1"' in dialog.text
    assert re.search(r'name="maintenance" value="1"[^>]*\schecked', dialog.text), \
        "the default is ON — without it a scheduled start brings the servers back"
    assert dialog.text.index('name="maintenance" value="0"') < \
           dialog.text.index('name="maintenance" value="1"'), \
        "the OFF companion must come FIRST (the last value wins)"
    assert "Also mark them as maintenance" in dialog.text
    assert "a scheduled start can bring the servers back" in dialog.text
    assert 'name="shutdown"' not in dialog.text, "the old option name is gone"
    assert reply.status_code == 303
    server = cluster.servers[0]
    assert server.maintenance is True
    assert server.calls == ["shutdown"]
    assert [call[0] for call in recording_bot.calls] == [
        "Node 'dcs-a-01': 1 server(s) marked as maintenance and stopped.",
    ]


@pytest.mark.asyncio
async def test_offline_with_the_box_cleared_stops_without_writing_a_flag(config_dir,
                                                                        clean_node_registry,
                                                                        recording_bot):
    """The box cleared: the servers stop, NO flag is written, and the message says so.

    This is the case the old flag-state gate could never recover from — hence the RECORD arm of the
    online gate, asserted in the round trip below.
    """
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        _, confirm = await open_dialog(http, OFFLINE, NODE_NAME, token)
        reply = await http.post(OFFLINE, data=browser_form(NODE_NAME, token, confirm, "maintenance",
                                                          checked=False))
        await asyncio.sleep(0.05)
    assert reply.status_code == 303
    server = cluster.servers[0]
    assert server.maintenance is False, "a cleared box must not write a flag"
    assert server.calls == ["shutdown"]
    assert [call[0] for call in recording_bot.calls] == [
        "Node 'dcs-a-01': 1 server(s) stopped, no maintenance flag was set.",
    ]


@pytest.mark.asyncio
async def test_online_offers_no_option_at_all(config_dir, clean_node_registry, recording_bot):
    """Bringing the servers back IS the operation -- there is no box and no token to skip."""
    cluster = fleet(status=Status.SHUTDOWN)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        dialog, _ = await open_dialog(http, ONLINE, NODE_NAME, token, expect_token=False)
        reply = await http.post(ONLINE, data=browser_form(NODE_NAME, token, None, None))
        await asyncio.sleep(0.05)
    assert dialog.status_code == 200
    assert 'name="startup"' not in dialog.text, "the startup option is deleted"
    assert 'name="maintenance"' not in dialog.text, "the flag option belongs to the OTHER half"
    assert 'type="checkbox"' not in dialog.text, "no option at all"
    assert 'name="_confirm_token"' not in dialog.text, \
        "a token nothing spends must not be minted for this half"
    assert reply.status_code == 303
    assert cluster.servers[0].calls == [], "the action clears flags only; the scheduler starts"
    assert [call[0] for call in recording_bot.calls] == [
        "Node 'dcs-a-01': no power-off is recorded for this node since the bot "
        f"started at {_started_at()}; 0 maintenance flag(s) cleared; 1 server(s) that were down and "
        "not in maintenance are left for the scheduler to start.",
    ]


def _started_at() -> str:
    from core.data.maintenance import process_started_at
    return process_started_at().strftime("%H:%M")


@pytest.mark.asyncio
async def test_the_off_option_is_never_read_as_on(config_dir, clean_node_registry, recording_bot):
    """``bool('0')`` is True: the companion alone must mean OFF (the parse, not a truthiness test)."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        _, confirm = await open_dialog(http, OFFLINE, NODE_NAME, token)
        reply = await http.post(OFFLINE, data=posted(("node", NODE_NAME), ("_csrf_token", token),
                                                     ("maintenance", "1"), ("maintenance", "0"),
                                                     ("_confirm_token", confirm)))
        await asyncio.sleep(0.05)
    assert reply.status_code == 303
    assert cluster.servers[0].calls == ["shutdown"]
    assert cluster.servers[0].maintenance is False, "the LAST value wins, and it is the companion's 0"


# ------------------------------------------------------------------------- 5. the round trip

@pytest.mark.asyncio
async def test_the_round_trip_offline_then_online_reverts_exactly_the_operation(
        config_dir, clean_node_registry, recording_bot):
    """The pair's whole point, through the browser: the flag flips, the row flips.

    Nothing about the node object is touched, which is what makes the operation callable from the
    browser; after the power-off the row offers the way back because a RECORD exists.
    """
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, NODES)
        assert OFFLINE_DIALOG in page and ONLINE_DIALOG not in page

        token = token_of(page)
        _, confirm = await open_dialog(http, OFFLINE, NODE_NAME, token)
        offline = await http.post(OFFLINE, data=browser_form(NODE_NAME, token, confirm, "maintenance",
                                                             checked=True))
        assert offline.status_code == 303
        await asyncio.sleep(0.05)

        after = await open_page(http, NODES)
        assert ONLINE_DIALOG in row_for(after, NODE_NAME), \
            "the row now offers the way back — which the copy's own claim states"
        assert OFFLINE_DIALOG not in row_for(after, NODE_NAME), \
            "nothing is up, so there is nothing to take offline"
        assert "maintenance and stopped" in after, "the notice reports the outcome"

        token = token_of(after)
        _, _unused = await open_dialog(http, ONLINE, NODE_NAME, token, expect_token=False)
        online = await http.post(ONLINE, data=browser_form(NODE_NAME, token, None, None))
        assert online.status_code == 303
        await asyncio.sleep(0.05)

        final = await open_page(http, NODES)
        assert OFFLINE_DIALOG in row_for(final, NODE_NAME)
        assert ONLINE_DIALOG not in row_for(final, NODE_NAME)

    server = cluster.servers[0]
    assert server.maintenance is False
    assert server.calls == ["shutdown"], "the flag flips here; the SCHEDULER does the starting"
    assert cluster.nodes[NODE_NAME].calls == [], "the node's own process is never touched"
    assert [call[0] for call in recording_bot.calls] == [
        "Node 'dcs-a-01': 1 server(s) marked as maintenance and stopped.",
        "Node 'dcs-a-01': 1 maintenance flag(s) cleared, "
        "1 server(s) left for the scheduler to start.",
    ]


# --------------------------------------------------------------------------- 6. the copy

@pytest.mark.asyncio
async def test_the_offline_dialog_names_the_consequence_and_the_flag_it_sets(config_dir,
                                                                            clean_node_registry):
    """The numbers, the disconnect, and the sentence about the flag it is about to set."""
    app = build(config_dir, fleet(players=3), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        dialog, _ = await open_dialog(http, OFFLINE, NODE_NAME, token)
    assert "<b>1 server(s) on dcs-a-01 go down and 3 player(s) are disconnected.</b>" in dialog.text
    assert "leave it ticked unless you mean to let a scheduled start bring them back" in dialog.text
    assert "Take the servers on dcs-a-01 offline?" in dialog.text
    assert ">Take servers offline<" in dialog.text
    assert "are you sure" not in dialog.text.lower()
    assert "Only the SERVERS on the node change." in dialog.text
    assert "including this console — stay up" in dialog.text
    assert "clear the box and no flag is written at all" in dialog.text


@pytest.mark.asyncio
async def test_the_online_page_is_a_form_and_its_button_is_not_end_maintenance(config_dir,
                                                                              clean_node_registry):
    """The button is a POWER control now, and its copy says what it will and will not do."""
    app = build(config_dir, fleet(status=Status.SHUTDOWN), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        dialog, _ = await open_dialog(http, ONLINE, NODE_NAME, token, expect_token=False)
    assert ">Bring servers online<" in dialog.text
    assert ">End maintenance<" not in dialog.text, "the button stops reading as a flag toggle"
    assert "Bring the servers on dcs-a-01 back online?" in dialog.text
    assert "clears only the maintenance flags that operation set" in dialog.text
    assert "A flag somebody set by hand is never cleared" in dialog.text
    assert "the record is gone" in dialog.text, "the no-record path is stated, not hidden"
    assert 'class="dlg-warn"' not in dialog.text, "no red warning block on a power-on form"
    assert "cannot be undone" not in dialog.text
    assert 'class="btn danger"' not in dialog.text, "the button is not the destructive one"
    assert '<span class="tag">options</span>' in dialog.text


def test_the_words_name_the_servers_never_the_node_process():
    """The labels, tips and aria of both halves, asserted on the DECLARATION."""
    by_key = {action.key: action for action in actions_page.NODE_ACTIONS}
    offline, online = by_key["offline"], by_key["online"]
    assert offline.label == "Take servers offline"
    assert online.label == "Bring servers online"
 # the {node} placeholder is the DIALOG's (formatted from the resolved node); the GO button's
 # title carries it too, and the button's own word never names the node's process
    assert "{node}" in offline.go_title and "{node}" in online.go_title
    assert "node" not in offline.go.lower() and "node" not in online.go.lower(), \
        "the button's word is the operation on the SERVERS, not on the node"
    for action in (offline, online):
        assert "servers" in (action.tip + action.aria).lower(), action.key
 # the trio keeps the node's own words — "Shut down" is the node's process, and it stays the ONLY
 # control that says so
    assert by_key["shutdown"].label == "Shut down"
    assert "This console runs inside that node" in by_key["shutdown"].master_note


@pytest.mark.asyncio
async def test_the_nodes_page_lead_is_shortened_to_the_one_consequence(config_dir, clean_node_registry):
    """Card F1: the page lead is the short pair of sentences, not the old wall of text.

    The old lead enumerated every action and every gate (the power pair, the offline note, the
    heartbeat note). The card drops that enumeration — each dialog carries its own action's copy —
    and keeps only what the page is and the one consequence: acting on a node takes its servers
    with it.
    """
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    assert "optionally starts them again" not in html, "the flag-toggle reading is gone"
    for fragment in ("Every node in the cluster, with the instances and servers it carries.",
                     "Acting on a node takes every server on it down with it"):
        assert fragment in nodes_page.NODES_LEAD, fragment
        assert fragment in html, f"the lead is not rendered: {fragment}"
 # the old wall's per-action enumeration is GONE from the lead (card F1)
    for gone in ("Take servers offline stops the servers that are up",
                 "Bring servers online reverts exactly that",
                 "An OFFLINE node offers no control",
                 "starting it is a job for the machine itself, never for this browser"):
        assert gone not in nodes_page.NODES_LEAD, gone


# ------------------------------------------------------------------------ 7. the declarations

def test_the_pair_declares_the_actions_parameters_and_the_power_gates():
    """The action declarations: the one place the gates and the option live."""
    by_key = {action.key: action for action in actions_page.NODE_ACTIONS}
    assert by_key["offline"].option is not None
    assert by_key["offline"].option.field == "maintenance"
    assert by_key["offline"].option.default is True
    assert by_key["offline"].confirm is True and by_key["offline"].danger is True
    assert by_key["offline"].states == (actions_page.NODE_POWER_OFF,)
    assert by_key["online"].option is None, "bringing them back IS the operation"
    assert by_key["online"].confirm is False and by_key["online"].danger is False
    assert by_key["online"].states == (actions_page.NODE_POWER_ON,)
 # the trio is gated on nothing: a node's own process is restartable whatever its servers do
    for key in ("restart", "shutdown", "upgrade"):
        assert by_key[key].states == ()


def test_the_option_field_is_the_actions_own_parameter_name(clean_node_registry):
    """The form field and the ACTION's parameter are ONE string, so a rename cannot drift.

    The route hands the parsed option to the action under ``action.option.field``; pinned against the
    REAL signatures, so a renamed parameter fails here rather than 500ing on a live node.
    """
    import inspect

    import plugins.admin.actions as admin_actions

    offline = inspect.signature(admin_actions.take_node_offline).parameters
    online = inspect.signature(admin_actions.bring_node_online).parameters
    by_key = {action.key: action for action in actions_page.NODE_ACTIONS}
    option = by_key["offline"].option
    assert option is not None
    assert option.field in offline, \
        "the offline dialog's field must be a parameter take_node_offline accepts"
    assert "shutdown" not in offline, "the old parameter is gone"
 # ``online`` gained ONE option of its own — the forced clear — while the dialog still renders no
 # field for it. The route therefore sends nothing, so the parameter must be OPTIONAL: a console
 # call has to keep the recorded revert rather than failing with a TypeError.
    assert "clear_all" in online, "the forced clear is a parameter of the action"
    assert online["clear_all"].default is False, \
        "and it defaults to off — the dialog's Power On sends no field for it"


def test_the_power_capabilities_are_admin_only_and_unscoped():
    """The security boundary: the pair stays Admin-only and a manager's scope must never widen."""
    from services.webservice import permissions as perms

    actions_page.declare()
    for capability in (actions_page.NODE_OFFLINE_CAPABILITY, actions_page.NODE_ONLINE_CAPABILITY):
        assert capability in perms.CAPABILITIES, capability
        assert perms.CAPABILITIES[capability] == ("Admin",), capability
        assert perms.SCOPE_GRANTS[capability] is False, \
            "a manager's scope is a set of SERVERS: it must not widen into a node operation"
 # the scope is not a second way in, for a manager or for anybody
        assert perms.allows(capability, (), manager=True) is False, capability
        assert perms.allows(capability, ("DCS Admin",), manager=True) is False, capability
        assert perms.allows(capability, ("Admin",)) is True, capability
 #... and the pair is reachable by an Admin whose scope would restrict a lesser caller
    node = FakeNode(NODE_NAME)
    admin = _ctx([], {NODE_NAME: node}, scope=Scope.everything())
    assert admin.resolve_node(NODE_NAME) is node


# ------------------------------------------------------------------------ 8. the confirm step

@pytest.mark.asyncio
async def test_a_post_that_skipped_the_offline_dialog_is_refused(config_dir, clean_node_registry,
                                                                recording_bot):
    """A confirmation ``curl`` can skip is decoration: the token is checked SERVER-side."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        direct = await http.post(OFFLINE, data=posted(("node", NODE_NAME), ("_csrf_token", token),
                                                      ("maintenance", "0"), ("maintenance", "1")))
        assert direct.status_code == 403
        assert "confirmed through its dialog" in direct.json()["detail"]
 #... and the dialog's token is ONE-SHOT: the same form a second time is refused
        _, confirm = await open_dialog(http, OFFLINE, NODE_NAME, token)
        form = browser_form(NODE_NAME, token, confirm, "maintenance", checked=True)
        first = await http.post(OFFLINE, data=form)
        replay = await http.post(OFFLINE, data=form)
        await asyncio.sleep(0.05)
    assert first.status_code == 303
    assert replay.status_code == 403
    assert cluster.servers[0].calls == ["shutdown"], "the action ran exactly once"


@pytest.mark.asyncio
async def test_the_power_routes_refuse_a_post_with_no_session_token(config_dir, clean_node_registry,
                                                                   recording_bot):
    """Every power route carries the session's CSRF dependency, the dialogs included."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    browser = {"Accept": "text/html"}
    async with client(app) as http:
        await sign_in(http, "specialk")
        for path in PAIR + (OFFLINE_DIALOG, ONLINE_DIALOG):
            reply = await http.post(path, data={"node": NODE_NAME}, headers=browser)
            assert reply.status_code == 403, path
            assert "nothing was saved" in reply.text, path
    assert cluster.servers[0].calls == []
    assert recording_bot.calls == []


# -------------------------------------------------------------------------- 9. the audit

@pytest.mark.asyncio
async def test_a_refused_target_and_a_refused_option_each_leave_a_row(config_dir, clean_node_registry,
                                                                     recording_bot):
    """Refusals are audited, including a body this console never sends."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
 # (a) the action's own typed refusal: a name nothing answers to (the online route confirms
 # nothing, so it is the one reachable directly)
        missing = await http.post(ONLINE, data=posted(("node", "NOPE-9"), ("_csrf_token", token)))
        after = await open_page(http, NODES)
 # (b) an option outside the known spellings: 400, and the attempt is recorded
        _, confirm = await open_dialog(http, OFFLINE, NODE_NAME, token)
        bad = await http.post(OFFLINE, data=posted(("node", NODE_NAME), ("_csrf_token", token),
                                                   ("maintenance", "maybe"),
                                                   ("_confirm_token", confirm)))
    assert missing.status_code == 303
    assert "offline or unknown" in after, "the typed refusal is rendered inline"
    assert bad.status_code == 400
    messages = [call[0] for call in recording_bot.calls]
    assert messages[0] == "Node 'NOPE-9' is offline or unknown."
    assert messages[1] == ("refused take_node_offline: invalid option "
                           "(the 'maintenance' option was not one this console sent) "
                           "(node 'dcs-a-01')")
    assert cluster.servers[0].calls == [], "neither attempt ran anything"


@pytest.mark.asyncio
async def test_a_refused_attempt_by_scope_is_audited_and_discloses_nothing(config_dir,
                                                                          clean_node_registry,
                                                                          recording_bot):
    """The destructiveness rule on the pair: a scope-refused attempt IS recorded."""
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
 # narrowed AFTER the install, never before: ``install_shell`` re-declares every capability, and a
 # redeclaration with different roles is refused loudly (``declare_capability``).
    permissions.CAPABILITIES[actions_page.NODE_OFFLINE_CAPABILITY] = ("DCS",)
    async with client(app) as http:
        await sign_in(http, "hoster-b")              # manages Host B only
        token = token_of(await open_page(http, NODES))
        foreign = await http.post(OFFLINE_DIALOG, data={"node": NODE_NAME, "_csrf_token": token})
        missing = await http.post(OFFLINE_DIALOG, data={"node": "NOPE-9", "_csrf_token": token})
    assert foreign.status_code == 403 and missing.status_code == 403
    assert foreign.text == missing.text, "the two refusals must be indistinguishable"
    assert NODE_NAME not in foreign.text
    assert [call[0] for call in recording_bot.calls] == [
        "refused take_node_offline: not permitted (outside your scope) "
        "(node 'dcs-a-01')",
        "refused take_node_offline: not permitted (outside your scope) "
        "(node 'NOPE-9')",
    ]


# -------------------------------------------------------------------- 10. the action itself

def _ctx(servers, nodes, *, scope=None) -> ActionContext:
    """The seam's context over a stub cluster, as the web shell builds it (duck-typed)."""
    bus = types.SimpleNamespace(servers={server.name: server for server in servers}, nodes=nodes)
    identity = types.SimpleNamespace(roles=(), scope=scope, shown_name="probe", backend="local",
                                     subject="probe")
    return ActionContext.from_web(identity, None, bus)


@pytest.mark.asyncio
async def test_offline_refuses_a_node_it_cannot_reach_and_says_so(clean_node_registry,
                                                                 recording_bot):
    """The action's own typed refusal — never a 500, never a success over nothing."""
    discover_actions(["admin"])
    result = await call_action("take_node_offline", _ctx([], {NODE_NAME: None}),
                               node_name=NODE_NAME)
    assert result.success is False
    assert result.message == "Node 'dcs-a-01' is offline or unknown."
    assert [call[0] for call in recording_bot.calls] == [
        "Node 'dcs-a-01' is offline or unknown.",
    ]


@pytest.mark.asyncio
async def test_offline_reports_a_flag_it_could_not_write(clean_node_registry, recording_bot):
    """A per-server failure is counted and stated: never a bare success over a half-done node."""
    discover_actions(["admin"])
    server = StubbornServer("SRS-1", NODE_NAME)
    result = await call_action("take_node_offline", _ctx([server], {NODE_NAME: FakeNode(NODE_NAME)}),
                               node_name=NODE_NAME)
    assert server.calls == ["shutdown"], "the stop is still attempted"
    assert result.success is False
    assert result.message == ("Node 'dcs-a-01': 0 server(s) marked as maintenance and stopped. "
                              "1 server(s) could not be marked as maintenance.")
