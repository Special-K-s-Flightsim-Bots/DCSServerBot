"""The node row's DCS control: a drawing of its own, and the amber mark while an update waits.

The control (``update_dcs``) and its dialog are declared with the row's DCS work; this module pins the
three things that make it READABLE on the row:

* its GLYPH — a set member the strip can resolve, drawn with the set's ``currentColor``/``stroke 2``
  rules, and a drawing no neighbour wears (``upgrade`` is an arrow out of a tray, ``restart`` a ring);
* the ``attention`` MARK — the row's DCS button carries it exactly while the CACHE says a newer build
  exists, and the amber chip beside the node's name says the same thing from the same record, so the
  glyph and the chip cannot disagree;
* the CSS — ``.act-btn.attention`` built from the EXISTING amber tokens (no literal, both themes), and
  the neighbouring controls' ``danger``/``busy`` classes unchanged.

Every pin reads the RENDERED row (through the real app, the real templates and the real read models)
except the two that read the glyph set and the stylesheet as text.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.webservice import dcs_update
from services.webservice.pages import actions as actions_page
from services.webservice.pages import nodes as nodes_page
from test_webui_node_dcs_update import (  # noqa: F401 - fixtures imported on purpose
    CONFIRM, DCS_PATH, DcsNode, Fleet, NODE, OTHER, app_with, config_dir, fleet,
)
from test_webui_write_pause import client, open_page, sign_in

WEBSERVICE = Path(actions_page.__file__).parent.parent
ICONS = (WEBSERVICE / "templates" / "_icons.html").read_text(encoding="utf-8")
SHELL = (WEBSERVICE / "static" / "shell.css").read_text(encoding="utf-8")

NODES = nodes_page.NODES_PATH
#: the control's form action: the ROW always posts to the DIALOG, never to the action
DCS_FORM = DCS_PATH + CONFIRM
RESTART_FORM = "/actions/node/restart" + CONFIRM
SHUTDOWN_FORM = "/actions/node/shutdown" + CONFIRM

#: the cached facts every render below stands in for: one installed build, one newer one.
INSTALLED = "2.9.8.110"
LATEST = "2.9.9.200"
BRANCH = "release"


# ------------------------------------------------------------------------------- reading the page

def glyphs() -> dict[str, str]:
    """Every glyph the set declares — ``{name: drawing}`` (the set's own one-line-per-glyph shape)."""
    return dict(re.findall(r"'([a-z_]+)':\s*'([^']*)'", ICONS))


def row_of(html: str, node_name: str) -> str:
    """The rendered ``<tr>`` of *node_name* — the row is what a person sees, so pins read it."""
    for row in re.findall(r"<tr>.*?</tr>", html, re.S):
        if f'name="node" value="{node_name}"' in row:
            return row
    raise AssertionError(f"the page rendered no row with a control for {node_name}")


def form_markup(row: str, path: str) -> str:
    """The ``<form>`` a control renders, with its button inside — the one that posts to *path*."""
    match = re.search(r'<form method="post" action="' + re.escape(path) + r'"[^>]*>.*?</form>',
                      row, re.S)
    assert match, f"the row rendered no form posting to {path}"
    return match.group(0)


def button_classes(row: str, path: str) -> list[str]:
    """The class LIST the control's button actually wears, as a browser would split it."""
    match = re.search(r'<button class="([^"]*)"', form_markup(row, path))
    assert match, f"the form for {path} carries no button"
    return match.group(1).split()


def chip_of(row: str) -> tuple[str, str]:
    """The DCS chip the row prints beside the node's name, its classes included."""
    match = re.search(r'<span class="([^"]*)" data-dcs-chip>([^<]*)</span>', row)
    assert match, "the row carries no DCS chip"
    return match.group(1), match.group(2)


def seed(node_name: str, *, available: bool | None, version: str = INSTALLED,
         latest: str = "") -> None:
    """Stand in for the background poller: ONE cached answer, written before the render."""
    dcs_update.record(node_name, has_dcs=True, branch=BRANCH, version=version,
                      latest=latest, available=available)


async def render(config_dir, cluster) -> str:
    """The standalone NODES page, rendered by the real application."""
    app = app_with(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        return await open_page(http, NODES)


# ----------------------------------------------------------------------------- 1. the glyph

def test_the_dcs_control_names_a_drawing_the_set_carries_and_no_neighbour_wears():
    """A name the set does not carry renders an EMPTY ``<svg>`` — a blank button, silently.

    The one drawing per operation rule, read on THIS glyph: it is not ``upgrade`` (an arrow out of a
    tray), not ``download`` (an arrow into the same tray), not ``restart`` (a ring), and not the
    node's maintenance pair.
    """
    known = glyphs()
    assert "dcs_update" in known, "the DCS control's glyph is not in the set"
    drawing = known["dcs_update"].strip()
    assert drawing, "the DCS control's drawing is empty"
    for neighbour in ("upgrade", "download", "restart", "shutdown", "online", "offline",
                      "start", "startup"):
        assert drawing != known[neighbour], f"the drawing is {neighbour}'s"
    shared = [name for name, other in known.items() if other == drawing and name != "dcs_update"]
    assert shared == [], f"another glyph wears the DCS drawing: {shared}"


def test_the_drawing_carries_no_colour_of_its_own():
    """The set's rule: colour and stroke come from the macro (``currentColor``, ``stroke 2``)."""
    drawing = glyphs()["dcs_update"]
    for forbidden in ("#", "rgb", "currentColor", "fill=", "stroke=", " stroke", "width="):
        assert forbidden not in drawing, f"the drawing carries {forbidden!r} of its own"


def test_the_glyph_set_test_no_longer_exempts_this_control():
    """The deferral that let the control ship with a blank glyph is GONE, not merely unused."""
    import test_webui_glyph_set as glyph_set

    assert not hasattr(glyph_set, "GLYPH_DEFERRED"), \
        "the glyph set test still exempts a control from the drawing rule"


@pytest.mark.asyncio
async def test_the_rendered_control_draws_it_with_the_sets_stroke_rules(config_dir, fleet):
    """The markup a browser receives: the macro's ``<svg>``, with THIS drawing inside it."""
    seed(NODE, available=True, latest=LATEST)
    html = await render(config_dir, fleet.declare(NODE).cluster())
    markup = form_markup(row_of(html, NODE), DCS_FORM)
    assert 'class="ic24"' in markup
    assert 'viewBox="0 0 24 24"' in markup
    assert 'stroke="currentColor"' in markup and 'stroke-width="2"' in markup
    assert glyphs()["dcs_update"] in markup, "the button renders the macro but not this drawing"


# ------------------------------------------------------- 2. the mark, read off the rendered row

class DeafNode(DcsNode):
    """A node the RENDER must never ask: every DCS call raises, and is counted as a call."""

    ASKED: list[str] = []

    async def get_dcs_branch_and_version(self):
        type(self).ASKED.append("get_dcs_branch_and_version")
        raise AssertionError("a render asked the node for its DCS branch and version")

    async def is_dcs_update_available(self):
        type(self).ASKED.append("is_dcs_update_available")
        raise AssertionError("a render asked the node whether an update is available")

    async def get_available_dcs_versions(self, branch):
        type(self).ASKED.append("get_available_dcs_versions")
        raise AssertionError("a render asked the node for its version list")

    async def dcs_update(self, **kwargs):
        type(self).ASKED.append("dcs_update")
        raise AssertionError("a render ran the updater")


@pytest.fixture
def asked():
    """The DCS calls the node recorded — empty when nothing asked it anything."""
    DeafNode.ASKED = []
    try:
        yield DeafNode.ASKED
    finally:
        DeafNode.ASKED = []


@pytest.mark.asyncio
async def test_a_node_with_an_update_waiting_wears_the_amber_mark(config_dir, fleet, asked):
    """``available is True`` in the cache → the row's DCS button carries ``attention``."""
    seed(NODE, available=True, latest=LATEST)
    html = await render(config_dir, fleet.declare(NODE).cluster(node=DeafNode(NODE, master=True)))
    classes = button_classes(row_of(html, NODE), DCS_FORM)
    print("\n[waiting] " + " ".join(classes))
    assert "attention" in classes, classes
    assert classes[0] == "act-btn"
    assert asked == [], "the render asked the node"


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", ["current", "unknown"])
async def test_a_node_with_nothing_waiting_wears_no_mark(config_dir, fleet, asked, cached):
    """``available is False`` (current) and UNKNOWN both render the plain button — no amber."""
    if cached == "current":
        seed(NODE, available=False, latest=INSTALLED)
    else:
        dcs_update.reset()          # nothing checked yet: no record at all
    html = await render(config_dir, fleet.declare(NODE).cluster(node=DeafNode(NODE, master=True)))
    classes = button_classes(row_of(html, NODE), DCS_FORM)
    print(f"\n[{cached}] " + " ".join(classes))
    assert "attention" not in classes, classes
    assert classes[0] == "act-btn"
    assert "attention" not in html, "the page rendered an attention class somewhere"
    assert asked == [], "the render asked the node"


@pytest.mark.asyncio
async def test_the_glyph_and_the_chip_read_one_cached_answer(config_dir, fleet, asked):
    """Two nodes, two cached answers: the amber button and the amber chip travel together."""
    seed(NODE, available=True, latest=LATEST)
    seed(OTHER, available=False, latest=INSTALLED)
    cluster = fleet.declare(NODE, OTHER).cluster(node=DeafNode(NODE, master=True),
                                                 other=DeafNode(OTHER))
    html = await render(config_dir, cluster)

    waiting = row_of(html, NODE)
    current = row_of(html, OTHER)
    assert "attention" in button_classes(waiting, DCS_FORM)
    assert "attention" not in button_classes(current, DCS_FORM)
    assert chip_of(waiting) == ("tag a", f"DCS {INSTALLED} → {LATEST}")
    assert chip_of(current) == ("tag", f"DCS {INSTALLED} @ {BRANCH}")
    assert asked == []


@pytest.mark.asyncio
async def test_the_mark_is_on_the_dcs_control_alone(config_dir, fleet, asked):
    """The row's neighbours keep their own classes: one amber glyph, never a run of them."""
    seed(NODE, available=True, latest=LATEST)
    html = await render(config_dir, fleet.declare(NODE).cluster(node=DeafNode(NODE, master=True)))
    row = row_of(html, NODE)
    for path in (RESTART_FORM, SHUTDOWN_FORM):
        classes = button_classes(row, path)
        assert classes == ["act-btn", "danger"], f"{path} renders {classes}"
    assert asked == []


# ------------------------------------------------------------------- 3. the flag and the class

def test_the_flag_is_a_parameter_of_the_control_builder_not_a_declaration_field():
    """``attention`` is a per-ROW fact: a declaration field would wear amber on every row."""
    action = next(a for a in actions_page.NODE_ACTIONS if a.key == actions_page.DCS_UPDATE_KEY)
    assert not hasattr(action, "attention"), "the declaration carries a static attention field"

    default = actions_page._node_control(action, NODE, "token", "/nodes")
    assert default["attention"] is False
    marked = actions_page._node_control(action, NODE, "token", "/nodes", attention=True)
    assert marked["attention"] is True


def test_every_server_control_builder_returns_the_flag_false():
    """No server row has a "something waits" fact, so the flag is carried and stays False."""
    action = actions_page.SERVER_ACTIONS[0]
    record = actions_page._control(action, "SRS-1", "token", "/fleet")
    assert record["attention"] is False
    assert record["danger"] == action.danger
    busy = actions_page._control(action, "SRS-1", "token", "/fleet", busy=True)
    assert busy["attention"] is False and busy["busy"] is True


def _attention_rules() -> list[tuple[str, str]]:
    """Every rule whose selector names ``.act-btn.attention`` — ``(selector, body)``."""
    sheet = re.sub(r"/\*.*?\*/", "", SHELL, flags=re.S)
    return [(m.group(1).strip(), m.group(2))
            for m in re.finditer(r"([^{}]*\.act-btn\.attention[^{}]*)\{([^}]*)\}", sheet)]


def test_the_mark_is_built_from_the_existing_amber_tokens_and_no_literal():
    """No new hex, no new token: the ``--warn-*`` family the chip already uses, in both themes."""
    rules = _attention_rules()
    assert rules, "the stylesheet declares no `.act-btn.attention` rule"
    selectors = [selector for selector, _ in rules]
    # the strip's own quiet-at-rest rule sets background/border-color, so the mark needs that level
    assert any(selector.split(",")[0].strip() ==
               "td.acts .act-strip > form > .act-btn.attention" for selector in selectors), selectors
    assert any(selector.split(",")[0].strip() ==
               "td.acts .act-strip > form > .act-btn.attention:hover"
               for selector in selectors), selectors
    # ONE block each: a second copy would silently decide the cascade by its position in the file
    assert len(selectors) == 2, f"the mark is declared {len(selectors)} times: {selectors}"

    declared = set(re.findall(r"(--[\w-]+)\s*:", re.sub(r"/\*.*?\*/", "", SHELL, flags=re.S)))
    for _, body in rules:
        assert "#" not in body and "rgb" not in body, f"a literal in the attention rule: {body}"
        used = set(re.findall(r"var\((--[\w-]+)\)", body))
        assert used, f"the attention rule paints nothing tokenised: {body}"
        assert used <= {"--warn-text", "--warn-line", "--warn-bg"}, used
        assert used <= declared, used - declared


# ------------------------------------------------------------------------ 4. the render evidence

@pytest.mark.asyncio
async def test_the_two_rendered_button_class_strings(config_dir, fleet, asked):
    """The evidence, printed: the SAME control, with and without something waiting."""
    seed(NODE, available=True, latest=LATEST)
    cluster = fleet.declare(NODE).cluster(node=DeafNode(NODE, master=True))
    waiting = button_classes(row_of(await render(config_dir, cluster), NODE), DCS_FORM)

    dcs_update.reset()
    seed(NODE, available=False, latest=INSTALLED)
    current = button_classes(row_of(await render(config_dir, cluster), NODE), DCS_FORM)

    print(f"\nwaiting: {' '.join(waiting)}\ncurrent: {' '.join(current)}")
    assert waiting != current
    assert "attention" in waiting and "attention" not in current
    assert asked == []
