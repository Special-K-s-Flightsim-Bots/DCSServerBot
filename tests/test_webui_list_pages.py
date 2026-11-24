"""The standalone list pages: Servers, Nodes, Instances, Players.

The dashboard caps what it shows -- its job is attention, not inventory -- so the full lists live on
their own pages, which are not a second implementation: they read the same validated view layer
(``pages.lists.list_view``), render the same table component, and advertise themselves through the
same nav/capability mechanism the dashboard and Logs page use. Each page answers 200 for a role it
names and 403 for one it does not, and the rendered sidebar withholds the link. The Servers page
renders the FULL list (uncapped) ordered exactly as the dashboard's Servers tab. The Instances page
carries no write control at all; the other three are asserted by their own suites."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import pytest

from conftest import asgi_client, permissions
from services.webservice import readmodels
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import instances as instances_page
from services.webservice.pages import lists as lists_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import players as players_page
from services.webservice.pages import servers as servers_page
from test_webui_dashboard import (BOT_YAML, Status, _build, _hash, _login, _source, instance, node,
                                  player, server)

TEMPLATES = Path(lists_page.__file__).parent.parent / "templates"

#: the four pages, in nav order.
PAGES = (servers_page, nodes_page, instances_page, players_page)
PAGE_IDS = ["servers", "nodes", "instances", "players"]

#: The page whose table carries NO write — see ``test_a_page_with_no_action_carries_no_write_control``.
#: The Servers table carries the row strip, the Players table the message control and the
#: Nodes table the node trio, so the "no write control" pin is NARROWED to Instances
#: rather than deleted: what it pins is a real property of a page that owns no action, and the reason
#: the other three are missing is stated here so a reader cannot mistake the narrowing for coverage.
READ_ONLY_PAGES = (instances_page,)
READ_ONLY_PAGE_IDS = ["instances"]

#: table name -> the read model that owns that page's empty-state sentence. The Players page reads
#: the DATABASE and states its own empty copy; ``NO_PLAYERS_MESSAGE`` is the live roster's sentence,
#: rendered by the same page's ``?show=online`` view (the dashboard's own table).
EMPTY_MESSAGES = {
    "servers": readmodels.NO_SERVERS_MESSAGE,
    "nodes": readmodels.NO_NODES_MESSAGE,
    "instances": readmodels.NO_INSTANCES_MESSAGE,
    "player_directory": players_page.PLAYERS_EMPTY,
    "players": readmodels.NO_PLAYERS_MESSAGE,
}

#: the query the "complete without JavaScript" pin opens each page with: the Players page's default
#: is the DATABASE directory, so its rendered table needs the read the test does not have — its live
#: roster is the server-rendered table it can prove.
PAGE_QUERY = {"player_directory": "?show=online"}


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def app_with(config_dir, roles, *, provider=None, username="staff"):
    """An app whose only local user holds *roles*."""
    app, registrar = _build(config_dir, users=[
        {"username": username, "password_hash": _hash(), "roles": list(roles)}])
    app.state.webui_source_provider = provider or (lambda: readmodels.EmptySource())
    return app, registrar


def manager_app(config_dir, *, provider=None, username="staff"):
    """An app whose only local user is a MANAGER: no role at all, a DECLARED scope.

    The console's third kind of identity (``Admin``, ``DCS Admin`` and a manager of at least one
    server). A local account has no Discord member to compare against, so its declared scope IS the
    fact — and a capability carrying ``scope_grants`` admits it without holding a single role.
    """
    app, registrar = _build(config_dir, users=[
        {"username": username, "password_hash": _hash(), "roles": [], "scope": ["Host A"]}])
    app.state.webui_source_provider = provider or (lambda: readmodels.EmptySource())
    return app, registrar


def narrow(*capabilities, roles=("Admin",)):
    """Take *capabilities* away from a manager: BOTH axes, or the narrowing does not bite.

    The scope axis (``SCOPE_GRANTS``) and the role axis (``CAPABILITIES``) are independent, so
    narrowing only the role tuple would leave a manager admitted through its scope and the test
    would quietly prove nothing. The suite's ``clean_capability_state`` restores both tables after
    every test, so a narrowing never leaks into the next one.
    """
    for capability in capabilities:
        permissions.CAPABILITIES[capability] = tuple(roles)
        permissions.SCOPE_GRANTS[capability] = False


async def get(app, path, username="staff"):
    async with asgi_client(app) as client:
        await _login(client, username)
        response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return response.text


def sidebar(html: str) -> str:
    return re.search(r"<aside.*?</aside>", html, re.S).group(0)


def row_names(html: str) -> list[str]:
    """The first cell of every table row -- on these pages exactly one table is rendered.

    A SERVER row's name is the LINK to that server's page, so the cell is
    ``<b><a href="/fleet/…">NAME</a></b>``; the anchor tags are stripped here so the caller reads the
    NAME, not the markup. A row shape without a link is read unchanged.
    """
    cells = re.findall(r"<td><b>(.*?)</b>", html)
    return [re.sub(r"</?a\b[^>]*>", "", cell) for cell in cells]


# ------------------------------------------------------------- 1. the gate and the sidebar

@pytest.mark.asyncio
@pytest.mark.parametrize("module", PAGES, ids=PAGE_IDS)
async def test_each_page_answers_a_role_it_names_and_refuses_one_it_does_not(config_dir, module):
    """200 for a named role, 403 for a role no page names — the same app, two users."""
    page = module.PAGE
    app, _registrar = app_with(config_dir, page.roles, username="allowed")
    html = await get(app, page.path, username="allowed")
    assert page.title in html, "the page renders its own title"

    app, _registrar = app_with(config_dir, ["GameMaster"], username="denied")
    async with asgi_client(app) as client:
        await _login(client, "denied")
        refused = await client.get(page.path)
    assert refused.status_code == 403, (page.path, refused.status_code)
    assert page.capability == module.capabilities()[page.path], \
        "the route reads the page's one capability string"


@pytest.mark.asyncio
async def test_the_rendered_sidebar_withholds_a_link_its_role_cannot_open(config_dir):
    """A menu entry whose route answers 403 to its role must not be rendered -- read from the page itself.

    The reader is a MANAGER whose Nodes and Instances pages have been narrowed away on BOTH axes (see
    ``narrow()``). The sidebar offers Servers and Players, withholds Nodes and Instances, and both
    directions hold on the same app.
    """
    app, _registrar = manager_app(config_dir)

 # positive control (so the withholds below cannot pass vacuously): before the narrowing, a
 # manager IS offered the menu items it may open (Servers, Nodes).
    before = sidebar(await get(app, servers_page.SERVERS_PATH))
    for module in (servers_page, nodes_page):
        assert f'href="{module.PAGE.path}"' in before, \
            f"a manager must be offered {module.PAGE.path} before the narrowing"
 # Instances is NOT a menu item any more (the Dashboard carries it), so it can never be offered in
 # the sidebar — that is the settled main menu, not the narrowing.
    for module in (instances_page,):
        assert f'href="{module.PAGE.path}"' not in before, \
            f"{module.PAGE.path} must not be a menu entry"

    narrow(nodes_page.NODES_CAPABILITY, instances_page.INSTANCES_CAPABILITY)

    html = await get(app, servers_page.SERVERS_PATH)
    links = sidebar(html)
    assert f'href="{servers_page.SERVERS_PATH}"' in links
    assert f'href="{nodes_page.NODES_PATH}"' not in links, \
        "the sidebar offers Nodes to a reader its route refuses"

 # the other direction: the withheld link is refused by its route, in the same app
    async with asgi_client(app) as client:
        await _login(client, "staff")
        assert (await client.get(nodes_page.NODES_PATH)).status_code == 403
        assert (await client.get(instances_page.INSTANCES_PATH)).status_code == 403
        assert (await client.get(servers_page.SERVERS_PATH)).status_code == 200
        assert (await client.get(players_page.PLAYERS_PATH)).status_code == 200


@pytest.mark.asyncio
async def test_dcs_admin_may_open_every_menu_page(config_dir):
    """The wider role is offered both menu pages it may open, so the split is a split and not a
    blanket no."""
    app, _registrar = app_with(config_dir, ["DCS Admin"])
    html = await get(app, nodes_page.NODES_PATH)
    links = sidebar(html)
    for module in (servers_page, nodes_page):
        assert f'href="{module.PAGE.path}"' in links, module.PAGE.path


# ------------------------------------------------------- 2. the full list, the same ordering

@pytest.mark.asyncio
async def test_the_servers_page_renders_the_full_list_and_sorts_like_the_dashboard_tab(config_dir):
    """50 servers (the biggest hosters' scale): rendered in full, in the SAME order the dashboard's
    Servers tab produces for the same model — both orders come from the one view layer."""
    fleet = [server(name=f"S{i:02d}", players=[player(f"P{i}-{j}")
                                               for j in range((i * 7) % 13)])
             for i in range(50)]
    provider = lambda: _source(servers=fleet, node_obj=node(instances={}))  # noqa: E731
    app, _registrar = app_with(config_dir, ["DCS Admin"], provider=provider)

    page_html = await get(app, f"{servers_page.SERVERS_PATH}?sort=players&dir=desc")
    dashboard_html = await get(
        app, f"{dashboard_page.DASHBOARD_PATH}?tab=servers&sort=players&dir=desc")

    state = readmodels.overview(provider())
    expected = [view.name for view in readmodels.sort_rows("servers", state.servers,
                                                           "players", "desc")]
    assert len(expected) == 50
    assert row_names(page_html) == expected, "the standalone page is not the full, sorted list"
    assert row_names(dashboard_html) == expected, \
        "the dashboard's Servers tab and the page disagrees — they must share the order"
 # the same component also means the same links: a header click must sort THIS page (the view
 # layer is told the page's own path), never bounce the reader back to the dashboard.
    assert re.search(r'href="/fleet\?[^"]*sort=players', page_html), \
        "the Servers page's headers do not sort the page they are on"
    assert re.search(r'href="/\?[^"]*sort=', page_html) is None, \
        "a header on the standalone page links back to the dashboard"


def test_the_four_pages_and_the_dashboard_render_one_table_component():
    """ONE table markup: ``_table.html`` is included by the dashboard's state panel and by the list
    template, and no other template owns a table body.

    THE ROWS ARE A PARTIAL OF THEIR OWN (``_table_rows.html``) because the Servers page's body is a
    live swap target: the fragment the stream sends must be the rows ALONE (a frame replaces the
    inner html of the element the page drew), so the rows moved one level down, and ``_table.html``
    includes them inside its ``<tbody>``. One rendering, still: the page and the stream read the same
    file, and neither owns a second copy of a row.
    """
    shared = (TEMPLATES / "_table.html").read_text(encoding="utf-8")
    assert "<thead" in shared and "<tbody" in shared
    assert 'include "_table_rows.html"' in shared
    assert "<tbody{% if table_body_id" in shared, \
        "the body carries the live target's id when the page streams, and nothing when it does not"

    rows = (TEMPLATES / "_table_rows.html").read_text(encoding="utf-8")
    assert "<thead" not in rows and "<tbody" not in rows, \
        "the rows partial draws no table frame: the page's own element is the wrapper"

    assert 'include "_table.html"' in (TEMPLATES / "_state.html").read_text(encoding="utf-8")
    assert 'include "_table.html"' in (TEMPLATES / "list.html").read_text(encoding="utf-8")

    # THE ALLOWLIST is for a table whose ROWS ARE THE DATA and whose controls are per row: the music
    # pages render an ordered playlist (a drag handle, a row number and the order glyphs in each row)
    # and the library's file pick list, none of which the sortable inventory component can express.
    # Every entry states why; an entry no template carries any more fails rather than lingering.
    ALLOWED = {
        "music.html": "the library summary — one row per node, no control column",
        "_music_playlists.html": "the playlist inventory — one glyph strip per row (the live "
                                 "fragment of the music page)",
        "music_playlist.html": "the ordered playlist (drag handle, row number, order glyphs) and "
                               "the folder's file pick list",
        # a MATRIX, not an inventory: one row per capability and one cell per role, and the cell is
        # the control (a checkbox that posts the whole matrix) — no column of the sortable component
        # can express a cell that IS the write
        "access.html": "the role → capability matrix — one control per role per capability",
        # the PUBLIC site's own document: it is not the console's page tree at all (it does not
        # extend base.html, it carries its own stylesheet and its own two tables), so the console's
        # sortable inventory component — its columns, its scope and its controls — is not available
        # to it and its tables are written where they are drawn
        "site_overview.html": "the public site's board — outside the console's shell and stylesheet",
        "site_leaderboard.html": "the public site's ranked board — outside the console's shell and "
                                 "stylesheet, and its one figure column is the chosen metric",
        "site_player.html": "the public site's pilot profile — outside the console's shell and "
                            "stylesheet",
        "site_players.html": "the public site's pilot search — outside the console's shell and "
                             "stylesheet",
        "site_me.html": "the viewer's own record — outside the console's shell and stylesheet",
    }
    elsewhere = [path.name for path in sorted(TEMPLATES.glob("*.html"))
                 if path.name != "_table.html" and "<thead" in path.read_text(encoding="utf-8")]
    assert [name for name in elsewhere if name not in ALLOWED] == [], \
        f"a second copy of the table markup exists in {[n for n in elsewhere if n not in ALLOWED]}"
    assert [name for name in ALLOWED if name not in elsewhere] == [], \
        "an allowlist entry no longer names a template that owns a table"


def test_the_page_modules_delegate_to_the_dashboard_view_layer():
    """No page re-implements sorting or link building: they call ``lists.list_view``, which calls
    the one ``readmodels.view_state`` the dashboard uses."""
    source = Path(lists_page.__file__).read_text(encoding="utf-8")
    assert "readmodels.view_state(" in source, "the shared page builder must use the view layer"
    for module in PAGES:
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert "sorted(" not in text, f"{module.__name__} sorts its own rows"
        assert "view_state(" not in text, f"{module.__name__} builds its own view"
        if module is not players_page:
            assert "urlencode" not in text, f"{module.__name__} builds its own links"
    # The Players page renders the Show switch and the pager — VIEW OPTION links, the shape the Bans
    # page's DGSA option already has: a closed vocabulary of validated values in one GET parameter.
    # It still builds no SORT link: ``column_link`` stays the view layer's.
    players_source = Path(players_page.__file__).read_text(encoding="utf-8")
    assert "column_link(" not in players_source, "the Players page builds its own sort link"
    assert "SORT_PARAM" not in players_source, "the Players page spells a sort parameter"


@pytest.mark.asyncio
async def test_a_hostile_sort_value_is_refused_and_never_reaches_a_link(config_dir):
    """The page goes through the same allow-list the dashboard does: junk in ``?sort=``/``?dir=``
    leaves the table on its default order and the raw value appears in no href."""
    provider = lambda: _source(  # noqa: E731
        servers=[server(name="Bravo", players=[player()]), server(name="Alpha")],
        node_obj=node(instances={}))
    app, _registrar = app_with(config_dir, ["DCS Admin"], provider=provider)

    html = await get(app, f"{servers_page.SERVERS_PATH}?sort=%3Cscript%3E&dir=sideways")
 # The shell's OWN `<script src=...>` tags are not the value (adds one on every page), so
 # the pin is on the hostile value itself, in both the raw and the escaped spelling.
    assert "<script>" not in html and "%3Cscript%3E" not in html, \
        "the raw sort value was echoed into the page"
    assert row_names(html) == ["Alpha", "Bravo"], "junk must fall back to the documented default"
    assert re.search(r'dir="?sideways', html) is None


# ------------------------------------------------------------ 3. the players search, reused

@pytest.mark.asyncio
async def test_the_players_page_searches_server_side_with_the_dashboard_component(config_dir):
    provider = lambda: _source(  # noqa: E731
        servers=[server(players=[player("Viper-1", "aaaaaaaa"), player("Maverick", "bbbbbbbb")])],
        node_obj=node(instances={}))
    app, _registrar = app_with(config_dir, ["DCS Admin"], provider=provider)

    html = await get(app, f"{players_page.PLAYERS_PATH}?show=online&q=viper")
    assert 'class="search"' in html and 'method="get"' in html
    assert 'name="q"' in html and 'value="viper"' in html
    assert "Filter: name or UCID contains viper" in html
    assert "1 of 2 players" in html
    assert re.search(r'href="/players\?show=online">Clear</a>', html), \
        "the clear link must point at THIS page, not at the dashboard"
    assert "aaaaaaaa" in await get(app, f"{players_page.PLAYERS_PATH}?show=online&q=aaaaaaaa"), \
        "the UCID is searchable too"

    plain = await get(app, f"{players_page.PLAYERS_PATH}?show=online")
    assert "Filter:" not in plain
    assert "2 player(s) online" in plain, "an unfiltered page states the total, not X of Y"


@pytest.mark.asyncio
async def test_a_hostile_search_query_is_escaped_and_yields_an_empty_result(config_dir):
    provider = lambda: _source(  # noqa: E731
        servers=[server(players=[player("Viper-1", "aaaaaaaa")])],
        node_obj=node(instances={}))
    app, _registrar = app_with(config_dir, ["DCS Admin"], provider=provider)

    payload = '"><script>alert("pwn")</script>'
    html = await get(app, f"{players_page.PLAYERS_PATH}?show=online&q={quote(payload)}")
    assert "<script>alert" not in html, "the query was injected into the page"
    assert "&lt;script&gt;alert" in html, "the query must be echoed escaped"
    assert dashboard_page.NO_PLAYERS_MATCHED_MESSAGE in html, \
        "a filtered search that matches nothing says so, it does not say 'nobody is online'"
    assert "0 of 1 players" in html
    assert readmodels.NO_PLAYERS_MESSAGE not in html


# ------------------------------------------------------------------- 4. the empty states

@pytest.mark.asyncio
@pytest.mark.parametrize("module", PAGES, ids=PAGE_IDS)
async def test_every_page_renders_the_read_models_own_empty_copy(config_dir, module):
    app, _registrar = app_with(config_dir, module.PAGE.roles)
    html = await get(app, module.PAGE.path)

    assert EMPTY_MESSAGES[module.PAGE.table] in html, "the page invented its own sentence"
    assert '<p class="empty"></p>' not in html, "an empty container stands in for the sentence"
    assert re.search(r"<tbody>\s*</tbody>", html) is None, "an empty table frame was rendered"


# ------------------------------------------------- 5. the pages with no write, and the two that write

@pytest.mark.asyncio
@pytest.mark.parametrize("module", READ_ONLY_PAGES, ids=READ_ONLY_PAGE_IDS)
async def test_a_page_with_no_action_carries_no_write_control(config_dir, module):
    """A write control is OMITTED, never rendered disabled.

    Scoped to the page whose table carries no write at all (Instances) -- see ``READ_ONLY_PAGES``: this
    test installs a shell with NO ``plugins`` attribute, so no action is discovered and
    ``action_available()`` is False for every one. The other three tables are asserted where the
    discovery runs (``test_webui_write_server_actions.py`` for /fleet, ``test_webui_write_message.py``
    for /players, ``test_webui_node_controls.py`` for /nodes).

    Asserted at three levels: the rendered markup, the forms (every one is a GET or the shell's Sign out
    POST), and the module.
    """
    provider = lambda: _source(  # noqa: E731
        servers=[server(status=Status.PAUSED, players=[player()])],
        node_obj=node(instances={"Training": instance()}))
    app, registrar = app_with(config_dir, module.PAGE.roles, provider=provider)
    html = await get(app, module.PAGE.path)

    for form in re.findall(r"<form[^>]*>", html):
        assert 'method="get"' in form or 'action="/auth/logout"' in form, form
    # The language pill is CHROME, not a write: it is the popover's trigger and must be a
    # <button> (the `popovertarget` attribute only answers on a button), so it is excluded here by
    # class — every OTHER button on the page is still held to the two control labels below.
    labels = {label.strip() for label in
              re.findall(r'<button(?![^>]*class="langchip")[^>]*>(.*?)</button>', html, re.S)}
    assert labels <= {"Search", "Sign out"}, f"unexpected control(s): {labels}"
    for verb in ("Pause", "Resume", "Restart", "Stop", "Start", "Kick", "Ban", "Message",
                 "Upload", "Install", "Uninstall", "Shutdown", "Update"):
        assert f">{verb}<" not in html, f"a '{verb}' control label is rendered on the page"
 # the strip's own marker, not only its labels: an icon-only control has no label to grep
    assert "act-strip" not in html, "an icon strip is rendered on a page that offers no action"

    routes = [route for route in registrar.routes
              if getattr(route, "path", None) == module.PAGE.path]
    assert routes, "floor: the page's route is registered"
    for route in routes:
        assert set(route.methods or ()) == {"GET"}, f"{module.PAGE.path} answers {route.methods}"
    source = Path(module.__file__).read_text(encoding="utf-8")
    for verb in ("@router.post", "@router.put", "@router.patch", "@router.delete"):
        assert verb not in source, f"{module.__name__} owns a write handler"
    shared = Path(lists_page.__file__).read_text(encoding="utf-8")
    for verb in ("@router.post", "@router.put", "@router.patch", "@router.delete"):
        assert verb not in shared


@pytest.mark.asyncio
@pytest.mark.parametrize("module", PAGES, ids=PAGE_IDS)
async def test_every_page_renders_complete_without_javascript(config_dir, module):
    """The full list is SERVER-RENDERED, script or no script.

    A page whose rows are LIVE (``ListPage.live``) also loads the console's stream script — and that
    stays an ENHANCEMENT: every row is in the HTML the request answered with, so a browser without
    JavaScript loses the updates and never the list. A page that does not stream loads no script at
    all, so a static page can never open a connection it has nothing to say on.
    """
    provider = lambda: _source(  # noqa: E731
        servers=[server(name="Training Caucasus", players=[player()])],
        node_obj=node(instances={"Training": instance()}))
    app, _registrar = app_with(config_dir, module.PAGE.roles, provider=provider)
    html = await get(app, module.PAGE.path + PAGE_QUERY.get(module.PAGE.table, ""))
    assert "<table>" in html and "<thead" in html, "the list itself must be in the HTML"
    assert "<tbody" in html and html.count("<tr>") >= 2, \
        "the rows are in the HTML, not fetched by the script"
    if module.PAGE.live:
        assert "shell.js" in html and f'data-stream="/api{module.PAGE.path}/stream"' in html
    else:
        assert "shell.js" not in html and "data-stream" not in html


# ------------------------------------------------------- 6. the pages render with no data

@pytest.mark.asyncio
@pytest.mark.parametrize("module", PAGES, ids=PAGE_IDS)
async def test_every_page_renders_without_a_bot_at_all(config_dir, module):
    """No source provider at all (``bot = None``): the chrome, the title and the empty sentence."""
    app, _registrar = _build(config_dir, users=[
        {"username": "staff", "password_hash": _hash(), "roles": list(module.PAGE.roles)}])
    html = await get(app, module.PAGE.path)
    assert module.PAGE.title in html
    assert f"<title>{module.PAGE.title} — DCSServerBot</title>" in html
    assert EMPTY_MESSAGES[module.PAGE.table] in html
    assert 'class="who"' in html, "the shell chrome is rendered"
