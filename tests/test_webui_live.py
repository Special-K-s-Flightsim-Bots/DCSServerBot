"""The live dashboard: server-rendered HTML fragments pushed over Server-Sent Events.

The stream and polling-fallback endpoints are gated exactly like the dashboard and live under
``/api/…`` so an EventSource is answered with the API face -- a status and a JSON body -- and never a
redirect to the login page. The first event on a fresh connection is ``snapshot``, and NOTHING is sent
when nothing changed (frames compared by a hash of the rendered fragment). Fragments are rendered by
the SAME templates the initial page uses, with autoescaping on (log content is attacker-influenceable).
The stream is bounded and leak-free: one slot per client, released on disconnect, with a configurable
cap. Runs in-process against a stub source."""
from __future__ import annotations

import asyncio
import re
import types
from pathlib import Path

import pytest
from starlette.requests import Request

from conftest import asgi_client, create_app, install_shell, permissions
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
                              node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                              config=_config(**dashboard))
    return app, registrar


async def _login(client, username: str = "mod1", password: str = PASSWORD):
    response = await client.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', response.text).group(1)
    return await client.post(auth_routes.LOGIN_PATH,
                             data={"username": username, "password": password,
                                   "_csrf_token": token})


def _write_log(path: Path, rows) -> Path:
    path.write_text("".join(row if row.endswith("\n") else row + "\n" for row in rows),
                    encoding="utf-8")
    return path


def _source(*, servers=(), instances=None, log_path=None):
    instances = dict(instances or {})
    node = types.SimpleNamespace(name="dcs-master-01", master=True, instances=instances)
    return readmodels.LiveSource(servers=tuple(servers), instances=tuple(instances.values()),
                                 nodes={"dcs-master-01": node}, node=node, log_path=log_path)


class Status:
    RUNNING = "Running"
    STOPPED = "Stopped"


def _server(name="Training Caucasus", *, status=Status.RUNNING):
    return types.SimpleNamespace(name=name, status=status,
                                 node=types.SimpleNamespace(name="dcs-master-01"),
                                 instance=types.SimpleNamespace(name="Training"), port=10309,
                                 current_mission=types.SimpleNamespace(name="Caucasus_Training",
                                                                       mission_time=600),
                                 players={}, is_remote=False)


def _request(app, path: str, headers=(), session=None) -> Request:
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "app": app, "session": dict(session or {}),
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": ("127.0.0.1", 51234), "server": ("testserver", 443),
    }
    return Request(scope)


def _renderer(app):
    """The production fragment renderer, wired to the app's stub source."""
    environment = app.state.webui_templates

    def render():
        state = readmodels.overview(app.state.webui_source_provider(), level="info")
        return live_page.fragments(environment, state)

    return render


# ------------------------------------------------------- the gate: gated, never public, API face

def test_the_stream_routes_are_gated_like_the_dashboard_and_not_public(config_dir):
    """Both new paths carry the dashboard's capability (the same session, the same check) and are
    NOT in the public set — the gate refuses them for anyone the dashboard refuses."""
    app, registrar = _build(config_dir)
    for path in (live_page.DASHBOARD_STREAM_PATH, live_page.DASHBOARD_FRAGMENTS_PATH):
        assert registrar.capability_for(path=path) == dashboard_page.DASHBOARD_CAPABILITY, path
        assert registrar.capability_for(path=path) != permissions.PUBLIC, f"{path} must not be public"
    registered = {getattr(route, "path", None) for route in registrar.routes}
    assert {live_page.DASHBOARD_STREAM_PATH, live_page.DASHBOARD_FRAGMENTS_PATH} <= registered
 # the shell declares everything it serves: no legacy/undeclared hole grew with this feature
    assert permissions.undeclared_routes(app, registrar) == []
 # neither stream route is a Mount (a mount answers before the gate runs at all)
    assert permissions.audit_mounts(app) == []


def test_an_event_source_accept_header_is_never_answered_with_a_page():
    """The EventSource sends ``Accept: text/event-stream``. That is not a document, so the
    one-status-two-faces predicate must answer the API face — with or without the ``/api`` prefix —
    because a redirect to the login page is a response an EventSource cannot use."""
    stream = _request(None, live_page.DASHBOARD_STREAM_PATH,
                      headers=[("accept", "text/event-stream")])
    assert permissions.wants_html(stream) is False
    assert permissions.wants_error_page(stream) is False
    assert permissions.is_api_path(live_page.DASHBOARD_STREAM_PATH) is True

 # the same judgement must not depend on the path prefix alone
    page = _request(None, "/some/page", headers=[("accept", "text/event-stream")])
    assert permissions.wants_error_page(page) is False

 # positive control: a browser asking for a document on a page still gets the HTML face
    browser = _request(None, "/some/page",
                       headers=[("accept", "text/html,application/xhtml+xml")])
    assert permissions.wants_error_page(browser) is True


@pytest.mark.asyncio
async def test_an_anonymous_event_source_gets_the_api_403_never_a_redirect(config_dir):
    app, _registrar = _build(config_dir)
    async with asgi_client(app) as client:
        for path in (live_page.DASHBOARD_STREAM_PATH, live_page.DASHBOARD_FRAGMENTS_PATH):
            response = await client.get(path, headers={"accept": "text/event-stream"})
            assert response.status_code == 403, path
            assert response.headers.get("location") is None, "an EventSource cannot follow a redirect"
            assert "text/html" not in response.headers.get("content-type", ""), path
            assert response.json()["detail"], path


# ----------------------------------------------------------- the stream: content type + snapshot

@pytest.mark.asyncio
async def test_the_stream_answers_text_event_stream_and_starts_with_a_snapshot(config_dir, tmp_path):
    """Over the real ASGI app: ``text/event-stream`` and a first ``snapshot`` event. The stream is
    made finite by filling the (configured) single client slot, which is the documented cap
    behaviour: the client still gets its snapshot and the stream closes cleanly."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log",
                     ["2026-09-24 21:33:12.123 INFO\tfirst line"])
    app, _registrar = _build(config_dir, stream_clients=1, refresh_seconds=1)
    app.state.webui_source_provider = lambda: _source(servers=[_server()], log_path=log)
    registry = live_page.get_registry(app, live_page.dashboard_config(app))
    assert registry.try_acquire() is True, "the only slot is taken before the request"
    try:
        async with asgi_client(app) as client:
            assert (await _login(client)).status_code == 303
            response = await client.get(live_page.DASHBOARD_STREAM_PATH,
                                        headers={"accept": "text/event-stream"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.text
        assert body.startswith("event: snapshot\n"), body[:120]
        assert '"targets"' in body
        assert '"log-panel"' in body and '"state-panel"' in body
        assert body.endswith("\n\n"), "SSE frames are terminated by a blank line"
        assert "event: log" not in body and "event: state" not in body, \
            "an over-cap client gets its snapshot and nothing more"
    finally:
        registry.release()


@pytest.mark.asyncio
async def test_the_fragment_endpoint_serves_both_panels_as_rendered_html(config_dir, tmp_path):
    """The polling fallback reads this endpoint; it returns the SAME fragments, as JSON."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log",
                     ["2026-09-24 21:33:12.123 INFO\thello from the log"])
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(servers=[_server()], log_path=log)
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        response = await client.get(live_page.DASHBOARD_FRAGMENTS_PATH)
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {live_page.LOG_TARGET, live_page.STATE_TARGET, live_page.PILLS_TARGET}
    assert 'id="log-body"' in payload[live_page.LOG_TARGET]
    assert "hello from the log" in payload[live_page.LOG_TARGET]
    assert "Training Caucasus" in payload[live_page.STATE_TARGET]
    assert 'class="pill"' in payload[live_page.PILLS_TARGET]


# ------------------------------------------------------- the frames: change detection, heartbeat

class _StopTicks(Exception):
    """Raised by a scripted sleep to end the stream after N ticks, with no timing involved."""


def _scripted_sleep(actions):
    """A ``sleep`` whose i-th call runs ``actions[i]`` and then raises after the last one."""
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


@pytest.mark.asyncio
async def test_a_new_log_line_is_a_log_event_and_nothing_else_is_sent(config_dir, tmp_path):
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log",
                     ["2026-09-24 21:33:12.123 INFO\tfirst line"])
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(servers=[_server()], log_path=log)
    registry = live_page.LiveRegistry(4)
    frames = live_page.stream_frames(
        render=_renderer(app), config=live_page.LiveConfig(refresh_seconds=1, stream_clients=4),
        registry=registry,
        sleep=_scripted_sleep([
            lambda: None,                                    # nothing changed -> nothing sent
            lambda: _write_log(log, ["2026-09-24 21:33:12.123 INFO\tfirst line",
                                     "2026-09-24 21:33:13.123 WARNING\tsecond line"]),
            lambda: None,                                    # the change is already known -> no repeat
        ]))

    out = await _drive(frames)
    assert len(out) == 2, out
    assert out[0].startswith("event: snapshot")
    assert out[1].startswith("event: log"), out[1]
    assert "second line" in out[1]
    assert sum(1 for frame in out if frame.startswith("event: log")) == 1, \
        "an unchanged fragment must not be re-sent"
    assert registry.active == 0, "the slot must be released when the stream ends"


@pytest.mark.asyncio
async def test_a_server_state_change_is_a_state_event(config_dir, tmp_path):
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log",
                     ["2026-09-24 21:33:12.123 INFO\tfirst line"])

    class Source:
        def __init__(self):
            self.servers = []

    source = Source()
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(servers=tuple(source.servers), log_path=log)

    def add_a_server():
        source.servers.append(_server(name="Sunday Ops", status=Status.STOPPED))

    frames = live_page.stream_frames(
        render=_renderer(app), config=live_page.LiveConfig(refresh_seconds=1, stream_clients=4),
        registry=live_page.LiveRegistry(4), sleep=_scripted_sleep([add_a_server]))

    out = await _drive(frames)
    assert out[0].startswith("event: snapshot")
    assert len(out) == 2 and out[1].startswith("event: state"), out
    assert "Sunday Ops" in out[1]
    assert "STOPPED" in out[1]


@pytest.mark.asyncio
async def test_an_idle_stream_sends_a_heartbeat_comment(config_dir, tmp_path):
    """A proxy (or the server) must not cut an idle stream: a comment frame is sent when no
    fragment changed and the heartbeat interval has elapsed."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log",
                     ["2026-09-24 21:33:12.123 INFO\tfirst line"])
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)
    frames = live_page.stream_frames(
        render=_renderer(app),
        config=live_page.LiveConfig(refresh_seconds=1, stream_clients=4, heartbeat_seconds=0),
        registry=live_page.LiveRegistry(4), sleep=_scripted_sleep([lambda: None]),
        monotonic=lambda: 0.0)

    out = await _drive(frames)
    assert out[0].startswith("event: snapshot")
    assert ": ping\n\n" in out, out


@pytest.mark.asyncio
async def test_a_stream_beyond_the_cap_still_gets_a_snapshot_then_closes(config_dir, tmp_path):
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log",
                     ["2026-09-24 21:33:12.123 INFO\tfirst line"])
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)
    registry = live_page.LiveRegistry(1)
    assert registry.try_acquire() is True
    frames = live_page.stream_frames(
        render=_renderer(app), config=live_page.LiveConfig(refresh_seconds=1, stream_clients=1),
        registry=registry, sleep=_scripted_sleep([]))

    first = await anext(frames)
    assert first.startswith("event: snapshot")
    with pytest.raises(StopAsyncIteration):
        await anext(frames)
    assert registry.active == 1, "the pre-existing stream's slot must be untouched"
    registry.release()


@pytest.mark.asyncio
async def test_a_disconnected_client_leaves_no_slot_behind(config_dir, tmp_path):
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log",
                     ["2026-09-24 21:33:12.123 INFO\tfirst line"])
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)
    registry = live_page.LiveRegistry(4)
    baseline = registry.active
    frames = live_page.stream_frames(
        render=_renderer(app), config=live_page.LiveConfig(refresh_seconds=1, stream_clients=4),
        registry=registry, sleep=_scripted_sleep([]))

    await anext(frames)                                  # connect (and read one frame)
    assert registry.active == baseline + 1
    await frames.aclose()                                # the client goes away
    assert registry.active == baseline, "no task may outlive its client"


# ------------------------------------------------------------------ config keys and defaults

def test_the_dashboard_config_keys_have_the_documented_defaults():
    defaults = live_page.live_config({})
    assert defaults.enabled is True
    assert defaults.refresh_seconds == 2
    assert defaults.stream_clients == 16

    configured = live_page.live_config({"dashboard": {"live": False, "refresh_seconds": 7,
                                                      "stream_clients": 3}})
    assert (configured.enabled, configured.refresh_seconds, configured.stream_clients) == (False, 7, 3)


@pytest.mark.parametrize("block,expected", [
    ({"live": "off"}, False),
    ({"live": "on"}, True),
    ({"live": "yes"}, True),
    ({"refresh_seconds": 0}, 2),          # below the floor -> the default, not a busy loop
    ({"stream_clients": -5}, 16),         # nonsense -> the default, never a cap of nothing
])
def test_a_bad_dashboard_value_falls_back_instead_of_breaking_the_stream(block, expected):
    config = live_page.live_config({"dashboard": block})
    key = next(iter(block))
    assert getattr(config, {"live": "enabled"}.get(key, key)) == expected


# ------------------------------------------------ progressive enhancement and autoescaping

@pytest.mark.asyncio
async def test_the_page_renders_its_content_without_javascript(config_dir, tmp_path):
    """With JavaScript off the page is still fully usable: the log entries and the server rows are
    in the server-rendered HTML, and the containers the script would swap exist already."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log",
                     ["2026-09-24 21:33:12.123 INFO\tfirst line"])
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(servers=[_server()], log_path=log)
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        response = await client.get("/")
    assert response.status_code == 200
    html = response.text
    assert '<div class="ln">' in html                       # the log entries are already rendered
    assert "Training Caucasus" in html                      # the server rows too
    assert 'id="state-panel"' in html and 'id="log-panel"' in html
    assert 'id="log-body"' in html and 'id="status-pills"' in html
    assert 'id="live-status"' in html
    assert "/static/shell.js" in html                       # the enhancement is additive


def test_a_log_line_cannot_break_out_of_the_fragment(config_dir, tmp_path):
    """Log content is attacker-influenceable (mission names, player names, plugin output). The
    fragment path renders through the same autoescaping environment as the page, so a line
    containing markup comes out as characters."""
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", [
        "2026-09-24 21:33:12.123 INFO\t<script>alert(1)</script>",
        "2026-09-24 21:33:13.123 INFO\t<img src=x onerror=alert(2)>",
    ])
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: _source(log_path=log)
    environment = app.state.webui_templates
    rendered = live_page.fragments(
        environment, readmodels.overview(app.state.webui_source_provider(), level="info"))

    log_html = rendered[live_page.LOG_TARGET]
    assert "<script>" not in log_html and "&lt;script&gt;" in log_html
    assert "<img" not in log_html and "&lt;img" in log_html
 # every fragment template is an.html template, so select_autoescape keeps escaping ON
    for name in (live_page.LOG_TEMPLATE, live_page.STATE_TEMPLATE, live_page.PILLS_TEMPLATE):
        assert name.endswith(".html"), name


def test_no_python_module_renders_raw_html_for_the_fragments():
    """The fragments come from the templates the page uses — no hand-written markup in Python."""
    source = Path(live_page.__file__).read_text(encoding="utf-8")
    for marker in ("<div", "<span", "<table", "<p "):
        assert marker not in source, f"live.py builds markup: {marker}"
    for name in (live_page.LOG_TEMPLATE, live_page.STATE_TEMPLATE, live_page.PILLS_TEMPLATE):
        assert name in source, f"{name} is not rendered by the fragment renderer"


@pytest.mark.asyncio
async def test_the_page_does_not_load_the_live_script_when_it_is_disabled(config_dir, tmp_path):
    app, _registrar = _build(config_dir, live=False)
    app.state.webui_source_provider = lambda: _source()
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        html = (await client.get("/")).text
    assert "/static/shell.js" not in html, "dashboard.live: false must not open a stream"
    assert "Dashboard" in html, "the page still renders without the live script"


@pytest.mark.asyncio
async def test_the_live_script_is_served_and_knows_the_contract(config_dir):
    """The client is a real asset, served through the same gated asset path, and it speaks the
    protocol this module defines: the stream, its polling fallback, the swap targets, and the
    "only auto-scroll a reader who is already at the bottom" rule."""
    app, _registrar = _build(config_dir)
    async with asgi_client(app) as client:
        response = await client.get("/static/shell.js")
    assert response.status_code == 200
    body = response.text
    assert "EventSource" in body
    assert "addEventListener" in body
    for target in (live_page.LOG_TARGET, live_page.STATE_TARGET, live_page.PILLS_TARGET):
        assert target in body, f"the client does not know the swap target {target}"
    assert "setInterval" in body, "the polling fallback is missing"
    assert "scrollHeight" in body, "the at-the-bottom rule is missing"
    assert "data-stream" in body and "data-fragments" in body
