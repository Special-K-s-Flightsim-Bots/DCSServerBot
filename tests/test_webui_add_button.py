"""THE "+ ADD" IS ONE PROPER BUTTON — the picker opener and the add buttons are the SAME control.

layout for the '++ Add' functionality? Maybe a nice button?"

WHAT IT WAS. The picker's opener was a ``<details><summary class="cfg-add">`` whose label carried a
LEADING decorative ``<span class="cfg-plus">+</span>`` BEFORE the translated ``+ Add`` — so it rendered
``++ Add`` — and the whole ``<details>`` wore its OWN dashed frame AROUND the summary (a box in a box).
The other adds were plain ``<button class="cfg-add">``, so one action wore two different looks.

WHAT IT IS NOW. ONE class (``cfg-add``), ONE label (``+ Add`` — the plus ONCE, in the translated text)
for the opener AND every add button. The opener stays a ``<summary>`` so the list still toggles with
JavaScript OFF (native disclosure, keyboard reachable) and is styled to be indistinguishable from the
``<button>`` variants; the ``<details>``'s own frame is gone, so when open the choice list drops below
as one panel. The tile language's own ``+ Add`` (``_tiles.html``) drops the same doubled glyph and wears
the same button chrome.

Pinned here, over the REAL app and the REAL catalogs:

* the opener and the add buttons render the SAME class and the SAME label;
* the label carries exactly ONE plus (no ``++ Add``, no decorative ``cfg-plus`` glyph);
* the picker still opens, a choice still reaches the same save path, and a no-op save stays
  byte-identical;
* a German render keeps the label intact (``+ Hinzufügen``);
* the four translated catalogs carry ``+ Add``, none fuzzy, each with a single plus and none of them
  English (English is the msgid and ships no catalog).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from babel.messages import pofile

from conftest import asgi_client
from services.webservice import i18n
from services.webservice.pages import settings as settings_page
from test_webui_configgen_containers import (
    _app, _config_dir, _csrf_and_revision, _posted_form,
)
from test_webui_dashboard import _login
from test_webui_picker import configgen_script  # noqa: F401 - the shipped-script harness, reused

REPO_ROOT = Path(__file__).resolve().parents[1]

#: a schema with ONE mandatory field, an optional-absent SCALAR (so the picker renders) and a SET
#: row-list (so a plain ``cfg-add`` add button renders too) — both "add" looks on ONE page.
ADD_SCHEMA = """\
type: map
nullable: false
mapping:
  required_a: {type: str, required: true, nullable: false}
  opt_num: {type: int, nullable: false}
  mylist:
    type: seq
    nullable: false
    sequence:
      - type: str
"""

#: the file: the mandatory field and ONE set row-list, the optional scalar absent (behind the picker).
ADD_FIXTURE = "required_a: hello\nmylist:\n  - alpha\n"


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Point the Settings page at *tmp_path*'s main.yaml + schemas/s.yaml (the containers recipe)."""
    monkeypatch.setattr(settings_page, "CONFIG_FILE_NAME", "main.yaml")
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(tmp_path / "schemas" / "s.yaml"))
    return lambda body, schema_text: _config_dir(tmp_path, body, schema_text)


async def _open(client) -> str:
    return (await client.get(settings_page.SETTINGS_MAIN_PATH)).text


async def _post(client, page: str, change: dict | None = None) -> str:
    csrf, revision = _csrf_and_revision(page)
    form = _posted_form(page)
    form["_csrf_token"] = csrf
    form["_revision"] = revision
    if change:
        form.update(change)
    await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
    return await _open(client)


def _live(page: str) -> str:
    """*page* with every inert ``<template>`` removed — the markup a browser actually RENDERS."""
    return re.sub(r"<template\b.*?</template>", "", page, flags=re.S)


_OPENER = re.compile(r'<summary class="cfg-add">(.*?)</summary>', re.S)
_ADD_BUTTON = re.compile(r'<button type="button" class="cfg-add" data-cfg-add>([^<]*)</button>')


# ==================================================== 1. one class, one label, one plus

@pytest.mark.asyncio
async def test_the_opener_and_the_add_buttons_share_one_class_and_one_label(patched):
    """The picker opener and every add button carry the SAME class and the SAME label — one control."""
    config_dir = patched(ADD_FIXTURE, ADD_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    live = _live(page)

    opener = _OPENER.search(live)
    assert opener, "the picker opener is not a `cfg-add` summary"
    buttons = _ADD_BUTTON.findall(live)
    assert buttons, "no `cfg-add` add button rendered"
    # ONE class, ONE label: the opener reads exactly like every add button
    assert opener.group(1).strip() == "+ Add"
    assert set(buttons) == {"+ Add"}
    assert opener.group(1).strip() == buttons[0].strip()


@pytest.mark.asyncio
async def test_the_add_label_carries_exactly_one_plus(patched):
    """The doubled glyph is GONE: one plus, once — ``+ Add``, never ``++ Add``."""
    config_dir = patched(ADD_FIXTURE, ADD_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    live = _live(page)

    label = _OPENER.search(live).group(1).strip()
    assert label.count("+") == 1, label
    assert "+ Add" == label
    # the decorative glyph and the doubled label appear NOWHERE (templates, inert or not)
    assert "cfg-plus" not in page, "the decorative leading glyph survived"
    assert "++ Add" not in page and "++ Add" not in live


# ==================================================== 2. behaviour unchanged

@pytest.mark.asyncio
async def test_the_picker_still_opens_and_a_choice_reaches_the_same_save_path(patched):
    """The opener is still a native disclosure; choosing a field still saves on the SAME path."""
    config_dir = patched(ADD_FIXTURE, ADD_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        live = _live(page)
        # still a real <details> (JS-off disclosure), still carrying its picker hooks
        assert '<details class="cfg-picker" data-cfg-picker>' in page
        assert "data-cfg-pick" in live
        # choose the offered field and post the page's own form — the SAME save path
        page = await _post(client, page, {"f.opt_num": "7"})
    text = (config_dir / "main.yaml").read_text(encoding="utf-8")
    assert "opt_num: 7" in text, "the added value did not reach the file"
    assert "required_a: hello" in text and "alpha" in text, "the other keys were disturbed"
    assert 'name="f.opt_num" value="7"' in page


@pytest.mark.asyncio
async def test_a_no_op_save_stays_byte_identical(patched):
    """Nothing about the look change touches the save: an unchanged post leaves the file alone."""
    config_dir = patched(ADD_FIXTURE, ADD_SCHEMA)
    before = (config_dir / "main.yaml").read_bytes()
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        await _post(client, page)
    assert (config_dir / "main.yaml").read_bytes() == before


def test_the_shipped_script_still_adds_a_picked_choice(configgen_script):  # noqa: F811
    """The SHIPPED ``configgen.js`` driven through the harness: picking a choice still adds its EMPTY
    control (the same ``f.`` name the save resolves), and removing it brings the choice back."""
    add = configgen_script["pickerAdd"]
    assert add == {"added": 1, "names": ["f.logging.utc"], "choiceHidden": True,
                   "holderUntouched": ""}
    remove = configgen_script["pickerRemoveAdded"]
    assert remove == {"added": 0, "names": [], "choiceShown": True}


# ==================================================== 3. the label in every language

@pytest.mark.asyncio
async def test_a_german_render_keeps_the_label_intact(patched):
    """German is ``+ Hinzufügen`` — the plus survives the translation, once."""
    config_dir = patched(ADD_FIXTURE, ADD_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        page = await _open(client)
    label = _OPENER.search(_live(page)).group(1).strip()
    assert label == "+ Hinzufügen", label
    assert label.count("+") == 1
    assert "+ Add" not in page, "the German render fell back to English"


def test_every_catalog_carries_the_add_label_once_and_unfuzzy():
    """The four TRANSLATED catalogs carry ``+ Add`` (no ``#, fuzzy``), each with exactly one plus and
    each differing from the msgid. English is the msgid, so it ships no catalog."""
    for language in ("cn", "de", "es", "ru"):
        with open(REPO_ROOT / "locale" / language / "LC_MESSAGES" / "webui.po",
                  encoding="utf-8") as handle:
            catalog = pofile.read_po(handle)
        entry = next((m for m in catalog if m.id == "+ Add"), None)
        assert entry is not None, "the '+ Add' label is missing from the %s catalog" % language
        assert not entry.fuzzy, "the '+ Add' label is fuzzy in %s (gettext would drop it)" % language
        text = entry.string or "+ Add"
        assert text.count("+") == 1, f"{language}: {text!r} does not carry exactly one plus"
        assert text != "+ Add", f"{language}: the label was left untranslated"
