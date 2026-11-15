"""M5, through the console: the row's order control, the bulk-remove dialog, and the offline gate.

Both writes replace ``missionList``'s ORDER in one go, so both are offered ONLY while the DCS process
is DOWN (``{UNREGISTERED, SHUTDOWN, SHUTTING_DOWN}``): a file write made while it is up is silently
reverted. This drives the real app with the real local backend, the real mission actions and the
stub cluster, and pins:

* the order control: the row's GRIP is the only one — the up/down pair is not drawn at all, so no
  ``direction`` rides the rendered page — and the offline reason is stated where the grip is omitted;
* the route keeps BOTH shapes: a dropped whole order (JSON) and the single-step index + direction
  (a redirect) that a crafted POST still reaches;
* the stale-page guard: the row's logical name rides beside its 1-based index;
* the bulk-remove dialog: it IS the selection — a checkbox per mission, the running one omitted with
  the reason said — plus the remove-from-disk box and the one-shot token; confirming posts the
  selection;
* the gate: the controls are absent while the process is up, and a crafted POST is REFUSED in each
  process-up state by the action itself;
* the console's own offline sentence equals the action's (one rule, one wording).
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
BULK = actions_page.MISSIONS_BULK_DELETE_PATH
BULK_DIALOG = BULK + actions_page.CONFIRM_SUFFIX
TOKEN_FIELD = actions_page.CONFIRM_FIELD

TEMPLATES = Path(actions_page.__file__).parent.parent / "templates"
ICONS = (TEMPLATES / "_icons.html").read_text(encoding="utf-8")
SERVER_DETAIL = (TEMPLATES / "server_detail.html").read_text(encoding="utf-8")


def glyphs() -> dict:
    return dict(re.findall(r"'([a-z_]+)':\s*'([^']*)'", ICONS))


def offline_fleet(status=Status.SHUTDOWN):
    """Server A with three configured missions (row 1 current), so rows 2 and 3 are selectable."""
    a = MissionServer(SERVER_A, ["Host A"], files=FILES, current=CURRENT, start=1,
                      configured=CONFIGURED, status=status)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                                instance("Host-B-Instance", b)])


async def missions_page(http, server=SERVER_A):
    response = await http.get(f"/fleet/{server}?tab=missions")
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


def posted(*pairs):
    """A form body that can carry REPEATED fields, in order (the bulk selection)."""
    body: dict = {}
    for name, value in pairs:
        if name not in body:
            body[name] = value
        elif isinstance(body[name], list):
            body[name].append(value)
        else:
            body[name] = [body[name], value]
    return body


def one_shot_token(html: str) -> str:
    match = re.search(rf'name="{re.escape(TOKEN_FIELD)}" value="([^"]+)"', html)
    assert match, "the destructive dialog carries no confirm token"
    return match.group(1)


# -------------------------------------------------------------- 1. the row's one order control

@pytest.mark.asyncio
async def test_the_rows_offer_no_drawn_move_control_offline(config_dir, clean_registry):
    """While the process is DOWN the order control is the GRIP alone; the reason is NOT shown."""
    app = build(config_dir, offline_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert page.count('class="grip"') == 3, "one grip per row"
    assert f'action="{REORDER}"' not in page, "the up/down pair is still drawn"
    assert actions_page.MISSION_LIST_OFFLINE_ONLY not in page
    # THE DROP'S OWN CONTEXT: the list names the server it belongs to, because the route resolves its
    # target from the body. Without it a drop cannot resolve and answers a redirect instead of JSON —
    # which the script reads as a refusal and puts the rows back (the "mission is back where it was"
    # report).
    assert f'data-reorder-params="server={SERVER_A}"' in page, "the drag names its server"
    assert "data-reorder-body" in page, "the rows are the script's row holder"


@pytest.mark.asyncio
async def test_no_direction_is_posted_anywhere_in_the_rendered_tab(config_dir, clean_registry):
    """The pair's shape is gone from the page: nothing carries an ``up``/``down`` direction."""
    app = build(config_dir, offline_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert 'value="up"' not in page and 'value="down"' not in page
    assert actions_page.MISSION_DIRECTION_FIELD not in page, "a direction field is still named"


@pytest.mark.asyncio
async def test_the_order_control_is_absent_and_reasoned_while_the_process_is_up(config_dir,
                                                                               clean_registry):
    """Online: no grip and no drag hook anywhere, and the reason is stated instead."""
    app = build(config_dir, offline_fleet(status=Status.RUNNING), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert f'action="{REORDER}"' not in page
    assert 'class="grip"' not in page and "data-reorder-handle" not in page
    assert actions_page.MISSION_LIST_OFFLINE_ONLY in page


def test_the_move_glyphs_are_gone_and_the_tab_pastes_no_svg():
    """One order affordance per row: the pair's drawings are deleted, and none is pasted instead."""
    set_ = glyphs()
    assert "move_up" not in set_ and "move_down" not in set_, "an unused drawing survives in the set"
    assert "icons.act('move_up')" not in SERVER_DETAIL and "icons.act('move_down')" not in SERVER_DETAIL
    assert "<svg" not in SERVER_DETAIL, "raw SVG markup was pasted into the tab"


# ------------------------------------------------------------------- 2. the reorder round trip

@pytest.mark.asyncio
async def test_a_reorder_post_moves_the_mission(config_dir, clean_registry):
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
async def test_a_dropped_order_writes_the_new_list_and_answers_json(config_dir, clean_registry):
    """THE DROP'S SHAPE, end to end: the whole new order, the server in the body, a JSON answer.

    This is the request the browser's drag posts. It must reach the same write the single-step form
    does and answer JSON (``ok``) so the script keeps the DOM it drew instead of restoring it.
    """
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(
            REORDER, data={"server": SERVER_A, "_csrf_token": token_of(page),
                           "order": ["Rotated.miz", "Training.miz", "Backup.miz"]},
            headers={"Accept": "application/json"})
    assert reply.status_code == 200, (reply.status_code, reply.text[:300])
    answer = reply.json()
    assert answer["ok"] is True, answer
    assert cluster.servers[0].set_lists == [[host_path(os.path.join(BASE_DIR, "Rotated.miz")),
                                             host_path(os.path.join(BASE_DIR, "Training.miz")),
                                             host_path(os.path.join(BASE_DIR, "Backup.miz"))]]


@pytest.mark.asyncio
async def test_the_load_form_carries_the_logical_name_beside_the_index(config_dir, clean_registry):
    """The one row write left posts the row's LOGICAL name with its index — the stale-page check."""
    app = build(config_dir, offline_fleet(status=Status.RUNNING), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    rows = re.findall(rf'<form[^>]*action="{re.escape(actions_page.MISSIONS_LOAD_PATH)}[^"]*"[^>]*>'
                      r'.*?</form>', page, re.S)
    assert len(rows) == 3, "one Load form per mission row"
    assert all('name="mission_name"' in row for row in rows), "every Load row posts its own name"
    assert 'value="2=Rotated.miz"' in page, "the row posts its own logical name"


@pytest.mark.asyncio
async def test_a_reorder_post_after_the_list_changed_is_refused(config_dir, clean_registry):
    """The list changes between render and POST: the captured row name no longer names that index."""
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        row2 = next(v for v in re.findall(r'name="mission_name" value="([^"]+)"', page)
                    if v.startswith("2="))
 # someone adds a mission at the FRONT while the page is open
        cluster.servers[0].configured.insert(0, os.path.join(BASE_DIR, "New.miz"))
        reply = await http.post(REORDER, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": "2",
            "mission_name": row2, "direction": "up"})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert mission_actions.MISSION_LIST_CHANGED in after
    assert cluster.servers[0].set_lists == [], "a stale page moves nothing"


@pytest.mark.asyncio
async def test_a_reorder_post_is_refused_while_the_process_is_up(config_dir, clean_registry):
    """A crafted POST outside the file-write states meets the ACTION's own refusal, nothing written."""
    cluster = offline_fleet(status=Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(REORDER, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": "2", "direction": "up"})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert actions_page.MISSION_LIST_OFFLINE_ONLY in after
    assert cluster.servers[0].set_lists == [], "a refused reorder writes nothing"


# ------------------------------------------------------------ 2b. the stale-page guard on LOAD
# Load addresses a mission by index too, and it CHANGES the server (starts a mission), so it wears the
# same name-beside-index guard as the two writes: a stale row must not load the wrong mission.

@pytest.mark.asyncio
async def test_a_stale_load_post_is_refused(config_dir, clean_registry):
    """The row's name no longer names the mission at that index — the load is refused, nothing starts."""
    cluster = offline_fleet(status=Status.RUNNING)   # the Load control is offered only online
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        assert 'name="mission_name" value="2=Rotated.miz"' in page, \
            "the Load row posts its own logical name"
        reply = await http.post(actions_page.MISSIONS_LOAD_PATH, data={
            "server": SERVER_A, "_csrf_token": token_of(page),
            "mission": "2", "mission_name": "2=Training.miz"})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert mission_actions.MISSION_LIST_CHANGED in after
    assert cluster.servers[0].loads == [], "a stale row loads nothing"


# ------------------------------------------------------------------- 3. the bulk-remove dialog

@pytest.mark.asyncio
async def test_the_bulk_dialog_is_a_read_only_confirmation(config_dir, clean_registry):
    """The SELECTION is made in the list (M9): the dialog shows the CHOSEN missions READ-ONLY — no
    checkboxes named ``mission`` — beside the disk box, and carries the selection as hidden fields and
    the one-shot token."""
    app = build(config_dir, offline_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
 # the selection lives on the TAB now: a box per row, owned by the ONE selection form
        assert '<form id="missions-form"' in page, "the selection form is gone"
        assert page.count("data-sel-box") == 3, "one box per row"
        dialog = await http.post(BULK_DIALOG, data=posted(
            ("server", SERVER_A), ("_csrf_token", token_of(page)),
            ("mission", "2"), ("mission", "3"),
            ("mission_name", "2=Rotated.miz"), ("mission_name", "3=Backup.miz")))
    assert dialog.status_code == 200, dialog.text
    text = dialog.text
    assert "<li>Rotated.miz</li>" in text and "<li>Backup.miz</li>" in text, "the chosen names are shown"
    assert 'name="mission" value="2"' in text and 'name="mission" value="3"' in text, "hidden selection"
    assert not re.search(r'type="checkbox"[^>]*name="mission"', text), \
        "the dialog must NOT draw the selection as checkboxes"
    assert 'name="delete_from_disk" value="1"' in text, "the remove-from-disk box rides along"
    assert 'form="dlg-form"' in text, "the disk box is form-associated to the footer form"
 # the durability warning, next to the box: a list-only removal is undone by the auto-scan
    assert "re-added" in text and "auto-scan" in text, "the durability of a list-only delete is said"
    assert f'name="{TOKEN_FIELD}"' in text, "the destructive action carries its token"
    assert "Remove 2 missions" in text, "the button's label carries the count"


@pytest.mark.asyncio
async def test_the_bulk_delete_post_removes_the_selected_missions(config_dir, clean_registry):
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await http.post(BULK_DIALOG, data={"server": SERVER_A,
                                                    "_csrf_token": token_of(page)})
        reply = await http.post(BULK, data=posted(
            ("server", SERVER_A), ("_csrf_token", token_of(dialog.text)),
            ("mission", "2"), ("mission", "3"),
            ("mission_name", "2=Rotated.miz"), ("mission_name", "3=Backup.miz"),
            ("delete_from_disk", "0"), (TOKEN_FIELD, one_shot_token(dialog.text))))
    assert reply.status_code == 303, (reply.status_code, reply.text)
    assert cluster.servers[0].deletes == [3, 2], "the row delete repeated, descending"
    assert cluster.servers[0].configured == [host_path(os.path.join(BASE_DIR, "Training.miz"))]


@pytest.mark.asyncio
async def test_the_bulk_delete_post_after_the_list_changed_is_refused(config_dir, clean_registry):
    """The list changes between the dialog render and the POST: the whole selection is refused."""
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
 # the dialog OPENER carries the selection now (M9), and the dialog forwards its name map on
        dialog = await http.post(BULK_DIALOG, data=posted(
            ("server", SERVER_A), ("_csrf_token", token_of(page)),
            ("mission", "2"), ("mission_name", "1=Training.miz"),
            ("mission_name", "2=Rotated.miz"), ("mission_name", "3=Backup.miz")))
        names = re.findall(r'name="mission_name" value="([^"]+)"', dialog.text)
 # a mission is added at the FRONT while the dialog sits open
        cluster.servers[0].configured.insert(0, os.path.join(BASE_DIR, "New.miz"))
        reply = await http.post(BULK, data=posted(
            ("server", SERVER_A), ("_csrf_token", token_of(dialog.text)),
            ("mission", "2"), ("delete_from_disk", "0"),
            (TOKEN_FIELD, one_shot_token(dialog.text)),
 *[("mission_name", value) for value in names]))
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert mission_actions.MISSION_LIST_CHANGED in after
    assert cluster.servers[0].set_lists == [], "a stale selection deletes nothing"


@pytest.mark.asyncio
async def test_the_bulk_route_refuses_a_post_that_skipped_its_dialog(config_dir, clean_registry):
    cluster = offline_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(BULK, data={"server": SERVER_A, "_csrf_token": token_of(page),
                                            "mission": "2"})
    assert reply.status_code == 403, reply.text
    assert cluster.servers[0].set_lists == []


@pytest.mark.asyncio
async def test_selecting_the_running_mission_is_refused_whole(config_dir, clean_registry):
    """A really RUNNING server: a crafted POST naming the loaded mission is refused, nothing removed.

    The load has to be LIVE for that: on a server that is not running no mission is running, so every
    row is removable (see ``test_webui_missions_running_row``).
    """
    cluster = offline_fleet(status=Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await http.post(BULK_DIALOG, data={"server": SERVER_A,
                                                    "_csrf_token": token_of(page)})
        reply = await http.post(BULK, data={
            "server": SERVER_A, "_csrf_token": token_of(dialog.text), "mission": "1",
            TOKEN_FIELD: one_shot_token(dialog.text)})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert "the running mission (Training.miz)" in after
    assert cluster.servers[0].deletes == [], "the running mission is refused, nothing removed"


@pytest.mark.asyncio
async def test_the_move_pair_is_the_only_offline_only_control(config_dir, clean_registry):
    """While the process is up the MOVE PAIR is absent (offline-only, its reason stated) while the bulk
    SELECTION is offered (M8's gate: every state but LOADING) — M9's deliberate asymmetry."""
    app = build(config_dir, offline_fleet(status=Status.RUNNING), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert f'action="{REORDER}"' not in page, "the move pair is offline-only"
    assert f'action="{BULK_DIALOG}"' in page, "the bulk selection works while running (M8's gate)"
    assert actions_page.MISSION_LIST_OFFLINE_ONLY in page


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.STOPPED, Status.PAUSED, Status.RUNNING])
async def test_the_move_pair_is_absent_and_the_bulk_present_in_every_non_loading_state(
        config_dir, clean_registry, status):
    """Every state but LOADING: the move pair is absent (reasoned), the bulk selection is present."""
    app = build(config_dir, offline_fleet(status=status), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert f'action="{REORDER}"' not in page, status
    assert f'action="{BULK_DIALOG}"' in page, status
    assert "data-sel-box" in page, "the selection boxes vanish with the bar"
    assert actions_page.MISSION_LIST_OFFLINE_ONLY in page, status


@pytest.mark.asyncio
async def test_the_bulk_selection_is_absent_in_loading_with_the_actions_own_sentence(config_dir,
                                                                                    clean_registry):
    """LOADING is the ONE state the bulk removal refuses: no bar, no boxes, and the ACTION's own
    sentence (never the move pair's offline-only one)."""
    app = build(config_dir, offline_fleet(status=Status.LOADING), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert f'action="{BULK_DIALOG}"' not in page
    assert "data-sel-box" not in page, "no selection box in LOADING"
    assert 'data-sel-bar' not in page, "no bar in LOADING"
    assert actions_page.MISSION_DELETE_LOADING_REFUSAL in page, "the action's own refusal sentence"
    assert f'action="{REORDER}"' not in page, "the move pair is offline-only"


# ------------------------------------------------------------------- 4. the gate and the wiring

def test_the_console_offline_sentence_matches_the_actions():
    """One rule, one wording: the console's own copy must equal the plugin's."""
    import plugins.mission.actions as mission_actions

    assert actions_page.MISSION_LIST_OFFLINE_ONLY == mission_actions.MISSION_LIST_OFFLINE_ONLY


def test_the_console_offline_states_are_the_file_write_states():
    """The console gates on the Status NAMES the action refuses outside of."""
    assert set(actions_page.MISSION_LIST_OFFLINE_STATES) == {"UNREGISTERED", "SHUTDOWN",
                                                             "SHUTTING_DOWN"}


def test_the_console_bulk_sentence_matches_the_actions():
    """One rule, one wording: the console's own copy of the bulk-remove refusal equals the plugin's."""
    assert actions_page.MISSION_DELETE_LOADING_REFUSAL == \
        mission_actions.MISSION_DELETE_LOADING_REFUSAL


def test_the_console_bulk_gate_is_every_state_but_loading():
    assert "LOADING" not in actions_page.MISSIONS_BULK_DELETE_STATES
    assert set(actions_page.MISSIONS_BULK_DELETE_STATES) == {
        "UNREGISTERED", "SHUTDOWN", "SHUTTING_DOWN", "STOPPED", "PAUSED", "RUNNING"}


def test_the_bulk_remove_declares_the_new_gate():
    by_key = {action.key: action for action in actions_page.MISSION_ACTIONS}
    assert set(by_key["mission_delete_bulk"].statuses) == \
        set(actions_page.MISSIONS_BULK_DELETE_STATES)


@pytest.mark.asyncio
async def test_both_new_routes_are_registered_under_their_capabilities(config_dir, clean_registry):
    app = build(config_dir, offline_fleet(), plugins=("mission",))
    registrar = app.state.webui_registrar
    assert registrar.capability_for(path=REORDER) == actions_page.MISSIONS_REORDER_CAPABILITY
    assert registrar.capability_for(path=BULK) == actions_page.MISSIONS_DELETE_CAPABILITY
    assert registrar.capability_for(path=BULK_DIALOG) == actions_page.MISSIONS_DELETE_CAPABILITY
