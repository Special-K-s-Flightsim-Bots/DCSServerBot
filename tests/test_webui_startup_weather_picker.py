"""The launch dialog's named-preset picker: the Startup control gains a weather select.

One section per acceptance criterion: the RENDERED dialog (the empty option FIRST plus every named
preset the server's own node carries, drawn as an ENABLED ``<select>`` — the server renders it, no
script enables it); the FORWARDING (a picked name reaches ``Server.startup`` as ``preset=<name>``,
the empty option as ``None``); the MESSAGE (the audit text names the preset when one was picked and
is byte-for-byte today's sentence when none was); and the FALLBACK (a node whose preset files cannot
be read does not break the dialog — it falls back to a free-text field).

The preset list is read from the node the console RESOLVED, through the node's own ``list_files`` /
``read_file``. The double binds the REAL ``NodeImpl`` methods for those two, so the test drives the
console's actual node-read path rather than a re-implementation of it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from core import Status
from core.data.impl.nodeimpl import NodeImpl
from services.webservice.pages import actions as actions_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, recording_bot,
    sign_in, token_of,
)
from test_webui_write_server_actions import FakeServer

STARTUP = "/actions/server/startup"
CONFIRM = actions_page.CONFIRM_SUFFIX
PRESET_FIELD = actions_page.PRESET_FIELD

#: two named presets in the flat file, one explicitly hidden, and one in the ``presets/`` directory.
PRESETS_YAML = """\
Summer:
  modify:
    weather:
      clouds:
        base: 1000
Hidden:
  hidden: true
  modify:
    weather:
      clouds:
        base: 100
"""
EXTRA_YAML = """\
Winter:
  modify:
    weather:
      clouds:
        base: 500
"""


class PresetNode:
    """A node double whose preset files live under *config_dir* on THIS machine.

    ``list_files`` and ``read_file`` are the REAL ``NodeImpl`` methods bound to this object — they
    touch nothing else on ``self`` (``read_file`` reads ``self.node.master``, which is ``self`` here,
    exactly as ``NodeImpl.__init__`` sets it) — so the console's own node-read path is what runs.
    ``list_error`` / ``read_error`` make a read FAIL, which is the fallback's own case.
    """

    def __init__(self, config_dir, *, name="dcs-a-01", list_error=None, read_error=None):
        self.name = name
        self.master = True
        self.node = self
        self.config_dir = str(config_dir)
        self._list_error = list_error
        self._read_error = read_error

    async def list_files(self, path, *, pattern="*"):
        if self._list_error is not None:
            raise self._list_error
        return await NodeImpl.list_files(self, path, pattern=pattern)  # type: ignore[arg-type]

    async def read_file(self, path):
        if self._read_error is not None:
            raise self._read_error
        return await NodeImpl.read_file(self, path)  # type: ignore[arg-type]


def node_fleet(node, *, status=Status.SHUTDOWN):
    """ONE SHUTDOWN server, ``SRS-1`` (Host A), whose node is *node*."""
    a = FakeServer("SRS-1", status=status, managed_by=["Host A"])
    a.node = node
    node_a = types.SimpleNamespace(name=node.name, master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a], nodes={node.name: node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a)])


async def open_dialog(http, path=STARTUP, name="SRS-1"):
    """Open the Startup dialog (an OPTIONS form: no confirm token) and return its response."""
    page = await open_page(http)
    reply = await http.post(path + CONFIRM, data={"server": name, "_csrf_token": token_of(page)})
    assert reply.status_code == 200, f"{path}: {reply.status_code}\n{reply.text}"
    return reply


def pick_block(html: str) -> str:
    """The rendered ``<fieldset class="dlg-field dlg-pick">…</fieldset>`` block."""
    match = re.search(r'<fieldset class="dlg-field dlg-pick".*?</fieldset>', html, re.S)
    assert match, "the dialog rendered no weather pick"
    return match.group(0)


def pick_values(block: str) -> list[str]:
    """Every ENTRY's NAME, in the order the dialog draws them (the typed box, when the node's
    presets could not be listed, is a text field and addresses :data:`PRESET_FIELD` too, but it draws
    no ``<option>`` and is excluded here).

    A grouped option's value carries its own file and its name; only the name is what the pick
    SHOWS. Use the raw values where the file part matters (``raw_pick_values``).
    """
    return [split_pick_value(value) for value in raw_pick_values(block)]


def raw_pick_values(block: str) -> list[str]:
    """Every ENTRY's POST value, file prefix included, in the order the dialog draws them."""
    return re.findall(r'<option value="([^"]*)"', block)


def split_pick_value(value: str) -> str:
    """The NAME part of a pick entry's value — the whole value when it carries no file."""
    separator = actions_page.PRESET_PAIR_SEPARATOR
    return value.split(separator, 1)[1] if separator in value else value


def pick_options(block: str) -> list[str]:
    """Every rendered ``<option>`` of the pick, as its own tag."""
    return re.findall(r'<option\b[^>]*>', block)


CUSTOM = actions_page.PRESET_CUSTOM_VALUE

TESTS = Path(__file__).resolve().parent
PICK_HARNESS = TESTS / "dialog_pick_harness.js"
MODE_HARNESS = TESTS / "dialog_mode_harness.js"
#: an inline script: ``<script>`` with no ``src`` attribute, body captured.
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


# ----------------------------------------------------------- 1. the rendered dialog

@pytest.mark.asyncio
async def test_the_dialog_offers_every_named_preset_and_no_console_entry(config_dir,
                                                                        clean_registry):
    """The MULTI select lists the node's presets — and NOTHING else: "leave the weather alone" and
    "Create your own weather" are MODES above it, not entries inside it.

    The options are read from the preset files the NODE carries: ``presets.yaml`` beside its config
    dir and ``presets/extra.yaml`` under it, each under its own group heading. A preset marked
    ``hidden`` is not offered. No control carries ``disabled`` — an unscripted browser can select any
    of them.
    """
    (config_dir / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")
    (config_dir / "presets").mkdir()
    (config_dir / "presets" / "extra.yaml").write_text(EXTRA_YAML, encoding="utf-8")
    app = build(config_dir, node_fleet(PresetNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = pick_block(dialog.text)
    values = pick_values(block)
    assert set(values) == {"Summer", "Winter"}, values
    assert "Hidden" not in values, "a hidden preset is not offered"
    assert "disabled" not in block, "the pick must be drawn enabled server-side, not by a script"
    # ONE field name on the ONE multi select: the pick repeats its value per selected entry, so a
    # selection is a repeated POST value exactly as the boxes posted.
    assert "<select" in block and "multiple" in block, "the pick is not a multi select"
    assert block.count(f'name="{PRESET_FIELD}"') == 1, "the pick must carry the field ONE time"
    assert all("name=" not in option for option in pick_options(block))
    # the entries' own words: a preset NAME is operator data; the console's two choices are the
    # mode radios, drawn ABOVE the pick
    assert ">Summer</option>" in block
    assert ">Create your own weather</option>" not in block
    assert ">Leave the weather alone</option>" not in block
    mode = re.search(r'<fieldset class="dlg-field dlg-radio".*?</fieldset>', dialog.text, re.S)
    assert mode, "the weather mode control is missing"
    assert ">Leave the weather alone</label>" in mode.group(0)
    assert ">Create your own weather</label>" in mode.group(0)
    assert "Weather preset" in dialog.text, "the field's label is missing"
    assert "preset-dialog-help" in dialog.text, "the field's help is missing"


@pytest.mark.asyncio
async def test_a_name_two_files_carry_is_listed_under_both_headings(config_dir, clean_registry):
    """A name BOTH of the node's preset files define stays pickable from either one: the pick draws it
    once under EACH file's heading, and the FILE field is what tells the two entries apart."""
    (config_dir / "presets.yaml").write_text(
        "Winter:\n  modify:\n    weather:\n      clouds:\n        base: 1000\n", encoding="utf-8")
    (config_dir / "presets").mkdir()
    (config_dir / "presets" / "extra.yaml").write_text(
        "Winter:\n  modify:\n    weather:\n      clouds:\n        base: 500\n", encoding="utf-8")
    app = build(config_dir, node_fleet(PresetNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = pick_block(dialog.text)
    assert pick_values(block) == ["Winter", "Winter"], pick_values(block)
    assert re.findall(r'<optgroup label="([^"]*)">', block) == \
        ["config/presets.yaml", "config/presets/extra.yaml"], block


@pytest.mark.asyncio
async def test_a_node_with_no_presets_still_renders_the_picker(config_dir, clean_registry):
    """A node that carries no preset draws no names and no file pick — never a refusal."""
    node = types.SimpleNamespace(name="dcs-a-01")          # no config_dir, no list_files
    app = build(config_dir, node_fleet(node))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    assert pick_values(pick_block(dialog.text)) == []


# ----------------------------------------------------------------- 2. the forwarding

@pytest.mark.asyncio
async def test_a_picked_name_and_the_empty_entry_reach_server_startup(config_dir, clean_registry,
                                                                     recording_bot):
    """A picked name arrives as ``preset=<name>``; the empty entry arrives as ``None``."""
    (config_dir / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")

    picked_cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, picked_cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(dialog.text),
                                               PRESET_FIELD: "Summer"})
    assert reply.status_code == 303, reply.text
    assert picked_cluster.servers[0].call_args == [
        {"modify_mission": True, "use_orig": True, "preset": "Summer"}]

 # the EMPTY entry: a fresh fleet (the double flips the status on startup, so a second POST would
 # meet the state guard). The browser sends the empty entry's own value — "" — and it must reach
 # the action as today's ``None``.
    empty_cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, empty_cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(dialog.text),
                                               PRESET_FIELD: ""})
    assert reply.status_code == 303, reply.text
    assert empty_cluster.servers[0].call_args == [
        {"modify_mission": True, "use_orig": True, "preset": None}]


@pytest.mark.asyncio
async def test_every_ticked_name_posts_its_own_value_and_travels_as_one_string(config_dir,
                                                                              clean_registry,
                                                                              recording_bot):
    """MULTI PICK: two boxes ticked post TWO values under the ONE field, and the action reads the
    comma list the node merges into the mission in order.

    RED on the single-value read: the route kept only the LAST posted entry, so ``Summer`` was
    dropped and the rotation lost a preset with nothing said about it.
    """
    (config_dir / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(dialog.text),
                                               PRESET_FIELD: ["Summer", "Winter"]})
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].call_args == [
        {"modify_mission": True, "use_orig": True, "preset": "Summer,Winter"}]
    assert [call[0] for call in recording_bot.calls] == [
        "Server 'SRS-1' started up — weather preset 'Summer,Winter' requested."]


@pytest.mark.asyncio
async def test_the_empty_entry_beside_a_ticked_name_is_ignored(config_dir, clean_registry,
                                                               recording_bot):
    """The checked "leave the weather alone" entry posts its ``""`` — it is neither a name nor a
    choice, so a browser that leaves both ticked (JavaScript off) still launches the picked preset."""
    (config_dir / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(dialog.text),
                                               PRESET_FIELD: ["", "Winter"]})
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].call_args == [
        {"modify_mission": True, "use_orig": True, "preset": "Winter"}]


@pytest.mark.asyncio
async def test_the_comma_list_the_pick_posts_is_resolved_by_the_node(tmp_path):
    """THE WIRE — the ONE string the multi pick posts is the shape the NODE's own extension reads.

    ``MizEdit.get_presets`` splits a comma string into names and resolves EACH against the node's
    preset file (``extensions/mizedit/extension.py``), so several presets can be carried through the
    action's ONE ``preset`` parameter without widening it. Driven through the REAL extension code:
    only the instance is built bare, because ``__init__`` reaches a live node.
    """
    import logging

    from extensions.mizedit.extension import MizEdit

    presets = tmp_path / "presets.yaml"
    presets.write_text("Summer:\n  modify:\n    weather:\n      clouds:\n        base: 1000\n"
                       "Winter:\n  modify:\n    weather:\n      clouds:\n        base: 500\n",
                       encoding="utf-8")
    extension = MizEdit.__new__(MizEdit)
    extension.log = logging.getLogger("tests.mizedit.comma")
    extension.presets = {"Summer": {"modify": {"weather": {"clouds": {"base": 1000}}}},
                         "Winter": {"modify": {"weather": {"clouds": {"base": 500}}}}}

    resolved = await extension.get_presets({"settings": "Summer,Winter"})

    assert [row["modify"]["weather"]["clouds"]["base"] for row in resolved] == [1000, 500]
    # and a single name is the same read: one entry, not a one-item split surprise
    assert len(await extension.get_presets({"settings": "Winter"})) == 1


# --------------------------------------------------------------------- 3. the message

@pytest.mark.asyncio
async def test_the_audit_message_names_the_preset_and_is_unchanged_when_none_was_picked(
        config_dir, clean_registry, recording_bot):
    """The action's message IS the audit text: it names the preset, or keeps today's sentence."""
    (config_dir / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")

    picked_cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, picked_cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        await http.post(STARTUP, data={"server": "SRS-1", "_csrf_token": token_of(dialog.text),
                                       PRESET_FIELD: "Summer"})

    empty_cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, empty_cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        await http.post(STARTUP, data={"server": "SRS-1", "_csrf_token": token_of(dialog.text)})

    assert [call[0] for call in recording_bot.calls] == [
        "Server 'SRS-1' started up — weather preset 'Summer' requested.",
        "Server 'SRS-1' started up.",
    ]


def test_the_action_declares_the_preset_parameter_and_the_field(config_dir):
    """The declaration's contract: ``startup_server`` takes ``preset`` (default ``""``) and the
    Startup control declares the pick's field under the SAME name."""
    import inspect

    from plugins.mission.actions import startup_server

    params = inspect.signature(startup_server).parameters
    assert "preset" in params and params["preset"].default == ""

    startup = next(action for action in actions_page.SERVER_ACTIONS if action.key == "startup")
    assert startup.select is not None
    assert startup.select.field == PRESET_FIELD == "preset"
    assert startup.select.options == (), "the options are drawn per request, not declared"


# ---------------------------------------------------------------- 4. the fallback

@pytest.mark.asyncio
async def test_a_node_whose_presets_cannot_be_read_offers_two_modes(config_dir,
                                                                   clean_registry):
    """The read fails: the dialog still renders (200), with the file mode WITHHELD.

    A preset out of a file is resolved by reading the node's files at POST time, so with nothing
    readable that mode could only be refused — it is not offered, and the pick says why. The launch
    itself is never refused: "leave the weather alone" and "create your own weather" are both there.
    """
    (config_dir / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")
    node = PresetNode(config_dir, read_error=PermissionError("the node said no"))
    app = build(config_dir, node_fleet(node))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = pick_block(dialog.text)
    assert pick_values(block) == [], "no name could be read, so none is offered"
    assert "could not be read" in dialog.text
    mode = re.search(r'<fieldset class="dlg-field dlg-radio".*?</fieldset>', dialog.text, re.S)
    assert mode, "the weather mode control is missing"
    assert 'value="file"' not in mode.group(0), "the file mode must not be offered unreadable"
    assert 'value="leave"' in mode.group(0) and 'value="custom"' in mode.group(0)
    assert dialog.status_code == 200


# ---------------------------------------------------------------- the i18n of the new copy

@pytest.mark.asyncio
async def test_the_modes_and_their_copy_render_in_german(config_dir, clean_registry):
    """The copy is wrapped: with German chosen, the mode rows, the label and the help are German,
    while a preset NAME is operator data and stays as it is.

    English stays the msgid, so the English render is byte-for-byte what it always was; this proves the
    REQUEST's language reaches the per-request field the page composes.
    """
    from services.webservice import i18n

    (config_dir / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")
    app = build(config_dir, node_fleet(PresetNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        http.cookies.set(i18n.COOKIE_NAME, "de")
        dialog = await http.post(STARTUP + CONFIRM,
                                 data={"server": "SRS-1", "_csrf_token": token_of(page)})

    assert dialog.status_code == 200, dialog.text
    mode = re.search(r'<fieldset class="dlg-field dlg-radio".*?</fieldset>', dialog.text, re.S)
    assert mode, "the weather mode control is missing"
    assert ">Wetter unverändert lassen</label>" in mode.group(0)
    assert ">Eigenes Wetter erstellen</label>" in mode.group(0)
    assert ">Preset aus einer Datei verwenden</label>" in mode.group(0)
    assert "Wetter-Preset" in dialog.text, "the field's label is not German"
    assert "Wird beim Start dieses Servers" in dialog.text, "the field's help is not German"
    assert ">Summer</option>" in pick_block(dialog.text), "a preset name is operator data"


# ------------------------------------------------- the reveal, driven against a minimal DOM

@pytest.mark.asyncio
async def test_the_mode_scopes_the_blocks_and_the_file_pick_narrows_the_list(tmp_path):
    """ACCEPTANCE — the SHIPPED ``static/dialog-preset.js`` really scopes the blocks by mode, and the
    file pick really narrows the list.

    Driven against a minimal DOM in ``node``: the mode radios, the file pick, the preset pick with two
    ``<optgroup>``s and the ``.dlg-temp`` block. Nothing is submitted (the dialog's form posts the
    launch), so the assertions are on ``display``/``disabled``/``selected`` after each interaction.
    """
    if shutil.which("node") is None:
        pytest.skip("node is not installed: no DOM to drive the script with")
    script = (TESTS.parent / "services" / "webservice" / "static" / "dialog-preset.js")

    done = subprocess.run(["node", str(MODE_HARNESS), str(script)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    report = {row["step"]: row for row in json.loads(done.stdout)}

    # the DEFAULT is "leave the weather alone": neither the pick nor the fields are shown
    assert report["initial"]["pickDisplay"] == "none", report["initial"]
    assert report["initial"]["fileDisplay"] == "none", report["initial"]
    assert report["initial"]["tempDisplay"] == "none", report["initial"]
    # the file mode shows the file pick and the preset pick, never the temporary fields
    assert report["file-mode"]["pickDisplay"] == "", report["file-mode"]
    assert report["file-mode"]["fileDisplay"] == "", report["file-mode"]
    assert report["file-mode"]["tempDisplay"] == "none", report["file-mode"]
    # every group is drawn from the start: the filter's "All files" hides nothing
    assert report["file-mode"]["flatGroupHidden"] is False, report["file-mode"]
    assert report["file-mode"]["extraGroupHidden"] is False, report["file-mode"]
    # a chosen file hides the OTHER file's group and NEVER clears a tick: the hidden group's entry
    # stays selected, and its value carries the file it came from
    assert report["other-file-chosen"]["flatGroupHidden"] is True, report["other-file-chosen"]
    assert report["other-file-chosen"]["extraGroupHidden"] is False, report["other-file-chosen"]
    assert report["other-file-chosen"]["selected"] == ["config/presets.yaml\nSummer",
                                                       "config/presets/extra.yaml\nWinter"], \
        report["other-file-chosen"]
    # the chosen block lists BOTH ticks in the select's order — the files' config order, Summer
    # first even though it was ticked first — each chip naming its file, with the count beside it
    assert report["other-file-chosen"]["chosenHidden"] is False, report["other-file-chosen"]
    assert report["other-file-chosen"]["chosenCount"] == "2", report["other-file-chosen"]
    assert report["other-file-chosen"]["chips"] == [
        {"name": "Summer", "file": "config/presets.yaml"},
        {"name": "Winter", "file": "config/presets/extra.yaml"}], report["other-file-chosen"]
    # the custom mode shows today's temporary fieldset, and hides the two picks
    assert report["custom-mode"]["tempDisplay"] == "", report["custom-mode"]
    assert report["custom-mode"]["pickDisplay"] == "none", report["custom-mode"]
    # and back to "leave": nothing but the modes is shown
    assert report["leave-mode"]["tempDisplay"] == "none", report["leave-mode"]
    assert report["leave-mode"]["pickDisplay"] == "none", report["leave-mode"]


# -------------------------------------- the pick's own pin, against the SHIPPED asset bytes

STATIC = TESTS.parent / "services" / "webservice" / "static"
PICK_ASSET = STATIC / "dialog-preset.js"


def run_pick_harness(script: Path = PICK_ASSET) -> dict[str, dict]:
    """Drive ``dialog_pick_harness.js`` against *script*; ``{step: observation}``.

    The harness reads the file it is given — by default the SHIPPED asset — and refuses (a non-zero
    exit) any run whose filter disables a group, whose filter clears a tick, or whose chips drift.
    """
    if shutil.which("node") is None:
        pytest.skip("node is not installed: no DOM to drive the script with")
    done = subprocess.run(["node", str(PICK_HARNESS), str(script)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return {row["step"]: row for row in json.loads(done.stdout)}


def test_the_pick_pin_drives_the_bytes_the_dialog_actually_loads():
    """The pin reads the asset the page names, so it cannot drift onto a copy of the script."""
    template = (TESTS.parent / "services" / "webservice" / "templates"
                / "confirm.html").read_text(encoding="utf-8")
    assert re.search(r'<script src="/static/dialog-preset\.js(\?v=[^"]*)?"', template), \
        "the dialog no longer loads the asset this pin drives"
    assert PICK_ASSET.is_file()


def test_the_file_filter_shows_all_files_by_default_and_only_hides_what_it_leaves_out():
    """The filter is a VIEW: it starts on ``All files`` (empty value), hides the other files' groups,
    and never disables a group, never disables an entry and never clears a tick."""
    report = run_pick_harness()
    start = report["initial"]
    assert start["fileValue"] == "", "the filter does not default to All files"
    assert start["groupsShown"] == ["config/presets.yaml", "config/presets/extra.yaml"], start
    assert start["groupsDisabled"] == [] and start["optionsDisabled"] == [], start
    assert start["selected"] == [] and start["chosenHidden"] is True, start
    assert start["chosenCount"] == "0", start

    filtered = report["filtered"]
    assert filtered["fileValue"] == "config/presets/extra.yaml", filtered
    assert filtered["groupsShown"] == ["config/presets/extra.yaml"], \
        "the filter did not hide the groups it leaves out"
    assert filtered["groupsDisabled"] == [], "the filter disabled a group"
    assert filtered["optionsDisabled"] == [], "the filter disabled an entry"
    assert filtered["selected"] == ["config/presets.yaml\nSummer",
                                    "config/presets/extra.yaml\nWinter"], \
        "the filter cleared a tick"
    assert [(chip["name"], chip["file"]) for chip in filtered["chips"]] == \
        [("Summer", "config/presets.yaml"), ("Winter", "config/presets/extra.yaml")], filtered


def test_the_chosen_block_lists_a_chip_per_tick_in_select_order_and_removes_it():
    """One chip per ticked entry, in the SELECT's order (the files' config order, never the click
    order), each naming its own file, removable through ``data-chosen-remove``."""
    report = run_pick_harness()
    # the mode, never a tick, decides whether the temporary fields are shown
    assert report["name"]["tempDisplay"] == "none", report["name"]
    assert report["name"]["chosenHidden"] is False and report["name"]["chosenCount"] == "1", \
        report["name"]
    assert [(chip["name"], chip["file"]) for chip in report["name"]["chips"]] == \
        [("Winter", "config/presets/extra.yaml")], report["name"]
    # the mode change keeps the tick and its chip
    assert report["custom"]["tempDisplay"] == "", report["custom"]
    assert [(chip["name"], chip["file"]) for chip in report["custom"]["chips"]] == \
        [("Winter", "config/presets/extra.yaml")], report["custom"]
    # the SECOND entry was ticked second, the FIRST group's entry was ticked last: the chips stand in
    # the select's order regardless
    later = report["name-from-custom"]
    assert later["tempDisplay"] == "none", later
    assert later["selected"] == ["config/presets.yaml\nSummer",
                                 "config/presets/extra.yaml\nWinter"], later
    assert [(chip["name"], chip["file"]) for chip in later["chips"]] == \
        [("Summer", "config/presets.yaml"), ("Winter", "config/presets/extra.yaml")], later
    assert later["chosenCount"] == "2", later
    # a chip's Remove button unticks exactly its own entry and leaves the filter's view alone
    removed = report["removed"]
    assert [(chip["name"], chip["file"]) for chip in removed["chips"]] == \
        [("Winter", "config/presets/extra.yaml")], removed
    assert removed["selected"] == ["config/presets/extra.yaml\nWinter"], removed
    assert removed["groupsShown"] == ["config/presets/extra.yaml"], \
        "Remove disturbed the filter's view"


@pytest.mark.parametrize("mutation", [
    pytest.param('group.disabled = !match;', id="disable"),
    pytest.param('if (!match) { var options = group.querySelectorAll("option");'
                 ' for (var index2 = 0; index2 < options.length; index2 += 1)'
                 ' { options[index2].selected = false; } }', id="clear-ticks"),
])
def test_the_pin_refuses_a_filter_that_disables_a_group_or_clears_a_tick(tmp_path, mutation):
    """The pin's own guard: a script whose filter DISABLES groups or CLEARS ticks must fail the run.

    The mutant is the shipped asset with one line added on a COPY — the shipped bytes are never
    written — so the pin proves it would catch the regression rather than merely passing today.
    """
    if shutil.which("node") is None:
        pytest.skip("node is not installed: no DOM to drive the script with")
    source = PICK_ASSET.read_text(encoding="utf-8")
    needle = "      group.style.display = match ? \"\" : \"none\";\n"
    assert needle in source, "the filter's own line moved: this guard needs re-anchoring"
    mutant = tmp_path / "dialog-preset.js"
    mutant.write_text(source.replace(needle, needle + "      " + mutation + "\n"), encoding="utf-8")

    done = subprocess.run(["node", str(PICK_HARNESS), str(mutant)], capture_output=True, text=True, encoding="utf-8")

    assert done.returncode != 0, "a filter that disables a group and clears a tick passed the pin"
    assert "FAIL filtered: " in done.stderr, done.stderr

