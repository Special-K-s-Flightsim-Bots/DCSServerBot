"""The server NAME in a list row is the link INTO the server's own page.

The per-server page existed but nothing linked to ``/fleet/{name}``. The name becomes the affordance
on the Servers list and the dashboard's Servers tab (one table component, so the same markup). Pins:
the ONE encoder (:func:`services.webservice.readmodels.servers.server_url`) percent-encodes every
reserved character a name may carry and the config write's redirect builds the SAME url, so the two
cannot drift; the byte-level ``href="/fleet/<encoded>"`` is present; a server outside a manager's
scope is absent from the markup (no link built) and a crafted URL naming it is still refused; and a
name with a space, an ``&`` and a ``#`` round-trips to the right page. The same defaulted ``link``
rides on the Instances and Players tables. Driven through the real app and local login backend."""
from __future__ import annotations

import types
from urllib.parse import quote

import pytest

from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import instances as instances_page
from services.webservice.pages import players as players_page
from services.webservice.pages import servers as servers_page
from services.webservice.readmodels.instances import instance_views
from services.webservice.readmodels.model import (InstanceView, PlayerView, ServerView,
                                                  status_view)
from services.webservice.readmodels.players import player_views
from services.webservice.readmodels.servers import server_url, server_view
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, client, config_dir, fleet, instance, open_page, server, sign_in,
)

SERVERS = servers_page.SERVERS_PATH
DASHBOARD = dashboard_page.DASHBOARD_PATH
INSTANCES = instances_page.INSTANCES_PATH
PLAYERS = f"{players_page.PLAYERS_PATH}?show=online"

#: every reserved character, in one name — the encoder's whole job
ODD_NAME = "Alpha & Bravo #1"

#: the exact markup one server row's first cell renders (the name IS the link)
def name_cell(name: str, href: str) -> str:
    return f'<td><b><a class="srv-name" href="{href}">{name}</a></b>'


def encoded(name: str) -> str:
    return server_url(name)


# ------------------------------------------------------------------ 1. the ONE encoder

def test_server_url_percent_encodes_every_reserved_character():
    """Space, ``&``, ``#``, ``?`` and ``/`` all travel in the path segment, encoded."""
    cases = {
        "DCS Syria Campaign": "/fleet/DCS%20Syria%20Campaign",
        "Alpha & Bravo": "/fleet/Alpha%20%26%20Bravo",
        "SRS #1": "/fleet/SRS%20%231",
        "What? Really": "/fleet/What%3F%20Really",
        "A/B server": "/fleet/A%2FB%20server",
    }
    for name, expected in cases.items():
        assert server_url(name) == expected, name
 # and it IS the same encoding the config action uses (one encoder)
        assert server_url(name) == f"/fleet/{quote(str(name), safe='')}", name


def test_server_url_appends_the_tab_as_a_query_pair():
    assert server_url("SRS-1", tab="configuration") == \
        "/fleet/SRS-1?tab=configuration"
    assert server_url("SRS-1") == "/fleet/SRS-1", "no tab means no query string"


def test_the_config_redirect_and_the_row_link_build_the_same_url():
    """ONE encoder: the write's redirect and the row's link cannot disagree for the same name."""
    for name in ("SRS-1", ODD_NAME, "DCS Syria Campaign"):
        assert actions_page._config_back_path(name) == \
            server_url(name, tab="configuration")


def test_a_row_without_a_link_renders_plain_text():
    """The field is defaulted, so a producer without a link still builds a row (no link)."""
    row = ServerView(name="X", node_name="n", instance_name=None,
                     status=status_view("Running"), port=None, mission_name=None,
                     mission_seconds=0, players=0, active_players=0, remote=False)
    assert row.link == ""
    assert server_view(types.SimpleNamespace(name="X")).link == "/fleet/X"


# ------------------------------------------------------- 2. the link, on both screens' bytes

def _hrefs(html: str) -> set[str]:
    import re

    return set(re.findall(r'href="(/fleet/[^"]+)"', html))


@pytest.mark.asyncio
async def test_the_servers_page_renders_the_name_as_a_link_to_its_page(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, SERVERS)
    assert name_cell("SRS-1", "/fleet/SRS-1") in html
    assert name_cell("SRS-B", "/fleet/SRS-B") in html
    assert {"/fleet/SRS-1", "/fleet/SRS-B"} <= _hrefs(html)


@pytest.mark.asyncio
async def test_the_dashboard_servers_tab_renders_the_same_link(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, f"{DASHBOARD}?tab=servers")
    assert name_cell("SRS-1", "/fleet/SRS-1") in html
    assert "/fleet/SRS-B" in _hrefs(html)


@pytest.mark.asyncio
async def test_a_dcs_admin_also_gets_the_link_on_their_own_servers(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, SERVERS)
    assert "/fleet/SRS-1" in _hrefs(html)


# ------------------------------------------------------------------- 3. the scope (the point)

@pytest.mark.asyncio
async def test_a_server_outside_the_viewers_scope_gets_no_link_and_no_name(config_dir):
    """The hoster's filtered list is the ONLY list: an out-of-scope server is absent from the bytes.

    Rendered as bytes, not read off the template: the scoped source has already removed the server,
    so its row — and therefore any link to it — does not exist. The link is never *built* for a
    server the viewer may not see, which is the property that matters.
    """
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster-b")           # a pure MANAGER of Host B
        html = await open_page(http, SERVERS)

    assert "/fleet/SRS-B" in _hrefs(html), "their OWN server must be linked"
    assert "SRS-1" not in html, "the out-of-scope server's name leaked into the page"
    assert "/fleet/SRS-1" not in html, "a link to an out-of-scope server was rendered"

    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster")              # the mirror: a manager of Host A
        other = await open_page(http, SERVERS)
    assert "/fleet/SRS-1" in _hrefs(other)
    assert "SRS-B" not in other and "/fleet/SRS-B" not in other


@pytest.mark.asyncio
async def test_the_route_keeps_its_refusal_for_a_name_out_of_scope(config_dir):
    """Crafting the URL does not get in: the scoped lookup refuses exactly as before the change."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster-b")
        refused = await http.get("/fleet/SRS-1")
 # 403 (refused, indistinguishable from a name that does not exist) — never the page
        assert refused.status_code == 403, refused.status_code
        assert "<h2>SRS-1" not in refused.text and "?tab=overview" not in refused.text
 # a scoped caller is not told whether the name exists either
        assert (await http.get("/fleet/Does%20Not%20Exist")).status_code == 403


# --------------------------------------------------------- 4. the round trip for an odd name

def odd_fleet():
    """A cluster whose only server carries a space, an ``&`` and a ``#`` in its name."""
    odd = server(ODD_NAME, ["Host A"])
    node_a = types.SimpleNamespace(
        name="dcs-a-01", master=True,
        instances={"Host-A-Instance": instance("Host-A-Instance", odd)})
    return Cluster([odd], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", odd)])


@pytest.mark.asyncio
async def test_an_odd_name_round_trips_from_the_list_to_the_page(config_dir):
    app = build(config_dir, odd_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, SERVERS)
        href = encoded(ODD_NAME)
        assert f'href="{href}"' in html, "the encoded link is not on the list"

 # FOLLOW the link: the server page opens (not a 404, not a truncated name)
        page = await open_page(http, href)
        assert "Alpha &amp; Bravo #1" in page, "the page did not show the server's name"
        assert '?tab=overview' in page and '?tab=configuration' in page, "the tab strip is missing"
 # the page's OWN tab links use the same encoder, so the tabs work for an odd name too
        assert f'href="{href}?tab=players"' in page


@pytest.mark.asyncio
async def test_an_odd_name_opens_the_configuration_tab_for_an_admin(config_dir):
    app = build(config_dir, odd_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        config = await open_page(http, f"{encoded(ODD_NAME)}?tab=configuration")
    assert actions_page.SERVER_CONFIG_ACTIONS[0].path in config, "the config form is not rendered"


@pytest.mark.asyncio
async def test_a_dcs_admin_gets_the_same_page_without_the_configuration_tab(config_dir):
    app = build(config_dir, odd_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, encoded(ODD_NAME))
        assert "?tab=configuration" not in page, "a DCS Admin must not be offered the tab"
        assert "?tab=overview" in page
        refused = await http.get(f"{encoded(ODD_NAME)}?tab=configuration")
        assert refused.status_code == 403, refused.status_code


# ==============================================================================================
# the SAME link on the Instances and Players tables: these two tables carry the server name as a
# COLUMN, so they get the same affordance through a defaulted ``link`` field on the two records.
# ==============================================================================================

def player(name: str, **kwargs):
    """A player double shaped like ``Player`` where the players read model reads it."""
    return types.SimpleNamespace(name=name, ucid=name.lower(), active=True, **kwargs)


def server_name_cell(name: str, href: str) -> str:
    """The exact markup a server-name cell renders when it IS the link.

    The DISPLAYED name is HTML-escaped by the template (``&`` -> ``&amp;``), so the expected
    string is escaped the same way; the ``href`` is the encoder's own output.
    """
    import html

    return f'<td><a class="srv-name" href="{href}">{html.escape(name)}</a></td>'


def games():
    """The :func:`fleet` cluster WITH players, so the Players table has a row on each scope side."""
    cluster = fleet()
    cluster.servers[0].players = {"u1": player("Ghost-9")}    # on SRS-1 (Host A)
    cluster.servers[1].players = {"u2": player("Falcon-B")}   # on SRS-B (Host B)
    return cluster


# ------------------------------------------------------ 1. the field and the encoder (pure)

def test_instance_and_player_rows_carry_the_link_from_the_one_encoder():
    """Both new rows are built by ``server_url`` — no second URL shape."""
    bound = types.SimpleNamespace(name="SRS & One")
    rows = {row.name: row for row in instance_views(
        {"Host-A-Instance": instance("Host-A-Instance", bound),
         "Idle-Instance": instance("Idle-Instance")})}
    assert rows["Host-A-Instance"].link == server_url("SRS & One") == "/fleet/SRS%20%26%20One"
    assert rows["Host-A-Instance"].server_name == "SRS & One"
 # an IDLE instance has no server to open: no link, and the cell renders plain text
    assert rows["Idle-Instance"].link == "" and rows["Idle-Instance"].server_name is None

    players = player_views("SRS & One", {"u": player("Viper")})
    assert players[0].link == server_url("SRS & One")


def test_an_instance_or_player_row_without_a_link_renders_plain_text():
    """The field is defaulted, so a producer without a link still builds a row (no link)."""
    row = InstanceView(name="Host-A-Instance", node_name="dcs-a-01", server_name="SRS-1",
                       dcs_port=None, webgui_port=None)
    assert row.link == ""
    prow = PlayerView(name="Viper", ucid="viper", server_name="SRS-1", slot="", active=True)
    assert prow.link == ""


# ------------------------------------------------------ 2. the link, on both screens' bytes

@pytest.mark.asyncio
async def test_the_instances_page_renders_the_server_name_as_a_link(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, INSTANCES)
    assert server_name_cell("SRS-1", "/fleet/SRS-1") in html
    assert server_name_cell("SRS-B", "/fleet/SRS-B") in html
    assert {"/fleet/SRS-1", "/fleet/SRS-B"} <= _hrefs(html)


@pytest.mark.asyncio
async def test_the_players_page_renders_the_server_name_as_a_link(config_dir):
    app = build(config_dir, games())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, PLAYERS)
    assert server_name_cell("SRS-1", "/fleet/SRS-1") in html
    assert server_name_cell("SRS-B", "/fleet/SRS-B") in html


@pytest.mark.asyncio
async def test_the_dashboard_instances_and_players_tabs_render_the_same_link(config_dir):
    """The dashboard's tabs render the ONE table component, so they carry the link too."""
    app = build(config_dir, games())
    async with client(app) as http:
        await sign_in(http, "specialk")
        inst = await open_page(http, f"{DASHBOARD}?tab=instances")
        ply = await open_page(http, f"{DASHBOARD}?tab=players")
    assert server_name_cell("SRS-1", "/fleet/SRS-1") in inst
    assert server_name_cell("SRS-1", "/fleet/SRS-1") in ply


# ------------------------------------------------------ 3. the scope (the point, both screens)

@pytest.mark.asyncio
async def test_an_out_of_scope_server_gets_no_link_on_the_instances_screen(config_dir):
    """The hoster's filtered instance list is the ONLY list: the other server is gone from the bytes."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster-b")
        html = await open_page(http, INSTANCES)
        refused = await http.get("/fleet/SRS-1")
    assert server_name_cell("SRS-B", "/fleet/SRS-B") in html, "their OWN server must be linked"
    assert "SRS-1" not in html, "the out-of-scope server's name leaked into the Instances page"
    assert "/fleet/SRS-1" not in html, "a link to an out-of-scope server was rendered"
 # the crafted URL for the name that is out of scope still refuses — not a 200 with their data
    assert refused.status_code == 403, refused.status_code
    assert "<h2>SRS-1" not in refused.text

    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster")              # the mirror: a manager of Host A
        other = await open_page(http, INSTANCES)
    assert server_name_cell("SRS-1", "/fleet/SRS-1") in other
    assert "SRS-B" not in other and "/fleet/SRS-B" not in other


@pytest.mark.asyncio
async def test_an_out_of_scope_server_gets_no_link_on_the_players_screen(config_dir):
    app = build(config_dir, games())
    async with client(app) as http:
        await sign_in(http, "hoster-b")
        html = await open_page(http, PLAYERS)
        refused = await http.get("/fleet/SRS-1")
    assert server_name_cell("SRS-B", "/fleet/SRS-B") in html
    assert "Ghost-9" not in html, "an out-of-scope player leaked into the Players screen"
    assert "/fleet/SRS-1" not in html
    assert refused.status_code == 403, refused.status_code

    app = build(config_dir, games())
    async with client(app) as http:
        await sign_in(http, "hoster")
        other = await open_page(http, PLAYERS)
    assert server_name_cell("SRS-1", "/fleet/SRS-1") in other
    assert "Falcon-B" not in other and "/fleet/SRS-B" not in other


# ------------------------------------------------------ 4. an odd name, from BOTH tables

@pytest.mark.asyncio
async def test_an_odd_name_round_trips_from_both_tables(config_dir):
    """A name with a space, ``&`` and ``#`` links and opens correctly from Instances and Players.

    The link is the same URL the Servers list produces; the slash case is covered by
    ``tests/test_webui_server_name_route.py``.
    """
    cluster = odd_fleet()
    cluster.servers[0].players = {"u": player("Odd-P")}
    app = build(config_dir, cluster)
    href = encoded(ODD_NAME)
    cell = server_name_cell(ODD_NAME, href)
    async with client(app) as http:
        await sign_in(http, "specialk")
        for path in (INSTANCES, PLAYERS):
            html = await open_page(http, path)
            assert cell in html, (path, cell)
        page = await open_page(http, href)
    assert "Alpha &amp; Bravo #1" in page, "the page did not show the server's name"
    assert "?tab=overview" in page and "?tab=configuration" in page, "the tab strip is missing"
