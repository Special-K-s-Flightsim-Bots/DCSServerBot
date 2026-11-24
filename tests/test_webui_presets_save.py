"""The Presets save: the ONE write route, the removal/rename guard and the honest banner.

One section per acceptance criterion of the card:

* ``POST /actions/presets/save`` is the console's ONE preset write — CSRF-protected, ``presets.write``
  gated, revision-guarded, and it writes through ``configwrite.patch_config_file``;
* a legal edit DOES write (the positive control), and a preset carrying ``modify`` / ``forcedOptions``
  / ``difficulty`` comes back with those keys INTACT — asserted on the document that lands on disk;
* a STALE form is refused and the file is byte-identical;
* removing or renaming a preset a node's ``MizEdit.settings`` names is REFUSED (the file's sha256 is
  unchanged and the message names the node+instance), and a preset a COMBINATION preset names is
  refused on the same rule;
* an unreferenced removal lands;
* a rename onto an existing name and a new preset whose name already exists are refused (no silent
  overwrite);
* a new preset is written into the file the operator chose; a posted destination naming a file no node
  names is refused;
* the reference scan is a PURE FUNCTION of the documents — unit-tested without a bot.

The wizard does NOT open a ``modify`` preset (S1's rule, unchanged here), so the "unrendered keys
survive" property is exercised by POSTing the ONE write route directly — which is exactly the branch
that decides whether those keys survive.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), with the write suite's own harness reused so the app under test is the running one.
"""
from __future__ import annotations

import hashlib
import html as html_module
import re
from pathlib import Path

import pytest

from services.webservice import weather_fields
from services.webservice.pages import presets as presets_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, fleet, sign_in,
)

#: a plain weather preset the wizard OPENS.
SPRING = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  qnh: 760
"""

#: a preset carrying weather values AND the keys the wizard does NOT render — those must survive.
HYBRID = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  qnh: 760
  modify:
    file: mission
  forcedOptions:
    fuel: false
  difficulty:
    externalViews: false
Other:
  temperature: 1
"""

#: an unreferenced preset, for the removal-that-lands case.
LONELY = """\
Lonely:
  temperature: 12
"""

#: a combination preset naming ``Spring`` — removing ``Spring`` breaks it silently, so it is refused.
COMBINATION = """\
Casual Weather:
  - Spring
  - Morning
"""

#: ``nodes.yaml`` whose ONE instance's ``MizEdit.settings`` (a time-window map) names ``Spring``.
NODES_REFERENCING = """\
NODE1:
  instances:
    Instance1:
      extensions:
        MizEdit:
          settings:
            "00:01-06:00": "Spring, Morning"
"""

#: the same, with the reference living in ``terrains.<map>.settings``.
NODES_TERRAIN = """\
NODE1:
  instances:
    Instance1:
      extensions:
        MizEdit:
          terrains:
            Caucasus:
              settings:
                - Winter, Spring
"""

#: a node block that does NOT name the preset — the positive control for the guard.
NODES_OTHER = """\
NODE1:
  instances:
    Instance1:
      extensions:
        MizEdit:
          settings:
            "00:01-06:00": "Winter, Morning"
"""


#: ``nodes.yaml`` naming the preset files the extension reads (its ``MizEdit.presets`` setting).
NODES_NAMING_FILES = """\
NODE1:
  instances:
    Instance1:
      extensions:
        MizEdit:
          presets:
            - config/presets.yaml
            - config/presets_weather.yaml
"""

#: the same, naming exactly ONE file (a bare string, the other shape the setting accepts).
NODES_NAMING_ONE = """\
NODE1:
  instances:
    Instance1:
      extensions:
        MizEdit:
          presets: config/presets.yaml
"""

#: a preset carrying time-keyed fog rows at 300 and 600 — the rows the wizard does not render.
FOGGED = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  enable_fog: true
  fog:
    mode: manual
    300:
      thickness: 200
    600:
      visibility: 1000
"""


def preset_file(config_dir: Path, text: str = SPRING, name: str = "presets.yaml") -> Path:
    path = config_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def nodes_file(config_dir: Path, text: str) -> Path:
    path = config_dir / "nodes.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hidden_fields(html: str) -> dict[str, str]:
    """Every ``<input type="hidden" name=… value=…>`` the page rendered, unescaped."""
    out: dict[str, str] = {}
    for match in re.finditer(r'<input type="hidden" name="([^"]*)" value="([^"]*)"', html):
        out[match.group(1)] = html_module.unescape(match.group(2))
    return out


async def page(http, path: str) -> str:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


async def review_form(http, name: str, *, new: bool = False, file: str = "") -> dict[str, str]:
    """The form the wizard's review step rendered for *name* — the fields the route reads."""
    url = presets_page.PRESETS_NEW_PATH if new else presets_page.wizard_url(name)
    params = "?step=review"
    if new:
        params += f"&name={name}"
        if file:
            params += f"&file={file}"
    return hidden_fields(await page(http, url + params))


async def post_save(http, name: str, *, action: str = "save", change: dict | None = None,
                    new: bool = False, file: str = "", drop: tuple[str, ...] = ()):
    """Open the wizard's review step for *name* and POST its own form, with *change* applied.

    The form is read off the RENDERED page (its CSRF token, its revision and the values it carries),
    so the test never mints a field the page does not produce.
    """
    data = await review_form(http, name, new=new, file=file)
    data["_action"] = action
    if change:
        data.update(change)
    for key in drop:
        data.pop(key, None)
    return await http.post(presets_page.PRESETS_SAVE_PATH, data=data)


# ------------------------------------------------------------------- 1. the pure reference scan

def test_mizedit_references_reads_every_settings_shape_and_never_filter():
    """The scan reads a time-window map, a list, a permutation, a bare string and ``terrains``."""
    document = {
        "NODE1": {"instances": {
            "Window": {"extensions": {"MizEdit": {"settings": {"00:01-06:00": "Spring, Morning"}}}},
            "List": {"extensions": {"MizEdit": {"settings": ["Spring", "Morning"]}}},
            "Permutation": {"extensions": {"MizEdit": {"settings": [["Spring", "Winter"], ["Morning"]]}}},
            "Bare": {"extensions": {"MizEdit": {"settings": "Spring"}}},
            "Terrain": {"extensions": {"MizEdit": {"terrains": {"Caucasus": {"settings": "Winter, Spring"}}}}},
            "Filtered": {"extensions": {"MizEdit": {"filter": "Spring*"}}},
        }},
    }
    refs = presets_page.mizedit_references(document, "Spring")
    assert refs == ["node 'NODE1', instance 'Window'", "node 'NODE1', instance 'List'",
                    "node 'NODE1', instance 'Permutation'", "node 'NODE1', instance 'Bare'",
                    "node 'NODE1', instance 'Terrain'"]
    # ``filter:`` is a REGEX matched against the mission FILE NAME — never a reference.
    assert "node 'NODE1', instance 'Filtered'" not in refs


def test_a_node_level_extension_block_is_never_read():
    """``MizEdit`` is an INSTANCE extension; ``nodes.<n>.extensions`` is not a reference site."""
    document = {"NODE1": {"extensions": {"MizEdit": {"settings": "Spring"}}}}
    assert presets_page.mizedit_references(document, "Spring") == []


def test_combination_references_reads_list_valued_presets():
    """A combination preset's LIST value is a reference; a map's weather is not."""
    documents = [{"Casual Weather": ["Spring", "Morning"], "Spring": {"temperature": 8}}]
    assert presets_page.combination_references(documents, "Spring") == ["the preset 'Casual Weather'"]
    assert presets_page.combination_references(documents, "Nope") == []


# ------------------------------------------------------------- 2. the write: a legal edit lands

@pytest.mark.asyncio
async def test_a_legal_edit_writes(config_dir, clean_registry):
    """The positive control: a weather change writes and the banner is the honest one."""
    from core.utils import configwrite

    path = preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", change={"temperature": "15"})
        assert reply.status_code == 303, reply.text
        assert reply.headers["location"] == f"{presets_page.wizard_url('Spring')}?step=review"
        html = await page(http, reply.headers["location"])

    document = configwrite.load_config_file(path)
    assert document["Spring"]["temperature"] == 15
    assert document["Spring"]["qnh"] == 760
    assert document["Spring"]["date"] == "2016-04-21"
    assert list(config_dir.glob("*.bak")), "a real write keeps a backup"
    # THE BANNER reads the primitive's OWN apply story: a config write applies on the next restart.
    assert "applies on the next bot restart" in html


@pytest.mark.asyncio
async def test_a_preset_carrying_the_keys_the_wizard_does_not_render_keeps_them(config_dir,
                                                                                clean_registry):
    """``modify`` / ``forcedOptions`` / ``difficulty`` survive a save — asserted on disk.

    The wizard does not open a ``modify`` preset, so this POSTs the write route directly (the branch
    that decides whether those keys survive), reading the CSRF token and the revision off a page the
    wizard DOES render for a sibling preset in the SAME file.
    """
    from core.utils import configwrite

    path = preset_file(config_dir, HYBRID)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Other")
        data.update({"_original": "Spring", "name": "Spring", "date": "2016-04-21",
                     "temperature": "15", "qnh": "760", "_action": "save"})
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303, reply.text

    spring = configwrite.load_config_file(path)["Spring"]
    assert spring["temperature"] == 15
    assert spring["qnh"] == 760
    assert spring["date"] == "2016-04-21"
    # THE PROPERTY THIS CARD EXISTS TO KEEP: the keys the wizard does not render survive.
    assert spring["modify"] == {"file": "mission"}
    assert spring["forcedOptions"] == {"fuel": False}
    assert spring["difficulty"] == {"externalViews": False}


# ------------------------------------------------------------------- 3. the stale form

@pytest.mark.asyncio
async def test_a_stale_form_is_refused_and_the_file_is_untouched(config_dir, clean_registry):
    """A form rendered from an older revision writes NOTHING and says the page is stale."""
    path = preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Spring")
        # the file moves on disk AFTER the form was rendered
        path.write_text(SPRING + "Late:\n  temperature: 1\n", encoding="utf-8")
        before = sha256(path)
        data["_action"] = "save"
        data["temperature"] = "15"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        assert sha256(path) == before, "a stale form must write nothing"
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    assert "stale" in html.lower()


# ------------------------------------------------------------------- 4. the removal/rename guard

@pytest.mark.asyncio
async def test_removing_a_referenced_preset_is_refused(config_dir, clean_registry):
    """A preset a node's ``MizEdit.settings`` names cannot be removed — nothing is written."""
    path = preset_file(config_dir)
    nodes_file(config_dir, NODES_REFERENCING)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", action="remove")
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, presets_page.PRESETS_PATH)
    assert "NODE1" in html and "Instance1" in html
    assert "still named by" in html


@pytest.mark.asyncio
async def test_removing_a_preset_named_in_terrains_is_refused(config_dir, clean_registry):
    """A ``terrains.<map>.settings`` reference refuses the removal on the same rule."""
    path = preset_file(config_dir)
    nodes_file(config_dir, NODES_TERRAIN)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", action="remove")
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, presets_page.PRESETS_PATH)
    assert "still named by" in html


@pytest.mark.asyncio
async def test_renaming_a_referenced_preset_is_refused(config_dir, clean_registry):
    """A rename of a referenced preset is refused and the file is byte-identical."""
    path = preset_file(config_dir)
    nodes_file(config_dir, NODES_REFERENCING)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", change={"name": "Renamed"})
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    assert "still named by" in html


@pytest.mark.asyncio
async def test_removing_a_preset_named_by_a_combination_is_refused(config_dir, clean_registry):
    """A COMBINATION preset naming the target refuses the removal on the same rule."""
    path = preset_file(config_dir, SPRING + COMBINATION)
    nodes_file(config_dir, NODES_OTHER)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", action="remove")
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, presets_page.PRESETS_PATH)
    assert "Casual Weather" in html and "still named by" in html


@pytest.mark.asyncio
async def test_an_unreferenced_removal_lands(config_dir, clean_registry):
    """The positive control: a preset nothing names IS removed, and the rest of the file survives."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING + LONELY)
    nodes_file(config_dir, NODES_OTHER)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Lonely", action="remove")
        assert reply.status_code == 303
        assert reply.headers["location"] == presets_page.PRESETS_PATH

    document = configwrite.load_config_file(path)
    assert "Lonely" not in document
    assert "Spring" in document and document["Spring"]["qnh"] == 760


# ------------------------------------------------------------------- 5. collisions

@pytest.mark.asyncio
async def test_a_rename_onto_an_existing_name_is_refused(config_dir, clean_registry):
    """A rename onto a name the file already carries would overwrite it — refused, nothing written."""
    path = preset_file(config_dir, SPRING + LONELY)
    nodes_file(config_dir, NODES_OTHER)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", change={"name": "Lonely"})
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    assert "already exists" in html


@pytest.mark.asyncio
async def test_a_new_preset_whose_name_already_exists_is_refused(config_dir, clean_registry):
    """A new preset named after an existing one is refused on the SAME collision rule."""
    path = preset_file(config_dir)
    nodes_file(config_dir, NODES_OTHER)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", new=True, file="config/presets.yaml",
                                change={"temperature": "3"})
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, f"{presets_page.PRESETS_NEW_PATH}?step=review&name=Spring")
    assert "already exists" in html


# ------------------------------------------------------------------- 6. a new preset's destination

@pytest.mark.asyncio
async def test_a_new_preset_is_written_into_the_chosen_file(config_dir, clean_registry):
    """A new preset lands in the file the operator chose, and no other file is touched."""
    from core.utils import configwrite

    first = preset_file(config_dir)
    second = preset_file(config_dir, "Summer:\n  temperature: 23\n", name="presets2.yaml")
    first_before = sha256(first)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Fresh", new=True, file="config/presets2.yaml",
                                change={"temperature": "4"})
        assert reply.status_code == 303
        assert reply.headers["location"] == f"{presets_page.wizard_url('Fresh')}?step=review"

    assert sha256(first) == first_before, "the other file is untouched"
    document = configwrite.load_config_file(second)
    assert document["Fresh"]["temperature"] == 4
    assert document["Summer"]["temperature"] == 23


@pytest.mark.asyncio
async def test_a_destination_file_no_node_names_is_refused(config_dir, clean_registry):
    """A posted destination that names no offered file is refused, and nothing is written."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Fresh", new=True, file="config/presets.yaml")
        data["file"] = "config/nowhere.yaml"      # a destination no node names
        data["_action"] = "save"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        assert not (config_dir / "nowhere.yaml").exists()
        html = await page(http, presets_page.PRESETS_NEW_PATH)
    assert "is not a preset file this installation names" in html


# ------------------------------------------------------------------- 7. the guards around the door

@pytest.mark.asyncio
async def test_a_write_without_a_csrf_token_is_refused(config_dir, clean_registry):
    """CSRF: a POST that carries no session token never reaches the write."""
    path = preset_file(config_dir)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Spring")
        data["_action"] = "save"
        data["temperature"] = "15"
        data.pop("_csrf_token", None)
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
    assert reply.status_code == 403
    assert sha256(path) == before


@pytest.mark.asyncio
async def test_a_caller_without_the_write_capability_is_refused(config_dir, clean_registry):
    """``presets.write`` is Admin-only: a caller without it is refused, nothing is written."""
    path = preset_file(config_dir)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "plain")
        reply = await http.post(presets_page.PRESETS_SAVE_PATH,
                                data={"name": "Spring", "_action": "remove"})
    assert reply.status_code == 403
    assert sha256(path) == before


# ------------------------------------------------------------------- 8. the review form itself

@pytest.mark.asyncio
async def test_the_review_step_renders_a_real_save_form(config_dir, clean_registry):
    """The save bar is a POST to the ONE write route, carrying the revision and the opened name."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    assert re.search(r'<form[^>]*method="post"[^>]*action="' +
                     re.escape(presets_page.PRESETS_SAVE_PATH) + '"', html)
    fields = hidden_fields(html)
    assert fields[presets_page.PRESETS_REVISION_FIELD]
    assert fields[presets_page.PRESETS_ORIGINAL_FIELD] == "Spring"
    assert fields[presets_page.PRESETS_FILE_FIELD] == "config/presets.yaml"
    assert fields["_csrf_token"]
    # the save control is a real submit naming the action field; the removal does NOT live here —
    # it is the Presets page's own confirm surface, and this form cannot reach it.
    assert f'name="{presets_page.PRESETS_ACTION_FIELD}" value="{presets_page.PRESETS_ACTION_SAVE}"' in html
    assert f'value="{presets_page.PRESETS_ACTION_REMOVE}"' not in html, \
        "the wizard's review step still draws a remove control"


@pytest.mark.asyncio
async def test_a_new_preset_offers_no_removal(config_dir, clean_registry):
    """A NEW preset has nothing to remove, so the Remove control is not drawn."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.PRESETS_NEW_PATH}?step=review&name=Fresh")
    assert f'value="{presets_page.PRESETS_ACTION_REMOVE}"' not in html
    assert f'value="{presets_page.PRESETS_ACTION_SAVE}"' in html


# ------------------------------------------------------- 9. the destination is the file a node NAMES
# The extension reads ONLY the files its own ``MizEdit.presets`` setting lists, so a new preset
# written anywhere else would never apply. The offered destinations are that set.

@pytest.mark.asyncio
async def test_a_new_preset_for_a_file_no_node_names_is_refused(config_dir, clean_registry):
    """A destination no node's ``MizEdit.presets`` names is refused, and the refusal names the node."""
    first = preset_file(config_dir)
    other = preset_file(config_dir, "Summer:\n  temperature: 23\n", name="presets2.yaml")
    nodes_file(config_dir, NODES_NAMING_FILES)
    first_before, before = sha256(first), sha256(other)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Fresh", new=True, file="config/presets.yaml")
        data["file"] = "config/presets2.yaml"      # a destination no node's MizEdit.presets names
        data["_action"] = "save"
        data["temperature"] = "4"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        assert sha256(other) == before, "a file no node names must not be written"
        assert sha256(first) == first_before, "the refusal writes NOTHING, not even the fallback file"
        html = await page(http, presets_page.PRESETS_NEW_PATH)
    assert "is not named by any node" in html
    assert "MizEdit.presets" in html
    assert "NODE1" in html and "Instance1" in html


@pytest.mark.asyncio
async def test_a_new_preset_lands_in_a_file_a_node_names(config_dir, clean_registry):
    """A file a node names that does not exist yet is OFFERED and the save creates it."""
    from core.utils import configwrite

    preset_file(config_dir)
    nodes_file(config_dir, NODES_NAMING_FILES)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_NEW_PATH)
        assert "config/presets_weather.yaml" in html
        assert presets_page.WIZARD_DEST_NAMED in html_module.unescape(html)
        assert presets_page.WIZARD_DEST_NOT_YET in html
        reply = await post_save(http, "Fresh", new=True, file="config/presets_weather.yaml",
                                change={"temperature": "4"})
        assert reply.status_code == 303, reply.text

    document = configwrite.load_config_file(config_dir / "presets_weather.yaml")
    assert document["Fresh"]["temperature"] == 4


@pytest.mark.asyncio
async def test_the_offered_destinations_are_the_files_a_node_names(config_dir, clean_registry):
    """The offered list is the set the nodes name — a discovered file NO node names is not offered."""
    preset_file(config_dir)
    preset_file(config_dir, "Summer:\n  temperature: 23\n", name="presets2.yaml")
    nodes_file(config_dir, NODES_NAMING_ONE)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_NEW_PATH)
    assert "config/presets.yaml" in html
    assert "config/presets2.yaml" not in html, "a file no node names must not be offered"
    assert presets_page.WIZARD_DEST_NAMED in html_module.unescape(html)


def test_mizedit_preset_files_reads_the_instances_own_setting():
    """The scan reads ``MizEdit.presets`` in both shapes and labels a path as the page prints it."""
    document = {"NODE1": {"instances": {
        "One": {"extensions": {"MizEdit": {"presets": ["config/a.yaml", "config/b.yaml"]}}},
        "Two": {"extensions": {"MizEdit": {"presets": "config/a.yaml"}}},
        "None": {"extensions": {"MizEdit": {"settings": "Spring"}}},
    }}}
    assert presets_page.mizedit_preset_files(document, Path("config")) == [
        ("config/a.yaml", "node 'NODE1', instance 'One'"),
        ("config/b.yaml", "node 'NODE1', instance 'One'"),
    ]


# ------------------------------------------------------------- 10. the fog rows must survive

@pytest.mark.asyncio
async def test_the_fog_rows_survive_a_save_and_the_table_carries_them(config_dir, clean_registry):
    """A preset with fog rows at 300 and 600 keeps BOTH after a save that changes another field, and
    the wizard's TABLE carries them (the review posts the table it draws)."""
    from core.utils import configwrite

    path = preset_file(config_dir, FOGGED)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        step = await page(http, f"{presets_page.wizard_url('Spring')}?step=wind")
        # the table draws ONE row per time-keyed row the preset carries, and none of them bare. The
        # two distance cells state the stored metres in the unit the table is drawn in (feet here).
        thickness = weather_fields.display_value("thickness", 200)
        visibility = weather_fields.display_value("visibility", 1000)
        assert re.search(r'name="fog_row_0_time" value="300"', step)
        assert re.search(r'name="fog_row_1_time" value="600"', step)
        assert re.search(rf'name="fog_row_0_thickness" value="{thickness}"', step)
        assert re.search(rf'name="fog_row_1_visibility" value="{visibility}"', step)
        review = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
        # the review POSTS the table in the preset's own metres, whatever the render displayed.
        assert f'name="{weather_fields.FOG_ROWS_KEY}" value="300:200:, 600::1000"' in review
        reply = await post_save(http, "Spring", change={"temperature": "15"})

    assert reply.status_code == 303, reply.text
    raw = configwrite.load_config_file(path)["Spring"]["fog"]
    fog = {str(key): value for key, value in raw.items()}      # the time keys as the file spells them
    assert fog["mode"] == "manual"
    assert fog["300"] == {"thickness": 200}
    assert fog["600"] == {"visibility": 1000}


@pytest.mark.asyncio
async def test_a_fog_row_a_form_states_stays_removed(config_dir, clean_registry):
    """A form that CARRIES the table is authoritative: a row the operator removed stays removed."""
    from core.utils import configwrite

    path = preset_file(config_dir, FOGGED)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", change={weather_fields.FOG_ROWS_KEY: "300:200:"})

    assert reply.status_code == 303, reply.text
    raw = configwrite.load_config_file(path)["Spring"]["fog"]
    fog = {str(key): value for key, value in raw.items()}
    assert fog == {"mode": "manual", "300": {"thickness": 200}}


def test_preset_map_from_values_writes_the_stated_rows_and_carries_the_unstated_ones():
    """The produced fog map is the table the FORM stated; a form that left the table out keeps the
    rows the preset already carries (the defect that dropped them), and ``auto`` writes none."""
    values = {key: "" for key in presets_page.WIZARD_KEYS}
    values[weather_fields.HAZE_KEY] = "fog"
    values[weather_fields.FOG_MODE_KEY] = "manual"
    values[weather_fields.FOG_ROWS_KEY] = "0:150:900"
    existing = {"fog": {"mode": "manual", "300": {"thickness": 200}}}
    stated = presets_page.preset_map_from_values(values, existing, fog_rows_stated=True)
    assert stated["fog"] == {"mode": "manual", 0: {"thickness": 150, "visibility": 900}}
    carried = presets_page.preset_map_from_values(values, existing)
    assert carried["fog"] == {"mode": "manual", 0: {"thickness": 150, "visibility": 900}}
    # a form that states NOTHING about the table keeps what the preset carries.
    values[weather_fields.FOG_ROWS_KEY] = ""
    assert presets_page.preset_map_from_values(values, existing)["fog"] == {
        "mode": "manual", 300: {"thickness": 200}}
    # ``auto`` is DCS's own build: no rows at all, even with a table posted beside it.
    values[weather_fields.FOG_MODE_KEY] = "auto"
    values[weather_fields.FOG_ROWS_KEY] = "0:150:900"
    assert presets_page.preset_map_from_values(values, existing, fog_rows_stated=True)["fog"] == {
        "mode": "auto"}


@pytest.mark.asyncio
async def test_a_fog_time_that_is_no_whole_number_is_refused_and_nothing_is_written(config_dir,
                                                                                   clean_registry):
    """A row is keyed by SECONDS: a time that is not one is refused in plain words, not dropped."""
    from core.utils import configwrite

    path = preset_file(config_dir, FOGGED)
    before = configwrite.load_config_file(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring",
                                change={weather_fields.FOG_ROWS_KEY: "half past:200:100"})

    assert reply.status_code == 303, reply.text
    assert configwrite.load_config_file(path) == before


# ------------------------------------------------- 11. dust and fog, on the way in

@pytest.mark.asyncio
async def test_dust_and_fog_can_never_both_be_written(config_dir, clean_registry):
    """ONE either-or value: choosing dust clears the fog keys, choosing fog clears the dust keys, and
    ``off`` clears both — DCS applies one of the two, so the file never carries the pair."""
    from core.utils import configwrite

    path = preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        dust = await post_save(http, "Spring", change={weather_fields.HAZE_KEY: "dust",
                                                       "dust_density": "10000"})
        assert dust.status_code == 303, dust.text
        written = configwrite.load_config_file(path)["Spring"]
        assert written["enable_dust"] is True and written["dust_density"] == 3048
        assert "enable_fog" not in written and "fog" not in written

        fog = await post_save(http, "Spring", change={weather_fields.HAZE_KEY: "fog",
                                                      weather_fields.FOG_MODE_KEY: "auto"})
        assert fog.status_code == 303, fog.text
        written = configwrite.load_config_file(path)["Spring"]
        assert written["enable_fog"] is True and written["fog"] == {"mode": "auto"}
        assert "enable_dust" not in written and "dust_density" not in written

        off = await post_save(http, "Spring", change={weather_fields.HAZE_KEY: "off"})
        assert off.status_code == 303, off.text
        written = configwrite.load_config_file(path)["Spring"]
        assert "enable_dust" not in written and "dust_density" not in written
        assert "enable_fog" not in written and "fog" not in written


# ------------------------------------------------------------------ 11. the revision cannot be skipped

@pytest.mark.asyncio
async def test_a_post_without_a_revision_is_refused_after_the_file_moved(config_dir, clean_registry):
    """A POST that carries no ``_revision`` is refused while the file has moved — nothing is written."""
    path = preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Spring")
        path.write_text(SPRING + "Late:\n  temperature: 1\n", encoding="utf-8")
        before = sha256(path)
        data.pop(presets_page.PRESETS_REVISION_FIELD, None)
        data["_action"] = "save"
        data["temperature"] = "15"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        assert sha256(path) == before, "a POST with no revision must write nothing"
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    assert "carried no revision" in html


# --------------------------------------------------------- 12. the case-insensitive match is ONE key

@pytest.mark.asyncio
async def test_a_wrongly_cased_original_mutates_the_existing_preset(config_dir, clean_registry):
    """A ``_original`` differing only in case resolves to the ONE key the handler mutates — never a
    second preset beside it."""
    from core.utils import configwrite

    path = preset_file(config_dir)
    nodes_file(config_dir, NODES_OTHER)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Spring")
        data[presets_page.PRESETS_ORIGINAL_FIELD] = "spring"
        data["name"] = "spring"
        data["_action"] = "save"
        data["temperature"] = "15"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303, reply.text

    document = configwrite.load_config_file(path)
    keys = [str(key) for key in document]
    assert len(keys) == 1, f"a wrongly-cased _original must not add a twin: {keys}"
    assert document[keys[0]]["temperature"] == 15


# --------------------------------------------------- 13. the two remove-branch messages (F5)

@pytest.mark.asyncio
async def test_a_case_only_remove_names_the_real_spelling(config_dir, clean_registry):
    """A remove whose posted name differs only in case leaves the file alone and names its spelling."""
    path = preset_file(config_dir, SPRING + LONELY)
    nodes_file(config_dir, NODES_OTHER)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Spring")
        data[presets_page.PRESETS_ORIGINAL_FIELD] = "spring"
        data["name"] = "spring"
        data["_action"] = "remove"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, presets_page.PRESETS_PATH)
    assert "Nothing changed" in html
    assert "The preset is spelled" in html and "Spring" in html


@pytest.mark.asyncio
async def test_a_remove_form_naming_another_preset_is_not_refused(config_dir, clean_registry):
    """The remove branch does not run the collision check: a form carrying ANOTHER preset's name
    removes the preset it opened, never refused with the "already exists" sentence."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING + LONELY)
    nodes_file(config_dir, NODES_OTHER)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        data = await review_form(http, "Spring")
        data["name"] = "Lonely"          # a name equal to ANOTHER preset
        data["_action"] = "remove"
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        html = await page(http, presets_page.PRESETS_PATH)
    assert "already exists" not in html
    document = configwrite.load_config_file(path)
    assert "Spring" not in document
    assert "Lonely" in document


# ------------------------------------------- 14. expert mode on the review step (one YAML editor)
#
# The review step's editor posts a WHOLE document for the ONE preset the wizard has open. It rides the
# SAME route, the SAME capability and the SAME revision guard as the plain save, and it writes that one
# key through the SAME primitive — so a sibling preset, and the file the preset lives in, never move.

#: two presets in ONE file; ``Spring`` carries keys the wizard's controls do NOT render (and that do
#: not make it the expert DSL), so an expert save can be shown to keep them when the edited document
#: states them.
TWO_PRESETS = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  forcedOptions:
    fuel: false
Other:
  temperature: 1
  qnh: 760
"""


def expert_prefill(html: str) -> str:
    """The expert editor's text, exactly as the review step rendered it."""
    match = re.search(r'<textarea[^>]*name="_yaml"[^>]*>(.*?)</textarea>', html, re.S)
    assert match, "the review step rendered no expert editor to post"
    return html_module.unescape(match.group(1))


async def post_expert(http, name: str, *, text: str | None = None, new: bool = False,
                      file: str = "", change: dict | None = None, drop: tuple[str, ...] = ()):
    """POST the review step's own form in EXPERT mode, with *text* as the edited document.

    The form is read off the RENDERED page (its CSRF token, its revision and the values it carries),
    and *text* defaults to the editor's own prefill — so a test that posts unchanged proves the
    review's YAML and the save agree.
    """
    url = presets_page.PRESETS_NEW_PATH if new else presets_page.wizard_url(name)
    params = "?step=review"
    if new:
        params += f"&name={name}"
        if file:
            params += f"&file={file}"
    html = await page(http, url + params)
    data = hidden_fields(html)
    data["_action"] = "save"
    data["_expert"] = "1"
    data["_yaml"] = expert_prefill(html) if text is None else text
    if change:
        data.update(change)
    for key in drop:
        data.pop(key, None)
    return await http.post(presets_page.PRESETS_SAVE_PATH, data=data)


@pytest.mark.asyncio
async def test_an_expert_save_of_the_reviews_own_yaml_writes_what_the_review_showed(config_dir,
                                                                                    clean_registry):
    """The positive control: the editor's prefill IS the document — saving it back changes nothing.

    The preset the review showed already carries exactly what the wizard produced for it, so the write
    is the primitive's own NO-OP: the file's bytes are untouched and no backup is kept, which is only
    true if the prefill round-trips through the ONE reader and writer unchanged.
    """
    from core.utils import configwrite

    path = preset_file(config_dir)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_expert(http, "Spring")
        assert reply.status_code == 303, reply.text
        assert reply.headers["location"] == f"{presets_page.wizard_url('Spring')}?step=review"
        html = await page(http, reply.headers["location"])
    assert "Nothing changed — the file is untouched." in html
    assert sha256(path) == before, "the review's own YAML must round-trip byte-identical"
    assert not list(config_dir.glob("*.bak")), "a no-op write keeps no backup"
    document = configwrite.load_config_file(path)
    assert document["Spring"]["date"] == "2016-04-21"
    assert document["Spring"]["temperature"] == 8
    assert document["Spring"]["qnh"] == 760


@pytest.mark.asyncio
async def test_an_expert_edit_changes_that_preset_only(config_dir, clean_registry):
    """The edited document replaces the ONE open preset; every sibling survives byte-identical."""
    from core.utils import configwrite

    path = preset_file(config_dir, TWO_PRESETS)
    before_text = path.read_text("utf-8")
    before_other = configwrite.dump_config(configwrite.load_config_file(path)["Other"])
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        # the wizard's OWN controls post other values; an expert save must not apply them.
        reply = await post_expert(
            http, "Spring", change={"temperature": "99", "qnh": "1"},
            text=("Spring:\n"
                  "  date: '2017-01-02'\n"
                  "  temperature: 21\n"
                  "  qnh: 780\n"
                  "  forcedOptions:\n"
                  "    fuel: false\n"))
        assert reply.status_code == 303, reply.text
    document = configwrite.load_config_file(path)
    assert list(document) == ["Spring", "Other"], "expert mode must not add or reorder a preset"
    assert document["Spring"] == {"date": "2017-01-02", "temperature": 21, "qnh": 780,
                                  "forcedOptions": {"fuel": False}}
    # THE SIBLING: byte-identical as a document AND in the file's own text.
    assert configwrite.dump_config(document["Other"]) == before_other
    after_text = path.read_text("utf-8")
    assert after_text.split("Other:")[1] == before_text.split("Other:")[1], (
        "the sibling preset's own bytes must survive an expert save")
    assert "temperature: 99" not in after_text


@pytest.mark.asyncio
async def test_a_malformed_expert_yaml_is_refused_and_nothing_is_written(config_dir, clean_registry):
    """Bad YAML is ONE plain sentence through the notice — never a traceback, never a write."""
    path = preset_file(config_dir, TWO_PRESETS)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_expert(http, "Spring", text="Spring:\n  date: '2016-04-21\n  bad: [")
        assert reply.status_code == 303
        assert sha256(path) == before, "an unreadable document must write nothing"
        html = await page(http, reply.headers["location"])
    assert "could not be read as YAML" in html
    assert "Traceback" not in html and "ConfigParseError" not in html
    assert not list(config_dir.glob("*.bak"))


@pytest.mark.asyncio
async def test_a_schema_invalid_expert_yaml_is_refused_and_nothing_is_written(config_dir,
                                                                             clean_registry):
    """The edited document is validated against the presets schema BEFORE anything is written."""
    path = preset_file(config_dir, TWO_PRESETS)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_expert(http, "Spring", text="Spring:\n  temperature: not-a-number\n")
        assert reply.status_code == 303
        assert sha256(path) == before, "a schema-invalid document must write nothing"
        html = await page(http, reply.headers["location"])
    assert "The change was refused, and nothing was written." in html
    assert "Traceback" not in html
    assert not list(config_dir.glob("*.bak"))


@pytest.mark.asyncio
async def test_expert_mode_cannot_rename_or_delete_the_preset(config_dir, clean_registry):
    """The door edits the VALUE of the open preset: a rename, a deletion and a several-preset
    document are each refused with their own sentence, and the file never moves."""
    path = preset_file(config_dir, TWO_PRESETS)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        # each refusal leaves its OWN one-shot notice, so each page is read before the next POST.
        renamed = await post_expert(http, "Spring", text="Renamed:\n  temperature: 3\n")
        assert renamed.status_code == 303
        assert sha256(path) == before, "a rename must write nothing"
        renamed_html = await page(http, renamed.headers["location"])
        several = await post_expert(http, "Spring", text=TWO_PRESETS)
        assert several.status_code == 303
        assert sha256(path) == before, "a several-preset document must write nothing"
        several_html = await page(http, several.headers["location"])
        deleted = await post_expert(http, "Spring", text="")
        assert deleted.status_code == 303
        assert sha256(path) == before, "a deletion must write nothing"
        deleted_html = await page(http, deleted.headers["location"])
    assert "Expert mode cannot rename a preset" in renamed_html
    # the notice renders the operator's own names verbatim, HTML-escaped like every other message.
    assert "&#39;Renamed&#39;" in renamed_html and "&#39;Spring&#39;" in renamed_html
    assert "carries 2 preset keys (&#39;Spring&#39;, &#39;Other&#39;)" in several_html
    assert "carries no preset at all" in deleted_html


@pytest.mark.asyncio
async def test_expert_mode_writes_the_file_the_preset_lives_in(config_dir, clean_registry):
    """A posted ``file`` cannot move the preset: the write goes to the file its name was found in."""
    from core.utils import configwrite

    path = preset_file(config_dir, TWO_PRESETS)
    sibling_file = config_dir / "presets_weather.yaml"
    sibling_file.write_text("Autumn:\n  temperature: 4\n", encoding="utf-8")
    sibling_before = sha256(sibling_file)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_expert(http, "Spring", text="Spring:\n  temperature: 30\n",
                                 change={presets_page.PRESETS_FILE_FIELD:
                                         "config/presets_weather.yaml"})
        assert reply.status_code == 303
    assert sha256(sibling_file) == sibling_before, "expert mode cannot change the file it writes"
    assert configwrite.load_config_file(path)["Spring"]["temperature"] == 30
    assert configwrite.load_config_file(sibling_file)["Autumn"]["temperature"] == 4


@pytest.mark.asyncio
async def test_a_stale_expert_form_is_refused(config_dir, clean_registry):
    """The SAME revision guard: a form rendered before the file moved writes nothing."""
    path = preset_file(config_dir, TWO_PRESETS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
        data = hidden_fields(html)
        # the file moves on disk AFTER the form was rendered
        path.write_text(TWO_PRESETS + "Late:\n  temperature: 1\n", encoding="utf-8")
        before = sha256(path)
        data.update({"_action": "save", "_expert": "1", "_yaml": "Spring:\n  temperature: 33\n"})
        reply = await http.post(presets_page.PRESETS_SAVE_PATH, data=data)
        assert reply.status_code == 303
        assert sha256(path) == before, "a stale expert form must write nothing"
        refreshed = await page(http, reply.headers["location"])
    assert "stale" in refreshed.lower()


def test_expert_mode_needs_no_new_capability():
    """Every preset write rides ``presets.write`` — the one write capability the area declares.

    Two write routes exist and both name that capability: the area's whole-document save (which carries
    the review step's expert-mode save) and the one-block editor's own save. No third write route may
    appear without declaring it here.
    """
    capabilities = presets_page.capabilities()
    assert capabilities[presets_page.PRESETS_SAVE_PATH] == presets_page.PRESETS_WRITE_CAPABILITY
    assert capabilities[presets_page.PRESETS_YAML_SAVE_PATH] == presets_page.PRESETS_WRITE_CAPABILITY
    assert presets_page.PRESETS_WRITE_CAPABILITY == "presets.write"
    writers = sorted(path for path, capability in capabilities.items()
                     if capability == presets_page.PRESETS_WRITE_CAPABILITY)
    assert writers == sorted([presets_page.PRESETS_SAVE_PATH, presets_page.PRESETS_YAML_SAVE_PATH]), \
        f"a preset write route was added or lost without declaring it here: {writers}"


@pytest.mark.asyncio
async def test_expert_mode_can_create_a_new_preset_in_the_chosen_file(config_dir, clean_registry):
    """A NEW preset's expert save writes the document's ONE key into the file the operator chose."""
    from core.utils import configwrite

    first = preset_file(config_dir)
    second = preset_file(config_dir, "Summer:\n  temperature: 23\n", name="presets2.yaml")
    first_before = sha256(first)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_expert(http, "Fresh", new=True, file="config/presets2.yaml",
                                  text="Fresh:\n  temperature: 4\n")
        assert reply.status_code == 303
        assert reply.headers["location"] == f"{presets_page.wizard_url('Fresh')}?step=review"
    assert sha256(first) == first_before, "the other file is untouched"
    document = configwrite.load_config_file(second)
    assert document["Fresh"] == {"temperature": 4}
    assert document["Summer"]["temperature"] == 23


@pytest.mark.asyncio
async def test_a_remove_is_never_answered_by_the_expert_door(config_dir, clean_registry):
    """The remove button removes the preset: the edited document is NOT what a removal writes."""
    from core.utils import configwrite

    path = preset_file(config_dir, TWO_PRESETS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_expert(http, "Spring", text="Spring:\n  temperature: 30\n",
                                  change={presets_page.PRESETS_ACTION_FIELD:
                                          presets_page.PRESETS_ACTION_REMOVE})
        assert reply.status_code == 303
    document = configwrite.load_config_file(path)
    assert "Spring" not in document, "a remove removes, whatever the editor holds"
    assert document["Other"]["temperature"] == 1
