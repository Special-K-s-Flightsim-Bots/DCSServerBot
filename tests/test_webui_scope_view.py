"""The scoped hoster view across EVERY surface.

A managing user sees ONLY their servers -- every number and list follows that view -- and cannot
reach another server's data by any means. Pins: the wrap is at ONE plug point
(``pages.dashboard.request_source``), so the tables, pills, tab badges, banner, standalone pages and
live stream all follow by construction (a source-level AST pin keeps a future page from reading the
seam itself and escaping the scope); a scoped identity sees exactly its own rows on every surface and
its live fragments contain NONE of the out-of-scope names; a directly named out-of-scope target is
refused 403 without disclosing whether it exists; ``?sort``/``?dir``/``?q``/``?tab``/``?log``/``?level``
cannot widen the view, and the scope is re-resolved per request; a declared local scope renders the
same view; break-glass and any ``Admin`` are unscoped; and an empty scope renders the honest
scope-specific copy. Runs in-process against a stub source."""
from __future__ import annotations

import ast
import re
import types
from html import unescape
from pathlib import Path

import pytest
from starlette.requests import Request

from conftest import asgi_client, create_app, install_shell, permissions
from services.webservice import readmodels, shell
from services.webservice.auth import Identity
from services.webservice.auth import routes as auth_routes
from services.webservice.auth.discord_oauth import DiscordOAuthBackend
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import live as live_page
from services.webservice.scope import Scope
from test_webui_dashboard import (_hash, _login, BOT_YAML, PASSWORD,
                                  SESSION_SECRET, COOKIE_NAME)
from test_webui_scope import _member

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = REPO_ROOT / "services" / "webservice" / "pages"

#: the out-of-scope names of the standard fixture cluster. Every scan below looks for ALL of them,
#: because a leak is a leak wherever it lands (a table cell, a banner sentence, a pill, a title).


# ------------------------------------------------------------------------------ test doubles

class Status:
    RUNNING = "Running"
    PAUSED = "Paused"
    STOPPED = "Stopped"
    SHUTDOWN = "Shutdown"


def player(name, ucid=None):
    return types.SimpleNamespace(name=name, ucid=ucid or name.lower(), active=True)


def server(name, managed_by=None, *, node_name="dcs-a-01", instance="Host-A-Instance",
           status=Status.RUNNING, players=()):
    """A server double shaped like ``ServerImpl`` where the scope and the read models read it.

    ``managed_by`` lives in ``locals``; ``managed_by=None`` produces the empty mapping that means "no
    restriction".
    """
    locals_ = {"managed_by": list(managed_by)} if managed_by else {}
    return types.SimpleNamespace(
        name=name, status=status, node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=instance), port=10309, current_mission=None,
        players={p.ucid: p for p in players}, is_remote=False, locals=locals_)


def instance(name, server_obj=None, *, dcs_port=10309, webgui_port=8088):
    return types.SimpleNamespace(name=name, dcs_port=dcs_port, webgui_port=webgui_port,
                                 server=server_obj)


def node(name, *, master=False, instances=()):
    return types.SimpleNamespace(name=name, master=master, instances=dict(instances))


class Cluster:
    """A stub cluster behind a provider, so a test can CHANGE it between two renders.

    The provider is what ``request_source`` calls per request, so mutating ``servers`` (a rename, a
    new ``managed_by``) lands on the next request - which is the point of the "while their page is
    open" tests.
    """

    def __init__(self, servers=(), *, nodes=None, node=None, instances=()):
        self.servers = list(servers)
        self.instances = list(instances)
        self.nodes = dict(nodes or {})
        self.node = node

    def source(self):
        return readmodels.LiveSource(servers=tuple(self.servers), instances=tuple(self.instances),
                                     nodes=self.nodes, node=self.node, log_path=None)


def fleet():
    """The standard cluster: A (hosted by ``Host A``) and B (hosted by ``Host B``).

    A is PAUSED and its node is online; B is SHUTDOWN and its node is unresolved (offline), so the
    banner has a problem of EACH kind and the scoped view must report only A's.
    """
    a = server("Host A Server", ["Host A"], node_name="dcs-a-01", instance="Host-A-Instance",
               status=Status.PAUSED, players=[player("Viper-1")])
    b = server("Host B Server", ["Host B"], node_name="dcs-b-01", instance="Host-B-Instance",
               status=Status.SHUTDOWN, players=[player("Viper-2")])
    node_a = node("dcs-a-01", master=True, instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a, "dcs-b-01": None}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


# ------------------------------------------------------------------------------ fixtures

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _config(users, **dashboard):
    config: dict = {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": users}},
    }
    if dashboard:
        config["dashboard"] = dashboard
    return config


def _users(**extra):
    """The three local accounts every test shares: an Admin, and one hoster per host role."""
    users = [{"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]},
             {"username": "hoster", "password_hash": _hash(), "roles": ["DCS Admin"],
              "scope": ["Host A"]},
             {"username": "hoster-b", "password_hash": _hash(), "roles": ["DCS Admin"],
              "scope": ["Host B"]}]
    users.extend(extra.pop("users", []))
    return users


def build(config_dir, cluster, *, users=None, **dashboard):
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=_config(users if users is not None else _users(), **dashboard))
    app.state.webui_source_provider = cluster.source
    return app


async def get(app, path, username="hoster", **params):
    async with asgi_client(app) as client:
        await _login(client, username)
        response = await client.get(path, params=params or None)
    return response


def assert_no_out_of_scope(text: str, where: str = ""):
    """Scan a whole payload for the out-of-scope rows of the fixture cluster.

    The names must not appear ANYWHERE. The player is checked as a
    rendered ROW (``<b>Viper-2</b>``) rather than as a bare word, because ``?q=Viper`` echoes the
    visitor's own query back into the search field — that is their input reflected, not another
    server's data leaking (and the row-level check is what "the search found somebody else's
    player" would look like). The players share a COMMON PREFIX (Viper-N) on purpose: ``?q=Viper``
    would reach both servers if the scope were not applied to the search.
    """
    for name in ("Host B Server", "dcs-b-01", "Host-B-Instance", "Viper-2"):
        assert name not in text, f"{where} leaked {name!r}"
    assert "<b>Viper-2</b>" not in text, f"{where} leaked the out-of-scope player row"


# ------------------------------------------------------- sees exactly their servers, on every surface

@pytest.mark.asyncio
async def test_a_scoped_hoster_sees_exactly_their_servers(config_dir):
    """The Servers table, the pills and the banner follow the scope - all of them."""
    app = build(config_dir, fleet())
    response = await get(app, "/")
    assert response.status_code == 200
    html = response.text

    assert "Host A Server" in html
    assert_no_out_of_scope(html, "the dashboard")

 # the pills: THEIR counts, never the cluster's (1 of 1, not 1 of 2)
    assert "1/1 servers up" in html
    assert "1/1 nodes online" in html
    assert "1 players online" in html

 # the banner: their problems only. A is paused; B is down and its node is offline - none of
 # that is theirs to see.
    assert "1 server(s) paused: Host A Server" in html
    assert "not heartbeating" not in html, "an offline node they do not manage is not their problem"
    assert "server(s) down" not in html


@pytest.mark.asyncio
async def test_a_node_carrying_their_server_is_their_problem_even_shared(config_dir):
    """The scope keeps the nodes that carry a surviving server -- a node is theirs when THEIR server runs
    on it, whatever else it carries.

    On the shared node A (theirs) and B (not) both sit, so the hoster is told because their own server
    is affected; the banner still names no out-of-scope server, instance or player.
    """
    a = server("Host A Server", ["Host A"], node_name="dcs-shared-01", instance="Host-A-Instance",
               status=Status.RUNNING, players=[player("Viper-1")])
    b = server("Host B Server", ["Host B"], node_name="dcs-shared-01", instance="Host-B-Instance",
               status=Status.RUNNING, players=[player("Viper-2")])
    cluster = Cluster([a, b], nodes={"dcs-shared-01": None, "dcs-b-01": None},
                      node=node("dcs-shared-01", instances={}),
                      instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])
    app = build(config_dir, cluster)
    html = (await get(app, "/")).text

    assert "1 node(s) not heartbeating: dcs-shared-01" in html
    assert "0/1 nodes online" in html, "the node count is theirs: one node, offline"
    assert_no_out_of_scope(html, "the shared-node dashboard")


@pytest.mark.asyncio
async def test_the_tab_badges_count_only_their_rows(config_dir):
    """The strip is data (``console.tabs``); the counts must be the scoped ones on every tab."""
    app = build(config_dir, fleet())
    html = (await get(app, "/")).text
    for label, count in (("Servers", 1), ("Nodes", 1), ("Instances", 1), ("Players", 1)):
        found = re.search(rf'{label} <span class="badge[^"]*">(\d+)</span>', html)
        assert found is not None, f"no badge rendered for the {label} tab"
        assert found.group(1) == str(count), f"{label} badge shows the cluster's count"


@pytest.mark.asyncio
async def test_the_standalone_pages_follow_the_scope(config_dir):
    """``/fleet``, ``/nodes``, ``/instances`` and ``/players`` read the same scoped source."""
    app = build(config_dir, fleet())
    for path in ("/fleet", "/nodes", "/instances", "/players"):
        response = await get(app, path)
        assert response.status_code == 200, path
        assert_no_out_of_scope(response.text, path)
    assert "Host A Server" in (await get(app, "/fleet")).text
    assert "Viper-1" in (await get(app, "/players")).text
 # an idle instance is cluster infrastructure, omitted for a scoped viewer
    assert "Host-A-Instance" in (await get(app, "/instances")).text


@pytest.mark.asyncio
async def test_a_scoped_hosters_fragments_carry_no_out_of_scope_names(config_dir):
    """The polling endpoint: scan the WHOLE payload, on every tab."""
    app = build(config_dir, fleet())
    async with asgi_client(app) as client:
        await _login(client, "hoster")
        for tab in ("servers", "nodes", "instances", "players"):
            response = await client.get(live_page.DASHBOARD_FRAGMENTS_PATH, params={"tab": tab})
            assert response.status_code == 200, (tab, response.status_code)
            payload = response.json()
 # the log is Admin-only: the hoster's fragment payload has no log target AT ALL (it is
 # omitted rather than emptied, so no later frame could ever carry it)
            assert set(payload) == {live_page.STATE_TARGET, live_page.PILLS_TARGET}, tab
            assert_no_out_of_scope("\n".join(payload.values()), f"the {tab} fragment payload")
            assert_no_out_of_scope(response.text, f"the raw {tab} response")
 # positive control: the endpoint really is rendering THEIR data
        servers = (await client.get(live_page.DASHBOARD_FRAGMENTS_PATH,
                                    params={"tab": "servers"})).json()
        assert "Host A Server" in servers[live_page.STATE_TARGET]


@pytest.mark.asyncio
async def test_a_scoped_hosters_stream_snapshot_carries_no_out_of_scope_names(config_dir):
    """The SSE stream resolves through the SAME scoped source (its snapshot is a whole-frame scan)."""
    app = build(config_dir, fleet(), stream_clients=1, refresh_seconds=1)
    registry = live_page.get_registry(app, live_page.dashboard_config(app))
    assert registry.try_acquire() is True, "the only slot is taken, so the stream ends after one frame"
    try:
        async with asgi_client(app) as client:
            await _login(client, "hoster")
            response = await client.get(live_page.DASHBOARD_STREAM_PATH,
                                        headers={"accept": "text/event-stream"})
        assert response.status_code == 200
        assert response.text.startswith("event: snapshot\n")
        assert_no_out_of_scope(response.text, "the SSE snapshot")
        assert "Host A Server" in response.text
    finally:
        registry.release()


# ------------------------------------------------------- a named target: 403, and no disclosure

def _request(app, path="/fleet", *, session=None, accept=None):
    headers = [("accept", accept)] if accept else []
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "app": app, "session": dict(session or {}),
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": ("127.0.0.1", 51234), "server": ("testserver", 443),
    }
    return Request(scope)


def _identity_session(username="hoster"):
    return {"webui_identity": {"backend": "local", "subject": username}}


def _hoster_request(app, username="hoster"):
    return _request(app, session=_identity_session(username))


@pytest.mark.asyncio
async def test_an_out_of_scope_server_named_directly_is_refused_and_does_not_disclose(config_dir):
    """A NAMED target outside the scope is a 403 - and a name that does not exist answers the SAME
    refusal, so the console never tells a scoped hoster whether a server they cannot see exists."""
    app = build(config_dir, fleet())
    hoster = _hoster_request(app)

 # in scope -> the server object, resolved through the scoped source
    resolved = dashboard_page.scoped_server(hoster, "Host A Server")
    assert getattr(resolved, "name", None) == "Host A Server"

    with pytest.raises(Exception) as refused:
        dashboard_page.scoped_server(hoster, "Host B Server")
    assert getattr(refused.value, "status_code", None) == 403
    with pytest.raises(Exception) as absent:
        dashboard_page.scoped_server(hoster, "No Such Server")
    assert getattr(absent.value, "status_code", None) == 403, "a scoped caller must not learn it exists"
    assert getattr(refused.value, "detail", None) == getattr(absent.value, "detail", None), \
        "the two refusals must be indistinguishable"


def test_the_refusal_has_the_consoles_two_faces(config_dir):
    """The 403 the scope raises is rendered by the ONE refusal handler: a page for a browser, the
    JSON body for an API caller (the wire contract that must not change)."""
    app = build(config_dir, fleet())
    exc = dashboard_page.out_of_scope_refusal()
    assert exc.status_code == 403

    browser = _request(app, session=_identity_session(), accept="text/html,application/xhtml+xml")
    page = shell.refusal_response(browser, exc)
    assert page.status_code == 403
    assert "text/html" in page.headers["content-type"]
    assert "Host B Server" not in _body(page)
    assert "denied" in _body(page)

    api = _request(app, path="/api/dashboard/fragments", session=_identity_session())
    body = shell.refusal_response(api, exc)
    assert body.status_code == 403
    assert body.headers["content-type"].startswith("application/json")
    assert "Host B Server" not in _body(body)


def _body(response) -> str:
    """The response body as text (``body`` is a ``memoryview`` on this Starlette version)."""
    return bytes(response.body).decode("utf-8")


# ------------------------------------------------------- parameters cannot widen the view

@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"sort": "name"},
    {"sort": "players", "dir": "desc"},
    {"dir": "desc"},
    {"q": "Viper"},
    {"tab": "players"},
    {"tab": "nodes"},
    {"tab": "instances"},
    {"log": "right"},
    {"log": "bottom"},
    {"log": "expanded"},
    {"level": "all"},
    {"tab": "players", "q": "Viper", "sort": "players", "dir": "desc", "level": "all"},
])
async def test_url_parameters_cannot_widen_the_view(config_dir, params):
    """Each parameter attempted separately (and then combined): the view stays theirs."""
    app = build(config_dir, fleet())
    response = await get(app, "/", **params)
    if response.status_code == 200:
        assert_no_out_of_scope(response.text, f"the dashboard with {params}")
    else:
 # ``?log=expanded`` asks for the Admin-only log and is refused before it renders anything
        assert response.status_code == 403, (params, response.status_code)
        assert_no_out_of_scope(response.text, f"the refusal for {params}")


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
        {"q": "Viper"},
    {"sort": "active", "dir": "desc"},
        {"q": "Viper", "sort": "active", "dir": "desc"},
])
async def test_the_players_search_and_sort_operate_over_scoped_rows_only(config_dir, params):
    app = build(config_dir, fleet())
    response = await get(app, "/players", **params)
    assert response.status_code == 200
    assert_no_out_of_scope(response.text, f"/players with {params}")
    assert "Viper-1" in response.text, "the scoped player is still found"
    assert "Viper-2" not in response.text, "the search reached another server's player"


# ------------------------------------------------------- re-resolution: identity, rename, scope change

@pytest.mark.asyncio
async def test_the_same_url_is_a_different_view_for_a_different_identity(config_dir):
    """The scope is resolved PER REQUEST from the identity - never from the cookie or the URL."""
    app = build(config_dir, fleet())
    first = (await get(app, "/fleet", "hoster")).text
    second = (await get(app, "/fleet", "hoster-b")).text
    assert "Host A Server" in first and "Host B Server" not in first
    assert "Host B Server" in second and "Host A Server" not in second


@pytest.mark.asyncio
async def test_a_renamed_server_shows_its_new_name_on_the_next_render(config_dir):
    """A rename while the page is open: the next fragment reads the live name (the read is per
    request; nothing about a server is remembered in the page or the stream)."""
    cluster = fleet()
    app = build(config_dir, cluster)
    async with asgi_client(app) as client:
        await _login(client, "hoster")
        before = (await client.get(live_page.DASHBOARD_FRAGMENTS_PATH,
                                   params={"tab": "servers"})).text
        assert "Host A Server" in before
        cluster.servers[0].name = "Host A Server (renamed)"
        after = (await client.get(live_page.DASHBOARD_FRAGMENTS_PATH,
                                  params={"tab": "servers"})).text
    assert "Host A Server (renamed)" in after
    assert ">Host A Server<" not in after, "the stale name must not survive a re-render"


@pytest.mark.asyncio
async def test_a_server_moved_out_of_the_scope_drops_out_of_the_next_render(config_dir):
    """A ``managed_by`` edit is an access change: the next render drops the server."""
    cluster = fleet()
    app = build(config_dir, cluster)
    async with asgi_client(app) as client:
        await _login(client, "hoster")
        before = (await client.get(live_page.DASHBOARD_FRAGMENTS_PATH,
                                   params={"tab": "servers"})).text
        assert "Host A Server" in before
        cluster.servers[0].locals["managed_by"] = ["Host Z"]
        after = (await client.get(live_page.DASHBOARD_FRAGMENTS_PATH,
                                  params={"tab": "servers"})).text
    assert "Host A Server" not in after
    assert "None of the servers" in after or "your roles manage" in after, \
        "an emptied scope must render the scope-specific copy, not a blank table"


# ------------------------------------------------------- the same view for a Discord-scoped identity

@pytest.mark.asyncio
async def test_a_local_declared_scope_and_a_discord_member_scope_render_the_same_view(config_dir):
    """The declared local scope and the Discord member's role walk produce the SAME ``Scope`` value,
    so the render path cannot tell them apart - which is what makes the hoster view one feature."""
    app = build(config_dir, fleet())
 # the scope the Discord resolver produces for a member holding the managed_by role "Host A"
    discord_identity = DiscordOAuthBackend.__new__(DiscordOAuthBackend)._identity(
        "42", _member((1, "@everyone"), (7, "Host A")), {})
    permissions.set_scope_resolver(lambda request: discord_identity.scope)
    try:
        response = await get(app, "/fleet", "hoster")
    finally:
        permissions.set_scope_resolver(None)
    assert response.status_code == 200
    assert "Host A Server" in response.text
    assert_no_out_of_scope(response.text, "the Discord-scoped render")


# ------------------------------------------------------- Admin and break-glass are unscoped

@pytest.mark.asyncio
async def test_an_admin_sees_the_whole_cluster(config_dir):
    """Every row, the cluster-wide pills and the cluster-wide banner."""
    app = build(config_dir, fleet())
    html = (await get(app, "/", "specialk")).text
    assert "Host A Server" in html and "Host B Server" in html
    assert "1/2 servers up" in html and "1/2 nodes online" in html
    assert "2 players online" in html
    assert "dcs-b-01" in html, "the cluster banner names the offline node"
    assert "server(s) down: Host B Server" in html
    assert "server(s) paused: Host A Server" in html


@pytest.mark.asyncio
async def test_an_admins_view_is_the_source_ITSELF_untouched(config_dir):
    """For an unscoped identity the wrapper is not applied at all, so the Admin reads the very object the
    process handed over."""
    cluster = fleet()
    app = build(config_dir, cluster)
    source = cluster.source()
    assert readmodels.scoped_source(source, Scope.everything()) is source
    assert readmodels.scoped_source(source, Scope.restricted({"Host A"})) is not source


def test_break_glass_is_unscoped(config_dir):
    """Break-glass is unscoped -- a scoped break-glass would defeat its purpose."""
    scope = _breakglass_scope(config_dir)
    assert scope.unrestricted is True
    assert scope.allows(["Host B"]) is True


def _breakglass_scope(config_dir):
    """The scope the break-glass backend produces, through the REAL manager.

    Built by asking the installed manager about a break-glass session, so this is the production
    rule rather than a re-statement of it.
    """
    from services.webservice.auth.breakglass import BreakglassAuthBackend
    from services.webservice.auth import AuthManager
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config={"session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME},
                          "auth": {"public_base_url": "http://localhost:9876",
                                   "breakglass": {"enabled": True, "username": "emergency",
                                                  "password_hash": _hash()}}})
    backend = app.state.webui_auth.backend("breakglass")
    assert isinstance(backend, BreakglassAuthBackend)
    request = _request(app, session={"webui_identity": {"backend": "breakglass",
                                                       "subject": "emergency"}})
    return app.state.webui_auth.scope_for(request)


# ------------------------------------------------------- the empty scope: honest copy, never the cluster

@pytest.mark.asyncio
async def test_a_scope_matching_no_server_renders_the_honest_copy(config_dir):
    app = build(config_dir, fleet(), users=[
        {"username": "outsider", "password_hash": _hash(), "roles": ["DCS Admin"],
         "scope": ["Host Q"]}])
    html = unescape((await get(app, "/", "outsider")).text)
    assert readmodels.SCOPED_EMPTY_MESSAGE in html
    assert_no_out_of_scope(html, "the empty-scoped dashboard")
    assert "Host A Server" not in html, "never the full cluster"
    assert "0/0 servers up" in html
    assert readmodels.NO_SERVERS_MESSAGE not in html, \
        "'no servers are registered on this cluster' would misdescribe their situation"


@pytest.mark.asyncio
async def test_an_unresolvable_scope_renders_nothing_and_says_so(config_dir):
    """FAIL CLOSED: a signed-in identity whose scope cannot be resolved gets an EMPTY view with the honest
    copy, never the full cluster."""
    app = build(config_dir, fleet())
    permissions.set_scope_resolver(lambda request: Scope.unresolved())
    try:
        response = await get(app, "/fleet", "hoster")
        dashboard = unescape((await get(app, "/", "hoster")).text)
    finally:
        permissions.set_scope_resolver(None)
    assert response.status_code == 200
    assert readmodels.SCOPED_EMPTY_MESSAGE in unescape(response.text)
    assert_no_out_of_scope(response.text, "the fail-closed page")
    assert "Host A Server" not in response.text, "fail closed must not fall back to the full cluster"
    assert readmodels.SCOPED_EMPTY_MESSAGE in dashboard


# ------------------------------------------------------- the seam: nothing bypasses the scope

def test_the_console_source_is_resolved_only_through_request_source():
    """A source-level pin for the ONE plug point.

    Every page reads the source through ``pages.dashboard.request_source`` (which applies the
    scope). A future page that read the seam itself — ``readmodels.resolve_source`` (the raw
    in-process source) or ``readmodels.console_source`` (the provider seam, still scope-free) —
    would render an UNSCOPED view and no rendered-output test would notice, so the seam is pinned
    structurally too: ``request_source`` is the ONE function allowed to bend it.
    """
    offenders: list[tuple[str, str]] = []
    for path in sorted(PAGES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function, node in _iter_functions(tree):
            for child in ast.walk(node):
                if (isinstance(child, ast.Call)
                        and _called_name(child) in ("resolve_source", "console_source")):
                    offenders.append((path.name, function))
    assert offenders == [("dashboard.py", "request_source")], (
        "the console's source must be resolved ONLY inside request_source, which applies the scope; "
        f"found {offenders}")


def test_console_source_is_the_only_caller_of_resolve_source():
    """The other half of the seam: ``readmodels.source.console_source`` is where it bends.

    It is the one place that decides where the console's data comes from, and both the pages (via
    ``request_source``) and the identity layer (the manager check) read it — so a SECOND caller of
    the raw ``resolve_source`` (a page convenience, say) would be a scope-free door nobody watches.
    """
    source = PAGES_DIR.parent / "readmodels" / "source.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    callers = sorted({name for name, node in _iter_functions(tree)
                      for child in ast.walk(node)
                      if isinstance(child, ast.Call) and _called_name(child) == "resolve_source"})
    assert callers == ["console_source"], (
        "readmodels.source.resolve_source must be called ONLY by console_source, which the pages "
        f"reach through request_source; found {callers}")


def _iter_functions(tree):
    """Yield ``(name, node)`` for every function defined in *tree*, at any nesting depth."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def test_no_page_passes_an_unscoped_source_to_the_read_models():
    """The second half of the seam: ``readmodels.overview(...)`` must be handed a scoped source.

    Every call site passes ``request_source(request)`` (directly or through ``source_for_viewer``);
    a call with anything else would build an unscoped ``Overview`` while looking perfectly normal.
    """
    allowed = {"source", "state"}          # a read model's own parameter, not a page's call
    for path in sorted(PAGES_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"readmodels\.overview\((.*?)\)", source, re.S):
            argument = match.group(1)
            first = argument.split(",")[0].strip()
            assert "request_source" in first or first in allowed, (
                f"{path.name}: readmodels.overview is handed {first!r}, which is not the scoped "
                "request source")
