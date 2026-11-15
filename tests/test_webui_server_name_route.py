"""EVERY server name opens its page, an encoded slash included.

The encoding was right (:func:`services.webservice.readmodels.servers.server_url`,
``quote(name, safe='')``); the ROUTE was the defect: ``/fleet/{name}`` is a single path SEGMENT, so
a name containing ``/`` (the encoder's ``%2F``) was decoded by the ASGI server into MORE than one
segment before routing and the default ``{name}`` converter never matched it -- a 404 on the page the
encoder had correctly built. The route's converter is now ``{name:path}`` and NO second decode is
applied: the ASGI server ALREADY decodes, so an ``unquote`` in the handler would double-decode and
corrupt a name that legitimately carries a percent-escape. The link SHAPE is untouched, and the wider
route shape is not a wider lookup -- every name still becomes a server only through ``scoped_server``
against the SCOPED source. Driven through the real app and local login backend."""
from __future__ import annotations

import html
import re
import types
from urllib.parse import quote

import pytest

from services.webservice.pages import instances as instances_page
from services.webservice.pages import players as players_page
from services.webservice.pages import servers as servers_page
from services.webservice.pages import server_detail as detail_page
from services.webservice.pages import settings as settings_page
from services.webservice.readmodels.servers import SERVERS_HREF_PREFIX, server_url
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, client, config_dir, fleet, instance, open_page, server, sign_in,
)

SERVERS = servers_page.SERVERS_PATH
INSTANCES = instances_page.INSTANCES_PATH
PLAYERS = f"{players_page.PLAYERS_PATH}?show=online"

#: every character, in one name each: slash, space, ``&``, ``#``, ``?``, ``%`` and a non-ASCII name.
#: ``/`` is the one the route could not carry before the ``{name:path}`` converter.
ODD_NAMES = (
    "A/B server",              # the slash — the broken one
    "DCS Syria Campaign",      # a space
    "Alpha & Bravo",           # an ampersand
    "SRS #1",                  # a fragment marker
    "What? Really",            # a query marker
    "100% true",               # a percent sign
    "caf\u00e9-1",             # non-ASCII
)


def _hrefs(html_text: str) -> set[str]:
    return set(re.findall(r'href="(/fleet/[^"]+)"', html_text))


def odd_fleet(name: str):
    """A cluster whose only server carries *name*."""
    odd = server(name, ["Host A"])
    node_a = types.SimpleNamespace(
        name="dcs-a-01", master=True,
        instances={"Host-A-Instance": instance("Host-A-Instance", odd)})
    return Cluster([odd], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", odd)])


def player(name: str, **kwargs):
    return types.SimpleNamespace(name=name, ucid=name.lower(), active=True, **kwargs)


# ------------------------------------------------------------------ 1. the route shape and the encoder

def test_the_detail_route_uses_the_catch_all_converter_and_the_link_shape_is_unchanged():
    """The mechanism, stated: ``{name:path}`` — and the emitted link is the encoder's own shape."""
    assert detail_page.SERVER_DETAIL_PATH == "/fleet/{name:path}"
 # the link every screen renders is STILL ``/fleet/<encoded-name>`` (encoder untouched)
    assert server_url("A/B server") == f"{SERVERS_HREF_PREFIX}{quote('A/B server', safe='')}"
    assert server_url("A/B server") == "/fleet/A%2FB%20server"


def test_the_route_declares_the_same_capability_as_before():
    """The wider converter did not change the gate: the page keeps ``servers.view``.

    The module also registers the mission-download route (``missions.view``), the log-window and
    log-download routes (the cluster log panel's own ``logs.view``), the status route (the PAGE's own
    ``servers.view`` — a read of the page's own data), the preset-thumbnail route (``servers.view``),
    the mission-weather read (``servers.view``) and the Music tab's two live endpoints
    (``music.view`` — a stream outlives the render that resolved the server, so it re-checks the
    capability the music area is opened on AND the server's scope in its own route), plus the Scheduler
    review panel's completion hints (the scheduler area's own ``scheduler.view`` — a read of its schema,
    with the server and the section resolved in the route). Those are the only other paths it declares.
    """
    declared = detail_page.capabilities()
    assert declared[detail_page.SERVER_DETAIL_PATH] == detail_page.SERVER_DETAIL_CAPABILITY
    assert detail_page.SERVER_DETAIL_CAPABILITY == servers_page.SERVERS_CAPABILITY
    assert declared == {
        detail_page.SERVER_DETAIL_PATH: detail_page.SERVER_DETAIL_CAPABILITY,
        detail_page.MISSIONS_DOWNLOAD_PATH: detail_page.MISSIONS_CAPABILITY,
        detail_page.LOG_WINDOW_PATH: detail_page.LOG_READ_CAPABILITY,
        detail_page.LOG_DOWNLOAD_PATH: detail_page.LOG_READ_CAPABILITY,
        detail_page.SERVER_STATUS_PATH: detail_page.SERVER_DETAIL_CAPABILITY,
        detail_page.PRESET_IMAGE_PATH: detail_page.SERVER_DETAIL_CAPABILITY,
        detail_page.MISSION_WEATHER_PATH: detail_page.SERVER_DETAIL_CAPABILITY,
        detail_page.MUSIC_TAB_STREAM_PATH: detail_page.music_page_module().CAPABILITY_VIEW,
        detail_page.MUSIC_TAB_FRAGMENTS_PATH: detail_page.music_page_module().CAPABILITY_VIEW,
        detail_page.SCHEDULER_HINTS_PATH: settings_page.SCHEDULER_VIEW_CAPABILITY,
    }


# --------------------------------------------------- 2. every named character round-trips to a page

@pytest.mark.asyncio
@pytest.mark.parametrize("name", ODD_NAMES)
async def test_every_named_character_opens_the_server_page(config_dir, name):
    """``server_url(name)`` requested -> 200, the name shown, the tab strip present."""
    app = build(config_dir, odd_fleet(name))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, server_url(name))
    assert html.escape(name) in page, f"the page did not show {name!r}"
    assert "?tab=overview" in page and "?tab=configuration" in page, "the tab strip is missing"


@pytest.mark.asyncio
async def test_the_encoded_href_as_the_list_renders_it_opens_the_page(config_dir):
    """The href ON THE BYTES (not a hand-built URL) is fetched and lands on the server's page."""
    app = build(config_dir, odd_fleet("A/B server"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        listed = await open_page(http, SERVERS)
        assert 'href="/fleet/A%2FB%20server"' in listed, "the slash name is not linked on the list"
        href = next(h for h in _hrefs(listed) if h.startswith("/fleet/A%2FB"))
        page = await open_page(http, href)
    assert "A/B server" in page, "the page did not show the server's name"


@pytest.mark.asyncio
async def test_the_slash_name_links_from_all_three_tables_and_opens_the_config_tab(config_dir):
    """The rendered href -> GET -> 200 + tab strip on Servers, Instances, Players, and the
    Configuration tab is reachable for an Admin."""
    cluster = odd_fleet("A/B server")
    cluster.servers[0].players = {"u": player("Odd-P")}
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        for path in (SERVERS, INSTANCES, PLAYERS):
            screen = await open_page(http, path)
            hrefs = {h for h in _hrefs(screen) if h.startswith("/fleet/A%2FB")}
            assert hrefs, f"{path} rendered no link to the slash name"
            for href in hrefs:
                page = await open_page(http, href)
                assert "A/B server" in page, (path, href)
                assert "?tab=overview" in page, "the tab strip is missing"
        config = await open_page(http, server_url("A/B server", tab="configuration"))
    assert "?tab=configuration" in config, "the configuration tab did not render"


# ---------------------------------------------------------------- 3. the scope (proven, not assumed)

def scoped_cluster():
    """A slash-named server on each host — the two sides of the scope."""
    a = server("A/slash", ["Host A"])
    b = server("B/slash", ["Host B"])
    return Cluster([a, b], nodes={}, node=types.SimpleNamespace(name="n", master=True))


@pytest.mark.asyncio
async def test_a_slash_name_out_of_scope_is_refused_even_if_it_resolves(config_dir):
    """The wider route shape accepts MORE SHAPES, never MORE SERVERS: the scoped lookup is the gate."""
    app = build(config_dir, scoped_cluster())
    async with client(app) as http:
        await sign_in(http, "hoster-b")           # manages Host B only
        assert (await http.get(server_url("B/slash"))).status_code == 200, "their OWN slash server"
        refused = await http.get(server_url("A/slash"))
        assert refused.status_code == 403, refused.status_code
        assert "A/slash" not in refused.text, "the refusal disclosed the out-of-scope name"

 # the mirror: the other manager, the other server
        await sign_in(http, "hoster")             # manages Host A only
        assert (await http.get(server_url("A/slash"))).status_code == 200
        assert (await http.get(server_url("B/slash"))).status_code == 403


@pytest.mark.asyncio
async def test_the_unencoded_slash_form_resolves_the_same_in_scope_server_and_still_refuses(
        config_dir):
    """The converter also matches a RAW slash; that is a SHAPE, not a way past the scope."""
    app = build(config_dir, scoped_cluster())
    async with client(app) as http:
        await sign_in(http, "hoster")             # manages Host A
        assert (await http.get("/fleet/A/slash")).status_code == 200, "in-scope raw slash name"
        assert (await http.get("/fleet/B/slash")).status_code == 403, "out-of-scope raw slash name"


# ------------------------------------------------------------- 4. refusals that are never a 500

@pytest.mark.asyncio
async def test_a_name_that_does_not_exist_is_a_refusal_never_a_500(config_dir):
    """Scoped caller -> 403 (no disclosure); unscoped Admin -> the honest 404."""
    app = build(config_dir, odd_fleet("Real"))
    async with client(app) as http:
        await sign_in(http, "specialk")           # Admin, unscoped
        for bad in ("/fleet/Does%20Not%20Exist", "/fleet/Ghost%2Fname"):
            response = await http.get(bad)
            assert response.status_code == 404, (bad, response.status_code)

    app = build(config_dir, odd_fleet("Real"))
    async with client(app) as http:
        await sign_in(http, "mod1")               # DCS Admin scoped to Host A
        for bad in ("/fleet/Does%20Not%20Exist", "/fleet/Ghost%2Fname"):
            assert (await http.get(bad)).status_code == 403, bad


@pytest.mark.asyncio
async def test_a_malformed_percent_sequence_is_a_refusal_not_a_stack_trace(config_dir):
    """``%ZZ`` / an empty name are refused by the scoped lookup, never a 500.

    ``/fleet/`` is NOT one of them: with nothing after the slash it is the page's own address with
    a trailing slash, and the shell answers it with a ``308`` to ``/fleet``. Fronting the redirect
    moved that case out of this loop and into its own assertion, so the refusal rule keeps only
    the values that are genuinely malformed.
    """
    app = build(config_dir, odd_fleet("Real"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        for bad in ("/fleet/%ZZ", "/fleet/%"):
            response = await http.get(bad)
            assert response.status_code in (404, 403), (bad, response.status_code)
        redirected = await http.get("/fleet/", follow_redirects=False)
        assert redirected.status_code == 308, "an empty name is the page's slash form, not a name"
        assert redirected.headers["location"] == "/fleet"
        followed = await http.get("/fleet/", follow_redirects=True)
        assert followed.status_code == 200, "/fleet/ no longer reaches the fleet page"


@pytest.mark.asyncio
async def test_a_name_holding_a_literal_percent_escape_is_not_double_decoded(config_dir):
    """The no-second-decode decision: ``100%2F x`` is a NAME, not a path that hides ``100/ x``."""
    literal = "100%2F x"
    app = build(config_dir, odd_fleet(literal))
    async with client(app) as http:
        await sign_in(http, "specialk")
        assert server_url(literal) == "/fleet/100%252F%20x"
        page = await open_page(http, server_url(literal))
    assert html.escape(literal) in page, "the literal-percent name did not round-trip to itself"


# ------------------------------------------------------------------ 5. the tab request is unchanged

@pytest.mark.asyncio
async def test_a_dcs_admin_is_refused_the_configuration_tab_of_a_slash_name(config_dir):
    """The tab's own gate is untouched by the route change: no capability, no tab, 403 if asked."""
    app = build(config_dir, odd_fleet("A/B server"))
    async with client(app) as http:
        await sign_in(http, "mod1")               # DCS Admin — no servers.config
        page = await open_page(http, server_url("A/B server"))
        assert "?tab=configuration" not in page, "a DCS Admin must not be offered the tab"
        refused = await http.get(server_url("A/B server", tab="configuration"))
        assert refused.status_code == 403, refused.status_code
