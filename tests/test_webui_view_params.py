"""The view parameters must survive the LIVE refresh, not just the first render.

The level bug was a seam: the page rendered the selected level, the shell handed the EventSource a URL
WITHOUT it, and the panel silently reverted about two seconds later. The same shape would make a sort
reset every few seconds, so the view parameters are pinned end to end here: the rendered
``data-stream``/``data-fragments`` URLs carry the VALIDATED ``sort``/``dir``/``q`` (and ``log``, and the
level) in force; a stream request built from THAT url resolves the same ``ViewParams``/``ViewState`` as
the page did; an unknown sort/direction neither 500s nor launders the raw value into the stream URL;
an unadorned page still streams ``?level=…`` only; and the stream re-resolves the view on EVERY tick.
Runs in-process from a stub source."""
from __future__ import annotations

import json
import re
import types
from html import unescape
from urllib.parse import parse_qs, quote, urlparse

import pytest
from starlette.requests import Request

from conftest import stub_identity, asgi_client, create_app, install_shell, permissions
from services.webservice import readmodels
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import live as live_page

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"
BOT_YAML = "roles:\n  DCS Admin:\n    - mod1\n"

PAYLOAD = '"><script>alert(1)</script>'


# ------------------------------------------------------------------------------ fixtures

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(**dashboard) -> dict:
    config: dict = {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": [
                     {"username": "mod1", "password_hash": _hash(), "roles": ["Admin"]}]}},
    }
    if dashboard:
        config["dashboard"] = dashboard
    return config


def _build(config_dir, **dashboard):
    app = create_app()
    registrar = install_shell(app,
                              node=types.SimpleNamespace(master=True,
                                                         config_dir=str(config_dir)),
                              config=_config(**dashboard))
    return app, registrar


async def _login(client, username: str = "mod1", password: str = PASSWORD):
    response = await client.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', response.text).group(1)
    return await client.post(auth_routes.LOGIN_PATH,
                             data={"username": username, "password": password,
                                   "_csrf_token": token})


def _player(name, ucid, *, server="Training Caucasus"):
    return types.SimpleNamespace(name=name, ucid=ucid, active=True, server_name=server,
                                 unit_display_name="A-10C", group_name="Hog 1")


def _server(name="Training Caucasus", *, players=()):
    return types.SimpleNamespace(
        name=name, status="Running", node=types.SimpleNamespace(name="dcs-master-01"),
        instance=types.SimpleNamespace(name="Training"), port=10309,
        current_mission=types.SimpleNamespace(name="Caucasus_Training", mission_time=600),
        players={p.ucid: p for p in players}, is_remote=False)


def _source(*, servers=()):
    node = types.SimpleNamespace(name="dcs-master-01", master=True, instances={})
    return readmodels.LiveSource(servers=tuple(servers), instances=(),
                                 nodes={"dcs-master-01": node}, node=node, log_path=None)


def _three_players():
    return [("Viper-1", "4b1f9c22"), ("Maverick", "aa11bb"), ("Viper-2", "cc33dd")]


def _request(app, path: str, query: str = "") -> Request:
 # These tests drive the generator directly with a hand-built Request, so there is no session to
 # resolve roles from: they are written from the ADMIN's perspective (narrowed
 # the log to that role, and the log fragment is what several of them assert).
    stub_identity({"Admin"})
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": query.encode(),
        "root_path": "", "app": app, "session": {},
        "headers": [], "client": ("127.0.0.1", 51234), "server": ("testserver", 443),
    }
    return Request(scope)


def _live_attrs(html: str) -> tuple[str, str]:
    stream = re.search(r'data-stream="([^"]*)"', html)
    fragments = re.search(r'data-fragments="([^"]*)"', html)
    assert stream and fragments, "the shell must render the live script's URLs"
 # the attribute is HTML-escaped in the markup (`&` -> `&amp;`): read it as the browser would
    return unescape(stream.group(1)), unescape(fragments.group(1))


async def _page_html(app, url: str) -> str:
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        response = await client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        return response.text


# ------------------------------------- A: the stream URL carries the validated view


@pytest.mark.asyncio
async def test_the_stream_and_polling_urls_carry_the_view_in_force(config_dir):
    """THE REGRESSION this card exists to prevent: the view the page resolved is the view the
    stream must ask for, or the sort/search reverts on the first frame."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[_server(players=[_player(n, u) for n, u in _three_players()])])
    html = await _page_html(app, "/?sort=players&dir=desc&q=viper&log=right&level=all")

    for url in _live_attrs(html):
        query = parse_qs(urlparse(url).query)
        assert query["sort"] == ["players"], url
        assert query["dir"] == ["desc"], url
        assert query["q"] == ["viper"], url
        assert query["log"] == ["right"], url
        assert query["level"] == ["all"], url


@pytest.mark.asyncio
async def test_the_stream_request_resolves_the_same_view_as_the_page(config_dir):
    """Read the URL off the RENDERED page and resolve it: the same ViewParams and the same sorted
    state. This is the interaction the level bug hid — an endpoint test would pass while the page
    handed the browser a URL with no view in it."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[_server(players=[_player(n, u) for n, u in _three_players()])])
    page_url = "/?sort=players&dir=desc&q=viper"
    html = await _page_html(app, page_url)
    stream_url = _live_attrs(html)[0]

    page_request = _request(app, "/", page_url.split("?", 1)[1])
    stream_request = _request(app, urlparse(stream_url).path, urlparse(stream_url).query)

    assert dashboard_page.view_params(stream_request) == dashboard_page.view_params(page_request)
    assert dashboard_page.view_params(stream_request).query == "viper"

    view = dashboard_page.view_state_for(stream_request)
    assert view.tables["players"].sort == "name", \
        "the players table has no 'players' column, so it keeps its default"
    assert view.tables["servers"].sort == "players"
    assert view.tables["servers"].direction == "desc"
    assert view.search.filtered == 2 and view.search.total == 3


@pytest.mark.asyncio
async def test_an_unadorned_page_still_streams_the_level_only(config_dir):
    """Positive control for the URL contract: with no view stated the stream URL is exactly what
    the level tests pin, so adding the view parameters cannot have widened every page's URL."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source()
    html = await _page_html(app, "/")
    assert _live_attrs(html) == ("/api/dashboard/stream?level=info",
                                 "/api/dashboard/fragments?level=info")


@pytest.mark.asyncio
async def test_an_unknown_view_parameter_never_reaches_the_stream_url(config_dir):
    """Junk in ``sort``/``dir``/``log``: the page answers 200, falls back to the defaults, and the
    stream URL carries none of the raw value (it is not laundered through the page)."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source()
    html = await _page_html(app, "/?sort=%3Cscript%3E&dir=sideways&log=nowhere")

    for url in _live_attrs(html):
        assert "script" not in url and "sideways" not in url and "nowhere" not in url
    stream = _live_attrs(html)[0]
    request = _request(app, urlparse(stream).path, urlparse(stream).query)
    params = dashboard_page.view_params(request)
    assert params.sort_for("servers") == "name"
    assert params.direction == "asc"


# ------------------------------------- B: every tick re-resolves the view from the request


class _StopTicks(Exception):
    """Raised by the scripted sleep to end the stream after N ticks, with no timing involved."""


def _scripted_sleep(n: int):
    state = {"n": 0}

    async def sleep(_seconds):
        if state["n"] >= n:
            raise _StopTicks()
        state["n"] += 1

    return sleep


@pytest.mark.asyncio
async def test_every_stream_tick_resolves_the_view_from_the_request(config_dir, monkeypatch):
    """The refresh re-derives the view per frame from the request. Because it is derived and not
    remembered in the DOM, no frame can render a different sort/search than the first one."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[_server(players=[_player(n, u) for n, u in _three_players()])])
    html = await _page_html(app, "/?sort=players&dir=desc&q=viper")
    stream_url = _live_attrs(html)[0]
    request = _request(app, urlparse(stream_url).path, urlparse(stream_url).query)

    seen: list = []
    real = dashboard_page.view_state_for

    def spy(req, state=None):
        seen.append(req)
        return real(req, state)

    monkeypatch.setattr(dashboard_page, "view_state_for", spy)

    frames = live_page.stream_frames(
        render=live_page.render_for(request),
        config=live_page.LiveConfig(refresh_seconds=1, stream_clients=4),
        registry=live_page.LiveRegistry(4), sleep=_scripted_sleep(2))

    out: list[str] = []
    try:
        while True:
            out.append(await anext(frames))
    except (_StopTicks, StopAsyncIteration):
        pass

    assert out[0].startswith("event: snapshot"), out[:1]
    assert len(seen) == 3, "the snapshot and each of the two ticks resolve the view once"
    for req in seen:
        assert dashboard_page.view_params(req).query == "viper"
    assert live_page.LOG_TARGET in json.loads(out[0].split("data: ", 1)[1])["targets"]


@pytest.mark.asyncio
async def test_the_live_refresh_keeps_the_search_and_level_for_the_request(config_dir):
    """Drive the real generator from the page's own URL and assert the rendered fragments are the
    ones that request produces — with the search and the level still in force on the last frame."""
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[_server(players=[_player(n, u) for n, u in _three_players()])])
    html = await _page_html(app, "/?q=viper&level=all")
    stream_url = _live_attrs(html)[0]
    request = _request(app, urlparse(stream_url).path, urlparse(stream_url).query)

    render = live_page.render_for(request)
    frames = live_page.stream_frames(
        render=render, config=live_page.LiveConfig(refresh_seconds=1, stream_clients=4),
        registry=live_page.LiveRegistry(4), sleep=_scripted_sleep(1))
    out: list[str] = []
    try:
        while True:
            out.append(await anext(frames))
    except (_StopTicks, StopAsyncIteration):
        pass

    assert dashboard_page.view_params(request).query == "viper"
    assert dashboard_page.log_level(request) == "all"
    for frame in out:
        targets = json.loads(frame.split("data: ", 1)[1])["targets"]
        assert live_page.STATE_TARGET in targets and live_page.LOG_TARGET in targets


# ------------------------------------- C: the payload, through the page and the environment


@pytest.mark.asyncio
async def test_a_script_payload_in_q_returns_200_and_nothing_unescaped_reaches_the_page(config_dir):
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(
        servers=[_server(players=[_player(n, u) for n, u in _three_players()])])
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        response = await client.get("/?q=" + quote(PAYLOAD, safe=""))
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text, \
        "the payload must not reach the markup as a live tag"
    assert dashboard_page.view_params(
        _request(app, "/", "q=" + quote(PAYLOAD, safe=""))).query == PAYLOAD


def test_the_environment_escapes_the_search_payload(config_dir):
    """The UI card will render ``view.query`` into an ``input value=`` and into a sentence. The
    environment it renders through must escape it — asserted on the REAL environment the shell
    installed, not on a fresh Jinja one."""
    app, _registrar = _build(config_dir)
    environment = app.state.webui_templates
    html = environment.from_string("{{ value }}").render(value=PAYLOAD)
    assert "<script>" not in html and '="' not in html
    assert "&lt;script&gt;" in html and "&#34;" in html, html


# ------------------------------------- D: the pure layer is reusable by the standalone pages


def test_the_view_layer_needs_no_http_and_no_request_object():
    """The helper is reusable (requirement 6): it takes a query MAPPING and a path, so the
    standalone Servers/Nodes/Instances/Players pages can call it with their own path."""
    params = readmodels.view_params({"sort": "name"})
    state = readmodels.view_state("/fleet", {"servers": []}, params=params)
    assert state.tables["servers"].sort == "name"
    assert "/fleet?" in state.tables["servers"].columns[1].link
