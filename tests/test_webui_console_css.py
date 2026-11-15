"""CSS pins for the console: the no-wrap log, the one scrollbar widget, and no dead tile CSS.

Read out of ``static/shell.css`` as text: ``white-space: pre`` keeps one log line on one line; exactly
ONE scrollbar mechanism is declared per engine (the standard pair only inside ``@supports not
selector(::-webkit-scrollbar)``, so it is never overridden, and the WebKit rules cover both axes from
one geometry variable); and the retired ``.kpi`` block is asserted ABSENT rather than merely unused."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SHEET = (Path(__file__).resolve().parent.parent
         / "services" / "webservice" / "static" / "shell.css")

#: the properties only the standard (non-WebKit) scrollbar mechanism understands, and which
#: Chromium would therefore prefer over the ``::-webkit-*`` rules if they were declared at all
STANDARD_PAIR = ("scrollbar-width", "scrollbar-color")

#: the engine gate the fix uses. `selector(::-webkit-scrollbar)` is true in Chromium/Safari and
#: false in Firefox, so `not …` addresses exactly the engine that has no WebKit pair.
GATE = "@supports not selector(::-webkit-scrollbar)"


def _selector_pattern(selector: str) -> str:
    """A regex for *selector* that treats CSS-insignificant whitespace as insignificant.

    Whitespace between the selectors of a ``:is()`` list (after a comma) and around a block's ``{``
    does not change the rule a browser applies, so pinning one spelling of the file is a test that
    breaks on reformatting and then gets "fixed" by weakening it. This normalises both sides: a comma
    allows whitespace around it, any other whitespace run in the selector becomes ``\\s+``, and the
    caller may write either the tight or the spaced form.
    """
    parts = []
    for char in selector:
        if char == ",":
            parts.append(r"\s*,\s*")
        elif char.isspace():
            parts.append(r"\s+")
        else:
            parts.append(re.escape(char))
    return "".join(parts)



@pytest.fixture(scope="module")
def sheet() -> str:
    """The stylesheet with CSS comments removed.

    Comments are stripped on purpose: a rule is what a browser parses, and a comment that *names* a
    property (the scrollbar block explains the defect by quoting `scrollbar-width`/`scrollbar-color`)
    must not be read as a declaration by the assertions below — nor, when the tiles are retired,
    must the sentence explaining WHY the `.kpi` block is gone make its own absence test fail.
    """
    return re.sub(r"/\*.*?\*/", "", SHEET.read_text(encoding="utf-8"), flags=re.S)


def _rule(sheet: str, selector: str) -> str:
    """The body of the ``selector { … }`` block — the one the scrollbar/log pins read.

    Fails loudly when the rule is missing entirely, so a renamed selector is a named failure
    rather than an empty-string assertion that passes.
    """
    match = re.search(_selector_pattern(selector) + r"\s*\{([^}]*)\}", sheet)
    assert match, f"the stylesheet declares no `{selector}` rule"
    return match.group(1)


def _gate_body(sheet: str) -> str:
    """The body of the ``@supports`` block (one nesting level of braces)."""
    match = re.search(_selector_pattern(GATE), sheet)
    assert match, "the @supports engine gate is gone"
    start = match.end()
    depth = 0
    for index in range(sheet.index("{", start), len(sheet)):
        if sheet[index] == "{":
            depth += 1
        elif sheet[index] == "}":
            depth -= 1
            if depth == 0:
                return sheet[sheet.index("{", start) + 1:index]
    raise AssertionError("the @supports gate block is not closed")


# ---------------------------------------------------------------- 1. the log does not wrap

def test_the_log_ships_the_no_wrap_rule(sheet):
    """One log line stays one line: ``white-space: pre``, never a wrapping value."""
    body = _rule(sheet, ".log")
    declared = re.search(r"white-space:\s*([a-z-]+)", body)
    assert declared, "`.log` declares no white-space: the no-wrap rule is gone"
    assert declared.group(1) == "pre", (
        "`.log` must be white-space: pre (one line = one line); "
        f"it is now `{declared.group(1)}`")


# ---------------------------------------------------------------- 2. one scrollbar, both axes

def test_the_standard_scrollbar_pair_is_declared_only_inside_the_engine_gate(sheet):
    """The standard pair away from Chromium — nowhere but the ``@supports`` block.

    This is the assertion that keeps the two bars the same widget: declared once outside the gate
    (as the old sheet did, next to the WebKit rules) Chromium ignores every ``::-webkit-*`` rule
    below and paints the platform scrollbar on each axis independently.
    """
    gate = _gate_body(sheet)
    for prop in STANDARD_PAIR:
        assert prop in gate, f"the gate block no longer declares `{prop}` for Firefox"
        outside = re.sub(re.escape(gate), "", sheet, count=1)
        # count the *declarations*, not the doc comment mentions
        declarations = re.findall(rf"(?<![\w-]){prop}\s*:", outside)
        assert declarations == [], (
            f"`{prop}` is declared {len(declarations)}x OUTSIDE the engine gate — in Chromium the "
            "standard pair wins and undoes the WebKit scrollbar rules")


def test_the_webkit_scrollbar_rules_cover_both_axes_from_one_variable(sheet):
    """Chromium/Safari get the WebKit pair: one width/height variable, track, thumb, corner, no
    steppers — the properties that make the vertical and the horizontal bar identical.

    The same rules cover every scrolling reader the sheet paints: the log, `.sbar` and the
    completion popup's list (`ul.CodeMirror-hints`, a body-level element the addon builds).
    """
    gutter = _rule(sheet, ":is(.log,.sbar,ul.CodeMirror-hints)::-webkit-scrollbar")
    width = re.search(r"width:\s*([^;]+);", gutter)
    height = re.search(r"height:\s*([^;]+);", gutter)
    assert width and height, "the WebKit scrollbar rule no longer sets both axes"
    assert width.group(1).strip() == height.group(1).strip() == "var(--sbar)", (
        "both axes must read ONE variable (--sbar), not two hand-kept numbers")
    for selector in (":is(.log,.sbar,ul.CodeMirror-hints)::-webkit-scrollbar-track",
                     ":is(.log,.sbar,ul.CodeMirror-hints)::-webkit-scrollbar-thumb",
                     ":is(.log,.sbar,ul.CodeMirror-hints)::-webkit-scrollbar-corner",
                     ":is(.log,.sbar,ul.CodeMirror-hints)::-webkit-scrollbar-button"):
        assert re.search(_selector_pattern(selector) + r"\s*\{", sheet), f"`{selector}` is gone"
    # the scroll region class the tabs' table and the nav ride on is covered by the same rules
    assert re.search(_selector_pattern(":is(.log,.sbar,ul.CodeMirror-hints)"), sheet), (
        "the one-scrollbar selector list is gone")


def test_the_scrollbar_geometry_token_is_declared_once(sheet):
    """``--sbar`` has one owner (``:root``); the two axes are equal by construction."""
    root = _rule(sheet, ":root")
    declared = re.findall(r"--sbar:\s*([^;]+);", root)
    assert declared, "--sbar is not declared in :root"
    assert len(re.findall(r"--sbar\s*:", sheet)) == len(declared), (
        "--sbar is declared outside :root as well — the two axes can drift apart again")


# ---------------------------------------------------------------- 3. the retired tiles leave nothing

def test_the_retired_kpi_tiles_have_no_css_left(sheet):
    """The tiles are gone from the page, so their rules are gone from the sheet (no dead CSS)."""
    assert not re.search(r"(?<![\w-])\.kpi(?![\w-])", sheet), \
        "the .kpi tile block still ships: nothing renders it"


# ---------------------------------------------------------------- 4. the select popup list

def test_the_select_popup_list_is_painted_by_the_sheet(sheet):
    """The ``<option>`` popup is drawn in the SHELL's colours, not the platform's.

    ``color-scheme: dark`` only TINTS the popup; Windows still draws the option list white-on-grey
    unless the stylesheet names the row colours. The rule and its ``:checked``/``:hover`` states must
    exist, and both colours and their states must come from the shell's OWN custom properties — a raw
    colour here would stop following the palette the way every other control does.
    """
    body = _rule(sheet, "option")
    background = re.search(r"background:\s*([^;]+);", body)
    colour = re.search(r"color:\s*([^;]+);", body)
    assert background and "var(--" in background.group(1), \
        "the option popup background must read a shell token"
    assert colour and "var(--" in colour.group(1), \
        "the option popup text colour must read a shell token"
    # the two states the popup draws: the chosen row and the row under the pointer
    states = re.search(_selector_pattern("option:checked, option:hover") + r"\s*\{([^}]*)\}", sheet)
    assert states, "the option popup declares no :checked / :hover state"
    assert re.search(r"background:\s*var\(--", states.group(1)) and \
        re.search(r"color:\s*var\(--", states.group(1)), \
        "the option states must also read shell tokens"


# ---------------------------------------------------------------- 5. the nested group's gutter

def test_a_group_marker_sits_in_the_parents_key_column(sheet):
    """A group's legend and its body share ONE left edge — the parent's key column (item 3).

    The fieldset's own horizontal padding and the legend's pushed a nested group's body right of the
    enclosing key column and its marker right of its own values, so nesting read as a staircase. Both
    are zero now, which is what the rendered-HTML assertion in ``test_webui_configgen`` relies on to
    say the group is one block.
    """
    sec = re.search(r"padding:\s*([^;]+);", _rule(sheet, ".cfg-sec"))
    assert sec, "`.cfg-sec` declares no padding"
    parts = sec.group(1).split()
    # a CSS padding shorthand: 1 value = all four; 3 = top / left+right / bottom; 4 = top/right/bottom/left
    n = len(parts)
    horizontal = parts[0] if n == 1 else (parts[1] if n == 3 else (parts[3] if n == 4 else None))
    assert horizontal == "0", \
        f"`.cfg-sec` must carry NO left/right padding so a group's body aligns with the parent key " \
        f"column; it is `{sec.group(1).strip()}`"
    legend = re.search(r"padding:\s*([^;]+);", _rule(sheet, ".cfg-sec > legend"))
    assert legend and legend.group(1).strip() == "0", \
        f"`.cfg-sec > legend` must carry NO padding so the marker aligns with its own values; it is " \
        f"`{legend.group(1).strip() if legend else None}`"


# ------------------------------------------------- 6. the Expert Mode editor: the palette, not literals

#: The editor's own ground (``.cfg-editor .CodeMirror`` paints ``var(--ground-2)``). Every token colour
#: is measured against THIS, not against the page.
EDITOR_GROUND = "--ground-2"

#: Every YAML token class the vendored yaml mode can emit, and the shell token that must paint it.
#: A rule that names a token but reads NO variable is the defect the operator reported: CodeMirror's light
#: theme ships literals (``.cm-s-default .cm-atom`` is ``#221199`` — the "dark blue") and, loaded from
#: the ``head`` block AFTER shell.css, it used to win the specificity tie and paint the editor.
EDITOR_TOKENS = {
    ".cfg-editor .CodeMirror .cm-keyword, .cfg-editor .CodeMirror .cm-atom": "--link",
    ".cfg-editor .CodeMirror .cm-string": "--run",
    ".cfg-editor .CodeMirror .cm-number": "--accent",
    ".cfg-editor .CodeMirror .cm-comment": "--text-3",
    ".cfg-editor .CodeMirror .cm-meta": "--pause",
    ".cfg-editor .CodeMirror .cm-def, .cfg-editor .CodeMirror .cm-variable": "--text",
    ".cfg-editor .CodeMirror .cm-error": "--danger",
}

#: WCAG AA for normal-size text — the bar a syntax colour has to clear on the editor's dark ground.
AA_NORMAL = 4.5


def _hex(text: str) -> tuple:
    """The ``#rrggbb``/``#rgb`` literal as an (r, g, b) triple."""
    text = text.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    assert len(text) == 6, f"not a 6-digit hex colour: {text!r}"
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _root_var(sheet: str, name: str) -> str:
    """The literal value of a custom property declared in ``:root`` (the palette's one owner)."""
    found = re.search(rf"{re.escape(name)}:\s*([^;]+);", _rule(sheet, ":root"))
    assert found, f"`{name}` is not declared in :root"
    return found.group(1).strip()


def _luminance(rgb: tuple) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple, b: tuple) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_editor_token_colours_are_palette_variables_not_literals(sheet):
    """Every syntax colour reads a ``var(--…)`` token, never a hard-coded colour.

    A literal here is what stops the editor following the palette — and, worse, is exactly how the
    vendored light theme's unreadable "dark blue" gets back in.
    """
    for selector, token in EDITOR_TOKENS.items():
        body = _rule(sheet, selector)
        declared = re.search(r"color:\s*([^;]+);", body)
        assert declared, f"`{selector}` declares no color"
        value = declared.group(1).strip()
        assert value == f"var({token})", \
            f"`{selector}` must read `var({token})`; it is `{value}`"


def test_the_editor_token_rules_out_specify_the_vendored_light_theme(sheet):
    """Each token rule carries the ``.CodeMirror`` step, so it beats ``.cm-s-default .cm-*``.

    The vendored codemirror.min.css paints its tokens at specificity 0,2,0 and is pulled in AFTER
    shell.css, so a 0,2,0 rule of ours would LOSE the tie; the extra class makes ours 0,3,0.
    """
    for selector in EDITOR_TOKENS:
        for part in selector.split(","):
            part = part.strip()
            assert ".cfg-editor" in part and ".CodeMirror" in part, \
                f"`{part}` must be scoped `… .cfg-editor .CodeMirror .cm-…` to out-specify the vendored theme"


def test_every_editor_token_is_readable_on_the_editor_ground(sheet):
    """Contrast of each token against the editor's own dark ground, asserted (a colour that is merely
    DIFFERENT from the background is not the same as one that is READABLE)."""
    ground = _hex(_root_var(sheet, EDITOR_GROUND))
    worst = []
    for selector, token in EDITOR_TOKENS.items():
        ratio = _contrast(_hex(_root_var(sheet, token)), ground)
        worst.append((ratio, token, selector))
        assert ratio >= AA_NORMAL, \
            f"`{selector}` ({token}) is {ratio:.2f}:1 on {EDITOR_GROUND} — below AA ({AA_NORMAL})"
    # a written-out record of the measured values, newest run included, so a regression names itself
    worst.sort()
    print("editor token contrast vs %s: %s" % (
        EDITOR_GROUND, ", ".join(f"{tok}={ratio:.2f}" for ratio, tok, _ in worst)))


def test_the_status_line_reserves_a_fixed_height(sheet):
    """The validation line is a FIXED-height block, so a verdict arriving/changing/clearing can never
    move the editor under the operator's caret.

    ``min-height`` alone is the defect: it reserves the FLOOR but lets a wrapped message push the
    editor down a row — so a plain ``height`` must be declared and must be a length, not ``auto``.
    """
    body = _rule(sheet, ".cfg-yaml-status")
    height = re.search(r"(?<!min-)height:\s*([^;]+);", body)
    assert height, "`.cfg-yaml-status` declares no fixed `height`: the editor can reflow under the caret"
    value = height.group(1).strip()
    assert value not in ("auto", "min-content", "max-content", "fit-content") and value != "0", \
        f"`.cfg-yaml-status` height must be a fixed length; it is `{value}`"
