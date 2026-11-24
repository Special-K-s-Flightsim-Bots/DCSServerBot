"""The palette block repaints the card's own preview from its own controls, before Save.

Only the wizard painted, through ``paintPreview``; a preset press and a typed sheet have to reach the
same frame through the same function. These pins drive the SHIPPED scripts — ``static/site-config.js``
and ``static/site-palette-wizard.js`` — in a DOM stub built from the RENDERED card, so what is
asserted is the elements the operator presses and the values the server put on them, never a copy:

* pressing a preset tile applies that preset's OWN declarations to the frame's ``documentElement``
  and sets its ``data-theme`` to that preset's scheme — the empty-value "Shipped palette" tile
  applies the default preset's map;
* typing or pasting a sheet applies the declarations the grammar describes and leaves the frame's
  last state for a line it does not describe, rather than emptying it;
* the deprecated ``theme.light`` switch paints nothing: a flag is not a palette.

Every expected value is read from the rendered payload or from the preset file — no colour is
restated here.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from services.webservice import theme_css
from test_webui_site_settings import (ADMIN, app, client, config_dir,  # noqa: F401 - fixtures
                                      open_page, sign_in_as)
from test_webui_site_settings_page import (SITE_KEY, _probe, live_config,  # noqa: F401 - fixtures
                                           served)

WEB = Path(theme_css.__file__).parent
STATIC = WEB / "static"
SITE_CONFIG_JS = STATIC / "site-config.js"
WIZARD_JS = STATIC / "site-palette-wizard.js"
HARNESS = Path(__file__).parent / "site_config_palette_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}

#: the block the palette controls sit in, as the template states it
PALETTE_SECTION = 'data-sc-block="palette"'

#: a line the sheet's grammar does not describe: no token name, no colon, no closing semicolon
JUNK = "this line is not a declaration"

#: the two tokens the sheet edit writes, taken from the payload the page rendered
EDIT_TOKENS = ("--ground", "--brass")


class _Node:
    """One element of the rendered page: its attributes and its children."""

    def __init__(self, tag: str, attrs: dict[str, str]):
        self.tag = tag
        self.attrs = attrs
        self.children: list["_Node"] = []
        self.text = ""


class _Tree(HTMLParser):
    """The rendered page as a tree: a closing tag pops to its match, an orphan is ignored."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {name: (value if value is not None else "") for name, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = _Node(tag, {name: (value if value is not None else "") for name, value in attrs})
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].text += data


def _walk(node: _Node):
    for child in node.children:
        yield child
        yield from _walk(child)


def _classes(node: _Node) -> list[str]:
    return node.attrs.get("class", "").split()


def card_form(html: str) -> _Node:
    """The FIRST card's form: the one holding the root site's palette block and its frame."""
    tree = _Tree()
    tree.feed(html)
    for node in _walk(tree.root):
        if node.tag != "form" or "sc-form" not in _classes(node):
            continue
        for inner in _walk(node):
            name = inner.attrs.get("name", "")
            if name.startswith("f.sites.root.") and name.endswith(".theme.preset"):
                return node
    raise AssertionError("the page rendered no card form for the root site")


def _node_json(node: _Node) -> dict:
    spec: dict = {"tag": node.tag, "attrs": dict(node.attrs)}
    if node.tag == "textarea":
        spec["value"] = node.text
    if "checked" in node.attrs:
        spec["checked"] = True
    if "data-sc-preview" in node.attrs:
        spec["frame"] = True
    children = [_node_json(child) for child in node.children]
    if children:
        spec["children"] = children
    return spec


def _run_harness(tmp_path: Path, form: _Node, signals: dict) -> dict:
    tree = tmp_path / "card.json"
    tree.write_text(json.dumps({"form": _node_json(form)}), encoding="utf-8")
    payload = tmp_path / "signals.json"
    payload.write_text(json.dumps(signals), encoding="utf-8")
    finished = subprocess.run(["node", str(HARNESS), str(SITE_CONFIG_JS), str(WIZARD_JS),
                               str(tree), str(payload)],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert finished.returncode == 0, f"the harness failed:\n{finished.stdout}\n{finished.stderr}"
    return json.loads(finished.stdout.strip().splitlines()[-1])


def _file_declarations(preset_id: str | None) -> dict[str, str]:
    """A preset's declarations read from its own shipped file — the source the tile must carry."""
    values: dict[str, str] = {}
    for line in theme_css.preset_sheet(preset_id).splitlines():
        stripped = line.strip()
        if stripped.startswith("--") and ":" in stripped:
            name, _, raw = stripped.partition(":")
            values[name.strip()] = raw.rstrip(";").strip()
    return values


def _escaped(value: str) -> str:
    """A rendered attribute value: what Jinja writes between the quotes."""
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&#34;").replace("'", "&#39;"))


def _tiles(form: _Node) -> list[_Node]:
    """Every preset tile of the card, in the order the gallery draws them."""
    return [node for node in _walk(form) if "data-sc-preset-tile" in node.attrs]


def _radio(tile: _Node) -> _Node:
    """The radio a tile wraps — the control a press lands on."""
    for child in _walk(tile):
        if child.tag == "input" and child.attrs.get("type") == "radio":
            return child
    raise AssertionError("a preset tile wraps no radio")


def _payloads(html: str, form: _Node) -> list[dict]:
    """Each tile's payload, decoded from the attribute the TEMPLATE rendered on its radio.

    The value is checked against the response itself, so a tree built by hand cannot stand in for
    the control the operator presses.
    """
    payloads = []
    for tile in _tiles(form):
        radio = _radio(tile)
        raw = radio.attrs.get("data-sc-preset-sheet")
        assert raw is not None, "a preset tile's radio carries no payload of its own"
        assert f'data-sc-preset-sheet="{_escaped(raw)}"' in html, \
            "the radio's payload is not the one the response rendered"
        payloads.append(json.loads(raw))
    assert payloads, "the template rendered no tile carrying its own sheet"
    return payloads


def _palette_section(form: _Node) -> _Node:
    for node in _walk(form):
        if node.attrs.get("data-sc-block") == "palette":
            return node
    raise AssertionError("the card rendered no palette block")


def _sheet_lines(payloads: list[dict]) -> list[str]:
    shipped = payloads[0]["declarations"]
    return [f"{token}: {shipped[token]};" for token in EDIT_TOKENS]


async def _page(http) -> tuple[str, _Node]:
    html = await open_page(http)
    return html, card_form(html)


# ============================================= the payload: the file's own map, and the default's

@pytest.mark.asyncio
async def test_every_tile_carries_its_own_sheet_and_the_shipped_tile_carries_the_default(app):
    """The payload is the preset FILE's declarations; the empty-value tile names the default."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html, form = await _page(http)

    payloads = _payloads(html, form)
    default = theme_css.PRESET_BY_ID[theme_css.DEFAULT_PRESET]
    assert len(payloads) == 1 + len(theme_css.PRESETS), \
        f"the gallery rendered {len(payloads)} tiles for {len(theme_css.PRESETS)} shipped presets"
    assert [_radio(tile).attrs.get("value") for tile in _tiles(form)] == \
        [""] + [preset.id for preset in theme_css.PRESETS], "the tiles are not the gallery's order"

    assert payloads[0] == {"scheme": default.scheme,
                           "declarations": _file_declarations(default.id)}, \
        "the shipped palette's tile does not carry the default preset's own sheet"
    for preset, payload in zip(theme_css.PRESETS, payloads[1:]):
        assert payload == {"scheme": preset.scheme,
                           "declarations": _file_declarations(preset.id)}, \
            f"tile {preset.id!r} does not carry its own file's declarations"


# ==================================================== the press: the values reach the frame root

@requires_node
@pytest.mark.asyncio
async def test_a_preset_press_paints_that_sheet_and_its_scheme_on_the_cards_own_frame(app, tmp_path):
    """Pressing a tile applies its file's declarations to the frame root, and its scheme."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html, form = await _page(http)
    rendered = _payloads(html, form)
    report = _run_harness(tmp_path, form, {"lines": _sheet_lines(rendered), "junk": JUNK})

    section = _palette_section(form)
    assert report["section"]["present"], "the shipped script resolved no palette block"
    assert report["section"]["className"] == section.attrs["class"], \
        "the block the script resolved is not the one the template rendered"
    assert report["section"]["scheme"] == section.attrs["data-sc-pal-scheme"], \
        "the block the script resolved carries no scheme of the site's own sheet"

    assert len(report["tiles"]) == len(rendered), \
        f"pressed {len(report['tiles'])} tiles, the page renders {len(rendered)}"
    for tile, payload in zip(report["tiles"], rendered):
        assert json.loads(tile["payload"]) == payload, \
            f"tile {tile['value']!r}: the script read a different payload than the template drew"
        assert tile["painted"]["props"] == payload["declarations"], \
            (f"tile {tile['value']!r} painted {len(tile['painted']['props'])} of "
             f"{len(payload['declarations'])} declarations")
        assert tile["painted"]["theme"] == payload["scheme"], \
            f"tile {tile['value']!r} left data-theme={tile['painted']['theme']!r}"


# ========================================= the sheet edit: what it parses, and what it keeps

@requires_node
@pytest.mark.asyncio
async def test_typing_a_sheet_paints_what_it_parses_and_keeps_the_last_state(app, tmp_path):
    """Two typed declarations land; the line the grammar does not describe changes nothing."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html, form = await _page(http)
    payloads = _payloads(html, form)
    editing = _run_harness(tmp_path, form, {"lines": _sheet_lines(payloads), "junk": JUNK})["editing"]

    expected = {token: payloads[0]["declarations"][token] for token in EDIT_TOKENS}
    assert editing["before"]["props"], "the press before the edit painted nothing to keep"
    for token, value in expected.items():
        assert editing["typed"]["props"][token] == value, \
            f"typing painted {token}={editing['typed']['props'].get(token)!r}"
    # every declaration the press had painted survives: the edit adds, it never empties
    for token, value in editing["before"]["props"].items():
        if token in expected:
            continue
        assert editing["typed"]["props"].get(token) == value, \
            f"typing dropped {token}, which nothing in the sheet describes"
    assert editing["typed"]["theme"] == _palette_section(form).attrs["data-sc-pal-scheme"], \
        "a typed sheet did not state the site's own scheme"

    # the editor's own report reaches the same paint as the textarea's `input`
    assert editing["keystroke"]["props"] == expected, editing["keystroke"]["props"]
    # a sheet that parses to nothing keeps the frame exactly as it stands
    assert editing["after_junk"] == editing["keystroke"], \
        "a line the grammar does not describe emptied the frame"


@requires_node
@pytest.mark.asyncio
async def test_the_deprecated_light_switch_paints_nothing(app, tmp_path):
    """`theme.light` is a flag, not a palette: moving it leaves the frame as it stands."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        _html, form = await _page(http)
    ignored = _run_harness(tmp_path, form, {"lines": [], "junk": JUNK})["ignored"]

    assert ignored["light"] is not None, "the card renders no deprecated light switch"
    assert ignored["light"] == {"props": {}, "theme": "dark"}, ignored["light"]


# ======================================================== the real page, in a real browser

@pytest.mark.asyncio
async def test_a_real_browser_sees_the_frame_repaint_before_save(served):
    """The whole path end to end: press each tile, then type a sheet, and read the FRAME back."""
    report = _probe(served, "palettepaint", site=SITE_KEY)["paint"]

    assert report["frames"] == 1, f"the card mounts {report['frames']} preview frames"
    assert report["presets"], "the card draws no preset tile to press"

    watched = ("--ground", "--panel", "--brass", "--fg")
    for pressed in report["presets"]:
        payload = pressed["payload"]
        after = pressed["after"]
        assert after["theme"] == payload["scheme"], (
            f"pressing {pressed['value']!r} left the frame on data-theme={after['theme']!r}")
        for token in watched:
            assert after[token] == payload["declarations"][token], (
                f"pressing {pressed['value']!r} left {token}={after[token]!r}, the file says "
                f"{payload['declarations'][token]!r}")

    assert report["presets"][0]["after"] != report["presets"][-1]["after"], (
        "every preset painted the same palette: the press changed nothing")

    sheet = report["sheet"]
    assert sheet["shown"], "the card draws no expert sheet to type into"
    assert sheet["typed"]["--ground"] == "#102030", sheet["typed"]
    assert sheet["typed"]["--brass"] == "#abcdef", sheet["typed"]
    assert sheet["after_junk"] == sheet["typed"], (
        "a line the grammar does not describe emptied the frame: "
        f"{sheet['typed']} -> {sheet['after_junk']}")

    assert report["press_requests"] == [], (
        f"a palette press talked to the server: {report['press_requests']}")


# ============================================================ no value is restated in the script

@pytest.mark.asyncio
async def test_no_palette_value_is_hardcoded_in_the_block_script(app):
    """Every value a press paints comes from the payload; none is spelled in the shipped script."""
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html, form = await _page(http)
    source = SITE_CONFIG_JS.read_text(encoding="utf-8")
    for payload in _payloads(html, form):
        for value in payload["declarations"].values():
            assert value not in source, f"{value!r} is restated in site-config.js"
