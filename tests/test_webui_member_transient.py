"""A member the bot cannot resolve RIGHT NOW is not a member who is gone.

Three outcomes in ``DiscordOAuthBackend``: a DEFINITIVE negative (the lookup ANSWERED and the person
is not in the guild -- the member list is there and empty, so the cache is authoritative) drops the
resolution and refuses, as before; a TRANSIENT / INDECISIVE one (the bot is not ready, has no member
list, or the lookup raised/rate-limited/timed out) KEEPS the session and its remembered resolution
and returns a retryable 503 + ``Retry-After`` with a background retry and backoff; success refreshes.
The client must RETRY a 503 with backoff while 401/403 still stop it. No request reaches the network,
and no test signs in twice."""
from __future__ import annotations

import asyncio
import logging
import re
import types
from pathlib import Path

import pytest
from fastapi import APIRouter, Request

from conftest import asgi_client, create_app, install_shell, permissions
from services.webservice.auth import discord_oauth
from services.webservice.auth import local as local_auth
from services.webservice.shell import SHELL_STATIC_DIR

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"
CLIENT_ID = "123456789012345678"
CLIENT_SECRET = "CLIENT-SECRET-MUST-NOT-LEAK"
PUBLIC_BASE_URL = "http://localhost:9876"
DISCORD_USER_ID = 987654321098765432
GUILD_ID = 424242424242424242
ACCESS_TOKEN = "ACCESS-TOKEN-MUST-NOT-LEAK"

BOT_YAML = "roles:\n  Admin:\n    - Staff\n"

ROLE_ID = 555555555555555555
ROLES_MAP = {"Admin": [ROLE_ID]}

LOGGER = "services.webservice.auth.discord_oauth"


# --------------------------------------------------------------------------- test doubles

class FakeRole:
    def __init__(self, name: str = "", role_id: int = 0):
        self.name = name
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()):
        self.roles = list(roles)


class FakeGuild:
    def __init__(self, members=None, guild_id=None):
        self._members = dict(members or {})
        self.id = GUILD_ID if guild_id is None else guild_id

    def get_member(self, user_id):
        return self._members.get(user_id)

    async def fetch_member(self, user_id):  # pragma: no cover - the REST lookup is stubbed
        raise AssertionError("a test reached Guild.fetch_member - stub fetch_member_rest instead")


class FakeBot:
    def __init__(self, *, guilds=(), roles=None, intents_members: bool = False):
        self.guilds = list(guilds)
        self.roles = dict(ROLES_MAP if roles is None else roles)
        self.intents = types.SimpleNamespace(members=intents_members)

    def __getattr__(self, name):
        raise AssertionError(f"the OAuth backend touched bot.{name} - not part of the role model")


def _member(*, ids=(ROLE_ID,)) -> FakeMember:
    return FakeMember([FakeRole("", i) for i in ids])


def _bot_without_a_member_list() -> FakeBot:
    """What ``privileged_intents: false`` gives the console: guilds, but an EMPTY member cache."""
    return FakeBot(guilds=[FakeGuild({})])


def _bot_not_ready() -> FakeBot:
    """A bot mid-start-up: the object exists, its guilds have not loaded yet."""
    return FakeBot(guilds=[])


def _bot_that_sees_members(members=None) -> FakeBot:
    """The members intent ON: the cache is populated and authoritative."""
    return FakeBot(guilds=[FakeGuild(members or {}, )], intents_members=True)


class _Forbidden(Exception):
    status = 403


class RestLookups:
    def __init__(self):
        self.bot_calls: list[str] = []
        self.user_calls: list[tuple] = []
        self.bot_result = None
        self.user_result = None
        self.bot_error: Exception | None = None
        self.user_error: Exception | None = None

    async def fetch_member_rest(self, bot, subject):
        self.bot_calls.append(subject)
        if self.bot_error is not None:
            raise self.bot_error
        return self.bot_result

    async def fetch_user_member(self, access_token, guild_ids):
        self.user_calls.append((access_token, tuple(guild_ids)))
        if self.user_error is not None:
            raise self.user_error
        return self.user_result


# --------------------------------------------------------------------------- fixtures

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(*, member_ttl: int = 60) -> dict:
    auth: dict = {"public_base_url": PUBLIC_BASE_URL,
                  "local": {"enabled": True, "users": [
                      {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]}]},
                  "discord": {"enabled": True, "client_id": CLIENT_ID,
                              "client_secret": CLIENT_SECRET, "member_ttl": member_ttl}}
    return {"session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 3600},
            "auth": auth}


@pytest.fixture(autouse=True)
def rest_lookups(monkeypatch):
    lookups = RestLookups()
    monkeypatch.setattr(discord_oauth, "fetch_member_rest", lookups.fetch_member_rest,
                        raising=False)
    monkeypatch.setattr(discord_oauth, "fetch_user_member", lookups.fetch_user_member,
                        raising=False)
    return lookups


@pytest.fixture(autouse=True)
def stub_discord(monkeypatch):
    async def exchange_code(client_id, client_secret, code, redirect_uri):
        return {"access_token": ACCESS_TOKEN}

    async def fetch_user(access_token):
        return {"id": str(DISCORD_USER_ID), "username": "specialk"}

    monkeypatch.setattr(discord_oauth, "exchange_code", exchange_code)
    monkeypatch.setattr(discord_oauth, "fetch_user", fetch_user)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(discord_oauth, "_now", lambda: now[0])
    return now


def _app_with_probe(config_dir, config, monkeypatch, holder):
    """The real app + a gated probe page, with the bot object swapped live through *holder*."""
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: holder["bot"])
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True,
                                                              config_dir=str(config_dir)),
                              config=config)
    permissions.declare_capability("probe.admin", ("Admin",))

    router = APIRouter()

    @router.get("/probe/whoami")
    async def whoami(request: Request):
        return {"roles": sorted(permissions.role_names_for(request))}

    @router.get("/probe/admin")
    async def admin():
        return {"ok": True}

    registrar.register_pages("probe", router,
                             capabilities={"/probe/whoami": permissions.PUBLIC,
                                           "/probe/admin": "probe.admin"})
    return app


async def _settle(backend) -> None:
    """Run the background member refreshes this backend scheduled."""
    tasks = list(backend._refreshes)
    if tasks:
        await asyncio.gather(*tasks)
    await asyncio.sleep(0)


async def _sign_in(client):
    from urllib.parse import parse_qs, urlparse
    start = await client.get(discord_oauth.START_PATH)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    return await client.get(f"{discord_oauth.CALLBACK_PATH}?code=AUTH-CODE&state={state}")


# ------------------------------------------------------- a transient failure keeps the session

@pytest.mark.asyncio
async def test_a_bot_restart_does_not_kill_an_open_session(config_dir, monkeypatch, clock,
                                                           rest_lookups):
    """Sign in, then the bot restarts (no guilds yet, REST raising) and the SAME cookie must survive --
    503 (retryable), then 200 again once the bot is ready, no re-login."""
    holder = {"bot": _bot_without_a_member_list()}
    rest_lookups.bot_result = _member()
    app = _app_with_probe(config_dir, _config(), monkeypatch, holder)
    backend = app.state.webui_auth.backend("discord")

    async with asgi_client(app) as client:
        assert (await _sign_in(client)).status_code == 303, "the sign-in itself must work"
        assert (await client.get("/probe/admin")).status_code == 200

        holder["bot"] = _bot_not_ready()          # the bot restarts
        rest_lookups.bot_error = _Forbidden("the bot is not ready")
        clock[0] += 61                            # the remembered resolution is beyond its TTL
        assert (await client.get("/probe/admin")).status_code == 200, \
            "the remembered resolution is still served once while the re-check runs"
        await _settle(backend)                    # the re-check cannot decide -> transient window

        transient = await client.get("/probe/admin")
        assert transient.status_code == 503, transient.text
        assert transient.headers.get("retry-after"), "a 503 must say when to retry"
        assert transient.headers["retry-after"].isdigit()

        clock[0] += 5                             # past the first backoff step
        holder["bot"] = _bot_without_a_member_list()
        rest_lookups.bot_error = None
        rest_lookups.bot_result = _member()
        assert (await client.get("/probe/admin")).status_code == 503, \
            "the request that SCHEDULES the retry is still inside the window"
        await _settle(backend)
        assert (await client.get("/probe/admin")).status_code == 200, \
            "the session recovered WITHOUT a new sign-in"
        assert (await client.get("/probe/whoami")).json()["roles"] == ["Admin"]


@pytest.mark.asyncio
async def test_the_transient_window_serves_no_data_to_anyone(config_dir, monkeypatch, clock,
                                                             rest_lookups):
    """Fail closed is unchanged: an unverifiable identity gets NO data — for an Admin too, and for
    the fragment endpoint an open dashboard polls."""
    holder = {"bot": _bot_without_a_member_list()}
    rest_lookups.bot_result = _member()
    app = _app_with_probe(config_dir, _config(), monkeypatch, holder)
    backend = app.state.webui_auth.backend("discord")

    async with asgi_client(app) as client:
        assert (await _sign_in(client)).status_code == 303
        assert (await client.get("/probe/admin")).status_code == 200
        holder["bot"] = _bot_not_ready()
        rest_lookups.bot_error = _Forbidden("not ready")
        clock[0] += 61
        await client.get("/probe/admin")
        await _settle(backend)

        page = await client.get("/probe/admin")
        assert page.status_code == 503, page.text
        assert page.json()["detail"], "the refusal must say something a person can act on"
        assert '"ok"' not in page.text, "no data may be served while the identity is unverifiable"

        from services.webservice.pages import live as live_page
        fragments = await client.get(live_page.DASHBOARD_FRAGMENTS_PATH,
                                     headers={"accept": "application/json"})
        assert fragments.status_code == 503, fragments.text
        assert fragments.headers.get("retry-after")
        assert "log-body" not in fragments.text


@pytest.mark.asyncio
async def test_a_browser_navigation_into_the_window_gets_the_retrying_page(config_dir, monkeypatch,
                                                                          clock, rest_lookups):
    """Through the REAL gate: a browser (``Accept: text/html``) on a page path during the transient
    window is answered with the AUTO-RETRYING page -- not the JSON document the operator saw, and NOT a
    redirect to the login door (the visitor is signed in; only the verification is late)."""
    holder = {"bot": _bot_without_a_member_list()}
    rest_lookups.bot_result = _member()
    app = _app_with_probe(config_dir, _config(), monkeypatch, holder)
    backend = app.state.webui_auth.backend("discord")

    async with asgi_client(app) as client:
        assert (await _sign_in(client)).status_code == 303
        assert (await client.get("/probe/admin")).status_code == 200
        holder["bot"] = _bot_not_ready()
        rest_lookups.bot_error = _Forbidden("not ready")
        clock[0] += 61
        await client.get("/probe/admin")
        await _settle(backend)

        page = await client.get("/probe/admin",
                                headers={"accept": "text/html,application/xhtml+xml"})

    assert page.status_code == 503, page.text
    assert page.headers["content-type"].startswith("text/html"), page.headers
    assert page.headers.get("retry-after"), "the page carries the Retry-After the JSON face does"
    assert '"detail"' not in page.text, "the browser must not get the raw JSON document"
    assert "location" not in {k.lower() for k in page.headers}, "never a redirect to the login door"
    assert "/auth/login" not in page.text and "/auth/discord" not in page.text
    assert "location.pathname" in page.text and "setInterval" in page.text, \
        "the page must re-request the path by itself"
    assert '\"ok\"' not in page.text, "fail closed is unchanged: no data is served"


@pytest.mark.asyncio
async def test_a_definitive_negative_still_refuses_the_session(config_dir, monkeypatch,
                                                               rest_lookups):
    """The other side of the split, unchanged: a bot that CAN see members (its member list is
    authoritative) and a person who is genuinely not one is refused — no retry, no 503."""
    holder = {"bot": _bot_without_a_member_list()}
    rest_lookups.bot_result = _member()
    app = _app_with_probe(config_dir, _config(), monkeypatch, holder)
    backend = app.state.webui_auth.backend("discord")

    async with asgi_client(app) as client:
        assert (await _sign_in(client)).status_code == 303
        assert (await client.get("/probe/admin")).status_code == 200
        holder["bot"] = _bot_that_sees_members()   # the member list is there, and the person is not
        refusal = await client.get("/probe/admin")
    assert refusal.status_code == 403, "a definitive negative is a refusal, not a retry"
    assert refusal.headers.get("retry-after") is None
    assert backend._unverifiable.get(str(DISCORD_USER_ID)) is None, \
        "a definitive negative must not open the transient window"


# ------------------------------------------------------------------- the terse, once-only log

@pytest.mark.asyncio
async def test_the_transient_window_logs_one_terse_line_and_no_paragraph(config_dir, monkeypatch,
                                                                         clock, rest_lookups,
                                                                         caplog):
    """One line per CONDITION, no subject id, no paragraph -- and ONE line when it clears."""
    holder = {"bot": _bot_without_a_member_list()}
    rest_lookups.bot_result = _member()
    app = _app_with_probe(config_dir, _config(), monkeypatch, holder)
    backend = app.state.webui_auth.backend("discord")

    with caplog.at_level(logging.DEBUG):
        async with asgi_client(app) as client:
            assert (await _sign_in(client)).status_code == 303
            holder["bot"] = _bot_not_ready()
            rest_lookups.bot_error = _Forbidden("not ready")
            clock[0] += 61
            await client.get("/probe/admin")
            await _settle(backend)
            for _ in range(25):
                assert (await client.get("/probe/admin")).status_code == 503
            clock[0] += 5
            holder["bot"] = _bot_without_a_member_list()
            rest_lookups.bot_error = None
            await client.get("/probe/admin")
            await _settle(backend)
            assert (await client.get("/probe/admin")).status_code == 200

    ours = [r for r in caplog.records if r.name == LOGGER]
    refusals = [r for r in ours if r.levelno == logging.WARNING]
    recoveries = [r for r in ours if "recovered" in r.getMessage()]
    assert len(refusals) == 1, [r.getMessage() for r in refusals]
    assert len(recoveries) == 1, [r.getMessage() for r in recoveries]
    assert "cannot resolve guild members" not in refusals[0].getMessage(), \
        "the long paragraph belongs to the one-shot notices, not the polling request path"
    assert str(DISCORD_USER_ID) not in refusals[0].getMessage(), "no subject id in the repeated line"
    assert "Server Members Intent" not in refusals[0].getMessage()
    assert re.search(r"recovered.*\d+ minute", recoveries[0].getMessage()), \
        recoveries[0].getMessage()


@pytest.mark.asyncio
async def test_the_same_condition_is_one_line_for_every_request(config_dir, monkeypatch, clock,
                                                                rest_lookups, caplog):
    """The request path and the background refresh share ONE condition (the existing
    once-per-condition helper), so they cannot report the same fact twice."""
    holder = {"bot": _bot_without_a_member_list()}
    rest_lookups.bot_result = _member()
    app = _app_with_probe(config_dir, _config(), monkeypatch, holder)
    backend = app.state.webui_auth.backend("discord")

    with caplog.at_level(logging.DEBUG):
        async with asgi_client(app) as client:
            assert (await _sign_in(client)).status_code == 303
            holder["bot"] = _bot_not_ready()
            rest_lookups.bot_error = _Forbidden("not ready")
            clock[0] += 61
            assert (await client.get("/probe/admin")).status_code == 200, \
                "the remembered resolution is served once while the re-check runs"
            await _settle(backend)
            for _ in range(10):
                assert (await client.get("/probe/admin")).status_code == 503
                await _settle(backend)
    warnings = [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "cannot resolve guild members" not in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_the_retryable_line_names_both_the_status_and_the_retry_after(config_dir, monkeypatch,
                                                                           clock, rest_lookups,
                                                                           caplog):
    """The line used to format the Retry-After SECONDS as an HTTP status: ``"retryable (HTTP %d)"
    % MEMBER_RETRY_AFTER`` logged "HTTP 5" while the answer the caller receives is a 503.

    Both numbers must be the ones the answer actually carries -- the status from the one constant
    the gate answers with (``permissions.RETRYABLE_STATUS``) and the same Retry-After header value.
    """
    holder = {"bot": _bot_without_a_member_list()}
    rest_lookups.bot_result = _member()
    app = _app_with_probe(config_dir, _config(), monkeypatch, holder)
    backend = app.state.webui_auth.backend("discord")

    with caplog.at_level(logging.DEBUG):
        async with asgi_client(app) as client:
            assert (await _sign_in(client)).status_code == 303
            holder["bot"] = _bot_not_ready()
            rest_lookups.bot_error = _Forbidden("not ready")
            clock[0] += 61
            assert (await client.get("/probe/admin")).status_code == 200, \
                "the remembered resolution is served once while the re-check runs"
            await _settle(backend)              # the re-check cannot decide -> transient window
            response = await client.get("/probe/admin")
            assert response.status_code == 503
            assert response.headers["retry-after"] == str(discord_oauth.MEMBER_RETRY_AFTER)

    warnings = [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    line = warnings[0].getMessage()
    assert permissions.RETRYABLE_STATUS == 503, "the constant the gate answers with"
    assert f"(HTTP {permissions.RETRYABLE_STATUS}, Retry-After {discord_oauth.MEMBER_RETRY_AFTER})" \
        in line, line
    assert f"HTTP {discord_oauth.MEMBER_RETRY_AFTER}," not in line, \
        f"the Retry-After seconds were formatted as the status again: {line}"


# ------------------------------------------------------- the chain the background refresh uses

@pytest.mark.asyncio
async def test_the_background_refresh_uses_the_same_resolution_chain_as_the_login(config_dir,
                                                                                  monkeypatch,
                                                                                  rest_lookups):
    """One chain, not a second one: the refresh calls ``resolve_login_member`` (live cache, then the
    bot-side single-member lookup, then the user-side members.read lookup when a token is available)
    — the same function the login resolves with."""
    holder = {"bot": _bot_without_a_member_list()}
    rest_lookups.bot_result = _member()
    app = _app_with_probe(config_dir, _config(), monkeypatch, holder)
    backend = app.state.webui_auth.backend("discord")

    seen: list[tuple] = []
    real = discord_oauth.resolve_login_member

    async def spy(bot, subject, access_token):
        seen.append((subject, access_token))
        return await real(bot, subject, access_token)

    monkeypatch.setattr(discord_oauth, "resolve_login_member", spy)
    async with asgi_client(app) as client:
        assert (await _sign_in(client)).status_code == 303
        signed_in_calls = list(rest_lookups.bot_calls)
        backend._resolution.clear()               # as if the webservice had restarted
        assert (await client.get("/probe/admin")).status_code == 503
        await _settle(backend)
        assert (await client.get("/probe/admin")).status_code == 200

    assert seen, "the refresh did not go through the login resolution chain"
    assert all(call[0] == str(DISCORD_USER_ID) for call in seen)
    assert rest_lookups.bot_calls == signed_in_calls + [str(DISCORD_USER_ID)], \
        "the bot-side single-member lookup is the second link of the chain"


@pytest.mark.asyncio
async def test_an_answer_of_nobody_without_a_member_list_keeps_the_session(config_dir, monkeypatch,
                                                                           rest_lookups):
    """Without the member list a "nobody" answer cannot be told apart from the intent gate, so it is
    INDECISIVE: the window stays open and the client keeps retrying instead of losing the session."""
    holder = {"bot": _bot_without_a_member_list()}
    rest_lookups.bot_result = _member()
    app = _app_with_probe(config_dir, _config(), monkeypatch, holder)
    backend = app.state.webui_auth.backend("discord")
    async with asgi_client(app) as client:
        assert (await _sign_in(client)).status_code == 303
        rest_lookups.bot_result = None            # the lookup answered "nobody"
        backend._resolution.pop(str(DISCORD_USER_ID), None)
        assert (await client.get("/probe/admin")).status_code == 503
        await _settle(backend)
    assert backend._unverifiable.get(str(DISCORD_USER_ID)) is not None, \
        "the window must stay open while the bot cannot answer"


# ------------------------------------------------------------------------------- the client

def test_the_client_retries_a_503_with_backoff_and_still_stops_on_401_403():
    """Source assertion on the SERVED asset (there is no JS harness in this repo — this is a source
    fact, not a live browser test): 401/403 stop the poll loop, 503 is waited out with a growing
    delay so an open dashboard recovers by itself once the bot is ready."""
    source = (Path(SHELL_STATIC_DIR) / "shell.js").read_text(encoding="utf-8")
    assert "503" in source, "the client does not know about the retryable refusal"
    assert "retry-after" in source.lower(), "the client must honour Retry-After"
    assert re.search(r"backoff|backOff|Math\.min", source), \
        "the client must back off instead of hammering"
    assert "status === 401 || response.status === 403" in source, \
        "the stop-on-refusal half must stay"
