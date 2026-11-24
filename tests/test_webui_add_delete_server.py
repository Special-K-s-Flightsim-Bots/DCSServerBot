"""The SERVER CREATION pair on the website: add a server, delete a server — LIVE.

ONE section per acceptance criterion of the card:

* the DISCOVERY WIRING — ``action_available("add_server")`` / ``action_available("delete_server")``
  must be True through the WEB SHELL's own install path (never through a helper that pre-registers:
  an empty registry renders the row "read-only" while the suite stays green);
* the ADD page control — offered to an Admin who may act, ABSENT for a capability the caller does not
  hold, ABSENT on every other list page, and absent when the action is not registered;
* the ADD dialog — the node picker offers the reachable nodes, the instance picker offers EVERY empty
  instance in the caller's view with the node it is on, and the form carries the node, the instance,
  the name and the "configure" box (its channel pickers are RETIRED: channels are configured on the
  server's own page);
* the ADD write — the form's fields reach the ACTION as its own parameters, a body stating the
  retired channel fields writes none of them, and an unreachable node is the seam's typed refusal;
* the DELETE row control and its dialog — the name must be TYPED, the Saved Games directory is its own
  box, and a POST that skipped the dialog (no one-shot token) or typed the wrong name is refused;
* the bot-side actions — create a new instance and name the server on it, ATTACH a blank server to an
  instance's EXISTING bot port on whichever node carries it (``Node.bind_server``), refuse a node whose
  bot does not answer the new method, and remove a server together with its instance after stopping it.

What these tests do NOT cover is stated in the card's report: no real DCS install and no real cluster
are exercised here — the node is a double, so "the node really creates the directory" and "a remote
node really binds the server" are the operator's own checks.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import httpx
import pytest

from conftest import create_app, install_shell

import core.actions as actions_module
from core import Status
from core.action_results import ActionResult
from core.actions import ActionContext, action_available, action_registry, reset_in_flight
from services.webservice import permissions, readmodels
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import instances as instances_page
from services.webservice.pages import lists as lists_page
from services.webservice.pages import servers as servers_page
from services.webservice.scope import CLUSTER_ROLES, Scope

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"

#: the repository root, derived from this file so the test carries no machine's path.
REPO = Path(__file__).resolve().parent.parent

#: the copy the ADD dialog's three channel pickers owned: three labels, three help lines and the
#: sentence that refused a value that was not a channel id. Retired with the pickers, so each is
#: asserted ABSENT from the rendered dialog and from every shipped catalog (see
#: :func:`test_the_retired_channel_copy_is_gone_from_every_catalog`).
RETIRED_CHANNEL_MSGIDS = (
    "Status channel id",
    "Chat channel id",
    "Admin channel id",
    "The Discord channel the bot reports this server's status in. Optional.",
    "Where the server's in-game chat is mirrored. Optional.",
    "This server's own admin channel, for an install whose bot has no central one. Optional.",
    'A channel id must be a positive number, or -1 for "no channel".',
)
#: the three form fields themselves, by the name the dialog would post them under.
RETIRED_CHANNEL_FIELDS = ('name="status"', 'name="chat"', 'name="admin"')

#: every role name an account declares must be a KNOWN role: the local backend validates the declared
#: roles at startup and refuses the whole install on an unknown one.
BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
  DCS:
    - hoster
"""

USERS = (
    {"username": "specialk", "roles": ["Admin"]},                       # unscoped by rule
    {"username": "mod1", "roles": ["DCS Admin"], "scope": ["Host A"]},  # a read/write role
    {"username": "hoster", "roles": ["DCS"], "scope": ["Host A"]},      # a pure MANAGER
)


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(users=None) -> dict:
    return {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True,
                           "users": users if users is not None
                           else [dict(user, password_hash=_hash()) for user in USERS]}},
    }


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def clean_admin_registry():
    """The ``@action`` registry and the in-flight guard are PROCESS state — restore both.

    Cleared BEFORE the test so the discovery assertion cannot pass on a registry an earlier test
    populated: the shell's own install has to have done the work. ``plugins.admin.actions`` is dropped
    from the MODULE CACHE as well, and that is what makes the pin honest — the decorators run ON
    IMPORT, which happens once per process, so without this only the FIRST test would see a genuine
    discovery and every later one would observe an empty registry while staying green.
    """
    import sys

    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    reset_in_flight()
    sys.modules.pop("plugins.admin.actions", None)
    try:
        yield actions_module._ACTIONS
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        reset_in_flight()
        sys.modules.pop("plugins.admin.actions", None)


# ---------------------------------------------------------------------------------- test doubles

def instance(name, server_obj=None, *, node_name="dcs-a-01", dcs_port=10308, bot_port=6666,
             home=None):
    """An ``Instance`` double where the picker, the dialog and the actions read it.

    ``bot_port`` is an ATTRIBUTE (a ``Port`` in production, a plain number here — both print as the
    number), because that is what ``add_instance`` and ``migrate_server`` pass to the server object.
    """
    return types.SimpleNamespace(
        name=name, server=server_obj, node=types.SimpleNamespace(name=node_name),
        bot_port=bot_port,
        locals={"dcs_port": dcs_port, "bot_port": bot_port},
        home=home or f"/saved/{name}", is_remote=False)


def server(name, *, node_name="dcs-a-01", instance_name="Host-A", instance_obj=None,
           status=Status.SHUTDOWN, managed_by=None, maintenance=False, node=None):
    """A ``Server`` double shaped like ``ServerImpl`` where the read models and the actions read it."""
    srv = types.SimpleNamespace(
        name=name, display_name=name, status=status,
        node=node or types.SimpleNamespace(name=node_name, master=True),
        instance=instance_obj or instance(instance_name, None, node_name=node_name),
        port=10309, players={}, current_mission=None, maintenance=maintenance,
        num_players=0, is_remote=False, settings={}, stopped=0,
        locals={"managed_by": list(managed_by)} if managed_by else {})
    srv.instance.server = srv

    async def _shutdown(force=False):
        srv.stopped += 1
        srv.status = Status.SHUTDOWN

    srv.shutdown = _shutdown
    return srv


class Cluster:
    """A stub cluster behind the app's provider seam (``app.state.webui_source_provider``)."""

    def __init__(self, servers=(), *, nodes=None, node=None, instances=()):
        self.servers = list(servers)
        self.instances = list(instances)
        self.nodes = dict(nodes or {})
        self.node = node

    def source(self):
        return readmodels.LiveSource(servers=tuple(self.servers), instances=tuple(self.instances),
                                     nodes=self.nodes, node=self.node, log_path=None)


def cluster_with(*, taken=False, empty_above=True, other_node_empty=True):
    """A one-node cluster: one bound server, one empty instance, and (optionally) another node's."""
    bound = server("SRS-1", instance_name="Host-A")
    free = instance("Host-B", None)
    instances = [bound.instance, free]
    servers = [bound]
    if taken:
        taken_server = server("SRS-2", instance_name="Host-C")
        instances.append(taken_server.instance)
        servers.append(taken_server)
    if other_node_empty:
        remote_empty = instance("Host-D", None, node_name="dcs-b-01")
        remote_bound = server("SRS-B", node_name="dcs-b-01", instance_name="Host-E")
        instances.append(remote_empty)
        instances.append(remote_bound.instance)
        servers.append(remote_bound)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={i.name: i for i in instances
                                              if i.node.name == "dcs-a-01"})
    node_b = types.SimpleNamespace(name="dcs-b-01", master=False,
                                   instances={i.name: i for i in instances
                                              if i.node.name == "dcs-b-01"})
    return Cluster(servers, nodes={"dcs-a-01": node_a, "dcs-b-01": node_b},
                   node=node_a, instances=instances)


class RecordingBot:
    """A bot whose ``audit`` records what it was asked to write — the audit's domain boundary."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def audit(self, message, **kwargs):
        self.calls.append((message, kwargs))


@pytest.fixture
def recording_bot():
    """Install a bot that CAN confirm an audit entry, for the process's whole request handling."""
    from core.services.registry import ServiceRegistry
    from services.bot.service import BotService

    bot = RecordingBot()
    saved = ServiceRegistry._singletons.get(BotService)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=bot)
    try:
        yield bot
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


@pytest.fixture
def fake_service_bus():
    """A ``ServiceBus`` singleton holding a ``servers`` mapping — the registry the bind writes to."""
    from core.services.registry import ServiceRegistry
    from services.servicebus import ServiceBus

    bus = types.SimpleNamespace(servers={})
    saved = ServiceRegistry._singletons.get(ServiceBus)
    ServiceRegistry._singletons[ServiceBus] = bus
    try:
        yield bus
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(ServiceBus, None)
        else:
            ServiceRegistry._singletons[ServiceBus] = saved


# ---------------------------------------------------------------------------------- the harness

def build(config_dir, cluster, *, plugins=("admin",)):
    """A real application: the production factory, the shell, the local backend and a stub source."""
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir),
                                                  plugins=list(plugins)),
                  config=_config())
    app.state.webui_source_provider = cluster.source
    return app


def client(app):
    """The suite's ASGI client; the 303 is NOT followed, so a test can assert it and re-read."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="https://testserver")


async def sign_in(http, username="specialk"):
    response = await http.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', response.text).group(1)
    reply = await http.post(auth_routes.LOGIN_PATH,
                            data={"username": username, "password": PASSWORD,
                                  "_csrf_token": token})
    assert reply.status_code in (200, 303), f"login for {username} failed: {reply.status_code}"
    return reply


def token_of(html: str) -> str:
    """The session's CSRF token, read out of a RENDERED form (never minted by the test)."""
    match = re.search(r'name="_csrf_token" value="([^"]*)"', html)
    assert match, "the page rendered no CSRF field"
    return match.group(1)


async def fleet(http) -> str:
    response = await http.get(servers_page.SERVERS_PATH)
    assert response.status_code == 200, f"{servers_page.SERVERS_PATH} answered {response.status_code}"
    return response.text


async def open_add_dialog(http, origin=None):
    """POST the page control and return the dialog's HTML — the way a browser reaches it."""
    origin = origin or servers_page.SERVERS_PATH
    html = await fleet(http)
    assert f'action="{actions_page.SERVER_ADD_PATH}/confirm"' in html, "no Add control on the page"
    response = await http.post(f"{actions_page.SERVER_ADD_PATH}/confirm",
                               data={actions_page.ORIGIN_FIELD: origin,
                                     "_csrf_token": token_of(html)})
    assert response.status_code == 200, f"the Add dialog answered {response.status_code}"
    return response.text


async def open_delete_dialog(http, name):
    """POST the row's Delete control and return the dialog's HTML."""
    html = await fleet(http)
    assert f'action="{actions_page.SERVER_DELETE_PATH}/confirm"' in html, "no Delete control"
    response = await http.post(f"{actions_page.SERVER_DELETE_PATH}/confirm",
                               data={"server": name, actions_page.ORIGIN_FIELD: servers_page.SERVERS_PATH,
                                     "_csrf_token": token_of(html)})
    assert response.status_code == 200, f"the Delete dialog answered {response.status_code}"
    return response.text


def recording_action(monkeypatch, qualname: str, result: ActionResult | None = None) -> list[dict]:
    """Replace ONE action with a recorder, and return the list of parameter dicts it was called with.

    The ROUTE is what is under test here: which parameters it hands the action, and which requests it
    refuses before any action runs. The real action's own behaviour is covered by the class below.
    """
    calls: list[dict] = []

    async def fake(ctx, **params):
        calls.append(params)
        return result or ActionResult(success=True, message="recorded",
                                      data={"audit": "recorded"})

    monkeypatch.setitem(actions_module._ACTIONS, qualname, fake)
    return calls


# ------------------------------------------------------------------ 1. the discovery wiring

def test_both_actions_are_discovered_through_the_shell(config_dir, clean_admin_registry):
    """``action_available`` answers True for BOTH actions after the SHELL discovered the plugins.

    The shell's own install path is what must populate the registry (``shell._discover_actions``), not
    a helper that pre-imports the module: an installation whose registry stays empty renders the Add
    control nowhere and refuses every POST, while the rest of this file would still pass.
    """
    build(config_dir, cluster_with())
    assert action_available("add_server") is True
    assert action_available("delete_server") is True


def test_the_plugin_declares_only_the_pair(config_dir, clean_admin_registry):
    """The two functions are ``@action``-decorated where every transport can read them."""
    import plugins.admin.actions as admin_actions

    assert admin_actions.add_server.__qualname__ == "add_server"
    assert admin_actions.delete_server.__qualname__ == "delete_server"


# ------------------------------------------------------------------ 2. the capabilities

def test_both_capabilities_are_admin_only_without_a_scope_grant(config_dir, clean_admin_registry):
    """``servers.add`` / ``servers.delete`` carry the NODE rule: Admin, and no managing scope.

    A manager's scope is a set of SERVER names: it cannot name a server that does not exist yet, and
    it must not become a licence to remove a machine's instance.
    """
    build(config_dir, cluster_with())
    for capability in ("servers.add", "servers.delete"):
        assert permissions.CAPABILITIES[capability] == ("Admin",)
        assert permissions.SCOPE_GRANTS[capability] is False


def test_a_dcs_admin_holds_neither_capability(config_dir, clean_admin_registry):
    """The two are NOT part of the ``WRITE_ROLES`` family: a DCS Admin is refused."""
    roles = {"DCS Admin"}
    assert permissions.allows("servers.add", roles, manager=False) is False
    assert permissions.allows("servers.delete", roles, manager=False) is False
    assert permissions.allows("servers.add", frozenset({"Admin"}), manager=False) is True


# ------------------------------------------------------------------ 3. the Add page control

@pytest.mark.asyncio
async def test_the_fleet_page_offers_the_add_control(config_dir, clean_admin_registry):
    """A signed-in Admin's Servers page carries the control, with the session's own CSRF token."""
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        html = await fleet(http)
    assert f'action="{actions_page.SERVER_ADD_PATH}/confirm"' in html
    assert f'name="{actions_page.ORIGIN_FIELD}" value="{servers_page.SERVERS_PATH}"' in html
    assert actions_page.SERVER_ADD_PATH not in instances_page.INSTANCES_PATH


@pytest.mark.asyncio
async def test_a_dcs_admin_is_offered_no_add_control(config_dir, clean_admin_registry):
    """``offered ⊆ authorised``: the control is ABSENT (never rendered disabled) for a DCS Admin."""
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await fleet(http)
    assert f'action="{actions_page.SERVER_ADD_PATH}/confirm"' not in html


@pytest.mark.asyncio
async def test_other_list_pages_carry_no_add_control(config_dir, clean_admin_registry):
    """The control belongs to the FLEET: the Instances page renders none of it."""
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        response = await http.get(instances_page.INSTANCES_PATH)
    assert response.status_code == 200
    assert f'action="{actions_page.SERVER_ADD_PATH}/confirm"' not in response.text


def test_page_controls_refuses_every_table_but_the_fleet(config_dir, clean_admin_registry):
    """The page asks BY TABLE: the control is offered for ``servers`` and for nothing else.

    ``page_controls`` answers before it reads any request state, so a table that has no page control
    cannot be handed one by a request that carries the fleet's own data.
    """
    assert actions_page.page_controls(types.SimpleNamespace(), "instances",
                                      instances_page.INSTANCES_PATH) is None
    assert actions_page.page_controls(types.SimpleNamespace(), "players", "/players") is None
    assert actions_page.SERVER_PAGE_ACTIONS[0].capability == "servers.add"
    assert actions_page.SERVER_PAGE_ACTIONS[0].path == actions_page.SERVER_ADD_PATH


# ------------------------------------------------ 3b. the control lives in the Servers CARD HEADER

#: the card header band as rendered: `<div class="hd">` up to the `</div>` that closes it (the band
#: nests a spacer, so a non-greedy match would stop one element early).
HEADER_OPEN = re.compile(r'<div class="hd">')


def card_header(html: str) -> str:
    """The first table card's header band, read by walking the divs it nests."""
    match = HEADER_OPEN.search(html)
    assert match, "the page rendered no card header"
    body = html[match.end():]
    depth = 1
    for tag in re.finditer(r"<(/?)div\b[^>]*>", body):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return body[:tag.start()]
    raise AssertionError("the card header is not closed")


async def dashboard(http, query: str = "") -> str:
    response = await http.get(f"{dashboard_page.DASHBOARD_PATH}{query}")
    assert response.status_code == 200, \
        f"{dashboard_page.DASHBOARD_PATH}{query} answered {response.status_code}"
    return response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ("", "?tab=servers", "?tab=instances", "?tab=players",
                                   "?tab=nodes"))
async def test_the_dashboard_carries_no_add_control_on_any_tab(config_dir, clean_admin_registry,
                                                              query):
    """Creating a server is the Servers page's action: the dashboard renders no page control.

    An Admin on the SAME app is the positive control — the Servers page carries the control — so the
    absence here is a decision about the page and not a control that failed to render anywhere.
    """
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        html = await dashboard(http, query)
        carrying = await fleet(http)
    assert f'action="{actions_page.SERVER_ADD_PATH}/confirm"' not in html
    assert '<div class="page-actions">' not in html, "the page head still draws an action band"
    assert f'action="{actions_page.SERVER_ADD_PATH}/confirm"' in carrying, \
        "the instrument sees no control on any page, so the absence above proves nothing"


@pytest.mark.asyncio
async def test_the_servers_page_draws_the_add_control_in_its_table_card_header(
        config_dir, clean_admin_registry):
    """The control sits in the TABLE's card header, as the accent button, on the fleet's own origin."""
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        html = await fleet(http)

    header = card_header(html)
    assert f'action="{actions_page.SERVER_ADD_PATH}/confirm"' in header, \
        "the page-level control is not in the card header"
    assert 'class="btn primary"' in header, "the control is not the shared accent button"
    assert f'name="{actions_page.ORIGIN_FIELD}" value="{servers_page.SERVERS_PATH}"' in header
    assert 'name="_csrf_token"' in header, "the control carries no session token"
    # the card header, not the page head: the action band is gone from both pages
    assert '<div class="page-actions">' not in html
    # the control comes BEFORE the table it acts on
    assert html.index(actions_page.SERVER_ADD_PATH) < html.index("<table")


@pytest.mark.asyncio
async def test_a_dcs_admin_is_offered_no_add_control_on_the_servers_page(config_dir,
                                                                         clean_admin_registry):
    """``offered ⊆ authorised``: the card header carries no control for a DCS Admin.

    An Admin on the SAME app is the positive control, so the absence is the capability decision and
    not a page that failed to render its card.
    """
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await fleet(http)
    async with client(app) as admin_http:
        await sign_in(admin_http)
        assert f'action="{actions_page.SERVER_ADD_PATH}/confirm"' in (await fleet(admin_http)), \
            "the Admin sees no control either, so the DCS Admin's absence proves nothing"
    assert f'action="{actions_page.SERVER_ADD_PATH}/confirm"' not in html


@pytest.mark.asyncio
async def test_a_write_started_from_the_servers_page_returns_to_the_servers_page(
        config_dir, clean_admin_registry, monkeypatch):
    """The origin the card control carries is one the WRITE honours: the 303 lands on the fleet page.

    ``origin_path`` is an allow-list over the registrar's own page paths, so an origin merely STATED
    proves nothing. This drives the control's own block to the redirect and reads the Location.
    """
    app = build(config_dir, cluster_with())
    recording_action(monkeypatch, "add_server")
    async with client(app) as http:
        await sign_in(http)
        control = card_header(await fleet(http))
        match = re.search(r'<form method="post" action="([^"]+)"', control)
        assert match, "the card header names no control form"
        path = match.group(1)
        token = re.search(r'name="_csrf_token" value="([^"]+)"', control)
        assert token, "the control carries no session token"
        dialog = await http.post(path, data={actions_page.ORIGIN_FIELD: servers_page.SERVERS_PATH,
                                             "_csrf_token": token.group(1)})
        assert dialog.status_code == 200, f"the Add dialog answered {dialog.status_code}"
        assert f'name="{actions_page.ORIGIN_FIELD}" value="{servers_page.SERVERS_PATH}"' \
               in dialog.text
        reply = await http.post(actions_page.SERVER_ADD_PATH,
                                data={actions_page.ORIGIN_FIELD: servers_page.SERVERS_PATH,
                                      "_csrf_token": token_of(dialog.text),
                                      actions_page.ADD_NODE_FIELD: "dcs-a-01",
                                      actions_page.ADD_INSTANCE_FIELD: "dcs-a-01/Host-B",
                                      actions_page.ADD_NAME_FIELD: "SRS-9"})
    assert reply.status_code == 303, f"the add write answered {reply.status_code}"
    assert reply.headers["location"] == servers_page.SERVERS_PATH


# ------------------------------------------------------------------ 4. the Add dialog

@pytest.mark.asyncio
async def test_the_add_dialog_offers_the_nodes_and_the_empty_instances(config_dir, clean_admin_registry):
    """The instance picker offers EVERY empty instance in the caller's view, its node stated with it."""
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        html = await open_add_dialog(http)
    for needle in ('name="node"', 'name="instance"', 'name="name"', 'name="configure"'):
        assert needle in html, f"the Add dialog is missing {needle}"
    assert "Host-B" in html                       # the empty instance on this node
    assert "Host-D" in html                       # empty, and on ANOTHER node
    assert "Host-A" not in html                   # carries a server already
    # the option VALUE states the OWNING node with the instance, so an instance on another machine is
    # bound on that machine rather than on the one the dialog runs in
    assert 'value="dcs-a-01/Host-B"' in html
    assert 'value="dcs-b-01/Host-D"' in html
    assert "<option value=\"\"" in html            # "create a new instance" is the first option
    assert "dcs-a-01" in html and "dcs-b-01" in html
    # THE FORM ASSOCIATION: the dialog's fields live in the BODY, outside the posting form, so each
    # one must name that form — a text field without it is silently NOT posted by a browser (the
    # ADD form's node, instance, name and configure fields are exactly that shape).
    for field_name in (actions_page.ADD_NODE_FIELD, actions_page.ADD_INSTANCE_FIELD,
                       actions_page.ADD_NAME_FIELD, actions_page.ADD_CONFIGURE_FIELD):
        assert re.search(rf'name="{field_name}"[^>]*form="dlg-form"', html), \
            f"the Add dialog's {field_name} field is not associated with the form"


@pytest.mark.asyncio
async def test_the_add_dialog_offers_no_channel_picker(config_dir, clean_admin_registry):
    """The Add dialog asks for the node, the instance, the name and the configure box — nothing else.

    Channels are configured on the server's own page (its Configuration tab), not while a server is
    created, so the dialog carries no channel field at all — and the admin-channel question, which
    asked about this server's own channel whether or not the bot has a central one, is gone with it.
    Both halves are asserted: the three FORM FIELDS by name, and the copy they carried (three labels,
    three help lines), so a picker that comes back is loud instead of silently resurrected.
    """
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        html = await open_add_dialog(http)
    for needle in RETIRED_CHANNEL_FIELDS:
        assert needle not in html, f"the Add dialog still offers {needle}"
    for copy in RETIRED_CHANNEL_MSGIDS[:6]:
        assert copy not in html, f"the Add dialog still carries the retired channel copy: {copy!r}"
    for field_name in (actions_page.ADD_NODE_FIELD, actions_page.ADD_INSTANCE_FIELD,
                       actions_page.ADD_NAME_FIELD, actions_page.ADD_CONFIGURE_FIELD):
        assert f'name="{field_name}"' in html, f"the Add dialog lost its {field_name} field"


def test_the_retired_channel_copy_is_gone_from_every_catalog():
    """Every catalog is in step with the dialog that no longer wraps that copy.

    The catalogs are a retired string's second home: an entry nothing wraps is the dead knob the
    console's own rules forbid, and it also keeps the ``de``/``es`` catalogs out of step with what
    Babel extracts (``test_webui_i18n`` compares the two sets). English ships no catalog — its strings
    are the msgids — so it is not a home for a retired string either. Read with Babel, so the read
    skips ``#~`` obsolete blocks — what is asserted is the LIVE entry list.
    """
    from babel.messages import pofile

    for language in ("cn", "de", "es", "ru"):
        po = REPO / "locale" / language / "LC_MESSAGES" / "webui.po"
        with po.open("rb") as handle:
            catalog = pofile.read_po(handle)
        live = {message.id for message in catalog
                if isinstance(message.id, str) and message.id
                and not getattr(message, "obsolete", False)}
        assert live, f"{po} carried no live msgids"
        survivors = sorted(set(RETIRED_CHANNEL_MSGIDS) & live)
        assert not survivors, f"{language} still carries the retired channel copy: {survivors}"


@pytest.mark.asyncio
async def test_the_picker_offers_a_node_less_instance_under_its_bare_name(config_dir,
                                                                          clean_admin_registry):
    """An instance whose OWN node cannot be read is offered under its bare name — never ``/name``."""
    naked = instance("Host-Z", None, node_name="")
    cluster = Cluster([], nodes={"dcs-a-01": types.SimpleNamespace(name="dcs-a-01", master=True,
                                                                   instances={})},
                      node=types.SimpleNamespace(name="dcs-a-01", master=True), instances=[naked])
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http)
        html = await open_add_dialog(http)
    assert 'value="Host-Z"' in html
    assert 'value="/Host-Z"' not in html


@pytest.mark.asyncio
async def test_the_add_dialog_marks_the_configure_box_as_ticked(config_dir, clean_admin_registry):
    """The dialog's option box EMITS its default tick, and the OFF companion still precedes it.

    ``NodeOption(default=True)`` is drawn by ``_option_fields`` and rendered by ``confirm.html``'s
    ``field_input``; a chain is only as good as its last link, so this pins the RENDERED box — a
    ``checked`` attribute on the checkbox the dialog body carries — instead of the declaration the
    page module holds. A browser paints the tick from that attribute alone: rendered in Chrome, the
    same page WITHOUT it changes pixels only inside the 15x15 box (accent-filled mark -> empty
    outline).
    """
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        html = await open_add_dialog(http)
    assert re.search(rf'name="{actions_page.ADD_CONFIGURE_FIELD}"[^>]*\bchecked\b', html), \
        "the Add dialog's configure box is not ticked in the emitted markup"
    # the OFF companion is still FIRST: a ticked checkbox sends no field of its own, and the route
    # resolves a repeated field to the LAST value — the other order makes a ticked box save OFF
    assert html.index(f'name="{actions_page.ADD_CONFIGURE_FIELD}" value="0"') < \
        html.index(f'name="{actions_page.ADD_CONFIGURE_FIELD}" value="1"')


@pytest.mark.asyncio
async def test_the_add_dialog_keeps_the_origin(config_dir, clean_admin_registry):
    """The page it was opened from travels through the dialog, so Cancel/Esc and the write return there."""
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        html = await open_add_dialog(http)
        assert f'name="{actions_page.ORIGIN_FIELD}" value="{servers_page.SERVERS_PATH}"' in html


# ------------------------------------------------------------------ 5. the Add write

@pytest.mark.asyncio
async def test_the_add_form_reaches_the_action_with_its_own_parameters(
        config_dir, clean_admin_registry, monkeypatch):
    """The dialog's four fields ARE the action's parameters — one name each, and nothing besides.

    The POST also STATES the three channel fields the dialog no longer offers: the route reads only
    the fields the dialog declares, so they cannot reach the action — and therefore cannot be written
    onto the new server by a crafted body.
    """
    app = build(config_dir, cluster_with())
    calls = recording_action(monkeypatch, "add_server")
    async with client(app) as http:
        await sign_in(http)
        html = await open_add_dialog(http)
        response = await http.post(actions_page.SERVER_ADD_PATH,
                                   data={actions_page.ORIGIN_FIELD: servers_page.SERVERS_PATH,
                                         "_csrf_token": token_of(html),
                                         actions_page.ADD_NODE_FIELD: "dcs-a-01",
                                         actions_page.ADD_INSTANCE_FIELD: "dcs-b-01/Host-D",
                                         actions_page.ADD_NAME_FIELD: "SRS-2",
                                         actions_page.ADD_CONFIGURE_FIELD: "1",
                                         "status": "123", "admin": "-1"})
    assert response.status_code == 303
    assert len(calls) == 1
    # the picker's value travels VERBATIM (`<node>/<instance>`): the action, not the route, resolves
    # the owning node from it. And the EXACT parameter set: a stated channel field is not read.
    assert calls[0] == {"node_name": "dcs-a-01", "name": "SRS-2", "instance": "dcs-b-01/Host-D",
                        "configure": True}


@pytest.mark.asyncio
async def test_a_post_stating_the_removed_channel_fields_writes_none_of_them(
        config_dir, clean_admin_registry, monkeypatch):
    """A body stating the retired channel fields is not a channel write, and is no longer refused.

    The console's answer to a body that states a field the dialog does not declare is to NOT READ it
    (the rule every route here follows for an unknown field), so a valid id, the reserved -1 and text
    all reach the action as no parameter at all. The 400 and its sentence — "A channel id must be a
    positive number..." — guarded a field the dialog still offered; it is retired with the fields, so
    a value that is not an id is neither written nor refused.
    """
    app = build(config_dir, cluster_with())
    calls = recording_action(monkeypatch, "add_server")
    async with client(app) as http:
        await sign_in(http)
        html = await open_add_dialog(http)
        response = await http.post(actions_page.SERVER_ADD_PATH,
                                   data={actions_page.ORIGIN_FIELD: servers_page.SERVERS_PATH,
                                         "_csrf_token": token_of(html),
                                         actions_page.ADD_NODE_FIELD: "dcs-a-01",
                                         actions_page.ADD_NAME_FIELD: "SRS-2",
                                         "status": "123", "chat": "-1",
                                         "admin": "not-a-channel"})
        assert response.status_code == 303, \
            "a channel field the dialog no longer declares refused the write"
        page = await http.get(servers_page.SERVERS_PATH)
    assert calls == [{"node_name": "dcs-a-01", "name": "SRS-2", "instance": "",
                      "configure": False}]
    assert RETIRED_CHANNEL_MSGIDS[6] not in page.text


@pytest.mark.asyncio
async def test_an_unknown_node_is_the_seams_refusal(config_dir, clean_admin_registry,
                                                    monkeypatch):
    """A node the cluster cannot reach answers the seam's own sentence, and nothing runs."""
    app = build(config_dir, cluster_with())
    calls = recording_action(monkeypatch, "add_server")
    async with client(app) as http:
        await sign_in(http)
        html = await open_add_dialog(http)
        response = await http.post(actions_page.SERVER_ADD_PATH,
                                   data={actions_page.ORIGIN_FIELD: servers_page.SERVERS_PATH,
                                         "_csrf_token": token_of(html),
                                         actions_page.ADD_NODE_FIELD: "dcs-z-99",
                                         actions_page.ADD_NAME_FIELD: "SRS-2"})
        assert response.status_code == 303
        page = await http.get(servers_page.SERVERS_PATH)
    assert calls == []
    assert "offline or unknown" in page.text


@pytest.mark.asyncio
async def test_the_add_needs_the_sessions_csrf_token(config_dir, clean_admin_registry):
    """No token, no write: the session dependency refuses it before the handler runs."""
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        response = await http.post(actions_page.SERVER_ADD_PATH,
                                   data={actions_page.ADD_NODE_FIELD: "dcs-a-01",
                                         actions_page.ADD_NAME_FIELD: "SRS-2"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_node_outside_the_callers_view_is_the_bare_403(config_dir, clean_admin_registry,
                                                               monkeypatch):
    """The refusal for a target outside the caller's view is the console's OWN 403 — the same answer
    a name that does not exist gets, so the two cannot be told apart (no enumeration).

    The capability is NARROWED for this test to a role pair a restricted caller can hold
    (``DCS Admin`` with a scope), because the module declares it Admin-only and an Admin is
    unrestricted by rule — the branch exists for a wider declaration, and the declarations are process
    state the conftest restores.
    """
    app = build(config_dir, cluster_with())
    monkeypatch.setitem(permissions.CAPABILITIES, "servers.add", ("DCS Admin",))
    monkeypatch.setitem(permissions.SCOPE_GRANTS, "servers.add", True)
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await fleet(http)
        assert f'action="{actions_page.SERVER_ADD_PATH}/confirm"' in html
        response = await http.post(f"{actions_page.SERVER_ADD_PATH}/confirm",
                                   data={actions_page.ORIGIN_FIELD: servers_page.SERVERS_PATH,
                                         "_csrf_token": token_of(html)})
        assert response.status_code == 200
        response = await http.post(actions_page.SERVER_ADD_PATH,
                                   data={actions_page.ORIGIN_FIELD: servers_page.SERVERS_PATH,
                                         "_csrf_token": token_of(response.text),
                                         actions_page.ADD_NODE_FIELD: "dcs-b-01",
                                         actions_page.ADD_NAME_FIELD: "SRS-2"})
    assert response.status_code == 403
    assert "Not authorized" in response.text


# ------------------------------------------------------------------ 6. the Delete row control

@pytest.mark.asyncio
async def test_the_server_row_offers_delete_to_an_admin(config_dir, clean_admin_registry):
    """The row's control posts to its DIALOG, and its glyph is the trashcan (a key, not a word)."""
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        html = await fleet(http)
    assert f'action="{actions_page.SERVER_DELETE_PATH}/confirm"' in html
    assert f'name="server" value="SRS-1"' in html


@pytest.mark.asyncio
async def test_a_dcs_admin_is_offered_no_delete_control(config_dir, clean_admin_registry):
    """Admin-only with no scope grant: a DCS Admin sees the row without the control."""
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await fleet(http)
    assert f'action="{actions_page.SERVER_DELETE_PATH}/confirm"' not in html


def test_every_offered_control_has_a_glyph(config_dir, clean_admin_registry):
    """A control whose glyph key is missing renders an EMPTY button — pin the key exists.

    ``_icons.html``'s ``act`` macro renders ``GLYPHS.get(name, '')``, so a wrong key is invisible
    rather than an error: the pin is the only thing that can catch it.
    """
    from pathlib import Path

    icons = (Path(__file__).resolve().parent.parent
             / "services/webservice/templates/_icons.html").read_text(encoding="utf-8")
    for action in actions_page.server_row_actions():
        assert f"'{action.key}':" in icons, f"no glyph for the '{action.key}' control"


# ------------------------------------------------------------------ 7. the Delete dialog and write

@pytest.mark.asyncio
async def test_the_delete_dialog_asks_for_the_typed_name_and_the_directory(config_dir,
                                                                           clean_admin_registry):
    """The two things a removal earns: the name typed, and the directory as its own choice OFF."""
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        html = await open_delete_dialog(http, "SRS-1")
    assert re.search(rf'name="{actions_page.CONFIRM_NAME_FIELD}"[^>]*form="dlg-form"', html), \
        "the typed-name field is not associated with the dialog's form"
    assert f'name="{actions_page.REMOVE_FILES_FIELD}"' in html
    assert re.search(rf'name="{actions_page.REMOVE_FILES_FIELD}" value="0"', html)
    assert 'value="SRS-1"' in html
    assert f'name="{actions_page.CONFIRM_FIELD}" value="' in html       # the one-shot token
    assert "cannot be undone" in html


@pytest.mark.asyncio
async def test_the_delete_refuses_a_post_that_skipped_its_dialog(config_dir, clean_admin_registry,
                                                                 monkeypatch):
    """A confirmation curl can bypass is decoration: no token, no removal."""
    app = build(config_dir, cluster_with())
    calls = recording_action(monkeypatch, "delete_server")
    async with client(app) as http:
        await sign_in(http)
        html = await fleet(http)
        response = await http.post(actions_page.SERVER_DELETE_PATH,
                                   data={"server": "SRS-1",
                                         actions_page.CONFIRM_NAME_FIELD: "SRS-1",
                                         "_csrf_token": token_of(html)})
    assert response.status_code == 403
    assert calls == []


@pytest.mark.asyncio
async def test_the_delete_refuses_a_name_that_does_not_match(config_dir, clean_admin_registry,
                                                             monkeypatch):
    """A typed name that is not the target's is refused BEFORE anything is read or stopped."""
    app = build(config_dir, cluster_with())
    calls = recording_action(monkeypatch, "delete_server")
    async with client(app) as http:
        await sign_in(http)
        dialog = await open_delete_dialog(http, "SRS-1")
        token = re.search(rf'name="{actions_page.CONFIRM_FIELD}" value="([^"]*)"', dialog).group(1)
        response = await http.post(actions_page.SERVER_DELETE_PATH,
                                   data={"server": "SRS-1",
                                         actions_page.CONFIRM_NAME_FIELD: "SRS-2",
                                         actions_page.CONFIRM_FIELD: token,
                                         "_csrf_token": token_of(dialog)})
        assert response.status_code == 400
        page = await http.get(servers_page.SERVERS_PATH)
    assert calls == []
    assert actions_page.REFUSAL_NAME_MISMATCH in page.text


@pytest.mark.asyncio
async def test_the_delete_write_passes_the_target_and_the_directory_choice(config_dir,
                                                                           clean_admin_registry,
                                                                           monkeypatch):
    """The happy path: the token is spent, the resolved name rides the body, the box is parsed."""
    app = build(config_dir, cluster_with())
    calls = recording_action(monkeypatch, "delete_server")
    async with client(app) as http:
        await sign_in(http)
        dialog = await open_delete_dialog(http, "SRS-1")
        token = re.search(rf'name="{actions_page.CONFIRM_FIELD}" value="([^"]*)"', dialog).group(1)
        response = await http.post(actions_page.SERVER_DELETE_PATH,
                                   data={"server": "SRS-1",
                                         actions_page.CONFIRM_NAME_FIELD: "srs-1",
                                         actions_page.CONFIRM_FIELD: token,
                                         actions_page.REMOVE_FILES_FIELD: "1",
                                         "_csrf_token": token_of(dialog)})
        assert response.status_code == 303
        page = await http.get(servers_page.SERVERS_PATH)
    assert calls == [{"server_name": "SRS-1", "remove_files": True}]
    assert "recorded" in page.text


@pytest.mark.asyncio
async def test_the_delete_token_is_spent_by_the_first_post(config_dir, clean_admin_registry,
                                                           monkeypatch):
    """One-shot: a replayed POST is refused rather than repeating a removal."""
    app = build(config_dir, cluster_with())
    calls = recording_action(monkeypatch, "delete_server")
    async with client(app) as http:
        await sign_in(http)
        dialog = await open_delete_dialog(http, "SRS-1")
        token = re.search(rf'name="{actions_page.CONFIRM_FIELD}" value="([^"]*)"', dialog).group(1)
        body = {"server": "SRS-1", actions_page.CONFIRM_NAME_FIELD: "SRS-1",
                actions_page.CONFIRM_FIELD: token, "_csrf_token": token_of(dialog)}
        first = await http.post(actions_page.SERVER_DELETE_PATH, data=body)
        second = await http.post(actions_page.SERVER_DELETE_PATH, data=body)
    assert first.status_code == 303
    assert second.status_code == 403
    assert len(calls) == 1


# ------------------------------------------------------------------ 8. the bot-side ADD action

def _context(node, servers=None, nodes=None):
    """An ``ActionContext`` whose node view is ``node`` (plus *nodes*) and whose registry holds
    ``servers``."""
    mapping = {node.name: node}
    mapping.update(nodes or {})
    bus = types.SimpleNamespace(servers=dict(servers or {}), nodes=mapping)
    return ActionContext(node=node, bus=bus)


class FakeNode:
    """A node double: what ``add_server`` / ``delete_server`` call, and what they were asked."""

    def __init__(self, name="dcs-a-01", *, master=True, instances=None):
        self.name = name
        self.master = master
        self.instances = dict(instances or {})
        self.added: list[str] = []
        self.deleted: list[tuple[str, bool]] = []
        self.bound: list[tuple[str, str, dict]] = []
        #: what ``bind_server`` raises instead of binding — a node's own refusal, or the RPC layer's
        #: answer to a method this node's bot does not have
        self.bind_error: Exception | None = None

    async def add_instance(self, name, *, template="", server=None):
        self.added.append(name)
        inst = instance(name, None, node_name=self.name)
        if server is not None:
            inst.server = server
            server.instance = inst
        self.instances[name] = inst
        return inst

    async def bind_server(self, instance_name, name, channels=None):
        if self.bind_error is not None:
            raise self.bind_error
        self.bound.append((instance_name, name, dict(channels or {})))

    async def delete_instance(self, instance_obj, remove_files):
        self.deleted.append((instance_obj.name, remove_files))
        self.instances.pop(instance_obj.name, None)


class FakeServer:
    """The server a node hands back: the name, the channels, the stop and the unlink.

    ``name`` is the name it carries FROM THE START — ``"n/a"`` for the blank server a bind or an
    ``add_instance`` produces, a real name for the server an instance already carries.
    """

    def __init__(self, name="n/a", *, node=None, instance_obj=None, status=Status.SHUTDOWN):
        self.name = name
        self.node = node
        self.instance = instance_obj
        self.status = status
        self.settings = {}
        self.renamed: list[tuple[str, bool]] = []
        self.channels: list[dict] = []
        self.stopped = 0
        self.unlinked = 0

    async def rename(self, new_name, update_settings=False):
        self.renamed.append((new_name, update_settings))
        self.name = new_name

    async def update_channels(self, channels):
        self.channels.append(dict(channels))

    async def shutdown(self, force=False):
        self.stopped += 1
        self.status = Status.SHUTDOWN

    async def unlink(self):
        self.unlinked += 1
        if self.instance is not None:
            self.instance.server = None


@pytest.mark.asyncio
async def test_add_server_creates_an_instance_and_names_the_server(recording_bot):
    """No instance named: ``node.add_instance`` runs, and the server it created is named + channelled."""
    from plugins.admin.actions import add_server

    node = FakeNode()
    server_obj = FakeServer("n/a", node=node)
    node.add_instance = types.MethodType(  # type: ignore[method-assign]
        lambda self, name, *, template="": _added(self, name, server_obj), node)
    result = await add_server(_context(node), "dcs-a-01", "SRS-2", status=123, chat=-1)
    assert result.success is True
    assert node.added == ["SRS-2"]
    assert server_obj.renamed == [("SRS-2", True)]
    assert server_obj.channels == [{"status": 123, "chat": -1}]
    assert result.data.get("audit") == "recorded"
    assert len(recording_bot.calls) == 1
    assert "SRS-2" in recording_bot.calls[0][0]


async def _added(node, name, server_obj):
    node.added.append(name)
    inst = instance(name, server_obj, node_name=node.name)
    node.instances[name] = inst
    return inst


@pytest.mark.asyncio
async def test_add_server_without_configure_leaves_a_blank_instance(recording_bot):
    """Discord's own Dismiss: the instance exists and carries NO server."""
    from plugins.admin.actions import add_server

    node = FakeNode()
    server_obj = FakeServer(node=node)
    node.add_instance = types.MethodType(  # type: ignore[method-assign]
        lambda self, name, *, template="": _added(self, name, server_obj), node)
    result = await add_server(_context(node), "dcs-a-01", "SRS-2", configure=False)
    assert result.success is True
    assert server_obj.unlinked == 1
    assert server_obj.renamed == []
    assert "no server bound" in result.message


@pytest.mark.asyncio
async def test_add_server_attaches_to_an_existing_empty_instance(recording_bot, fake_service_bus):
    """The attach path ASKS the instance's node to bind — this process binds nothing itself.

    The action resolves the instance's own node from the value and calls its ``bind_server``; the
    bus is asserted UNTOUCHED, which is the design: the binding (the blank server, the instance, the
    registration) belongs to the machine that owns the instance, so the console never builds one.
    """
    from plugins.admin.actions import add_server

    free = instance("Host-B", None, bot_port=7001, dcs_port=10350)
    node = FakeNode(instances={"Host-B": free})

    async def _no_add_instance(self, name, *, template=""):
        raise AssertionError("add_instance must NOT run when an instance was chosen")

    node.add_instance = types.MethodType(_no_add_instance, node)
    result = await add_server(_context(node), "dcs-a-01", "SRS-2", instance="dcs-a-01/Host-B",
                              status=123, chat=-1)
    assert result.success is True, result.message
    assert node.bound == [("Host-B", "SRS-2", {"status": 123, "chat": -1})]
    assert fake_service_bus.servers == {}, "the console must not bind the server itself"
    assert "Host-B" in result.message and "SRS-2" in result.message
    assert result.data.get("audit") == "recorded"


@pytest.mark.asyncio
async def test_an_attach_binds_on_the_instance_s_own_node(recording_bot):
    """The VALUE states the owning node: another machine's instance is bound by THAT node.

    The node picker is the new-instance target, so an attach to an instance on ``dcs-b-01`` is bound
    by ``dcs-b-01`` even when the picked node is the console's own.
    """
    from plugins.admin.actions import add_server

    remote = FakeNode("dcs-b-01", master=False,
                      instances={"Host-D": instance("Host-D", None, node_name="dcs-b-01")})
    own = FakeNode("dcs-a-01", master=True)
    result = await add_server(_context(own, nodes={"dcs-b-01": remote}),
                              "dcs-a-01", "SRS-2", instance="dcs-b-01/Host-D")
    assert result.success is True, result.message
    assert remote.bound == [("Host-D", "SRS-2", {})]
    assert own.bound == []
    assert "SRS-2" in result.message and "dcs-b-01" in result.message


@pytest.mark.asyncio
async def test_add_server_reports_the_nodes_own_refusal(recording_bot):
    """The node is authoritative: its refusal — naming the bound server — is what the operator reads."""
    from plugins.admin.actions import add_server

    node = FakeNode(instances={"Host-C": instance("Host-C", None)})
    node.bind_error = ValueError("Instance 'Host-C' already carries server 'SRS-1'.")
    result = await add_server(_context(node), "dcs-a-01", "SRS-9", instance="dcs-a-01/Host-C")
    assert result.success is False
    assert "SRS-1" in result.message and "Host-C" in result.message
    assert result.data.get("audit") == "recorded"


@pytest.mark.asyncio
async def test_add_server_names_a_node_whose_bot_has_no_bind_method(recording_bot):
    """A node whose bot predates ``Node.bind_server`` gets the fallback sentence, not an RPC error."""
    from plugins.admin.actions import add_server

    remote = FakeNode("dcs-b-01", master=False,
                      instances={"Host-D": instance("Host-D", None, node_name="dcs-b-01")})
    remote.bind_error = ValueError("Call to non-existing function bind_server()")
    result = await add_server(_context(FakeNode("dcs-a-01"), nodes={"dcs-b-01": remote}),
                              "dcs-a-01", "SRS-2", instance="dcs-b-01/Host-D")
    assert result.success is False
    assert "dcs-b-01" in result.message and "does not support binding" in result.message
    assert remote.bound == []


@pytest.mark.asyncio
async def test_add_server_refuses_binding_and_configuring_at_once(recording_bot):
    """``configure`` off is Discord's Dismiss: it applies to a NEW instance, never to an attach."""
    from plugins.admin.actions import add_server

    node = FakeNode(instances={"Host-B": instance("Host-B", None)})
    result = await add_server(_context(node), "dcs-a-01", "SRS-2", instance="dcs-a-01/Host-B",
                              configure=False)
    assert result.success is False
    assert node.bound == []


def test_the_instance_value_carries_the_owning_node():
    """``<node>/<instance>`` splits on the LAST separator; a bare name is the named node's own."""
    from plugins.admin.actions import _instance_target, _node_has_no_bind_server

    assert _instance_target("dcs-b-01/Host-D", "dcs-a-01") == ("dcs-b-01", "Host-D")
    assert _instance_target("Host-B", "dcs-a-01") == ("dcs-a-01", "Host-B")
    assert _instance_target("", "dcs-a-01") == ("dcs-a-01", "")
    assert _node_has_no_bind_server(ValueError("Call to non-existing function bind_server()"))
    assert not _node_has_no_bind_server(ValueError("Instance 'X' does not exist on node 'Y'."))


@pytest.mark.asyncio
async def test_add_server_refuses_an_unknown_node(recording_bot):
    """The seam's own sentence for a node that is offline or unknown."""
    from plugins.admin.actions import add_server

    node = FakeNode()
    ctx = ActionContext(node=node, bus=types.SimpleNamespace(servers={}, nodes={}))
    result = await add_server(ctx, "dcs-z-99", "SRS-2")
    assert result.success is False
    assert result.message == "Node 'dcs-z-99' is offline or unknown."


# ------------------------------------------------------------------ 9. the bot-side DELETE action

@pytest.mark.asyncio
async def test_delete_server_stops_the_server_and_removes_the_instance(recording_bot):
    """The removal stops the server first, then asks the NODE to drop the instance."""
    from plugins.admin.actions import delete_server

    node = FakeNode()
    inst = instance("Host-A", None)
    server_obj = server("SRS-1", instance_obj=inst, status=Status.RUNNING, node=node)
    ctx = _context(node, {"SRS-1": server_obj})
    result = await delete_server(ctx, "SRS-1", remove_files=True)
    assert result.success is True
    assert server_obj.stopped == 1
    assert node.deleted == [("Host-A", True)]
    assert "Saved Games" in result.message
    assert result.data.get("audit") == "recorded"


@pytest.mark.asyncio
async def test_delete_server_does_not_stop_a_server_that_is_already_down(recording_bot):
    """A SHUTDOWN server is not stopped again — the engine's own in-service test decides."""
    from plugins.admin.actions import delete_server

    node = FakeNode()
    inst = instance("Host-A", None)
    server_obj = server("SRS-1", instance_obj=inst, status=Status.SHUTDOWN, node=node)
    result = await delete_server(_context(node, {"SRS-1": server_obj}), "SRS-1")
    assert result.success is True
    assert server_obj.stopped == 0
    assert node.deleted == [("Host-A", False)]


@pytest.mark.asyncio
async def test_delete_server_refuses_a_server_that_is_not_bound(recording_bot):
    """Nothing to remove an instance with: a typed refusal, and no node call."""
    from plugins.admin.actions import delete_server

    node = FakeNode()
    loose = types.SimpleNamespace(name="SRS-1", node=node, instance=None)
    result = await delete_server(_context(node, {"SRS-1": loose}), "SRS-1")
    assert result.success is False
    assert "not bound" in result.message
    assert node.deleted == []


@pytest.mark.asyncio
async def test_delete_server_reports_an_unknown_server(recording_bot):
    """The seam's own name refusal, never an exception."""
    from plugins.admin.actions import delete_server

    node = FakeNode()
    result = await delete_server(_context(node, {}), "SRS-9")
    assert result.success is False
    assert result.message == "Server 'SRS-9' not found."


# ------------------------------------------- 10. the English render is the SOURCE, labels and all

@pytest.mark.asyncio
async def test_the_english_console_renders_the_source_label_for_every_control(
        config_dir, clean_admin_registry):
    """The ENGLISH console shows the MSGID for the labels the source catalog used to mis-serve.

    English is the msgid, so a source-language catalog must carry no translation (or its own msgid);
    a stray one makes the console render ANOTHER label's text — the page-level "Add server" control
    came out as "DCS servers" and the Add dialog's instance option "Create a new instance" as "Create
    a new database". This drives the REAL app (the console's own routes and the one ``confirm.html``
    dialog), not a unit stub, so the render is what a browser gets.
    ``test_the_english_catalog_is_the_source_and_serves_nothing_else`` pins the catalog; this pins the
    DOCUMENT that catalog feeds.
    """
    app = build(config_dir, cluster_with())
    async with client(app) as http:
        await sign_in(http)
        servers = await fleet(http)
        add = await open_add_dialog(http)
        delete = await open_delete_dialog(http, "SRS-1")

 # the Servers page's page-level control reads its own label
    assert ">Add server</button>" in servers
    assert ">DCS servers</button>" not in servers

 # the Add dialog: its heading, its remaining field labels, its picker option and its go button
    assert ">Add a server</h3>" in add
    assert ">Server name</label>" in add
    assert ">Create a new instance</option>" in add
    assert ">Add server</button>" in add
    assert ">DCS servers</button>" not in add
    assert "Create a new database" not in add

 # the Delete dialog's primary button reads its own label
    assert ">Delete server</button>" in delete
    assert ">DCS servers</button>" not in delete
