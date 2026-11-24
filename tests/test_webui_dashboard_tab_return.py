"""A write pressed on a dashboard TAB comes back to that tab, not to the default one.

The dashboard renders all four tables at ONE path (``/``) and the table in force is a query
parameter, while a write returns by looking its origin up in the registry — an allow-list of PAGE
PATHS, which refuses ``/?tab=players``. So the tab rides BESIDE the origin in its own field
(``actions.TAB_FIELD``) and the routes re-attach it (``actions.origin_url``): the dialog's Cancel
link and the post-write redirect both land back on the tab the row was pressed on, and a page whose
table IS its path (``/fleet``, ``/nodes``) carries no tab field at all. The origin
itself is still the allow-listed value, so every crafted destination is refused exactly as before.

Driven through the real app and the stub clusters the other write suites use."""
from __future__ import annotations

import re
from urllib.parse import urljoin

import pytest

from conftest import back_target

from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import live as live_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import players as players_page
from services.webservice.pages import servers as servers_page

# ONE harness for the console's write actions: the pause pilot's doubles and fixtures are reused.
from test_webui_write_pause import (  # noqa: F401  (fixtures are used by name)
    build, clean_registry, client, config_dir, open_page, recording_bot, sign_in, token_of,
)
from test_webui_write_server_actions import fleet as running_fleet
from test_webui_write_player_actions import player_fleet
from test_webui_node_controls import clean_node_registry, fleet as node_fleet  # noqa: F401

PAUSE = actions_page.PAUSE_PATH
POPUP = actions_page.POPUP_PATH
RESTART = "/actions/server/restart"
NODE_RESTART = "/actions/node/restart"
CONFIRM = actions_page.CONFIRM_SUFFIX
ORIGIN = actions_page.ORIGIN_FIELD
TAB = actions_page.TAB_FIELD

DASHBOARD = dashboard_page.DASHBOARD_PATH
SERVERS = servers_page.SERVERS_PATH
PLAYERS = players_page.PLAYERS_PATH
NODES = nodes_page.NODES_PATH

PLAYERS_TAB = f"{DASHBOARD}?tab=players"
NODES_TAB = f"{DASHBOARD}?tab=nodes"


# ---------------------------------------------------------------------------------- helpers

def form_for(html: str, path: str) -> str:
    """The rendered ``<form>`` that posts to *path* — the whole POST shape the browser would send."""
    match = re.search(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)
    assert match, f"the page rendered no control posting to {path}"
    return match.group(0)


def fields_of(form: str) -> dict[str, str]:
    """Every hidden/typed field of a rendered form, as the browser would submit it."""
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))


def back_of(html: str, page_path: str) -> str:
    """Where the dialog's Cancel control (and its ``Esc``) goes back to — the anchor's href,
    RESOLVED against the dialog's own URL (the href is relative, so a proxy prefix survives)."""
    match = re.search(r'<a class="btn" href="([^"]*)"', html)
    assert match, "the dialog renders no Cancel link"
    return urljoin(page_path, match.group(1))


def confirm_token(html: str) -> str:
    match = re.search(r'name="_confirm_token" value="([^"]+)"', html)
    assert match, "the dialog carries no confirm token"
    return match.group(1)


# ============================================================ 1. the tab rides with the control

@pytest.mark.asyncio
async def test_a_player_row_on_the_players_tab_carries_that_tab(config_dir, clean_registry):
    """The row's own control states BOTH halves of where it was pressed: the page path (the value the
    registry can resolve) and the tab in force (a separate field, because ``/?tab=players`` is not a
    page path)."""
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, PLAYERS_TAB)
        directory = await open_page(http, PLAYERS)
    row = fields_of(form_for(dashboard, POPUP + CONFIRM))
    assert row[ORIGIN] == DASHBOARD
    assert row[TAB] == "players"
    # the Players page is the database directory and draws no player control at all: its rows are
    # doors, so there is no second screen whose control could state the path (or a dead carrier).
    assert POPUP + CONFIRM not in directory


@pytest.mark.asyncio
async def test_a_server_row_on_the_default_tab_states_no_tab(config_dir, clean_registry):
    """The Servers tab IS the default, so an unadorned page stays unadorned: no tab field is emitted
    and the origin is the dashboard's own path."""
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        standing = await open_page(http, SERVERS)
    assert TAB not in fields_of(form_for(dashboard, PAUSE))
    assert fields_of(form_for(dashboard, PAUSE))[ORIGIN] == DASHBOARD
    assert fields_of(form_for(standing, PAUSE))[ORIGIN] == SERVERS


@pytest.mark.asyncio
async def test_a_refreshed_players_fragment_keeps_the_tab(config_dir, clean_registry):
    """The live stream re-renders the same table through its own ``/api`` request, so the tab the
    fragment belongs to has to come from the STREAM'S URL — a refreshed control that dropped it would
    undo the fix about two seconds after the page loaded."""
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        fragments = await http.get(f"{live_page.DASHBOARD_FRAGMENTS_PATH}?level=all&tab=players")
        assert fragments.status_code == 200, fragments.text
        state = fragments.json()[live_page.STATE_TARGET]
    row = fields_of(form_for(state, POPUP + CONFIRM))
    assert row[ORIGIN] == DASHBOARD
    assert row[TAB] == "players"


# ============================================================ 2. the cancel link returns to the tab

@pytest.mark.asyncio
async def test_cancel_on_a_players_dialog_goes_back_to_the_players_tab(config_dir, clean_registry):
    """THE REPORT: pressing Cancel must not drop the person on the Servers view. The dialog's Cancel
    anchor (and its ``Esc``, which assigns the same value) carries the tab back with the path."""
    app = build(config_dir, player_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, PLAYERS_TAB)
        row = fields_of(form_for(dashboard, POPUP + CONFIRM))
        dialog = await http.post(POPUP + CONFIRM, data=row)
        assert dialog.status_code == 200, dialog.text
    assert back_of(dialog.text, POPUP + CONFIRM) == PLAYERS_TAB
    # the dialog re-emits BOTH halves, so its own POST returns to the same tab
    assert fields_of(form_for(dialog.text, POPUP))[TAB] == "players"
    assert back_target(dialog.text, POPUP + CONFIRM) == PLAYERS_TAB


@pytest.mark.asyncio
async def test_cancel_on_a_node_dialog_goes_back_to_the_nodes_tab(config_dir, clean_node_registry):
    """The same rule on the node surface: the Nodes tab is a query parameter too."""
    app = build(config_dir, node_fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dashboard = await open_page(http, NODES_TAB)
        row = fields_of(form_for(dashboard, NODE_RESTART + CONFIRM))
        dialog = await http.post(NODE_RESTART + CONFIRM, data=row)
        assert dialog.status_code == 200, dialog.text
    assert back_of(dialog.text, NODE_RESTART + CONFIRM) == NODES_TAB
    assert fields_of(form_for(dialog.text, NODE_RESTART))[TAB] == "nodes"


@pytest.mark.asyncio
async def test_cancel_on_a_default_tab_dialog_still_goes_back_to_the_dashboard(
        config_dir, clean_registry):
    """The other side of the same rule: the Servers tab states no tab, so Cancel is exactly ``/``."""
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        row = fields_of(form_for(dashboard, RESTART + CONFIRM))
        dialog = await http.post(RESTART + CONFIRM, data=row)
        assert dialog.status_code == 200, dialog.text
    assert back_of(dialog.text, RESTART + CONFIRM) == DASHBOARD


# ============================================================ 3. the write returns to the tab

@pytest.mark.asyncio
async def test_a_player_write_pressed_on_the_players_tab_returns_to_the_players_tab(
        config_dir, clean_registry, recording_bot):
    """The redirect half: a popup sent from the Players tab lands back on the Players tab."""
    cluster = player_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, PLAYERS_TAB)
        row = fields_of(form_for(dashboard, POPUP + CONFIRM))
        dialog = await http.post(POPUP + CONFIRM, data=row)
        reply = await http.post(POPUP, data={**row, "message": "hi",
                                             "_confirm_token": confirm_token(dialog.text)})
    assert reply.status_code == 303
    assert reply.headers["location"] == PLAYERS_TAB


@pytest.mark.asyncio
async def test_a_server_write_pressed_on_the_default_tab_returns_to_the_dashboard(
        config_dir, clean_registry, recording_bot):
    """The default tab states no tab, so its write returns to the bare dashboard — never to
    ``/?tab=servers``."""
    cluster = running_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dashboard = await open_page(http, DASHBOARD)
        reply = await http.post(PAUSE, data=fields_of(form_for(dashboard, PAUSE)))
    assert reply.status_code == 303
    assert reply.headers["location"] == DASHBOARD


@pytest.mark.asyncio
async def test_a_node_write_pressed_on_the_nodes_tab_returns_to_the_nodes_tab(
        config_dir, clean_node_registry, recording_bot):
    """And on the node surface, through its dialog."""
    cluster = node_fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dashboard = await open_page(http, NODES_TAB)
        row = fields_of(form_for(dashboard, NODE_RESTART + CONFIRM))
        dialog = await http.post(NODE_RESTART + CONFIRM, data=row)
        reply = await http.post(NODE_RESTART, data={**row,
                                                    "_confirm_token": confirm_token(dialog.text)})
    assert reply.status_code == 303
    assert reply.headers["location"] == NODES_TAB


# ============================================================ 4. the allow-list is unchanged

@pytest.mark.asyncio
@pytest.mark.parametrize("crafted", ["https://evil.example", "//evil.example", "/not-a-page",
                                     "javascript:alert(1)", "", None,
                                     "/players?tab=x",           # a query string on a page path
                                     "/?tab=players",            # the tab's own URL as an origin
                                     "/fleet\r\nLocation: https://evil.example",  # CR/LF injection
                                     "/\\evil", "%2F%2Fevil.example", "/players#frag"])
async def test_a_crafted_origin_is_still_refused_even_with_a_tab_beside_it(
        config_dir, clean_registry, recording_bot, crafted):
    """THE safety line, unchanged by the carrier: a query-carrying origin (the dashboard's OWN tab
    URL included) is not a page path, so the registry refuses it and the section default answers — a
    tab posted beside it does not make it acceptable."""
    cluster = running_fleet()
    app = build(config_dir, cluster)
    data = {"server": "SRS-1", "_csrf_token": "x", TAB: "players"}
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, DASHBOARD)
        data["_csrf_token"] = token_of(page)
        if crafted is not None:
            data[ORIGIN] = crafted
        reply = await http.post(PAUSE, data=data)
    assert reply.status_code == 303
    assert reply.headers["location"] == SERVERS, "a crafted origin must answer the section default"
    location = reply.headers["location"]
    assert "evil" not in location
    assert "\r" not in location and "\n" not in location


@pytest.mark.asyncio
@pytest.mark.parametrize("crafted", ["nonsense", "/players?tab=x", "servers",
                                     "players#frag", "", None])
async def test_a_crafted_tab_is_dropped_and_the_bare_dashboard_answers(
        config_dir, clean_registry, recording_bot, crafted):
    """The tab is a CLOSED VOCABULARY read the same way the dashboard reads it: a value this console
    does not serve becomes the default tab, which leaves the URL unadorned — never an echo of the
    request."""
    cluster = running_fleet()
    app = build(config_dir, cluster)
    data = {"server": "SRS-1", "_csrf_token": "x", ORIGIN: DASHBOARD}
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, DASHBOARD)
        data["_csrf_token"] = token_of(page)
        if crafted is not None:
            data[TAB] = crafted
        reply = await http.post(PAUSE, data=data)
    assert reply.status_code == 303
    assert reply.headers["location"] == DASHBOARD, \
        f"a tab this console does not serve must not reach the browser: {crafted!r}"


@pytest.mark.asyncio
async def test_a_tab_posted_beside_another_page_is_ignored(config_dir, clean_registry, recording_bot):
    """The carrier is meaningful for the dashboard ONLY: a tab posted beside ``/fleet`` (a page whose
    table IS its path) changes nothing."""
    cluster = running_fleet()
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        standing = await open_page(http, SERVERS)
        row = fields_of(form_for(standing, PAUSE))
        reply = await http.post(PAUSE, data={**row, TAB: "nodes"})
    assert reply.status_code == 303
    assert reply.headers["location"] == SERVERS


# ============================================================ 5. the carrier's own vocabulary

def test_the_tab_carrier_is_read_through_the_dashboard_s_own_validator():
    """The one reader the routes and the dialogs share: a tab this console serves is normalised, one
    it does not becomes the default, and the default answers EMPTY so an unadorned URL stays
    unadorned."""
    assert actions_page.posted_tab("players") == "players"
    assert actions_page.posted_tab("  Players  ") == "players"
    assert actions_page.posted_tab("servers") == ""
    assert actions_page.posted_tab(None) == ""
    assert actions_page.posted_tab("") == ""
    assert actions_page.posted_tab("nonsense") == ""
    assert actions_page.posted_tab("/players?tab=x") == ""


def test_the_tab_is_re_attached_to_the_dashboard_only():
    """The composition: the tab reaches the URL for the dashboard's own path and for nothing else."""
    assert actions_page.tabbed_origin(DASHBOARD, "players") == PLAYERS_TAB
    assert actions_page.tabbed_origin(DASHBOARD, "servers") == DASHBOARD
    assert actions_page.tabbed_origin(PLAYERS, "players") == PLAYERS
    assert actions_page.tabbed_origin(NODES, "nodes") == NODES
