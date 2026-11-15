"""The Presets page's tiles, its ONE removal door and its per-file tabs.

One section per acceptance criterion of the card:

* the preset card IS the console's tile component (``_tiles.html``'s ``tile_card``), drawn with no
  switch and a foot of trash + cog — and its kind draws the PRESET glyph, never the plugin fallback;
* a ``modify`` preset keeps its own note and its YAML door opens the Presets area's one-block editor
  for that preset — never the wizard, and with no cog beside the door;
* the trash is drawn only for a preset nothing references;
* ``GET /presets?remove=<name>`` is the ONE removal door: it shows the preset's document and EVERY
  reference (node/instance, combination, Scheduler), and its form posts to the EXISTING write route;
  a hand-made POST of a preset still in use is refused with the unchanged sentence;
* the wizard's review step draws no remove control any more;
* the tab strip: one tab per preset file in the strip order, "All files" last, the counts right,
  ``?file=`` in the URL, the search and the kind kept across a tab switch, no strip for one file, a
  stale ``?file=`` falling back with its own sentence, and an unreadable file keeping its tab;
* the GET writes nothing: every preset file's bytes are sha256-identical afterwards.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), with the write suite's own harness reused so the app under test is the running one.
"""
from __future__ import annotations

import hashlib
import html as html_module
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from services.webservice.pages import presets as presets_page
from services.webservice.pages import settings as settings_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, fleet, sign_in,
)

TEMPLATES = Path(__file__).resolve().parent.parent / "services" / "webservice" / "templates"

PRESETS_YAML = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  clouds:
    preset: Preset1
Morning:
  start_time: 08:00
Casual Weather:
  - Summer
  - Morning
Carrier Freqs:
  modify:
    file: mission
"""

OTHER_FILE = """\
Summer:
  temperature: 23
Autumn:
  temperature: 4
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
      - Spring
"""

_TILE = re.compile(r'<article class="st-tile preset">')


# ------------------------------------------------------------------------------ the harness

def preset_file(root: Path, text: str = PRESETS_YAML, name: str = "presets.yaml") -> Path:
    """Write a preset file into the install's config directory and return its path."""
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def _editor_url(root: Path, name: str = "Carrier Freqs") -> str:
    """The Presets page's OWN one-block editor URL — the URL a modify tile's cog must carry.

    It names the PRESET, not the file: two presets of one file are two distinct pages.
    """
    return presets_page.preset_yaml_url(name)


def nodes_file(root: Path, text: str) -> Path:
    path = root / "nodes.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def scheduler_file(root: Path, text: str) -> Path:
    directory = root / "plugins"
    directory.mkdir(exist_ok=True)
    path = directory / "scheduler.yaml"
    path.write_text(text, encoding="utf-8")
    return path


async def page(http, path: str) -> str:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


def tiles(html: str) -> list[str]:
    """The page's preset tiles, in render order."""
    return [chunk for chunk in _TILE.split(html)[1:]]


def a_tile(html: str, name: str) -> str:
    """The ONE tile whose name is *name*."""
    matches = [chunk for chunk in tiles(html) if f'<h3 class="st-name">{name}</h3>' in chunk]
    assert len(matches) == 1, f"expected exactly one tile for '{name}', got {len(matches)}"
    return matches[0]


def tile_names(html: str) -> list[str]:
    return re.findall(r'<h3 class="st-name">([^<]*)</h3>', html)


def tabs_of(html: str) -> list[str]:
    """Every tab's text, in render order."""
    return [chunk.strip() for chunk in
            re.findall(r'<a class="ps-tab[^"]*" href="[^"]*">(.*?)</a>', html, re.S)]


def _squash(text: str) -> str:
    """Markup with every run of whitespace between tags removed — the comparison unit for two renders."""
    return re.sub(r">\s+<", "><", text.strip())


def _text(text: str) -> str:
    """A sentence as the page renders it (Jinja escapes the quote the same way)."""
    return html_module.escape(text, quote=True).replace("&#x27;", "&#39;")


def _href(url: str) -> str:
    """A URL as the page writes it into an attribute (Jinja escapes the separator)."""
    return url.replace("&", "&amp;")


def hidden_fields(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in re.finditer(r'<input type="hidden" name="([^"]*)" value="([^"]*)"', html):
        out[match.group(1)] = html_module.unescape(match.group(2))
    return out


def digests(config_dir: Path) -> dict[str, str]:
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(config_dir.rglob("presets*.yaml"))}


def _macro_env() -> Environment:
    """A plain Jinja environment over the shipped templates, translating nothing (English source)."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True,
                      extensions=["jinja2.ext.i18n"])
    env.install_null_translations()
    env.globals["_"] = lambda text: text
    return env


def shared_tile(**kwargs) -> str:
    """``_tiles.html``'s own ``tile_card``, rendered on its own for the arguments under test."""
    env = _macro_env()
    module = env.get_template("_tiles.html").make_module({"_": lambda text: text,
                                                          "cog_label": "Config"})
    assert module is not None
    return str(module.tile_card(**kwargs)).strip()


# ------------------------------------------------------- 1. the card IS the shared tile component

@pytest.mark.asyncio
async def test_the_preset_card_is_the_console_tile_with_no_switch(config_dir, clean_registry):
    """The card is ``tile_card`` itself: ``st-tile preset``, no switch, and a trash + cog foot."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)

    assert "ps-card" not in html, "the page still draws its own card lookalike"
    card = a_tile(html, "Spring")
    for marker in ("st-top", "st-kind", "st-name", "st-foot", "st-sp", "st-trash", "st-cog",
                   "st-yaml"):
        assert marker in card, f"the tile carries no '{marker}'"
    assert "st-sw" not in card, "a preset tile drew an on/off switch"
    assert "Config" in card, "the cog rendered without its label"

    # the SAME macro, fed the same arguments, produces the markup the page rendered
    expected = shared_tile(
        kind="preset", kind_word="Preset", name="Spring", description="", sw=None,
        cog_url=presets_page.wizard_url("Spring"), cog_title="Spring",
        scope="its own values",
        body=Markup('<div class="ps-sets" aria-label="keys this preset writes">'
                    "<span>date</span><span>temperature</span><span>clouds</span></div>"
                    '<div class="st-src">config/presets.yaml</div>'),
        trash_url=presets_page.removal_url("Spring"), trash_title="Remove preset",
        yaml_url=presets_page.preset_yaml_url("Spring"), yaml_title="Edit YAML")
    assert _squash(expected) in _squash(html), \
        "the page's card is not the shared macro's own output"


@pytest.mark.asyncio
async def test_the_preset_kind_draws_the_preset_glyph_not_the_plugin_one(config_dir,
                                                                        clean_registry):
    """An unknown kind silently falls back to the plugin glyph; ``preset`` must not."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)
    card = a_tile(html, "Spring")
    assert "M7.2 3.2h6.1l3.5 3.5v9.6" in card, "the preset glyph is missing from the tile"
    assert "M14.6 7.6 9.4 16.4" not in card, "the tile drew the PLUGIN glyph for a preset"


@pytest.mark.asyncio
async def test_a_modify_preset_opens_the_yaml_editor_and_keeps_its_note(config_dir, clean_registry):
    """The wizard does not open the expert DSL, so its one editor is the YAML door.

    The URL must be this area's OWN one-block editor for that preset, never a hand-built string or a
    wizard link; the door is drawn once and no cog sits beside it.
    """
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)
        # the editor the cog points at is the page this console serves for that very file
        editor = await http.get(_editor_url(config_dir))
    assert editor.status_code == 200, editor.text[:300]
    expert = a_tile(html, "Carrier Freqs")
    assert "st-yaml" in expert, "a preset nobody can edit offered no door at all"
    assert "st-cog" not in expert, "the expert tile drew a wizard cog beside its YAML door"
    assert f'href="{_editor_url(config_dir)}"' in expert, \
        "the expert tile's YAML door is not this area's own URL for the preset"
    assert "/wizard" not in expert
    assert presets_page.PRESETS_MODIFY_NOTE in expert, "the expert tile lost its note"
    spring = a_tile(html, "Spring")
    assert f'href="{presets_page.wizard_url("Spring")}"' in spring, "an openable preset lost its wizard"
    assert _editor_url(config_dir) not in spring, "a wizard preset offered the file editor too"


# ---------------------------------------------------------------- 2. the trash, and the one door

@pytest.mark.asyncio
async def test_the_trash_is_absent_for_a_preset_in_use_and_present_otherwise(config_dir,
                                                                            clean_registry):
    """A referenced preset carries no trash; an unreferenced one links to the confirm page."""
    preset_file(config_dir)
    nodes_file(config_dir, NODES_REFERENCING)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)
    assert presets_page.removal_url("Morning") not in html, "an in-use preset still offers a trash"
    assert presets_page.removal_url("Spring") in a_tile(html, "Spring")

    # the combination is a reference too
    for name in ("Summer", "Morning"):
        assert presets_page.removal_url(name) not in html, \
            f"'{name}' is named by a combination preset and still offers a trash"


@pytest.mark.asyncio
async def test_the_confirm_page_lists_every_kind_of_reference(config_dir, clean_registry):
    """Node/instance settings, a combination preset and the Scheduler are all named."""
    preset_file(config_dir)
    nodes_file(config_dir, NODES_REFERENCING)
    scheduler_file(config_dir, SCHEDULER_REFERENCING)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.removal_url("Morning"))
        scheduled = await page(http, presets_page.removal_url("Spring"))

    assert "node &#39;NODE1&#39;, instance &#39;Instance1&#39;" in html
    assert "the preset &#39;Casual Weather&#39;" in html
    assert "config/presets.yaml" in html
    assert _text(presets_page.PRESETS_REMOVAL_SENTENCE) in html
    assert "Morning:" in html, "the confirm page did not show the preset's own document"
    assert "st-trash" not in html and "Remove preset" not in html, \
        "the confirm page drew a remove control for a preset in use"
    assert "the scheduler&#39;s default section" in scheduled, "the Scheduler reference is missing"


@pytest.mark.asyncio
async def test_the_confirm_page_posts_to_the_one_write_route(config_dir, clean_registry):
    """The button is a form POST to ``PRESETS_SAVE_PATH`` with the action, the name and the revision."""
    preset_file(config_dir, OTHER_FILE, name="presets2.yaml")
    scheduler_file(config_dir, SCHEDULER_REFERENCING)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.removal_url("Autumn"))
    assert re.search(r'<form[^>]*method="post"[^>]*action="' +
                     re.escape(presets_page.PRESETS_SAVE_PATH) + '"', html), \
        "the confirm page drew no POST to the write route"
    fields = hidden_fields(html)
    assert fields[presets_page.PRESETS_ACTION_FIELD] == presets_page.PRESETS_ACTION_REMOVE
    assert fields[presets_page.PRESETS_ORIGINAL_FIELD] == "Autumn"
    assert fields[presets_page.PRESETS_REVISION_FIELD], "the form carries no file revision"
    assert fields["_csrf_token"]


@pytest.mark.asyncio
async def test_the_confirm_post_removes_the_preset_through_the_existing_route(config_dir,
                                                                             clean_registry):
    """The removal lands through the ONE write route, and only the named preset goes."""
    from core.utils import configwrite

    path = preset_file(config_dir, OTHER_FILE, name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = hidden_fields(await page(http, presets_page.removal_url("Autumn")))
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        assert reply.headers["location"] == presets_page.PRESETS_PATH
    document = configwrite.load_config_file(path)
    assert "Autumn" not in document, "the removal did not land"
    assert document["Summer"]["temperature"] == 23, "the removal took another preset with it"


@pytest.mark.asyncio
async def test_a_hand_made_removal_post_of_a_preset_in_use_is_refused(config_dir, clean_registry):
    """The route's own guard is the backstop for a form the page never draws."""
    path = preset_file(config_dir)
    nodes_file(config_dir, NODES_REFERENCING)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = hidden_fields(
            await page(http, f"{presets_page.wizard_url('Morning')}?step=review"))
        data[presets_page.PRESETS_ACTION_FIELD] = presets_page.PRESETS_ACTION_REMOVE
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        after = await page(http, presets_page.PRESETS_PATH)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before, "an in-use preset was removed"
    assert "NODE1" in after, "the refusal does not name the node that still references the preset"


@pytest.mark.asyncio
async def test_the_wizard_draws_no_remove_control(config_dir, clean_registry):
    """The review step offers the save alone; the removal lives on the Presets page."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    assert f'value="{presets_page.PRESETS_ACTION_REMOVE}"' not in html
    assert f'value="{presets_page.PRESETS_ACTION_SAVE}"' in html
    assert presets_page.PRESETS_REMOVAL_SENTENCE not in html


# --------------------------------------------------------------------------- 3. the tab strip

@pytest.mark.asyncio
async def test_the_first_tab_is_presets_yaml_and_it_is_the_default(config_dir, clean_registry):
    """``presets.yaml`` comes first and is what the page shows when no tab is named."""
    preset_file(config_dir)
    preset_file(config_dir, OTHER_FILE, name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)

    labels = tabs_of(html)
    assert labels[0].startswith("config/presets.yaml"), labels
    assert labels[-1].startswith(presets_page.PRESETS_ALL_FILES), labels
    assert tile_names(html) == ["Spring", "Morning", "Casual Weather", "Carrier Freqs"], \
        "the default tab is not presets.yaml alone"
    assert 'class="ps-tab on" href="/presets?file=config/presets.yaml"' in html, \
        "the presets.yaml tab does not look selected"


@pytest.mark.asyncio
async def test_the_strip_puts_presets_yaml_before_the_presets_directory(config_dir,
                                                                      clean_registry):
    """``presets.yaml`` first, the other flat files, then ``config/presets/*.yaml`` — not discovery order.

    ``discover_preset_files`` lists the ``presets/`` directory FIRST (the order the bot reads them in);
    the STRIP is ordered for the operator, and that difference is what this test pins.
    """
    preset_file(config_dir)
    directory = config_dir / "presets"
    directory.mkdir()
    (directory / "aaa.yaml").write_text("Winter:\n  temperature: 2\n", encoding="utf-8")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)

    labels = [tab.split(" <b>")[0] for tab in tabs_of(html)]
    assert labels == ["config/presets.yaml", "config/presets/aaa.yaml",
                      presets_page.PRESETS_ALL_FILES], labels
    assert tile_names(html) == ["Spring", "Morning", "Casual Weather", "Carrier Freqs"]


@pytest.mark.asyncio
async def test_the_counts_are_per_file_and_all_files_shows_everything(config_dir, clean_registry):
    preset_file(config_dir)
    preset_file(config_dir, OTHER_FILE, name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)
        everything = await page(http, f"{presets_page.PRESETS_PATH}?file=")

    assert "config/presets.yaml <b>4</b>" in html
    assert "config/presets2.yaml <b>2</b>" in html
    assert f"{presets_page.PRESETS_ALL_FILES} <b>6</b>" in html
    assert tile_names(html) == ["Spring", "Morning", "Casual Weather", "Carrier Freqs"]
    assert len(tile_names(everything)) == 6, "All files did not show every file's presets"


@pytest.mark.asyncio
async def test_a_file_tab_shows_only_that_file_and_keeps_the_search(config_dir, clean_registry):
    """A tab switch keeps the search; a search keeps the tab; the kind filter too."""
    preset_file(config_dir)
    preset_file(config_dir, OTHER_FILE, name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.PRESETS_PATH}?file=config/presets2.yaml")
        searched = await page(http, f"{presets_page.PRESETS_PATH}?file=config/presets2.yaml&q=autumn")
        # the tab's URL carries the kind filter too
        kinded = await page(http, f"{presets_page.PRESETS_PATH}?file=config/presets.yaml"
                                  f"&kind={presets_page.KIND_COMBINATION}")

    assert tile_names(html) == ["Summer", "Autumn"] and "Spring" not in html
    # the search form carries the tab, so submitting it stays in the file
    assert '<input type="hidden" name="file" value="config/presets2.yaml">' in html
    # every other tab's URL carries the search, so a tab switch keeps it
    assert _href(presets_page.preset_tab_url("config/presets.yaml",
                                             query="autumn")) in searched
    assert tile_names(searched) == ["Autumn"], "the search did not stay inside the tab"
    assert _href(presets_page.preset_tab_url("config/presets2.yaml",
                                             kind=presets_page.KIND_COMBINATION)) in kinded


@pytest.mark.asyncio
async def test_one_file_draws_no_strip(config_dir, clean_registry):
    """A tab strip of one is noise: it is not drawn at all."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)
    assert "ps-tabs" not in html
    assert tile_names(html) == ["Spring", "Morning", "Casual Weather", "Carrier Freqs"]


@pytest.mark.asyncio
async def test_a_stale_file_tab_falls_back_to_every_file_and_says_so(config_dir, clean_registry):
    """A bookmark for a file that is gone renders the whole area, with the sentence that says it."""
    preset_file(config_dir)
    preset_file(config_dir, OTHER_FILE, name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.PRESETS_PATH}?file=config/gone.yaml")
    assert len(tile_names(html)) == 6, "the stale tab did not fall back to every file"
    assert "config/gone.yaml" in html and "is not here any more" in html


@pytest.mark.asyncio
async def test_an_unreadable_file_keeps_its_tab_with_its_own_sentence(config_dir, clean_registry):
    """A broken file is a tab with count 0 and its own state — never a tab that vanishes."""
    preset_file(config_dir, "Spring:\n  date: [unclosed\n", name="presets.yaml")
    preset_file(config_dir, OTHER_FILE, name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        broken = await page(http, f"{presets_page.PRESETS_PATH}?file=config/presets.yaml")
        everything = await page(http, f"{presets_page.PRESETS_PATH}?file=")

    assert "config/presets.yaml <b>0</b>" in broken, "the broken file lost its tab"
    assert "config/presets.yaml could not be read as YAML" in broken
    assert "config/presets2.yaml <b>2</b>" in broken
    # "All files" keeps the global notice, and the readable file's presets still render
    assert "config/presets.yaml could not be read as YAML" in everything
    assert tile_names(everything) == ["Summer", "Autumn"]


@pytest.mark.asyncio
async def test_a_new_preset_on_a_tab_carries_that_file(config_dir, clean_registry):
    """The wizard opens on the tab's own file, so the destination step is the right one."""
    preset_file(config_dir)
    preset_file(config_dir, OTHER_FILE, name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.PRESETS_PATH}?file=config/presets2.yaml")
    assert (f'href="{presets_page.PRESETS_NEW_PATH}?file=config/presets2.yaml"' in html), \
        "New preset did not carry the active tab's file"


@pytest.mark.asyncio
async def test_the_page_still_writes_nothing(config_dir, clean_registry):
    """Every GET the page offers leaves the config tree byte-identical."""
    preset_file(config_dir)
    preset_file(config_dir, OTHER_FILE, name="presets2.yaml")
    nodes_file(config_dir, NODES_REFERENCING)
    before = digests(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        await page(http, presets_page.PRESETS_PATH)
        await page(http, f"{presets_page.PRESETS_PATH}?file=config/presets2.yaml&q=summer")
        await page(http, presets_page.removal_url("Autumn"))
    assert digests(config_dir) == before, "a GET changed a preset file"
    assert not list(config_dir.glob("*.bak")), "a GET left a backup behind"
