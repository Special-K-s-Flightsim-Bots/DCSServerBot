"""M7: ``set_active_mission`` — making a CONFIGURED mission the one the server loads.

Two things are pinned here, at the BOT side (``plugins/mission/actions.py``):

* the action changes the start index through the bot's OWN ``Server.setStartIndex`` — never a
  hand-written ``settings`` write — and refuses EXACTLY the states where that write would not stick;
* the LIST READ (``get_mission_list``) marks the ACTIVE row (the ``listStartIndex`` one) as a fact
  DISTINCT from the RUNNING row (``current``).

The state gate is the card's own ruling, restated: the bot's ``setStartIndex`` routes to DCS in
``{STOPPED, PAUSED, RUNNING}`` and writes the FILE otherwise, but in ``LOADING`` the process is up
(DCS owns ``net.missionlist``) while the method still takes the file branch — so the change is
silently reverted. Only ``LOADING`` is refused; every other state sticks.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

import core.actions as actions_module
from core import Status
from core.actions import ActionContext, discover_actions, action_registry
import plugins.mission.actions as mission_actions
from services.webservice.pages import actions as actions_page

#: host-spelled: the bot joins and normalises it, so a raw POSIX root never matches on Windows
BASE = os.path.abspath("/missions")
THREE = [os.path.join(BASE, "A.miz"), os.path.join(BASE, "B.miz"), os.path.join(BASE, "C.miz")]


class ActionServer:
    """A ``Server`` double recording every ``setStartIndex`` call and holding the configured list."""

    def __init__(self, name="SRS-1", missions=(), *, current=None, start=1, status=Status.RUNNING):
        self.name = name
        self.display_name = name
        self.status = status
        self.configured = [os.path.normpath(x) for x in missions]
        self._current = current
        self._start = start
        self.indexes: list[int] = []
        self.set_error: Exception | None = None
 # a hand-written settings write is exactly what the action must NOT do — exposed so a
 # regression that touches it is visible.
        self.settings = {"missionList": list(self.configured), "listStartIndex": start}

    async def getMissionList(self):
        return list(self.configured)

    async def get_missions_dir(self):
        return BASE

    async def getStartIndex(self):
        return self._start

    async def get_current_mission_file(self):
        return self._current

    async def getAllMissionFiles(self):
        return [(path, path) for path in self.configured]

    async def setStartIndex(self, mission_id):
        if self.set_error is not None:
            raise self.set_error
        self.indexes.append(mission_id)


@pytest.fixture
def actions():
    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    sys.modules.pop("plugins.mission.actions", None)
    discover_actions(["mission"])
    registry = action_registry()
    assert "set_active_mission" in registry
    try:
        yield registry
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.mission.actions", None)


def ctx_for(server) -> ActionContext:
    bus = types.SimpleNamespace(servers={server.name: server})
    return ActionContext(node=None, bus=bus)


async def run(actions, server, **params):
    return await actions["set_active_mission"](ctx_for(server), server_name=server.name, **params)


# ---------------------------------------------- 1. the write goes through the bot's own method

@pytest.mark.asyncio
async def test_activate_sets_the_start_index_through_the_bot_method(actions):
    """The bot's ``setStartIndex`` is called with the 1-based index — no hand-written write."""
    server = ActionServer(missions=THREE)
    result = await run(actions, server, mission=2)
    assert result.success is True
    assert server.indexes == [2], "the start index is set through Server.setStartIndex"
    assert server.settings["listStartIndex"] == 1, "the action never writes settings by hand"
    assert result.data["index"] == 2 and result.data["active"] == 2


@pytest.mark.asyncio
async def test_activate_accepts_a_logical_name(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, server, mission="B")
    assert result.success is True
    assert server.indexes == [2]


@pytest.mark.asyncio
async def test_activate_accepts_a_name_without_the_extension(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, server, mission="C.miz")
    assert result.success is True
    assert server.indexes == [3]


# ----------------------------------------------------------------------- 2. the refusals

@pytest.mark.asyncio
async def test_activate_refuses_an_unknown_name(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, server, mission="Nope")
    assert result.success is False
    assert result.message == "Mission 'Nope' not found on server 'SRS-1'."
    assert server.indexes == [], "an unknown mission is refused without touching the bot's method"


@pytest.mark.asyncio
async def test_activate_refuses_an_out_of_range_index_without_clamping(actions):
    """The bot's own method CLAMPS an out-of-range index to 1 — the action must refuse first."""
    server = ActionServer(missions=THREE)
    result = await run(actions, server, mission=9)
    assert result.success is False
    assert result.message == "Mission '9' not found on server 'SRS-1'."
    assert server.indexes == [], "index 9 must never reach setStartIndex (which would clamp it to 1)"


@pytest.mark.asyncio
async def test_activate_refuses_a_non_positive_index(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, server, mission=0)
    assert result.success is False
    assert server.indexes == []


@pytest.mark.asyncio
async def test_activate_refuses_loading(actions):
    """The ONE state where the write would be silently reverted — refused in the action's sentence."""
    server = ActionServer(missions=THREE, status=Status.LOADING)
    result = await run(actions, server, mission=2)
    assert result.success is False
    assert result.message == mission_actions.MISSION_ACTIVE_LOADING_REFUSAL
    assert server.indexes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.UNREGISTERED, Status.SHUTDOWN, Status.SHUTTING_DOWN,
                                    Status.STOPPED, Status.PAUSED, Status.RUNNING])
async def test_activate_is_accepted_in_every_other_state(actions, status):
    """Both the process-up trio (through DCS) and the file-write trio stick — so both are accepted."""
    server = ActionServer(missions=THREE, status=status)
    result = await run(actions, server, mission=2)
    assert result.success is True
    assert server.indexes == [2]


@pytest.mark.asyncio
async def test_activate_reports_a_failure_from_the_bot_method(actions):
    server = ActionServer(missions=THREE)
    server.set_error = RuntimeError("boom")
    result = await run(actions, server, mission=2)
    assert result.success is False
    assert "boom" in result.message


# --------------------------------------------------------- 3. the stale-page guard

@pytest.mark.asyncio
async def test_activate_accepts_when_the_posted_name_matches(actions):
    server = ActionServer(missions=THREE)
    result = await run(actions, server, mission=2, expected_name="B.miz")
    assert result.success is True
    assert server.indexes == [2]


@pytest.mark.asyncio
async def test_activate_refuses_a_stale_name(actions):
    """The list changed since render: the name at the index is different — refused, nothing written."""
    server = ActionServer(missions=THREE)
    result = await run(actions, server, mission=2, expected_name="A.miz")
    assert result.success is False
    assert result.message == mission_actions.MISSION_LIST_CHANGED
    assert server.indexes == []


# -------------------------------------- 4. the list read names the ACTIVE row (distinct from RUNNING)

@pytest.mark.asyncio
async def test_the_list_marks_the_active_row_distinct_from_the_running_row(actions):
    """``active`` is the ``listStartIndex`` row; ``current`` is the loaded one — a DIFFERENT fact."""
    server = ActionServer(missions=THREE, current=os.path.join(BASE, "B.miz"), start=3)
    result = await actions["get_mission_list"](ctx_for(server), server_name=server.name)
    assert result.success is True
    rows = result.data["missions"]
    assert [row["active"] for row in rows] == [False, False, True], "row 3 is active"
    assert [row["current"] for row in rows] == [False, True, False], "row 2 is running"
    assert result.data["listStartIndex"] == 3
    assert result.data["active"] == 3
    assert result.data["current"] == 2, "the running mission is reported separately"


@pytest.mark.asyncio
async def test_only_one_row_is_ever_active(actions):
    server = ActionServer(missions=THREE, current=os.path.join(BASE, "B.miz"), start=1)
    result = await actions["get_mission_list"](ctx_for(server), server_name=server.name)
    actives = [row for row in result.data["missions"] if row["active"]]
    assert len(actives) == 1 and actives[0]["index"] == 1


@pytest.mark.asyncio
async def test_no_row_is_active_when_the_start_index_names_none(actions):
    """A 0 (or out-of-range) ``listStartIndex`` names no row — and the read says so, unambiguously."""
    server = ActionServer(missions=THREE, start=9)
    result = await actions["get_mission_list"](ctx_for(server), server_name=server.name)
    assert [row["active"] for row in result.data["missions"]] == [False, False, False]
    assert result.data["active"] == 0


# ------------------------------------------------------ 5. the console's copy of the sentence

def test_the_console_keeps_the_actions_refusal_sentence_in_step():
    """One rule, one wording: the console's own copy equals the action's."""
    assert actions_page.MISSION_ACTIVE_LOADING_REFUSAL == \
        mission_actions.MISSION_ACTIVE_LOADING_REFUSAL


def test_the_console_gate_is_every_state_but_loading():
    assert "LOADING" not in actions_page.MISSIONS_ACTIVATE_STATES
    assert set(actions_page.MISSIONS_ACTIVATE_STATES) == {
        "UNREGISTERED", "SHUTDOWN", "SHUTTING_DOWN", "STOPPED", "PAUSED", "RUNNING"}
