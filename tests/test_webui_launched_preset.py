"""The launch record, the label and the announcement (the visibility of a weather preset).

Three things are pinned here, one per part of the visibility:

* the RECORD — two ``servers`` columns (``launched_preset`` / ``launched_preset_temporary``), the
  migration that adds them and the fresh-install path that carries them; the NODE writes them inside
  ``ServerImpl.apply_mission_changes``, when it REALLY applied a per-load preset, and clears them on a
  load that applied none;
* the LABEL — the server page renders the preset name, marks a preset built on the spot as temporary,
  and renders NOTHING when the load applied no preset;
* the ANNOUNCEMENT — a checkbox on the Startup dialog, OFF by default; the NODE sends the player
  message only when it really applied a preset AND the box was ticked.

The record is asserted on the ROW that lands in the database (a recording pool), never on the branch
that decides it. ``launched_preset_record`` in the page is the ONE read of the row; the route tests
replace it so a page render needs no database.

The record lives on the NAMED server (``servers``), never on the instance slot: a slot is rebound from
one server to the next (``Instance.set_server`` -> ``update_server``), so a record kept on the slot
would follow it to a server that never launched with that preset. The rebind test below pins that.
"""
from __future__ import annotations

import inspect
import logging
import os
import re
import sqlparse
import types
from pathlib import Path

import pytest

from core import ServiceRegistry
from core import Status
from core.data.impl.serverimpl import ServerImpl
from services.webservice import readmodels
from services.webservice.pages import actions as actions_page
from services.webservice.pages import server_detail as detail_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, recording_bot,
    sign_in, token_of,
)
from test_webui_write_server_actions import FakeServer

REPO = Path(__file__).resolve().parent.parent
SQL_DIR = REPO / "sql"
TABLES_SQL = SQL_DIR / "tables.sql"
MIGRATION = SQL_DIR / "update_v3.19.sql"
PREVIOUS = SQL_DIR / "update_v3.18.sql"

STARTUP = "/actions/server/startup"
CONFIRM = actions_page.CONFIRM_SUFFIX
ANNOUNCE_FIELD = "announce"

SERVER = "SRS-1"
NODE = "dcs-a-01"


# ------------------------------------------------------------------------------- the node harness

class RecordingPool:
    """A pool double that keeps every statement the record write ran."""

    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def connection(self):
        return _RecordingConn(self)


class _RecordingConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self._pool.statements.append((" ".join(sql.split()), params))
        return None


#: A node preset file BEFORE / AFTER the ``Fresh`` preset was saved on the node.
BEFORE = """\
Configured:
  modify:
    weather:
      clouds:
        base: 1000
"""
AFTER = BEFORE + """\
Fresh:
  modify:
    weather:
      clouds:
        base: 500
"""


def build_mizedit(presets_file, *, settings="Configured"):
    """A REAL ``MizEdit`` bound to a node whose preset file is *presets_file*."""
    from extensions.mizedit.extension import MizEdit

    node = types.SimpleNamespace(config_dir=os.path.dirname(str(presets_file)),
                                 config={"validation": "off"}, pool=None)
    config: dict = {"presets": str(presets_file)}
    if settings is not None:
        config["settings"] = settings
    server = types.SimpleNamespace(status=Status.RUNNING, name=SERVER, node=node)
    return MizEdit(server, config)  # type: ignore[arg-type] - a deliberately minimal Server double


def node_sink(mizedit, *, pool=None, popups=None):
    """The node-side ``self`` ``apply_mission_changes`` needs, plus the record/announce recorders."""

    async def send_popup(recipient, message, **kwargs):
        if popups is not None:
            popups.append((recipient, message))

    return types.SimpleNamespace(
        locals={}, log=logging.getLogger("test.launched.preset"),
        extensions={"MizEdit": mizedit},
        apool=pool, node=types.SimpleNamespace(name=NODE), name=SERVER,
        sendPopupMessage=send_popup,
    )


@pytest.fixture
def no_write(monkeypatch):
    """Replace MizEdit's file writer so the harness never touches a real preset file."""
    from extensions.mizedit.extension import MizEdit

    async def recorder(server, filename, preset, debug=False):
        return None

    monkeypatch.setattr(MizEdit, "apply_presets", staticmethod(recorder))
    return recorder


@pytest.fixture
def mission(tmp_path):
    f = tmp_path / "Training.miz"
    f.write_bytes(b"MIZ")
    return str(f)


def _record_statement(pool):
    """The single ``UPDATE servers`` the record write ran, or fail loudly."""
    assert len(pool.statements) == 1, f"expected one statement, got {pool.statements}"
    sql, params = pool.statements[0]
    assert sql.startswith("UPDATE servers SET launched_preset"), sql
    return sql, params


# ------------------------------------------------------------------- 1. the migration and the base

def _statements(path):
    return [s.strip() for s in sqlparse.split(path.read_text(encoding="utf-8")) if s.strip()]


def _target_version(path: Path) -> str | None:
    """The version a migration file moves the database to."""
    found = re.search(r"UPDATE version SET version='(v[\d.]+)'", path.read_text(encoding="utf-8"))
    return found.group(1) if found else None


def test_the_migration_is_named_for_the_version_the_loader_runs():
    """The loader runs ``sql/update_<db_version>.sql`` and each file moves the version on.

    ``update_v3.18.sql`` leaves the database at ``v3.19``, so the file that runs NEXT is
    ``update_v3.19.sql`` — and it must be the one that carries this change. Later migrations are
    legitimate; what must hold is that the chain is CONTIGUOUS from here on, because the loader stops at
    the first version whose file is missing, and that no later file rewrites these columns.
    """
    assert PREVIOUS.exists(), "the version the loader is at was not found"
    assert "version='v3.19'" in PREVIOUS.read_text(encoding="utf-8")
    assert MIGRATION.name == "update_v3.19.sql"
    assert MIGRATION.exists()
    assert _target_version(MIGRATION) == "v3.20"
    current = MIGRATION
    while True:
        following = SQL_DIR / f"update_{_target_version(current)}.sql"
        if not following.exists():
            break
        current = following
    later = [path.name for path in SQL_DIR.glob("update_v*.sql")
             if path != MIGRATION and "launched_preset" in path.read_text(encoding="utf-8")]
    assert later == [], f"a later migration touches the launch-preset columns: {later}"


def test_the_migration_adds_both_columns_and_moves_the_version_on():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE servers ADD COLUMN IF NOT EXISTS launched_preset TEXT NULL" in _statements_text(MIGRATION)
    assert ("ALTER TABLE servers ADD COLUMN IF NOT EXISTS launched_preset_temporary "
            "BOOLEAN NOT NULL DEFAULT FALSE") in _statements_text(MIGRATION)
    assert "UPDATE version SET version='v3.20'" in _statements_text(MIGRATION)
    # PURE ADDITIONS: nothing is dropped or rewritten, so every existing row keeps its meaning
    assert re.search(r"\b(DROP|DELETE|TRUNCATE)\b", sql, re.I) is None, sql


def _statements_text(path):
    return " ".join(_statements(path))


def test_a_fresh_install_from_tables_sql_carries_both_columns():
    """``tables.sql`` is what a NEW install gets, so the two paths must agree."""
    sql = TABLES_SQL.read_text(encoding="utf-8")
    block = re.search(r"CREATE TABLE IF NOT EXISTS servers \((.*?)\);", sql, re.S)
    assert block, "the servers table is not in sql/tables.sql"
    body = block.group(1)
    assert "launched_preset TEXT NULL" in body
    assert "launched_preset_temporary BOOLEAN NOT NULL DEFAULT FALSE" in body
    # the record is NOT on the instance slot: a slot is rebound from one server to the next, so the
    # fact must not live on the slot the server occupies
    instances = re.search(r"CREATE TABLE IF NOT EXISTS instances \((.*?)\);", sql, re.S)
    assert instances, "the instances table is not in sql/tables.sql"
    assert "launched_preset" not in instances.group(1), \
        "the launched-preset record must not be declared on the instances table"
    # A fresh install must land where the migration chain lands: whatever version the NEWEST
    # migration sets, ``tables.sql`` already declares — never a hardcoded release string.
    newest = max(SQL_DIR.glob("update_v3.*.sql"),
                 key=lambda path: [int(part) for part in re.findall(r"\d+", path.stem)])
    final = re.search(r"UPDATE version SET version='([^']+)'", newest.read_text(encoding="utf-8"))
    assert final, f"{newest.name} never sets the schema version"
    assert f"VALUES ('{final.group(1)}')" in sql
    assert _statements_text(PREVIOUS).endswith("UPDATE version SET version='v3.19';")


def test_the_new_columns_are_additive_for_every_other_consumer(tmp_path):
    """THE MIGRATION'S BLAST RADIUS: every other statement on ``servers`` reads its rows by NAME.

    The record moved onto ``servers``, so the columns are new THERE. Adding columns changes nothing
    for a consumer that lists the columns it wants; it would break a positional read (``SELECT *``)
    or an ``INSERT`` that relied on the column ORDER. So every ``servers`` statement outside this
    change must name its columns, and none may use ``*``.
    """
    statements = []
    for path in list((REPO / "core").rglob("*.py")) + list((REPO / "services").rglob("*.py")) \
            + list((REPO / "plugins").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(?:FROM|INTO|UPDATE|TABLE)\s+servers\b", text, re.I):
            # the SQL text around the table name, up to the closing quote of the statement
            window = text[max(0, match.start() - 120):match.end() + 240]
            statements.append((path, window))
    assert statements, "no consumer of the servers table was found - the scan is broken"
    offenders = [(str(path), window) for path, window in statements
                 if re.search(r"\bSELECT\s+\*", window, re.I)
                 or re.search(r"INTO\s+servers\s+VALUES", window, re.I)]
    assert not offenders, f"a positional read/write of the servers table would break: {offenders}"


# ------------------------------------------------------------------- 2. the record, on the row

@pytest.mark.asyncio
async def test_a_named_preset_is_recorded_on_the_servers_row(tmp_path, no_write, mission):
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER, encoding="utf-8")
    miz = build_mizedit(presets)
    pool = RecordingPool()
    sink = node_sink(miz, pool=pool)

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset="Fresh")

    sql, params = _record_statement(pool)
    assert params == ("Fresh", False, SERVER), params


@pytest.mark.asyncio
async def test_a_temporary_preset_is_recorded_as_temporary(tmp_path, no_write, mission):
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER, encoding="utf-8")
    miz = build_mizedit(presets)
    pool = RecordingPool()
    sink = node_sink(miz, pool=pool)
    doc = {"modify": {"weather": {"clouds": {"base": 250}}}}

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset=doc)

    sql, params = _record_statement(pool)
    assert params == (None, True, SERVER), params


@pytest.mark.asyncio
async def test_a_load_with_no_preset_clears_the_record(tmp_path, no_write, mission):
    """A load that carries no preset CLEARS both columns — the record is never the previous load's."""
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER, encoding="utf-8")
    miz = build_mizedit(presets)
    pool = RecordingPool()
    sink = node_sink(miz, pool=pool)

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False)

    sql, params = _record_statement(pool)
    assert params == (None, False, SERVER), params


@pytest.mark.asyncio
async def test_a_name_the_instance_does_not_carry_clears_the_record(tmp_path, no_write, mission):
    """THE TRUTHFUL EDGE: a picked name the instance does not carry applies nothing, so nothing is
    recorded — the label must not appear for it."""
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER, encoding="utf-8")
    miz = build_mizedit(presets)
    pool = RecordingPool()
    sink = node_sink(miz, pool=pool)

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset="Nope")

    sql, params = _record_statement(pool)
    assert params == (None, False, SERVER), params


@pytest.mark.asyncio
async def test_a_list_of_names_is_recorded_as_the_combination(tmp_path, no_write, mission):
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER + """\
Other:
  modify:
    weather:
      clouds:
        base: 700
""", encoding="utf-8")
    miz = build_mizedit(presets)
    pool = RecordingPool()
    sink = node_sink(miz, pool=pool)

    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset=["Fresh", "Other"])

    sql, params = _record_statement(pool)
    assert params == ("Fresh, Other", False, SERVER), params


# ------------------------------------------------------ 2b. the rebind leak (record vs. slot)

OTHER = "SRS-2"
INSTANCE_SLOT = "Host-A-Instance"


class SlotDB:
    """A minimal in-memory model of the two tables the record touches.

    ``servers`` is keyed by ``server_name`` (one row per NAMED server, the row the node created at
    registration). ``instances`` is keyed by ``(node, instance)`` and carries a ``server_name``
    binding that the node REBINDS when a slot is handed from one server to the next
    (``Instance.set_server`` -> ``update_server``). Every statement is routed to the TABLE IT NAMES,
    so the SAME model runs a tree that keeps the record on ``instances`` and one that moved it to
    ``servers`` — which is what lets the rebind test fail only when the record can follow a slot.
    """

    def __init__(self):
        self.servers = {name: {"server_name": name, "launched_preset": None,
                               "launched_preset_temporary": False}
                        for name in (SERVER, OTHER)}
        # one slot on the node, currently bound to server A
        self.instances = {(NODE, INSTANCE_SLOT): {"node": NODE, "instance": INSTANCE_SLOT,
                                                  "server_name": SERVER}}

    def connection(self):
        return _SlotConn(self)


def _one(pattern, text):
    match = re.search(pattern, text)
    assert match, f"{pattern!r} did not match {text!r}"
    return match.group(1)


class _SlotConn:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split())
        params = tuple(params or ())
        if s.startswith("UPDATE"):
            table = _one(r"UPDATE\s+(\w+)", s)
            head, where = s.split("WHERE", 1)
            set_cols = re.findall(r"(\w+)\s*=\s*%s", head)
            where_cols = re.findall(r"(\w+)\s*=\s*%s", where)
            set_vals, where_vals = params[:len(set_cols)], params[len(set_cols):]
            for row in self._rows(table):
                if all(row.get(c) == v for c, v in zip(where_cols, where_vals)):
                    row.update(dict(zip(set_cols, set_vals)))
            return _Cursor(None)
        if s.startswith("SELECT"):
            table = _one(r"FROM\s+(\w+)", s)
            cols = [c.strip() for c in _one(r"SELECT\s+(.*?)\s+FROM", s).split(",")]
            where_cols = re.findall(r"(\w+)\s*=\s*%s", s)
            for row in self._rows(table):
                if all(row.get(c) == v for c, v in zip(where_cols, params)):
                    return _Cursor([row.get(c) for c in cols])
            return _Cursor(None)
        if s.startswith("DELETE"):
            table = _one(r"DELETE FROM\s+(\w+)", s)
            where_cols = re.findall(r"(\w+)\s*=\s*%s", s)
            rows = self._table(table)
            for key in [k for k, row in rows.items()
                        if all(row.get(c) == v for c, v in zip(where_cols, params))]:
                del rows[key]
            return _Cursor(None)
        raise AssertionError(f"unexpected statement: {s}")

    def _table(self, table):
        if table == "servers":
            return self._db.servers
        if table == "instances":
            return self._db.instances
        raise AssertionError(f"unexpected table: {table}")

    def _rows(self, table):
        return list(self._table(table).values())


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


@pytest.mark.asyncio
async def test_a_rebound_slot_does_not_hand_the_record_to_the_next_server(tmp_path, no_write, mission,
                                                                          monkeypatch):
    """RED while the record lives on the instance slot: server A launches with a preset, the slot it
    ran in is rebound to server B, and B's page then shows A's preset — a claim B never made. GREEN
    once the record lives on ``servers``: B has no record, so B shows NO label.
    """
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER, encoding="utf-8")
    db = SlotDB()

    # 1. server A launches with a named preset — the node writes the record
    sink = node_sink(build_mizedit(presets), pool=db)
    await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset="Fresh")

    # 2. the slot A ran in is handed to server B (the node's own ``update_server`` rebind): the row's
    #    binding moves to B and NOTHING clears the launch record
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE instances SET server_name = %s WHERE node = %s AND instance = %s",
            (OTHER, NODE, INSTANCE_SLOT))

    # 3. B's page reads the record through the PAGE's OWN read, never the writer's return value
    fake_bus = types.SimpleNamespace(apool=db)
    monkeypatch.setattr(ServiceRegistry, "get", lambda service: fake_bus)
    server_b = types.SimpleNamespace(name=OTHER, node=types.SimpleNamespace(name=NODE))

    name, temporary = await detail_page.launched_preset_record(server_b)

    assert (name, temporary) == ("", False), \
        "server B must show NO label: it never launched with a preset"


# ------------------------------------------------------------------- 3. the announcement

@pytest.mark.asyncio
async def test_the_announcement_is_sent_only_when_applied_and_ticked(tmp_path, no_write, mission):
    """All four cases: applied+ticked sends; applied+off sends nothing; not-applied+ticked sends
    nothing; the no-preset load sends nothing even with the box on."""
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER, encoding="utf-8")
    doc = {"modify": {"weather": {"clouds": {"base": 250}}}}

    async def run(*, preset, announce):
        popups: list = []
        sink = node_sink(build_mizedit(presets), pool=RecordingPool(), popups=popups)
        await ServerImpl.apply_mission_changes(sink, mission, use_orig=False, preset=preset,
                                               announce=announce)
        return popups

    applied_ticked = await run(preset=doc, announce=True)
    assert len(applied_ticked) == 1, "a really-applied preset with the box ticked must announce"
    assert applied_ticked[0][1], "the popup must carry a message"

    assert await run(preset=doc, announce=False) == [], "the box off must send nothing"
    assert await run(preset="Nope", announce=True) == [], \
        "a name the instance does not carry applied nothing, so nothing may be announced"
    assert await run(preset=None, announce=True) == [], \
        "no preset asked for means nothing was applied, so nothing may be announced"


# ------------------------------------------------------------------- 4. the label (read model)

def test_the_read_model_renders_the_name_and_the_temporary_mark():
    named = readmodels.launched_preset_view("Summer", False)
    assert named is not None and named.name == "Summer" and named.temporary is False

    temporary = readmodels.launched_preset_view(None, True)
    assert temporary is not None and temporary.name == "" and temporary.temporary is True


def test_the_read_model_returns_nothing_when_no_preset_was_applied():
    assert readmodels.launched_preset_view(None, False) is None
    assert readmodels.launched_preset_view("", False) is None


# ------------------------------------------------------------------- 5. the rendered page

#: a running server with a mission, so the Overview tab (and its weather block) renders
def page_server():
    mission_obj = types.SimpleNamespace(name="Training", display_name="Training", mission_time=60,
                                        map="Caucasus",
                                        weather={"season": {"temperature": 18.2}, "qnh": 760.0,
                                                 "wind": {"atGround": {"dir": 180, "speed": 5.0}}},
                                        clouds={"preset": {"readableName": "Preset/Fair Weather"}},
                                        date=None, start_time=0, num_slots_blue=0, num_slots_red=0)
    a = FakeServer(SERVER, status=Status.RUNNING, managed_by=["Host A"])
    a.current_mission = mission_obj
    a.node = types.SimpleNamespace(name=NODE)
    a.port = 10309
    a.locals = {"managed_by": ["Host A"]}
    return a


def fleet(server_obj):
    node = types.SimpleNamespace(name=NODE, master=True,
                                 instances={"Host-A-Instance": instance("Host-A-Instance", server_obj)})
    return Cluster([server_obj], nodes={NODE: node}, node=node,
                   instances=[instance("Host-A-Instance", server_obj)])


async def open_overview(app):
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get(f"/fleet/{SERVER}?tab=overview")
        assert response.status_code == 200, response.text
        return response.text


@pytest.mark.asyncio
async def test_the_page_shows_the_preset_name_beside_the_weather(config_dir, clean_registry,
                                                                 monkeypatch):
    async def record(server):
        return "Summer", False

    monkeypatch.setattr(detail_page, "launched_preset_record", record)
    html = await open_overview(build(config_dir, fleet(page_server())))

    assert "Launched with weather preset" in html
    assert "<b>Summer</b>" in html


@pytest.mark.asyncio
async def test_the_page_marks_a_temporary_preset_as_temporary(config_dir, clean_registry,
                                                              monkeypatch):
    async def record(server):
        return "", True

    monkeypatch.setattr(detail_page, "launched_preset_record", record)
    html = await open_overview(build(config_dir, fleet(page_server())))

    assert "Launched with a temporary weather preset" in html


@pytest.mark.asyncio
async def test_the_page_shows_no_label_when_no_preset_was_applied(config_dir, clean_registry,
                                                                  monkeypatch):
    async def record(server):
        return "", False

    monkeypatch.setattr(detail_page, "launched_preset_record", record)
    html = await open_overview(build(config_dir, fleet(page_server())))

    assert "Launched with weather preset" not in html
    assert "Launched with a temporary weather preset" not in html


@pytest.mark.asyncio
async def test_the_label_escapes_a_hostile_preset_name(config_dir, clean_registry, monkeypatch):
    hostile = "<script>alert(1)</script>"

    async def record(server):
        return hostile, False

    monkeypatch.setattr(detail_page, "launched_preset_record", record)
    html = await open_overview(build(config_dir, fleet(page_server())))

    assert "<script>alert(1)</script>" not in html, "operator data must be escaped in the label"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# ------------------------------------------------------------------- 6. the dialog and the wire

def test_neither_launch_dialog_declares_the_announce_box():
    """The box is GONE from both declarations; the node's ``announce`` parameter is not."""
    for key in ("startup", "mission_load"):
        action = next(action for action in (*actions_page.SERVER_ACTIONS,
                                            *actions_page.MISSION_ACTIONS)
                      if action.key == key)
        fields = [option.field for option in actions_page.dialog_options(action)]
        assert ANNOUNCE_FIELD not in fields, f"{key} still declares the announce box"


def test_the_startup_action_declares_announce_defaulting_to_false():
    from plugins.mission.actions import startup_server

    params = inspect.signature(startup_server).parameters
    assert "announce" in params and params["announce"].default is False


@pytest.mark.asyncio
async def test_the_website_never_states_announce(config_dir, clean_registry):
    """The dialog draws no box, so a start up from the website adds no ``announce`` parameter — while
    the ACTION keeps the parameter for the callers that do state it (a direct / REST / MCP caller)."""
    (config_dir / "presets.yaml").write_text(AFTER, encoding="utf-8")
    from test_webui_startup_weather_picker import PresetNode, node_fleet

    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        dialog = await http.post(STARTUP + CONFIRM,
                                 data={"server": SERVER, "_csrf_token": token_of(page)})
        assert dialog.status_code == 200, dialog.text
        assert f'name="{ANNOUNCE_FIELD}"' not in dialog.text, "the dialog drew the announce box"
        assert "Tell the players the weather changed" not in dialog.text
        # even a POST that still carries the field reaches the action without it
        reply = await http.post(STARTUP, data={"server": SERVER,
                                               "_csrf_token": token_of(dialog.text),
                                               ANNOUNCE_FIELD: "1"})
    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert "announce" not in call, "the website must not state announce any more"

    from plugins.mission.actions import startup_server

    params = inspect.signature(startup_server).parameters
    assert "announce" in params and params["announce"].default is False


@pytest.mark.asyncio
async def test_the_rpc_carries_announce_only_when_true():
    """On the wire ``announce`` is an ABSENT key when it is False, so a no-announce RPC is unchanged."""
    from core.data.proxy.serverproxy import ServerProxy

    sent: list[dict] = []

    class FakeBus:
        async def send_to_node_sync(self, message, timeout=None, node=None):
            sent.append(message)
            return None

    proxy = types.SimpleNamespace(bus=FakeBus(), name=SERVER,
                                  node=types.SimpleNamespace(name=NODE, slow_system=False))
    await ServerProxy.startup(proxy, announce=False)          # type: ignore[arg-type]
    await ServerProxy.startup(proxy, announce=True)           # type: ignore[arg-type]
    assert "announce" not in sent[0]["params"], sent[0]["params"]
    assert sent[1]["params"]["announce"] is True

    sent.clear()
    await ServerProxy.loadMission(proxy, "Training.miz", modify_mission=False)
    await ServerProxy.loadMission(proxy, "Training.miz", modify_mission=False, announce=True)
    assert "announce" not in sent[0]["params"]
    assert sent[1]["params"]["announce"] is True


# ------------------------------------------------------------------- 7. live PostgreSQL (opt-in)

LIVE_URL = os.environ.get("DCSSB_TEST_PG_MASTER_URL")


@pytest.mark.skipif(not LIVE_URL, reason="no DCSSB_TEST_PG_MASTER_URL -- live DDL not exercised here")
def test_live_both_paths_create_the_columns(tmp_path):
    """Apply the two paths against a throwaway PostgreSQL: a fresh install and the migration.

    Run with ``DCSSB_TEST_PG_MASTER_URL=postgres://postgres:***@127.0.0.1:5432/postgres pytest -k live``.
    The DDL is the repository's own; the role and database are unique and removed afterwards.
    """
    import uuid
    from urllib.parse import urlparse

    import psycopg
    from core.utils import db_setup
    from core.utils.db_setup import DatabaseProvisionIO

    class _IO(DatabaseProvisionIO):
        def __init__(self, user, db, password):
            self._u, self._d, self._p = user, db, password

        def master_credentials(self):
            return self._d, self._u

        def master_password(self, master_user):
            return self._p

    parsed = urlparse(LIVE_URL)
    suffix = uuid.uuid4().hex[:8]
    user, database = f"dcssb_s3_{suffix}", f"dcssb_s3_{suffix}"
    config_dir = tmp_path / "config"
    (config_dir / ".secret").mkdir(parents=True)
    io = _IO(parsed.username, parsed.path.lstrip("/"), parsed.password or "")
    result = db_setup.provision_database(parsed.hostname, parsed.port or 5432, user, database,
                                         str(config_dir), io)
    assert result is not None, "provisioning failed against the configured PostgreSQL"

    from core import utils
    url = db_setup.connection_url(user, utils.get_password("database", str(config_dir)),
                                  parsed.hostname, parsed.port or 5432, database)
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            # a FRESH install: the base schema alone carries the columns
            for statement in _statements(TABLES_SQL):
                conn.execute(statement)
            columns = _column_names(conn)
            assert {"launched_preset", "launched_preset_temporary"} <= columns, columns
            assert not ({"launched_preset", "launched_preset_temporary"}
                        & _column_names(conn, "instances")), \
                "the record must not be declared on the instances table"
            assert conn.execute("SELECT version FROM version").fetchone()[0] == "v3.18"

            # the MIGRATION path: applied by the loader's own convention, idempotent on a re-run
            for _ in range(2):
                for statement in _statements(PREVIOUS) + _statements(MIGRATION):
                    conn.execute(statement)
            assert {"launched_preset", "launched_preset_temporary"} <= _column_names(conn)
            assert conn.execute("SELECT version FROM version").fetchone()[0] == "v3.20"

            # DELETING the server takes its record: the columns live ON the servers row, so the
            # server's own DELETE removes them with it (no separate cleanup, no orphaned record)
            conn.execute("INSERT INTO servers (server_name) VALUES ('doomed')")
            conn.execute("UPDATE servers SET launched_preset = 'Fresh', "
                         "launched_preset_temporary = FALSE WHERE server_name = 'doomed'")
            conn.execute("DELETE FROM servers WHERE server_name = 'doomed'")
            assert conn.execute(
                "SELECT launched_preset FROM servers WHERE server_name = 'doomed'").fetchone() is None, \
                "deleting a server must remove its launch record with it"
    finally:
        with psycopg.connect(LIVE_URL, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{database}"')
            conn.execute(f'DROP ROLE IF EXISTS "{user}"')


def _column_names(conn, table="servers"):
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,)).fetchall()
    return {row[0] for row in rows}
