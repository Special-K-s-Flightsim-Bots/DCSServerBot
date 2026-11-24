"""THE ROW NAME IS THE ONLY DOOR — no Servers or Nodes list row carries a settings cogwheel.

A list row's NAME opens that entity's own page (a server's default tab, a node's ``/nodes/<node>``).
The rule pinned here: no ``act-btn cfg`` link is drawn beside the strip of row ACTIONS, because a
second glyph pointing at the same page is a navigation duplicate wearing an action's clothes — and
gating such a glyph per origin is what lets the Dashboard disagree with both lists.

Pins, through the real app with the real local login backend:

* NO cogwheel on ``/servers``, ``/nodes``, or the Dashboard's Servers and Nodes tabs — for an Admin, a
  scoped manager, and an identity that cannot open the target page;
* the row NAME still links into the page for a caller who may open it, and stays plain text for one
  who may not;
* the pages behind those links are unchanged: the Configuration tab still renders its form, the node
  page the node's own block, and it mutates ONLY that node on save;
* the OLD ``/nodes/<node>/settings`` URL is kept as a 307 redirect to the node page.
"""
from __future__ import annotations

import re
import types

import httpx
import pytest

from conftest import create_app, install_shell

from core import Status
from services.webservice import readmodels
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import actions as actions_page
from services.webservice.pages import node_settings as node_settings_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import server_detail as server_detail_page
from services.webservice.pages import servers as servers_page

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
  DCS:
    - member1
"""

USERS = (
    {"username": "specialk", "roles": ["Admin"]},                          # Admin: unscoped by rule
    {"username": "mod1", "roles": ["DCS Admin"], "scope": ["Host A"]},     # DCS Admin, in scope
    {"username": "hoster", "roles": ["DCS"], "scope": ["Host A"]},         # a pure MANAGER
    {"username": "hoster-b", "roles": ["DCS"], "scope": ["Host B"]},       # manager, other server
    {"username": "plain", "roles": ["DCS"]},                              # no capability at all
)

#: a valid ``nodes.yaml`` for the two nodes the cluster below knows, so the settings page has a block
#: to render (and the schema validates the whole file on a save).
NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  nodestats: true
  DCS:
    installation: /opt/dcs-a
    minimized: true
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  nodestats: false
  DCS:
    installation: /opt/dcs-b
    minimized: true
"""


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(users=None) -> dict:
    return {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True,
                           "users": users if users is not None else [dict(u, password_hash=_hash())
                                                                    for u in USERS]}},
    }


@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """The schema validator's ``unique_port`` reads ``COMMAND_LINE_ARGS.node``.

    The test interpreter's program is not one of the bot's own entry points, so the bot's parser takes
    its defaults and the ``-n/--node`` attribute never exists; pykwalify loads
    ``core/utils/validators.py`` as a FRESH extension module, so the only patch that reaches it is the
    one on ``core.commandline`` itself. This is the SAME fixture ``test_webui_frontend_switch`` uses.
    """
    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------------- test doubles

def server(name, managed_by=None, *, status=Status.RUNNING, node_name="dcs-a-01",
           instance="Host-A-Instance"):
    srv = types.SimpleNamespace(
        name=name, display_name=name, status=status,
        node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=instance), port=10309,
        current_mission=None, players={}, is_remote=False,
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )
    return srv


def instance(name, server_obj=None):
    return types.SimpleNamespace(name=name, dcs_port=10309, webgui_port=8088, server=server_obj)


class Cluster:
    def __init__(self, servers=(), *, nodes=None, node=None, instances=()):
        self.servers = list(servers)
        self.instances = list(instances)
        self.nodes = dict(nodes or {})
        self.node = node

    def source(self):
        return readmodels.LiveSource(servers=tuple(self.servers), instances=tuple(self.instances),
                                     nodes=self.nodes, node=self.node, log_path=None)


def fleet():
    """SRS-1 (Host A, on dcs-a-01) and SRS-B (Host B, on dcs-b-01) — the scope's two sides."""
    a = server("SRS-1", ["Host A"], node_name="dcs-a-01", instance="Host-A-Instance")
    b = server("SRS-B", ["Host B"], node_name="dcs-b-01", instance="Host-B-Instance")
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    node_b = types.SimpleNamespace(name="dcs-b-01", master=False,
                                   instances={"Host-B-Instance": instance("Host-B-Instance", b)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a, "dcs-b-01": node_b},
                   node=node_a, instances=[instance("Host-A-Instance", a),
                                           instance("Host-B-Instance", b)])


def build(config_dir, cluster, *, plugins=("mission", "admin"), users=None):
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir),
                                                  plugins=list(plugins)),
                  config=_config(users))
    app.state.webui_source_provider = cluster.source
    return app


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="https://testserver")


async def sign_in(http, username):
    response = await http.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', response.text).group(1)
    reply = await http.post(auth_routes.LOGIN_PATH,
                            data={"username": username, "password": PASSWORD, "_csrf_token": token})
    assert reply.status_code in (200, 303), f"login for {username} failed: {reply.status_code}"
    return reply


async def get(http, path):
    response = await http.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code}"
    return response.text


def settings_hrefs(html: str) -> set[str]:
    """Every settings-cogwheel link on the page (the ``act-btn cfg`` anchor's href).

    A list row must yield NONE: the row's NAME is the only door into the entity's page, so a cogwheel
    pointing at that same page is exactly the duplicate this file rules out.
    """
    return set(re.findall(r'<a class="act-btn cfg" href="([^"]+)"', html))


# ======================================================== SERVERS: the name, and no cogwheel beside it

@pytest.mark.asyncio
async def test_the_servers_page_carries_no_cogwheel_and_the_name_is_the_door(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await get(http, servers_page.SERVERS_PATH)
    # NO cogwheel and NO link into the Configuration tab on a list row...
    assert settings_hrefs(html) == set(), settings_hrefs(html)
    assert "?tab=configuration" not in html, "a row still points at the Configuration tab"
 # ...the NAME is the door...
    assert '<a class="srv-name" href="/fleet/SRS-1">SRS-1</a>' in html
    assert '<a class="srv-name" href="/fleet/SRS-B">SRS-B</a>' in html
 # ...and behind it the EXISTING tab still renders its own form
    async with client(app) as http2:
        await sign_in(http2, "specialk")
        target = await get(http2, "/fleet/SRS-1?tab=configuration")
    assert actions_page.SERVER_CONFIG_ACTIONS[0].path in target


@pytest.mark.asyncio
async def test_a_scoped_manager_gets_the_name_link_to_their_own_server_and_no_cogwheel(config_dir):
    """The Configuration tab admits a MANAGER (``servers.config`` carries a scope grant), so their
    server's row carries the NAME link and no cogwheel — and only their server is in their view."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster")              # pure manager of Host A
        html = await get(http, servers_page.SERVERS_PATH)
        followed = await get(http, "/fleet/SRS-1?tab=configuration")
    assert settings_hrefs(html) == set(), settings_hrefs(html)
    assert "?tab=configuration" not in html, "a row still points at the Configuration tab"
    assert '<a class="srv-name" href="/fleet/SRS-1">SRS-1</a>' in html
    assert "SRS-B" not in html and "/fleet/SRS-B" not in html
    assert actions_page.SERVER_CONFIG_ACTIONS[0].path in followed

    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster-b")            # the mirror: manager of Host B
        other = await get(http, servers_page.SERVERS_PATH)
    assert settings_hrefs(other) == set(), settings_hrefs(other)
    assert '<a class="srv-name" href="/fleet/SRS-B">SRS-B</a>' in other


@pytest.mark.asyncio
async def test_a_dcs_admin_is_offered_no_cogwheel_and_the_tab_refuses_them(config_dir):
    """A DCS Admin may READ the Servers page but holds no ``servers.config`` (its scope is unscoped),
    so no cogwheel is built — and the crafted target answers the console's own refusal."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await get(http, servers_page.SERVERS_PATH)
        refused = await http.get("/fleet/SRS-1?tab=configuration")
    assert settings_hrefs(html) == set(), settings_hrefs(html)
    assert "?tab=configuration" not in html, "a cogwheel to a tab the viewer cannot open was rendered"
    assert refused.status_code == 403, refused.status_code


@pytest.mark.asyncio
async def test_the_configuration_tab_renders_unchanged_for_an_untouched_server(config_dir):
    """The tab the name's page opens is its own editor: it still renders its own form, reached from
    the page's tab strip as it always was."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await get(http, "/fleet/SRS-1?tab=configuration")
    assert actions_page.SERVER_CONFIG_ACTIONS[0].path in html, "the config form is not rendered"
    assert 'name="f.name"' in html or 'name="name"' in html
 # the tab strip still offers the tab it always did
    assert "?tab=configuration" in html


# ===================================================== NODES: the name, and no cogwheel beside it

@pytest.mark.asyncio
async def test_the_nodes_page_carries_no_cogwheel_and_the_name_is_the_door(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await get(http, nodes_page.NODES_PATH)
        target = await get(http, "/nodes/dcs-a-01")
 # the OLD per-node settings URL is kept as a redirect, so a bookmark still lands useful
        redirected = await http.get("/nodes/dcs-a-01/settings", follow_redirects=False)
    assert settings_hrefs(html) == set(), settings_hrefs(html)
    assert 'class="act-btn cfg"' not in html, "a node row still draws a settings cogwheel"
    # each row's NAME carries the page's own URL — the row's only door
    assert '<a class="srv-name" href="/nodes/dcs-a-01"><b>dcs-a-01</b></a>' in html
    assert '<a class="srv-name" href="/nodes/dcs-b-01"><b>dcs-b-01</b></a>' in html
 # the target IS the node page, rendering THAT node's block, generated from the node schema
    assert 'name="f.dcs-a-01.listen_address"' in target
    assert node_settings_page.NODES_YAML_NAME in target
    assert redirected.status_code == 307, redirected.status_code
    assert redirected.headers["location"] == "/nodes/dcs-a-01"


@pytest.mark.asyncio
async def test_a_dcs_admin_gets_neither_cogwheel_nor_name_link_and_the_page_refuses_them(config_dir):
    """A DCS Admin may READ the Nodes list but holds no ``nodes.settings`` (Admin only), so the row
    offers NO door at all — no cogwheel, and the name stays plain text — and the crafted target
    answers the console's own refusal."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await get(http, nodes_page.NODES_PATH)
        refused = await http.get("/nodes/dcs-a-01/settings")
    assert settings_hrefs(html) == set(), settings_hrefs(html)
    assert 'href="/settings"' not in html, \
        "a cogwheel to a page the viewer cannot open was rendered"
    assert 'href="/nodes/dcs-a-01"' not in html, "a door the page refuses was rendered as a link"
    assert '<b>dcs-a-01</b>' in html, "the name is not rendered at all"
    assert refused.status_code == 403, refused.status_code


@pytest.mark.asyncio
async def test_a_manager_is_refused_the_node_settings_page(config_dir):
    """A node's settings are the machine's, not a server a manager's scope names: no scope grant."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster")
        page = await http.get(nodes_page.NODES_PATH)
        refused = await http.get("/nodes/dcs-a-01/settings")
    assert page.status_code == 200, "a manager reads the Nodes list through the scope"
    assert settings_hrefs(page.text) == set()
    assert refused.status_code == 403, refused.status_code


@pytest.mark.asyncio
async def test_the_node_page_is_reached_by_the_name_on_an_offline_node(config_dir):
    """An offline node has no reachable object, but its settings live in the MASTER's nodes.yaml, so
    its row keeps its NAME link and the node page still renders (never hidden-and-served)."""
    cluster = fleet()
    cluster.nodes["dcs-b-01"] = None          # the cluster knows it and cannot reach it
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await get(http, nodes_page.NODES_PATH)
        target = await get(http, "/nodes/dcs-b-01")
    assert '<a class="srv-name" href="/nodes/dcs-b-01"><b>dcs-b-01</b></a>' in html
    assert settings_hrefs(html) == set(), settings_hrefs(html)
    assert "offline" in html
    assert 'name="f.dcs-b-01.listen_address"' in target


# ==================================== the DASHBOARD's two tabs agree with both standalone lists

@pytest.mark.asyncio
async def test_the_dashboard_servers_and_nodes_tabs_carry_no_cogwheel(config_dir):
    """The Dashboard draws the SAME row records as the two list pages, so the three surfaces cannot
    disagree: no ``act-btn cfg`` on either tab, and the row NAME is the door on both."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        servers = await get(http, "/?tab=servers")
        nodes = await get(http, "/?tab=nodes")
    for label, html in (("servers", servers), ("nodes", nodes)):
        assert settings_hrefs(html) == set(), f"{label}: {settings_hrefs(html)}"
        assert 'class="act-btn cfg"' not in html, f"{label}: a cogwheel was drawn"
    assert "?tab=configuration" not in servers, "a server row still points at the Configuration tab"
    assert '<a class="srv-name" href="/fleet/SRS-1">SRS-1</a>' in servers
    assert '<a class="srv-name" href="/nodes/dcs-a-01"><b>dcs-a-01</b></a>' in nodes


# ============================================== the node settings SAVE mutates ONLY that node's block

def test_applying_a_node_form_mutates_only_that_nodes_block():
    """The scoped tree's own round trip, on a document held in memory (no file touched): a change to
    one node's field lands on that node and leaves the OTHER node's block alone."""
    from core.utils import configwrite
    from services.webservice import configgen

    document = configwrite.load_config_text(NODES_YAML)
    tree = node_settings_page.node_tree("dcs-a-01")
 # the page's field names ARE the document paths for this node
    names = {field.name for field in configgen.leaves(tree)}
    assert "dcs-a-01.listen_address" in names and "dcs-a-01.DCS.installation" in names

    configgen.apply_form(document, {"f.dcs-a-01.nodestats": "0"}, tree)
    assert document["dcs-a-01"]["nodestats"] is False
 # the other node is untouched, byte for byte, in its own block
    assert document["dcs-b-01"]["nodestats"] is False
    assert document["dcs-b-01"]["DCS"]["installation"] == "/opt/dcs-b"


def test_the_node_tree_carries_the_nodes_own_key_at_the_root():
    tree = node_settings_page.node_tree("dcs-a-01")
    assert len(tree.children) == 1
    block = tree.children[0]
    assert block.key == "dcs-a-01" and block.path == ("dcs-a-01",)
 # a nested field keeps the node token as its first path segment
    nested = next(child for child in block.children if child.key == "DCS")
    assert nested.path == ("dcs-a-01", "DCS")


@pytest.mark.asyncio
async def test_saving_the_node_page_lands_only_on_that_node(config_dir):
    """END TO END through the real route + the one write primitive: a change to one node's field is
    written, the OTHER node's block is byte-for-byte untouched, and the change is validated."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await get(http, "/nodes/dcs-a-01")
        csrf = re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)
        revision = re.search(r'name="_revision" value="([^"]*)"', page).group(1)
        reply = await http.post("/nodes/dcs-a-01",
                                data={"_csrf_token": csrf, "_revision": revision,
                                      "f.dcs-a-01.nodestats": "0"},
                                follow_redirects=False)
    assert reply.status_code == 303, reply.text
    text = (config_dir / "nodes.yaml").read_text(encoding="utf-8")
 # the changed node is written...
    assert "nodestats: false" in text
 #...and the other node's block is exactly what it was
    assert "dcs-b-01:\n  listen_address: 127.0.0.1\n  listen_port: 10043\n  nodestats: false\n" in text


@pytest.mark.asyncio
async def test_an_untouched_node_page_save_is_byte_identical(config_dir):
    """The no-op rule holds on the node page too: posting no field writes NOTHING (the file keeps its
    exact bytes), and the page says so."""
    before = (config_dir / "nodes.yaml").read_bytes()
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await get(http, "/nodes/dcs-a-01")
        csrf = re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)
        revision = re.search(r'name="_revision" value="([^"]*)"', page).group(1)
        reply = await http.post("/nodes/dcs-a-01",
                                data={"_csrf_token": csrf, "_revision": revision},
                                follow_redirects=False)
        after = await get(http, "/nodes/dcs-a-01")
    assert reply.status_code == 303, reply.text
    assert (config_dir / "nodes.yaml").read_bytes() == before, "an untouched save rewrote the file"
    assert "Nothing changed" in after
