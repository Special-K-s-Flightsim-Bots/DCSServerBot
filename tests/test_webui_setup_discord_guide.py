"""The blocking guide for a broken Discord connection: invalid token, no guild, too many guilds.

THE RULE THIS PINS: a Discord connection that does not work must block the tour AND guide the
operator — never a raw API/driver string, but what is wrong, why it matters, and what to do about it.
The Continue gate is already covered (``test_webui_setup_continue_gate``); this file is about the
GUIDE each classified failure carries.

THREE STATES, all over the shipped application, all with the network stubbed (``_read_guild``):

* an INVALID TOKEN names where the token comes from (the Developer Portal path the project's own help
  line uses) and how to reset it;
* NO GUILD gets a REAL invite link, built from the token's own application id and the project's own
  permission set (``core.const.DEFAULT_CHANNEL_PERMISSIONS``), rendered clickable AND as plain text;
* MORE THAN ONE GUILD lists where the bot is (escaped), explains only one is allowed, and offers the
  chooser that can make the bot leave the extras.

The chooser's own action — ``leave_others`` — is driven twice: through the route (with the leave call
stubbed) so the draft/config relationship is pinned, and directly on ``setup.discord.leave_others``
with a fake Discord client so "the kept guild is NEVER left" and the per-guild report are pinned
WITHOUT touching discord.com.
"""

from __future__ import annotations

import asyncio
import re

import discord
import pytest
from babel.messages import pofile

from conftest import asgi_client
from core.const import DEFAULT_CHANNEL_PERMISSIONS
from services.webservice.setup import discord as discord_step
from services.webservice.setup import tour
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import SetupDraft
from services.webservice.setup.tour import STEP_PATH
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')
REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
#: the languages that ship a ``webui`` catalog. English is the source language and the msgid, so it
#: ships none: its strings are the msgids themselves.
CATALOGS = ("cn", "de", "es", "ru")

#: application id 123456789 (the first dot-segment is base64 of the snowflake)
TOKEN = "MTIzNDU2Nzg5.guide-token.AAAA"
GUILD = 111111111111111111
OTHER = 222222222222222222
PERMISSIONS = 126016


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


def shown(html: str) -> str:
    from html import unescape
    return unescape(html)


def seed(config_dir, *, step: int = 4) -> SetupDraft:
    draft = SetupDraft(step=step, discord=True)
    draft.node = {"listen_port": 10042, "no_dcs": True, "dcs_installation": None}
    draft.servers = {"scanned": True, "detected": {}, "rows": {}}
    draft.save(config_dir)
    return draft


def form(csrf: str, step: int, **fields) -> dict:
    return {"_csrf_token": csrf, "n": str(step), **fields}


def read_fails(monkeypatch, code: str, guilds=()):
    """The read is stubbed to FAIL with *code*; the multiple-guilds state also carries its list."""
    async def fake(token, guild_id):
        if code == "discord_multiple_guilds":
            data = discord_step.GuildData(guild_id=int(guild_id), guild_name="", guilds=guilds)
            return data, code
        return None, code

    monkeypatch.setattr(discord_step, "_read_guild", fake)


async def post_test(client, page: str, **extra):
    data = form(token_of(page), 5, guild_id=str(GUILD), owner="7", token=TOKEN)
    data.update(extra)
    return await client.post(STEP_PATH, data={**data, "action": "test"})


# ------------------------------------------------------------------- 1: the invalid token

@pytest.mark.asyncio
async def test_an_invalid_token_guides_to_the_portal_and_a_reset(tmp_path, monkeypatch):
    read_fails(monkeypatch, "discord_bad_token")
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await post_test(client, page)
    html = shown(tested.text)
    assert "Discord refused this token" in html
    # where the token comes from, traced to the project's own path, and how to recover it
    assert "Discord Developer Portal → your application → Bot → Reset Token" in html
    assert "reset" in html and "invalidates the old one" in html
    # the gate stays closed: the read failed
    assert 'value="continue" disabled' in tested.text


# ------------------------------------------------------------------- 2: no guild + invite link

@pytest.mark.asyncio
async def test_no_guild_generates_a_clickable_and_plain_invite_link(tmp_path, monkeypatch):
    read_fails(monkeypatch, "discord_no_guild")
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await post_test(client, page)
    html = tested.text
    assert "the bot is not a member of any Discord server" in html
    url = f"https://discord.com/oauth2/authorize?client_id=123456789&scope=bot&permissions={PERMISSIONS}"
    # the URL carries an '&', so it is HTML-escaped in the markup (the browser renders/copies '&'):
    # clickable AND plain text, so a copy-paste survives
    markup_url = url.replace("&", "&amp;")
    assert f'<a class="link" href="{markup_url}">{markup_url}</a>' in html
    assert f"<code>{markup_url}</code>" in html
    assert url in shown(html)
    assert 'value="continue" disabled' in tested.text


def test_the_invite_link_shape_and_its_permission_source():
    url = discord_step.invite_url(TOKEN)
    assert url == ("https://discord.com/oauth2/authorize?client_id=123456789"
                   f"&scope=bot&permissions={PERMISSIONS}")
    # the integer is the project's OWN set (core.const), never a hand-written number
    derived = discord.Permissions(**{name: True for name in DEFAULT_CHANNEL_PERMISSIONS}).value
    assert discord_step.invite_permissions() == derived == PERMISSIONS
    # no derivable application id: no link (never a bogus one)
    assert discord_step.invite_url("not-a-token") is None
    assert discord_step.invite_url(None) is None


# ------------------------------------------------------------------- 3: too many guilds

@pytest.mark.asyncio
async def test_more_than_one_guild_lists_where_and_offers_the_chooser(tmp_path, monkeypatch):
    read_fails(monkeypatch, "discord_multiple_guilds", ((GUILD, "Alpha"), (OTHER, "Beta")))
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await post_test(client, page)
    html = shown(tested.text)
    assert "more than one Discord server" in html and "allows exactly one" in html
    assert "Alpha (111111111111111111)" in html and "Beta (222222222222222222)" in html
    # the chooser: a radio per server, an explicit confirmation and the leave button
    assert f'name="keep_guild" value="{GUILD}"' in tested.text
    assert f'name="keep_guild" value="{OTHER}"' in tested.text
    assert 'name="confirm_leave"' in tested.text
    assert 'name="action" value="leave_others"' in tested.text
    assert 'value="continue" disabled' in tested.text


@pytest.mark.asyncio
async def test_a_hostile_guild_name_is_escaped_in_the_list_and_the_chooser(tmp_path, monkeypatch):
    hostile = '<script>alert(1)</script>'
    read_fails(monkeypatch, "discord_multiple_guilds", ((GUILD, hostile), (OTHER, "Beta")))
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await post_test(client, page)
    assert "<script>alert(1)</script>" not in tested.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in tested.text


# ----------------------------------------------------------- the chooser's action, over the route

def fake_leave(monkeypatch, report, calls):
    async def leave(token, keep_id):
        calls.append((token, keep_id))
        return report

    monkeypatch.setattr(discord_step, "leave_others", leave)


@pytest.mark.asyncio
async def test_leaving_the_others_keeps_the_chosen_server_and_reports(tmp_path, monkeypatch):
    read_fails(monkeypatch, "discord_multiple_guilds", ((GUILD, "Alpha"), (OTHER, "Beta")))
    calls: list = []
    fake_leave(monkeypatch, {"kept": (GUILD, "Alpha"),
                             "left": [(OTHER, "Beta")], "failed": []}, calls)
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await post_test(client, page)
        # the kept guild now reads back cleanly (the leaves changed the bot's own guild list)
        read_ok(monkeypatch)
        data = form(token_of(tested.text), 5, guild_id=str(GUILD), owner="7",
                    keep_guild=str(GUILD), confirm_leave="on")
        left = await client.post(STEP_PATH, data={**data, "action": "leave_others"})
    assert calls == [(TOKEN, GUILD)], "the leave must be made for the chosen server"
    assert left.status_code == 200
    html = shown(left.text)
    assert "Beta (222222222222222222)" in html and "Alpha (111111111111111111)" in html
    draft = SetupDraft.load(tmp_path)
    # the kept guild becomes what the tour writes as the config guild id
    assert draft.discord_bot["guild_id"] == GUILD
    assert discord_step.main_block(
        discord_step.answers_from_mapping(draft.discord_bot)) == {"guild_id": GUILD}
    assert draft.discord_bot["leave"]["left"] == [[OTHER, "Beta"]]


def read_ok(monkeypatch):
    async def fake(token, guild_id):
        return discord_step.GuildData(guild_id=int(guild_id), guild_name="Alpha", channels=(),
                                      roles=(), guilds=((int(guild_id), "Alpha"),)), ""

    monkeypatch.setattr(discord_step, "_read_guild", fake)


@pytest.mark.asyncio
async def test_a_failed_leave_is_reported_per_guild_and_does_not_stop_the_others(tmp_path, monkeypatch):
    read_fails(monkeypatch, "discord_multiple_guilds", ((GUILD, "Alpha"), (OTHER, "Beta")))
    calls: list = []
    fake_leave(monkeypatch, {"kept": (GUILD, "Alpha"), "left": [],
                             "failed": [(OTHER, "Beta", "Forbidden")]}, calls)
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await post_test(client, page)
        read_ok(monkeypatch)
        data = form(token_of(tested.text), 5, guild_id=str(GUILD), owner="7",
                    keep_guild=str(GUILD), confirm_leave="on")
        left = await client.post(STEP_PATH, data={**data, "action": "leave_others"})
    html = shown(left.text)
    assert "could not be left" in html and "Beta (222222222222222222)" in html
    assert "kick them by hand" in html


@pytest.mark.asyncio
async def test_the_leave_needs_a_choice_and_an_explicit_confirmation(tmp_path, monkeypatch):
    read_fails(monkeypatch, "discord_multiple_guilds", ((GUILD, "Alpha"), (OTHER, "Beta")))
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await post_test(client, page)
        token = token_of(tested.text)
        # no choice made
        no_choice = await client.post(STEP_PATH, data={**form(token, 5, guild_id=str(GUILD),
                                                              owner="7", confirm_leave="on"),
                                                       "action": "leave_others"})
        assert "Choose the server to keep." in no_choice.text
        # a choice the read does not know (a crafted id) is refused too
        crafted = await client.post(STEP_PATH, data={**form(token_of(no_choice.text), 5,
                                                            guild_id=str(GUILD), owner="7",
                                                            keep_guild="999", confirm_leave="on"),
                                                     "action": "leave_others"})
        assert "Choose the server to keep." in crafted.text
        # a choice, but no confirmation
        no_confirm = await client.post(STEP_PATH, data={**form(token_of(crafted.text), 5,
                                                               guild_id=str(GUILD), owner="7",
                                                               keep_guild=str(GUILD)),
                                                        "action": "leave_others"})
    assert "Tick the box to confirm" in no_confirm.text


# ------------------------------------- the leave walk itself: the kept guild is NEVER left (unit)

class _FakeGuild:
    def __init__(self, gid, name):
        self.id = gid
        self.name = name


def _install_fake_client(monkeypatch, guilds, *, fail: set | None = None, login_ok: bool = True):
    """Replace ``discord.Client`` with a fake whose only jobs are login/list/leave."""
    fail = fail or set()
    left: list[int] = []

    class FakeHTTP:
        async def leave_guild(self, guild_id):
            if guild_id in fail:
                raise discord.Forbidden(_Resp(), "nope")
            left.append(guild_id)

    class FakeClient:
        def __init__(self, *, intents=None):
            self.http = FakeHTTP()
            self.closed = False

        async def login(self, token):
            if not login_ok:
                raise discord.LoginFailure("bad token")

        async def close(self):
            self.closed = True

        async def fetch_guilds(self, limit=200):
            for guild in guilds:
                yield guild

    monkeypatch.setattr(discord, "Client", FakeClient)
    return left


class _Resp:
    status = 403
    reason = "Forbidden"


def test_leave_others_never_leaves_the_kept_guild_and_reports_per_guild(monkeypatch):
    guilds = [_FakeGuild(GUILD, "Alpha"), _FakeGuild(OTHER, "Beta")]
    left = _install_fake_client(monkeypatch, guilds)
    report = asyncio.run(discord_step.leave_others(TOKEN, GUILD))
    assert report is not None
    assert report["kept"] == (GUILD, "Alpha")
    assert report["left"] == [(OTHER, "Beta")]
    assert report["failed"] == []
    assert GUILD not in left, "the kept guild must NEVER be left"
    assert left == [OTHER]


def test_leave_others_reports_a_guild_that_refuses(monkeypatch):
    guilds = [_FakeGuild(GUILD, "Alpha"), _FakeGuild(OTHER, "Beta")]
    left = _install_fake_client(monkeypatch, guilds, fail={OTHER})
    report = asyncio.run(discord_step.leave_others(TOKEN, GUILD))
    assert report["left"] == []
    assert report["failed"] == [(OTHER, "Beta", "Forbidden")]
    assert left == []


def test_leave_others_refuses_when_the_choice_is_not_a_guild_it_is_in(monkeypatch):
    guilds = [_FakeGuild(GUILD, "Alpha"), _FakeGuild(OTHER, "Beta")]
    left = _install_fake_client(monkeypatch, guilds)
    assert asyncio.run(discord_step.leave_others(TOKEN, 999)) is None
    assert left == [], "nothing is left when the choice is not a guild the bot is in"


def test_leave_others_refuses_when_the_token_cannot_log_in(monkeypatch):
    _install_fake_client(monkeypatch, [_FakeGuild(GUILD, "Alpha")], login_ok=False)
    assert asyncio.run(discord_step.leave_others(TOKEN, GUILD)) is None


# ------------------------------------------------------------------------------- i18n coverage

GUIDE_STRINGS = (
    "Discord refused this token, so the bot has no connection and DCSServerBot cannot start. "
    "Get the token from the Discord Developer Portal → your application → Bot → Reset Token; it is "
    "shown once. If you no longer have it, reset it there and copy the new value — a reset "
    "invalidates the old one.",
    "Discord accepted this token, but the bot is not a member of any Discord server. DCSServerBot "
    "manages exactly one server and cannot start without it.",
    "Invite the bot with this link, then test again:",
    "Discord accepted this token, but the bot is a member of more than one Discord server, and "
    "DCSServerBot allows exactly one.",
    "Kick it from every server but the one you keep, then test again. The servers it is in:",
    "Or let the tour make the bot leave the others: pick the server to keep — it becomes the guild "
    "id written to your configuration.",
    "I understand the bot will leave every other server.",
    "Leave the other servers",
    "The bot left {left}. The server kept is {guild} — that is the guild id your configuration gets.",
    "These could not be left, so kick them by hand and test again: {failed}.",
    "Choose the server to keep.",
    "Tick the box to confirm the bot will leave the other servers.",
)


def test_every_guide_string_is_shipped_in_every_catalog():
    for language in CATALOGS:
        catalog = pofile.read_po(open(
            REPO_ROOT / f"locale/{language}/LC_MESSAGES/webui.po", encoding="utf-8"))
        have = {message.id: message for message in catalog if message.id}
        for msgid in GUIDE_STRINGS:
            assert msgid in have, f"'{msgid}' is missing from the {language} catalog"
            assert not have[msgid].fuzzy, f"'{msgid}' is fuzzy in {language}"
            assert have[msgid].string, f"'{msgid}' is untranslated in {language}"
