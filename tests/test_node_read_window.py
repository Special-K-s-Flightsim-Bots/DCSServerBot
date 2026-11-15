"""The windowed node read (L1, Part A): a bounded WINDOW of a file, never the whole file.

The read path's ``read_file`` is a WHOLE-FILE contract and cannot move a running server's ``dcs.log``
tail. :meth:`Node.read_file_window` is its read-only, WINDOWED sibling: ``offset`` reads forward (the
way a follower continues) and ``behind`` reads backward (the way a reader pages through a file it
cannot move whole). This module pins the REAL local implementation (the master branch, which reads
its own disk with a ``seek``) on the boundaries the card names — an offset at the end, an offset past
it, a window larger than the file, a missing file, a shrunk file — plus the size-at-read-time AND the
file IDENTITY the reader needs to see a rotation (L1-fix), and the PRIMITIVE'S OWN CEILING on
``length``.
"""
from __future__ import annotations

import os
import types

import pytest

from core.data.impl.nodeimpl import NODE_READ_WINDOW_MAX_BYTES, NodeImpl, file_identity
from services.webservice.readmodels import dcslog as dcs_log

HALF = b"AAA\nBBB\nCCC\n"


def master_node() -> NodeImpl:
    """A real ``NodeImpl`` in its LOCAL (master) shape: ``read_file_window`` reads this disk directly."""
    node = object.__new__(NodeImpl)
    node.node = types.SimpleNamespace(master=True)
    return node


@pytest.fixture
def logfile(tmp_path):
    path = tmp_path / "Logs" / "dcs.log"
    path.parent.mkdir(parents=True)
    path.write_bytes(HALF)
    return path


@pytest.mark.asyncio
async def test_a_window_larger_than_the_file_returns_the_whole_file_and_its_size(logfile):
    window, size, _ = await master_node().read_file_window(str(logfile), length=10_000, offset=0)
    assert window == HALF
    assert size == len(HALF)


@pytest.mark.asyncio
async def test_an_offset_at_the_end_returns_an_empty_window_and_the_size(logfile):
    window, size, _ = await master_node().read_file_window(str(logfile), length=10_000,
                                                           offset=len(HALF))
    assert window == b""
    assert size == len(HALF)


@pytest.mark.asyncio
async def test_an_offset_past_the_end_returns_an_empty_window_and_the_size(logfile):
    """The reader sees ``size < offset`` and knows the file was truncated/rotated (a NEW log)."""
    window, size, _ = await master_node().read_file_window(str(logfile), length=10_000, offset=99)
    assert window == b""
    assert size < 99


@pytest.mark.asyncio
async def test_a_short_length_returns_only_that_many_bytes_from_the_offset(logfile):
    window, size, _ = await master_node().read_file_window(str(logfile), length=4, offset=4)
    assert window == HALF[4:8]
    assert size == len(HALF)


@pytest.mark.asyncio
async def test_behind_reads_the_window_ending_there(logfile):
    window, size, _ = await master_node().read_file_window(str(logfile), length=8, behind=12)
    assert window == b"BBB\nCCC\n"
    assert size == 12


@pytest.mark.asyncio
async def test_behind_past_the_end_is_clamped_to_the_file(logfile):
    window, _, _ = await master_node().read_file_window(str(logfile), length=8, behind=2 ** 63 - 1)
    assert window == b"BBB\nCCC\n"          # the LAST 8 bytes, not an empty window


@pytest.mark.asyncio
async def test_a_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        await master_node().read_file_window(str(tmp_path / "nope.log"), length=8, offset=0)


@pytest.mark.asyncio
async def test_a_shrunk_file_reports_a_smaller_size_than_the_offset(logfile):
    """A rotation/truncation: ``size`` falls below the follower's offset — the detection the reader uses."""
    before, size_before, _ = await master_node().read_file_window(str(logfile), length=8, offset=0)
    assert size_before == 12
    logfile.write_bytes(b"X\n")             # the log was truncated/rotated
    window, size_after, _ = await master_node().read_file_window(str(logfile), length=8, offset=12)
    assert window == b""
    assert size_after == 2 < 12


@pytest.mark.asyncio
async def test_exactly_one_of_offset_or_behind_is_required(logfile):
    node = master_node()
    with pytest.raises(ValueError):
        await node.read_file_window(str(logfile), length=5)
    with pytest.raises(ValueError):
        await node.read_file_window(str(logfile), length=5, offset=0, behind=5)
    with pytest.raises(ValueError):
        await node.read_file_window(str(logfile), length=0, offset=0)
    with pytest.raises(ValueError):
        await node.read_file_window(str(logfile), length=5, offset=-1)


# ------------------------------------------------------------------- the primitive's own ceiling

def test_the_ceiling_is_at_least_the_consoles_window():
    """The caller's window is a value UNDER the ceiling, not the only thing standing between a log and
    a whole-file transfer."""
    assert 0 < dcs_log.WINDOW_BYTES <= NODE_READ_WINDOW_MAX_BYTES


@pytest.mark.asyncio
async def test_an_absurd_length_is_clamped_to_the_ceiling(tmp_path):
    """A caller asking for far more than the ceiling still gets a bounded window — the primitive refuses
    to move a whole file, whatever is asked of it."""
    big = tmp_path / "big.log"
    big.write_bytes(b"x" * (NODE_READ_WINDOW_MAX_BYTES + 4096))
    window, size, _ = await master_node().read_file_window(str(big), length=1_000_000_000, offset=0)
    assert len(window) == NODE_READ_WINDOW_MAX_BYTES
    assert size == NODE_READ_WINDOW_MAX_BYTES + 4096


@pytest.mark.asyncio
async def test_a_behind_read_is_clamped_to_the_ceiling_too(tmp_path):
    big = tmp_path / "big.log"
    big.write_bytes(b"y" * (NODE_READ_WINDOW_MAX_BYTES + 4096))
    window, _, _ = await master_node().read_file_window(str(big), length=1_000_000_000,
                                                        behind=2 ** 63 - 1)
    assert len(window) == NODE_READ_WINDOW_MAX_BYTES


# ---------------------------------------------------------------------------- the file identity

@pytest.mark.asyncio
async def test_the_read_carries_a_file_identity(logfile):
    _, _, identity = await master_node().read_file_window(str(logfile), length=8, offset=0)
    assert isinstance(identity, (int, float))


@pytest.mark.asyncio
async def test_the_identity_does_not_change_when_the_file_is_APPENDED_to(logfile):
    """An append is NOT a rotation: the follower must keep splicing (the identity stays put)."""
    _, _, before = await master_node().read_file_window(str(logfile), length=8, offset=0)
    with open(logfile, "ab") as handle:
        handle.write(b"DDD\n")
    _, _, after = await master_node().read_file_window(str(logfile), length=8, offset=0)
    assert after == before


@pytest.mark.asyncio
async def test_the_identity_changes_when_the_file_is_REPLACED(tmp_path):
    """A rotation: the name points at a DIFFERENT file afterwards, so the identity moves."""
    path = tmp_path / "dcs.log"
    path.write_bytes(b"old log\n")
    _, _, before = await master_node().read_file_window(str(path), length=8, offset=0)
    replacement = tmp_path / "dcs.log.new"
    replacement.write_bytes(b"a brand new file, already longer than the offset\n")
    os.replace(replacement, path)           # rotate: the SAME name, a different inode
    _, _, after = await master_node().read_file_window(str(path), length=8, offset=0)
    assert after != before


def test_file_identity_reports_the_inode_where_ctime_is_not_a_creation_time(tmp_path):
    """On POSIX ``st_ctime`` is an inode-change time (it moves on every write), so the identity reports
    the INODE there — stable across an append — not ``st_ctime``."""
    path = tmp_path / "x.log"
    path.write_bytes(b"one\n")
    st = os.stat(path)
    expected = st.st_ctime if os.name == "nt" else st.st_ino
    assert file_identity(st) == expected
