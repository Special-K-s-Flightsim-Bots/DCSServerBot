"""Containment on the WRITE path, and the exclusive create — one rule, two surfaces, real disks.

Two findings from the music-upload review are pinned here, each by RESULT rather than by shape:

1. a folder that is a symbolic link (on Windows a junction) inside the library and points elsewhere is
   REFUSED, and nothing is written outside. Judged on the RESOLVED path by
   :func:`core.utils.resolve_within` — the one rule — and reached from BOTH places that hold a tree:
   the music upload (:func:`upload_destination` / ``MusicService.save_track``) and
   ``utils.sanitize_filename``, which the MISSION upload uses;
2. a name the folder already holds is refused by an EXCLUSIVE CREATE (:attr:`UploadStatus.FILE_EXISTS`),
   not by a check that another writer can pass at the same time.

The cases drive a real temporary directory; the only doubles are the node's writer (it IS the disk)
and a connection pool that answers one staged row.
"""
from __future__ import annotations

import asyncio
import logging
import os
import types

import pytest

from conftest import needs_symlinks, symlinks_available

import core.data.impl.nodeimpl as nodeimpl
from core import UploadStatus
from core import utils
from core.data.impl.nodeimpl import NodeImpl
from core.utils import resolve_within
from services.music.service import (
    TRACK_EXISTS, TRACK_FOLDER_INVALID, TRACK_OK, upload_destination,
)

# the music upload suite's own service builder and disk double, reused so both files drive ONE service
from test_music_upload import TRACK_BYTES, UPLOAD, _Node, library, service  # noqa: F401 - fixtures


# --------------------------------------------------------------------------- the shared primitive

def test_resolve_within_accepts_an_inside_path_and_refuses_every_route_out(tmp_path):
    root = tmp_path / "lib"
    (root / "jungle").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    assert resolve_within(root, root / "jungle" / "a.mp3") == str(root / "jungle" / "a.mp3")
    assert resolve_within(root, str(root / "jungle")) == str(root / "jungle"), "a directory is inside"
    for escaped in (root / "a.mp3" / ".." / ".." / "outside" / "a.mp3", tmp_path / "a.mp3",
                    outside / "a.mp3", root):
        assert resolve_within(root, escaped) is None, escaped


@needs_symlinks
def test_resolve_within_refuses_a_link_that_points_out_and_accepts_one_that_points_in(tmp_path):
    """The rule is about where the path RESOLVES, never about the link: both answers are pinned."""
    root = tmp_path / "lib"
    (root / "jungle").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "away")
    os.symlink(root / "jungle", root / "here")

    assert resolve_within(root, root / "away" / "a.mp3") is None
    assert resolve_within(root, root / "here" / "a.mp3") == str(root / "jungle" / "a.mp3")


# --------------------------------------------------------------------------- the MISSION surface

@needs_symlinks
def test_sanitize_filename_refuses_a_symlinked_subfolder_of_the_missions_directory(tmp_path):
    base = tmp_path / "missions"
    base.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    os.symlink(outside, base / "sub")
    (base / "real").mkdir()

    with pytest.raises(ValueError):
        utils.sanitize_filename(os.path.join(str(base), "sub", "x.miz"), base)
    assert list(outside.iterdir()) == [], "nothing was resolved into the linked folder"

    kept = utils.sanitize_filename(os.path.join(str(base), "real", "x.miz"), base)
    assert kept == base.resolve() / "real" / "x.miz"
    # the shape the mission upload actually uses — one plain leaf — is untouched
    assert utils.sanitize_filename(os.path.join(str(base), "x.miz"), base) == base.resolve() / "x.miz"


# --------------------------------------------------------------------------- the MUSIC surface

@needs_symlinks
def test_upload_destination_refuses_a_symlinked_folder_that_points_outside_the_library(tmp_path):
    root = tmp_path / "music"
    (root / "jungle").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "linked")

    assert upload_destination(str(root), "linked", "a.mp3") is None
    assert upload_destination(str(root), "linked/sub", "a.mp3") is None
    # a link INSIDE the library is a folder like any other: it resolves to a path that is accepted
    os.symlink(root / "jungle", root / "here")
    assert upload_destination(str(root), "here", "a.mp3") == (
        str(root / "jungle" / "a.mp3"), "here", "here/a.mp3")


@needs_symlinks
def test_the_service_refuses_a_symlinked_folder_and_writes_nothing_outside(library, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, library / "linked")
    node = _Node(staged={21: TRACK_BYTES})
    built = service(library, node)

    answer = asyncio.run(built.save_track(node, "linked", UPLOAD, 21))

    assert answer["status"] == TRACK_FOLDER_INVALID, answer
    assert node.attempts == [], "the writer is never reached"
    assert list(outside.iterdir()) == [], "no byte landed outside the library"
    assert node.staged == {21: TRACK_BYTES}, "the caller's staged row is still its own"


@needs_symlinks
def test_the_service_still_writes_a_folder_that_resolves_inside_the_library(library):
    """The containment refuses a ROUTE out, not a link: a link that stays inside is a folder."""
    os.symlink(library / "jungle", library / "here")
    node = _Node(staged={22: TRACK_BYTES})
    built = service(library, node)

    answer = asyncio.run(built.save_track(node, "here", UPLOAD, 22))

    assert answer["status"] == TRACK_OK, answer
    assert (library / "jungle" / UPLOAD).read_bytes() == TRACK_BYTES


# --------------------------------------------------------------------------- the exclusive create

class _Row:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Conn:
    """The one statement pair ``write_file`` makes on a staged row: the read, then the delete."""

    def __init__(self, rows, deleted):
        self.rows = rows
        self.deleted = deleted

    async def execute(self, statement, params: tuple | None = None, binary: bool = False):
        key = params[0] if params else None
        if "SELECT" in statement:
            row = self.rows.get(key)
            return _Row(None if row is None else (row,))
        self.deleted.append(key)
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Pool:
    """A pool that answers one ``files`` row and records the delete."""

    def __init__(self, rows):
        self.rows = dict(rows)
        self.deleted: list[int] = []

    def connection(self):
        return _Conn(self.rows, self.deleted)


def master_node(pool=None) -> NodeImpl:
    """A real ``NodeImpl`` in its LOCAL shape: its writer puts the bytes on THIS disk."""
    node = object.__new__(NodeImpl)
    node.node = types.SimpleNamespace(master=True)
    node.log = logging.getLogger("test.node.write")
    node.locals = {"proxy": None, "proxy_auth": None}
    if pool is not None:
        node.apool = pool
    return node


@pytest.mark.asyncio
async def test_the_writer_refuses_an_occupied_name_by_an_exclusive_create(tmp_path, monkeypatch):
    """A file the folder ALREADY holds is refused without the caller-side check having a say.

    ``os.path.exists`` is forced to ``False``, which is exactly what two uploads arriving together
    see: both pass the check, and only the exclusive create can refuse the second one.
    """
    target = tmp_path / "jungle" / "a.mp3"
    target.parent.mkdir()
    target.write_bytes(b"OLD")
    pool = _Pool({7: b"NEW"})
    node = master_node(pool)
    monkeypatch.setattr(os.path, "exists", lambda path: False)

    outcome = await node.write_file(str(target), 7)

    assert outcome is UploadStatus.FILE_EXISTS, outcome
    assert target.read_bytes() == b"OLD", "the first writer's bytes survive"
    assert pool.deleted == [7], "the attempt consumed its staged row, as it always has"


@pytest.mark.asyncio
async def test_the_writers_copy_and_url_paths_are_exclusive_too(tmp_path, monkeypatch):
    """The two non-row sources share the one rule; ``overwrite=True`` still replaces, as before."""
    target = tmp_path / "copy.miz"
    target.write_bytes(b"OLD")
    source = tmp_path / "source.miz"
    source.write_bytes(b"NEW")
    node = master_node()
    monkeypatch.setattr(os.path, "exists", lambda path: False)

    assert await node.write_file(str(target), str(source)) is UploadStatus.FILE_EXISTS
    assert target.read_bytes() == b"OLD"

    assert await node.write_file(str(target), str(source), True) is UploadStatus.OK
    assert target.read_bytes() == b"NEW"


def test_the_service_answers_the_occupied_name_the_writer_reports(library):
    """A writer that raised instead of answering is the SAME refusal for the operator."""
    class Raises:
        name = "dcs-a-01"
        master = True
        is_remote = False

        async def write_file(self, target, source, overwrite=False):
            raise FileExistsError(target)

    node = Raises()
    built = service(library, node)

    answer = asyncio.run(built.save_track(node, "jungle", UPLOAD, 31))

    assert answer["status"] == TRACK_EXISTS, answer
    assert not (library / "jungle" / UPLOAD).exists()


# ------------------------------------------------------- a failed write leaves no file behind

@pytest.mark.asyncio
async def test_a_failed_copy_removes_its_own_file_and_the_name_stays_free(tmp_path, monkeypatch):
    """A copy that dies mid-body must not leave the 0-byte file that blocks the name forever."""
    target = tmp_path / "copy.miz"
    source = tmp_path / "source.miz"
    source.write_bytes(b"NEW BYTES")
    node = master_node()

    real_copy = nodeimpl.shutil.copyfileobj

    def die(src, out, *args, **kwargs):
        out.write(b"partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(nodeimpl.shutil, "copyfileobj", die)
    outcome = await node.write_file(str(target), str(source))

    assert outcome is UploadStatus.WRITE_ERROR, outcome
    assert not target.exists(), "the file this call created was removed, not left at 0 bytes"

    monkeypatch.setattr(nodeimpl.shutil, "copyfileobj", real_copy)
    assert await node.write_file(str(target), str(source)) is UploadStatus.OK, "the name is retryable"
    assert target.read_bytes() == b"NEW BYTES"


@pytest.mark.asyncio
async def test_a_failed_url_body_removes_its_own_file_and_the_name_stays_free(tmp_path):
    """A body that stops short of its Content-Length fails the read after the create; clean up."""
    target = tmp_path / "url.miz"

    async def truncate(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\n\r\npartial")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def whole(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nWHOLE!")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    node = master_node()
    broken = await asyncio.start_server(truncate, "127.0.0.1", 0)
    broken_port = broken.sockets[0].getsockname()[1]
    async with broken:
        outcome = await node.write_file(str(target), f"http://127.0.0.1:{broken_port}/x.miz")

    assert outcome is UploadStatus.WRITE_ERROR, outcome
    assert not target.exists(), "the file this call created was removed, not left at 0 bytes"

    good = await asyncio.start_server(whole, "127.0.0.1", 0)
    good_port = good.sockets[0].getsockname()[1]
    async with good:
        assert await node.write_file(str(target), f"http://127.0.0.1:{good_port}/x.miz") is UploadStatus.OK
    assert target.read_bytes() == b"WHOLE!"


@pytest.mark.asyncio
async def test_a_failed_row_write_keeps_the_staged_row_and_a_successful_one_consumes_it(tmp_path, monkeypatch):
    """The staged bytes survive a failed write; a write that lands consumes the row after the fact."""
    target = tmp_path / "track.mp3"
    pool = _Pool({7: b"AUDIO BYTES"})
    node = master_node(pool)

    real_open = nodeimpl.aiofiles.open

    class DiesOnWrite:
        def __init__(self, handle):
            self._handle = handle

        async def __aenter__(self):
            await self._handle.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._handle.__aexit__(*exc)

        async def write(self, data):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(nodeimpl.aiofiles, "open", lambda *a, **k: DiesOnWrite(real_open(*a, **k)))
    outcome = await node.write_file(str(target), 7)

    assert outcome is UploadStatus.WRITE_ERROR, outcome
    assert not target.exists(), "the file this call created was removed"
    assert pool.deleted == [], "the staged row is still the caller's to retry with"

    monkeypatch.setattr(nodeimpl.aiofiles, "open", real_open)
    assert await node.write_file(str(target), 7) is UploadStatus.OK, "the retry uses the same row"
    assert target.read_bytes() == b"AUDIO BYTES"
    assert pool.deleted == [7], "the row is consumed ONLY after the bytes are on disk"

