"""Where the install help sits: the local-port probe and the TOP line of both steps.

The Database step offers its install help only while the LOCAL PostgreSQL port is not a proven open,
and then as the card's FIRST line; the Discord step's first line is always where the values come
from. Both sentences are SERVER-rendered with a real anchor (a plain ``href``), so nothing here
depends on a script — the raw HTML is the JavaScript-off document.

THE PROBE IS REAL. :func:`test_the_probe_reads_open_closed_and_no_answer` drives
``setup.database._probe_local_postgres`` against a live listener, a refused port and a filtered
address, and ``test_the_rendered_top_for_each_real_port_state`` walks the SAME three states through
the shipped ASGI application — the probe's own port is pointed at a socket the test controls, so the
render's top is deterministic on any machine.

UNVERIFIED: a filtered port is exercised with a blackhole address (``10.255.255.1``); on a host whose
network refuses that route outright the probe still returns ``None`` (an unreachable route is not a
refusal), but the "no answer in time" branch itself is only proven where the SYN is actually dropped.
"""
from __future__ import annotations

import asyncio
import socket
import time

import pytest

from conftest import asgi_client
from services.webservice.setup import database as db_step
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import SetupDraft
from services.webservice.setup.tour import STEP_PATH
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"

#: a blackhole address whose SYNs are dropped (the reachable-but-filtered case).
BLACKHOLE = "10.255.255.1"


def build(config_dir, *, code: str = CODE):
    window = SetupWindow(code=code, wrong_delay=0.0)
    return create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN), window


async def enter(client, code: str = CODE):
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


async def step_html(client, number: int) -> str:
    return (await client.get(STEP_PATH, params={"n": str(number)})).text


def seed_step_three(config_dir, **database):
    draft = SetupDraft(step=2)
    draft.database = dict(database) or {"path": "create"}
    draft.save(config_dir)


def seed_step_five(config_dir):
    SetupDraft(step=4, discord=True).save(config_dir)


def _listener() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


# ------------------------------------------------------------------------------- the probe itself

def test_the_probe_reads_open_closed_and_no_answer(monkeypatch):
    """The three states, driven through the REAL probe (a socket, not a stub)."""
    listener = _listener()
    monkeypatch.setattr(db_step, "LOCAL_POSTGRES_PORT", listener.getsockname()[1])
    try:
        assert db_step._probe_local_postgres(db_step.LOCAL_PROBE_TIMEOUT) is True
    finally:
        listener.close()
    # the listener is gone: the same port now refuses the connection — or, on a host whose firewall
    # drops loopback SYN, answers nothing. Either way it must never read as OPEN
    assert db_step._probe_local_postgres(db_step.LOCAL_PROBE_TIMEOUT) is not True
    # a filtered port (SYN dropped) answers nothing in time
    monkeypatch.setattr(db_step, "DEFAULT_HOST", BLACKHOLE)
    assert db_step._probe_local_postgres(db_step.LOCAL_PROBE_TIMEOUT) is None


def test_a_refused_connection_is_a_definite_no(monkeypatch):
    """The ``False`` branch itself, independent of this host's loopback behaviour."""
    class _Refusing:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            raise ConnectionRefusedError(10061, "no listener")

    monkeypatch.setattr(db_step.socket, "socket", _Refusing)
    assert db_step._probe_local_postgres(db_step.LOCAL_PROBE_TIMEOUT) is False


def test_the_filtered_probe_is_bounded(monkeypatch):
    monkeypatch.setattr(db_step, "DEFAULT_HOST", BLACKHOLE)
    start = time.monotonic()
    answer = db_step._probe_local_postgres(db_step.LOCAL_PROBE_TIMEOUT)
    elapsed = time.monotonic() - start
    assert answer is None, answer
    assert elapsed < db_step.LOCAL_PROBE_TIMEOUT * 3, f"the probe took {elapsed:.2f}s"


def test_the_probe_timeout_is_at_most_one_second():
    assert 0 < db_step.LOCAL_PROBE_TIMEOUT <= 1.0


@pytest.mark.asyncio
async def test_the_probe_runs_once_per_process_and_is_cached(monkeypatch, tmp_path):
    app, _ = build(tmp_path)
    calls: list[float] = []

    def fake(timeout: float) -> bool:
        calls.append(timeout)
        return True

    monkeypatch.setattr(db_step, "_probe_local_postgres", fake)
    assert await db_step.local_postgres_open(app) is True
    assert await db_step.local_postgres_open(app) is True
    assert calls == [db_step.LOCAL_PROBE_TIMEOUT], "the port was probed more than once"
    assert db_step.cached_local_postgres(app) is True


@pytest.mark.asyncio
async def test_the_probe_runs_off_the_event_loop(monkeypatch, tmp_path):
    """The blocking connect runs in a worker thread: the loop keeps ticking while it waits."""
    app, _ = build(tmp_path)

    def slow(timeout: float) -> bool:
        time.sleep(0.2)
        return False

    monkeypatch.setattr(db_step, "_probe_local_postgres", slow)
    ticks: list[int] = []

    async def tick():
        for _ in range(5):
            await asyncio.sleep(0.01)
            ticks.append(1)

    await asyncio.gather(db_step.local_postgres_open(app), tick())
    assert len(ticks) == 5, "the event loop was blocked by the probe"


# ------------------------------------------------------------------------------- the Database top

@pytest.mark.asyncio
async def test_the_rendered_top_for_each_real_port_state(tmp_path, monkeypatch):
    """Open / closed / filtered, each through the real probe and the shipped app, at the page's top."""
    listener = _listener()
    monkeypatch.setattr(db_step, "LOCAL_POSTGRES_PORT", listener.getsockname()[1])

    # OPEN: a real listener answers — the page carries NO install sentence at all
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        open_html = await step_html(client, 3)
    listener.close()
    assert "install PostgreSQL" not in open_html
    assert "postgresql.org" not in open_html
    # the page starts with the fields: the path choice is the first control
    assert open_html.index("<fieldset") < open_html.index('name="host"')

    # CLOSED: the listener is gone, so the port refuses — the sentence is the FIRST line
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        closed_html = await step_html(client, 3)
    assert "To use DCSServerBot, you need to install PostgreSQL. You can download it " in closed_html
    assert ('href="https://www.postgresql.org/download/" target="_blank" '
            'rel="noopener noreferrer"') in closed_html
    assert closed_html.index("To use DCSServerBot") < closed_html.index("<fieldset")

    # FILTERED (no answer in time): err toward SHOWING the sentence
    monkeypatch.setattr(db_step, "DEFAULT_HOST", BLACKHOLE)
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        filtered_html = await step_html(client, 3)
    assert "To use DCSServerBot, you need to install PostgreSQL." in filtered_html


@pytest.mark.asyncio
async def test_the_top_line_is_server_rendered_with_a_plain_anchor(tmp_path, monkeypatch):
    """JavaScript off: the sentence and its link are in the served HTML — a real href, no script."""
    monkeypatch.setattr(db_step, "_probe_local_postgres", lambda timeout: False)
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = await step_html(client, 3)
    assert html.index("To use DCSServerBot") < html.index("<script")
    assert ('<a class="link" href="https://www.postgresql.org/download/" '
            'target="_blank" rel="noopener noreferrer">here</a>') in html
    # the address is also plain text, so a copy carries it without following the link
    assert "<code>https://www.postgresql.org/download/</code>" in html


@pytest.mark.asyncio
async def test_the_cluster_block_never_repeats_the_top_sentence(tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "_probe_local_postgres", lambda timeout: False)
    seed_step_three(tmp_path, federation=True, cluster={"path": "create"})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = await step_html(client, 3)
    assert "install PostgreSQL" in html
    assert html.count("To use DCSServerBot, you need to install PostgreSQL.") == 1


# ------------------------------------------------------------------------------- the Discord top

@pytest.mark.asyncio
async def test_the_discord_page_starts_with_the_portal_sentence(tmp_path):
    """Step 5's FIRST line, the link inside it, drawn once — and the token help carries no link."""
    seed_step_five(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = await step_html(client, 5)
    assert "To obtain the following values, you need to log into the " in html
    assert ('<a class="link" href="https://discord.com/developers/applications" '
            'target="_blank" rel="noopener noreferrer">Discord Developer Portal</a>') in html
    # the sentence is the FIRST line: above the credential grid and above the first field
    assert html.index("To obtain the following values") < html.index('<div class="fields">')
    assert html.index("To obtain the following values") < html.index('name="guild_id"')
    # JS-off: it is in the served HTML, before any script
    assert html.index("To obtain the following values") < html.index("<script")
    # the link is drawn ONCE, not in the token field's own help (which keeps its portal path)
    assert html.count('href="https://discord.com/developers/applications"') == 1
    assert "→ your application → Bot → Reset Token." in html
