"""The server page's Statistics tab: the Statistics page's body for ONE server.

Pins:

* the tab is offered to everyone who may open the server page (a DCS Admin and a hoster manager
  alike) and lives after Players in the strip;
* the read it makes NAMES the server — it is never the installation-wide read, which is what keeps
  the Admin-only page Admin-only;
* a hoster asking for another hoster's server is refused and sees nothing of it, through the page's
  own scoped resolution (the tab adds no rule of its own);
* the figures are the shared body's, and the page-only cards (the per-server comparison table, the
  scope selector) do not appear in the tab;
* the period chips stay on the tab and reach the read;
* ``/statistics`` itself stays Admin-only.
"""
from __future__ import annotations

import pytest

from core.action_results import ActionResult
from services.webservice.pages import statistics as stats_page
from services.webservice.pages import server_detail as detail_page
from test_webui_statistics import payload
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, client, config_dir, fleet, sign_in,
)

MINE = "SRS-1"
THEIRS = "SRS-B"


def stub_read(monkeypatch, data=None, *, success=True, calls=None):
    """Replace the page seam both surfaces read through (``pages.statistics.read_action``)."""
    calls = calls if calls is not None else []

    async def _read(qualname, ctx, /, **params):
        calls.append({"action": qualname, "params": params})
        return ActionResult(success=success, data=data)

    monkeypatch.setattr(stats_page, "read_action", _read)
    return calls


async def page(app, http, path, username):
    """Sign *username* in and GET *path*, returning the response."""
    await sign_in(http, username)
    return await http.get(path)


TAB_PATH = f"/fleet/{MINE}?tab=statistics"


@pytest.mark.asyncio
async def test_a_manager_gets_the_statistics_tab_for_their_own_server(config_dir, monkeypatch):
    """A pure hoster opens the tab and the read is asked for THAT server, not the installation."""
    calls = stub_read(monkeypatch, payload())
    app = build(config_dir, fleet())
    async with client(app) as http:
        response = await page(app, http, TAB_PATH, "hoster")
        assert response.status_code == 200, response.status_code
        body = response.text

    assert calls[-1]["action"] == "get_server_statistics"
    assert calls[-1]["params"]["server"] == MINE, (
        "the tab must never read installation-wide: that is what keeps /statistics Admin-only")
    assert calls[-1]["params"]["period"] == stats_page.DEFAULT_PERIOD


@pytest.mark.asyncio
async def test_the_tab_renders_the_shared_body_and_not_the_page_only_cards(config_dir, monkeypatch):
    """The figures come from the one body; the comparison table and the scope selector do not."""
    stub_read(monkeypatch, payload())
    app = build(config_dir, fleet(), plugins=())
    async with client(app) as http:
        response = await page(app, http, TAB_PATH, "hoster")
    body = response.text
    assert response.status_code == 200, response.status_code
    main = body.split("<main", 1)[-1]

    market = main.split("stt-kpis", 1)[1]
    for figure in ("1,284", "1:22", "187", "38 %", "Caucasus", "Through the Inferno"):
        assert figure in market, f"the shared body is missing {figure}"
    # the page's own cards: one compares SERVERS, the other scopes the whole installation
    assert 'class="stt-table"' not in main, "the per-server comparison table belongs to the page"
    assert f'href="{stats_page.STATISTICS_PATH}?period=' not in main, (
        "the scope selector belongs to the page: this tab is always one server")


@pytest.mark.asyncio
async def test_the_period_chips_stay_on_the_tab_and_reach_the_read(config_dir, monkeypatch):
    """A chip is a plain link back to THIS tab, and the period it names is what the read gets."""
    calls = stub_read(monkeypatch, payload())
    app = build(config_dir, fleet(), plugins=())
    async with client(app) as http:
        await sign_in(http, "hoster")
        response = await http.get(f"{TAB_PATH}&{stats_page.PERIOD_PARAM}=quarter")
        assert response.status_code == 200
        body = response.text

    assert calls[-1]["params"]["period"] == "quarter"
    assert 'class="tab on"' in body
    for name, _label in stats_page.CHIPS:
        assert f"tab=statistics&amp;period={name}" in body or \
               f"tab=statistics&period={name}" in body, f"no chip link for {name}"


@pytest.mark.asyncio
async def test_another_hosters_server_is_refused_through_the_page_scope(config_dir, monkeypatch):
    """The tab adds no scope rule; the page's own refusal is what protects the other server."""
    stub_read(monkeypatch, payload())
    app = build(config_dir, fleet(), plugins=())
    async with client(app) as http:
        await sign_in(http, "hoster")
        response = await http.get(f"/fleet/{THEIRS}?tab=statistics")
        assert response.status_code == 403, response.status_code
        assert THEIRS not in response.text.split("<main", 1)[-1]


@pytest.mark.asyncio
async def test_a_dcs_admin_gets_the_tab_too(config_dir, monkeypatch):
    """The cluster role reads a server's figures without the Admin-only page behind it."""
    stub_read(monkeypatch, payload())
    app = build(config_dir, fleet(), plugins=())
    async with client(app) as http:
        response = await page(app, http, TAB_PATH, "mod1")
        assert response.status_code == 200, response.status_code
        assert stats_page.CARD_DAILY in response.text


@pytest.mark.asyncio
async def test_the_statistics_page_itself_stays_admin_only(config_dir, monkeypatch):
    """Widening what a DCS Admin and a hoster may read did not widen /statistics."""
    stub_read(monkeypatch, payload())
    app = build(config_dir, fleet(), plugins=())
    async with client(app) as http:
        for username in ("mod1", "hoster"):
            await sign_in(http, username)
            response = await http.get(stats_page.STATISTICS_PATH)
            assert response.status_code == 403, f"{username} reached /statistics"


@pytest.mark.asyncio
async def test_the_tab_loads_the_statistics_stylesheet_and_its_neighbours_do_not(
        config_dir, monkeypatch):
    """The cards are styled by the page's own sheet, borrowed only while this tab is in force."""
    stub_read(monkeypatch, payload())
    app = build(config_dir, fleet(), plugins=())
    async with client(app) as http:
        await sign_in(http, "hoster")
        statistics = await http.get(TAB_PATH)
        players = await http.get(f"/fleet/{MINE}?tab=players")

    assert statistics.status_code == 200 and players.status_code == 200
    assert f"/static/{stats_page.STATISTICS_STYLE}" in statistics.text
    assert f"/static/{stats_page.STATISTICS_STYLE}" not in players.text, (
        "every other tab must load exactly what it loaded before")


def test_the_tab_is_offered_to_every_viewer_of_the_page():
    """No capability of its own: whoever may open the page is offered the tab, after Players."""
    confined = detail_page.tabs_for({"DCS"})
    assert detail_page.STATISTICS_TAB in confined
    assert detail_page.STATISTICS_TAB in detail_page.tabs_for({"DCS Admin"})
    assert detail_page.STATISTICS_TAB in detail_page.tabs_for((), manager=True)
    order = detail_page.TAB_ORDER
    assert order.index(detail_page.STATISTICS_TAB) == order.index("players") + 1
    assert detail_page.TAB_LABELS[detail_page.STATISTICS_TAB] is stats_page.NAV_LABEL


def test_the_tab_never_asks_for_the_installation_wide_read():
    """The tab's reader always names a server — an unnamed read is the Admin-only page's scope."""
    source = stats_page.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    tab = text.split("async def tab_context", 1)[1].split("async def statistics_context", 1)[0]
    assert "server=server_name" in tab
    assert stats_page.TAB_KEY == detail_page.STATISTICS_TAB
