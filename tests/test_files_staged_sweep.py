"""The staged-upload sweep: one rule, one implementation, startup only.

``files`` has no kind column and no consumed flag, so the sweeper keys on ``name``: the two upload
actions stage under the destination LEAF (``track.mp3``, ``mission.miz``), every other writer stages
a full path or a sink token. The table double below applies the WHERE the sweeper writes
(``guild_id`` equality, ``created`` older than the cutoff) and the sweep's own name rule runs in
Python, so the four states are checked on real row values rather than on the SQL's spelling.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.data.file_staging import (  # noqa: E402
    STAGED_UPLOAD_ORPHAN_SECONDS,
    STAGED_UPLOAD_SUFFIXES,
    is_staged_upload_name,
    sweep_staged_uploads,
)

GUILD = 7
OTHER_GUILD = 9
#: A fixed "now" (UTC, naive — the shape ``files.created`` stores) so ages are exact.
NOW = datetime(2026, 10, 31, 12, 0, 0)


# ------------------------------------------------------------------------------------ the double

class _Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)

    async def fetchall(self):
        return list(self._rows)


class _Table:
    """The ``files`` table as rows, applying the statements the sweeper issues."""

    def __init__(self):
        self.rows: list[dict] = []
        self.statements: list[tuple[str, tuple]] = []
        self._next = 1

    def insert(self, name, *, guild_id=GUILD, age_seconds=0, created=None):
        row = {
            "id": self._next,
            "guild_id": guild_id,
            "name": name,
            "created": created if created is not None else NOW - timedelta(seconds=age_seconds),
        }
        self._next += 1
        self.rows.append(row)
        return row["id"]

    def consume(self, row_id):
        """A reader's own DELETE — what every consumer does with the row it takes."""
        self.rows = [row for row in self.rows if row["id"] != row_id]

    def names(self):
        return sorted(row["name"] for row in self.rows)

    def deletes(self):
        return [params for sql, params in self.statements if sql.startswith("DELETE")]


class _Conn:
    def __init__(self, table):
        self._table = table

    async def execute(self, sql, params=None):
        text = str(sql)
        params = tuple(params or ())
        self._table.statements.append((text, params))
        if text.startswith("SELECT"):
            guild_id, cutoff = params
            return _Cursor((row["id"], row["name"]) for row in self._table.rows
                           if row["guild_id"] == guild_id and row["created"] < cutoff)
        if text.startswith("DELETE"):
            guild_id, ids = params
            self._table.rows = [row for row in self._table.rows
                                if not (row["guild_id"] == guild_id and row["id"] in set(ids))]
            return _Cursor()
        raise AssertionError(f"unexpected statement: {text}")


class _ConnCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, table):
        self._table = table

    def connection(self):
        return _ConnCtx(_Conn(self._table))


@pytest.fixture
def table():
    return _Table()


# ------------------------------------------------------------------- 1. an aged row is removed

@pytest.mark.asyncio
async def test_an_aged_staged_row_is_removed(table):
    track = table.insert("track.mp3", age_seconds=STAGED_UPLOAD_ORPHAN_SECONDS + 60)
    mission = table.insert("operation.miz", age_seconds=STAGED_UPLOAD_ORPHAN_SECONDS + 1)

    removed = await sweep_staged_uploads(_Pool(table), guild_id=GUILD, now=NOW)

    assert removed == 2
    assert table.names() == []
    assert table.deletes(), "the sweep deleted nothing"
    # both kinds of staged row are one implementation's business
    assert track not in {row["id"] for row in table.rows}
    assert mission not in {row["id"] for row in table.rows}


# -------------------------------------------------------------- 2. a fresh row is left alone

@pytest.mark.asyncio
async def test_a_fresh_staged_row_is_left_alone(table):
    table.insert("track.ogg", age_seconds=0)
    table.insert("just-over.miz", age_seconds=STAGED_UPLOAD_ORPHAN_SECONDS - 1)

    removed = await sweep_staged_uploads(_Pool(table), guild_id=GUILD, now=NOW)

    assert removed == 0
    assert table.names() == ["just-over.miz", "track.ogg"]
    assert table.deletes() == [], "a row inside the age window was deleted"


@pytest.mark.asyncio
async def test_the_age_is_a_stated_constant_one_hour():
    assert STAGED_UPLOAD_ORPHAN_SECONDS == 3600


# ---------------------------------------------------------- 3. a row consumed normally is untouched

@pytest.mark.asyncio
async def test_a_row_consumed_normally_is_untouched(table):
    """Consumption IS the reader's DELETE, so a consumed row is already gone: nothing to sweep."""
    row_id = table.insert("track.mp3", age_seconds=STAGED_UPLOAD_ORPHAN_SECONDS * 3)
    table.consume(row_id)

    removed = await sweep_staged_uploads(_Pool(table), guild_id=GUILD, now=NOW)

    assert removed == 0
    assert table.names() == []
    assert table.deletes() == [], "the sweep wrote a DELETE over a table with no staged row"


@pytest.mark.asyncio
async def test_a_consumed_row_beside_an_aged_one_leaves_only_the_aged_one_named(table):
    consumed = table.insert("played.mp3", age_seconds=STAGED_UPLOAD_ORPHAN_SECONDS * 3)
    orphan = table.insert("killed.mp3", age_seconds=STAGED_UPLOAD_ORPHAN_SECONDS * 3)
    table.consume(consumed)

    removed = await sweep_staged_uploads(_Pool(table), guild_id=GUILD, now=NOW)

    assert removed == 1
    assert [row["id"] for row in table.rows] == []          # the orphan was the only row left
    assert consumed not in table.deletes()[0][1]            # the consumed id is never named
    assert orphan in table.deletes()[0][1]


# ----------------------------------------------------------- 4. another kind is never swept

@pytest.mark.asyncio
async def test_a_row_of_another_kind_is_never_swept(table):
    """A mission staged as a write TARGET and any other drop-box row keep their bytes."""
    aged = STAGED_UPLOAD_ORPHAN_SECONDS * 5
    for name in ("/srv/missions/operation.miz", "C:\\DCS\\missions\\operation.miz",
                 "cpuinfo", "cpuinfo.json", "notes.txt", "archive.zip", "..", "."):
        table.insert(name, age_seconds=aged)

    removed = await sweep_staged_uploads(_Pool(table), guild_id=GUILD, now=NOW)

    assert removed == 0
    assert table.names() == sorted(["/srv/missions/operation.miz",
                                    "C:\\DCS\\missions\\operation.miz",
                                    "cpuinfo", "cpuinfo.json", "notes.txt", "archive.zip",
                                    "..", "."])
    assert table.deletes() == []


@pytest.mark.asyncio
async def test_a_staged_row_of_another_guild_is_never_swept(table):
    table.insert("mine.mp3", guild_id=OTHER_GUILD, age_seconds=STAGED_UPLOAD_ORPHAN_SECONDS * 5)
    table.insert("theirs.miz", guild_id=OTHER_GUILD, age_seconds=STAGED_UPLOAD_ORPHAN_SECONDS * 5)

    removed = await sweep_staged_uploads(_Pool(table), guild_id=GUILD, now=NOW)

    assert removed == 0
    assert table.names() == ["mine.mp3", "theirs.miz"]


# ------------------------------------------------------------------------ 5. the name rule

@pytest.mark.parametrize("name", ["track.mp3", "TRACK.MP3", "a.ogg", "operation.miz", "x .Miz",
                                  "spaced name.mp3"])
def test_a_leaf_with_an_upload_suffix_is_staged(name):
    assert is_staged_upload_name(name) is True


@pytest.mark.parametrize("name", [
    "", "   ", ".", "..", "track.wav", "track", "track.mp3.wav",
    "/srv/music/track.mp3", "srv\\music\\track.mp3", "C:track.mp3", "C:\\music\\track.mp3",
    "cpuinfo", "cpuinfo.json", "notes.txt", "track.mp3/evil", None, 7, b"track.mp3",
])
def test_anything_but_a_leaf_track_or_mission_is_not_staged(name):
    assert is_staged_upload_name(name) is False


# ------------------------------------------------------- 6. it cannot be reached from a request

@pytest.mark.asyncio
async def test_the_sweep_needs_an_explicit_guild(table):
    with pytest.raises(TypeError):
        await sweep_staged_uploads(_Pool(table))
    with pytest.raises(TypeError):
        await sweep_staged_uploads(_Pool(table), guild_id="7")
    assert table.statements == []


def test_the_sweep_is_not_a_registered_action():
    import core.actions as actions_module

    registered = [fn for fn in actions_module.action_registry().values()
                  if getattr(fn, "__module__", "") == "core.data.file_staging"]
    assert registered == [], f"the sweep must not be offerable as an action: {registered}"


def test_the_delete_is_bounded_by_the_guild(table):
    """Source-level: both statements carry the guild, so no call can name another guild's row."""
    import inspect

    source = inspect.getsource(sweep_staged_uploads)
    assert source.count("guild_id = %s") == 2


# ---------------------------------------------------------------------- 7. the suffix set

def test_the_suffixes_mirror_the_two_upload_gates():
    from plugins.mission.actions import _MIZ_SUFFIX
    from services.music.service import track_suffix
    from services.music.tags import AUDIO_SUFFIXES

    offered = {suffix.lower() for suffix in AUDIO_SUFFIXES}
    assert offered == {suffix for suffix in STAGED_UPLOAD_SUFFIXES if suffix != _MIZ_SUFFIX}
    assert _MIZ_SUFFIX in STAGED_UPLOAD_SUFFIXES
    # the gate itself agrees with the set the sweep declares
    for suffix in STAGED_UPLOAD_SUFFIXES:
        assert track_suffix(f"probe{suffix}") == (suffix if suffix in AUDIO_SUFFIXES else None)


def test_the_name_rule_agrees_with_the_music_upload_leaf_gate():
    """A path, a drive letter or ``.``/``..`` is refused by BOTH; a track leaf is staged by both."""
    from services.music.service import track_suffix, upload_leaf

    for name in ("track.mp3", "a.ogg"):
        assert upload_leaf(name) is not None
        assert track_suffix(name) is not None
        assert is_staged_upload_name(name) is True, name
    for name in ("/srv/track.mp3", "srv\\track.mp3", "C:\\t.mp3", "..", "."):
        assert upload_leaf(name) is None, name
        assert is_staged_upload_name(name) is False, name
    # the suffix gate is the second half: a leaf the music gate refuses an extension for is not
    # something either upload path stages
    assert upload_leaf("notes.txt") is not None
    assert track_suffix("notes.txt") is None
    assert is_staged_upload_name("notes.txt") is False
