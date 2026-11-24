"""The Expert Mode editor's COMPLETION POPUP over the hints JSON — the payload drawn, pinned.

The parent card serves the schema tree (``services/webservice/config_hints.py``); this one vendors
CodeMirror's ``show-hint`` addon and makes ``static/settings-yaml.js`` offer the payload's keys, its
values and its examples. What is pinned here:

* THE REAL SCRIPT, through the browser-less harness (``tests/configgen_harness.js``, the ``hints``
  scenario set): ``Ctrl+Space`` lists the block's keys, a key already written is marked ``set``, a
  pick inserts the KEY and never a value, a sequence item offers its element's enum, a nested block
  resolves by plain path, a description carrying markup arrives as literal text, the popup opens by
  itself at an empty key position (and not on an ordinary keystroke), a block's example row inserts
  the example, and every failure — a refused status, a dead network, no ``data-hints-url`` — opens
  nothing and leaves the editor exactly as it was.
* THE PRODUCTION PAYLOADS, not fixtures: the `servers_schema.yaml` tree resolves a caret inside a
  `regex;` block, and a one-key-slice payload (the preset schema's, whose root is that same
  invented-key row) offers NO key where the document's root key is written.
* THE COMMENT STRIP IS QUOTE-AWARE: a `#` inside a quoted scalar is content, so the lines after it
  keep their `set` marks.
* THE SECURITY POSTURE: the popup's every string is schema prose or a schema example, so the script
  assigns no ``innerHTML``; the rows are plain-text items, which ``show-hint`` renders as text nodes.
* THE VENDORING: both addon files exist, their sha256 match the record in ``SOURCE.txt``, and no
  page or shipped script loads anything from a CDN.
* THE PAGES: every template that loads the editor loads the addon too, before it, with the asset
  version on each of the four tags so a stale browser cache cannot serve the old script.

WHAT THIS DOES NOT PROVE: the popup's LOOK and how a real CodeMirror maps a keystroke to it. The
harness models the addon's call shape and its acceptance, not the browser's rendering or its key
handling — that half needs a browser.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from conftest import run_node_harness

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB = REPO_ROOT / "services" / "webservice"
TEMPLATES = WEB / "templates"
STATIC = WEB / "static"
VENDOR = STATIC / "vendor" / "codemirror"
SETTINGS_YAML_JS = STATIC / "settings-yaml.js"
CONFIGGEN_HARNESS = REPO_ROOT / "tests" / "configgen_harness.js"

#: the pages that load the editor. Each must also load the popup.
EDITOR_PAGES = ("settings.html", "settings_scope.html", "node_page.html", "presets_yaml.html",
                "presets_wizard.html", "scheduler_body.html", "site_config.html")

#: A strict pin on a defect in the PRODUCT, not a weaker assertion: the site.yaml editor ships no
#: completion popup, because `services/webservice/pages/site_config.py` declares no
#: `/settings/site/yaml/hints` route and `templates/site_config.html` links no show-hint stylesheet.
#: The page is expected to fail until that lands, and fails the suite again (XPASS) when it does.
POPUP_PAGES = (
    "settings.html", "settings_scope.html", "node_page.html", "presets_yaml.html",
    "presets_wizard.html", "scheduler_body.html",
    pytest.param("site_config.html", marks=pytest.mark.xfail(
        strict=True,
        reason="templates/site_config.html loads no completion popup: no show-hint stylesheet and no "
               "hints URL, because pages/site_config.py declares no /settings/site/yaml/hints route")),
)

#: the files the vendored record must name, with their hashes parsed out of it.
RECORD = VENDOR / "SOURCE.txt"
RECORD_LINE = re.compile(r"^\s*([\w./-]+)\s+sha256:\s+([0-9a-f]{64})\s*$", re.M)

#: anything a page could load from the network. The console must work with none.
CDN_MARKERS = ("cdnjs.cloudflare.com", "unpkg.com", "jsdelivr.net", "cdn.jsdelivr.net", "//cdn.")

_requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def popup() -> dict:
    """The shipped ``settings-yaml.js``'s own answers about the payload, over the REAL file."""
    assert CONFIGGEN_HARNESS.is_file() and SETTINGS_YAML_JS.is_file()
    done = run_node_harness(["node", str(CONFIGGEN_HARNESS), str(SETTINGS_YAML_JS), "hints"])
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ------------------------------------------------------------------ 1. the popup's rows

@_requires_node
def test_ctrl_space_opens_the_popup_with_the_blocks_keys(popup):
    """`Ctrl+Space` on an empty document lists the root's keys — no keystroke was needed to earn it."""
    assert popup["ctrlSpace"]["open"] is True, "Ctrl+Space opened nothing with a payload present"
    keys = popup["ctrlSpace"]["keys"]
    for key in ("guild_id: ", "language: ", "logging: ", "plugins: "):
        assert key in keys, f"the popup does not offer {key!r}: {keys}"


@_requires_node
def test_a_single_row_is_never_completed_for_the_operator(popup):
    """`completeSingle: false` — the popup never types a row the operator did not pick."""
    assert popup["ctrlSpace"]["completeSingle"] is False


@_requires_node
def test_a_row_carries_the_label_and_the_required_word(popup):
    """`key · label · required` on the first line, the field's prose underneath."""
    first = popup["ctrlSpace"]["firstRow"]
    head, _, desc = first.partition("\n")
    assert head == "guild_id · Number · required", head
    assert desc == "Discord guild/server ID."


@_requires_node
def test_a_key_already_in_the_block_is_marked_and_an_absent_one_is_not(popup):
    """Presence comes from the editor's OWN text: two keys are written, two are not."""
    marked = popup["marks"]["set"]
    assert sorted(marked) == ["guild_id: ", "language: "], marked
    assert "logging: " not in marked and "plugins: " not in marked
    assert popup["marks"]["required"] == ["guild_id: "], "the `required` word moved"


@_requires_node
def test_enter_inserts_the_key_only_and_touches_no_other_line(popup):
    """A pick writes the key and its separator — never a value — and leaves the rest of the file."""
    accepted = popup["accept"]
    assert accepted["inserted"] == "logging: ", accepted["inserted"]
    assert accepted["lines"] == ["guild_id: 1", "language: en", "logging: "], accepted["lines"]
    assert accepted["lines"][2] == "logging: ", "a value was written with the key"


@_requires_node
def test_the_payload_s_desc_arrives_as_literal_text(popup):
    """Schema prose is TEXT. A desc containing `<b>x</b>` reaches the row unchanged, never as markup."""
    shown = popup["desc"]["displayText"]
    assert shown is not None, "the row for `language` was not offered at all"
    assert "<b>x</b>" in shown, shown
    assert shown.endswith("Schema prose with <b>x</b> in it."), shown


@_requires_node
def test_a_sequence_item_offers_its_element_s_enum(popup):
    """A caret after `- ` completes the ELEMENT: its enum members, from the payload."""
    assert popup["sequence"]["open"] is True
    assert popup["sequence"]["values"] == ["admin", "mission"]


@_requires_node
def test_a_nested_block_resolves_to_its_own_keys(popup):
    """Inside `logging:` the caret completes THAT container, by plain path — never the root's keys."""
    assert popup["nested"]["keys"] == ["loglevel: ", "utc: "]


@_requires_node
def test_a_block_s_example_gets_its_own_row(popup):
    """A container the schema gives an example offers one row that inserts it, indented under the key."""
    assert popup["exampleRow"]["found"] == 1, popup["exampleRow"]
    assert popup["exampleRow"]["text"] == "logging:\n  loglevel: DEBUG\n  logfile: bot.log"


@_requires_node
def test_the_popup_opens_by_itself_at_an_empty_key_position(popup):
    """An empty key position opens it; an ordinary keystroke does not — the behaviour people switch off."""
    assert popup["auto"]["atEmptyKey"] is True, "the popup did not open at an empty key position"
    assert popup["auto"]["atTypedValue"] is False, "the popup opened on an ordinary keystroke"


# ------------------------------------------------------------- 2. every failure is silent

@_requires_node
def test_a_refused_fetch_opens_nothing_and_leaves_the_editor_usable(popup):
    """A non-200 is not an error to report: the editor keeps working, with no popup and no message."""
    refused = popup["refused"]
    assert refused["open"] is False, "a refused payload still opened a popup"
    assert refused["status"] == "Valid.", f"the failure changed the status line: {refused['status']!r}"
    assert refused["carrier"] == "guild_id: 7\n", "the editor stopped carrying edits"


@_requires_node
def test_a_dead_network_opens_nothing_and_says_nothing(popup):
    assert popup["offline"]["open"] is False
    assert popup["offline"]["status"] == "Valid.", popup["offline"]["status"]


@_requires_node
def test_a_panel_without_the_attribute_is_the_editor_it_always_was(popup):
    """No `data-hints-url`: one fetch (the validate check), no popup, no second request."""
    assert popup["noPayload"]["open"] is False
    assert popup["noPayload"]["fetches"] == 1, popup["noPayload"]["fetches"]


# ------------------------------------------------- 3. the PRODUCTION payload, not a fixture

@_requires_node
def test_the_production_payload_resolves_through_a_regex_container(tmp_path):
    """`servers_schema.yaml` names its server blocks with `regex;(.+)` — a key the DOCUMENT invents.

    The payload is the real one (:func:`config_hints.payload`), the document is a real scoped file, and
    the caret sits inside `default:`. A plain dotted lookup would find no `default` path and go dead;
    the container's own `regex` row names the element's path, so the node block's keys come back.
    """
    from services.webservice import config_hints

    data = config_hints.payload([REPO_ROOT / "schemas" / "servers_schema.yaml"])
    assert data["paths"], "the servers payload is empty"
    payload_file = tmp_path / "hints.json"
    payload_file.write_text(json.dumps(data), encoding="utf-8")

    done = run_node_harness(["node", str(CONFIGGEN_HARNESS), str(SETTINGS_YAML_JS), "real",
                             str(payload_file)])
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)

    assert result["insideNode"]["open"] is True, "the popup went dead inside a `regex;` block"
    keys = result["insideNode"]["keys"]
    for key in ("messages: ", "afk: ", "serverSettings: "):
        assert key in keys, f"the node block's keys are incomplete: {keys[:5]}…"

    # a container the schema gives an example offers it, from the real payload, indented under the key
    assert result["nodeValues"]["open"] is True
    values = result["nodeValues"]["values"]
    assert any("greeting_message_members" in value for value in values), values


@_requires_node
def test_a_one_key_slice_payload_offers_no_key_at_the_document_root(tmp_path):
    """A preset payload's root row IS the schema's `regex;(.+)` container — never a completable key.

    A preset document is ONE key (`{<name>: block}`), so the payload's root carries the schema's
    invented-key row and nothing else. Offering that row writes the SCHEMA's own syntax into the file
    (`regex;(.+): `), so the popup at the document root offers NO row at all, and accepting whatever
    rows do exist can never put `regex;` into the document. The block's own keys are still resolved
    through that row once the operator has written the name: the resolver keeps its descent.
    """
    from services.webservice import config_hints
    from services.webservice.pages import presets as presets_page

    data = config_hints.payload(presets_page.preset_schema_files())
    assert [child["key"] for child in data["paths"][""]["children"]] == ["regex;(.+)"], \
        "the preset payload's root is the schema's invented-key row — the shape this pin is about"
    payload_file = tmp_path / "preset_hints.json"
    payload_file.write_text(json.dumps(data), encoding="utf-8")

    done = run_node_harness(["node", str(CONFIGGEN_HARNESS), str(SETTINGS_YAML_JS), "slice",
                             str(payload_file)])
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)

    assert result["root"]["texts"] == [], result["root"]
    assert result["accepted"] == [], "a row was accepted at the document root"
    for accepted in result["accepted"]:
        assert "regex;" not in accepted["value"], accepted

    assert result["insideBlock"]["open"] is True, "the resolver stopped descending into the block"
    keys = result["insideBlock"]["texts"]
    for key in ("wind: ", "date: ", "temperature: "):
        assert key in keys, f"the block's keys are incomplete: {keys[:5]}…"
    assert not any(key.startswith("regex;") for key in keys), keys


@_requires_node
def test_a_hash_inside_a_quoted_scalar_does_not_cut_the_line_it_marks(popup):
    """A `#` inside a quoted scalar is CONTENT, not a comment: the scan must not truncate there.

    Two readings of one defect. Inside a block, the line above the caret carries `desc: "x # y"` and
    the key that follows is still marked `set`. At the document root, a QUOTED KEY carrying a `#`
    (`"a # b": 2`) is still read as the key it is — the strip used to cut both lines at the `#`,
    which lost that key and with it its mark.
    """
    quoted = popup["quotedHash"]
    assert quoted["insideBlockMarked"] == ["loglevel: "], quoted
    assert quoted["rootMarked"] == ["guild_id: ", '"a # b": '], quoted


# ------------------------------------------------------------------ 4. the security posture

def test_the_script_assigns_no_html_where_a_schema_string_lands():
    """Every popup string is schema prose or a schema example, so nothing is built as markup."""
    source = SETTINGS_YAML_JS.read_text("utf-8")
    assert "innerHTML" not in source, "the editor builds markup from a schema string"
    assert "insertAdjacentHTML" not in source and "outerHTML" not in source


# ------------------------------------------------------------------ 5. the vendoring

def test_the_addon_is_vendored_beside_the_core():
    for name in ("show-hint.js", "show-hint.css"):
        path = VENDOR / "addon" / "hint" / name
        assert path.is_file(), f"the popup addon is not shipped: {name}"
        assert path.stat().st_size > 0


def test_the_vendored_addon_matches_the_recorded_hashes():
    """`SOURCE.txt` names the addon's exact URL and sha256; the shipped bytes are those bytes."""
    recorded = {name.split("/")[-1]: digest for name, digest in RECORD_LINE.findall(
        RECORD.read_text("utf-8"))}
    for name in ("show-hint.js", "show-hint.css"):
        assert name in recorded, f"SOURCE.txt records no sha256 for {name}"
        #: hashed with the line ending NORMALISED: a Windows checkout may materialise the file with
        #: CRLF, which is not an edit of the vendored bytes
        shipped = hashlib.sha256(
            (VENDOR / "addon" / "hint" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        assert shipped == recorded[name], (
            f"{name}: the vendored file was edited — SOURCE.txt says {recorded[name]}, "
            f"the file hashes to {shipped}")
        assert "5.65.16/addon/hint/" in RECORD.read_text("utf-8"), (
            "the record does not name the release the addon came from")


def test_no_page_or_shipped_script_loads_anything_from_a_cdn():
    """The console works with NO NETWORK: every asset is served from `/static`."""
    offenders = []
    for path in sorted(TEMPLATES.glob("*.html")) + sorted(STATIC.rglob("*.js")):
        text = path.read_text("utf-8", errors="replace")
        for marker in CDN_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {marker}")
    assert not offenders, "a page or script reaches for a CDN: " + "; ".join(offenders)


# ------------------------------------------------------------------ 6. the pages that load it

@pytest.mark.parametrize("name", POPUP_PAGES)
def test_every_editor_page_loads_the_popup_before_the_script(name):
    """The addon's stylesheet in the head, its script in the scripts block, BEFORE `settings-yaml.js`."""
    text = (TEMPLATES / name).read_text("utf-8")
    assert ('<link rel="stylesheet" href="/static/vendor/codemirror/addon/hint/show-hint.css'
            '?v={{ asset_version }}">') in text, f"{name} does not link the popup's stylesheet"
    assert ('<script src="/static/vendor/codemirror/addon/hint/show-hint.js?v={{ asset_version }}"'
            ' defer></script>') in text, f"{name} does not load the popup addon"
    addon = text.index('<script src="/static/vendor/codemirror/addon/hint/show-hint.js')
    editor = text.index('<script src="/static/settings-yaml.js')
    assert addon < editor, f"{name} loads the editor script before the addon it calls"


@pytest.mark.parametrize("name", EDITOR_PAGES)
def test_every_editor_asset_is_versioned(name):
    """A browser cache must not serve the previous `settings-yaml.js` or CodeMirror to a new build."""
    text = (TEMPLATES / name).read_text("utf-8")
    for tag in ('<script src="/static/settings-yaml.js?v={{ asset_version }}" defer></script>',
                '<script src="/static/vendor/codemirror/codemirror.min.js?v={{ asset_version }}"'
                ' defer></script>',
                '<script src="/static/vendor/codemirror/mode/yaml.min.js?v={{ asset_version }}"'
                ' defer></script>'):
        assert tag in text, f"{name} serves an asset without its version: {tag}"


# ------------------------------------------------------------------ 7. the popup's theme

#: the classes an ancestor can never carry for the popup: the addon appends the list to
#: `document.body`, so no rule naming one of these can match it.
NEVER_AN_ANCESTOR = frozenset({"cfg-editor", "cfg-yaml", "CodeMirror"})

#: properties whose value is a colour: a literal here is the defect this pin exists for.
COLOUR_PROPERTIES = ("color", "background", "border", "outline", "box-shadow", "text-shadow",
                     "fill", "stroke", "caret-color", "text-decoration", "scrollbar-color",
                     "column-rule")

#: a colour written out, after every `var(--…)` is removed: `#rgb`, `#rrggbb`, `rgb()/hsl()` and a
#: named colour standing alone. (`transparent`/`currentColor`/`inherit` are keywords, not literals.)
COLOUR_LITERAL = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|(?<![\w-])(?:rgba?|hsla?|color-mix)\(|(?<![\w-])[a-z]+(?=[\s,;]|$)",
    re.IGNORECASE)


def _css_rules(sheet):
    """Every `(selector, body)` pair in *sheet*, at any `@media`/`@supports` depth, comments gone."""
    text = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    rules, buf, prelude = [], [], ""
    for ch in text:
        if ch == "{":
            prelude = "".join(buf).strip()
            buf = []
        elif ch == "}":
            if prelude and not prelude.startswith("@"):
                rules.append((prelude, "".join(buf)))
            prelude = ""
            buf = []
        else:
            buf.append(ch)
    return rules


def _split_selectors(selector):
    """A selector list split on its top-level commas (`:is(a, b)` stays in one piece)."""
    parts, depth, buf = [], 0, []
    for ch in selector:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf).strip())
    return parts


def _compounds(selector):
    """A complex selector split into its compounds: `a > b c` -> `['a', 'b', 'c']`."""
    out, depth, buf = [], 0, []
    for ch in selector:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if depth == 0 and ch in " >+~":
            if "".join(buf).strip():
                out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def _classes(compound):
    return set(re.findall(r"\.([A-Za-z0-9_-]+)", compound))


def _element(compound):
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9]*)", compound)
    return match.group(1).lower() if match else ""


#: the functional pseudo-classes whose argument list contributes its STRONGEST member.
_FUNC_PSEUDO = re.compile(r":(is|where|not|has)\(")


def _specificity(selector):
    """`(ids, classes, elements)` — the tie-break CSS actually uses, `:is()`/`:where()` resolved."""
    total = [0, 0, 0]
    plain, pos = [], 0
    for match in _FUNC_PSEUDO.finditer(selector):
        if match.start() < pos:
            continue
        plain.append(selector[pos:match.start()])
        depth, end = 1, match.end()
        while end < len(selector) and depth:
            depth += {"(": 1, ")": -1}.get(selector[end], 0)
            end += 1
        inner = selector[match.end():end - 1]
        best = max((_specificity(part) for part in _split_selectors(inner)), default=(0, 0, 0))
        if match.group(1) != "where":
            total = [total[0] + best[0], total[1] + best[1], total[2] + best[2]]
        pos = end
    plain.append(selector[pos:])
    rest = "".join(plain)
    total[0] += len(re.findall(r"#[A-Za-z0-9_-]+", rest))
    total[1] += (len(re.findall(r"\.[A-Za-z0-9_-]+", rest))
                 + len(re.findall(r"\[[^\]]*\]", rest))
                 + len(re.findall(r"(?<!:):(?!:)[a-z-]+", rest)))
    total[2] += (len(re.findall(r"::[a-z-]+", rest))
                 + len(re.findall(r"(?<![\w.:#-])[a-zA-Z][a-zA-Z0-9]*", rest)))
    return tuple(total)


def _matches_body_child(selector, css_class, element):
    """Can *selector* match a `.<css_class>` that the addon mounted as a CHILD OF BODY?"""
    compounds = _compounds(selector)
    if not compounds:
        return False
    last = compounds[-1]
    if css_class not in _classes(last):
        return False
    if _element(last) and _element(last) != element:
        return False
    return not any(_classes(part) & NEVER_AN_ANCESTOR for part in compounds[:-1])


def _declarations(body):
    """`[(property, value)]` for one rule body; a `;` inside quotes or parens does not split."""
    out, depth, buf = [], 0, []
    for ch in body:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == ";" and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        out.append("".join(buf))
    pairs = []
    for chunk in out:
        if ":" in chunk:
            prop, value = chunk.split(":", 1)
            pairs.append((prop.strip().lower(), value.strip()))
    return pairs


def _popup_rules(sheet):
    """`[(selector part, body)]` — the parts that can match the popup where it actually lives."""
    wanted = (("CodeMirror-hints", "ul"), ("CodeMirror-hint", "li"), ("CodeMirror-hint-active", "li"))
    hits = []
    for selector, body in _css_rules(sheet):
        for part in _split_selectors(selector):
            if any(_matches_body_child(part, cls, el) for cls, el in wanted):
                hits.append((part, body))
    return hits


def test_the_popup_is_themed_where_it_actually_renders():
    """A rule in shell.css must match the popup as the addon mounts it: a CHILD OF BODY.

    The addon appends `<ul class="CodeMirror-hints">` to `document.body`, so a rule written
    `.cfg-editor .CodeMirror-hints` matches nothing at all — the stylesheet only looked themed.
    Each of the list, a row and the selected row needs at least one rule with no editor ancestor,
    and no rule may still carry one (a selector that cannot match is worse than none: it reads as
    coverage).
    """
    sheet = (STATIC / "shell.css").read_text("utf-8")
    for css_class, element in (("CodeMirror-hints", "ul"), ("CodeMirror-hint", "li"),
                               ("CodeMirror-hint-active", "li")):
        live = [sel for sel, _ in _popup_rules(sheet)
                if _matches_body_child(sel, css_class, element)]
        assert live, (f"no rule in shell.css can match a body-level `.{css_class}` — the popup is "
                      f"mounted on `document.body`, not inside the editor")
        dead = []
        for selector, _ in _css_rules(sheet):
            for part in _split_selectors(selector):
                if css_class in _classes(_compounds(part)[-1]) and part not in live:
                    dead.append(part)
        assert not dead, ("these selectors name an ancestor the popup never has, so they match "
                          "nothing: " + "; ".join(sorted(set(dead))))


def test_every_popup_colour_comes_from_a_shell_token():
    """No colour literal in the popup's rules: a second palette must be a declaration, not an edit."""
    sheet = (STATIC / "shell.css").read_text("utf-8")
    rules = _popup_rules(sheet)
    assert rules, "no rule in shell.css matches the popup at all"
    offenders = []
    for selector, body in rules:
        for prop, value in _declarations(body):
            if not prop.startswith(COLOUR_PROPERTIES):
                continue
            bare = re.sub(r"var\(--[A-Za-z0-9_-]+\)", "", value)
            for literal in COLOUR_LITERAL.findall(bare):
                if literal.lower() in ("inherit", "transparent", "currentcolor", "none", "auto",
                                       "solid", "dashed", "dotted", "thin", "inset", "0"):
                    continue
                offenders.append(f"{selector} {{ {prop}: {value} }} -> {literal}")
    assert not offenders, ("the popup's theme writes a colour instead of reading a token: "
                           + "; ".join(offenders))


def test_every_popup_rule_out_specifies_the_vendored_sheet():
    """shell.css is linked BEFORE the addon's sheet, so an equal-specificity rule loses the tie.

    The popup has no wrapper to borrow weight from, so its selectors must carry the extra element
    (`ul.CodeMirror-hints`) or a second class. This is the second half of the same defect: a bare
    `.CodeMirror-hints` rule would match the element and still paint nothing.
    """
    shell = (STATIC / "shell.css").read_text("utf-8")
    vendored = (VENDOR / "addon" / "hint" / "show-hint.css").read_text("utf-8")
    for css_class, element in (("CodeMirror-hints", "ul"), ("CodeMirror-hint", "li"),
                               ("CodeMirror-hint-active", "li")):
        theirs = max(_specificity(sel) for sel, _ in _css_rules(vendored)
                     if css_class in _classes(_compounds(sel)[-1]))
        ours = [_specificity(sel) for sel, _ in _popup_rules(shell)
                if _matches_body_child(sel, css_class, element)]
        assert ours, f"no body-level rule for `.{css_class}`"
        assert max(ours) > theirs, (
            f"`.{css_class}`: the strongest shell rule is {max(ours)}, the vendored sheet's is "
            f"{theirs} — shell.css loads first, so it would lose the tie and paint nothing")
