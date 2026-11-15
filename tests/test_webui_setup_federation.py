"""Step 3's optional cluster database — the ``Federation support`` sibling block.

The feature is exercised the way the tour ships it:

* the VIEW over a real ASGI request against the shipped setup application (the default-OFF screen,
  the revealed second block, the distinct field names, the scoped re-submit, the Continue gate);
* the CLASSIFICATION through :func:`services.webservice.setup.database.run_test` with a fake
  connection, so the cluster block's create path is proven to write ``config/.secret/clusterdb.pkl``
  and never the node's own ``database.pkl``;
* the WRITE through the real Review commit — ``main.yaml``'s ``database`` block, the cluster secret,
  and the default-OFF ``main.yaml`` proven BYTE-IDENTICAL to today's document.

UNVERIFIED on this machine: a real PostgreSQL (the DDL runs against a stub, as in
``test_webui_setup_database.py``) and therefore a real federation boot. The operator must point the
cluster block at a second, live database and confirm the bot logs "Federation detected." — see the
card's report.
"""
from __future__ import annotations

import re
import types
from html import unescape
from urllib.parse import quote, urlparse

import psycopg
import pytest
from ruamel.yaml import YAML

from conftest import asgi_client
from core import utils
from core.utils import configwrite, setup_config
from services.webservice.setup import database as db_step
from services.webservice.setup import review as review_step
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import SetupDraft, draft_path
from services.webservice.setup.tour import STEP_PATH
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')
NODE = "TestNode"
GUILD = 111111111111111111
OWNER = 222222222222222222
TOKEN = "discord-bot-token-not-a-real-one"
CONSOLE_HASH = "pbkdf2_sha256$600000$cafe$beef"


@pytest.fixture(autouse=True)
def _node_arguments(monkeypatch):
    """The schema validator reads ``COMMAND_LINE_ARGS.node``; pytest is not ``run.py``."""
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
    assert found, "the step form carries no CSRF token"
    return found.group(1)


def seed_step_three(config_dir, **database):
    draft = SetupDraft(step=2)
    draft.database = dict(database)
    draft.save(config_dir)


def form(token: str, **fields) -> dict:
    data = {"_csrf_token": token, "n": "3"}
    data.update(fields)
    return data


def load(path) -> dict:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------- the default OFF

@pytest.mark.asyncio
async def test_the_federation_box_is_present_unchecked_and_reveals_nothing(tmp_path):
    """Default OFF: the checkbox exists, unticked, and the second block is not on the screen."""
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    assert 'name="federation"' in html
    assert 'name="federation" checked' not in html
    assert "Federation support" in html
    # the second block is absent: no cluster field, no cluster radio
    for name in ("cluster_path", "cluster_host", "cluster_port", "cluster_database", "cluster_user",
                 "cluster_master_password"):
        assert f'name="{name}"' not in html, name
    # and the scoped listener for the second block is not emitted either
    assert 'input[name="cluster_path"]' not in html
    # today's first block is untouched
    assert 'name="path" value="create" checked' in html
    assert 'name="host"' in html


@pytest.mark.asyncio
async def test_the_default_path_adds_nothing_to_the_draft(tmp_path):
    """The default path's draft carries no ``cluster``/``federation`` key at all."""
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        step_one = (await client.get(STEP_PATH, params={"n": "1"})).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(step_one), "n": "1"})
        step_two = (await client.get(STEP_PATH, params={"n": "2"})).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(step_two), "n": "2",
                                           "autoupdate": "on", "discord": "on"})
        step_three = (await client.get(STEP_PATH, params={"n": "3"})).text
        await client.post(STEP_PATH, data=form(
            token_of(step_three), path="create", host="127.0.0.1", port="5432",
            master_database="postgres", master_user="postgres", master_password="pw",
            database="dcsserverbot", user="dcsserverbot", action="switch", stay="1"))
    document = SetupDraft.load(tmp_path).database
    assert db_step.CLUSTER_KEY not in document
    assert db_step.FEDERATION_KEY not in document


# -------------------------------------------------------------------------- the revealed block

@pytest.mark.asyncio
async def test_ticking_federation_reveals_the_second_block_with_distinct_names(tmp_path):
    """The tick reveals the cluster block; its fields and radio are prefixed, so nothing collides."""
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        revealed = await client.post(STEP_PATH, data=form(
            token_of(html), action="federation", stay="1", federation="on"))
        body = revealed.text
    assert 'name="federation" checked' in body
    for name in ("cluster_path", "cluster_host", "cluster_port", "cluster_master_database",
                 "cluster_master_user", "cluster_master_password", "cluster_database",
                 "cluster_user"):
        assert f'name="{name}"' in body, name
    # both blocks' radios are on the page, each named for its own block
    assert body.count('type="radio" name="path"') == 2
    assert body.count('type="radio" name="cluster_path"') == 2
    # the scoped re-submit for the SECOND block is emitted (and points at its own action)
    assert 'input[name="cluster_path"]' in body
    assert '"cluster_switch"' in body
    assert "How should the bot get the cluster database?" in body


@pytest.mark.asyncio
async def test_the_cluster_block_inherits_the_create_path_fields(tmp_path):
    """The second block is the SAME renderer: the create path shows the master fields too."""
    seed_step_three(tmp_path, federation=True, cluster={"path": "create"})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    assert "The bot will create the database dcsserverbot and the user dcsserverbot." in html
    # the derived sentence and the Test label appear for BOTH blocks
    assert html.count("The bot will create the database") == 2
    assert html.count("Test connection and create") == 2


@pytest.mark.asyncio
async def test_the_cluster_block_inherits_the_existing_path_fields(tmp_path):
    seed_step_three(tmp_path, path="existing", federation=True, cluster={"path": "existing"})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    assert 'name="cluster_password"' in html
    assert 'name="cluster_master_password"' not in html
    # both existing blocks carry the plain "Test connection" label
    assert html.count("Test connection</button>") == 2


# ----------------------------------------------------------------------------- the Continue gate

def _fake_run_test(monkeypatch, *, code="ok_created", ok=True, version="16.4", data=None):
    """A stub that records the secret key it was handed, so the block's own store is proven."""
    seen: list[dict] = []

    def _run(answers, *, master_password, bot_password, reset_user=False, config_dir):
        seen.append({"secret_key": answers.secret_key, "database": answers.database,
                     "master_password": master_password, "bot_password": bot_password,
                     "reset_user": reset_user})
        return db_step.TestOutcome(code=code, ok=ok, version=version, data=dict(data or {}))

    monkeypatch.setattr(db_step, "run_test", _run)
    return seen


@pytest.mark.asyncio
async def test_the_cluster_test_runs_the_same_machinery_with_its_own_secret_key(tmp_path, monkeypatch):
    seen = _fake_run_test(monkeypatch)
    seed_step_three(tmp_path, federation=True, cluster={"path": "existing"})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        # the local block first
        await client.post(STEP_PATH, data=form(
            token_of(html), action="test", path="create", host="127.0.0.1", port="5432",
            master_database="postgres", master_user="postgres", master_password="local-master",
            database="dcsserverbot", user="dcsserverbot"))
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        await client.post(STEP_PATH, data=form(
            token_of(html), action="cluster_test", federation="on", cluster_path="existing",
            cluster_host="cluster.example", cluster_port="5433", cluster_database="federation",
            cluster_user="feduser", cluster_master_password="cluster-master",
            cluster_password="cluster-bot"))
    assert [entry["secret_key"] for entry in seen] == [db_step.DATABASE_SECRET_KEY,
                                                       db_step.CLUSTER_SECRET_KEY]
    cluster = seen[1]
    assert cluster["database"] == "federation"
    assert cluster["master_password"] == "cluster-master"
    assert cluster["bot_password"] == "cluster-bot"


@pytest.mark.asyncio
async def test_continue_stays_closed_until_the_ticked_cluster_block_has_passed(tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="ok_created", ok=True, version="16.4"))
    seed_step_three(tmp_path, federation=True, cluster={"path": "create"})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        # the cluster block's own Test first: the local block is still untested, so the gate is shut
        cluster = await client.post(STEP_PATH, data=form(
            token_of(html), action="cluster_test", federation="on", cluster_path="create",
            cluster_host="cluster.example", cluster_port="5432", cluster_master_database="postgres",
            cluster_master_user="postgres", cluster_master_password="pw",
            cluster_database="federation", cluster_user="feduser"))
        assert 'value="continue" disabled' in cluster.text
        # now the local block passes, and with the tick kept both blocks verify: the gate opens
        local = await client.post(STEP_PATH, data=form(
            token_of(cluster.text), action="test", federation="on", path="create",
            host="127.0.0.1", port="5432", master_database="postgres", master_user="postgres",
            master_password="pw", database="dcsserverbot", user="dcsserverbot"))
        assert 'value="continue" disabled' not in local.text


@pytest.mark.asyncio
async def test_continue_without_a_test_is_refused_with_the_tick_kept(tmp_path):
    seed_step_three(tmp_path, federation=True, cluster={"path": "create"})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        refused = await client.post(STEP_PATH, data=form(
            token_of(html), action="continue", federation="on"))
    assert refused.status_code == 200
    assert "Test the connection before you continue." in refused.text
    assert SetupDraft.load(tmp_path).step == 2


@pytest.mark.asyncio
async def test_editing_a_cluster_field_after_its_test_re_closes_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="ok_created", ok=True, version="16.4"))
    seed_step_three(tmp_path, federation=True, cluster={"path": "create"})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        await client.post(STEP_PATH, data=form(
            token_of(html), action="test", host="127.0.0.1", port="5432",
            master_database="postgres", master_user="postgres", master_password="pw",
            database="dcsserverbot", user="dcsserverbot"))
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(html), action="cluster_test", federation="on", cluster_path="create",
            cluster_host="cluster.example", cluster_port="5432", cluster_master_database="postgres",
            cluster_master_user="postgres", cluster_master_password="pw",
            cluster_database="federation", cluster_user="feduser"))
        assert 'value="continue" disabled' not in tested.text
        # a DIFFERENT cluster host with the same green light re-closes the gate
        moved = await client.post(STEP_PATH, data=form(
            token_of(tested.text), action="continue", federation="on", cluster_path="create",
            cluster_host="otherhost", cluster_port="5432", cluster_master_password="",
            cluster_database="federation", cluster_user="feduser"))
    assert 'value="continue" disabled' in moved.text


@pytest.mark.asyncio
async def test_the_cluster_block_keeps_the_typed_values_and_never_renders_its_password(
        tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="unreachable", ok=False, data={"host": "cluster.example", "port": 5433}))
    seed_step_three(tmp_path, federation=True, cluster={"path": "existing"})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        failed = await client.post(STEP_PATH, data=form(
            token_of(html), action="cluster_test", federation="on", cluster_path="existing",
            cluster_host="cluster.example", cluster_port="5433", cluster_database="federation",
            cluster_user="feduser", cluster_master_password="cluster-master",
            cluster_password="cluster-bot"))
        body = failed.text
    # the classified sentence, never a driver string
    assert "nothing is answering on cluster.example:5433" in body
    assert "psycopg" not in body and "OperationalError" not in body
    # typed non-secrets survive; the password never renders back
    assert 'value="cluster.example"' in body and 'value="federation"' in body
    assert "cluster-bot" not in body and "cluster-master" not in body
    assert 'name="cluster_password" value=""' in body
    raw = draft_path(tmp_path).read_text(encoding="utf-8")
    assert "cluster-bot" not in raw and "cluster-master" not in raw


# ------------------------------------------------------------------------ the same-name warning

@pytest.mark.asyncio
async def test_the_same_database_name_is_an_amber_warning_that_lets_it_through(tmp_path, monkeypatch):
    """A cluster database named like this node's own warns and PASSES the gate (warn-and-allow)."""
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="ok_created", ok=True, version="16.4"))
    seed_step_three(tmp_path, path="create", database="dcsserverbot",
                    cluster={"path": "create", "database": "dcsserverbot"},
                    federation=True)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        await client.post(STEP_PATH, data=form(
            token_of(html), action="test", path="create", master_password="pw",
            database="dcsserverbot", user="dcsserverbot"))
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        warned = await client.post(STEP_PATH, data=form(
            token_of(html), action="cluster_test", federation="on", cluster_path="create",
            cluster_master_password="pw", cluster_database="dcsserverbot", cluster_user="feduser"))
        body = warned.text
    # the amber warning rides on the notice, and the gate stays open
    assert "has the same name as this installation" in body
    assert "Connected to dcsserverbot." in body
    assert 'class="notice warn"' in body
    assert 'value="continue" disabled' not in body
    document = SetupDraft.load(tmp_path).database
    assert document[db_step.CLUSTER_KEY]["last"]["code"] == db_step.SAME_DATABASE_CODE


# ------------------------------------------------------------------------------------ the write

def complete_draft(config_dir, *, federation: bool = False, same_name: bool = False) -> SetupDraft:
    """A finished draft at step 9, optionally with a verified cluster block."""
    draft = SetupDraft(step=9, language="en", bot_language=True, autoupdate=True, discord=True)
    draft.database = {"path": "create", "host": "127.0.0.1", "port": 5432,
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
    draft.community = {}
    draft.console = {"username": "admin", "password_hash": CONSOLE_HASH}
    if federation:
        draft.database[db_step.FEDERATION_KEY] = True
        draft.database[db_step.CLUSTER_KEY] = {
            "path": "existing", "host": "cluster.example", "port": 5433,
            "master_database": "postgres", "master_user": "postgres",
            "database": "dcsserverbot" if same_name else "federation",
            "user": "feduser", "user_password_attempts": 0}
    draft.save(config_dir)
    return draft


def seed_token(app) -> None:
    """Put the Discord bot token in the process store (what step 5 would have typed)."""
    from services.webservice.setup import discord as discord_step
    discord_step.token_store(app)[discord_step.TOKEN_KEY] = TOKEN


def verify_cluster(config_dir, app, *, password: str = "cluster-bot") -> None:
    """Seed the token and the cluster password and stamp the cluster's matching fingerprint."""
    seed_token(app)
    db_step.secret_store(app, db_step.CLUSTER_SECRET_KEY)[db_step.BOT_PASSWORD_KEY] = password
    draft = SetupDraft.load(config_dir)
    answers = db_step.answers_from_mapping(draft.database.get(db_step.CLUSTER_KEY),
                                           secret_key=db_step.CLUSTER_SECRET_KEY)
    block = dict(draft.database[db_step.CLUSTER_KEY])
    block["verified"] = True
    block["signature"] = db_step.signature(answers, "", password)
    draft.database[db_step.CLUSTER_KEY] = block
    draft.save(config_dir)


@pytest.mark.asyncio
async def test_with_the_box_off_main_yaml_is_byte_identical_to_today(tmp_path):
    """The default path writes EXACTLY today's document — proven byte for byte."""
    complete_draft(tmp_path, federation=False)
    app, _ = build(tmp_path)
    seed_token(app)
    utils.create_secret_dir(str(tmp_path))
    utils.set_password("database", "db-password", str(tmp_path))
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        done = await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                                  "action": "write"})
        assert "Setup is done" in unescape(done.text)

    written = (tmp_path / "main.yaml").read_bytes()
    expected_path = tmp_path / "expected-main.yaml"
    configwrite.save_config_file(expected_path, setup_config.build_main(GUILD, True, "en"),
                                 schemas=None)
    assert written == expected_path.read_bytes(), "the default path changed main.yaml"
    assert "database" not in load(tmp_path / "main.yaml")
    # and no cluster secret was written
    assert not (tmp_path / ".secret" / "clusterdb.pkl").exists()


@pytest.mark.asyncio
async def test_the_written_main_yaml_carries_only_the_cluster_url(tmp_path):
    complete_draft(tmp_path, federation=True)
    app, _ = build(tmp_path)
    verify_cluster(tmp_path, app, password="cluster-bot")
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                           "action": "write"})

    main = load(tmp_path / "main.yaml")
    # the URL carries the literal placeholder the bot substitutes from config/.secret at boot
    assert main["database"] == {"url": "postgres://feduser:SECRET@cluster.example:5433/"
                                       "federation?sslmode=prefer"}
    # nothing else: no pool keys, no ssl choice, no cluster name
    assert set(main["database"]) == {"url"}
    assert main["guild_id"] == GUILD
    # the cluster secret went to the cluster key, and the node's own password is untouched
    assert utils.get_password("clusterdb", str(tmp_path)) == "cluster-bot"
    assert "cluster-bot" not in (tmp_path / "main.yaml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_the_review_screen_names_the_cluster_database_only_when_it_is_written(tmp_path):
    # off: no cluster line, no cluster secret row
    complete_draft(tmp_path, federation=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        off = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "config/.secret/clusterdb.pkl" not in off
    assert "the cluster database" not in off

    # on: the cluster secret row and the main.yaml detail name it
    complete_draft(tmp_path, federation=True)
    app, _ = build(tmp_path)
    verify_cluster(tmp_path, app)
    async with asgi_client(app) as client:
        await enter(client)
        on = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "config/.secret/clusterdb.pkl" in on
    assert "the cluster database's password" in on
    assert "guild, language, autoupdate, the cluster database" in on


@pytest.mark.asyncio
async def test_the_review_screen_says_plainly_when_it_is_not_a_federation(tmp_path):
    complete_draft(tmp_path, federation=True, same_name=True)
    app, _ = build(tmp_path)
    verify_cluster(tmp_path, app)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "NOT a federation (same database name)" in html


@pytest.mark.asyncio
async def test_unticking_after_typing_keeps_the_values_but_writes_nothing(tmp_path, monkeypatch):
    """Unticking keeps what was typed (in the draft and the store) but the commit writes nothing."""
    seed_step_three(tmp_path, path="create", database="dcsserverbot", user="dcsserverbot")
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        # tick, type the cluster block, and untick again
        await client.post(STEP_PATH, data=form(
            token_of(html), action="federation", stay="1", federation="on"))
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        typed = await client.post(STEP_PATH, data=form(
            token_of(html), action="federation", stay="1", federation="on", cluster_path="existing",
            cluster_host="cluster.example", cluster_port="5433", cluster_database="federation",
            cluster_user="feduser", cluster_password="cluster-bot"))
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        # the block is revealed and the typed values are rendered
        assert 'value="cluster.example"' in html
        unticked = await client.post(STEP_PATH, data=form(
            token_of(html), action="federation", stay="1"))
        assert 'name="federation" checked' not in unticked.text
        assert 'name="cluster_path"' not in unticked.text

    document = SetupDraft.load(tmp_path).database
    # the values are KEPT, the tick is gone
    assert db_step.FEDERATION_KEY not in document
    assert document[db_step.CLUSTER_KEY]["host"] == "cluster.example"
    assert document[db_step.CLUSTER_KEY]["database"] == "federation"
    # the password is still in this process's store (not on disk)
    assert db_step.secret_store(app, db_step.CLUSTER_SECRET_KEY)[db_step.BOT_PASSWORD_KEY] == \
        "cluster-bot"

    # and a commit on the unticked draft writes neither the block nor the cluster secret
    loaded = SetupDraft.load(tmp_path)
    assert review_step.cluster_written(loaded, app=app) is False
    assert "clusterdb_password" not in [t.key for t in review_step.required_secrets(loaded)]
    # complete the draft so a plan can be built, then prove the commit carries no cluster database
    loaded.discord_bot = {"guild_id": GUILD, "owner": OWNER, "privileged_intents": True}
    loaded.roles = {"roles": {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]},
                    "central_admin": False, "audit_channel": "", "admin_channel": ""}
    loaded.node = {"listen_port": 10042, "use_upnp": False, "no_dcs": False,
                   "dcs_installation": "/tmp/dcs", "public_ip": None,
                   "dgsa_dcs": False, "dgsa_discord": False}
    loaded.servers = {"scanned": True, "detected": {}, "rows": {}}
    loaded.console = {"username": "admin", "password_hash": CONSOLE_HASH}
    loaded.step = 8
    plan = review_step.plan(loaded, app=app)
    assert "clusterdb_password" not in {item.target.key for item in plan}
    assert "database" not in plan[-1].document


@pytest.mark.asyncio
async def test_a_cluster_block_with_the_tick_off_is_never_written(tmp_path):
    """A typed cluster block whose tick was removed: kept in the draft, named as NOT written, and
    nothing of it reaches the configuration."""
    draft = complete_draft(tmp_path, federation=False)
    draft.database[db_step.CLUSTER_KEY] = {"path": "existing", "host": "cluster.example",
                                           "port": 5433, "database": "federation", "user": "feduser",
                                           "verified": True, "signature": "stale"}
    draft.save(tmp_path)
    reloaded = SetupDraft.load(tmp_path)
    assert db_step.FEDERATION_KEY not in reloaded.database
    assert db_step.CLUSTER_KEY in reloaded.database            # the answers are kept

    app, _ = build(tmp_path)
    seed_token(app)
    db_step.secret_store(app, db_step.CLUSTER_SECRET_KEY)[db_step.BOT_PASSWORD_KEY] = "cluster-bot"
    async with asgi_client(app) as client:
        await enter(client)
        screen = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        # named in the "not written" list, with no cluster secret row in the "will be written" one
        assert "Federation support is off" in screen
        assert "the cluster database's password" not in screen
        done = await client.post(STEP_PATH, data={"_csrf_token": token_of(screen), "n": "10",
                                                  "action": "write"})

    assert "Setup is done" in unescape(done.text)
    assert "database" not in load(tmp_path / "main.yaml")
    assert not (tmp_path / ".secret" / "clusterdb.pkl").exists()


# -------------------------------------------------------------------------- the process store

def test_the_two_blocks_get_separate_password_slots(tmp_path):
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    db_step.secret_store(app)[db_step.BOT_PASSWORD_KEY] = "node-pw"
    db_step.secret_store(app, db_step.CLUSTER_SECRET_KEY)[db_step.BOT_PASSWORD_KEY] = "cluster-pw"
    assert db_step.secret_store(app)[db_step.BOT_PASSWORD_KEY] == "node-pw"
    assert db_step.secret_store(app, db_step.CLUSTER_SECRET_KEY)[db_step.BOT_PASSWORD_KEY] == \
        "cluster-pw"


class FakeConn:
    def close(self):
        pass


def _stub_shared(monkeypatch, *, version="16.2"):
    monkeypatch.setattr(db_step, "_connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(db_step.db_setup, "get_server_version", lambda conn: version)
    monkeypatch.setattr(db_step.db_setup, "is_version_outdated", lambda v: False)
    monkeypatch.setattr(db_step.db_setup, "create_user", lambda conn, user, pw: None)
    monkeypatch.setattr(db_step, "_database_exists", lambda conn, db: False)
    monkeypatch.setattr(db_step.db_setup, "create_database", lambda conn, db, owner: True)


def test_the_cluster_create_path_writes_the_clusterdb_secret_and_not_the_node_one(
        tmp_path, monkeypatch):
    """The SAME run_test writes ``clusterdb.pkl`` for the cluster block, never ``database.pkl``."""
    _stub_shared(monkeypatch)
    answers = db_step.answers_from_mapping({"path": "create", "database": "federation"},
                                           secret_key=db_step.CLUSTER_SECRET_KEY)
    outcome = db_step.run_test(answers, master_password="pw", bot_password="", config_dir=tmp_path)
    assert outcome.code == "ok_created" and outcome.ok
    assert (tmp_path / ".secret" / "clusterdb.pkl").exists()
    assert not (tmp_path / ".secret" / "database.pkl").exists()
    assert utils.get_password("clusterdb", str(tmp_path))


# ------------------------------------------------------------------------ over a real socket

def _stub_the_machine(monkeypatch) -> None:
    """The things a test machine cannot provide: the PostgreSQL test and the machine's DCS probes."""
    from services.webservice.setup import discord as discord_step
    from services.webservice.setup import servers as servers_step

    monkeypatch.setattr(db_step, "run_test",
                        lambda *a, **k: db_step.TestOutcome(code="ok_existing_data", ok=True,
                                                            version="16.4", data={"count": 3}))
    monkeypatch.setattr(servers_step, "detect_instances",
                        lambda: {"DCS.openbeta": "Test Server"})

    # the guild read is stubbed to SUCCEED: step 5's own Test must pass before the walk can continue
    async def fake_read(token, guild_id):
        return discord_step.GuildData(guild_id=int(guild_id or 0), guild_name="Test Guild",
                                      channels=(), roles=("Admin",)), ""

    monkeypatch.setattr(discord_step, "_read_guild", fake_read)


def _free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.asyncio
async def test_the_ticked_federation_block_commits_over_a_real_socket(tmp_path, monkeypatch):
    """The whole tour — steps 1-8 with the tick and BOTH blocks' tests, then the commit — over HTTP.

    Both database blocks are on the EXISTING path so the commit (not the stubbed test) is what writes
    their passwords: that is the run that proves ``clusterdb.pkl`` lands beside ``database.pkl``.
    """
    import httpx

    from services.webservice.setup import boot as setup_boot
    from services.webservice.setup.node import Detection

    _stub_the_machine(monkeypatch)
    port = _free_port()
    service = await setup_boot.start_setup_console(node=NODE, config_dir=tmp_path,
                                                   open_browser=False, announce=False, port=port)
    try:
        service.app.state.setup_node_detection = Detection(
            candidates=(("DCS World", "/tmp/dcs"),), public_ip=None, upnp=False)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            async def step(number, **fields):
                page = await client.get(STEP_PATH, params={"n": str(number)})
                data = {"_csrf_token": token_of(page.text), "n": str(number)}
                data.update(fields)
                return await client.post(STEP_PATH, data=data)

            assert (await client.get(SETUP_PATH,
                                     params={"code": service.window.code})).status_code == 303
            assert (await step(1, bot_language="on")).status_code == 303
            assert (await step(2, autoupdate="on", discord="on")).status_code == 303
            # tick Federation support, test BOTH existing blocks, then continue
            assert (await step(3, action="federation", stay="1",
                               federation="on")).status_code == 200
            await step(3, action="test", federation="on", path="existing", host="127.0.0.1",
                       port="5432", database="dcsserverbot", user="dcsserverbot",
                       password="local-bot")
            await step(3, action="cluster_test", federation="on", cluster_path="existing",
                       cluster_host="cluster.example", cluster_port="5433",
                       cluster_database="federation", cluster_user="feduser",
                       cluster_password="cluster-bot")
            assert (await step(3, action="continue", federation="on", path="existing",
                               host="127.0.0.1", port="5432", database="dcsserverbot",
                               user="dcsserverbot", cluster_path="existing",
                               cluster_host="cluster.example", cluster_port="5433",
                               cluster_database="federation",
                               cluster_user="feduser")).status_code == 303
            listen_port = _free_port()
            assert (await step(4, dcs_choice="0", listen_port=str(listen_port),
                               dgsa_dcs="on")).status_code == 303
            # step 5: the token read must succeed before Continue is offered
            assert (await step(5, action="test", guild_id=str(GUILD), token=TOKEN,
                               owner=str(OWNER))).status_code == 200
            assert (await step(5, action="continue", guild_id=str(GUILD), token=TOKEN,
                               owner=str(OWNER))).status_code == 303
            assert (await step(6, role_0="Admin", role_1="DCS Admin", role_2="@everyone",
                               admin_scope="per_server", audit_channel="",
                               admin_channel="")).status_code == 303
            assert (await step(7, console_enabled="on", console_username="admin",
                               console_password="console-password")).status_code == 303
            assert (await step(8, row_manage_0="on", row_name_0="Test Server", row_status_0="123",
                               row_chat_0="-1", row_admin_0="-1",
                               row_autostart_0="on")).status_code == 303
            assert (await step(9, template_0="public-server")).status_code == 303
            review = await client.get(STEP_PATH, params={"n": "10"})
            assert "config/.secret/clusterdb.pkl" in unescape(review.text)
            assert "guild, language, autoupdate, the cluster database" in unescape(review.text)
            done = await client.post(STEP_PATH, data={
                "_csrf_token": token_of(review.text), "n": "10", "action": "write"})
    finally:
        await service.stop()

    assert "Setup is done" in unescape(done.text)
    main = load(tmp_path / "main.yaml")
    assert main["database"] == {"url": "postgres://feduser:SECRET@cluster.example:5433/"
                                       "federation?sslmode=prefer"}
    # the cluster password landed on the key the bot reads for its cluster pool
    assert utils.get_password("clusterdb", str(tmp_path)) == "cluster-bot"
    # the node's own password is a SEPARATE secret and is still there
    assert utils.get_password("database", str(tmp_path)) == "local-bot"


@pytest.mark.asyncio
async def test_step_three_is_served_over_a_real_socket_with_the_federation_box(tmp_path):
    """``start_setup_console`` (the ``run.py`` boot path) up to a real Step 3 over a real socket."""
    import socket

    import httpx

    from services.webservice.setup.boot import should_run_setup, start_setup_console

    assert should_run_setup(tmp_path), "an unconfigured dir must allow the console"
    seed_step_three(tmp_path)                       # resume lands on step 3
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    service = await start_setup_console(config_dir=tmp_path, open_browser=False, announce=False,
                                        port=port)
    try:
        assert service.bound, "the setup console never bound its port"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                     follow_redirects=True, timeout=5.0) as client:
            entered = await client.get("/setup", params={"code": service.window.display_code})
            assert entered.status_code == 200
            body = entered.text
            assert "Create a new database" in body
            # the sibling block is on the served screen, default OFF, revealing nothing
            assert "Federation support" in body
            assert 'name="federation" checked' not in body
            assert 'name="cluster_path"' not in body
    finally:
        await service.stop()

    # and the window is still open: nothing in this step writes main.yaml
    assert should_run_setup(tmp_path)


# ---------------------------------------------------- the Review line: off vs unverified

@pytest.mark.asyncio
async def test_the_review_says_not_verified_when_the_tick_is_on_but_the_block_is_untested(tmp_path):
    """The tick ON with an untested block must NOT read as "Federation support is off"."""
    draft = complete_draft(tmp_path, federation=False)
    draft.database[db_step.FEDERATION_KEY] = True
    draft.database[db_step.CLUSTER_KEY] = {
        "path": "create", "host": "cluster.example", "port": 5433,
        "master_database": "postgres", "master_user": "postgres",
        "database": "federation", "user": "feduser", "user_password_attempts": 0}
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    seed_token(app)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
    assert "the cluster database was not verified, so it is not written" in html
    assert "Federation support is off" not in html


# ---------------------------------------------------- the master password: the two slots

def test_a_cluster_create_test_cannot_clobber_the_local_master_password(tmp_path, monkeypatch):
    """The local block's master password must survive a cluster create-test with the SAME user."""
    _stub_shared(monkeypatch)
    local = db_step.answers_from_mapping(
        {"path": "create", "host": "127.0.0.1", "database": "dcsserverbot",
         "master_user": "postgres"}, secret_key=db_step.DATABASE_SECRET_KEY)
    cluster = db_step.answers_from_mapping(
        {"path": "create", "host": "cluster.example", "database": "federation",
         "master_user": "postgres"}, secret_key=db_step.CLUSTER_SECRET_KEY)

    assert db_step.run_test(local, master_password="LOCAL-MASTER", bot_password="",
                            config_dir=tmp_path).ok
    assert db_step.run_test(cluster, master_password="CLUSTER-MASTER", bot_password="",
                            config_dir=tmp_path).ok

    # the local entry is the password the LOCAL block was tested with, not the cluster's
    assert utils.get_password("postgres", str(tmp_path)) == "LOCAL-MASTER"
    # the cluster's master password lives in its own, block-scoped slot
    assert db_step.master_secret_key(cluster) == "postgres.clusterdb"
    assert utils.get_password("postgres.clusterdb", str(tmp_path)) == "CLUSTER-MASTER"


def test_a_refused_cluster_master_password_keeps_the_local_master_password(tmp_path, monkeypatch):
    """A refused cluster master password deletes the CLUSTER's slot only, never the local one."""
    _stub_shared(monkeypatch)
    local = db_step.answers_from_mapping(
        {"path": "create", "host": "127.0.0.1", "database": "dcsserverbot",
         "master_user": "postgres"}, secret_key=db_step.DATABASE_SECRET_KEY)
    db_step.run_test(local, master_password="LOCAL-MASTER", bot_password="", config_dir=tmp_path)

    def _refuse(*a, **k):
        raise psycopg.errors.InvalidPassword("wrong")

    monkeypatch.setattr(db_step, "_connect", _refuse)
    cluster = db_step.answers_from_mapping(
        {"path": "create", "host": "cluster.example", "database": "federation",
         "master_user": "postgres"}, secret_key=db_step.CLUSTER_SECRET_KEY)
    outcome = db_step.run_test(cluster, master_password="WRONG", bot_password="",
                               config_dir=tmp_path)
    assert outcome.code == "master_password_refused"
    assert utils.get_password("postgres", str(tmp_path)) == "LOCAL-MASTER"


# ---------------------------------------------------- the URL: a hostile database name

@pytest.mark.asyncio
async def test_a_hostile_database_name_still_parses_to_its_own_path(tmp_path):
    """A ``?``/``#`` database name must keep its PATH, so the boot-time decision is honest."""
    complete_draft(tmp_path, federation=True)
    draft = SetupDraft.load(tmp_path)
    draft.database["database"] = "a?b"                       # this node's own, hostile
    draft.database[db_step.CLUSTER_KEY]["database"] = "c#d"  # the cluster's, hostile
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    verify_cluster(tmp_path, app)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                           "action": "write"})

    cluster_url = load(tmp_path / "main.yaml")["database"]["url"]
    local_url = load(tmp_path / "nodes.yaml")[NODE]["database"]["url"]
    assert urlparse(cluster_url).path == "/" + quote("c#d", safe="")
    assert urlparse(cluster_url).query == "sslmode=prefer"
    assert urlparse(local_url).path == "/" + quote("a?b", safe="")
    # the bot decides a federation by comparing the two paths: they differ, so it IS one
    assert urlparse(local_url).path != urlparse(cluster_url).path


@pytest.mark.asyncio
async def test_the_same_hostile_name_reads_as_no_federation(tmp_path):
    """The SAME hostile name encodes identically on both URLs, so the paths compare equal."""
    complete_draft(tmp_path, federation=True)
    draft = SetupDraft.load(tmp_path)
    draft.database["database"] = "a?b"
    draft.database[db_step.CLUSTER_KEY]["database"] = "a?b"
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    verify_cluster(tmp_path, app)
    async with asgi_client(app) as client:
        await enter(client)
        html = unescape((await client.get(STEP_PATH, params={"n": "10"})).text)
        await client.post(STEP_PATH, data={"_csrf_token": token_of(html), "n": "10",
                                           "action": "write"})
    cluster_url = load(tmp_path / "main.yaml")["database"]["url"]
    local_url = load(tmp_path / "nodes.yaml")[NODE]["database"]["url"]
    assert urlparse(local_url).path == urlparse(cluster_url).path


# ---------------------------------------------------- the tick guard and the live relation

@pytest.mark.asyncio
async def test_a_cluster_test_with_the_tick_off_runs_nothing(tmp_path, monkeypatch):
    """A crafted ``cluster_test`` POST while the box is unticked must not run or plant anything."""
    seen = _fake_run_test(monkeypatch)
    seed_step_three(tmp_path)                       # no federation tick, no cluster block
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        await client.post(STEP_PATH, data=form(
            token_of(html), action="cluster_test", cluster_path="create",
            cluster_host="cluster.example", cluster_database="federation",
            cluster_user="feduser", cluster_master_password="pw"))
    assert seen == [], "a connection test ran for an unticked block"
    assert db_step.CLUSTER_KEY not in SetupDraft.load(tmp_path).database


@pytest.mark.asyncio
async def test_renaming_the_local_database_re_renders_the_same_name_warning(tmp_path, monkeypatch):
    """The amber warning follows the CURRENT names, without the cluster block being re-tested."""
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="ok_existing_data", ok=True, version="16.4", data={"count": 3}))
    seed_step_three(tmp_path, path="existing", database="dcsserverbot", user="dcsserverbot",
                    federation=True,
                    cluster={"path": "existing", "host": "cluster.example", "port": 5433,
                             "database": "federation", "user": "feduser"})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(html), action="cluster_test", federation="on", cluster_path="existing",
            cluster_host="cluster.example", cluster_port="5433", cluster_database="federation",
            cluster_user="feduser", cluster_password="cluster-bot"))
        assert "has the same name as this installation" not in tested.text
        # rename the LOCAL database to the cluster's name: the warning appears with no re-test
        renamed = await client.post(STEP_PATH, data=form(
            token_of(tested.text), action="federation", federation="on",
            path="existing", host="127.0.0.1", port="5432", database="federation",
            user="dcsserverbot", cluster_path="existing", cluster_host="cluster.example",
            cluster_port="5433", cluster_database="federation", cluster_user="feduser"))
        assert "has the same name as this installation" in renamed.text
        # moving the local name away again removes it
        back = await client.post(STEP_PATH, data=form(
            token_of(renamed.text), action="federation", federation="on",
            path="existing", host="127.0.0.1", port="5432", database="dcsserverbot",
            user="dcsserverbot", cluster_path="existing", cluster_host="cluster.example",
            cluster_port="5433", cluster_database="federation", cluster_user="feduser"))
        assert "has the same name as this installation" not in back.text


@pytest.mark.asyncio
async def test_moving_the_local_name_away_clears_a_warning_earned_at_test_time(tmp_path, monkeypatch):
    """The reverse direction: a warning stored by a same-name Test clears when the names diverge."""
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="ok_existing_data", ok=True, version="16.4", data={"count": 3}))
    seed_step_three(tmp_path, path="existing", database="dcsserverbot", user="dcsserverbot",
                    federation=True,
                    cluster={"path": "existing", "host": "cluster.example", "port": 5433,
                             "database": "dcsserverbot", "user": "feduser"})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(html), action="cluster_test", federation="on", cluster_path="existing",
            cluster_host="cluster.example", cluster_port="5433", cluster_database="dcsserverbot",
            cluster_user="feduser", cluster_password="cluster-bot"))
        assert "has the same name as this installation" in tested.text
        # rename the LOCAL database away: the warning clears without a re-test
        moved = await client.post(STEP_PATH, data=form(
            token_of(tested.text), action="federation", federation="on",
            path="existing", host="127.0.0.1", port="5432", database="another",
            user="dcsserverbot", cluster_path="existing", cluster_host="cluster.example",
            cluster_port="5433", cluster_database="dcsserverbot", cluster_user="feduser"))
        assert "has the same name as this installation" not in moved.text
