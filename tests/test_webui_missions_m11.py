"""M11 through the console: Set as start into the bar, the add dialog's footer, and where the add lands.

the operator, testing the M9/M10 build:

* *"Instead of having a 'Set as Start' with every mission, why can I not set the start mission by
  ticking the checkbox and having a 'Set as Start' at the top like with 'Remove selected'? Only if one
  is selected, the button would work."* — the per-row control moved into the selection bar and acts on
  the TICK (exactly one), while ``Load`` stays a per-row control.
* *"The 'Cancel' button is still misaligned in the 'Add a mission...' modal. Why not put it left of the
  'Add to the rotation'?"* — the add dialog's picker/load options moved into the BODY, so the footer is
  exactly one cancel and one primary.
* *"Also, if I press that modal, it redirects me to the Overview page and not back to the Missions."* —
  this pins where the add (and the upload) land.

Driven through the real app with the real local backend, the real mission actions and the stub cluster,
on the SAME fixtures the sibling mission tests use.
"""
from __future__ import annotations

import re

import pytest
from conftest import back_target

from core import Status
from services.webservice.pages import actions as actions_page
import plugins.mission.actions as mission_actions
from test_webui_missions_m9 import three_missions  # noqa: F401 - fixture builder reused on purpose
from test_webui_missions_tab import (  # noqa: F401
    BASE_DIR, CONFIGURED, CONFIGURED_FILES, CURRENT, MissionServer, SERVER_A, SERVER_B, instance,
)
from test_webui_write_pause import (  # noqa: F401
    build, clean_registry, client, config_dir, sign_in, token_of,
)

ACTIVATE = actions_page.MISSIONS_ACTIVATE_PATH
ADD_DIALOG = actions_page.MISSIONS_ADD_PATH + actions_page.CONFIRM_SUFFIX
UPLOAD = actions_page.MISSIONS_UPLOAD_PATH
MISSIONS_TAB = f"/fleet/{SERVER_A}?tab=missions"

#: the whole-row stale-page map the selection form posts (one ``index=logical name`` per row), exactly
#: as ``server_detail.html`` renders it — the bar's start route reads the CHOSEN index's entry.
NAME_MAP = ["1=Training.miz", "2=Rotated.miz", "3=Backup.miz"]


def configured(status=Status.RUNNING):
    """Server A with the 'configured' three-mission list of the sibling tests, plus server B."""
    a = MissionServer(SERVER_A, ["Host A"], files=CONFIGURED_FILES, current=CURRENT, start=1,
                      configured=CONFIGURED, status=status)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    from test_webui_missions_tab import Cluster
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                                instance("Host-B-Instance", b)])


async def missions_page(http, server=SERVER_A):
    response = await http.get(f"/fleet/{server}?tab=missions")
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


# ------------------------------------------- 1. the bar's Set as start acts on the tick

def test_the_start_refusal_is_one_named_constant():
    """The selection rule is the console's own sentence, named so the route, the template and the tests
    cannot drift — and it names the ONE requirement (exactly one)."""
    assert actions_page.MISSIONS_ACTIVATE_SELECTION_REFUSAL == \
        "Select exactly one mission to set as the start mission."


@pytest.mark.asyncio
async def test_one_ticked_mission_becomes_the_start(config_dir, clean_registry):
    """Exactly one ticked: the bar's form posts the whole selection (all rows' names, ONE ticked box)
    to the activate route and the chosen index is set through the bot's method."""
    cluster = three_missions()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(ACTIVATE, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": ["2"],
            "mission_name": NAME_MAP})
    assert reply.status_code == 303, (reply.status_code, reply.text)
    assert reply.headers["location"] == MISSIONS_TAB, "the write must land back on the Missions tab"
    assert cluster.servers[0].indexes == [2], "the start index is set through the bot's method"


@pytest.mark.asyncio
async def test_the_routes_name_check_reads_the_chosen_index_not_the_last_row(config_dir, clean_registry):
    """The bar posts the stale-page map for EVERY row. The route must take the CHOSEN index's name — a
    last-row name would make a fresh activation of row 1 read as stale. Row 1 (the last in the map is
    row 3's name) succeeds, so the chosen entry is the one used."""
    cluster = configured()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(ACTIVATE, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": ["1"],
            "mission_name": NAME_MAP})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert mission_actions.MISSION_LIST_CHANGED not in after, \
        "the chosen row's own name must be used, not the last row's"
    assert cluster.servers[0].indexes == [1]


@pytest.mark.asyncio
async def test_no_selection_is_refused_with_the_console_sentence(config_dir, clean_registry):
    """NONE ticked (the no-JavaScript submit of an empty selection): the route refuses in its OWN
    sentence, stored as the notice the operator reads on the Missions tab, and nothing is written."""
    cluster = three_missions()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(ACTIVATE, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission_name": NAME_MAP})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        assert reply.headers["location"] == MISSIONS_TAB
        after = await missions_page(http)
    assert actions_page.MISSIONS_ACTIVATE_SELECTION_REFUSAL in after
    assert cluster.servers[0].indexes == [], "an empty selection activates nothing"


@pytest.mark.asyncio
async def test_several_ticked_missions_are_refused_with_the_console_sentence(config_dir,
                                                                            clean_registry):
    """SEVERAL ticked: the tick names no single mission, so the route refuses in the SAME sentence and
    nothing is written — the answer the client's disable can never be the only guard for."""
    cluster = three_missions()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(ACTIVATE, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": ["2", "3"],
            "mission_name": NAME_MAP})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert actions_page.MISSIONS_ACTIVATE_SELECTION_REFUSAL in after
    assert cluster.servers[0].indexes == [], "several selected activates nothing"


@pytest.mark.asyncio
async def test_the_running_state_is_still_refused_by_the_action(config_dir, clean_registry):
    """The per-state gate is UNCHANGED (M7): the bar offers the button in every state but LOADING, and a
    LOADING POST meets the action's own sentence — never a second copy of the rule."""
    cluster = three_missions(status=Status.LOADING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(ACTIVATE, data={
            "server": SERVER_A, "_csrf_token": token_of(page), "mission": ["2"],
            "mission_name": NAME_MAP})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert actions_page.MISSION_ACTIVE_LOADING_REFUSAL in after, \
        "the omitted bar must state the ACTIVATE action's own sentence in LOADING"
    assert "data-sel-bar" not in after, "the selection is omitted in LOADING, never drawn dead"
    assert cluster.servers[0].indexes == []


@pytest.mark.asyncio
async def test_the_rendered_bar_button_carries_the_routes_reason(config_dir, clean_registry):
    """ONE wording: the ``data-sel-why`` the bar renders is the route's own refusal constant, so the
    client's count reason and the server's answer are the same sentence."""
    app = build(config_dir, three_missions(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    tag = re.search(r"<button[^>]*data-sel-start[^>]*>", page)
    assert tag is not None, "the bar's Set as start button is missing"
    assert f'data-sel-why="{actions_page.MISSIONS_ACTIVATE_SELECTION_REFUSAL}"' in tag.group(0)


# ------------------------------------------- 2. the add dialog's footer is one cancel + one primary

@pytest.mark.asyncio
async def test_the_add_dialog_options_live_in_the_body(config_dir, clean_registry):
    """The picker and the load pair sit in the dialog BODY (form-associated to ``dlg-form``), so the
    footer's action form carries no option field ahead of the primary button — the shape, not spacing,
    is the fix."""
    app = build(config_dir, three_missions(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await http.post(ADD_DIALOG, data={"server": SERVER_A, "_csrf_token": token_of(page)})
    assert dialog.status_code == 200, dialog.text
    text = dialog.text

    body = text[text.index('<div class="bd">'):text.index('<div class="ft">')]
    footer = text[text.index('<div class="ft">'):]
    # the picker + the two load options are in the BODY, form-associated to the footer's form
    assert 'name="path"' in body, "the picker left the dialog"
    assert 'name="autostart"' in body and 'name="load"' in body, "the load pair left the dialog"
    assert body.count('form="dlg-form"') >= 3, "the body fields must be form-associated to dlg-form"
    # ... and NONE of them is in the footer, so the footer row is [Cancel] [spacer] [primary]
    assert 'name="path"' not in footer
    assert 'name="autostart"' not in footer and 'name="load"' not in footer
    assert footer.count(">Cancel</a>") == 1, "exactly one cancel"
    assert footer.count(">Add to the rotation</button>") == 1, "exactly one primary"


# ------------------------------------------- 3. where the add (and the upload) land

@pytest.mark.asyncio
async def test_the_add_dialog_returns_to_the_missions_tab_on_both_paths(config_dir, clean_registry):
    """Its ``back`` is the Missions tab, so the JavaScript interceptor (which navigates to ``data-back``)
    and the plain form (which follows the 303) end on the SAME page — never the server page's Overview
    (which is what a bare ``/fleet/<name>`` would be)."""
    cluster = three_missions()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await http.post(ADD_DIALOG, data={"server": SERVER_A, "_csrf_token": token_of(page)})
        assert dialog.status_code == 200, dialog.text
        text = dialog.text
        # the JS path
        assert back_target(text, ADD_DIALOG) == MISSIONS_TAB, \
            "the dialog's return must resolve back to the Missions tab"
        # the no-JS path
        reply = await http.post(actions_page.MISSIONS_ADD_PATH, data={
            "server": SERVER_A, "_csrf_token": token_of(text), "path": "New.miz",
            "autostart": "0", "load": "0"})
    assert reply.status_code == 303, (reply.status_code, reply.text)
    assert reply.headers["location"] == MISSIONS_TAB
    assert reply.headers["location"] != f"/fleet/{SERVER_A}", "the write must not land on the Overview"


@pytest.mark.asyncio
async def test_the_upload_write_lands_on_the_missions_tab(config_dir, clean_registry):
    """The upload is a direct write (no dialog), and its 303 lands on the Missions tab too — the same
    guarantee as the add, pinned in the same pass."""
    cluster = three_missions()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.post(UPLOAD, data={"server": SERVER_A, "_csrf_token": token_of(page)},
                                files={"file": ("Inbound.miz", b"MIZ-BYTES", "application/octet-stream")})
    assert reply.status_code == 303, (reply.status_code, reply.text)
    assert reply.headers["location"] == MISSIONS_TAB
