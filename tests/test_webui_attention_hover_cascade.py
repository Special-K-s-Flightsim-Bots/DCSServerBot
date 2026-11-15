"""The row strip's hover palette, resolved through the CASCADE, and the versions URL's real contract.

Two pins with two mechanisms:

* the BUTTON STATES are resolved by a small selector matcher over ``shell.css`` — rule order plus
  specificity decide each declaration, exactly as a browser would, so the pin names the rule that
  WINS instead of the rule that merely exists. The control the operator presses carries ``danger``
  and ``attention`` together, and the amber has to survive the pointer.
* the STRIP STATES (plain, ``danger``, ``busy``, ``more``) are rendered through the shipped
  ``_strip.html`` and their class attributes are pinned verbatim, so a change here cannot quietly
  alter a class list the pages depend on.

The versions route's docstring is pinned as text AND as behaviour: the module is grepped for any
join of the decoded node name into a filesystem path or a shell command.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from services.webservice.pages import actions as actions_page

ROOT = Path(__file__).resolve().parent.parent
SHEET = ROOT / "services" / "webservice" / "static" / "shell.css"
TEMPLATES = ROOT / "services" / "webservice" / "templates"

#: the probe element: the node row's DCS control inside the strip, the only control that can carry
#: BOTH classes (it stops every server on the node, and it also says an update waits).
STRIP_ANCESTORS = (
    {"tag": "form", "classes": set(), "states": set()},
    {"tag": "span", "classes": {"act-strip"}, "states": set()},
    {"tag": "td", "classes": {"acts"}, "states": set()},
)

#: every pseudo-class the matcher understands; any other ``:pseudo`` (``:has()``, ``:is()``,
#: ``::webkit-*``) is treated as NOT matching this probe, which is what keeps unrelated rules out.
KNOWN_PSEUDO = {"hover", "focus-visible", "active", "disabled", "checked"}

DECLARATIONS = ("color", "background", "border-color")


# ----------------------------------------------------------------- the stylesheet, as a browser reads it

def _without_at_blocks(css: str) -> str:
    """*css* with every ``@…{…}`` block removed (balanced braces), comments already stripped.

    ``@keyframes``/``@media``/``@supports`` bodies carry no declarations for the probe element and
    their nested braces would otherwise be read as one flat rule each.
    """
    out = []
    index = 0
    while index < len(css):
        at = css.find("@", index)
        if at == -1:
            out.append(css[index:])
            break
        out.append(css[index:at])
        brace = css.find("{", at)
        if brace == -1:
            break
        depth = 0
        for position in range(brace, len(css)):
            if css[position] == "{":
                depth += 1
            elif css[position] == "}":
                depth -= 1
                if depth == 0:
                    index = position + 1
                    break
        else:
            break
    return "".join(out)


def _declarations(body: str) -> dict[str, str]:
    """The ``property: value`` pairs of a rule body, values normalised to one space."""
    found = {}
    for chunk in body.split(";"):
        if ":" not in chunk:
            continue
        prop, value = chunk.split(":", 1)
        found[prop.strip()] = " ".join(value.split())
    return found


def rules() -> list[tuple[list[str], dict[str, str]]]:
    """Every ``selector list { declarations }`` rule in file order, comments and at-blocks removed."""
    css = re.sub(r"/\*.*?\*/", "", SHEET.read_text(encoding="utf-8"), flags=re.S)
    css = _without_at_blocks(css)
    out = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [part.strip() for part in match.group(1).split(",") if part.strip()]
        out.append((selectors, _declarations(match.group(2))))
    return out


def _compound_matches(compound: str, element: dict) -> bool:
    """Whether one compound selector (``.a.b:hover``) describes *element*."""
    if "#" in compound:
        return False
    rest = re.sub(r"^[A-Za-z][\w-]*", "", compound)
    tag = compound[:len(compound) - len(rest)]
    if tag and tag != (element.get("tag") or ""):
        return False
    for part in re.findall(r"[.:][\w-]+(?:\([^)]*\))?", rest):
        if part.startswith("."):
            if part[1:] not in element["classes"]:
                return False
            continue
        pseudo = part[1:]
        if pseudo not in KNOWN_PSEUDO or pseudo not in element["states"]:
            return False
    return True


def _selector_parts(selector: str) -> tuple[list[str], list[str]]:
    """*selector* as ``(compounds, combinators)`` — ``combinators[i]`` joins compound ``i`` to ``i+1``."""
    tokens = selector.replace(">", " > ").split()
    compounds: list[str] = []
    combinators: list[str] = []
    pending = None
    for token in tokens:
        if token == ">":
            pending = ">"
            continue
        if compounds:
            combinators.append(pending or "descendant")
        compounds.append(token)
        pending = None
    return compounds, combinators


def matches(selector: str, element: dict, ancestors: tuple) -> bool:
    """Whether *selector* matches *element* with *ancestors* (parent first)."""
    compounds, combinators = _selector_parts(selector)
    if not compounds or not _compound_matches(compounds[-1], element):
        return False
    chain = list(ancestors)
    available = 0
    index = len(compounds) - 2
    while index >= 0:
        if combinators[index] == ">":
            if available >= len(chain) or not _compound_matches(compounds[index], chain[available]):
                return False
            available += 1
        else:
            while available < len(chain) and not _compound_matches(compounds[index], chain[available]):
                available += 1
            if available >= len(chain):
                return False
            available += 1
        index -= 1
    return True


def specificity(selector: str) -> tuple[int, int, int]:
    """``(ids, classes, elements)`` for a selector — the browser's sort key."""
    ids = len(re.findall(r"#[\w-]+", selector))
    classes = len(re.findall(r"\.[\w-]+|\[[^\]]*\]|:(?!:)[\w-]+", selector))
    elements = len(re.findall(r"(?:^|[\s>+~])([A-Za-z][\w-]*)|::[\w-]+", selector))
    return ids, classes, elements


def cascade(element: dict, ancestors: tuple) -> dict[str, tuple[str, str]]:
    """``property -> (value, winning selector)`` as the CASCADE resolves it for *element*.

    Specificity first, file order second — the two keys a browser uses when both selectors match.
    """
    won: dict[str, tuple[tuple, str, str]] = {}
    for order, (selectors, declared) in enumerate(rules()):
        for selector in selectors:
            if not matches(selector, element, ancestors):
                continue
            key = (specificity(selector), order)
            for prop, value in declared.items():
                if prop not in won or key > won[prop][0]:
                    won[prop] = (key, value, selector)
    return {prop: (value, selector) for prop, (_, value, selector) in won.items()}


def strip_control(*classes: str) -> dict:
    return {"tag": "button", "classes": {"act-btn", *classes}, "states": set()}


def hovering(element: dict) -> dict:
    return {**element, "states": {*element["states"], "hover"}}


# ----------------------------------------------------------------- the amber survives the pointer

def test_the_control_carrying_both_classes_paints_amber_at_rest_and_on_hover():
    """``danger attention``: amber at rest AND amber under the pointer — the cue never drops out."""
    control = strip_control("attention", "danger")
    rest = cascade(control, STRIP_ANCESTORS)
    under = cascade(hovering(control), STRIP_ANCESTORS)
    assert rest["color"] == ("var(--warn-text)", rest["color"][1]), rest["color"]
    assert rest["border-color"] == ("var(--warn-line)", rest["border-color"][1])
    assert under["color"][0] == "var(--warn-text)", under["color"]
    assert under["border-color"][0] == "var(--warn-line)", under["border-color"]
    assert under["background"][0] == "var(--warn-bg)", under["background"]


def test_a_danger_only_control_keeps_its_neutral_rest_and_red_hover():
    """``danger`` alone: ordinary glyph at rest, the red tint on hover — unchanged by the mark."""
    control = strip_control("danger")
    rest = cascade(control, STRIP_ANCESTORS)
    under = cascade(hovering(control), STRIP_ANCESTORS)
    assert rest["color"][0] == "var(--text)", rest["color"]
    assert rest["border-color"][0] == "transparent", rest["border-color"]
    assert under["color"][0] == "var(--danger-text)", under["color"]
    assert under["border-color"][0] == "var(--bad-line)", under["border-color"]
    assert under["background"][0] == "var(--danger-hover)", under["background"]


def test_the_quiet_at_rest_rule_still_strips_the_chrome():
    """The rule the two marks have to out-specify is itself unchanged: no chrome at rest."""
    rest = cascade(strip_control(), STRIP_ANCESTORS)
    body = " ".join(f"{prop}: {value};" for prop, (value, _) in rest.items())
    assert "background: transparent;" in body, body
    assert "border-color: transparent;" in body, body
    chrome = cascade(hovering(strip_control()), STRIP_ANCESTORS)
    assert chrome["background"][0] == "var(--panel-2)", chrome["background"]
    assert chrome["border-color"][0] == "var(--line-2)", chrome["border-color"]


def test_the_more_trigger_keeps_its_own_quiet_colour():
    """``.act-btn.more`` is the strip's one always-visible trigger: no mark, no danger, muted glyph."""
    more = {"tag": "button", "classes": {"act-btn", "more"}, "states": set()}
    ancestors = ({"tag": "span", "classes": {"act-wrap"}, "states": set()},) + STRIP_ANCESTORS
    assert cascade(more, ancestors)["color"][0] == "var(--text-3)"


# ----------------------------------------------------------------- the strip states, as rendered

def _environment() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True,
                      extensions=["jinja2.ext.i18n"])
    env.install_null_translations()
    env.globals["_"] = lambda text: text
    env.globals["say"] = lambda record: record
    return env


def _control(**overrides) -> dict:
    record = {"csrf_field": "_csrf_token", "csrf_token": "token", "hidden": {}, "path": "/row",
              "title": "A title", "aria": "An aria label", "icon": "pause", "danger": False,
              "direct": True, "attention": False}
    record.update(overrides)
    return record


def render_strip(**strip) -> str:
    record = {"strip": [], "menu": [], "wide": False, "menu_id": "row-menu", "aria": "More"}
    record.update(strip)
    return _environment().get_template("_strip.html").render(strip=record)


def button_classes(html: str) -> list[str]:
    """Every ``<button class="…">`` in *html*, in document order — the rendered class strings."""
    return re.findall(r'<button class="([^"]*)"', html)


STRIP_STATES = {
    "plain": ({"strip": [_control()]}, ["act-btn"]),
    "danger": ({"strip": [_control(danger=True)]}, ["act-btn danger"]),
    "busy": ({"strip": [_control(busy=True)]}, ["act-btn busy"]),
    "more": ({"strip": [_control()], "menu": [_control(danger=True)], "wide": True},
             ["act-btn", "act-btn more", "mi danger"]),
}


@pytest.mark.parametrize("state", list(STRIP_STATES))
def test_the_strip_states_render_the_same_class_lists(state):
    """The four states the strip already had, byte-identical: only the palette may change here."""
    strip, expected = STRIP_STATES[state]
    rendered = button_classes(render_strip(**strip))
    assert rendered == expected, f"{state} renders {rendered}"


# ----------------------------------------------------------------- the versions URL, as it really behaves

def _docstring() -> str:
    return actions_page.dcs_versions_path.__doc__ or ""


def test_the_docstring_names_the_multi_segment_converter_and_the_lookup_only_use():
    """What the route really does: escaped into one segment, decoded once, used as a lookup key."""
    text = " ".join(_docstring().split())
    assert "percent-encoded" in text, text
    assert "safe=''" in text, text
    assert "decoded" in text.lower(), text
    assert "lookup key" in text, text
    assert "{node:path}" in text and "MULTI-segment" in text, text
    # the two claims the route does NOT keep: one-segment-and-nothing-else, and the log's rule
    assert "One segment and nothing else" not in text, text
    assert "log_download_path" not in text, text


def test_the_path_is_escaped_with_no_safe_character_and_matches_the_declaration():
    """The encoder and the route agree: everything escaped, and the route is the multi-segment one."""
    assert "{node:path}" in actions_page.DCS_VERSIONS_PATH
    source = inspect.getsource(actions_page.dcs_versions_path)
    definition = ast.parse(source).body[0]
    # the CODE only: the docstring quotes `safe=''` too, so it cannot be the evidence
    statements = [node for node in getattr(definition, "body", [])
                  if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
    code = "\n".join(ast.get_source_segment(source, node) or "" for node in statements)
    assert "quote(" in code and "safe=''" in code, code


FORBIDDEN_JOINS = ("os.path.join", "Path(", "subprocess", "os.system", "Popen", "shell=True",
                   "open(")


def test_the_decoded_name_only_ever_reaches_the_registry():
    """The decoded value is a LOOKUP KEY: no filesystem join and no shell join anywhere near it."""
    source = inspect.getsource(actions_page._dcs_versions_handler)
    tree = ast.parse(source)
    read = [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path_params"]
    assert len(read) == 1, f"the route reads its path parameter {len(read)} times"
    for forbidden in FORBIDDEN_JOINS:
        assert forbidden not in source, f"the handler joins the node name: {forbidden}"
    assert "resolve_scoped_node(" in source and "node_name" in source, source
