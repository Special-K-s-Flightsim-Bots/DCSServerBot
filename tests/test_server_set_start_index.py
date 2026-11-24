"""``ServerImpl.setStartIndex`` records the pointer in BOTH branches.

The DCS command moves ``net.missionlist`` in the running process; the bot's own ``settings`` cache is
what ``getStartIndex`` — and therefore the Missions tab's "start mission" marker — reads. Writing only
the file branch left that cache on the old index until a restart, so a refresh after choosing the start
mission on a RUNNING server showed the previous mission as active.

The method is exercised UNBOUND against a double that binds only what it touches (``status``,
``settings``, ``send_to_dcs``), which is how this suite drives ``ServerImpl`` — no node, no pool.
"""
import types

import pytest

from core import Status
from core.data.impl.serverimpl import ServerImpl


class Dcs:
    """Records every datagram the method would send to the DCS process."""

    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)


def server(status, missions=("A.miz", "B.miz", "C.miz")):
    dcs = Dcs()
    double = types.SimpleNamespace(status=status, settings={"missionList": list(missions)},
                                   send_to_dcs=dcs)
    double._mission_write_refused = lambda: ServerImpl._mission_write_refused(double)
    return double, dcs


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.RUNNING, Status.PAUSED, Status.STOPPED])
async def test_a_running_process_writes_the_pointer_it_just_told_dcs_about(status):
    """The command AND the cache: the process keeps the list, the bot keeps the marker."""
    double, dcs = server(status)
    await ServerImpl.setStartIndex(double, 2)
    assert dcs.messages == [{"command": "setStartIndex", "id": 2}]
    assert double.settings["listStartIndex"] == 2


@pytest.mark.asyncio
async def test_a_stopped_server_writes_only_the_file_side():
    """No process owns the list: the value goes to settings (the file) and nothing is sent."""
    double, dcs = server(Status.SHUTDOWN)
    await ServerImpl.setStartIndex(double, 3)
    assert dcs.messages == []
    assert double.settings["listStartIndex"] == 3


@pytest.mark.asyncio
async def test_an_out_of_range_index_is_clamped_before_it_is_stored():
    """The clamp applies to the pointer that is kept, in either branch."""
    double, dcs = server(Status.RUNNING)
    await ServerImpl.setStartIndex(double, 9)
    assert dcs.messages == [{"command": "setStartIndex", "id": 1}]
    assert double.settings["listStartIndex"] == 1
