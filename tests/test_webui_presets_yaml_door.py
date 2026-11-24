"""The YAML door on every preset tile — creation of an expert-DSL preset without a new page.

One section per acceptance criterion:

* every preset tile carries exactly ONE YAML door; a ``modify`` tile carries it once and no cog;
* the shared tile component renders the door only when a caller passes it: a plugin, service or
  extension tile is byte-identical to the markup it produced before the door existed;
* a weather preset saved through the YAML page with a ``modify`` block becomes ``KIND_MODIFY``: its
  tile is badged expert DSL, offers no wizard cog and its YAML door still opens the editor;
* the YAML page states the conversion sentence once, and only for a preset that is not yet the DSL;
* the door follows ``presets.view`` while the save still demands ``presets.write``;
* the refusals are the existing ones: the schema still decides, nothing is written on a refusal.

Driven in-process through the real application (the production factory, the shell and the local
login backend), reusing the write suite's harness so the app under test is the running one.
"""
from __future__ import annotations

import hashlib
import html as html_module
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from core.utils import configwrite
from services.webservice import permissions
from services.webservice.auth import local as local_auth
from services.webservice.pages import presets as presets_page
from test_webui_write_pause import (  # noqa: F401 - the fixtures are imported on purpose
    PASSWORD, build, clean_registry, client, fleet, sign_in,
)

TEMPLATES = Path(__file__).resolve().parent.parent / "services" / "webservice" / "templates"

BOT_YAML = """\
roles:
  Admin:
    - admin
  Viewer:
    - viewer
"""

PRESETS_YAML = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  clouds:
    preset: Preset1
Casual Weather:
  - Spring
Carrier Freqs:
  modify:
    file: mission
"""

#: Spring's block after the operator replaces its value with the DSL — the conversion path.
SPRING_AS_MODIFY = """\
Spring:
  modify:
    file: mission
"""

#: VALID YAML the preset schema REFUSES (``terrain`` is an enum) — the schema still decides.
INVALID_TERRAIN = """\
Carrier Freqs:
  modify:
    file: mission
  terrain: Nowhere
"""

#: The exact ``tile_card`` markup a plugin tile produced BEFORE the optional door existed, captured
#: from the shipped component. A caller that leaves the door unset must still produce this byte for
#: byte, so the shared component's other pages cannot drift.
GOLDEN_PLUGIN_TILE = """\
<article class="st-tile plugin">
  <div class="st-top"><span class="st-kind"><svg class="k" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3.4" y="3.4" width="17.2" height="17.2" rx="4.6"/><path d="M14.6 7.6 9.4 16.4"/></svg>Plugin</span><span class="st-scope">node &amp; instances</span></div>
  <h3 class="st-name">Sample</h3>
  <p class="st-desc clamp2">A plugin.</p>
  
  
  <div class="st-foot">
  <form class="st-swform" method="post" action="/settings/x">
  <input type="hidden" name="a" value="b">
  
  <button type="submit" class="st-sw on" title="On"><span class="bx" aria-hidden="true"><i></i></span>On</button>
</form>
  
  <span class="st-sp"></span>
  <a class="st-trash" href="/settings/remove" title="Remove" aria-label="Remove"><svg class="ic24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4.6 6.6h14.8"/><path d="M9.6 6.6V4.9a1.9 1.9 0 0 1 1.9-1.9h1a1.9 1.9 0 0 1 1.9 1.9v1.7"/><path d="M6.6 6.6 7.5 18.9a1.9 1.9 0 0 0 1.9 1.8h5.2a1.9 1.9 0 0 0 1.9-1.8l.9-12.3"/></svg></a>
  <a class="st-cog" href="/settings/plugin" title="Sample"><svg class="ic24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="3.1"/><path d="M19.1 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14.2 3h-4l-.4 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.6 7.6 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.06-.4.1-.8.1-1.2Z"/></svg>Config</a>
</div>
</article>"""

_TILE = re.compile(r'<article class="st-tile preset[^"]*">')
_DOOR = re.compile(r'<a class="st-yaml"[^>]*>')


def _hash() -> str:
    return local_auth.hash_password(PASSWORD, iterations=local_auth.MIN_ITERATIONS)


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")
    return tmp_path


def _app(config_dir, roles=("Admin",)):
    users = [{"username": "staff", "password_hash": _hash(), "roles": list(roles)}]
    return build(config_dir, fleet(), users=users)


def _macro_environment() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True,
                      extensions=["jinja2.ext.i18n"])
    env.install_null_translations()
    env.globals["_"] = lambda text: text
    return env


def shared_tile(**kwargs) -> str:
    """``_tiles.html``'s own ``tile_card``, rendered on its own for the arguments under test."""
    module = _macro_environment().get_template("_tiles.html").make_module(
        {"_": lambda text: text, "cog_label": "Config"})
    return str(module.tile_card(**kwargs)).strip()


def tiles(html: str) -> list[str]:
    return [chunk for chunk in _TILE.split(html)[1:]]


def a_tile(html: str, name: str) -> str:
    matches = [chunk for chunk in tiles(html) if f'<h3 class="st-name">{name}</h3>' in chunk]
    assert len(matches) == 1, f"expected exactly one tile for '{name}', got {len(matches)}"
    return matches[0]


def doors(tile: str) -> list[str]:
    return _DOOR.findall(tile)


def _csrf_and_revision(page: str) -> tuple[str, str]:
    csrf = re.search(r'name="_csrf_token" value="([^"]*)"', page)
    revision = re.search(r'name="_revision" value="([^"]*)"', page)
    assert csrf is not None and revision is not None, "the page rendered no form carriers"
    return csrf.group(1), revision.group(1)


def _text(text: str) -> str:
    """A sentence as the page renders it (Jinja escapes the quote the same way)."""
    return html_module.escape(text, quote=True).replace("&#x27;", "&#39;")


def _notice(page: str) -> str:
    found = re.search(r'notice-msg">([^<]*)<', page)
    assert found is not None, "no notice was rendered"
    return html_module.unescape(found.group(1))


async def _open(http, url: str) -> str:
    response = await http.get(url)
    assert response.status_code == 200, f"{url} answered {response.status_code}: {response.text[:300]}"
    return response.text


# ------------------------------------------------------- 1. one door on every preset tile

@pytest.mark.asyncio
async def test_every_preset_tile_carries_exactly_one_yaml_door(config_dir, clean_registry):
    """One door per tile; the modify tile's door is its only editor and no cog sits beside it."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, presets_page.PRESETS_PATH)

    for name in ("Spring", "Casual Weather", "Carrier Freqs"):
        tile = a_tile(page, name)
        found = doors(tile)
        assert len(found) == 1, f"'{name}' carries {len(found)} YAML doors"
        assert f'href="{presets_page.preset_yaml_url(name)}"' in found[0], \
            f"'{name}' door does not open its own YAML page"
        assert 'aria-label="Edit YAML"' in found[0] and 'title="Edit YAML"' in found[0], \
            f"'{name}' door is unlabelled"

    expert = a_tile(page, "Carrier Freqs")
    assert "st-cog" not in expert, "the expert tile drew a cog beside its YAML door"
    assert "/wizard" not in expert
    weather = a_tile(page, "Spring")
    assert "st-cog" in weather, "a weather preset lost its wizard cog"
    assert f'href="{presets_page.wizard_url("Spring")}"' in weather


# ------------------------------------------- 2. the shared component's other pages do not change

def test_a_plugin_tile_is_byte_identical_when_the_door_is_unset():
    """The optional door unset reproduces the pre-change markup exactly — no new control, no drift."""
    rendered = shared_tile(
        kind="plugin", kind_word="Plugin", name="Sample", description="A plugin.",
        sw={"type": "form", "action": "/settings/x", "fields": [["a", "b"]],
            "list_values": [], "on": True, "title": "On", "label": "On"},
        cog_url="/settings/plugin", cog_title="Sample",
        scope="node &amp; instances", trash_url="/settings/remove", trash_title="Remove")
    assert rendered == GOLDEN_PLUGIN_TILE, "the plugin tile markup changed with the door unset"
    assert "st-yaml" not in rendered, "a tile with no door argument drew a door"


def test_service_and_extension_tiles_draw_no_door_when_it_is_unset():
    for kind in ("service", "ext"):
        rendered = shared_tile(kind=kind, kind_word="Kind", name="Sample", description="",
                               sw=None, cog_url="/x", cog_title="Sample")
        assert "st-yaml" not in rendered, f"a {kind} tile drew a YAML door it was never given"


# --------------------------------- 3. the creation path: weather preset -> saved modify block

@pytest.mark.asyncio
async def test_a_weather_preset_saved_as_modify_becomes_an_expert_dsl_tile(config_dir,
                                                                         clean_registry):
    """The wizard names the preset, this page turns it into the DSL, and its kind decides the tile."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        before = await _open(http, presets_page.PRESETS_PATH)
    assert "st-cog" in a_tile(before, "Spring")

    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, presets_page.preset_yaml_url("Spring"))
        csrf, revision = _csrf_and_revision(page)
        saved = await http.post(presets_page.preset_yaml_save_url("Spring"), data={
            "_csrf_token": csrf, "_revision": revision, "_yaml": SPRING_AS_MODIFY})
        assert saved.status_code == 303, saved.text[:300]
        after = await _open(http, presets_page.PRESETS_PATH)
        editor = await http.get(presets_page.preset_yaml_url("Spring"))

    stored = configwrite.load_config_text(
        (config_dir / "presets.yaml").read_text(encoding="utf-8"),
        path=config_dir / "presets.yaml")
    assert stored["Spring"] == {"modify": {"file": "mission"}}, "the conversion did not land"
    assert stored["Casual Weather"] == ["Spring"], "the conversion touched another preset"

    tile = a_tile(after, "Spring")
    assert presets_page.KIND_LABELS[presets_page.KIND_MODIFY] in tile, \
        "the converted preset is not badged expert DSL"
    assert "st-cog" not in tile, "the converted preset kept its wizard cog"
    assert "/wizard" not in tile, "the converted preset still offers the wizard"
    found = doors(tile)
    assert len(found) == 1 and f'href="{presets_page.preset_yaml_url("Spring")}"' in found[0], \
        "the converted preset lost its YAML door"
    assert editor.status_code == 200, editor.text[:300]


# ------------------------------------------------ 4. the conversion sentence, stated once

@pytest.mark.asyncio
async def test_the_yaml_page_states_the_conversion_once_and_only_before_it_is_dsl(config_dir,
                                                                                 clean_registry):
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        weather = await _open(http, presets_page.preset_yaml_url("Spring"))
        expert = await _open(http, presets_page.preset_yaml_url("Carrier Freqs"))

    sentence = _text(presets_page.PRESETS_YAML_CONVERT_HINT)
    assert weather.count(sentence) == 1, "the conversion sentence is not stated exactly once"
    assert "modify: …" in sentence and "expert-DSL preset" in sentence
    assert sentence not in expert, "an expert-DSL preset is told how to become one"


# ------------------------------------------------- 5. presets.view draws the door, write saves

@pytest.mark.asyncio
async def test_the_door_follows_presets_view_while_the_save_needs_presets_write(config_dir,
                                                                               clean_registry,
                                                                               monkeypatch):
    """A view-only caller reads the tiles and their doors; the save route still refuses them."""
    app = _app(config_dir, roles=("Viewer",))
    # Grant the read capability to the test's own role AFTER the app declared the page's roles.
    monkeypatch.setitem(permissions.CAPABILITIES, presets_page.PRESETS_CAPABILITY, ("Viewer",))
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, presets_page.PRESETS_PATH)
        csrf, revision = _csrf_and_revision(await _open(http,
                                                        presets_page.preset_yaml_url("Spring")))
        refused = await http.post(presets_page.preset_yaml_save_url("Spring"), data={
            "_csrf_token": csrf, "_revision": revision, "_yaml": SPRING_AS_MODIFY})

    assert len(doors(a_tile(page, "Spring"))) == 1, "a presets.view caller got no YAML door"
    assert refused.status_code == 403, f"a view-only caller could save: {refused.status_code}"


# ------------------------------------------------------- 6. the refusals are the existing ones

@pytest.mark.asyncio
async def test_the_schema_still_refuses_an_invalid_block_with_nothing_written(config_dir,
                                                                             clean_registry):
    path = config_dir / "presets.yaml"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, presets_page.preset_yaml_url("Carrier Freqs"))
        csrf, revision = _csrf_and_revision(page)
        refused = await http.post(presets_page.preset_yaml_save_url("Carrier Freqs"), data={
            "_csrf_token": csrf, "_revision": revision, "_yaml": INVALID_TERRAIN})
        landed = await _open(http, presets_page.preset_yaml_url("Carrier Freqs"))

    assert refused.status_code == 303
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before, "a refused block was written"
    assert _notice(landed).startswith(presets_page.PRESETS_REFUSED_BANNER)
