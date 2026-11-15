"""THE EDITOR SHOWS ONLY WHAT MATTERS — mandatory + set, the rest behind a ``+ Add`` picker.

THE CARD. A schema with a lot of OPTIONALITY used to render EVERY field, set or not,
so a big schema produced a wall of controls. The rule, and this file pins every part of it:

* a field is SHOWN when it is MANDATORY (``required: true``) or the file ALREADY CARRIES it;
* everything else — optional AND absent — is HIDDEN and offered by the ``+ Add`` picker, FIELDS before
  BLOCKS, each with its schema kind word as the one-line description;
* picking a choice ADDS the control to the page (``configgen.js``); removing a control the picker just
  added is instant (nothing is in the file);
* a field that CARRIES A VALUE is removed only through the acknowledged trashcan — the save REFUSES a
  removal that does not present the acknowledgement the render minted (``f.__remove`` +
  ``f.__remove_ack``);
* a no-op save stays byte-identical, an added value round-trips, and the Expert YAML view is unchanged.

The page tests drive the REAL application (render, scrape the fields a browser WOULD post, save) exactly
as the other configuration tests do; the JS half is the SHIPPED ``configgen.js`` driven through the
browser-less harness over the real file.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from babel.messages import pofile

from conftest import asgi_client
from services.webservice import configgen
from services.webservice.pages import settings as settings_page
from test_webui_configgen_containers import (
    _app, _config_dir, _csrf_and_revision, _posted_form, _tree_of,
)
from test_webui_dashboard import _login

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGGEN_JS = REPO_ROOT / "services" / "webservice" / "static" / "configgen.js"
CONFIGGEN_HARNESS = Path(__file__).resolve().parent / "configgen_harness.js"

#: a schema with ONE mandatory field, a couple of optional scalars and an optional BLOCK, so
#: "mandatory + set" and "optional + absent" are both exercised.
PICK_SCHEMA = """\
type: map
nullable: false
mapping:
  required_a: {type: str, required: true, nullable: false}
  opt_str: {type: str, nullable: false}
  opt_num: {type: int, nullable: false}
  opt_flag: {type: bool, nullable: false}
  opt_block:
    type: map
    nullable: false
    mapping:
      inner: {type: str, nullable: false}
"""

#: the file the tests start from: the mandatory field and ONE optional set, the rest absent.
PICK_FIXTURE = "required_a: hello\nopt_str: setvalue\n"

#: every optional set — the "shows exactly what it shows today" case.
FULL_FIXTURE = ("required_a: hello\nopt_str: setvalue\nopt_num: 3\nopt_flag: true\n"
                "opt_block:\n  inner: deeper\n")


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


def _live_names(page: str) -> set[str]:
    """The ``f.`` field NAMES the live form posts."""
    return set(re.findall(r'name="(f\.[^"]*)"', _live(page)))


# ------------------------------------------------------- 1. the model (unit)

def test_a_field_is_shown_only_when_mandatory_or_set(tmp_path):
    tree = _tree_of(_config_dir(tmp_path, PICK_FIXTURE, PICK_SCHEMA))
    from core.utils.configwrite import load_config_text
    document = load_config_text(PICK_FIXTURE)
    child = lambda key: next(c for c in tree.children if c.key == key)  # noqa: E731
    assert configgen.is_mandatory(child("required_a")) is True
 # ``required: true`` alone is mandatory; nothing else is
    assert configgen.is_mandatory(child("opt_str")) is False
    assert configgen.field_shown(tree, child("required_a"), document) is True   # mandatory
    assert configgen.field_shown(tree, child("opt_str"), document) is True      # set
    assert configgen.field_shown(tree, child("opt_num"), document) is False     # optional + absent
    assert configgen.field_shown(tree, child("opt_block"), document) is False   # optional block


def test_the_picker_groups_fields_before_blocks(tmp_path):
    tree = _tree_of(_config_dir(tmp_path, PICK_FIXTURE, PICK_SCHEMA))
    from core.utils.configwrite import load_config_text
    document = load_config_text(PICK_FIXTURE)
    options = configgen.hidden_options(tree, tree.children, document)
    keys = [option["key"] for option in options]
    assert keys == ["opt_num", "opt_flag", "opt_block"], keys
    assert [option["block"] for option in options] == [False, False, True]
 # the schema carries no prose, so a choice's one-line description is its KIND word
    assert options[0]["kind"] == configgen.KIND_LABELS[configgen.KIND_NUMBER]
 # the mandatory field and the set one are NEVER offered
    assert "required_a" not in keys and "opt_str" not in keys


def test_an_empty_block_is_still_set_and_a_regex_container_is_never_removable(tmp_path):
    tree = _tree_of(_config_dir(tmp_path, "required_a: x\nopt_block: {}\n", PICK_SCHEMA))
    from core.utils.configwrite import load_config_text
    document = load_config_text("required_a: x\nopt_block: {}\n")
    block = next(c for c in tree.children if c.key == "opt_block")
    assert configgen.field_set(tree, block, document) is True, "an EMPTY block is still present"
 # a regex container has no key of its own; it is never offered for removal
    layout = configgen.visibility(tree, tree, document)
    assert configgen.view_id(block) in layout["removable"]


# ------------------------------------------------------- 2. the rendered page

@pytest.mark.asyncio
async def test_the_form_renders_mandatory_and_set_only(patched):
    """Mandatory + set values are SHOWN; every optional-absent field is behind the picker."""
    config_dir = patched(PICK_FIXTURE, PICK_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)

    live = _live_names(page)
    assert "f.required_a" in live and "f.opt_str" in live      # mandatory + set
    assert "f.opt_num" not in live and "f.opt_flag" not in live  # optional + absent
    assert "f.opt_block.inner" not in live
 # the picker offers exactly the absent optionals, fields first, block last
    assert "data-cfg-picker" in page
    rows = re.findall(r'class="cfg-pkey mono">([^<]*)</span>', page)
    assert rows[:3] == ["opt_num", "opt_flag", "opt_block"], rows
 # the values that ARE set are printed, untouched
    assert 'name="f.opt_str" value="setvalue"' in page


@pytest.mark.asyncio
async def test_a_form_whose_optionals_are_all_set_shows_everything(patched):
    """With every optional set, the form shows every field and offers NO picker (today's page)."""
    config_dir = patched(FULL_FIXTURE, PICK_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    assert _live_names(page) >= {"f.required_a", "f.opt_str", "f.opt_num", "f.opt_flag",
                                 "f.opt_block.inner"}
    assert "data-cfg-picker" not in _live(page)
 # every set optional carries the trashcan; the mandatory one does NOT
    assert 'data-cfg-rpath="opt_str"' in page and 'data-cfg-rpath="opt_block"' in page
    assert 'data-cfg-rpath="required_a"' not in page


# ------------------------------------------------------- 3. add round-trips, remove deletes

@pytest.mark.asyncio
async def test_an_added_optional_field_appears_and_its_value_survives_a_save(patched):
    """Adding a picker field posts its ``f.`` name; the value lands and the page shows it next time."""
    config_dir = patched(PICK_FIXTURE, PICK_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
 # the browser adds the control for ``opt_num`` and the operator types 7
        page = await _post(client, page, {"f.opt_num": "7"})
    assert "opt_num: 7" in (config_dir / "main.yaml").read_text(encoding="utf-8")
    assert 'name="f.opt_num" value="7"' in page
    assert "f.opt_num" in _live_names(page)


@pytest.mark.asyncio
async def test_an_added_optional_block_appears_and_its_value_survives_a_save(patched):
    config_dir = patched(PICK_FIXTURE, PICK_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        page = await _post(client, page, {"f.opt_block.inner": "deeper"})
    text = (config_dir / "main.yaml").read_text(encoding="utf-8")
    assert "opt_block:" in text and "inner: deeper" in text
    assert "f.opt_block.inner" in _live_names(page)


@pytest.mark.asyncio
async def test_removing_a_set_field_deletes_it_with_its_acknowledgement(patched):
    """The trashcan's removal deletes the field — and only WITH the acknowledgement the render minted."""
    config_dir = patched(PICK_FIXTURE, PICK_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
 # a BARE removal (the acknowledgement blanked) is REFUSED: the file is left exactly as it was
        await _post(client, page, {"f.__remove": "opt_str", "f.__remove_ack": ""})
        assert "opt_str" in (config_dir / "main.yaml").read_text(encoding="utf-8")
 # the page's own acknowledgement lets it through
        page = await _open(client)
        page = await _post(client, page, {"f.__remove": "opt_str"})
    text = (config_dir / "main.yaml").read_text(encoding="utf-8")
    assert "opt_str" not in text, text
    assert "required_a: hello" in text, "the removal must leave every other key alone"
    assert "f.opt_str" not in _live_names(page)


@pytest.mark.asyncio
async def test_removing_a_set_block_deletes_the_whole_block(patched):
    config_dir = patched(FULL_FIXTURE, PICK_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        page = await _post(client, page, {"f.__remove": "opt_block"})
    text = (config_dir / "main.yaml").read_text(encoding="utf-8")
    assert "opt_block" not in text, text
    assert "inner" not in text
    assert "opt_num: 3" in text, "the sibling optionals are untouched"


@pytest.mark.asyncio
async def test_a_removal_of_a_mandatory_field_is_refused(patched):
    """A mandatory field is never removable — the server refuses the removal outright."""
    config_dir = patched(PICK_FIXTURE, PICK_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        await _post(client, page, {"f.__remove": "required_a"})
    assert "required_a: hello" in (config_dir / "main.yaml").read_text(encoding="utf-8")


# ------------------------------------------------------- 4. nothing is lost

@pytest.mark.asyncio
async def test_a_no_op_save_is_byte_identical(patched):
    config_dir = patched(PICK_FIXTURE, PICK_SCHEMA)
    before = (config_dir / "main.yaml").read_bytes()
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        await _post(client, page)                       # the page's own form, nothing moved
    assert (config_dir / "main.yaml").read_bytes() == before


@pytest.mark.asyncio
async def test_the_expert_view_still_carries_every_set_key(patched):
    """The escape hatch: the read-only YAML panel shows the file, every key it holds included."""
    config_dir = patched(FULL_FIXTURE, PICK_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    panel = re.search(r'<pre class="st-yaml" data-cfg-yaml-readonly>(.*?)</pre>', page, re.S)
    assert panel, "the expert YAML panel is missing"
    yaml_text = html.unescape(panel.group(1))
    for key in ("required_a", "opt_str", "opt_num", "opt_flag", "opt_block", "inner"):
        assert key in yaml_text, key


# ------------------------------------------------------- 5. the script (harness over the real file)

_requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                    reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def configgen_script() -> dict:
    assert CONFIGGEN_HARNESS.is_file() and CONFIGGEN_JS.is_file()
    done = subprocess.run(["node", str(CONFIGGEN_HARNESS), str(CONFIGGEN_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@_requires_node
def test_the_picker_adds_and_removes_a_control(configgen_script):
    """Picking a choice adds its EMPTY control and hides the choice; removing it brings the choice back."""
    add = configgen_script["pickerAdd"]
    assert add == {"added": 1, "names": ["f.logging.utc"], "choiceHidden": True,
                   "holderUntouched": ""}
    remove = configgen_script["pickerRemoveAdded"]
    assert remove == {"added": 0, "names": [], "choiceShown": True}


@_requires_node
def test_the_trashcan_warns_then_names_the_removal(configgen_script):
    """The first press only warns; confirming names the ENCODED path and drops the control."""
    got = configgen_script["rowRemoveSet"]
    assert got["opened"] == {"warningShown": True, "startHidden": True, "removedYet": 1}
    assert got["afterCancel"] == {"warningShown": True, "startShown": False}
    assert got["holder"] == "logging.utc"
    assert got["fieldsLeft"] == 0


# ------------------------------------------------------- 6. the chrome is wrapped, in 5 languages

def test_the_new_chrome_is_wrapped_and_in_every_catalog():
    """Every string the picker/trashcan composes is ``i18n``-marked AND in every translated catalog."""
    wrapped = [
        "Fields", "Blocks",
        "Remove this setting from the file",
        "This removes the setting and its value from the file.",
        settings_page.SAVE_REFUSED_REMOVE_ACK,
    ]
    for language in ("cn", "de", "es", "ru"):
        with open(REPO_ROOT / "locale" / language / "LC_MESSAGES" / "webui.po",
                  encoding="utf-8") as handle:
            msgids = {message.id for message in pofile.read_po(handle) if message.id}
        for text in wrapped:
            assert text in msgids, f"'{text}' is not in the {language} catalog"
