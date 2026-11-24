"""The Missions tab's Load control gains the SAME launch form the Startup dialog carries.

Slice S4. The row's ``Load`` control used to post immediately; it now opens the console's ONE dialog
(``confirm.html``), which renders the launch form — the weather pick, the temporary preset's weather
fields and the announce box — from the ONE declaration the Startup dialog already renders
(``WEATHER_PRESET_SELECT`` / ``ANNOUNCE_OPTION`` / ``WriteAction.weather``). No second copy of the
fields exists: the two dialogs read the same objects.

One section per acceptance criterion:

1. the RENDERED dialog carries the select, the temporary fieldset and the announce box, and the
   declarations are the SAME objects Startup renders (identity, not a duplicate);
2. a STALE POST (the list changed between render and POST) is still refused — the row's LOGICAL name
   travels through the dialog;
3. a preset picked on Load reaches ``server.loadMission`` with it, and loading the CURRENT mission
   with a preset APPLIES the weather instead of silently dropping it (the restart trap);
4. a Load with no preset behaves exactly as today, and the dialog is a real server-rendered form
   (progressive enhancement: with JavaScript off the picker still works).
"""
from __future__ import annotations

import html
import os
import re
import types

import pytest

from core import Status
from core.data.impl.serverimpl import ServerImpl
from core.data.proxy.serverproxy import ServerProxy
from services.webservice.pages import actions as actions_page
import plugins.mission.actions as mission_actions
from test_webui_missions_tab import (  # noqa: F401 - fixtures imported on purpose
    BASE_DIR, CONFIGURED, CONFIGURED_FILES, CURRENT, SERVER_A, SERVER_B, MissionNode, MissionServer,
    Cluster, host_path, instance, missions_page,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

LOAD = actions_page.MISSIONS_LOAD_PATH
LOAD_DIALOG = LOAD + actions_page.CONFIRM_SUFFIX

PRESETS_YAML = """\
Summer:
  modify:
    weather:
      clouds:
        base: 1000
"""

MISSION_CHANGED = mission_actions.MISSION_LIST_CHANGED


# ------------------------------------------------------------------------- doubles

class LoadNode(MissionNode):
    """A node whose preset file lives under *config_dir* — the two primitives the picker reads.

    ``MissionNode`` keeps its ADD-picker fixture in a ``list_files`` ATTRIBUTE, while the launch
    picker reads ``node.list_files`` as a CALLABLE — so the attribute is stashed (``_addable``) and
    the name rebound to the picker's primitive, and ``list_directory`` reads the stash instead.
    ``presets.yaml`` is served only for its own path, so a mission-file read is untouched.
    """

    def __init__(self, name, *, presets_yaml="", config_dir="", **kwargs):
        super().__init__(name, **kwargs)
        self.presets_yaml = presets_yaml
        self.config_dir = config_dir
        self._addable = list(self.list_files)
        self.list_files = self._list_preset_files  # type: ignore[assignment]

    async def _list_preset_files(self, path, *, pattern="*"):
        return [(os.path.join(self.config_dir, "presets.yaml"), 0)] if self.presets_yaml else []

    async def list_directory(self, path, pattern=None, traverse=False, ignore=None):
        if self.list_error is not None:
            raise self.list_error
        return (self.list_base or path), list(self._addable)

    async def read_file(self, path):
        if path.endswith("presets.yaml"):
            return self.presets_yaml.encode("utf-8")
        return await super().read_file(path)


class LoadServer(MissionServer):
    """A mission server whose NODE carries named presets AND whose load path records what it saw.

    ``loadMission`` and ``restart`` record the weather pick they were handed, so the forwarding and
    the restart trap are observable.
    """

    presets_yaml = PRESETS_YAML

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.node = LoadNode(self.node.name, content=self.node.content,
                             config_dir=self.missions_dir, presets_yaml=type(self).presets_yaml)
        self.restarts: list[dict] = []

    async def loadMission(self, mission, modify_mission=True, use_orig=True, no_reload=False, *,
                          preset=None, preset_name=None, announce=False):
        self.loads.append((mission, modify_mission, preset, preset_name, announce))
        return True

    async def restart(self, modify_mission=True, use_orig=True, *, preset=None, preset_name=None,
                      announce=False):
        self.restarts.append({"modify_mission": modify_mission, "use_orig": use_orig,
                              "preset": preset, "preset_name": preset_name,
                              "announce": announce})


def action_by_key(key: str):
    """The ONE declaration with *key*, across the server row and the mission tab."""
    for action in (*actions_page.SERVER_ACTIONS, *actions_page.MISSION_ACTIONS):
        if action.key == key:
            return action
    raise AssertionError(f"no action declared with key {key!r}")


def load_fleet(*, current=CURRENT, status=Status.RUNNING):
    """Server A (Host A) with the shared mission fixture and a preset-carrying node, plus server B."""
    a = LoadServer(SERVER_A, ["Host A"], files=CONFIGURED_FILES, current=current, start=1,
                   configured=CONFIGURED, status=status)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                                instance("Host-B-Instance", b)])


async def open_load_dialog(http, *, name=SERVER_A, mission="2", mission_name="2=Rotated.miz"):
    """Press the row's Load control: POST to its DIALOG route and return the rendered dialog."""
    page = await missions_page(http, server=name)
    token = token_of(page)
    body = {"server": name, "_csrf_token": token, "mission": mission}
    if mission_name is not None:
        body["mission_name"] = mission_name
    reply = await http.post(LOAD_DIALOG, data=body)
    assert reply.status_code == 200, (reply.status_code, reply.text[:400])
    return reply


def dialog_form(html: str) -> str:
    """The dialog's OWN ``<form id="dlg-form">`` block."""
    match = re.search(r'<form id="dlg-form".*?</form>', html, re.S)
    assert match, "the dialog rendered no form"
    return match.group(0)


def pick_block(html: str) -> str:
    """The rendered ``<fieldset class="dlg-field dlg-pick">…</fieldset>`` block."""
    match = re.search(r'<fieldset class="dlg-field dlg-pick".*?</fieldset>', html, re.S)
    assert match, "the Load dialog rendered no weather pick"
    return match.group(0)


def pick_values(block: str) -> list[str]:
    """Every ENTRY's value, in the order the dialog draws them."""
    return re.findall(r'<option value="([^"]*)"', block)


def dialog_hidden(html: str) -> dict:
    """The dialog form's hidden fields — the CSRF token, the target and the row's mission."""
    hidden: dict = {}
    for name, value in re.findall(r'name="([^"]+)" value="([^"]*)"',
                                  dialog_form(html)):
        hidden.setdefault(name, value)
    return hidden


async def post_dialog(http, response, *, name=SERVER_A, **fields):
    """Submit a rendered dialog's form: read its own hidden fields and post them to the action."""
    body = {"server": name, **dialog_hidden(response.text)}
    body.update(fields)
    return await http.post(LOAD, data=body)


class _RestartSink:
    """The minimum ``ServerImpl.restart`` touches: the current mission file and ``loadMission``."""

    def __init__(self):
        self.calls: list[tuple] = []

    def _get_current_mission_file(self):
        return os.path.join(BASE_DIR, "Rotated.miz")

    async def loadMission(self, mission, modify_mission=True, use_orig=True, no_reload=False, *,
                          preset=None, preset_name=None, announce=False):
        self.calls.append((mission, modify_mission, use_orig, preset, preset_name, announce))
        return True


class _Bus:
    def __init__(self):
        self.calls: list[dict] = []

    async def send_to_node_sync(self, message, **kwargs):
        self.calls.append(message)
        return None


# ================================================== 1. the rendered dialog and the shared declarations

@pytest.mark.asyncio
async def test_load_renders_the_same_declarations_startup_renders():
    """The picker and the temporary fields are the SAME objects, not two copies, and neither dialog
    carries the announce box any more.

    Identity, not equality: a duplicate declaration would pass an equal-but-separate check, which is
    exactly the drift this pins out.
    """
    load = action_by_key("mission_load")
    startup = action_by_key("startup")
    assert load.select is startup.select is actions_page.WEATHER_PRESET_SELECT
    assert load.weather is True and startup.weather is True
    assert load.select.field == actions_page.PRESET_FIELD
    load_opts = actions_page.dialog_options(load)
    startup_opts = actions_page.dialog_options(startup)
    assert "announce" not in [option.field for option in load_opts], \
        "Load still carries the removed announce box"
    assert "announce" not in [option.field for option in startup_opts], \
        "Startup still carries the removed announce box"
    # the extensions box is the SAME object on both launch dialogs — the refusal's two inputs
    # (this box and the pick) cannot drift apart
    assert any(option is actions_page.MODIFY_MISSION_OPTION for option in load_opts), \
        "Load does not carry the shared extensions box"
    assert any(option is actions_page.MODIFY_MISSION_OPTION for option in startup_opts), \
        "Startup lost the shared extensions box"
    # the temporary fieldset is ONE source: both dialogs read the same function's answer, and it is a
    # plain POST fieldset here too (no node is passed, so the choice controls carry the table's own
    # ``off``/``auto`` and the pickers fall back to their free-text shape).
    load_fieldset = await actions_page._temporary_fieldset(load)
    startup_fieldset = await actions_page._temporary_fieldset(startup)
    assert load_fieldset == startup_fieldset
    assert load_fieldset is not None
    assert [row["key"] for row in load_fieldset["rows"]] == list(actions_page.TEMPORARY_KEYS)


@pytest.mark.asyncio
async def test_the_load_dialog_draws_the_pick_the_temporary_fieldset_and_the_modes(
        config_dir, clean_registry):
    """The rendered Load dialog carries all three shapes: the ENABLED multi select, the weather
    fieldset as a plain region, and the mode radios — all drawn by the server, none enabled by a
    script. The announce box is gone."""
    app = build(config_dir, load_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)

    html = dialog.text
    block = pick_block(html)
    values = pick_values(block)
    assert values == ["Summer"], values
    assert "disabled" not in block, "the pick must be drawn enabled server-side"
    assert "<select" in block and "multiple" in block, "the pick is not a multi select"
    assert '<div class="dlg-temp">' in html, "the temporary weather fieldset is missing"
    assert "<details" not in html, "the block must not be a disclosure the fields hide behind"
    # the three modes are the control that reveals the blocks, drawn as native radios
    assert f'name="{actions_page.WEATHER_MODE_FIELD}"' in html
    assert html.count('type="radio"') >= 3
    assert "body:not(" not in html, "a CSS gate on a <select multiple> can never match"
    assert "/static/dialog-preset.js" in html, "the gate's asset is not loaded"
    assert 'name="announce"' not in dialog_form(html), "the announce box must be gone"
    assert "Tell the players the weather changed" not in html
    # the launch form's extensions box is drawn too: with it off a pick is refused, so the input
    # the refusal reads must be on the form
    assert f'name="{actions_page.MODIFY_MISSION_FIELD}"' in dialog_form(html), \
        "the extensions checkbox is missing"
    assert f'id="{actions_page.MODIFY_MISSION_FIELD}-dialog"' in html, \
        "the extensions box has no label target"


# ================================================== 2. the stale-page guard survives the dialog

@pytest.mark.asyncio
async def test_a_stale_post_through_the_dialog_is_refused(config_dir, clean_registry):
    """The row's LOGICAL name travels through the dialog; a list that changed since the render is
    refused and NOTHING loads."""
    cluster = load_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http, mission="2", mission_name="2=Rotated.miz")
        # the hidden fields carry BOTH the position and the logical name the row posted
        assert 'name="mission" value="2"' in dialog.text
        assert 'name="mission_name" value="2=Rotated.miz"' in dialog.text
        # now post a name that does NOT match what index 2 resolves to -> stale
        reply = await post_dialog(http, dialog, mission="2", mission_name="2=Training.miz",
                                  modify_mission="1")
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert MISSION_CHANGED in after
    assert cluster.servers[0].loads == [], "a stale row loads nothing"
    assert cluster.servers[0].restarts == [], "a stale row must not restart either"


# ================================================== 3. the choice is forwarded — including the trap

@pytest.mark.asyncio
async def test_a_name_picked_on_load_reaches_loadMission(config_dir, clean_registry):
    """A preset picked in the file mode reaches ``server.loadMission`` as its DOCUMENT plus its NAME."""
    cluster = load_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)
        reply = await post_dialog(http, dialog, preset="Summer", modify_mission="1",
                                  weather_mode=actions_page.WEATHER_MODE_FILE)
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.loads, "nothing reached loadMission"
    _mission, _modify, preset, preset_name, announce = server.loads[0]
    assert preset == {"modify": {"weather": {"clouds": {"base": 1000}}}}, server.loads
    assert preset_name == "Summer", server.loads
    assert announce is False


@pytest.mark.asyncio
async def test_loading_the_current_mission_with_a_preset_applies_it(config_dir, clean_registry):
    """THE TRAP: the running mission is loaded again, so the action takes the RESTART branch — and
    the weather pick must be forwarded there too, never silently dropped.

    RED on the pre-fix behaviour: ``restart`` was called without a preset, so the chosen weather was
    lost while the message claimed a load.
    """
    # the running mission IS the row we load: current == CONFIGURED[1] (Rotated, row 2)
    cluster = load_fleet(current=os.path.join(BASE_DIR, "Rotated.miz"))
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http, mission="2", mission_name="2=Rotated.miz")
        reply = await post_dialog(http, dialog, preset="Summer", announce="1", modify_mission="1",
                                  weather_mode=actions_page.WEATHER_MODE_FILE)
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.loads == [], "the current mission must take the RESTART branch, not a plain load"
    assert server.restarts, "the restart branch was not taken"
    assert server.restarts[0]["preset"] == {"modify": {"weather": {"clouds": {"base": 1000}}}}, \
        server.restarts
    assert server.restarts[0]["preset_name"] == "Summer", \
        "the restart branch must carry the name too"
    # the announce box is gone from the dialog: a posted value it no longer declares reaches nothing
    assert server.restarts[0]["announce"] is False, server.restarts


@pytest.mark.asyncio
async def test_every_ticked_name_on_load_reaches_loadMission_as_one_string(config_dir,
                                                                          clean_registry):
    """MULTI PICK on Load: every ticked box posts its own value, and the action reads the comma list
    the node merges into the mission in the order the entries are listed."""
    cluster = load_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)
        body = {"server": SERVER_A, **dialog_hidden(dialog.text), "modify_mission": "1",
                "weather_mode": actions_page.WEATHER_MODE_FILE,
                "preset": ["Summer", "Summer"]}
        reply = await http.post(LOAD, data=body)
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.loads, "nothing reached loadMission"
    assert server.loads[0][3] == "Summer,Summer", server.loads


@pytest.mark.asyncio
async def test_a_rotation_picked_for_the_running_mission_reaches_restart(config_dir, clean_registry):
    """THE TRAP, with SEVERAL names: the already-running mission takes the RESTART branch, and the
    whole rotation must be forwarded there too — a copy of the pick wired into one branch only would
    drop it silently, and the message would still claim the weather was applied."""
    cluster = load_fleet(current=os.path.join(BASE_DIR, "Rotated.miz"))
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http, mission="2", mission_name="2=Rotated.miz")
        body = {"server": SERVER_A, **dialog_hidden(dialog.text), "modify_mission": "1",
                "weather_mode": actions_page.WEATHER_MODE_FILE,
                "preset": ["Summer", "Summer"]}
        reply = await http.post(LOAD, data=body)
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.loads == [], "the current mission must take the RESTART branch"
    assert server.restarts, "the restart branch was not taken"
    assert server.restarts[0]["preset_name"] == "Summer,Summer", server.restarts
    assert len(server.restarts[0]["preset"]) == 2, server.restarts


@pytest.mark.asyncio
async def test_serverimpl_restart_forwards_the_weather_pick():
    """``ServerImpl.restart`` threads ``preset`` / ``announce`` into its ``loadMission`` — RED
    before the fix, when the restart path dropped both."""
    sink = _RestartSink()
    await ServerImpl.restart(sink, modify_mission=True, preset={"modify": {}}, announce=True)
    assert sink.calls == [(host_path(os.path.join(BASE_DIR, "Rotated.miz")), True, True, {"modify": {}}, None,
                           True)], sink.calls


@pytest.mark.asyncio
async def test_restart_without_a_choice_keeps_todays_behaviour():
    """Every other restart caller passes nothing: ``preset=None`` / ``announce=False`` reach
    ``loadMission`` unchanged."""
    sink = _RestartSink()
    await ServerImpl.restart(sink)
    assert sink.calls == [(host_path(os.path.join(BASE_DIR, "Rotated.miz")), True, True, None, None, False)], \
        sink.calls


@pytest.mark.asyncio
async def test_the_restart_rpc_carries_the_choice_on_the_wire():
    """A restart with a pick sends it; a restart without one is byte-identical to the old RPC."""
    bus = _Bus()
    proxy = types.SimpleNamespace(name="SRS-1",
                                  node=types.SimpleNamespace(slow_system=False, name="node-1"),
                                  bus=bus)
    await ServerProxy.restart(proxy, preset="Summer", announce=True)   # type: ignore[arg-type]
    await ServerProxy.restart(proxy)                                   # type: ignore[arg-type]
    await ServerProxy.restart(proxy, preset={"modify": {}},            # type: ignore[arg-type]
                              preset_name="Winter")
    assert bus.calls[0]["params"] == {"modify_mission": True, "use_orig": True,
                                      "preset": "Summer", "announce": True}
    assert bus.calls[1]["params"] == {"modify_mission": True, "use_orig": True}
    assert bus.calls[2]["params"] == {"modify_mission": True, "use_orig": True,
                                      "preset": {"modify": {}}, "preset_name": "Winter"}


@pytest.mark.asyncio
async def test_the_load_rpc_carries_the_document_and_its_name():
    """``loadMission`` on the wire: the document travels as ``preset`` and the name beside it, and a
    call that states no name is byte-identical to the pre-change RPC."""
    bus = _Bus()
    proxy = types.SimpleNamespace(name="SRS-1",
                                  node=types.SimpleNamespace(slow_system=False, name="node-1"),
                                  bus=bus)
    await ServerProxy.loadMission(proxy, "Rotated.miz", preset={"modify": {}},      # type: ignore[arg-type]
                                  preset_name="Winter")
    await ServerProxy.loadMission(proxy, "Rotated.miz")                             # type: ignore[arg-type]
    assert bus.calls[0]["params"] == {"mission": "Rotated.miz", "modify_mission": True,
                                      "use_orig": True, "no_reload": False,
                                      "preset": {"modify": {}}, "preset_name": "Winter"}
    assert bus.calls[1]["params"] == {"mission": "Rotated.miz", "modify_mission": True,
                                      "use_orig": True, "no_reload": False}


# ================================================== 4. no pick = today's behaviour; JS-off works

@pytest.mark.asyncio
async def test_a_load_with_no_preset_behaves_exactly_as_today(config_dir, clean_registry):
    """No preset, no temporary fields, no announce: the action gets today's arguments and the
    message is byte-for-byte the sentence it always returned."""
    cluster = load_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)
        reply = await post_dialog(http, dialog, modify_mission="1")
        after = await missions_page(http)
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.loads and server.loads[0][2] is None, server.loads
    assert server.loads[0][4] is False, server.loads
    # the page HTML-escapes the apostrophes, so compare the UNESCAPED render
    assert "Mission 'Rotated' loaded." in html.unescape(after), after[:400]


@pytest.mark.asyncio
async def test_a_temporary_preset_reaches_loadMission_as_a_document(config_dir, clean_registry):
    """The temporary weather fields build a DOCUMENT (the wizard's own builder) that rides the SAME
    ``preset`` parameter — proving the two shapes of the one choice meet one reader."""
    cluster = load_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)
        # fill ONE weather row: the first key of the shared field table
        key = actions_page.TEMPORARY_KEYS[0]
        reply = await post_dialog(http, dialog, **{key: "777"}, modify_mission="1",
                                  weather_mode=actions_page.WEATHER_MODE_CUSTOM)
    assert reply.status_code == 303, (reply.status_code, reply.text)
    server = cluster.servers[0]
    assert server.loads, "nothing reached loadMission"
    preset = server.loads[0][2]
    assert isinstance(preset, dict) and preset, preset
    assert server.loads[0][3] is None, "a one-off preset has no name"
    assert "777" in str(preset), preset


@pytest.mark.asyncio
async def test_a_dialog_stating_a_name_and_the_fields_is_refused(config_dir, clean_registry):
    """Both shapes of the ONE choice at once is refused (the console's own sentence), nothing loads."""
    cluster = load_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)
        key = actions_page.TEMPORARY_KEYS[0]
        # a caller with NO dialog states both shapes at once: no mode field at all
        body = {"server": SERVER_A, **dialog_hidden(dialog.text), "preset": "Summer", key: "777"}
        body.pop("weather_mode", None)
        reply = await http.post(LOAD, data=body)
        assert reply.status_code == 303, (reply.status_code, reply.text)
        after = await missions_page(http)
    assert cluster.servers[0].loads == [], "a conflicting POST must load nothing"
    assert actions_page.REFUSAL_WEATHER_CONFLICT in after, after[:400]


@pytest.mark.asyncio
async def test_the_load_copy_and_the_launch_form_render_in_german(config_dir, clean_registry):
    """The new sentences are wrapped and reach the catalogs: with German chosen the dialog's heading,
    detail, primary button and the shared form's copy are German, while a preset NAME stays operator
    data (untranslated)."""
    from services.webservice import i18n

    app = build(config_dir, load_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        http.cookies.set(i18n.COOKIE_NAME, "de")
        reply = await http.post(LOAD_DIALOG, data={"server": SERVER_A, "_csrf_token": token_of(page),
                                                   "mission": "2", "mission_name": "2=Rotated.miz"})
    assert reply.status_code == 200, reply.text
    assert "Rotated.miz auf SRS-1 laden?" in reply.text, "the heading is not German"
    assert "Die Mission wird jetzt auf SRS-1 geladen." in reply.text, "the detail is not German"
    assert "Mission laden" in reply.text, "the primary button is not German"
    assert "Wetter-Preset" in reply.text, "the shared select's label is not German"
    assert "Oder ein temporäres Preset anlegen" in reply.text, "the fieldset is not German"
    assert 'value="Summer"' in reply.text, "a preset name is operator data and stays as it is"


@pytest.mark.asyncio
async def test_the_load_dialog_is_a_real_form_a_no_javascript_browser_can_submit(
        config_dir, clean_registry):
    """Progressive enhancement: the SERVER renders the form, the select is enabled, and the dialog's
    own form posts to the LOAD action — a browser with JavaScript off can submit it."""
    app = build(config_dir, load_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)

    html = dialog.text
    form = re.search(rf'<form id="dlg-form"[^>]*action="{re.escape(LOAD)}"[^>]*>', html)
    assert form, "the dialog is not a real form posting to the load action"
    assert "disabled" not in form.group(0)
    assert 'type="submit"' in html, "no submit button for a no-JS browser"
    hidden = dialog_hidden(html)
    assert hidden.get("mission") == "2" and hidden.get("mission_name") == "2=Rotated.miz"
