"""The three ``/mission`` commands that call a list writer directly answer the busy refusal.

``ServerImpl.addMission`` / ``deleteMission`` / ``setStartIndex`` raise ``RuntimeError`` carrying the
shared sentence while the server is ``LOADING`` or ``SHUTTING_DOWN``. ``/mission add``,
``/mission delete`` and ``/mission set`` (through ``_load``) call those writers straight, so an
uncaught refusal reached the bot's global command-error handler, which answers the operator with a
``repr`` and logs it as an error.

The three shipped bodies are DRIVEN here — ``Command.callback`` is a plain coroutine and every
collaborator is a double, so the refusal becomes observable as the one message the interaction
receives and as the absence of a raised exception (which is what would reach the global handler).
"""
from __future__ import annotations

import ast
import importlib
import os
import sys
import types
from pathlib import Path

import pytest

from conftest import host_path

from core import Status

REPO = Path(__file__).resolve().parent.parent
COMMANDS = REPO / "plugins" / "mission" / "commands.py"

#: The writers the commands reach directly, and the command body that reaches each one.
#:
#: ``replaceMission`` is reached from three bodies: ``modify`` (a preset that writes a new mission
#: file), ``rollback`` and ``persistence``.
WRITER_CALL_SITES = {
    ("add", "addMission"),
    ("delete", "deleteMission"),
    ("_load", "setStartIndex"),
    ("modify", "replaceMission"),
    ("rollback", "replaceMission"),
    ("persistence", "replaceMission"),
}


def _plugin_module(name: str):
    """Import a plugin module with a stub for an absent optional dependency."""
    stub = {mod: types.ModuleType(mod) for mod in ("openpyxl", "openpyxl.utils", "trueskill")}
    stub["openpyxl.utils"].get_column_letter = lambda n: "A"
    stub["openpyxl"].utils = stub["openpyxl.utils"]
    stub["trueskill"].TrueSkill = lambda *args, **kwargs: None
    stub["trueskill"].Rating = type("Rating", (), {})
    stub["trueskill"].BETA = 0
    stub["trueskill"].global_env = None
    added = {k: v for k, v in stub.items() if k not in sys.modules}
    sys.modules.update(added)
    try:
        return importlib.import_module(name)
    finally:
        for mod in added:
            sys.modules.pop(mod, None)


@pytest.fixture()
def mission():
    return _plugin_module("plugins.mission.commands")


def _refusal() -> str:
    from plugins.mission.actions import MISSION_LIST_BUSY_REFUSAL
    from core.data.impl.serverimpl import MISSION_LIST_BUSY_REFUSAL as WRITER_SENTENCE

    assert MISSION_LIST_BUSY_REFUSAL == WRITER_SENTENCE, "the plugin and the writer share one sentence"
    return MISSION_LIST_BUSY_REFUSAL


# ----------------------------------------------------------------------- the doubles

class _Recorder:
    """Records every ``send`` of one interaction surface."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send(self, content=None, **kwargs):
        self.calls.append((content, kwargs))

    #: the initial response surface names its reply ``send_message``
    async def send_message(self, content=None, **kwargs):
        self.calls.append((content, kwargs))

    async def defer(self, **kwargs):
        self.calls.append(("__defer__", kwargs))


class _Interaction:
    """The interaction surface the three commands touch, recording what the operator would see."""

    def __init__(self, deferred: bool = True):
        self.response, self.followup = _Recorder(), _Recorder()
        self._deferred = deferred
        # ``get_server`` answering None makes ``utils.get_ephemeral`` answer True without a bot.
        self.client = types.SimpleNamespace(get_server=lambda interaction: None)
        self.user = types.SimpleNamespace(display_name="tester")
        self.channel = None
        self.channel_id = 1
        self.response.is_done = lambda: self._deferred


MISSIONS = os.path.abspath("/missions")


def m(name: str) -> str:
    """*name* below MISSIONS, normalised the way the server normalises a mission path."""
    return os.path.normpath(os.path.join(MISSIONS, name))


class _Server:
    """The writer the command calls, plus the reads each body makes before reaching it."""

    def __init__(self, *, status=Status.LOADING, missions=(m("A.miz"),), writer="addMission",
                 busy=True):
        self.status = status
        self.current_mission = None
        self.on_empty = None
        self.restart_pending = False
        self.node = object()
        self.locals = {}
        self.missions = list(missions)
        self.writer = writer
        self.busy = busy
        self.calls: list[tuple] = []

    async def get_missions_dir(self):
        return MISSIONS

    async def getMissionList(self):
        return list(self.missions)

    def is_populated(self):
        return True

    def _answer(self, *call):
        self.calls.append(call)
        if self.busy:
            raise RuntimeError(_refusal())
        return list(self.missions)

    async def addMission(self, path, *, idx=-1, autostart=False):
        return self._answer("addMission", path, autostart)

    async def deleteMission(self, mission_id):
        return self._answer("deleteMission", mission_id)

    async def setStartIndex(self, mission_id):
        return self._answer("setStartIndex", mission_id)


def _command_body(module, name: str):
    """The coroutine behind an ``/mission`` subcommand.

    Read off the ``mission`` group rather than the class attribute: ``delete`` is a name the
    ``watch`` group declares too, and the later class attribute shadows the mission one.
    """
    for command in module.Mission.mission.commands:
        if command.name == name:
            return command.callback
    raise AssertionError(f"/mission {name} is not declared")


# ------------------------------------------------------------------ the helper, over a real double

@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_the_helper_answers_the_writers_sentence_ephemerally_and_nothing_else(mission, deferred):
    """The refusal is spoken once, word for word, ephemerally, on whichever surface still answers."""
    refusal = _refusal()
    interaction = _Interaction(deferred)

    assert await mission.answer_mission_refusal(interaction, RuntimeError(refusal)) is None

    spoken, silent = (interaction.followup, interaction.response) if deferred \
        else (interaction.response, interaction.followup)
    assert spoken.calls == [(refusal, {"ephemeral": True})], spoken.calls
    assert silent.calls == [], "the helper must answer once, on one surface"


@pytest.mark.asyncio
async def test_the_helper_repeats_whatever_sentence_the_writer_raised(mission):
    """No wording of its own: only ``str(ex)`` travels, so a second refusal cannot drift here."""
    interaction = _Interaction(deferred=True)

    await mission.answer_mission_refusal(interaction, RuntimeError("some other refusal"))

    assert interaction.followup.calls == [("some other refusal", {"ephemeral": True})]
    assert interaction.response.calls == []


# ------------------------------------------------------------- the three shipped command bodies

@pytest.mark.asyncio
async def test_mission_add_answers_the_refusal_and_raises_nothing(mission):
    """``/mission add`` on a busy server answers the sentence; no exception reaches the global handler."""
    refusal = _refusal()
    interaction = _Interaction(deferred=True)
    server = _Server(status=Status.LOADING)

    assert await _command_body(mission, "add")(types.SimpleNamespace(), interaction, server,
                                              "A.miz", False) is None

    assert server.calls == [("addMission", m("A.miz"), False)], server.calls
    assert interaction.followup.calls == [(refusal, {"ephemeral": True})], interaction.followup.calls
    # the command's own defer, and nothing else on the initial-response surface
    assert interaction.response.calls == [("__defer__", {"ephemeral": True})], interaction.response.calls


@pytest.mark.asyncio
async def test_mission_delete_answers_the_refusal_and_raises_nothing(mission, monkeypatch):
    """``/mission delete`` answers the sentence instead of the timeout-only handler's repr."""
    refusal = _refusal()
    interaction = _Interaction(deferred=True)
    server = _Server(status=Status.LOADING, missions=(m("A.miz"), m("B.miz")))

    async def confirmed(ctx, question, **kwargs):
        return True

    monkeypatch.setattr(mission.utils, "yn_question", confirmed)

    assert await _command_body(mission, "delete")(types.SimpleNamespace(), interaction, server,
                                                 0) is None

    assert server.calls == [("deleteMission", 1)], server.calls
    assert interaction.followup.calls == [(refusal, {"ephemeral": True})], interaction.followup.calls


@pytest.mark.asyncio
async def test_mission_set_answers_the_refusal_and_raises_nothing(mission, monkeypatch):
    """``/mission set`` reaches ``setStartIndex`` through ``_load`` and answers the sentence there."""
    refusal = _refusal()
    interaction = _Interaction(deferred=True)
    # the body's own gate allows RUNNING; the writer is asked after a prompt, when the state may
    # already have flipped, which is what the writer's refusal is for
    server = _Server(status=Status.RUNNING, missions=(m("A.miz"), m("B.miz")),
                     writer="setStartIndex")

    async def later(ctx, question, **kwargs):
        return "later"

    async def picked(interaction, node, **kwargs):
        return types.SimpleNamespace(kwargs={}, values=[])

    monkeypatch.setattr(mission.utils, "populated_question", later)
    monkeypatch.setattr(mission, "launch_presets", picked)

    assert await mission.Mission._load(types.SimpleNamespace(), interaction, server, 1) is None

    assert server.calls == [("setStartIndex", 2)], server.calls
    assert interaction.followup.calls == [(refusal, {"ephemeral": True})], interaction.followup.calls


# ------------------------------------------------------------------------------- the controls

@pytest.mark.asyncio
async def test_mission_add_still_answers_the_success_when_the_writer_accepts(mission, monkeypatch):
    """The new branch must not capture the ordinary path."""
    interaction = _Interaction(deferred=True)
    server = _Server(status=Status.STOPPED, busy=False)

    async def declined(ctx, question, **kwargs):
        return False

    monkeypatch.setattr(mission.utils, "yn_question", declined)

    await _command_body(mission, "add")(types.SimpleNamespace(), interaction, server, "A.miz", False)

    assert server.calls == [("addMission", m("A.miz"), False)], server.calls
    assert interaction.followup.calls == [
        (f'Mission "{mission.utils.escape_string("A.miz")}" added.', {"ephemeral": True})], \
        interaction.followup.calls


@pytest.mark.asyncio
async def test_mission_delete_still_answers_the_success_when_the_writer_accepts(mission, monkeypatch):
    """The inner branch keeps the outer timeout handler and the ordinary reply intact."""
    interaction = _Interaction(deferred=True)
    server = _Server(status=Status.STOPPED, missions=(m("A.miz"), m("B.miz")), busy=False)
    answers = iter([True, False])

    async def confirmed(ctx, question, **kwargs):
        return next(answers)

    monkeypatch.setattr(mission.utils, "yn_question", confirmed)

    await _command_body(mission, "delete")(types.SimpleNamespace(), interaction, server, 0)

    assert server.calls == [("deleteMission", 1)], server.calls
    assert interaction.followup.calls == [('Mission "A" removed from list.', {"ephemeral": True})], \
        interaction.followup.calls


@pytest.mark.asyncio
async def test_mission_set_still_answers_the_deferred_load_when_the_writer_accepts(mission, monkeypatch):
    """The pointer write keeps its place in the deferred-load path."""
    interaction = _Interaction(deferred=True)
    server = _Server(status=Status.RUNNING, missions=(m("A.miz"), m("B.miz")),
                     busy=False)

    async def later(ctx, question, **kwargs):
        return "later"

    async def picked(interaction, node, **kwargs):
        return types.SimpleNamespace(kwargs={}, values=[])

    monkeypatch.setattr(mission.utils, "populated_question", later)
    monkeypatch.setattr(mission, "launch_presets", picked)

    await mission.Mission._load(types.SimpleNamespace(), interaction, server, 1)

    assert server.calls == [("setStartIndex", 2)], server.calls
    assert server.on_empty["method"] == "load"
    assert [content for content, _ in interaction.followup.calls] == \
        ['Mission B will be loaded when server is empty or on the next restart.']


# ----------------------------------------------------------------------------- the ratchet

def _answers_with_the_helper(handler: ast.ExceptHandler) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "answer_mission_refusal"
        for node in ast.walk(handler)
    )


def _catches_runtime_error(handler: ast.ExceptHandler) -> bool:
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(name, ast.Name) and name.id == "RuntimeError" for name in names)


def _writer_call_sites(source: str) -> dict[str, list]:
    """Every ``await server.<writer>(...)`` in *source*, split by how its ``RuntimeError`` is answered."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing_function(node: ast.AST) -> str:
        parent = parents.get(node)
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return parent.name
            parent = parents.get(parent)
        return "<module>"

    writers = {name for _, name in WRITER_CALL_SITES}
    found: dict[str, list] = {"answered": [], "unguarded": [], "re_raised": []}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "server"):
            continue
        if node.func.attr not in writers:
            continue
        site = (enclosing_function(node), node.func.attr)

        branch = None
        parent = parents.get(node)
        while parent is not None:
            if isinstance(parent, ast.Try):
                branch = next((handler for handler in parent.handlers
                               if _catches_runtime_error(handler)), None)
                if branch is not None:
                    break
            parent = parents.get(parent)
        if branch is None or not _answers_with_the_helper(branch):
            found["unguarded"].append(site)
        elif isinstance(branch.body[-1], ast.Raise):
            found["re_raised"].append(site)
        elif not isinstance(branch.body[-1], ast.Return):
            # a branch that answers and then falls through would run the rest of the command
            found["unguarded"].append(site)
        else:
            found["answered"].append(site)
    return found


def test_every_direct_writer_call_answers_the_busy_refusal_itself():
    """Six call sites, six guarded branches — a writer added without one shows up here."""
    found = _writer_call_sites(COMMANDS.read_text(encoding="utf-8"))

    assert found["unguarded"] == [], f"writer calls with no answering branch: {found['unguarded']}"
    assert found["re_raised"] == [], f"branches that re-raise into the global handler: {found['re_raised']}"
    assert set(found["answered"]) == WRITER_CALL_SITES


def test_the_three_replacement_call_sites_call_the_helper():
    """One assertion per ``replaceMission`` call site, read off this module's own source."""
    found = _writer_call_sites(COMMANDS.read_text(encoding="utf-8"))
    answered = set(found["answered"])
    for function in ("modify", "rollback", "persistence"):
        assert (function, "replaceMission") in answered, (
            f"{function}() calls replaceMission without a RuntimeError branch that answers the "
            f"refusal; unguarded: {found['unguarded']}")


def test_the_helper_is_the_only_thing_the_branches_call():
    """The sentence is spoken in one place: no branch builds a message of its own."""
    tree = ast.parse(COMMANDS.read_text(encoding="utf-8"))
    handlers = [
        handler
        for node in ast.walk(tree) if isinstance(node, ast.Try)
        for handler in node.handlers if _catches_runtime_error(handler)
    ]
    assert len(handlers) == len(WRITER_CALL_SITES)
    for handler in handlers:
        calls = [node for node in ast.walk(handler) if isinstance(node, ast.Call)]
        assert [getattr(call.func, "id", None) for call in calls] == ["answer_mission_refusal"], \
            ast.unparse(handler)
        assert not [node for node in ast.walk(handler) if isinstance(node, ast.Constant)], \
            "a branch must not carry a message of its own"
