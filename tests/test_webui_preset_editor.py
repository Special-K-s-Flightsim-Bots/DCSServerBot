"""The settings area has no preset door any more: a preset is edited on the Presets page only.

A preset file is not a global Settings file: it carries many presets and the Presets page draws one
tile per preset, each opening that preset's own block. The Settings area's parameterised file editor
therefore no longer resolves the preset kind, and the audience union that existed only to admit
preset-editing callers is gone with it — one editor for presets, the Presets page's own.

One section per acceptance criterion:

* ``/settings/preset/<stem>`` answers 404 again;
* the preset kind, its resolver, its entries helper and the capability union are gone from the module
  (nothing keeps a permission nobody consults);
* the parameterised file routes declare the Settings audience only, and ``file_capability`` names it
  for every kind;
* a modify preset's tile's cog is the Presets page's own YAML page, never the Settings editor.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import permissions
from services.webservice.auth import local as local_auth
from services.webservice.pages import presets as presets_page
from services.webservice.pages import settings as settings_page
from test_webui_write_pause import (  # noqa: F401 - the fixtures are imported on purpose
    PASSWORD, build, clean_registry, client, fleet, sign_in,
)

BOT_YAML = """\
roles:
  Admin:
    - admin
"""

PRESETS_YAML = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  clouds:
    preset: Preset1
Carrier Freqs:
  modify:
    file: mission
    variables:
      theatre: theatre
"""


def _hash() -> str:
    return local_auth.hash_password(PASSWORD, iterations=local_auth.MIN_ITERATIONS)


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")
    return tmp_path


def _app(config_dir):
    users = [{"username": "staff", "password_hash": _hash(), "roles": ["Admin"]}]
    return build(config_dir, fleet(), users=users)


def _settings_preset_url(name: str = "presets") -> str:
    """The URL the retired door used to serve — spelled literally, since the kind is gone."""
    return f"/settings/preset/{name}"


# ------------------------------------------------------ 1. the settings door is closed again

@pytest.mark.asyncio
async def test_the_settings_preset_route_is_a_404_again(config_dir):
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        response = await http.get(_settings_preset_url())
    assert response.status_code == 404, \
        f"the settings area still serves a preset file: {response.status_code}"


@pytest.mark.asyncio
async def test_no_settings_path_resolves_a_preset_file(config_dir):
    """Every kind the Settings area offers still resolves; the preset kind resolves to nothing."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        main = await http.get(settings_page.SETTINGS_MAIN_PATH)
        preset = await http.get(_settings_preset_url("presets"))
    assert main.status_code == 200, "the settings area stopped serving its own files"
    assert preset.status_code == 404


# ------------------------------------------------------ 2. the kind and its helpers are retired

def test_the_preset_kind_is_gone_from_the_settings_module():
    """The module keeps no resolver, no entries helper and no kind for a file it does not edit."""
    for name in ("KIND_PRESET", "preset_files", "preset_editor_url", "_preset_schema_files"):
        assert not hasattr(settings_page, name), f"settings.{name} still ships with no consumer"
    assert "KIND_PRESET" not in settings_page.__all__
    assert "preset_files" not in settings_page.__all__
    assert "preset_editor_url" not in settings_page.__all__


def test_the_file_routes_declare_the_settings_audience_only():
    """The union existed for the preset door; with the door gone the declared audience is Settings."""
    caps = settings_page.capabilities()
    for path, capability in ((settings_page.SETTINGS_FILE_PATH,
                              settings_page.SETTINGS_FILE_VIEW_CAPABILITY),
                             (settings_page.SETTINGS_FILE_SAVE_PATH,
                              settings_page.SETTINGS_FILE_WRITE_CAPABILITY),
                             (settings_page.SETTINGS_VALIDATE_FILE_PATH,
                              settings_page.SETTINGS_FILE_WRITE_CAPABILITY)):
        assert caps[path] == capability
        assert permissions.roles_for_capability(capability) == ("Admin",), \
            "a preset audience is still folded into the settings file routes"


def test_every_kind_names_the_settings_capability():
    for kind in (settings_page.KIND_MAIN, settings_page.KIND_SERVICE, settings_page.KIND_PLUGIN,
                 "anything-else"):
        assert settings_page.file_capability(kind, write=False) == settings_page.SETTINGS_VIEW_CAPABILITY
        assert settings_page.file_capability(kind, write=True) == settings_page.SETTINGS_WRITE_CAPABILITY


@pytest.fixture
def split_audiences():
    """The Presets page's audience moved AWAY from Settings', so a union would be visible.

    The shipped pair is ``("Admin",)`` on both sides, where a union and one audience look identical.
    Splitting them is what makes the difference observable: the file routes serve this area's files
    only, so their declared audience must stay Settings' own.
    """
    saved = dict(permissions.CAPABILITIES)
    saved_roles = presets_page.PRESETS_ROLES
    presets_page.PRESETS_ROLES = ("PresetAdmin",)
    permissions.CAPABILITIES[presets_page.PRESETS_CAPABILITY] = ("PresetAdmin",)
    permissions.CAPABILITIES[presets_page.PRESETS_WRITE_CAPABILITY] = ("PresetAdmin",)
    try:
        yield
    finally:
        presets_page.PRESETS_ROLES = saved_roles
        permissions.CAPABILITIES.clear()
        permissions.CAPABILITIES.update(saved)


def test_the_file_capabilities_never_widen_to_the_presets_audience(split_audiences):
    """A preset audience is not folded into the Settings file routes' audience."""
    for capability in (settings_page.SETTINGS_FILE_VIEW_CAPABILITY,
                       settings_page.SETTINGS_FILE_WRITE_CAPABILITY):
        permissions.CAPABILITIES.pop(capability, None)
    settings_page.declare_file_capabilities()
    for capability in (settings_page.SETTINGS_FILE_VIEW_CAPABILITY,
                       settings_page.SETTINGS_FILE_WRITE_CAPABILITY):
        assert permissions.roles_for_capability(capability) == ("Admin",), \
            f"{capability} carries a preset audience the settings area does not serve"


# ------------------------------------------------------ 3. the cog points at the Presets page

@pytest.mark.asyncio
async def test_the_modify_cog_is_the_presets_pages_own_yaml_page(config_dir):
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        tiles = (await http.get(presets_page.PRESETS_PATH)).text
        editor = await http.get(presets_page.preset_yaml_url("Carrier Freqs"))
    assert editor.status_code == 200, editor.text[:300]
    expected = presets_page.preset_yaml_url("Carrier Freqs")
    assert f'href="{expected}"' in tiles, "the modify tile's cog is not this preset's YAML page"
    assert _settings_preset_url() not in tiles, "a preset tile still links the settings editor"
    assert presets_page.wizard_url("Carrier Freqs") not in tiles, "a modify preset offered a wizard"


def test_the_tile_url_names_the_preset_not_only_the_file(config_dir):
    """TWO presets of one file must not share a cog: the URL carries the preset's own name."""
    assert (presets_page.preset_yaml_url("Spring")
            != presets_page.preset_yaml_url("Carrier Freqs"))
    assert Path(presets_page.preset_yaml_url("Spring")).name == "yaml"
