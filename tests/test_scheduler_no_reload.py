"""A scheduled ``load`` under ``no_reload`` never reloads the mission it is already running.

The pick-less path passes ``no_reload`` through to ``Server.loadMission``, which skips a load of the
current mission. The preset path builds its own call, so it has to honour the flag itself — before
resolving the pick, because resolving it rewrites the mission file.

Pins:
* ``load`` + ``no_reload`` + running mission → no preset is resolved, nothing is written or loaded,
  and ``restart_pending`` is cleared so the scheduler keeps running;
* the same action on a DIFFERENT mission still loads it with its pick;
* ``restart`` is untouched: ``no_reload`` is a load-only option;
* the pick-less path still forwards ``no_reload`` to the server;
* a skipped load states nothing in the audit: no "loaded mission" line is written.
"""
from __future__ import annotations

import os

import asyncio
import logging
import types

import pytest

from core import Status
import plugins.scheduler.commands as sched_cmds
from core.data.impl import serverimpl
from core.data.impl.serverimpl import ServerImpl
from plugins.scheduler.commands import Scheduler

#: host-spelled: the bot joins and normalises these, so a raw POSIX root never matches on Windows
MISSIONS = os.path.abspath("/missions")
MISSION = os.path.join(MISSIONS, "Training.miz")
OTHER_MISSION = os.path.join(MISSIONS, "Night.miz")
PRESET = {"modify": {"weather": {"clouds": {"base": 1000}}}}


class FakeServer:
    """Only what the two run paths touch, recording every call in order."""

    def __init__(self, mission: str = MISSION):
        self.status = Status.RUNNING
        self.name = "SRS-1"
        self.locals = {"mission_rewrite": True}
        self.current_mission = types.SimpleNamespace(filename=mission, display_name="Training")
        self.restart_pending = None
        self.calls: list[tuple[str, dict]] = []

    async def getMissionList(self):
        return [MISSION, OTHER_MISSION]

    async def modifyMission(self, filename, preset, use_orig=True):
        self.calls.append(("modifyMission", {"filename": filename, "presets": preset}))
        return filename

    async def replaceMission(self, idx, path):
        self.calls.append(("replaceMission", {"index": idx, "path": path}))
        return [MISSION, OTHER_MISSION]

    async def stop(self):
        self.calls.append(("stop", {}))

    async def start(self, **kwargs):
        self.calls.append(("start", kwargs))

    async def startup(self, *args, **kwargs):
        self.calls.append(("startup", kwargs))

    async def loadMission(self, mission, modify_mission=True, use_orig=True,
                          no_reload=False, **kwargs):
        # Server.loadMission's own rule: a no_reload load of the current mission returns None
        target = [MISSION, OTHER_MISSION][mission - 1] if isinstance(mission, int) else mission
        rc = None if (no_reload and target == self.current_mission.filename) else True
        self.calls.append(("loadMission", {"mission": mission, "modify_mission": modify_mission,
                                           "use_orig": use_orig, "no_reload": no_reload,
                                           "rc": rc, **kwargs}))
        return rc


class FakeNode:
    def __init__(self):
        self.config_dir = os.path.abspath("/config")
        self.name = "dcs-a-01"
        self.locals = {}
        self.master = True

    async def list_files(self, *_args, **_kwargs):
        return []

    async def read_file(self, _path):
        return b""


class FakeBot:
    def __init__(self):
        self.audits: list[str] = []

    async def audit(self, message, **kwargs):
        self.audits.append(message)
        return True


def build(monkeypatch):
    """A Scheduler double whose preset lookup answers with PRESET, no file read involved."""
    monkeypatch.setattr(sched_cmds.utils, "get_preset", lambda *_a, **_k: PRESET)
    node = FakeNode()
    cog = types.SimpleNamespace(node=node, bot=FakeBot(),
                                log=logging.getLogger("test.scheduler.no_reload"),
                                config_dir=node.config_dir)
    cog.__cog_name__ = "Scheduler"
    cog._preset_file_text = lambda *_a, **_k: "presets.yaml"
    for name in ("_run_with_presets", "_run_without_presets", "_announce_refused_pick",
                 "run_action"):
        setattr(cog, name, types.MethodType(getattr(Scheduler, name), cog))
    return cog


def load_conf(**extra):
    conf = {"method": "load", "mission_id": 1, "settings": ["Configured"],
            "presets": "/config/presets.yaml", "use_orig": False}
    conf.update(extra)
    return conf


@pytest.mark.asyncio
async def test_a_no_reload_load_of_the_running_mission_does_nothing(monkeypatch):
    cog, server = build(monkeypatch), FakeServer()

    await Scheduler._run_with_presets(cog, server, load_conf(no_reload=True), 1, True)  # type: ignore[arg-type]

    assert server.calls == [], "the pick was resolved, written or loaded anyway"
    assert server.restart_pending is False


@pytest.mark.asyncio
async def test_the_same_load_on_another_mission_still_applies_its_pick(monkeypatch):
    server = FakeServer()

    await Scheduler._run_with_presets(
        build(monkeypatch), server, load_conf(no_reload=True), 2, False)  # type: ignore[arg-type]

    assert [name for name, _ in server.calls] == ["modifyMission", "loadMission"]
    loaded = server.calls[-1][1]
    assert loaded["no_reload"] is True
    assert loaded["preset"] == ["Configured"]


@pytest.mark.asyncio
async def test_restart_ignores_no_reload(monkeypatch):
    """``no_reload`` is a load-only option: a restart of the running mission still restarts."""
    server = FakeServer()

    await Scheduler._run_with_presets(
        build(monkeypatch), server, load_conf(method="restart"), 1, True)  # type: ignore[arg-type]

    assert [name for name, _ in server.calls] == ["modifyMission", "loadMission"]


@pytest.mark.asyncio
async def test_the_pick_less_path_still_forwards_no_reload(monkeypatch):
    cog, server = build(monkeypatch), FakeServer()

    await Scheduler._run_without_presets(  # type: ignore[arg-type]
        cog, server, {"method": "load", "mission_id": 1, "no_reload": True}, 1)

    loaded = server.calls[-1][1]
    assert server.calls[-1][0] == "loadMission"
    assert loaded["no_reload"] is True


# ---------------------------------------- the caller: run_action decides which mission is "running"

@pytest.mark.asyncio
async def test_run_action_skips_a_no_reload_load_of_the_running_mission(monkeypatch):
    """The entry point the scheduler's idle trigger reaches: nothing is written, nothing loads."""
    cog, server = build(monkeypatch), FakeServer()

    await Scheduler.run_action(cog, server, load_conf(no_reload=True))  # type: ignore[arg-type]

    assert server.calls == []
    assert server.restart_pending is False


@pytest.mark.asyncio
async def test_run_action_still_loads_the_next_mission_under_no_reload(monkeypatch):
    cog, server = build(monkeypatch), FakeServer()

    await Scheduler.run_action(cog, server, load_conf(no_reload=True, mission_id=2))  # type: ignore[arg-type]

    assert [name for name, _ in server.calls] == ["modifyMission", "loadMission"]
    assert server.calls[-1][1]["mission"] == OTHER_MISSION


# ---------------------------------------------------- what the audit channel is told

@pytest.mark.asyncio
async def test_run_action_audits_nothing_when_the_load_was_skipped(monkeypatch):
    """A skip is not a load: the audit channel must not read "loaded mission"."""
    cog, server = build(monkeypatch), FakeServer()

    await Scheduler.run_action(
        cog, server, {"method": "load", "mission_id": 1, "no_reload": True})  # type: ignore[arg-type]

    assert server.calls and server.calls[-1][1]["rc"] is None
    assert cog.bot.audits == []


@pytest.mark.asyncio
async def test_run_action_audits_the_mission_it_really_loaded(monkeypatch):
    cog, server = build(monkeypatch), FakeServer()

    await Scheduler.run_action(
        cog, server, {"method": "load", "mission_id": 2, "no_reload": True})  # type: ignore[arg-type]

    assert cog.bot.audits and "loaded mission" in cog.bot.audits[-1]


# ------------------------------- the pre-launch pointer: LOADING with no DCS process yet

class PreLaunchServer(FakeServer):
    """The shape ``run_action`` leaves a scheduled start in: marked LOADING, DCS not launched.

    ``setStartIndex`` is the SHIPPED writer, over the real ``ServerImpl.is_running`` with no process:
    the window the scheduler itself creates before it reaches ``startup()``.
    """

    def __init__(self):
        super().__init__()
        self.status = Status.LOADING
        self.settings = {"missionList": [MISSION, OTHER_MISSION], "listStartIndex": 1, "current": 1}
        self.process = None
        self._lock = asyncio.Lock()
        self.instance = types.SimpleNamespace(name="SRS-1", missions_dir=MISSIONS)

    async def getStartIndex(self):
        return self.settings["listStartIndex"]

    async def setStartIndex(self, mission_id):
        return await ServerImpl.setStartIndex(self, mission_id)

    async def _mission_write_refused(self):
        return await ServerImpl._mission_write_refused(self)

    async def is_running(self):
        return await ServerImpl.is_running(self)


@pytest.mark.asyncio
async def test_a_scheduled_start_points_the_rotation_before_dcs_is_launched(monkeypatch):
    """LOADING with no process is the scheduler's own window: the pointer write must land."""
    monkeypatch.setattr(serverimpl.utils, "find_process", lambda *args, **kwargs: iter(()))
    cog, server = build(monkeypatch), PreLaunchServer()

    await Scheduler.run_action(cog, server, {"method": "rotate"})  # type: ignore[arg-type]

    assert server.settings["listStartIndex"] == 2, "the pointer names the mission to start"
    assert server.calls and server.calls[0][0] == "startup", "the launch was not swallowed"
