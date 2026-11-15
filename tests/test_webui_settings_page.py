"""The MAIN.yaml editor: the install's own file, edited by the SHARED config view.

older, single-form editor below it. It must instead open a MAIN-ONLY page: the SAME tabbed,
schema-driven editor every other config file uses (``settings_scope.html``), reached at
``/settings/main/main`` — no file rail, no list of other files, and no ``plugins``/``opt_plugins``
part (both lists are managed by the plugin TILES on the Settings landing).

Pinned here, through the REAL application:

* the page RENDERS from the real schema in the shared scoped layout (``st-layout``/``st-tabs``/
  ``st-yaml``) — NOT the older ``cfg-*`` single form, and with NO file rail;
* an edit round-trips and a no-op save is BYTE-IDENTICAL;
* a ``plugins``/``opt_plugins`` list already in the file SURVIVES a save through this editor untouched
  (the form never renders or posts those keys, and the save refuses one that is smuggled in);
* the save goes through the ONE write primitive (``configwrite.patch_config_file``) and never opens or
  writes a file itself.
"""
from __future__ import annotations

import html
import re

import pytest

from conftest import asgi_client, permissions
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login
from test_webui_settings_files import _rendered_form

#: A ``main.yaml`` carrying EVERY key the schema names — INCLUDING both plugin lists, so the
#: "the editor does not show them and a save keeps them" promise has something to be exact about.
MAIN_FIXTURE = """\
# main.yaml — an operator's own file.
guild_id: 112233445566    # the Discord server
guild_name: My Server
autoupdate: true
use_dashboard: false
chat_command_prefix: .
language: en
validation: none
database:
  url: postgres://user:***@localhost:5432/db   # the pool
  pool_min: 5
  pool_max: 10
  max_reties: 1
  max_idle: 0
logging:
  loglevel: DEBUG           # the level
  logrotate_count: 5
  logrotate_size: 10485760
  utc: false
filter:
  server_name: ^Special K -
  mission_name: ^Operation
  tag: "'^[JDS]'"
plugins:
  - mission
opt_plugins:
  - serverstats
"""


@pytest.fixture
def settings_dir(tmp_path):
    """A config dir with the bot.yaml the auth layer needs AND the main.yaml the page edits."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(MAIN_FIXTURE, encoding="utf-8")
    return tmp_path


def _app(settings_dir, roles=("Admin",)):
    return _build(settings_dir, users=[{"username": "staff", "password_hash": _hash(),
                                        "roles": list(roles)}])


def _csrf_and_revision(page: str) -> tuple[str, str]:
    csrf = re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)
    revision = re.search(r'name="_revision" value="([^"]*)"', page).group(1)
    return csrf, revision


async def _open(client) -> str:
    response = await client.get(settings_page.SETTINGS_MAIN_PATH)
    assert response.status_code == 200, response.text[:200]
    return response.text


#: the main editor's OWN save route — the per-file one, exactly like every service/plugin page.
MAIN_SAVE_URL = settings_page.SETTINGS_FILE_SAVE_PATH.format(
    kind=settings_page.KIND_MAIN, name="main")


def _banner(page: str) -> str:
    found = re.search(r'notice-msg">([^<]*)<', page)
    assert found is not None, "no notice was rendered"
    return html.unescape(found.group(1))


# ------------------------------------------------------------------ 1. the page renders

@pytest.mark.asyncio
async def test_an_admin_opens_the_main_editor_generated_from_the_schema(settings_dir):
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    # the SHARED scoped editor — the SAME markers every other config page's tests assert
    assert 'class="st-layout"' in page
    assert 'class="st-tabs"' in page
    assert "The file, as YAML" in page
    # NOT the older single-form page, and NOT the file rail
    assert 'class="cfg-form"' not in page
    assert 'class="cfg-files"' not in page
    # the schema-generated controls are there …
    assert 'name="f.guild_id"' in page                       # a number field
    assert 'name="f.autoupdate"' in page                     # a bool switch (with its companion)
    assert 'name="f.language"' in page                       # a select
    # … and BOTH plugin lists are OMITTED (managed by the landing's plugin tiles)
    assert 'name="f.plugins"' not in page
    assert 'name="f.opt_plugins"' not in page
    # the form posts to THIS FILE's own save route
    assert MAIN_SAVE_URL in page


@pytest.mark.asyncio
async def test_the_main_editor_lists_no_other_file(settings_dir):
    """No file rail and no other file's name in the markup — the page edits main.yaml ONLY."""
    (settings_dir / "services" / "dashboard.yaml").write_text("DEFAULT: {}\n", encoding="utf-8")
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    assert 'class="cfg-files"' not in page
    assert "dashboard.yaml" not in page
    assert "bot.yaml" not in page
    assert f'href="{settings_page.SETTINGS_FILE_PATH.format(kind="service", name="dashboard")}"' \
        not in page
    # the page's own heading IS the file it edits
    assert "main.yaml" in page


@pytest.mark.asyncio
async def test_the_yaml_panel_matches_the_file(settings_dir):
    from core.utils.configwrite import dump_config, load_config_file
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    panel = re.search(r'<pre class="st-yaml"[^>]*>(.*?)</pre>', page, re.DOTALL).group(1)
    expected = dump_config(load_config_file(settings_dir / "main.yaml"))
    assert html.unescape(panel).strip() == expected.strip()


# ------------------------------------------------------------------ 1b. the required marker

def _row_badges(page: str, key: str) -> list[str]:
    """The ``st-badge`` words on the row whose key marker is *key* (`required` etc.)."""
    found = re.search(r'<div class="nm">' + re.escape(key) + r'</div>\s*<div class="kk">(.*?)</div>',
                      page, re.S)
    assert found, f"no row block for {key!r} in the rendered page"
    return re.findall(r'<span class="st-badge">([^<]*)</span>', found.group(1))


@pytest.mark.asyncio
async def test_the_required_marker_is_the_only_label_marker(settings_dir):
    """``required`` marks ONLY ``required: true``; ``nullable: false`` carries NO marker.

    many fields that set it without ``required: true``. The label was split into a ``required`` marker
    and a "no empty value" marker; the "no empty value" marker was later removed as noise — the RULE stays (an empty value is still refused where the schema says so), only the label
    is gone.
    """
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    # required: true -> the one marker (its nullable:false fact adds no badge)
    assert _row_badges(page, "guild_id") == ["required"]
    # nullable: false ALONE -> NO marker at all
    assert _row_badges(page, "autoupdate") == []
    # neither fact -> no marker at all
    assert _row_badges(page, "validation") == []


@pytest.mark.asyncio
async def test_a_nullable_false_field_alone_still_refuses_an_empty_value(settings_dir):
    """``nullable: false`` without ``required: true`` STILL refuses a blank — the fact it really means.

    ``guild_name`` is ``nullable: false`` (no ``required: true``): clearing it must be refused by the
    schema, so nothing is written and the file is byte-identical.
    """
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = _rendered_form(page)
        form["f.guild_name"] = ""                             # clear a nullable:false field
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        response = await client.post(MAIN_SAVE_URL, data=form)
        landed = await _open(client)
    assert response.status_code == 303
    assert (settings_dir / "main.yaml").read_text(encoding="utf-8") == MAIN_FIXTURE, \
        "an empty value for a nullable:false field was written — it must be refused"
    assert "refused" in _banner(landed).lower(), \
        "the save did not report the schema's refusal"


# ------------------------------------------------------------------ 2. refusals

@pytest.mark.asyncio
async def test_a_caller_without_the_capability_can_neither_open_nor_save(settings_dir):
    app, _ = _app(settings_dir, roles=("DCS Admin",))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        assert (await client.get(settings_page.SETTINGS_MAIN_PATH)).status_code == 403
        # a crafted POST is refused too — the gate runs before the handler
        response = await client.post(MAIN_SAVE_URL, data={"f.language": "de"})
    assert response.status_code == 403
    assert (settings_dir / "main.yaml").read_text(encoding="utf-8") == MAIN_FIXTURE


def test_the_capabilities_are_admin_only_with_no_scope_grant():
    settings_page.declare()
    assert permissions.roles_for_capability(settings_page.SETTINGS_VIEW_CAPABILITY) == ("Admin",)
    assert permissions.roles_for_capability(settings_page.SETTINGS_WRITE_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(settings_page.SETTINGS_WRITE_CAPABILITY) is False


# ------------------------------------------------------------------ 3. the save round-trip

@pytest.mark.asyncio
async def test_a_save_changes_only_the_moved_key_and_the_banner_says_restart(settings_dir):
    import difflib
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = _rendered_form(page)
        form["f.logging.loglevel"] = "INFO"                  # the ONE key moved
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        response = await client.post(MAIN_SAVE_URL, data=form)
        assert response.status_code == 303
        after = (settings_dir / "main.yaml").read_text(encoding="utf-8")
        diff = list(difflib.unified_diff(MAIN_FIXTURE.splitlines(), after.splitlines(),
                                         lineterm="", n=0))
        added = [line for line in diff if line.startswith("+") and not line.startswith("+++")]
        removed = [line for line in diff if line.startswith("-") and not line.startswith("---")]
        assert len(added) == 1 and "loglevel: INFO" in added[0], f"unexpected diff: {diff}"
        assert len(removed) == 1 and "loglevel: DEBUG" in removed[0], f"unexpected diff: {diff}"
        assert "# main.yaml" in after                        # the leading comment survived
        assert "# the pool" in after                         # an untouched sibling's comment survived
        landed = await _open(client)
    assert _banner(landed) == settings_page.SAVED_RESTART


@pytest.mark.asyncio
async def test_an_unchanged_save_is_a_no_op_the_file_is_untouched(settings_dir):
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = _rendered_form(page)
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        await client.post(MAIN_SAVE_URL, data=form)
        landed = await _open(client)
    assert (settings_dir / "main.yaml").read_text(encoding="utf-8") == MAIN_FIXTURE
    assert _banner(landed) == settings_page.SAVED_NO_CHANGE


@pytest.mark.asyncio
async def test_a_plugins_list_survives_a_main_editor_save_untouched(settings_dir):
    """THE card's promise: the editor omits ``plugins``/``opt_plugins``, so a save keeps them EXACTLY.

    The form the page renders posts neither list, and a REAL change elsewhere must not disturb them.
    A smuggled ``f.plugins`` is refused too (the save tree drops those keys), never written.
    """
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = _rendered_form(page)                           # post back EXACTLY what was rendered
        assert "f.plugins" not in form and "f.opt_plugins" not in form, \
            "the main editor rendered a plugin-list control"
        form["f.logging.loglevel"] = "INFO"                   # a genuine change elsewhere
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        response = await client.post(MAIN_SAVE_URL, data=form)
    assert response.status_code == 303
    after = (settings_dir / "main.yaml").read_text(encoding="utf-8")
    assert "loglevel: INFO" in after
    assert "plugins:\n  - mission\n" in after, "the plugins list was disturbed by the save"
    assert "opt_plugins:\n  - serverstats\n" in after, "the opt_plugins list was disturbed by the save"


@pytest.mark.asyncio
async def test_a_smuggled_plugin_list_is_refused_and_nothing_is_written(settings_dir):
    """A crafted ``f.opt_plugins`` on the main editor's save is UNPLACEABLE and refused — nothing written."""
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = {"f.opt_plugins": ["hacked"], "_csrf_token": csrf, "_revision": revision}
        response = await client.post(MAIN_SAVE_URL, data=form)
        landed = await _open(client)
    assert response.status_code == 303
    assert (settings_dir / "main.yaml").read_text(encoding="utf-8") == MAIN_FIXTURE
    assert _banner(landed) == settings_page.SAVE_REFUSED_UNPLACEABLE


# ------------------------------------------------------------------ 4. no second write path

@pytest.mark.asyncio
async def test_the_save_goes_through_the_primitive(settings_dir, monkeypatch):
    calls = {}
    real = settings_page.configwrite.patch_config_file

    def spy(path, mutate, **kwargs):
        calls["path"] = path
        calls["schemas"] = kwargs.get("schemas")
        return real(path, mutate, **kwargs)

    monkeypatch.setattr(settings_page.configwrite, "patch_config_file", spy)
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = _rendered_form(page)
        form["f.language"] = "de"
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        await client.post(MAIN_SAVE_URL, data=form)
    assert calls.get("path") == settings_dir / "main.yaml"
    assert calls.get("schemas") == [settings_page.MAIN_SCHEMA_PATH]


def test_the_page_module_does_not_write_files_itself():
    """The ONE write path rule, statically: the page neither opens nor writes a config file."""
    source = (settings_dir_source()).read_text(encoding="utf-8")
    assert "patch_config_file" in source
    assert "open(" not in source and "write_text" not in source and "os.replace" not in source


def settings_dir_source():
    from pathlib import Path
    return Path(__file__).resolve().parents[1] / "services" / "webservice" / "pages" / "settings.py"


# ------------------------------------------------------------------ 5. the banner follows `applies`

@pytest.mark.asyncio
async def test_the_banner_is_derived_from_the_primitives_applies_value(settings_dir, monkeypatch):
    """The banner is a READING of ``SaveResult.applies``, never a hard-coded claim."""
    from core.utils.configwrite import APPLIES_NOW, SaveResult

    def fake(path, mutate, **kwargs):
        return SaveResult(path=path, changed=True, revision="x", applies=APPLIES_NOW)

    monkeypatch.setattr(settings_page.configwrite, "patch_config_file", fake)
    app, _ = _app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = _rendered_form(page)
        form["f.logging.loglevel"] = "INFO"
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        await client.post(MAIN_SAVE_URL, data=form)
        landed = await _open(client)
    assert _banner(landed) == settings_page.SAVED_NOW
    assert settings_page.SAVED_NOW != settings_page.SAVED_RESTART


# ------------------------------------------------------------------ 6. the layout chooser marks a shape

def test_the_layout_chooser_marks_the_shape_the_file_actually_has(tmp_path, monkeypatch):
    """The ``type: any`` chooser marks which of its two editors matches the file on disk.

    Both shapes were offered prefilled with no indication which one the file carries; posting the
    wrong one is refused by validation, so nothing corrupts, but the operator was left guessing.
    The ordinary-form context (:func:`settings_page._page_context`) marks the matching option
    (``matches``) — main.yaml itself now renders through the scoped editor, so this pins the
    generator's choice-marking on the context builder that still produces it.
    """
    import types

    schema = tmp_path / "choice_schema.yaml"
    schema.write_text(
        "schema;a:\n  type: seq\n  nullable: false\n  sequence:\n    - {type: str, nullable: false}\n"
        "schema;b:\n  type: map\n  nullable: false\n  mapping:\n    n: {type: int, nullable: false}\n"
        "type: map\nmapping:\n"
        "  h: {type: any, func: seq_or_map, nullable: false, enum: ['a', 'b']}\n",
        encoding="utf-8")
    (tmp_path / "main.yaml").write_text("h:\n  - x\n", encoding="utf-8")   # a LIST -> the seq matches
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(schema))
    monkeypatch.setattr(settings_page, "CONFIG_FILE_NAME", "main.yaml")
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(webui_config_dir=str(tmp_path))))

    entry = settings_page.GlobalFile(
        kind=settings_page.KIND_MAIN, name="main", path=tmp_path / "main.yaml",
        primary_schema=str(schema), schema_files=(str(schema),))
    options = settings_page._page_context(request, entry, settings_page.SETTINGS_PATH)["choices"]["h"]
    assert [option["key"] for option in options] == ["a", "b"]
    assert [option["matches"] for option in options] == [True, False]

    # and a MAPPING value marks the other option instead
    (tmp_path / "main.yaml").write_text("h:\n  n: 3\n", encoding="utf-8")
    options = settings_page._page_context(request, entry, settings_page.SETTINGS_PATH)["choices"]["h"]
    assert [option["matches"] for option in options] == [False, True]
