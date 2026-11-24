"""Where the console's Discord client secret is asked for, per Discord decision.

The secret belongs with the other Discord credentials on step 5 ("Discord — the bot") and NOTHING
Discord-shaped belongs on the no-Discord path (there is no Discord OAuth2 there at all). These tests
drive the SHIPPED setup application over HTTP and read the real stores, the real draft and the real
written files:

* the RENDER table — Discord on asks for it on step 5 and on no other step; Discord off asks for it
  nowhere and offers no Discord login anywhere;
* it is a password field that never renders back, and a hostile value is stored verbatim;
* the Review screen and the commit name it BY PRESENCE, never by value, and the written
  ``services/webservice.yaml`` is exactly the shape the ``auth.discord`` block had before.
"""

from __future__ import annotations

import re
import socket
import types
from html import unescape

import pytest
from ruamel.yaml import YAML

from conftest import asgi_client
from core import utils as core_utils
from services.webservice.setup import console as console_step
from services.webservice.setup import database as db_step
from services.webservice.setup import discord as discord_step
from services.webservice.setup import node as node_step
from services.webservice.setup import review as review_step
from services.webservice.setup import servers as servers_step
from services.webservice.setup import tour
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.node import Detection
from services.webservice.setup.state import DRAFT_FILENAME, SetupDraft, draft_path
from services.webservice.setup.tour import STEP_PATH
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')
#: a token whose first segment decodes to application id 123456789 (``discord.application_id``)
TOKEN = "MTIzNDU2Nzg5.guild-cache-token.AAAA"
GUILD = 111111111111111111
OWNER = 222222222222222222
CONSOLE_HASH = "pbkdf2_sha256$600000$cafe$beef"
#: UCIDs the no-Discord community screen accepts: exactly 32 letters and digits, lowercase
UCID_ONE = "1" * 32
UCID_TWO = "2" * 32
#: the exact marker the render carries when the secret field is drawn (never the value)
SECRET_FIELD = 'type="password" name="console_client_secret" value=""'
#: the note under the credential grid that names what an empty secret means (the skip path)
LOGIN_NOTE = "Leave it empty and the console's Discord login stays off."


@pytest.fixture(autouse=True)
def _node_arguments(monkeypatch):
    """The schema validator reads ``COMMAND_LINE_ARGS.node``; pytest is not ``run.py``."""
    import core.commandline as commandline
    from core.utils import validators
    argv = types.SimpleNamespace(node="TestNode", config="config")
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)


def build(config_dir, *, code: str = CODE):
    window = SetupWindow(code=code, wrong_delay=0.0)
    return create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN, node="TestNode"), window


async def enter(client, code: str = CODE):
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the form carries no CSRF token"
    return found.group(1)


def seed_secrets(app, *, token: str = TOKEN) -> None:
    """The in-memory secrets a live session holds (the Review step refuses to write without them)."""
    discord_step.token_store(app)[discord_step.TOKEN_KEY] = token


def seed_detection(app) -> None:
    app.state.setup_node_detection = Detection(candidates=(("DCS World", "/tmp/dcs"),),
                                              public_ip=None, upnp=False)


def load(path) -> dict:
    return YAML(typ="safe").load(open(path, encoding="utf-8"))


def complete_draft(config_dir, *, discord: bool = True) -> SetupDraft:
    """A draft with every step answered — the tour as it looks the moment step 9 is reached."""
    draft = SetupDraft(step=9, language="en", bot_language=True, autoupdate=True, discord=discord)
    draft.database = {"path": db_step.CREATE, "host": "127.0.0.1", "port": 5432,
                      "master_database": "postgres", "master_user": "postgres",
                      "database": "dcsserverbot", "user": "dcsserverbot",
                      "user_password_attempts": 0, "verified": True, "signature": "sig"}
    draft.node = {"listen_port": 10042, "use_upnp": False, "no_dcs": False,
                  "dcs_installation": "/tmp/dcs", "public_ip": None,
                  "dgsa_dcs": True, "dgsa_discord": True}
    draft.servers = {"scanned": True, "detected": {"DCS.openbeta": "Test Server"},
                     "rows": {"DCS.openbeta": {"manage": True, "name": "Test Server",
                                               "status": "123", "chat": "-1", "admin": "-1",
                                               "autostart": True}}}
    draft.discord_bot = {"guild_id": GUILD, "owner": OWNER, "privileged_intents": True}
    draft.roles = {"roles": {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]},
                   "audit_channel": "", "admin_channel": "", "central_admin": False}
    draft.community = {"guild_name": "Test Group", "users": {"Admin": ["u1"], "DCS Admin": ["u2"]}}
    draft.console = {"enabled": True, "username": "admin", "password_hash": CONSOLE_HASH}
    draft.save(config_dir)
    return draft


async def _pages(client, numbers=range(1, 10)) -> dict[int, str]:
    """Every step's rendered page, keyed by its rail id (a step not on the rail renders the current)."""
    return {n: (await client.get(STEP_PATH, params={"n": str(n)})).text for n in numbers}


# ------------------------------------------------------------------------- the per-path render table

@pytest.mark.asyncio
async def test_discord_on_the_secret_renders_on_the_discord_step_and_nowhere_else(tmp_path):
    """Discord ON: the secret is asked ONCE, on step 5 — the console step no longer asks it."""
    complete_draft(tmp_path, discord=True)
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        pages = await _pages(client)
    carriers = [n for n, html in pages.items() if 'name="console_client_secret"' in html]
    assert carriers == [5], f"the secret must be asked on step 5 only, found on {carriers}"
    assert SECRET_FIELD in pages[5]                       # a password field, rendered empty
    # ONE credential grid, in the card's order: Guild ID, Owner ID, TOKEN, client secret
    order = [pages[5].index(f'name="{name}"') for name in
             ("guild_id", "owner", "token", "console_client_secret")]
    assert order == sorted(order), "the credential fields are not in the card's layout order"
    assert LOGIN_NOTE in unescape(pages[5])               # what an empty secret means
    # the console step keeps its own questions and no secret field
    assert 'name="console_enabled"' in pages[7]
    assert 'name="console_username"' in pages[7]
    assert SECRET_FIELD not in pages[7]


@pytest.mark.asyncio
async def test_discord_off_asks_for_no_client_secret_and_offers_no_discord_login(tmp_path):
    """Discord OFF: no Discord page exists, so nothing Discord-shaped is asked anywhere."""
    complete_draft(tmp_path, discord=False)
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        pages = await _pages(client)
    for n, html in pages.items():
        assert 'name="console_client_secret"' not in html, f"step {n} asks for a client secret"
        assert LOGIN_NOTE not in unescape(html), f"step {n} offers a Discord login"
    # the console step still asks its own questions, and the break-glass account is how you get in
    assert 'name="console_enabled"' in pages[7]
    assert 'name="console_username"' in pages[7]
    assert 'name="console_password"' in pages[7]


# ------------------------------------------------------------------------- never renders back, hostile

@pytest.mark.asyncio
async def test_the_secret_never_renders_back_and_never_reaches_the_draft(tmp_path, monkeypatch):
    """A typed secret goes to the process store; no page and no draft file ever carries the value."""
    async def no_read(token, guild_id):
        return None, "discord_unreachable"

    monkeypatch.setattr(discord_step, "_read_guild", no_read)
    SetupDraft(step=4, discord=True, discord_bot={"guild_id": GUILD, "owner": OWNER,
                                                  "privileged_intents": True}).save(tmp_path)
    app, _ = build(tmp_path)
    secret = "CLIENT-SECRET-VALUE"
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(page), "n": "5",
                                           "guild_id": str(GUILD), "owner": str(OWNER),
                                           "token": TOKEN, "console_client_secret": secret})
        after = (await client.get(STEP_PATH, params={"n": "5"})).text
    assert discord_step.client_secret_store(app)[discord_step.CLIENT_SECRET_KEY] == secret
    assert secret not in after
    assert SECRET_FIELD in after                                  # still a field, still EMPTY
    assert "value=\"" + secret not in after
    # the held state is SAID, not shown: the note replaces the help line
    assert "A client secret is kept" in after
    # the draft is plain text on disk and never carries it
    body = draft_path(tmp_path).read_text(encoding="utf-8")
    assert secret not in body


@pytest.mark.asyncio
async def test_a_hostile_secret_is_stored_verbatim_and_is_never_rendered(tmp_path, monkeypatch):
    """A secret is an opaque value: stored as typed, and it can never inject into a page."""
    async def no_read(token, guild_id):
        return None, "discord_unreachable"

    monkeypatch.setattr(discord_step, "_read_guild", no_read)
    SetupDraft(step=4, discord=True, discord_bot={"guild_id": GUILD, "owner": OWNER,
                                                  "privileged_intents": True}).save(tmp_path)
    app, _ = build(tmp_path)
    hostile = '"><script>alert(1)</script>&'
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(page), "n": "5",
                                           "guild_id": str(GUILD), "owner": str(OWNER),
                                           "token": TOKEN, "console_client_secret": hostile})
        after = unescape((await client.get(STEP_PATH, params={"n": "5"})).text)
    assert discord_step.client_secret_store(app)[discord_step.CLIENT_SECRET_KEY] == hostile
    assert "<script>alert(1)</script>" not in after
    assert hostile not in after
    assert hostile not in draft_path(tmp_path).read_text(encoding="utf-8")


# ------------------------------------------------------------------------- the Review and the write

@pytest.mark.asyncio
async def test_discord_on_the_review_names_the_secret_by_presence_and_the_yaml_is_unchanged(tmp_path):
    """The Review line and the ``auth.discord`` block carry the secret by PRESENCE, never by value."""
    draft = complete_draft(tmp_path, discord=True)
    app, _ = build(tmp_path)
    seed_secrets(app)
    secret = "CLIENT-SECRET-VALUE"
    discord_step.client_secret_store(app)[discord_step.CLIENT_SECRET_KEY] = secret
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "config/.secret/discord_client_secret.pkl" in html
    assert "the console's Discord client secret" in html
    assert secret not in html                                  # the value is never on the screen
    # the commit writes it as a SECRET (its store key + the value), never into a config file
    planned = {item.target.key: item for item in review_step.plan(draft, app=app)}
    assert planned["discord_client_secret"].secret == ("discord_client_secret", secret)
    # and the written auth.discord block is exactly the shape it had before the move
    auth = review_step._webservice_document(draft, app)["DEFAULT"]["auth"]
    assert auth["discord"] == {"enabled": True, "client_id": 123456789,
                               "client_secret_key": "discord_client_secret"}
    assert auth["breakglass"] == {"enabled": True, "username": "admin",
                                  "password_hash": CONSOLE_HASH}


@pytest.mark.asyncio
async def test_discord_off_names_no_client_secret_in_the_review_or_the_plan(tmp_path):
    """Discord OFF: no client secret is named in the Review, planned or written — only break-glass."""
    draft = complete_draft(tmp_path, discord=False)
    app, _ = build(tmp_path)
    seed_secrets(app)
    # even a secret left in the process store is inert: nothing Discord-shaped reaches the config
    discord_step.client_secret_store(app)[discord_step.CLIENT_SECRET_KEY] = "must-not-be-written"
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "discord_client_secret" not in html
    assert "the console's Discord client secret" not in html
    keys = {item.target.key for item in review_step.plan(draft, app=app)}
    assert "discord_client_secret" not in keys and "token" not in keys
    auth = review_step._webservice_document(draft, app)["DEFAULT"]["auth"]
    assert "discord" not in auth
    assert auth["breakglass"]["username"] == "admin"


# ------------------------------------------------------------------------- the whole tour, both paths

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stub_the_machine(monkeypatch) -> None:
    """What a test machine cannot provide: the PostgreSQL test, the DCS probes and Discord."""
    monkeypatch.setattr(db_step, "run_test",
                        lambda *a, **k: db_step.TestOutcome(code="ok_created", ok=True,
                                                            version="16.4"))
    monkeypatch.setattr(servers_step, "detect_instances",
                        lambda: {"DCS.openbeta": "Test Server"})

    # the guild read is stubbed to SUCCEED, so step 5's own Test passes and the walk can continue
    async def fake_read(token, guild_id):
        return discord_step.GuildData(guild_id=int(guild_id or 0), guild_name="Test Guild",
                                      channels=(), roles=("Admin",)), ""

    monkeypatch.setattr(discord_step, "_read_guild", fake_read)


async def _step(client, number: int, **fields):
    page = await client.get(STEP_PATH, params={"n": str(number)})
    data = {"_csrf_token": token_of(page.text), "n": str(number)}
    data.update(fields)
    return await client.post(STEP_PATH, data=data)


async def walk_to_review(client, *, discord: bool, secret: str | None = None):
    """The gate and the rail's steps over HTTP; returns the Review screen."""
    assert (await client.get(SETUP_PATH, params={"code": CODE})).status_code == 303
    await _step(client, 1, bot_language="on")
    await _step(client, 2, autoupdate="on", **({"discord": "on"} if discord else {}))
    await _step(client, 3, action="test", path="create", host="127.0.0.1", port="5432",
                master_database="postgres", master_user="postgres", master_password="master-pw",
                database="dcsserverbot", user="dcsserverbot")
    await _step(client, 3, action="continue", path="create", host="127.0.0.1", port="5432",
                database="dcsserverbot", user="dcsserverbot")
    await _step(client, 4, dcs_choice="0", listen_port=str(_free_port()), dgsa_dcs="on")
    if discord:
        fields = {"guild_id": str(GUILD), "token": TOKEN, "owner": str(OWNER)}
        if secret:
            fields["console_client_secret"] = secret
        # step 5 needs its own Test to pass (the guild read for these values) before Continue
        await _step(client, 5, action="test", **fields)
        await _step(client, 5, action="continue", **fields)
        await _step(client, 6, role_0="Admin", role_1="DCS Admin", role_2="@everyone",
                    admin_scope="per_server", audit_channel="", admin_channel="")
    else:
        await _step(client, 5, guild_name="Test Group", user_0=UCID_ONE, user_1=UCID_TWO)
    await _step(client, 7, console_enabled="on", console_username="admin",
                console_password="console-password")
    await _step(client, 8, row_manage_0="on", row_name_0="Test Server", row_status_0="123",
                row_chat_0="-1", row_admin_0="-1", row_autostart_0="on")
    await _step(client, 9, template_0="public-server")
    return await client.get(STEP_PATH, params={"n": "10"})


@pytest.mark.asyncio
async def test_the_whole_tour_writes_the_secret_to_the_installer_store(tmp_path, monkeypatch):
    """Discord ON end to end: the secret typed on step 5 reaches ``config/.secret/`` and nothing else."""
    stub_the_machine(monkeypatch)
    secret = "CLIENT-SECRET-VALUE"
    app, _ = build(tmp_path)
    seed_detection(app)
    async with asgi_client(app) as client:
        review = await walk_to_review(client, discord=True, secret=secret)
        html = unescape(review.text)
        assert "config/.secret/discord_client_secret.pkl" in html
        assert secret not in html
        done = await client.post(STEP_PATH, data={"_csrf_token": token_of(review.text), "n": "10",
                                                  "action": "write"})
    assert done.status_code == 200 and "Setup is done" in unescape(done.text)
    assert core_utils.get_password(discord_step.CLIENT_SECRET_KEY,
                                   str(tmp_path)) == secret
    auth = load(tmp_path / "services" / "webservice.yaml")["DEFAULT"]["auth"]
    assert auth["discord"] == {"enabled": True, "client_id": 123456789,
                               "client_secret_key": "discord_client_secret"}
    assert auth["breakglass"]["username"] == "admin"
    assert not (tmp_path / DRAFT_FILENAME).exists()


@pytest.mark.asyncio
async def test_the_whole_no_discord_tour_writes_no_client_secret_and_no_discord_login(
        tmp_path, monkeypatch):
    """Discord OFF end to end: no secret file, no ``auth.discord`` — only the break-glass account."""
    stub_the_machine(monkeypatch)
    app, _ = build(tmp_path)
    seed_detection(app)
    async with asgi_client(app) as client:
        review = await walk_to_review(client, discord=False)
        assert "discord_client_secret" not in unescape(review.text)
        done = await client.post(STEP_PATH, data={"_csrf_token": token_of(review.text), "n": "10",
                                                  "action": "write"})
    assert done.status_code == 200 and "Setup is done" in unescape(done.text)
    assert not (tmp_path / ".secret" / "discord_client_secret.pkl").exists()
    auth = load(tmp_path / "services" / "webservice.yaml")["DEFAULT"]["auth"]
    assert "discord" not in auth
    assert auth["breakglass"]["username"] == "admin"
    assert not (tmp_path / DRAFT_FILENAME).exists()
