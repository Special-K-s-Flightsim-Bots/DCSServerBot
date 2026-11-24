"""The Missions tab's reorder affordance: a visible grip, and no up/down pair.

The list is reorderable by DRAG only, so the row must LOOK draggable: a ``⠿`` grip in its own
column, before the selection box, as the row's ``data-reorder-handle``. The glyph pair the list used
to draw is gone — one order control per row, never two — and the route the drop posts to is the one
the pair posted to.

Every pin drives the LAYER that owns the fact:

* the rendered tab (a real request through the application, the mission plugin's action layer and
  the stub cluster) for the grip, the column grid and the absence of the pair;
* the offline gate for the absence of BOTH the drag and the grip;
* ``_icons.html`` and the templates for the glyph the pair used to spend;
* the SHIPPED ``static/reorder.js`` booted in a DOM stub by ``tests/reorder_harness.js``, over the
  attributes and row ids the rendered page produced — one drop, one POST, the whole order.

RED before GREEN: every pin fails on the state that preceded it (the handle on the name line, no
grip column, the pair still drawn, the two glyphs still in the set).
"""
from __future__ import annotations

import os

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from core import Status
from services.webservice.pages import actions as actions_page
from test_webui_missions_tab import (  # noqa: F401 - fixtures imported on purpose
    BASE_DIR, Cluster, MissionServer, SERVER_A, SERVER_B, instance,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in,
)

CURRENT = os.path.join(BASE_DIR, ".dcssb", "Training.miz")
FILES = [
    (os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, ".dcssb", "Training.miz")),
    (os.path.join(BASE_DIR, "Rotated.miz"), os.path.join(BASE_DIR, "Rotated.miz")),
    (os.path.join(BASE_DIR, "Backup.miz"), os.path.join(BASE_DIR, ".dcssb", "Backup.miz.orig")),
]
CONFIGURED = [os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, "Rotated.miz"), os.path.join(BASE_DIR, "Backup.miz")]
NAMES = ["Training.miz", "Rotated.miz", "Backup.miz"]

REORDER = actions_page.MISSIONS_REORDER_PATH
ORDER_FIELD = actions_page.MISSION_ORDER_FIELD
CSRF_FIELD = "_csrf_token"

WEB = Path(actions_page.__file__).parent.parent
TEMPLATES = WEB / "templates"
STATIC = WEB / "static"
SERVER_DETAIL = (TEMPLATES / "server_detail.html").read_text(encoding="utf-8")
ICONS = (TEMPLATES / "_icons.html").read_text(encoding="utf-8")
SHELL_CSS = (STATIC / "shell.css").read_text(encoding="utf-8")
REORDER_JS = STATIC / "reorder.js"
HARNESS = Path(__file__).parent / "reorder_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")

#: the row container's opening tag, as the template draws it
CONTAINER = re.compile(r"<div class=\"cfg-form\" data-reorder\b[^>]*>", re.S)


def offline_fleet(status=Status.SHUTDOWN):
    a = MissionServer(SERVER_A, ["Host A"], files=FILES, current=CURRENT, start=1,
                      configured=CONFIGURED, status=status)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                                instance("Host-B-Instance", b)])


async def missions_page(http, server=SERVER_A):
    response = await http.get(f"/fleet/{server}?tab=missions")
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


def container_attributes(html: str) -> dict:
    """The rendered row container's own attributes — what the script reads on load.

    A ``data-reorder`` / ``data-reorder-body`` flag carries NO value, so the scan must accept a bare
    name as well as a quoted one.
    """
    match = CONTAINER.search(html)
    assert match, "the missions list carries no data-reorder container"
    return {found.group(1): (found.group(2) if found.group(2) is not None else "")
            for found in re.finditer(r"(data-[\w-]+)(?:=\"([^\"]*)\")?", match.group(0))}


def rows_of(html: str) -> list:
    """The rendered rows, each as the slice of markup its own element opens."""
    marks = [m.start() for m in re.finditer(r"<div class=\"st-row[^\"]*\"[^>]*data-reorder-row", html)]
    assert marks, "no row carries data-reorder-row"
    return [html[start:end] for start, end in zip(marks, marks[1:] + [len(html)])]


# ------------------------------------------------------------- 1. the grip the row must show

@pytest.mark.asyncio
async def test_every_row_carries_a_focusable_grip_in_its_own_column(config_dir, clean_registry):
    """Offline, each row opens with a visible ⠿ that is the handle, a tab stop, and says what it does."""
    app = build(config_dir, offline_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)

    assert page.count('class="grip"') == len(NAMES), "one grip per mission row"
    assert page.count('data-reorder-handle tabindex="0"') == len(NAMES), \
        "the grip is the row's keyboard route, so it is a tab stop"
    assert page.count("⠿") == len(NAMES), "the grip draws the music playlist's glyph"
    assert page.count('title="Drag to reorder"') == len(NAMES), "the grip says what it does"
    assert "Drag to reorder, or press Alt+Up or Alt+Down" in page, \
        "the grip's accessible name states the keyboard route"


@pytest.mark.asyncio
async def test_the_grip_owns_its_column_and_the_name_stays_beside_the_box(config_dir,
                                                                          clean_registry):
    """The grip is a COLUMN before the box: the name never lands under it, nor under the box."""
    app = build(config_dir, offline_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)

    assert 'class="nm" data-reorder-handle' not in page, \
        "the name line is no longer the handle"
    assert re.search(r"grid-template-columns:16px( 22px)? minmax\(0, 1fr\)", page), \
        "the row's grid is grip (16px), then the box, then the fact column"

    for row in rows_of(page):
        grip = row.index('class="grip"')
        box = row.index('class="m-box"') if 'class="m-box"' in row else len(row)
        name = row.index("<b>")
        assert grip < box < name, "the grip, then the box, then the name — in that order"

    assert ".m-row .grip" in SHELL_CSS, "the grip's own rule ships with the sheet"


# --------------------------------------------------------- 2. the pair is gone, the route is not

@pytest.mark.asyncio
async def test_the_rendered_tab_draws_no_up_or_down_control(config_dir, clean_registry):
    """ONE order control per row: with the grip carrying the drag, the pair is not drawn at all."""
    app = build(config_dir, offline_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)

    assert f'action="{REORDER}"' not in page, "a move form is still rendered"
    assert 'value="up"' not in page and 'value="down"' not in page, "the pair's direction is posted"
    assert "in the rotation list" not in page, "a move title survives"
    for path in re.findall(r"'move_(?:up|down)': '([^']*)'", ICONS):
        assert path not in page, "a move glyph is still drawn"


@pytest.mark.asyncio
async def test_the_drop_posts_the_whole_order_to_the_pairs_route(config_dir, clean_registry):
    """The gate and the route are unchanged: the rendered container points at the reorder path."""
    app = build(config_dir, offline_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        attrs = container_attributes(page)
        assert attrs["data-reorder-url"] == REORDER, "the drop posts where the pair posted"
        assert attrs["data-reorder-field"] == ORDER_FIELD
        assert attrs["data-reorder-token"] and attrs["data-reorder-token-field"] == CSRF_FIELD
        assert "data-reorder-body" in attrs

        reply = await http.post(REORDER, data={
            "server": SERVER_A, CSRF_FIELD: attrs["data-reorder-token"], ORDER_FIELD:
            [os.path.join(BASE_DIR, "Backup.miz"), os.path.join(BASE_DIR, "Training.miz"), os.path.join(BASE_DIR, "Rotated.miz")]})
        assert reply.json()["ok"] is True, reply.text


# --------------------------------------------------------------------- 3. the offline gate

@pytest.mark.asyncio
async def test_the_offline_gate_suppresses_the_drag_and_the_grip(config_dir, clean_registry):
    """With the process up there is no grip, no row hook, no script — and the reason is stated."""
    app = build(config_dir, offline_fleet(status=Status.RUNNING), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)

    assert 'class="grip"' not in page and "⠿" not in page, "a grip is drawn while the drag is off"
    assert "data-reorder-handle" not in page
    assert "data-reorder-row" not in page and "data-reorder-body" not in page
    assert "/static/reorder.js" not in page
    assert actions_page.MISSION_LIST_OFFLINE_ONLY in page, "the reason is stated"


# ------------------------------------------------------------------ 4. one drawing per control

def test_the_move_glyphs_are_gone_from_the_set_and_no_surface_asks_for_them():
    """The pair had ONE reader; with the pair removed the drawings go with it.

    The presets wizard's combination rows still carry ORDER links, but they draw an arrow as TEXT
    (``↑`` / ``↓``) off page-context names — they never asked this set for a drawing, so the pair's
    removal costs them nothing. Only a ``icons.act(...)`` call is an ask on the set.
    """
    assert "move_up" not in ICONS and "move_down" not in ICONS, "a glyph or a comment still names them"
    asked = [path.name for path in TEMPLATES.glob("*.html")
             if re.search(r"icons\.\w+\(\s*['\"]move_(?:up|down)['\"]",
                          path.read_text(encoding="utf-8"))]
    assert asked == [], f"a template still asks the set for the pair: {asked}"
    wizard = (TEMPLATES / "presets_wizard.html").read_text(encoding="utf-8")
    assert "icons.act" not in wizard, "the wizard's order links draw a glyph after all"


# ------------------------------------------------------- 5. the shipped script, on this markup

@requires_node
@pytest.mark.asyncio
async def test_the_shipped_script_enhances_the_rendered_list_and_posts_one_order(
        config_dir, clean_registry, tmp_path):
    """A drop on the rendered container is ONE fetch carrying the WHOLE new order, under its field."""
    app = build(config_dir, offline_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)

    attrs = {key: value for key, value in container_attributes(page).items()
             if key.startswith("data-reorder")}
    shape = tmp_path / "missions-container.json"
    shape.write_text(json.dumps({"attributes": attrs, "ids": NAMES}), encoding="utf-8")

    done = subprocess.run(["node", str(HARNESS), str(REORDER_JS), str(shape)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)["container"]

    assert report["handled_by_the_script"], "the rendered container opts in with data-reorder alone"
    assert "data-reorder-glyphs" not in report["attributes_rendered"]

    keyboard = report["keyboard"]
    assert keyboard["requests"] == 1, "one gesture is one write"
    assert keyboard["url"] == REORDER and keyboard["method"] == "POST"
    assert keyboard["order"] == ["Rotated.miz", "Training.miz", "Backup.miz"], \
        "the body carries the WHOLE new order, one value per row"
    assert keyboard["order"] == keyboard["dom"]
    assert keyboard["token"] and keyboard["token"][0] != "", "the drop carries the CSRF token"

    drop = report["drop"]
    assert drop["requests"] == 1, "a pointer drop writes once, not once per row"
    assert drop["order"] == drop["dom"] == ["Rotated.miz", "Training.miz", "Backup.miz"]
