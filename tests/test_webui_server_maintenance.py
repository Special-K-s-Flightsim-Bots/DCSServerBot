"""The server row's maintenance flag pair: ``Maintenance`` / ``End maintenance``.

The flag (``servers.maintenance``) is the scheduler's concept; this card gives it its ONE home in the
console, the server row (it used to hide inside the node row's *Bring online*, which copied every
server's flag). Pins: the discovery wiring; the state gate per row on BOTH screens (*Maintenance* only
on an unflagged server, *End maintenance* only on a flagged one, in EVERY status -- a flag is not a
power state); the roles and the scope (``Admin`` and ``DCS Admin`` on any server in view, a MANAGER on
their own only, a foreign-server POST refused indistinguishably from a nonexistent one, and the
capability declaration asserted where it lives); the round trip (the flag flips, the other server is
untouched, the audit names the web actor); the action's own typed refusal for an already-in-state
server; the set half aborting a pending restart; no confirmation (direct submit) with CSRF enforced;
the row renders the flag; and no bulk flag control on the node row. Driven through the real app."""
from __future__ import annotations

import re
import types
from typing import Any

import pytest

from conftest import permissions
from core import Status
from core.actions import ActionContext, action_available, call_action, discover_actions
from services.webservice.pages import actions as actions_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import servers as servers_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    BOT_YAML, Cluster, build, clean_registry, client, config_dir, instance, open_page,
    recording_bot, sign_in, token_of,
)

SERVERS = servers_page.SERVERS_PATH
NODES = nodes_page.NODES_PATH
DASHBOARD = "/"

#: the two controls this card adds, and the capabilities behind them
MAINTENANCE = "/actions/server/maintenance"
END_MAINTENANCE = "/actions/server/end-maintenance"
PAIR = (MAINTENANCE, END_MAINTENANCE)

#: every server status the state table is proven on. The flag pair applies in ALL of them — the two
#: transient ones included, which is the point of the design's "a flag is not a power state".
EVERY_STATUS = (Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.SHUTDOWN, Status.LOADING,
                Status.SHUTTING_DOWN)


# ------------------------------------------------------------------------------- test doubles

class FlagServer:
    """A server double: the flag, the four triggers a set aborts, and the fields the row reads.

    ``maintenance`` is what the control's gate reads and what the action writes; ``restart_pending``
    and the three trigger sets are what:func:`plugins.scheduler.actions._abort_pending_restart`
    clears. ``calls`` is unused by these actions (a flag is not a power operation) and is here so a
    test can ASSERT that: nothing about the process may be touched.
    """

    def __init__(self, name, node_name="dcs-a-01", managed_by=(), *, status=Status.RUNNING,
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

    def get_active_players(self):
        return list(self.players.values())


def fleet(*, status=Status.RUNNING, flagged=(), node_class=None):
    """Two servers, one hoster each — ``SRS-1`` (Host A, the master's node) and ``SRS-B`` (Host B).

    ``flagged`` names the servers whose maintenance flag is ON, so one fleet can carry both sides of
    the flag gate at once and the per-row pin is never vacuous.
    """
    a = FlagServer("SRS-1", "dcs-a-01", ["Host A"], status=status,
                   maintenance="SRS-1" in flagged)
    b = FlagServer("SRS-B", "dcs-b-01", ["Host B"], status=status,
                   maintenance="SRS-B" in flagged)
    node_a = (node_class or types.SimpleNamespace)(name="dcs-a-01", master=True, instances={})
    return Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


@pytest.fixture
def clean_scheduler_registry(clean_registry):
    """``clean_registry`` drops ``plugins.mission.actions``; this card's actions live in the scheduler.

    Discovery works by IMPORTING a module, and the import is a no-op the second time in one process:
    without dropping ``plugins.scheduler.actions`` too, the FIRST test would observe a genuine
    discovery and every later one an empty registry — a green suite over controls nobody renders.
    """
    import sys

    sys.modules.pop("plugins.scheduler.actions", None)
    try:
        yield
    finally:
        sys.modules.pop("plugins.scheduler.actions", None)


def offered(html: str) -> set[str]:
    """The server-control paths the page actually RENDERS."""
    return set(re.findall(r'action="(/actions/server/[^"]+)"', html))


def row_for(html: str, name: str) -> str:
    """The ``<tr>`` that belongs to *name* — ONE row, never a span from the header row down."""
    for row in re.findall(r"<tr>.*?</tr>", html, re.S):
        if name in row:
            return row
    raise AssertionError(f"no row for {name}")


async def screens(http) -> dict[str, str]:
    """The standalone /fleet page and the dashboard's Servers tab — two screens of one component."""
    return {"servers": await open_page(http, SERVERS),
            "dashboard": await open_page(http, DASHBOARD)}


def build_with_scheduler(config_dir, cluster, users=None):
    """An install that HAS the scheduler plugin: without it the pair cannot be discovered at all."""
    return build(config_dir, cluster, plugins=("mission", "scheduler"), users=users)


# ------------------------------------------------------------------ 1. the discovery wiring

@pytest.mark.asyncio
async def test_the_web_shell_discovers_the_flag_actions_in_its_own_process(config_dir,
                                                                          clean_scheduler_registry):
    """``action_available`` for both halves, through the shell's own install.

    The flag actions live in ``plugins/scheduler/actions.py``, so the plugin list the shell is handed
    decides whether the console can offer them AT ALL. Without the wiring the registry stays empty
    here, every control is silently omitted and this file's other assertions would pass over a page
    that renders nothing.
    """
    for qualname in ("set_maintenance", "clear_maintenance"):
        assert action_available(qualname) is False, "the registry must start EMPTY for this test"
    build(config_dir, fleet(), plugins=("mission", "scheduler"))
    for qualname in ("set_maintenance", "clear_maintenance"):
        assert action_available(qualname) is True, qualname


@pytest.mark.asyncio
async def test_an_install_without_the_scheduler_plugin_offers_no_flag_control(
        config_dir, clean_scheduler_registry):
    """The control for the discovery pin above: the flag is the SCHEDULER's, so no plugin, no control.

    It also states the boundary plainly: the console does not re-implement the flag when the plugin
    that owns it is absent — it offers nothing, which is the same shape every other write has
    (``action_available``).
    """
    app = build(config_dir, fleet(), plugins=("mission",))
    for qualname in ("set_maintenance", "clear_maintenance"):
        assert action_available(qualname) is False
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http)
    for path in PAIR:
        assert f'action="{path}"' not in html, path


# ---------------------------------------------------------------------- 2. the state gate

@pytest.mark.asyncio
@pytest.mark.parametrize("status", EVERY_STATUS, ids=[s.name.lower() for s in EVERY_STATUS])
async def test_the_flag_pair_is_offered_in_every_status_on_both_screens(config_dir,
                                                                       clean_scheduler_registry,
                                                                       status):
    """A flag is NOT a power state: the pair applies while the process is up, down or between.

    The row is asserted rather than the page, and the fleet carries BOTH flag states at once, so this
    pins the per-row gate rather than "some control somewhere". LOADING and SHUTTING_DOWN are in the
    list on purpose: those are the two states where the PROCESS controls are withheld, and the flag
    pair is the one family that is still offered there.
    """
    app = build_with_scheduler(config_dir, fleet(status=status))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = await screens(http)
    for where, html in pages.items():
        row = row_for(html, "SRS-1")
        assert f'action="{MAINTENANCE}"' in row, f"{where} at {status.value}: Maintenance"
        assert f'action="{END_MAINTENANCE}"' not in row, \
            f"{where} at {status.value}: the server is NOT flagged, so End maintenance may not appear"


@pytest.mark.asyncio
async def test_a_flagged_server_is_offered_end_maintenance_and_not_maintenance(
        config_dir, clean_scheduler_registry):
    """The other side of the gate, with the sibling server as the control that must not follow.

    Both rows are asserted on ONE page: ``SRS-1`` is flagged and offers *End maintenance*; ``SRS-B``
    is not and offers *Maintenance* — so the gate is per row and neither control is offered twice.
    """
    app = build_with_scheduler(config_dir, fleet(flagged={"SRS-1"}))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = await screens(http)
    for where, html in pages.items():
        flagged_row = row_for(html, "SRS-1")
        assert f'action="{END_MAINTENANCE}"' in flagged_row, where
        assert f'action="{MAINTENANCE}"' not in flagged_row, where
        plain_row = row_for(html, "SRS-B")
        assert f'action="{MAINTENANCE}"' in plain_row, where
        assert f'action="{END_MAINTENANCE}"' not in plain_row, where


@pytest.mark.asyncio
async def test_the_row_says_the_flag_and_it_is_the_read_model_that_carries_it(
        config_dir, clean_scheduler_registry):
    """``ServerView.maintenance`` is rendered, so an operator can SEE which servers are out of service.

    The field exists on the read model for exactly this: the control is gated on the flag, and a flag
    nobody can see would make an absent control read as a missing feature.
    """
    app = build_with_scheduler(config_dir, fleet(flagged={"SRS-1"}))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http)
    assert '<span class="tag">maintenance</span>' in row_for(html, "SRS-1")
    assert '<span class="tag">maintenance</span>' not in row_for(html, "SRS-B")


def test_the_declaration_carries_the_console_roles_and_the_scope_rule():
    """Asserted where the decision lives instead of through a rendering.

    The two capabilities are ``WRITE_ROLES`` (``Admin`` + ``DCS Admin``) WITH ``scope_grants``, so a
    manager may flag their own server -- deliberately the SERVER rule: the node write map stays
    Admin-only and unscoped (``NODE_ROLES``), pinned by the second half.
    """
    actions_page.declare()
    for capability in (actions_page.SERVER_MAINTENANCE_CAPABILITY,
                       actions_page.SERVER_CLEAR_MAINTENANCE_CAPABILITY):
        assert capability in permissions.CAPABILITIES, capability
        assert permissions.CAPABILITIES[capability] == actions_page.WRITE_ROLES, capability
        assert permissions.SCOPE_GRANTS[capability] is True, capability
    for capability in (actions_page.NODE_OFFLINE_CAPABILITY, actions_page.NODE_ONLINE_CAPABILITY):
        assert permissions.CAPABILITIES[capability] == ("Admin",), capability
        assert permissions.SCOPE_GRANTS[capability] is False, capability
 # the two halves are TWO capabilities, each named for its own operation (the PAUSE/UNPAUSE shape)
    assert (actions_page.SERVER_MAINTENANCE_CAPABILITY
            != actions_page.SERVER_CLEAR_MAINTENANCE_CAPABILITY)


@pytest.mark.asyncio
async def test_the_servers_page_copy_names_the_page_and_what_acting_means(config_dir,
                                                                         clean_scheduler_registry):
    """The page's own lead is what the page IS plus what a reader must know before acting.

    Card I5 trimmed it the way card F1 trimmed the Nodes page's lead: the old 377-character wall
    enumerated every row control (Pause/Unpause, Restart/Shutdown, Maintenance/End maintenance,
    Start/Stop) — the same defect the node page had. The enumeration is GONE (each control's own
    tooltip and dialog carry that copy); what remains is the page and one thing a reader must know.
    """
    app = build_with_scheduler(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, SERVERS)
    for fragment in ("Every server on every node, in full.",
                     "A row you may operate carries its controls on the row",
                     "acting on one affects that server alone"):
        assert fragment in servers_page.SERVERS_LEAD, fragment
        assert fragment.replace("'", "&#39;") in html, f"the lead is not rendered: {fragment}"
 # the per-control wall is GONE from the lead (card I5)
    for gone in ("Pause/Unpause for the mission",
                 "Maintenance/End maintenance for the flag",
                 "Start/Stop live in the row's menu",
                 "The flag has this one home"):
        assert gone not in servers_page.SERVERS_LEAD, gone


# ---------------------------------------------------------------------- 3. the role matrix

def flag_control_offered(html: str, server: str) -> bool:
    """Whether *server*'s ROW carries one of the two flag controls — the control, not just the row.

    Asserting ``value="SRS-1"`` alone would be vacuous: every other server-row write carries that
    hidden field too, so a role refused ONLY the flag pair would still pass. The pin is on the two
    paths this card adds and nothing else.
    """
    if server not in html:
        return False
    row = row_for(html, server)
    return MAINTENANCE in row or END_MAINTENANCE in row


@pytest.mark.asyncio
@pytest.mark.parametrize("who, sees_own, sees_foreign", [
    ("specialk", True, True),       # Admin — unscoped by rule, so both rows are theirs
    ("mod1", True, False),          # DCS Admin; their local account declares a scope of Host A
    ("hoster", True, False),        # a pure MANAGER: their own server only, through the scope
    ("hoster-b", False, True),      # a manager of the OTHER server: same rule, mirrored
    ("plain", False, False),        # no capability at all
])
async def test_the_role_matrix_on_the_server_row(config_dir, clean_scheduler_registry, who,
                                                 sees_own, sees_foreign):
    """Who is offered the pair, on the rendered markup — asserted as ABSENCE for a role without it.

    SRS-1 is Host A's server and SRS-B is Host B's. A row outside a manager's scope is not in the
    scoped source at all, so for that caller it is not in the markup either.
    """
    app = build_with_scheduler(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, who)
        response = await http.get(SERVERS)
        html = response.text if response.status_code == 200 else ""
    if who == "plain":
        assert response.status_code == 403, "a DCS member reads no console at all"
        assert "act-strip" not in html
        return
    assert response.status_code == 200, who
    assert flag_control_offered(html, "SRS-1") is sees_own, f"{who}: Host A's server"
    assert flag_control_offered(html, "SRS-B") is sees_foreign, f"{who}: Host B's server"
    if who == "specialk":
 # an unscoped Admin reaches the pair on BOTH rows: the pin above is not measuring one row
        assert flag_control_offered(html, "SRS-1") and flag_control_offered(html, "SRS-B")


@pytest.mark.asyncio
async def test_a_crafted_post_for_a_foreign_server_is_refused_indistinguishably(
        config_dir, clean_scheduler_registry, recording_bot):
    """No control for somebody else's server, and one bare refusal for both.

    A scope refusal is the ROUTE's answer and happens before any action, so it carries no ActionResult
    and leaves no audit entry.
    """
    cluster = fleet()
    app = build_with_scheduler(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "hoster-b")              # manages Host B only
        token = token_of(await open_page(http))
        foreign = await http.post(MAINTENANCE, data={"server": "SRS-1", "_csrf_token": token})
        missing = await http.post(MAINTENANCE, data={"server": "NOPE-9", "_csrf_token": token})
    assert foreign.status_code == 403 and missing.status_code == 403
    assert foreign.text == missing.text, "the two refusals must be indistinguishable"
    assert "SRS-1" not in foreign.text
    assert cluster.servers[0].maintenance is False, "nothing may mutate for a refused caller"
    assert recording_bot.calls == []


@pytest.mark.asyncio
async def test_an_anonymous_post_is_refused(config_dir, clean_scheduler_registry, recording_bot):
    """No session at all: the gate answers, and nothing runs."""
    cluster = fleet()
    app = build_with_scheduler(config_dir, cluster)
    async with client(app) as http:
        for path in PAIR:
            reply = await http.post(path, data={"server": "SRS-1", "_csrf_token": "t" * 43})
            assert reply.status_code == 403, path
    assert cluster.servers[0].maintenance is False
    assert recording_bot.calls == []


# ------------------------------------------------------------------------ 4. the round trip

@pytest.mark.asyncio
async def test_the_round_trip_flags_and_unflags_ONE_server(config_dir, clean_scheduler_registry,
                                                           recording_bot):
    """The pair's whole point: the flag flips on the named server and NOWHERE else."""
    cluster = fleet()
    app = build_with_scheduler(config_dir, cluster)
    server, sibling = cluster.servers
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        assert f'action="{MAINTENANCE}"' in row_for(page, "SRS-1")

        set_reply = await http.post(MAINTENANCE, data={"server": "SRS-1",
                                                       "_csrf_token": token_of(page)})
        assert set_reply.status_code == 303

        after = await open_page(http)
        assert server.maintenance is True
        assert sibling.maintenance is False, "the sibling server's flag is untouched"
        assert f'action="{END_MAINTENANCE}"' in row_for(after, "SRS-1")
        assert f'action="{MAINTENANCE}"' not in row_for(after, "SRS-1")
        assert "Maintenance mode set for server" in after, "the notice reports the outcome"

        clear_reply = await http.post(END_MAINTENANCE, data={"server": "SRS-1",
                                                             "_csrf_token": token_of(after)})
        assert clear_reply.status_code == 303
        final = await open_page(http)
        assert server.maintenance is False
        assert f'action="{MAINTENANCE}"' in row_for(final, "SRS-1")

 # a flag is NOT a power operation: nothing about the process was touched
    assert server.calls == [] and sibling.calls == []
    assert [call[0] for call in recording_bot.calls] == [
        "Maintenance mode set for server \"SRS-1\".",
        "Maintenance mode cleared for server \"SRS-1\".",
    ]


@pytest.mark.asyncio
async def test_setting_the_flag_aborts_a_pending_restart(config_dir, clean_scheduler_registry,
                                                         recording_bot):
    """The one consequence of setting the flag, asserted: everything waiting to restart is dropped.

    This is what ``/scheduler maintenance`` does today (``plugins/scheduler/commands.py``) and the
    reason its Discord arm keeps a ``yn_question`` — the ACTION cannot ask, so it clears and reports.
    """
    cluster = fleet()
    app = build_with_scheduler(config_dir, cluster)
    server = cluster.servers[0]
    server.restart_pending = True
    server.on_empty = {"restart"}
    server.on_mission_end = {"restart"}
    server.on_coalition_win = {"restart"}
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http))
        reply = await http.post(MAINTENANCE, data={"server": "SRS-1", "_csrf_token": token})
    assert reply.status_code == 303
    assert server.maintenance is True
    assert server.restart_pending is False
    assert server.on_empty == set() and server.on_mission_end == set() and server.on_coalition_win == set()


# ------------------------------------------------------------------ 5. the action's own guard

def _ctx(servers, *, scope=None) -> ActionContext:
    """The seam's context over a stub cluster, as the web shell builds it (duck-typed)."""
    bus = types.SimpleNamespace(servers={server.name: server for server in servers}, nodes={})
    identity = types.SimpleNamespace(roles=(), scope=scope, shown_name="probe", backend="local",
                                     subject="probe")
    return ActionContext.from_web(identity, None, bus)


@pytest.mark.asyncio
async def test_the_action_refuses_a_server_already_in_the_requested_state(clean_scheduler_registry,
                                                                         recording_bot):
    """A typed refusal, never a silent re-write."""
    discover_actions(["scheduler"])
    flagged = FlagServer("SRS-1", maintenance=True)
    result = await call_action("set_maintenance", _ctx([flagged]), server_name="SRS-1")
    assert result.success is False
    assert result.message == 'Server "SRS-1" is already in maintenance mode.'
    assert flagged.maintenance is True
    assert [call[0] for call in recording_bot.calls] == [
        'Server "SRS-1" is already in maintenance mode.']

    plain = FlagServer("SRS-2", maintenance=False)
    result = await call_action("clear_maintenance", _ctx([plain]), server_name="SRS-2")
    assert result.success is False
    assert result.message == 'Server "SRS-2" is not in maintenance mode.'
    assert plain.maintenance is False


@pytest.mark.asyncio
async def test_the_action_refuses_a_server_it_cannot_find(clean_scheduler_registry, recording_bot):
    """Never a 500, never a fake success."""
    discover_actions(["scheduler"])
    result = await call_action("set_maintenance", _ctx([]), server_name="NOPE-9")
    assert result.success is False
    assert result.message == "Server 'NOPE-9' not found."
    assert [call[0] for call in recording_bot.calls] == [
        "Server 'NOPE-9' not found."]


@pytest.mark.asyncio
async def test_the_flag_actions_never_touch_the_process_they_only_write_the_flag(
        clean_scheduler_registry, recording_bot):
    """The action calls NO domain method.

    A flag is a scheduler-side switch; a Start/Stop/Shutdown behind it would be a different concept, so
    the double counts every process call and asserts the list stays empty.
    """
    discover_actions(["scheduler"])
    server = FlagServer("SRS-1")
    ctx = _ctx([server])
    assert (await call_action("set_maintenance", ctx, server_name="SRS-1")).success is True
    assert server.maintenance is True and server.calls == []
    assert (await call_action("clear_maintenance", ctx, server_name="SRS-1")).success is True
    assert server.maintenance is False and server.calls == []


# ------------------------------------------------------------------- 6. no confirmation, CSRF

@pytest.mark.asyncio
async def test_neither_half_confirms_and_both_carry_the_csrf_dependency(
        config_dir, clean_scheduler_registry, recording_bot):
    """A reversible state change does not get a dialog, and every route still carries the session token.

    The fleet has one flagged and one unflagged server, so BOTH halves of the pair are on the same page
    and the direct-submit shape is asserted for each.
    """
    cluster = fleet(flagged={"SRS-1"})
    app = build_with_scheduler(config_dir, cluster)
    browser = {"Accept": "text/html"}
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http)
        for path in PAIR:
            form = re.search(rf'<form method="post" action="{re.escape(path)}"[^>]*>', page)
            assert form is not None, f"the row offered no control for {path}"
            assert "data-async" in form.group(0), \
                f"{path}: a control that confirms nothing submits in the background"
        assert f'action="{MAINTENANCE}{actions_page.CONFIRM_SUFFIX}"' not in page, \
            "no dialog route is rendered for a control that confirms nothing"
        for path in PAIR:
            reply = await http.post(path, data={"server": "SRS-B"}, headers=browser)
            assert reply.status_code == 403, path
            assert "nothing was saved" in reply.text, path
    assert cluster.servers[1].maintenance is False
    assert recording_bot.calls == []


def test_the_pair_is_a_direct_write_with_no_confirm_and_no_field():
    """The declaration itself: ``confirm`` off, no dialog, no field, no danger tint."""
    by_key = {action.key: action for action in actions_page.SERVER_ACTIONS}
    for key in ("maintenance", "end_maintenance"):
        action = by_key[key]
        assert action.confirm is False and action.danger is False, key
        assert action.row == actions_page.ROW_STRIP, key
        assert action.statuses == (), f"{key}: the flag applies in EVERY state"
    assert by_key["maintenance"].when_maintenance is False
    assert by_key["end_maintenance"].when_maintenance is True
    assert by_key["maintenance"].path == MAINTENANCE
    assert by_key["end_maintenance"].path == END_MAINTENANCE
 # the two accessible names and tooltips are DISTINCT (two identical names on one row is the same
 # defect as an unlabelled icon)
    assert by_key["maintenance"].aria != by_key["end_maintenance"].aria
    assert by_key["maintenance"].tip != by_key["end_maintenance"].tip


def test_the_flag_controls_post_the_actions_own_target_parameter():
    """The route sends ``server_name`` — the ACTION's parameter, pinned against its real signature.

    ``_handler`` resolves the posted ``server`` field and calls the action with ``server_name=name``
    (``pages/actions._handler``), so the action must accept exactly that. A rename on either side is a
    refusal or an exception on every press, and this is what turns that into a test failure here.
    """
    import inspect

    import plugins.scheduler.actions as scheduler_actions

    for qualname in ("set_maintenance", "clear_maintenance"):
        action = getattr(scheduler_actions, qualname)
        assert set(inspect.signature(action).parameters) == {"ctx", "server_name"}, qualname


@pytest.mark.asyncio
async def test_a_server_name_with_markup_is_escaped_wherever_it_is_rendered(
        config_dir, clean_scheduler_registry):
    """A server's name is CONFIG data: it must not inject markup into the row or into a control.

    Asserted on the two places the flag pair writes the name out — the button's accessible name (and
    its tooltip, both formatted from the name) and the hidden ``server`` field of the form that posts
    it — plus the row's own title cell. The console escapes at render, so the pin is on the ESCAPED
    form being present and the raw one being absent from the page.
    """
    hostile = FlagServer("<b>SRS-1</b>", "dcs-a-01", ["Host A"])
    node = types.SimpleNamespace(name="dcs-a-01", master=True, instances={})
    cluster = Cluster([hostile], nodes={"dcs-a-01": node}, node=node, instances=[])
    app = build_with_scheduler(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http)
    assert "<b>SRS-1</b>" not in html, "the raw markup must never reach the page"
    assert "&lt;b&gt;SRS-1&lt;/b&gt;" in html, "the name is rendered where the row and its control are"
    assert 'value="&lt;b&gt;SRS-1&lt;/b&gt;"' in html, "the hidden target field is escaped too"


@pytest.mark.asyncio
async def test_the_two_controls_are_named_apart_in_the_rendered_row(config_dir,
                                                                   clean_scheduler_registry):
    """The rendered names, one per control, each naming its own direction and its target.

    One flagged row and one unflagged one share the page, so BOTH accessible names are on it at once
    and can be compared — which is the actually dangerous case (two identical names on one screen).
    """
    app = build_with_scheduler(config_dir, fleet(flagged={"SRS-1"}))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http)
 # the flagged row offers the CLEAR half, and only it
    assert 'aria-label="Take server SRS-1 out of maintenance"' in html
    assert 'aria-label="Put server SRS-1 into maintenance"' not in html
 #... and the unflagged sibling offers the SET half, with its own wording
    assert 'aria-label="Put server SRS-B into maintenance"' in html
    assert 'aria-label="Take server SRS-B out of maintenance"' not in html
    assert 'title="Maintenance — keep this server out of service; the scheduler will not start it"' in html
    assert 'title="End maintenance — let the scheduler start this server again"' in html


# --------------------------------------------------------- 7. no bulk flag control on the node row

def test_no_bulk_flag_control_was_added_to_the_node_row():
    """ONE HOME PER CONCEPT: no node operation writes the maintenance flag.

    Asserted on the DECLARATION rather than a rendering (a rendering would need the ``admin`` plugin):
    what must never exist is a node action writing the maintenance flag. The row's OTHER operations
    are not this pin's subject — the DCS update touches an installation and flags nothing — so the
    list below names the flag-relevant pair and the set of keys that must never grow into a bulk
    toggle, rather than freezing the row's whole membership.
    """
    keys = [action.key for action in actions_page.NODE_ACTIONS]
    # the maintenance pair, in the mockup's order, and nothing beside it writes a flag
    assert [key for key in keys if key in ("offline", "online")] == ["offline", "online"], keys
    qualnames = {action.qualname for action in actions_page.NODE_ACTIONS}
    assert qualnames.isdisjoint({"set_maintenance", "clear_maintenance"}), qualnames
 # the two maintenance capabilities are on the SERVER map and nowhere else
    for capability in (actions_page.SERVER_MAINTENANCE_CAPABILITY,
                       actions_page.SERVER_CLEAR_MAINTENANCE_CAPABILITY):
        assert capability not in {action.capability for action in actions_page.NODE_ACTIONS}


@pytest.mark.asyncio
async def test_the_node_row_renders_no_flag_control(config_dir, clean_scheduler_registry):
    """The rendered half of the pin above, on a real node row with the node plugin installed too.

    The node row's control set is asserted as a WHOLE set, because a bulk flag control would be a
    sixth path: with the scheduler plugin loaded — i.e. in the install where the flag pair EXISTS for
    the server rows — the node row still offers exactly its own controls (the lifecycle trio plus
 *Take servers offline*, which is the half this fleet's RUNNING servers make offerable), and
    neither flag path appears at all.
    """
    app = build(config_dir, fleet(node_class=_NodeDouble),
                plugins=("mission", "scheduler", "admin"))
 # the whole node control set is asserted below, and Upgrade is gated on the CACHED check
 # seed it as a poll that found an update pending, so the trio is whole.
    from services.webservice import upgrade as upgrade_signal
    upgrade_signal.record("dcs-a-01", True)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    node_paths = set(re.findall(r'action="(/actions/node/[^"]+)"', html))
    assert node_paths == {"/actions/node/restart" + actions_page.CONFIRM_SUFFIX,
                          "/actions/node/shutdown" + actions_page.CONFIRM_SUFFIX,
                          "/actions/node/upgrade" + actions_page.CONFIRM_SUFFIX,
                          "/actions/node/offline" + actions_page.CONFIRM_SUFFIX}, node_paths
    assert MAINTENANCE not in html and END_MAINTENANCE not in html


class _NodeDouble(types.SimpleNamespace):
    """``SimpleNamespace`` with the trio's no-op coroutines, so a real node row renders."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls: list[str] = []

    async def restart(self):
        self.calls.append("restart")

    async def shutdown(self, rc=-2):
        self.calls.append("shutdown")

    async def upgrade(self):
        self.calls.append("upgrade")


def test_the_flag_capabilities_are_reachable_only_through_the_scope_for_a_manager():
    """The capability's second way in is the SCOPE, and only for a manager — asserted directly.

    ``allows`` is the ONE predicate the gate and the control both run; this pins the two answers that
    matter for the new capabilities: a role WITHOUT the scope grant is refused, and a manager IS
    admitted on the strength of the scope alone.
    """
    actions_page.declare()
    capability = actions_page.SERVER_MAINTENANCE_CAPABILITY
    assert permissions.allows(capability, (), manager=False) is False
    assert permissions.allows(capability, (), manager=True) is True
    assert permissions.allows(capability, ("DCS Admin",), manager=False) is True
    assert permissions.allows(capability, ("Admin",), manager=False) is True
    assert permissions.allows(capability, ("DCS",), manager=False) is False
