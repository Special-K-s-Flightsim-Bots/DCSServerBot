"""The log level control and the live stream must agree on ONE level.

The level links reload the page with ``?level=`` and the initial render honours it, but the shell
rendered the stream URL WITHOUT the level, so the EventSource asked for the configured default and its
``snapshot`` (and every later ``log`` frame) replaced the panel with default-level content about two
seconds later -- the choice silently reverted. Pins: the rendered ``data-stream``/``data-fragments``
carry the level in force; a stream request with ``?level=all`` snapshots DEBUG lines the default omits
and the level survives two consecutive ``log`` events; an unknown level behaves exactly like no level
and never leaks the string; the level control sits outside the swap target. Runs in-process from a
stub source."""
from __future__ import annotations

import json
import re
import types
from pathlib import Path

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


# ------------------------------------------------------------------------------ fixtures

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(log_level=None, **dashboard) -> dict:
    config: dict = {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": [
                     {"username": "mod1", "password_hash": _hash(), "roles": ["Admin"]}]}},
    }
    if log_level is not None:
        config["log_level"] = log_level
    if dashboard:
        config["dashboard"] = dashboard
    return config


def _build(config_dir, log_level=None, **dashboard):
    app = create_app()
    registrar = install_shell(app,
                              node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                              config=_config(log_level, **dashboard))
    return app, registrar


async def _login(client, username: str = "mod1", password: str = PASSWORD):
    response = await client.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', response.text).group(1)
    return await client.post(auth_routes.LOGIN_PATH,
                             data={"username": username, "password": password,
                                   "_csrf_token": token})


def _source(*, servers=(), log_path=None):
    node = types.SimpleNamespace(name="dcs-master-01", master=True, instances={})
    return readmodels.LiveSource(servers=tuple(servers), instances=(), nodes={"dcs-master-01": node},
                                 node=node, log_path=log_path)


def _server(name="Training Caucasus"):
    return types.SimpleNamespace(name=name, status="Running",
                                 node=types.SimpleNamespace(name="dcs-master-01"),
                                 instance=types.SimpleNamespace(name="Training"), port=10309,
                                 current_mission=types.SimpleNamespace(name="Caucasus_Training",
                                                                       mission_time=600),
                                 players={}, is_remote=False)


def _write_log(path: Path, rows) -> Path:
    path.write_text("".join(row if row.endswith("\n") else row + "\n" for row in rows),
                    encoding="utf-8")
    return path


def _append(path: Path, row: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(row if row.endswith("\n") else row + "\n")


#: a log whose tail carries a DEBUG line the INFO+ default must hide
_LEVEL_ROWS = ["2026-09-24 21:33:12.123 INFO\tinfo line",
               "2026-09-24 21:33:13.123 DEBUG\tdebug line"]


def _request(app, path: str, query: str = "", headers=(), session=None) -> Request:
 # A hand-built Request has no session to resolve roles from, so these tests state their
 # perspective: the ADMIN (narrowed the log to that role, and the log fragment is
 # what several of them assert). The stream's own gate is tested over the real app elsewhere.
    stub_identity({"Admin"})
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": query.encode(),
        "root_path": "", "app": app, "session": dict(session or {}),
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": ("127.0.0.1", 51234), "server": ("testserver", 443),
    }
    return Request(scope)


# ------------------------------------------- A: the rendered stream URL carries the level

def _live_attrs(html: str) -> tuple[str, str]:
    """The ``data-stream``/``data-fragments`` the shell gave the browser."""
    stream = re.search(r'data-stream="([^"]*)"', html)
    fragments = re.search(r'data-fragments="([^"]*)"', html)
    assert stream and fragments, "the shell must render the live script's URLs"
    return stream.group(1), fragments.group(1)


@pytest.mark.asyncio
async def test_the_rendered_stream_and_polling_urls_carry_the_level_in_force(config_dir, tmp_path):
    """THE REGRESSION: the level the page rendered is the level the stream must ask for.

    This is the seam the earlier review never touched — it tested the level parameter on the
    fragment endpoint directly, so a stream URL with no level passed every test while the panel
    reverted to the default on the first frame.
    """
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", _LEVEL_ROWS)
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        default_html = (await client.get("/")).text
        all_html = (await client.get("/?level=all")).text

    assert _live_attrs(default_html) == ("/api/dashboard/stream?level=info",
                                         "/api/dashboard/fragments?level=info")
    assert _live_attrs(all_html) == ("/api/dashboard/stream?level=all",
                                     "/api/dashboard/fragments?level=all")

 # the URL the stream asks for is the SAME level the control marks as active
    active = re.search(r'class="tag a"[^>]*>([^<]*)</a>', all_html)
    assert active and active.group(1) == "ALL"
    assert "level=all" in _live_attrs(all_html)[0]


@pytest.mark.asyncio
async def test_the_configured_default_is_what_the_stream_asks_for_too(config_dir, tmp_path):
    """With ``log_level: all`` configured, an unadorned page must stream at ALL — the URL follows
    the level in force, not a hardcoded INFO."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", _LEVEL_ROWS)
    app, _registrar = _build(config_dir, log_level="all")
    app.state.webui_source_provider = lambda: _source(log_path=log)
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        html = (await client.get("/")).text
    assert _live_attrs(html) == ("/api/dashboard/stream?level=all",
                                 "/api/dashboard/fragments?level=all")
    assert "debug line" in html, "the initial render and the stream must agree"


# ------------------------------------------- B: the stream REQUEST honours ?level=

@pytest.mark.asyncio
async def test_the_stream_endpoint_over_http_emits_debug_for_level_all(config_dir, tmp_path):
    """A real request to the real endpoint with ``?level=all``: the snapshot's log fragment carries
    the DEBUG line the default omits. (The stream is made finite by filling the configured single
    client slot — the documented cap behaviour.)"""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", _LEVEL_ROWS)
    app, _registrar = _build(config_dir, stream_clients=1, refresh_seconds=1)
    app.state.webui_source_provider = lambda: _source(log_path=log)
    registry = live_page.get_registry(app, live_page.dashboard_config(app))
    assert registry.try_acquire() is True
    try:
        async with asgi_client(app) as client:
            assert (await _login(client)).status_code == 303
            framed = await client.get(live_page.DASHBOARD_STREAM_PATH + "?level=all",
                                      headers={"accept": "text/event-stream"})
            plain = await client.get(live_page.DASHBOARD_STREAM_PATH,
                                     headers={"accept": "text/event-stream"})
    finally:
        registry.release()

    assert framed.status_code == 200 and plain.status_code == 200
    assert "debug line" in _log_fragment(framed.text)
    assert "debug line" not in _log_fragment(plain.text)


async def _snapshot(app, request) -> dict:
    """The first frame of the REAL stream generator for *request*, then close it.

    Deterministic: the snapshot is written before the first sleep, so no wall clock is involved.
    """
    frames = live_page.event_stream(request)
    first = await anext(frames)
    await frames.aclose()
    assert first.startswith("event: snapshot\n"), first[:80]
    payload = json.loads(first.split("data: ", 1)[1].strip())
    return payload["targets"]


@pytest.mark.asyncio
async def test_a_stream_request_with_level_all_snapshots_debug_the_default_omits(config_dir,
                                                                                tmp_path):
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", _LEVEL_ROWS)
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)

    default = await _snapshot(app, _request(app, live_page.DASHBOARD_STREAM_PATH))
    all_level = await _snapshot(app,
                                _request(app, live_page.DASHBOARD_STREAM_PATH, "level=all"))

    assert "info line" in default[live_page.LOG_TARGET]
    assert "debug line" not in default[live_page.LOG_TARGET], \
        "the default stream must hide DEBUG"
    assert "debug line" in all_level[live_page.LOG_TARGET], \
        "?level=all on the STREAM must show DEBUG"
    assert all_level[live_page.LOG_TARGET] != default[live_page.LOG_TARGET]


@pytest.mark.asyncio
async def test_an_unknown_level_on_the_stream_behaves_like_the_default(config_dir, tmp_path):
    """An attacker-shaped query parameter is refused by the same validator the config uses: it
    falls back to the configured default and never reaches the filter or the fragment."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", _LEVEL_ROWS)
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)

    default = await _snapshot(app, _request(app, live_page.DASHBOARD_STREAM_PATH))
    garbage = await _snapshot(app, _request(app, live_page.DASHBOARD_STREAM_PATH,
                                            "level=%3Cscript%3Enonsense"))

    assert garbage[live_page.LOG_TARGET] == default[live_page.LOG_TARGET]
    assert "debug line" not in garbage[live_page.LOG_TARGET]
    assert "nonsense" not in json.dumps(garbage), "the refused value must not leak into a fragment"


@pytest.mark.asyncio
async def test_a_garbage_level_never_reaches_the_rendered_stream_url(config_dir, tmp_path):
    """The URL the page hands the browser is built from the VALIDATED level, so a garbage query
    parameter cannot be laundered into the stream's own query string."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", _LEVEL_ROWS)
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        html = (await client.get("/?level=%3Cscript%3E")).text
    stream, fragments = _live_attrs(html)
    assert stream == "/api/dashboard/stream?level=info"
    assert fragments == "/api/dashboard/fragments?level=info"
    assert "script" not in stream and "script" not in fragments


# ------------------------------------------- C: the level survives two consecutive log events

class _StopTicks(Exception):
    """Raised by a scripted sleep to end the stream after N ticks, with no timing involved."""


def _scripted_sleep(actions):
    state = {"n": 0}

    async def sleep(_seconds):
        n = state["n"]
        if n >= len(actions):
            raise _StopTicks()
        state["n"] += 1
        actions[n]()

    return sleep


async def _drive(frames) -> list[str]:
    out: list[str] = []
    try:
        while True:
            out.append(await anext(frames))
    except (_StopTicks, StopAsyncIteration):
        pass
    return out


def _log_fragment(frame: str) -> str:
    """The log panel HTML out of one SSE frame, asserted to actually carry that target."""
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    html = payload["targets"].get(live_page.LOG_TARGET)
    assert html is not None, f"the frame carries no {live_page.LOG_TARGET}: {frame[:120]}"
    return html


async def _stream_request_from_page(app, page_url: str) -> Request:
    """The Request an EventSource would actually make: read the URL off the RENDERED page.

    This is what makes the test an interaction test rather than an endpoint test — the URL under
    test is the one the page handed the browser, not one the test typed itself.
    """
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        html = (await client.get(page_url)).text
    stream_url = _live_attrs(html)[0]
    path, _, query = stream_url.partition("?")
    return _request(app, path, query)


@pytest.mark.asyncio
async def test_the_selected_level_survives_two_consecutive_log_events(config_dir, tmp_path):
    """The revert happened on the NEXT frame, so the snapshot alone is not enough evidence: the
    level must hold across the snapshot AND the two following ``log`` events.

    The stream URL is taken from the rendered ``/?level=all`` page, so this fails on a page whose
    stream URL lost the level — which is exactly the bug."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", _LEVEL_ROWS)
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)
    request = await _stream_request_from_page(app, "/?level=all")
    registry = live_page.LiveRegistry(4)
    frames = live_page.stream_frames(
        render=live_page.render_for(request),
        config=live_page.LiveConfig(refresh_seconds=1, stream_clients=4), registry=registry,
        sleep=_scripted_sleep([
            lambda: _append(log, "2026-09-24 21:33:14.123 DEBUG\tdebug one"),
            lambda: _append(log, "2026-09-24 21:33:15.123 DEBUG\tdebug two"),
        ]))

    out = await _drive(frames)
    assert "DEBUG" in _log_fragment(out[0]), \
        "the snapshot must already render at the selected level"
    assert [frame.split("\n", 1)[0] for frame in out] == \
        ["event: snapshot", "event: log", "event: log"], out
    for frame in out:
        html = _log_fragment(frame)
        assert "DEBUG" in html, f"the level reverted on this frame: {html[:200]}"
        assert "debug line" in html or "debug one" in html or "debug two" in html
    assert "debug two" in _log_fragment(out[2]), "the second log event is a fresh render"
    assert registry.active == 0, "the slot must be released when the stream ends"


@pytest.mark.asyncio
async def test_the_default_level_keeps_debug_out_across_the_same_frames(config_dir, tmp_path):
    """Positive control for the test above: at the default level the same two log events must NOT
    contain DEBUG — so the first test is measuring the level, not the renderer."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", _LEVEL_ROWS)
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)
    request = await _stream_request_from_page(app, "/")
    frames = live_page.stream_frames(
        render=live_page.render_for(request),
        config=live_page.LiveConfig(refresh_seconds=1, stream_clients=4),
        registry=live_page.LiveRegistry(4),
        sleep=_scripted_sleep([
            lambda: _append(log, "2026-09-24 21:33:14.123 DEBUG\tdebug one"),
            lambda: _append(log, "2026-09-24 21:33:15.123 INFO\tinfo two"),
        ]))

    out = await _drive(frames)
    assert out[0].startswith("event: snapshot")
    for frame in out:
        assert "DEBUG" not in _log_fragment(frame)
    assert "info two" in _log_fragment(out[-1])


# ------------------------------------------- D: the control survives a swap

@pytest.mark.asyncio
async def test_the_level_control_marks_exactly_one_active_level_after_a_swap(config_dir, tmp_path):
    """The control lives OUTSIDE ``#log-panel``, and the swap only writes the ids an event carries.
    So after a ``log`` event the control is untouched and still marks exactly one level."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", _LEVEL_ROWS)
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        html = (await client.get("/?level=warning")).text

    control = re.search(r'<span class="log-level" id="log-level">(.*?)</span>', html, re.S)
    assert control, "the level control must be a stable element outside the swap target"
    assert control.group(1).count('class="tag a"') == 1, "exactly one level is marked active"
    assert re.search(r'href="/\?level=warning"[^>]*class="tag a"', control.group(1))

 # the fragment the stream swaps in must not carry the control, or every log event would
 # re-render (and could clobber) the level choice
    fragment = live_page.fragments(
        app.state.webui_templates,
        readmodels.overview(app.state.webui_source_provider(), level="warning"))[live_page.LOG_TARGET]
    assert 'id="log-level"' not in fragment
    assert 'class="tag a"' not in fragment
    assert "#log-panel" not in fragment, "no markup may re-target the swap"


@pytest.mark.asyncio
async def test_the_client_never_swaps_the_level_control(config_dir):
    """The swap target list is the contract: ``log-level`` must not be in it."""
    app, _registrar = _build(config_dir)
    async with asgi_client(app) as client:
        body = (await client.get("/static/shell.js")).text
    targets = re.search(r"var TARGETS = \[(.*?)\]", body, re.S)
    assert targets, "the client declares its swap targets"
    listed = targets.group(1)
    assert "log-level" not in listed
    for target in (live_page.LOG_TARGET, live_page.STATE_TARGET, live_page.PILLS_TARGET):
        assert f'"{target}"' in listed
