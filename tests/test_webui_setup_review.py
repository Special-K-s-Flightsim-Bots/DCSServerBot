"""Step 9 of the tour — Review and write: the real configuration, the closing rule, the handover.

Three layers, all over the shipped code:

* the SCREEN is driven through a real ASGI request against the shipped setup application — the two
  lists, the commit button, the per-target results and the Done screen are the shipped ones;
* the WRITE is exercised for real: the files land in a temporary config directory through the one
  config-write primitive, with the bot's own schemas, and every written shape is read back and
  checked against what the bot and the console read;
* the WHOLE TOUR is walked end to end over HTTP — the gate, steps 1-9, the commit — with only the
  two things a test machine cannot provide stubbed (the PostgreSQL test and the machine's own DCS
  probes).

UNVERIFIED here: a real PostgreSQL server (the database test's DDL runs against a stub, as in
``test_webui_setup_database.py``) and a real DCS installation / Saved Games tree (the detection is
seeded). Everything else — every file, every key, the closing rule — is the shipped path.
"""
from __future__ import annotations

import asyncio
import ast
import pathlib
import re
import socket
import types
from html import unescape

import pytest
from babel.messages import pofile
from ruamel.yaml import YAML

from conftest import asgi_client
from services.webservice.setup import boot as setup_boot
from services.webservice.setup import database as db_step
from services.webservice.setup import discord as discord_step
from services.webservice.setup import review as review_step
from services.webservice.setup import servers as servers_step
from services.webservice.setup import tour as tour_step
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.node import Detection
from services.webservice.setup.state import DRAFT_FILENAME, SetupDraft
from services.webservice.setup.tour import LANGUAGE_PATH, STEP_PATH
from services.webservice.setup.window import SetupWindow
from core import utils

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
#: the languages that ship a ``webui`` catalog. English is the source language and the msgid, so it
#: ships none: its strings are the msgids themselves.
CATALOGS = ("cn", "de", "es", "ru")
NODE = "TestNode"
GUILD = 111111111111111111
OWNER = 222222222222222222
TOKEN = "discord-bot-token-not-a-real-one"
CONSOLE_HASH = "pbkdf2_sha256$600000$cafe$beef"


@pytest.fixture(autouse=True)
def _node_arguments(monkeypatch):
    """The schema validator reads ``COMMAND_LINE_ARGS.node``; pytest is not ``run.py``.

    pykwalify loads ``core/utils/validators.py`` as a fresh extension module, so the patch that
    reaches it is the one on ``core.commandline`` (the same recipe ``test_webui_frontend_switch``
    uses).
    """
    import core.commandline as commandline
    from core.utils import validators
    argv = types.SimpleNamespace(node=NODE, config="config")
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)


def build(config_dir, *, code: str = CODE):
    window = SetupWindow(code=code, wrong_delay=0.0)
    return create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN, node=NODE), window


async def enter(client, code: str = CODE):
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the form carries no CSRF token"
    return found.group(1)


def seed_secrets(app, *, token: str | None = TOKEN, database: str | None = None) -> None:
    """Put into the process stores the secrets a live session holds.

    A real tour types these into THIS process; a test that plants a complete draft has to seed them
    too, or the Review step correctly refuses to write (see ``review.missing_secrets``).
    """
    if token is not None:
        discord_step.token_store(app)[discord_step.TOKEN_KEY] = token
    if database is not None:
        db_step.secret_store(app)[db_step.BOT_PASSWORD_KEY] = database


def load(path) -> dict:
    return YAML(typ="safe").load(pathlib.Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------------- a complete draft, planted

def complete_draft(config_dir, *, discord: bool = True, path: str = db_step.CREATE) -> SetupDraft:
    """A draft with every step answered — what the tour looks like the moment the Review is reached."""
    draft = SetupDraft(step=9, language="en", bot_language=True, autoupdate=True, discord=discord)
    draft.database = {"path": path, "host": "127.0.0.1", "port": 5432, "master_database": "postgres",
                      "master_user": "postgres", "database": "dcsserverbot", "user": "dcsserverbot",
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
    draft.community = {}
    draft.console = {"username": "admin", "password_hash": CONSOLE_HASH}
    draft.save(config_dir)
    return draft


# ------------------------------------------------------------------------------- the screen

@pytest.mark.asyncio
async def test_the_review_screen_shows_the_two_lists_and_no_input(tmp_path):
    complete_draft(tmp_path)
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "Already done" in html
    assert "Will be written, nothing yet" in html
    assert "Write configuration" in html
    # the screen carries no answer: the only inputs are the CSRF token, the step id and the button
    inputs = re.findall(r'<input[^>]*name="([^"]+)"', html)
    assert set(inputs) == {"_csrf_token", "n"}
    assert "<textarea" not in html and "<select" not in html


@pytest.mark.asyncio
async def test_the_already_done_list_names_the_created_database_and_the_master_password(tmp_path):
    complete_draft(tmp_path, path=db_step.CREATE)
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "the bot's database and its own user were created (step 3)" in html
    assert "the PostgreSQL master password is stored in config/.secret/" in html


@pytest.mark.asyncio
async def test_the_existing_path_reports_a_verified_database_instead(tmp_path):
    complete_draft(tmp_path, path=db_step.EXISTING)
    app, _ = build(tmp_path)
    seed_secrets(app, database="the-existing-password")
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "the bot's database and its user were verified (step 3)" in html
    assert "created (step 3)" not in html
    assert "the PostgreSQL master password is stored in config/.secret/" not in html


@pytest.mark.asyncio
async def test_the_will_write_list_is_the_commit_itself(tmp_path):
    draft = complete_draft(tmp_path)
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    planned = {f"config/{item.target.relpath}" for item in review_step.plan(draft, app=app)}
    listed = set(re.findall(r'<span class="path">([^<]+)</span>', html))
    assert listed == planned
    assert "config/main.yaml" in listed


@pytest.mark.asyncio
async def test_the_not_written_list_names_the_block_the_discord_answer_left_out(tmp_path):
    draft = complete_draft(tmp_path, discord=False)
    draft.discord_bot = {"guild_id": GUILD, "owner": OWNER, "privileged_intents": True}
    draft.roles = {"roles": {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]}}
    draft.community = {"guild_name": "My Group", "users": {"Admin": ["u1"], "DCS Admin": []}}
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "Not written" in html
    assert "the Discord answers (guild, owner, roles) — Discord support is off" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("discord, console_enabled, hold_secret, expected", [
    (True, True, False, True),      # Discord on + console on + no secret held: the state the line is for
    (True, True, True, False),      # the secret is held, so the login IS written — nothing to say
    (True, False, False, False),    # console off, so there is no Discord login to name
    (False, True, False, False),    # no Discord at all, so the console asks for no login
])
async def test_the_console_login_line_only_for_discord_on_console_on_and_no_secret(
        tmp_path, discord, console_enabled, hold_secret, expected):
    """The one Review line: Discord on AND console enabled AND no client secret held in this process.

    Every other combination keeps the screen silent — the sentence names exactly the state where the
    console's Discord login will stay off, the contract point the commit could otherwise hide.
    """
    draft = complete_draft(tmp_path, discord=discord)
    draft.console = {"enabled": console_enabled, "username": "admin", "password_hash": CONSOLE_HASH}
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    seed_secrets(app)
    if hold_secret:
        discord_step.client_secret_store(app)[discord_step.CLIENT_SECRET_KEY] = "cd-client-secret"
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)

    line = "the console's Discord login — no client secret is held, so it stays off"
    assert (line in html) is expected
    # the commit is still offered: only the NOT-WRITTEN line changed, no new gate
    assert "Write configuration" in html


@pytest.mark.asyncio
async def test_no_secret_typed_says_the_console_login_stays_off_and_writes_no_discord_login(
        tmp_path, monkeypatch):
    """A whole walk that leaves the client secret empty: the Review screen says so, and the commit
    writes a ``webservice.yaml`` whose ``auth`` carries NO ``discord`` block."""
    stub_the_machine(monkeypatch)
    app, _ = build(tmp_path)
    seed_detection(app)
    async with asgi_client(app) as client:
        review = await walk_to_review(client)
        html = unescape(review.text)
        assert "the console's Discord login — no client secret is held, so it stays off" in html
        assert "Write configuration" in html
        done = await client.post(STEP_PATH, data={"_csrf_token": token_of(review.text), "n": "10",
                                                  "action": "write"})

    assert "Setup is done" in unescape(done.text)
    auth = load(tmp_path / "services" / "webservice.yaml")["DEFAULT"]["auth"]
    assert "discord" not in auth, "a console Discord login was written with no secret held"
    assert auth["breakglass"]["username"] == "admin"


# ------------------------------------------------------------------------------- the write

def record_writes(monkeypatch, *, fail_on: str | None = None,
                  explode_on: str | None = None) -> list[str]:
    """Wrap the ONE config-write primitive: record each target's name, optionally refuse one.

    ``fail_on`` raises the primitive's own ``ConfigWriteError`` (a validation refusal); ``explode_on``
    raises a bare non-pykwalify exception (the validator-side error the Review step must classify).
    """
    from core.utils import configwrite

    order: list[str] = []
    real = configwrite.save_config_file

    def wrapper(path, data, **kwargs):
        name = pathlib.Path(path).name
        if name != DRAFT_FILENAME:          # the tour's own draft is not a configuration target
            order.append(name)
        if fail_on is not None and name == fail_on:
            raise configwrite.ConfigWriteError(f"{name} refused by the test", path=path)
        if explode_on is not None and name == explode_on:
            raise AttributeError("'NoneType' object has no attribute 'value'")
        return real(path, data, **kwargs)

    monkeypatch.setattr(configwrite, "save_config_file", wrapper)
    return order


@pytest.mark.asyncio
async def test_the_write_writes_every_target_and_main_yaml_last(tmp_path, monkeypatch):
    complete_draft(tmp_path)
    order = record_writes(monkeypatch)
    utils.create_secret_dir(str(tmp_path))
    utils.set_password("database", "db-password", str(tmp_path))
    app, _ = build(tmp_path)
    seed_secrets(app)
    finished: list[bool] = []
    app.state.setup_finish = lambda: finished.append(True)

    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        response = await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                                      "action": "write"})

    assert response.status_code == 200
    assert "Setup is done" in unescape(response.text)
    assert finished == [True], "the console must be asked to stop after the response"
    assert order[-1] == "main.yaml", order
    assert order == ["bot.yaml", "nodes.yaml", "servers.yaml", "scheduler.yaml", "cloud.yaml",
                     "webservice.yaml", "main.yaml"], order

    # every file exists, and the draft (tour state) is gone, backup included
    assert not (tmp_path / DRAFT_FILENAME).exists()
    assert not (tmp_path / (DRAFT_FILENAME + ".bak")).exists()
    for name in ("main.yaml", "nodes.yaml", "servers.yaml", "services/bot.yaml",
                 "services/webservice.yaml", "plugins/scheduler.yaml", "plugins/cloud.yaml"):
        assert (tmp_path / name).is_file(), name


@pytest.mark.asyncio
async def test_the_written_files_are_the_shapes_the_bot_and_the_console_read(tmp_path, monkeypatch):
    complete_draft(tmp_path)
    record_writes(monkeypatch)
    utils.create_secret_dir(str(tmp_path))
    utils.set_password("database", "db-password", str(tmp_path))
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                           "action": "write"})

    main = load(tmp_path / "main.yaml")
    assert main["guild_id"] == GUILD and main["autoupdate"] is True and main["language"] == "en"

    bot = load(tmp_path / "services" / "bot.yaml")
    assert bot["owner"] == OWNER and bot["privileged_intents"] is True
    assert bot["roles"] == {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]}

    nodes = load(tmp_path / "nodes.yaml")
    node = nodes[NODE]
    assert node["listen_port"] == 10042
    # the password is the placeholder the bot substitutes from config/.secret at boot
    assert node["database"]["url"] == ("postgres://dcsserverbot:SECRET@127.0.0.1:5432/"
                                       "dcsserverbot?sslmode=prefer")
    assert node["DCS"]["installation"] == "/tmp/dcs"
    instance = node["instances"]["DCS.openbeta"]
    assert instance["bot_port"] == 6666 and instance["home"].endswith("DCS.openbeta")

    servers = load(tmp_path / "servers.yaml")
    assert servers["Test Server"]["channels"] == {"status": 123, "chat": -1, "admin": -1}

    scheduler = load(tmp_path / "plugins" / "scheduler.yaml")
    assert scheduler["DCS.openbeta"]["schedule"] == {"00-24": "YYYYYYY"}

    cloud = load(tmp_path / "plugins" / "cloud.yaml")
    assert cloud["DEFAULT"]["dcs-ban"] is True and cloud["DEFAULT"]["discord-ban"] is True

    webservice = load(tmp_path / "services" / "webservice.yaml")
    default = webservice["DEFAULT"]
    assert default["frontend"] is True and default["port"] == 9876
    assert default["listen"] == "127.0.0.1"
    assert default["auth"]["breakglass"] == {"enabled": True, "username": "admin",
                                            "password_hash": CONSOLE_HASH}


def test_the_node_document_carries_the_dcs_autoupdate_answer(tmp_path):
    """The stage-9 document is built by the review function itself, not re-shaped here."""
    draft = complete_draft(tmp_path)
    document = review_step._node_document(draft)
    node = document[next(iter(document))]
    # the installer's default rides the draft and reaches the DCS block
    assert node["DCS"]["autoupdate"] is True

    draft.node["dcs_autoupdate"] = False
    document = review_step._node_document(draft)
    node = document[next(iter(document))]
    assert "autoupdate" not in node["DCS"]
    assert node["DCS"]["installation"] == "/tmp/dcs"


@pytest.mark.asyncio
@pytest.mark.parametrize("central_admin, expected_channels",
                         [(True, {"audit": 555, "admin": 999}), (False, {"audit": 555})])
async def test_the_written_bot_yaml_carries_the_admin_channel_only_on_the_central_scope(
        tmp_path, central_admin, expected_channels):
    """The written ``services/bot.yaml`` for BOTH admin scopes: central keeps the admin channel under
    ``channels``; per-server writes no admin channel at all (the installer's "unset" is omitted) —
    the value typed is never read."""
    draft = complete_draft(tmp_path)
    draft.roles = {"roles": {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]},
                   "audit_channel": "555", "admin_channel": "999", "central_admin": central_admin}
    draft.save(tmp_path)
    utils.create_secret_dir(str(tmp_path))
    utils.set_password("database", "db-password", str(tmp_path))
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                           "action": "write"})

    bot = load(tmp_path / "services" / "bot.yaml")
    assert bot["channels"] == expected_channels


@pytest.mark.asyncio
async def test_the_discord_token_goes_to_the_secret_store_and_never_to_a_config_file(
        tmp_path, monkeypatch):
    complete_draft(tmp_path)
    record_writes(monkeypatch)
    app, _ = build(tmp_path)
    from services.webservice.setup import discord as discord_step
    discord_step.token_store(app)[discord_step.TOKEN_KEY] = TOKEN
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                           "action": "write"})

    assert utils.get_password("token", str(tmp_path)) == TOKEN
    for path in tmp_path.rglob("*.yaml"):
        assert TOKEN not in path.read_text(encoding="utf-8"), path


@pytest.mark.asyncio
async def test_the_existing_database_password_reaches_the_store(tmp_path, monkeypatch):
    complete_draft(tmp_path, path=db_step.EXISTING)
    record_writes(monkeypatch)
    app, _ = build(tmp_path)
    seed_secrets(app)
    db_step.secret_store(app)[db_step.BOT_PASSWORD_KEY] = "the-existing-password"
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                           "action": "write"})
    assert utils.get_password("database", str(tmp_path)) == "the-existing-password"
    assert "the-existing-password" not in (tmp_path / "nodes.yaml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_refused_file_leaves_main_yaml_unwritten_and_says_so(tmp_path, monkeypatch):
    complete_draft(tmp_path)
    order = record_writes(monkeypatch, fail_on="nodes.yaml")
    app, _ = build(tmp_path)
    seed_secrets(app)
    finished: list[bool] = []
    app.state.setup_finish = lambda: finished.append(True)

    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        response = await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                                      "action": "write"})

    assert response.status_code == 200
    assert "Only the entries marked written reached the disk" in unescape(response.text)
    assert "not written" in unescape(response.text)
    assert "Write again" in unescape(response.text)
    # the half-written state, stated: every file but the refused one landed, main.yaml never ran
    assert (tmp_path / "services" / "bot.yaml").is_file()
    assert not (tmp_path / "nodes.yaml").exists()
    assert not (tmp_path / "main.yaml").exists()
    assert order == ["bot.yaml", "nodes.yaml", "servers.yaml", "scheduler.yaml", "cloud.yaml",
                     "webservice.yaml"], order
    assert finished == [], "the window must not close on a half-written configuration"
    # the window is still open, and the tour state survives for the retry
    assert setup_boot.should_run_setup(tmp_path) is True
    assert (tmp_path / DRAFT_FILENAME).is_file()
    assert "nodes.yaml" in (tmp_path / DRAFT_FILENAME).read_text(encoding="utf-8")


# ------------------------------------------------------------------------- the closing rule

@pytest.mark.asyncio
async def test_the_window_is_closed_by_main_yaml_and_cannot_be_re_opened(tmp_path, monkeypatch):
    complete_draft(tmp_path)
    record_writes(monkeypatch)
    utils.create_secret_dir(str(tmp_path))
    utils.set_password("database", "db-password", str(tmp_path))
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                           "action": "write"})
        # a reload after the write is the terminal screen, never step 1 again
        reloaded = await client.get(SETUP_PATH)

    assert "Setup is done" in unescape(reloaded.text)
    assert "Which language do you speak?" not in unescape(reloaded.text)

    # the boot rule: no boot may build the setup console now
    assert setup_boot.should_run_setup(tmp_path) is False
    reached: list[bool] = []

    def factory(*args, **kwargs):
        reached.append(True)
        raise AssertionError("the setup application factory must not be reached")

    from services.webservice.setup import app as setup_app
    from services.webservice.setup import service as setup_service
    monkeypatch.setattr(setup_app, "create_setup_app", factory)
    monkeypatch.setattr(setup_service, "create_setup_app", factory)
    with pytest.raises(setup_boot.SetupWindowClosed):
        await setup_boot.start_setup_console(config_dir=tmp_path)
    assert reached == []


@pytest.mark.asyncio
async def test_the_done_screen_names_the_console_url_and_the_run_script(tmp_path):
    complete_draft(tmp_path)
    app, _ = build(tmp_path)
    (tmp_path / "main.yaml").write_text("guild_id: 1\n", encoding="utf-8")
    # the Done screen reads the enable fact from the file the commit writes
    console_dir = tmp_path / "services"
    console_dir.mkdir(parents=True, exist_ok=True)
    (console_dir / "webservice.yaml").write_text(
        "DEFAULT:\n  listen: 127.0.0.1\n  port: 9876\n  frontend: true\n", encoding="utf-8")
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(SETUP_PATH)).text)
    assert "Setup is done" in html
    assert 'href="http://localhost:9876"' in html
    assert "Open the console" in html
    assert "Sign in with the console account you just created." in html
    assert "run.sh" in html or "run.cmd" in html
    assert "Add your DCS servers in the console" in html
    # the console-off sentence is not on the console-on screen
    assert "config/nodes.yaml and config/servers.yaml" not in html


@pytest.mark.asyncio
async def test_the_done_screen_drops_the_console_link_when_the_console_is_off(tmp_path):
    complete_draft(tmp_path)
    app, _ = build(tmp_path)
    (tmp_path / "main.yaml").write_text("guild_id: 1\n", encoding="utf-8")
    console_dir = tmp_path / "services"
    console_dir.mkdir(parents=True, exist_ok=True)
    (console_dir / "webservice.yaml").write_text(
        "DEFAULT:\n  listen: 127.0.0.1\n  port: 9876\n  frontend: false\n", encoding="utf-8")
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(SETUP_PATH)).text)
    assert "Setup is done" in html
    assert "Open the console" not in html
    assert 'href="http://localhost:9876"' not in html
    assert "The web admin console is off" in html
    # a console-less operator is NOT sent to a console that does not exist
    assert "Add your DCS servers in the console" not in html
    assert "To add or change DCS servers, edit config/nodes.yaml and config/servers.yaml." in html


# ------------------------------------------------------------------------------- the walk

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def walk_to_review(client, *, code: str = CODE, listen_port: int | None = None,
                         server_name: str = "Test Server"):
    """The gate and steps 1-9, over whatever transport *client* speaks; returns the Review screen."""
    listen_port = _free_port() if listen_port is None else listen_port

    async def step(number: int, **fields):
        page = await client.get(STEP_PATH, params={"n": str(number)})
        data = {"_csrf_token": token_of(page.text), "n": str(number)}
        data.update(fields)
        return await client.post(STEP_PATH, data=data)

    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    assert (await step(1, bot_language="on")).status_code == 303
    assert (await step(2, autoupdate="on", discord="on")).status_code == 303
    tested = await step(3, action="test", path="create", host="127.0.0.1", port="5432",
                        master_database="postgres", master_user="postgres",
                        master_password="master-pw", database="dcsserverbot", user="dcsserverbot")
    assert tested.status_code == 200
    assert (await step(3, action="continue", path="create", host="127.0.0.1", port="5432",
                       database="dcsserverbot", user="dcsserverbot")).status_code == 303
    # the two DGSA opt-ins are deliberately NOT both on: the write must record what was answered
    assert (await step(4, dcs_choice="0", listen_port=str(listen_port),
                       dgsa_dcs="on")).status_code == 303
    # step 5: Discord, the bot — the guild read must succeed in THIS process before Continue is
    # offered, so the walk presses the step's own Test first (the read is stubbed by stub_the_machine)
    assert (await step(5, action="test", guild_id=str(GUILD), token=TOKEN,
                       owner=str(OWNER))).status_code == 200
    assert (await step(5, action="continue", guild_id=str(GUILD), token=TOKEN,
                       owner=str(OWNER))).status_code == 303
    assert (await step(6, role_0="Admin", role_1="DCS Admin", role_2="@everyone",
                       admin_scope="per_server", audit_channel="",
                       admin_channel="")).status_code == 303
    # step 7: the Web console — the account is mandatory while the console is enabled
    assert (await step(7, console_enabled="on", console_username="admin",
                       console_password="console-password")).status_code == 303
    # step 8: DCS servers — the detected instance's row
    assert (await step(8, row_manage_0="on", row_name_0=server_name, row_status_0="123",
                       row_chat_0="-1", row_admin_0="-1", row_autostart_0="on")).status_code == 303
    # step 9: what do you run — one template ticked, the extension rows left on their defaults
    assert (await step(9, template_0="public-server")).status_code == 303
    review = await client.get(STEP_PATH, params={"n": "10"})
    assert "Write configuration" in unescape(review.text)
    return review


async def walk_the_tour(client, *, code: str = CODE, listen_port: int | None = None,
                        server_name: str = "Test Server"):
    """The gate, steps 1-9 and the commit, over whatever transport *client* speaks."""
    review = await walk_to_review(client, code=code, listen_port=listen_port,
                                  server_name=server_name)
    return await client.post(STEP_PATH, data={"_csrf_token": token_of(review.text), "n": "10",
                                              "action": "write"})


def stub_the_machine(monkeypatch) -> None:
    """The things a test machine cannot provide: the PostgreSQL test, the DCS probes and Discord."""
    monkeypatch.setattr(db_step, "run_test",
                        lambda *a, **k: db_step.TestOutcome(code="ok_created", ok=True,
                                                            version="16.4"))
    monkeypatch.setattr(servers_step, "detect_instances",
                        lambda: {"DCS.openbeta": "Test Server"})

    # no Discord network in the suite: the guild read is stubbed to SUCCEED, so the step's Test passes
    # and the pickers the following steps offer carry data (a FAILING read is exercised in
    # ``test_webui_setup_pickers`` and ``test_webui_setup_discord``)
    async def fake_read(token, guild_id):
        return discord_step.GuildData(guild_id=int(guild_id or 0), guild_name="Test Guild",
                                      channels=(), roles=("Admin", "DCS Admin")), ""

    monkeypatch.setattr(discord_step, "_read_guild", fake_read)


def seed_detection(app) -> None:
    app.state.setup_node_detection = Detection(candidates=(("DCS World", "/tmp/dcs"),),
                                               public_ip=None, upnp=False)


@pytest.mark.asyncio
async def test_the_whole_tour_walks_end_to_end_and_writes_the_configuration(tmp_path, monkeypatch):
    """The gate, steps 1-9, the commit, the handover — over real HTTP, against the shipped app."""
    order = record_writes(monkeypatch)
    stub_the_machine(monkeypatch)
    port = _free_port()

    app, _ = build(tmp_path)
    seed_detection(app)
    finished: list[bool] = []
    app.state.setup_finish = lambda: finished.append(True)

    async with asgi_client(app) as client:
        done = await walk_the_tour(client)

    assert done.status_code == 200 and "Setup is done" in unescape(done.text)
    assert finished == [True]
    assert order[-1] == "main.yaml"
    assert utils.get_password("token", str(tmp_path)) == TOKEN
    assert setup_boot.should_run_setup(tmp_path) is False
    assert not (tmp_path / DRAFT_FILENAME).exists()

    nodes = load(tmp_path / "nodes.yaml")
    assert nodes[NODE]["instances"]["DCS.openbeta"]["bot_port"] == 6666
    assert load(tmp_path / "main.yaml")["guild_id"] == GUILD
    assert load(tmp_path / "servers.yaml")["Test Server"]["channels"]["status"] == 123
    assert load(tmp_path / "services" / "webservice.yaml")["DEFAULT"]["auth"]["breakglass"][
        "username"] == "admin"
    cloud = load(tmp_path / "plugins" / "cloud.yaml")
    assert cloud["DEFAULT"]["dcs-ban"] is True and cloud["DEFAULT"]["discord-ban"] is False


@pytest.mark.asyncio
async def test_a_server_added_on_an_empty_scan_reaches_the_written_documents(tmp_path, monkeypatch):
    """The acceptance for an EMPTY scan, asserted on the documents the commit produced.

    ``detect_instances`` answers nothing, so step 8 renders its one BLANK row; the walk fills THAT
    row in (index 0, the same field names a detected row uses) and the commit writes an installation
    carrying it. The step must also still be on the rail with nothing detected — an operator whose
    DCS has no Saved Games instance yet has no other way in but the command line.
    """
    record_writes(monkeypatch)
    stub_the_machine(monkeypatch)
    monkeypatch.setattr(servers_step, "detect_instances", lambda: {})

    app, _ = build(tmp_path)
    seed_detection(app)
    async with asgi_client(app) as client:
        done = await walk_the_tour(client, server_name="DCS.openbeta")

    assert done.status_code == 200 and "Setup is done" in unescape(done.text)

    # the one name the row asked for IS the instance, so it keys nodes.yaml and scheduler.yaml
    nodes = load(tmp_path / "nodes.yaml")
    instances = nodes[NODE]["instances"]
    assert list(instances) == ["DCS.openbeta"]
    assert instances["DCS.openbeta"]["bot_port"] == 6666          # no nodes.yaml existed: 6666
    assert instances["DCS.openbeta"]["home"].endswith("DCS.openbeta")
    scheduler = load(tmp_path / "plugins" / "scheduler.yaml")
    assert scheduler["DCS.openbeta"]["schedule"] == {"00-24": "YYYYYYY"}
    # and servers.yaml is keyed by the same value, with the channels the row was asked for
    servers = load(tmp_path / "servers.yaml")
    assert servers["DCS.openbeta"]["channels"] == {"status": 123, "chat": -1, "admin": -1}


@pytest.mark.asyncio
async def test_the_setup_console_stops_itself_over_a_real_socket(tmp_path, monkeypatch):
    """The same walk against a real uvicorn server: the console ends the window by itself."""
    import httpx

    stub_the_machine(monkeypatch)
    port = _free_port()
    service = await setup_boot.start_setup_console(node=NODE, config_dir=tmp_path,
                                                   open_browser=False, announce=False, port=port)
    try:
        seed_detection(service.app)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            done = await walk_the_tour(client, code=service.window.code)
        assert done.status_code == 200 and "Setup is done" in unescape(done.text)
        # the response carried the finish callback: the console stops on its own, and the launcher
        # then continues into the normal boot on the configuration just written
        await asyncio.wait_for(service.wait_closed(), timeout=10)
        assert setup_boot.should_run_setup(tmp_path) is False
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_the_written_console_configuration_boots_the_admin_console(tmp_path, monkeypatch):
    """The handover, proven: the console's own shell accepts the written ``webservice.yaml``."""
    import types

    from conftest import create_app, install_shell
    from services.webservice.auth import routes as auth_routes

    stub_the_machine(monkeypatch)
    app, _ = build(tmp_path)
    seed_detection(app)
    async with asgi_client(app) as client:
        assert (await walk_the_tour(client)).status_code == 200

    written = load(tmp_path / "services" / "webservice.yaml")["DEFAULT"]
    normal = create_app()
    install_shell(normal, node=types.SimpleNamespace(master=True, config_dir=str(tmp_path)),
                  config=dict(written))
    manager = normal.state.webui_auth
    assert manager is not None and manager.backend("breakglass") is not None
    async with asgi_client(normal) as client:
        page = await client.get(auth_routes.LOGIN_PATH, headers={"accept": "text/html"})
        assert page.status_code == 200
        # an anonymous caller is still refused: the written console is a normal, gated console
        assert (await client.get("/")).status_code == 403


# ------------------------------------------------------------------------------- i18n

def _review_msgids() -> list[str]:
    """Every ``i18n._``-marked string of the Review step and the Done screen, read from the shipped source."""
    tree = ast.parse((REPO_ROOT / "services" / "webservice" / "setup" / "tour.py").read_text(
        encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not any(name.startswith(("T_REVIEW", "T_DONE")) for name in names):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "_" and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                    and call.args[0].value not in found):
                found.append(call.args[0].value)
    return found


def test_every_review_string_is_shipped_in_every_catalog():
    msgids = _review_msgids()
    assert len(msgids) >= 25, msgids
    for language in CATALOGS:
        catalog = pofile.read_po(open(
            REPO_ROOT / f"locale/{language}/LC_MESSAGES/webui.po", encoding="utf-8"))
        have = {message.id for message in catalog if message.id}
        fuzzy = {message.id for message in catalog if message.id and message.fuzzy}
        for msgid in msgids:
            assert msgid in have, f"'{msgid}' is missing from the {language} catalog"
            assert msgid not in fuzzy, f"'{msgid}' is fuzzy in the {language} catalog"


def _write_funnel_msgids() -> list[str]:
    """Every ``i18n._``-marked string ``review.py`` puts in front of the operator, from the source."""
    tree = ast.parse((REPO_ROOT / "services" / "webservice" / "setup" / "review.py").read_text(
        encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not any(name.startswith("T_") for name in names):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "_" and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                    and call.args[0].value not in found):
                found.append(call.args[0].value)
    return found


def test_every_operator_sentence_a_write_reports_is_translated():
    """The write funnel's notes and errors reach every catalog FILLED, with the same placeholders.

    A catalog entry with an empty ``msgstr`` renders English even though the msgid is present, and the
    values are interpolated AFTER translation, so a line whose holes do not match the msgid's set
    raises into the sentence. Both are asserted for review.py's own strings and the Done screen's
    no-port line.
    """
    msgids = _write_funnel_msgids()
    assert len(msgids) >= 7, msgids
    msgids.append(tour_step.T_DONE_SRS_PORT)
    placeholder = re.compile(r"\{(\w+)\}")
    for language in CATALOGS:
        catalog = pofile.read_po(open(
            REPO_ROOT / f"locale/{language}/LC_MESSAGES/webui.po", encoding="utf-8"))
        by_id = {message.id: message for message in catalog if message.id}
        for msgid in msgids:
            message = by_id.get(msgid)
            assert message is not None, f"'{msgid}' is missing from the {language} catalog"
            assert not message.fuzzy, f"'{msgid}' is fuzzy in the {language} catalog"
            translation = message.string if isinstance(message.string, str) else ""
            assert translation, f"'{msgid}' has an EMPTY msgstr in the {language} catalog"
            assert set(placeholder.findall(translation)) == set(placeholder.findall(msgid)), \
                f"'{msgid}' has different placeholders in the {language} catalog"


def test_the_review_strings_keep_their_placeholders_and_literals():
    catalog = pofile.read_po(open(
        REPO_ROOT / "locale/de/LC_MESSAGES/webui.po", encoding="utf-8"))
    by_id = {message.id: message.string for message in catalog if message.id}
    assert "{url}" in by_id["The console will be at {url} once it is up."]
    assert "{script}" in by_id["If it is not running, start it with {script}."]
    assert "config/.secret/" in by_id["the PostgreSQL master password is stored in config/.secret/"]
    assert "config/.secret/" in by_id["the bot's database password is stored in config/.secret/"]


@pytest.mark.asyncio
async def test_the_review_screen_is_translated_when_the_tour_language_is_german(tmp_path):
    complete_draft(tmp_path)
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        await client.get(LANGUAGE_PATH, params={"lang": "de", "next": f"{STEP_PATH}?n=10"})
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "Bereits erledigt" in html
    assert "Konfiguration schreiben" in html


# ---------------------------------------------------- the required secrets (a mid-tour restart)

def _fake_app():
    """An object with the ``.state`` the two process stores hang off (no real application)."""
    return types.SimpleNamespace(state=types.SimpleNamespace())


def test_required_secrets_follow_the_discord_and_database_answers():
    """The token is required with Discord on; the database password only on the existing path."""
    discord_create = complete_on_fake(discord=True, path=db_step.CREATE)
    discord_existing = complete_on_fake(discord=True, path=db_step.EXISTING)
    community_create = complete_on_fake(discord=False, path=db_step.CREATE)

    assert review_step.required_secrets(discord_create) == (review_step.TOKEN_SECRET,)
    assert review_step.required_secrets(discord_existing) == (review_step.TOKEN_SECRET,
                                                              review_step.DATABASE_SECRET)
    assert review_step.required_secrets(community_create) == ()

    app = _fake_app()
    assert [t.key for t in review_step.missing_secrets(discord_existing, app=app)] == \
        ["token", "database_password"]
    seed_secrets(app)
    assert [t.key for t in review_step.missing_secrets(discord_existing, app=app)] == \
        ["database_password"]
    seed_secrets(app, database="pw")
    assert review_step.missing_secrets(discord_existing, app=app) == []


def complete_on_fake(*, discord: bool, path: str) -> SetupDraft:
    draft = SetupDraft(step=9, discord=discord)
    draft.database = {"path": path}
    return draft


@pytest.mark.asyncio
async def test_a_restart_before_step_9_refuses_to_write_and_names_the_step(tmp_path, monkeypatch):
    """A mid-tour restart drops the in-memory token; the commit must refuse, not close the window.

    The two phases are two SEPARATE applications over the same config directory — the process stores
    are per application, so a fresh application is exactly what a console restart leaves behind.
    """
    stub_the_machine(monkeypatch)

    # phase A — the operator walks to the Review step with the token typed into THIS process
    app_a, _ = build(tmp_path)
    seed_detection(app_a)
    finished_a: list[bool] = []
    app_a.state.setup_finish = lambda: finished_a.append(True)
    async with asgi_client(app_a) as client:
        await walk_to_review(client)
    assert finished_a == []
    assert not (tmp_path / "main.yaml").exists()

    # phase B — a restart: a fresh application, its process stores empty, the same directory
    app_b, _ = build(tmp_path)
    finished_b: list[bool] = []
    app_b.state.setup_finish = lambda: finished_b.append(True)
    async with asgi_client(app_b) as client:
        await enter(client)
        csrf = token_of((await client.get(STEP_PATH, params={"n": "8"})).text)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)

        # the refusal: the missing secret named, the step to re-enter named, no commit offered
        assert "A restart dropped a secret" in html
        assert "config/.secret/token.pkl" in html
        assert "step 5" in html
        assert "Write configuration" not in html
        # the truth: what IS held is still shown beside what is missing
        assert "config/nodes.yaml" in html and "Missing — enter again" in html

        # a replayed commit writes nothing and does not close the window
        write = await client.post(STEP_PATH, data={"_csrf_token": csrf, "n": "10",
                                                   "action": "write"})
        assert write.status_code == 200
        assert "A restart dropped a secret" in unescape(write.text)
        assert not (tmp_path / "main.yaml").exists()
        assert not (tmp_path / "nodes.yaml").exists()
        assert not (tmp_path / ".secret" / "token.pkl").exists()
        assert setup_boot.should_run_setup(tmp_path) is True
        assert finished_b == [], "the window must not close on the refused commit"

        # recovery: re-enter the token on step 5 — a restart holds no guild read, so the step's own
        # Test must pass again — then the commit succeeds
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        fields = {"_csrf_token": token_of(page), "n": "5", "guild_id": str(GUILD),
                  "owner": str(OWNER), "token": TOKEN}
        tested = await client.post(STEP_PATH, data={**fields, "action": "test"})
        assert tested.status_code == 200
        fields["_csrf_token"] = token_of(tested.text)
        recovered = await client.post(STEP_PATH, data={**fields, "action": "continue"})
        assert recovered.status_code == 303
        review = await client.get(STEP_PATH, params={"n": "10"})
        assert "Write configuration" in unescape(review.text)
        done = await client.post(STEP_PATH, data={"_csrf_token": token_of(review.text), "n": "10",
                                                  "action": "write"})

    # the recovery did write the whole configuration, token included
    assert "Setup is done" in unescape(done.text)
    assert utils.get_password("token", str(tmp_path)) == TOKEN
    assert setup_boot.should_run_setup(tmp_path) is False
    assert finished_b == [True]


@pytest.mark.asyncio
async def test_the_existing_database_path_refuses_after_a_restart_too(tmp_path):
    """The existing path's read-only test stores nothing, so its password is lost on restart too."""
    complete_draft(tmp_path, path=db_step.EXISTING)

    app_a, _ = build(tmp_path)
    seed_secrets(app_a, database="the-existing-password")
    assert review_step.missing_secrets(SetupDraft.load(tmp_path), app=app_a) == []

    app_b, _ = build(tmp_path)
    async with asgi_client(app_b) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "Write configuration" not in html
    assert "config/.secret/token.pkl" in html and "config/.secret/database.pkl" in html
    assert "step 5" in html and "step 3" in html


@pytest.mark.asyncio
async def test_a_restart_then_re_entering_the_token_leaves_the_console_discord_login_off(
        tmp_path, monkeypatch):
    """The mid-tour restart: the typed client secret is gone (process memory, like the token), and
    nothing gates it. The operator re-enters the token, leaves the secret empty, commits — the Review
    screen must SAY the console's Discord login stays off, and the written config must carry none."""
    stub_the_machine(monkeypatch)

    # phase A — the walk with the token typed into THIS process and no client secret ever given
    app_a, _ = build(tmp_path)
    seed_detection(app_a)
    async with asgi_client(app_a) as client:
        await walk_to_review(client)
    assert not (tmp_path / "main.yaml").exists()

    # phase B — a restart: a fresh application, its process stores empty, the same directory
    app_b, _ = build(tmp_path)
    async with asgi_client(app_b) as client:
        await enter(client)
        # the token is gone: the commit is refused and step 5 named
        assert "A restart dropped a secret" in unescape(
            (await client.get(STEP_PATH, params={"n": "10"})).text)

        # recovery: re-enter the token on step 5 (Test then Continue), leave the secret empty
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        fields = {"_csrf_token": token_of(page), "n": "5", "guild_id": str(GUILD),
                  "owner": str(OWNER), "token": TOKEN}
        tested = await client.post(STEP_PATH, data={**fields, "action": "test"})
        assert tested.status_code == 200
        fields["_csrf_token"] = token_of(tested.text)
        assert (await client.post(STEP_PATH, data={**fields, "action": "continue"})).status_code == 303

        review = await client.get(STEP_PATH, params={"n": "10"})
        html = unescape(review.text)
        assert "the console's Discord login — no client secret is held, so it stays off" in html
        assert "Write configuration" in html
        done = await client.post(STEP_PATH, data={"_csrf_token": token_of(review.text), "n": "10",
                                                  "action": "write"})

    assert "Setup is done" in unescape(done.text)
    assert utils.get_password("token", str(tmp_path)) == TOKEN
    auth = load(tmp_path / "services" / "webservice.yaml")["DEFAULT"]["auth"]
    assert "discord" not in auth, "the console's Discord login was written with no secret held"
    assert auth["breakglass"]["username"] == "admin"


# ------------------------------------------------------- the closing rule on the POST write route

@pytest.mark.asyncio
async def test_a_post_replayed_after_the_commit_does_not_reopen_the_tour(tmp_path, monkeypatch):
    """The file check render_root/render_step carry is enforced on the ONE write route too."""
    stub_the_machine(monkeypatch)
    app, _ = build(tmp_path)
    seed_detection(app)
    async with asgi_client(app) as client:
        review = await walk_to_review(client)
        csrf = token_of(review.text)
        done = await client.post(STEP_PATH, data={"_csrf_token": csrf, "n": "10", "action": "write"})
        assert "Setup is done" in unescape(done.text)
        assert not (tmp_path / DRAFT_FILENAME).exists()

        # a POST replayed after the commit (a stale form, a back button): the state is spent
        replay = await client.post(STEP_PATH, data={"_csrf_token": csrf, "n": "1",
                                                    "bot_language": "on"})

    assert replay.status_code == 200
    assert "Setup is done" in unescape(replay.text)
    assert not (tmp_path / DRAFT_FILENAME).exists(), "the replay must not recreate the draft"
    assert not (tmp_path / (DRAFT_FILENAME + ".bak")).exists()
    assert (tmp_path / "main.yaml").is_file()


# ----------------------------------------------------- a validator exception is not a raw 500

@pytest.mark.asyncio
async def test_a_validator_exception_is_a_classified_failure_not_a_500(tmp_path, monkeypatch):
    """A non-pykwalify exception mid-commit is per-target; it must not escape as a bare 500."""
    complete_draft(tmp_path)
    order = record_writes(monkeypatch, explode_on="servers.yaml")
    utils.create_secret_dir(str(tmp_path))
    utils.set_password("database", "db-password", str(tmp_path))
    app, _ = build(tmp_path)
    seed_secrets(app)
    finished: list[bool] = []
    app.state.setup_finish = lambda: finished.append(True)

    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        response = await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                                      "action": "write"})

    assert response.status_code == 200, "a validator exception must not become a raw 500"
    body = unescape(response.text)
    assert "Only the entries marked written reached the disk" in body
    # no traceback on the page — only the operator-facing reason
    assert "Traceback" not in body and "AttributeError" not in body
    assert "config/servers.yaml" in body
    assert "not written" in body
    # the failing target was classified, and the targets AFTER it still ran
    assert order == ["bot.yaml", "nodes.yaml", "servers.yaml", "scheduler.yaml", "cloud.yaml",
                     "webservice.yaml"], order
    assert not (tmp_path / "servers.yaml").exists()
    assert (tmp_path / "plugins" / "scheduler.yaml").is_file()
    assert (tmp_path / "services" / "webservice.yaml").is_file()
    # main.yaml is held back, the window stays open
    assert not (tmp_path / "main.yaml").exists()
    assert setup_boot.should_run_setup(tmp_path) is True
    assert finished == []
    assert (tmp_path / DRAFT_FILENAME).is_file()
