"""Steps 5, 6 and 5' of the tour — Discord (the bot / roles and channels) and the no-Discord
community screen. The Web console step (7) has its own file.

TWO LAYERS, both over the shipped code:

* the VIEWS are driven through a real ASGI request against the shipped setup application, so the four
  Discord answers, the TOKEN's password-style field (and that it never renders back), the member-count
  switch with its consequence sentence, the three role rows, the channel controls and the UCID lists
  are the shipped ones and not a description of them;
* the ANSWER RESOLUTION and the config shapes (:mod:`services.webservice.setup.discord`,
  :mod:`~services.webservice.setup.community`) are driven directly, so the classification of an
  unusable answer and the parity with the installer's own builders are pinned.

THE TOKEN IS THE POINT OF HALF OF THIS FILE. The bot TOKEN goes through a real form post and the
draft file is then read back: it is in NEITHER (it is held in process memory).

NO DISCORD NETWORK HERE. The guild read a submit triggers is stubbed (``_read_guild``) so the suite
never depends on discord.com: the graceful path (a failed read -> typed values) is what it exercises,
and ``test_webui_setup_pickers`` covers the read itself.
"""

from __future__ import annotations

import ast
import pathlib
import re

from html import unescape

import pytest
from babel.messages import pofile

from conftest import asgi_client
from core.utils import setup_config
from services.webservice.auth import local as local_auth
from services.webservice.setup import community as community_step
from services.webservice.setup import console as console_step
from services.webservice.setup import discord as discord_step
from services.webservice.setup import tour
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import SetupDraft, draft_path
from services.webservice.setup.tour import LANGUAGE_PATH, STEP_PATH
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
#: the languages that ship a ``webui`` catalog. English is the source language and the msgid, so it
#: ships none: its strings are the msgids themselves.
CATALOGS = ("cn", "de", "es", "ru")

#: a token value no other word in the draft could contain by accident
SECRET_TOKEN = "MTIzNDU2Nzg5.SECRET-TOKEN-VALUE.QWERTY"

NODE_ANSWERED = {"listen_port": 10042, "no_dcs": True, "dcs_installation": None}

#: UCIDs the community screen accepts: exactly 32 letters and digits, lowercase.
UCID_A = "a" * 32
UCID_B = "b" * 32
UCID_C = "c" * 32


@pytest.fixture(autouse=True)
def no_discord_network(monkeypatch):
    """The guild read is stubbed out: the default classification is the graceful "unreachable"."""
    async def fake(token, guild_id):
        return None, "discord_unreachable"

    monkeypatch.setattr(discord_step, "_read_guild", fake)


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


def rail_rows(html: str) -> list[str]:
    block = re.search(r'<details class="rail".*?</details>', html, re.S)
    scope = block.group(0) if block else ""
    return re.findall(r'<span class="lbl">([^<]*)</span>', scope)


def shown(html: str) -> str:
    """The page as a reader sees it: an apostrophe is escaped in the markup, not in the sentence."""
    return unescape(html)


def seed(config_dir, *, step: int = 4, discord: bool = True) -> SetupDraft:
    """A draft whose current step is the Discord bot (5) with Discord on, or the community (5) off."""
    draft = SetupDraft(step=step, discord=discord)
    draft.node = dict(NODE_ANSWERED)
    draft.servers = {"scanned": True, "detected": {}, "rows": {}}
    draft.save(config_dir)
    return draft


def form(csrf: str, step: int, **fields) -> dict:
    data = {"_csrf_token": csrf, "n": str(step)}
    data.update(fields)
    return data


DISCORD_ANSWERS = {"guild_id": "1234567890", "token": SECRET_TOKEN, "owner": "987654321"}
ROLE_ANSWERS = {"role_0": "Admin", "role_1": "DCS Admin", "role_2": "@everyone"}


def guild_reads(monkeypatch) -> None:
    """Make the step's own Test SUCCEED (the suite never touches discord.com).

    The default fixture stubs a FAILED read (the graceful path); a test that walks PAST step 5 needs
    the read to succeed for these values first, which is what the step's gate requires.
    """
    async def fake(token, guild_id):
        return discord_step.GuildData(guild_id=1234567890, guild_name="Test Guild",
                                      channels=(), roles=("Admin", "DCS Admin")), ""

    monkeypatch.setattr(discord_step, "_read_guild", fake)


async def post_step_five(client, page: str, **extra) -> object:
    """Answer step 5 the way the screen requires: its own Test first, then Continue."""
    data = form(token_of(page), 5, **DISCORD_ANSWERS)
    data.update(extra)
    tested = await client.post(STEP_PATH, data={**data, "action": "test"})
    assert tested.status_code == 200
    data["_csrf_token"] = token_of(tested.text)
    return await client.post(STEP_PATH, data={**data, "action": "continue"})


async def post_step_six(client, page: str, **extra) -> object:
    data = form(token_of(page), 6, **ROLE_ANSWERS)
    data.update(extra)
    return await client.post(STEP_PATH, data=data)


# --------------------------------------------------------------------------------- step 5: the view

@pytest.mark.asyncio
async def test_the_card_asks_the_four_discord_questions(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "5"})).text
    assert "Guild ID" in html and "TOKEN" in html and "Owner ID" in html
    assert "Right-click your Discord server → Copy Server ID." in html
    assert "Right-click your own Discord user → Copy User ID." in html
    # the token guide names the portal path; the Developer Portal is a real link on the page's
    # FIRST line (step 5's own intro sentence), not in the token field's help
    assert ('<a class="link" href="https://discord.com/developers/applications" '
            'target="_blank" rel="noopener noreferrer">Discord Developer Portal</a>') in html
    assert "→ your application → Bot → Reset Token." in html
    assert "Do you have more than 10000 members in your Discord server?" in html
    for name in ("guild_id", "token", "owner", "more_than_members"):
        assert f'name="{name}"' in html, name
    # the TOKEN is a password-style field and is drawn EMPTY
    assert 'type="password" name="token" value=""' in html
    # the consequence sentence is not shown while the switch is off
    assert 'id="members-note" hidden' in html


@pytest.mark.asyncio
async def test_the_token_help_links_to_the_developer_portal(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "5"})).text
    # a real anchor that opens in a new tab, its label the portal's own name
    assert ('<a class="link" href="https://discord.com/developers/applications" '
            'target="_blank" rel="noopener noreferrer">Discord Developer Portal</a>') in html
    # the URL is repeated as plain text, so a copy carries the address
    assert "<code>https://discord.com/developers/applications</code>" in html
    # the sentence carrying the link is the page's FIRST line, the link inside it
    assert ('To obtain the following values, you need to log into the '
            '<a class="link"') in html
    assert html.index("https://discord.com/developers/applications") < html.index('name="guild_id"')
    # the link is drawn ONCE: the token field's own help only names the path inside the portal
    assert html.count('href="https://discord.com/developers/applications"') == 1
    assert "<br>→ your application → Bot → Reset Token." not in html
    assert "→ your application → Bot → Reset Token." in html


# ------------------------------------------------------- step 5: the credential layout and the claim

#: the Test's SUCCESS confirmation, as it renders for the suite's own read
CONNECTED = "Connected — the bot is in Test Guild (1234567890)."


@pytest.mark.asyncio
async def test_the_credentials_are_one_grid_guild_owner_then_token_secret(tmp_path):
    """The card's layout: Guild ID and Owner ID on line 1, TOKEN and the client secret on line 2.

    The order is DOCUMENT order inside ONE ``.fields`` grid, which the stylesheet renders in two
    columns and collapses to one on a narrow screen — so the layout a JavaScript-off reader gets is
    the card's, exactly.
    """
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "5"})).text
    grid = html[html.index('<div class="fields">'):html.index('<label class="switch">')]
    order = [grid.index(f'name="{name}"') for name in
             ("guild_id", "owner", "token", "console_client_secret")]
    assert order == sorted(order), "the credential fields are not in the card's order"
    assert grid.count('name="console_client_secret"') == 1, "the secret sits outside the one grid"
    # the skip note follows the grid and says what an empty secret means
    assert "Leave it empty and the console's Discord login stays off." in shown(grid)


@pytest.mark.asyncio
async def test_a_successful_test_states_the_connection_and_names_the_guild(tmp_path, monkeypatch):
    """The POSITIVE half of the Test: a successful read is stated, and it names what it found."""
    guild_reads(monkeypatch)
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        assert CONNECTED not in page, "a connection is claimed before anything was tested"
        tested = await client.post(STEP_PATH, data={**form(token_of(page), 5, **DISCORD_ANSWERS),
                                                    "action": "test"})
        rendered = await client.get(STEP_PATH, params={"n": "5"})
    assert tested.status_code == 200
    assert 'class="notice ok" role="status"' in tested.text
    assert CONNECTED in shown(tested.text)
    # the fact is PROCESS state (the read's own answer), so it survives a plain re-render
    assert CONNECTED in shown(rendered.text)


@pytest.mark.asyncio
async def test_a_failed_read_states_its_guide_and_claims_no_connection(tmp_path):
    """The autouse fixture's read fails: the guide stands and no connection is asserted."""
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await client.post(STEP_PATH, data={**form(token_of(page), 5, **DISCORD_ANSWERS),
                                                    "action": "test"})
    assert tested.status_code == 200
    assert "Connected — the bot is in" not in shown(tested.text)
    assert 'class="notice ok"' not in tested.text


@pytest.mark.asyncio
async def test_the_confirmation_is_keyed_on_the_values_it_was_made_for(tmp_path, monkeypatch):
    """A read that passed for one guild id claims nothing for another: the key decides."""
    async def selective(token, guild_id):
        if int(guild_id or 0) == 1234567890:
            return discord_step.GuildData(guild_id=1234567890, guild_name="Test Guild",
                                          channels=(), roles=("Admin",)), ""
        return None, "discord_unreachable"

    monkeypatch.setattr(discord_step, "_read_guild", selective)
    seed(tmp_path)
    app, _ = build(tmp_path)
    other = {"guild_id": "1234567891", "token": SECRET_TOKEN, "owner": "987654321"}
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await client.post(STEP_PATH, data={**form(token_of(page), 5, **DISCORD_ANSWERS),
                                                    "action": "test"})
        assert CONNECTED in shown(tested.text)
        moved = await client.post(STEP_PATH, data={**form(token_of(tested.text), 5, **other),
                                                   "action": "test"})
    assert moved.status_code == 200
    assert "Connected — the bot is in" not in shown(moved.text)
    assert 'class="notice ok"' not in moved.text


@pytest.mark.asyncio
async def test_the_member_count_switch_carries_the_consequence_sentence(tmp_path):
    draft = seed(tmp_path)
    draft.discord_bot = {"guild_id": 1, "owner": 2, "privileged_intents": False}
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "5"})).text
    assert 'name="more_than_members" checked' in html
    assert 'id="members-note"' in html and 'id="members-note" hidden' not in html
    assert "You need to grant the 'View Audit Log' permission to your bot." in shown(html)


@pytest.mark.asyncio
async def test_step_five_needs_the_three_fields_and_reports_each_failure(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        missing = await client.post(STEP_PATH, data=form(
            token_of(page), 5, **{**DISCORD_ANSWERS, "guild_id": ""}))
        assert missing.status_code == 200
        assert "Enter your Discord Guild ID." in missing.text
        bad_owner = await client.post(STEP_PATH, data=form(
            token_of(missing.text), 5, **{**DISCORD_ANSWERS, "owner": "not-a-number"}))
    assert "The Owner ID must be a whole number." in bad_owner.text
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 4                      # nothing advanced
    assert draft.discord_bot["guild_id"] == 1234567890   # what was typed is kept
    assert draft.discord_bot["owner"] == 987654321


# -------------------------------------------------------------------------------- step 5: the write

@pytest.mark.asyncio
async def test_a_complete_step_five_writes_the_answers_and_advances(tmp_path, monkeypatch):
    guild_reads(monkeypatch)
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        response = await post_step_five(client, page)
    assert response.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 5
    assert draft.discord_bot == {"guild_id": 1234567890, "owner": 987654321,
                                 "privileged_intents": True}


@pytest.mark.asyncio
async def test_the_member_count_answer_is_stored_as_privileged_intents(tmp_path, monkeypatch):
    guild_reads(monkeypatch)
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        response = await post_step_five(client, page, more_than_members="on")
    assert response.status_code == 303
    assert SetupDraft.load(tmp_path).discord_bot["privileged_intents"] is False


@pytest.mark.asyncio
async def test_the_token_is_held_in_the_process_and_never_in_the_draft(tmp_path):
    """The TOKEN goes to process memory (its destination is config/.secret/token.pkl, the installer's
    own), and the draft file — plain text on disk — never carries it."""
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        await post_step_five(client, page)
        # the second render offers to reuse the held token, and never shows it back
        again = (await client.get(STEP_PATH, params={"n": "5"})).text
    assert SECRET_TOKEN not in again
    assert 'type="password" name="token" value=""' in again
    assert "A token is kept for this step — leave the field empty to reuse it." in again
    assert discord_step.token_store(app)[discord_step.TOKEN_KEY] == SECRET_TOKEN
    body = draft_path(tmp_path).read_text(encoding="utf-8")
    assert SECRET_TOKEN not in body
    assert "password_hash" not in body                # no account was given on this step
    assert not (tmp_path / "main.yaml").exists()


@pytest.mark.asyncio
async def test_a_missing_token_is_reported_when_none_is_held(tmp_path):
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        response = await client.post(STEP_PATH, data=form(
            token_of(page), 5, **{**DISCORD_ANSWERS, "token": ""}))
    assert "Enter your Discord TOKEN." in response.text
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 4
    assert draft.discord_bot["guild_id"] == 1234567890   # the rest of the answers are kept


@pytest.mark.asyncio
async def test_a_blank_token_continues_once_one_is_held(tmp_path, monkeypatch):
    """The token is kept for the process lifetime (the database step's own rule for its passwords),
    so the step stops asking for it — and never renders it back."""
    guild_reads(monkeypatch)
    seed(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        assert await post_step_five(client, page) is not None
        again = (await client.get(STEP_PATH, params={"n": "5"})).text
        assert 'type="password" name="token" value="">' in again      # no longer required
        assert "A token is kept for this step — leave the field empty to reuse it." in again
        # a NEW guild id: the held token is reused, but the read for it must pass again before Continue
        data = form(token_of(again), 5, guild_id="1", owner="2")
        await client.post(STEP_PATH, data={**data, "action": "test"})
        response = await client.post(STEP_PATH, data={**data, "action": "continue"})
    assert response.status_code == 303


# --------------------------------------------------------------------------------- step 6: the view

@pytest.mark.asyncio
async def test_the_card_shows_the_three_role_rows_with_their_meanings(tmp_path):
    seed(tmp_path, step=5)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "6"})).text
    assert "We now need to setup your Discord roles and channels." in html
    for role in ("Admin", "DCS Admin", "DCS"):
        assert f'<div class="role-name">{role}</div>' in html
    assert "Users can delete data, change the bot, run commands on your server" in html
    assert "Users can upload missions, start/stop DCS servers, kick/ban users, etc." in html
    assert "Normal user, can pull statistics, ATIS, etc." in html
    # the guild could not be read, so the + Add control stands, the comma sentence is GONE (the roles
    # are entered through role selects, never a comma list) and the defaults sentence stands
    assert "Please separate roles by comma, if you want to provide more than one." not in html
    assert ("You can keep the defaults, if unsure and create the respective roles in your Discord "
            "server.") in html
    assert "class=\"add\"" in html and "+ Add" in html
    # the defaults are prefilled, one field per value (a text field, the no-picker fallback)
    assert 'name="role_0" value="Admin"' in html
    assert 'name="role_1" value="DCS Admin"' in html
    assert 'name="role_2" value="@everyone"' in html
    # the admin-channel question comes BEFORE any channel control, and the central answer is the
    # preselected default
    assert html.index('name="admin_scope"') < html.index('name="audit_channel"')
    assert html.index('name="audit_channel"') < html.index('name="admin_channel"')
    assert 'name="admin_scope" value="central" checked' in html
    assert 'name="admin_scope" value="per_server" checked' not in html
    assert 'name="audit_channel"' in html
    # the central admin channel field is rendered while the central answer is in force
    assert 'name="admin_channel"' in html
    assert 'name="console_username"' not in html
    assert "Console account" not in html


@pytest.mark.asyncio
async def test_the_admin_channel_question_carries_its_own_bold_lead_and_spacing(tmp_path):
    """The question is its own block: a bold lead line, its sentence beneath it, and the spacing class.

    The shape a role row already gives its name and meaning (a bold lead, then the sentence), plus the
    margin that keeps the block from running into the role-mapping note above it.
    """
    seed(tmp_path, step=5)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "6"})).text
    assert ('<fieldset class="choice admin-choice"><legend class="choice-legend">'
            '<b>admin channel</b>The bot can either use a dedicated admin channel for each server '
            'or a central admin channel for all servers.</legend>') in html
    assert ".choice.admin-choice{margin-top:14px}" in html
    assert ".choice-legend b{display:block;font-weight:600;color:var(--text);font-size:13px}" in html


@pytest.mark.asyncio
async def test_a_missing_role_and_a_bad_channel_are_reported_on_the_step(tmp_path):
    seed(tmp_path, step=5)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "6"})).text
        empty_role = await client.post(STEP_PATH, data=form(
            token_of(page), 6, **{**ROLE_ANSWERS, "role_0": ""}))
        assert "Enter at least one role or role id for Admin." in empty_role.text
        bad_channel = await client.post(STEP_PATH, data=form(
            token_of(empty_role.text), 6, **ROLE_ANSWERS, audit_channel="not-a-number"))
    assert "A channel id must be a whole number, or -1 for none." in bad_channel.text
    assert SetupDraft.load(tmp_path).step == 5


@pytest.mark.asyncio
async def test_the_per_server_scope_removes_the_admin_channel_field_from_the_document(tmp_path):
    """On the per-server answer the central admin channel field is ABSENT, not disabled-but-present."""
    draft = seed(tmp_path, step=5)
    draft.roles = {"roles": {}, "central_admin": False}
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "6"})).text
    assert 'name="admin_scope" value="per_server" checked' in html
    assert 'name="audit_channel"' in html              # the audit channel stays
    assert "admin_channel" not in html                 # absent, not disabled-but-present


@pytest.mark.asyncio
async def test_switching_the_admin_scope_re_renders_and_toggles_the_admin_field(tmp_path):
    """The stay re-post (what the no-JS Continue and the script's change handler both use) re-renders
    the step: the central admin field appears with central and is gone with per-server."""
    seed(tmp_path, step=5)
    app, _ = build(tmp_path)
    role_fields = {**ROLE_ANSWERS}
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "6"})).text
        per = await client.post(STEP_PATH, data=form(token_of(page), 6, stay="1",
                                                     admin_scope="per_server", **role_fields))
        assert per.status_code == 200
        assert "admin_channel" not in per.text and 'value="per_server" checked' in per.text
        central = await client.post(STEP_PATH, data=form(token_of(per.text), 6, stay="1",
                                                         admin_scope="central", **role_fields))
    assert 'name="admin_channel"' in central.text and 'value="central" checked' in central.text
    # the answer is stored either way
    assert SetupDraft.load(tmp_path).roles["central_admin"] is True


# -------------------------------------------------------------------------------- step 6: the write

@pytest.mark.asyncio
async def test_a_complete_step_six_writes_the_mapping_and_advances(tmp_path):
    seed(tmp_path, step=5)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "6"})).text
        response = await post_step_six(client, page, audit_channel="555", admin_scope="central",
                                       admin_channel="666")
    assert response.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 6
    assert draft.roles == {
        "roles": {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]},
        "audit_channel": "555", "admin_channel": "666", "central_admin": True}


@pytest.mark.asyncio
async def test_the_comma_form_and_the_repeated_fields_both_parse(tmp_path):
    """``+ Add`` sends one field per value; the installer's comma form stays accepted server-side."""
    seed(tmp_path, step=5)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "6"})).text
        response = await client.post(STEP_PATH, data={
            "_csrf_token": token_of(page), "n": "6",
            "role_0": ["Admin", "1234567890"],
            "role_1": "DCS Admin, 42",
            "role_2": "@everyone"})
    assert response.status_code == 303
    roles = SetupDraft.load(tmp_path).roles["roles"]
    assert roles["Admin"] == ["Admin", "1234567890"]
    assert roles["DCS Admin"] == ["DCS Admin", "42"]
    # the installer coerces a numeric role to an int in the config it writes
    answers = discord_step.roles_from_mapping({"roles": roles})
    assert discord_step.roles_block(answers)["Admin"] == ["Admin", 1234567890]


# ------------------------------------------------------------------------- step 5': the community

@pytest.mark.asyncio
async def test_the_no_discord_card_asks_for_the_group_and_the_ucids(tmp_path):
    seed(tmp_path, discord=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "5"})).text
    assert "Please enter the name of your DCS group" in html
    assert "You now need to setup your users." in html
    assert "Users can delete data and change the bot" in html
    assert "Users can restart missions, kick/ban users, etc." in html
    assert "These users are UCIDs, not Discord roles" in html
    assert "A UCID is the 32-character player id DCS gives a user" in html
    assert 'name="guild_name"' in html
    assert 'name="user_0"' in html and 'name="user_1"' in html
    # one value per field: each row carries its own `+ Add` and its own removal control
    assert html.count('class="add"') == 2 and html.count('class="rm"') == 2
    # the console account is NOT here any more (the Web console step owns it)
    assert "Console account" not in html and 'name="console_username"' not in html
    # the Discord card is NOT the one rendered
    assert 'name="guild_id"' not in html


@pytest.mark.asyncio
async def test_a_complete_community_step_writes_the_answers_and_advances(tmp_path):
    seed(tmp_path, discord=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        response = await client.post(STEP_PATH, data=form(
            token_of(page), 5, guild_name="My Group",
            user_0=f"{UCID_A}, {UCID_B}", user_1=UCID_C))
    assert response.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 5
    assert draft.community == {"guild_name": "My Group",
                               "users": {"Admin": [UCID_A, UCID_B], "DCS Admin": [UCID_C]}}


@pytest.mark.asyncio
async def test_the_group_name_is_required(tmp_path):
    seed(tmp_path, discord=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        response = await client.post(STEP_PATH, data=form(
            token_of(page), 5, user_0="111"))
    assert "Enter the name of your DCS group." in response.text
    assert SetupDraft.load(tmp_path).step == 4


# ------------------------------------------------------------ the rail flip: both sides keep theirs

@pytest.mark.asyncio
async def test_flipping_discord_off_and_on_keeps_both_sides_answers(tmp_path, monkeypatch):
    """Discord answered off and back on: the Discord blocks and the community block all survive."""
    guild_reads(monkeypatch)
    seed(tmp_path, step=4)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        step_five = (await client.get(STEP_PATH, params={"n": "5"})).text
        await post_step_five(client, step_five)
        step_six = (await client.get(STEP_PATH, params={"n": "6"})).text
        await post_step_six(client, step_six)
        # now flip Discord off on step 2 and walk back to the community screen
        step_two = (await client.get(STEP_PATH, params={"n": "2"})).text
        await client.post(STEP_PATH, data=form(token_of(step_two), 2, stay="1"))
        off = (await client.get(STEP_PATH, params={"n": "2"})).text
        assert "Your community" in rail_rows(off)
        community = (await client.get(STEP_PATH, params={"n": "5"})).text
        assert "Please enter the name of your DCS group" in community
        await client.post(STEP_PATH, data=form(token_of(community), 5, guild_name="My Group",
                                               user_0=UCID_A, user_1=UCID_B))
        # and back on: the Discord answers are exactly as they were left
        step_two = (await client.get(STEP_PATH, params={"n": "2"})).text
        await client.post(STEP_PATH, data=form(token_of(step_two), 2, discord="on", stay="1"))
        back = (await client.get(STEP_PATH, params={"n": "5"})).text
    assert "Do you have more than 10000 members in your Discord server?" in back
    assert 'name="guild_id" value="1234567890"' in back
    document = SetupDraft.load(tmp_path).as_document()
    assert document["discord_bot"]["owner"] == 987654321
    assert document["roles"]["roles"]["Admin"] == ["Admin"]
    assert document["community"] == {"guild_name": "My Group",
                                     "users": {"Admin": [UCID_A], "DCS Admin": [UCID_B]}}


# --------------------------------------------------------------------------- the resolution layer

def test_resolve_bot_reports_each_unusable_answer():
    previous = discord_step.BotAnswers()
    assert discord_step.resolve_bot({}, previous, validate=True)[1] == \
        ("discord_guild_required", {})
    assert discord_step.resolve_bot({"guild_id": "x"}, previous, validate=True)[1] == \
        ("discord_guild_invalid", {})
    assert discord_step.resolve_bot({"guild_id": "1"}, previous, validate=True)[1] == \
        ("discord_owner_required", {})
    answers, failure = discord_step.resolve_bot({"guild_id": "5", "owner": "6"}, previous,
                                                validate=True)
    assert failure is None and answers.guild_id == 5 and answers.owner == 6
    assert answers.privileged_intents is True


def test_resolve_bot_accepts_a_later_guild_and_owner_id():
    answers, failure = discord_step.resolve_bot({"guild_id": "1234567890", "owner": "9876543210",
                                                 "more_than_members": "on"},
                                                discord_step.BotAnswers(), validate=True)
    assert failure is None
    assert answers.guild_id == 1234567890 and answers.owner == 9876543210
    assert answers.privileged_intents is False


def test_resolve_roles_needs_every_row_and_reads_the_admin_choice():
    previous = discord_step.RoleAnswers()
    answers, failure = discord_step.resolve_roles(
        {"role_0": "A", "role_1": "", "role_2": "C"}, previous, validate=True)
    assert answers is None and failure == ("role_required", {"role": "DCS Admin"})
    ok, failure = discord_step.resolve_roles(
        {"role_0": "A", "role_1": "B", "role_2": "C", "admin_scope": "central",
         "admin_channel": "42"}, previous, validate=True)
    assert failure is None and ok.central_admin is True and ok.admin_channel == "42"


def test_resolve_roles_neither_clears_a_kept_value_nor_fails_on_a_stray_switch():
    previous = discord_step.RoleAnswers()
    answers, failure = discord_step.resolve_roles(
        {"role_0": "A", "role_1": "B", "role_2": "C", "admin_channel": "7"},
        previous, validate=False)
    assert failure is None and answers.central_admin is True   # the central default
    assert answers.admin_channel == "7"


def test_roles_from_mapping_falls_back_to_the_installers_defaults():
    answers = discord_step.roles_from_mapping(None)
    assert answers.roles["Admin"] == ("Admin",)
    assert answers.roles["DCS Admin"] == ("DCS Admin",)
    assert answers.roles["DCS"] == ("@everyone",)
    stored = discord_step.roles_from_mapping({"roles": {"Admin": ["A", "B"]}})
    assert stored.roles["Admin"] == ("A", "B")


def test_resolve_community_requires_only_the_group_name():
    """The group name and the UCIDs are both checked; the name is the one reported first."""
    previous = community_step.CommunityAnswers()
    answers, failure = community_step.resolve_community({"user_0": "not-a-ucid"}, previous,
                                                        validate=True)
    assert answers is None and failure == ("community_name_required", {})
    answers, failure = community_step.resolve_community({"guild_name": "G", "user_0": UCID_A,
                                                         "user_1": UCID_B}, previous, validate=True)
    assert failure is None and answers.users == {"Admin": (UCID_A,), "DCS Admin": (UCID_B,)}


# ------------------------------------------------------------------------------- the config shapes

def test_the_discord_shapes_match_the_installers_own_builders():
    bot = discord_step.BotAnswers(guild_id=123, owner=42, privileged_intents=True)
    roles = discord_step.RoleAnswers(
        roles={"Admin": ("Admin",), "DCS Admin": ("DCS Admin", "42"), "DCS": ("@everyone",)},
        audit_channel="7", admin_channel="9", central_admin=True)
    assert discord_step.main_block(bot) == {"guild_id": 123}
    assert discord_step.roles_block(roles) == {
        "Admin": ["Admin"], "DCS Admin": ["DCS Admin", 42], "DCS": ["@everyone"]}
    assert discord_step.bot_block(bot, roles) == setup_config.build_discord_bot(
        owner=42, privileged_intents=True,
        roles={"Admin": ["Admin"], "DCS Admin": ["DCS Admin", 42], "DCS": ["@everyone"]},
        audit_channel=7, admin_channel=9)


def test_a_per_server_admin_channel_writes_no_central_one():
    bot = discord_step.BotAnswers(guild_id=1, owner=2)
    roles = discord_step.RoleAnswers(roles={"Admin": ("A",), "DCS Admin": ("B",), "DCS": ("C",)},
                                     admin_channel="9", central_admin=False)
    assert "channels" not in discord_step.bot_block(bot, roles)


def test_the_community_shapes_match_the_installers_own_builders():
    answers = community_step.CommunityAnswers(
        guild_name="My Group", users={"Admin": ("111",), "DCS Admin": ("222", "333")})
    assert community_step.main_block(answers) == {"guild_name": "My Group"}
    assert community_step.bot_block(answers) == setup_config.build_no_discord_bot(
        {"Admin": ["111"], "DCS Admin": ["222", "333"]})
    assert setup_config.build_no_discord_main("My Group", True)["guild_name"] == "My Group"


def test_the_console_account_block_is_the_breakglass_shape():
    from services.webservice.auth import breakglass

    answers = console_step.ConsoleAnswers(username="specialk",
                                          password_hash=local_auth.hash_password("pw"))
    block = console_step.account_block(answers)
    assert block == {"enabled": True, "username": "specialk",
                     "password_hash": answers.password_hash}
    assert console_step.account_block(console_step.ConsoleAnswers()) == {}
    # the block is accepted by the backend that reads it, and the credential signs in
    backend = breakglass.breakglass_backend(block)
    assert backend is not None and backend.username == "specialk"
    assert backend.verify_credentials(None, "specialk", "pw") is not None
    assert backend.verify_credentials(None, "specialk", "wrong") is None


def test_the_console_account_hash_is_the_console_scheme():
    encoded = console_step.hash_password("pw")
    assert local_auth.SCHEME in encoded
    assert console_step.MAX_PASSWORD_LENGTH == local_auth.MAX_PASSWORD_LENGTH


# ------------------------------------------------------------------------------- i18n coverage

#: every string the Discord/community screens add (the step titles/help lines, the notices)
NEW_STRINGS = (
    "Guild ID", "TOKEN", "Owner ID",
    "Right-click your Discord server → Copy Server ID.",
    "Right-click your own Discord user → Copy User ID.",
    "Discord Developer Portal",
    "To obtain the following values, you need to log into the {link}.",
    "→ your application → Bot → Reset Token. This is shown once; the console stores it in "
    "config/.secret/.",
    "Do you have more than 10000 members in your Discord server?",
    "You need to grant the 'View Audit Log' permission to your bot.",
    "A token is kept for this step — leave the field empty to reuse it.",
    "Enter your Discord Guild ID.", "The Guild ID must be a whole number.",
    "Enter your Discord Owner ID.", "The Owner ID must be a whole number.",
    "Enter your Discord TOKEN.",
    "We now need to setup your Discord roles and channels.\nDCSServerBot creates a role mapping for "
    "your bot users. It has the following internal roles:",
    "You can keep the defaults, if unsure and create the respective roles in your Discord server.",
    "+ Add",
    "Users can delete data, change the bot, run commands on your server",
    "Users can upload missions, start/stop DCS servers, kick/ban users, etc.",
    "Normal user, can pull statistics, ATIS, etc.",
    "audit channel", "A channel for audit events — optional, -1 for none.",
    "The bot can either use a dedicated admin channel for each server or a central admin channel "
    "for all servers.",
    "A dedicated admin channel per server", "One central admin channel for all servers",
    "admin channel", "The id of the central admin channel — optional, -1 for none.",
    "Enter at least one role or role id for {role}.",
    "A channel id must be a whole number, or -1 for none.",
    "Please enter the name of your DCS group",
    "You now need to setup your users.\nDCSServerBot uses the following internal roles:",
    "Users can delete data and change the bot",
    "Users can restart missions, kick/ban users, etc.",
    "These users are UCIDs, not Discord roles",
    "A UCID is the 32-character player id DCS gives a user — it is not a Discord role, name or id. "
    "Enter one UCID per field, exactly 32 characters; use + Add for more users. A role may stay "
    "empty: it then grants nothing.",
    "Remove",
    "Enter the name of your DCS group.",
    "'{value}' is not a UCID. A UCID is exactly 32 letters and digits — the DCS player id, not a "
    "Discord role or user id. Check the {role} entry.",
    # the blocking guide for a broken Discord connection (invalid token / no guild / too many guilds)
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
    # the Test's SUCCESS confirmation (it names the guild the read found)
    "Connected — the bot is in {guild}.",
)

#: a string these screens RETIRED — no longer rendered, no longer a msgid. It used to be one msgid with
#: the sentence above it, and it described a comma list the role SELECTS never ask for.
RETIRED_STRINGS = (
    "Please separate roles by comma, if you want to provide more than one.",
    # ... and the merged msgid it was half of, so the two-part entry cannot survive in a catalog
    "Please separate roles by comma, if you want to provide more than one.\n"
    "You can keep the defaults, if unsure and create the respective roles in your Discord server.",
    # the community screen's single comma-joined field: reworded into the UCID explanation above it
    "Please enter a comma-separated list of UCIDs",
)


def test_every_new_string_is_an_i18n_marked_literal():
    source = (REPO_ROOT / "services/webservice/setup/tour.py").read_text(encoding="utf-8")
    marked = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "i18n" and node.args
                and isinstance(node.args[0], ast.Constant)):
            marked.add(node.args[0].value)
    for msgid in NEW_STRINGS:
        assert msgid in marked, msgid


def test_every_new_string_is_shipped_in_every_catalog():
    for language in CATALOGS:
        catalog = pofile.read_po(open(
            REPO_ROOT / f"locale/{language}/LC_MESSAGES/webui.po", encoding="utf-8"))
        have = {message.id for message in catalog if message.id}
        for msgid in NEW_STRINGS:
            assert msgid in have, f"'{msgid}' is missing from the {language} catalog"


def test_every_retired_string_is_gone_from_the_source_and_the_catalogs():
    """The removal is pinned where a presence list cannot express it.

    The retired sentence is absent from the step's marked literals (so nothing renders it) and from
    every catalog (so no catalog promises a string the code no longer owns). A catalog left holding it
    as a live msgid fails here, which is what keeps the extraction-diff honest rather than merely
    smaller.
    """
    source = (REPO_ROOT / "services/webservice/setup/tour.py").read_text(encoding="utf-8")
    marked = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "i18n" and node.args
                and isinstance(node.args[0], ast.Constant)):
            marked.add(node.args[0].value)
    for msgid in RETIRED_STRINGS:
        assert msgid not in marked, msgid
        assert msgid not in source, msgid
    for language in CATALOGS:
        catalog = pofile.read_po(open(
            REPO_ROOT / f"locale/{language}/LC_MESSAGES/webui.po", encoding="utf-8"))
        have = {message.id for message in catalog if isinstance(message.id, str) and message.id
                and not getattr(message, "obsolete", False)}
        for msgid in RETIRED_STRINGS:
            assert msgid not in have, f"'{msgid}' is still a live msgid in the {language} catalog"


def test_the_step_five_and_six_cards_render_in_german(tmp_path):
    """Through the compiled catalog, so an untranslated-but-present file is not mistaken for one."""
    import gettext
    translations = gettext.translation("webui", localedir=str(REPO_ROOT / "locale"),
                                       languages=["de"])
    assert translations.gettext("A dedicated admin channel per server") == \
        "Ein eigener Admin-Kanal pro Server"
    assert translations.gettext("Enter your Discord TOKEN.") == "Gebe Deinen Discord TOKEN ein."


@pytest.mark.asyncio
async def test_the_card_is_translated_when_the_tour_language_is_german(tmp_path):
    seed(tmp_path, step=5)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        await client.get(LANGUAGE_PATH, params={"lang": "de", "next": f"{STEP_PATH}?n=6"})
        html = (await client.get(STEP_PATH, params={"n": "6"})).text
    assert "Audit-Kanal" in html
    assert "<b>Admin-Kanal</b>" in html
    assert "Ein eigener Admin-Kanal pro Server" in html
