"""THE NODES LIST AND THE DASHBOARD'S NODES TAB LINK TO THE NODE PAGE (``/nodes/<node>``).

The node page is the only place a node's own settings are edited, and the row's NAME is the way in:
no cogwheel is drawn beside the strip of row ACTIONS, because a second glyph pointing at the same page
is a navigation duplicate. This module pins the LINK itself, through the REAL app, so the claim cannot
be made without it:

* every ``/nodes`` row's NAME links to ``node_page_url(node)``, and NO row carries a cogwheel;
* every Dashboard Nodes-tab row links to the same URL and carries no cogwheel either;
* NO rendered page links the OLD ``/nodes/<node>/settings`` path any more (a NEGATIVE assertion);
* following a row's link renders the node page with its DEFAULT + instance tabs for THAT node;
* the Admin-only gate still refuses a viewer who may read the list but not open the node page — and
  that viewer gets the name as plain text, not a link.
"""
from __future__ import annotations

import re
import types

import pytest

from services.webservice.pages import node_settings as node_settings_page
from services.webservice.pages import nodes as nodes_page
from test_webui_settings_cogwheels import (BOT_YAML, build, client, fleet, get, settings_hrefs,
                                           sign_in)

#: TWO nodes; ``dcs-a-01`` declares two INSTANCES so the node page opens with DEFAULT + 2 tabs, and
#: ``dcs-b-01`` none. Two nodes is enough to prove the link is PER-ROW (a fixed link would match one).
NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
    Caucasus:
      home: /home/a-caucasus
      bot_port: 6667
      webgui_port: 8087
      dcs_port: 10307
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
"""

#: the row's NAME anchor — the very ``srv-name`` class the Servers/Instances rows use.
ROW_LINK = re.compile(r'<a class="srv-name" href="([^"]+)">(?:<b>)?([^<]*)(?:</b>)?</a>')

#: any surviving link to the OLD per-node settings path (the thing that must be gone).
OLD_SETTINGS = re.compile(r'/nodes/[^/"]+/settings')


@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """The schema validator reads ``COMMAND_LINE_ARGS.node`` (same fixture the sibling tests use)."""
    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    return tmp_path


def row_links(html: str) -> dict[str, str]:
    """``{node name: href}`` for every row-name link on the page."""
    return {name: href for href, name in ROW_LINK.findall(html)}


def _tabs(page: str) -> list[str]:
    """The node page's tab labels (the text before the ``<span class="n">`` note)."""
    nav = re.search(r'<nav class="st-tabs".*?</nav>', page, re.DOTALL)
    assert nav is not None, "the tab strip was not rendered"
    return [re.sub(r"<[^>]+>", "", whole.split('<span class="n">')[0]).strip()
            for whole in re.findall(r'<a class="t[^"]*"[^>]*>(.*?)</a>', nav.group(0), re.DOTALL)]


def _expected() -> dict[str, str]:
    return {name: node_settings_page.node_page_url(name) for name in ("dcs-a-01", "dcs-b-01")}


# ============================================================= the /nodes list

@pytest.mark.asyncio
async def test_the_nodes_list_row_name_is_the_only_door_to_the_node_page(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await get(http, nodes_page.NODES_PATH)
    expected = _expected()
    # the ROW ITSELF (its name) links to the node page...
    assert row_links(html) == expected, row_links(html)
    # ...and NOTHING else on the row does: no cogwheel to the very same URL
    assert settings_hrefs(html) == set(), settings_hrefs(html)
    assert 'class="act-btn cfg"' not in html, "a node row still draws a settings cogwheel"


@pytest.mark.asyncio
async def test_following_the_row_link_renders_the_node_page_with_its_tabs(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        href = row_links(await get(http, nodes_page.NODES_PATH))["dcs-a-01"]
        page = await get(http, href)
    # the DEFAULT tab's own form field for the node that was clicked...
    assert 'name="f.dcs-a-01.listen_address"' in page
    # ...and the DEFAULT + one tab per declared instance
    assert _tabs(page) == ["Node-wide", "Instance Syria", "Instance Caucasus"], _tabs(page)


# ============================================================= the Dashboard's Nodes tab

@pytest.mark.asyncio
async def test_the_dashboard_node_rows_link_to_the_node_page(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await get(http, "/?tab=nodes")
    assert row_links(html) == _expected(), row_links(html)
    assert settings_hrefs(html) == set(), settings_hrefs(html)


@pytest.mark.asyncio
async def test_following_a_dashboard_row_link_renders_the_node_page(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        href = row_links(await get(http, "/?tab=nodes"))["dcs-b-01"]
        page = await get(http, href)
    assert 'name="f.dcs-b-01.listen_address"' in page


# ============================================================= the negative assertion

@pytest.mark.asyncio
async def test_no_rendered_page_links_the_old_node_settings_path(config_dir):
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        listing = await get(http, nodes_page.NODES_PATH)
        dashboard = await get(http, "/?tab=nodes")
        target = await get(http, node_settings_page.node_page_url("dcs-a-01"))
    for name, html in (("nodes list", listing), ("dashboard", dashboard), ("node page", target)):
        assert OLD_SETTINGS.findall(html) == [], f"{name} links the old settings path"
    # and the old URL is still the redirect it always was — a bookmark, not a link
    async with client(app) as http:
        await sign_in(http, "specialk")
        redirected = await http.get("/nodes/dcs-a-01/settings", follow_redirects=False)
    assert redirected.status_code == 307, redirected.status_code
    assert redirected.headers["location"] == node_settings_page.node_page_url("dcs-a-01")


# ============================================================= the Admin-only gate

@pytest.mark.asyncio
async def test_a_dcs_admin_gets_no_row_link_and_the_page_refuses_them(config_dir):
    """A DCS Admin may READ the Nodes list but holds no ``nodes.settings`` (Admin only): no row link
    is offered (never a door the page refuses) and the crafted target answers the console's 403."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await get(http, nodes_page.NODES_PATH)
        refused = await http.get(node_settings_page.node_page_url("dcs-a-01"))
    assert row_links(html) == {}, row_links(html)
    assert settings_hrefs(html) == set(), settings_hrefs(html)
    assert refused.status_code == 403, refused.status_code
