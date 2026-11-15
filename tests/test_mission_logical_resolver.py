"""M5-fix3: ONE mission-path normaliser (``core.utils.logical_mission_path``), everywhere.

The reviewer's P12 proved the collision: a bare ``str.replace('.dcssb', '')`` maps
``foo.dcssbbar`` onto ``foobar``. M5-fix gave the resolver's callers a component-anchored
normaliser, but ``ServerImpl.getAllMissionFiles`` still carried its own substring copy — and
``getAllMissionFiles`` is what decides WHICH copy is current and feeds the tab, the add picker and
the upload path. These tests pin the resolver itself (the real ``ServerImpl`` method, over a stub
``self`` and a real on-disk tree) and the two mirrors the card names (``_configured_name`` /
``_copy_of`` in the mission actions), so the three can never drift apart again.
"""
from __future__ import annotations

import logging
import os
import types

import pytest

from core.utils.dcs import logical_mission_path
from core.data.impl.serverimpl import ServerImpl
import plugins.mission.actions as mission_actions


# ----------------------------------------------------------------- the resolver's stub ``self``

class _Node:
    """The server's node: ``list_directory`` answers ``getAllMissionFiles`` with the PRIMARIES.

    The real node never yields the ``.dcssb`` copies (they are in the ``ignore`` list), so the
    listing hands back the primary ``*.miz`` files and the resolver finds the copy/orig beside each
    one on the real disk.
    """

    def __init__(self, base, missions):
        self._base = str(base)
        self._missions = [str(m) for m in missions]

    async def list_directory(self, path, pattern=None, traverse=False, ignore=None):
        return self._base, list(self._missions)


class ResolverStub:
    """A minimal ``self`` for ``ServerImpl.getAllMissionFiles``: the node and the instance's dir."""

    def __init__(self, base, missions):
        self.instance = types.SimpleNamespace(missions_dir=str(base))
        self.node = _Node(base, missions)


def _touch(path, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    os.utime(path, (mtime, mtime))


async def _resolve(base, missions):
    return await ServerImpl.getAllMissionFiles(ResolverStub(base, missions))


# ------------------------------------------------------------- 1. the resolver names the winner

@pytest.mark.asyncio
async def test_colliding_directories_resolve_to_distinct_logical_names(tmp_path):
    """``foo.dcssbbar`` and ``foobar`` are two missions — the old substring replace made them one.

    ``foo.dcssbbar/C.miz`` wins with its PRIMARY (its ``.dcssb`` copy is older, so the primary is the
    current file); ``foobar/C.miz`` wins with its newer ``.dcssb`` copy. Under the old
    ``str.replace('.dcssb', '')`` BOTH logical names came out ``foobar/C.miz`` — the two missions
    collapsed into one row, and the tab/pointer resolved the wrong file. Component anchoring keeps
    them distinct and the newest-copy rule still picks the right physical file for each.
    """
    folder = tmp_path / "Missions"
    _touch(folder / "foo.dcssbbar" / "C.miz", 5_000)               # primary wins for foo.dcssbbar
    _touch(folder / "foo.dcssbbar" / ".dcssb" / "C.miz", 1_000)    # its older copy
    _touch(folder / "foobar" / "C.miz", 2_000)
    _touch(folder / "foobar" / ".dcssb" / "C.miz", 6_000)          # the newer copy wins for foobar

    resolved = dict(await _resolve(folder, [
        folder / "foo.dcssbbar" / "C.miz",
        folder / "foobar" / "C.miz",
    ]))

    assert set(resolved) == {
        str(folder / "foo.dcssbbar" / "C.miz"),
        str(folder / "foobar" / "C.miz"),
    }, "the two directories must not collide into one logical name"
    assert resolved[str(folder / "foo.dcssbbar" / "C.miz")] == \
        str(folder / "foo.dcssbbar" / "C.miz"), "foo.dcssbbar's primary is the current file"
    assert resolved[str(folder / "foobar" / "C.miz")] == \
        str(folder / "foobar" / ".dcssb" / "C.miz"), "foobar's newer .dcssb copy is the current file"


@pytest.mark.asyncio
async def test_the_orig_winner_resolves_to_the_logical_primary_name(tmp_path):
    """A primary whose ``.orig`` is newer still resolves to the LOGICAL PRIMARY name.

    The old ``shorten_filename`` returned ``file[:-5]`` first, so an ``.orig`` winner kept its
    ``.dcssb`` component in the LOGICAL name (``.dcssb/Backup.miz``) — a SECOND row for the same
    mission. Component anchoring strips both the ``.orig`` suffix AND the ``.dcssb`` component.
    """
    folder = tmp_path / "Missions"
    _touch(folder / "Backup.miz", 1_000)
    _touch(folder / ".dcssb" / "Backup.miz.orig", 5_000)          # the newer orig wins

    resolved = dict(await _resolve(folder, [folder / "Backup.miz"]))

    assert resolved == {
        str(folder / "Backup.miz"): str(folder / ".dcssb" / "Backup.miz.orig"),
    }


@pytest.mark.asyncio
async def test_a_plain_mission_with_a_newer_dcssb_copy_names_the_primary(tmp_path):
    """The ordinary copy case: the ``.dcssb`` copy is newer, the row is named by the primary."""
    folder = tmp_path / "Missions"
    _touch(folder / "Rotated.miz", 1_000)
    _touch(folder / ".dcssb" / "Rotated.miz", 4_000)

    resolved = dict(await _resolve(folder, [folder / "Rotated.miz"]))

    assert resolved == {str(folder / "Rotated.miz"): str(folder / ".dcssb" / "Rotated.miz")}


# ------------------------------------------------------------- 2. the ONE normaliser, unit level

def test_logical_mission_path_drops_only_the_exact_component():
    """A component that IS ``.dcssb`` is dropped; a NAME that merely contains it is not."""
    assert logical_mission_path(os.path.join("/m", ".dcssb", "C.miz")) == os.path.normpath(os.path.join("/m", "C.miz"))
    assert logical_mission_path(os.path.join("/m", "C.miz.orig")) == os.path.normpath(os.path.join("/m", "C.miz"))
    assert logical_mission_path(os.path.join("/m", ".dcssb", "C.miz.orig")) == os.path.normpath(os.path.join("/m", "C.miz"))
 # the collision the bare replace created:
    assert logical_mission_path(os.path.join("/m", "foo.dcssbbar", "C.miz")) == \
        os.path.normpath(os.path.join("/m", "foo.dcssbbar", "C.miz"))
    assert logical_mission_path(os.path.join("/m", "foobar", "C.miz")) == os.path.normpath(os.path.join("/m", "foobar", "C.miz"))


# ------------------------------------------------------------- 3. the two mirrors the card names

def test_configured_name_uses_the_shared_normaliser():
    """``_configured_name`` no longer keeps a second substring implementation of the strip."""
    base = os.path.join(os.sep, "missions")
    assert mission_actions._configured_name(os.path.join(base, ".dcssb", "Training.miz"), base) == \
        "Training.miz"
    assert mission_actions._configured_name(os.path.join(base, "Training.miz"), base) == "Training.miz"
 # a directory whose NAME contains the marker is NOT treated as the copy:
    assert mission_actions._configured_name(os.path.join(base, "foo.dcssbbar", "C.miz"), base) == \
        os.path.normpath(os.path.join("foo.dcssbbar", "C.miz"))


def test_copy_of_classifies_by_component():
    """``_copy_of`` decides the copy by the COMPONENT, never the substring."""
    assert mission_actions._copy_of(os.path.join("/m", ".dcssb", "X.miz")) == "secondary"
    assert mission_actions._copy_of(os.path.join("/m", ".dcssb", "X.miz.orig")) == "orig"
    assert mission_actions._copy_of(os.path.join("/m", "X.miz")) == "primary"
    assert mission_actions._copy_of(os.path.join("/m", "foo.dcssbbar", "X.miz")) == "primary"


# --------------------------------------------- M5-fix4: the normaliser knows BOTH separators

def test_logical_mission_path_knows_both_separators():
    """A ``.dcssb`` COMPONENT is dropped however it is delimited — forward slash, backslash, or a mix.

    This bot runs on Windows as well as on Linux, so a path that arrives with the OTHER host's
    separator (config file, node reply, JSON payload, another host's build) must still be split into
    components; a single-separator split let the ``.dcssb`` component survive and silently defeated
    the collision protection for that path. The marker is still a COMPONENT, never a substring.
    """
    # normalised like the product: os.path.join("/m", ...) keeps the leading "/" on Windows while
    # the resolver answers a normalised "\\m\\C.miz"
    expected = os.path.normpath(os.path.join("/m", "C.miz"))
    assert logical_mission_path("/m/.dcssb/C.miz") == expected                 # forward slash
    assert logical_mission_path("\\m\\.dcssb\\C.miz") == expected             # Windows backslash
    assert logical_mission_path("/m\\.dcssb/C.miz") == expected               # mixed separators
    assert logical_mission_path("\\m/.dcssb\\C.miz") == expected              # mixed, the other way
 # a trailing ``.orig`` still strips exactly as before, on either separator
    assert logical_mission_path("\\m\\.dcssb\\C.miz.orig") == expected
 # a name that merely CONTAINS the letters is never touched, on either separator
    assert logical_mission_path("\\m\\foo.dcssbbar\\C.miz") == \
        os.path.normpath(os.path.join("/m", "foo.dcssbbar", "C.miz"))


# --------------------------------------------- M5-fix4: a skipped check must be visible

class _UnreadableServer:
    """A server whose missions dir cannot be read — the skip the log must record."""

    async def get_missions_dir(self):
        raise RuntimeError("node offline")


@pytest.mark.asyncio
async def test_a_skipped_staleness_check_is_logged(caplog):
    """An unreadable missions dir skips the guard — silently that is indistinguishable from a pass."""
    with caplog.at_level(logging.WARNING, logger=mission_actions.__name__):
        reason = await mission_actions._stale_mission_refusal(
            _UnreadableServer(), "Missions/C.miz", "C.miz")
    assert reason == "", "the returned value is unchanged when the check is skipped"
    assert any("staleness check skipped" in rec.getMessage() for rec in caplog.records), \
        "a skipped staleness check must leave a log line saying so"


# -------------------------------- the PERSISTENT mission: a configured ``.sav`` is a real file

class _PatternNode(_Node):
    """A node whose listing HONOURS the patterns it is asked for, like the real one.

    The resolver passes them to ``node.list_directory`` and the node — not the resolver — decides
    which files exist; a stub that ignores the patterns cannot show a missing file kind.
    """

    def __init__(self, base, missions):
        super().__init__(base, missions)
        self.list_calls: list[tuple] = []

    async def list_directory(self, path, pattern=None, traverse=False, ignore=None):
        import fnmatch
        self.list_calls.append((path, pattern, traverse, ignore))
        patterns = [pattern] if isinstance(pattern, str) else list(pattern or [])
        wanted = [m for m in self._missions
                  if not patterns or any(fnmatch.fnmatch(os.path.basename(m), p) for p in patterns)]
        return self._base, wanted

    async def read_file(self, path):
        return b"x"


@pytest.mark.asyncio
async def test_the_resolver_asks_for_persistent_missions_as_well_as_miz(tmp_path):
    """A ``.sav`` mission is a mission: every other listing in the bot asks for ``*.miz`` AND ``*.sav``.

    ``/mission persistence`` renames the file to ``.sav`` and points ``missionList`` at it, so a
    resolver that only lists ``*.miz`` cannot name the file that is right there on disk — the console
    then reports the configured mission as ``missing``.
    """
    folder = tmp_path / "Missions"
    _touch(folder / "C.miz", 1_000)
    _touch(folder / "C.sav", 2_000)
    stub = ResolverStub(folder, [folder / "C.miz", folder / "C.sav"])
    node = _PatternNode(folder, stub.node._missions)
    stub.node = node

    await ServerImpl.getAllMissionFiles(stub)

    _path, pattern, _traverse, _ignore = node.list_calls[-1]
    assert sorted(pattern) == ["*.miz", "*.sav"], \
        f"the resolver listed only {pattern}: a persistent mission cannot be found"


@pytest.mark.asyncio
async def test_a_configured_sav_mission_resolves_instead_of_reading_missing(tmp_path):
    """The end-to-end shape the operator sees: one row, its file present, no ``missing`` chip."""
    folder = tmp_path / "Missions"
    _touch(folder / "Persistent" / "MyMission.sav", 5_000)
    stub = ResolverStub(folder, [folder / "Persistent" / "MyMission.sav"])
    node = _PatternNode(folder, stub.node._missions)
    stub.node = node

    files = await ServerImpl.getAllMissionFiles(stub)
    index = mission_actions._resolved_index(files, str(folder))
    name, path, copy, real = mission_actions._lookup_configured(
        str(folder / "Persistent" / "MyMission.sav"), index, str(folder))

    assert copy == "primary", f"the row reads {copy!r} — the console draws its chip from this"
    assert real is not None, "the resolver found no file for a file that exists"
    assert os.path.normpath(real) == os.path.normpath(str(folder / "Persistent" / "MyMission.sav"))
    assert name == os.path.normpath(os.path.join("Persistent", "MyMission.sav"))
    assert path == os.path.normpath(os.path.join("Persistent", "MyMission.sav"))


class _SavServer(ResolverStub):
    """A server whose ``missionList`` is ``[A.miz, Persistent/MyMission.sav]``, over a real tree.

    ``getAllMissionFiles`` is the REAL ``ServerImpl`` method bound to this stub, so the download goes
    through the same resolver the console uses — not a hand-written answer.
    """

    name = "Special K - Development"

    def __init__(self, base, configured):
        super().__init__(base, configured)
        self._configured = [str(p) for p in configured]
        self.node = _PatternNode(base, self._configured)
        self.getAllMissionFiles = types.MethodType(ServerImpl.getAllMissionFiles, self)

    async def get_missions_dir(self):
        return self.instance.missions_dir

    async def getMissionList(self):
        return list(self._configured)


class _SavContext:
    def __init__(self, server):
        self._server = server

    def resolve_server(self, name):
        return self._server


@pytest.mark.asyncio
async def test_the_download_of_a_persistent_mission_by_its_index_works(tmp_path):
    """The operator's second symptom: mission #2 IS the ``.sav``, and Download answered "not found".

    Same cause as the missing chip — the download resolves the index through the same newest-copy
    resolver, so an unlisted ``.sav`` has no ``real`` path to read and the seam refuses a mission
    that is configured, present and two rows down the list.
    """
    folder = tmp_path / "Missions"
    _touch(folder / "A.miz", 1_000)
    _touch(folder / "Persistent" / "MyMission.sav", 5_000)
    server = _SavServer(folder, [folder / "A.miz", folder / "Persistent" / "MyMission.sav"])

    result = await mission_actions.download_mission(_SavContext(server), server_name=server.name,
                                                    mission=2)

    assert result.success, result.message
    assert result.content == b"x"
    assert result.filename == "MyMission.sav"


