"""The Servers list is LIVE: its table body follows a write without a refresh.

A list row carries state a WRITE moves on the server — the busy marker a press raises, and the state
a boot settles on — and rendered once at the write's redirect it can be one step behind the truth.
So the Servers page's table body is a swap target, re-rendered every tick from the SAME context
builder the page used and delivered over the console's own transport (``pages/live.stream_frames`` +
``LiveRegistry`` + ``static/shell.js``). One section per acceptance criterion:

* the page draws the table-body target with a stable id and passes ``live`` only while live updates
  are on — and the three pages that carry no live state draw neither;
* both live paths are registered under the page's own capability and refuse a caller without it
  BEFORE a generator exists (a refusal inside a ``StreamingResponse`` arrives after the headers);
* a fragment is re-sent only when its rendered HTML changed;
* a row rendered for a server that is STARTING keeps the busy class, and a row for a loaded server
  carries the running glyph — through the SAME builder the stream renders with, asserted by
  comparing the page's own body with the fragment;
* a scoped manager's rows are only their own, and a role revoked mid-stream loses the rows at the
  next tick.

Harness: the write suites' real application, local login backend and stub cluster
(``test_webui_write_pause``), plus the non-flipping start double (``test_webui_busy_await_change``).
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest
from starlette.requests import Request

from core import Status
from services.webservice import permissions
from services.webservice.auth import SESSION_IDENTITY_KEY
from services.webservice.pages import actions as actions_page
from services.webservice.pages import instances as instances_page
from services.webservice.pages import lists as lists_page
from services.webservice.pages import live as live_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.pages import players as players_page
from services.webservice.pages import servers as servers_page
from conftest import create_app, install_shell

from test_webui_busy_await_change import booting_server  # the non-flipping-startup double
from test_webui_write_pause import (  # noqa: F401 - fixtures/helpers imported on purpose
    Cluster, build, clean_registry, client, config_dir, fleet, instance, open_page, server,
    sign_in, token_of,
)

#: the accessible signal of a pending control (the busy half) and its animating class
BUSY = 'aria-busy="true"'
BUSY_CLASS = re.compile(r'class="act-btn[^"]*\bbusy\b')

#: the declared server actions, by their key — the row's own vocabulary
_BY_KEY = {action.key: action for action in actions_page.SERVER_ACTIONS}

STREAM, FRAGMENTS = lists_page.stream_paths(servers_page.PAGE)


# ------------------------------------------------------------------------------- the harness

def _config(users=None) -> dict:
    from test_webui_write_pause import _config as base

    return base(users)


def _app(config_dir, cluster, *, dashboard=None, users=None, plugins=("mission",)):
    """A real application around *cluster*, with an optional live block in the service config."""
    config = _config(users)
    if dashboard is not None:
        config["dashboard"] = dict(dashboard)
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir),
                                                  plugins=list(plugins)),
                  config=config)
    app.state.webui_source_provider = cluster.source
    return app


def _request(app, *, subject: str = "mod1", path: str = STREAM, query: str = "") -> Request:
    """A Request carrying the local backend's identity reference — what a session holds."""
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": query.encode(),
        "root_path": "", "app": app,
        "session": {SESSION_IDENTITY_KEY: {"backend": "local", "subject": subject}},
        "headers": [(b"accept", b"application/json")],
        "client": ("127.0.0.1", 51234), "server": ("testserver", 443),
    }
    return Request(scope)


def _squeeze(html: str) -> str:
    """*html* with every run of whitespace collapsed — markup and text, never bytes."""
    return re.sub(r"\s+", " ", html).strip()


def _body_of(page_html: str) -> str:
    """The INNER html of the page's live table body — what a frame replaces."""
    marker = f'<tbody id="{lists_page.TABLE_BODY_TARGET}">'
    assert marker in page_html, "the page drew no live table body"
    return _squeeze(page_html.split(marker, 1)[1].split("</tbody>", 1)[0])


async def _fragments(http) -> dict:
    """The JSON the polling fallback reads — the fragment exactly as the stream sends it."""
    response = await http.get(FRAGMENTS, headers={"accept": "application/json"})
    assert response.status_code == 200, response.text
    return response.json()


def _narrow(capability: str) -> None:
    """Take a capability away from *capability*'s holders: BOTH axes, or the narrowing does not bite.

    The role tuple is narrowed to a role the caller does not hold (an EMPTY tuple would read as
    "declared, no role needed" and admit everyone — see ``permissions.allows``), and the scope axis
    is switched off so a manager cannot enter through it.
    """
    permissions.CAPABILITIES[capability] = ("Admin",)
    permissions.SCOPE_GRANTS[capability] = False


def _loading_fleet(*, status=Status.LOADING):
    """ONE server — SRS-1 on Host A — in the given (transitional) state."""
    a = booting_server("SRS-1", ["Host A"], status=status)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a)])


# ------------------------------------------ 1. the page draws the target, and only when live is on

@pytest.mark.asyncio
async def test_the_servers_page_draws_the_table_body_target_and_loads_the_shared_script(config_dir):
    """The body carries the id a frame names, and the page hands the browser the console's script."""
    app = _app(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http)

    assert f'<tbody id="{lists_page.TABLE_BODY_TARGET}">' in html
    assert "/static/shell.js" in html, "the console's own client, never a new one"
    assert f'data-stream="{STREAM}"' in html
    assert f'data-fragments="{FRAGMENTS}"' in html
    assert 'id="live-status"' in html, "the page states which transport is running"


@pytest.mark.asyncio
async def test_live_off_leaves_a_static_page_that_opens_no_connection(config_dir):
    """``dashboard.live: false``: no script, no stream URL, and no id on the body."""
    app = _app(config_dir, fleet(), dashboard={"live": False})
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http)

    assert "/static/shell.js" not in html
    assert "data-stream" not in html
    assert lists_page.TABLE_BODY_TARGET not in html, "a static body is the bare element"
    assert "<tbody>" in html
    assert "SRS-1" in html, "the page is still complete on load"


def test_only_the_pages_whose_rows_carry_live_state_register_a_stream(config_dir):
    """Servers AND Nodes stream; Instances and Players open no ``/api`` door.

    A node row carries the console's cached DCS answer, and a completed update refreshes it — a page
    rendered once would keep showing the update the node has just installed, so the Nodes table is a
    stream target under its own capability.
    """
    app = _app(config_dir, fleet())
    registrar = app.state.webui_registrar
    registered = {getattr(route, "path", None) for route in registrar.routes}
    assert nodes_page.PAGE.live is True
    for path in lists_page.stream_paths(nodes_page.PAGE):
        assert path in registered, f"the nodes page registered no {path}"
        assert registrar.capability_for(path=path) == nodes_page.NODES_CAPABILITY, path
    for module in (instances_page, players_page):
        assert module.PAGE.live is False, module.__name__
        for path in lists_page.stream_paths(module.PAGE):
            assert path not in registered, f"{module.__name__} registered {path}"
            assert registrar.capability_for(path=path) is None


# ------------------------------------------------- 2. the gate: the page's capability, before a stream

def test_the_two_live_paths_are_gated_with_the_page_s_capability(config_dir):
    """Registered under ``servers.view``, not public, and declared like every other route."""
    app = _app(config_dir, fleet())
    registrar = app.state.webui_registrar
    for path in (STREAM, FRAGMENTS):
        assert registrar.capability_for(path=path) == servers_page.SERVERS_CAPABILITY, path
        assert registrar.capability_for(path=path) != permissions.PUBLIC, path
    registered = {getattr(route, "path", None) for route in registrar.routes}
    assert {STREAM, FRAGMENTS} <= registered
    assert permissions.undeclared_routes(app, registrar) == []


@pytest.mark.asyncio
async def test_a_caller_without_servers_view_is_refused_with_the_api_face(config_dir):
    """An EventSource is answered with a status and a JSON body — never a page it cannot follow."""
    app = _app(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "plain")
        for path in (STREAM, FRAGMENTS):
            response = await http.get(path, headers={"accept": "text/event-stream"})
            assert response.status_code == 403, path
            assert response.headers.get("location") is None, "an EventSource cannot follow a redirect"
            assert "text/html" not in response.headers.get("content-type", ""), path
            assert response.json()["detail"], path


def test_the_generator_itself_refuses_before_a_stream_exists(config_dir):
    """The refusal happens BEFORE a generator exists — inside a stream body it would arrive late."""
    from fastapi import HTTPException

    app = _app(config_dir, fleet())
    with pytest.raises(HTTPException) as raised:
        lists_page.event_stream(_request(app, subject="plain"), servers_page.PAGE)
    assert raised.value.status_code == 403
    assert raised.value.detail == (
        "Not authorized (needs one of: "
        f"{permissions.requirement_text(servers_page.SERVERS_CAPABILITY)})."), \
        "the stream's refusal says exactly what the access gate says"

    stream = lists_page.event_stream(_request(app), servers_page.PAGE)
    assert hasattr(stream, "__anext__"), "a permitted caller receives a real generator"


@pytest.mark.asyncio
async def test_the_stream_answers_text_event_stream_starting_with_a_snapshot(config_dir):
    """Over the real app: the content type, and a first ``snapshot`` frame carrying the body target.

    The stream is made finite by filling the configured single client slot — the documented cap
    behaviour: a client over the cap still receives its snapshot, and then the stream closes.
    """
    app = _app(config_dir, fleet(), dashboard={"stream_clients": 1})
    registry = live_page.get_registry(app, live_page.dashboard_config(app))
    assert registry.try_acquire() is True, "the only slot is taken before the request"
    try:
        async with client(app) as http:
            await sign_in(http, "mod1")
            response = await http.get(STREAM, headers={"accept": "text/event-stream"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.text
        assert body.startswith("event: snapshot\n"), body[:120]
        assert f'"{lists_page.TABLE_BODY_TARGET}"' in body
        assert "SRS-1" in body, "the rows the page drew are in the snapshot"
        assert body.endswith("\n\n"), "SSE frames are terminated by a blank line"
        assert "event: state" not in body, "an over-cap client gets its snapshot and nothing more"
    finally:
        registry.release()


# --------------------------------------------------------- 3. change detection on the console tick

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
async def test_a_frame_is_sent_only_when_the_rendered_rows_changed(config_dir):
    """Two identical ticks send nothing; a server changing state sends exactly one frame."""
    cluster = fleet()

    def pause() -> None:
        cluster.servers[0].status = Status.PAUSED

    app = _app(config_dir, cluster)
    request = _request(app, subject="specialk")     # Admin: the whole cluster, so both rows render
    frames = live_page.stream_frames(
        render=lambda: lists_page.render_rows(request, servers_page.PAGE),
        config=live_page.dashboard_config(app), registry=live_page.LiveRegistry(4),
        sleep=_scripted_sleep([lambda: None, pause, lambda: None]),
        monotonic=lambda: 0.0)                       # no heartbeat can land in two ticks

    out = await _drive(frames)
    assert out[0].startswith("event: snapshot")
    assert f'"{lists_page.TABLE_BODY_TARGET}"' in out[0]
    assert "RUNNING" in out[0]
    assert len(out) == 2, out
    assert out[1].startswith("event: state"), out[1]
    assert "PAUSED" in out[1], "the changed row is on the wire"
    assert "SRS-B" in out[1], "the fragment is the whole body, not one row"


# --------------------------------------- 4. the busy marker and the loaded state survive the re-render

@pytest.mark.asyncio
async def test_a_starting_row_keeps_the_busy_marker_in_the_streamed_body(config_dir, clean_registry):
    """A LOADING row keeps the pending start's pulsing, disabled glyph — in the FRAGMENT too.

    This is the defect's other face: a streamed row that dropped the marker would stop the blink two
    seconds after the click. The fragment is compared with the page's own body, so "the same marker
    the page drew" is asserted rather than described.
    """
    app = _app(config_dir, _loading_fleet())
    actions_page.remember_awaiting_change("server", "SRS-1", _BY_KEY["startup"], "SHUTDOWN", False)

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        served = (await _fragments(http))[lists_page.TABLE_BODY_TARGET]

    assert _squeeze(served) == _body_of(page), "one builder: the page and the stream render the same rows"
    assert BUSY in served, "the pending control must still pulse in the streamed row"
    assert BUSY_CLASS.search(served), "carrying the animating class the page drew"
    assert "disabled" in served, "it is a statement while transitional, not an invitation"
    assert "LOADING" in served, "the row says the state it is actually in"


@pytest.mark.asyncio
async def test_a_loaded_row_carries_the_running_glyph_in_the_streamed_body(config_dir):
    """The settled state lands without a refresh: the dot and the word of a RUNNING server."""
    app = _app(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        served = (await _fragments(http))[lists_page.TABLE_BODY_TARGET]

    assert _squeeze(served) == _body_of(page)
    assert '<span class="dot run"' in served, "the running glyph the page draws"
    assert "RUNNING" in served


# --------------------------------------------------------- 5. the scope, and a revocation mid-stream

@pytest.mark.asyncio
async def test_a_scoped_manager_s_rows_are_only_their_own(config_dir):
    """Host A's manager: their server's row on the page and in the fragment, B nowhere."""
    app = _app(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "hoster")
        page = await open_page(http)
        served = (await _fragments(http))[lists_page.TABLE_BODY_TARGET]

    assert "SRS-1" in served
    assert "SRS-B" not in served, "a server outside the caller's scope leaked into the rows"
    assert "SRS-B" not in page


def test_a_role_revoked_mid_stream_loses_the_rows_at_the_next_tick(config_dir):
    """The capability is re-resolved per tick: the tick after a revocation carries no rows."""
    app = _app(config_dir, fleet())
    request = _request(app)
    before = lists_page.render_rows(request, servers_page.PAGE)[lists_page.TABLE_BODY_TARGET]
    assert "SRS-1" in before, "floor: the permitted caller has rows"

    _narrow(servers_page.SERVERS_CAPABILITY)
    after = lists_page.render_rows(request, servers_page.PAGE)[lists_page.TABLE_BODY_TARGET]
    assert "SRS-1" not in after and "SRS-B" not in after, \
        "the revoked caller kept the fleet it may no longer read"


# ------------------------------------------------------------------- 6. one render path, no markup in Python

def test_the_fragment_has_no_markup_of_its_own(config_dir):
    """The rows come from the template the page includes — ``lists.py`` builds no markup."""
    source = Path(lists_page.__file__).read_text(encoding="utf-8")
    for marker in ("<div", "<span", "<td", "<table"):
        assert marker not in source, f"lists.py builds markup: {marker}"
    assert lists_page.TABLE_ROWS_TEMPLATE in source
