"""The bot log belongs to the Admin role only -- the panel, the Logs page AND the live stream.

The capability ``logs.view`` names ``("Admin",)`` and nothing else. The dashboard's Recent log panel
is OMITTED for anyone else (the container, level filter and position tags are absent from the HTML,
not hidden), the ``Logs`` nav entry is absent, and a direct request to ``/logs`` or the dashboard with
``?log=expanded`` is refused with the console's existing refusal face. The live stream sends no
``log-panel`` fragment to a session that may not read the log and refuses a log request, while an
Admin still receives log frames and the allowed state keeps flowing. Runs in-process from a stub
source."""
from __future__ import annotations

import json
import re
import types
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from conftest import stub_identity, asgi_client, create_app, install_shell, permissions
from services.webservice import readmodels
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import live as live_page
from services.webservice.pages import logs as logs_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login, _source, node, server

TEMPLATES = Path(dashboard_page.__file__).parent.parent / "templates"

ADMIN = {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]}
DCS_ADMIN = {"username": "mod1", "password_hash": _hash(), "roles": ["DCS Admin"]}

LOG_ROW = "2026-09-24 21:33:12.123 INFO\tthe log line only an admin may read"


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _log(tmp_path) -> Path:
    path = tmp_path / "dcssb-dcs-master-01.log"
    path.write_text(LOG_ROW + "\n", encoding="utf-8")
    return path


def _app(config_dir, users, tmp_path, *, log=None):
    app, registrar = _build(config_dir, users=users)
    source = _source(servers=[server()], node_obj=node(instances={}),
                     log_path=log if log is not None else _log(tmp_path))
    app.state.webui_source_provider = lambda: source
    return app, registrar


async def _get(app, username, url, *, accept="text/html"):
    """One request as *username*, with the Accept header the caller would send."""
    async with asgi_client(app) as client:
        assert (await _login(client, username)).status_code == 303
        return await client.get(url, headers={"accept": accept})


def _sidebar(html: str) -> str:
    return html.split("</aside>")[0]


# ------------------------------------------------------------------ the capability is the source

def test_the_capability_table_is_where_the_log_is_narrowed():
    """ONE declaration: ``logs.view`` is the Admin role and nothing else — not the dashboard tuple."""
    assert permissions.roles_for_capability("logs.view") == ("Admin",)
    assert logs_page.LOGS_ROLES == ("Admin",), \
        "the log's roles must not alias the dashboard's (DCS Admin / DCS may read the console)"
    assert dashboard_page.may_read_log({"Admin"}) is True
    for roles in ({"DCS Admin"}, {"DCS"}, set(), {"GameMaster"}, {"DCS Admin", "DCS"}):
        assert dashboard_page.may_read_log(roles) is False, roles


def test_the_templates_do_not_special_case_a_role_string():
    """The decision lives in the capability table, never in a template that compares a role name.

    Comments are stripped first (a template may *say* which capability it renders by), so what is
    checked is the executable markup: no template names a role or the capability in a condition.
    """
    for name in ("dashboard.html", "_log_card.html", "_log_panel.html", "_state.html"):
        text = re.sub(r"\{#.*?#\}", "", (TEMPLATES / name).read_text(encoding="utf-8"), flags=re.S)
        assert '"Admin"' not in text and "'Admin'" not in text, name
        assert "logs.view" not in text, name
        assert "role" not in text.lower(), name


# ------------------------------------------------------ the dashboard: present for Admin only

@pytest.mark.asyncio
async def test_an_admin_still_gets_the_panel_filters_and_position_control(config_dir, tmp_path):
    app, _registrar = _app(config_dir, [ADMIN], tmp_path)
    response = await _get(app, "specialk", dashboard_page.DASHBOARD_PATH)
    assert response.status_code == 200
    html = response.text
    assert 'id="log-panel"' in html and 'id="log-body"' in html
    assert 'id="log-level"' in html, "the level filter rides with the panel"
    assert 'class="logpos"' in html, "the position control rides with the panel"
    assert "Recent log" in html and LOG_ROW.split("\t")[1] in html


@pytest.mark.asyncio
async def test_a_non_admin_gets_a_dashboard_with_no_log_markup_at_all(config_dir, tmp_path):
    """ABSENT, not hidden: no container, no level filter, no position tags, no log text."""
    app, _registrar = _app(config_dir, [DCS_ADMIN], tmp_path)
    response = await _get(app, "mod1", dashboard_page.DASHBOARD_PATH)
    assert response.status_code == 200, "the dashboard is theirs — only the log is not"
    html = response.text
    assert 'id="log-panel"' not in html
    assert 'id="log-body"' not in html
    assert 'id="log-level"' not in html
    assert 'class="logpos"' not in html
    assert "Recent log" not in html
    assert "the log line only an admin may read" not in html
 # the rest of the dashboard is intact: the state panel and the pills are still rendered
    assert 'id="state-panel"' in html and 'id="status-pills"' in html
    assert "Training Caucasus" in html


@pytest.mark.asyncio
async def test_a_non_admin_is_not_offered_the_logs_entry(config_dir, tmp_path):
    app, registrar = _app(config_dir, [DCS_ADMIN], tmp_path)
    html = (await _get(app, "mod1", dashboard_page.DASHBOARD_PATH)).text
    assert 'href="/logs"' not in _sidebar(html)
    assert "/logs" not in _sidebar(html)
 # the nav machinery itself answers, computed with the same predicate the gate uses — and the
 # Logs page has NO menu entry for anyone now (the log lives on the dashboard).
    links = [link["url"] for group in dashboard_page.nav_groups(registrar, {"DCS Admin"},
                                                                current="/")
             for link in group["links"]]
    assert logs_page.LOGS_PATH not in links, "the Logs page must have no menu entry"
    admin_links = [link["url"] for group in dashboard_page.nav_groups(registrar, {"Admin"},
                                                                     current="/")
                   for link in group["links"]]
    assert logs_page.LOGS_PATH not in admin_links, \
        "the Logs page must have no menu entry, not even for the Admin"


# ------------------------------------------------------- direct requests are refused (two faces)

@pytest.mark.asyncio
async def test_the_logs_page_refuses_a_non_admin_with_the_refusal_face(config_dir, tmp_path):
    app, _registrar = _app(config_dir, [DCS_ADMIN], tmp_path)
    browser = await _get(app, "mod1", logs_page.LOGS_PATH)
    assert browser.status_code == 403
    assert "text/html" in browser.headers.get("content-type", ""), "a browser gets the page"
    assert "Access denied" in browser.text
    assert "the log line only an admin may read" not in browser.text

    api = await _get(app, "mod1", logs_page.LOGS_PATH, accept="application/json")
    assert api.status_code == 403
    assert api.json()["detail"], "an API caller keeps the JSON body it has always read"
    assert "the log line only an admin may read" not in api.text


@pytest.mark.asyncio
async def test_expanded_mode_on_the_dashboard_is_refused_for_a_non_admin(config_dir, tmp_path):
    """`?log=expanded` makes the log the page, so it is a request for the log itself."""
    app, _registrar = _app(config_dir, [DCS_ADMIN], tmp_path)
    browser = await _get(app, "mod1", "/?log=expanded")
    assert browser.status_code == 403
    assert "text/html" in browser.headers.get("content-type", "")
    assert "Access denied" in browser.text
    assert 'id="log-panel"' not in browser.text
    assert "the log line only an admin may read" not in browser.text

    api = await _get(app, "mod1", "/?log=expanded", accept="application/json")
    assert api.status_code == 403 and api.json()["detail"]


@pytest.mark.asyncio
async def test_an_admin_keeps_expanded_mode(config_dir, tmp_path):
    """The positive control for the two refusals above."""
    app, _registrar = _app(config_dir, [ADMIN], tmp_path)
    response = await _get(app, "specialk", "/?log=expanded")
    assert response.status_code == 200
    assert "log-card expanded" in response.text
    assert 'id="log-panel"' in response.text


# --------------------------------------------------------------------------- THE LIVE STREAM

def _request(app, path: str, query: str = "", headers=()) -> Request:
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": query.encode(),
        "root_path": "", "app": app, "session": {},
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": ("127.0.0.1", 51234), "server": ("testserver", 443),
    }
    return Request(scope)


async def _snapshot(app, request) -> dict:
    frames = live_page.event_stream(request)
    first = await anext(frames)
    await frames.aclose()
    assert first.startswith("event: snapshot\n"), first[:80]
    return json.loads(first.split("data: ", 1)[1].strip())["targets"]


@pytest.mark.asyncio
async def test_an_admin_stream_receives_the_log_fragment(config_dir, tmp_path):
    app, _registrar = _app(config_dir, [ADMIN], tmp_path)
    stub_identity({"Admin"})
    targets = await _snapshot(app, _request(app, live_page.DASHBOARD_STREAM_PATH))
    assert live_page.LOG_TARGET in targets
    assert "the log line only an admin may read" in targets[live_page.LOG_TARGET]
    assert live_page.STATE_TARGET in targets and live_page.PILLS_TARGET in targets


@pytest.mark.asyncio
async def test_a_non_admin_stream_never_receives_a_log_fragment(config_dir, tmp_path):
    """Hiding the panel is not enough — the WIRE must not carry the log to that session."""
    app, _registrar = _app(config_dir, [DCS_ADMIN], tmp_path)
    stub_identity({"DCS Admin"})
    targets = await _snapshot(app, _request(app, live_page.DASHBOARD_STREAM_PATH))
    assert live_page.LOG_TARGET not in targets
    assert "the log line only an admin may read" not in json.dumps(targets)
    assert "log-body" not in json.dumps(targets)
 # the targets they MAY see are still there
    assert live_page.STATE_TARGET in targets and live_page.PILLS_TARGET in targets
    assert "Training Caucasus" in targets[live_page.STATE_TARGET]


@pytest.mark.asyncio
async def test_a_non_admin_asking_the_stream_for_the_log_is_refused(config_dir, tmp_path):
    """`?log=expanded` on the stream is a request for the log itself, refused before any frame is
    produced (an EventSource is answered with a status and a JSON body, never a page)."""
    app, _registrar = _app(config_dir, [DCS_ADMIN], tmp_path)
    stub_identity({"DCS Admin"})
    request = _request(app, live_page.DASHBOARD_STREAM_PATH, "log=expanded")
    with pytest.raises(HTTPException) as refused:
        live_page.event_stream(request)
    assert refused.value.status_code == 403
    assert "the log line only an admin may read" not in str(refused.value.detail)

 # the polling fallback: over the real wire, the JSON face, no log content
    response = await _get(app, "mod1", f"{live_page.DASHBOARD_FRAGMENTS_PATH}?log=expanded",
                          accept="application/json")
    assert response.status_code == 403
    assert "text/html" not in response.headers.get("content-type", ""), \
        "an EventSource cannot use a page"
    assert response.json()["detail"]
    assert "the log line only an admin may read" not in response.text


@pytest.mark.asyncio
async def test_an_admin_asking_the_stream_for_the_log_is_served(config_dir, tmp_path):
    app, _registrar = _app(config_dir, [ADMIN], tmp_path,
                           log=_log(tmp_path))
    app.state.webui_source_provider = lambda: _source(servers=[server()],
                                                     node_obj=node(instances={}),
                                                     log_path=_log(tmp_path))
    async with asgi_client(app) as client:
        assert (await _login(client, "specialk")).status_code == 303
        response = await client.get(f"{live_page.DASHBOARD_FRAGMENTS_PATH}?log=expanded")
    assert response.status_code == 200
    assert live_page.LOG_TARGET in response.json()


@pytest.mark.asyncio
async def test_a_non_admin_live_refresh_still_serves_the_targets_they_may_see(config_dir, tmp_path):
    """The live page keeps working for a DCS Admin — minus the log target, with no log content."""
    app, _registrar = _app(config_dir, [DCS_ADMIN], tmp_path)
    async with asgi_client(app) as client:
        assert (await _login(client, "mod1")).status_code == 303
        response = await client.get(live_page.DASHBOARD_FRAGMENTS_PATH,
                                    headers={"accept": "application/json"})
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {live_page.STATE_TARGET, live_page.PILLS_TARGET}
    assert "Training Caucasus" in payload[live_page.STATE_TARGET]
    assert 'class="pill"' in payload[live_page.PILLS_TARGET]
    assert "the log line only an admin may read" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_the_rendered_stream_url_of_a_non_admin_is_the_same_gated_url(config_dir, tmp_path):
    """The shell still hands a non-Admin an ordinary stream URL (no log parameter smuggled in)."""
    app, _registrar = _app(config_dir, [DCS_ADMIN], tmp_path)
    html = (await _get(app, "mod1", dashboard_page.DASHBOARD_PATH)).text
    stream = re.search(r'data-stream="([^"]*)"', html)
    assert stream, "the live script is still wired for a non-Admin"
    assert "log=" not in stream.group(1) and "expanded" not in stream.group(1)
