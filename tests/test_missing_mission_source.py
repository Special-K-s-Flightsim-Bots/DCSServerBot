"""A launch must not die when the mission file it was told to load is gone.

The failure this pins: ``ServerImpl._get_current_mission_file`` handed back the mission DCS last
reported (``current_mission.filename``) WITHOUT checking that it still exists, while its
mission-list branch does check. A mission renamed, replaced or deleted while the server was down (a
crash or a forced shutdown keeps ``current_mission`` alive) therefore reached
``utils.get_orig_file`` -> ``shutil.copy2`` -> an opaque ``WinError 2 / FileNotFoundError``, and
``apply_mission_changes`` re-tried the same copy in its except-fallback, so the whole launch failed
instead of starting a mission that IS on disk.

The pins are the real shipped bodies over stub ``self`` objects and a real temporary tree.
"""
from __future__ import annotations

import logging
import os
import types

import pytest

from core.data.impl.serverimpl import ServerImpl
from core.utils.dcs import get_orig_file


class _Log:
    def __init__(self):
        self.records: list[tuple[str, str]] = []

    def warning(self, msg, *a, **kw):
        self.records.append(("warning", str(msg)))

    def info(self, msg, *a, **kw):
        self.records.append(("info", str(msg)))

    def exception(self, msg, *a, **kw):
        self.records.append(("exception", str(msg)))

    def debug(self, msg, *a, **kw):
        pass

    def error(self, msg, *a, **kw):
        self.records.append(("error", str(msg)))

    def warning_msgs(self) -> list[str]:
        return [m for lvl, m in self.records if lvl == "warning"]


class _ServerStub:
    """The minimum a ``ServerImpl`` method needs: settings, the extension map, a log."""

    def __init__(self, mission_list, current=None, current_index=1):
        self._settings = {"missionList": list(mission_list),
                          "listStartIndex": current_index, "current": current_index}
        self._log = _Log()
        self.locals = {}
        self.extensions = {}
        self.current_mission = (types.SimpleNamespace(filename=current, name="m")
                                if current else None)
        self.get_current_mission_file = self._current
        self.name = "stub"

    async def _current(self):
        return ServerImpl._get_current_mission_file(self)

    @property
    def settings(self):
        return self._settings

    @property
    def log(self):
        return self._log


# ------------------------------------------------------------------ get_orig_file

def test_get_orig_file_names_a_missing_source(tmp_path):
    missing = tmp_path / "Gone.miz"
    with pytest.raises(FileNotFoundError) as exc:
        get_orig_file(str(missing))
    assert str(missing) in str(exc.value)
    assert not (tmp_path / ".dcssb").exists(), "a missing source must not leave a copy dir behind"


def test_get_orig_file_relative_name_does_not_pollute_cwd(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    with pytest.raises(FileNotFoundError):
        get_orig_file("Foothold.miz")
    assert not (work / ".dcssb").exists(), \
        "a relative mission name must not create a .dcssb dir in the bot's own working directory"


def test_get_orig_file_control_makes_the_backup(tmp_path):
    mission = tmp_path / "Real.miz"
    mission.write_bytes(b"miz")
    orig = get_orig_file(str(mission))
    assert orig == str(tmp_path / ".dcssb" / "Real.miz.orig")
    assert os.path.exists(orig)


# --------------------------------------------------- _get_current_mission_file

def test_current_mission_is_resolved_from_the_list_when_it_is_gone(tmp_path):
    other = tmp_path / "Next.miz"
    other.write_bytes(b"miz")
    stub = _ServerStub([str(other)], current=str(tmp_path / "Gone.miz"))
    assert ServerImpl._get_current_mission_file(stub) == os.path.normpath(str(other))
    assert any("Gone.miz" in m for m in stub._log.warning_msgs())


def test_current_mission_wins_while_it_exists(tmp_path):
    current = tmp_path / "Live.miz"
    current.write_bytes(b"miz")
    other = tmp_path / "Next.miz"
    other.write_bytes(b"miz")
    stub = _ServerStub([str(other)], current=str(current))
    assert ServerImpl._get_current_mission_file(stub) == os.path.normpath(str(current))


def test_current_mission_gone_with_no_usable_list_entry_is_none(tmp_path):
    stub = _ServerStub([str(tmp_path / "AlsoGone.miz")], current=str(tmp_path / "Gone.miz"))
    assert ServerImpl._get_current_mission_file(stub) is None


# ------------------------------------------------------- apply_mission_changes

@pytest.mark.asyncio
async def test_apply_mission_changes_skips_the_rewrite_for_a_missing_mission(tmp_path):
    other = tmp_path / "Next.miz"
    other.write_bytes(b"miz")
    stub = _ServerStub([str(other)], current=str(tmp_path / "Gone.miz"))
    result = await ServerImpl.apply_mission_changes(stub, use_orig=True)
    assert result == os.path.normpath(str(other)), "the launch must continue with a mission on disk"
    assert stub._log.records, "the skipped rewrite must be reported"
