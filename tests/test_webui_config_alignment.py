"""Servers -> Configuration: ONE right edge for every control, and a WIDER Description.

the operator (looking at the tab): "I want the checkboxes and text fields to be aligned to the right. The
'Description' field can also be wider as people should add a bunch of text in there."

TWO THINGS ARE PINNED HERE, and one of them is a rule, not a pixel count:

* ONE RIGHT EDGE. Every control of the tab — a checkbox, a text input, a number input, a select and the
  multi-line Description — lives in the SAME control column (``.st-w``), which is the shared row's LAST
  grid track. Being the last track is what puts every control on the SAME right edge: the row's right
  edge, which is the panel/block's own content edge. A control narrower than the column is right-anchored
  INSIDE it by ``.st-w { justify-content: flex-end }`` — never floated in the middle — so no dead band is
  left on the right. (Live measurement of the rendered tab: every control's right edge equals the form's
  right edge minus the block's 13px padding at 761-2560px, in light and dark, English and German.)
* WIDER DESCRIPTION. The one multi-line row carries the ``cfg-wide`` marker: its control column is capped
  at 620px instead of the shared 286px, so the textarea is genuinely larger (measured 620px vs 286px at
  1920px, and 515px vs 286px at 1500px where the label keeps its 220px floor). It still ends on the SAME
  right edge, because the wider track is STILL the last track.

SCOPE (b): the change is LOCAL to the Configuration tab. The row macro (``_editor.field_row``) is
UNCHANGED — ``cfg-wide`` is passed ONLY by the description row in ``server_detail.html`` — and both new
rules are scoped to ``.cfg-form .st-row.cfg-wide``, which the schema-driven editors never emit. So the
node/settings pages render exactly as before (asserted below by the marker appearing nowhere else).

CSS can't be laid out by pytest, so the layout GUARANTEE is asserted at the rule level (the class/rule
that makes the right edge shared and the control column wider), and the markup that carries it is
asserted from the real render.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from test_webui_config_editor import _config_rows  # noqa: F401 - reused row splitter
from test_webui_server_config import (  # noqa: F401 - fixtures imported on purpose
    BASE_SETTINGS, _namespace_server, app_with, config_dir, get,
)

STATIC = Path(REPO_ROOT) / "services" / "webservice" / "static"
SHELL_CSS = (STATIC / "shell.css").read_text(encoding="utf-8")
SCOPE_CSS = (STATIC / "settings-scope.css").read_text(encoding="utf-8")

#: the shared row's control column width (`settings-scope.css`)
DEFAULT_CTL_PX = 286


def _rule(css: str, selector: str) -> str:
    """The declaration block of the top-level rule whose selector IS *selector*.

    Anchored at the start of a line (MULTILINE) and to the whole selector, so a rule that merely
    CONTAINS the selector as a descendant (``.st-tile .st-row { ... }``) is never mistaken for it.
    """
    match = re.search(r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.MULTILINE)
    assert match, f"no top-level rule found for {selector!r}"
    return match.group(1)


def _media_760_blocks(css: str) -> list[str]:
    """Every ``@media (max-width: 760px) { ... }`` body (none of them nest braces)."""
    return re.findall(r"@media \(max-width:\s*760px\)\s*\{([^}]*)\}", css)


# ----------------------------------------------------- 1. the shared right edge (the rule that makes it)

def test_the_control_column_is_the_rows_last_track():
    """``.st-row`` is a two-track grid whose SECOND (last) track is the control column.

    The LAST track reaches the grid container's right edge, so putting the control column there is what
    gives every row ONE right edge — the row's (and the panel's) own right edge. If a future edit moved
    the control column to the first track, controls would sit on the LEFT and this pin fails.
    """
    block = _rule(SCOPE_CSS, ".st-row")
    assert re.search(r"grid-template-columns:\s*minmax\(0,\s*1fr\)\s+286px", block), \
        "the control column must be the row's LAST (rightmost) track"
    assert "display: grid" in block, "the shared row is the two-column grid"


def test_a_narrow_control_is_right_anchored_inside_its_column():
    """``.st-w`` right-anchors its control, so a control that does not fill the column still ends on the
    row's right edge instead of floating in the middle and leaving a dead band."""
    block = _rule(SCOPE_CSS, ".st-w")
    assert "justify-content: flex-end" in block, \
        "a control narrower than its column must be right-anchored inside it"


# ------------------------------------------------- 2. the wider Description (rule + floor, scoped)

def test_the_description_row_gets_a_wider_control_column_sharing_the_same_right_edge():
    """``cfg-wide`` widens ONLY the control column (capped at 620px), keeping it the LAST track."""
    block = _rule(SHELL_CSS, ".cfg-form .st-row.cfg-wide")
    match = re.search(r"grid-template-columns:\s*minmax\([^)]*\)\s+minmax\(0,\s*(\d+)px\)", block)
    assert match, "the description row must widen its (last) control track"
    wider = int(match.group(1))
    assert wider > DEFAULT_CTL_PX, \
        f"the description control column ({wider}px) must be wider than the shared {DEFAULT_CTL_PX}px"
    # ... and the label column keeps a FLOOR, so it cannot collapse to 0px on a tight row
    assert re.search(r"minmax\(\d+px,\s*1fr\)", block), \
        "the label column must keep a minimum width, or it collapses on a narrow row"


def test_the_narrow_breakpoint_still_stacks_the_wider_row():
    """The description's wider rule OUT-SPECIFIES the bare stack, so the breakpoint must re-state it."""
    stacking = [b for b in _media_760_blocks(SHELL_CSS) if ".cfg-form .st-row" in b]
    assert stacking, "the 760px stacking rule for the config form is gone"
    block = stacking[0]
    assert re.search(r"\.cfg-form \.st-row(?:\.cfg-wide)?\s*,\s*\.cfg-form \.st-row\.cfg-wide|"
                     r"\.cfg-form \.st-row\.cfg-wide\s*,\s*\.cfg-form \.st-row", block), \
        "the breakpoint must stack the WIDER row too, or it stays two-column on a narrow screen"
    assert "grid-template-columns: minmax(0, 1fr)" in block, "the stack collapses to ONE track"


# ----------------------------------------------------------- 3. the markup: controls in `.st-w`

@pytest.mark.asyncio
async def test_every_kind_of_control_shares_the_one_control_column(config_dir):
    """A bool, a text input, a select and the Description all put their control in ``.st-w``."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    page = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    rows = _config_rows(page)

    def row_for(key: str) -> str:
        found = [html for _attrs, html in rows if f'name="{key}"' in html]
        assert len(found) == 1, f"expected exactly one row for {key!r}, found {len(found)}"
        return found[0]

    def ctl_of(row_html: str) -> str:
        """: the control column's markup, i.e. ``.st-w`` to the row's end."""
        return row_html[row_html.index('<div class="st-w">'):]

    # a bool row's CHECKBOX is inside the control column (never in the label column)
    bool_row = row_for("isPublic")
    assert '<input type="checkbox"' in ctl_of(bool_row), \
        "the bool toggle must sit in the control column, not the label column"
    assert '<input type="checkbox"' not in bool_row.split('<div class="st-w">')[0], \
        "the bool toggle must not remain in the fact column"

    # a text input row
    assert '<input type="text"' in ctl_of(row_for("name"))
    # a number input row
    assert 'type="number"' in ctl_of(row_for("maxPlayers"))
    # a select row (the channels face renders a select)
    assert "<select" in ctl_of(row_for("channels.admin"))


@pytest.mark.asyncio
async def test_the_description_is_the_only_wider_row_and_is_a_multiline_control(config_dir):
    """``cfg-wide`` rides ONLY the multi-line Description, so every other surface is untouched."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    page = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    rows = _config_rows(page)

    def open_tag(html: str) -> str:
        return html[:html.index(">") + 1]

    wider = [(attrs, html) for attrs, html in rows if "cfg-wide" in open_tag(html)]
    assert len(wider) == 1, f"exactly one row may be wider; found {len(wider)}"
    attrs, html = wider[0]
    assert "cfg-wide" in open_tag(html), "the wider marker rides the row's own class list"
    assert 'name="description"' in html, "the wider row is the Description"
    assert "<textarea" in html[html.index('<div class="st-w">'):], \
        "the Description is the multi-line control in the control column"
    # ... and no OTHER row carries the marker: the wider column is scoped to the Description alone
    for _attrs, other in rows:
        if other != html:
            assert "cfg-wide" not in open_tag(other)


def test_cfg_wide_is_emitted_by_the_configuration_template_alone():
    """SCOPE (b) made checkable: only ``server_detail.html`` passes the ``cfg-wide`` class."""
    templates = (Path(REPO_ROOT) / "services" / "webservice" / "templates")
    emitters = [p.name for p in templates.glob("*.html") if "cfg-wide" in p.read_text(encoding="utf-8")]
    assert emitters == ["server_detail.html"], \
        f"cfg-wide must be emitted by the Configuration tab only, found {emitters}"
