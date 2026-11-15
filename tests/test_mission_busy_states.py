"""The mission-list writers refuse the two states in which DCS writes the settings file itself.

``serverSettings.lua`` is DCS's own while a server loads and while it shuts down: ``SettingsDict``
re-reads only on an mtime change, then rewrites the WHOLE file from a cache that may predate DCS's
write, so a bot write made in either window mangles or reverts the file. Every list writer therefore
refuses those two states **while their DCS process is up**, with ONE sentence, and every other shape
keeps behaving exactly as it did — including the ``LOADING`` window a scheduler marks BEFORE it
launches DCS, where the file is not DCS's yet and the pointer must still be written.

The shipped method bodies are driven UNBOUND over a stub ``self`` — no pool, no DCS — so a refusal
is proved by the settings dict recording NO assignment, and the ordinary path is proved by the same
stub recording one. ``is_running`` is the SHIPPED method over a process double, so the process
question is answered by the code that ships, never by a lambda standing in for it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import types
from typing import Any, cast

import pytest


from core import Status, UploadStatus
from core.data.impl import serverimpl
from core.data.impl.serverimpl import (
    MISSION_LIST_BUSY_REFUSAL,
    MISSION_LIST_BUSY_STATES,
    MissionFileSystemEventHandler,
    ServerImpl,
)

BUSY_STATES = (Status.LOADING, Status.SHUTTING_DOWN)
DCS_STATES = (Status.STOPPED, Status.PAUSED, Status.RUNNING)
FILE_STATES = (Status.SHUTDOWN, Status.UNREGISTERED)


@pytest.fixture(autouse=True)
def no_process_scan(monkeypatch):
    """A scan in this module finds NO DCS process: ``is_running`` never looks at the real box."""
    monkeypatch.setattr(serverimpl.utils, "find_process", lambda *args, **kwargs: iter(()))


# ------------------------------------------------------------------------------------ the stub self

#: ONE host spelling of the fake mission tree: ``"/missions"`` is absolute on POSIX but not on
#: Windows, where the bot's own ``abspath`` prepends the CWD's drive and a raw POSIX expectation then
#: names a file the double was never asked for.
MISSIONS = os.path.abspath("/missions")


def m(*parts: str) -> str:
    """*parts* joined below :data:`MISSIONS`, normalised the way the bot normalises a path."""
    return os.path.normpath(os.path.join(MISSIONS, *parts))


class ProcessDouble:
    """The slice of the DCS process handle ``ServerImpl.is_running`` asks."""

    def __init__(self, alive: bool = True):
        self.alive = alive

    def is_running(self) -> bool:
        return self.alive


class RecordingSettings(dict):
    """A settings dict that records every assignment, so "nothing was written" is checkable."""

    def __init__(self, **entries):
        super().__init__(**entries)
        self.writes: list[tuple[str, Any]] = []

    def __setitem__(self, key, value):
        self.writes.append((key, value))
        super().__setitem__(key, value)


class Node:
    """The node slice ``uploadMission`` touches: the writer and its typed answer."""

    def __init__(self, rc=UploadStatus.OK):
        self.rc = rc
        self.writes: list[tuple[str, str, bool]] = []

    async def write_file(self, filename, url, force=False):
        self.writes.append((filename, url, force))
        return self.rc


def stub(status, missions=("A.miz", "B.miz", "C.miz"), start=1, current=None, *, mission_id=None,
         running=True):
    """The slice of ``ServerImpl`` the five writers and ``uploadMission`` read.

    *running* is the DCS process: ``False`` leaves ``process`` unset, the shape a scheduler marks
    LOADING in before it launches DCS.
    """
    settings = RecordingSettings(missionList=list(missions), listStartIndex=start, current=start)
    server = types.SimpleNamespace(
        name="test",
        status=status,
        settings=settings,
        log=logging.getLogger("test.mission.busy"),
        node=Node(),
        locals={},
        extensions={},
        mission_id=mission_id,
        current_mission=types.SimpleNamespace(filename=current) if current else None,
        instance=types.SimpleNamespace(missions_dir=MISSIONS, name="test"),
        process=ProcessDouble() if running else None,
        _lock=asyncio.Lock(),
        sync=[],
        dcs=[],
        added=[],
    )

    async def get_mission_list():
        return list(settings["missionList"])

    async def send_to_dcs(message):
        server.dcs.append(message)

    async def send_to_dcs_sync(message):
        server.sync.append(message)
        return {"missionList": [os.path.normpath(x) for x in settings["missionList"]]}

    async def add_mission(path, *, idx=-1, autostart=False):
        server.added.append((path, idx, autostart))
        settings["missionList"] = list(settings["missionList"]) + [path]
        return settings["missionList"]

    server.getMissionList = get_mission_list
    server._get_current_mission_file = (lambda: os.path.normpath(current)) if current else (lambda: None)
    server.send_to_dcs = send_to_dcs
    server.send_to_dcs_sync = send_to_dcs_sync
    server.addMission = add_mission
    server.is_running = lambda: ServerImpl.is_running(server)
    server._mission_write_refused = lambda: ServerImpl._mission_write_refused(server)
    server.setStartIndex = lambda index: ServerImpl.setStartIndex(server, index)
    server.replaceMission = lambda index, path: ServerImpl.replaceMission(server, index, path)
    return server


#: The five list writers, each with the arguments that reach its FILE branch on a default stub.
WRITERS = {
    "addMission": lambda server: ServerImpl.addMission(server, "D.miz"),
    "deleteMission": lambda server: ServerImpl.deleteMission(server, 2),
    "replaceMission": lambda server: ServerImpl.replaceMission(server, 1, "D.miz"),
    "setStartIndex": lambda server: ServerImpl.setStartIndex(server, 2),
    "setMissionList": lambda server: ServerImpl.setMissionList(server, ["A.miz", "C.miz"]),
}

#: What each writer above leaves in ``missionList`` once it is allowed to write.
WRITTEN_LIST = {
    "addMission": ["A.miz", "B.miz", "C.miz", "D.miz"],
    "deleteMission": ["A.miz", "C.miz"],
    "replaceMission": ["D.miz", "B.miz", "C.miz"],
    "setStartIndex": ["A.miz", "B.miz", "C.miz"],
    "setMissionList": ["A.miz", "C.miz"],
}


# ------------------------------------------------------------------- 1. RED: the two busy states

@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY_STATES, ids=lambda s: s.name)
@pytest.mark.parametrize("writer", sorted(WRITERS))
async def test_a_busy_state_is_refused_with_the_one_sentence_and_writes_nothing(writer, status):
    server = stub(status)
    with pytest.raises(RuntimeError) as caught:
        await WRITERS[writer](server)

    assert str(caught.value) == MISSION_LIST_BUSY_REFUSAL
    assert server.settings.writes == [], f"{writer} wrote the mission list in {status.name}"
    assert server.settings["missionList"] == ["A.miz", "B.miz", "C.miz"], "the list is untouched"
    assert server.sync == [] and server.dcs == [], "no DCS round-trip is attempted either"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY_STATES, ids=lambda s: s.name)
@pytest.mark.parametrize("autostart", [True, False], ids=["autostart", "plain"])
async def test_adding_a_mission_already_in_the_list_is_refused_in_a_busy_state(status, autostart):
    """The already-in-list branch moves the pointer — the top guard must stop it before it reads."""
    server = stub(status)
    with pytest.raises(RuntimeError) as caught:
        await ServerImpl.addMission(server, "A.miz", autostart=autostart)

    assert str(caught.value) == MISSION_LIST_BUSY_REFUSAL
    assert server.settings.writes == [], "the pointer was written in a busy state"
    assert server.settings["missionList"] == ["A.miz", "B.miz", "C.miz"]
    assert server.sync == [] and server.dcs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY_STATES, ids=lambda s: s.name)
async def test_replacing_a_copy_already_in_the_list_is_refused_in_a_busy_state(status):
    """The ``mission in missions`` branch delegates to ``replaceMission``; the guard beats it there."""
    server = stub(status, missions=(".dcssb/A.miz", "B.miz"))
    with pytest.raises(RuntimeError) as caught:
        await ServerImpl.addMission(server, "A.miz", autostart=True)

    assert str(caught.value) == MISSION_LIST_BUSY_REFUSAL
    assert server.settings.writes == []


@pytest.mark.asyncio
async def test_adding_a_mission_already_in_the_list_still_moves_the_pointer_in_shutdown():
    server = stub(Status.SHUTDOWN, start=2)
    assert await ServerImpl.addMission(server, "A.miz", autostart=True) == \
        ["A.miz", "B.miz", "C.miz"]

    assert server.settings.writes == [("listStartIndex", 1)]
    assert server.sync == []


@pytest.mark.asyncio
async def test_adding_a_mission_already_in_the_list_keeps_its_pointer_write_while_running():
    """The already-in-list branch writes the pointer directly — it never takes the DCS route."""
    server = stub(Status.RUNNING, start=2)
    assert await ServerImpl.addMission(server, "A.miz", autostart=True) == \
        ["A.miz", "B.miz", "C.miz"]

    assert server.settings.writes == [("listStartIndex", 1)]
    assert server.sync == [] and server.dcs == [], "no DCS round-trip on this branch"


@pytest.mark.asyncio
async def test_adding_an_unlisted_mission_still_takes_the_dcs_route_while_running():
    server = stub(Status.RUNNING)
    assert await ServerImpl.addMission(server, "D.miz", autostart=True) == \
        ["A.miz", "B.miz", "C.miz"]

    assert len(server.sync) == 1
    assert server.sync[0]["command"] == "addMission"


@pytest.mark.asyncio
async def test_adding_an_already_listed_mission_unticked_writes_nothing_in_shutdown():
    server = stub(Status.SHUTDOWN)
    assert await ServerImpl.addMission(server, "A.miz") == ["A.miz", "B.miz", "C.miz"]

    assert server.settings.writes == [], "an unticked already-listed add writes nothing"


def test_the_two_busy_states_are_the_only_ones_named():
    assert set(MISSION_LIST_BUSY_STATES) == {Status.LOADING, Status.SHUTTING_DOWN}


# ------------------------------- 1b. the state is only half the question: the process is the other

@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY_STATES, ids=lambda s: s.name)
@pytest.mark.parametrize("writer", sorted(WRITERS))
async def test_a_busy_state_with_no_process_still_writes(writer, status):
    """A scheduler marks a server LOADING *before* it launches DCS; the file is not DCS's yet."""
    server = stub(status, running=False)

    await WRITERS[writer](server)

    assert server.settings.writes, f"{writer} must write while no DCS process is up in {status.name}"
    assert server.settings["missionList"] == WRITTEN_LIST[writer]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY_STATES, ids=lambda s: s.name)
@pytest.mark.parametrize("autostart", [True, False], ids=["autostart", "plain"])
async def test_adding_an_already_listed_mission_with_no_process_moves_nothing_unless_ticked(
        status, autostart):
    server = stub(status, start=2, running=False)

    assert await ServerImpl.addMission(server, "A.miz", autostart=autostart) == \
        ["A.miz", "B.miz", "C.miz"]

    assert server.settings.writes == ([("listStartIndex", 1)] if autostart else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY_STATES, ids=lambda s: s.name)
async def test_an_upload_with_no_process_uploads_and_adds_the_mission(status):
    server = stub(status, running=False)

    rc = await ServerImpl.uploadMission(server, "D.miz", "http://example/D.miz")

    assert rc == UploadStatus.OK
    assert [path for path, _, _ in server.node.writes] == [
        m("D.miz")]
    assert server.added == [(m("D.miz"), -1, False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY_STATES, ids=lambda s: s.name)
@pytest.mark.parametrize("running,refused", [(True, True), (False, False)],
                         ids=["process-up", "process-gone"])
async def test_the_pointer_is_written_exactly_when_the_process_is_not_up(status, running, refused):
    """The four combinations of (LOADING|SHUTTING_DOWN) x (process up | gone), on the one writer
    the scheduler calls."""
    server = stub(status, running=running)

    if refused:
        with pytest.raises(RuntimeError) as caught:
            await ServerImpl.setStartIndex(server, 2)
        assert str(caught.value) == MISSION_LIST_BUSY_REFUSAL
        assert server.settings.writes == []
        return

    await ServerImpl.setStartIndex(server, 2)

    assert server.settings.writes == [("listStartIndex", 2)]
    assert server.dcs == [], "the process is gone, so no DCS round-trip is attempted"


@pytest.mark.asyncio
async def test_the_predicate_is_the_state_and_the_process_too():
    """The predicate is false outside the two states, false with the process gone, true with it up."""
    for busy in BUSY_STATES:
        assert await ServerImpl._mission_write_refused(stub(busy, running=True)) is True
        assert await ServerImpl._mission_write_refused(stub(busy, running=False)) is False
    for other in DCS_STATES + FILE_STATES:
        assert await ServerImpl._mission_write_refused(stub(other, running=True)) is False


# ---------------------------------------------------- 2. CONTROL: the ordinary path is untouched

@pytest.mark.asyncio
@pytest.mark.parametrize("status", DCS_STATES, ids=lambda s: s.name)
@pytest.mark.parametrize("writer", ["addMission", "deleteMission", "replaceMission"])
async def test_a_process_up_state_still_routes_to_dcs(writer, status):
    """The DCS-routed path is not gated: the round-trip happens and the reply lands in the list."""
    server = stub(status)
    await WRITERS[writer](server)

    assert len(server.sync) == 1, f"{writer} must take the DCS route in {status.name}"
    assert server.settings["missionList"] == ["A.miz", "B.miz", "C.miz"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", DCS_STATES, ids=lambda s: s.name)
async def test_set_start_index_still_tells_dcs_and_writes_the_pointer(status):
    server = stub(status)
    await ServerImpl.setStartIndex(server, 2)

    assert server.dcs == [{"command": "setStartIndex", "id": 2}]
    assert server.settings["listStartIndex"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", FILE_STATES, ids=lambda s: s.name)
@pytest.mark.parametrize("writer", ["addMission", "deleteMission", "replaceMission"])
async def test_a_file_state_still_writes_the_list(writer, status):
    """``SHUTDOWN`` / ``UNREGISTERED`` are the states the FILE is authoritative in — they keep writing."""
    server = stub(status)
    await WRITERS[writer](server)

    assert server.sync == [], "no DCS round-trip in a state where the process is down"
    assert server.settings.writes, f"{writer} must still write the list in {status.name}"
    assert ("missionList", server.settings["missionList"]) == server.settings.writes[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", FILE_STATES, ids=lambda s: s.name)
async def test_set_start_index_still_writes_the_pointer_and_tells_nobody(status):
    server = stub(status)
    await ServerImpl.setStartIndex(server, 3)

    assert server.dcs == []
    assert server.settings.writes == [("listStartIndex", 3)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", FILE_STATES, ids=lambda s: s.name)
async def test_the_full_list_writer_still_accepts_a_permutation(status):
    server = stub(status)
    assert await ServerImpl.setMissionList(server, ["C.miz", "A.miz", "B.miz"]) == \
        ["C.miz", "A.miz", "B.miz"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", DCS_STATES, ids=lambda s: s.name)
async def test_the_full_list_writer_keeps_its_own_sentence_while_the_process_is_up(status):
    """``{STOPPED, PAUSED, RUNNING}`` keep the older sentence: that refusal is about the PROCESS."""
    server = stub(status)
    with pytest.raises(RuntimeError) as caught:
        await ServerImpl.setMissionList(server, ["A.miz"])
    assert str(caught.value) == ("The mission list can only be changed while the server is not "
                                 "running.")
    assert server.settings.writes == []


# ------------------------------------------------------------------- 3. uploadMission: a typed stop

@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY_STATES, ids=lambda s: s.name)
async def test_an_upload_in_a_busy_state_is_refused_before_anything_is_written(status):
    server = stub(status)
    rc = await ServerImpl.uploadMission(server, "D.miz", "http://example/D.miz")

    assert rc == UploadStatus.BUSY
    assert server.node.writes == [], "the node writer is never reached"
    assert server.added == [], "``addMission`` is never reached"
    assert server.settings.writes == []


@pytest.mark.asyncio
async def test_an_upload_in_shutdown_still_uploads_and_adds_the_mission():
    server = stub(Status.SHUTDOWN)
    rc = await ServerImpl.uploadMission(server, "D.miz", "http://example/D.miz")

    assert rc == UploadStatus.OK
    assert [path for path, _, _ in server.node.writes] == [m("D.miz")]
    assert server.added == [(m("D.miz"), -1, False)]


# -------------------------------------------------- 4. autoscan: a refusal never leaves the callback

def _handler():
    server = types.SimpleNamespace(name="test", settings={"missionList": []})
    handler = cast(MissionFileSystemEventHandler, types.SimpleNamespace(
        server=server,
        log=logging.getLogger("test.mission.busy.autoscan"),
    ))
    return handler, server


@pytest.mark.asyncio
async def test_a_refused_autoscan_write_is_logged_and_not_raised(caplog):
    """A raise inside a watchdog callback kills the subscription; a refusal is only a log line."""
    handler, _ = _handler()
    written = []

    async def refused():
        written.append("called")
        raise RuntimeError(MISSION_LIST_BUSY_REFUSAL)

    with caplog.at_level(logging.INFO, logger="test.mission.busy.autoscan"):
        await MissionFileSystemEventHandler._apply(handler, refused(), what="add", path=m("D.miz"))

    assert written == ["called"], "the write really was attempted"
    assert any(MISSION_LIST_BUSY_REFUSAL in record.getMessage() for record in caplog.records)
    assert any(m("D.miz") in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_a_successful_autoscan_write_logs_nothing(caplog):
    handler, _ = _handler()
    written = []

    async def accepted():
        written.append("called")

    with caplog.at_level(logging.INFO, logger="test.mission.busy.autoscan"):
        await MissionFileSystemEventHandler._apply(handler, accepted(), what="add", path=m("D.miz"))

    assert written == ["called"]
    assert caplog.records == []
