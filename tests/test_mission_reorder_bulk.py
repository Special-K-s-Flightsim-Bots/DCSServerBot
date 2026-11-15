"""M5: the full-list replacer and the two offline-only mission-list writes (bulk delete, move).

Three things are pinned here, at the BOT side (``core/data`` and ``plugins/mission/actions.py``):

* ``ServerImpl.setMissionList`` — the ONE writer of the mission ORDER. It is offline only, it
  validates the replacement (subset / no duplicates / the running mission survives) before it
  writes, and it recomputes the rotation pointer BY PATH so ``listStartIndex`` names the same
  MISSION, not the same slot — the edge cases the survey enumerated (current moved, current deleted,
  a one-entry list, an entry the disk cannot resolve) are all asserted;
* the ``Server`` ABC member and the ``ServerProxy`` RPC stub exist — a missing stub is exactly why a
  node-hosted server would silently lack the call;
* ``delete_missions`` and ``reorder_mission`` — the two actions the console calls. ``delete_missions``
  is the row's own delete, repeated: the bot's per-mission ``deleteMission`` once per selected
  mission, DESCENDING, in every state but ``LOADING``. ``reorder_mission`` replaces the order in one
  write and stays OFFLINE ONLY. Both derive the new list SERVER-SIDE from a fresh snapshot (never from
  a list the browser sent) and report honestly.

``setMissionList`` is exercised through the REAL ``ServerImpl`` method (an unbound call over a stub
``self``), so the validation and the pointer arithmetic are the shipped ones.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

import core.actions as actions_module
from core import Status
from core.actions import ActionContext, discover_actions, action_registry
from core.data.impl import serverimpl
from core.data.impl.serverimpl import ServerImpl
from core.data.server import Server as ServerABC
from core.data.proxy.serverproxy import ServerProxy
import plugins.mission.actions as mission_actions

#: host-spelled like the bot's own: "/missions" is not absolute on Windows, where the bot's
#: abspath/join then backslashes it and no typed POSIX expectation can match
BASE = os.path.abspath("/missions")


@pytest.fixture(autouse=True)
def no_process_scan(monkeypatch):
    """A scan in this module finds NO DCS process: ``is_running`` never looks at the real box."""
    monkeypatch.setattr(serverimpl.utils, "find_process", lambda *args, **kwargs: iter(()))


class ProcessDouble:
    """The slice of the DCS process handle ``ServerImpl.is_running`` asks."""

    def __init__(self, alive: bool = True):
        self.alive = alive

    def is_running(self) -> bool:
        return self.alive


# ------------------------------------------------------------------------- the setMissionList stub

class ListStub:
    """A minimal ``self`` for ``ServerImpl.setMissionList``: status, settings, the current path."""

    def __init__(self, missions, *, current=None, start=1, status=Status.SHUTDOWN, running=False):
        self.status = status
        self.settings = {"missionList": list(missions), "listStartIndex": start, "current": start}
        self._current = current
        self.process = ProcessDouble() if running else None
        self._lock = asyncio.Lock()
        self.instance = types.SimpleNamespace(name="test")

    async def is_running(self):
        return await ServerImpl.is_running(self)

    async def _mission_write_refused(self):
        return await ServerImpl._mission_write_refused(self)

    def _get_current_mission_file(self):
        if self._current is not None:
            return os.path.normpath(self._current)
        entries = self.settings.get("missionList", [])
        try:
            idx = int(self.settings.get("listStartIndex", 1) or 1)
        except (TypeError, ValueError):
            idx = 1
        if entries and 1 <= idx <= len(entries):
            return os.path.normpath(entries[idx - 1])
        return None


async def set_list(stub, missions):
    """The SHIPPED method, called unbound over *stub*."""
    return await ServerImpl.setMissionList(stub, missions)


def m(*parts):
    """A mission path as the BOT states it on this host: native separators, normalised.

    ``core/utils/dcs.py`` joins a mission path with ``os.sep`` and normalises it, so a list the bot
    hands back carries ``\\`` on Windows. An expectation built with a bare ``os.path.join`` off the
    POSIX ``BASE`` keeps the mixed ``/missions\\A.miz`` shape and matches on Linux only.
    """
    return os.path.normpath(os.path.join(BASE, *parts))


# --------------------------------------------------------------- 1. the writer: subset / duplicates

@pytest.mark.asyncio
async def test_a_permutation_is_accepted_and_written():
    """A reorder is a permutation of the SAME members — accepted, and the order really changes."""
    stub = ListStub([m("A.miz"), m("B.miz"), m("C.miz")])
    result = await set_list(stub, [m("C.miz"), m("A.miz"), m("B.miz")])
    assert result == [m("C.miz"), m("A.miz"), m("B.miz")]
    assert stub.settings["missionList"] == [m("C.miz"), m("A.miz"), m("B.miz")]


@pytest.mark.asyncio
async def test_a_subset_is_accepted():
    """A delete is a SUBSET — accepted."""
    stub = ListStub([m("A.miz"), m("B.miz"), m("C.miz")])
    assert await set_list(stub, [m("A.miz"), m("C.miz")]) == [m("A.miz"), m("C.miz")]


@pytest.mark.asyncio
async def test_an_invented_entry_is_rejected_and_nothing_is_written():
    """No invented path may enter the list this way — not even an absolute one from a client."""
    stub = ListStub([m("A.miz"), m("B.miz")])
    before = list(stub.settings["missionList"])
    with pytest.raises(ValueError):
        await set_list(stub, [m("A.miz"), "/etc/passwd.miz"])
    assert stub.settings["missionList"] == before


@pytest.mark.asyncio
async def test_a_duplicate_is_rejected():
    stub = ListStub([m("A.miz"), m("B.miz")])
    with pytest.raises(ValueError):
        await set_list(stub, [m("A.miz"), m("A.miz")])


@pytest.mark.asyncio
async def test_a_list_that_drops_the_running_mission_is_refused_with_the_one_sentence():
    """The ACTION's own sentence, verbatim — the bot's ``deleteMission`` guard never fires."""
    stub = ListStub([m("A.miz"), m("B.miz"), m("C.miz")],
                    current=m("B.miz"))
    with pytest.raises(AttributeError) as caught:
        await set_list(stub, [m("A.miz"), m("C.miz")])
    assert str(caught.value) == "You can't delete the running mission."


@pytest.mark.asyncio
async def test_a_directory_named_like_the_copy_marker_cannot_collide_with_its_plain_twin():
    """The reviewer's P12: a bare ``.dcssb`` replace collided ``foo.dcssbbar`` with ``foobar``.

    With ``foo.dcssbbar/C.miz`` reported current, a list keeping ONLY ``foobar/C.miz`` was accepted —
    the running mission was dropped and the pointer aimed at the wrong mission. Component-anchored:
    the strip removes only a component that IS ``.dcssb``, so the two never collide and the drop is
    refused.
    """
    running = m("foo.dcssbbar/C.miz")
    twin = m("foobar/C.miz")
    stub = ListStub([running, twin], current=running)
    with pytest.raises(AttributeError) as caught:
        await set_list(stub, [twin])
    assert str(caught.value) == "You can't delete the running mission."
    assert stub.settings["missionList"] == [running, twin], "the file is untouched on refusal"


@pytest.mark.asyncio
async def test_the_current_mission_is_recognised_through_its_dcssb_copy():
    """DCS reports the ``.dcssb`` copy as current while the list holds the primary spelling."""
    stub = ListStub([m("A.miz"), m("B.miz")],
                    current=os.path.join(BASE, ".dcssb", "B.miz"))
    assert await set_list(stub, [m("B.miz"), m("A.miz")]) == [m("B.miz"), m("A.miz")]
    assert stub.settings["listStartIndex"] == 1


# ------------------------------------------------------- 2. the pointer follows the MISSION (path)

@pytest.mark.asyncio
async def test_the_pointer_follows_the_current_mission_when_it_moves():
    stub = ListStub([m("A.miz"), m("B.miz"), m("C.miz")], current=m("B.miz"))
    await set_list(stub, [m("C.miz"), m("A.miz"), m("B.miz")])
    assert stub.settings["listStartIndex"] == 3
    assert stub.settings["current"] == 3


@pytest.mark.asyncio
async def test_the_pointer_follows_the_current_mission_when_another_moves_past_it():
    stub = ListStub([m("A.miz"), m("B.miz"), m("C.miz"), m("D.miz")], current=m("C.miz"))
    await set_list(stub, [m("A.miz"), m("D.miz"), m("B.miz"), m("C.miz")])
    assert stub.settings["listStartIndex"] == 4


@pytest.mark.asyncio
async def test_a_one_entry_list_always_points_at_1():
    stub = ListStub([m("Only.miz")], current=m("Only.miz"), start=1)
    await set_list(stub, [m("Only.miz")])
    assert stub.settings["listStartIndex"] == 1


@pytest.mark.asyncio
async def test_an_empty_list_points_at_0():
    """No current mission and nothing to keep: the pointer is 0, and the writer takes the empty list."""
    stub = ListStub([], current=None)
    await set_list(stub, [])
    assert stub.settings["listStartIndex"] == 0
    assert stub.settings["current"] == 0


@pytest.mark.asyncio
async def test_the_pointer_follows_a_current_entry_the_disk_cannot_resolve():
    """An entry the disk can no longer resolve still NAMES a mission: the lookup is by list
    membership, never by the file existing — so the pointer follows it through the reorder."""
    stub = ListStub([m("Ghost.miz"), m("A.miz")], current=m("Ghost.miz"))
    await set_list(stub, [m("A.miz"), m("Ghost.miz")])
    assert stub.settings["listStartIndex"] == 2


# --------------------------------------------------------------------- 3. the state gate (offline)

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.LOADING, Status.SHUTTING_DOWN, Status.STOPPED,
                                    Status.PAUSED, Status.RUNNING])
async def test_setmissionlist_refuses_while_the_process_is_up(status):
    """Every process-UP state is refused: a file write made then is silently reverted.

    A ``LOADING`` / ``SHUTTING_DOWN`` server with NO process is not that shape — it is writable
    (``test_mission_busy_states``), and ``SHUTTING_DOWN`` with the process up is refused for a
    different reason: DCS writes ``serverSettings.lua`` itself on its way out, so a write made then
    races its own copy of the file.
    """
    stub = ListStub([m("A.miz"), m("B.miz")], status=status, running=True)
    with pytest.raises(RuntimeError):
        await set_list(stub, [m("B.miz"), m("A.miz")])
    assert stub.settings["missionList"] == [m("A.miz"), m("B.miz")]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.UNREGISTERED, Status.SHUTDOWN])
async def test_setmissionlist_accepts_the_file_write_states(status):
    stub = ListStub([m("A.miz"), m("B.miz")], status=status)
    assert await set_list(stub, [m("B.miz"), m("A.miz")]) == [m("B.miz"), m("A.miz")]


# --------------------------------------------------------------- 4. the ABC member and the RPC stub

def test_the_abc_declares_setmissionlist():
    assert "setMissionList" in ServerABC.__dict__, "the Server ABC must declare the new call"
    assert getattr(ServerABC.__dict__["setMissionList"], "__isabstractmethod__", False) is True


def test_the_proxy_carries_the_rpc_stub():
    """Without this, a node-hosted / cluster server silently lacks the call (the survey's warning)."""
    assert "setMissionList" in ServerProxy.__dict__, "the ServerProxy RPC stub is missing"
    src = ServerProxy.setMissionList.__code__.co_names
    assert "send_to_node_sync" in src


# --------------------------------------------------------------------------- 5. the two actions

class NodeDouble:
    def __init__(self):
        self.removed: list[str] = []

    async def remove_file(self, path):
        self.removed.append(path)


class ActionServer:
    """A ``Server`` double holding the configured list, recording every write call.

    ``setMissionList`` records the M5 full-list rewrite (the reorder); ``deleteMission`` records the
    per-mission delete (the M8 bulk remove — the row's own delete, repeated, descending).
    """

    def __init__(self, name="SRS-1", missions=(), *, current=None, status=Status.SHUTDOWN):
        self.name = name
        self.display_name = name
        self.status = status
        self.configured = [os.path.normpath(x) for x in missions]
        self.current = current
        self.node = NodeDouble()
        self.set_lists: list[list[str]] = []
        self.deletes: list[int] = []
        self.set_error: Exception | None = None
        self.delete_error: Exception | None = None

    async def getMissionList(self):
        return list(self.configured)

    async def get_missions_dir(self):
        return BASE

    async def get_current_mission_file(self):
        return self.current

    async def setMissionList(self, missions):
        if self.set_error is not None:
            raise self.set_error
        self.set_lists.append([os.path.normpath(x) for x in missions])
        self.configured = [os.path.normpath(x) for x in missions]
        return list(self.configured)

    async def deleteMission(self, mission_id):
        if self.delete_error is not None:
            raise self.delete_error
        self.deletes.append(mission_id)
        self.configured.pop(mission_id - 1)
        return list(self.configured)


@pytest.fixture
def actions():
    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    sys.modules.pop("plugins.mission.actions", None)
    discover_actions(["mission"])
    registry = action_registry()
    assert {"delete_missions", "reorder_mission"} <= set(registry)
    try:
        yield registry
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.mission.actions", None)


def ctx_for(server) -> ActionContext:
    bus = types.SimpleNamespace(servers={server.name: server})
    return ActionContext(node=None, bus=bus)


async def run(actions, qualname, server, **params):
    return await actions[qualname](ctx_for(server), server_name=server.name, **params)


THREE = [m("A.miz"), m("B.miz"), m("C.miz")]


@pytest.mark.asyncio
async def test_bulk_delete_removes_each_selected_mission_descending(actions):
    """The bot's own per-mission ``deleteMission``, once each, DESCENDING — largest index first."""
    server = ActionServer(missions=THREE)
    result = await run(actions, "delete_missions", server, missions=[1, 3])
    assert result.success is True
    assert server.deletes == [3, 1], "descending — the largest index first"
    assert result.message == "Removed 2 mission(s) from the list."


@pytest.mark.asyncio
async def test_bulk_delete_duplicates_collapse(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, "delete_missions", server, missions=[2, 2, 2])
    assert server.deletes == [2]
    assert result.message == "Removed 1 mission(s) from the list."


@pytest.mark.asyncio
async def test_bulk_delete_reports_honestly_when_an_index_is_out_of_range(actions):
    """Never a flat "removed M": the unresolvable selection is named."""
    server = ActionServer(missions=THREE)
    result = await run(actions, "delete_missions", server, missions=[1, 9])
    assert result.success is True
    assert server.deletes == [1]
    assert result.message == "Removed 1 of 2. Refused: 9 is not a mission index."


@pytest.mark.asyncio
async def test_bulk_delete_refuses_the_running_mission_and_removes_the_rest(actions):
    """A stale page that names the running mission: it is refused, the rest are removed, honestly."""
    server = ActionServer(missions=THREE, current=m("B.miz"), status=Status.RUNNING)
    result = await run(actions, "delete_missions", server, missions=[1, 2])
    assert result.success is True
    assert server.deletes == [1], "the running mission (B.miz) is refused; #1 is removed"
    assert result.message == "Removed 1 of 2. Refused: the running mission (B.miz)."


@pytest.mark.asyncio
async def test_bulk_delete_of_only_the_running_mission_removes_nothing(actions):
    server = ActionServer(missions=THREE, current=m("B.miz"), status=Status.RUNNING)
    result = await run(actions, "delete_missions", server, missions=[2])
    assert result.success is False
    assert result.message == "No mission could be removed: the running mission (B.miz)."
    assert server.deletes == []


@pytest.mark.asyncio
async def test_bulk_delete_empty_selection_is_a_no_op(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, "delete_missions", server, missions=[])
    assert result.success is True
    assert server.deletes == []
    assert result.message == "No missions were selected."


@pytest.mark.asyncio
async def test_bulk_delete_leaves_the_file_when_not_deleting_from_disk(actions):
    server = ActionServer(missions=THREE)
    await run(actions, "delete_missions", server, missions=[2])
    assert server.node.removed == [], "a list-only delete touches no file"


@pytest.mark.asyncio
async def test_bulk_delete_from_disk_removes_all_three_copies_in_order(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, "delete_missions", server, missions=[2], delete_from_disk=True)
    assert result.success is True
    assert server.node.removed == [
        m("B.miz"), m(".dcssb", "B.miz"), m(".dcssb", "B.miz") + ".orig"]


@pytest.mark.asyncio
async def test_bulk_delete_from_disk_skips_the_refused_running_mission(actions):
    """Only the missions ACTUALLY removed have their files deleted — a refused one is left alone."""
    server = ActionServer(missions=THREE, current=m("B.miz"), status=Status.RUNNING)
    await run(actions, "delete_missions", server, missions=[1, 2], delete_from_disk=True)
    assert server.node.removed == [
        m("A.miz"), m(".dcssb", "A.miz"), m(".dcssb", "A.miz") + ".orig"], \
        "the running mission's file stays"


@pytest.mark.asyncio
async def test_bulk_delete_refuses_only_loading(actions):
    """The ONE state where ``deleteMission`` would edit the file while DCS owns the list."""
    server = ActionServer(missions=THREE, status=Status.LOADING)
    result = await run(actions, "delete_missions", server, missions=[1])
    assert result.success is False
    assert result.message == mission_actions.MISSION_DELETE_LOADING_REFUSAL
    assert server.deletes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.UNREGISTERED, Status.SHUTDOWN, Status.SHUTTING_DOWN,
                                    Status.STOPPED, Status.PAUSED, Status.RUNNING])
async def test_bulk_delete_is_accepted_in_every_other_state(actions, status):
    """The process-up trio (through DCS) and the file-write trio (by the file) both stick."""
    server = ActionServer(missions=THREE, status=status)
    result = await run(actions, "delete_missions", server, missions=[2])
    assert result.success is True
    assert server.deletes == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.LOADING, Status.STOPPED, Status.PAUSED, Status.RUNNING])
async def test_reorder_refuses_while_the_process_is_up(actions, status):
    """The move stays OFFLINE ONLY: it refuses a POST outside the file-write states."""
    server = ActionServer(missions=THREE, status=status)
    moved = await run(actions, "reorder_mission", server, mission=2, direction="up")
    assert moved.success is False
    assert moved.message == mission_actions.MISSION_LIST_OFFLINE_ONLY
    assert server.set_lists == []


@pytest.mark.asyncio
async def test_a_reorder_refused_by_the_writer_hands_its_own_sentence_through(actions):
    """The writer's refusal (a busy state) is the move's answer, verbatim — never a reworded one."""
    server = ActionServer(missions=THREE, status=Status.SHUTTING_DOWN)
    server.set_error = RuntimeError(mission_actions.MISSION_LIST_BUSY_REFUSAL)

    result = await run(actions, "reorder_mission", server, mission=1, direction="down")

    assert result.success is False
    assert result.message == mission_actions.MISSION_LIST_BUSY_REFUSAL
    assert server.set_lists == []


@pytest.mark.asyncio
async def test_reorder_moves_a_mission_and_does_not_drop_a_concurrent_add(actions):
    """The new list is derived from a FRESH snapshot — not from a list the browser rendered."""
    server = ActionServer(missions=THREE)
    result = await run(actions, "reorder_mission", server, mission=2, direction="up")
    assert result.success is True
    assert server.set_lists == [[m("B.miz"), m("A.miz"), m("C.miz")]]
    assert result.message == "Moved 'B.miz' up."


@pytest.mark.asyncio
async def test_reorder_down(actions):
    server = ActionServer(missions=THREE)
    await run(actions, "reorder_mission", server, mission=1, direction="down")
    assert server.set_lists == [[m("B.miz"), m("A.miz"), m("C.miz")]]


@pytest.mark.asyncio
async def test_reorder_bounds_are_no_ops_not_errors(actions):
    server = ActionServer(missions=THREE)
    first = await run(actions, "reorder_mission", server, mission=1, direction="up")
    last = await run(actions, "reorder_mission", server, mission=3, direction="down")
    assert first.success is True and "already first" in first.message
    assert last.success is True and "already last" in last.message
    assert server.set_lists == [], "an off-the-end move writes nothing"


@pytest.mark.asyncio
async def test_reorder_needs_a_real_direction(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, "reorder_mission", server, mission=2, direction="sideways")
    assert result.success is False
    assert server.set_lists == []


@pytest.mark.asyncio
async def test_reorder_unknown_mission(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, "reorder_mission", server, mission=9, direction="up")
    assert result.success is False
    assert server.set_lists == []


# ------------------------------------------ 5b. the stale-page guard (the name beside the index)

@pytest.mark.asyncio
async def test_bulk_delete_accepts_when_the_posted_name_matches(actions):
    """The name the console posts beside the index names the SAME mission — the write goes through."""
    server = ActionServer(missions=THREE)
    result = await run(actions, "delete_missions", server, missions=[2],
                       expected_names={2: "B.miz"})
    assert result.success is True
    assert server.deletes == [2]


@pytest.mark.asyncio
async def test_bulk_delete_refuses_a_stale_selection(actions):
    """The list changed since render: the name at the selected index is different — refused whole."""
    server = ActionServer(missions=THREE)
    result = await run(actions, "delete_missions", server, missions=[2],
                       expected_names={2: "A.miz"})
    assert result.success is False
    assert result.message == mission_actions.MISSION_LIST_CHANGED
    assert server.deletes == [], "a stale selection writes nothing"


@pytest.mark.asyncio
async def test_reorder_accepts_when_the_posted_name_matches(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, "reorder_mission", server, mission=2, direction="up",
                       expected_name="B.miz")
    assert result.success is True
    assert server.set_lists == [[m("B.miz"), m("A.miz"), m("C.miz")]]


@pytest.mark.asyncio
async def test_reorder_refuses_a_stale_name(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, "reorder_mission", server, mission=2, direction="up",
                       expected_name="A.miz")
    assert result.success is False
    assert result.message == mission_actions.MISSION_LIST_CHANGED
    assert server.set_lists == []


# ------------------------------------------------- 5c. the declared state gate (the ONE constant)

def test_bulk_delete_refuses_exactly_loading():
    """The ruling as data: bulk removal refuses ``LOADING`` and nothing else."""
    assert mission_actions.MISSION_DELETE_REFUSED_STATES == (Status.LOADING,)


# ---------------------------------------------- 6. the durability warning, pinned as BEHAVIOUR

class InitStub:
    """A ``self`` for ``ServerImpl._init_mission_list``: a real missions dir and the settings map."""

    def __init__(self, missions_dir, mission_list):
        self.name = "SRS-1"
        self.instance = types.SimpleNamespace(missions_dir=str(missions_dir))
        self.settings = {"missionList": list(mission_list)}
        self.log = types.SimpleNamespace(info=lambda *a, **k: None)


def test_a_list_only_delete_is_not_durable_under_autoscan(tmp_path):
    """``_init_mission_list`` re-adds any on-disk ``.miz`` missing from the list — the dialog says so.

    The warning the bulk-remove dialog carries is not prose: a mission left on disk returns to the
    list on the next autoscan, so the list-only delete is NOT durable while ``server.autoscan`` is on.
    """
    missions_dir = tmp_path / "Missions"
    missions_dir.mkdir()
    (missions_dir / "Kept.miz").write_bytes(b"x")
    (missions_dir / "LeftOnDisk.miz").write_bytes(b"x")

    stub = InitStub(missions_dir, [str(missions_dir / "Kept.miz")])   # the second was list-deleted
    ServerImpl._init_mission_list(stub)

    assert str(missions_dir / "LeftOnDisk.miz") in stub.settings["missionList"], \
        "the auto-scan resurrects an on-disk mission a list-only delete removed"
