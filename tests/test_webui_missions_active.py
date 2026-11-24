"""M7, through the console: the "Make active" write route and the list's active row.

The write makes ONE configured mission the one the server loads, through the bot's own
``setStartIndex`` (``plugins/mission/actions.py::set_active_mission``). It differs from the M5 pair:
it is offered in EVERY state except ``LOADING`` (the one state where the bot's file-branch write is
silently reverted), so it works while a server runs. This drives the real app with the real local
backend, the real mission actions and the stub cluster, and pins:

* the route is registered under its own capability, ``missions.activate`` (WRITE_ROLES + a scope
  grant), and a POST reaches the action;
* the action sets the start index through the bot's method, in a file-write state AND while running;
* a ``LOADING`` POST is refused by the action in the ONE shared sentence — the console keeps its own
  copy equal to the action's;
* the console's ``mission_rows`` carries the ACTIVE flag the action's read produced.
"""
from __future__ import annotations

import os

import pytest

from core import Status
from services.webservice.pages import actions as actions_page
from services.webservice.pages import server_detail as detail_page
import plugins.mission.actions as mission_actions
from test_webui_missions_reorder import (  # noqa: F401 - fixtures imported on purpose
    BASE_DIR, CONFIGURED, FILES, CURRENT,
)
from test_webui_missions_tab import (  # noqa: F401 - fixtures imported on purpose
    Cluster, MissionServer, SERVER_A, SERVER_B, instance,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

ACTIVATE = actions_page.MISSIONS_ACTIVATE_PATH


def fleet(status=Status.SHUTDOWN):
    """Server A with three configured missions (row 1 current), so rows 2/3 are activatable."""
    a = MissionServer(SERVER_A, ["Host A"], files=FILES, current=CURRENT, start=1,
                      configured=CONFIGURED, status=status)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                               instance("Host-B-Instance", b)])


async def missions_page(http, server=SERVER_A):
    response = await http.get(f"/fleet/{server}?tab=missions")
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


# --------------------------------------------------------------------- 1. the write route

@pytest.mark.asyncio
async def test_activate_post_sets_the_active_mission_offline(config_dir, clean_registry):
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(ACTIVATE, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": "2",
            "mission_name": "2=Rotated.miz"})
    assert reply.status_code == 303, (reply.status_code, reply.text)
    assert cluster.servers[0].indexes == [2], "the start index is set through the bot's method"


@pytest.mark.asyncio
async def test_activate_post_works_while_running(config_dir, clean_registry):
    """The whole point of the gate: unlike the M5 writes, this one applies while a server runs."""
    cluster = fleet(status=Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(ACTIVATE, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": "3",
            "mission_name": "3=Backup.miz"})
    assert reply.status_code == 303, (reply.status_code, reply.text)
    assert cluster.servers[0].indexes == [3]


@pytest.mark.asyncio
async def test_a_loading_post_is_refused_by_the_action(config_dir, clean_registry):
    """A crafted POST in the one non-sticking state meets the action's own refusal, nothing written."""
    cluster = fleet(status=Status.LOADING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(ACTIVATE, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": "2",
            "mission_name": "2=Rotated.miz"})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert actions_page.MISSION_ACTIVE_LOADING_REFUSAL in after
    assert cluster.servers[0].indexes == [], "a LOADING write sticks nowhere — it is refused"


@pytest.mark.asyncio
async def test_a_stale_activate_post_is_refused(config_dir, clean_registry):
    """The list changes between render and POST: the captured row name no longer names that index."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        cluster.servers[0].configured.insert(0, os.path.join(BASE_DIR, "New.miz"))
        reply = await http.post(ACTIVATE, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": "2",
            "mission_name": "2=Rotated.miz"})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert mission_actions.MISSION_LIST_CHANGED in after
    assert cluster.servers[0].indexes == [], "a stale page activates nothing"


# ------------------------------------------------------------------ 2. the wiring and the gate

def test_the_console_loading_sentence_matches_the_actions():
    """One rule, one wording: the console's own copy equals the plugin's."""
    assert actions_page.MISSION_ACTIVE_LOADING_REFUSAL == \
        mission_actions.MISSION_ACTIVE_LOADING_REFUSAL


def test_the_console_gate_is_every_state_but_loading():
    assert "LOADING" not in actions_page.MISSIONS_ACTIVATE_STATES
    assert set(actions_page.MISSIONS_ACTIVATE_STATES) == {
        "UNREGISTERED", "SHUTDOWN", "SHUTTING_DOWN", "STOPPED", "PAUSED", "RUNNING"}


def test_the_activate_write_is_declared_with_its_own_capability():
    by_key = {action.key: action for action in actions_page.MISSION_ACTIONS}
    action = by_key["mission_activate"]
    assert action.qualname == "set_active_mission"
    assert action.capability == actions_page.MISSIONS_ACTIVATE_CAPABILITY == "missions.activate"
    assert action.path == ACTIVATE
    assert set(action.statuses) == set(actions_page.MISSIONS_ACTIVATE_STATES)
    assert action.dialog is False, "the activate control posts directly — no dialog"


def test_the_activate_capability_is_write_roles_with_a_scope_grant():
    from conftest import permissions

    actions_page.declare()
    assert permissions.roles_for_capability(actions_page.MISSIONS_ACTIVATE_CAPABILITY) == \
        actions_page.WRITE_ROLES
    assert permissions.scope_grants(actions_page.MISSIONS_ACTIVATE_CAPABILITY) is True


@pytest.mark.asyncio
async def test_the_activate_route_is_registered_under_its_capability(config_dir, clean_registry):
    app = build(config_dir, fleet(), plugins=("mission",))
    registrar = app.state.webui_registrar
    assert registrar.capability_for(path=ACTIVATE) == actions_page.MISSIONS_ACTIVATE_CAPABILITY


# ------------------------------------------------------------- 3. the list's active row, on the page

def test_mission_rows_carry_the_active_flag():
    """The renderer's own row dict marks the active entry — carried through from the action's read."""
    data = {"missions": [
        {"index": 1, "name": "A.miz", "path": "A.miz", "copy": "primary",
         "current": False, "active": False, "resolved": True},
        {"index": 2, "name": "B.miz", "path": "B.miz", "copy": "primary",
         "current": True, "active": True, "resolved": True},
    ]}
    rows = detail_page.mission_rows(data, SERVER_A)
    assert [row["active"] for row in rows] == [False, True]
    assert [row["current"] for row in rows] == [False, True]
