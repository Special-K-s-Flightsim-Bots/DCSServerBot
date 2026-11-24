"""The mission-weather READ: ``read_mission_weather``, its resolution and its ONE mapping home.

Contract pinned here:

* the mission is resolved against the SERVER'S OWN ``missionList`` (a 1-based index, like
  ``_resolve_mission_id`` / ``set_active_mission``, or a logical name) or the mission
  ``get_current_mission_file`` names; a raw PATH is REFUSED before anything is asked of the node;
* the bytes come over ``server.node.read_file``, are materialised to a temp file and parsed with
  ``MizFile``;
* the document is built by ``MizFile.preset_document`` — the SAME mapping ``/mission save_preset``
  writes — and it states the haze the way DCS applies it: ONE of dust or fog, never a fabricated fog
  map (which a reader would take for an enabled fog);
* every failure (no current mission, an out-of-range index, a missing file, permission denied, a
  timeout, a raised node, a failure code, an unsupported ``.miz``, a ``.miz`` with no weather)
  answers a typed refusal, never a document of zeros or defaults;
* a READ: the action writes no audit entry.
"""
from __future__ import annotations

import os

import ast
import sys
import types
import zipfile
from pathlib import Path

import luadata
import pytest


import core.actions as actions_module
from core import Status
from core.actions import ActionContext, action_registry, discover_actions
from core.mizfile import MizFile
from core.services.registry import ServiceRegistry
from services.servicebus import ServiceBus

#: a mission whose weather every value of the mapping is read from — the fixture's own document.
MISSION = {
    "theatre": "Caucasus",
    "start_time": 28800,
    "date": {"Year": 2024, "Month": 6, "Day": 1},
    "weather": {
        "season": {"temperature": 15},
        "atmosphere_type": 0,
        "wind": {"atGround": {"speed": 3, "dir": 200},
                 "at2000": {"speed": 5, "dir": 210},
                 "at8000": {"speed": 8, "dir": 220}},
        "groundTurbulence": 1,
        "qnh": 760,
        "clouds": {"preset": "Preset1"},
        "enable_dust": False,
        "dust_density": 0,
        "enable_fog": False,
        "halo": {"preset": "off"},
    },
}

#: THE EXPECTED DOCUMENT — the exact dict ``preset_document`` produces for :data:`MISSION`. Pinned by
#: value, so a renamed key or a changed haze rule fails here. The mission carries NEITHER dust nor
#: fog, so it states ``enable_fog`` false and carries NO ``fog`` key: a fabricated empty fog map is a
#: fog the mission does not have.
EXPECTED_DOCUMENT = {
    "start_time": 28800,
    "date": "2024-06-01",
    "temperature": 15,
    "clouds": {"preset": "Preset1"},
    "wind": {"atGround": {"speed": 3, "dir": 200},
             "at2000": {"speed": 5, "dir": 210},
             "at8000": {"speed": 8, "dir": 220}},
    "groundTurbulence": 1,
    "enable_dust": False,
    "dust_density": 0,
    "qnh": 760,
    "enable_fog": False,
    "halo": {"preset": "off"},
}


def build_miz(path: Path, mission: dict | None = None, *, raw: str | None = None) -> str:
    """Write a ``.miz`` archive whose ``mission`` entry is *mission* (a dict serialised as Lua).

    ``options``/``warehouses`` are written too: ``MizFile._load`` opens them unconditionally, and a
    real mission archive always carries them.
    """
    body = raw if raw is not None else ("mission = " + luadata.serialize(
        mission if mission is not None else MISSION, 'utf-8', indent='\t', indent_level=0))
    with zipfile.ZipFile(path, "w") as zout:
        zout.writestr("mission", body)
        zout.writestr("options", "options = {}")
        zout.writestr("warehouses", "warehouses = {}")
    return str(path)


# --------------------------------------------------------------------------------- test doubles

#: ONE host spelling of the fake mission tree. The action hands the CONFIGURED value through
#: untouched, so both the configured list and the expectations must be spelled the same way the bot
#: spells a path — ``"/missions"`` alone is not an absolute path on Windows.
MISSIONS = os.path.abspath("/missions")


def m(*parts: str) -> str:
    """*parts* joined below :data:`MISSIONS`, normalised the way the bot normalises a path."""
    return os.path.normpath(os.path.join(MISSIONS, *parts))


class NodeDouble:
    """A server's node: it hands back the bytes a read is asked for, or raises the installed error."""

    def __init__(self, name="dcs-a-01", *, content=b"", error=None, dcs_version="2.9.14",
                 installation=""):
        self.name = name
        self.master = True
        self.dcs_version = dcs_version
        self.locals = {"DCS": {"installation": installation}} if installation else {}
        self.content = content
        self.error = error
        self.reads: list[str] = []

    async def read_file(self, path):
        self.reads.append(path)
        if self.error is not None:
            raise self.error
        return self.content


class ServerDouble:
    """A ``Server`` double: the list, the missions dir and the current mission the action reads."""

    def __init__(self, name="SRS-1", *, node=None, configured=(), current=None,
                 missions_dir=MISSIONS):
        self.name = name
        self.display_name = name
        self.status = Status.RUNNING
        self.node = node if node is not None else NodeDouble()
        self.configured = list(configured)
        self.current = current
        self.missions_dir = missions_dir

    async def getMissionList(self):
        return list(self.configured)

    async def get_missions_dir(self):
        return self.missions_dir

    async def get_current_mission_file(self):
        return self.current


# ---------------------------------------------------------------------------------- the harness

@pytest.fixture
def actions():
    """The mission plugin's actions, freshly discovered, with the process registry restored after."""
    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    sys.modules.pop("plugins.mission.actions", None)
    discover_actions(["mission"])
    registry = action_registry()
    assert "read_mission_weather" in registry
    try:
        yield registry
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.mission.actions", None)


@pytest.fixture
def miz_service(monkeypatch, tmp_path):
    """A ``ServiceBus`` whose ``node`` is a node double, so ``MizFile`` can be built in-process.

    ``MizFile.__init__`` reads the process's own node (``ServiceRegistry.get(ServiceBus).node``); the
    action then points the parsed file at the SERVER's node for the DCS-version-dependent fog keys.
    """
    node = NodeDouble(installation=str(tmp_path))

    def fake_get(cls, t):
        return types.SimpleNamespace(node=node) if t is ServiceBus else None

    monkeypatch.setattr(ServiceRegistry, "get", classmethod(fake_get))
    return node


def ctx_for(server) -> ActionContext:
    return ActionContext(node=None, bus=types.SimpleNamespace(servers={server.name: server}))


async def run(actions, server, **params):
    return await actions["read_mission_weather"](ctx_for(server), server_name=server.name, **params)


def mission_payload(tmp_path: Path, name: str = "Training.miz") -> bytes:
    return Path(build_miz(tmp_path / name)).read_bytes()


# ------------------------------------------------------------------ 1. the read, and its name

@pytest.mark.asyncio
async def test_reads_the_current_missions_weather_and_names_it(actions, miz_service, tmp_path):
    """The mission ``get_current_mission_file`` names is read over the node and its weather returned."""
    node = NodeDouble(content=mission_payload(tmp_path))
    server = ServerDouble(node=node, current=m("Training.miz"))

    result = await run(actions, server)

    assert result.success is True
    assert result.status == 200
    assert result.mission_name == "Training.miz"
    assert result.document == EXPECTED_DOCUMENT
    assert node.reads == [m("Training.miz")], "the current mission's file is what was read"
    assert "audit" not in result.data, "a READ writes no audit entry"


@pytest.mark.asyncio
async def test_the_current_mission_is_named_by_its_logical_file_name(actions, miz_service, tmp_path):
    """A ``.orig`` copy is named without the marker — the dialog's line reads ``<name>.miz``."""
    node = NodeDouble(content=mission_payload(tmp_path))
    server = ServerDouble(node=node, current=m(".dcssb", "Training.miz.orig"))

    result = await run(actions, server)

    assert result.success is True
    assert result.mission_name == "Training.miz"


@pytest.mark.asyncio
async def test_a_one_based_index_names_a_configured_mission(actions, miz_service, tmp_path):
    """An INT is the 1-based ``missionList`` position — the identifier ``set_active_mission`` takes."""
    node = NodeDouble(content=mission_payload(tmp_path))
    server = ServerDouble(node=node, configured=[m("A.miz"), m("Training.miz")])

    result = await run(actions, server, mission=2)

    assert result.success is True
    assert result.mission_name == "Training.miz"
    assert node.reads == [m("Training.miz")]


@pytest.mark.asyncio
async def test_a_numeric_string_is_the_same_one_based_index(actions, miz_service, tmp_path):
    """A NUMERIC STRING (what a route query parameter always is) is the SAME 1-based index."""
    node = NodeDouble(content=mission_payload(tmp_path))
    server = ServerDouble(node=node, configured=[m("A.miz"), m("Training.miz")])

    result = await run(actions, server, mission="2")

    assert result.success is True
    assert result.mission_name == "Training.miz"
    assert node.reads == [m("Training.miz")]


@pytest.mark.asyncio
async def test_a_logical_name_resolves_against_the_servers_own_list(actions, miz_service, tmp_path):
    """A NAME (with or without ``.miz``) resolves against the server's own list."""
    node = NodeDouble(content=mission_payload(tmp_path))
    server = ServerDouble(node=node, configured=[m("A.miz"), m("Training.miz")])

    result = await run(actions, server, mission="Training")

    assert result.success is True
    assert result.mission_name == "Training.miz"
    assert node.reads == [m("Training.miz")]


@pytest.mark.asyncio
async def test_an_index_out_of_range_is_refused_without_a_read(actions, miz_service):
    node = NodeDouble()
    server = ServerDouble(node=node, configured=[m("A.miz")])

    result = await run(actions, server, mission=5)

    assert result.success is False
    assert result.status == 404
    assert node.reads == []


@pytest.mark.asyncio
async def test_a_name_the_list_does_not_carry_is_refused_without_a_read(actions, miz_service):
    node = NodeDouble()
    server = ServerDouble(node=node, configured=[m("A.miz")])

    result = await run(actions, server, mission="Nope.miz")

    assert result.success is False
    assert result.status == 404
    assert node.reads == []


@pytest.mark.asyncio
async def test_a_server_with_no_current_mission_is_refused(actions, miz_service):
    node = NodeDouble()
    server = ServerDouble(node=node, current=None)

    result = await run(actions, server)

    assert result.success is False
    assert result.status == 404
    assert node.reads == []


# ---------------------------------------------------- 2. the traversal refusal (the risk to close)

@pytest.mark.parametrize("bad", [
    "../evil.miz",              # a relative escape
    "/etc/passwd",              # an absolute path
    "sub/evil.miz",             # a directory component (posix)
    "sub\\evil.miz",            # a directory component (windows)
    "..",                       # the parent directory
    ".",                        # the current directory
    "../../missions/A.miz",     # an escape that names a real configured mission
])
@pytest.mark.asyncio
async def test_a_raw_path_is_refused_before_the_node_is_asked(actions, miz_service, bad):
    """THE PROPERTY THIS CARD EXISTS TO KEEP: a path never reaches ``read_file``.

    The assertion on ``reads`` is the point — a 400 that had already opened a path would look
    identical in the result.
    """
    node = NodeDouble(content=b"MISSION")
    server = ServerDouble(node=node, configured=[m("A.miz")])

    result = await run(actions, server, mission=bad)

    assert result.success is False
    assert result.status == 400
    assert "never by a path" in result.message
    assert node.reads == [], f"a refused path was still asked of the node: {node.reads}"


# ------------------------------------------------------------- 3. every failure is its own answer

@pytest.mark.parametrize("error,status", [
    (FileNotFoundError(), 404),
    (PermissionError(), 403),
    (TimeoutError(), 504),
    (RuntimeError("the transport exploded"), 502),
])
@pytest.mark.asyncio
async def test_every_node_read_failure_answers_its_own_sentence(actions, miz_service, error, status):
    """The node's own failure mode is reported, and NO document is invented."""
    node = NodeDouble(error=error)
    server = ServerDouble(node=node, current=m("Training.miz"))

    result = await run(actions, server)

    assert result.success is False
    assert result.status == status
    assert result.document == {}
    assert result.message, "a failure carries its own sentence"


@pytest.mark.asyncio
async def test_a_failure_code_is_never_content(actions, miz_service):
    """``read_file`` answers ``bytes | int`` and an ``int`` is a FAILURE CODE, never mission bytes."""
    node = NodeDouble(content=7)  # type: ignore[arg-type] - read_file answers bytes | int
    server = ServerDouble(node=node, current=m("Training.miz"))

    result = await run(actions, server)

    assert result.success is False
    assert result.status == 502
    assert result.document == {}


@pytest.mark.asyncio
async def test_an_unparseable_archive_answers_a_failure(actions, miz_service):
    """A ``.miz`` ``MizFile`` cannot open is refused — never a document of zeros."""
    node = NodeDouble(content=b"this is not a zip archive")
    server = ServerDouble(node=node, current=m("Broken.miz"))

    result = await run(actions, server)

    assert result.success is False
    assert result.status == 422
    assert result.document == {}


@pytest.mark.asyncio
async def test_a_mission_without_weather_answers_a_failure(actions, miz_service, tmp_path):
    """A valid archive whose weather cannot be read is refused — never a document of zeros."""
    no_weather = {k: v for k, v in MISSION.items() if k != "weather"}
    path = build_miz(tmp_path / "NoWeather.miz", no_weather)
    node = NodeDouble(content=Path(path).read_bytes())
    server = ServerDouble(node=node, current=m("NoWeather.miz"))

    result = await run(actions, server)

    assert result.success is False
    assert result.status == 422
    assert result.document == {}


# ------------------------------------------------------- 4. the ONE mapping home (the extraction)

def test_the_document_states_the_haze_the_way_dcs_applies_it(miz_service, tmp_path):
    """THE FOUR HAZE STATES, each stated by the keys the mission really carries.

    A mission has dust, or fog (``fog2`` on 2.9.10+, the older ``enable_fog``/``fog`` pair before it),
    or neither — and the document says exactly that, so a reader cannot see a fog the mission lacks.
    """
    base = MISSION["weather"]

    # 1. DUST: the switch and its visibility, and NO fog key at all.
    dust = build_miz(tmp_path / "Dust.miz", dict(MISSION, weather=dict(
        base, enable_dust=True, dust_density=3048)))
    miz = MizFile(dust)
    miz.node = NodeDouble(dcs_version="2.9.14")
    document = miz.preset_document()
    assert document["enable_dust"] is True and document["dust_density"] == 3048
    assert document["enable_fog"] is False, "the fog side is stated off beside the dust switch"
    assert "fog" not in document, "a dust mission carries no fog map, so the document states none"

    # 2. FOG, DCS's own build: ``fog2`` mode 2 states the mode and no keyframes.
    auto = build_miz(tmp_path / "Auto.miz", dict(MISSION, weather=dict(
        base, fog2={"mode": 2})))
    miz = MizFile(auto)
    miz.node = NodeDouble(dcs_version="2.9.14")
    document = miz.preset_document()
    assert document["enable_fog"] is True and document["fog"] == {"mode": "auto"}
    assert document["enable_dust"] is False

    # 3. FOG, manual keyframes: ``fog2`` mode 4 carries them as time-keyed rows.
    manual = build_miz(tmp_path / "Manual.miz", dict(MISSION, weather=dict(
        base, fog2={"mode": 4, "manual": [
            {"time": 0, "thickness": 200, "visibility": 1500},
            {"time": 300, "thickness": 500, "visibility": 400}]})))
    miz = MizFile(manual)
    miz.node = NodeDouble(dcs_version="2.9.14")
    document = miz.preset_document()
    assert document["enable_fog"] is True
    assert document["fog"] == {0: {"thickness": 200, "visibility": 1500},
                               300: {"thickness": 500, "visibility": 400}}, "the keyframes are kept"

    # 4. THE OLD FOG: no ``fog2``, ``enable_fog`` true, the values in the ``fog`` map — read the same
    # way by a 2.9.10+ node, because the FILE is what is being described, not the build reading it.
    legacy = build_miz(tmp_path / "Legacy.miz", dict(MISSION, weather=dict(
        base, enable_fog=True, fog={"thickness": 300, "visibility": 2000})))
    for version in ("2.7.18", "2.9.14"):
        miz = MizFile(legacy)
        miz.node = NodeDouble(dcs_version=version)
        document = miz.preset_document()
        assert document["enable_fog"] is True, version
        assert document["fog"] == {0: {"thickness": 300, "visibility": 2000}}, \
            f"the old fog's own values are shown as the single keyframe ({version})"

    # 5. NEITHER: no fog key, and the base document pinned for the mission without either.
    plain = MizFile(build_miz(tmp_path / "Training.miz"))
    plain.node = NodeDouble(dcs_version="2.9.14")
    assert plain.preset_document() == EXPECTED_DOCUMENT


def test_a_mission_carrying_both_switches_is_read_as_dust(miz_service, tmp_path):
    """A converted file may carry dust AND a ``fog2`` map; DCS applies one, and dust is the choice.

    The dust switch wins, so the document states dust alone — the fog keys are not written beside it.
    """
    both = dict(MISSION, weather=dict(MISSION["weather"], enable_dust=True, dust_density=5,
                                      fog2={"mode": 4, "manual": [
                                          {"time": 0, "thickness": 100, "visibility": 500}]}))
    miz = MizFile(build_miz(tmp_path / "Both.miz", both))
    miz.node = NodeDouble(dcs_version="2.9.14")

    document = miz.preset_document()

    assert document["enable_dust"] is True
    assert document["dust_density"] == 5
    assert document["enable_fog"] is False and "fog" not in document


# ------------------------------------------- 5. ``/mission save_preset`` writes the SAME document

#: the mission plugin's commands module — the home of ``/mission save_preset``.
COMMANDS_MODULE = Path(__file__).resolve().parents[1] / "plugins" / "mission" / "commands.py"

#: the top-level preset keys the document carries — the inline mapping's key list, plus the ``fog``
#: map a fog mission adds. ``save_preset`` must spell none of them itself.
INLINE_KEYS = frozenset(EXPECTED_DOCUMENT) | {"fog"}


def _save_preset_function():
    """The AST of ``save_preset`` in ``plugins/mission/commands.py``."""
    tree = ast.parse(COMMANDS_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "save_preset":
            return node
    raise AssertionError("save_preset is not in plugins/mission/commands.py")


def test_save_preset_assigns_the_shared_document_and_carries_no_inline_mapping():
    """THE EXTRACTION'S BLAST RADIUS, pinned by source.

    ``/mission save_preset`` must assign ``miz.preset_document()`` to its preset key and must NOT
    carry the inline key list any more — the second list that could drift from the console's read.
    """
    function = _save_preset_function()
    source = ast.get_source_segment(COMMANDS_MODULE.read_text(encoding="utf-8"), function) or ""
    assert "miz.preset_document()" in source, "the command does not use the shared mapping"
    for key in INLINE_KEYS:
        assert f'"{key}"' not in source, f"the inline key {key!r} is still spelled in save_preset"


class _Interaction:
    """The minimum of a Discord interaction ``save_preset`` touches."""

    def __init__(self):
        self.client = types.SimpleNamespace(get_server=lambda interaction: None)
        self.channel = None
        self.sent: list[str] = []
        self.response = types.SimpleNamespace(defer=self._defer)
        self.followup = types.SimpleNamespace(send=self._send)

    async def _defer(self, **kwargs):
        return None

    async def _send(self, content, **kwargs):
        self.sent.append(content)


@pytest.mark.asyncio
async def test_save_preset_writes_the_shared_document(miz_service, tmp_path):
    """``/mission save_preset`` writes EXACTLY ``MizFile.preset_document`` — the pin on its output.

    The plugin's ``commands`` module needs the bot's full dependency set, so the runtime pin is
    skipped where that set is incomplete (the source pin above always runs).
    """
    pytest.importorskip("openpyxl", reason="plugins/mission/commands.py needs the bot's full deps")
    from core.utils.yaml_helper import new_yaml

    from plugins.mission.commands import Mission

    miz_path = build_miz(tmp_path / "Training.miz")
    mission = object.__new__(Mission)
    mission.node = types.SimpleNamespace(config_dir=str(tmp_path))  # type: ignore[assignment]
    server = types.SimpleNamespace(name="SRS-1", display_name="SRS-1",
                                   current_mission=types.SimpleNamespace(filename=miz_path))
    interaction = _Interaction()

    await Mission.save_preset.callback(  # type: ignore[attr-defined]
        mission, interaction, server=server, name="Weather")

    written = new_yaml().load((tmp_path / "presets.yaml").read_text(encoding="utf-8"))
    miz = MizFile(miz_path)
    miz.node = NodeDouble(dcs_version="2.9.14")
    assert written["Weather"] == miz.preset_document()
    assert written["Weather"] == EXPECTED_DOCUMENT
    assert interaction.sent, "the command confirmed the preset was added"
