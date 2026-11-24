"""The dashboard: its acceptance criteria plus the no-write-handler and purity pins.

A signed-in ``DCS Admin`` gets 200 on ``/``; a user whose only role is ``GameMaster`` gets 403 and no
nav item. The page renders with no bot at all (``bot = None``) and from a bot stub that RAISES on any
attribute other than the two the read models may read. Empty states render their own sentence with an
empty element absent. The read models are pure functions of a stub source, and
``test_no_read_model_imports_core_discord_or_http`` pins every readmodels module free of absolute
``core``/``discord``/HTTP imports. No request touches Discord, a database or the network."""
from __future__ import annotations

import ast
import enum
import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client, create_app, install_shell, permissions
from services.webservice import readmodels
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import dashboard as dashboard_page

REPO_ROOT = Path(__file__).resolve().parent.parent
READMODELS_DIR = REPO_ROOT / "services" / "webservice" / "readmodels"
DASHBOARD_PY = REPO_ROOT / "services" / "webservice" / "pages" / "dashboard.py"
DASHBOARD_HTML = REPO_ROOT / "services" / "webservice" / "templates" / "dashboard.html"

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"
BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
"""


# ------------------------------------------------------------------------------ test doubles

class Status(enum.Enum):
    """The shape of ``core.data.const.Status`` where it matters: an enum whose ``.value`` is the
    word the read model maps. Declared here so this file needs no ``core`` import."""
    RUNNING = "Running"
    PAUSED = "Paused"
    STOPPED = "Stopped"
    SHUTDOWN = "Shutdown"
    LOADING = "Loading"


def player(name="Maverick", ucid=None, active=True, **kwargs):
 # the ucid defaults to the name: two players built with the default ucid would collapse into
 # one entry in the server's {ucid: player} registry and quietly halve every player figure
    return types.SimpleNamespace(name=name, ucid=ucid or name.lower(), active=active, **kwargs)


def server(name="Training Caucasus", *, status=Status.RUNNING, node="dcs-master-01",
           instance="Training", port=10309, mission="Caucasus_Training", mission_time=2472,
           players=()):
    mission_obj = (types.SimpleNamespace(name=mission, mission_time=mission_time)
                   if mission else None)
    return types.SimpleNamespace(
        name=name, status=status, node=types.SimpleNamespace(name=node),
        instance=types.SimpleNamespace(name=instance), port=port,
        current_mission=mission_obj, players={p.ucid: p for p in players}, is_remote=False)


def instance(name="Training", *, dcs_port=10309, webgui_port=8088, server_obj=None):
    return types.SimpleNamespace(name=name, dcs_port=dcs_port, webgui_port=webgui_port,
                                 server=server_obj)


def node(name="dcs-master-01", *, master=True, instances=()):
    return types.SimpleNamespace(name=name, master=master, instances=dict(instances),
                                 all_nodes=None)


class NoDiscordBot:
    """A bot stub that FAILS LOUDLY if the page reads anything it may not.

    The read models are allowed exactly three attributes off the bot: the in-process server
    registry and the node it belongs to. Everything else — every Discord method, the guilds, the
    roles, the caches — raises, so "no Discord call on render" is proved by the page rendering
    without tripping this, rather than by reading the source and hoping.
    """

    ALLOWED = {"servers", "node"}

    def __init__(self, servers=(), node=None):
        self.servers = {s.name: s for s in servers}
        self.node = node

    def __getattr__(self, name):
        raise AssertionError(f"the dashboard touched bot.{name} - a Discord attribute on render")


# ------------------------------------------------------------------------------ fixtures

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(users=None) -> dict:
    return {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": users if users is not None else [
                     {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]}]}},
    }


def _build(config_dir, users=None):
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True,
                                                             config_dir=str(config_dir)),
                              config=_config(users))
    return app, registrar


async def _login(client, username: str = "specialk", password: str = PASSWORD):
    response = await client.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', response.text).group(1)
    return await client.post(auth_routes.LOGIN_PATH,
                             data={"username": username, "password": password,
                                   "_csrf_token": token})


def _source(*, servers=(), instances=(), nodes=None, node_obj=None, log_path=None):
 # mirror resolve_source: with no explicit instances, EVERY node's own registry feeds the
 # source — not just the master's — so a stub built as node(instances={...}) behaves
 # like the live process does, and a two-node stub must pass the remote node in ``nodes``.
    node_obj = node_obj or types.SimpleNamespace(name="dcs-master-01", master=True)
    if nodes is None:
        nodes = {"dcs-master-01": node_obj}
    if not instances:
        instances = tuple(instance
                          for member in nodes.values() if member is not None
                          for instance in (getattr(member, "instances", None) or {}).values())
    return readmodels.LiveSource(servers=servers, instances=instances,
                                 nodes=nodes,
                                 node=node_obj, log_path=log_path)


# ------------------------------------------------------- acceptance 1: the capability

@pytest.mark.asyncio
async def test_a_staff_role_sees_the_page_and_a_role_without_it_gets_403(config_dir,
                                                                       music_plugin_loaded):
    """200 + a rendered link for the role the page names; 403 + no offered item for one it does not.
    The two users differ ONLY in their declared roles, so the answer is the capability and not a
    missing route or a broken login."""
    app, registrar = _build(config_dir, users=[
        {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]},
        {"username": "nobody", "password_hash": _hash(), "roles": ["GameMaster"]},
    ])
    app.state.webui_source_provider = lambda: readmodels.EmptySource()

    async with asgi_client(app) as client:
        assert (await _login(client, "specialk")).status_code == 303
        response = await client.get(dashboard_page.DASHBOARD_PATH)
        assert response.status_code == 200
        assert re.search(r'href="/"[^>]*>\s*<span class="ic">', response.text), \
            "the sidebar of the page the viewer may open must link the page"
        assert dashboard_page.DASHBOARD_TITLE in response.text

    async with asgi_client(app) as client:
        assert (await _login(client, "nobody")).status_code == 303
        assert (await client.get(dashboard_page.DASHBOARD_PATH)).status_code == 403, \
            "a role the page does not name must be refused by the gate"

 # the nav that role receives: computed with the same predicate the gate uses
    assert dashboard_page.nav_groups(registrar, {"GameMaster"}, current="/") == []
    links = [link for group in dashboard_page.nav_groups(registrar, {"Admin"}, current="/")
             for link in group["links"]]
 # the main menu is the nine items (Dashboard, Servers, Nodes, Statistics, Players, Music, Bans,
 # Presets, Settings), in registration order. Enumerated rather than counted: a nav that quietly
 # LOST an entry would pass a length check.
    # THE RAIL'S OWN ORDER: group by group (Operations, Media, Cluster, Data, Administration), and
    # within a group the order the shell registers the pages in.
    assert [link["url"] for link in links] == [
        dashboard_page.DASHBOARD_PATH, "/fleet", "/players", "/bans",
        "/music", "/nodes", "/audit", "/statistics", "/presets", "/access", "/settings",
        "/settings/site"]
    assert [link["active"] for link in links] == [True] + [False] * 11, \
        "the rail marks the page in force and leaves every sibling unmarked"
    assert "/logs" not in [link["url"] for link in links], \
        "the Logs page must have no menu entry (the log lives on the dashboard)"

 # and a role that may open the dashboard (DCS Admin) is still offered no Logs entry — the same
 # predicate, so the sidebar cannot advertise a door the gate refuses.
    dcs_admin = [link["url"] for group in
                 dashboard_page.nav_groups(registrar, {"DCS Admin"}, current="/")
                 for link in group["links"]]
    assert "/logs" not in dcs_admin and dashboard_page.DASHBOARD_PATH in dcs_admin


@pytest.mark.asyncio
async def test_the_dashboard_route_is_get_only(config_dir):
    """NO WRITE HANDLER in this module, pinned at the route and at the source.

    THIS module answers GET and owns no write handler. The writes a server row performs are
    ``services/webservice/pages/actions.py``'s POST routes (with the session's CSRF token and the target
    in the body), and the dashboard renders their CONTROLS as data. A module that says "no mutations
    here" must be checkable, not remembered."""
    _app, registrar = _build(config_dir)
    routes = [route for route in registrar.routes
              if getattr(route, "path", None) == dashboard_page.DASHBOARD_PATH]
    assert routes, "floor: the dashboard route exists in the registrar"
    for route in routes:
        assert set(route.methods or ()) == {"GET"}, f"the dashboard answers {route.methods}"
    source = DASHBOARD_PY.read_text(encoding="utf-8")
    for verb in ("@router.post", "@router.put", "@router.patch", "@router.delete"):
        assert verb not in source


# ------------------------------------------ acceptance 2: no bot, and no Discord either

@pytest.mark.asyncio
async def test_the_page_renders_without_a_bot_at_all(config_dir):
    """`bot = None`, the early start / post-takeover case: the handler resolves its source with no
    bot and must still answer 200 with the empty-state wording."""
    app, _registrar = _build(config_dir)
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.get("/")
    assert response.status_code == 200
    assert readmodels.NO_LIVE_REASON in response.text
    assert readmodels.NO_SERVERS_MESSAGE in response.text
    assert readmodels.NO_PLAYERS_MESSAGE not in response.text, \
        "one list is visible at a time: the players sentence belongs to the players tab"
    async with asgi_client(app) as client:
        await _login(client)
        players = await client.get(f"{dashboard_page.DASHBOARD_PATH}?tab=players")
    assert players.status_code == 200
    assert readmodels.NO_PLAYERS_MESSAGE in players.text


@pytest.mark.asyncio
async def test_the_page_renders_from_a_bot_that_has_no_discord(config_dir):
    """The headless install (``no_discord: true``): the source is resolved from a bot object whose
    every unlisted attribute raises. The page renders, so it never asked Discord for anything."""
    live_node = node(name="dcs-master-01", instances={"Training": instance()})
    live_node.all_nodes = {"dcs-master-01": live_node}
    stub_bot = NoDiscordBot(servers=[server(players=[player()])], node=live_node)

    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: readmodels.resolve_source(node=live_node,
                                                                      bot=stub_bot)
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.get("/")
    assert response.status_code == 200
 # the live figures came through, and no Discord attribute was touched to get them
 # (the players are on the PLAYERS tab — one list is visible at a time)
    assert "Training Caucasus" in response.text
    async with asgi_client(app) as client:
        await _login(client)
        players = await client.get("/?tab=players")
    assert players.status_code == 200
    assert "Maverick" in players.text
    assert readmodels.NO_SERVERS_MESSAGE not in response.text


# ------------------------------------------------------- acceptance 3: the empty states

@pytest.mark.asyncio
async def test_empty_states_render_their_own_message(config_dir, tmp_path):
    """0 servers, 0 players (with servers present), a node with no instances and a missing log file
    each render THEIR OWN sentence - and no empty container stands in for one.

    ONE LIST IS VISIBLE AT A TIME (the tab strip), so each empty state is read on ITS OWN TAB - the
    page never grows with the fleet, and a tab's sentence is the answer for that tab.
    """
    app, _registrar = _build(config_dir)

    async def _tabs() -> dict[str, str]:
        async with asgi_client(app) as client:
            await _login(client)
            pages = {}
            for tab in ("servers", "nodes", "instances", "players"):
                response = await client.get(f"/?tab={tab}")
                assert response.status_code == 200, (tab, response.status_code)
                pages[tab] = response.text
            return pages

 # (a) nothing at all but a node
    app.state.webui_source_provider = lambda: _source(
        node_obj=node(instances={}), log_path=tmp_path / "does-not-exist.log")
    pages = await _tabs()
    assert readmodels.NO_SERVERS_MESSAGE in pages["servers"]
    assert readmodels.NO_INSTANCES_MESSAGE in pages["instances"]
    assert readmodels.NO_PLAYERS_MESSAGE in pages["players"]
    assert readmodels.NO_NODE_INSTANCES_MESSAGE in pages["nodes"], \
        "a node with no instances says so in its own words"
    assert readmodels.LOG_MISSING_MESSAGE in pages["servers"]
    for tab, html in pages.items():
 # the empty state is the sentence, not an empty frame around nothing
        assert re.search(r"<tbody>\s*</tbody>", html) is None, tab
        assert '<p class="empty"></p>' not in html, tab

 # (a2) no node at all: the node tab has its OWN sentence, distinct from the per-node one
    app.state.webui_source_provider = lambda: _source(
        nodes={}, node_obj=node(instances={}), log_path=tmp_path / "does-not-exist.log")
    no_nodes = (await _tabs())["nodes"]
    assert readmodels.NO_NODES_MESSAGE in no_nodes
    assert readmodels.NO_NODE_INSTANCES_MESSAGE not in no_nodes

 # (b) one server, nobody online: the PLAYERS message shows and the servers message does not,
 # so the two empty states are told apart rather than one standing in for both
    app.state.webui_source_provider = lambda: _source(
        servers=[server(players=[])],
        node_obj=node(instances={"Training": instance()}),
        log_path=tmp_path / "does-not-exist.log")
    pages = await _tabs()
    assert readmodels.NO_PLAYERS_MESSAGE in pages["players"]
    assert readmodels.NO_SERVERS_MESSAGE not in pages["servers"]
    assert readmodels.NO_NODE_INSTANCES_MESSAGE not in pages["nodes"], \
        "the node HAS an instance, so the message must be gone"


# ------------------------------------------------ acceptance 4: the read models are pure

def _stub_source():
    live = server(name="DCS Syria Campaign", status=Status.PAUSED, players=[player("Wraith",
                                                                                   ucid="f9e8")])
    stopped = server(name="Sunday Ops", status=Status.STOPPED, node="dcs-node-02",
                     instance="SundayOps", port=10311, mission=None, players=())
    return _source(servers=[live, stopped],
                   instances=[instance("Training", server_obj=live)],
                   nodes={"dcs-master-01": node(instances={"Training": instance()}),
                          "dcs-node-02": None},
                   node_obj=node(instances={"Training": instance()}))


def test_the_read_models_are_pure_functions_of_a_stub_source():
    """No HTTP, no Discord: every read model is called with a plain object and returns records."""
    source = _stub_source()

    views = readmodels.servers(source)
    assert [view.name for view in views] == ["DCS Syria Campaign", "Sunday Ops"]
    assert views[0].status.word == "PAUSED" and views[0].status.dot == "pause"
    assert views[1].status.word == "STOPPED" and views[1].status.dot == "stop"
    assert views[0].port == 10309 and views[0].active_players == 1

    players = readmodels.players_online(source)
    assert [(p.name, p.server_name) for p in players] == [("Wraith", "DCS Syria Campaign")]

    assert [i.name for i in readmodels.instances(source)] == ["Training"]
    nodes = {view.name: view for view in readmodels.nodes(source)}
    assert nodes["dcs-master-01"].online is True and nodes["dcs-master-01"].servers == 1
    assert nodes["dcs-node-02"].online is False and nodes["dcs-node-02"].servers == 1
    assert readmodels.overview(source).server_count == 2


def test_an_empty_source_yields_messages_not_exceptions():
    """The same functions against an ``EmptySource``: empty collections and the reason, never an
    exception and never ``None``."""
    source = readmodels.EmptySource()
    for read in (readmodels.servers, readmodels.players_online, readmodels.instances,
                 readmodels.nodes):
        assert read(source) == []
    state = readmodels.overview(source)
    assert state.status.live is False and state.status.reason == readmodels.NO_LIVE_REASON
    assert state.log.available is False and state.log.message == readmodels.LOG_MISSING_MESSAGE


def test_no_read_model_imports_core_discord_or_http():
    """The purity pin, structurally: no module under ``readmodels/`` carries an ABSOLUTE
    ``core``/``discord``/``httpx``/``aiohttp``/``psycopg``/``requests`` import at module level — no
    HTTP or Discord client and not the bot's core may be pulled in while rendering.

    Careful with the claim: this is NOT "the read models are core-free". The package reaches
    ``core`` through the RELATIVE ``from ..scope import server_managed_by``, which the absolute-name
    check below cannot see (a relative import's module string starts with a dot), and that is
    deliberate — the console consumes the shared ``managed_by`` rule. See the readmodels package
    docstring and ``tests/test_layering_direction.py``."""
    forbidden = {"core", "discord", "httpx", "aiohttp", "psycopg", "requests"}
    for path in sorted(READMODELS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for item in tree.body:                       # module level only: inside a def is the seam
            if isinstance(item, ast.Import):
                names = {alias.name.split(".")[0] for alias in item.names}
            elif isinstance(item, ast.ImportFrom):
                names = {(item.module or "").split(".")[0]}
            else:
                continue
            assert not (names & forbidden), f"{path.name} imports {names & forbidden} at import time"
 # and the one place that needs `core` does it lazily, inside the function
    source = (READMODELS_DIR / "source.py").read_text(encoding="utf-8")
    assert "from core import" in source and "def resolve_source" in source


def test_the_log_tail_reads_a_real_file_and_reports_what_it_finds(tmp_path):
    """The one read model with I/O: a real file on disk, read from the back, bounded, read-only.

    ``level="all"`` is passed for the mixed-level read: the DEFAULT is INFO and up (DEBUG hidden),
    which is pinned separately below and in ``tests/test_webui_log_panel.py``."""
    log = tmp_path / "dcssb-dcs-master-01.log"
    log.write_text("".join(
        f"2026-09-24 21:3{i}:12.{i:03d} {level}\tmessage {i}\n"
        for i, level in enumerate(["INFO", "WARNING", "ERROR", "DEBUG"])), encoding="utf-8")
    tail = readmodels.tail(log, lines=3, level="all")
    assert tail.available is True and tail.path == str(log)
    assert [line.level for line in tail.lines] == ["WARNING", "ERROR", "DEBUG"]
    assert [line.css for line in tail.lines] == ["w", "e", "d"]
    assert tail.lines[0].text == "message 1"
    assert tail.lines[0].time == "2026-09-24 21:31:12.001"

 # the DEFAULT filter drops the DEBUG line and keeps the three above it
    default = readmodels.tail(log)
    assert [line.level for line in default.lines] == ["INFO", "WARNING", "ERROR"]
    assert default.level == "info"

    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    assert readmodels.tail(empty).message == readmodels.LOG_EMPTY_MESSAGE
    assert readmodels.tail(tmp_path / "absent.log").message == readmodels.LOG_MISSING_MESSAGE
    assert readmodels.tail(None).message == readmodels.LOG_MISSING_MESSAGE


# ------------------------------- no hand-typed figures; the status vocabulary; the log block

@pytest.mark.asyncio
async def test_every_figure_on_the_page_moves_with_the_data(config_dir):
    """The rendered numbers are the Overview's numbers: change the source, the page changes. A
    figure typed into the template would stay put."""
    app, _registrar = _build(config_dir)
    first = _source(servers=[server(name="A", players=[player("P1"), player("P2")])],
                    node_obj=node(instances={"Training": instance()}))
    app.state.webui_source_provider = lambda: first
    async with asgi_client(app) as client:
        await _login(client)
        html = (await client.get("/")).text
    state = readmodels.overview(first)
 # the TAB BADGES are the figures now (the three KPI tiles are gone: one fact is stated once).
 # The badge class carries the problem marker (`badge` / `badge bad`), so match the class loosely.
    assert re.search(rf'<span class="badge[^>]*">{state.server_count}</span>', html)
    assert re.search(rf'<span class="badge[^>]*">{state.player_count}</span>', html)
    assert "2 player(s)" in html and state.player_count == 2
    assert '<div class="v">' not in html, "the KPI tiles were removed with the tab badges"

    second = _source(servers=[server(name="A"), server(name="B", status=Status.SHUTDOWN)],
                     node_obj=node(instances={}))
    app.state.webui_source_provider = lambda: second
    async with asgi_client(app) as client:
        await _login(client)
        html2 = (await client.get("/")).text
    state2 = readmodels.overview(second)
    assert state2.server_count == 2 and state2.running_count == 1
    assert re.search(rf'<span class="badge[^>]*">{state2.server_count}</span>', html2)
    assert state2.player_count == 0
    assert re.search(r'<span class="badge[^>]*">0</span>', html2), \
        "the players badge follows the data too"


@pytest.mark.asyncio
async def test_the_status_vocabulary_is_the_mockups_one(config_dir):
    """RUNNING / PAUSED / STOPPED / SHUTDOWN, each as a word plus its dot class — including the two
    transient values the mockup does not name (LOADING gets its own word; SHUTTING_DOWN folds into
    SHUTDOWN). An unmapped value renders UNKNOWN, never a blank."""
    raw = types.SimpleNamespace
    expected = [
        (Status.RUNNING, ("RUNNING", "run")),
        (Status.PAUSED, ("PAUSED", "pause")),
        (Status.STOPPED, ("STOPPED", "stop")),
        (Status.SHUTDOWN, ("SHUTDOWN", "dead")),
        (Status.LOADING, ("LOADING", "pause")),
        (raw(value="Shutting down"), ("SHUTDOWN", "dead")),
        (raw(value="Unregistered"), ("SHUTDOWN", "dead")),
        (raw(value="Something New"), ("UNKNOWN", "stop")),
        (None, ("UNKNOWN", "stop")),
    ]
    for value, (word, dot) in expected:
        view = readmodels.status_view(value)
        assert (view.word, view.dot) == (word, dot), f"{value!r} rendered as {view}"

    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(servers=[
        server(name="Critical", status=Status.RUNNING),
        server(name="Rotorheads", status=Status.PAUSED),
        server(name="Sunday Ops", status=Status.STOPPED),
        server(name="Aggressors", status=Status.SHUTDOWN),
        server(name="Booting", status=Status.LOADING),
    ], node_obj=node(instances={}))
    async with asgi_client(app) as client:
        await _login(client)
        html = (await client.get("/")).text
    for word in ("RUNNING", "PAUSED", "STOPPED", "SHUTDOWN", "LOADING"):
        assert f'<span class="tag">{word}</span>' in html, f"{word} is missing from the page"
    assert '<span class="dot run"' in html and '<span class="dot dead"' in html


@pytest.mark.asyncio
async def test_the_log_block_shows_lines_or_its_own_message(config_dir, tmp_path):
    app, _registrar = _build(config_dir)
    log = tmp_path / "dcssb-dcs-master-01.log"
    log.write_text("2026-09-24 21:33:12.123 WARNING\tdcs-node-03: DCS update detected\n"
                   "2026-09-24 21:34:02.400 INFO\tplayer join Wraith\n", encoding="utf-8")
    app.state.webui_source_provider = lambda: _source(node_obj=node(instances={}), log_path=log)
    async with asgi_client(app) as client:
        await _login(client)
        html = (await client.get("/")).text
    assert "DCS update detected" in html
    assert '<span class="w">WARNING</span>' in html and '<span class="i">INFO</span>' in html
    assert readmodels.LOG_MISSING_MESSAGE not in html


def test_the_template_holds_no_figure_of_its_own():
    """The pin behind "every number has a named source": the page template contains no numeric
    literal outside markup-required digits (the doctype-free body has none), so a figure cannot be
    typed in where a read model belongs."""
    source = DASHBOARD_HTML.read_text(encoding="utf-8")
    body = source.split("{% extends \"base.html\" %}", 1)[-1]
    stray = re.findall(r"(?<![\w{.-])(\d+)(?![\w}])", body)
    assert stray == [], f"numeric literals in the dashboard template: {stray}"
