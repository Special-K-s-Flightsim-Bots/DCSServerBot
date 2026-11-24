"""The Discord OAuth identity backend: the flow, the state nonce, the role expansion, refusals.

``GET /auth/discord`` mints a single-use state into the session and 302s to Discord's authorize
endpoint (identify + guilds.members.read); the callback consumes the state, exchanges the code,
fetches ``/users/@me``, resolves the member THROUGH THE RUNNING BOT, expands the member's Discord
roles into the bot's role NAMES and stores the identity reference. No request touches the network:
Discord's HTTP layer is stubbed and the bot is a plain stub handed in through the ``current_bot``
seam."""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import types
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest
from fastapi import APIRouter, Request

from conftest import asgi_client, create_app, install_shell, permissions
from services.webservice import auth
from services.webservice.auth import discord_oauth
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"

CLIENT_ID = "123456789012345678"
CLIENT_SECRET = "CLIENT-SECRET-MUST-NOT-LEAK"
AUTH_CODE = "AUTH-CODE-MUST-NOT-LEAK"
ACCESS_TOKEN = "ACCESS-TOKEN-MUST-NOT-LEAK"
PUBLIC_BASE_URL = "http://localhost:9876"
REDIRECT_URI = f"{PUBLIC_BASE_URL}{discord_oauth.CALLBACK_PATH}"
DISCORD_USER_ID = 987654321098765432
GUILD_ID = 424242424242424242

BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

#: `bot.yaml -> roles:` has the DISCORD meaning on this path: a bot role NAME -> the Discord roles
#: (names or ids) that grant it (`services/bot/dcsserverbot.py:114-125`). `mod1` holds the role
#: named `Staff` and the role whose id is `555...`, so it is an Admin and a DCS Admin and nothing
#: else -- the two spellings of a grant are both exercised.
DISCORD_ROLE_ID = 555555555555555555
ROLES_MAP = {"Admin": ["Staff"], "DCS Admin": [DISCORD_ROLE_ID], "DCS": ["DCS"]}


# --------------------------------------------------------------------------- test doubles

class FakeRole:
    def __init__(self, name: str, role_id: int = 0):
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
        raise AssertionError("a test reached Guild.fetch_member - stub "
                             "discord_oauth.fetch_member_rest instead")


class FakeBot:
    """The attributes the backend may read: ``guilds``, ``roles``, ``intents`` -- nothing else."""

    def __init__(self, *, guilds=(), roles=None, intents_members: bool = True):
        self.guilds = list(guilds)
        self.roles = dict(ROLES_MAP if roles is None else roles)
        self.intents = types.SimpleNamespace(members=intents_members)

    def __getattr__(self, name):
        raise AssertionError(f"the OAuth backend touched bot.{name} - not part of the role model")


def member(*, names=("Staff",), ids=()) -> FakeMember:
    return FakeMember([FakeRole(name) for name in names] + [FakeRole("", i) for i in ids])


def bot_with(member_obj, *, intents_members: bool = True) -> FakeBot:
    return FakeBot(guilds=[FakeGuild({DISCORD_USER_ID: member_obj})],
                   intents_members=intents_members)


# --------------------------------------------------------------------------- fixtures

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _node(config_dir):
    return types.SimpleNamespace(master=True, config_dir=str(config_dir))


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(*, discord: dict | None = None, local_users=None,
            public_base_url: str = PUBLIC_BASE_URL) -> dict:
    config = {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {
            "public_base_url": public_base_url,
            "local": {"enabled": True, "users": local_users if local_users is not None else [
                {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]}]},
        },
    }
    if discord is not None:
        config["auth"]["discord"] = discord
    return config


def _enabled_discord(**overrides) -> dict:
    block = {"enabled": True, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
    block.update(overrides)
    return block


def _build(config_dir, config=None):
    app = create_app()
    registrar = install_shell(app, node=_node(config_dir), config=config or _config())
    return app, registrar


@pytest.fixture
def stub_discord(monkeypatch):
    """Discord's HTTP layer, stubbed. No test in this file may reach the network."""

    async def exchange_code(client_id, client_secret, code, redirect_uri):
        assert client_id == CLIENT_ID
        assert client_secret == CLIENT_SECRET
        assert code
        assert redirect_uri == REDIRECT_URI, "the redirect URI must be the configured one"
        return {"access_token": ACCESS_TOKEN}

    async def fetch_user(access_token):
        assert access_token == ACCESS_TOKEN
        return {"id": str(DISCORD_USER_ID), "username": "specialk"}

    monkeypatch.setattr(discord_oauth, "exchange_code", exchange_code)
    monkeypatch.setattr(discord_oauth, "fetch_user", fetch_user)


@pytest.fixture
def stub_bot(monkeypatch):
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: bot_with(member(
        names=("Staff",), ids=(DISCORD_ROLE_ID,))))


class RestLookups:
    """The two REST member lookups, recorded and stubbed. NO test may reach the network.

    Both module-level functions are replaced unconditionally (autouse fixture), so a test that
    forgets to set a result gets ``None`` (no member) instead of an ``aiohttp`` call.
    """

    def __init__(self):
        self.bot_calls: list[str] = []
        self.user_calls: list[tuple] = []
        self.bot_result = None
        self.user_result = None
        self.bot_error: Exception | None = None
        self.user_error: Exception | None = None
 # the un-stubbed module functions, filled in by the fixture
        self.real_fetch_member_rest = None
        self.real_fetch_user_member = None

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


@pytest.fixture(autouse=True)
def rest_lookups(monkeypatch):
    lookups = RestLookups()
 # the REAL functions, for the tests that exercise them directly (the stubs below shadow the
 # module attributes, which is what keeps every other test off the network)
    lookups.real_fetch_member_rest = getattr(discord_oauth, "fetch_member_rest", None)
    lookups.real_fetch_user_member = getattr(discord_oauth, "fetch_user_member", None)
    monkeypatch.setattr(discord_oauth, "fetch_member_rest", lookups.fetch_member_rest,
                        raising=False)
    monkeypatch.setattr(discord_oauth, "fetch_user_member", lookups.fetch_user_member,
                        raising=False)
    return lookups


@pytest.fixture
def clock(monkeypatch):
    """A fake monotonic clock, so a TTL test never sleeps."""
    now = [1000.0]
    monkeypatch.setattr(discord_oauth, "_now", lambda: now[0])
    return now


class _Forbidden(Exception):
    """Shaped like ``discord.Forbidden``: a failure that is NOT 'no such member'."""

    status = 403


def _backend_of(app):
    return app.state.webui_auth.backend("discord")


async def _settle(backend) -> None:
    """Run the background member refreshes this backend scheduled."""
    tasks = list(backend._refreshes)
    if tasks:
        await asyncio.gather(*tasks)


async def _start(client, next_url="/"):
    return await client.get(f"{discord_oauth.START_PATH}?next={next_url}")


def _query(response) -> dict:
    return parse_qs(urlparse(response.headers["location"]).query)


def _state(response) -> str:
    return _query(response)["state"][0]


async def _callback(client, state: str, code: str = AUTH_CODE):
    return await client.get(f"{discord_oauth.CALLBACK_PATH}?code={code}&state={state}")


# ------------------------------------------------------------------- a cancelled authorization

@pytest.mark.asyncio
async def test_a_cancelled_authorization_discards_the_flow_and_needs_a_fresh_start(config_dir):
    """``?error=access_denied`` with the real state: a page (not a broken login), and the flow is
    spent -- the browser has to start again."""
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        start = await _start(client)
        state = _state(start)
        cancelled = await client.get(
            f"{discord_oauth.CALLBACK_PATH}?error=access_denied&state={state}")
        assert cancelled.status_code == 400
        assert discord_oauth.CANCELLED_MESSAGE in cancelled.text
 # the state was consumed by the cancel: the same one cannot be used to finish a login
        replay = await _callback(client, state)
        assert replay.status_code == 400


@pytest.mark.asyncio
async def test_a_cancelled_callback_from_another_browser_cannot_touch_this_session(
        config_dir, stub_discord, stub_bot):
    """A URL anybody can type must not be able to clear a pending login: the state is only judged,
    never consumed, unless it belongs to THIS session."""
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as mine, asgi_client(app) as theirs:
        start = await _start(mine)
        state = _state(start)
        stranger = await theirs.get(
            f"{discord_oauth.CALLBACK_PATH}?error=access_denied&state={state}")
        assert stranger.status_code == 400
 # my flow is still intact
        assert (await _callback(mine, state)).status_code == 303


# --------------------------------------------------------------- the happy path

@pytest.mark.asyncio
async def test_the_happy_path_signs_in_with_exactly_the_roles_the_member_granted(
        config_dir, stub_discord, stub_bot):
    """Acceptance: stubbed token + user + member -> the identity is set, ``role_names_for()`` is
    exactly the expanded role names, and the capability set matches (same shape the local backend
    asserts, so "offered <= authorised" holds per backend)."""
    permissions.declare_capability("probe.admin", ("Admin",))
    permissions.declare_capability("probe.other", ("GameMaster",))

    app, registrar = _build(config_dir, _config(discord=_enabled_discord()))
    router = APIRouter()

    @router.get("/probe/whoami")
    async def whoami(request: Request):
        return {"roles": sorted(permissions.role_names_for(request)),
                "ref": auth.session_identity_ref(request)}

    @router.get("/probe/admin")
    async def admin():
        return {"ok": True}

    @router.get("/probe/other")
    async def other():
        return {"ok": True}

    registrar.register_pages("probe", router, capabilities={
        "/probe/whoami": permissions.PUBLIC,
        "/probe/admin": "probe.admin",
        "/probe/other": "probe.other"})

    async with asgi_client(app) as client:
        start = await _start(client)
        assert start.status_code == 302
        finish = await _callback(client, _state(start))
        assert finish.status_code == 303, finish.text
        assert finish.headers["location"] == "/"

        payload = await client.get("/probe/whoami")
        assert payload.status_code == 200, payload.text
        payload = payload.json()

    assert payload["ref"] == {"backend": "discord", "subject": str(DISCORD_USER_ID)}
    assert payload["roles"] == ["Admin", "DCS Admin"], \
        "the member's Discord roles must expand into the bot's role NAMES"

 # the same shape the local backend asserts, per backend: whatever the sidebar OFFERS under
 # these roles must answer through the real gate
    offered = permissions.visible_nav(registrar.nav, payload["roles"])
    assert offered == [item for item in registrar.nav
                       if permissions.allows(item.capability, payload["roles"])]

    async with asgi_client(app) as client:
        start = await _start(client)
        await _callback(client, _state(start))
        assert (await client.get("/probe/admin")).status_code == 200
        assert (await client.get("/probe/other")).status_code == 403, \
            "a capability naming a role the member does not hold must still refuse"
        for item in offered:
            assert (await client.get(item.url)).status_code == 200, \
                f"the nav offers '{item.label}' ({item.url}) but the route does not answer"


def _probe_router() -> APIRouter:
    router = APIRouter()

    @router.get("/probe/whoami")
    async def whoami(request: Request):
        return {"roles": sorted(permissions.role_names_for(request)),
                "ref": auth.session_identity_ref(request)}

    @router.get("/probe/admin")
    async def admin():
        return {"ok": True}

    return router


def _app_with_probe(config_dir, config):
    permissions.declare_capability("probe.admin", ("Admin",))
    app, registrar = _build(config_dir, config)
    registrar.register_pages("probe", _probe_router(), capabilities={
        "/probe/whoami": permissions.PUBLIC, "/probe/admin": "probe.admin"})
    return app


async def _signed_in(client) -> None:
    start = await _start(client)
    finish = await _callback(client, _state(start))
    assert finish.status_code == 303, finish.text


# ------------------------------------------------------------------- the state nonce

@pytest.mark.asyncio
async def test_a_callback_without_a_state_is_refused(config_dir, stub_discord, stub_bot):
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        await _start(client)
        response = await client.get(f"{discord_oauth.CALLBACK_PATH}?code={AUTH_CODE}")
    assert response.status_code == 400
    assert discord_oauth.GENERIC_MESSAGE in response.text
    assert "Traceback" not in response.text


@pytest.mark.asyncio
async def test_an_unknown_state_is_refused_and_burns_the_nonce(config_dir, stub_discord,
                                                              stub_bot):
    """A wrong state is refused AND the session's own state is consumed with it: the mismatched
    attempt cannot be followed by a legitimate one, which is what makes a replay impossible."""
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        start = await _start(client)
        real = _state(start)
        wrong = await _callback(client, "not-the-session-state")
        assert wrong.status_code == 400
        assert discord_oauth.GENERIC_MESSAGE in wrong.text
        after = await _callback(client, real)
        assert after.status_code == 400, "the nonce must have been consumed by the failed attempt"


@pytest.mark.asyncio
async def test_a_replayed_state_is_refused(config_dir, stub_discord, stub_bot):
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        start = await _start(client)
        state = _state(start)
        assert (await _callback(client, state)).status_code == 303
        replayed = await _callback(client, state)
    assert replayed.status_code == 400
    assert discord_oauth.GENERIC_MESSAGE in replayed.text


@pytest.mark.asyncio
async def test_a_state_from_a_foreign_session_is_refused(config_dir, stub_discord, stub_bot):
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client_a:
        state = _state(await _start(client_a))
    async with asgi_client(app) as client_b:
        response = await _callback(client_b, state)
    assert response.status_code == 400, "a state is bound to the browser that started the flow"


# --------------------------------------------------- membership, roles and a missing bot

@pytest.mark.asyncio
async def test_a_non_member_of_the_guild_is_refused_with_a_logged_reason(
        config_dir, stub_discord, monkeypatch, caplog):
    """A bot that CAN see members (the intent is on) and a user who is genuinely not one: the
    refusal says so, and the reason stays a log line."""
    monkeypatch.setattr(discord_oauth, "current_bot",
                        lambda: FakeBot(guilds=[FakeGuild()], intents_members=True))
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            start = await _start(client)
            response = await _callback(client, _state(start))
    assert response.status_code == 400
    assert discord_oauth.GENERIC_MESSAGE in response.text
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "not a member of the bot's guild" in messages
    assert "not a member of the bot's guild" not in response.text, "the reason is a log line"


@pytest.mark.asyncio
async def test_a_member_with_no_mapped_roles_signs_in_and_is_refused_every_gated_page(
        config_dir, stub_discord, monkeypatch):
    monkeypatch.setattr(discord_oauth, "current_bot",
                        lambda: bot_with(member(names=("Nobody",))))
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        await _signed_in(client)
        payload = (await client.get("/probe/whoami")).json()
        assert payload["ref"] == {"backend": "discord", "subject": str(DISCORD_USER_ID)}
        assert payload["roles"] == []
        assert (await client.get("/probe/admin")).status_code == 403


@pytest.mark.asyncio
async def test_the_bot_being_none_is_a_clear_refusal_not_a_crash(
        config_dir, stub_discord, monkeypatch, caplog):
    """`bot = None` (early start, after a takeover, a headless install) is a refusal with a logged
    reason — never a crash and never a silent downgrade to "no roles"."""
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: None)
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            start = await _start(client)
            response = await _callback(client, _state(start))
    assert response.status_code == 400
    assert discord_oauth.GENERIC_MESSAGE in response.text
    assert "Traceback" not in response.text
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "the bot object is None" in messages


@pytest.mark.asyncio
async def test_bot_none_on_a_later_request_also_refuses(config_dir, stub_discord, monkeypatch):
    """The same refusal on the *per-request* path: a session that resolved once must not keep its
    capabilities while the bot object is gone."""
    monkeypatch.setattr(discord_oauth, "current_bot",
                        lambda: bot_with(member(names=("Staff",))))
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        await _signed_in(client)
        assert (await client.get("/probe/admin")).status_code == 200
        monkeypatch.setattr(discord_oauth, "current_bot", lambda: None)
        assert (await client.get("/probe/admin")).status_code == 403


@pytest.mark.asyncio
async def test_the_roles_are_resolved_live_and_never_stored_in_the_cookie(
        config_dir, stub_discord, monkeypatch):
    """The same cookie, a different answer: roles are a function of the live bot, not of the
    session. A role set stored in the cookie would survive the member losing it."""
    holder = types.SimpleNamespace(bot=bot_with(member(names=("Staff",))))
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: holder.bot)
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        await _signed_in(client)
        assert (await client.get("/probe/admin")).status_code == 200
        holder.bot = bot_with(member(names=("Nobody",)))
        assert (await client.get("/probe/admin")).status_code == 403
        assert (await client.get("/probe/whoami")).json()["roles"] == []


# --------------------------------------------------------------- secrets never leak

@pytest.mark.asyncio
async def test_neither_the_secret_nor_the_code_nor_the_token_reaches_a_response_or_a_log(
        config_dir, stub_discord, stub_bot, caplog):
    """No response body and no log record of this project carries the secret, the code or the token.

    Records from ``httpx`` (the in-process test client) are excluded: that is the transport narrating the
    URL it was asked to fetch, and the deployment starts uvicorn at ``log_level=logging.WARNING``, so its
    access logger -- the one that would print the one-time ``code=`` -- is off. The scope of this
    assertion is what the application logs itself (``services.webservice.*``).
    """
    secrets = (CLIENT_SECRET, AUTH_CODE, ACCESS_TOKEN)
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    with caplog.at_level(logging.DEBUG):
        async with asgi_client(app) as client:
            start = await _start(client)
            finish = await _callback(client, _state(start))
            page = await client.get(auth_routes.LOGIN_PATH)
    ours = [record for record in caplog.records if not record.name.startswith("httpx")]
    logged = "\n".join(record.getMessage() for record in ours)
    for body in (start.text, finish.text, page.text, logged, start.headers["location"]):
        for secret in secrets:
            assert secret not in body, f"{secret} leaked"
    assert ours, "floor: the flow must log something, or this assertion measures nothing"


def test_the_deployment_turns_off_the_access_log_that_would_carry_the_code():
    """The compensating fact behind the assertion above: uvicorn is started at WARNING, so the
    access logger (INFO) that prints ``GET /auth/discord/callback?code=...`` is not emitted.
    Changing that level is a one-time code appearing in every access log."""
    source = (REPO_ROOT / "services" / "webservice" / "service.py").read_text(encoding="utf-8")
    assert "log_level=logging.WARNING" in source


@pytest.mark.asyncio
async def test_a_failed_token_exchange_shows_one_generic_page(
        config_dir, stub_bot, monkeypatch, caplog):
    async def failing_exchange(client_id, client_secret, code, redirect_uri):
        raise discord_oauth.DiscordAuthError("Discord answered HTTP 401")

    monkeypatch.setattr(discord_oauth, "exchange_code", failing_exchange)
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            start = await _start(client)
            response = await _callback(client, _state(start))
    assert response.status_code == 400
    assert discord_oauth.GENERIC_MESSAGE in response.text
    assert "HTTP 401" not in response.text, "the reason is a log line, never the page"
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "HTTP 401" in messages


class _FailingSession:
    """An ``aiohttp.ClientSession`` whose connection fails — so the mapping can be tested without
    a network call. ``raises`` decides which failure class bubbles out."""

    raises = aiohttp.ClientConnectionError

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        raise self.raises("the identity provider is unreachable")

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("raises, expected", [
    (aiohttp.ClientConnectionError, "ClientConnectionError"),
    (TimeoutError, "TimeoutError"),
])
async def test_a_discord_network_failure_becomes_a_discord_auth_error(
        monkeypatch, raises, expected):
    """The bounded request maps every transport failure onto a DiscordAuthError whose message names
    the failure CLASS only — no URL, no body, no secret, and no traceback for the route to render."""
    _FailingSession.raises = raises
    monkeypatch.setattr(discord_oauth.aiohttp, "ClientSession", _FailingSession)
    with pytest.raises(discord_oauth.DiscordAuthError) as excinfo:
        await discord_oauth._request("GET", discord_oauth.USER_URL)
    assert expected in str(excinfo.value)


# ------------------------------------------------------------- the response body size cap

class _StubBody:
    """The bounded read seam a real ``aiohttp`` response exposes as ``.content``."""

    def __init__(self, raw: bytes):
        self.raw = raw

    async def read(self, size=None):
        return self.raw if not size else self.raw[:size]


class _CappedResponse:
    def __init__(self, status, raw: bytes):
        self.status = status
        self.content = _StubBody(raw)

    async def json(self, content_type=None):
        """The old seam, kept so an implementation that still calls it is not silently excused."""
        return json.loads(self.content.raw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _CappedSession:
    """An ``aiohttp.ClientSession`` answering from class state with a given raw body -- no network."""

    status = 200
    raw: bytes = b"{}"

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def request(self, method, url, **kwargs):
        return _CappedResponse(type(self).status, type(self).raw)


@pytest.mark.asyncio
async def test_an_oversized_discord_answer_is_refused_without_being_used(monkeypatch, caplog):
    """A wrong or hostile endpoint (or a hijacked DNS answer) must not be able to balloon memory in
    the bot process: the body is read with a cap, an oversized answer is a FAILURE, and neither the
    body nor a partial version of it is used, returned or logged."""
    marker = "OVERSIZED-ANSWER-MUST-NOT-BE-USED"
    _CappedSession.status = 200
    _CappedSession.raw = (b'{"id": "' + marker.encode() + b'", "pad": "'
                          + b"x" * discord_oauth.MAX_RESPONSE_BYTES + b'"}')
    assert len(_CappedSession.raw) > discord_oauth.MAX_RESPONSE_BYTES
    monkeypatch.setattr(discord_oauth.aiohttp, "ClientSession", _CappedSession)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(discord_oauth.DiscordAuthError) as excinfo:
            await discord_oauth.fetch_user(ACCESS_TOKEN)

    limit = str(discord_oauth.MAX_RESPONSE_BYTES)
    assert limit in str(excinfo.value), "the refusal must name the limit"
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert limit in messages, "a log line must name the limit"
    assert marker not in messages, "the body is never logged"
    assert marker not in str(excinfo.value), "the body is never in the failure reason"


@pytest.mark.asyncio
async def test_a_normal_small_discord_answer_still_passes_unchanged(monkeypatch):
    """The cap is a ceiling, not a behaviour change: an ordinary answer is parsed exactly as before."""
    payload = {"id": str(DISCORD_USER_ID), "username": "specialk"}
    _CappedSession.status = 200
    _CappedSession.raw = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(discord_oauth.aiohttp, "ClientSession", _CappedSession)

    assert await discord_oauth._request("GET", discord_oauth.USER_URL) == payload
    assert await discord_oauth.fetch_user(ACCESS_TOKEN) == payload


def test_the_module_does_not_import_the_bot_core_at_import_time():
    """Structural: no ABSOLUTE ``core``/``discord``/``services``/``psycopg`` import stands at module
    level of this file, and the secret store (``core.utils.os``) is imported INSIDE the function
    that reads it. This is NOT "the module is core-free": it reaches the bot's ``core`` through
    ``..scope`` (the shared ``managed_by`` rule), deliberately — the console runs in the bot's own
    environment. What the pin holds is that the heavy, side-effecting ABSOLUTE imports stay out of
    the module body."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for item in tree.body:
        if isinstance(item, ast.Import):
            names = {alias.name.split(".")[0] for alias in item.names}
        elif isinstance(item, ast.ImportFrom):
            names = {(item.module or "").split(".")[0]}
        else:
            continue
        assert not (names & {"core", "discord", "services", "psycopg"}), \
            f"{MODULE_PATH.name} imports {names} at import time"
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "from core.utils.os import get_password" in source


# --------------------------------------------------------------- the redirect URI

@pytest.mark.asyncio
async def test_the_redirect_uri_ignores_a_hostile_host_and_forwarded_headers(
        config_dir, stub_discord, stub_bot):
    """The redirect URI is the configured public_base_url plus the callback path — a request header
    must not be able to move it (a proxy would otherwise flip https to http, or land the callback
    on an origin that has no session cookie)."""
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        response = await client.get(discord_oauth.START_PATH, headers={
            "Host": "evil.example.com", "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "evil.example.com", "X-Forwarded-For": "10.0.0.9"})
    location = response.headers["location"]
    assert location.startswith(discord_oauth.AUTHORIZE_URL)
    query = parse_qs(urlparse(location).query)
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["scope"] == ["identify guilds.members.read"], \
        "identify plus the user-side members.read scope the fallback lookup needs"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == [CLIENT_ID]
    assert "evil.example.com" not in location


@pytest.mark.asyncio
async def test_the_configured_base_url_is_what_the_redirect_uri_uses(config_dir, stub_discord,
                                                                     stub_bot):
    """The other direction: change the config, the redirect URI moves with it (so the assertion
    above is not satisfied by a hard-coded literal)."""
    app, _ = _build(config_dir, _config(discord=_enabled_discord(),
                                        public_base_url="https://console.example.org"))
    async with asgi_client(app) as client:
        response = await client.get(discord_oauth.START_PATH)
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["redirect_uri"] == [f"https://console.example.org{discord_oauth.CALLBACK_PATH}"]


@pytest.mark.asyncio
async def test_an_open_redirect_next_target_is_refused(config_dir, stub_discord, stub_bot):
    """The landing target rides the existing validated `_safe_next` behaviour."""
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        for bad in ("//evil.example.com", "https://evil.example.com", "evil", ""):
            start = await _start(client, next_url=bad)
            finish = await _callback(client, _state(start))
            assert finish.status_code == 303
            assert finish.headers["location"] == "/", f"{bad!r} must not redirect off-site"
        start = await _start(client, next_url="/probe/admin")
        finish = await _callback(client, _state(start))
        assert finish.headers["location"] == "/probe/admin"


# ------------------------------------------------------------- the routes and the door

def test_the_oauth_paths_are_declared_public_and_the_shell_audit_is_still_empty(config_dir):
    app, registrar = _build(config_dir, _config(discord=_enabled_discord()))
    assert discord_oauth.capabilities() == {
        discord_oauth.START_PATH: permissions.PUBLIC,
        discord_oauth.CALLBACK_PATH: permissions.PUBLIC}
    assert registrar.capability_for(path=discord_oauth.START_PATH) == permissions.PUBLIC
    assert registrar.capability_for(path=discord_oauth.CALLBACK_PATH) == permissions.PUBLIC
    paths = {getattr(route, "path", None) for route in registrar.routes}
    assert {discord_oauth.START_PATH, discord_oauth.CALLBACK_PATH} <= paths
    assert permissions.undeclared_routes(app, registrar) == [], \
        "the shell's own surface must declare everything it serves"


@pytest.mark.asyncio
async def test_the_start_path_answers_an_anonymous_browser_with_a_redirect(config_dir,
                                                                          stub_discord):
    """PUBLIC means the GATE lets an anonymous caller through: the answer is Discord's authorize
    redirect, not the login door the refusal handler would have sent them to."""
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        response = await client.get(discord_oauth.START_PATH)
    assert response.status_code == 302
    assert response.headers["location"].startswith(discord_oauth.AUTHORIZE_URL)


@pytest.mark.asyncio
async def test_the_oauth_paths_refuse_politely_when_the_backend_is_disabled(config_dir):
    app, _ = _build(config_dir, _config())
    async with asgi_client(app) as client:
        start = await client.get(discord_oauth.START_PATH)
        callback = await client.get(f"{discord_oauth.CALLBACK_PATH}?code=x&state=y")
    assert (start.status_code, callback.status_code) == (404, 404)
    assert discord_oauth.DISABLED_MESSAGE in start.text
    assert "Traceback" not in start.text


@pytest.mark.asyncio
async def test_the_login_page_shows_the_discord_control_only_when_enabled(config_dir):
    enabled, _ = _build(config_dir, _config(discord=_enabled_discord()))
    disabled, _ = _build(config_dir, _config())
    async with asgi_client(enabled) as client:
        shown = await client.get(auth_routes.LOGIN_PATH)
    async with asgi_client(disabled) as client:
        hidden = await client.get(auth_routes.LOGIN_PATH)
    assert discord_oauth.CONTROL_LABEL in shown.text
    assert f'href="{discord_oauth.START_PATH}?next=/"' in shown.text
    assert discord_oauth.CONTROL_LABEL not in hidden.text
    assert discord_oauth.START_PATH not in hidden.text


@pytest.mark.asyncio
async def test_the_local_form_still_works_while_discord_is_enabled(config_dir):
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        page = await client.get(auth_routes.LOGIN_PATH)
        token = __import__("re").search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(auth_routes.LOGIN_PATH,
                                     data={"username": "specialk", "password": PASSWORD,
                                           "_csrf_token": token})
        assert response.status_code == 303, response.text
        assert response.headers["location"] == "/"


# ------------------------------------------------------------------- the configuration

def test_the_discord_backend_is_absent_unless_enabled(config_dir):
    assert discord_oauth.discord_backend(None, config_dir=config_dir,
                                         public_base_url=PUBLIC_BASE_URL) is None
    assert discord_oauth.discord_backend({"enabled": False, "client_id": CLIENT_ID,
                                          "client_secret": CLIENT_SECRET},
                                         config_dir=config_dir,
                                         public_base_url=PUBLIC_BASE_URL) is None


@pytest.mark.parametrize("block, expected", [
    ({"enabled": True, "client_secret": CLIENT_SECRET}, "client_id"),
    ({"enabled": True, "client_id": CLIENT_ID, "client_secret": "  "}, "client secret"),
])
def test_an_enabled_but_unusable_backend_is_refused_at_startup(config_dir, block, expected):
    """A half-configured backend is discovered at startup, not in the middle of somebody's login."""
    with pytest.raises(ValueError) as excinfo:
        discord_oauth.parse_discord_config(block, config_dir=config_dir,
                                           public_base_url=PUBLIC_BASE_URL)
    assert expected in str(excinfo.value)


@pytest.mark.parametrize("base", ["", None, "localhost:9876", "discord.com"])
def test_an_enabled_backend_needs_an_absolute_public_base_url(config_dir, base):
    with pytest.raises(ValueError) as excinfo:
        discord_oauth.parse_discord_config(_enabled_discord(), config_dir=config_dir,
                                           public_base_url=base)
    assert "public_base_url" in str(excinfo.value)


def test_the_named_secret_store_key_wins_over_the_inline_secret(config_dir):
    """Pinned precedence: a NAMED store key wins when it is set; the inline value is the fallback."""
    def reader(key, config_dir_arg):
        return "STORE-SECRET"

    settings = discord_oauth.parse_discord_config(
        _enabled_discord(client_secret_key="discord_secret"), config_dir=config_dir,
        public_base_url=PUBLIC_BASE_URL, secret_reader=reader)
    assert settings.client_secret == "STORE-SECRET"
    assert settings.client_id == CLIENT_ID
    assert settings.public_base_url == PUBLIC_BASE_URL


def test_the_inline_secret_is_used_when_no_store_key_is_named(config_dir):
    def reader(key, config_dir_arg):  # pragma: no cover - must NOT be called
        raise AssertionError("the store must not be consulted when no key is named")

    settings = discord_oauth.parse_discord_config(
        _enabled_discord(), config_dir=config_dir, public_base_url=PUBLIC_BASE_URL,
        secret_reader=reader)
    assert settings.client_secret == CLIENT_SECRET


def test_a_named_store_key_that_yields_nothing_is_refused_not_fallen_back(config_dir):
    """An operator who named a store key did not get it: refusing loudly beats silently using a
    different secret than the one they asked for."""
    with pytest.raises(ValueError) as excinfo:
        discord_oauth.parse_discord_config(
            _enabled_discord(client_secret_key="discord_secret"), config_dir=config_dir,
            public_base_url=PUBLIC_BASE_URL, secret_reader=lambda key, cfg: "")
    assert "discord_secret" in str(excinfo.value)


def test_the_secret_store_is_used_when_only_a_key_is_configured(config_dir):
    seen = {}

    def reader(key, config_dir_arg):
        seen["key"], seen["config_dir"] = key, config_dir_arg
        return "STORE-SECRET"

    settings = discord_oauth.parse_discord_config(
        {"enabled": True, "client_id": CLIENT_ID, "client_secret_key": "discord_secret"},
        config_dir=config_dir, public_base_url=PUBLIC_BASE_URL, secret_reader=reader)
    assert settings.client_secret == "STORE-SECRET"
    assert seen["key"] == "discord_secret"
    assert str(seen["config_dir"]) == str(config_dir)


def test_a_trailing_slash_on_the_base_url_does_not_double_up(config_dir):
    settings = discord_oauth.parse_discord_config(
        _enabled_discord(), config_dir=config_dir, public_base_url=f"{PUBLIC_BASE_URL}/")
    assert settings.public_base_url == PUBLIC_BASE_URL
    backend = discord_oauth.DiscordOAuthBackend(settings)
    assert backend.redirect_uri() == REDIRECT_URI


def _fake_core_os(monkeypatch, get_password):
    """Install a stand-in ``core.utils.os`` so the DEFAULT reader can run without the bot's core.

    The real ``core.utils.os`` imports ``psycopg``, which this suite's interpreter does not have —
    which is exactly why the import lives inside the function (pinned above). Faking the module here
    tests the translation and the ``config_dir`` passthrough, which is the part that is ours.
    """
    import sys
    module = types.ModuleType("core.utils.os")
    module.get_password = get_password
    monkeypatch.setitem(sys.modules, "core", types.ModuleType("core"))
    monkeypatch.setitem(sys.modules, "core.utils", types.ModuleType("core.utils"))
    monkeypatch.setitem(sys.modules, "core.utils.os", module)


def test_the_default_secret_reader_uses_the_bot_helper_with_the_given_config_dir(
        config_dir, monkeypatch):
    seen = {}

    def get_password(key, config_dir="config"):
        seen["key"], seen["config_dir"] = key, config_dir
        return "STORE-SECRET"

    _fake_core_os(monkeypatch, get_password)
    settings = discord_oauth.parse_discord_config(
        {"enabled": True, "client_id": CLIENT_ID, "client_secret_key": "discord_secret"},
        config_dir=config_dir, public_base_url=PUBLIC_BASE_URL)
    assert settings.client_secret == "STORE-SECRET"
    assert seen == {"key": "discord_secret", "config_dir": str(config_dir)}


def test_a_missing_store_key_is_a_loud_refusal(config_dir, monkeypatch):
    def get_password(key, config_dir="config"):
        raise ValueError(key)          # what `utils.get_password` raises when the pickle is absent

    _fake_core_os(monkeypatch, get_password)
    with pytest.raises(ValueError) as excinfo:
        discord_oauth.parse_discord_config(
            {"enabled": True, "client_id": CLIENT_ID, "client_secret_key": "discord_secret"},
            config_dir=config_dir, public_base_url=PUBLIC_BASE_URL)
    message = str(excinfo.value)
    assert "discord_secret" in message and "client_secret_key" in message

# ------------------------------------------------------- the role expansion, in isolation

def test_role_expansion_matches_by_role_name_and_by_role_id():
    member_obj = member(names=("Staff",), ids=(DISCORD_ROLE_ID,))
    assert discord_oauth.role_names_for_member(member_obj, ROLES_MAP) == {"Admin", "DCS Admin"}


def test_role_expansion_tolerates_a_missing_role_key():
    """`DummyBot.roles` has no `"DCS"` key at all; a missing key must grant nothing, never raise."""
    member_obj = member(names=("DCS",))
    assert discord_oauth.role_names_for_member(member_obj, {"Admin": ["Staff"]}) == set()
    assert discord_oauth.role_names_for_member(member_obj, None) == set()


def test_role_expansion_grants_nothing_for_an_absent_or_empty_grant_list():
    member_obj = member(names=("Admin",))
    assert discord_oauth.role_names_for_member(member_obj, {"Admin": []}) == set()
    assert discord_oauth.role_names_for_member(member_obj, {"Admin": None}) == set()


def test_role_expansion_does_not_read_a_boolean_as_a_role_id():
    member_obj = member(ids=(1,))
    assert discord_oauth.role_names_for_member(member_obj, {"Admin": [True]}) == set()


def test_role_expansion_grants_nothing_to_an_empty_member_roles_list():
    from types import SimpleNamespace
    assert discord_oauth.role_names_for_member(SimpleNamespace(roles=[]), ROLES_MAP) == set()


# ------------------------------------------------------- the schema and the sample config

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "services" / "webservice" / "auth" / "discord_oauth.py"
SCHEMA_PATH = REPO_ROOT / "services" / "webservice" / "schemas" / "webservice_schema.yaml"
SAMPLE_PATH = REPO_ROOT / "samples" / "services" / "webservice.yaml"


def _validate(text: str, tmp_path):
    """The same stand-in the local backend's test uses: the real `unique_port` extension lives in
    `core/utils/validators.py`, which imports the bot's `core` (psycopg) and cannot load here."""
    from pykwalify.core import Core
    extension = tmp_path / "ext.py"
    extension.write_text("def unique_port(value, rule_obj, path):\n    return True\n",
                         encoding="utf-8")
    source = tmp_path / "webservice.yaml"
    source.write_text(text, encoding="utf-8")
    Core(source_file=str(source), schema_files=[str(SCHEMA_PATH)],
         extensions=[str(extension)]).validate()


def test_the_sample_config_carries_a_discord_block_the_schema_accepts(tmp_path):
    _validate(SAMPLE_PATH.read_text(encoding="utf-8"), tmp_path)
    assert "discord:" in SAMPLE_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("body", [
    "  auth:\n    discord:\n      enabled: true\n      bogus_key: 1\n",
    "  auth:\n    discord:\n      enabled: maybe\n",
    "  auth:\n    discord: not-a-map\n",
])
def test_the_schema_refuses_a_discord_block_it_does_not_define(body, tmp_path):
    """Positive control for the sample pin: a key, a type and a shape the block does not define are
    rejected — otherwise "the sample validates" proves nothing."""
    from pykwalify.errors import SchemaError
    with pytest.raises(SchemaError):
        _validate(f"DEFAULT:\n  debug: false\n  listen: 0.0.0.0\n  port: 9876\n{body}", tmp_path)


# ------------------------------- member resolution WITHOUT the Server Members Intent
#
# The bug this section pins: with `privileged_intents: false` (config/services/bot.yaml, what the
# repo itself recommends above 10.000 members) discord.py caches NO guild members, so a cache-only
# lookup resolves EVERY user -- the guild owner included -- to None, and every login was refused
# with "is not a member of the bot's guild": true of nobody, and pointing the operator at the wrong
# problem. Resolution is now: the live cache, then the bot's own REST lookup (`Guild.fetch_member`,
# one member, no intent needed), then the user's own token
# (`GET /users/@me/guilds/<id>/member`, the `guilds.members.read` scope). The per-request path stays
# sync and cheap: the live cache, then a short-TTL in-memory resolution cache refreshed by a
# background REST fetch.

NO_INTENT = {"intents_members": False}


def _no_intent_bot(*, guilds=None, roles=None):
    return FakeBot(guilds=guilds if guilds is not None else [FakeGuild()],
                   roles=roles, **NO_INTENT)


def test_the_member_resolution_api_is_exported():
    """The seam the tests and the login rely on is part of the module's public surface."""
    for name in ("fetch_member_rest", "fetch_user_member", "members_intent_available",
                 "member_refusal_reason", "member_roles_snapshot", "resolve_login_member",
                 "MemberLookup", "DEFAULT_MEMBER_TTL", "MIN_MEMBER_TTL", "MAX_MEMBER_TTL",
                 "GUILD_MEMBER_URL", "NO_MEMBERS_INTENT_REASON", "LOOKUP_LABELS"):
        assert hasattr(discord_oauth, name), f"{name} is missing"
        assert name in discord_oauth.__all__, f"{name} is not exported"


def test_a_bot_that_cannot_report_its_intents_is_treated_as_unable_to_see_members():
    """A headless install (``DummyBot``) has no ``intents`` at all: it cannot be claimed to see
    members, so the truthful wording is the one used."""
    assert discord_oauth.members_intent_available(None) is False
    assert discord_oauth.members_intent_available(types.SimpleNamespace(guilds=[])) is False
    assert discord_oauth.members_intent_available(types.SimpleNamespace(
        intents=types.SimpleNamespace(members=True))) is True
    assert discord_oauth.members_intent_available(types.SimpleNamespace(
        intents=types.SimpleNamespace(members=False))) is False


@pytest.mark.asyncio
async def test_without_the_members_intent_the_login_resolves_the_member_through_the_bot_rest_lookup(
        config_dir, stub_discord, monkeypatch, rest_lookups, caplog):
    """Acceptance: intents.members False, an uncached member, and the login still succeeds -- the
    member is resolved over REST, and the log says which path resolved it."""
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: _no_intent_bot())
    rest_lookups.bot_result = member(names=("Staff",), ids=(DISCORD_ROLE_ID,))
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord()))
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            await _signed_in(client)
            payload = (await client.get("/probe/whoami")).json()
            assert (await client.get("/probe/admin")).status_code == 200
    assert payload["ref"] == {"backend": "discord", "subject": str(DISCORD_USER_ID)}
    assert payload["roles"] == ["Admin", "DCS Admin"]
    assert rest_lookups.bot_calls == [str(DISCORD_USER_ID)]
    assert rest_lookups.user_calls == [], "the user-side lookup is only the fallback"
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "via the bot REST lookup" in messages


@pytest.mark.asyncio
async def test_the_user_side_members_read_lookup_is_the_fallback_when_the_bot_cannot_read_members(
        config_dir, stub_discord, monkeypatch, rest_lookups, caplog):
    """When Discord refuses the bot's own single-member fetch, the login asks as the USER: the
    member comes back over `/users/@me/guilds/<id>/member` with the user's own token."""
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: _no_intent_bot())
    rest_lookups.bot_error = _Forbidden("the bot's REST lookup was refused")
    rest_lookups.user_result = member(names=(), ids=(DISCORD_ROLE_ID,))
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord()))
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            await _signed_in(client)
            payload = (await client.get("/probe/whoami")).json()
            assert (await client.get("/probe/admin")).status_code == 403, \
                "the role arrived (DCS Admin) but it does not grant probe.admin"
    assert payload["roles"] == ["DCS Admin"], \
        "the user-side lookup carries role IDs, expanded through the bot's role map"
    assert rest_lookups.bot_calls == [str(DISCORD_USER_ID)]
    assert rest_lookups.user_calls == [(ACCESS_TOKEN, (GUILD_ID,))]
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "via the user-side members.read lookup" in messages
    assert "the bot-side member lookup failed" in messages


@pytest.mark.asyncio
async def test_a_genuinely_unresolvable_user_without_the_intent_gets_the_truthful_refusal(
        config_dir, stub_discord, monkeypatch, rest_lookups, caplog):
    """Both lookups fail without answering the membership question: the refusal must name the
    INTENT/cache problem and the way out -- never "is not a member of the bot's guild", which is
    what sent the operator hunting for a membership problem. Fail closed, one generic page, and no
    traceback."""
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: _no_intent_bot())
    rest_lookups.bot_error = _Forbidden("the bot's REST lookup was refused")
    rest_lookups.user_error = discord_oauth.DiscordAuthError("Discord answered HTTP 403")
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            start = await _start(client)
            response = await _callback(client, _state(start))
    assert response.status_code == 400
    assert discord_oauth.GENERIC_MESSAGE in response.text
    assert "Traceback" not in response.text
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "cannot resolve guild members" in messages
    assert "Server Members Intent" in messages
    assert "privileged_intents" in messages, "the refusal must name the way out"
    assert "is not a member of the bot's guild" not in messages, \
        "the old wording accuses the wrong thing when the bot cannot see members"
    assert "not a member" not in response.text, "the reason is a log line, never the page"


@pytest.mark.asyncio
async def test_the_old_not_a_member_wording_is_reserved_for_a_bot_that_can_see_members(
        config_dir, stub_discord, monkeypatch, rest_lookups, caplog):
    """With the members intent ON the member cache is authoritative, so an empty cache IS the
    answer: the wording stays what it always was."""
    monkeypatch.setattr(discord_oauth, "current_bot",
                        lambda: FakeBot(guilds=[FakeGuild()], intents_members=True))
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            start = await _start(client)
            response = await _callback(client, _state(start))
    assert response.status_code == 400
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "is not a member of the bot's guild" in messages
    assert "cannot resolve guild members" not in messages
    assert rest_lookups.user_calls == [], "with the intent on the user-side lookup is not needed"


@pytest.mark.asyncio
async def test_a_404_from_the_user_side_lookup_is_still_not_reported_as_not_a_member(
        config_dir, stub_discord, monkeypatch, rest_lookups, caplog):
    """"Not a member" may only be claimed when the bot can actually see members: without the intent
    even a definitive 404 from the user-side endpoint is reported as the intent/cache problem, so
    the wording can never accuse the wrong thing."""
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: _no_intent_bot())
    rest_lookups.bot_result = None          # the bot's own lookup found nothing
    rest_lookups.user_result = None         # ... and so did the user's (404)
    app, _ = _build(config_dir, _config(discord=_enabled_discord()))
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            start = await _start(client)
            response = await _callback(client, _state(start))
    assert response.status_code == 400
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "cannot resolve guild members" in messages
    assert "is not a member of the bot's guild" not in messages
    assert rest_lookups.user_calls == [(ACCESS_TOKEN, (GUILD_ID,))]


@pytest.mark.asyncio
async def test_a_bot_side_lookup_that_finds_nobody_is_not_the_end_of_the_story(
        config_dir, stub_discord, monkeypatch, rest_lookups, caplog):
    """Without the intent a 404 from the BOT's lookup cannot be told apart from the intent gate, so
    the user-side lookup is asked before anything is concluded — and it decides the outcome."""
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: _no_intent_bot())
    rest_lookups.bot_result = None
    rest_lookups.user_result = member(names=(), ids=(DISCORD_ROLE_ID,))
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord()))
    async with asgi_client(app) as client:
        await _signed_in(client)
        assert (await client.get("/probe/whoami")).json()["roles"] == ["DCS Admin"]
        assert (await client.get("/probe/admin")).status_code == 403, \
            "the role arrived (DCS Admin) but it does not grant probe.admin"
    assert rest_lookups.user_calls == [(ACCESS_TOKEN, (GUILD_ID,))]


# ------------------------------------------- the per-request path: the short-TTL cache

@pytest.mark.asyncio
async def test_the_per_request_path_serves_the_login_resolution_within_the_ttl(
        config_dir, stub_discord, monkeypatch, rest_lookups, clock, caplog):
    """The per-request path is sync and cheap: the login seeds the TTL cache, and requests inside
    the TTL are answered from memory with no Discord call at all."""
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: _no_intent_bot())
    rest_lookups.bot_result = member(names=("Staff",), ids=(DISCORD_ROLE_ID,))
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord(member_ttl=60)))
    backend = _backend_of(app)
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            await _signed_in(client)
            clock[0] += 59                       # still inside the TTL
            assert (await client.get("/probe/admin")).status_code == 200
            assert (await client.get("/probe/whoami")).json()["roles"] == ["Admin", "DCS Admin"]
    assert rest_lookups.bot_calls == [str(DISCORD_USER_ID)], \
        "within the TTL the per-request path must not call Discord again"
    await _settle(backend)
    assert rest_lookups.bot_calls == [str(DISCORD_USER_ID)]


@pytest.mark.asyncio
async def test_a_role_change_becomes_effective_within_the_ttl(
        config_dir, stub_discord, monkeypatch, rest_lookups, clock):
    """The invariant: a member who loses a role loses the capability within the TTL -- not at cookie
    expiry. A value older than the TTL is still SERVED (a request must not fail just because the
    entry aged out) while exactly one background refresh runs and replaces it."""
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: _no_intent_bot())
    rest_lookups.bot_result = member(names=("Staff",), ids=(DISCORD_ROLE_ID,))
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord(member_ttl=60)))
    backend = _backend_of(app)
    async with asgi_client(app) as client:
        await _signed_in(client)
        assert (await client.get("/probe/admin")).status_code == 200
        rest_lookups.bot_result = member(names=("Nobody",))
        clock[0] += 61
        assert (await client.get("/probe/admin")).status_code == 200, \
            "the stale entry is served while the refresh runs"
        await _settle(backend)
        assert (await client.get("/probe/whoami")).json()["roles"] == []
        assert (await client.get("/probe/admin")).status_code == 403
    assert len(rest_lookups.bot_calls) == 2, "exactly one background refresh"


@pytest.mark.asyncio
async def test_an_unanswerable_lookup_keeps_the_session_and_does_not_cache_a_negative(
        config_dir, stub_discord, monkeypatch, rest_lookups, clock):
    """RE-POINTED Without the Server Members Intent a "nobody" answer cannot be
    told apart from the intent gate, so it is INDECISIVE, not a membership fact: the remembered
    resolution is KEPT, the request is answered with a RETRYABLE 503, and nothing is cached as
    "removed". The DEFINITIVE negative (the bot's member list answering "nobody") is pinned in
    ``tests/test_webui_member_transient.py``, where the three outcomes are the subject.
    """
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: _no_intent_bot())
    rest_lookups.bot_result = member(names=("Staff",))
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord(member_ttl=60)))
    backend = _backend_of(app)
    subject = str(DISCORD_USER_ID)
    async with asgi_client(app) as client:
        await _signed_in(client)
        assert (await client.get("/probe/admin")).status_code == 200
        rest_lookups.bot_result = None           # the lookup answered "nobody"
        clock[0] += 61
        assert (await client.get("/probe/admin")).status_code == 200, \
            "the stale entry is served once while the refresh runs"
        await _settle(backend)
        assert backend._resolution.get(subject) is not None, \
            "an unanswered question must not drop the remembered resolution"
        assert backend._unverifiable.get(subject) is not None, "the retryable window must be open"
        transient = await client.get("/probe/admin")
        assert transient.status_code == 503, transient.text
        assert transient.headers.get("retry-after")
    assert len(rest_lookups.bot_calls) == 2


@pytest.mark.asyncio
async def test_a_failing_refresh_is_retryable_and_does_not_hammer_discord(
        config_dir, stub_discord, monkeypatch, rest_lookups, clock, caplog):
    """RE-POINTED A REST failure must not become a 500 and must not become an
    open door: the session is KEPT, the request is answered with a RETRYABLE 503 (never a 401/403,
    which the client would stop on), NO data is served, and the refresh is retried on the backoff —
    never once per request."""
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: _no_intent_bot())
    rest_lookups.bot_result = member(names=("Staff",))
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord(member_ttl=60)))
    backend = _backend_of(app)
    with caplog.at_level(logging.WARNING):
        async with asgi_client(app) as client:
            await _signed_in(client)
            rest_lookups.bot_error = _Forbidden("Discord is unreachable")
            clock[0] += 61
            assert (await client.get("/probe/admin")).status_code == 200, \
                "the stale entry is served once while the re-check runs"
            await _settle(backend)
            assert len(rest_lookups.bot_calls) == 2
            clock[0] += 5                        # past the first backoff step
            transient = await client.get("/probe/admin")
            assert transient.status_code == 503, transient.text
            assert transient.headers.get("retry-after")
            assert "ok" not in transient.text, "no data may be served to an unverifiable identity"
            await _settle(backend)
            assert len(rest_lookups.bot_calls) == 3, \
                "the failed refresh is retried on the backoff, once"
            clock[0] += 1                        # inside the NEXT backoff step
            assert (await client.get("/probe/admin")).status_code == 503
            await _settle(backend)
            assert len(rest_lookups.bot_calls) == 3, \
                "a failing refresh must not be retried on every request"
 # the CONDITION line is the one an operator reads; the per-attempt lookup lines are paced by the
 # backoff (there were three attempts) and are not the request-path flood this card removes
    conditions = [record for record in caplog.records
                  if record.name == "services.webservice.auth.discord_oauth"
                  and "verify a session" in record.getMessage()]
    assert len(conditions) == 1, [record.getMessage() for record in conditions]
    assert "cannot resolve guild members" not in conditions[0].getMessage()
    assert str(DISCORD_USER_ID) not in conditions[0].getMessage()


@pytest.mark.asyncio
async def test_with_the_members_intent_the_live_cache_is_authoritative_and_no_rest_lookup_runs(
        config_dir, stub_discord, monkeypatch, rest_lookups, clock):
    """The other direction of the same switch: with the intent ON nothing is cached and no TTL
    applies -- a role change is effective on the very next request."""
    holder = types.SimpleNamespace(bot=bot_with(member(names=("Staff",))))
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: holder.bot)
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord(member_ttl=60)))
    async with asgi_client(app) as client:
        await _signed_in(client)
        assert (await client.get("/probe/admin")).status_code == 200
        holder.bot = bot_with(member(names=("Nobody",)))
        clock[0] += 10_000                       # the TTL is irrelevant on this path
        assert (await client.get("/probe/admin")).status_code == 403
        assert (await client.get("/probe/whoami")).json()["roles"] == []
    assert rest_lookups.bot_calls == [], "the live cache answered; no REST call was needed"


@pytest.mark.asyncio
async def test_the_per_request_path_without_a_cache_entry_refuses_and_schedules_one_refresh(
        config_dir, stub_discord, monkeypatch, rest_lookups, clock, caplog):
    """A cookie whose resolution is gone (a webservice restart, or an entry the refresh dropped):
    the request is refused -- fail closed -- and the next one is served from the refresh."""
    holder = types.SimpleNamespace(bot=_no_intent_bot())
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: holder.bot)
    rest_lookups.bot_result = member(names=("Staff",))
    app = _app_with_probe(config_dir, _config(discord=_enabled_discord(member_ttl=60)))
    backend = _backend_of(app)
    async with asgi_client(app) as client:
        await _signed_in(client)
        backend._resolution.clear()              # as if the process had just restarted
        with caplog.at_level(logging.WARNING):
            transient = await client.get("/probe/admin")
        assert transient.status_code == 503, \
            "an unavailable lookup is RETRYABLE, never the 401/403 the client stops on"
        assert transient.headers.get("retry-after")
        await _settle(backend)
        assert (await client.get("/probe/admin")).status_code == 200
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "cannot resolve a session" in messages or "cannot verify a session" in messages, messages


# --------------------------------------------------------------- the user-side REST lookup

class _StubResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload
 # ``_request`` reads the body through the capped ``.content.read(n)`` seam, so the stub has
 # to expose it the way a real ``aiohttp`` response does
        self.content = _StubBody(json.dumps(payload).encode("utf-8"))

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _StubSession:
    """An ``aiohttp.ClientSession`` that answers from class state -- no network."""

    status = 200
    payload: dict | None = {}
    seen: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def request(self, method, url, **kwargs):
        type(self).seen.append((method, url, kwargs.get("headers") or {}))
        return _StubResponse(type(self).status, type(self).payload)


@pytest.mark.asyncio
async def test_the_user_side_lookup_reads_404_as_not_a_member_and_keeps_the_token_out_of_the_url(
        monkeypatch, rest_lookups):
    fetch_user_member = rest_lookups.real_fetch_user_member
    _StubSession.seen = []
    _StubSession.status = 404
    _StubSession.payload = None
    monkeypatch.setattr(discord_oauth.aiohttp, "ClientSession", _StubSession)

    assert await fetch_user_member(ACCESS_TOKEN, [GUILD_ID]) is None
    assert await fetch_user_member(ACCESS_TOKEN, [GUILD_ID, 999]) is None
    method, url, headers = _StubSession.seen[0]
    assert method == "GET"
    assert url == f"https://discord.com/api/users/@me/guilds/{GUILD_ID}/member"
    assert headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"
    assert ACCESS_TOKEN not in url, "a token must never travel in a URL"
    assert _StubSession.seen[-1][1].endswith("/guilds/999/member"), \
        "every guild the bot is in is asked, in order"

    _StubSession.status = 403
    with pytest.raises(discord_oauth.DiscordAuthError) as excinfo:
        await fetch_user_member(ACCESS_TOKEN, [GUILD_ID])
    assert "403" in str(excinfo.value)
    assert ACCESS_TOKEN not in str(excinfo.value)

    _StubSession.status = 200
    _StubSession.payload = {"roles": [str(DISCORD_ROLE_ID), "1"]}
    found = await fetch_user_member(ACCESS_TOKEN, [GUILD_ID])
    assert discord_oauth.role_names_for_member(found, ROLES_MAP) == {"DCS Admin"}


class _NotFound(Exception):
    """``discord.NotFound``: this guild does not have that member."""

    status = 404


class _Guild:
    """A guild whose single-member fetch answers ``result`` (a member, or an exception)."""

    def __init__(self, result, guild_id=1):
        self.id = guild_id
        self._result = result

    async def fetch_member(self, user_id):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.mark.asyncio
async def test_the_bot_side_lookup_asks_each_guild_and_raises_anything_that_is_not_not_found(
        rest_lookups):
    """The single-member REST lookup: one guild at a time, a NotFound moves on, and every other
    failure is raised (so it can never be misread as "not a member")."""
    fetch = rest_lookups.real_fetch_member_rest
    subject = str(DISCORD_USER_ID)
    found = member(names=("Staff",))
    bot = types.SimpleNamespace(guilds=[_Guild(_NotFound()), _Guild(found, guild_id=2)])
    assert await fetch(bot, subject) is found

    bot = types.SimpleNamespace(guilds=[_Guild(_NotFound()), _Guild(_NotFound())])
    assert await fetch(bot, subject) is None

    bot = types.SimpleNamespace(guilds=[_Guild(_Forbidden("nope"))])
    with pytest.raises(_Forbidden):
        await fetch(bot, subject)

    assert await fetch(types.SimpleNamespace(guilds=[]), subject) is None
    assert await fetch(bot, "") is None


def test_a_bot_rest_member_is_snapshotted_so_the_ttl_cache_is_a_value_not_a_live_object():
    live = member(names=("Staff",), ids=(DISCORD_ROLE_ID,))
    snapshot = discord_oauth.member_roles_snapshot(live)
    assert snapshot is not live
    assert discord_oauth.role_names_for_member(snapshot, ROLES_MAP) == {"Admin", "DCS Admin"}
    live.roles.clear()
    assert discord_oauth.role_names_for_member(snapshot, ROLES_MAP) == {"Admin", "DCS Admin"}, \
        "the snapshot must not follow the live member"


@pytest.mark.parametrize("given, expected", [
    (None, 60),
    (0, 1),
    (-5, 1),
    (100000, 3600),
    ("45", 45),
    ("abc", 60),
    (True, 60),
])
def test_the_member_ttl_is_validated_and_clamped(config_dir, given, expected):
    block = _enabled_discord()
    if given is not None:
        block["member_ttl"] = given
    settings = discord_oauth.parse_discord_config(block, config_dir=config_dir,
                                                  public_base_url=PUBLIC_BASE_URL)
    assert settings.member_ttl == expected


@pytest.mark.parametrize("given", [0, -5, 100000, "abc", True])
def test_an_absurd_member_ttl_is_reported_rather_than_silently_used(config_dir, given, caplog):
    block = _enabled_discord(member_ttl=given)
    with caplog.at_level(logging.WARNING):
        discord_oauth.parse_discord_config(block, config_dir=config_dir,
                                           public_base_url=PUBLIC_BASE_URL)
    assert "member_ttl" in " ".join(record.getMessage() for record in caplog.records)


def test_the_member_ttl_key_is_in_the_schema_the_sample_and_the_readme():
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    sample = SAMPLE_PATH.read_text(encoding="utf-8")
    readme = (REPO_ROOT / "services" / "webservice" / "README.md").read_text(encoding="utf-8")
    assert "member_ttl" in schema
    assert "member_ttl" in sample
    assert "member_ttl" in readme
    assert "identify only" not in readme, \
        "the README must not still promise the identify scope only"


def test_the_schema_refuses_a_member_ttl_that_is_not_an_integer(tmp_path):
    from pykwalify.errors import SchemaError
    body = "  auth:\n    discord:\n      enabled: true\n      member_ttl: sixty\n"
    with pytest.raises(SchemaError):
        _validate(f"DEFAULT:\n  debug: false\n  listen: 0.0.0.0\n  port: 9876\n{body}", tmp_path)
