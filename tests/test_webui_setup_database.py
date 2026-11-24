"""Step 3 of the tour — the Database card: the two paths, the test action and the failure states.

TWO LAYERS, both over the shipped code:

* the VIEW is driven through a real ASGI request against the shipped setup application, so the
  per-path field visibility, the Test button's label, the Continue gate and the "secrets never come
  back" rule are the shipped ones and not a description of them (``db_step.run_test`` is stubbed, so
  no database is touched);
* the CLASSIFICATION (:func:`services.webservice.setup.database.run_test`) is driven with a fake
  connection, one test per failure-state code, so every driver error maps to the operator's sentence
  and never to a raw string.

A REAL connection failure IS exercised: ``127.0.0.1:1`` refuses the socket, so the "nothing is
answering" branch runs against a live driver.

UNVERIFIED on this machine: the CREATE DDL (create the role / the database) needs a real PostgreSQL
server and none is reachable here (``127.0.0.1:5432`` refuses). The operator must check those paths
against a live server — start one, then drive the real console (a boot of ``run.py`` with no
``config/main.yaml`` reaches the setup console) and press "Test connection and create". The
statement sequence is pinned separately in ``tests/test_db_setup.py``.
"""
from __future__ import annotations

import ast
import pathlib
import re

import psycopg
import pytest
from babel.messages import pofile

from conftest import asgi_client
from services.webservice.setup import database as db_step
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


def seed_step_three(config_dir, **database):
    draft = SetupDraft(step=2)
    draft.database = dict(database)
    draft.save(config_dir)


def form(token: str, **fields) -> dict:
    data = {"_csrf_token": token, "n": "3"}
    data.update(fields)
    return data


# ------------------------------------------------------------------------------- the view

@pytest.mark.asyncio
async def test_the_card_offers_both_paths_with_create_the_default(tmp_path):
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    assert "Create a new database" in html
    assert "Use an existing database" in html
    assert 'name="path" value="create" checked' in html
    assert 'name="path" value="existing" checked' not in html


@pytest.mark.asyncio
async def test_the_database_step_puts_the_download_help_at_the_very_top_when_the_port_is_closed(
        tmp_path):
    """A closed local port: the install sentence is the card's FIRST line, above the path choice."""
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    app.state.setup_pg_probe, app.state.setup_pg_probed = False, True   # nothing answers locally
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    # a real anchor that opens in a new tab; the sentence puts the download page on the word "here"
    assert ('<a class="link" href="https://www.postgresql.org/download/" '
            'target="_blank" rel="noopener noreferrer">here</a>') in html
    # the URL is repeated as plain text, so a copy carries the address
    assert "<code>https://www.postgresql.org/download/</code>" in html
    # the operator's sentence, verbatim, with the link inside it
    assert ("To use DCSServerBot, you need to install PostgreSQL. You can download it "
            '<a class="link"') in html
    # it sits at the VERY TOP: above the path choice and above the first field
    assert html.index("https://www.postgresql.org/download/") < html.index("<fieldset")
    assert html.index("https://www.postgresql.org/download/") < html.index('name="host"')


@pytest.mark.asyncio
async def test_the_cluster_block_does_not_repeat_the_download_link(tmp_path):
    seed_step_three(tmp_path, federation=True, cluster={"path": "create"})
    app, _ = build(tmp_path)
    app.state.setup_pg_probe, app.state.setup_pg_probed = False, True
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    # the download link is drawn once per PAGE (at the top), never per block
    assert html.count('href="https://www.postgresql.org/download/"') == 1


@pytest.mark.asyncio
async def test_the_create_path_shows_the_master_fields_and_the_derived_sentence(tmp_path):
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    for name in ("master_database", "master_user", "master_password", "database", "user"):
        assert f'name="{name}"' in html, name
    # the existing path's own password field is hidden on the create path
    assert 'name="password"' not in html
    assert "The bot will create the database dcsserverbot and the user dcsserverbot." in html
    assert "Test connection and create" in html
    assert "Test connection</button>" not in html


@pytest.mark.asyncio
async def test_the_existing_path_hides_the_master_fields_and_renames_the_button(tmp_path):
    seed_step_three(tmp_path, path="existing")
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    assert 'name="password"' in html
    assert 'name="master_password"' not in html
    assert 'name="master_user"' not in html
    assert "Test connection" in html
    assert "Test connection and create" not in html


@pytest.mark.asyncio
async def test_switching_the_path_keeps_the_shared_names_and_host_port(tmp_path):
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        # reach step 3 by answering 1 and 2
        step_one = (await client.get(STEP_PATH, params={"n": "1"})).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(step_one), "n": "1"})
        step_two = (await client.get(STEP_PATH, params={"n": "2"})).text
        await client.post(STEP_PATH, data={"_csrf_token": token_of(step_two), "n": "2",
                                           "autoupdate": "on", "discord": "on"})
        step_three = (await client.get(STEP_PATH, params={"n": "3"})).text
        # edit the create block, then switch the path (the progressive-enhancement submit)
        switched = await client.post(STEP_PATH, data=form(
            token_of(step_three), path="existing", host="db.example", port="5433",
            master_database="postgres", master_user="postgres", database="mydb", user="myuser",
            action="switch", stay="1"))
        assert switched.status_code == 200
        html = switched.text
        assert 'name="path" value="existing" checked' in html
        assert 'value="mydb"' in html and 'value="myuser"' in html
        assert 'value="db.example"' in html and 'value="5433"' in html
    document = SetupDraft.load(tmp_path).database
    assert document["path"] == "existing"
    assert document["database"] == "mydb" and document["user"] == "myuser"
    assert document["host"] == "db.example" and document["port"] == 5433


@pytest.mark.asyncio
async def test_continue_is_disabled_until_a_successful_test(tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="ok_created", ok=True, version="16.2",
        data={"database": "dcsserverbot", "user": "dcsserverbot"}))
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        before = (await client.get(STEP_PATH, params={"n": "3"})).text
        assert 'value="continue" disabled' in before
        tested = await client.post(STEP_PATH, data=form(
            token_of(before), action="test", host="127.0.0.1", port="5432",
            master_database="postgres", master_user="postgres", master_password="secret",
            database="dcsserverbot", user="dcsserverbot"))
        after = tested.text
    assert 'value="continue" disabled' not in after
    assert "Connected to PostgreSQL 16.2." in after


@pytest.mark.asyncio
async def test_editing_a_field_after_a_successful_test_disables_continue_again(tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="ok_created", ok=True, version="16.2", data={}))
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(html), action="test", master_password="secret", host="127.0.0.1",
            port="5432", database="dcsserverbot", user="dcsserverbot"))
        assert 'value="continue" disabled' not in tested.text
        # a DIFFERENT host with the same green light: the button goes back to disabled
        moved = await client.post(STEP_PATH, data=form(
            token_of(tested.text), action="continue", master_password="", host="otherhost",
            port="5432", database="dcsserverbot", user="dcsserverbot"))
    assert 'value="continue" disabled' in moved.text


@pytest.mark.asyncio
async def test_continue_without_a_test_is_refused(tmp_path):
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        refused = await client.post(STEP_PATH, data=form(token_of(html), action="continue"))
    assert refused.status_code == 200
    assert "Test the connection before you continue." in refused.text
    assert SetupDraft.load(tmp_path).step == 2


@pytest.mark.asyncio
async def test_a_failed_test_keeps_the_typed_values_and_the_password_never_renders(
        tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="unreachable", ok=False, data={"host": "db.example", "port": 5433}))
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        failed = await client.post(STEP_PATH, data=form(
            token_of(html), action="test", host="db.example", port="5433",
            master_password="sup3rs3cret", database="mydb", user="myuser"))
        body = failed.text
        # the classified sentence, never a driver string
        assert "nothing is answering on db.example:5433" in body
        assert "psycopg" not in body and "OperationalError" not in body
        # typed non-secrets survive; the password does NOT come back
        assert 'value="db.example"' in body and 'value="mydb"' in body
        assert "sup3rs3cret" not in body
        assert 'name="master_password" value=""' in body
        # a kept password is announced, not shown
        assert "A password is kept for this test" in body
    # and the draft on disk carries no secret
    raw = draft_path(tmp_path).read_text(encoding="utf-8")
    assert "sup3rs3cret" not in raw
    assert "master_password" not in raw


@pytest.mark.asyncio
async def test_a_successful_test_moves_to_step_four(tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="ok_created", ok=True, version="16.2", data={}))
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        await client.post(STEP_PATH, data=form(token_of(html), action="test",
                                               master_password="secret"))
        moved = await client.post(STEP_PATH, data=form(token_of(html), action="continue",
                                                       master_password="secret"))
    assert moved.status_code == 303
    assert SetupDraft.load(tmp_path).step == 3


# ------------------------------------------------------------------------------- path A flow

class FakeConn:
    """A stand-in psycopg connection; the shared DDL helpers are stubbed in each test."""

    def close(self):
        pass


def _stub_shared(monkeypatch, *, version="16.2", outdated=False, user_exists=False,
                 database_exists=False, login_ok=True, reset_calls=None):
    monkeypatch.setattr(db_step, "_connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(db_step.db_setup, "get_server_version", lambda conn: version)
    monkeypatch.setattr(db_step.db_setup, "is_version_outdated", lambda v: outdated)
    monkeypatch.setattr(db_step.db_setup, "create_user",
                        lambda conn, user, pw: (_ for _ in ()).throw(psycopg.Error("exists"))
                        if user_exists else None)
    monkeypatch.setattr(db_step.db_setup, "create_database",
                        lambda conn, db, owner: not database_exists)
    monkeypatch.setattr(db_step, "_database_exists", lambda conn, db: database_exists)
    monkeypatch.setattr(db_step.db_setup, "verify_login", lambda *a, **k: login_ok)

    def _reset(conn, user, pw):
        if reset_calls is not None:
            reset_calls.append(pw)

    monkeypatch.setattr(db_step.db_setup, "reset_user_password", _reset)


def test_the_create_path_creates_the_role_and_database(tmp_path, monkeypatch):
    _stub_shared(monkeypatch)
    outcome = db_step.run_test(db_step.DatabaseAnswers(), master_password="pw",
                               bot_password="", config_dir=tmp_path)
    assert outcome.code == "ok_created" and outcome.ok
    assert (tmp_path / ".secret" / "postgres.pkl").exists()
    assert (tmp_path / ".secret" / "database.pkl").exists()


def test_the_master_password_refusal_is_classified(tmp_path, monkeypatch):
    def _refuse(*a, **k):
        raise psycopg.errors.InvalidPassword("wrong")

    monkeypatch.setattr(db_step, "_connect", _refuse)
    outcome = db_step.run_test(db_step.DatabaseAnswers(), master_password="pw",
                               bot_password="", config_dir=tmp_path)
    assert outcome.code == "master_password_refused" and not outcome.ok


def test_an_existing_role_without_a_password_asks_for_it(tmp_path, monkeypatch):
    _stub_shared(monkeypatch, user_exists=True)
    outcome = db_step.run_test(db_step.DatabaseAnswers(), master_password="pw",
                               bot_password="", config_dir=tmp_path)
    assert outcome.code == "user_exists" and not outcome.ok
    assert outcome.data["user"] == "dcsserverbot"


def test_an_existing_role_is_asked_three_times_then_reset(tmp_path, monkeypatch):
    resets = []
    _stub_shared(monkeypatch, user_exists=True, login_ok=False, reset_calls=resets)
    # attempts 1 and 2 are refusals, with the count going up
    for previous, expected_attempt in ((0, 1), (1, 2)):
        outcome = db_step.run_test(
            db_step.DatabaseAnswers(user_password_attempts=previous),
            master_password="pw", bot_password="guess", config_dir=tmp_path)
        assert outcome.code == "user_password_refused"
        assert outcome.data["attempts"] == expected_attempt
    # the third wrong answer triggers the installer's own reset
    outcome = db_step.run_test(db_step.DatabaseAnswers(user_password_attempts=2),
                               master_password="pw", bot_password="guess", config_dir=tmp_path)
    assert outcome.code == "user_password_reset" and outcome.ok
    assert resets, "ALTER USER … ENCRYPTED PASSWORD never ran"
    assert (tmp_path / ".secret" / "database.pkl").exists()


def test_a_database_that_already_holds_data_is_a_repair_prompt(tmp_path, monkeypatch):
    _stub_shared(monkeypatch, database_exists=True)
    monkeypatch.setattr(db_step, "_table_names", lambda conn: {"servers", "players"})
    monkeypatch.setattr(db_step, "_schema_versions", lambda conn: ["v3.18"])
    outcome = db_step.run_test(db_step.DatabaseAnswers(), master_password="pw",
                               bot_password="", config_dir=tmp_path)
    assert outcome.code == "bad_database_holds_data" and not outcome.ok


def test_a_database_with_foreign_tables_is_refused(tmp_path, monkeypatch):
    _stub_shared(monkeypatch, user_exists=False, database_exists=False)
    monkeypatch.setattr(db_step, "_connect", lambda *a, **k: FakeConn())
    # path B: connect succeeds, the database holds only tables this bot did not create
    monkeypatch.setattr(db_step.db_setup, "get_server_version", lambda conn: "16.2")
    monkeypatch.setattr(db_step, "_table_names", lambda conn: {"orders", "customers"})
    monkeypatch.setattr(db_step, "_schema_versions", lambda conn: [])
    outcome = db_step.run_test(db_step.DatabaseAnswers(path="existing"), master_password="",
                               bot_password="pw", config_dir=tmp_path)
    assert outcome.code == "bad_foreign_tables" and not outcome.ok


# ------------------------------------------------------------------------------- path B flow

def test_the_existing_path_reports_an_empty_database(tmp_path, monkeypatch):
    _stub_shared(monkeypatch)
    monkeypatch.setattr(db_step, "_table_names", lambda conn: set())
    outcome = db_step.run_test(db_step.DatabaseAnswers(path="existing"), master_password="",
                               bot_password="pw", config_dir=tmp_path)
    assert outcome.code == "ok_existing_empty" and outcome.ok


def test_the_existing_path_adopts_a_bot_database(tmp_path, monkeypatch):
    _stub_shared(monkeypatch)
    monkeypatch.setattr(db_step, "_table_names", lambda conn: {"servers", "players", "version"})
    # the DB is at the NEWEST shipped schema version, whatever that is today: the fixture derives it
    # from the repo's own sql/update_v*.sql set rather than hardcoding a version a migration moves on
    monkeypatch.setattr(db_step, "_schema_versions",
                        lambda conn: [f"v{db_step.current_schema_version()}"])
    outcome = db_step.run_test(db_step.DatabaseAnswers(path="existing"), master_password="",
                               bot_password="pw", config_dir=tmp_path)
    assert outcome.code == "ok_existing_data" and outcome.ok
    assert outcome.data["count"] == 3


def test_the_existing_path_warns_about_an_older_schema(tmp_path, monkeypatch):
    _stub_shared(monkeypatch)
    monkeypatch.setattr(db_step, "_table_names", lambda conn: {"servers", "version"})
    monkeypatch.setattr(db_step, "_schema_versions", lambda conn: ["v1.4"])
    outcome = db_step.run_test(db_step.DatabaseAnswers(path="existing"), master_password="",
                               bot_password="pw", config_dir=tmp_path)
    assert outcome.code == "ok_existing_older" and outcome.ok


def test_path_b_never_writes_a_secret(tmp_path, monkeypatch):
    _stub_shared(monkeypatch)
    monkeypatch.setattr(db_step, "_table_names", lambda conn: set())
    db_step.run_test(db_step.DatabaseAnswers(path="existing"), master_password="",
                     bot_password="pw", config_dir=tmp_path)
    assert not (tmp_path / ".secret").exists(), "the read-only test wrote to config/.secret/"


def test_path_b_cannot_sign_in_is_classified(tmp_path, monkeypatch):
    def _refuse(*a, **k):
        raise psycopg.errors.InvalidPassword("nope")

    monkeypatch.setattr(db_step, "_connect", _refuse)
    outcome = db_step.run_test(db_step.DatabaseAnswers(path="existing"), master_password="",
                               bot_password="pw", config_dir=tmp_path)
    assert outcome.code == "user_cannot_login" and not outcome.ok
    assert outcome.data["user"] == "dcsserverbot"


def test_an_older_server_is_an_amber_warning_not_a_block(tmp_path, monkeypatch):
    _stub_shared(monkeypatch, version="13.4", outdated=True)
    monkeypatch.setattr(db_step, "_table_names", lambda conn: set())
    outcome = db_step.run_test(db_step.DatabaseAnswers(path="existing"), master_password="",
                               bot_password="pw", config_dir=tmp_path)
    assert outcome.code == "ok_existing_empty" and outcome.ok
    assert outcome.version_outdated is True
    assert outcome.version == "13.4"


@pytest.mark.asyncio
async def test_the_amber_version_warning_renders_but_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="ok_existing_empty", ok=True, version="13.4", version_outdated=True,
        data={"database": "dcsserverbot"}))
    seed_step_three(tmp_path, path="existing")
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(token_of(html), action="test",
                                                        path="existing", password="pw",
                                                        database="dcsserverbot", user="dcsserverbot"))
        assert "PostgreSQL 13.4 is older than the supported 14." in tested.text
        assert 'value="continue" disabled' not in tested.text


# ------------------------------------------------------------------------------- the real driver

def test_nothing_listening_is_a_real_classified_failure(tmp_path):
    """A live driver against a closed port: the socket refuses, and the refusal is classified."""
    answers = db_step.DatabaseAnswers(path="existing", host="127.0.0.1", port=1)
    outcome = db_step.run_test(answers, master_password="", bot_password="pw", config_dir=tmp_path)
    assert outcome.code == "unreachable", outcome
    assert not outcome.ok
    assert "psycopg" not in outcome.code


# ------------------------------------------------------------------------------- the table marks

def test_the_recognised_tables_come_from_the_repo_sql():
    known = db_step.recognised_tables()
    for name in ("servers", "players", "version", "cluster", "nodes", "missionstats"):
        assert name in known, name
    # a plugin's own table is part of the same set (scanned, not hard-coded)
    assert "logbook_pilots" in known


def test_the_current_schema_version_is_the_newest_update_file():
    version = db_step.current_schema_version()
    assert version and version.startswith("3.")


# ------------------------------------------------------------------------------- the three findings

def test_a_mid_provision_driver_error_is_a_classified_failure(tmp_path, monkeypatch):
    """A psycopg.Error raised past the master connection is classified, never a bare escape."""
    _stub_shared(monkeypatch, user_exists=True)

    def _reset(conn, user, pw):
        raise psycopg.errors.InsufficientPrivilege("must be superuser to alter superuser roles")

    monkeypatch.setattr(db_step.db_setup, "reset_user_password", _reset)
    outcome = db_step.run_test(db_step.DatabaseAnswers(), master_password="pw", bot_password="",
                               reset_user=True, config_dir=tmp_path)
    assert outcome.code == "provision_failed" and not outcome.ok, outcome
    assert "psycopg" not in outcome.code and "InsufficientPrivilege" not in outcome.code


@pytest.mark.asyncio
async def test_a_mid_provision_failure_never_leaves_a_successful_test(tmp_path, monkeypatch):
    """Through the real ASGI app: the classified sentence renders and the gate stays closed."""
    _stub_shared(monkeypatch, user_exists=True)

    def _reset(conn, user, pw):
        raise psycopg.errors.InsufficientPrivilege("must be superuser to alter superuser roles")

    monkeypatch.setattr(db_step.db_setup, "reset_user_password", _reset)
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        before = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(before), action="test", path="create", host="127.0.0.1", port="5432",
            master_database="postgres", master_user="postgres", master_password="secret",
            database="dcsserverbot", user="dcsserverbot", reset_user="on"))
        body = tested.text
    assert tested.status_code == 200
    assert "The server refused while the database or user was being created" in body
    assert "psycopg" not in body and "InsufficientPrivilege" not in body
    assert 'value="continue" disabled' in body
    document = SetupDraft.load(tmp_path).database
    assert document.get("verified") is not True
    assert "signature" not in document


def test_a_reused_database_whose_login_fails_is_not_a_success(tmp_path, monkeypatch):
    """create_database returned False but the bot's own login never proved: a failure, not ok."""
    calls = {"n": 0}

    def _connect(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:                     # the master connection succeeds
            return FakeConn()
        raise psycopg.errors.InvalidPassword("nope")   # the bot's own login does not

    monkeypatch.setattr(db_step, "_connect", _connect)
    monkeypatch.setattr(db_step.db_setup, "get_server_version", lambda conn: "16.2")
    monkeypatch.setattr(db_step.db_setup, "is_version_outdated", lambda v: False)
    monkeypatch.setattr(db_step.db_setup, "create_user", lambda conn, user, pw: None)
    monkeypatch.setattr(db_step, "_database_exists", lambda conn, db: True)
    monkeypatch.setattr(db_step.db_setup, "create_database", lambda conn, db, owner: False)
    outcome = db_step.run_test(db_step.DatabaseAnswers(), master_password="pw", bot_password="",
                               config_dir=tmp_path)
    assert outcome.code == "user_cannot_login" and not outcome.ok, outcome


def test_the_existing_role_is_verified_against_the_target_database(tmp_path, monkeypatch):
    """The bot login is checked against the target database, never the master's (pg_hba differs)."""
    seen = {}

    def _verify(user, pw, host, port, database):
        seen["database"] = database
        return True

    monkeypatch.setattr(db_step, "_connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(db_step.db_setup, "get_server_version", lambda conn: "16.2")
    monkeypatch.setattr(db_step.db_setup, "is_version_outdated", lambda v: False)
    monkeypatch.setattr(db_step.db_setup, "create_user",
                        lambda conn, user, pw: (_ for _ in ()).throw(psycopg.Error("exists")))
    monkeypatch.setattr(db_step.db_setup, "verify_login", _verify)
    monkeypatch.setattr(db_step, "_database_exists", lambda conn, db: False)
    monkeypatch.setattr(db_step.db_setup, "create_database", lambda conn, db, owner: True)
    answers = db_step.DatabaseAnswers(master_database="postgres", database="botdb")
    db_step.run_test(answers, master_password="pw", bot_password="guess", config_dir=tmp_path)
    assert seen.get("database") == "botdb", seen
    assert seen.get("database") != answers.master_database


def test_a_refused_create_is_a_failure_not_already_existed(tmp_path, monkeypatch):
    """create_database False after a refused CREATE/GRANT/ALTER must not read as 'already existed'."""
    monkeypatch.setattr(db_step, "_connect", lambda *a, **k: FakeConn())
    monkeypatch.setattr(db_step.db_setup, "get_server_version", lambda conn: "16.2")
    monkeypatch.setattr(db_step.db_setup, "is_version_outdated", lambda v: False)
    monkeypatch.setattr(db_step.db_setup, "create_user", lambda conn, user, pw: None)
    monkeypatch.setattr(db_step, "_database_exists", lambda conn, db: False)
    monkeypatch.setattr(db_step.db_setup, "create_database", lambda conn, db, owner: False)
    outcome = db_step.run_test(db_step.DatabaseAnswers(), master_password="pw", bot_password="",
                               config_dir=tmp_path)
    assert outcome.code == "provision_failed" and not outcome.ok, outcome


def test_the_connect_passes_a_bounded_timeout(monkeypatch):
    """The connect carries connect_timeout, so a firewalled host cannot block indefinitely."""
    seen = {}

    def _fake_connect(url, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(db_step.psycopg, "connect", _fake_connect)
    db_step._connect("u", "p", "127.0.0.1", 5432, "d")
    assert seen.get("connect_timeout") == db_step.CONNECT_TIMEOUT == 5
    assert seen.get("autocommit") is True


def test_a_firewalled_host_wait_is_bounded(tmp_path):
    """A blackhole host (SYN dropped, not refused) returns in about connect_timeout, not minutes."""
    import time

    answers = db_step.DatabaseAnswers(path="existing", host="10.255.255.1", port=5432)
    start = time.monotonic()
    outcome = db_step.run_test(answers, master_password="", bot_password="pw", config_dir=tmp_path)
    elapsed = time.monotonic() - start
    assert outcome.code in {"unreachable", "connection_failed"}, outcome
    assert elapsed < db_step.CONNECT_TIMEOUT * 3, f"the wait was {elapsed:.1f}s"


# ------------------------------------------------------------------------------- i18n

def _step_three_msgids() -> list[str]:
    """Every string ``tour.py`` marks with ``i18n._`` (the AST folds implicit concatenation)."""
    source = (REPO_ROOT / "services/webservice/setup/tour.py").read_text(encoding="utf-8")
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "i18n" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value not in found):
            found.append(node.args[0].value)
    return found


def test_every_tour_string_is_shipped_in_every_catalog():
    """No ``i18n._``-marked string may be missing from a shipped catalog (born wrapped)."""
    for language in CATALOGS:
        catalog = pofile.read_po(open(
            REPO_ROOT / f"locale/{language}/LC_MESSAGES/webui.po", encoding="utf-8"))
        have = {message.id for message in catalog if message.id}
        for msgid in _step_three_msgids():
            assert msgid in have, f"'{msgid}' is missing from the {language} catalog"


def test_the_database_step_strings_are_literal_where_they_must_be():
    """The path names and ``config/.secret/`` are the design's keep-literal set."""
    catalog = pofile.read_po(open(REPO_ROOT / "locale/de/LC_MESSAGES/webui.po", encoding="utf-8"))
    by_id = {message.id: message.string for message in catalog if message.id}
    assert "config/.secret/" in by_id["Stored in config/.secret/, never on this page again."]


@pytest.mark.asyncio
async def test_the_card_is_translated_when_the_tour_language_is_german(tmp_path):
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        await client.get(LANGUAGE_PATH, params={"lang": "de", "next": f"{STEP_PATH}?n=3"})
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    assert "Eine neue Datenbank anlegen" in html
    assert "Verbindung testen und anlegen" in html


# ------------------------------------------------------------------------------- the entry point

@pytest.mark.asyncio
async def test_the_real_entry_point_serves_step_three_over_a_socket(tmp_path):
    """`start_setup_console` (the ``run.py`` boot path) up to a real Step 3 over a real socket."""
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
            # the gate accepted the code and the tour resumed at step 3
            assert "Create a new database" in entered.text
            assert 'name="master_password"' in entered.text
    finally:
        await service.stop()

    # and the window is still open: nothing in this step writes main.yaml
    assert should_run_setup(tmp_path)


def test_a_pre_existing_empty_database_is_reported_as_reused(tmp_path, monkeypatch):
    _stub_shared(monkeypatch, database_exists=True)
    monkeypatch.setattr(db_step, "_table_names", lambda conn: set())
    outcome = db_step.run_test(db_step.DatabaseAnswers(), master_password="pw",
                               bot_password="", config_dir=tmp_path)
    assert outcome.code == "ok_reused_database" and outcome.ok


def test_a_pre_existing_database_with_foreign_tables_carries_count_and_example(tmp_path, monkeypatch):
    _stub_shared(monkeypatch, database_exists=True)
    monkeypatch.setattr(db_step, "_table_names", lambda conn: {"orders", "customers"})
    monkeypatch.setattr(db_step, "_schema_versions", lambda conn: [])
    outcome = db_step.run_test(db_step.DatabaseAnswers(), master_password="pw",
                               bot_password="", config_dir=tmp_path)
    assert outcome.code == "bad_foreign_tables" and not outcome.ok
    assert outcome.data["count"] == 2
    assert outcome.data["example"] == "customers"


@pytest.mark.asyncio
async def test_the_foreign_tables_notice_fills_its_holes(tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code="bad_foreign_tables", ok=False,
        data={"database": "dcsserverbot", "count": 7, "example": "orders"}))
    seed_step_three(tmp_path, path="existing")
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(token_of(html), action="test",
                                                        path="existing", password="pw"))
    assert "did not create (7 tables, e.g. orders)" in tested.text
    assert "{count}" not in tested.text and "{example}" not in tested.text


# ------------------------------------------------------------------------------- the master user

def test_an_unsafe_master_user_is_refused_before_any_connection(tmp_path, monkeypatch):
    """A '/' or '.' is refused at the STEP, before it can reach a connection or ``.secret/``."""
    opened = []

    def _connect(*a, **k):
        opened.append(a)
        return FakeConn()

    monkeypatch.setattr(db_step, "_connect", _connect)
    outcome = db_step.run_test(db_step.DatabaseAnswers(master_user="a/b"), master_password="pw",
                               bot_password="", config_dir=tmp_path)
    assert outcome.code == "bad_master_user" and not outcome.ok
    assert outcome.data["user"] == "a/b"
    assert opened == [], "a connection was opened for an unsafe master user"
    assert not (tmp_path / ".secret").exists()


@pytest.mark.parametrize("master_user", ["a/b", "X.clusterdb", "..", ".", "/etc/passwd", "a b"])
def test_an_unsafe_master_user_is_never_a_secret_file(master_user, tmp_path, monkeypatch):
    monkeypatch.setattr(db_step, "_connect", lambda *a, **k: FakeConn())
    outcome = db_step.run_test(db_step.DatabaseAnswers(master_user=master_user),
                               master_password="pw", bot_password="", config_dir=tmp_path)
    assert outcome.code == "bad_master_user" and not outcome.ok
    assert not (tmp_path / ".secret").exists()


def test_the_dot_rule_removes_the_cluster_secret_slot_collision():
    """The cluster block stores under ``<master_user>.clusterdb``; a master user literally named
    ``X.clusterdb`` would take master user ``X``'s cluster slot — forbidding ``.`` removes it."""
    assert db_step.master_secret_key(db_step.DatabaseAnswers(
        master_user="X", secret_key=db_step.CLUSTER_SECRET_KEY)) == "X.clusterdb"
    assert db_step.db_setup.is_valid_master_user("X.clusterdb") is False


@pytest.mark.parametrize("master_user", ["database", "clusterdb", "DCS", "dcs", "proxy", "token",
                                         "hash", "discord_client_secret", "Database", "CLUSTERDB"])
def test_a_reserved_master_user_is_refused_before_any_connection(master_user, tmp_path, monkeypatch):
    """A name equal to one of the bot's own ``config/.secret/<key>.pkl`` files is refused at the STEP:
    the master user is stored under its own name, so ``database`` would overwrite ``database.pkl``."""
    opened = []

    def _connect(*a, **k):
        opened.append(a)
        return FakeConn()

    monkeypatch.setattr(db_step, "_connect", _connect)
    outcome = db_step.run_test(db_step.DatabaseAnswers(master_user=master_user),
                               master_password="pw", bot_password="", config_dir=tmp_path)
    assert outcome.code == "bad_reserved_master_user" and not outcome.ok
    assert outcome.data["user"] == master_user
    assert opened == [], "a connection was opened for a reserved master user"
    assert not (tmp_path / ".secret").exists()


def test_a_reserved_master_user_does_not_block_the_existing_path(tmp_path, monkeypatch):
    """``master_user`` is a create-path credential; the read-only existing path must ignore it."""
    _stub_shared(monkeypatch)
    monkeypatch.setattr(db_step, "_table_names", lambda conn: set())
    outcome = db_step.run_test(
        db_step.DatabaseAnswers(path="existing", master_user="database"),
        master_password="", bot_password="pw", config_dir=tmp_path)
    assert outcome.code == "ok_existing_empty" and outcome.ok


def test_an_unsafe_master_user_does_not_block_the_existing_path(tmp_path, monkeypatch):
    """``master_user`` is a create-path credential; the read-only existing path must ignore it."""
    _stub_shared(monkeypatch)
    monkeypatch.setattr(db_step, "_table_names", lambda conn: set())
    outcome = db_step.run_test(
        db_step.DatabaseAnswers(path="existing", master_user="a/b"),
        master_password="", bot_password="pw", config_dir=tmp_path)
    assert outcome.code == "ok_existing_empty" and outcome.ok


@pytest.mark.asyncio
async def test_an_unsafe_master_user_renders_the_step_s_own_error(tmp_path):
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(html), action="test", path="create", host="127.0.0.1", port="5432",
            master_database="postgres", master_user="a/b", master_password="secret",
            database="dcsserverbot", user="dcsserverbot"))
    body = tested.text
    # the render HTML-escapes the quotes in the msgid
    assert ("The master user may only use letters, digits, &#x27;_&#x27; and &#x27;-&#x27;."
            in body)
    assert 'notice bad' in body
    assert 'value="continue" disabled' in body
    document = SetupDraft.load(tmp_path).database
    assert document.get("verified") is not True
    assert "signature" not in document
    assert not (tmp_path / ".secret").exists()


@pytest.mark.asyncio
async def test_a_reserved_master_user_renders_its_own_sentence(tmp_path):
    """The reserved case has its OWN sentence — not the charset one, which the name satisfies."""
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(html), action="test", path="create", host="127.0.0.1", port="5432",
            master_database="postgres", master_user="database", master_password="secret",
            database="dcsserverbot", user="dcsserverbot"))
    body = tested.text
    assert ("The master user may not be named after one of the bot&#x27;s own secret files. "
            "Choose another name." in body)
    assert "The master user may only use letters, digits" not in body
    assert 'notice bad' in body
    assert 'value="continue" disabled' in body
    assert not (tmp_path / ".secret").exists()

