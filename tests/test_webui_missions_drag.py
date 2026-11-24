"""The Missions tab's row drag: the shared ``static/reorder.js`` writing the WHOLE new order.

The drag is the SAME gesture the music playlist uses — one shared script, one POST per drop, the
whole new order in the body — and it is an ENHANCEMENT: the row's grip is its only order control, and
the tab reads and writes nothing through JavaScript that it cannot read without it.

Pins here:

* the tab opts the rows in only while the process is DOWN (the order write's own gate), ships the
  shared script only then, and carries the whole-order field + the CSRF token the script posts;
* a dropped PERMUTATION is accepted and the resulting ``missionList`` is pinned (one
  ``setMissionList``);
* a dropped set that is NOT a permutation — a member missing, an extra member, a member named twice
  — is refused with the action's own sentence and NOTHING is written;
* the route's OTHER shape (one index + a direction, a redirect) still posts and still moves, for a
  crafted POST — no page draws that control any more;
* a crafted POST without the CSRF token is refused before the write.
"""
from __future__ import annotations

import os

import re
from pathlib import Path

import pytest

from core import Status
from services.webservice.pages import actions as actions_page
import plugins.mission.actions as mission_actions
from test_webui_missions_tab import (  # noqa: F401 - fixtures imported on purpose
    BASE_DIR, Cluster, MissionServer, SERVER_A, SERVER_B, host_path, instance,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

CURRENT = os.path.join(BASE_DIR, ".dcssb", "Training.miz")
FILES = [
    (os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, ".dcssb", "Training.miz")),
    (os.path.join(BASE_DIR, "Rotated.miz"), os.path.join(BASE_DIR, "Rotated.miz")),
    (os.path.join(BASE_DIR, "Backup.miz"), os.path.join(BASE_DIR, ".dcssb", "Backup.miz.orig")),
]
CONFIGURED = [os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, "Rotated.miz"), os.path.join(BASE_DIR, "Backup.miz")]

REORDER = actions_page.MISSIONS_REORDER_PATH
ORDER_FIELD = actions_page.MISSION_ORDER_FIELD

TEMPLATES = Path(actions_page.__file__).parent.parent / "templates"
SERVER_DETAIL = (TEMPLATES / "server_detail.html").read_text(encoding="utf-8")


def offline_fleet(status=Status.SHUTDOWN):
    """Server A with three configured missions (row 1 current), so every row can move."""
    a = MissionServer(SERVER_A, ["Host A"], files=FILES, current=CURRENT, start=1,
                      configured=CONFIGURED, status=status)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                                instance("Host-B-Instance", b)])


async def missions_page(http, server=SERVER_A):
    response = await http.get(f"/fleet/{server}?tab=missions")
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


def dropped(*names):
    """A drop's body: the whole new order, one repeated ``order`` value per row."""
    body: dict = {}
    for name in names:
        if ORDER_FIELD not in body:
            body[ORDER_FIELD] = name
        elif isinstance(body[ORDER_FIELD], list):
            body[ORDER_FIELD].append(name)
        else:
            body[ORDER_FIELD] = [body[ORDER_FIELD], name]
    return body


# ------------------------------------------------------------------- 1. the tab's drag wiring

@pytest.mark.asyncio
async def test_the_rows_opt_in_to_the_shared_drag_offline(config_dir, clean_registry):
    """Offline the row container carries the script's attributes, the token and the order field."""
    app = build(config_dir, offline_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert "data-reorder" in page and "data-reorder-body" in page
    assert f'data-reorder-url="{REORDER}"' in page
    assert f'data-reorder-field="{ORDER_FIELD}"' in page
    assert "data-reorder-token=" in page, "the drop carries the CSRF token in the body"
    assert page.count("data-reorder-row") == 3, "one draggable row per mission"
    assert page.count("data-reorder-handle") == 3, "one grip per mission row"
    assert 'data-reorder-id="Training.miz"' in page, "the drop names each mission by its logical name"
    assert '<script src="/static/reorder.js" defer></script>' in page
    assert actions_page.MISSION_LIST_OFFLINE_ONLY not in page


@pytest.mark.asyncio
async def test_the_rows_are_not_draggable_while_the_process_is_up(config_dir, clean_registry):
    """The process-down gate does not change: no drop hooks, no script, and the reason is stated."""
    app = build(config_dir, offline_fleet(status=Status.RUNNING), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert "data-reorder-row" not in page
    assert "data-reorder-body" not in page
    assert "/static/reorder.js" not in page
    assert actions_page.MISSION_LIST_OFFLINE_ONLY in page


def test_the_tab_uses_the_shared_script_and_draws_no_pair():
    """One drag implementation: the tab loads ``static/reorder.js`` and the pair is gone."""
    assert "/static/reorder.js" in SERVER_DETAIL
    assert "icons.act('move_up')" not in SERVER_DETAIL
    assert "icons.act('move_down')" not in SERVER_DETAIL
    assert "data-reorder-glyphs" not in SERVER_DETAIL, "the script's opt-in is data-reorder alone"


def test_the_shared_script_serves_a_row_container_not_only_a_table():
    """The ONE drag script resolves a table body OR a ``data-reorder-body`` container."""
    script = (Path(actions_page.__file__).parent.parent / "static" / "reorder.js").read_text(
        encoding="utf-8")
    assert "data-reorder-body" in script
    assert "rowHolder" in script
    assert "querySelectorAll(\"[data-reorder]\")" in script, "a container opts in, not only a table"


# ------------------------------------------------------------------- 2. the drop round trip

@pytest.mark.asyncio
async def test_a_dropped_permutation_is_written_once(config_dir, clean_registry):
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(REORDER, data=dropped("Backup.miz", "Training.miz", "Rotated.miz")
                                | {"server": SERVER_A, "_csrf_token": token_of(page)})
    assert reply.status_code == 200, (reply.status_code, reply.text)
    answer = reply.json()
    assert answer["ok"] is True, answer
    assert cluster.servers[0].set_lists == [[host_path(os.path.join(BASE_DIR, "Backup.miz")),
                                             host_path(os.path.join(BASE_DIR, "Training.miz")),
                                             host_path(os.path.join(BASE_DIR, "Rotated.miz"))]], \
        "one write, the new order"
    assert cluster.servers[0].configured == [host_path(os.path.join(BASE_DIR, "Backup.miz")),
                                             host_path(os.path.join(BASE_DIR, "Training.miz")),
                                             host_path(os.path.join(BASE_DIR, "Rotated.miz"))], \
        "the list is pinned"


@pytest.mark.asyncio
async def test_a_dropped_order_missing_a_member_is_refused(config_dir, clean_registry):
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(REORDER, data=dropped("Rotated.miz", "Training.miz")
                                | {"server": SERVER_A, "_csrf_token": token_of(page)})
    answer = reply.json()
    assert answer["ok"] is False
    assert mission_actions.MISSION_ORDER_NOT_A_PERMUTATION in answer["message"]
    assert cluster.servers[0].set_lists == [], "a non-permutation writes nothing"


@pytest.mark.asyncio
async def test_a_dropped_order_naming_an_extra_member_is_refused(config_dir, clean_registry):
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(REORDER, data=dropped("Training.miz", "Rotated.miz", "Backup.miz",
                                                      "New.miz")
                                | {"server": SERVER_A, "_csrf_token": token_of(page)})
    answer = reply.json()
    assert answer["ok"] is False
    assert mission_actions.MISSION_ORDER_NOT_A_PERMUTATION in answer["message"]
    assert "New.miz" in answer["message"], "the refusal names the member it could not place"
    assert cluster.servers[0].set_lists == []


@pytest.mark.asyncio
async def test_a_dropped_order_naming_a_member_twice_is_refused(config_dir, clean_registry):
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(REORDER, data=dropped("Training.miz", "Training.miz", "Rotated.miz")
                                | {"server": SERVER_A, "_csrf_token": token_of(page)})
    answer = reply.json()
    assert answer["ok"] is False
    assert mission_actions.MISSION_ORDER_NOT_A_PERMUTATION in answer["message"]
    assert "Training.miz" in answer["message"], "the refusal names the duplicated mission"
    assert cluster.servers[0].set_lists == []


@pytest.mark.asyncio
async def test_a_drop_after_the_list_changed_is_refused(config_dir, clean_registry):
    """A mission added between render and drop: the set no longer matches the list — refused whole."""
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        cluster.servers[0].configured.append(os.path.join(BASE_DIR, "Late.miz"))
        reply = await http.post(REORDER, data=dropped("Backup.miz", "Training.miz", "Rotated.miz")
                                | {"server": SERVER_A, "_csrf_token": token_of(page)})
    answer = reply.json()
    assert answer["ok"] is False
    assert mission_actions.MISSION_ORDER_NOT_A_PERMUTATION in answer["message"]
    assert cluster.servers[0].set_lists == []


# --------------------------------------------------------------- 3. the single-step path stays

@pytest.mark.asyncio
async def test_the_single_step_shape_still_moves_one_mission(config_dir, clean_registry):
    """The route's second shape is untouched: an index + a direction still redirects and moves.

    No rendered control posts this any more (the grip is the one order affordance), but the route
    keeps answering it, so a crafted or scripted POST cannot be mistaken for a malformed drop.
    """
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(REORDER, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": "2",
            "mission_name": "2=Rotated.miz", "direction": "up"})
    assert reply.status_code == 303, (reply.status_code, reply.text)
    assert cluster.servers[0].set_lists == [[host_path(os.path.join(BASE_DIR, "Rotated.miz")),
                                             host_path(os.path.join(BASE_DIR, "Training.miz")),
                                             host_path(os.path.join(BASE_DIR, "Backup.miz"))]]


@pytest.mark.asyncio
async def test_a_drop_is_refused_while_the_process_is_up(config_dir, clean_registry):
    """A crafted drop meets the ACTION's own offline refusal, and nothing is written."""
    cluster = offline_fleet(status=Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(REORDER, data=dropped("Backup.miz", "Training.miz", "Rotated.miz")
                                | {"server": SERVER_A, "_csrf_token": token_of(page)})
    answer = reply.json()
    assert answer["ok"] is False
    assert answer["message"] == mission_actions.MISSION_LIST_OFFLINE_ONLY
    assert cluster.servers[0].set_lists == []


@pytest.mark.asyncio
async def test_a_drop_without_the_csrf_token_is_refused(config_dir, clean_registry):
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        await missions_page(http)
        reply = await http.post(REORDER, data=dropped("Backup.miz", "Training.miz", "Rotated.miz")
                                | {"server": SERVER_A})
    assert reply.status_code == 403, reply.text
    assert cluster.servers[0].set_lists == []


# ------------------------------------------------------------------- 4. one rule, one wording

def test_the_drop_field_is_the_one_the_script_posts():
    """The page's ``data-reorder-field`` is the field the route reads — one name, no second copy."""
    field = re.search(r'data-reorder-field="([^"]+)"', SERVER_DETAIL)
    assert field and field.group(1) == "{{ missions_order_field }}"
    assert ORDER_FIELD == "order"
