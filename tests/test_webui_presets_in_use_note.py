"""The Presets page's tile says WHY a removal is refused, instead of only dropping the trash.

One section per acceptance criterion:

* a preset the guard would refuse carries its own NOTE — the same sentence and the same reference
  labels the removal confirm page prints — and no live trash;
* a preset nothing references keeps the trash and carries no such note;
* the ``modify`` preset keeps its own DSL note, and a referenced ``modify`` preset carries both;
* the note names all three sources: a node/instance settings block, a combination preset and a
  Scheduler section;
* the reference map is built ONCE per render, whatever the number of tiles and however many
  references point at one tile, and the per-name doors are not used by the page;
* the same words have ONE home: the tile's sentence and the confirm page's are the same translation;
* the GET writes nothing: every file of the config tree is sha256-identical afterwards.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), reusing the write suite's harness so the app under test is the running one.
"""
from __future__ import annotations

import hashlib
import html as html_module
import re
from pathlib import Path

import pytest

from services.webservice.pages import presets as presets_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, fleet, sign_in,
)

PRESETS_YAML = """\
Spring:
  date: '2016-04-21'
  temperature: 8
Morning:
  start_time: 08:00
Summer:
  temperature: 23
Carrier Freqs:
  modify:
    file: mission
Casual Weather:
  - Summer
  - Morning
"""

NODES_REFERENCING = """\
NODE1:
  instances:
    Instance1:
      extensions:
        MizEdit:
          settings:
            "00:01-06:00": "Morning"
"""

SCHEDULER_REFERENCING = """\
DEFAULT:
  action:
    settings:
      - Summer
"""

_TILE = re.compile(r'<article class="st-tile preset">')


# ------------------------------------------------------------------------------ the harness

def preset_file(root: Path, text: str = PRESETS_YAML, name: str = "presets.yaml") -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def nodes_file(root: Path, text: str = NODES_REFERENCING) -> Path:
    path = root / "nodes.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def scheduler_file(root: Path, text: str = SCHEDULER_REFERENCING) -> Path:
    directory = root / "plugins"
    directory.mkdir(exist_ok=True)
    path = directory / "scheduler.yaml"
    path.write_text(text, encoding="utf-8")
    return path


async def page(http, path: str) -> str:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


def a_tile(html: str, name: str) -> str:
    """The ONE tile whose name is *name*."""
    matches = [chunk for chunk in _TILE.split(html)[1:]
               if f'<h3 class="st-name">{name}</h3>' in chunk]
    assert len(matches) == 1, f"expected exactly one tile for '{name}', got {len(matches)}"
    return matches[0]


def note_of(tile: str) -> str:
    """The tile's in-use note, or ``""`` when it carries none."""
    found = re.search(r'<div class="st-note st-inuse">(.*?)</div>', tile, re.S)
    return found.group(1) if found else ""


def references_in(note: str) -> list[str]:
    return [html_module.unescape(one) for one in re.findall(r"<li>(.*?)</li>", note)]


def _text(text: str) -> str:
    return html_module.escape(text, quote=True).replace("&#x27;", "&#39;")


def tree_digests(config_dir: Path) -> dict[str, str]:
    return {str(path.relative_to(config_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(config_dir.rglob("*")) if path.is_file()}


# --------------------------------------------- 1. a refused removal says why, on its own tile

@pytest.mark.asyncio
async def test_a_referenced_preset_says_who_holds_it_and_has_no_live_trash(config_dir,
                                                                         clean_registry):
    """``Morning`` is named by a node and by a combination: the tile states it, and draws no trash."""
    preset_file(config_dir)
    nodes_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)

    tile = a_tile(html, "Morning")
    note = note_of(tile)
    assert note, "a preset whos removal the guard would refuse carries no note"
    assert _text(presets_page.PRESETS_REMOVE_IN_USE % {"name": "Morning"}) in note, \
        "the tile does not say why the removal is refused"
    assert references_in(note) == ["node 'NODE1', instance 'Instance1'",
                                   "the preset 'Casual Weather'"], references_in(note)
    # the foot keeps its cog and its spacer; the trash is the control that is gone
    assert "st-cog" in tile and "st-sp" in tile
    assert "st-trash" not in tile, "an in-use preset still carries a live trash"
    assert presets_page.removal_url("Morning") not in tile


@pytest.mark.asyncio
async def test_the_note_names_a_node_and_a_scheduler_reference(config_dir, clean_registry):
    """The two other sources: a Scheduler section is named exactly as the confirm page names it."""
    preset_file(config_dir)
    nodes_file(config_dir)
    scheduler_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)

    scheduled = references_in(note_of(a_tile(html, "Summer")))
    assert "the scheduler's default section" in scheduled, scheduled
    assert "the preset 'Casual Weather'" in scheduled, scheduled
    node_only = references_in(note_of(a_tile(html, "Morning")))
    assert "node 'NODE1', instance 'Instance1'" in node_only, node_only


# ------------------------------------------------- 2. a removable preset is left exactly as it was

@pytest.mark.asyncio
async def test_a_removable_preset_keeps_its_trash_and_carries_no_note(config_dir, clean_registry):
    """Nothing references ``Spring`` or ``Casual Weather``: both keep the trash and gain no note."""
    preset_file(config_dir)
    nodes_file(config_dir)
    scheduler_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)

    for name in ("Spring", "Casual Weather"):
        tile = a_tile(html, name)
        assert note_of(tile) == "", f"'{name}' is removable and still carries a refusal note"
        assert "st-trash" in tile, f"'{name}' is removable and lost its trash"
        assert f'href="{presets_page.removal_url(name)}"' in tile


@pytest.mark.asyncio
async def test_the_modify_preset_keeps_its_dsl_note_and_gains_the_refusal_when_held(
        config_dir, clean_registry):
    """The DSL note stays; a referenced ``modify`` preset carries it AND the refusal."""
    preset_file(config_dir)
    nodes_file(config_dir, """\
NODE1:
  instances:
    Instance1:
      extensions:
        MizEdit:
          settings:
            "06:01-12:00": "Carrier Freqs"
""")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)

    tile = a_tile(html, "Carrier Freqs")
    assert _text(presets_page.PRESETS_MODIFY_NOTE) in tile, "the DSL note was lost"
    assert "st-trash" not in tile, "a referenced modify preset still carries a trash"
    assert references_in(note_of(tile)) == ["node 'NODE1', instance 'Instance1'"]


# --------------------------------------------------------------- 3. same words, one home

@pytest.mark.asyncio
async def test_the_tile_and_the_confirm_page_print_the_same_sentence(config_dir, clean_registry):
    """One sentence, translated once per surface: the two must not drift into two spellings."""
    preset_file(config_dir)
    nodes_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)
        confirm = await page(http, presets_page.removal_url("Morning"))

    found = re.search(r'<p class="pw-note">(Morning[^<]*)</p>', confirm)
    assert found is not None, "the confirm page stated no in-use sentence"
    sentence = found.group(1)
    tile_note = note_of(a_tile(html, "Morning"))
    assert tile_note.startswith(sentence), (sentence, tile_note)
    # and the same reference list, built from the same map
    refs = re.search(r'<ul class="ps-refs">(.*?)</ul>', confirm, re.S)
    assert refs is not None, "the confirm page listed no reference"
    listed = [html_module.unescape(one) for one in re.findall(r"<li>(.*?)</li>", refs.group(1))]
    assert references_in(tile_note) == listed, (references_in(tile_note), listed)


@pytest.mark.asyncio
async def test_the_sentence_helper_is_the_only_home(config_dir, clean_registry):
    """The route asks :func:`in_use_sentence` for every in-use sentence it renders."""
    preset_file(config_dir)
    nodes_file(config_dir)
    scheduler_file(config_dir)
    app = build(config_dir, fleet())
    calls: list[str] = []
    original = presets_page.in_use_sentence

    def counting(request, name):
        calls.append(name)
        return original(request, name)

    presets_page.in_use_sentence = counting
    try:
        async with client(app) as http:
            await sign_in(http, "specialk")
            html = await page(http, presets_page.PRESETS_PATH)
            await page(http, presets_page.removal_url("Summer"))
    finally:
        presets_page.in_use_sentence = original

    assert sorted(calls) == ["Morning", "Summer", "Summer"], calls
    assert presets_page.removal_url("Summer") not in a_tile(html, "Summer")


@pytest.mark.asyncio
async def test_the_note_speaks_the_page_language(config_dir, clean_registry):
    """The tile's sentence is the catalog's, not the English source: a German page says it in German."""
    from services.webservice import i18n

    preset_file(config_dir)
    nodes_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        await http.get(i18n.LANGUAGE_PATH, params={"lang": "de", "next": presets_page.PRESETS_PATH})
        html = await page(http, presets_page.PRESETS_PATH)
    note = note_of(a_tile(html, "Morning"))
    assert "Auf Morning wird noch verwiesen" in note, note
    assert "is still referenced" not in note, note


# ------------------------------------------------------------- 4. one walk, whatever the tiles

@pytest.mark.asyncio
async def test_the_reference_map_is_built_once_per_render(config_dir, clean_registry):
    """ONE map per render — never a scan per tile — and the per-name doors are not used by the page."""
    preset_file(config_dir)
    nodes_file(config_dir)
    scheduler_file(config_dir)
    app = build(config_dir, fleet())
    calls: list[int] = []
    original_map = presets_page.preset_reference_map
    original_doors = (presets_page.mizedit_references, presets_page.combination_references,
                      presets_page.scheduler_references)

    def counting_map(*args, **kwargs):
        calls.append(1)
        return original_map(*args, **kwargs)

    def forbid(module, attribute):
        def refuse(*args, **kwargs):
            raise AssertionError(f"the page scanned per tile through {attribute}")
        return refuse

    presets_page.preset_reference_map = counting_map
    presets_page.mizedit_references = forbid(presets_page, "mizedit_references")
    presets_page.combination_references = forbid(presets_page, "combination_references")
    presets_page.scheduler_references = forbid(presets_page, "scheduler_references")
    try:
        async with client(app) as http:
            await sign_in(http, "specialk")
            html = await page(http, presets_page.PRESETS_PATH)
    finally:
        presets_page.preset_reference_map = original_map
        (presets_page.mizedit_references, presets_page.combination_references,
         presets_page.scheduler_references) = original_doors

    assert len(calls) == 1, f"the reference map was built {len(calls)} times for one render"
    assert len(_TILE.findall(html)) == 5


@pytest.mark.asyncio
async def test_one_walk_however_many_references_point_at_one_tile(config_dir, clean_registry):
    """Twelve references to ONE preset still cost ONE walk: the map is keyed by name, not scanned."""
    many = "NODE1:\n  instances:\n"
    for instance in range(1, 13):
        many += (f"    Instance{instance}:\n      extensions:\n        MizEdit:\n"
                 f"          settings:\n            \"00:00-01:00\": \"Morning\"\n")
    preset_file(config_dir)
    nodes_file(config_dir, many)
    app = build(config_dir, fleet())
    calls: list[int] = []
    original_map = presets_page.preset_reference_map

    def counting_map(*args, **kwargs):
        calls.append(1)
        return original_map(*args, **kwargs)

    presets_page.preset_reference_map = counting_map
    try:
        async with client(app) as http:
            await sign_in(http, "specialk")
            html = await page(http, presets_page.PRESETS_PATH)
    finally:
        presets_page.preset_reference_map = original_map

    assert len(calls) == 1, calls
    listed = references_in(note_of(a_tile(html, "Morning")))
    assert len([one for one in listed if one.startswith("node 'NODE1'")]) == 12, listed


# ------------------------------------------------------------------ 5. the page stays a read

@pytest.mark.asyncio
async def test_the_page_still_writes_nothing(config_dir, clean_registry):
    """Every GET — the tiles and the confirm page — leaves the whole config tree byte-identical."""
    preset_file(config_dir)
    nodes_file(config_dir)
    scheduler_file(config_dir)
    before = tree_digests(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        await page(http, presets_page.PRESETS_PATH)
        await page(http, f"{presets_page.PRESETS_PATH}?file=")
        await page(http, presets_page.removal_url("Morning"))
    assert tree_digests(config_dir) == before, "a GET changed the config tree"
