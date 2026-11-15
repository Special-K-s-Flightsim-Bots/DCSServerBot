"""The Continue gate: a successful connection TEST in THIS PROCESS, on every step that has one.

The operator's rule: *"they will not get the offer for the Continue button"* until the step's own connection
test has passed — in the run the operator is in. A ``verified``/``signature`` restored from a draft is
NOT enough: the proof lives in the process store, so a restarted console (the store is empty, the
password is not in the draft and is re-entered anyway) must test again.

TWO STEPS HAVE A CONNECTION TEST, and both are covered here:

* the Database step's local block AND its ticked Federation block (``action=test`` / ``action=cluster_test``);
* the Discord step's token fetch (``action=test`` → the guild read).

The gate is SERVER-SIDE: the served markup carries the ``disabled`` attribute and the POST is refused,
so it behaves identically with JavaScript off (no script turns the button on).

The presentation is DISABLED-WITH-REASON, the same on both steps: the Continue submit stays in the
document with ``disabled``, and ONE translated sentence stands visibly on the step — a ``title`` on a
disabled control is not reliably shown.
"""
from __future__ import annotations

import re

import pytest
from babel.messages import pofile

from conftest import asgi_client
from services.webservice.setup import database as db_step
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

SENTENCE = "Test the connection before you continue."
GUILD = 111111111111111111
OWNER = 222222222222222222
TOKEN = "discord-bot-token-not-a-real-one"

DB_FIELDS = {"path": "create", "host": "127.0.0.1", "port": "5432",
             "master_database": "postgres", "master_user": "postgres",
             "database": "dcsserverbot", "user": "dcsserverbot"}


def build(config_dir):
    window = SetupWindow(code=CODE, wrong_delay=0.0)
    return create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN)


async def enter(client):
    assert (await client.get(SETUP_PATH, params={"code": CODE})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the step form carries no CSRF token"
    return found.group(1)


def form(csrf: str, n: int, **fields) -> dict:
    return {"_csrf_token": csrf, "n": str(n), **fields}


def db_answers(**over):
    base = dict(host="127.0.0.1", port=5432, master_database="postgres", master_user="postgres",
                database="dcsserverbot", user="dcsserverbot", path="create")
    base.update(over)
    return db_step.DatabaseAnswers(**base)


def seed_passing_run(config_dir, *, master="secret", bot="", cluster=None, cluster_master="",
                     cluster_bot=""):
    """A draft exactly as an EARLIER RUN left it: the tests passed, so their signatures are on disk."""
    draft = SetupDraft(step=2)
    answers = db_answers()
    draft.database = dict(db_step.non_secret_document(answers))
    draft.database["verified"] = True
    draft.database["signature"] = db_step.signature(answers, master, bot)
    if cluster is not None:
        draft.database[db_step.FEDERATION_KEY] = True
        c_answers = db_answers(**cluster)
        block = dict(db_step.non_secret_document(c_answers))
        block["verified"] = True
        block["signature"] = db_step.signature(c_answers, cluster_master, cluster_bot)
        draft.database[db_step.CLUSTER_KEY] = block
    draft.save(config_dir)
    return draft


def guild_reads(monkeypatch, *, guild_id=GUILD):
    """A SUCCESSFUL guild read (the suite never touches discord.com)."""
    async def fake(token, guild):
        return discord_step.GuildData(guild_id=int(guild or 0), guild_name="Test Guild",
                                      channels=(), roles=("Admin", "DCS Admin")), ""

    monkeypatch.setattr(discord_step, "_read_guild", fake)


def guild_read_fails(monkeypatch):
    async def fake(token, guild):
        return None, "discord_bad_token"

    monkeypatch.setattr(discord_step, "_read_guild", fake)


def seed_discord(config_dir, *, step=4):
    draft = SetupDraft(step=step, discord=True)
    draft.node = {"listen_port": 10042, "no_dcs": True, "dcs_installation": None}
    draft.servers = {"scanned": True, "detected": {}, "rows": {}}
    draft.save(config_dir)
    return draft


def ok_test(monkeypatch, code="ok_created", version="16.4"):
    monkeypatch.setattr(db_step, "run_test", lambda *a, **k: db_step.TestOutcome(
        code=code, ok=True, version=version))


def assert_gate_closed(html: str) -> None:
    assert 'value="continue" disabled' in html, "Continue must not be offered"
    assert SENTENCE in html, "the why must be visible on the step"


def assert_gate_hint_visible(html: str) -> None:
    """The visible sentence stands on the step BEFORE any submit (not only after a blocked one)."""
    assert f'<p class="note" role="note">{SENTENCE}</p>' in html


def assert_gate_open(html: str) -> None:
    assert 'value="continue" disabled' not in html


# ------------------------------------------------------------------------ step 3: the current gate

@pytest.mark.asyncio
async def test_continue_is_never_offered_on_a_fresh_draft(tmp_path):
    """A draft with no test at all: the button is disabled and the sentence says why."""
    draft = SetupDraft(step=2)
    draft.save(tmp_path)
    app = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    assert_gate_closed(html)
    assert_gate_hint_visible(html)


@pytest.mark.asyncio
async def test_a_passing_signature_restored_from_an_earlier_run_does_not_open_the_gate(
        tmp_path, monkeypatch):
    """The reported case, reproduced: the draft passed a test in an EARLIER run.

    The static screen is gated, AND a POST carrying the re-typed password (the password is never in
    the draft, so it must be re-entered) is REFUSED — that POST is what the old gate let through.
    """
    seed_passing_run(tmp_path)
    ok_test(monkeypatch)
    app = build(tmp_path)                          # a NEW process: its store is empty
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        assert_gate_closed(html)
        assert_gate_hint_visible(html)
        refused = await client.post(STEP_PATH, data=form(
            token_of(html), 3, action="continue", master_password="secret", **DB_FIELDS))
    assert refused.status_code == 200
    assert SENTENCE in refused.text
    assert SetupDraft.load(tmp_path).step == 2, "Continue must not have advanced the tour"


@pytest.mark.asyncio
async def test_a_signature_that_matches_with_no_password_is_still_not_enough(tmp_path, monkeypatch):
    """The draft's OWN fingerprint rule cannot open the gate: only a Test in THIS process can.

    The signature is computed with empty passwords, so ``verification`` is TRUE in a fresh process
    (the store's empty values reproduce it) — the old rule alone would have opened the gate here. The
    process proof is what keeps it shut.
    """
    seed_passing_run(tmp_path, master="", bot="")
    draft = SetupDraft.load(tmp_path)
    app = build(tmp_path)
    assert db_step.verification(draft.database, app=app) is True, "the draft fingerprint matches"
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        assert_gate_closed(html)
        assert_gate_hint_visible(html)
        refused = await client.post(STEP_PATH, data=form(token_of(html), 3, action="continue",
                                                         **DB_FIELDS))
    assert refused.status_code == 200
    assert SetupDraft.load(tmp_path).step == 2


@pytest.mark.asyncio
async def test_a_test_in_this_process_opens_the_gate(tmp_path, monkeypatch):
    ok_test(monkeypatch)
    draft = SetupDraft(step=2)
    draft.save(tmp_path)
    app = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        before = (await client.get(STEP_PATH, params={"n": "3"})).text
        assert_gate_closed(before)
        assert_gate_hint_visible(before)
        tested = await client.post(STEP_PATH, data=form(
            token_of(before), 3, action="test", master_password="secret", **DB_FIELDS))
        assert_gate_open(tested.text)
        assert SENTENCE not in tested.text, "the sentence is about a test that is still missing"
        continued = await client.post(STEP_PATH, data=form(
            token_of(tested.text), 3, action="continue", **DB_FIELDS))
    assert continued.status_code == 303
    assert SetupDraft.load(tmp_path).step == 3


@pytest.mark.asyncio
async def test_an_edit_after_the_passing_test_closes_the_gate_again(tmp_path, monkeypatch):
    ok_test(monkeypatch)
    draft = SetupDraft(step=2)
    draft.save(tmp_path)
    app = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(html), 3, action="test", master_password="secret", **DB_FIELDS))
        assert_gate_open(tested.text)
        moved = await client.post(STEP_PATH, data=form(
            token_of(tested.text), 3, action="continue", **{**DB_FIELDS, "host": "otherhost"}))
    assert_gate_closed(moved.text)


@pytest.mark.asyncio
async def test_a_failed_test_clears_a_previous_pass(tmp_path, monkeypatch):
    """A second Test that fails drops the proof: the gate is closed again."""
    draft = SetupDraft(step=2)
    draft.save(tmp_path)
    app = build(tmp_path)
    calls = {"n": 0}

    def _run(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return db_step.TestOutcome(code="ok_created", ok=True, version="16.4")
        return db_step.TestOutcome(code="unreachable", ok=False, data={"host": "127.0.0.1",
                                                                       "port": 5432})

    monkeypatch.setattr(db_step, "run_test", _run)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(html), 3, action="test", master_password="secret", **DB_FIELDS))
        assert_gate_open(tested.text)
        failed = await client.post(STEP_PATH, data=form(
            token_of(tested.text), 3, action="test", master_password="secret", **DB_FIELDS))
        assert_gate_closed(failed.text)


@pytest.mark.asyncio
async def test_the_gate_is_closed_over_a_restart_with_the_cluster_block_ticked(tmp_path, monkeypatch):
    """The ticked Federation block follows the same rule across a restart (its own signature)."""
    seed_passing_run(tmp_path, master="local-master",
                     cluster={"path": "existing", "database": "federation",
                              "host": "cluster.example", "port": 5433},
                     cluster_master="", cluster_bot="cluster-bot")
    ok_test(monkeypatch)
    app = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
        assert_gate_closed(html)
        assert_gate_hint_visible(html)
        refused = await client.post(STEP_PATH, data=form(
            token_of(html), 3, action="continue", federation="on", master_password="local-master",
            cluster_password="cluster-bot", cluster_path="existing", cluster_database="federation",
            cluster_host="cluster.example", cluster_port="5433", **DB_FIELDS))
    assert refused.status_code == 200
    assert SENTENCE in refused.text, "the blocked POST must state why, never silence"
    assert SetupDraft.load(tmp_path).step == 2


# ------------------------------------------------------------------------------- step 5: Discord

@pytest.mark.asyncio
async def test_the_discord_gate_is_closed_before_the_token_fetch(tmp_path, monkeypatch):
    guild_reads(monkeypatch)
    seed_discord(tmp_path)
    app = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        assert_gate_closed(page)
        assert_gate_hint_visible(page)
        # a Continue before the read succeeds in this process: refused, nothing advanced
        refused = await client.post(STEP_PATH, data=form(
            token_of(page), 5, action="continue", guild_id=str(GUILD), token=TOKEN,
            owner=str(OWNER)))
        assert refused.status_code == 200
        assert_gate_closed(refused.text)
    assert SetupDraft.load(tmp_path).step == 4


@pytest.mark.asyncio
async def test_the_token_fetch_opens_the_discord_gate(tmp_path, monkeypatch):
    guild_reads(monkeypatch)
    seed_discord(tmp_path)
    app = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(page), 5, action="test", guild_id=str(GUILD), token=TOKEN, owner=str(OWNER)))
        assert tested.status_code == 200, "the Test stays on the step"
        assert_gate_open(tested.text)
        continued = await client.post(STEP_PATH, data=form(
            token_of(tested.text), 5, action="continue", guild_id=str(GUILD), token=TOKEN,
            owner=str(OWNER)))
    assert continued.status_code == 303
    assert SetupDraft.load(tmp_path).step == 5


@pytest.mark.asyncio
async def test_a_changed_guild_id_closes_the_discord_gate(tmp_path, monkeypatch):
    guild_reads(monkeypatch)
    seed_discord(tmp_path)
    app = build(tmp_path)
    other = GUILD + 1
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(page), 5, action="test", guild_id=str(GUILD), token=TOKEN, owner=str(OWNER)))
        assert_gate_open(tested.text)
        moved = await client.post(STEP_PATH, data=form(
            token_of(tested.text), 5, action="continue", guild_id=str(other), token=TOKEN,
            owner=str(OWNER)))
    assert moved.status_code == 200
    assert_gate_closed(moved.text)


@pytest.mark.asyncio
async def test_a_failed_fetch_leaves_the_discord_gate_closed_and_says_why(tmp_path, monkeypatch):
    guild_read_fails(monkeypatch)
    seed_discord(tmp_path)
    app = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "5"})).text
        tested = await client.post(STEP_PATH, data=form(
            token_of(page), 5, action="test", guild_id=str(GUILD), token=TOKEN, owner=str(OWNER)))
        assert_gate_closed(tested.text)
        assert_gate_hint_visible(tested.text)
        assert "Discord refused this token" in tested.text
    assert SetupDraft.load(tmp_path).step == 4


# ------------------------------------------------------------------------------ shared presentation

@pytest.mark.asyncio
async def test_the_same_sentence_and_presentation_on_both_steps(tmp_path, monkeypatch):
    """DISABLED-WITH-REASON, consistently: the one sentence is the same text on both steps."""
    guild_reads(monkeypatch)
    db_dir = tmp_path / "db"
    discord_dir = tmp_path / "discord"
    SetupDraft(step=2).save(db_dir)
    seed_discord(discord_dir)

    async with asgi_client(build(db_dir)) as client:
        await enter(client)
        db_html = (await client.get(STEP_PATH, params={"n": "3"})).text
    async with asgi_client(build(discord_dir)) as client:
        await enter(client)
        discord_html = (await client.get(STEP_PATH, params={"n": "5"})).text

    for html in (db_html, discord_html):
        assert_gate_closed(html)
        assert_gate_hint_visible(html)
    assert tour.T_CONTINUE_NEEDS_TEST == SENTENCE
    assert tour.T_TEST_CONNECTION == "Test connection"


def test_the_sentence_is_translated_in_every_shipped_catalog():
    for lang in CATALOGS:
        path = REPO_ROOT / "locale" / lang / "LC_MESSAGES" / "webui.po"
        catalog = pofile.read_po(open(path, "rb"))
        entry = next((m for m in catalog if m.id == SENTENCE), None)
        assert entry is not None, f"{lang}: the sentence is not in the catalog"
        assert not entry.fuzzy, f"{lang}: the sentence is fuzzy"
        assert entry.string, f"{lang}: the sentence is untranslated"


# ------------------------------------------------------------------------- the gate's LOOK (the CSS)

#: The disabled-submit rule and the hover/press override, exactly as the tour's stylesheet ships
#: them. A disabled control drops the accent fill for the muted panel inside the line-2 ring.
DISABLED_RULE = "button:disabled{background:var(--panel);color:var(--text-3)"
DISABLED_RING = "box-shadow:0 0 0 1px var(--line-2)"
DISABLED_OVERRIDE = "button:disabled:hover,button:disabled:active{"


def test_the_stylesheet_mutes_a_disabled_submit():
    """The look the attribute alone does not give: no accent fill, dimmed, a not-allowed cursor."""
    style = tour._the_style()
    assert DISABLED_RULE in style, "a disabled submit keeps its accent fill"
    assert DISABLED_RING in style, "a disabled submit is not visually separated from the step"
    assert "cursor:not-allowed" in style
    assert "opacity:.6" in style
    assert DISABLED_OVERRIDE in style, "a hover/press on a disabled submit must not restore the accent"
    # the ENABLED button is untouched: the accent rule is the disabled rule's own neighbour
    assert "background:var(--accent)" in style
    assert style.index("button{") < style.index(DISABLED_RULE), "the muted rule must win the cascade"


@pytest.mark.asyncio
async def test_both_gate_steps_serve_the_disabled_submit_rule(tmp_path, monkeypatch):
    """Every document that can draw a disabled Continue carries the rule that mutes it."""
    guild_reads(monkeypatch)
    db_dir = tmp_path / "db"
    discord_dir = tmp_path / "discord"
    SetupDraft(step=2).save(db_dir)
    seed_discord(discord_dir)
    for config_dir, n in ((db_dir, 3), (discord_dir, 5)):
        async with asgi_client(build(config_dir)) as client:
            await enter(client)
            html = (await client.get(STEP_PATH, params={"n": str(n)})).text
        assert_gate_closed(html)
        assert DISABLED_RULE in html
        assert DISABLED_OVERRIDE in html
