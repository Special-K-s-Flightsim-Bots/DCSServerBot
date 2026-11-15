"""The file listing node read (L2): the artifacts a download list needs, each with size and mtime.

:meth:`Node.list_files` is a read-only sibling of :meth:`Node.list_directory`, adding the two facts a
download list carries — each file's SIZE and its MODIFICATION TIME — and ordering the result NEWEST
FIRST. This module pins the REAL local implementation (the master branch, which reads its own disk)
on the boundaries the card names: the order, the size/mtime, a pattern, and a missing directory that
must be an EMPTY list rather than an error.
"""
from __future__ import annotations

import os
import types

import pytest

from core.data.impl.nodeimpl import NodeImpl


def master_node() -> NodeImpl:
    """A real ``NodeImpl`` in its LOCAL (master) shape: ``list_files`` reads this disk directly."""
    node = object.__new__(NodeImpl)
    node.node = types.SimpleNamespace(master=True)
    return node


def touch(path, text: str, mtime: float):
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


@pytest.mark.asyncio
async def test_the_listing_carries_path_size_and_mtime_newest_first(tmp_path):
    logs = tmp_path / "Logs"
    logs.mkdir()
    touch(logs / "dcs.log", "live", 3_000)
    touch(logs / "dcs.log.old", "previous", 1_000)
    touch(logs / "dcs-crash.zip", "x" * 40, 2_000)

    rows = await master_node().list_files(str(logs), pattern=["dcs*.log*", "dcs*.zip"])
    names = [os.path.basename(path) for path, _, _ in rows]
    assert names == ["dcs.log", "dcs-crash.zip", "dcs.log.old"], "newest first"
    sizes = {os.path.basename(path): size for path, size, _ in rows}
    assert sizes["dcs.log.old"] == len("previous")
    assert sizes["dcs-crash.zip"] == 40
    mtimes = {os.path.basename(path): mtime for path, _, mtime in rows}
    assert mtimes["dcs.log"] == 3_000 and mtimes["dcs.log.old"] == 1_000


@pytest.mark.asyncio
async def test_the_listing_returns_absolute_paths(tmp_path):
    logs = tmp_path / "Logs"
    logs.mkdir()
    touch(logs / "dcs.log", "x", 1_000)
    rows = await master_node().list_files(str(logs), pattern="dcs*.log*")
    assert rows and os.path.isabs(rows[0][0])
    assert rows[0][0].endswith("dcs.log")


@pytest.mark.asyncio
async def test_a_missing_directory_is_an_empty_list_not_an_error(tmp_path):
    rows = await master_node().list_files(str(tmp_path / "nope"), pattern="dcs*.log*")
    assert rows == []


@pytest.mark.asyncio
async def test_the_pattern_selects_only_matching_files(tmp_path):
    logs = tmp_path / "Logs"
    logs.mkdir()
    touch(logs / "dcs.log", "x", 1_000)
    touch(logs / "events.log", "y", 2_000)
    touch(logs / "other.txt", "z", 3_000)
    rows = await master_node().list_files(str(logs), pattern=["dcs*.log*", "events.log"])
    names = sorted(os.path.basename(path) for path, _, _ in rows)
    assert names == ["dcs.log", "events.log"], "other.txt is not an artifact"


@pytest.mark.asyncio
async def test_directories_are_not_listed(tmp_path):
    logs = tmp_path / "Logs"
    (logs / "dcs.log.d").mkdir(parents=True)
    touch(logs / "dcs.log", "x", 1_000)
    rows = await master_node().list_files(str(logs), pattern="dcs*")
    assert [os.path.basename(path) for path, _, _ in rows] == ["dcs.log"]