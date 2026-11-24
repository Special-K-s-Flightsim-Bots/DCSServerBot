"""Expected refusals must not flood the bot log, and the client must stop polling into one.

An EXPECTED refusal (no session, or a session the identity layer could not resolve) is not an
incident: no WARNING and no INFO from the three request-path sites. A CONDITION is explained once --
the first occurrence carries the full reason, repeats are counted into a single summary line keyed per
condition, never per request. An anonymous request never attempts member resolution. The refusal FACES
are unchanged (same status and body for both API caller and browser). The shipped client STOPS its
poll loop on a refusal and offers the way back in (a source assertion on the served asset, not a live
browser test). Runs in-process."""
from __future__ import annotations

import logging
import re
import types
from pathlib import Path

import pytest
from starlette.requests import Request

from conftest import asgi_client, create_app, install_shell, permissions
from services.webservice import auth as auth_pkg
from services.webservice.auth import discord_oauth
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import live as live_page

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"
CLIENT_ID = "123456789012345678"
CLIENT_SECRET = "CLIENT-SECRET-MUST-NOT-LEAK"
PUBLIC_BASE_URL = "http://localhost:9876"
DISCORD_USER_ID = 987654321098765432

BOT_YAML = "roles:\n  Admin:\n    - specialk\n  DCS Admin:\n    - mod1\n"

#: the three request-path loggers (the identity manager, the Discord backend and the
#: access gate). The counts are filtered by logger so an unrelated INFO at startup cannot mask a
#: refusal that still writes a line.
NOISY_LOGGERS = frozenset({
    "services.webservice.auth",
    "services.webservice.auth.discord_oauth",
    "services.webservice.permissions",
})


# ------------------------------------------------------------------------------------ fixtures

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _node(config_dir) -> types.SimpleNamespace:
    return types.SimpleNamespace(master=True, config_dir=str(config_dir))


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(*, discord: dict | None = None, local_roles=("Admin",)) -> dict:
    config = {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": PUBLIC_BASE_URL,
                 "local": {"enabled": True, "users": [
                     {"username": "mod1", "password_hash": _hash(),
                      "roles": list(local_roles)}]}},
    }
    if discord is not None:
        config["auth"]["discord"] = discord
    return config


def _enabled_discord() -> dict:
    return {"enabled": True, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}


def _build(config_dir, config=None):
    app = create_app()
    registrar = install_shell(app, node=_node(config_dir), config=config or _config())
    return app, registrar


class _FakeGuild:
    """A guild whose member CACHE is empty — what a bot without the Server Members Intent has."""

    def __init__(self, members=None, guild_id=1):
        self._members = dict(members or {})
        self.id = guild_id

    def get_member(self, user_id):
        return self._members.get(user_id)


class _FakeBot:
    def __init__(self, *, guilds=(), intents_members: bool = False, roles=None):
        self.guilds = list(guilds)
        self.roles = dict(roles or {})
        self.intents = types.SimpleNamespace(members=intents_members)


def _authenticated_request(app, subject=str(DISCORD_USER_ID), backend="discord") -> Request:
    """A request whose session names a backend and a subject — the valid-but-unresolvable shape."""
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
             "path": live_page.DASHBOARD_FRAGMENTS_PATH, "raw_path": b"/", "query_string": b"",
             "root_path": "", "app": app,
             "session": {"webui_identity": {"backend": backend, "subject": subject}},
             "headers": [], "client": ("127.0.0.1", 51234), "server": ("testserver", 443)}
    return Request(scope)


def _records(caplog, *, levels=(logging.INFO, logging.WARNING, logging.ERROR), loggers=NOISY_LOGGERS):
    return [record for record in caplog.records
            if record.name in loggers and record.levelno in levels]


async def _login(client, username: str = "mod1", password: str = PASSWORD):
    response = await client.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', response.text).group(1)
    return await client.post(auth_routes.LOGIN_PATH,
                             data={"username": username, "password": password,
                                   "_csrf_token": token})


# ------------------------------------------------- expected refusals are not incidents (no session)

@pytest.mark.asyncio
async def test_repeated_anonymous_fragment_requests_write_no_warning_or_info(config_dir, caplog):
    """N polls with no session: ZERO records at WARNING and ZERO at INFO from the three sites.

    Asserted as COUNTS so a future "helpful" line inside the request path fails this test."""
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    with caplog.at_level(logging.DEBUG):
        async with asgi_client(app) as client:
            for _ in range(25):
                response = await client.get(live_page.DASHBOARD_FRAGMENTS_PATH,
                                            headers={"accept": "application/json"})
                assert response.status_code == 403
                assert response.json()["detail"]
    noisy = _records(caplog)
    assert noisy == [], "\n".join(f"{r.levelname} {r.name}: {r.getMessage()}" for r in noisy)


@pytest.mark.asyncio
async def test_an_anonymous_request_never_attempts_member_resolution(config_dir, monkeypatch):
    """The short-circuit: with NO session the identity layer must answer before any member
    resolution runs. A stub that raises proves nothing was attempted (a regression is a 500, not a
    logged line)."""
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))

    def _boom(*args, **kwargs):
        raise AssertionError("member resolution was attempted for an anonymous request")

    monkeypatch.setattr(discord_oauth, "resolve_member", _boom)
    monkeypatch.setattr(discord_oauth, "current_bot", _boom)
    async with asgi_client(app) as client:
        for _ in range(3):
            response = await client.get(live_page.DASHBOARD_FRAGMENTS_PATH,
                                        headers={"accept": "application/json"})
            assert response.status_code == 403, response.text


# --------------------------------------- a condition is explained once, its repeats are counted

def test_the_condition_log_explains_once_then_surfaces_the_suppressed_count(caplog):
    """The unit under every once-per-condition site: the first hit is logged in FULL, the repeats
    are counted silently, and one summary line carries the count when the interval elapses."""
    clock = [1000.0]
    conditions = permissions.ConditionLog(interval=300, clock=lambda: clock[0])
    summary = lambda count, since: f"suppressed {count} similar refusals since {since}"

    with caplog.at_level(logging.DEBUG):
        for index in range(20):
            conditions.hit("member-resolution", "the full reason, occurrence %d", index,
                           level=logging.WARNING, summary=summary)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "the full reason, occurrence 0" in warnings[0].getMessage()

    clock[0] += 301
    conditions.hit("member-resolution", "the full reason, occurrence %d", 99,
                   level=logging.WARNING, summary=summary)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2, [r.getMessage() for r in warnings]
    assert "suppressed 20 similar refusals" in warnings[1].getMessage()
    assert "occurrence" not in warnings[1].getMessage(), "the repeating line must not restate it"


def test_the_condition_log_surfaces_the_count_when_the_condition_ends(caplog):
    clock = [1000.0]
    conditions = permissions.ConditionLog(interval=300, clock=lambda: clock[0])
    summary = lambda count, since: f"suppressed {count} similar refusals since {since}"
    with caplog.at_level(logging.DEBUG):
        conditions.hit("member-resolution", "the full reason", level=logging.WARNING, summary=summary)
        conditions.hit("member-resolution", "the full reason", level=logging.WARNING, summary=summary)
        conditions.cleared("member-resolution", "the condition ended (%d suppressed)",
                           level=logging.INFO)
    assert any("the condition ended (1 suppressed)" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_valid_but_unresolvable_session_is_explained_exactly_once(config_dir, caplog,
                                                                         monkeypatch):
    """A cookie the identity layer cannot resolve (a bot restart, or no member list): ONE WARNING, and
    nothing further however many requests arrive.

    The long ``NO_MEMBERS_INTENT_REASON`` paragraph belongs to the one-shot notices (the login refusal
    and its operator-facing reading); a dashboard polls the request path all day, and the repeated
    paragraph is exactly the noise this pins out."""
    bot = _FakeBot(guilds=[_FakeGuild()], intents_members=False)
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: bot)

    async def _no_member(_bot, _subject):     # the background refresh must never reach Discord
        return None

    monkeypatch.setattr(discord_oauth, "fetch_member_rest", _no_member)
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    backend = app.state.webui_auth.backend("discord")

    with caplog.at_level(logging.DEBUG):
        for _ in range(25):
            assert backend.authenticate(_authenticated_request(app)) is None

    warnings = [r for r in caplog.records if r.name == "services.webservice.auth.discord_oauth"
                and r.levelno == logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    terse = warnings[0].getMessage()
    assert "cannot verify a session" in terse
    assert "cannot resolve guild members" not in terse, \
        "the long paragraph must not be rewritten onto the polling request path"
    assert "Server Members Intent" not in terse
    assert str(DISCORD_USER_ID) not in terse, "the repeating line carries no subject id"


# ------------------------------------ a signed-in caller lacking a capability gets ONE INFO line

@pytest.mark.asyncio
async def test_a_signed_in_session_lacking_a_capability_logs_one_info(config_dir, caplog):
    """The gate's INFO line is a CONDITION (this role set never satisfies this capability): once
    per capability, not once per request."""
    from conftest import simple_router

    app, registrar = _build(config_dir, _config(local_roles=("DCS Admin",)))
    permissions.declare_capability("probe.admin", ("Admin",))
    registrar.register_pages("probe", simple_router("/probe/admin"),
                             capabilities={"/probe/admin": "probe.admin"})
    with caplog.at_level(logging.DEBUG):
        async with asgi_client(app) as client:
            assert (await _login(client)).status_code == 303
            for _ in range(15):
                assert (await client.get("/probe/admin")).status_code == 403

    infos = [r for r in caplog.records
             if r.name == "services.webservice.permissions" and r.levelno == logging.INFO]
    assert len(infos) == 1, [r.getMessage() for r in infos]
    assert "capability 'probe.admin' needs one of Admin" in infos[0].getMessage()

    warnings = _records(caplog, levels=(logging.WARNING, logging.ERROR))
    assert warnings == [], [r.getMessage() for r in warnings]


# ------------------------------------------------------------- the refusal faces are unchanged

@pytest.mark.asyncio
async def test_the_refusal_faces_and_statuses_are_unchanged(config_dir):
    """The wire contract: an API/EventSource caller keeps the 403 + JSON body with no redirect, and
    an anonymous browser on a page path is still bounced to the login door with its `next`."""
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        api = await client.get(live_page.DASHBOARD_FRAGMENTS_PATH,
                               headers={"accept": "application/json"})
        assert api.status_code == 403
        assert api.headers.get("location") is None
        assert "text/html" not in api.headers.get("content-type", "")
        assert api.json()["detail"]

        event_source = await client.get(live_page.DASHBOARD_STREAM_PATH,
                                        headers={"accept": "text/event-stream"})
        assert event_source.status_code == 403
        assert event_source.headers.get("location") is None

        browser = await client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        assert browser.status_code in (302, 303, 307)
        assert browser.headers["location"].startswith(auth_routes.LOGIN_PATH)
        assert "next=/" in browser.headers["location"]


# --------------------------------------------------------------------- the client stops polling

def test_the_poll_loop_stops_on_a_refusal_and_offers_the_way_back():
    """Source assertion on the SERVED asset (there is no JS harness in this repo — stated as such in
    the report): on a refusal the poll interval is cleared, the status line says so in words, and a
    link back to the sign-in flow carrying the current path is offered.

    The WORDING itself is not in the script any more (card I5): the console's user-visible strings are
    SERVED per language (``/i18n/messages.js``), so the script says WHAT happens through a map key and
    the wording lives in the ``webui`` catalog. The source is read from the file the shell serves, and
    the key's English wording is read from the map the server renders."""
    from services.webservice import i18n
    from services.webservice.shell import SHELL_STATIC_DIR

    source = (Path(SHELL_STATIC_DIR) / "shell.js").read_text(encoding="utf-8")
    assert "clearInterval" in source, "the poll loop must be stoppable"
    assert "stopPolling" in source
 # the refusal is STATED in words — via the served map, not a literal baked into the script
    assert 't("live.not_permitted")' in source, "the refusal must name its served key"
    assert "live.not_permitted" in i18n.JS_MESSAGES
    assert "not permitted" in i18n.JS_MESSAGES["live.not_permitted"], \
        "the served wording says what happened in words"
    assert "/auth/login?next=" in source, "the way back in must be offered"
    assert "window.location.pathname" in source, "the current page is the landing target"
