"""The start-mission pointer must keep naming a MISSION across adds and removals.

``listStartIndex`` is a SLOT, but the thing it means is a mission. Three consequences, each pinned
below:

* removing an entry shifts every later one down a slot, so a pointer beyond the removed slot has to
  move with them — otherwise it names a different mission;
* removing the START mission hands the slot to the mission that followed it, and a list that shrinks
  under the pointer is clamped instead of left dangling (a dangling pointer is what left a server with
  NO start mission, showing nothing selected at all);
* adding never moves the pointer except for the FIRST mission of an otherwise empty rotation, which is
  the only mission there is to start. A dangling pointer used to be "repaired" by the next add, so an
  unticked mission silently became the start one.

``ServerImpl`` is driven UNBOUND against a double that binds only what the methods touch — the same
pattern as ``test_server_set_start_index``.
"""
import types

import pytest

from core import Status
from core.data.impl.serverimpl import ServerImpl


class Dcs:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)


class Sync:
    """``send_to_dcs_sync`` for the process-up branch: the delete reply carries the new list."""

    def __init__(self, missions):
        self.missions = missions
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)
        if message["command"] == "deleteMission":
            index = int(message["id"]) - 1
            self.missions = self.missions[:index] + self.missions[index + 1:]
            return {"missionList": list(self.missions)}
        return {}


def double(status=Status.SHUTDOWN, missions=("A.miz", "B.miz", "C.miz"), start=1):
    dcs = Dcs()
    settings = {"missionList": list(missions), "listStartIndex": start}
    server = types.SimpleNamespace(status=status, settings=settings, send_to_dcs=dcs,
                                   mission_id=None)

    async def get_mission_list():
        return list(settings["missionList"])

    server.getMissionList = get_mission_list
    server.setStartIndex = lambda index: ServerImpl.setStartIndex(server, index)
    server._mission_write_refused = lambda: ServerImpl._mission_write_refused(server)
    server.sync = Sync(list(missions))

    async def send_to_dcs_sync(message):
        return await server.sync(message)

    server.send_to_dcs_sync = send_to_dcs_sync
    return server, dcs


@pytest.mark.asyncio
async def test_removing_a_mission_before_the_start_one_keeps_the_same_start_mission():
    server, _ = double(start=3)
    await ServerImpl.deleteMission(server, 1)
    assert server.settings["missionList"] == ["B.miz", "C.miz"]
    assert server.settings["listStartIndex"] == 2, "C.miz moved down a slot; the pointer must follow it"


@pytest.mark.asyncio
async def test_removing_the_start_mission_hands_the_slot_to_the_next_mission():
    server, _ = double(start=2)
    await ServerImpl.deleteMission(server, 2)
    assert server.settings["missionList"] == ["A.miz", "C.miz"]
    assert server.settings["listStartIndex"] == 2, "C.miz takes the slot the removed mission held"


@pytest.mark.asyncio
async def test_removing_the_last_start_mission_clamps_to_the_list():
    server, _ = double(start=3)
    await ServerImpl.deleteMission(server, 3)
    assert server.settings["missionList"] == ["A.miz", "B.miz"]
    assert server.settings["listStartIndex"] == 2, "the pointer cannot name a slot that no longer exists"


@pytest.mark.asyncio
async def test_removing_the_only_mission_leaves_no_start_mission():
    server, _ = double(missions=("A.miz",), start=1)
    await ServerImpl.deleteMission(server, 1)
    assert server.settings["missionList"] == []
    assert server.settings["listStartIndex"] == 0, "an empty rotation has no start mission"


@pytest.mark.asyncio
async def test_a_running_process_is_told_about_the_new_start_mission():
    """STOPPED owns the list in its own memory, so the pointer has to move THERE, not only in settings."""
    server, dcs = double(status=Status.STOPPED, start=3)
    await ServerImpl.deleteMission(server, 3)
    assert server.sync.messages == [{"command": "deleteMission", "id": 3}], \
        "the removal itself goes through the awaited DCS reply"
    assert dcs.messages == [{"command": "setStartIndex", "id": 2}], \
        "the pointer is moved in the process too, which owns the list in this state"
    assert server.settings["listStartIndex"] == 2


@pytest.mark.asyncio
async def test_the_first_mission_of_an_empty_rotation_is_the_start_mission_unticked():
    server, _ = double(missions=(), start=0)
    await ServerImpl.addMission(server, "A.miz")
    assert server.settings["missionList"] == ["A.miz"]
    assert server.settings["listStartIndex"] == 1, "there is nothing else it could start"


@pytest.mark.asyncio
async def test_an_add_does_not_move_the_pointer_unless_it_is_ticked():
    server, _ = double(start=1)
    await ServerImpl.addMission(server, "D.miz")
    assert server.settings["missionList"] == ["A.miz", "B.miz", "C.miz", "D.miz"]
    assert server.settings["listStartIndex"] == 1, "an unticked add must leave the start mission alone"

    await ServerImpl.addMission(server, "E.miz", autostart=True)
    assert server.settings["listStartIndex"] == 5, "a ticked add becomes the start mission"
