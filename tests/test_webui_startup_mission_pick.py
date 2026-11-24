"""The Startup dialog's MISSION PICK — which mission the launch loads.

The dialog used to launch whatever ``listStartIndex`` already named, with no way to say otherwise:
the pick is new, and it is the only website surface that chooses the START mission at launch time
(the Missions tab's "Set as start" moves the pointer outside a launch; Discord's
``/server startup mission:`` is the chat side of the same pair).

One section per acceptance criterion:

1. the RENDERED pick — one entry per mission in the server's rotation, the configured start mission
   SELECTED, form-associated to the dialog's own form and drawn in the BODY above the weather block it
   qualifies; a rotation of ONE mission is stated as a sentence instead of a dropdown that cannot
   choose, and a rotation that cannot be listed withholds the pick and the weather with its own
   sentence;
2. the READ it feeds — the weather prefill is the picked mission's, and the page carries that name so
   the answer's line names the same file;
3. the WRITE — the posted mission reaches the action, which moves the rotation pointer only when the
   choice DIFFERS and then launches; an unknown name starts nothing;
4. the unchanged default — posting the mission the rotation already names writes no pointer at all,
   so "do nothing" still means "change nothing".
"""
from __future__ import annotations

import re

import pytest

from core import Status
import plugins.mission.actions as mission_actions
from services.webservice.pages import actions as actions_page
from test_webui_startup_weather_picker import (  # noqa: F401 - fixtures imported on purpose
    PresetNode, node_fleet, open_dialog,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, recording_bot, sign_in, token_of,
)
from test_webui_write_server_actions import FakeServer

STARTUP = "/actions/server/startup"
PRESET_FIELD = actions_page.PRESET_FIELD
MISSION_FIELD = actions_page.MISSION_FIELD


def pick_block(html: str) -> str:
    """The rendered mission pick, as its own ``<p class="dlg-field">…</p>`` block."""
    match = re.search(r'<p class="dlg-field">\s*<label for="mission-dialog">.*?</p>', html, re.S)
    assert match, "the dialog rendered no mission pick"
    return match.group(0)


def pick_entries(block: str) -> list[tuple[str, bool]]:
    """``(value, selected)`` for every option the pick draws, in the order it draws them."""
    return [(value, " selected" in tag) for value, tag in re.findall(r'<option value="([^"]*)"([^>]*)>', block)]


def posted(http, page, **fields):
    data = {"server": "SRS-1", "_csrf_token": token_of(page)}
    data.update(fields)
    return http.post(STARTUP, data=data)


class OneMissionServer(FakeServer):
    """A rotation with ONE mission: the pick has nothing to choose, so it must not be drawn."""

    async def getMissionList(self):
        return [f"{self.missions_dir}/Training.miz"]

    async def getAllMissionFiles(self):
        return [("Training.miz", f"{self.missions_dir}/Training.miz")]


class UnreadableRotationServer(FakeServer):
    """A rotation the console cannot list: no pick, no weather, and a sentence saying so."""

    async def getMissionList(self):
        raise RuntimeError("missions dir missing")


def fleet_with(server, node):
    import types
    from test_webui_write_pause import Cluster, instance
    server.node = node
    node_a = types.SimpleNamespace(name=node.name, master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", server)})
    return Cluster([server], nodes={node.name: node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", server)])


# ------------------------------------------------------------------- 1. the rendered pick

@pytest.mark.asyncio
async def test_the_pick_lists_the_rotation_with_the_configured_start_mission_selected(config_dir,
                                                                                     clean_registry):
    app = build(config_dir, node_fleet(PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_dialog(http)

    block = pick_block(dialog.text)
    assert pick_entries(block) == [("Training.miz", True), ("Rotated.miz", False)], block
    assert f'name="{MISSION_FIELD}"' in block and 'form="dlg-form"' in block, \
        "the pick must post with the dialog's own form"
    # …and it is drawn ABOVE the weather block it qualifies, in the dialog's body.
    body = dialog.text.index('class="bd"')
    assert body < dialog.text.index('id="mission-dialog"') < dialog.text.index('class="dlg-temp"')


@pytest.mark.asyncio
async def test_a_one_mission_rotation_states_the_fact_instead_of_drawing_a_pick(config_dir,
                                                                                clean_registry):
    server = OneMissionServer("SRS-1", status=Status.SHUTDOWN, managed_by=["Host A"])
    app = build(config_dir, fleet_with(server, PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_dialog(http)

    assert 'id="mission-dialog"' not in dialog.text, "a dropdown with one entry cannot choose"
    assert "Loaded at start: Training.miz" in dialog.text
    # the weather is still offered, read from that one mission
    assert 'data-mission-name="Training.miz"' in dialog.text


@pytest.mark.asyncio
async def test_an_unlistable_rotation_withholds_the_pick_and_says_why(config_dir, clean_registry):
    server = UnreadableRotationServer("SRS-1", status=Status.SHUTDOWN, managed_by=["Host A"])
    app = build(config_dir, fleet_with(server, PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_dialog(http)

    assert 'id="mission-dialog"' not in dialog.text
    assert "mission list could not be read" in dialog.text
    assert 'class="dlg-temp"' not in dialog.text, "nothing to apply a preset to"


# ------------------------------------------------------------------- 2. the read it feeds

@pytest.mark.asyncio
async def test_the_page_names_the_picked_mission_for_the_weather_read(config_dir, clean_registry):
    app = build(config_dir, node_fleet(PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_dialog(http)

    assert 'data-mission-name="Training.miz"' in dialog.text
    assert f'{MISSION_FIELD}=' not in dialog.text.split('class="dlg-temp"')[0].split("<select")[0], \
        "the read's fallback name must not be invented from the pick's markup order"


# ------------------------------------------------------------------- 3. the write

@pytest.mark.asyncio
async def test_a_chosen_mission_moves_the_pointer_and_then_launches(config_dir, clean_registry,
                                                                  recording_bot):
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text, **{MISSION_FIELD: "Rotated.miz"})
    assert reply.status_code == 303, reply.text

    server = cluster.servers[0]
    assert server.calls == ["setStartIndex", "startup"], server.calls
    assert server.call_args[0] == {"mission": 2}
    # the pointer is moved BEFORE the launch: the launch loads listStartIndex
    assert [call[0] for call in recording_bot.calls] == [
        "Server 'SRS-1' started up for mission 'Rotated.miz'."]


@pytest.mark.asyncio
async def test_an_unknown_mission_starts_nothing(config_dir, clean_registry, recording_bot):
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text, **{MISSION_FIELD: "NoSuchMission"})
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].calls == [], "a refused mission must not launch anything"
    assert "not found" in recording_bot.calls[-1][0]


# ------------------------------------------------------------------- 4. the unchanged default

@pytest.mark.asyncio
async def test_the_configured_mission_writes_no_pointer_at_all(config_dir, clean_registry,
                                                              recording_bot):
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_dialog(http)
        await posted(http, dialog.text, **{MISSION_FIELD: "Training.miz"})

    server = cluster.servers[0]
    assert server.calls == ["startup"], "the default choice must write nothing"
    assert [call[0] for call in recording_bot.calls] == ["Server 'SRS-1' started up."]


def test_the_action_takes_the_mission_and_the_dialog_declares_it():
    """The two names that must agree: the action's parameter and the declaration's field."""
    import inspect
    from services.webservice.pages.actions import SERVER_ACTIONS

    assert "mission" in inspect.signature(mission_actions.startup_server).parameters
    action = next(item for item in SERVER_ACTIONS if item.key == "startup")
    assert action.mission_select is not None
    assert action.mission_select.field == MISSION_FIELD
