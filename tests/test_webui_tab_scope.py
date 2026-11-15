"""The dashboard's tab strip must obey the SAME role model as the pages it mirrors.

``/nodes`` and ``/instances`` deliberately refuse a member who is not a cluster role, but such a
member could read the same two tables from ``/?tab=nodes`` and ``/?tab=instances``. A page that
refuses data must not be reachable by adding a parameter, so the strip AND the tab's table are
filtered with the SAME predicate the standalone pages declare (their capability, read out of their own
modules), so the two doors cannot drift apart again. A manager is admitted through its SCOPE rather
than a role name. Tab links CARRY the active query (so every link on the page states the same filter
the field shows), and a ``sort`` key is carried into the stream URL only when the rendered table
honours it. The pages are driven through the real app and the RENDERED markup is read."""
from __future__ import annotations

import re
import types
from html import unescape
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import asgi_client, create_app, install_shell, permissions
from services.webservice import readmodels
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import instances as instances_page
from services.webservice.pages import lists as lists_page
from services.webservice.pages import live as live_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import players as players_page
from services.webservice.pages import servers as servers_page
from services.webservice.readmodels import views as view_layer
from test_webui_dashboard import (BOT_YAML, _build, _hash, _login, _source, instance, node,
                                 player, server)

#: the tab keys the console's vocabulary is built from: the read models' table names PLUS the Load
#: tab, which is the one tab that renders no table (``dashboard_page.TABS`` states the exception).
ALL_TABS = dashboard_page.TABS
assert tuple(view_layer.TABLE_COLUMNS) == ALL_TABS[:-1], "the Load tab must stay last"


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def app_with(config_dir, roles, *, provider, username="staff"):
    """An app whose only local user holds *roles* and which reads *provider*."""
    app, registrar = _build(config_dir, users=[
        {"username": username, "password_hash": _hash(), "roles": list(roles)}])
    app.state.webui_source_provider = provider
    return app, registrar


def manager_app(config_dir, *, provider, username="staff"):
    """An app whose only local user is a MANAGER: no role at all, a DECLARED scope.

    The console's third kind of reader (see ``tests/test_webui_console_access.py``): a local account
    has no Discord member to compare against, so its declared scope IS the fact — and with the
    console's capabilities carrying ``scope_grants`` it is admitted without holding a single role.
    """
    app, registrar = _build(config_dir, users=[
        {"username": username, "password_hash": _hash(), "roles": [], "scope": ["Host A"]}])
    app.state.webui_source_provider = provider
    return app, registrar


def narrow(*capabilities, roles=("Admin",)):
    """Take *capabilities* away from a manager: BOTH axes, or the narrowing does not bite.

    The scope axis (``SCOPE_GRANTS``) and the role axis (``CAPABILITIES``) are independent, so
    narrowing only the role tuple would leave a manager admitted through its scope and the test would
    quietly prove nothing. The suite's ``clean_capability_state`` restores both tables after every
    test, so a narrowing never leaks into the next one.
    """
    for capability in capabilities:
        permissions.CAPABILITIES[capability] = tuple(roles)
        permissions.SCOPE_GRANTS[capability] = False


def fleet_source():
    """A small cluster: one server, one node carrying one instance, two players.

    The server is TAGGED with the manager's scope value, since ``managed_by`` is an allow-list and an
    untagged fixture would render empty for a manager.
    """
    fleet_server = server(players=[player("Viper-1", "aaaaaaaa"), player("Maverick", "bbbbbbbb")])
    fleet_server.locals = {"managed_by": ["Host A"]}
    return _source(servers=[fleet_server],
                   node_obj=node(instances={"Training": instance()}))


async def get(app, path, username="staff"):
    async with asgi_client(app) as client:
        await _login(client, username)
        response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return response.text


def tab_strip(html: str) -> str:
    """The ``<nav class="tabs">`` block, sliced out of the page."""
    start = html.index('<nav class="tabs"')
    return html[start:html.index("</nav>", start)]


def offered_tabs(html: str) -> list[str]:
    """The tab values the strip actually offers, in render order."""
    return re.findall(r'href="/\?[^"]*?tab=([a-z]+)', tab_strip(html))


def table_sort_keys(html: str) -> set[str]:
    """The sort keys of the ONE rendered table's header row (the table's own allow-list)."""
    body = html[html.index("<thead"):html.index("</thead>") + len("</thead>")]
    return set(re.findall(r'href="/\?[^"]*?sort=([a-z_]+)', body))


def live_urls(html: str) -> tuple[str, str]:
    """The ``data-stream``/``data-fragments`` URLs the shell handed the browser (unescaped)."""
    stream = re.search(r'data-stream="([^"]*)"', html)
    fragments = re.search(r'data-fragments="([^"]*)"', html)
    assert stream and fragments, "the shell must render the live script's URLs"
    return unescape(stream.group(1)), unescape(fragments.group(1))


def live_query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


# ============================================================ FINDING 1: the tab scope

@pytest.mark.asyncio
async def test_a_narrowed_cluster_page_is_not_offered_as_a_tab(config_dir):
    """The strip offers the pages this reader may open, and no others.

    A manager whose Nodes/Instances pages have been narrowed away (see ``narrow()``) receives Servers
    and Players only."""
    app, _registrar = manager_app(config_dir, provider=fleet_source)
    await get(app, "/")                          # one install declares the real capabilities
    narrow(nodes_page.NODES_CAPABILITY, instances_page.INSTANCES_CAPABILITY)

    html = await get(app, "/")
    offered = offered_tabs(html)
    # the Load tab stays: it is not one of the narrowed PAGES (it has no page of its own), and its
    # own scope is bounded on the route instead — a manager reads only the servers they hold.
    assert offered == ["servers", "players", dashboard_page.LOAD_TAB], offered
    assert nodes_page.NODES_PATH not in tab_strip(html)
    assert instances_page.INSTANCES_PATH not in tab_strip(html)

 # and the ONE table it renders is the Servers one: no instance/node column set leaked in
    keys = table_sort_keys(html)
    assert {"status", "mission"} <= keys, "the Servers table must be the one rendered"
    assert "dcs_port" not in keys and "webgui_port" not in keys and "instances" not in keys


@pytest.mark.asyncio
async def test_a_narrowed_cluster_tab_cannot_be_reached_with_a_parameter(config_dir):
    """``?tab=nodes`` / ``?tab=instances`` render the DEFAULT tab for a reader without the capability.

    Default-tab rather than 403, deliberately: the standalone pages refuse at the GATE (their route
    declares a capability the reader lacks), while a tab is a parameter of a route the reader may
    legitimately open — and the console's own documented behaviour for a tab value that is not for
    this viewer is the same as for one that does not exist at all (``?tab=nonsense`` -> Servers).
    Either way the cluster table is NOT rendered, which is the property under test.
    """
    app, _registrar = manager_app(config_dir, provider=fleet_source)
    await get(app, "/")
    narrow(nodes_page.NODES_CAPABILITY, instances_page.INSTANCES_CAPABILITY)

    for value in ("nodes", "instances"):
        html = await get(app, f"/?tab={value}")
        assert value not in offered_tabs(html), f"tab={value} is still offered"
        keys = table_sort_keys(html)
        assert {"status", "mission"} <= keys, f"?tab={value} did not fall back to Servers"
        assert "dcs_port" not in keys and "instances" not in keys, \
            f"?tab={value} rendered the cluster table"
        assert 'aria-current="page"' in tab_strip(html), "a tab must still be marked current"
 # the active tab is Servers, stated by the strip itself
    active = re.search(r'class="tab on" href="([^"]*)"', tab_strip(html))
    assert active and "tab=servers" in active.group(1), tab_strip(html)


@pytest.mark.asyncio
@pytest.mark.parametrize("roles", [["Admin"], ["DCS Admin"]])
async def test_a_cluster_role_still_gets_every_tab(config_dir, roles):
    """The narrowing is a narrowing and not a blanket no: both cluster roles keep every tab."""
    app, _registrar = app_with(config_dir, roles, provider=fleet_source)
    html = await get(app, "/")
    assert offered_tabs(html) == list(ALL_TABS), offered_tabs(html)

    nodes_html = await get(app, "/?tab=nodes")
    assert "instances" in table_sort_keys(nodes_html), "the Nodes table did not render"
    instances_html = await get(app, "/?tab=instances")
    assert "dcs_port" in table_sort_keys(instances_html), "the Instances table did not render"


@pytest.mark.asyncio
async def test_the_tab_scope_is_the_standalone_pages_own_capability(config_dir):
    """One predicate, not a second spelling: each tab is decided by the capability ITS page declares.

    This is the drift guard the finding asks for — the mapping is read from the page modules, and
    the roles the capability needs are the page's own ``roles`` (the lists' ``CLUSTER_ROLES`` for
    Nodes/Instances).
    """
    app, _registrar = app_with(config_dir, ["Admin"], provider=fleet_source)
    await get(app, "/")          # one install is what declares the capabilities

    for key, module in (("servers", servers_page), ("players", players_page),
                        ("nodes", nodes_page), ("instances", instances_page)):
        capability = dashboard_page.tab_capability(key)
        assert capability == module.PAGE.capability, key
        assert capability == module.capabilities()[module.PAGE.path], key
        assert permissions.roles_for_capability(capability) == module.PAGE.roles, key

    assert permissions.roles_for_capability(dashboard_page.tab_capability("nodes")) == \
        lists_page.CLUSTER_ROLES
    assert permissions.roles_for_capability(dashboard_page.tab_capability("servers")) == \
        lists_page.READ_ONLY_ROLES


@pytest.mark.asyncio
async def test_the_rendered_table_is_always_one_the_strip_offers(config_dir):
    """Offered == rendered, in BOTH directions, even when the default tab is not for this viewer.

    A synthetic narrowing (the Servers page taken away from the manager, on BOTH axes) stands in for
    the day a page's roles change again: the strip loses Servers, and the page must then render a tab
    the strip DOES offer rather than quietly rendering the default table as an unlinked extra.
    """
    app, _registrar = manager_app(config_dir, provider=fleet_source)
    await get(app, "/")                      # one install declares the real role sets
    narrow(dashboard_page.tab_capability("servers"),     # the synthetic narrowing
           dashboard_page.tab_capability("nodes"),
           dashboard_page.tab_capability("instances"))

    html = await get(app, "/")
    offered = offered_tabs(html)
    assert offered and "servers" not in offered, offered

    active = re.search(r'class="tab on" href="([^"]*)"', tab_strip(html))
    assert active and f"tab={offered[0]}" in active.group(1), tab_strip(html)
 # and the table under it is that tab's table (Players), not the default one
    assert {"ucid", "slot"} <= table_sort_keys(html), table_sort_keys(html)


@pytest.mark.asyncio
async def test_a_refused_tab_is_not_carried_into_the_live_urls(config_dir):
    """A tab the reader may not open must not ride in the stream URL either (the live refresh
    re-resolves the tab from that URL — carrying it would re-open the same door)."""
    app, _registrar = manager_app(config_dir, provider=fleet_source)
    await get(app, "/")
    narrow(instances_page.INSTANCES_CAPABILITY)
    html = await get(app, "/?tab=instances")
    for url in live_urls(html):
        assert "tab=instances" not in url, url


@pytest.mark.asyncio
async def test_the_live_fragment_endpoint_refuses_the_cluster_table(config_dir):
    """The bypass driven over the LIVE seam: asking the fragment endpoint itself for
    ``?tab=instances`` must answer with the Servers table, not the Instances one — the parameter is
    re-validated per request against the roles, never trusted from the page that handed the URL
    over."""
    app, _registrar = manager_app(config_dir, provider=fleet_source)
    await get(app, "/")
    narrow(nodes_page.NODES_CAPABILITY, instances_page.INSTANCES_CAPABILITY)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        for value in ("nodes", "instances"):
            response = await client.get(f"{live_page.DASHBOARD_FRAGMENTS_PATH}?tab={value}")
            assert response.status_code == 200
            panel = response.json()[live_page.STATE_TARGET]
            keys = table_sort_keys(panel)
            assert {"status", "mission"} <= keys, f"?tab={value} rendered the cluster table"
            assert "dcs_port" not in keys and "instances" not in keys
            assert f"tab={value}" not in panel, "the refused tab is still offered in the fragment"


# ============================================================ FINDING 2: the carried query

@pytest.mark.asyncio
async def test_the_tab_links_carry_the_active_search(config_dir):
    """With a search active, EVERY tab link carries it — and following one away and back keeps the
    field and the filtering in agreement."""
    app, _registrar = manager_app(config_dir, provider=fleet_source)
    html = await get(app, "/?tab=players&q=viper")

    assert 'name="q" value="viper"' in html, "the field echoes the query"
    assert "Filter: name or UCID contains viper" in html
    assert "1 of 2 players" in html, "the filtering agrees with the echo"

    links = re.findall(r'href="([^"]*?tab=[^"]*)"', tab_strip(html))
    assert links, tab_strip(html)
    for href in links:
        assert "q=viper" in unescape(href), f"a tab link dropped the active search: {href}"

 # follow the Servers tab away, then the Players tab back: the search is still in force
    away_href = next(unescape(href) for href in links if "tab=servers" in href)
    away = await get(app, away_href)
    back_href = next(unescape(href) for href in
                     re.findall(r'href="([^"]*?tab=[^"]*)"', tab_strip(away))
                     if "tab=players" in href)
    back = await get(app, back_href)
    assert 'name="q" value="viper"' in back, "the search field lost the query on the way back"
    assert "Filter: name or UCID contains viper" in back and "1 of 2 players" in back, \
        "the filtering no longer agrees with the field"
 # and the clearing link still clears (the search is a link away, not a trap)
    assert re.search(r'href="/\?tab=players"[^>]*>Clear</a>', back)


@pytest.mark.asyncio
async def test_no_search_means_no_q_in_any_tab_link(config_dir):
    """Positive control: with no search active, no link grows a ``q`` — the page's URLs are the
    ones the level tests pin."""
    app, _registrar = manager_app(config_dir, provider=fleet_source)
    html = await get(app, "/?tab=players")
    for href in re.findall(r'href="([^"]*)"', html):
        assert "q=" not in unescape(href), href


# ============================================================ FINDING 3: the unhonoured sort key

@pytest.mark.asyncio
async def test_a_sort_key_the_rendered_table_does_not_honour_is_not_carried(config_dir):
    """``?sort=dcs_port`` on the Servers tab is a column of ANOTHER table: the page ignores it and
    the stream URL must not carry it."""
    app, _registrar = app_with(config_dir, ["Admin"], provider=fleet_source)
    html = await get(app, "/?sort=dcs_port")
    assert table_sort_keys(html) >= {"status", "mission"}, "the Servers table must be rendered"
    assert live_urls(html) == ("/api/dashboard/stream?level=info",
                               "/api/dashboard/fragments?level=info"), live_urls(html)

 # positive control 1: a column the rendered table DOES honour is still carried
    honoured = await get(app, "/?sort=players&dir=desc")
    assert live_query(live_urls(honoured)[0])["sort"] == ["players"]
    assert live_query(live_urls(honoured)[0])["dir"] == ["desc"]

 # positive control 2: the same key IS honoured once the table that owns it is the visible one
    instances_html = await get(app, "/?tab=instances&sort=dcs_port")
    assert "dcs_port" in table_sort_keys(instances_html)
    stream = live_urls(instances_html)[0]
    assert live_query(stream)["sort"] == ["dcs_port"], stream


# ================================ FINDING 4: the flake — the capability state is re-declared

@pytest.mark.asyncio
async def test_one_install_is_enough_for_the_console_capabilities(config_dir):
    """Process-global capability state.

    The console's capabilities are process state and the suite's ``clean_capability_state`` fixture
    restores them after every test, so no test may depend on the residue of the one before. WIPING the
    table and installing ONE app re-declares everything the console needs, every time (a declaration
    that only ran at first import, or a one-shot guard in ``declare_capability``, would leave a later
    test with no ``logs.view``). Checked through the rendered page.
    """
    permissions.CAPABILITIES.clear()
    permissions.CAPABILITIES.update({"public": ()})
    permissions.set_role_resolver(None)

    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config={"session": {"secret": "s" * 40, "cookie_name": "dcssb_test_session"},
                          "auth": {"public_base_url": "http://localhost:9876",
                                   "local": {"enabled": True, "users": [
                                       {"username": "staff",
                                        "password_hash": _hash(),
                                        "roles": ["Admin"]}]}}})
    for key in ALL_TABS:
        assert dashboard_page.tab_capability(key) in permissions.CAPABILITIES, key
    assert dashboard_page.DASHBOARD_CAPABILITY in permissions.CAPABILITIES
    assert "logs.view" in permissions.CAPABILITIES

    app.state.webui_source_provider = fleet_source
    html = await get(app, "/")
    assert 'id="log-panel"' in html, \
        "the log panel vanished for an Admin: a capability was not re-declared by this install"
    assert offered_tabs(html) == list(ALL_TABS)


def test_the_console_reads_its_own_declaration_before_any_install():
    """The import-time declaration is the other half of the same property: importing the modules is
    enough for the console's own capabilities (a fresh process, before any shell is installed)."""
    dashboard_page.declare()
    from services.webservice.pages import logs as logs_page
    logs_page.declare()
    assert dashboard_page.DASHBOARD_CAPABILITY in permissions.CAPABILITIES
    assert logs_page.LOGS_CAPABILITY in permissions.CAPABILITIES
    assert live_page.DASHBOARD_STREAM_PATH in live_page.capabilities()
