"""The launch dialogs' weather is ONE three-way MODE: leave it alone / a preset out of a file / custom.

Both launch dialogs (the Servers row's Startup and the Missions tab's Load) draw the SAME control set:
the mode radios first (``leave`` selected, so a launch nobody touched behaves as before), then — while
the mode is ``file`` — the preset FILE pick (only for a node carrying more than one preset file) and
the preset NAME pick, then the temporary weather fieldset for ``custom``.

One section per acceptance criterion:

1. THE RENDERED DIALOG — three exclusive rows, ``leave`` first and checked, the file pick only for a
   node carrying more than one preset file, the pick carrying preset NAMES and nothing else, and no
   "Tell the players the weather changed" box on either dialog;
2. THE POST — a ``file`` mode resolves the picked names into the DOCUMENT they stand for, passes it as
   ``preset`` and the names as ``preset_name``, a COMBINATION resolves across the node's files, and
   ``custom`` posts the temporary document with no name;
3. THE REFUSALS — a file this node does not list (including every path shape), a preset that vanished
   between render and POST, a ``file`` mode with no preset ticked, and the direct caller's
   document-plus-name conflict;
4. THE WIRE — the two actions still accept ``announce`` (the box is gone, the parameter is not).

The preset files are the node's OWN, read through the real ``NodeImpl`` methods bound to the double, so
the console's actual read path runs.
"""
from __future__ import annotations

import re

import pytest

from core import Coalition, Status
from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page
from test_webui_server_message import (  # noqa: F401 - fixtures imported on purpose
    confirm_of, message_fleet, open_dialog as open_message_dialog,
    open_page as open_servers_page, send as send_message,
)
from test_webui_missions_load_form import (  # noqa: F401 - fixtures imported on purpose
    LoadServer, load_fleet, open_load_dialog,
)
from test_webui_startup_weather_picker import (  # noqa: F401 - fixtures imported on purpose
    CONFIRM, PRESETS_YAML, STARTUP, EXTRA_YAML, PresetNode, node_fleet, open_dialog, open_page,
    pick_block, pick_values,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

MODE_FIELD = actions_page.WEATHER_MODE_FIELD
FILE_FIELD = actions_page.PRESET_FILE_FIELD
PRESET_FIELD = actions_page.PRESET_FIELD
FLAT = "config/presets.yaml"
EXTRA = "config/presets/extra.yaml"

#: the flat file: two presets and ONE combination whose members live in it.
COMBO_YAML = """\
Summer:
  modify:
    weather:
      clouds:
        base: 1000
Mixed:
  - Summer
  - Winter
"""


def mode_block(html: str) -> str:
    """The rendered ``<fieldset class="dlg-field dlg-radio">…</fieldset>`` block."""
    match = re.search(r'<fieldset class="dlg-field dlg-radio".*?</fieldset>', html, re.S)
    assert match, "the dialog rendered no weather mode control"
    return match.group(0)


def radios(block: str) -> list[str]:
    """Every mode row's value, in the order the dialog draws them."""
    return re.findall(r'<input type="radio"[^>]*name="weather_mode"[^>]*value="([^"]*)"', block)


def file_block(html: str) -> str | None:
    """The rendered preset-FILE select's own ``<p>`` wrapper, or ``None`` when it is not drawn."""
    match = re.search(r'<p class="dlg-field">\s*<label for="preset_file-dialog">.*?</p>', html, re.S)
    return match.group(0) if match else None


def dialog_form(html: str) -> str:
    """The dialog's OWN ``<form id="dlg-form">`` block."""
    match = re.search(r'<form id="dlg-form".*?</form>', html, re.S)
    assert match, "the dialog rendered no form"
    return match.group(0)


def form_fields(html: str) -> dict:
    """The dialog form's hidden fields — the CSRF token and the target."""
    hidden: dict = {}
    for name, value in re.findall(r'name="([^"]+)" value="([^"]*)"', dialog_form(html)):
        hidden.setdefault(name, value)
    return hidden


def two_files(config_dir):
    """The node's TWO preset files: the flat one and one under ``presets/``."""
    (config_dir / "presets.yaml").write_text(COMBO_YAML, encoding="utf-8")
    (config_dir / "presets").mkdir(exist_ok=True)
    (config_dir / "presets" / "extra.yaml").write_text(EXTRA_YAML, encoding="utf-8")


# --------------------------------------------------------------- 1. the rendered dialog

@pytest.mark.asyncio
async def test_the_startup_dialog_draws_the_three_modes_with_leave_selected(config_dir,
                                                                           clean_registry):
    """THREE exclusive radio rows under their own field, ``leave`` FIRST and CHECKED, each with the
    hint the drawing asks for; the pick carries the node's preset NAMES only; and the announce box is
    gone from the form."""
    two_files(config_dir)
    app = build(config_dir, node_fleet(PresetNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = mode_block(dialog.text)
    assert radios(block) == [actions_page.WEATHER_MODE_LEAVE, actions_page.WEATHER_MODE_FILE,
                             actions_page.WEATHER_MODE_CUSTOM], block
    assert 'value="leave"' in block and "checked" in block.split('value="leave"')[1][:200], \
        "the leave row must be the pre-selected default"
    assert block.count('name="weather_mode"') == 3, "the rows are not one exclusive field"
    assert block.count('type="radio"') == 3, "the rows are not native radios"
    assert 'class="dlg-field opt"' in block, "the rows do not wear the console's option grammar"
    for word in ("Leave the weather alone", "Use a preset from a file", "Create your own weather"):
        assert word in block, f"the mode row {word!r} is missing"
    assert "The mission loads as its file stands." in block
    assert "Pick the preset file, then the preset inside it." in block
    assert "Build a one-off preset in the fields below." in block
    assert "Weather preset" in dialog.text, "the mode control lost its legend"
    # the pick is the node's names, and NOTHING else: the console's two entries became modes
    values = pick_values(pick_block(dialog.text))
    assert actions_page.PRESET_CUSTOM_VALUE not in values, "the custom entry must not be a list entry"
    assert "" not in values, "leave the weather alone must not be a list entry"
    assert set(values) == {"Summer", "Mixed", "Winter"}, values
    # no announce box, anywhere on the dialog
    assert 'name="announce"' not in dialog.text
    assert "Tell the players the weather changed" not in dialog.text


@pytest.mark.asyncio
async def test_the_file_pick_is_drawn_only_when_the_node_carries_more_than_one_file(config_dir,
                                                                                   clean_registry):
    """ONE preset file: no file pick (there is nothing to choose). TWO: the pick lists both labels."""
    (config_dir / "presets.yaml").write_text(COMBO_YAML, encoding="utf-8")
    app = build(config_dir, node_fleet(PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        one = await open_dialog(http)

    two_files(config_dir)
    app = build(config_dir, node_fleet(PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        two = await open_dialog(http)

    assert file_block(one.text) is None, "a single preset file must not draw a file filter"
    block = file_block(two.text)
    assert block is not None, "two preset files must draw the file filter"
    assert f'name="{FILE_FIELD}"' in block
    assert f'value="{FLAT}"' in block and f'value="{EXTRA}"' in block
    assert f'value="{actions_page.PRESET_FILE_ALL}"' in block and "All files" in block, \
        "the filter's default entry is missing"
    assert "Show" in block
    # the presets stay grouped per file, with every group rendered (JavaScript narrows them)
    assert '<optgroup label="config/presets.yaml">' in two.text
    assert '<optgroup label="config/presets/extra.yaml">' in two.text


#: the SAME name in both preset files with different bodies — each group's entry is its own file's.
SHARED_FLAT_YAML = """\
Storm:
  modify:
    weather:
      clouds:
        base: 900
"""

SHARED_EXTRA_YAML = """\
Storm:
  modify:
    weather:
      clouds:
        base: 100
"""


@pytest.mark.asyncio
async def test_a_name_two_files_define_is_drawn_under_both_and_resolves_per_file(config_dir,
                                                                               clean_registry):
    """A shared name is offered by BOTH groups — it must stay pickable from the file that defines it —
    and the FILE the operator names decides the body the launch applies."""
    (config_dir / "presets.yaml").write_text(SHARED_FLAT_YAML, encoding="utf-8")
    (config_dir / "presets").mkdir(exist_ok=True)
    (config_dir / "presets" / "extra.yaml").write_text(SHARED_EXTRA_YAML, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        block = pick_block(dialog.text)
        assert re.findall(r'<optgroup label="([^"]*)">', block) == [FLAT, EXTRA], block
        assert block.count(">Storm</option>") == 2, \
            "a name both files define was offered under one of them only"
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(dialog.text),
                                               MODE_FIELD: actions_page.WEATHER_MODE_FILE,
                                               FILE_FIELD: EXTRA,
                                               PRESET_FIELD: "Storm"})

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == {"modify": {"weather": {"clouds": {"base": 100}}}}, \
        "the name picked from the second file applied the first file's body"


@pytest.mark.asyncio
async def test_a_node_whose_presets_cannot_be_read_offers_two_modes(config_dir, clean_registry):
    """The read fails: the file mode is WITHHELD (it could not be resolved at POST time) and the pick
    says why — the launch itself is never refused."""
    (config_dir / "presets.yaml").write_text(COMBO_YAML, encoding="utf-8")
    node = PresetNode(config_dir, read_error=PermissionError("the node said no"))
    app = build(config_dir, node_fleet(node))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    assert dialog.status_code == 200
    assert radios(mode_block(dialog.text)) == [actions_page.WEATHER_MODE_LEAVE,
                                               actions_page.WEATHER_MODE_CUSTOM]
    assert "preset files could not be read" in dialog.text


# --------------------------------------------------------------------- 2. the POST

@pytest.mark.asyncio
async def test_the_posted_file_mode_resolves_to_a_document_and_a_name(config_dir, clean_registry):
    """A name picked in mode 2 reaches ``Server.startup`` as the DOCUMENT it stands for, with the NAME
    beside it under ``preset_name``."""
    (config_dir / "presets.yaml").write_text(COMBO_YAML, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(dialog.text),
                                               MODE_FIELD: actions_page.WEATHER_MODE_FILE,
                                               PRESET_FIELD: "Summer"})

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == {"modify": {"weather": {"clouds": {"base": 1000}}}}, call
    assert call["preset_name"] == "Summer", call
    assert call["modify_mission"] is True
    assert "announce" not in call, "the dialog must not state announce any more"


@pytest.mark.asyncio
async def test_several_names_post_their_documents_in_order_with_their_joined_names(config_dir,
                                                                                   clean_registry):
    """Several names ticked from ONE file: the document list follows the pick's order and
    ``preset_name`` is their comma list — what the server page names."""
    (config_dir / "presets.yaml").write_text(COMBO_YAML, encoding="utf-8")
    (config_dir / "presets").mkdir(exist_ok=True)
    (config_dir / "presets" / "extra.yaml").write_text(EXTRA_YAML, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(dialog.text),
                                               MODE_FIELD: actions_page.WEATHER_MODE_FILE,
                                               FILE_FIELD: FLAT,
                                               PRESET_FIELD: ["Summer", "Mixed"]})

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == [{"modify": {"weather": {"clouds": {"base": 1000}}}},
                              {"modify": {"weather": {"clouds": {"base": 1000}}}},
                              {"modify": {"weather": {"clouds": {"base": 500}}}}], call
    assert call["preset_name"] == "Summer,Mixed", call


@pytest.mark.asyncio
async def test_a_combination_resolves_across_the_node_s_files(config_dir, clean_registry):
    """A COMBINATION names members that may live in ANOTHER file: the node resolves it against its own
    merged map, so the console must too — a member must never vanish."""
    two_files(config_dir)      # presets.yaml: Summer, Mixed -> [Summer, Winter]; extra: Winter
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(dialog.text),
                                               MODE_FIELD: actions_page.WEATHER_MODE_FILE,
                                               FILE_FIELD: FLAT,
                                               PRESET_FIELD: "Mixed"})

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == [{"modify": {"weather": {"clouds": {"base": 1000}}}},
                              {"modify": {"weather": {"clouds": {"base": 500}}}}], call
    assert call["preset_name"] == "Mixed", call


@pytest.mark.asyncio
async def test_the_custom_mode_posts_the_temporary_document_and_no_name(config_dir, clean_registry):
    """Mode 3 is today's temporary shape: the weather fields' document, and no ``preset_name``."""
    (config_dir / "presets.yaml").write_text(COMBO_YAML, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        body = {"server": "SRS-1", "_csrf_token": token_of(dialog.text),
                MODE_FIELD: actions_page.WEATHER_MODE_CUSTOM,
                "clouds": "dynamic", "clouds.base": "250"}
        reply = await http.post(STARTUP, data=body)

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"]["clouds"]["base"] == 250, call
    assert "preset_name" not in call, "a built-on-the-spot preset has no name"


@pytest.mark.asyncio
async def test_the_leave_mode_ignores_a_pick_the_form_still_carries(config_dir, clean_registry):
    """The default mode states NOTHING: a pick left in the form (JavaScript off, a crafted POST) is
    ignored, so an untouched launch behaves exactly as one from before the choice existed."""
    (config_dir / "presets.yaml").write_text(COMBO_YAML, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        body = {"server": "SRS-1", "_csrf_token": token_of(dialog.text),
                MODE_FIELD: actions_page.WEATHER_MODE_LEAVE, PRESET_FIELD: "Summer",
                "clouds.base": "250"}
        reply = await http.post(STARTUP, data=body)

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call == {"modify_mission": True, "use_orig": True, "preset": None}, call


@pytest.mark.asyncio
async def test_the_load_dialog_draws_the_same_modes_and_resolves_its_pick(config_dir,
                                                                         clean_registry):
    """The Missions tab's Load dialog draws the same control set and its mode-2 post reaches
    ``loadMission`` as a document plus a name."""
    cluster = load_fleet()
    app = build(config_dir, cluster, plugins=("mission",))

    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)
        assert radios(mode_block(dialog.text)) == [actions_page.WEATHER_MODE_LEAVE,
                                                   actions_page.WEATHER_MODE_FILE,
                                                   actions_page.WEATHER_MODE_CUSTOM]
        assert 'name="announce"' not in dialog.text
        assert "Tell the players the weather changed" not in dialog.text
        body = {"server": "SRS-A", **form_fields(dialog.text), "modify_mission": "1",
                MODE_FIELD: actions_page.WEATHER_MODE_FILE, PRESET_FIELD: "Summer"}
        reply = await http.post(actions_page.MISSIONS_LOAD_PATH, data=body)

    assert reply.status_code == 303, reply.text
    server = cluster.servers[0]
    assert server.loads, "nothing reached loadMission"
    mission, _modify, preset, preset_name, announce = server.loads[0]
    assert preset == {"modify": {"weather": {"clouds": {"base": 1000}}}}, server.loads
    assert preset_name == "Summer", server.loads
    assert announce is False, "the website never states announce"


# ------------------------------------------------------------------- 3. the refusals

@pytest.mark.parametrize("value", [
    "../../etc/presets.yaml",
    "config/../../presets.yaml",
    "/etc/passwd",
    "C:\\presets.yaml",
    "\\\\host\\share\\presets.yaml",
    "presets.yaml",
    "config/presets_missing.yaml",
])
@pytest.mark.asyncio
async def test_a_file_value_that_is_not_a_listed_label_is_refused(config_dir, clean_registry, value):
    """Every shape a hostile file value can take meets ONE sentence, and NOTHING launches."""
    two_files(config_dir)
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        body = {"server": "SRS-1", "_csrf_token": token_of(dialog.text),
                MODE_FIELD: actions_page.WEATHER_MODE_FILE, FILE_FIELD: value,
                PRESET_FIELD: "Summer"}
        reply = await http.post(STARTUP, data=body)

    assert reply.status_code == 400, reply.text
    assert cluster.servers[0].call_args == [], "a refused pick must not launch"
    assert actions_page.REFUSAL_WEATHER_UNKNOWN_FILE in reply.text


@pytest.mark.asyncio
async def test_a_preset_that_vanished_between_render_and_post_is_refused(config_dir,
                                                                        clean_registry):
    """The dialog listed ``Summer``; the file was rewritten before the POST. The launch is REFUSED
    rather than launched applying nothing."""
    (config_dir / "presets.yaml").write_text(COMBO_YAML, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        (config_dir / "presets.yaml").write_text(
            "Other:\n  modify:\n    weather:\n      clouds:\n        base: 100\n", encoding="utf-8")
        body = {"server": "SRS-1", "_csrf_token": token_of(dialog.text),
                MODE_FIELD: actions_page.WEATHER_MODE_FILE, PRESET_FIELD: "Summer"}
        reply = await http.post(STARTUP, data=body)

    assert reply.status_code == 400, reply.text
    assert cluster.servers[0].call_args == [], "a vanished preset must not launch"
    assert actions_page.REFUSAL_WEATHER_UNKNOWN_PRESET in reply.text


@pytest.mark.asyncio
async def test_the_file_mode_with_no_preset_ticked_is_refused(config_dir, clean_registry):
    """Mode 2 states an intent the POST does not carry: refused, in its own sentence."""
    (config_dir / "presets.yaml").write_text(COMBO_YAML, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(dialog.text),
                                               MODE_FIELD: actions_page.WEATHER_MODE_FILE})

    assert reply.status_code == 400, reply.text
    assert cluster.servers[0].call_args == []
    assert "was chosen and no preset was ticked" in reply.text


@pytest.mark.asyncio
async def test_a_document_and_a_name_together_are_still_refused(config_dir, clean_registry):
    """THE BACKSTOP: a caller with no dialog states the two shapes of the ONE field, and both
    together is refused — the UI no longer produces it, the predicate is not dead."""
    (config_dir / "presets.yaml").write_text(COMBO_YAML, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        body = {"server": "SRS-1", "_csrf_token": token_of(dialog.text),
                PRESET_FIELD: ["Summer", actions_page.PRESET_CUSTOM_VALUE],
                "clouds.base": "250"}
        reply = await http.post(STARTUP, data=body)

    assert reply.status_code == 400, reply.text
    assert cluster.servers[0].call_args == []
    assert actions_page.REFUSAL_WEATHER_CONFLICT in reply.text


# ---------------------------------------------------------------------- 4. the wire

def test_both_actions_still_accept_announce_defaulting_to_false():
    """The announce parameter is untouched on the actions: only the DIALOG stopped offering it."""
    import inspect

    from plugins.mission.actions import load_mission, startup_server

    for action in (startup_server, load_mission):
        params = inspect.signature(action).parameters
        assert "announce" in params and params["announce"].default is False, action.__name__
        assert "preset_name" in params and params["preset_name"].default is None, action.__name__


# ---------------------------------------- 5. the coalition writes declare no weather pick

@pytest.mark.asyncio
async def test_the_weather_reader_answers_nothing_for_a_dialog_without_a_pick():
    """A select-bearing write that declares NO weather pick (`chat`, `popup`) reads no weather.

    The reader's guard is the declaration's ``weather`` flag, so a select-bearing dialog added later
    cannot silently inherit mode parsing through its coalition select.
    """
    declarations = [action for action in actions_page.SERVER_ACTIONS
                    if action.key in ("chat", "popup")]
    assert declarations, "the server message pair is not declared"

    for action in declarations:
        assert action.select is not None, action.key
        assert await actions_page.launch_weather_params(
            {actions_page.WEATHER_MODE_FIELD: actions_page.WEATHER_MODE_FILE,
             PRESET_FIELD: "Summer"}, action) == {}, \
            f"{action.key}: the weather reader parsed a dialog that declares no weather pick"


@pytest.mark.asyncio
async def test_the_two_coalition_writes_post_exactly_their_own_parameters(config_dir,
                                                                        clean_registry):
    """``chat`` and ``popup`` reach the domain with their own parameters and nothing else — the
    weather reader contributes no parameter to either."""
    cluster = message_fleet()
    app = build(config_dir, cluster)
    a = cluster.servers[0]

    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_servers_page(http, servers_page.SERVERS_PATH))
        chat = await send_message(http, actions_page.SERVER_CHAT_PATH, token, "Look alive.",
                                  coalition="all")
        popup = await send_message(http, actions_page.SERVER_POPUP_PATH, token, "Hi.",
                                   coalition="red")

    assert chat.status_code == 303, chat.text
    assert popup.status_code == 303, popup.text
    assert a.calls == [("chat", Coalition.ALL, "Look alive.", "mod1"),
                       ("popup", Coalition.RED, "Hi.", -1, "mod1")], a.calls


@pytest.mark.asyncio
async def test_a_coalition_write_carrying_weather_fields_still_sends_its_message(config_dir,
                                                                                clean_registry):
    """A crafted body stating a weather mode and a pick beside the coalition select changes nothing:
    the write is the one the control declares and the message arrives."""
    cluster = message_fleet()
    app = build(config_dir, cluster)
    a = cluster.servers[0]

    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_servers_page(http, servers_page.SERVERS_PATH))
        dialog = await open_message_dialog(http, actions_page.SERVER_CHAT_PATH, token)
        reply = await http.post(actions_page.SERVER_CHAT_PATH,
                                data={"server": "SRS-1", "message": "Look alive.",
                                      "coalition": "all", "_csrf_token": token,
                                      "_confirm_token": confirm_of(dialog),
                                      MODE_FIELD: actions_page.WEATHER_MODE_FILE,
                                      PRESET_FIELD: "Summer"})

    assert reply.status_code == 303, reply.text
    assert a.calls == [("chat", Coalition.ALL, "Look alive.", "mod1")], a.calls
