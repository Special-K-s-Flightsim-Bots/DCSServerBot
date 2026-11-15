"""M9 through the console: one list, the ticks, the START mission, and the one reused dialog.

The Missions tab's interface, rebuilt to the designer's proposal with the operator's rulings applied:

* the LIST is the selection — a box per row belongs to ONE selection form; the card header is the
  selection bar (a count, a Clear link and the one count-bearing action);
* the START marker (amber) names the mission the server loads when it STARTs; the live dot names the
  running mission — two different facts;
* the per-row TRASHCAN is gone (the tick plus the bar is the only path), and the two row writes stay
  as two LABELLED controls — ``Set as start`` (the rotation pointer) and ``Load`` (load now);
* the bulk-REMOVE dialog is the console's ONE dialog, REUSED: the selection was made in the list, so
  the dialog shows it READ-ONLY (the new ``list`` input shape) beside the disk box, with one primary
  and one cancel in the footer.

Driven through the real app with the real local backend, the real mission actions and the stub
cluster, on the SAME fixtures the sibling mission tests use.
"""
from __future__ import annotations

import os

import json
import re
import shutil
from pathlib import Path

import pytest

from conftest import run_node_harness
from core import Status
from services.webservice.pages import actions as actions_page
from test_webui_missions_reorder import BASE_DIR, CONFIGURED, CURRENT, FILES  # noqa: F401
from test_webui_missions_tab import (  # noqa: F401
    Cluster, MissionServer, SERVER_A, SERVER_B, instance,
)
from test_webui_write_pause import (  # noqa: F401
    build, clean_registry, client, config_dir, sign_in, token_of,
)

BULK = actions_page.MISSIONS_BULK_DELETE_PATH
BULK_DIALOG = BULK + actions_page.CONFIRM_SUFFIX
TOKEN_FIELD = actions_page.CONFIRM_FIELD

TEMPLATES = Path(actions_page.__file__).parent.parent / "templates"


def three_missions(status=Status.RUNNING):
    """Server A with three configured missions (row 1 the running AND start one), plus server B."""
    a = MissionServer(SERVER_A, ["Host A"], files=FILES, current=CURRENT, start=1,
                      configured=CONFIGURED, status=status)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                                instance("Host-B-Instance", b)])


def split_marks(status=Status.RUNNING):
    """Server A where the START row and the running row DIFFER: ``listStartIndex``=2 (row 2, Rotated)

    while the loaded mission is Training (row 1). Review point 3 wants the two marks proven to sit on
    two different rows, which the ``start=current=1`` fixtures cannot show.
    """
    a = MissionServer(SERVER_A, ["Host A"], files=FILES, current=CURRENT, start=2,
                      configured=CONFIGURED, status=status)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                                instance("Host-B-Instance", b)])


def mission_row_blocks(page: str) -> list[str]:
    """The page's mission rows, split on the row marker — only blocks that name a mission file.

    The add/upload cards carry their own ``.st-row`` chrome, so the mission rows are the blocks that
    hold a ``<b>name.miz</b>``.
    """
    return [block for block in page.split('<div class="st-row')[1:] if ".miz</b>" in block]


async def missions_page(http, server=SERVER_A):
    response = await http.get(f"/fleet/{server}?tab=missions")
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


# --------------------------------------------------------------- 1. the bar is the card header

@pytest.mark.asyncio
async def test_the_card_header_is_the_selection_bar(config_dir, clean_registry):
    """The whole 'Remove several missions' card is gone; the header carries the bar — a select-all box,
    the live count, a Clear link and the ONE count-bearing action."""
    app = build(config_dir, three_missions(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)

    assert 'class="sel-bar" data-sel-bar' in page, "the card header is not the selection bar"
    assert "Remove several missions" not in page, "the second (bulk) card survives"
    assert "data-sel-all" in page, "no select-all box"
    assert "data-sel-count>0 of 3 selected" in page, \
        "the count must start at 0 of ALL rows (the running row is selectable too)"
    assert 'href="/fleet/SRS-1?tab=missions">Clear</a>' in page, "the Clear link is gone"
    go = re.search(r"<button[^>]*data-sel-go[^>]*>(.*?)</button>", page)
    assert go is not None, "the selection bar's action button is gone"
    assert "Remove selected" in go.group(1), "the count-bearing label's default is gone"
    # THE NO-JS PATH (M9-fix): the button ships ENABLED and the ONE selection form carries the
    # server, the CSRF and the row boxes' own field — so ticking and submitting work with JavaScript
    # off; the tab's only removal path can never depend on the script.
    opening_tag = go.group(0).split(">", 1)[0]
    assert "disabled" not in opening_tag, "the bar button must ship ENABLED (the no-JS removal path)"
    assert '<form id="missions-form"' in page, "the selection form is gone"
    assert 'form="missions-form" name="mission" value="1"' in page, \
        "the selection form carries no row box field"
    assert 'name="server" value="SRS-1"' in page, "the form carries no server"
    assert 'name="_csrf_token"' in page, "the form carries no CSRF token"
    assert 'form="missions-form"' in page
    assert "/static/mission-select.js" in page, "the count-bearing enhancement is not loaded"


@pytest.mark.asyncio
async def test_the_running_rows_box_is_tickable_and_says_what_it_may_do(config_dir, clean_registry):
    """EVERY row's box is tickable, the running one included.

    The selection feeds two bar actions and they treat the running row differently: the start write
    accepts it (making the running mission the start mission again after a mistake is legitimate),
    the removal refuses only that row and removes the rest. A disabled box blocked both, so the
    START correction was impossible; the row now states what it can and cannot do instead.
    """
    app = build(config_dir, three_missions(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)

    assert "data-sel-box disabled" not in page, "no row's box may be disabled"
    assert page.count("data-sel-box") == 3, "every row keeps its box"
    assert "The running mission can be set as the start mission, but it cannot be removed." in page, \
        "the running row states no reason"


# ------------------------------------------------------- 2. the START marker and the live mark

@pytest.mark.asyncio
async def test_the_start_marker_is_on_exactly_the_start_row(config_dir, clean_registry):
    """``listStartIndex``=1, so the FIRST row wears the amber START marker and its row treatment —
    and no other row does."""
    app = build(config_dir, three_missions(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)

    assert page.count('<span class="tag a">START</span>') == 1, "the START marker is not unique"
    assert page.count('st-row row-active') == 1, "the marker treatment is not unique"
    # the marked row is the start one (Training, row 1), and it is where the marker sits
    marked = re.search(r'<div class="st-row row-active"[^>]*>.*?</div>\s*</div>', page, re.S).group(0)
    assert "<b>Training.miz</b>" in marked, "the marker is on the wrong row"
    assert '<span class="tag a">START</span>' in marked


@pytest.mark.asyncio
async def test_the_running_row_carries_the_console_live_dot(config_dir, clean_registry):
    """The running mission keeps the console's own live mark — a DIFFERENT fact from the start one."""
    app = build(config_dir, three_missions(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert "<b>Training.miz</b> <span class=\"dot run\" aria-hidden=\"true\"></span>" in page, \
        "the running row's live mark is gone"


@pytest.mark.asyncio
async def test_the_start_mark_and_the_running_dot_sit_on_different_rows(config_dir, clean_registry):
    """THE TWO MARKS CAN DIFFER (M9-fix, review point 3): with ``listStartIndex``=2 the START marker is
    on row 2 (Rotated) while the running dot is on row 1 (Training) — neither mark moves the other, and
    neither carries the other's meaning."""
    app = build(config_dir, split_marks(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)

    assert page.count('<span class="tag a">START</span>') == 1, "the START marker is not unique"
    assert page.count('st-row row-active') == 1, "the marker treatment is not unique"

    rows = mission_row_blocks(page)
    # the page HEAD also carries a status dot (class \"run\" while RUNNING) — only the ROWS matter here
    assert sum(row.count('<span class="dot run"') for row in rows) == 1, "the live dot is not unique"
    marked = [row for row in rows if ">START</span>" in row]
    dotted = [row for row in rows if "dot run" in row]
    assert len(marked) == 1, "exactly one row wears the START marker"
    assert len(dotted) == 1, "exactly one row wears the running dot"
    # the START marker is on the START row (row 2, Rotated) and carries no dot ...
    assert "<b>Rotated.miz</b>" in marked[0], "the START marker is on the wrong row"
    assert marked[0].startswith(' row-active'), "the START row must wear the row treatment"
    assert "dot run" not in marked[0], "the START row must not wear the running dot"
    # ... and the dot is on the RUNNING row (row 1, Training), which wears no START marker
    assert "<b>Training.miz</b>" in dotted[0], "the live dot is on the wrong row"
    assert ">START</span>" not in dotted[0], "the running row must not wear the START marker"
    assert marked[0] != dotted[0], "the two marks must be able to sit on different rows"


# ------------------------------------------------ 3. the bar's start action and the row's Load
#
# M11: the per-row ``Set as start`` was REMOVED and the bar gained it beside
# ``Remove selected…`` — it acts on the TICK and is meaningful with EXACTLY ONE mission ticked. ``Load``
# STAYS a per-row control (a different write: load now, not the rotation pointer). The asymmetry is a
# decision on record, not an accident.

@pytest.mark.asyncio
async def test_set_as_start_is_a_bar_action_and_load_stays_on_the_row(config_dir, clean_registry):
    """The two writes are still two LABELLED controls, but their homes differ now: ``Set as start`` is
    the bar's (it posts the SAME selection form to the activate route via ``formaction``), ``Load`` is
    the row's. Neither is drawn disabled by the server — both ship ENABLED for the no-JS path."""
    app = build(config_dir, three_missions(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)

    # the row no longer opens the activate route; only Load does (the bar's button uses `formaction`,
    # which is NOT a per-row form — check the FORM elements, not the bare substring)
    assert f'<form method="post" action="{actions_page.MISSIONS_ACTIVATE_PATH}"' not in page, \
        "a per-row form still posts the activate write"
    assert page.count(f'<form method="post" action="{actions_page.MISSIONS_LOAD_PATH}'
                       f'{actions_page.CONFIRM_SUFFIX}"') == 3, \
        "the row Load controls are gone"
    assert '>Load</button>' in page
    # exactly ONE Set as start, in the bar, an ENABLED submit of the selection form to the activate route
    assert page.count(">Set as start</button>") == 1, "the bar's Set as start is gone or duplicated"
    start = re.search(r"<button[^>]*data-sel-start[^>]*>Set as start</button>", page)
    assert start is not None
    tag = start.group(0)
    assert 'form="missions-form"' in tag, "the start action must submit the ONE selection form"
    assert f'formaction="{actions_page.MISSIONS_ACTIVATE_PATH}"' in tag, \
        "the start action must post to the activate route"
    assert "disabled" not in tag, "the start button must ship ENABLED (the no-JS path)"
    assert "loads when it starts" in tag, "the title does not say what it sets"


@pytest.mark.asyncio
async def test_the_bar_start_is_offered_while_running_and_absent_in_loading(config_dir, clean_registry):
    """The M7 gate holds through the bar: offered in every state but LOADING — so it works while the
    server runs, and is omitted (never drawn dead) where the action would refuse."""
    app = build(config_dir, three_missions(status=Status.RUNNING), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        running = await missions_page(http)
    app = build(config_dir, three_missions(status=Status.LOADING), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        loading = await missions_page(http)
    assert "data-sel-start" in running, "Set as start must be offered while RUNNING"
    assert "data-sel-start" not in loading, "Set as start must be omitted in LOADING"
    # LOADING omits the whole bar (the select gate) and states the action's OWN sentence instead
    assert "data-sel-bar" not in loading
    assert actions_page.MISSION_DELETE_LOADING_REFUSAL in loading


def test_the_bar_carries_the_routes_selection_refusal_as_its_own_reason():
    """ONE wording: the reason the bar renders (``data-sel-why``) is the route's refusal constant."""
    from pathlib import Path

    detail = (Path(actions_page.__file__).parent / "server_detail.py").read_text(encoding="utf-8")
    assert "missions_activate_why" in detail
    assert "MISSIONS_ACTIVATE_SELECTION_REFUSAL" in detail


@pytest.mark.asyncio
async def test_the_load_title_states_the_start_index_difference_per_state(config_dir, clean_registry):
    """M9-fix (ruling 5): the Load title says what the control does IN THE STATE THE ROW IS RENDERED
    IN — the bot's ``loadMission`` moves ``listStartIndex`` only at STOPPED (and starts the server),
    while RUNNING/PAUSED merely load now and leave the start index alone."""
    app = build(config_dir, three_missions(status=Status.RUNNING), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        running = await missions_page(http)
    app = build(config_dir, three_missions(status=Status.STOPPED), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        stopped = await missions_page(http)

    assert "the start mission is unchanged" in running, \
        "while RUNNING, the Load title must say the start index does not move"
    assert "also becomes the start mission" not in running, \
        "while RUNNING, the Load title must not claim it moves the start index"
    assert "also becomes the start mission and starts the server" in stopped, \
        "at STOPPED, the Load title must say the load becomes the start mission and starts it"
    assert "the start mission is unchanged" not in stopped, \
        "at STOPPED, the Load title must not say the start index is unchanged"


@pytest.mark.asyncio
async def test_the_bar_start_is_drawn_even_when_the_start_row_is_ticked(config_dir, clean_registry):
    """The bar's control is on the SELECTION, not on a row's START state: it is drawn whatever is
    ticked (the running row cannot be ticked anyway), and selecting the mission already START simply
    re-sets it — the action's own answer, never a second client-side rule."""
    app = build(config_dir, three_missions(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert page.count("data-sel-start") == 1, "the bar must carry exactly one start action"


# ------------------------------------------- 4. the ONE dialog, reused, with the read-only list

@pytest.mark.asyncio
async def test_the_remove_dialog_is_a_read_only_list_with_one_primary_and_one_cancel(config_dir,
                                                                                    clean_registry):
    """The selection was made in the list, so the dialog shows it READ-ONLY (the new ``list`` shape)
    beside the disk box, and the footer carries exactly one primary and one cancel."""
    app = build(config_dir, three_missions(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        dialog = await http.post(BULK_DIALOG, data={
            "server": SERVER_A, "_csrf_token": token_of(page),
            "mission": ["2", "3"],
            "mission_name": ["2=Rotated.miz", "3=Backup.miz"]})
    assert dialog.status_code == 200, dialog.text
    text = dialog.text

    assert 'class="dlg-list"' in text, "the read-only selection list is gone"
    assert "<li>Rotated.miz</li>" in text and "<li>Backup.miz</li>" in text
    assert f'name="{TOKEN_FIELD}"' in text, "the destructive action carries no token"
    # the selection is carried forward as hidden fields (the list is presentation, not the write)
    assert 'name="mission" value="2"' in text and 'name="mission" value="3"' in text
    assert not re.search(r'type="checkbox"[^>]*name="mission"', text), \
        "the dialog must not draw the selection as checkboxes"
    # the disk box is in the BODY, form-associated to the footer form
    assert 'form="dlg-form"' in text and '<form id="dlg-form"' in text
    # exactly one primary and one cancel, both in the footer; the list + box are in the body
    assert text.count(">Cancel</a>") == 1
    assert text.count('class="btn danger"') == 1
    footer = text[text.index('class="ft"'):]
    assert "dlg-list" not in footer, "the list must live in the body, not the footer"
    assert "Remove 2 missions" in footer, "the button's label carries the count"


@pytest.mark.asyncio
async def test_the_new_list_shape_leaves_the_shared_dialog_intact(config_dir, clean_registry):
    """confirm.html is ONE component: a dialog that carries no BODY field renders as it always did —
    its own chrome and its fields, with NO form indirection. Startup's options form keeps its
    checkbox; the destructive Restart dialog (no body field at all) carries no ``form="dlg-form"``.

    Startup itself gains a body fieldset (the temporary preset's weather fields), so the no-body
    invariant is pinned on the dialog that genuinely has no body field.
    """
    from test_webui_shutdown_startup_option import STARTUP, fleet as server_fleet, open_dialog
    from test_webui_write_pause import open_page

    app = build(config_dir, server_fleet(status=Status.SHUTDOWN), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply, _token = await open_dialog(http, STARTUP, expect_token=False)
    assert '<span class="tag">options</span>' in reply.text, "the shared options chrome is gone"
    assert 'name="maintenance"' in reply.text, "the option checkbox is gone"

    app = build(config_dir, server_fleet(status=Status.RUNNING), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        text = await open_page(http)
        dialog = await http.post("/actions/server/restart" + actions_page.CONFIRM_SUFFIX,
                                data={"server": "SRS-1", "_csrf_token": token_of(text)})
    assert dialog.status_code == 200, dialog.text
    assert 'form="dlg-form"' not in dialog.text, \
        "a dialog with no body fields must not use the form indirection"


# ------------------------------------------------- 5. the read-only face keeps the same facts

@pytest.mark.asyncio
async def test_the_read_only_face_marks_and_drops_the_copy_tag(config_dir, clean_registry, monkeypatch):
    """A write-less face (no mission write registered) still marks the START row and the running one,
    still shows ``missing``, and shows no copy tag and no selection."""
    monkeypatch.setattr(actions_page, "missions_write_available", lambda: False)
    cluster = three_missions()
    cluster.servers[0]._files = [(os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, "Training.miz"))]
    cluster.servers[0].configured = [os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, "Gone.miz")]
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
    assert '<span class="tag a">START</span>' in page, "the read-only face lost the START marker"
    assert '<span class="dot run"' in page, "the read-only face lost the live mark"
    assert '<span class="tag">missing</span>' in page, "the read-only face lost the `missing` tag"
    assert '<span class="tag">primary</span>' not in page, "the copy tag survives in read-only"
    assert "data-sel-box" not in page, "a write-less face must offer no selection"
    assert "data-sel-bar" not in page, "a write-less face must offer no bar"


# ------------------------------------------- 6. the enhancement, driven against a minimal DOM

MISSION_SELECT_JS = Path(actions_page.__file__).parent.parent / "static" / "mission-select.js"
HARNESS = Path(__file__).resolve().parent / "mission_select_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def selection_bar() -> dict:
    """The shipped script's own answers, booted against a minimal DOM by the harness."""
    assert HARNESS.is_file(), "the harness that drives the shipped script is missing"
    assert MISSION_SELECT_JS.is_file(), "the shipped selection script is missing"
    done = run_node_harness(["node", str(HARNESS), str(MISSION_SELECT_JS)])
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["states"]


@requires_node
def test_the_bar_counts_every_row_and_disables_the_button_when_empty(selection_bar):
    """EVERY row is counted: the running mission's box is tickable too, so the bar's total is the number
    of rows and the button is disabled until something is ticked.
    M11: the same empty selection also disables the START button and the count states the reason."""
    initial = selection_bar["initial"]
    assert initial["count"].startswith("0 of 3 selected"), \
        "the running row is selectable and must be counted"
    assert initial["disabled"] is True, "nothing selected -> the button is disabled"
    assert initial["label"] == "Remove selected…"
    assert initial["start_disabled"] is True, "nothing selected -> Set as start is disabled too"
    assert "Select exactly one mission" in initial["count"], \
        "the count must state why Set as start cannot be used"
    assert initial["boxes"] == [False, False, False], "nothing is ticked to begin with"


@requires_node
def test_the_label_carries_the_count_and_the_select_all_is_tri_state(selection_bar):
    """One box -> singular, two boxes -> plural, and the select-all box is checked / indeterminate to
    match — every box tickable, the running row's included. M11: the START button is enabled for
    EXACTLY one ticked and disabled otherwise, with the count's reason appearing only when it cannot be
    used."""
    one = selection_bar["one"]
    assert one["count"] == "1 of 3 selected", "exactly one ticked -> no start reason"
    assert one["disabled"] is False
    assert one["start_disabled"] is False, "exactly one ticked -> Set as start is live"
    assert one["label"] == "Remove 1 mission…", "singular"
    assert one["indeterminate"] is True, "a partial selection is indeterminate"
    two = selection_bar["two"]
    assert two["label"] == "Remove 2 missions…", "plural"
    assert two["start_disabled"] is True, "several ticked -> Set as start is disabled"
    assert "Select exactly one mission" in two["count"], "several ticked -> the count says why"
    assert two["all"] is False and two["indeterminate"] is True, \
        "two of THREE rows ticked is a partial selection, so the select-all box stays indeterminate"
    # select-all ticks EVERY box, the running row's included
    assert selection_bar["all"]["boxes"] == [True, True, True]
    assert selection_bar["all"]["all"] is True and selection_bar["all"]["indeterminate"] is False
    # and clearing turns the count back to zero and the button back to disabled
    assert selection_bar["cleared"]["boxes"] == [False, False, False]
    assert selection_bar["cleared"]["disabled"] is True
    assert selection_bar["cleared"]["start_disabled"] is True
    assert selection_bar["cleared"]["label"] == "Remove selected…"
    assert "Select exactly one mission" in selection_bar["cleared"]["count"]
