"""The Settings area's OTHER global files: services, through the EXISTING generator.

Pins the acceptance the card names, per FILE KIND, with a REAL file from the repo:

* the entries list and the file list are read from DISK at render time (a new service file appears
  with no code change);
* a save writes into the file the operator OPENED and a no-op save leaves it BYTE-IDENTICAL;
* an unplaceable posted field REFUSES the save, nothing written (the existing refusal path);
* the page keeps the SAME tabbed schema-driven view a plugin's cogwheel gets — a service is never a
  different renderer (the older single-form ``cfg-layout`` page is gone for services);
* a file that does not validate against its schema is SHOWN AS SUCH, not silently rendered;
* a kind with no schema is read-only and says so.

A PRESET FILE IS NEVER LISTED AND HAS NO DOOR HERE (the Presets page is its
home, and it edits one preset per page). The suite pins both halves: absent from the list, a 404 by
URL.

Driven in-process through the real application, exactly like the main Settings page's tests.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "samples"

#: A minimal but VALID ``main.yaml`` (the install's own file — the first entry in the list).
MAIN_YAML = """\
guild_id: 112233445566
guild_name: My Server
language: en
plugins:
  - mission
"""


def _copy(source: Path, target: Path) -> None:
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


@pytest.fixture
def files_dir(tmp_path):
    """A config dir carrying a REAL service file, plus REAL preset files (which must stay UNLISTED)."""
    (tmp_path / "services").mkdir()
    # bot.yaml must exist: the auth backend reads it for the local users.
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    _copy(SAMPLES / "services" / "dashboard.yaml", tmp_path / "services" / "dashboard.yaml")
    (tmp_path / "main.yaml").write_text(MAIN_YAML, encoding="utf-8")
    # the repo's OWN convention: presets*.yaml files AND a presets/ directory. Both are PARKED, so
    # they exist here to prove the discoverer does NOT list them.
    _copy(SAMPLES / "presets.yaml", tmp_path / "presets.yaml")
    (tmp_path / "presets").mkdir()
    _copy(SAMPLES / "presets_tournament.yaml", tmp_path / "presets" / "tournament.yaml")
    return tmp_path


def _app(files_dir, roles=("Admin",)):
    return _build(files_dir, users=[{"username": "staff", "password_hash": _hash(),
                                     "roles": list(roles)}])


def _service_url(name: str) -> str:
    return settings_page.SETTINGS_FILE_PATH.format(kind=settings_page.KIND_SERVICE, name=name)


def _preset_url(name: str) -> str:
    """A preset file's URL in the Settings area — a path the kind used to serve and no longer does."""
    return f"/settings/preset/{name}"


def _csrf_and_revision(page: str) -> tuple[str, str]:
    csrf = re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)
    revision = re.search(r'name="_revision" value="([^"]*)"', page).group(1)
    return csrf, revision


def _rendered_form(page: str) -> dict:
    """Every ``f.`` field the browser would post, read straight off the rendered page.

    Text/number/url inputs (and a bool's hidden companion) take their ``value``; a checked checkbox
    overrides its companion with ``1``; a textarea takes its (unescaped) text; a select takes the
    ``selected`` option's value. Named exactly as the form names them, so posting the result is a
    faithful "save what you saw" — the honest no-op.

    ``<template>`` content is DROPPED first: it is the JS row-clone seed, and a browser never submits
    inputs inside an inert ``<template>`` — so including them would over-count the form.
    """
    page = re.sub(r"<template\b[^>]*>.*?</template>", "", page, flags=re.S)
    form: dict[str, str] = {}
    for match in re.finditer(r"<input\b[^>]*>", page):
        tag = match.group(0)
        name = re.search(r'name="([^"]*)"', tag)
        if not name or not name.group(1).startswith("f."):
            continue
        value = re.search(r'value="([^"]*)"', tag)
        field_type = re.search(r'type="([^"]*)"', tag)
        if field_type and field_type.group(1) == "checkbox":
            if "checked" in tag:
                form[name.group(1)] = value.group(1) if value else "1"
            continue
        form[name.group(1)] = html.unescape(value.group(1)) if value else ""
    for match in re.finditer(r'<textarea\b[^>]*name="(f\.[^"]*)"[^>]*>(.*?)</textarea>', page, re.S):
        form[match.group(1)] = html.unescape(match.group(2))
    for match in re.finditer(r'<select\b[^>]*name="(f\.[^"]*)"[^>]*>(.*?)</select>', page, re.S):
        selected = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', match.group(2))
        if selected is None:
            selected = re.search(r'<option[^>]*selected[^>]*value="([^"]*)"', match.group(2))
        form[match.group(1)] = html.unescape(selected.group(1)) if selected else ""
    return form


# ------------------------------------------------------ 1. the file list comes from DISK

def test_discover_files_reads_every_kind_from_disk(files_dir):
    entries = settings_page.discover_files(files_dir)
    got = {(entry.kind, entry.name) for entry in entries}
    assert (settings_page.KIND_MAIN, "main") in got
    assert (settings_page.KIND_SERVICE, "bot") in got
    assert (settings_page.KIND_SERVICE, "dashboard") in got
    # PRESETS ARE NOT PART OF THIS AREA: the preset files exist on disk but are not listed, and no
    # kind here names them — a preset is one key of one of those files, edited on its own page.
    assert not [entry for entry in entries if entry.kind == "preset"], \
        "a preset file was listed although this area does not edit preset files"
    # main is listed FIRST, so the install's own file keeps the top slot
    assert entries[0].kind == settings_page.KIND_MAIN


def test_a_new_file_appears_with_no_code_change(files_dir):
    before = {entry.name for entry in settings_page.discover_files(files_dir)
              if entry.kind == settings_page.KIND_SERVICE}
    (files_dir / "services" / "brandnew.yaml").write_text("DEFAULT: {}\n", encoding="utf-8")
    after = {entry.name for entry in settings_page.discover_files(files_dir)
             if entry.kind == settings_page.KIND_SERVICE}
    assert after - before == {"brandnew"}, "a new service file did not show up from disk"


def test_the_entry_carries_the_schema_the_bot_uses(files_dir):
    entries = {(entry.kind, entry.name): entry for entry in settings_page.discover_files(files_dir)}
    dashboard = entries[(settings_page.KIND_SERVICE, "dashboard")]
    assert dashboard.primary_schema is not None
    assert dashboard.primary_schema.endswith("dashboard_schema.yaml")


@pytest.mark.asyncio
async def test_a_service_file_page_renders_and_the_main_editor_has_no_file_rail(files_dir):
    app, _ = _app(files_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        response = await client.get(_service_url("dashboard"))
        main = await client.get(settings_page.SETTINGS_MAIN_PATH)
    assert response.status_code == 200, response.text[:300]
    page = response.text
    # the FILE's own name is the heading, and the view is the SAME tabbed schema-driven page a plugin
    # gets (the older ``cfg-layout`` single form is NOT what a service renders)
    assert "dashboard.yaml" in page
    assert 'class="st-layout"' in page
    assert 'class="st-tabs"' in page
    assert "The file, as YAML" in page
    # THE main.yaml COGWHEEL OPENS A MAIN-ONLY EDITOR: the SAME tabbed page, with NO file rail and NO
    # other file's name in its markup (the file list is gone from every scoped view now).
    assert main.status_code == 200, main.text[:300]
    assert 'class="st-layout"' in main.text and 'class="st-tabs"' in main.text
    assert 'class="cfg-files"' not in main.text, "the main editor still renders the file rail"
    for url in (_service_url("dashboard"), _service_url("bot")):
        assert f'href="{url}"' not in main.text, f"the main editor lists another file: {url}"
    assert "dashboard.yaml" not in main.text and "bot.yaml" not in main.text
    # the parked preset files are NOT offered anywhere
    assert f'href="{_preset_url("presets")}"' not in main.text, "a parked preset entry was offered"
    assert f'href="{_preset_url("tournament")}"' not in main.text, "a parked preset entry was offered"


@pytest.mark.asyncio
async def test_the_yaml_panel_matches_the_service_file(files_dir):
    from core.utils.configwrite import dump_config, load_config_file
    app, _ = _app(files_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(_service_url("dashboard"))).text
    panel = re.search(r'<pre class="st-yaml"[^>]*>(.*?)</pre>', page, re.DOTALL).group(1)
    expected = dump_config(load_config_file(files_dir / "services" / "dashboard.yaml"))
    assert html.unescape(panel).strip() == expected.strip()


# ------------------------------------------------------ 2. the save: byte-identical no-op, per kind

async def _open(client, url: str) -> str:
    response = await client.get(url)
    assert response.status_code == 200, response.text[:300]
    return response.text


@pytest.mark.asyncio
async def test_a_no_op_save_leaves_the_file_byte_identical(files_dir):
    name = "dashboard"
    path = files_dir / "services" / "dashboard.yaml"
    before = path.read_bytes()

    app, _ = _app(files_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, _service_url(name))
        csrf, revision = _csrf_and_revision(page)
        form = _rendered_form(page)                      # post back EXACTLY what was rendered
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        form["_expert"] = "0"
        save_url = settings_page.SETTINGS_FILE_SAVE_PATH.format(
            kind=settings_page.KIND_SERVICE, name=name)
        response = await client.post(save_url, data=form)
        landed = await _open(client, _service_url(name))
    assert response.status_code == 303
    assert path.read_bytes() == before, "an unchanged save rewrote the file"
    banner = re.search(r'notice-msg">([^<]*)<', landed)
    assert banner is not None and html.unescape(banner.group(1)) == settings_page.SAVED_NO_CHANGE


@pytest.mark.asyncio
async def test_a_real_change_lands_in_the_file_that_was_opened(files_dir):
    path = files_dir / "services" / "dashboard.yaml"
    before = path.read_text(encoding="utf-8")
    app, _ = _app(files_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, _service_url("dashboard"))
        csrf, revision = _csrf_and_revision(page)
        form = _rendered_form(page)
        # move ONE value the file carries
        form["f.DEFAULT.servers.background"] = "white on black"
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        form["_expert"] = "0"
        response = await client.post(
            settings_page.SETTINGS_FILE_SAVE_PATH.format(
                kind=settings_page.KIND_SERVICE, name="dashboard"), data=form)
        landed = await _open(client, _service_url("dashboard"))
    assert response.status_code == 303
    after = path.read_text(encoding="utf-8")
    assert after != before
    assert "white on black" in after
    assert "# config/services/dashboard.yaml" in after            # the comment survived
    banner = re.search(r'notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVED_RESTART


# ------------------------------------------------------ 3. the refusal (unplaceable field)

@pytest.mark.parametrize("kind,url_for,name,target,bogus", [
    (settings_page.KIND_SERVICE, _service_url, "dashboard", ("services", "dashboard.yaml"),
     "f.not_a_real_key"),
], ids=["service"])
@pytest.mark.asyncio
async def test_an_unplaceable_field_refuses_the_save_and_writes_nothing(
        files_dir, kind, url_for, name, target, bogus):
    path = files_dir.joinpath(*target)
    before = path.read_bytes()
    app, _ = _app(files_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, url_for(name))
        csrf, revision = _csrf_and_revision(page)
        form = {bogus: "x", "_csrf_token": csrf, "_revision": revision, "_expert": "0"}
        save_url = settings_page.SETTINGS_FILE_SAVE_PATH.format(kind=kind, name=name)
        response = await client.post(save_url, data=form)
        landed = await _open(client, url_for(name))
    assert response.status_code == 303
    assert path.read_bytes() == before, "a refused save wrote to the file"
    banner = re.search(r'notice-msg">([^<]*)<', landed)
    assert banner is not None
    assert html.unescape(banner.group(1)) == settings_page.SAVE_REFUSED_UNPLACEABLE


# ------------------------------------------------------ 4. shown as such: invalid / no schema

@pytest.mark.asyncio
async def test_a_file_that_does_not_validate_is_shown_as_such(files_dir):
    # a REAL file shape gone wrong: DEFAULT must be a map, not a scalar
    (files_dir / "services" / "dashboard.yaml").write_text("DEFAULT: 5\n", encoding="utf-8")
    app, _ = _app(files_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, _service_url("dashboard"))
    assert settings_page.SCHEMA_INVALID_BANNER in html.unescape(page), \
        "an invalid file was rendered as if it were fine"


@pytest.mark.asyncio
async def test_a_kind_with_no_schema_is_read_only_and_says_so(files_dir):
    (files_dir / "services" / "mystery.yaml").write_text("anything: goes\n", encoding="utf-8")
    app, _ = _app(files_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client, _service_url("mystery"))
    assert settings_page.NO_SCHEMA_BANNER in html.unescape(page)
    assert 'type="submit"' not in page or "cfg-save" not in page


# ------------------------------------------------------ 5. resolution + the capability gate

@pytest.mark.asyncio
async def test_an_unknown_file_or_kind_is_a_404(files_dir):
    app, _ = _app(files_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        assert (await client.get(_service_url("does_not_exist"))).status_code == 404
        # a preset file has no door in this area at all — the Presets page edits one preset per page
        assert (await client.get(_preset_url("nope"))).status_code == 404
        assert (await client.get(_preset_url("presets"))).status_code == 404, \
            "a preset file is still reachable through the settings file editor"
        assert (await client.get(settings_page.SETTINGS_FILE_PATH.format(
            kind="bogus", name="x"))).status_code == 404


@pytest.mark.asyncio
async def test_a_caller_without_the_capability_can_neither_open_nor_save(files_dir):
    app, _ = _app(files_dir, roles=("DCS Admin",))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        assert (await client.get(_service_url("dashboard"))).status_code == 403
        response = await client.post(
            settings_page.SETTINGS_FILE_SAVE_PATH.format(
                kind=settings_page.KIND_SERVICE, name="dashboard"), data={"f.x": "1"})
    assert response.status_code == 403


def test_the_file_capabilities_are_admin_only_with_no_scope_grant():
    from conftest import permissions
    settings_page.declare()
    caps = settings_page.capabilities()
    for path in (settings_page.SETTINGS_FILE_PATH, settings_page.SETTINGS_FILE_SAVE_PATH,
                 settings_page.SETTINGS_VALIDATE_FILE_PATH):
        assert path in caps
        assert permissions.roles_for_capability(caps[path]) == ("Admin",)
    assert permissions.scope_grants(settings_page.SETTINGS_WRITE_CAPABILITY) is False
