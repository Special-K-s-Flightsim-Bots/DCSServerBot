"""The Logs page: one linkable, bookmarkable place to read the log at full width.

It is the dashboard's log card rendered from the SAME partials, with its own route and its own
capability, so "make the log bigger" is a link rather than a second viewer. Pins: the route exists and
answers GET only; the nav entry and the gate read the same capability string; a role without it is
refused; the level filter points at THIS page; the placement tags are absent because the log is the
page."""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import permissions, readmodels
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import logs as logs_page
from test_webui_dashboard import BOT_YAML, _build, _login, _source, node, server

LOGS_HTML = (Path(logs_page.__file__).parent.parent / "templates" / "logs.html")
LOGS_JS = (Path(logs_page.__file__).parent.parent / "static" / "shell.js")


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


async def _page(app, query: str = "") -> str:
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.get(f"{logs_page.LOGS_PATH}{query}")
    assert response.status_code == 200, (query, response.status_code)
    return response.text


@pytest.mark.asyncio
async def test_a_staff_role_gets_the_page_and_another_gets_403(config_dir):
    """The gate is the capability the sidebar advertises, checked with the shell's own predicate."""
    app, registrar = _build(config_dir, users=[
        {"username": "specialk", "password_hash": _dashboard_hash(),
         "roles": list(logs_page.LOGS_ROLES)},
        {"username": "nobody", "password_hash": _dashboard_hash(), "roles": ["GameMaster"]},
    ])
    app.state.webui_source_provider = lambda: readmodels.EmptySource()
    assert await _page(app)

    async with asgi_client(app) as client:
        await _login(client, "nobody")
        refused = await client.get(logs_page.LOGS_PATH)
    assert refused.status_code == 403, "a role the page does not name must be refused"

 # the route reads ONE capability string, and the Logs page has NO nav entry — its content is the
 # dashboard's own log panel, so a role that may open the dashboard is offered no Logs entry at
 # all (the same predicate the gate refused it with).
    assert logs_page.capabilities()[logs_page.LOGS_PATH] == logs_page.LOGS_CAPABILITY
    for roles in (set(logs_page.LOGS_ROLES), {"DCS Admin"}):
        links = [link["url"] for group in dashboard_page.nav_groups(registrar, roles, current="/")
                 for link in group["links"]]
        assert "/logs" not in links, "the Logs page must have no menu entry"


def _dashboard_hash() -> str:
    from test_webui_dashboard import _hash
    return _hash()


@pytest.mark.asyncio
async def test_the_page_is_off_the_menu(config_dir):
    """The Logs page has no sidebar entry: the log lives on the dashboard, so /logs is a standalone
    page reached by URL, never from the menu."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: readmodels.EmptySource()
    html = await _page(app)
    assert f'href="{logs_page.LOGS_PATH}"' not in html.split("</aside>")[0], \
        "the Logs page must not appear in the sidebar"


@pytest.mark.asyncio
async def test_the_page_renders_the_log_at_full_width_without_the_state_panel(config_dir, tmp_path):
    """Same card, same partials, no tab strip and no list of servers."""
    log_file = tmp_path / "bot.log"
    log_file.write_text("2026-09-27 10:00:00.000 INFO\tCache warm\n", encoding="utf-8")
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server()], node_obj=node(instances={}), log_path=log_file)
    html = await _page(app)
    assert 'id="log-panel"' in html and 'id="log-body"' in html
    assert "Cache warm" in html
    assert 'class="tabs"' not in html, "the tab strip is the dashboard's, not this page's"
    assert 'id="state-panel"' not in html
    assert 'class="console ' not in html, "the log is not a column here, it is the page"
    assert "log-card" in html


@pytest.mark.asyncio
async def test_the_page_gives_the_log_the_page_height_not_the_panel_box(config_dir, tmp_path):
    """The linkable "pop out" has to be the BIG-log home: the page carries the hook and the
    stylesheet gives that hook a taller box than the dashboard panel's 320px."""
    log_file = tmp_path / "bot.log"
    log_file.write_text("2026-09-27 10:00:00.000 INFO\tCache warm\n", encoding="utf-8")
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[server()], node_obj=node(instances={}), log_path=log_file)
    html = await _page(app)
    assert 'class="logs-page"' in html, "the page lost the hook the stylesheet sizes the log by"

    sheet = (Path(logs_page.__file__).parent.parent / "static" / "shell.css").read_text("utf-8")
    match = re.search(r"\.logs-page \.log-card \.log\s*\{([^}]*)\}", sheet)
    assert match, "the stylesheet no longer sizes the Logs page's log"
    assert "max-height" in match.group(1), "the hook exists but sizes nothing"
    assert "320px" not in match.group(1), "that is the dashboard panel's box, not the page's"


@pytest.mark.asyncio
async def test_the_level_filter_stays_on_this_page(config_dir):
    """`?level=` links point at /logs: filtering the log must not bounce the reader elsewhere."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: readmodels.EmptySource()
    html = await _page(app, "?level=warning")
    assert re.search(r'href="/logs\?level=all"', html)
    assert re.search(r'href="/\?level=all"', html) is None, \
        "the dashboard's level links belong to the dashboard"
    assert re.search(r'class="tag a">WARNING\+</a>', html), "the level in force is marked"


@pytest.mark.asyncio
async def test_the_page_offers_no_placement_control(config_dir):
    """The log IS the page: offering `?log=` would be a control that lies."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: readmodels.EmptySource()
    html = await _page(app)
    assert 'class="logpos"' not in html
    assert "?log=" not in html


@pytest.mark.asyncio
async def test_the_page_is_get_only_and_owns_no_write_handler(config_dir):
    _app, registrar = _build(config_dir)
    routes = [route for route in registrar.routes
              if getattr(route, "path", None) == logs_page.LOGS_PATH]
    assert routes, "floor: the route is registered in the registrar"
    for route in routes:
        assert set(route.methods or ()) == {"GET"}, f"the logs page answers {route.methods}"
    source = Path(logs_page.__file__).read_text(encoding="utf-8")
    for verb in ("@router.post", "@router.put", "@router.patch", "@router.delete"):
        assert verb not in source


@pytest.mark.asyncio
async def test_the_page_renders_without_any_data_at_all(config_dir):
    """No bot, no source: the shell, the log card, and the missing-log sentence."""
    app, _registrar = _build(config_dir)
    html = await _page(app)
    assert readmodels.LOG_MISSING_MESSAGE in html
    assert "Logs" in html
    assert 'id="live-status"' in html
