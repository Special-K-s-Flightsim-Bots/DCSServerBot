"""The three indexes the public site reads through: one migration per owning plugin, plus the
version step that makes the loader run it.

``core/plugin.py::_init_db`` walks ``plugins/<plugin>/db/update_v<installed>.sql`` one revision per
step while ``installed < plugins.<plugin>.__version__``. A migration file whose version was never
bumped does not run, and a bumped version with no file steps over a revision silently. Each case
below pins both halves for one plugin; the chain tests pin the top of each version line.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import sqlparse

REPO = Path(__file__).resolve().parents[1]

#: plugin, migration file, the version the plugin must declare, the exact index statement the file
#: carries, and how many statements the file holds (the userstats hop creates the server-board
#: aggregate, puts its unique index in place, then refreshes it)
CASES = [
    (
        "userstats",
        "update_v3.14.sql",
        "3.15",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_serverstats ON mv_serverstats (server_name);",
        19,
    ),
    (
        "mission",
        "update_v3.21.sql",
        "3.22",
        "CREATE INDEX IF NOT EXISTS idx_players_name_lower ON players (LOWER(name));",
        1,
    ),
    (
        "gamemaster",
        "update_v3.7.sql",
        "3.8",
        "CREATE INDEX IF NOT EXISTS idx_campaigns_servers_server ON campaigns_servers (server_name);",
        1,
    ),
]


def _db_dir(plugin: str) -> Path:
    return REPO / "plugins" / plugin / "db"


def _declared_version(plugin: str) -> str:
    text = (REPO / "plugins" / plugin / "version.py").read_text(encoding="utf-8")
    found = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    assert found, f"plugins/{plugin}/version.py carries no __version__"
    return found.group(1)


def _revisions(plugin: str) -> list[tuple[int, int]]:
    out = []
    for path in _db_dir(plugin).glob("update_v*.sql"):
        found = re.fullmatch(r"update_v(\d+)\.(\d+)\.sql", path.name)
        if found:
            out.append((int(found.group(1)), int(found.group(2))))
    return sorted(out)


def _statements(path: Path) -> list[str]:
    """Split the file the way the loader does: ``sqlparse.split`` then drop blank statements."""
    text = path.read_text(encoding="utf-8")
    return [stmt.strip() for stmt in sqlparse.split(text, encoding="utf-8") if stmt.strip()]


def _executable(statement: str) -> str:
    """The statement with its ``--`` comment lines removed, comments inside a statement kept."""
    return "\n".join(line for line in statement.splitlines()
                     if not line.strip().startswith("--")).strip()


def _body(path: Path, name: str | None = None) -> str:
    """The executable text of ONE ``CREATE INDEX`` statement of the file.

    *name* selects the index by its own identifier: a merged revision carries every view's index, so
    "the first index statement" is not the one a case is about. A hop may also create the object it
    indexes first and refresh it afterwards, so the index is not necessarily the last statement.
    """
    for statement in _statements(path):
        body = _executable(statement)
        if re.match(r"CREATE (UNIQUE )?INDEX", body, re.I):
            if name is None or name in body:
                return body
    raise AssertionError(f"{path} carries no CREATE INDEX statement for {name or 'any index'}")


def _index_name(statement: str) -> str:
    """The index identifier a case pins, read out of the statement it compares against."""
    found = re.search(r"\b(idx_\w+)", statement)
    assert found, f"the pinned statement names no index: {statement!r}"
    return found.group(1)


def _normalised(statement: str) -> str:
    """The statement with ``--`` comment lines dropped and all whitespace runs collapsed to one space."""
    lines = [line for line in statement.splitlines() if not line.strip().startswith("--")]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


@pytest.mark.parametrize("plugin, filename, version, statement, count", CASES)
def test_the_migration_is_the_next_step_of_the_chain(plugin, filename, version, statement, count):
    """The file names the version the loader is at, and the bump makes that version its target."""
    migration = _db_dir(plugin) / filename
    assert migration.exists(), f"{migration} is missing, so the index never reaches an install"
    found = re.search(r"update_v(\d+\.\d+)", filename)
    assert found, f"{filename} is not named update_v<major>.<minor>.sql"
    revision = tuple(int(part) for part in found.group(1).split("."))
    assert _declared_version(plugin) == version
    assert revision == _revisions(plugin)[-1], "the new file is not the newest of its chain"
    # the loader runs update_v<installed>.sql and then bumps one revision per file
    assert version == f"{revision[0]}.{revision[1] + 1}"


@pytest.mark.parametrize("plugin, filename, version, statement, count", CASES)
def test_the_loader_would_run_this_file(plugin, filename, version, statement, count):
    """Walk the loader's loop from the previous version: exactly one step, and it is this file."""
    migration = _db_dir(plugin) / filename
    major, previous_minor = (int(part) for part in version.split("."))
    installed = f"{major}.{previous_minor - 1}"
    seen = []
    while tuple(int(p) for p in installed.split(".")) < (major, previous_minor):
        step_file = _db_dir(plugin) / f"update_v{installed}.sql"
        assert step_file.exists(), f"the chain has no file for v{installed}"
        seen.append(step_file)
        step_major, step_minor = installed.split(".")
        installed = f"{step_major}.{int(step_minor) + 1}"
    assert seen == [migration]


@pytest.mark.parametrize("plugin, filename, version, statement, count", CASES)
def test_each_file_splits_into_its_intended_statements(plugin, filename, version, statement, count):
    """The loader's split yields exactly the intended statements, comments not glued to them."""
    statements = _statements(_db_dir(plugin) / filename)
    assert len(statements) == count, f"{filename} holds {len(statements)} statements"
    body = _body(_db_dir(plugin) / filename, _index_name(statement))
    assert body.count(";") == 1 and body.endswith(";")
    assert body == statement


@pytest.mark.parametrize("plugin, filename, version, statement, count", CASES)
def test_the_statement_is_a_plain_create_index(plugin, filename, version, statement, count):
    """A transaction-block-hostile form cannot run through the loader's single connection."""
    body = _body(_db_dir(plugin) / filename, _index_name(statement))
    assert re.match(r"CREATE (UNIQUE )?INDEX IF NOT EXISTS", body.upper())
    assert "CONCURRENTLY" not in body.upper()
    assert not re.search(r"\b(BEGIN|COMMIT|ROLLBACK|TRANSACTION)\b", body, re.I)


@pytest.mark.parametrize("plugin, filename, version, statement, count", CASES)
def test_no_extension_and_no_second_object(plugin, filename, version, statement, count):
    """No ``pg_trgm`` (it needs superuser rights) and no index beyond the one named."""
    body = _body(_db_dir(plugin) / filename, _index_name(statement))
    assert "pg_trgm" not in body.lower()
    assert "CREATE EXTENSION" not in body.upper()
    assert body.upper().count("INDEX IF NOT EXISTS") == 1


@pytest.mark.parametrize("plugin, filename, version, statement, count", CASES)
def test_the_index_lands_where_the_public_site_reads(plugin, filename, version, statement, count):
    """The supported lookup shapes only: prefix match on the lowered name, not a contains match."""
    body = _body(_db_dir(plugin) / filename, _index_name(statement))
    if plugin == "userstats":
        assert "ON mv_serverstats (server_name)" in body
    if plugin == "mission":
        assert "ON players (LOWER(name))" in body
        assert "missions" not in body, "missions(server_name) already has idx_missions_server_name"
    if plugin == "gamemaster":
        assert "ON campaigns_servers (server_name)" in body


@pytest.mark.parametrize("plugin, filename, version, statement, count", CASES)
def test_a_fresh_install_creates_the_same_index(plugin, filename, version, statement, count):
    """A fresh install builds its schema from tables.sql and never walks the update chain."""
    tables = _db_dir(plugin) / "tables.sql"
    assert tables.exists(), f"{tables} is missing, so a new install has no schema at all"
    statements = _statements(tables)
    wanted = _normalised(statement)
    found = [_normalised(stmt) for stmt in statements if _normalised(stmt) == wanted]
    assert found, (f"plugins/{plugin}/db/tables.sql carries no statement equal to {wanted!r}; "
                   f"a fresh install would never create this index")
    tables_text = _normalised(tables.read_text(encoding="utf-8"))
    assert tables_text.count(wanted) == 1, f"{wanted!r} appears more than once in {tables}"


@pytest.mark.parametrize("plugin, filename, version, statement, count", CASES)
def test_the_fresh_install_copy_matches_the_migration_copy(plugin, filename, version, statement, count):
    """Both halves are the identical statement, so a fresh install and a migrated install cannot drift."""
    wanted = _normalised(statement)
    assert wanted == _normalised(_body(_db_dir(plugin) / filename, _index_name(statement)))
    assert wanted in (_normalised(stmt) for stmt in _statements(_db_dir(plugin) / "tables.sql"))


@pytest.mark.parametrize("plugin", [case[0] for case in CASES])
def test_the_version_line_is_the_revision_above_the_newest_file(plugin):
    """A bumped version with no file steps over a revision; a stale version never runs the file."""
    major, minor = (int(part) for part in _declared_version(plugin).split("."))
    newest = _revisions(plugin)[-1]
    assert newest[0] == major
    assert newest[1] + 1 == minor, f"plugins/{plugin}/version.py is {major}.{minor} above v{newest[0]}.{newest[1]}"


#: the aggregates the public site reads, the unique index each one needs before the hourly refresh
#: can run CONCURRENTLY, and the migration hop that introduces it.
VIEWS = {
    "mv_serverstats": ("idx_mv_serverstats", "(server_name)", "update_v3.14.sql"),
    "mv_site_activity": ("idx_mv_site_activity", "(server_name, week_start)", "update_v3.14.sql"),
    "mv_site_pilots": ("idx_mv_site_pilots", "(server_name, player_ucid)", "update_v3.14.sql"),
    "mv_site_missions": ("idx_mv_site_missions", "(server_name)", "update_v3.14.sql"),
    "mv_site_pilot_totals": ("idx_mv_site_pilot_totals", "(server_name, player_ucid)",
                             "update_v3.14.sql"),
    "mv_site_pilot_aircraft": ("idx_mv_site_pilot_aircraft",
                               "(server_name, player_ucid, slot)", "update_v3.14.sql"),
    "mv_site_aircraft": ("idx_mv_site_aircraft", "(server_name, slot)", "update_v3.14.sql"),
    "mv_site_hours": ("idx_mv_site_hours", "(server_name, dow, hour)", "update_v3.14.sql"),
}
MIGRATION = "update_v3.14.sql"


def _view_statement(statements: list[str], name: str) -> str | None:
    """The statement that CREATES *name*, by the object its own head names.

    A migration's leading comment may name a view the statement it precedes does not create, so the
    match is on the first executable line, not on any mention.
    """
    for statement in statements:
        body = "\n".join(line for line in statement.splitlines()
                          if not line.strip().startswith("--"))
        found = re.match(r"CREATE MATERIALIZED VIEW (?:IF NOT EXISTS )?(\w+)", body.strip(), re.I)
        if found and found.group(1) == name:
            return _normalised(statement)
    return None


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_each_aggregate_is_created_by_the_migration_and_by_a_fresh_install(view):
    """Both halves carry the same view: an upgraded install and a new one cannot drift."""
    migration = _db_dir("userstats") / VIEWS[view][2]
    tables = _db_dir("userstats") / "tables.sql"
    migrated = _view_statement(_statements(migration), view)
    fresh = _view_statement(_statements(tables), view)
    assert migrated, f"{migration} creates no {view}"
    assert fresh, f"{tables} creates no {view}"
    assert migrated == fresh


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_each_aggregate_has_the_unique_index_concurrently_needs(view):
    """REFRESH ... CONCURRENTLY refuses a view without a unique index over all its rows."""
    index, columns, hop = VIEWS[view]
    wanted = f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {view} {columns};"
    for path in (_db_dir("userstats") / hop, _db_dir("userstats") / "tables.sql"):
        statements = [_normalised(statement) for statement in _statements(path)]
        assert wanted in statements, f"{path} carries no unique index for {view}"


def test_the_migration_creates_its_views_without_dropping_anything():
    """The hop is the first revision that has these views, so it creates and never replaces them.

    ``IF NOT EXISTS`` keeps the hop safe to re-run on an install whose version lagged behind, and a
    ``DROP`` here would take a working board away from a live console for no gain.
    """
    migration = _db_dir("userstats") / MIGRATION
    statements = [_normalised(statement) for statement in _statements(migration)]
    assert not [one for one in statements if one.upper().startswith("DROP")], (
        f"{MIGRATION} drops an object the release already serves")
    created = [one for one in statements if one.upper().startswith("CREATE MATERIALIZED VIEW")]
    assert created and all("IF NOT EXISTS" in one.upper() for one in created), (
        "a view in the hop is created without IF NOT EXISTS, so a re-run aborts the migration")


def test_the_weekly_view_carries_the_closed_session_sums():
    """The strip's seconds-over-closed-sessions figures are summed, never recomputed."""
    body = _view_statement(_statements(_db_dir("userstats") / MIGRATION), "mv_site_activity") or ""
    assert "server_name" in body and "week_start" in body
    assert "playtime" in body and "pilots" in body
    assert "closed_playtime" in body and "closed_sessions" in body and "takeoffs" in body
    # an open session has no duration to add: the sums are over closed sessions only
    assert "FILTER (WHERE s.hop_off IS NOT NULL)" in body


def test_the_pilot_view_keeps_the_two_weeks_the_readouts_ask_about():
    """The readouts name this ISO week and the one before it, so the key set carries both."""
    body = _view_statement(_statements(_db_dir("userstats") / MIGRATION), "mv_site_pilots") or ""
    assert "player_ucid" in body
    assert "last_week" in body and "prev_week" in body


def test_the_mission_view_counts_only_missions_that_were_flown():
    """A mission with no statistics row is not "flown", which is what the readout means."""
    body = _view_statement(_statements(_db_dir("userstats") / MIGRATION), "mv_site_missions") or ""
    assert "EXISTS (SELECT 1 FROM statistics" in body


def test_the_userstats_plugin_refreshes_every_view_in_one_place():
    """One hourly task keeps the server board and the three readout aggregates current."""
    source = (REPO / "plugins" / "userstats" / "commands.py").read_text(encoding="utf-8")
    start = source.index("async def refresh_views")
    end = source.index("async def before_refresh_views", start)
    body = source[start:end]
    for view in VIEWS:
        assert f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view};" in body, view
        assert f"REFRESH MATERIALIZED VIEW {view};" in body, view
    restapi = (REPO / "plugins" / "restapi" / "commands.py").read_text(encoding="utf-8")
    assert "REFRESH MATERIALIZED VIEW" not in restapi, \
        "the restapi plugin must not refresh any of the views it no longer owns"


HOUR_HOP = _db_dir("userstats") / "update_v3.14.sql"
HOUR_VIEW_INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_hours "
                   "ON mv_site_hours (server_name, dow, hour);")


def _hop_statements() -> list[str]:
    return [_normalised(_executable(statement)) for statement in _statements(HOUR_HOP)]


def test_the_hour_view_hop_creates_it_before_its_index_and_refreshes_it_last():
    """The hop is ONE revision for every view, so the order inside it is the whole guarantee."""
    statements = _hop_statements()
    index_of = {}
    for position, statement in enumerate(statements):
        head = statement.upper()
        if head.startswith("CREATE MATERIALIZED VIEW IF NOT EXISTS MV_SITE_HOURS"):
            index_of["create"] = position
        elif head.startswith("CREATE UNIQUE INDEX IF NOT EXISTS IDX_MV_SITE_HOURS"):
            index_of["index"] = position
        elif statement == "REFRESH MATERIALIZED VIEW mv_site_hours;":
            index_of["refresh"] = position
    assert set(index_of) == {"create", "index", "refresh"}, f"the hop is missing a step: {index_of}"
    assert index_of["create"] < index_of["index"] < index_of["refresh"]
    assert statements[index_of["index"]] == HOUR_VIEW_INDEX


def test_the_hour_view_hop_carries_no_transaction_control_and_no_concurrent_refresh():
    """The loader runs the file inside one transaction: BEGIN/COMMIT break it and the concurrent
    refresh raises FeatureNotSupported, which would abort the migration it is meant to heal."""
    text = HOUR_HOP.read_text(encoding="utf-8")
    assert "CONCURRENTLY" not in text.upper()
    assert not re.search(r"\b(BEGIN|COMMIT|ROLLBACK|SAVEPOINT)\b", text, re.I)


def test_a_fresh_install_refreshes_the_hour_view_after_building_it():
    """A materialized view starts empty; without the refresh the panel has no data until the tick."""
    statements = [_normalised(_executable(statement))
                  for statement in _statements(_db_dir("userstats") / "tables.sql")]
    index = statements.index(HOUR_VIEW_INDEX)
    assert "REFRESH MATERIALIZED VIEW mv_site_hours;" in statements[index:], \
        "tables.sql builds mv_site_hours without filling it"
    text = (_db_dir("userstats") / "tables.sql").read_text(encoding="utf-8")
    assert "CONCURRENTLY" not in text.upper()


def test_the_hour_view_definition_is_one_text_in_every_file():
    """A drift between the copies would leave a migrated install and a fresh one on different views."""
    fresh_view = _view_statement(_statements(_db_dir("userstats") / "tables.sql"), "mv_site_hours")
    assert fresh_view
    wanted = _normalised(fresh_view)
    for hop in ("update_v3.14.sql",):
        hop_view = _view_statement(_statements(_db_dir("userstats") / hop), "mv_site_hours")
        assert hop_view, hop
        assert _normalised(hop_view) == wanted, hop


#: the two files a fresh install and a migrated install build the view from
HOUR_VIEW_FILES = ("update_v3.14.sql", "tables.sql")


def _split_top_level(text: str) -> list[str]:
    """Split a select list on the commas that are not inside parentheses."""
    items, depth, current = [], 0, ""
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            items.append(current)
            current = ""
        else:
            current += char
    items.append(current)
    return [item.strip() for item in items if item.strip()]


def _selected_name(item: str) -> str:
    """The output name of one select-list item: its ``AS`` alias, else its trailing identifier."""
    found = re.search(r"\bAS\s+(\w+)\s*$", item, re.I) or re.search(r"(\w+)\s*$", item)
    assert found, f"cannot name the select-list item {item!r}"
    return found.group(1)


def _select_columns(body: str) -> set[str]:
    """The names a subquery produces: the output name of every item of its top-level select list."""
    head = re.match(r"\s*SELECT\s", body, re.I)
    assert head, "the LATERAL block does not start with SELECT"
    start = head.end()
    depth = 0
    for index in range(start, len(body)):
        if body[index] == "(":
            depth += 1
        elif body[index] == ")":
            depth -= 1
        elif depth == 0 and re.match(r"FROM\b", body[index:], re.I):
            return {_selected_name(item) for item in _split_top_level(body[start:index])}
    raise AssertionError("the LATERAL subquery has no FROM")


def _lateral_blocks(statement: str) -> dict[str, set[str]]:
    """alias -> the column names that alias's LATERAL block produces, for every LATERAL in the view."""
    text = _normalised(statement)
    blocks: dict[str, set[str]] = {}
    for found in re.finditer(r"LATERAL\s*\(", text):
        start = found.end() - 1
        depth = 0
        end = -1
        for index in range(start, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        assert end > start, "unbalanced parentheses in the LATERAL block"
        body = text[start + 1:end]
        tail = re.match(r"\s*AS\s+(\w+)\s*(?:\(([^)]*)\))?", text[end + 1:])
        assert tail, f"the LATERAL block at offset {start} is not aliased"
        alias, column_list = tail.group(1), tail.group(2)
        if column_list:
            # a set-returning function in FROM: the column list names what it produces
            blocks[alias] = {name.strip() for name in column_list.split(",") if name.strip()}
        else:
            blocks[alias] = _select_columns(body)
    return blocks


@pytest.mark.parametrize("filename", HOUR_VIEW_FILES)
def test_every_column_the_hour_view_qualifies_exists_in_that_block(filename):
    """``<alias>.<column>`` must name a column its own block produces.

    A second LATERAL aliased ``o`` while the sums qualified the first alias ``h`` passed every text
    pin and only PostgreSQL refused it, taking the whole plugin down with it.
    """
    statements = _statements(_db_dir("userstats") / filename)
    view = _view_statement(statements, "mv_site_hours")
    assert view, f"{filename} creates no mv_site_hours"
    blocks = _lateral_blocks(view)
    assert blocks, f"{filename} joins no LATERAL block"
    for found in re.finditer(r"\b(\w+)\.(\w+)\b", view):
        alias, column = found.group(1), found.group(2)
        if alias not in blocks:
            continue  # a base table (s, m) is not a LATERAL block
        assert column in blocks[alias], (
            f"{filename}: {alias}.{column} resolves to no column of that block "
            f"(it produces {sorted(blocks[alias])})")
    summed = re.search(r"SUM\(\s*(\w+)\.overlap\s*\)", view)
    assert summed, f"{filename}: seconds is not summed from a qualified overlap column"
    assert "overlap" in blocks[summed.group(1)], (
        f"{filename}: SUM({summed.group(1)}.overlap) names a block that produces no overlap column")

