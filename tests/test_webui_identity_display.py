"""The sidebar chip shows the user's NAME and AVATAR, never the Discord id.

The Discord backend already resolves the guild member on every request for the ROLES, and that member
carries the display name and the avatar, so nothing here needs a new Discord call or a new stored
value. The numeric id is nowhere in the rendered page; the avatar is requested at the small (64px)
size; a local user shows username, derived initials and no ``img``; a display name and an avatar URL
are escaped (the URL is only ever an absolute http(s) one); the identity reference stays
``{backend, subject}`` -- the name is re-resolved per request, never stored. No request touches
Discord, a database or the network."""
from __future__ import annotations

import base64
import re
import types
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import asgi_client, create_app, install_shell
from services.webservice import readmodels
from services.webservice.auth import Identity
from services.webservice.auth import discord_oauth
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import dashboard as dashboard_page

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"

CLIENT_ID = "123456789012345678"
CLIENT_SECRET = "CLIENT-SECRET-MUST-NOT-LEAK"
AUTH_CODE = "AUTH-CODE-MUST-NOT-LEAK"
ACCESS_TOKEN = "ACCESS-TOKEN-MUST-NOT-LEAK"
PUBLIC_BASE_URL = "http://localhost:9876"
DISCORD_USER_ID = 987654321098765432
GUILD_ID = 424242424242424242
DISCORD_ROLE_ID = 555555555555555555

#: `bot.yaml -> roles:` has the DISCORD meaning here: a bot role NAME -> the Discord roles that
#: grant it. The member holds the role named `Staff`, so it signs in as an Admin.
ROLES_MAP = {"Admin": ["Staff"], "DCS Admin": [DISCORD_ROLE_ID], "DCS": ["DCS"]}
BOT_YAML = "roles:\n  Admin:\n    - Staff\n"

DISPLAY_NAME = "Special K"
#: deliberately WITHOUT the numeric id in the path: the id must not appear on the page at all, and
#: an avatar URL that embeds it would make an "id is not rendered" assertion fail for the wrong
#: reason (a Discord URL does carry the id — that is the CDN's own path, not a label).
AVATAR_BASE = "https://cdn.discordapp.com/avatars/1234567890/a_hash.png"


# --------------------------------------------------------------------------- test doubles

class StubAsset:
    """``discord.Asset`` where it matters: ``with_size(n)`` returns a sized asset whose ``url``
    carries the requested size — that is how "a small size was asked for" becomes assertable."""

    def __init__(self, url: str, size: int | None = None):
        self._url = url
        self._size = size

    def with_size(self, size: int) -> "StubAsset":
        return StubAsset(self._url, size)

    @property
    def url(self) -> str:
        if self._size is not None:
            joiner = "&" if "?" in self._url else "?"
            return f"{self._url}{joiner}size={self._size}"
        return self._url


class StubRole:
    def __init__(self, name: str = "", role_id: int = 0):
        self.name = name
        self.id = role_id


class StubMember:
    """A guild member as the backend reads it: roles plus the presentation fields."""

    def __init__(self, *, display_name: str = "", role_names=("Staff",), role_ids=(),
                 avatar=None, username: str = "specialk"):
        self.display_name = display_name
        self.name = username
        self.username = username
        self.roles = [StubRole(name=name) for name in role_names] + [StubRole("", i)
                                                                    for i in role_ids]
        self._avatar = avatar

    @property
    def display_avatar(self):
        return self._avatar


class StubGuild:
    def __init__(self, members=None, guild_id=GUILD_ID):
        self._members = dict(members or {})
        self.id = guild_id

    def get_member(self, user_id):
        return self._members.get(user_id)

    async def fetch_member(self, user_id):  # pragma: no cover - the REST lookup is stubbed
        raise AssertionError("a test reached Guild.fetch_member - stub fetch_member_rest")


class StubBot:
    def __init__(self, *, members=None, intents_members: bool = True):
        self.guilds = [StubGuild(members)]
        self.roles = dict(ROLES_MAP)
        self.intents = types.SimpleNamespace(members=intents_members)

    def __getattr__(self, name):
        raise AssertionError(f"the OAuth backend touched bot.{name} - not part of the role model")


def member(**kwargs) -> StubMember:
    return StubMember(**kwargs)


def cached_bot(member_obj, *, intents_members: bool = True) -> StubBot:
    return StubBot(members={DISCORD_USER_ID: member_obj}, intents_members=intents_members)


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


def _config(*, discord: bool = False, local_users=None) -> dict:
    auth = {
        "public_base_url": PUBLIC_BASE_URL,
        "local": {"enabled": True, "users": local_users if local_users is not None else [
            {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]}]},
    }
    if discord:
        auth["discord"] = {"enabled": True, "client_id": CLIENT_ID,
                           "client_secret": CLIENT_SECRET}
    return {"session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
            "auth": auth}


def _build(config_dir, config=None):
    app = create_app()
    registrar = install_shell(app, node=_node(config_dir), config=config or _config())
    app.state.webui_source_provider = lambda: readmodels.EmptySource()
    return app, registrar


@pytest.fixture
def stub_discord(monkeypatch):
    """Discord's HTTP layer, stubbed: no test here may reach the network."""

    async def exchange_code(client_id, client_secret, code, redirect_uri):
        return {"access_token": ACCESS_TOKEN}

    async def fetch_user(access_token):
        return {"id": str(DISCORD_USER_ID), "username": "specialk"}

    monkeypatch.setattr(discord_oauth, "exchange_code", exchange_code)
    monkeypatch.setattr(discord_oauth, "fetch_user", fetch_user)


@pytest.fixture
def lookups(monkeypatch):
    """Both member lookups, stubbed and COUNTED (they are what a second Discord call would be)."""

    class Counted:
        def __init__(self):
            self.bot_calls: list = []
            self.user_calls: list = []
            self.bot_result = None

        async def fetch_member_rest(self, bot, subject):
            self.bot_calls.append(subject)
            return self.bot_result

        async def fetch_user_member(self, access_token, guild_ids):
            self.user_calls.append((access_token, tuple(guild_ids)))
            return None

    counted = Counted()
    monkeypatch.setattr(discord_oauth, "fetch_member_rest", counted.fetch_member_rest)
    monkeypatch.setattr(discord_oauth, "fetch_user_member", counted.fetch_user_member)
    return counted


@pytest.fixture
def bot_holder(monkeypatch):
    """``current_bot`` behind a mutable holder, so a test can swap the bot mid-flow."""

    holder = types.SimpleNamespace(bot=None)
    monkeypatch.setattr(discord_oauth, "current_bot", lambda: holder.bot)
    return holder


# --------------------------------------------------------------------------- helpers

async def _discord_login(client):
    start = await client.get(f"{discord_oauth.START_PATH}?next=/")
    assert start.status_code == 302, start.text
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    return await client.get(f"{discord_oauth.CALLBACK_PATH}?code={AUTH_CODE}&state={state}")


async def _local_login(client, username: str = "specialk", password: str = PASSWORD):
    page = await client.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
    return await client.post(auth_routes.LOGIN_PATH,
                             data={"username": username, "password": password,
                                   "_csrf_token": token})


def _who_html(html: str) -> str:
    """The user chip, sliced out of the page (its own nested divs make a regex fragile).

    The chip lives in the TOP BAR (it moved out of the sidebar), so the slice ends at the top bar's
    closing tag rather than at ``</aside>``.
    """
    start = html.index('<div class="who">')
    return html[start:html.index("</header>", start)]


def _no_who(html: str) -> bool:
    return '<div class="who">' not in html


def _cookie_payload(cookie_value: str) -> str:
    """The signed session cookie's JSON payload (``<b64>.<ts>.<sig>``)."""
    segment = cookie_value.split(".")[0]
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)).decode("utf-8")


async def _set_cookies(response) -> str:
    return " ".join(response.headers.get_list("set-cookie"))


# --------------------------------------------------------------------------- the display name

@pytest.mark.asyncio
async def test_a_discord_member_is_shown_by_display_name_and_avatar(config_dir, stub_discord,
                                                                   bot_holder, lookups):
    """The chip carries the member's display name and avatar; the numeric id is not the label."""
    bot_holder.bot = cached_bot(member(display_name=DISPLAY_NAME,
                                       avatar=StubAsset(AVATAR_BASE)))
    app, _ = _build(config_dir, _config(discord=True))

    async with asgi_client(app) as client:
        assert (await _discord_login(client)).status_code == 303
        response = await client.get(dashboard_page.DASHBOARD_PATH)
        assert response.status_code == 200
        who = _who_html(response.text)
        assert f"<b>{DISPLAY_NAME}</b>" in who, who
        assert f'src="{AVATAR_BASE}?size=64"' in who, who
        assert str(DISCORD_USER_ID) not in response.text, \
            "the numeric id must not be the visible name any more"
        assert str(DISCORD_USER_ID) not in who


@pytest.mark.asyncio
async def test_the_avatar_is_requested_at_the_small_size(config_dir, stub_discord, bot_holder,
                                                         lookups):
    """Discord substitutes a default avatar, and the CDN is asked for the 64px rendition."""
    bot_holder.bot = cached_bot(member(display_name=DISPLAY_NAME,
                                       avatar=StubAsset(AVATAR_BASE)))
    app, _ = _build(config_dir, _config(discord=True))

    async with asgi_client(app) as client:
        await _discord_login(client)
        who = _who_html((await client.get("/")).text)
    assert "?size=64" in who, who
    assert f'alt="{DISPLAY_NAME}"' in who, "alt carries the display name"
    assert 'referrerpolicy="no-referrer"' in who, \
        "the browser fetches the image straight from Discord's CDN, so no referrer is sent"


@pytest.mark.asyncio
async def test_a_local_user_is_shown_by_username_with_initials_and_no_avatar(config_dir):
    """The password backends have a username and no avatar: username + derived initials."""
    app, _ = _build(config_dir)

    async with asgi_client(app) as client:
        assert (await _local_login(client)).status_code == 303
        response = await client.get(dashboard_page.DASHBOARD_PATH)
        assert response.status_code == 200
        who = _who_html(response.text)
        assert "<b>specialk</b>" in who, who
        assert '<div class="av">SP</div>' in who, who
        assert "<img" not in who, "no avatar source exists on this backend"


def test_an_identity_without_a_display_name_never_shows_the_id():
    """``identity_card``: initials come from the display name, and an empty one is never a label."""
    card = dashboard_page.identity_card(Identity(
        subject=str(DISCORD_USER_ID), backend="discord", roles=frozenset({"Admin"}),
        display_name=DISPLAY_NAME, avatar_url=f"{AVATAR_BASE}?size=64"))
    assert card["name"] == DISPLAY_NAME
    assert card["initials"] == "SK"
    assert card["avatar"] == f"{AVATAR_BASE}?size=64"
    assert card["subtitle"] == "discord · Admin"

    blank = dashboard_page.identity_card(Identity(
        subject=str(DISCORD_USER_ID), backend="discord", roles=frozenset()))
    assert blank["name"], "never an empty label on a resolved identity"
    assert blank["name"] != str(DISCORD_USER_ID), "never the id"
    assert blank["initials"], "initials are derived from the shown name"
    assert blank["avatar"] == ""


# --------------------------------------------------------------------------- escaping

@pytest.mark.asyncio
@pytest.mark.parametrize("with_avatar", [False, True], ids=["initials-chip", "avatar"])
async def test_a_display_name_with_html_is_escaped(config_dir, stub_discord, bot_holder, lookups,
                                                   with_avatar):
    """The display name is user-controlled text: escaped in the chip AND in the alt attribute."""
    payload = "<script>alert(1)</script>"
    escaped = "&lt;script&gt;alert(1)&lt;/script&gt;"
    bot_holder.bot = cached_bot(member(display_name=payload,
                                       avatar=StubAsset(AVATAR_BASE) if with_avatar else None))
    app, _ = _build(config_dir, _config(discord=True))

    async with asgi_client(app) as client:
        await _discord_login(client)
        response = await client.get(dashboard_page.DASHBOARD_PATH)
    assert response.status_code == 200
    assert payload not in response.text, "the display name must never render as markup"
    who = _who_html(response.text)
    assert f"<b>{escaped}</b>" in who, who
    if with_avatar:
        assert f'alt="{escaped}"' in who, who
    else:
        assert '<div class="av">&lt;S</div>' in who, who


# --------------------------------------------------------------------------- avatar safety

@pytest.mark.parametrize("hostile", [
    "javascript:alert(1)",
    "data:image/png;base64,AAAA",
    "/static/avatar.png",
    "//cdn.discordapp.com/avatars/1/x.png",
    "ftp://cdn.discordapp.com/avatar.png",
    "  ",
])
def test_an_avatar_url_that_is_not_http_is_refused(hostile):
    """Only an ABSOLUTE http(s) URL may reach the ``img``; anything else falls back to initials."""
    assert dashboard_page.identity_card(Identity(
        subject="1", backend="discord", roles=frozenset(), display_name="Special K",
        avatar_url=hostile))["avatar"] == ""


@pytest.mark.asyncio
async def test_an_avatar_url_cannot_break_out_of_the_attribute(config_dir, stub_discord,
                                                               bot_holder, lookups):
    """A hostile URL that passes the scheme check still cannot add an attribute of its own.

    (No whitespace in it — a URL carrying whitespace is refused outright and covered by the
    parametrized case above; this one is the URL that DOES pass and must be escaped.)
    """
    hostile = 'https://cdn.example/a.png"onerror="alert(1)'
    bot_holder.bot = cached_bot(member(display_name="Special K", avatar=StubAsset(hostile)))
    app, _ = _build(config_dir, _config(discord=True))

    async with asgi_client(app) as client:
        await _discord_login(client)
        who = _who_html((await client.get("/")).text)
    assert 'onerror="alert(1)"' not in who, who
    assert "&#34;onerror=&#34;alert(1)" in who, who


# --------------------------------------------------------------------------- the session cookie

@pytest.mark.asyncio
async def test_the_session_cookie_carries_no_display_name(config_dir, stub_discord, bot_holder,
                                                          lookups):
    """The identity reference stays ``{backend, subject}``; the name is resolved per request."""
    name = "Zaphod-Beeblebrox"
    bot_holder.bot = cached_bot(member(display_name=name))
    app, _ = _build(config_dir, _config(discord=True))

    async with asgi_client(app) as client:
        await client.get(f"{discord_oauth.START_PATH}?next=/")
        callback = await _discord_login(client)
        cookies = await _set_cookies(callback)
    assert name not in cookies, "the display name must not travel in the cookie"
    payload = _cookie_payload(next(value for value in cookies.split()
                                   if value.startswith(f"{COOKIE_NAME}="))
                              .split(";", 1)[0].split("=", 1)[1])
    assert name not in payload
    assert str(DISCORD_USER_ID) in payload, "the subject IS what the cookie stores"


# --------------------------------------------------------------------------- no extra call

@pytest.mark.asyncio
async def test_no_extra_discord_lookup_is_made_for_the_name_and_avatar(config_dir, stub_discord,
                                                                       bot_holder, lookups):
    """The live member cache answers both the roles and the presentation: no REST lookup at all."""
    bot_holder.bot = cached_bot(member(display_name=DISPLAY_NAME,
                                       avatar=StubAsset(AVATAR_BASE)))
    app, _ = _build(config_dir, _config(discord=True))

    async with asgi_client(app) as client:
        await _discord_login(client)
        response = await client.get(dashboard_page.DASHBOARD_PATH)
    assert response.status_code == 200
    assert DISPLAY_NAME in response.text
    assert lookups.bot_calls == [] and lookups.user_calls == [], \
        "the name and avatar come from the member already resolved for the roles"


@pytest.mark.asyncio
async def test_the_name_and_avatar_survive_the_member_snapshot(config_dir, stub_discord,
                                                               bot_holder, lookups):
    """With no member list (intents off) the short-TTL snapshot must carry them too.

    One REST lookup for the whole login plus first page; the render resolves from the snapshot.
    """
    lookups.bot_result = member(display_name=DISPLAY_NAME, avatar=StubAsset(AVATAR_BASE))
    bot_holder.bot = StubBot(members={}, intents_members=False)
    app, _ = _build(config_dir, _config(discord=True))

    async with asgi_client(app) as client:
        assert (await _discord_login(client)).status_code == 303
        response = await client.get(dashboard_page.DASHBOARD_PATH)
    assert response.status_code == 200
    who = _who_html(response.text)
    assert f"<b>{DISPLAY_NAME}</b>" in who, who
    assert f'src="{AVATAR_BASE}?size=64"' in who, who
    assert len(lookups.bot_calls) == 1, lookups.bot_calls
    assert lookups.user_calls == [], lookups.user_calls


# --------------------------------------------------------------------------- nothing to show

@pytest.mark.asyncio
async def test_an_unresolved_member_falls_back_cleanly(config_dir, stub_discord, bot_holder,
                                                       lookups):
    """A bot that can see members but does not hold this one: refused, no exception, no chip."""
    bot_holder.bot = cached_bot(member(display_name=DISPLAY_NAME))
    app, _ = _build(config_dir, _config(discord=True))

    async with asgi_client(app) as client:
        assert (await _discord_login(client)).status_code == 303
        bot_holder.bot = StubBot(members={}, intents_members=True)
        response = await client.get(dashboard_page.DASHBOARD_PATH)
        login_page = await client.get(auth_routes.LOGIN_PATH)
    assert response.status_code == 403, "an unresolved member is not authorised"
    assert _no_who(response.text), "no half-filled chip"
    assert _no_who(login_page.text)
