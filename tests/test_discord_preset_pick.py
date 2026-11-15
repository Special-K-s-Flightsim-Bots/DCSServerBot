"""The three Discord commands offer ONE cross-file preset pick, built from the core preset rule.

The website's launch dialogs already draw one pick over every preset file a node carries; the
Discord commands have to offer the same choice, so a preset is picked by NAME, the file it comes from
is its description, several files' presets can be ticked together, and the picked documents plus
their names travel into the launch.

Contract pinned here, one section per acceptance item of the card:

* OPTION BUILDING — every file's presets are offered, each labelled with the preset's name and
  described by its file; the file filter narrows to one file; more than 25 presets split into pages;
  a combination whose member lives in ANOTHER file still resolves (the core rule, not a copy);
* THE PICK — a cancelled pick, an empty pick, a pick over the limit and a refused pick each report
  one sentence and answer nothing, so no launch can start on a pick it never honoured;
* COMMAND WIRING — ``presets=False`` posts the same launch call as before, a ``presets_file`` alone
  means "yes, pick from that file", a cancelled pick starts nothing, and the picked names reach
  ``startup`` / ``loadMission`` and the node's recorded row.

The REAL ``plugins.mission.views`` helpers, the REAL ``Scheduler.startup`` / ``launch_dcs`` and the
REAL ``ServerImpl`` load path are driven over doubles: no live Discord, no DCS, no transport. Only the
pick VIEW is replaced (``PresetPickView``), because its outcome is what a human would produce.
"""
from __future__ import annotations

import asyncio
import gettext
import importlib
import io
import os
import logging
import types
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from core import ServiceRegistry, Status
from core.data.impl import serverimpl
from core.data.impl.serverimpl import ServerImpl

from test_mission_load_clears_stale_preset import (  # noqa: F401 - fixtures imported on purpose
    SERVER, RecordingPool, RowState, build_mizedit, build_server, label_of, mission, no_write,
)

YAML_WRITER = YAML()


# ------------------------------------------------------------------ the node / pick / launch doubles

def _dump(document) -> bytes:
    stream = io.StringIO()
    YAML_WRITER.dump(document, stream)
    return stream.getvalue().encode("utf-8")


class _Node:
    """A node that carries preset files: ``config_dir``, ``list_files`` and ``read_file`` only."""

    def __init__(self, files: dict[str, dict], config_dir: str = "config"):
        self.config_dir = config_dir
        self.name = "node-a"
        # the bot composes this path with os.path.join, so the double must too: a "/" key
        # would miss on Windows and every read would raise KeyError
        self.files = {os.path.normpath(os.path.join(config_dir, name)): document
                      for name, document in files.items()}
        self.listed = 0
        self.read = 0

    async def list_files(self, path, *, pattern=None):
        self.listed += 1
        return [(path_, 1, 1) for path_ in sorted(self.files)]

    async def read_file(self, path):
        self.read += 1
        return _dump(self.files[os.path.normpath(str(path))])


class _Msg:
    async def delete(self):
        return None


class _Ctx:
    """What the pick sends on: a context's ``send`` and nothing else."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, *args, **kwargs):
        self.sent.append({"content": args[0] if args else None, **kwargs})
        return _Msg()


def _pick_outcome(monkeypatch, result, cancelled: bool = False):
    """Replace the pick VIEW with one answering *result* at once, as a human's ticks would.

    *cancelled* is what the view's Cancel button sets; a timeout is ``result=None`` with it False.
    """
    from plugins.mission import views

    class _View:
        instances: list = []

        def __init__(self, pages, placeholder=None):
            self.pages = pages
            self.placeholder = placeholder
            self.result = result
            self.cancelled = cancelled
            _View.instances.append(self)

        async def wait(self):
            return None

    monkeypatch.setattr(views, "PresetPickView", _View)
    return _View


@pytest.fixture
def views_module():
    return importlib.import_module("plugins.mission.views")


@pytest.fixture
def scheduler_commands():
    """Import ``plugins.scheduler.commands`` with the ``BotService`` lookup stubbed.

    The module builds its app-commands at class-body time, where ``core.plugin.Command`` reads
    ``ServiceRegistry.get(BotService).bot`` - a cluster a bare test interpreter has none of.
    """
    from services.bot import BotService

    stub_bot = types.SimpleNamespace(node=types.SimpleNamespace(all_nodes={"n": 1}, config_dir="config"),
                                     servers={}, locals={})
    saved = ServiceRegistry._singletons.get(BotService, None)
    ServiceRegistry._singletons[BotService] = types.SimpleNamespace(bot=stub_bot)
    try:
        yield importlib.import_module("plugins.scheduler.commands")
    finally:
        if saved is None:
            ServiceRegistry._singletons.pop(BotService, None)
        else:
            ServiceRegistry._singletons[BotService] = saved


FILES = {
    "presets.yaml": {
        "plain": {"modify": {"weather": {"clouds": {"base": 1000}}}},
        "combo": ["plain", "snow"],
        "hidden_one": {"hidden": True},
        "scalar": 5,
    },
    "presets_winter.yaml": {
        "snow": {"modify": {"weather": {"clouds": {"base": 500}}}},
        "fog": {"modify": {"weather": {"fog": {"mode": "manual"}}}},
    },
}
LABELS = {
    "config/presets.yaml": FILES["presets.yaml"],
    "config/presets_winter.yaml": FILES["presets_winter.yaml"],
}


# --------------------------------------------------------- 1. option building: every file, labelled

def test_every_files_presets_are_offered_with_their_file(views_module):
    """Every name a file OFFERS is an option, described by the file it comes from."""
    pages = views_module.preset_pages(LABELS)

    options = [option for page in pages for option in page]
    assert [(option.label, option.description) for option in options] == [
        ("plain", "config/presets.yaml"),
        ("combo", "config/presets.yaml"),
        ("snow", "config/presets_winter.yaml"),
        ("fog", "config/presets_winter.yaml"),
    ], "every file's offered names must be present, each described by its own file, in file order"

    assert options[0].value == "config/presets.yaml\nplain", \
        "an option's value must carry its file, so a name two files define applies its own body"


def test_a_hidden_preset_and_a_scalar_entry_are_not_offered(views_module):
    options = [option for page in views_module.preset_pages({"config/presets.yaml": FILES["presets.yaml"]})
               for option in page]
    assert [option.label for option in options] == ["plain", "combo"], \
        "a hidden preset and a scalar entry carry no name a pick could apply"


def test_the_file_filter_narrows_the_pick_to_one_file(views_module):
    pages = views_module.preset_pages(LABELS, "config/presets_winter.yaml")

    assert [[option.label for option in page] for page in pages] == [["snow", "fog"]], \
        "naming one file must offer that file's presets and no other file's"


def test_more_than_25_presets_split_into_pages(views_module):
    many = {"config/presets.yaml": {f"p{index}": {"v": index} for index in range(30)}}

    pages = views_module.preset_pages(many)

    assert [len(page) for page in pages] == [25, 5], "the pick must split 30 presets into 25 + 5"
    assert [option.label for page in pages for option in page] == [f"p{index}" for index in range(30)], \
        "the pages must keep the node's order across the split"


def test_a_name_two_files_define_is_two_options(views_module):
    pages = views_module.preset_pages({"config/presets.yaml": {"snow": {"a": 1}},
                                       "config/presets_winter.yaml": {"snow": {"b": 2}}})

    options = [option for page in pages for option in page]
    assert [option.description for option in options] == ["config/presets.yaml", "config/presets_winter.yaml"]
    assert len({option.value for option in options}) == 2, \
        "a name two files define must be two distinct options, one per file"


# ------------------------------------------- 2. the core rule: a combination spanning two files

@pytest.mark.asyncio
async def test_a_combination_member_from_another_file_still_resolves(views_module, monkeypatch):
    """Picking the combination applies its members, one of which lives in the OTHER file."""
    _pick_outcome(monkeypatch, ["config/presets.yaml\ncombo"])

    picked = await views_module.pick_presets(_Ctx(), LABELS, file_label="", ephemeral=True)

    assert picked is not None, "a combination whose members exist must resolve"
    assert picked.resolution.names == ["combo"]
    assert picked.resolution.documents == [
        {"modify": {"weather": {"clouds": {"base": 1000}}}},
        {"modify": {"weather": {"clouds": {"base": 500}}}},
    ], "the member from the other file must contribute its own document, in the combination's order"


@pytest.mark.asyncio
async def test_a_pick_across_two_files_states_its_documents_and_every_name(views_module, monkeypatch):
    _pick_outcome(monkeypatch, ["config/presets.yaml\nplain", "config/presets_winter.yaml\nsnow"])

    picked = await views_module.pick_presets(_Ctx(), LABELS, file_label="", ephemeral=True)

    assert picked.resolution.names == ["plain", "snow"], \
        "the record must name every picked preset, in the pick's own order"
    assert views_module.launch_preset_kwargs(picked.resolution) == {
        "preset": [{"modify": {"weather": {"clouds": {"base": 1000}}}},
                   {"modify": {"weather": {"clouds": {"base": 500}}}}],
        "preset_name": ["plain", "snow"],
    }, "several documents travel as a list, with the names beside them"


@pytest.mark.asyncio
async def test_one_picked_preset_travels_as_its_own_document(views_module, monkeypatch):
    _pick_outcome(monkeypatch, ["config/presets_winter.yaml\nsnow"])

    picked = await views_module.pick_presets(_Ctx(), LABELS, file_label="", ephemeral=True)

    assert views_module.launch_preset_kwargs(picked.resolution) == {
        "preset": {"modify": {"weather": {"clouds": {"base": 500}}}},
        "preset_name": ["snow"],
    }, "a single document must travel as the document itself, not wrapped in a list"


# ------------------------------------------------------------- 3. every way a pick answers nothing

@pytest.mark.asyncio
async def test_a_cancelled_pick_says_it_was_cancelled_and_answers_nothing(views_module, monkeypatch):
    _pick_outcome(monkeypatch, None, cancelled=True)
    ctx = _Ctx()

    picked = await views_module.pick_presets(ctx, LABELS, ephemeral=True)

    assert picked is None, "a cancelled pick must answer nothing, so nothing can start on it"
    said = [entry["content"] for entry in ctx.sent if entry["content"]]
    assert said == ["Cancelled — nothing was applied."], \
        "a cancel must be reported with the cancel sentence, not the no-tick one"


@pytest.mark.asyncio
async def test_a_timed_out_pick_says_it_timed_out_with_its_own_sentence(views_module, monkeypatch):
    _pick_outcome(monkeypatch, None, cancelled=False)
    ctx = _Ctx()

    picked = await views_module.pick_presets(ctx, LABELS, ephemeral=True)

    assert picked is None, "a timed out pick must answer nothing either"
    said = [entry["content"] for entry in ctx.sent if entry["content"]]
    assert said == ["The pick timed out, so nothing was applied."], \
        "a timeout must not borrow the cancel sentence or the no-tick one"


def test_the_three_endings_that_answer_nothing_read_differently(views_module):
    """One sentence per ending, so an operator can tell a cancel from a timeout from an empty OK."""
    cancelled = views_module.PRESET_PICK_CANCELLED
    timed_out = views_module.PRESET_PICK_TIMED_OUT
    no_tick = views_module.PRESET_REFUSAL_SENTENCES[views_module.REFUSAL_NO_PICK]

    assert len({cancelled, timed_out, no_tick}) == 3, \
        f"the three endings must read differently, got {[cancelled, timed_out, no_tick]}"
    assert cancelled == "Cancelled — nothing was applied."
    assert no_tick == "No preset was picked, so nothing was applied."
    assert "timed out" in timed_out, "the timeout sentence must name what happened"


@pytest.mark.asyncio
async def test_an_empty_pick_says_so_and_answers_nothing(views_module, monkeypatch):
    _pick_outcome(monkeypatch, [])
    ctx = _Ctx()

    picked = await views_module.pick_presets(ctx, LABELS, ephemeral=True)

    assert picked is None
    assert "No preset was picked" in ctx.sent[-1]["content"], \
        "OK with nothing ticked is a pick of nothing, and must be said out loud"
    assert "Cancelled" not in ctx.sent[-1]["content"], "and it must not read as a cancel"


@pytest.mark.asyncio
async def test_a_pick_over_the_limit_is_refused_rather_than_trimmed(views_module, monkeypatch):
    many = {"config/presets.yaml": {f"p{index}": {"v": index} for index in range(12)}}
    _pick_outcome(monkeypatch, [f"config/presets.yaml\np{index}" for index in range(11)])
    ctx = _Ctx()

    picked = await views_module.pick_presets(ctx, many, ephemeral=True)

    assert picked is None, "a pick over the limit must not be silently trimmed to the first ten"
    assert "at most 10" in ctx.sent[-1]["content"], "the refusal must name the limit"


@pytest.mark.asyncio
async def test_a_pick_naming_a_preset_its_file_no_longer_carries_is_refused(views_module, monkeypatch):
    _pick_outcome(monkeypatch, ["config/presets.yaml\ngone"])
    ctx = _Ctx()

    picked = await views_module.pick_presets(ctx, LABELS, ephemeral=True)

    assert picked is None
    assert ctx.sent[-1]["content"] == "One of the picked presets is not in its file any more.", \
        "a name the node no longer carries must be refused, never dropped from the pick"


@pytest.mark.asyncio
async def test_a_node_with_no_preset_files_says_so(views_module):
    ctx = _Ctx()

    picked = await views_module.pick_presets(ctx, {}, ephemeral=True)

    assert picked is None
    assert ctx.sent[-1]["content"] == "There are no presets to choose from."


# --------------------------------------------------------------- 4. the launch command's decision

@pytest.mark.asyncio
async def test_no_presets_requested_posts_the_same_launch_as_before(views_module, monkeypatch):
    """``presets=False`` and no file: no read, no pick, and NO preset keyword at all."""
    node = _Node(FILES)

    def _explode(*_args, **_kwargs):
        raise AssertionError("a launch that asked for no preset must not read the node's files")

    monkeypatch.setattr(views_module, "pick_node_presets", _explode)

    decided = await views_module.launch_presets(_Ctx(), node, presets=False, presets_file=None)

    assert decided.kwargs == {} and decided.values == [], \
        "a launch nobody picked a preset for must state no preset at all"
    assert node.listed == 0, "and it must not touch the node"


@pytest.mark.asyncio
async def test_a_presets_file_alone_implies_the_pick(views_module, monkeypatch):
    """Forgiving input: a file given without ``presets`` means "yes, pick from that file"."""
    node = _Node(FILES)
    view = _pick_outcome(monkeypatch, ["config/presets_winter.yaml\nsnow"])

    decided = await views_module.launch_presets(_Ctx(), node, presets=False,
                                                presets_file="config/presets_winter.yaml")

    assert decided is not None, "the file alone must be read as a pick request"
    assert [[option.label for option in page] for page in view.instances[0].pages] == [["snow", "fog"]], \
        "the pick must be drawn, narrowed to the file that was given"
    assert decided.kwargs["preset_name"] == ["snow"]
    assert decided.values == ["config/presets_winter.yaml\nsnow"], \
        "the raw entry must travel so a deferred run can resolve it again"


@pytest.mark.asyncio
async def test_a_cancelled_pick_answers_nothing_to_the_launch(views_module, monkeypatch):
    node = _Node(FILES)
    _pick_outcome(monkeypatch, None, cancelled=True)

    decided = await views_module.launch_presets(_Ctx(), node, presets=True, presets_file=None)

    assert decided is None, "a cancelled pick must leave the caller nothing to launch with"


@pytest.mark.asyncio
async def test_a_timed_out_pick_answers_nothing_to_the_launch(views_module, monkeypatch):
    node = _Node(FILES)
    _pick_outcome(monkeypatch, None, cancelled=False)

    decided = await views_module.launch_presets(_Ctx(), node, presets=True, presets_file=None)

    assert decided is None, "a timed out pick must leave the caller nothing to launch with"


# ------------------------------------------------------------------ 5. the three commands' wiring

class _Response:
    def __init__(self):
        self.defers = 0
        self.messages: list[str] = []

    def is_done(self):
        return False

    async def defer(self, **kwargs):
        self.defers += 1

    async def send_message(self, content=None, **kwargs):
        self.messages.append(content)


class _Interaction(_Ctx):
    """A command interaction: a send target, a response, and a client with no server in scope."""

    def __init__(self):
        super().__init__()
        self.response = _Response()
        self.user = types.SimpleNamespace(display_name="tester")
        self.client = types.SimpleNamespace(get_server=lambda interaction: None,
                                            log=logging.getLogger("test.preset.pick"))


class _Server:
    def __init__(self, status=Status.SHUTDOWN):
        self.name = SERVER
        self.display_name = SERVER
        self.status = status
        self.maintenance = False
        self.node = _Node(FILES)


def _scheduler_with_startup():
    """The slice of the cog ``startup`` reads, with ``_startup`` recording instead of launching."""
    calls: dict = {}
    self_ = types.SimpleNamespace(log=logging.getLogger("test.preset.pick"),
                                  bot=types.SimpleNamespace(), __cog_name__="Scheduler")

    async def _startup(interaction, *, embed=None, server=None, **kwargs):
        calls["server"] = server
        calls["kwargs"] = kwargs

    self_._startup = _startup
    return self_, calls


def test_server_startup_offers_the_two_optional_preset_options(scheduler_commands):
    """``/server startup``'s option list: both preset options, both optional."""
    from plugins.scheduler.commands import Scheduler

    options = {option.name: option for option in Scheduler.startup.parameters}
    assert "presets" in options and "presets_file" in options, \
        "/server startup must offer the preset pick and its file filter"
    assert options["presets"].required is False and options["presets_file"].required is False, \
        "both must be optional, so the command behaves as before when they are omitted"


def _command_signature(path, name):
    """The parameter names and the SOURCE TEXT of each default of one command function.

    The mission plugin imports ``openpyxl`` at module scope, which a test interpreter does not carry,
    so its command signatures are read from the file rather than from the imported module. The path
    is derived from this file, never from the working directory.
    """
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    tree = ast.parse((repo / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            args = node.args.args + node.args.kwonlyargs
            defaults = node.args.defaults + node.args.kw_defaults
            return ([argument.arg for argument in args],
                    {argument.arg: ast.unparse(default)
                     for argument, default in zip(args[-len(defaults):], defaults)})
    raise AssertionError(f"{name} not found in {path}")


def test_mission_load_offers_the_two_optional_preset_options():
    names, defaults = _command_signature("plugins/mission/commands.py", "load")

    assert names[-2:] == ["presets", "presets_file"], \
        "/mission load must gain the preset pick and its file filter"
    assert defaults["presets"] == "False" and defaults["presets_file"] == "None", \
        "both must default to off, so the command behaves exactly as today"


def test_mission_modify_takes_its_file_option_as_a_filter():
    names, defaults = _command_signature("plugins/mission/commands.py", "modify")

    assert "presets_file" in names and defaults["presets_file"] == "None", \
        "/mission modify keeps its file option, now an optional FILTER (omitted = all files)"


@pytest.mark.asyncio
async def test_startup_without_presets_launches_exactly_as_before(scheduler_commands, monkeypatch):
    self_, calls = _scheduler_with_startup()
    interaction = _Interaction()
    server = _Server()

    await scheduler_commands.Scheduler.startup.callback(self_, interaction, server)

    assert calls["kwargs"] == {"maintenance": False, "mission_id": None, "run_extensions": True,
                               "use_orig": True, "ephemeral": True}, \
        "a startup with no preset asked for must reach the launch with no preset keyword at all"


@pytest.mark.asyncio
async def test_startup_with_presets_picks_before_launching_and_states_the_pick(scheduler_commands, monkeypatch):
    from plugins.mission import views

    self_, calls = _scheduler_with_startup()
    interaction = _Interaction()
    server = _Server()
    _pick_outcome(monkeypatch, ["config/presets_winter.yaml\nsnow"])

    await scheduler_commands.Scheduler.startup.callback(self_, interaction, server, presets=True)

    assert calls["kwargs"]["preset"] == {"modify": {"weather": {"clouds": {"base": 500}}}}, \
        "the picked document must reach the launch"
    assert calls["kwargs"]["preset_name"] == ["snow"], "and so must the name, for the record"


@pytest.mark.asyncio
async def test_a_cancelled_pick_starts_nothing(scheduler_commands, monkeypatch):
    self_, calls = _scheduler_with_startup()
    interaction = _Interaction()
    server = _Server()
    _pick_outcome(monkeypatch, None, cancelled=True)

    await scheduler_commands.Scheduler.startup.callback(self_, interaction, server, presets=True)

    assert calls == {}, "a cancelled pick must not reach the launch at all"
    assert interaction.response.messages == [], "and it must not send anything else either"


@pytest.mark.asyncio
async def test_a_pick_beside_no_extensions_is_refused(scheduler_commands):
    self_, calls = _scheduler_with_startup()
    interaction = _Interaction()
    server = _Server()

    await scheduler_commands.Scheduler.startup.callback(self_, interaction, server,
                                                        presets=True, run_extensions=False)

    assert calls == {}, "a pick beside 'do not run the extensions' must not launch"
    assert interaction.response.messages and "does not run the extensions" in interaction.response.messages[0], \
        "the refusal must name both ways out"


@pytest.mark.asyncio
async def test_launch_dcs_states_a_pick_only_when_there_is_one(scheduler_commands):
    """The launch call itself: no preset keyword without a pick, both with one."""
    launched: list[dict] = []

    async def startup(**kwargs):
        launched.append(kwargs)

    plain = types.SimpleNamespace(name=SERVER, status=Status.SHUTDOWN, startup=startup)
    self_ = types.SimpleNamespace(log=logging.getLogger("test.preset.pick"),
                                  bot=types.SimpleNamespace(audit=_audit),
                                  __cog_name__="Scheduler")

    await scheduler_commands.Scheduler.launch_dcs(self_, plain)
    assert launched[-1] == {"modify_mission": True, "use_orig": True}, \
        "a launch with no pick must post the same call it always posted"

    await scheduler_commands.Scheduler.launch_dcs(self_, plain, preset={"a": 1}, preset_name=["snow"])
    assert launched[-1] == {"modify_mission": True, "use_orig": True,
                            "preset": {"a": 1}, "preset_name": ["snow"]}


async def _audit(*args, **kwargs):
    return None


# ------------------------------------------------------- 6. the RPC hop carries the names as given

class _Bus:
    """The service bus a proxy hands its RPC to: records the payload, answers a value."""

    def __init__(self, answer=None):
        self.sent: list[dict] = []
        self.answer = answer

    async def send_to_node_sync(self, message, timeout=None, node=None):
        self.sent.append(message)
        return self.answer


def _proxy(bus):
    from core.data.proxy.serverproxy import ServerProxy

    srv = object.__new__(ServerProxy)
    srv.name = SERVER
    srv.node = types.SimpleNamespace(name="node-a", slow_system=False)
    srv.bus = bus
    return srv


@pytest.mark.asyncio
async def test_the_proxy_sends_a_list_of_names_unchanged():
    """The hop between the bot and the node passes the picked names through as they are."""
    from core.data.proxy.serverproxy import ServerProxy

    bus = _Bus(answer=True)
    srv = _proxy(bus)

    await ServerProxy.loadMission(srv, "Training.miz", preset=[{"a": 1}],
                                  preset_name=["plain", "snow"])

    params = bus.sent[-1]["params"]
    assert params["preset"] == [{"a": 1}]
    assert params["preset_name"] == ["plain", "snow"], \
        "a list of picked names must travel as a list, never flattened by the hop"


@pytest.mark.asyncio
async def test_the_proxy_omits_the_preset_keys_when_nothing_was_picked():
    """``None`` stays an ABSENT key, which is what keeps an older node callable."""
    from core.data.proxy.serverproxy import ServerProxy

    bus = _Bus(answer=True)
    srv = _proxy(bus)

    await ServerProxy.loadMission(srv, "Training.miz")

    params = bus.sent[-1]["params"]
    assert "preset" not in params and "preset_name" not in params, \
        "an untouched launch must not state a preset at all"


# ------------------------------------- 7. a DEFERRED pick ("when empty") is applied by its file

class _DeferredServer:
    """The slice of a server the scheduler's deferred run touches, recording what it was asked."""

    def __init__(self, status=Status.SHUTDOWN):
        self.name = SERVER
        self.status = status
        self.locals = {}
        self.node = _Node(FILES)
        self.modified = None
        self.launched: list[tuple] = []

    async def getMissionList(self):
        return ["Training.miz"]

    async def modifyMission(self, filename, preset, use_orig=True):
        self.modified = (filename, preset)
        return filename

    async def replaceMission(self, index, filename):
        return ["Training.miz"]

    async def start(self, **kwargs):
        self.launched.append(("start", kwargs))

    async def startup(self, **kwargs):
        self.launched.append(("startup", kwargs))

    async def loadMission(self, mission, **kwargs):
        self.launched.append(("loadMission", kwargs))


@pytest.mark.asyncio
async def test_a_deferred_pick_is_resolved_by_its_own_file(scheduler_commands):
    """The "when empty" branch keeps the entry's file, so a cross-file pick applies the right body."""
    server = _DeferredServer()
    self_ = types.SimpleNamespace(log=logging.getLogger("test.preset.pick"), __cog_name__="Scheduler",
                                  node=types.SimpleNamespace(config_dir="config"))
    self_._preset_file_text = scheduler_commands.Scheduler._preset_file_text
    rconf = {"method": "restart", "use_orig": True,
             "settings": ["config/presets_winter.yaml\nsnow"]}

    await scheduler_commands.Scheduler._run_with_presets(self_, server, rconf, 1, False)

    assert server.modified == ("Training.miz",
                               [{"modify": {"weather": {"clouds": {"base": 500}}}}]), \
        "the deferred run must apply the document of the file the preset was ticked in"
    assert server.launched == [("startup", {"modify_mission": False, "use_orig": False,
                                            "preset": ["snow"]})], \
        "and it must state the applied name so the node records it"


# ------------------------------------------------- 8. the picked names reach the node's record

@pytest.mark.asyncio
async def test_a_load_with_picked_presets_names_every_one_in_the_record(mission, no_write, tmp_path):
    presets = tmp_path / "presets.yaml"
    presets.write_text("plain:\n  modify:\n    weather:\n      clouds:\n        base: 1000\n", encoding="utf-8")
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool, extensions={"MizEdit": build_mizedit(presets)})
    documents = [{"modify": {"weather": {"clouds": {"base": 1000}}}},
                 {"modify": {"weather": {"clouds": {"base": 500}}}}]

    await ServerImpl.loadMission(srv, mission, modify_mission=True, use_orig=False,
                                 preset=documents, preset_name=["plain", "snow"])

    assert pool.row.preset == "plain, snow", \
        "the record must name every picked preset instead of recording a temporary one"
    assert pool.row.temporary is False, "a named pick is not a temporary preset"
    label = label_of(pool.row)
    assert label is not None and "plain" in label.name and "snow" in label.name, \
        "the server page must name every picked preset"


@pytest.mark.asyncio
async def test_a_start_that_states_picked_names_records_them(mission, monkeypatch):
    monkeypatch.setattr(serverimpl.utils, "is_desanitized", lambda node: True)
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool, status=Status.STOPPED)

    async def noop(*args, **kwargs):
        return None

    srv._assert_resource_available = noop
    srv._assert_ports_free = noop
    srv.init_extensions = noop
    srv.prepare_extensions = noop
    srv._cleanup_sqlite = lambda: None
    srv.do_startup = lambda: None

    await ServerImpl.startup(srv, modify_mission=False,
                             preset=[{"a": 1}], preset_name=["plain", "snow"])

    assert pool.row.preset == "plain, snow", \
        "a launch that already applied its weather states the names the record takes"
    assert pool.row.temporary is False


@pytest.mark.asyncio
async def test_a_name_keeps_its_own_spaces_in_the_record(mission, monkeypatch):
    """Names are operator data: joined VERBATIM, never trimmed into a different preset."""
    monkeypatch.setattr(serverimpl.utils, "is_desanitized", lambda node: True)
    pool = RecordingPool(RowState())
    srv = build_server(mission, pool=pool, status=Status.STOPPED)

    async def noop(*args, **kwargs):
        return None

    srv._assert_resource_available = noop
    srv._assert_ports_free = noop
    srv.init_extensions = noop
    srv.prepare_extensions = noop
    srv._cleanup_sqlite = lambda: None
    srv.do_startup = lambda: None

    await ServerImpl.startup(srv, modify_mission=False, preset={"a": 1}, preset_name=[" Snow ", "plain"])

    assert pool.row.preset == " Snow , plain", \
        "a preset name whose file spells ' Snow ' must be recorded exactly as it is"


# ---------------------------------------- 9. the real view's Cancel reaches the pick's report

@pytest.mark.asyncio
async def test_the_real_views_cancel_button_lands_on_the_cancel_sentence(views_module, monkeypatch):
    """The REAL ``PresetPickView``, its REAL Cancel callback, then the REAL ``pick_presets`` report."""
    class _Response:
        def __init__(self):
            self.defers = 0

        def is_done(self):
            return False

        async def defer(self):
            self.defers += 1

    class _Interaction:
        def __init__(self):
            self.response = _Response()

    view = views_module.PresetPickView(views_module.preset_pages(LABELS), placeholder="pick")
    cancel = next(item for item in view.children if getattr(item, "label", None) == "Cancel")
    await cancel.callback(_Interaction())
    assert view.cancelled is True, "the real button must set the flag pick_presets reads"

    async def _already_closed():
        return None

    view.wait = _already_closed
    monkeypatch.setattr(views_module, "PresetPickView", lambda *args, **kwargs: view)
    ctx = _Ctx()

    picked = await views_module.pick_presets(ctx, LABELS, ephemeral=True)

    assert picked is None, "a cancel must start nothing"
    assert ctx.sent[-1]["content"] == "Cancelled — nothing was applied.", \
        "the real cancel must land on its own sentence end to end"


# ---------------------------------------- 10. the cancel and timeout sentences ship translated

REPO = Path(__file__).resolve().parent.parent
LANGUAGES = ("cn", "de", "es", "ru")
NEW_SENTENCES = ("Cancelled — nothing was applied.", "The pick timed out, so nothing was applied.")


@pytest.mark.parametrize("language", LANGUAGES)
def test_each_mission_catalog_carries_the_cancel_and_timeout_sentences(language):
    text = (REPO / "locale" / language / "LC_MESSAGES" / "mission.po").read_text(encoding="utf-8")
    for msgid in NEW_SENTENCES:
        assert f'msgid "{msgid}"' in text, f"{language} mission.po lacks {msgid!r}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_compiled_mission_catalog_resolves_both_sentences(language):
    """A ``.po`` can carry a sentence its stale ``.mo`` never serves, so the compiled side is pinned."""
    translations = gettext.translation("mission", localedir=str(REPO / "locale"), languages=[language])
    for msgid in NEW_SENTENCES:
        resolved = translations.gettext(msgid)
        assert resolved != msgid, f"{language} mission: {msgid!r} still renders English"
