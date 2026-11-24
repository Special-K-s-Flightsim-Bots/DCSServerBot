"""Who may see the console at all: Admin, DCS Admin -- and a manager of at least one server.

The console is for exactly three kinds of identity: ``Admin`` (unscoped), ``DCS Admin``, and a
MANAGER (an identity whose resolved scope holds the ``managed_by`` of at least one server, or one
that declared such a scope -- a local account's declaration is the fact). Condition 3 is resolved per
request through ONE resolver, so a rename or config edit lands on the next request. Fail closed: an
unresolvable scope refuses, and an unrestricted scope is only reachable through a role. A refused
signed-in member gets the 403 refusal FACE, never a bounce into the login flow. Runs in-process
against a stub cluster."""
from __future__ import annotations

import re
import types

import pytest
from starlette.requests import Request

from conftest import asgi_client, create_app, install_shell, permissions
from services.webservice import readmodels
from services.webservice.auth import AuthBackend, AuthManager, Identity
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import bans as bans_page
from services.webservice.pages import instances as instances_page
from services.webservice.pages import lists as lists_page
from services.webservice.pages import logs as logs_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import players as players_page
from services.webservice.pages import servers as servers_page
from services.webservice.scope import Scope, manages_any_server
from test_webui_dashboard import (BOT_YAML, COOKIE_NAME, SESSION_SECRET, _hash, _login)
from test_webui_scope_view import Cluster, fleet, instance, node, player, server

#: the four standalone pages, the dashboard and the Admin-only logs page: the whole console.
CONSOLE_PAGES = (dashboard_page.DASHBOARD_PATH, servers_page.SERVERS_PATH, nodes_page.NODES_PATH,
                 instances_page.INSTANCES_PATH, players_page.PLAYERS_PATH)

HTML = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


# ------------------------------------------------------------------------------ fixtures

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _config(users, *, breakglass=None) -> dict:
    auth: dict = {"public_base_url": "http://localhost:9876",
                  "local": {"enabled": True, "users": users}}
    if breakglass is not None:
        auth["breakglass"] = {"enabled": True, "username": "emergency",
                              "password_hash": breakglass}
    return {"session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
            "auth": auth}


#: every persona of the rule, as a local account (the local backend declares roles AND scope).
USERS = [
    {"username": "admin", "password_hash": _hash(), "roles": ["Admin"]},
    {"username": "dcs_admin", "password_hash": _hash(), "roles": ["DCS Admin"]},
 # a MANAGER: no console role at all, but the scope of a server that declares managed_by.
    {"username": "hoster", "password_hash": _hash(), "roles": [], "scope": ["Host A"]},
 # several roles, none of them a console role, one of which manages a server.
    {"username": "hoster_multi", "password_hash": _hash(), "roles": ["DCS", "Alert"],
     "scope": ["Host A"]},
 # a MANAGER whose declaration matches no server (a typo, or a renamed role): the console opens
 # with the honest scope-specific copy - never the cluster, and never a silent lock-out.
    {"username": "outsider", "password_hash": _hash(), "roles": [], "scope": ["Host Q"]},
 # the general public: signed in, no scope, nothing to see.
    {"username": "plain", "password_hash": _hash(), "roles": ["DCS"]},
]


def build(config_dir, cluster, *, users=None, **extra):
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True,
                                                             config_dir=str(config_dir)),
                              config=_config(users if users is not None else USERS, **extra))
    app.state.webui_source_provider = cluster.source
    return app, registrar


async def get(app, path, username, **params):
    async with asgi_client(app) as client:
        assert (await _login(client, username)).status_code == 303, username
        return await client.get(path, params=params or None)


async def get_html(app, path, username, **params):
    """One browser navigation as *username*, with the Accept header a browser sends."""
    async with asgi_client(app) as client:
        assert (await _login(client, username)).status_code == 303, username
        return await client.get(path, params=params or None, headers=HTML)


# --------------------------------------------------------- the pure rule (one place)

def test_a_scope_is_a_licence_only_when_it_manages_something():
    """The rule itself: unrestricted and unresolvable scopes are NOT licences; a restricted one is
    a licence when a server declares it (or when it was DECLARED by the backend)."""
    servers = [server("Unmanaged"), server("Host A Server", ["Host A"])]

    assert manages_any_server(Scope.everything(), servers) is False, \
        "an unrestricted scope is a role's licence, never a manager's"
    assert manages_any_server(Scope.unresolved(), servers) is False, "fail closed"
    assert manages_any_server(Scope.restricted({"Host A"}), servers) is True
    assert manages_any_server(Scope.restricted({"Host A", "DCS"}), servers) is True
    assert manages_any_server(Scope.restricted({"DCS"}), servers) is False, \
        "holding a role no server declares managed_by is not managing a server"
    assert manages_any_server(Scope.restricted({7, "Host A"}), servers) is True, \
        "a managed_by role may be matched by its Discord id"
    assert manages_any_server(Scope.restricted({"Host Q"}), servers) is False
 # a server that declares NOTHING is visible to everyone who may read the console - it must not
 # promote the caller to manager (that would make every member a manager)
    assert manages_any_server(Scope.restricted({"Host A"}), [server("Unmanaged")]) is False
    assert manages_any_server(Scope.restricted({"Host A"}), []) is False
 # a DECLARED scope (a local account) is the fact itself, even before a server matches
    assert manages_any_server(Scope.restricted({"Host Q"}, declared=True), []) is True


# ------------------------------------------------- the real resolver, per request

class _Backend(AuthBackend):
    """A backend that returns one fixed identity for any request carrying a session reference."""

    name = "probe"
    supports_password = False

    def __init__(self, identity):
        self._identity = identity

    def authenticate(self, request):
        ref = request.session.get("webui_identity")
        if not ref or ref.get("backend") != self.name:
            return None
        return self._identity


def _request(app, *, session=None):
    """A real ``Request`` over *app* — the shape ``Request(scope)`` needs to carry a session."""
    path = "/"
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "app": app, "session": dict(session or {}), "headers": [], "client": ("127.0.0.1", 51234),
        "server": ("testserver", 443),
    })


def _manager(guild_role="Host A", *, roles=("DCS",), app=None):
    """``AuthManager.manages_any_server`` for a member holding *roles* plus *guild_role*.

    The tokens are the member's Discord role NAMES and ids, exactly what the Discord backend walks
    (``member_scope_tokens``), which is why a plain member's token set is NEVER empty — the rule has
    to be the ``managed_by`` match, not the presence of roles.
    """
    identity = Identity(subject="42", backend="probe", roles=frozenset(roles),
                        scope=Scope.restricted(set(roles) | {guild_role}))
    manager = AuthManager([_Backend(identity)])
    return manager, _request(app, session={"webui_identity": {"backend": "probe",
                                                              "subject": "42"}})


def test_a_plain_member_is_not_a_manager_however_many_roles_it_holds(config_dir):
    """THE regression this card exists for: the refusal line used to include ``DCS``."""
    cluster = fleet()
    app, _registrar = build(config_dir, cluster)

    manager, request = _manager("DCS", app=app)          # the general member role
    assert manager.manages_any_server(request) is False

    manager, request = _manager("Host A", roles=("DCS", "Alert"), app=app)
    assert manager.manages_any_server(request) is True, \
        "a member holding a server's managed_by role IS a manager"


def test_an_admin_is_not_a_manager_but_does_not_need_to_be(config_dir):
    """``Admin`` is unscoped by rule: it enters through its role, never through the scope."""
    app, _registrar = build(config_dir, fleet())
    from services.webservice.scope import ADMIN_ROLE
    manager, request = _manager("Host A", roles=(ADMIN_ROLE,), app=app)
    assert manager.manages_any_server(request) is False


def test_the_manager_resolution_fails_closed_without_an_identity(config_dir):
    app, _registrar = build(config_dir, fleet())
    manager = AuthManager([_Backend(None)])
    assert manager.manages_any_server(_request(app)) is False


def test_the_manager_resolution_follows_the_live_cluster(config_dir):
    """A ``managed_by`` edit is an access change: the SAME identity flips on the next request."""
    cluster = fleet()
    app, _registrar = build(config_dir, cluster)
    manager, request = _manager("Host A", roles=(), app=app)
    assert manager.manages_any_server(request) is True

    cluster.servers[0].locals["managed_by"] = ["Host Z"]
    assert manager.manages_any_server(request) is False, \
        "the decision is read from the live cluster, never cached across requests"


# ------------------------------------------------------- the capability map (one rule, once)

def test_exactly_the_console_capabilities_carry_the_scope(config_dir):
    """The exception is DECLARED, not inferred: the console carries it, the Admin-only log does not."""
    app, _registrar = build(config_dir, fleet())

    assert permissions.scope_grants(dashboard_page.DASHBOARD_CAPABILITY) is True
    for module in (servers_page, nodes_page, instances_page, players_page):
        assert permissions.scope_grants(module.PAGE.capability) is True, module.PAGE.capability
    assert permissions.scope_grants(logs_page.LOGS_CAPABILITY) is False, \
        "the bot log stays Admin-only: a manager reaches it through no scope"


def test_the_capability_grants_the_console_to_the_three_kinds_of_identity(config_dir):
    app, _registrar = build(config_dir, fleet())
    capability = dashboard_page.DASHBOARD_CAPABILITY

    assert permissions.allows(capability, {"Admin"}) is True
    assert permissions.allows(capability, {"DCS Admin"}) is True
    assert permissions.allows(capability, {"DCS"}) is False
    assert permissions.allows(capability, {"DCS", "Alert"}) is False
    assert permissions.allows(capability, {"DCS"}, manager=True) is True, \
        "a manager enters through the scope, whatever roles they also hold"
    assert permissions.allows(capability, set(), manager=False) is False


# ---------------------------------------------------------- the gate: who gets in, and how

@pytest.mark.asyncio
@pytest.mark.parametrize("username", ["admin", "dcs_admin", "hoster", "hoster_multi", "outsider"])
async def test_the_three_kinds_of_identity_reach_the_whole_console(config_dir, username):
    """Admin, DCS Admin and a manager: 200 on the dashboard and on every page."""
    app, _registrar = build(config_dir, fleet())
    for path in CONSOLE_PAGES:
        response = await get_html(app, path, username)
        assert response.status_code == 200, (username, path, response.status_code)


@pytest.mark.asyncio
async def test_a_manager_sees_only_their_own_rows(config_dir):
    """The hoster customer's landing experience: their servers, their figures, nobody else's."""
    app, _registrar = build(config_dir, fleet())
    html = (await get_html(app, "/fleet", "hoster")).text
    assert "Host A Server" in html and "Host B Server" not in html

    dashboard = (await get_html(app, dashboard_page.DASHBOARD_PATH, "hoster")).text
    assert "1/1 servers up" in dashboard, "the pills follow the scope"
    assert "Host A Server" in dashboard


@pytest.mark.asyncio
async def test_a_manager_whose_scope_matches_nothing_gets_the_honest_copy(config_dir):
    """A declared scope that matches no server still opens the console - and the page says WHY it is
    empty instead of claiming the cluster has no servers (or showing somebody else's)."""
    app, _registrar = build(config_dir, fleet())
    html = (await get_html(app, servers_page.SERVERS_PATH, "outsider")).text
    assert "none of them are here" in html, "the scope-specific copy, not the cold-start one"
    assert " ".join(readmodels.SCOPED_EMPTY_MESSAGE.split()).replace("'", "&#39;") \
        in " ".join(html.split()), "the whole sentence, as the template renders it"
    assert readmodels.NO_SERVERS_MESSAGE not in html
    assert "Host A Server" not in html and "Host B Server" not in html


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CONSOLE_PAGES)
async def test_a_plain_member_is_refused_on_every_page(config_dir, path):
    """The general public gets NOTHING: the 403 refusal face on the dashboard and on each page."""
    app, _registrar = build(config_dir, fleet())
    response = await get_html(app, path, "plain")
    assert response.status_code == 403, (path, response.status_code)
    assert response.headers["content-type"].startswith("text/html"), "a browser gets a page"
    assert "Access denied" in response.text
    assert "Not authorized" not in response.text, "the JSON body is not what a browser renders"


@pytest.mark.asyncio
async def test_a_refused_member_is_never_bounced_into_the_login_flow(config_dir):
    """THE TRAP: a signed-in, unauthorised member must not be sent back to the door - and the second
    request must behave identically (no loop, no state that changes the answer)."""
    app, _registrar = build(config_dir, fleet())
    async with asgi_client(app) as client:
        assert (await _login(client, "plain")).status_code == 303, "the member IS signed in"
        for path in CONSOLE_PAGES + (logs_page.LOGS_PATH,):
            first = await client.get(path, headers=HTML)
            second = await client.get(path, headers=HTML)
            for attempt, response in enumerate((first, second), start=1):
                assert response.status_code == 403, (path, attempt, response.status_code)
                assert "location" not in response.headers, \
                    f"{path}: a signed-in refusal must be a page, not a redirect to the door"
                assert auth_routes.LOGIN_PATH not in (response.headers.get("location") or "")
                assert "Access denied" in response.text
            assert first.text == second.text, f"{path}: the second request answered differently"

 # the anonymous door still works: the auto-redirect is for ANONYMOUS visits only (a second,
 # cookie-less client — the first one is carrying this member's session)
        async with asgi_client(app) as anonymous:
            anon = await anonymous.get(dashboard_page.DASHBOARD_PATH, headers=HTML)
            assert anon.status_code == 303, "the login door still redirects an anonymous visit"
            assert auth_routes.LOGIN_PATH in anon.headers["location"]


@pytest.mark.asyncio
async def test_a_plain_member_is_offered_no_console_entry(config_dir):
    """The nav is the same predicate as the gate: nothing is offered to a member who cannot open it,
    and the list of what they may see is EMPTY (not merely short)."""
    app, registrar = build(config_dir, fleet())

    assert permissions.visible_nav(registrar.nav, {"DCS"}) == []
    assert dashboard_page.nav_groups(registrar, {"DCS"}, current="/") == []
    assert dashboard_page.allowed_tabs({"DCS"}) == ()

 # positive control, through the SAME predicate: a manager IS offered the console
    groups = dashboard_page.nav_groups(registrar, set(), current="/", manager=True)
    # a manager is offered the MAIN MENU items, and the Site settings page: ``site.view`` carries a
    # manager-scope grant, so a group admin reaches the one site their scope holds. The Settings page
    # itself stays the Admin's, which is why it is not in this list.
    assert [link["url"] for group in groups for link in group["links"]] == [
        dashboard_page.DASHBOARD_PATH, servers_page.SERVERS_PATH,
        players_page.PLAYERS_PATH, bans_page.BANS_PATH, nodes_page.NODES_PATH,
        "/settings/site"], \
        "a manager is offered the console's MAIN MENU items, group by group"


@pytest.mark.asyncio
async def test_the_console_narrows_again_when_the_scope_is_gone(config_dir):
    """Dynamic, per request: the same manager loses the ROWS whose server stops declaring them.

    A local account's declaration is the fact (so the door stays theirs — the honest empty copy is
    what a declared scope that matches nothing gets, see below), but the VIEW is resolved from the
    live cluster on every request: rename the ``managed_by`` and the server is gone on the next
    render. The Discord-shaped flip — where the same edit takes the console AWAY, fail closed — is
    :func:`test_the_manager_resolution_follows_the_live_cluster`, because a member's tokens are every
    role they hold and an unmatched token set is not a manager's licence.
    """
    cluster = fleet()
    app, _registrar = build(config_dir, cluster)
    html = (await get_html(app, "/", "hoster")).text
    assert "Host A Server" in html

    cluster.servers[0].locals["managed_by"] = ["Host Z"]
    response = await get_html(app, "/", "hoster")
    assert response.status_code == 200, "a declared scope keeps its door (the honest copy, below)"
    assert "Host A Server" not in response.text, "the row follows the live managed_by"
    assert "Host B Server" not in response.text, "and never another hoster's server"
    assert "none of them are here" in response.text, \
        "with nothing left in scope, the honest empty copy - never the cluster"


def test_a_manager_is_refused_when_the_scope_cannot_be_resolved(config_dir):
    """FAIL CLOSED (bot restarting, guild unreachable): an unresolvable scope is not a licence.

    Driven through the REAL ``AuthManager.manages_any_server`` — the method the app installs as the
    manager resolver — so this pins the rule, not a stub of it. An identity with roles but no
    resolvable scope is refused; falling back to ``everything()`` here is exactly the hole that would
    show the whole cluster during a restart.
    """
    app, _registrar = build(config_dir, fleet())
    identity = Identity(subject="9", backend="probe", roles=frozenset({"DCS"}),
                        scope=Scope.unresolved())
    manager = AuthManager([_Backend(identity)])
    request = _request(app, session={"webui_identity": {"backend": "probe", "subject": "9"}})

    assert manager.manages_any_server(request) is False
    assert manager.scope_for(request) == Scope.unresolved(), "and the view stays empty"


@pytest.mark.asyncio
async def test_break_glass_reaches_the_console(config_dir):
    """Break-glass is unscoped and holds ``Admin``, so it enters, as before."""
    app, _registrar = build(config_dir, fleet(), users=[], breakglass=_hash())
    async with asgi_client(app) as client:
        assert (await _login(client, "emergency")).status_code == 303
        assert (await client.get("/", headers=HTML)).status_code == 200


# --------------------------------------------------------------- the refusal names the rule

def test_the_refusal_names_the_new_requirement_and_never_claims_dcs(config_dir):
    """The wording of record: it names the three kinds of identity, in ONE place (the log line and
    the JSON detail read the same function), and it does not invite DCS into the console."""
    app, _registrar = build(config_dir, fleet())
    text = permissions.requirement_text(dashboard_page.DASHBOARD_CAPABILITY)

    assert "Admin" in text and "DCS Admin" in text
    assert "manager" in text, text
    assert re.search(r"\bDCS\b(?! Admin)", text) is None, \
        f"the refusal still claims the plain DCS role may enter: {text}"
    assert permissions.requirement_text(logs_page.LOGS_CAPABILITY) == "Admin", \
        "the log's requirement is unchanged (and names no manager)"


@pytest.mark.asyncio
async def test_the_gate_refusal_carries_the_new_requirement(config_dir):
    app, _registrar = build(config_dir, fleet())
    response = await get(app, "/", "plain", accept="application/json")
    assert response.status_code == 403
    assert response.json()["detail"] == (
        f"Not authorized (needs one of: "
        f"{permissions.requirement_text(dashboard_page.DASHBOARD_CAPABILITY)}).")


# ------------------------------------------------------------------- the pages' own roles

def test_no_console_page_names_dcs_as_a_reader():
    """The role tuples of record: the console's pages name Admin and DCS Admin, and the scope
    carries the third kind. ``DCS`` appears in NONE of them (it is the general member role)."""
    assert dashboard_page.DASHBOARD_ROLES == ("Admin", "DCS Admin")
    assert lists_page.READ_ONLY_ROLES == ("Admin", "DCS Admin")
    assert lists_page.CLUSTER_ROLES == ("Admin", "DCS Admin")
    for module in (servers_page, nodes_page, instances_page, players_page):
        assert "DCS" not in module.PAGE.roles, module.PAGE.capability
    assert logs_page.LOGS_ROLES == ("Admin",)
