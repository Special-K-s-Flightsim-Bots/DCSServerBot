"""The Missions tab's UX fixes: the dialog the console already has, a real trashcan, and a
decisive add.

The tab's destructive REMOVE and its ADD now go through the console's ONE dialog component
(``templates/confirm.html``) instead of the inline forms M3 shipped:

* **REMOVE** is a dialog with a checkbox for deleting the real file, minting the one-shot confirm
  token its route demands — the same two questions Discord's ``/mission delete`` asks, in one dialog.
  The row's control wears a TRASHCAN glyph added to the glyph set (``_icons.html``), never pasted.
* **ADD** is DECISIVE: the card's button opens the dialog, and the FILE PICKER and the load/autostart
  pair live ON it — choosing a mission and confirming happen in one dialog, never a picker followed by
  a button followed by a question.

Driven through the real app with the real local backend, the real mission actions and the stub
cluster, on the SAME fixtures as ``test_webui_missions_tab`` (whose M3 behaviour this must not
regress: the configured list, the picker's exclusions, the running-mission refusal)."""
from __future__ import annotations

import os

import re
from pathlib import Path
from urllib.parse import urljoin

import pytest

from conftest import back_target

from services.webservice.pages import actions as actions_page
import plugins.mission.actions as mission_actions
from test_webui_missions_tab import (  # noqa: F401 - fixtures imported on purpose
    BASE_DIR, CONFIGURED, CONFIGURED_FILES, CURRENT, SERVER_A, SERVER_B, MissionServer, Cluster,
    configured_fleet, fleet, instance, missions_page,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

DELETE_DIALOG = actions_page.MISSIONS_DELETE_PATH + actions_page.CONFIRM_SUFFIX
ADD_DIALOG = actions_page.MISSIONS_ADD_PATH + actions_page.CONFIRM_SUFFIX

TEMPLATES = Path(actions_page.__file__).parent.parent / "templates"
ICONS = (TEMPLATES / "_icons.html").read_text(encoding="utf-8")
SERVER_DETAIL = (TEMPLATES / "server_detail.html").read_text(encoding="utf-8")
TOKEN_FIELD = actions_page.CONFIRM_FIELD


def glyphs() -> dict:
    """Every glyph the set declares — ``{name: drawing}``, read the way the node-rows pin reads it."""
    return dict(re.findall(r"'([a-z_]+)':\s*'([^']*)'", ICONS))


def form_for(html: str, path: str) -> str:
    """The ONE ``<form>`` that posts to *path* (the opener / the row control)."""
    found = re.findall(rf'<form[^>]*action="{re.escape(path)}"[^>]*>.*?</form>', html, re.S)
    assert found, f"no control posting to {path}"
    return found[0]


async def open_dialog(http, path: str, **fields):
    """POST one mission control's dialog opener and return its response."""
    reply = await http.post(path, data={"server": SERVER_A, "_csrf_token": fields.pop("csrf"), **fields})
    assert reply.status_code == 200, (reply.status_code, reply.text[:300])
    return reply


def one_shot_token(html: str) -> str:
    match = re.search(rf'name="{re.escape(TOKEN_FIELD)}" value="([^"]+)"', html)
    assert match, "the destructive dialog carries no confirm token"
    return match.group(1)


def posted(*pairs):
    """A form body that can carry REPEATED fields, in order (the checkbox and its OFF companion)."""
    body: dict = {}
    for name, value in pairs:
        if name not in body:
            body[name] = value
        elif isinstance(body[name], list):
            body[name].append(value)
        else:
            body[name] = [body[name], value]
    return body


# ------------------------------------------------- 1. the trashcan is gone (M9), and no glyph pasted

def test_the_trash_glyph_is_gone_from_the_set():
    """The trashcan lost its only reader (the per-row Remove), so the drawing is DELETED (M9)."""
    assert "trash" not in glyphs(), "the unused trashcan glyph survives in the set"


def test_the_tab_asks_the_set_and_pastes_nothing():
    """Every glyph on the tab is drawn through ``icons.act(...)`` — no raw SVG pasted, and (M9) no
    trashcan control at all: the per-row delete is gone."""
    assert "icons.act('trash')" not in SERVER_DETAIL, "a trashcan control survived on the tab"
    assert "<svg" not in SERVER_DETAIL, "raw SVG markup was pasted into the tab"


@pytest.mark.asyncio
async def test_the_rendered_tab_has_no_trashcan(config_dir, clean_registry):
    """The rendered Missions tab carries NO trash glyph anywhere — removal is the tick plus the bar."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert "trash" not in page, "a trashcan control survived"
    assert f'action="{actions_page.MISSIONS_DELETE_PATH}"' not in page, "a per-row delete survived"


# ------------------------------------------------------- 2. the delete DIALOG and its two answers

@pytest.mark.asyncio
async def test_the_remove_dialog_names_the_consequence_first_and_carries_the_box(config_dir,
                                                                                clean_registry):
    """The dialog: the mission named, the consequence FIRST, the delete-file box (off by default), a
    one-shot token and the destructive chrome."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await open_dialog(http, DELETE_DIALOG, csrf=token_of(page), mission="2")
    text = dialog.text
    assert "Rotated.miz" in text, "the dialog names the mission it will remove"
    assert "<b>The mission is removed from the rotation list" in text, "the consequence comes first"
 # the box + its OFF companion, OFF first (the last value wins)
    assert 'name="delete_from_disk" value="0"' in text
    assert 'name="delete_from_disk" value="1"' in text
    assert text.index('name="delete_from_disk" value="0"') < \
        text.index('name="delete_from_disk" value="1"')
    assert not re.search(r'name="delete_from_disk" value="1"[^>]*\schecked', text), \
        "deleting the file is NOT the default"
    assert 'name="_confirm_token"' in text, "the destructive action carries its token"
    assert 'class="btn danger"' in text, "the destructive button"
    assert '<span class="tag">confirm</span>' in text
    assert 'value="' in text and re.search(r'name="mission" value="2"', text), "the target rides along"


@pytest.mark.asyncio
async def test_removing_with_the_box_off_only_changes_the_list(config_dir, clean_registry):
    """List-only: the mission leaves the rotation and NO file is touched. The body is what a BROWSER
    sends for a CLEARED box — the OFF companion alone."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await open_dialog(http, DELETE_DIALOG, csrf=token_of(page), mission="2")
        reply = await http.post(actions_page.MISSIONS_DELETE_PATH, data=posted(
            ("server", SERVER_A), ("_csrf_token", token_of(dialog.text)), ("mission", "2"),
            ("delete_from_disk", "0"), (TOKEN_FIELD, one_shot_token(dialog.text))))
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.deletes == [2]
    assert server.node.removed == [], "the box was OFF — no file may be deleted"


@pytest.mark.asyncio
async def test_removing_with_the_box_on_also_deletes_the_file(config_dir, clean_registry):
    """Also-from-disk: the mission leaves the list AND its file(s) are removed. The body is what a
    BROWSER sends for a TICKED box — the OFF companion FIRST, then the box's ON (last wins)."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await open_dialog(http, DELETE_DIALOG, csrf=token_of(page), mission="2")
        reply = await http.post(actions_page.MISSIONS_DELETE_PATH, data=posted(
            ("server", SERVER_A), ("_csrf_token", token_of(dialog.text)), ("mission", "2"),
            ("delete_from_disk", "0"), ("delete_from_disk", "1"),
            (TOKEN_FIELD, one_shot_token(dialog.text))))
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.deletes == [2]
    assert any(path.endswith("Rotated.miz") for path in server.node.removed), server.node.removed


@pytest.mark.asyncio
async def test_the_remove_route_refuses_a_post_that_skipped_its_dialog(config_dir, clean_registry):
    """The token is the control: a POST with no token (or a stale one) is refused and nothing runs."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        no_token = await http.post(actions_page.MISSIONS_DELETE_PATH,
                                   data={"server": SERVER_A, "_csrf_token": token_of(page),
                                         "mission": "2"})
        assert no_token.status_code == 403, no_token.text
 # a token is SPENT by the first POST: the same body a second time is refused too
        dialog = await open_dialog(http, DELETE_DIALOG, csrf=token_of(page), mission="2")
        token = one_shot_token(dialog.text)
        spent = await http.post(actions_page.MISSIONS_DELETE_PATH,
                                data={"server": SERVER_A, "_csrf_token": token_of(dialog.text),
                                      "mission": "2", TOKEN_FIELD: token})
        assert spent.status_code == 303
        replay = await http.post(actions_page.MISSIONS_DELETE_PATH,
                                 data={"server": SERVER_A, "_csrf_token": token_of(dialog.text),
                                       "mission": "2", TOKEN_FIELD: token})
        assert replay.status_code == 403
    assert cluster.servers[0].deletes == [2], "only the first, confirmed write may run"


@pytest.mark.asyncio
async def test_a_running_mission_is_still_refused(config_dir, clean_registry):
    """M3 must not regress: the running mission's removal is still refused — in the DISCORD COMMAND's
    own sentence ("You can't delete the running mission."), so both surfaces read as one rule."""
    cluster = configured_fleet()
    cluster.servers[0].configured = [os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, "Rotated.miz")]
 # the running mission is #1: BOTH the loaded-file read and the action's own pre-check look here
    cluster.servers[0]._current = os.path.join(BASE_DIR, "Training.miz")
    cluster.servers[0].current_mission.filename = os.path.join(BASE_DIR, "Training.miz")
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await open_dialog(http, DELETE_DIALOG, csrf=token_of(page), mission="1")
        reply = await http.post(actions_page.MISSIONS_DELETE_PATH,
                                data={"server": SERVER_A, "_csrf_token": token_of(dialog.text),
                                      "mission": "1", TOKEN_FIELD: one_shot_token(dialog.text)})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert "You can&#39;t delete the running mission." in after or \
           "You can't delete the running mission." in after
    assert cluster.servers[0].deletes == [], "the running mission is never deleted"


# --------------------------------------------------------------- 3. the ADD flow, in one step

@pytest.mark.asyncio
async def test_the_add_dialog_owns_the_picker_and_the_load_choice(config_dir, clean_registry):
    """DECISIVE: the picker and the load pair are ON the dialog, and the picker excludes what is
    already configured. Adding is not destructive, so NO token is minted (the options chrome)."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        assert f'action="{ADD_DIALOG}"' in page, "the add card opens the dialog"
        assert 'name="path"' not in page, "the picker must NOT sit on the card any more"
        dialog = await open_dialog(http, ADD_DIALOG, csrf=token_of(page))
    text = dialog.text
    assert 'name="path"' in text, "the picker is on the dialog"
    assert 'value="New.miz"' in text, "an unconfigured file is offered"
    assert "value=\"Training.miz\"" not in text, "a configured mission is excluded from the picker"
    assert 'name="autostart"' in text and 'name="load"' in text, "the load pair lives here too"
    assert 'name="' + TOKEN_FIELD + '"' not in text, "adding is not destructive — no token"
    assert '<span class="tag">options</span>' in text


@pytest.mark.asyncio
async def test_the_add_flow_adds_the_selected_mission_with_the_load_choice_honoured(config_dir,
                                                                                   clean_registry):
    """One flow: open the dialog, choose a mission, tick a load box, confirm — the action is handed
    the picker's path and the load choice. The body is the BROWSER's: each box's OFF companion then
    its ON (last wins), so the OFF companion of the autostart box cannot read as ON."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await open_dialog(http, ADD_DIALOG, csrf=token_of(page))
        reply = await http.post(actions_page.MISSIONS_ADD_PATH, data=posted(
            ("server", SERVER_A), ("_csrf_token", token_of(dialog.text)), ("path", "New.miz"),
            ("autostart", "0"), ("load", "0"), ("load", "1")))
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert [path for path, _ in server.adds] and server.adds[0][0].endswith("New.miz")
    assert server.adds[0][1] is False, "the 'load at next start' box was left off"
    assert server.loads, "the 'load now' choice rode the same POST and was honoured"


@pytest.mark.asyncio
async def test_the_add_dialog_is_registered_under_the_add_capability(config_dir, clean_registry):
    """Both mission dialogs are real, registered POST routes gated by their OWN write capability."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    registrar = app.state.webui_registrar
    assert registrar.capability_for(path=ADD_DIALOG) == actions_page.MISSIONS_ADD_CAPABILITY
    assert registrar.capability_for(path=DELETE_DIALOG) == actions_page.MISSIONS_DELETE_CAPABILITY


@pytest.mark.asyncio
async def test_a_manager_cannot_open_a_foreign_servers_dialogs(config_dir, clean_registry):
    """Scope still holds through the dialog routes: a hoster's foreign server is a bare refusal."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "hoster")
        page = await missions_page(http, SERVER_A)
        for path in (ADD_DIALOG, DELETE_DIALOG):
            reply = await http.post(path, data={"server": SERVER_B, "_csrf_token": token_of(page)})
            assert reply.status_code == 403, path
            assert SERVER_B not in reply.text, "the refusal must not disclose the target"


# ---------------------------------------------- 4. M5-fix2: the single Remove's stale-index guard
# The per-row trashcan used to post the INDEX ALONE, so a page whose list changed between render and
# POST deleted a DIFFERENT (destructive!) mission. It now carries the row's LOGICAL name beside the
# index — through the confirm dialog, which posts the write — and the delete route checks it via the
# SAME ``_stale_mission_refusal`` / ``MISSION_LIST_CHANGED`` path the load and the two list writes use.

def mission_name_of(html: str) -> str:
    """The ONE ``mission_name`` value a form posts (``"<index>=<logical name>"``)."""
    match = re.search(r'name="mission_name" value="([^"]+)"', html)
    assert match, "the form carries no logical name beside the index"
    return match.group(1)


def test_the_selection_form_carries_every_rows_logical_name():
    """The SELECTION form posts each row's LOGICAL name with its index — the stale-page cross-check the
    bulk route runs (M9: the companion moved from the row's delete to the selection)."""
    form = form_for(SERVER_DETAIL, "{{ missions_delete_bulk_dialog }}")
    assert 'name="{{ missions_name_field }}" value="{{ m.index }}={{ m.name }}"' in form, \
        "the selection form must carry the per-row logical name beside the index"


@pytest.mark.asyncio
async def test_the_selection_form_posts_every_rows_logical_name(config_dir, clean_registry):
    """Rendered: the selection form carries one ``mission_name`` per row (``index=name``)."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        form = form_for(page, actions_page.MISSIONS_BULK_DELETE_PATH + actions_page.CONFIRM_SUFFIX)
    assert 'name="mission_name" value="1=Training.miz"' in form
    assert 'name="mission_name" value="2=Rotated.miz"' in form


@pytest.mark.asyncio
async def test_the_single_remove_dialog_forwards_the_logical_name(config_dir, clean_registry):
    """The single-remove dialog — its tab control is gone (M9), but the route remains — carries a
    posted ``mission_name`` forward to the delete route, so the stale-page guard still has it."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await open_dialog(http, DELETE_DIALOG, csrf=token_of(page), mission="2",
                                   mission_name="2=Rotated.miz")
    assert mission_name_of(dialog.text) == "2=Rotated.miz", \
        "the dialog must post the row's logical name on to the delete route"


@pytest.mark.asyncio
async def test_a_stale_single_remove_is_refused_and_deletes_nothing(config_dir, clean_registry):
    """The list changes between render and POST: the single remove is refused with the console's ONE
    sentence, and the mission it would have hit is STILL in the list."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await open_dialog(http, DELETE_DIALOG, csrf=token_of(page), mission="2",
                                   mission_name="2=Rotated.miz")
        forwarded = mission_name_of(dialog.text)
 # someone adds a mission at the FRONT while the dialog sits open
        cluster.servers[0].configured.insert(0, os.path.join(BASE_DIR, "New.miz"))
        reply = await http.post(actions_page.MISSIONS_DELETE_PATH, data=posted(
            ("server", SERVER_A), ("_csrf_token", token_of(dialog.text)), ("mission", "2"),
            ("mission_name", forwarded), ("delete_from_disk", "0"),
            (TOKEN_FIELD, one_shot_token(dialog.text))))
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert mission_actions.MISSION_LIST_CHANGED in after
    assert cluster.servers[0].deletes == [], "a stale row deletes nothing"
    assert any(path.endswith("Rotated.miz") for path in cluster.servers[0].configured), \
        "the mission the stale row would have hit is still in the list"


@pytest.mark.asyncio
@pytest.mark.parametrize("disk", ["0", "1"])
async def test_a_fresh_single_remove_still_deletes_exactly_the_clicked_mission(config_dir,
                                                                              clean_registry, disk):
    """The name carried beside the index matches: the remove proceeds and hits EXACTLY the clicked
    mission (index 2 — Rotated), with and without the delete-from-disk box."""
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await open_dialog(http, DELETE_DIALOG, csrf=token_of(page), mission="2",
                                   mission_name="2=Rotated.miz")
        reply = await http.post(actions_page.MISSIONS_DELETE_PATH, data=posted(
            ("server", SERVER_A), ("_csrf_token", token_of(dialog.text)), ("mission", "2"),
            ("mission_name", mission_name_of(dialog.text)), ("delete_from_disk", disk),
            (TOKEN_FIELD, one_shot_token(dialog.text))))
        assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.deletes == [2], "exactly the clicked mission, by its 1-based index"
    if disk == "1":
        assert any(path.endswith("Rotated.miz") for path in server.node.removed), server.node.removed
    else:
        assert server.node.removed == [], "the box was OFF — no file may be deleted"



# ── the dialogs WAIT for their write, and their return survives a proxy sub-path ──

MISSIONS_TAB = f"/fleet/{SERVER_A}?tab=missions"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,action,fields", [
    (DELETE_DIALOG, actions_page.MISSIONS_DELETE_PATH, {"mission": "2"}),
    (ADD_DIALOG, actions_page.MISSIONS_ADD_PATH, {}),
])
async def test_a_mission_dialog_waits_for_its_write_and_returns_relatively(
        config_dir, clean_registry, path, action, fields):
    """``data-await`` on the dialog, and a return reference the browser resolves itself.

    The tab a mission dialog returns to is the Missions tab, and its ROW SET is exactly what the write
    changed — a dialog that navigated on the press would paint the list as it stood BEFORE the add or
    the removal. The reference is RELATIVE to the dialog, so the sub-path the console was served under
    survives the navigation: resolving it against a prefixed dialog URL stays under that prefix.
    """
    cluster = configured_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await open_dialog(http, path, csrf=token_of(page), **fields)

    form = form_for(dialog.text, action)
    assert "data-await" in form, "the dialog must read the page it returns to AFTER its write"
    assert f'data-back="{MISSIONS_TAB}"' not in form, "an absolute return drops a proxy prefix"
    assert back_target(dialog.text, path) == MISSIONS_TAB

    prefix = "/serverbot"
    relative = re.search(r'data-back="([^"]*)"', form).group(1)
    assert urljoin(prefix + path, relative) == prefix + MISSIONS_TAB, \
        "the return must stay under the sub-path the console is served on"
