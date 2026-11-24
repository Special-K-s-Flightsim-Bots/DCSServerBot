"""The launch pick's per-entry FILE LABEL is the node's own, or the POST is refused.

The parent's pins cover the filter, the shared name and the bare name; the label INSIDE a pick
entry's value is the one input this batch added, and a hostile value there has no pin. Three:

1. a grouped entry whose file label is not a label the node listed — a traversal, an absolute path,
   a drive letter, or a shape-legal name the render never offered — is REFUSED, and nothing launches
   (the entry is never silently dropped into another file's body);
2. a pick posted in REVERSE order still reaches the action in the node's own config order — the
   order is the files', never the click order;
3. a LABEL-ONLY entry (a file label and an EMPTY name after the separator) keeps its shape through
   the read and is refused as an EMPTY PICK — never as an unknown preset, and never resolved as a
   bare name into a file the operator did not name.
"""
from __future__ import annotations

import json

import pytest

from services.webservice.pages import actions as actions_page
from test_webui_preset_pick_spanning_files import (  # noqa: F401 - fixtures imported on purpose
    EXTRA, FLAT, SPAN_EXTRA, SPAN_FLAT, entry, mode_post, two_files,
)
from test_webui_startup_weather_picker import (  # noqa: F401 - fixtures imported on purpose
    STARTUP, PresetNode, node_fleet, open_dialog,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

MODE_FIELD = actions_page.WEATHER_MODE_FIELD


@pytest.mark.parametrize("bad_label", [
    "../../etc/passwd",
    "/etc/presets.yaml",
    "C:\\presets.yaml",
    "\\\\host\\share\\presets.yaml",
    "config/presets/other.yaml",
])
@pytest.mark.asyncio
async def test_a_grouped_entry_whose_label_the_node_never_listed_is_refused(
        config_dir, clean_registry, bad_label):
    """The pick entry carries its own file label; a hostile one is a refusal, never a body."""
    two_files(config_dir)
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data=mode_post(
            dialog, [entry(bad_label, "Summer")]))

    assert reply.status_code == 400, reply.text
    assert cluster.servers[0].call_args == [], "a hostile pick entry launched"
    assert actions_page.REFUSAL_WEATHER_UNKNOWN_FILE in reply.text


@pytest.mark.asyncio
async def test_a_pick_posted_in_reverse_order_still_applies_the_nodes_config_order(
        config_dir, clean_registry):
    """Extra's entry ticked first, the flat one second: the action sees flat first all the same."""
    two_files(config_dir, SPAN_FLAT, SPAN_EXTRA)
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data=mode_post(
            dialog, [entry(EXTRA, "Snow"), entry(FLAT, "Summer")]))

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == [{"modify": {"weather": {"clouds": {"base": 1000}}}},
                              {"modify": {"weather": {"clouds": {"base": 300}}}}], call
    assert call["preset_name"] == "Summer,Snow", call


@pytest.mark.asyncio
async def test_a_label_only_entry_is_refused_and_never_launched(config_dir, clean_registry):
    """A value that is only a file label carries no preset name: the answer is the EMPTY-PICK one.

    The entry's trailing separator IS its shape, so it has to survive the read — read through a
    whole-value trim it reads as a bare preset name and the operator is told a preset they picked is
    unknown, which is not what happened.
    """
    two_files(config_dir)
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data=mode_post(dialog, [entry(EXTRA, "")]))

    assert reply.status_code == 400, reply.text
    assert cluster.servers[0].call_args == [], "a nameless pick entry launched"
    # the refusal travels as a JSON detail, so its own quoted sentence arrives escaped
    assert json.loads(reply.text)["detail"] == actions_page.REFUSAL_WEATHER_NO_PICK, reply.text
    assert actions_page.REFUSAL_WEATHER_UNKNOWN_PRESET not in reply.text, \
        "a label-only entry was refused as an unknown preset"
