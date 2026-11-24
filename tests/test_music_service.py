"""Pins for the MUSIC service: the playlist writes, the playback order and the library scan.

The cases drive :class:`services.music.service.MusicService` directly over a fake ``apool`` that
holds real state (the playlists' rows and the radio assignments), so what is asserted is the RESULT
— which rows a write touched, what order the playlist reads back in — and not the spelling of a
statement. Two questions are source-shaped on purpose: the radio's playlist read must carry its
``ORDER BY`` (a reorder is a no-op without it) and the config write must not dump a document.

No database and no audio files: the tag reader is replaced, because what a real file's tags say is
not this suite's question.
"""
from __future__ import annotations

import os

import logging
import types

import pytest

from core.const import DEFAULT_TAG
from services.music.radios import Mode, Radio
from services.music.service import (
    PLAYLIST_DUPLICATE, PLAYLIST_EXISTS, PLAYLIST_INVALID, PLAYLIST_MISSING,
    PLAYLIST_NOT_A_PERMUTATION, PLAYLIST_OK,
    RADIO_IDLE, RADIO_INVALID, RADIO_OK, RADIO_UNKNOWN, MusicService, music_file, music_path,
)

#: host-spelled: the service joins and normalises it, so a raw POSIX root never matches on Windows
ROOT = os.path.abspath("/music")


# --------------------------------------------------------------------------- the doubles

class _Cursor:
    """A cursor over the store: every statement is recorded and answered from the live rows."""

    def __init__(self, store):
        self._store = store
        self._rows: list = []
        self.rowcount = -1

    async def execute(self, statement, params=None):
        self._rows, self.rowcount = self._store.run(statement, params)
        self._store.statements.append((" ".join(str(statement).split()), params))
        return self

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, store):
        self._store = store

    async def execute(self, statement, params=None):
        cursor = _Cursor(self._store)
        await cursor.execute(statement, params)
        return cursor

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _SyncConn:
    """The SYNC connection the service writes a radio row through (``pool``, not ``apool``)."""

    def __init__(self, store):
        self._store = store
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        _rows, self.rowcount = self._store.run(statement, params)
        self._store.statements.append((" ".join(str(statement).split()), params))
        return self


class SyncPool:
    """The sync pool double: ``pool.connection()`` is a plain context manager."""

    def __init__(self, store):
        self._store = store

    def connection(self):
        return _SyncConn(self._store)


class Store:
    """``music_playlists`` and ``music_radios`` as live rows, plus the sequence that orders them."""

    def __init__(self, playlists=None, radios=()):
        self.playlists: dict[str, list[list]] = {}
        self.radios: list[list[str]] = [list(row) for row in radios]
        self.sequence = 0
        self.statements: list[tuple[str, object]] = []
        for name, files in (playlists or {}).items():
            for file in files:
                self.sequence += 1
                self.playlists.setdefault(name, []).append([self.sequence, file])

    # ── the rows a case reads back ────────────────────────────────────────
    def ordered(self, name: str) -> list[str]:
        """The playlist's files in ``song_id`` order — what a radio would play."""
        return [row[1] for row in sorted(self.playlists.get(name, []), key=lambda row: row[0])]

    def ids(self, name: str) -> list[int]:
        return [row[0] for row in sorted(self.playlists.get(name, []), key=lambda row: row[0])]

    def assignments(self) -> list[tuple[str, str, str]]:
        return [tuple(row) for row in self.radios]

    def ran(self, marker: str) -> list[tuple[str, object]]:
        return [(statement, params) for statement, params in self.statements if marker in statement]

    # ── the psycopg surface the service uses ──────────────────────────────
    def connection(self):
        return _Conn(self)

    def run(self, statement, params):
        text = " ".join(str(statement).split())
        params = params or ()
        if text.startswith("SELECT DISTINCT name FROM music_playlists"):
            wanted = str(params[0]).lower()
            return [[name] for name in sorted(self.playlists) if name.lower() == wanted], -1
        if text.startswith("SELECT song_id, song_file FROM music_playlists WHERE name"):
            return [[sid, file] for sid, file in self._rows_of(params[0])], -1
        if text.startswith("SELECT name, song_id, song_file FROM music_playlists"):
            rows = [[name, sid, file] for name in sorted(self.playlists)
                    for sid, file in self._rows_of(name)]
            return rows, -1
        if text.startswith("SELECT song_id FROM music_playlists WHERE name"):
            return [[sid] for sid in self.ids(params[0])], -1
        if text.startswith("SELECT count(*) FROM music_playlists"):
            return [[len(self.playlists.get(params[0], []))]], -1
        if text.startswith("SELECT nextval('music_song_id_seq') FROM generate_series"):
            rows = []
            for _ in range(int(params[0])):
                self.sequence += 1
                rows.append([self.sequence])
            return rows, -1
        if text.startswith("INSERT INTO music_radios"):
            server_name, radio_name, playlist = params
            for row in self.radios:
                if row[0] == server_name and row[1] == radio_name:
                    row[2] = playlist
                    return [], 1
            self.radios.append([server_name, radio_name, playlist])
            return [], 1
        if text.startswith("INSERT INTO music_playlists"):
            name, file = params
            rows = self.playlists.setdefault(name, [])
            if "ON CONFLICT (name, song_file) DO NOTHING" in text and any(row[1] == file for row in rows):
                return [], 0
            self.sequence += 1
            rows.append([self.sequence, file])
            return [], 1
        if text.startswith("DELETE FROM music_playlists WHERE name = %s AND song_id = ANY"):
            name, wanted = params
            rows = self.playlists.get(name, [])
            keep = [row for row in rows if row[0] not in set(wanted)]
            if keep:
                self.playlists[name] = keep
            else:
                self.playlists.pop(name, None)
            return [], len(rows) - len(keep)
        if text.startswith("DELETE FROM music_playlists WHERE name"):
            return [], len(self.playlists.pop(params[0], []))
        if text.startswith("DELETE FROM music_radios WHERE playlist_name"):
            keep = [row for row in self.radios if row[2] != params[0]]
            removed = len(self.radios) - len(keep)
            self.radios = keep
            return [], removed
        if text.startswith("UPDATE music_playlists SET song_id"):
            new_id, name, old_id = params
            for row in self.playlists.get(name, []):
                if row[0] == old_id:
                    row[0] = new_id
            return [], 1
        if text.startswith("UPDATE music_playlists SET name"):
            target, source = params
            rows = self.playlists.pop(source, [])
            if rows:
                self.playlists[target] = rows
            return [], len(rows)
        if text.startswith("UPDATE music_radios SET playlist_name"):
            target, source = params
            moved = 0
            for row in self.radios:
                if row[2] == source:
                    row[2] = target
                    moved += 1
            return [], moved
        if text.startswith("SELECT playlist_name, server_name, radio_name FROM music_radios"):
            rows = [[row[2], row[0], row[1]] for row in
                    sorted(self.radios, key=lambda row: (row[2], row[0], row[1]))]
            return rows, -1
        if text.startswith("SELECT radio_name, playlist_name FROM music_radios"):
            return [[row[1], row[2]] for row in self.radios if row[0] == params[0]], -1
        return [], -1

    def _rows_of(self, name):
        return [(row[0], row[1]) for row in sorted(self.playlists.get(name, []),
                                                   key=lambda row: row[0])]


class FakeRadio:
    """A live radio double: records what a write did to it, in the order it happened."""

    def __init__(self, name="Radio 1", running=False):
        self.name = name
        self.calls: list = []
        self.playlist_value = None
        self.config_value = {}
        self.mode_value = Mode.REPEAT
        self.songs: list[str] = []
        self.current = ""
        self.current_file = ""
        self._running = running

    def is_running(self):
        return self._running

    async def stop(self):
        self.calls.append("stop")
        self._running = False

    async def start(self):
        self.calls.append("start")
        self._running = True

    async def skip(self):
        self.calls.append("skip")

    def reset(self):
        self.calls.append("reset")

    @property
    def playlist(self):
        return self.playlist_value

    @playlist.setter
    def playlist(self, value):
        self.calls.append(("playlist", value))
        self.playlist_value = value

    @property
    def config(self):
        return dict(self.config_value)

    @config.setter
    def config(self, value):
        self.calls.append(("config", dict(value)))
        self.config_value.update(value)

    @property
    def mode(self):
        return self.mode_value

    @mode.setter
    def mode(self, value):
        self.calls.append(("mode", value))
        self.mode_value = value


def node(name="NODE-01"):
    return types.SimpleNamespace(name=name, display_name=name, is_remote=False)


def server(name="SRS-1", node_name="NODE-01"):
    return types.SimpleNamespace(
        name=name, display_name=name, node=node(node_name),
        instance=types.SimpleNamespace(name=name),
    )


def service(store, root=ROOT, radios=None):
    """A ``MusicService`` over *store* whose music directory is *root* (no ``__init__``)."""
    built = MusicService.__new__(MusicService)
    built.node = node()
    built.log = logging.getLogger("test.music")
    built.locals = {DEFAULT_TAG: {"music_dir": root,
                                  "radios": {"Radio 1": {"type": "SRSRadio", "mode": 1}}}}
    built._config = {}
    built.apool = store
    built.pool = SyncPool(store)
    built.radios = dict(radios or {})
    built._library_cache = {}
    built._library_generation = {}
    return built


# --------------------------------------------------------------------------- the path rules

def test_music_path_accepts_a_relative_track_and_refuses_a_location():
    assert music_path("album/track 01.mp3") == "album/track 01.mp3"
    assert music_path("./album//track.ogg") == "album/track.ogg"
    assert music_path("album\\track.mp3") == "album/track.mp3"
    for escape in ("", "   ", "/etc/passwd", "../track.mp3", "album/../../track.mp3",
                   "C:/music/track.mp3", "C:\\music\\track.mp3", "album/\x00track.mp3", ".."):
        assert music_path(escape) is None, escape


def test_music_file_refuses_a_path_that_resolves_outside_the_root(tmp_path):
    root = tmp_path / "music"
    inside = root / "album"
    inside.mkdir(parents=True)
    (inside / "track.mp3").write_bytes(b"")
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"")
    assert music_file(str(root), "album/track.mp3")
    assert music_file(str(root), "../../secret.mp3") is None
    # a symbolic link is the shape a lexical rule cannot see
    link = root / "link.mp3"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this filesystem carries no symbolic links")
    assert music_file(str(root), "link.mp3") is None


def test_music_path_refuses_a_name_that_is_a_directory_inside_the_root(tmp_path):
    root = tmp_path / "music"
    (root / "Rock").mkdir(parents=True)
    (root / "Rock" / "a.mp3").write_bytes(b"")
    assert music_path("Rock", str(root)) is None
    assert music_path("Rock", str(root / "missing")) == "Rock"
    assert music_path("Rock/a.mp3", str(root)) == "Rock/a.mp3"


# --------------------------------------------------------------------------- the playlist writes

def test_adding_a_first_track_creates_the_playlist():
    import asyncio

    store = Store()
    built = service(store)
    answer = asyncio.run(built.add_tracks("Rock", ["rock/a.mp3", "rock/b.mp3"]))
    assert answer["status"] == PLAYLIST_OK
    assert answer["created"] is True
    assert answer["added"] == 2
    assert store.ordered("Rock") == ["rock/a.mp3", "rock/b.mp3"]


def test_adding_a_track_that_leaves_the_music_directory_writes_no_row():
    """``add_tracks`` is peer-callable: the containment rule is the service's, not the action's."""
    import asyncio

    store = Store()
    built = service(store)
    answer = asyncio.run(built.add_tracks("Rock", ["rock/a.mp3", "../../secret.mp3"]))
    assert answer["status"] == PLAYLIST_INVALID
    assert answer["added"] == 0
    assert store.playlists == {}
    assert store.ran("INSERT INTO music_playlists") == []


def test_adding_a_name_that_is_a_directory_writes_no_row(tmp_path):
    import asyncio

    root = tmp_path / "music"
    (root / "Rock").mkdir(parents=True)
    store = Store()
    built = service(store, root=str(root))
    answer = asyncio.run(built.add_tracks("Rock", ["Rock"]))
    assert answer["status"] == PLAYLIST_INVALID
    assert store.playlists == {}


def test_adding_to_an_existing_name_joins_its_own_spelling():
    """A name is the playlist's identity, so a spelling that differs only in case is the SAME one."""
    import asyncio

    store = Store({"Rock": ["rock/a.mp3"]})
    built = service(store)
    answer = asyncio.run(built.add_tracks("rock", ["rock/b.mp3"]))
    assert answer["playlist"] == "Rock"
    assert answer["created"] is False
    assert list(store.playlists) == ["Rock"]
    assert store.ordered("Rock") == ["rock/a.mp3", "rock/b.mp3"]


def test_removing_the_last_track_removes_the_radio_mapping():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3"]}, radios=[["SRS-1", "Radio 1", "Rock"]])
    built = service(store)
    answer = asyncio.run(built.remove_tracks("Rock", store.ids("Rock")))
    assert answer["status"] == PLAYLIST_OK
    assert answer["removed"] == 1
    assert answer["tracks"] == 0
    assert answer["unmapped"] == 1
    assert store.assignments() == []
    assert "Rock" not in store.playlists


def test_removing_one_track_keeps_the_playlist_and_its_mapping():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3", "rock/b.mp3"]}, radios=[["SRS-1", "Radio 1", "Rock"]])
    built = service(store)
    answer = asyncio.run(built.remove_tracks("Rock", [store.ids("Rock")[0]]))
    assert answer["removed"] == 1
    assert answer["tracks"] == 1
    assert answer["unmapped"] == 0
    assert store.ordered("Rock") == ["rock/b.mp3"]
    assert store.assignments() == [("SRS-1", "Radio 1", "Rock")]


def test_reorder_accepts_a_permutation_and_the_new_order_is_what_plays():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3", "rock/b.mp3", "rock/c.mp3"]})
    built = service(store)
    first, second, third = store.ids("Rock")
    answer = asyncio.run(built.reorder_tracks("Rock", [third, first, second]))
    assert answer["status"] == PLAYLIST_OK
    assert store.ordered("Rock") == ["rock/c.mp3", "rock/a.mp3", "rock/b.mp3"]
    # the renumbering comes from the sequence, so a later insert still lands at the END
    asyncio.run(built.add_tracks("Rock", ["rock/d.mp3"]))
    assert store.ordered("Rock")[-1] == "rock/d.mp3"


def test_reorder_refuses_a_non_permutation_and_writes_nothing():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3", "rock/b.mp3"]})
    built = service(store)
    first, second = store.ids("Rock")
    before = store.ordered("Rock")
    for order in ([first], [first, first], [first, second, 999], [second, 998]):
        answer = asyncio.run(built.reorder_tracks("Rock", order))
        assert answer["status"] == PLAYLIST_NOT_A_PERMUTATION, order
    assert store.ordered("Rock") == before
    assert store.ran("UPDATE music_playlists SET song_id") == []


def test_reorder_of_an_unknown_playlist_is_missing():
    import asyncio

    built = service(Store())
    answer = asyncio.run(built.reorder_tracks("Nowhere", [1]))
    assert answer["status"] == PLAYLIST_MISSING


def test_rename_refuses_an_existing_name_and_moves_both_tables():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3"], "Pop": ["pop/a.mp3"]},
                  radios=[["SRS-1", "Radio 1", "Rock"]])
    built = service(store)
    refused = asyncio.run(built.rename_playlist("Rock", "pop"))
    assert refused["status"] == PLAYLIST_EXISTS
    assert store.assignments() == [("SRS-1", "Radio 1", "Rock")]
    assert store.ordered("Rock") == ["rock/a.mp3"]
    renamed = asyncio.run(built.rename_playlist("Rock", "Metal"))
    assert renamed["status"] == PLAYLIST_OK
    assert renamed["previous"] == "Rock"
    assert renamed["playlist"] == "Metal"
    assert renamed["radios"] == 1
    # ONE transaction: the tracks and the assignment both name the new playlist
    assert store.ordered("Metal") == ["rock/a.mp3"]
    assert store.assignments() == [("SRS-1", "Radio 1", "Metal")]
    assert "Rock" not in store.playlists


def test_rename_to_the_same_name_in_another_case_is_a_rename():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3"]})
    built = service(store)
    answer = asyncio.run(built.rename_playlist("Rock", "ROCK"))
    assert answer["status"] == PLAYLIST_OK
    assert list(store.playlists) == ["ROCK"]


def test_rename_with_an_empty_name_is_invalid():
    import asyncio

    built = service(Store({"Rock": ["rock/a.mp3"]}))
    assert asyncio.run(built.rename_playlist("Rock", "   "))["status"] == PLAYLIST_INVALID
    assert asyncio.run(built.rename_playlist("   ", "Pop"))["status"] == PLAYLIST_INVALID


def test_delete_removes_the_tracks_and_the_assignments():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3", "rock/b.mp3"]},
                  radios=[["SRS-1", "Radio 1", "Rock"], ["SRS-2", "Radio 2", "Pop"]])
    built = service(store)
    answer = asyncio.run(built.delete_playlist("Rock"))
    assert answer["status"] == PLAYLIST_OK
    assert answer["tracks"] == 2
    assert answer["unmapped"] == 1
    assert store.assignments() == [("SRS-2", "Radio 2", "Pop")]
    assert "Rock" not in store.playlists


def test_playlist_exists_answers_in_any_casing():
    import asyncio

    built = service(Store({"Rock": ["rock/a.mp3"]}))
    assert asyncio.run(built.playlist_exists("rock")) is True
    assert asyncio.run(built.playlist_exists("Jazz")) is False


# --------------------------------------------------------------------------- the playback order

class SyncStore:
    """The sync ``pool`` the radio reads through: answers the rows and records the statement."""

    def __init__(self, files):
        self.files = list(files)
        self.statements: list[tuple[str, object]] = []

    def connection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        self.statements.append((" ".join(str(statement).split()), params))
        return [(file,) for file in self.files]


def test_the_radio_reads_its_playlist_in_song_id_order(monkeypatch):
    """``ORDER BY song_id`` is the whole reorder feature: without it a reorder is not audible."""
    store = SyncStore(["rock/b.mp3", "rock/a.mp3"])
    radio = types.SimpleNamespace(pool=store, _playlist="Rock", log=logging.getLogger("test.music"),
                                  service=types.SimpleNamespace(music_dir=ROOT))
    monkeypatch.setattr("services.music.radios.base.os.path.isfile", lambda path: True)
    played = Radio._read_playlist(radio)
    statement, params = store.statements[0]
    assert "ORDER BY song_id" in statement
    assert params == ("Rock",)
    assert played == ["rock/b.mp3", "rock/a.mp3"]


def test_a_symlinked_track_row_is_skipped_and_dropped_from_the_playlist(tmp_path):
    """The row is joined onto the music directory, so a link out of it must never reach the player."""
    root = tmp_path / "music"
    (root / "rock").mkdir(parents=True)
    (root / "rock" / "a.mp3").write_bytes(b"")
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"")
    try:
        (root / "link.mp3").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this filesystem carries no symbolic links")
    store = SyncStore(["rock/a.mp3", "link.mp3"])
    radio = types.SimpleNamespace(pool=store, _playlist="Rock", log=logging.getLogger("test.music"),
                                  service=types.SimpleNamespace(music_dir=str(root)))
    assert Radio._read_playlist(radio) == ["rock/a.mp3"]
    deleted = [params for statement, params in store.statements if statement.startswith("DELETE")]
    assert deleted == [("Rock", "link.mp3")]


def test_a_track_row_that_names_a_location_is_skipped(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"")
    store = SyncStore(["../secret.mp3"])
    radio = types.SimpleNamespace(pool=store, _playlist="Rock", log=logging.getLogger("test.music"),
                                  service=types.SimpleNamespace(music_dir=str(root)))
    assert Radio._read_playlist(radio) == []
    assert [params for statement, params in store.statements if statement.startswith("DELETE")] == [
        ("Rock", "../secret.mp3")]


# --------------------------------------------------------------------------- the library scan

def _walk_strings(value) -> list[str]:
    """Every string in a payload, keys and values alike."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for key, item in value.items():
            found.extend(_walk_strings(key))
            found.extend(_walk_strings(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_walk_strings(item))
        return found
    return []


def test_the_library_scan_answers_the_tree_and_is_cached_until_a_refresh(tmp_path, monkeypatch):
    import asyncio

    from services.music import service as service_module

    root = tmp_path / "music"
    (root / "rock").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "track 1.mp3").write_bytes(b"")
    (root / "rock" / "track 2.ogg").write_bytes(b"")
    (root / "rock" / "notes.txt").write_text("not audio", encoding="utf-8")

    reads: list[str] = []

    def fake_tags(file):
        reads.append(file)
        return {"title": os.path.basename(file), "artist": "A", "album": "B", "duration": 10.0,
                "size": 1}

    monkeypatch.setattr(service_module, "read_tags", fake_tags)
    built = service(Store(), root=str(root))
    first = asyncio.run(built.read_library(node()))
    assert "root" not in first
    assert not [value for value in _walk_strings(first)
                if value.startswith("/") or value.startswith(str(root))], \
        "the node's absolute music directory must not cross the wire"
    assert first["files"] == 2
    assert first["seconds"] == 20.0
    folders = {folder["path"]: folder for folder in first["folders"]}
    assert sorted(folders) == ["", "empty", "rock"]
    assert folders[""]["count"] == 1
    assert folders["rock"]["files"][0]["path"] == "rock/track 2.ogg"
    assert folders["rock"]["count"] == 1
    assert sorted(reads) == sorted([str(root / "track 1.mp3"), str(root / "rock" / "track 2.ogg")])

    # a second read is answered from the cache: no second tag read
    second = asyncio.run(built.read_library(node()))
    assert second == first
    assert len(reads) == 2

    # an explicit refresh re-reads the tree and bumps the generation
    refreshed = asyncio.run(built.read_library(node(), refresh=True))
    assert len(reads) == 4
    assert refreshed["generation"] == first["generation"] + 1


def test_the_playlist_read_reports_the_missing_and_the_present_tracks(tmp_path, monkeypatch):
    import asyncio

    from services.music import service as service_module

    root = tmp_path / "music"
    root.mkdir()
    (root / "present.mp3").write_bytes(b"")
    monkeypatch.setattr(service_module, "read_tags",
                        lambda file: {"title": "Present", "artist": "A", "album": "B",
                                      "duration": 30.0, "size": 5})
    store = Store({"Rock": ["present.mp3", "gone.mp3"]})
    built = service(store, root=str(root))
    answer = asyncio.run(built.get_playlist(node(), "rock"))
    assert answer["name"] == "Rock"
    assert answer["missing"] == 1
    assert answer["seconds"] == 30.0
    assert [track["present"] for track in answer["tracks"]] == [True, False]
    assert answer["tracks"][0]["title"] == "Present"
    assert answer["tracks"][1]["title"] == "gone.mp3"


def test_the_playlist_list_answers_every_playlist_with_its_counts(tmp_path, monkeypatch):
    import asyncio

    from services.music import service as service_module

    root = tmp_path / "music"
    root.mkdir()
    (root / "a.mp3").write_bytes(b"")
    monkeypatch.setattr(service_module, "read_tags",
                        lambda file: {"title": "A", "artist": "", "album": "", "duration": 12.5,
                                      "size": 1})
    store = Store({"Rock": ["a.mp3"], "Jazz": ["missing.ogg"]},
                  radios=[["SRS-1", "Radio 1", "Rock"]])
    built = service(store, root=str(root))
    rows = asyncio.run(built.list_playlists(node()))
    assert rows == [{"name": "Jazz", "tracks": 1, "seconds": 0.0, "missing": 1},
                    {"name": "Rock", "tracks": 1, "seconds": 12.5, "missing": 0}]
    usage = asyncio.run(built.playlist_usage())
    assert usage == [{"playlist": "Rock", "server": "SRS-1", "radio": "Radio 1"}]


# --------------------------------------------------------------------------- the radio writes

def test_assigning_a_playlist_reloads_the_playing_radio():
    import asyncio

    radio = FakeRadio(running=True)
    built = service(Store({"Rock": ["rock/a.mp3"]}), radios={"SRS-1": {"Radio 1": radio}})
    reason = asyncio.run(built.assign_playlist(server(), "Radio 1", "Rock"))
    assert reason == ""
    assert radio.calls == ["stop", ("playlist", "Rock"), "start"]


def test_assigning_a_playlist_to_an_idle_radio_writes_the_row_alone():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3"]})
    built = service(store, radios={})
    reason = asyncio.run(built.assign_playlist(server(), "Radio 1", "Rock"))
    assert reason == ""
    assert store.assignments() == [("SRS-1", "Radio 1", "Rock")]


def test_assigning_a_playlist_the_installation_does_not_hold_is_refused():
    """The playlist name is peer-supplied: the row a radio reads its songs by is never a guess."""
    import asyncio

    radio = FakeRadio(running=True)
    store = Store()
    built = service(store, radios={"SRS-1": {"Radio 1": radio}})
    reason = asyncio.run(built.assign_playlist(server(), "Radio 1", "Nowhere"))
    assert reason == PLAYLIST_MISSING
    assert store.assignments() == []
    assert radio.calls == []
    assert radio.playlist_value is None


def test_assigning_an_unknown_playlist_without_a_radio_writes_no_row():
    import asyncio

    store = Store(radios=[["SRS-1", "Radio 1", "Rock"]])
    built = service(store, radios={})
    reason = asyncio.run(built.assign_playlist(server(), "Radio 1", "Nowhere"))
    assert reason == PLAYLIST_MISSING
    assert store.assignments() == [("SRS-1", "Radio 1", "Rock")]


def test_an_empty_assignment_is_not_answered_as_success():
    """An empty name returns early in the setter: nothing is written, so nothing may answer OK."""
    import asyncio

    radio = FakeRadio(running=True)
    store = Store({"Rock": ["rock/a.mp3"]})
    built = service(store, radios={"SRS-1": {"Radio 1": radio}})
    for empty in ("", "   ", None):
        reason = asyncio.run(built.assign_playlist(server(), "Radio 1", empty))
        assert reason != RADIO_OK, empty
        assert reason == RADIO_INVALID
    assert store.assignments() == []
    assert radio.calls == []


def test_assigning_a_playlist_stores_the_playlists_own_spelling():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3"]})
    built = service(store, radios={})
    assert asyncio.run(built.assign_playlist(server(), "Radio 1", "rock")) == RADIO_OK
    assert store.assignments() == [("SRS-1", "Radio 1", "Rock")]


def test_a_radio_the_config_does_not_declare_is_unknown():
    import asyncio

    built = service(Store(), radios={})
    for call in (built.assign_playlist(server(), "Radio 9", "Rock"),
                 built.start_radio(server(), "Radio 9"),
                 built.stop_radio(server(), "Radio 9"),
                 built.skip_radio(server(), "Radio 9"),
                 built.set_radio_config(server(), "Radio 9", {"volume": "0.5"}),
                 built.set_mode(server(), "Radio 9", Mode.REPEAT)):
        assert asyncio.run(call) == RADIO_UNKNOWN


def test_a_declared_radio_without_a_live_player_is_idle():
    import asyncio

    built = service(Store(), radios={})
    assert asyncio.run(built.start_radio(server(), "Radio 1")) == RADIO_IDLE


def test_a_radio_config_write_stops_and_restarts_the_player(monkeypatch):
    import asyncio

    from services.music import service as service_module

    written: list[dict] = []
    monkeypatch.setattr(service_module, "write_radio_config",
                        lambda *args: written.append(args))
    radio = FakeRadio(running=True)
    built = service(Store(), radios={"SRS-1": {"Radio 1": radio}})
    reason = asyncio.run(built.set_radio_config(server(), "Radio 1", {"frequency": "31.5"}))
    assert reason == ""
    assert radio.calls == ["stop", ("config", {"frequency": "31.5"}), "start"]
    assert written == []


def test_a_radio_config_write_of_an_idle_radio_lands_in_the_file(monkeypatch):
    import asyncio

    from services.music import service as service_module

    written: list[tuple] = []
    monkeypatch.setattr(service_module, "write_radio_config",
                        lambda *args: written.append(args))
    built = service(Store(), radios={})
    reason = asyncio.run(built.set_radio_config(server(), "Radio 1", {"frequency": "31.5"}))
    assert reason == ""
    assert written[0][1].name == "SRS-1"
    assert written[0][2] == "Radio 1"
    assert written[0][3] == {"frequency": "31.5"}


def test_the_mode_write_persists_the_mode_and_answers_its_name(monkeypatch):
    import asyncio

    from services.music import service as service_module

    written: list[dict] = []
    monkeypatch.setattr(service_module, "write_radio_config",
                        lambda *args: written.append(args[3]))
    radio = FakeRadio()
    built = service(Store(), radios={"SRS-1": {"Radio 1": radio}})
    assert asyncio.run(built.set_mode(server(), "Radio 1", Mode.SHUFFLE)) == ""
    assert radio.mode is Mode.SHUFFLE
    assert written == [{"mode": 2}]
    assert radio.calls[-1] == "reset"


def test_read_radios_reports_the_state_the_assignment_and_the_current_track(tmp_path, monkeypatch):
    import asyncio

    from services.music import service as service_module

    root = tmp_path / "music"
    root.mkdir()
    (root / "a.mp3").write_bytes(b"")
    monkeypatch.setattr(service_module, "read_tags",
                        lambda file: {"title": "Song", "artist": "Someone", "album": "Record",
                                      "duration": 42.0, "size": 9})
    radio = FakeRadio(running=True)
    radio.current = "Song"
    radio.current_file = "a.mp3"
    built = service(Store(radios=[["SRS-1", "Radio 1", "Rock"]]), root=str(root),
                    radios={"SRS-1": {"Radio 1": radio}})
    radios = asyncio.run(built.read_radios(server()))
    assert len(radios) == 1
    entry = radios[0]
    assert entry["name"] == "Radio 1"
    assert entry["on_air"] is True
    assert entry["playlist"] == "Rock"
    assert entry["current"]["title"] == "Song"
    assert entry["current"]["artist"] == "Someone"
    assert entry["frequency"] == ""          # declared by the config, empty when it states none
    assert entry["mode"] == "repeat"


def test_a_row_written_with_windows_separators_resolves_its_file(tmp_path, monkeypatch):
    """``os.path.relpath`` on Windows keeps the backslash: one row, one file, either separator."""
    import asyncio

    from services.music import service as service_module

    root = tmp_path / "music"
    (root / "rock").mkdir(parents=True)
    (root / "rock" / "a.mp3").write_bytes(b"")
    monkeypatch.setattr(service_module, "read_tags",
                        lambda file: {"title": "A", "artist": "", "album": "", "duration": 5.0,
                                      "size": 1})
    store = Store({"Rock": ["rock\\a.mp3"]})
    built = service(store, root=str(root))
    answer = asyncio.run(built.get_playlist(node(), "Rock"))
    assert answer["missing"] == 0
    assert answer["tracks"][0]["present"] is True
    assert answer["tracks"][0]["title"] == "A"
    assert service_module.library_key("rock\\a.mp3") == "rock/a.mp3"


# ------------------------------------------------------- one track once per playlist (the constraint)

def test_adding_a_track_the_playlist_already_holds_writes_nothing():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3"]})
    built = service(store)
    before = store.ordered("Rock")
    answer = asyncio.run(built.add_tracks("Rock", ["rock/a.mp3"]))
    assert answer["status"] == PLAYLIST_DUPLICATE
    assert answer["added"] == 0
    assert answer["already"] == 1
    assert store.ordered("Rock") == before


def test_adding_a_folder_counts_what_went_in_and_what_was_already_there():
    import asyncio

    store = Store({"Rock": ["rock/a.mp3", "rock/b.mp3"]})
    built = service(store)
    answer = asyncio.run(built.add_tracks("Rock", ["rock/a.mp3", "rock/b.mp3", "rock/c.mp3"]))
    assert answer["status"] == PLAYLIST_OK
    assert answer["added"] == 1
    assert answer["already"] == 2
    assert store.ordered("Rock") == ["rock/a.mp3", "rock/b.mp3", "rock/c.mp3"]


def test_a_track_is_one_row_however_many_writers_ask_for_it_at_once():
    """The write is ``ON CONFLICT DO NOTHING``: the constraint decides, the loser reports the fact."""
    import asyncio

    store = Store({"Rock": ["rock/a.mp3"]})
    built = service(store)
    first, second = asyncio.run(built.add_tracks("Rock", ["rock/b.mp3"])), \
        asyncio.run(built.add_tracks("Rock", ["rock/b.mp3"]))
    assert (first["added"], second["added"]) == (1, 0)
    assert store.ordered("Rock") == ["rock/a.mp3", "rock/b.mp3"]
    assert all("ON CONFLICT (name, song_file) DO NOTHING" in statement
               for statement, _params in store.ran("INSERT INTO music_playlists"))
