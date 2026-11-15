"""Pins for the playlist WRITE door and for the one-track-once constraint.

Two holes, one card:

* a PLAYLIST write names no server, so a REST/plugin/MCP caller could edit an installation-wide
  playlist. ``plugins.music.actions`` now refuses every playlist write for a transport that is not
  the admin console; the Discord commands reach the service directly and are untouched.
* one track sat in one playlist more than once. ``UNIQUE (name, song_file)`` is declared by
  ``tables.sql`` (a fresh install) and by ``update_v3.1.sql`` (an existing 3.1 install, which must
  de-duplicate BEFORE the index can be created).

The migration is pinned as TEXT (the delete-then-index order and its predicate) and EXECUTED against
SQLite through a mechanical translation of the two statements, so the de-duplication outcome is a real
query result and not a description. PostgreSQL itself is the deployment check named in the report.
"""
from __future__ import annotations

import os
import pathlib
import re
import sqlite3
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SQL_DIR = REPO / "plugins" / "music" / "db"
TABLES = SQL_DIR / "tables.sql"
MIGRATION = SQL_DIR / "update_v3.1.sql"


# --------------------------------------------------------------------------- the schema text pins

def test_the_plugin_version_runs_the_migration_on_the_next_start():
    """The loader runs ``update_v<installed>.sql`` and then stores ``plugin_version``."""
    version = re.search(r'__version__ = "([^"]+)"',
                        (REPO / "plugins" / "music" / "version.py").read_text(encoding="utf-8"))
    assert version, "plugins/music/version.py carries no __version__"
    assert version.group(1) == "3.2"
    assert MIGRATION.name == "update_v3.1.sql"
    assert MIGRATION.exists()


def test_a_fresh_install_declares_the_pair_unique():
    sql = TABLES.read_text(encoding="utf-8")
    block = re.search(r"CREATE TABLE music_playlists\((.*?)\);", sql, re.S)
    assert block, "the music_playlists table is not in tables.sql"
    assert "UNIQUE (name, song_file)" in block.group(1)


def test_the_migration_deletes_duplicates_BEFORE_it_creates_the_index():
    """An index cannot be created over dirty data, so the order of the two statements is the rule."""
    statements = [stmt.strip() for stmt in MIGRATION.read_text(encoding="utf-8").split(";")
                  if stmt.strip()]
    assert len(statements) == 2, statements
    delete, index = statements
    assert delete.startswith("DELETE FROM music_playlists p")
    assert "USING music_playlists q" in delete
    assert "p.name = q.name" in delete
    assert "p.song_file = q.song_file" in delete
    # the LOWEST song_id of a pair survives: every row with a smaller-id sibling is the deleted one
    assert "p.song_id > q.song_id" in delete
    assert index == ("ALTER TABLE music_playlists ADD CONSTRAINT music_playlists_name_song_file_key "
                     "UNIQUE (name, song_file)")


# --------------------------------------------------------------------------- the migration, executed

def _translate(statement: str) -> str:
    """The same statement in SQLite's spelling — the table, the predicate and the columns are kept.

    PostgreSQL's ``DELETE … USING`` becomes ``DELETE … WHERE EXISTS`` over the same self-join and
    ``ALTER TABLE … ADD CONSTRAINT … UNIQUE`` becomes the unique index that carries the same name and
    the same column list. Nothing about the outcome is restated here: the predicate text is the file's.
    """
    text = " ".join(statement.split())
    delete = re.match(r"^DELETE FROM (\w+) p USING (\w+) q WHERE (.*)$", text)
    if delete:
        table, other, condition = delete.groups()
        assert table == other, "the self-join must name one table"
        return f"DELETE FROM {table} AS p WHERE EXISTS (SELECT 1 FROM {other} AS q WHERE {condition})"
    alter = re.match(r"^ALTER TABLE (\w+) ADD CONSTRAINT (\w+) UNIQUE \((.*)\)$", text)
    if alter:
        table, name, columns = alter.groups()
        return f"CREATE UNIQUE INDEX {name} ON {table} ({columns})"
    raise AssertionError(f"the migration carries a statement this test cannot translate: {statement}")


def _migrated() -> sqlite3.Connection:
    """A ``music_playlists`` table seeded with duplicates, with the migration's two statements run."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE music_playlists (name TEXT NOT NULL, song_id INTEGER NOT NULL, "
                 "song_file TEXT NOT NULL)")
    rows = [
        # Rock holds three copies of a.mp3 (ids 1, 4, 7) and two of b.mp3 (ids 2, 5)
        ("Rock", 1, "rock/a.mp3"),
        ("Rock", 2, "rock/b.mp3"),
        ("Rock", 3, "rock/c.mp3"),
        ("Rock", 4, "rock/a.mp3"),
        ("Rock", 5, "rock/b.mp3"),
        ("Rock", 6, "rock/d.mp3"),
        ("Rock", 7, "rock/a.mp3"),
        # Pop is clean and must be untouched
        ("Pop", 8, "pop/x.mp3"),
        ("Pop", 9, "pop/y.mp3"),
    ]
    conn.executemany("INSERT INTO music_playlists (name, song_id, song_file) VALUES (?, ?, ?)", rows)
    statements = [stmt.strip() for stmt in MIGRATION.read_text(encoding="utf-8").split(";")
                  if stmt.strip()]
    for statement in statements:
        conn.execute(_translate(statement))
    return conn


def _rows(conn, playlist: str) -> list[tuple[int, str]]:
    return list(conn.execute("SELECT song_id, song_file FROM music_playlists WHERE name = ? "
                             "ORDER BY song_id", (playlist,)))


def test_the_migration_keeps_the_lowest_id_of_each_pair_and_drops_the_rest():
    conn = _migrated()
    assert _rows(conn, "Rock") == [(1, "rock/a.mp3"), (2, "rock/b.mp3"), (3, "rock/c.mp3"),
                                   (6, "rock/d.mp3")]


def test_the_migration_leaves_a_clean_playlist_untouched():
    assert _rows(_migrated(), "Pop") == [(8, "pop/x.mp3"), (9, "pop/y.mp3")]


def test_the_migration_leaves_the_index_in_place():
    conn = _migrated()
    names = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'music_playlists'")]
    assert "music_playlists_name_song_file_key" in names
    # the index is what makes a second copy of one file a database error, not a silent second row
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO music_playlists (name, song_id, song_file) "
                     "VALUES ('Rock', 99, 'rock/a.mp3')")


# ------------------------------------------------------- the Discord path: ``Playlist.add`` in words

class _Cursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _Connection:
    def __init__(self, rowcounts):
        self.rowcounts = rowcounts
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        self.statements.append(" ".join(str(statement).split()))
        return _Cursor(self.rowcounts.pop(0))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, connection):
        self.connection_ = connection

    def connection(self):
        return self.connection_


def _playlist(items: list[str], rowcounts: list[int]):
    """A ``Playlist`` over a connection double that answers one ``rowcount`` per insert."""
    from plugins.music.utils import Playlist

    built = Playlist.__new__(Playlist)
    built.playlist = "Rock"
    built._items = list(items)
    built.apool = _Pool(_Connection(list(rowcounts)))
    return built, built.apool.connection_


def test_playlist_add_reports_a_track_it_already_holds_instead_of_raising():
    import asyncio

    built, conn = _playlist(["rock/a.mp3"], [0])
    assert asyncio.run(built.add("rock/a.mp3")) is False
    assert built.items == ["rock/a.mp3"]
    assert "ON CONFLICT (name, song_file) DO NOTHING" in conn.statements[0]


def test_playlist_add_writes_a_track_the_playlist_does_not_hold():
    import asyncio

    built, conn = _playlist(["rock/a.mp3"], [1])
    assert asyncio.run(built.add("rock/b.mp3")) is True
    assert built.items == ["rock/a.mp3", "rock/b.mp3"]
    assert "ON CONFLICT (name, song_file) DO NOTHING" in conn.statements[0]


# --------------------------------------------------------------------------- the import order

def test_the_plugin_utils_then_the_service_import_cleanly_in_a_fresh_interpreter():
    """The plugin's ``utils`` first, the service second, in a process that imported neither before.

    The test file's own imports reach the service through the plugin and so only prove the order they
    happen to run. A caller that touches ``plugins.music.utils`` before ``services.music`` (a reload,
    or anything that imports the Discord side first) must find the same two modules, so the order is
    pinned in its own interpreter where no earlier import can pre-populate ``sys.modules``.
    """
    probe = "import plugins.music.utils; import services.music; print('IMPORTED')"
    done = subprocess.run([sys.executable, "-c", probe], cwd=REPO, text=True, encoding="utf-8",
                          capture_output=True, env={**os.environ, "PYTHONPATH": str(REPO)})
    assert done.returncode == 0, done.stderr
    assert "IMPORTED" in done.stdout
