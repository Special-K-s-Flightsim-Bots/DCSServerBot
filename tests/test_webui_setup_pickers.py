"""The Discord pickers, the religion of the guild cache, and the Web console step.

These are the re-cut's own behaviours, exercised over the shipped application:

* the guild READ (``setup.discord.fetch_guild_data``) is a short-lived HTTP-only client keyed on
  ``(token, guild_id)`` — the suite stubs ``_read_guild`` so no test touches discord.com, but it
  drives the REAL cache/status logic and the REAL render;
* the PICKERS are real ``<select>`` elements, served in the HTML (so they work with JavaScript off),
  and they DEGRADE to the installer's typed fields — with the honest sentence — when the read failed;
* the Web console step writes ``auth.discord`` only when a secret is held, writes the break-glass
  account ALWAYS while the console is on, and writes the minimal ``frontend: false`` document when it
  is off.
"""

from __future__ import annotations

import re
import types

import discord
import pytest
from ruamel.yaml import YAML

from conftest import asgi_client
from services.webservice.setup import console as console_step
from services.webservice.setup import discord as discord_step
from services.webservice.setup import node as node_step
from services.webservice.setup import review as review_step
from services.webservice.setup import servers as servers_step
from services.webservice.setup import tour
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import SetupDraft, draft_path
from services.webservice.setup.tour import STEP_PATH
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')

TOKEN = "MTIzNDU2Nzg5.guild-cache-token.AAAA"        # application id 123456789
OTHER_TOKEN = "OTg3NjU0MzIx.another-token.BBBB"      # application id 987654321
GUILD = 111111111111111111
OTHER_GUILD = 222222222222222222
NODE_ANSWERED = {"listen_port": 10042, "no_dcs": False, "dcs_installation": "/opt/dcs"}

GUILD_DATA = discord_step.GuildData(
    guild_id=GUILD, guild_name="Test Guild",
    channels=(discord_step.ChannelOption(id=555, name="bot-status", category="BOT"),
              discord_step.ChannelOption(id=666, name="general", category="")),
    roles=("Admin", "DCS Admin", "Pilot"),
    guilds=((GUILD, "Test Guild"),))


def build(config_dir, *, code: str = CODE):
    window = SetupWindow(code=code, wrong_delay=0.0)
    return create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN), window


async def enter(client, code: str = CODE):
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the step form carries no CSRF token"
    return found.group(1)


def seed(config_dir, *, step: int, discord: bool = True, **blocks) -> SetupDraft:
    draft = SetupDraft(step=step, discord=discord)
    draft.node = dict(NODE_ANSWERED)
    draft.servers = servers_step.non_secret_document(servers_step.answers_from_mapping(
        {"scanned": True}, detected={"DCS.serverrelease": "SRS-1"}))
    for name, value in blocks.items():
        setattr(draft, name, value)
    draft.save(config_dir)
    return draft


def plant_guild(app, data=GUILD_DATA, token: str = TOKEN, guild_id: int = GUILD) -> None:
    """Put a guild read into the process cache under exactly the key the steps derive."""
    discord_step.guild_cache(app)[(token, guild_id)] = data
    discord_step.guild_status(app).update({"key": (token, guild_id), "code": ""})


def load(path) -> dict:
    return YAML(typ="safe").load(open(path, encoding="utf-8"))


def _state_bag():
    """An object with the ``.state`` the process caches hang off (no real application)."""
    return types.SimpleNamespace(state=types.SimpleNamespace())


# ------------------------------------------------------------------ the fetch: the cache is keyed

@pytest.mark.asyncio
async def test_fetch_guild_data_caches_on_token_and_guild_and_never_refetches_the_same_key(monkeypatch):
    calls: list = []

    async def fake(token, guild_id):
        calls.append((token, guild_id))
        return GUILD_DATA, ""

    monkeypatch.setattr(discord_step, "_read_guild", fake)
    app = _state_bag()
    first, code = await discord_step.fetch_guild_data(app, TOKEN, GUILD)
    second, code2 = await discord_step.fetch_guild_data(app, TOKEN, GUILD)
    assert code == "" and code2 == ""
    assert first is second                      # the SAME object: one read, served from the cache
    assert calls == [(TOKEN, GUILD)]
    # a DIFFERENT token or guild id re-reads — the cache cannot serve a stale key
    await discord_step.fetch_guild_data(app, OTHER_TOKEN, GUILD)
    await discord_step.fetch_guild_data(app, TOKEN, OTHER_GUILD)
    assert calls == [(TOKEN, GUILD), (OTHER_TOKEN, GUILD), (TOKEN, OTHER_GUILD)]


@pytest.mark.asyncio
async def test_a_failed_read_is_recorded_for_its_own_key_only(monkeypatch):
    async def bad(token, guild_id):
        return None, "discord_bad_token"

    monkeypatch.setattr(discord_step, "_read_guild", bad)
    app = _state_bag()
    data, code = await discord_step.fetch_guild_data(app, TOKEN, GUILD)
    assert data is None and code == "discord_bad_token"
    assert discord_step.cached_guild(app, TOKEN, GUILD) is None
    assert discord_step.fetch_status(app, TOKEN, GUILD) == "discord_bad_token"
    # another key has no status at all (no read attempted)
    assert discord_step.fetch_status(app, OTHER_TOKEN, GUILD) is None


def test_application_id_is_derived_from_the_token():
    assert discord_step.application_id(TOKEN) == 123456789
    assert discord_step.application_id(OTHER_TOKEN) == 987654321
    assert discord_step.application_id("not-a-token") is None
    assert discord_step.application_id(None) is None


def test_the_guild_data_shape_offers_no_category_and_no_everyone():
    """``_as_guild_data`` drops category channels and role kind that must never be offered."""
    import discord
    from types import SimpleNamespace

    def channel(cid, name, kind=discord.ChannelType.text, category=None, position=0):
        return SimpleNamespace(id=cid, name=name, type=kind, category=category, position=position)

    def role(name, default=False, managed=False, integration=False):
        return SimpleNamespace(name=name, is_default=lambda: default,
                               is_bot_managed=lambda: managed,
                               is_integration=lambda: integration)

    category = SimpleNamespace(name="TEXT")
    guild = SimpleNamespace(name="G")
    data = discord_step._as_guild_data(
        GUILD, guild,
        [channel(1, "general"), channel(2, "TEXT", kind=discord.ChannelType.category),
         channel(3, "bot-status", category=category, position=-1)],
        [role("@everyone", default=True), role("Admin"), role("Bot", managed=True),
         role("Integration", integration=True)],
        [(GUILD, "G")])
    assert [c.name for c in data.channels] == ["bot-status", "general"]   # category channel dropped
    assert data.channels[0].category == "TEXT"                            # its category becomes the group
    assert data.roles == ("Admin",)
    assert data.guilds == ((GUILD, "Test Guild"),) or data.guilds == ((GUILD, "G"),)


@pytest.mark.asyncio
async def test_the_bot_admin_state_comes_from_its_own_membership_or_is_unknown():
    """``_bot_administrator`` reads the bot's own member; an unknown id or a failed read is NOT admin.

    The bot's id is its own user id, with the application id as the fallback; an id that is unknown,
    and a member read that raises, both yield ``None`` — the honest ``unknown`` — never ``False``
    masquerading as ``True`` and never a guess in either direction.
    """
    import discord

    class Member:
        def __init__(self, admin: bool):
            self.guild_permissions = discord.Permissions(administrator=admin)

    class Guild:
        owner_id = 0

        def __init__(self, member=None, error=None):
            self._member, self._error = member, error

        async def fetch_member(self, member_id):
            assert member_id == 42, "the read must ask for the bot's OWN id"
            if self._error is not None:
                raise self._error
            return self._member

    class Client:
        def __init__(self, *, user_id=None, application_id=None):
            self.user = types.SimpleNamespace(id=user_id) if user_id else None
            self.application_id = application_id

    assert await discord_step._bot_administrator(Client(user_id=42), Guild(Member(True))) is True
    assert await discord_step._bot_administrator(Client(user_id=42), Guild(Member(False))) is False
    # the application id stands in for a missing user id (the bot's id, derived from the token)
    assert await discord_step._bot_administrator(
        Client(application_id=42), Guild(Member(True))) is True
    # no id at all: unknown, never admin
    assert await discord_step._bot_administrator(Client(), Guild(Member(True))) is None
    # the member read fails: unknown, never admin
    assert await discord_step._bot_administrator(
        Client(user_id=42), Guild(error=RuntimeError("boom"))) is None


# ------------------------------------------------------------------------------ the pickers: step 6

@pytest.mark.asyncio
async def test_the_roles_and_channels_are_real_selects_when_the_guild_was_read(tmp_path):
    draft = seed(tmp_path, step=5, discord_bot={"guild_id": GUILD, "owner": 7,
                                                "privileged_intents": True})
    app, _ = build(tmp_path)
    plant_guild(app)
    discord_step.token_store(app)[discord_step.TOKEN_KEY] = TOKEN
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "6"})).text
    # the channel pickers are real <select> elements, with <optgroup> per category and #name options
    assert '<select name="audit_channel">' in html
    assert '<select name="admin_channel">' in html
    assert '<optgroup label="BOT">' in html and '>#bot-status</option>' in html
    assert '>#general</option>' in html
    # the role rows are multiple selects, name-valued
    assert '<select name="role_0" multiple' in html
    assert '<option value="Admin"' in html and '<option value="Pilot"' in html
    # a guild role that is NOT offered (`@everyone`) but is the stored default stays selectable
    assert '<option value="@everyone" selected>' in html
    # the typed fallback and the + Add control are gone while the picker is shown
    assert 'name="role_0" value="Admin"' not in html
    assert 'class="add"' not in html


@pytest.mark.asyncio
async def test_a_failed_read_degrades_to_typed_values_and_says_so(tmp_path):
    draft = seed(tmp_path, step=5, discord_bot={"guild_id": GUILD, "owner": 7,
                                                "privileged_intents": True})
    app, _ = build(tmp_path)
    discord_step.token_store(app)[discord_step.TOKEN_KEY] = TOKEN
    discord_step.guild_status(app).update({"key": (TOKEN, GUILD), "code": "discord_unreachable"})
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "6"})).text
    assert "<select" not in html                                  # no picker
    assert 'name="audit_channel"' in html and 'name="role_0" value="Admin"' in html
    assert "Discord could not be reached" in html                 # the honest sentence
    assert 'class="notice warn" role="status"' in html


@pytest.mark.asyncio
async def test_no_stale_pickers_when_the_guild_id_changes(tmp_path):
    """The cache is keyed on the guild too: a changed guild id serves NO other guild's channels."""
    draft = seed(tmp_path, step=5, discord_bot={"guild_id": GUILD, "owner": 7,
                                                "privileged_intents": True})
    app, _ = build(tmp_path)
    plant_guild(app)
    discord_step.token_store(app)[discord_step.TOKEN_KEY] = TOKEN
    async with asgi_client(app) as client:
        await enter(client)
        assert "<select" in (await client.get(STEP_PATH, params={"n": "6"})).text
        # the operator goes back to step 5 and changes the guild id
        saved = SetupDraft.load(tmp_path)
        saved.discord_bot = {"guild_id": OTHER_GUILD, "owner": 7, "privileged_intents": True}
        saved.save(tmp_path)
        html = (await client.get(STEP_PATH, params={"n": "6"})).text
    assert "<select" not in html, "the old guild's pickers must not be served for a new guild id"


@pytest.mark.asyncio
async def test_going_back_and_changing_the_token_refetches(monkeypatch, tmp_path):
    reads: list = []

    async def fake(token, guild_id):
        reads.append((token, guild_id))
        return GUILD_DATA, ""

    monkeypatch.setattr(discord_step, "_read_guild", fake)
    seed(tmp_path, step=4)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(page), "n": "5",
                                           "action": "test",
                                           "guild_id": str(GUILD), "owner": "7", "token": TOKEN})
        assert reads == [(TOKEN, GUILD)]
        # change the token: the (token, guild) key changes, so the read runs again
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(page), "n": "5",
                                           "action": "test", "guild_id": str(GUILD), "owner": "7",
                                           "token": OTHER_TOKEN})
    assert reads == [(TOKEN, GUILD), (OTHER_TOKEN, GUILD)]


# ------------------------------------------------- the read: the category comes from the list

def test_the_category_name_is_resolved_from_the_fetched_channels_not_the_guild_cache():
    """The HTTP-only read leaves the guild's channel cache empty, so ``channel.category`` is None.

    The parent category must therefore be resolved from the category channels of the fetched list —
    the name that becomes the ``<optgroup>`` label — not from ``channel.category``.
    """
    class Chan:
        def __init__(self, cid, name, kind, parent=None, position=0):
            self.id = cid
            self.name = name
            self.type = kind
            self.category_id = parent
            self.position = position
            self.category = None          # exactly what the real read leaves behind

    channels = [
        Chan(100, "Admin", discord.ChannelType.category, position=1),
        Chan(101, "Ops", discord.ChannelType.category, position=2),
        Chan(200, "admin", discord.ChannelType.text, parent=100, position=1),
        Chan(201, "admin", discord.ChannelType.text, parent=101, position=2),
        Chan(202, "general", discord.ChannelType.text, parent=100, position=3),
        Chan(203, "loose", discord.ChannelType.text, parent=None, position=4),
    ]
    data = discord_step._as_guild_data(GUILD, types.SimpleNamespace(name="G"), channels, [], [])
    by_id = {c.id: c for c in data.channels}
    assert by_id[200].category == "Admin"
    assert by_id[201].category == "Ops"           # same name, disambiguated by its category
    assert by_id[202].category == "Admin"
    assert by_id[203].category == ""              # no parent -> the ungrouped bucket


def test_a_hostile_channel_and_category_name_cannot_inject():
    """A channel or category name is escaped wherever it is rendered (option text and optgroup label)."""
    hostile_name = '#a</option><script>alert(1)</script>'
    hostile_cat = '"><img src=x onerror=alert(2)>Cat'
    data = discord_step.GuildData(
        guild_id=GUILD, guild_name="Test Guild",
        channels=(discord_step.ChannelOption(id=1, name=hostile_name, category=hostile_cat),),
        roles=(), guilds=((GUILD, "Test Guild"),))
    html = tour._channel_control("audit channel", "audit_channel", "", data, lambda s: s)
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x onerror=" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert 'label="&quot;&gt;&lt;img' in html     # the optgroup label is attribute-escaped


# ------------------------------------------------------------------------------ the pickers: step 8

@pytest.mark.asyncio
async def test_the_server_channel_fields_are_selects_when_the_guild_was_read(tmp_path):
    draft = seed(tmp_path, step=7, discord_bot={"guild_id": GUILD, "owner": 7,
                                                "privileged_intents": True})
    app, _ = build(tmp_path)
    plant_guild(app)
    discord_step.token_store(app)[discord_step.TOKEN_KEY] = TOKEN
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert 'name="row_status_0"' in html and "<select" in html
    assert '>#bot-status</option>' in html
    assert "Chosen from your Discord server." in html


# --------------------------------------------------------------------------- the Web console step

@pytest.mark.asyncio
async def test_the_console_step_asks_the_switch_the_address_and_the_account_but_not_the_secret(
        tmp_path):
    """The console step keeps its own questions; the Discord client secret is step 5's (see below)."""
    seed(tmp_path, step=6, discord_bot={"guild_id": GUILD, "owner": 7,
                                        "privileged_intents": True})
    app, _ = build(tmp_path)
    app.state.setup_node_detection = node_step.Detection(public_ip="203.0.113.5")
    async with asgi_client(app) as client:
        await enter(client)
        console = (await client.get(STEP_PATH, params={"n": "7"})).text
        discord = (await client.get(STEP_PATH, params={"n": "5"})).text
    assert "Do you want the web admin console?" in console
    assert 'name="console_enabled" checked' in console
    assert 'name="console_public_base_url" value="http://203.0.113.5:9876"' in console
    assert 'name="console_username"' in console
    assert 'type="password" name="console_password" value=""' in console
    # the secret is NOT asked on the console step any more — it sits with the other Discord creds
    assert 'name="console_client_secret"' not in console
    assert 'type="password" name="console_client_secret" value=""' in discord


@pytest.mark.asyncio
async def test_the_console_off_variant_asks_only_the_switch(tmp_path):
    seed(tmp_path, step=6)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "7"})).text
        response = await client.post(STEP_PATH, data={"_csrf_token": token_of(page), "n": "7"})
        after = (await client.get(STEP_PATH, params={"n": "7"})).text
    assert response.status_code == 303
    assert "The console is off." in after
    draft = SetupDraft.load(tmp_path)
    assert draft.console["enabled"] is False
    assert "username" not in draft.console or not draft.console["username"]


@pytest.mark.asyncio
async def test_the_console_account_is_required_while_the_console_is_on(tmp_path):
    seed(tmp_path, step=6)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "7"})).text
        response = await client.post(STEP_PATH, data={"_csrf_token": token_of(page), "n": "7",
                                                      "console_enabled": "on",
                                                      "console_public_base_url": "https://c.example"})
    assert "Enter a username for the console account." in response.text
    # the typed address was kept even though the account was refused
    assert SetupDraft.load(tmp_path).console["public_base_url"] == "https://c.example"


@pytest.mark.asyncio
async def test_the_client_secret_goes_to_the_process_store_and_never_the_draft(tmp_path, monkeypatch):
    """The secret is typed on STEP 5 now — with the token and the guild — and never reaches the draft."""
    async def no_read(token, guild_id):
        return None, "discord_unreachable"

    monkeypatch.setattr(discord_step, "_read_guild", no_read)
    seed(tmp_path, step=4, discord_bot={"guild_id": GUILD, "owner": 7,
                                        "privileged_intents": True})
    app, _ = build(tmp_path)
    secret = "CLIENT-SECRET-VALUE"
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(page), "n": "5",
                                           "guild_id": str(GUILD), "owner": "7",
                                           "token": TOKEN, "console_client_secret": secret})
        # and it never renders back
        again = (await client.get(STEP_PATH, params={"n": "5"})).text
    assert discord_step.client_secret_store(app)[discord_step.CLIENT_SECRET_KEY] == secret
    body = draft_path(tmp_path).read_text(encoding="utf-8")
    assert secret not in body
    assert secret not in again
    assert "A client secret is kept — leave the field empty to reuse it." in again


# ------------------------------------------------------------------- the written webservice.yaml

@pytest.mark.asyncio
async def test_the_written_console_enables_discord_only_when_the_secret_is_held(tmp_path):
    draft = seed(tmp_path, step=8, discord_bot={"guild_id": GUILD, "owner": 7,
                                                "privileged_intents": True},
                 console={"enabled": True, "public_base_url": "https://c.example",
                          "username": "admin", "password_hash": "pbkdf2_sha256$x"})
    app, _ = build(tmp_path)
    # no secret held: ONLY the break-glass door is written
    document = review_step._webservice_document(draft, app)["DEFAULT"]
    assert document["frontend"] is True
    assert document["auth"]["public_base_url"] == "https://c.example"
    assert document["auth"]["breakglass"] == {"enabled": True, "username": "admin",
                                             "password_hash": "pbkdf2_sha256$x"}
    assert "discord" not in document["auth"]
    # a secret held + a token whose application id is derivable: the Discord door joins it
    discord_step.token_store(app)[discord_step.TOKEN_KEY] = TOKEN
    discord_step.client_secret_store(app)[discord_step.CLIENT_SECRET_KEY] = "s3cret"
    document = review_step._webservice_document(draft, app)["DEFAULT"]
    assert document["auth"]["discord"] == {"enabled": True, "client_id": 123456789,
                                           "client_secret_key": "discord_client_secret"}
    assert document["auth"]["breakglass"]["enabled"] is True    # the account is ALWAYS there


@pytest.mark.asyncio
async def test_the_written_console_off_is_a_minimal_frontend_false_document(tmp_path):
    draft = seed(tmp_path, step=8, console={"enabled": False,
                                            "public_base_url": "https://c.example"})
    app, _ = build(tmp_path)
    document = review_step._webservice_document(draft, app)["DEFAULT"]
    assert document["frontend"] is False
    assert document["listen"] == "127.0.0.1"
    assert "auth" in document and "breakglass" not in document["auth"]
