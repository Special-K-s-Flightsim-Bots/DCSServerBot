"""The node row's lifecycle controls: Restart / Shut down / Upgrade (Admin-only).

Pins which controls the row offers, from the scoped source, to the role that may use them: the
discovery wiring; the role matrix (Admin only, a crafted POST refused); the state matrix (an offline
node offers no control that cannot work); Upgrade gated on the cached background check; the master
node warned about, not hidden; the one-shot confirm token; the scoped-node resolver's
not-found/not-permitted distinction; and CSRF plus the audit. The trio itself is ``Node.restart()`` /
``Node.shutdown()`` / ``Node.upgrade()`` (``core/data/impl/nodeimpl.py``), each mirrored by an
``@action`` in ``plugins/admin/actions.py``. Driven through the real app and local login backend."""
from __future__ import annotations

import re
import types

import pytest

from conftest import asgi_client, permissions
from core import Status
from core.actions import (ActionContext, NodeResolution, action_available, call_action,
                          discover_actions)
from services.webservice.pages import actions as actions_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.scope import Scope
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    BOT_YAML, Cluster, build, clean_registry, client, config_dir, instance, open_page,
    recording_bot, sign_in, token_of,
)

NODES = nodes_page.NODES_PATH
DASHBOARD = "/"
RESTART = "/actions/node/restart"
SHUTDOWN = "/actions/node/shutdown"
UPGRADE = "/actions/node/upgrade"
NODE_PATHS = (RESTART, SHUTDOWN, UPGRADE)
#: the MAINTENANCE pair -- the same row's fourth and fifth controls.
#: the node row now also carries these two (see ``tests/test_webui_node_maintenance.py``), so every
#: count of "the controls a node renders" in this file moved by one. The property these tests pin is
#: unchanged: what the row offers, from the scoped source, for the role that may use it.
OFFLINE = "/actions/node/offline"
ONLINE = "/actions/node/online"

#: built from the constant so a rename cannot leave a test pointing at a route that is gone
CONFIRM = actions_page.CONFIRM_SUFFIX

#: what a row actually RENDERS for each operation: all five open a DIALOG, so every control posts to
#: its dialog (the offline half's own route refuses a POST that skipped it — asserted further down).
DIALOGS = tuple(path + CONFIRM for path in NODE_PATHS)

#: EVERY control an ``Admin`` sees on a node whose servers are NOT under maintenance: the lifecycle
#: trio plus *Take offline*. The pair is state-gated, so an in-service node is offered the
#: offline half and not the online one.
ADMIN_DIALOGS = frozenset(DIALOGS) | {OFFLINE + CONFIRM}

# ------------------------------------------------------------------------------- test doubles

class FakeNode:
    """The ``Node`` double: the three lifecycle calls, counted.

    ``master`` matters twice over — the read model renders the tag, and the console's copy turns on
    it, because the console runs INSIDE the master node's process.
    """

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


class FailingNode(FakeNode):
    """A node whose lifecycle call RAISES — the action's own failure path, through the route."""

    async def restart(self):
        self.calls.append("restart")
        raise RuntimeError("the node refused to restart")


def node_server(name, node_name, managed_by, *, players=0):
    """A server double shaped where the scope, the read models and the console's copy read it.

    ``maintenance`` is the flag the maintenance pair's state gate reads (``actions.node_state``); it
    is ``False`` here, so a node built from this fleet is IN SERVICE and is offered *Take offline*.
    """
    srv = types.SimpleNamespace(
        name=name, display_name=name, status=Status.RUNNING, maintenance=False,
        node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=f"{node_name}-Instance"), port=10309,
        current_mission=None, is_remote=False,
        players={f"ucid{i}": types.SimpleNamespace(ucid=f"ucid{i}") for i in range(players)},
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )
    srv.get_active_players = lambda: list(srv.players.values())
    return srv


def fleet(*, players=0, b_online=False, node_class=FakeNode):
    """Two nodes: ``dcs-a-01`` (online, the MASTER) and ``dcs-b-01`` (online only on request).

    Both carry exactly one server, one per hoster — so the scope has two sides to prove.
    """
    a = node_server("SRS-1", "dcs-a-01", ["Host A"], players=players)
    b = node_server("SRS-B", "dcs-b-01", ["Host B"], players=players)
    node_a = node_class("dcs-a-01", master=True)
    node_b = node_class("dcs-b-01") if b_online else None
    return Cluster([a, b], nodes={"dcs-a-01": node_a, "dcs-b-01": node_b}, node=node_a,
                   instances=[instance("dcs-a-01-Instance", a), instance("dcs-b-01-Instance", b)])


@pytest.fixture
def clean_node_registry(clean_registry):
    """``clean_registry`` drops ``plugins.mission.actions``; the node actions live in ``admin``.

    Discovery works by IMPORTING a module, and the import is a no-op the second time in one process:
    without dropping ``plugins.admin.actions`` too, the FIRST test would observe a genuine discovery
    and every later one an empty registry — a green suite over controls nobody renders.
    """
    import sys

    sys.modules.pop("plugins.admin.actions", None)
    try:
        yield
    finally:
        sys.modules.pop("plugins.admin.actions", None)


@pytest.fixture(autouse=True)
def pending_upgrade():
    """Seed the CACHED upgrade value for this file's nodes .

    Upgrade is gated on the background poll's cache, so this file's assertions about "the controls a
    node renders" (and its dialogs) need a value: this stands in for a poll that has run and found
    an update pending. The gate's OWN behaviour — unknown, ``False``, and the poller's triggers — is
    the subject of ``tests/test_webui_upgrade_gate.py``.
    """
    from services.webservice import upgrade as upgrade_signal

    upgrade_signal.record("dcs-a-01", True)
    upgrade_signal.record("dcs-b-01", True)
    try:
        yield
    finally:
        upgrade_signal.reset()


def offered(html: str) -> set[str]:
    """The node-control paths the page actually RENDERS."""
    return set(re.findall(r'action="(/actions/node/[^"]+)"', html))


async def both_screens(http) -> dict[str, str]:
    """The standalone /nodes page and the dashboard's Nodes tab — two screens of one component."""
    return {"nodes": await open_page(http, NODES),
            "dashboard": await open_page(http, DASHBOARD + "?tab=nodes")}


def node_dialog(html: str) -> str:
    match = re.search(r'name="_confirm_token" value="([^"]+)"', html)
    assert match, "the dialog carries no confirm token"
    return match.group(1)


# ------------------------------------------------------------------ 1. the discovery wiring

@pytest.mark.asyncio
async def test_the_web_shell_discovers_the_node_actions_in_its_own_process(config_dir,
                                                                          clean_node_registry):
    """``action_available`` for every node action, through the shell's own install.

    Without the wiring in ``install_shell`` the registry stays empty here, every control is silently
    omitted and this file's other assertions would pass over a page that renders nothing.
    """
    for qualname in ("restart_node", "shutdown_node", "upgrade_node"):
        assert action_available(qualname) is False, "the registry must start EMPTY for this test"
    build(config_dir, fleet(), plugins=("mission", "admin"))
    for qualname in ("restart_node", "shutdown_node", "upgrade_node"):
        assert action_available(qualname) is True, qualname


@pytest.mark.asyncio
async def test_an_install_without_the_admin_plugin_offers_no_node_control(config_dir,
                                                                         clean_node_registry):
    """The control for the discovery pin above: with no ``admin`` plugin there is nothing to find."""
    build(config_dir, fleet(), plugins=("mission",))
    assert action_available("restart_node") is False
    assert action_available("shutdown_node") is False
    assert action_available("upgrade_node") is False


# ---------------------------------------------------------------------- 2. the role matrix

@pytest.mark.asyncio
@pytest.mark.parametrize("who, sees_controls", [
    ("specialk", True),      # Admin — the only role that may take a whole node down
    ("mod1", False),         # DCS Admin
    ("hoster", False),       # a MANAGER (scope Host A) — node writes are NOT scope-granted
])
async def test_the_role_matrix_on_both_screens(config_dir, clean_node_registry, who,
                                               sees_controls):
    """The controls, in the rendered markup — ABSENT for a role that may not use them.

    RESTATED: an in-service node now renders FOUR controls (the lifecycle trio
    plus *Take offline*), so the expected set is ``ADMIN_DIALOGS`` rather than the trio's.
    """
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, who)
        pages = await both_screens(http)
    for where, html in pages.items():
        assert (offered(html) == ADMIN_DIALOGS) is sees_controls, f"{who} on {where}"
        if not sees_controls:
            assert "act-strip" not in html, f"{who} on {where}: an icon strip is rendered"


@pytest.mark.asyncio
@pytest.mark.parametrize("who, refused_by_gate", [
    ("mod1", True),          # DCS Admin: the capability is Admin-only, so the gate answers first
    ("hoster", True),        # a manager: node writes carry no scope grant
    ("specialk", False),     # Admin: past the gate, refused by the CONFIRM step instead
])
async def test_a_crafted_post_is_refused_by_the_gate_or_by_the_confirm_step(config_dir,
                                                                           clean_node_registry,
                                                                           recording_bot, who,
                                                                           refused_by_gate):
    """Two different refusals, and neither of them runs an action."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, who)
        page = await open_page(http, NODES)
        reply = await http.post(RESTART, data={"node": "dcs-a-01",
                                               "_csrf_token": token_of(page)})
    assert reply.status_code == 403
    if refused_by_gate:
        assert "Not authorized" in reply.json()["detail"]
    else:
        assert "confirmed through its dialog" in reply.json()["detail"]
    assert cluster.nodes["dcs-a-01"].calls == []
    assert recording_bot.calls == []


# --------------------------------------------------------------------- 3. the state matrix

@pytest.mark.asyncio
async def test_an_online_node_offers_restart_shutdown_and_upgrade_on_both_screens(
        config_dir, clean_node_registry):
    """The controls, one glyph each, with a tooltip AND an accessible name per control.

    RESTATED: an in-service node also offers *Take offline* (the pair is
    state-gated, and these servers are not under maintenance), so the expected set is
    ``ADMIN_DIALOGS``.
    """
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = await both_screens(http)
    for where, html in pages.items():
        assert offered(html) == ADMIN_DIALOGS, where
        assert 'title="Restart node — every server on dcs-a-01 goes down while the node restarts"' in html
        assert 'aria-label="Restart node dcs-a-01"' in html
        assert 'aria-label="Shut down node dcs-a-01"' in html
        assert 'aria-label="Upgrade node dcs-a-01"' in html
 # a control posts to its DIALOG, never straight at the action
        for path in ADMIN_DIALOGS:
            assert f'action="{path}"' in html, path


@pytest.mark.asyncio
async def test_an_offline_node_offers_no_control_and_the_page_says_why(config_dir,
                                                                      clean_node_registry):
    """The heartbeat verdict decides: a node the cluster cannot reach offers nothing at all.

    Its control could only answer a refusal — the node object is ``None`` in ``all_nodes`` — so it is
    OMITTED rather than rendered disabled, and the row states the verdict it was judged by.
    """
    app = build(config_dir, fleet(b_online=False), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = await both_screens(http)
    for where, html in pages.items():
        assert 'value="dcs-b-01"' not in html, f"{where}: a control for the offline node"
        assert offered(html) == ADMIN_DIALOGS, where
        assert "offline" in html, f"{where}: the row does not say the node is offline"
 # the ONLINE node next to it keeps all three — the assertion above is not vacuous
        assert 'value="dcs-a-01"' in html, where


@pytest.mark.asyncio
async def test_the_nodes_page_stopped_claiming_read_only(config_dir, clean_node_registry):
    """The page's own copy, restated and shortened: it offers node controls and names the one
    consequence that matters.

    This is the RENDERED half of the pin restated in ``test_webui_list_pages.py`` (the Nodes page
    left ``READ_ONLY_PAGES``): the old lead's "Read-only." is gone, and the lead now says what the
    page is and that acting on a node takes its servers with it. Card F1 shortened the lead — the
    per-action enumeration moved to the dialogs — so the assertions here follow the shortened copy.

    Asserted on the RENDERED page and on substrings of the module's own constant.
    """
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    assert "Read-only." not in html, "the Nodes page still claims to be read-only"
    for fragment in ("Every node in the cluster, with the instances and servers it carries.",
                     "Acting on a node takes every server on it down with it"):
        assert fragment in nodes_page.NODES_LEAD, fragment
        assert fragment in html, f"the lead is not rendered: {fragment}"


# ------------------------------------------------------------------ 4. the upgrade signal

def test_upgrade_declares_the_cached_check_gate():
    """Upgrade carries the CHECK gate, and its copy no longer claims the console cannot tell.

    ``Node.upgrade_pending()`` is an async git/HTTP check (the local node) or an RPC (a remote one);
    the console polls it in the background (``services/webservice/upgrade.py``) and the render reads
    only the cache. The declaration is the ONE place the gate and the wording live: it carries
    ``check=True`` (offered only while the cached value is ``True``) and the dialog says the node
    itself reports what is pending.
    """
    action = next(a for a in actions_page.NODE_ACTIONS if a.key == "upgrade")
    assert action.check is True, "Upgrade must declare the cached-check gate"
    assert "reports an update pending" in action.detail
    assert "asks every node in the background" in action.detail
    assert "cannot tell whether a newer version is available" not in action.detail
 # …and the console never issues a call from the render path: the declaration carries no signal
 # field at all, so a render cannot even be tempted to read one (the value is read from the cache).
    import dataclasses

    assert not [field.name for field in dataclasses.fields(action)
                if "pending" in field.name or "rpc" in field.name]


# ------------------------------------------------------------------- 5. the master's own row

@pytest.mark.asyncio
async def test_the_master_node_is_warned_about_and_still_offered(config_dir, clean_node_registry,
                                                                 recording_bot):
    """WARN AND ALLOW, never hidden and never refused: the console runs inside that node.

    Refusing would make the master the one node an Admin cannot restart from the console; hiding the
    control would be a silent per-row inconsistency. So the dialog states the consequence the machine
    cannot escape — this very page's process dies with it — and the action runs.
    """
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, NODES)
        dialog = await http.post(RESTART + CONFIRM,
                                 data={"node": "dcs-a-01", "_csrf_token": token_of(page)})
        token = node_dialog(dialog.text)
        accepted = await http.post(RESTART, data={"node": "dcs-a-01",
                                                  "_csrf_token": token_of(page),
                                                  "_confirm_token": token})
    assert dialog.status_code == 200
    assert "This console runs inside that node" in dialog.text
    assert accepted.status_code == 303
    assert cluster.nodes["dcs-a-01"].calls == ["restart"]


@pytest.mark.asyncio
async def test_an_agent_node_dialog_carries_no_master_note(config_dir, clean_node_registry):
    """The positive control: the master warning is not copy every node's dialog wears."""
    app = build(config_dir, fleet(b_online=True), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, NODES)
        dialog = await http.post(RESTART + CONFIRM,
                                 data={"node": "dcs-b-01", "_csrf_token": token_of(page)})
    assert dialog.status_code == 200
    assert "This console runs inside that node" not in dialog.text


# ------------------------------------------------------------------------ 6. the confirm step

@pytest.mark.asyncio
async def test_a_confirm_required_node_route_refuses_a_post_that_skipped_its_dialog(
        config_dir, clean_node_registry, recording_bot):
    """A confirmation ``curl`` can skip is decoration: the token is checked SERVER-side."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, NODES)
        token = token_of(page)

        for path in NODE_PATHS:
            direct = await http.post(path, data={"node": "dcs-a-01", "_csrf_token": token})
            assert direct.status_code == 403, path
        assert cluster.nodes["dcs-a-01"].calls == [], "nothing may run without the confirmation"

        dialog = await http.post(RESTART + CONFIRM,
                                 data={"node": "dcs-a-01", "_csrf_token": token})
        assert dialog.status_code == 200
        assert "Every server on dcs-a-01 goes down with it" in dialog.text
        assert "Restart node" in dialog.text
 # the numbers in the warning come from the RESOLVED node, never from the request
        assert "1 server(s) and 0 player(s)" in dialog.text

        confirm = node_dialog(dialog.text)
        accepted = await http.post(RESTART, data={"node": "dcs-a-01", "_csrf_token": token,
                                                  "_confirm_token": confirm})
        assert accepted.status_code == 303
        assert cluster.nodes["dcs-a-01"].calls == ["restart"]

 # the token is SPENT: a replayed dialog form must not run the action a second time
        replay = await http.post(RESTART, data={"node": "dcs-a-01", "_csrf_token": token,
                                                "_confirm_token": confirm})
        assert replay.status_code == 403
    assert cluster.nodes["dcs-a-01"].calls == ["restart"]


@pytest.mark.asyncio
async def test_the_three_node_dialogs_name_the_consequence(config_dir, clean_node_registry):
    """The copy is asserted, so it cannot drift into \"are you sure?\" — one per action."""
    app = build(config_dir, fleet(players=3), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        restart = await http.post(RESTART + CONFIRM,
                                  data={"node": "dcs-a-01", "_csrf_token": token})
        shutdown = await http.post(SHUTDOWN + CONFIRM,
                                   data={"node": "dcs-a-01", "_csrf_token": token})
        upgrade = await http.post(UPGRADE + CONFIRM,
                                  data={"node": "dcs-a-01", "_csrf_token": token})

    assert "1 server(s) and 3 player(s)" in restart.text
    assert "goes down with it" in restart.text
    assert "Restart node" in restart.text

    assert "<b>1 server(s) on dcs-a-01 go down and 3 player(s) are disconnected" in shutdown.text
    assert "nothing brings the node back" in shutdown.text
    assert "cannot be started from this browser" in shutdown.text
    assert "Shut down node" in shutdown.text

    assert "reports an update pending" in upgrade.text
    assert "asks every node in the background" in upgrade.text
    assert "Upgrade node" in upgrade.text

    for dialog in (restart, shutdown, upgrade):
        assert "are you sure" not in dialog.text.lower()
        assert f'name="{actions_page.CONFIRM_FIELD}"' in dialog.text
        assert 'name="node" value="dcs-a-01"' in dialog.text


@pytest.mark.asyncio
async def test_a_dialog_for_the_offline_node_is_a_404_not_a_control(config_dir,
                                                                    clean_node_registry):
    """A node the cluster cannot reach is not a target: the dialog answers honestly."""
    app = build(config_dir, fleet(b_online=False), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        dialog = await http.post(RESTART + CONFIRM,
                                 data={"node": "dcs-b-01", "_csrf_token": token})
    assert dialog.status_code == 404
    assert "dcs-b-01" in dialog.json()["detail"]


# ------------------------------------------------------------------------------- 7. the seam

def _identity(scope):
    return types.SimpleNamespace(roles=(), scope=scope, shown_name="probe", backend="local",
                                 subject="probe")


@pytest.mark.asyncio
async def test_the_seam_keeps_the_not_found_not_permitted_distinction_for_nodes():
    """``resolve_scoped_node`` mirrors ``resolve_scoped_server``: two failures, two answers.

    An unrestricted caller (``Admin``) gets the honest NOT_FOUND for a name it cannot resolve — an
    offline node included, because nothing can reach it either. A restricted caller gets
    NOT_PERMITTED for everything outside its view, so it can never enumerate the cluster by probing
    names, and the two answers are the SAME answer for a name that exists and one that does not.
    """
    node_a, node_b = FakeNode("dcs-a-01"), FakeNode("dcs-b-01")
    full = types.SimpleNamespace(servers={}, nodes={"dcs-a-01": node_a, "dcs-b-01": node_b})
    admin = ActionContext.from_web(_identity(Scope.everything()), None, full)
    assert (await admin.resolve_scoped_node("dcs-a-01")).status == NodeResolution.FOUND
    assert (await admin.resolve_scoped_node("dcs-a-01")).node is node_a
    assert (await admin.resolve_scoped_node("nope-9")).status == NodeResolution.NOT_FOUND

    offline = types.SimpleNamespace(servers={}, nodes={"dcs-a-01": None})
    admin = ActionContext.from_web(_identity(Scope.everything()), None, offline)
    resolution = await admin.resolve_scoped_node("dcs-a-01")
    assert resolution.status == NodeResolution.NOT_FOUND
    assert not resolution.is_found, "an offline node is not a target"
    assert "dcs-a-01" in resolution.message

 # a manager's bus carries ONLY the nodes their servers are on
    scoped = types.SimpleNamespace(servers={}, nodes={"dcs-b-01": node_b})
    manager = ActionContext.from_web(_identity(Scope.restricted({"Host B"})), None, scoped)
    assert (await manager.resolve_scoped_node("dcs-b-01")).status == NodeResolution.FOUND
    for name in ("dcs-a-01", "nope-9"):
        assert (await manager.resolve_scoped_node(name)).status == NodeResolution.NOT_PERMITTED, name


@pytest.mark.asyncio
async def test_the_seam_resolves_nodes_without_a_bus_that_carries_them(clean_node_registry):
    """The other transports hand in the real ``ServiceBus``, which carries no ``nodes`` mapping.

    The fallback is the process's own node registry (``node.all_nodes``), so the SAME action reachable
    from Discord keeps working — and an empty registry answers NOT_FOUND rather than raising.
    """
    node_a = FakeNode("dcs-a-01")
    bus = types.SimpleNamespace(servers={})
    process_node = types.SimpleNamespace(name="dcs-a-01", all_nodes={"dcs-a-01": node_a})
    ctx = ActionContext(node=process_node, bus=bus)
    assert ctx.resolve_node("dcs-a-01") is node_a
    assert ctx.resolve_node("DCS-A-01") is node_a, "the node lookup folds case like the server one"
    assert ctx.resolve_node("nope") is None


# ------------------------------------------------------------------------------- 8. the CSRF

@pytest.mark.asyncio
async def test_the_node_routes_refuse_a_post_with_no_session_token(config_dir, clean_node_registry,
                                                                  recording_bot):
    """Every node route carries the session's CSRF dependency, the dialogs included."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    browser = {"Accept": "text/html"}
    async with client(app) as http:
        await sign_in(http, "specialk")
        for path in NODE_PATHS + tuple(path + CONFIRM for path in NODE_PATHS):
            reply = await http.post(path, data={"node": "dcs-a-01"}, headers=browser)
            assert reply.status_code == 403, path
            assert "nothing was saved" in reply.text, path
    assert cluster.nodes["dcs-a-01"].calls == []
    assert recording_bot.calls == []


# ------------------------------------------------------------------------------ 9. the audit

@pytest.mark.asyncio
async def test_the_audit_records_the_success_the_failure_and_the_refused_dialog(
        config_dir, clean_node_registry, recording_bot):
    """One entry per ATTEMPT, naming the web actor and the node.

    Three outcomes, three entries: the success, the action's own failure (a node that refuses to restart),
    and -- separately, below -- the refused-permission dialog. The trail is the ACTION's.
    """
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        dialog = await http.post(RESTART + CONFIRM,
                                 data={"node": "dcs-a-01", "_csrf_token": token})
        confirm = node_dialog(dialog.text)
        first = await http.post(RESTART, data={"node": "dcs-a-01", "_csrf_token": token,
                                               "_confirm_token": confirm})
        assert first.status_code == 303

 # the SAME node, now failing: the double raises inside its lifecycle call
        cluster.nodes["dcs-a-01"].__class__ = FailingNode
        token = token_of(await open_page(http, NODES))
        dialog = await http.post(RESTART + CONFIRM,
                                 data={"node": "dcs-a-01", "_csrf_token": token})
        second = await http.post(RESTART, data={"node": "dcs-a-01", "_csrf_token": token,
                                                "_confirm_token": node_dialog(dialog.text)})
        assert second.status_code == 303

    messages = [call[0] for call in recording_bot.calls]
    assert messages == [
        "Node 'dcs-a-01' restarted.",
        "Failed to restart node: the node refused to restart",
    ], messages


@pytest.mark.asyncio
async def test_the_audit_records_a_refused_permission_dialog_and_discloses_nothing(
        config_dir, clean_node_registry, recording_bot):
    """A refused attempt IS recorded.

    The reachable refusal is the DIALOG's: the action's own route checks the confirm token first, so a
    scope-refused caller cannot even reach it -- the dialog is where the scope is applied. The capability
    is narrowed here ONLY to get such a caller through the gate; the declaration itself is Admin-only,
    asserted in the role matrix above.
    """
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
 # narrowed AFTER the install, never before: ``install_shell`` re-declares every capability, and a
 # redeclaration with different roles is refused loudly (``declare_capability``).
    permissions.CAPABILITIES[actions_page.NODE_RESTART_CAPABILITY] = ("DCS",)
    async with client(app) as http:
        await sign_in(http, "hoster-b")             # manages Host B only
        token = token_of(await open_page(http, NODES))
        foreign = await http.post(RESTART + CONFIRM,
                                  data={"node": "dcs-a-01", "_csrf_token": token})
        missing = await http.post(RESTART + CONFIRM,
                                  data={"node": "NOPE-9", "_csrf_token": token})
    assert foreign.status_code == 403 and missing.status_code == 403
    assert foreign.text == missing.text, "the two refusals must be indistinguishable"
    assert "dcs-a-01" not in foreign.text
    assert cluster.nodes["dcs-a-01"].calls == []
    assert [call[0] for call in recording_bot.calls] == [
        "refused restart_node: not permitted (outside your scope) "
        "(node 'dcs-a-01')",
        "refused restart_node: not permitted (outside your scope) "
        "(node 'NOPE-9')",
    ]


# ------------------------------------------------------------- 10. one implementation, 3 paths

@pytest.mark.asyncio
async def test_both_screens_render_the_same_node_strip(config_dir, clean_node_registry):
    """The dashboard's Nodes tab and /nodes are two screens of ONE component: one map, one partial."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, NODES)
        dashboard = await open_page(http, DASHBOARD + "?tab=nodes")
    assert offered(page) == offered(dashboard) == ADMIN_DIALOGS
    assert 'action="{0}"'.format(SHUTDOWN + CONFIRM) in page
    assert 'action="{0}"'.format(SHUTDOWN + CONFIRM) in dashboard


@pytest.mark.asyncio
async def test_every_node_control_points_at_a_glyph_that_exists():
    """No control may name a glyph the partial does not draw (an empty ``<svg>`` in a button).

    The node trio shares ``restart`` and ``shutdown`` with the server row -- one word, one glyph -- and
    owns ``upgrade``; the maintenance pair owns the node row's ``online``/``offline`` pair, which changes
    only the servers on the node.
    """
    from pathlib import Path

    source = Path(actions_page.__file__).parent.parent.joinpath("templates", "_icons.html")
    glyphs = set(re.findall(r"'([a-z_]+)':\s*'", source.read_text(encoding="utf-8")))
    missing = sorted({action.key for action in actions_page.NODE_ACTIONS} - glyphs)
    assert missing == [], f"node control(s) with no glyph: {missing}"
 # the maintenance pair's own glyphs are DRAWN, and they are the mockup's reviewed pair — not a
 # fallback to the trio's (a glyph that silently falls back renders an empty <svg>)
    assert {"online", "offline"} <= glyphs, "the maintenance pair's glyphs are missing"
    keys = {action.key: action for action in actions_page.NODE_ACTIONS}
    assert keys["offline"].key == "offline" and keys["online"].key == "online"


# -------------------------------------------------------------------- 11. the action's guard

@pytest.mark.asyncio
async def test_a_node_action_refuses_a_target_it_cannot_reach(clean_node_registry):
    """The action's own typed refusal for a name no node answers to — never a 500, never a success.

    Reached directly (not through the route): the route's confirm step refuses a POST the dialog did
    not mint, so this guard is what a stale page or another transport meets.
    """
    discover_actions(["admin"])
    bus = types.SimpleNamespace(servers={}, nodes={})
    ctx = ActionContext(node=None, bus=bus)
    result = await call_action("restart_node", ctx, node_name="NOPE-9")
    assert result.success is False
    assert "NOPE-9" in result.message
    assert "offline or unknown" in result.message


# ------------------------------------------------------------------- 12. the upgrade's own check

class CheckedNode(FakeNode):
    """A node that answers ``upgrade_pending()`` — the guard ``/node upgrade`` runs first.

    The plain:class:`FakeNode` deliberately does NOT define it (an older agent, a double), which is
    the "cannot check here" branch: the operation proceeds as before.
    """

    def __init__(self, name, *, master=False, pending=True):
        super().__init__(name, master=master)
        self.pending = pending

    async def upgrade_pending(self):
        self.calls.append("upgrade_pending")
        return self.pending


@pytest.mark.asyncio
async def test_an_upgrade_with_nothing_pending_is_refused_and_never_calls_upgrade(clean_node_registry,
                                                                                 recording_bot):
    """``/node upgrade`` refuses with "There is no upgrade available" when nothing is pending.

    Without the check the console would call ``Node.upgrade()``, which logs "No update found" and returns,
    and the console would report a success that never happened. The refusal mirrors the Discord arm
    (``plugins/admin/commands.py``) word for word, and it is audited one entry per attempt.
    """
    discover_actions(["admin"])
    node = CheckedNode("dcs-a-01", pending=False)
    bus = types.SimpleNamespace(servers={}, nodes={"dcs-a-01": node})
    ctx = ActionContext(node=None, bus=bus)
    result = await call_action("upgrade_node", ctx, node_name="dcs-a-01")
    assert result.success is False
    assert result.message == "There is no upgrade available for node 'dcs-a-01'."
    assert node.calls == ["upgrade_pending"], "upgrade() must not run with nothing to upgrade"
    assert [call[0] for call in recording_bot.calls] == [
        "There is no upgrade available for node 'dcs-a-01'."]


@pytest.mark.asyncio
async def test_an_upgrade_that_is_pending_runs_and_is_audited(clean_node_registry, recording_bot):
    """The positive control: the check is a GATE, not a refusal of every upgrade."""
    discover_actions(["admin"])
    node = CheckedNode("dcs-a-01", pending=True)
    bus = types.SimpleNamespace(servers={}, nodes={"dcs-a-01": node})
    ctx = ActionContext(node=None, bus=bus)
    result = await call_action("upgrade_node", ctx, node_name="dcs-a-01")
    assert result.success is True
    assert node.calls == ["upgrade_pending", "upgrade"]
    assert [call[0] for call in recording_bot.calls] == ["Node 'dcs-a-01' upgraded."]


@pytest.mark.asyncio
async def test_the_upgrade_refusal_reaches_the_page_through_the_real_route(config_dir,
                                                                          clean_node_registry,
                                                                          recording_bot):
    """The whole path: confirm dialog → the action's check → the audit — for a real Admin session.

    Driven through the application because the acceptance is about what a signed-in Admin experiences,
    not about the function: the dialog renders (the control is offered unconditionally), the action
    then refuses, and the trail names the web actor and the node.
    """
    cluster = fleet(node_class=CheckedNode)
    cluster.nodes["dcs-a-01"].pending = False
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http, NODES))
        dialog = await http.post(UPGRADE + CONFIRM,
                                 data={"node": "dcs-a-01", "_csrf_token": token})
        assert dialog.status_code == 200
        assert "reports an update pending" in dialog.text
        accepted = await http.post(UPGRADE, data={"node": "dcs-a-01", "_csrf_token": token,
                                                  "_confirm_token": node_dialog(dialog.text)})
    assert accepted.status_code == 303
    assert cluster.nodes["dcs-a-01"].calls == ["upgrade_pending"], "no upgrade was launched"
    assert [call[0] for call in recording_bot.calls] == [
        "There is no upgrade available for node 'dcs-a-01'.",
    ]
