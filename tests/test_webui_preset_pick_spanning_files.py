"""The launch pick spans preset FILES: every file's group is drawn, the file control filters only.

One section per acceptance criterion:

1. TWO FILES IN ONE PICK — a tick from each file reaches the action as two documents, in the node's
   config order, with both names beside them;
2. A SHARED NAME — the entry ticked under a file applies THAT file's body;
3. THE FILTER — it hides groups in the browser, never a tick: a POST whose filter names one file
   still applies the entry ticked under the other;
4. A BARE NAME — a direct / REST caller posting one keeps working (the named file, else the node's
   merged map);
5. ONE FILE — no groups, no filter, a flat list, and the chosen block as on every pick;
6. BOTH DIALOGS draw the same chosen block.

``\n`` is the page's file/name separator: the value of a grouped entry carries its own file.
"""
from __future__ import annotations

import re

import pytest

from services.webservice.pages import actions as actions_page
from test_webui_missions_load_form import (  # noqa: F401 - fixtures imported on purpose
    load_fleet, open_load_dialog,
)
from test_webui_startup_weather_picker import (  # noqa: F401 - fixtures imported on purpose
    STARTUP, PresetNode, node_fleet, open_dialog, pick_block,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

SEP = "\n"
MODE_FIELD = actions_page.WEATHER_MODE_FIELD
MODE_FILE = actions_page.WEATHER_MODE_FILE
FILE_FIELD = actions_page.PRESET_FILE_FIELD
PRESET_FIELD = actions_page.PRESET_FIELD
FLAT = "config/presets.yaml"
EXTRA = "config/presets/extra.yaml"

SPAN_FLAT = """\
Summer:
  modify:
    weather:
      clouds:
        base: 1000
Winter:
  modify:
    weather:
      clouds:
        base: 500
"""

SPAN_EXTRA = """\
Snow:
  modify:
    weather:
      clouds:
        base: 300
Fog:
  modify:
    weather:
      clouds:
        base: 1200
"""

DUPLICATE_FLAT = """\
Storm:
  modify:
    weather:
      clouds:
        base: 900
Flat_Only:
  modify:
    weather:
      clouds:
        base: 1200
"""

DUPLICATE_EXTRA = """\
Storm:
  modify:
    weather:
      clouds:
        base: 100
Extra_Only:
  modify:
    weather:
      clouds:
        base: 200
"""


def entry(label: str, name: str) -> str:
    """A grouped pick entry's POST value: the file, then the name."""
    return f"{label}{SEP}{name}"


def two_files(config_dir, flat: str = SPAN_FLAT, extra: str = SPAN_EXTRA) -> None:
    """The node's two preset files, the flat one and one beside it."""
    (config_dir / "presets.yaml").write_text(flat, encoding="utf-8")
    (config_dir / "presets").mkdir(exist_ok=True)
    (config_dir / "presets" / "extra.yaml").write_text(extra, encoding="utf-8")


def file_select(block: str) -> str | None:
    """The rendered preset-FILE control's own ``<p>`` wrapper, or ``None`` when undrawn."""
    match = re.search(r'<p class="dlg-field">\s*<label for="preset_file-dialog">.*?</p>',
                      block, re.S)
    return match.group(0) if match else None


def mode_post(dialog, entries, *, file_value: str = "") -> dict:
    return {"server": "SRS-1", "_csrf_token": token_of(dialog.text),
            MODE_FIELD: MODE_FILE, FILE_FIELD: file_value, PRESET_FIELD: entries}


# ------------------------------------------------------------------ 1. two files in one pick

@pytest.mark.asyncio
async def test_two_files_names_in_one_pick_reach_the_action_as_documents_and_names(
        config_dir, clean_registry):
    """A tick out of EACH file is ONE pick: two documents in the node's config order (flat first,
    then the ``presets/`` file), and both names beside them."""
    two_files(config_dir)
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data=mode_post(
            dialog, [entry(FLAT, "Summer"), entry(EXTRA, "Snow")]))

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == [{"modify": {"weather": {"clouds": {"base": 1000}}}},
                              {"modify": {"weather": {"clouds": {"base": 300}}}}], call
    assert call["preset_name"] == "Summer,Snow", call


# ------------------------------------------------------------------------ 2. a shared name

@pytest.mark.asyncio
async def test_a_name_in_both_files_applies_the_body_of_the_group_it_was_ticked_in(
        config_dir, clean_registry):
    """``Storm`` exists in both files; the entry ticked under ``presets/extra.yaml`` applies 100,
    not the 900 the flat file defines and not the merge's last-wins answer."""
    two_files(config_dir, DUPLICATE_FLAT, DUPLICATE_EXTRA)
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data=mode_post(dialog, [entry(EXTRA, "Storm")]))

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == {"modify": {"weather": {"clouds": {"base": 100}}}}, call
    assert call["preset_name"] == "Storm", call


# --------------------------------------------------------------------------- 3. the filter

@pytest.mark.asyncio
async def test_the_filter_narrows_the_groups_while_the_post_keeps_the_hidden_groups_tick(
        config_dir, clean_registry):
    """The filter names one file, but the tick came from the other: the rendered list has both
    groups and an "All files" default, and the POST applies the entry the filter hides."""
    two_files(config_dir, DUPLICATE_FLAT, DUPLICATE_EXTRA)
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        block = pick_block(dialog.text)
        assert re.findall(r'<optgroup label="([^"]*)">', block) == [FLAT, EXTRA], block
        assert ">Flat_Only</option>" in block and ">Extra_Only</option>" in block, \
            "one file's group was not drawn before a file was chosen"
        control = file_select(dialog.text)
        assert control is not None, "the file control is not drawn"
        assert "Show" in control, "the file control must be a filter labelled Show"
        assert 'value=""' in control and "All files" in control, \
            "the filter does not default to All files"
        reply = await http.post(STARTUP, data=mode_post(
            dialog, [entry(FLAT, "Storm")], file_value=EXTRA))

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == {"modify": {"weather": {"clouds": {"base": 900}}}}, \
        "the tick from the group the filter hid was dropped or resolved elsewhere"


# ------------------------------------------------------------------------ 4. a bare name

@pytest.mark.asyncio
async def test_a_bare_name_from_a_direct_caller_still_resolves(config_dir, clean_registry):
    """A direct caller posts a bare name: with no filter value it resolves against the node's merged
    map (last file wins), and with a named file it resolves against that file."""
    two_files(config_dir, DUPLICATE_FLAT, DUPLICATE_EXTRA)

    merged_cluster = node_fleet(PresetNode(config_dir))
    merged_app = build(config_dir, merged_cluster)
    async with client(merged_app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        merged = await http.post(STARTUP, data=mode_post(dialog, "Storm"))

    named_cluster = node_fleet(PresetNode(config_dir))
    named_app = build(config_dir, named_cluster)
    async with client(named_app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        named = await http.post(STARTUP, data=mode_post(dialog, "Storm", file_value=FLAT))

    assert merged.status_code == 303, merged.text
    assert named.status_code == 303, named.text
    assert merged_cluster.servers[0].call_args[0]["preset"] == \
        {"modify": {"weather": {"clouds": {"base": 100}}}}, \
        "a bare name without a file did not resolve against the node's merged map"
    assert named_cluster.servers[0].call_args[0]["preset"] == \
        {"modify": {"weather": {"clouds": {"base": 900}}}}, \
        "a bare name with a named file did not resolve against that file"


# ---------------------------------------------------------------------------- 5. one file

@pytest.mark.asyncio
async def test_the_single_file_node_draws_no_groups_and_no_filter(config_dir, clean_registry):
    """ONE preset file: the names are drawn flat and post bare, no ``<optgroup>`` and no filter are
    drawn, and the pick still carries the chosen block."""
    (config_dir / "presets.yaml").write_text(SPAN_FLAT, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        block = pick_block(dialog.text)
        assert "<optgroup" not in block, "a single preset file drew a group heading"
        assert file_select(dialog.text) is None, "a single preset file drew a file filter"
        assert re.findall(r'<option value="([^"]*)"', block) == ["Summer", "Winter"], block
        assert "data-chosen" in block and "Chosen, in this order" in block, \
            "the pick draws no chosen block"
        reply = await http.post(STARTUP, data=mode_post(dialog, "Summer"))

    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].call_args[0]["preset"] == \
        {"modify": {"weather": {"clouds": {"base": 1000}}}}


# ---------------------------------------------------------------------- 6. both dialogs

@pytest.mark.asyncio
async def test_both_launch_dialogs_draw_the_same_chosen_block(config_dir, clean_registry):
    """Startup and Load draw the same ``Chosen, in this order`` block under the same pick."""
    cluster = load_fleet()
    app = build(config_dir, cluster, plugins=("mission",))

    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)

    block = pick_block(dialog.text)
    assert "data-chosen" in block, "the Load dialog draws no chosen block"
    assert "Chosen, in this order" in block
    assert "the server page lists every name" in block, \
        "the chosen block does not state the merge rule"
