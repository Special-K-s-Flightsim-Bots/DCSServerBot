"""The running mission is a SELECTABLE row, and only a RUNNING server has one.

Two related defects, one cause: the tab treated "the mission the server would start" as "the mission
the server is running".

* the running row's box was drawn ``disabled``, so the single selection could not tick it — which
  blocked BOTH bar actions, including "Set as start". Making the running mission the start mission
  again after a mistake is a legitimate correction and the write accepts it; only the removal refuses
  that row.
* on a server that is not running, ``get_current_mission_file`` FALLS BACK to the start mission, so
  the last mission stayed flagged (and unselectable, therefore undeletable) on a stopped server. The
  running marker is now gated on ``RUNNING`` / ``PAUSED``.

These drive the real app, the real mission actions and the stub cluster.
"""
from __future__ import annotations

import os
import re

import pytest

from core import Status
from services.webservice.pages import actions as actions_page
from test_webui_missions_tab import (  # noqa: F401 - fixtures imported on purpose
    CONFIGURED, CURRENT, SERVER_A, SERVER_B, Cluster, MissionServer, instance,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

ACTIVATE = actions_page.MISSIONS_ACTIVATE_PATH
BULK = actions_page.MISSIONS_BULK_DELETE_PATH
BULK_DIALOG = actions_page.MISSIONS_BULK_DELETE_PATH + actions_page.CONFIRM_SUFFIX
#: row 2 is the one the server has LOADED; the start pointer is row 1 (``start=1``).
RUNNING_ROW = 2


def fleet(status, *, current_loaded=True, start=1):
    """Server A with three configured missions, row 2 loaded when *current_loaded* is set."""
    loaded = CONFIGURED[RUNNING_ROW - 1] if current_loaded else None
    a = MissionServer(SERVER_A, ["Host A"], files=None, current=loaded, start=start,
                      configured=CONFIGURED, status=status)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                               instance("Host-B-Instance", b)])


async def missions_page(http, server=SERVER_A):
    response = await http.get(f"/fleet/{server}?tab=missions")
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


def running_marks(page: str) -> int:
    """How many MISSION ROWS the tab marks as the mission being run now (the live dot in ``.nm``).

    Scoped to the row's own name cell: the shell draws a live dot for a running server as well, and
    that one says nothing about which mission is loaded.
    """
    return len(re.findall(r'<div class="nm">.*?<span class="dot run"', page))


def start_marks(page: str) -> int:
    return len(re.findall(r'<span class="tag a">START</span>', page))


@pytest.mark.asyncio
async def test_the_running_mission_can_be_made_the_start_mission_again(config_dir, clean_registry):
    """Tick the RUNNING row, press Set as start: the pointer moves to it.

    The box used to be disabled, so the correction — the running mission is no longer the start
    mission and should be made the start mission again — was impossible from the tab.
    """
    cluster = fleet(Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        assert "data-sel-box disabled" not in page, "the running row's box must be tickable"
        assert running_marks(page) == 1, "exactly the loaded mission wears the live dot"
        assert start_marks(page) == 1 and f'value="{RUNNING_ROW}"' in page
        reply = await http.post(ACTIVATE, data={
            "server": SERVER_A, "_csrf_token": token_of(page),
            "mission": str(RUNNING_ROW), "mission_name": f"{RUNNING_ROW}={os.path.basename(CONFIGURED[1])}",
        })
        after = await missions_page(http)
    assert reply.status_code == 303, (reply.status_code, reply.text)
    assert cluster.servers[0].indexes == [RUNNING_ROW], "the start index moved to the running mission"
    assert start_marks(after) == 1, "exactly one row carries the START marker afterwards"
    assert running_marks(after) == 1, "the live dot is untouched by the start write"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.STOPPED, Status.SHUTDOWN])
async def test_a_server_that_is_not_running_marks_no_mission_as_running(config_dir, clean_registry,
                                                                       status):
    """``get_current_mission_file`` still names the START mission here — it must not mark a row.

    That fallback answers "what would start"; read as "what is running" it kept the last mission
    flagged, and its box unselectable, on a server that was not running.
    """
    cluster = fleet(status)
    assert cluster.servers[0]._current, "the double still reports a mission file, as the bot does"
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert running_marks(page) == 0, "no row may wear the live dot while nothing runs"
    assert "data-sel-box disabled" not in page
    assert f"0 of {len(CONFIGURED)} selected" in page, "every row is counted as selectable"


@pytest.mark.asyncio
async def test_the_last_running_mission_is_deletable_once_the_server_is_stopped(config_dir,
                                                                              clean_registry):
    """The whole point of the report: on a stopped server ALL missions can be removed."""
    cluster = fleet(Status.STOPPED)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await http.post(BULK_DIALOG, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": str(RUNNING_ROW),
            "mission_name": f"{RUNNING_ROW}={os.path.basename(CONFIGURED[1])}",
        })
        assert dialog.status_code == 200, dialog.text
        token = re.search(rf'name="{actions_page.CONFIRM_FIELD}" value="([^"]+)"', dialog.text)
        assert token, "the removal dialog carries no confirm token"
        reply = await http.post(BULK, data={
            "server": SERVER_A, "_csrf_token": token_of(dialog.text), "mission": str(RUNNING_ROW),
            "mission_name": f"{RUNNING_ROW}={os.path.basename(CONFIGURED[1])}",
            actions_page.CONFIRM_FIELD: token.group(1), "delete_from_disk": "0",
        })
    assert reply.status_code == 303, (reply.status_code, reply.text)
    assert cluster.servers[0].deletes == [RUNNING_ROW], \
        "the mission that last ran must be removable while the server is stopped"
