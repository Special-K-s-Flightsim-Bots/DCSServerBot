"""The Load route's NODE side carries the preset's NAME beside its document, on BOTH branches.

A preset read out of a preset file reaches the node as a DOCUMENT (the change itself, with no name in
it) plus ``preset_name``. ``ServerImpl.loadMission`` is the hop between the console action and
``apply_mission_changes``, and it had no way to carry the name — so a document applied through the
Load dialog was recorded as a TEMPORARY preset, which the server page then claimed.

Two entry points, because ``load_mission`` uses both: ``loadMission`` for a mission that is not the
running one, and ``restart`` — which forwards to ``loadMission`` — for the mission that IS running.
The record only pairs a name with a document when both arrive, so a name wired into one branch and
dropped on the other silently records nothing on that branch.
"""
from __future__ import annotations

import logging

import pytest

from core import Status
from core.data.impl.serverimpl import ServerImpl

MISSION = "Rotated.miz"
DOCUMENT = {"modify": {"weather": {"clouds": {"base": 250}}}}
NAME = "Winter"


class _LoadSink:
    """The minimum ``ServerImpl.loadMission`` touches, with ``apply_mission_changes`` recording."""

    def __init__(self, current: str = MISSION):
        self.log = logging.getLogger("tests.load_preset_name")
        self.settings: dict = {"missionList": [MISSION], "listStartIndex": 1}
        self.status = Status.STOPPED
        self._current = current
        self.applied: list[dict] = []
        self.started = 0

    async def getMissionList(self) -> list[str]:
        return list(self.settings["missionList"])

    def _get_current_mission_file(self) -> str:
        return self._current

    async def apply_mission_changes(self, filename=None, *, use_orig=True, preset=None,
                                    preset_name=None, announce=False):
        self.applied.append({"filename": filename, "use_orig": use_orig, "preset": preset,
                             "preset_name": preset_name, "announce": announce})
        return filename

    async def start(self) -> bool:
        self.started += 1
        return True


class _RestartSink:
    """The minimum ``ServerImpl.restart`` touches: the current mission file and ``loadMission``."""

    def __init__(self):
        self.log = logging.getLogger("tests.load_preset_name.restart")
        self.calls: list[dict] = []

    def _get_current_mission_file(self) -> str:
        return MISSION

    async def loadMission(self, mission, **kwargs):
        self.calls.append({"mission": mission, **kwargs})
        return True


@pytest.mark.asyncio
async def test_load_mission_passes_the_name_to_apply_mission_changes():
    """RED before the keyword existed: ``apply_mission_changes`` got the document and no name, so the
    record derived a temporary preset."""
    sink = _LoadSink()
    await ServerImpl.loadMission(sink, MISSION, preset=DOCUMENT, preset_name=NAME)  # type: ignore[arg-type]

    assert sink.applied, "apply_mission_changes was never reached"
    assert sink.applied[0]["preset"] == DOCUMENT, sink.applied
    assert sink.applied[0]["preset_name"] == NAME, sink.applied


@pytest.mark.asyncio
async def test_load_mission_without_a_name_is_unchanged():
    """Every other caller passes no name, and the call is what it always was."""
    sink = _LoadSink()
    await ServerImpl.loadMission(sink, MISSION, preset="Winter")  # type: ignore[arg-type]

    assert sink.applied[0]["preset"] == "Winter", sink.applied
    assert sink.applied[0]["preset_name"] is None, sink.applied


@pytest.mark.asyncio
async def test_restart_passes_the_name_on_the_restart_branch():
    """THE ALTERNATE BRANCH: the Load route RESTARTS when the picked mission is the running one, so
    the name must survive ``restart`` too — a name wired into ``loadMission`` alone records nothing
    here."""
    sink = _RestartSink()
    await ServerImpl.restart(sink, preset=DOCUMENT, preset_name=NAME)  # type: ignore[arg-type]

    assert sink.calls, "the restart branch never reached loadMission"
    assert sink.calls[0]["preset"] == DOCUMENT, sink.calls
    assert sink.calls[0]["preset_name"] == NAME, sink.calls
