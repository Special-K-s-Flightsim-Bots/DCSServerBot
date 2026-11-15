"""A load that runs no extensions applies no preset, so the record must not survive it.

The server page renders its "launched with weather preset" label from the ``servers`` row
(``launched_preset`` / ``launched_preset_temporary``), which the NODE writes inside
``ServerImpl.apply_mission_changes`` and clears on any load that carries no preset. But
``apply_mission_changes`` is only CALLED when ``modify_mission`` is true: a load with the extensions
unticked takes the ``elif use_orig`` / ``else`` branch of ``loadMission`` (or the ``else`` of
``startup``) and never touches the record. The label then names a preset the load never applied.

Contract pinned here, one section per acceptance item:

* a ``modify_mission=False`` load of either kind (``loadMission`` and ``startup``) CLEARS a previously
  applied preset, so the label is absent afterwards — the RED case this file was written for;
* a load WITH a preset still records it (named and temporary) — the clearing must not swallow it;
* a load carrying a NAME the instance does not carry records nothing.

The real ``ServerImpl.loadMission`` / ``ServerImpl.startup`` are driven over a double wired with only
the attributes they touch, and a recording pool stands in for the node's ``asyncpg`` pool. The row
state the pool keeps is fed to the page's OWN read model (``launched_preset_view``), so the assertion
is on the label, not on the branch that decides it.
"""
from __future__ import annotations

import asyncio
import logging
import types

import pytest

from core import Status
from core.data.impl import serverimpl
from core.data.impl.serverimpl import ServerImpl
from services.webservice import readmodels

try:
    from extensions.mizedit.extension import MizEdit
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)

NODE = "dcs-a-01"
SERVER = "SRS-1"

BEFORE = """\
Configured:
  modify:
    weather:
      clouds:
        base: 1000
"""
AFTER = BEFORE + """\
Fresh:
  modify:
    weather:
      clouds:
        base: 500
"""


# --------------------------------------------------------------------------------- the recording pool

class RowState:
    """The ``servers`` row the record write lands on — the state the label reads."""

    def __init__(self, preset: str | None = None, temporary: bool = False):
        self.preset = preset
        self.temporary = temporary


class RecordingPool:
    def __init__(self, row: RowState | None = None):
        self.statements: list[tuple[str, tuple | None]] = []
        self.row = row if row is not None else RowState()

    def connection(self):
        return _RecordingConn(self)


class _RecordingConn:
    def __init__(self, pool: RecordingPool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        normalised = " ".join(sql.split())
        self._pool.statements.append((normalised, params))
        if normalised.startswith("UPDATE servers SET launched_preset") and params:
            name, temporary, _server = params
            self._pool.row.preset = name
            self._pool.row.temporary = temporary
        return None


def record_writes(pool: RecordingPool) -> list[tuple[str, tuple | None]]:
    """Every statement that wrote the launch record, in order."""
    return [(sql, params) for sql, params in pool.statements
            if sql.startswith("UPDATE servers SET launched_preset")]


def label_of(row: RowState):
    """The label the server page would render for this row — the page's OWN read model."""
    return readmodels.launched_preset_view(row.preset, row.temporary)


# --------------------------------------------------------------------------------- the node double

def build_mizedit(presets_file, *, settings="Configured") -> MizEdit:
    node = types.SimpleNamespace(config_dir=str(presets_file.parent),
                                 config={"validation": "off"}, pool=None)
    config: dict = {"presets": str(presets_file)}
    if settings is not None:
        config["settings"] = settings
    server = types.SimpleNamespace(status=Status.RUNNING, name=SERVER, node=node)
    return MizEdit(server, config)  # type: ignore[arg-type] - a deliberately minimal Server double


def build_server(mission: str, *, pool: RecordingPool, status=Status.RUNNING,
                 extensions=None) -> ServerImpl:
    """The REAL ``ServerImpl`` wired with only the attributes the load path touches."""
    srv = object.__new__(ServerImpl)
    srv.name = SERVER
    srv.node = types.SimpleNamespace(name=NODE, master=True,
                                     locals={"DCS": {"desanitize": True}}, installation=None)
    srv.log = logging.getLogger("test.load.clears")
    srv.apool = pool
    srv._settings = {"missionList": [mission], "listStartIndex": 1}
    srv.current_mission = None
    srv._status = status
    srv.status_change = asyncio.Event()
    srv._lock = asyncio.Lock()
    srv.is_remote = False
    srv.bus = types.SimpleNamespace(loop=None)
    srv.locals = {}
    srv.extensions = extensions or {}

    async def noop(*args, **kwargs):
        return None

    async def start_mission(message, *args, **kwargs):
        return {"result": True}

    srv.wait_for_status_change = noop
    srv.send_to_dcs_sync = start_mission

    async def replace_mission(idx, filename):
        return srv.settings["missionList"]

    srv.replaceMission = replace_mission
    return srv


@pytest.fixture
def no_write(monkeypatch):
    """Replace MizEdit's file writer so the harness never touches a real preset file."""
    async def recorder(server, filename, preset, debug=False):
        return None

    monkeypatch.setattr(MizEdit, "apply_presets", staticmethod(recorder))
    return recorder


@pytest.fixture
def mission(tmp_path):
    f = tmp_path / "Training.miz"
    f.write_bytes(b"MIZ")
    return str(f)


# --------------------------------------- 1. a load that applied nothing must not keep the record

@pytest.mark.asyncio
async def test_a_load_without_extensions_clears_a_previously_applied_preset(mission):
    """RED on the unfixed tree: the record survives the load and the page keeps the stale label."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool)

    await ServerImpl.loadMission(srv, mission, modify_mission=False, use_orig=False)

    assert label_of(pool.row) is None, \
        "the page must not claim a preset a load with the extensions off never applied"


@pytest.mark.asyncio
async def test_a_start_without_extensions_clears_a_previously_applied_preset(mission, monkeypatch):
    """The other load shape: ``startup`` with the extensions unticked must clear the record too."""
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

    await ServerImpl.startup(srv, modify_mission=False)

    assert label_of(pool.row) is None, \
        "the page must not claim a preset a start with the extensions off never applied"


# --------------------------------------- 2. positive controls: a load that DID apply a preset keeps it

@pytest.mark.asyncio
async def test_a_load_with_a_named_preset_still_records_it(mission, no_write, tmp_path):
    """The clearing must not swallow a preset the load really applied."""
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER, encoding="utf-8")
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool, extensions={"MizEdit": build_mizedit(presets)})

    await ServerImpl.loadMission(srv, mission, modify_mission=True, use_orig=False, preset="Fresh")

    assert label_of(pool.row) is not None
    assert pool.row.preset == "Fresh" and pool.row.temporary is False


@pytest.mark.asyncio
async def test_a_load_with_a_temporary_preset_still_records_it(mission, no_write, tmp_path):
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER, encoding="utf-8")
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool, extensions={"MizEdit": build_mizedit(presets)})
    doc = {"modify": {"weather": {"clouds": {"base": 250}}}}

    await ServerImpl.loadMission(srv, mission, modify_mission=True, use_orig=False, preset=doc)

    assert label_of(pool.row) is not None
    assert pool.row.preset is None and pool.row.temporary is True


@pytest.mark.asyncio
async def test_a_load_with_a_name_the_instance_does_not_carry_records_nothing(mission, no_write, tmp_path):
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER, encoding="utf-8")
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool, extensions={"MizEdit": build_mizedit(presets)})

    await ServerImpl.loadMission(srv, mission, modify_mission=True, use_orig=False, preset="Nope")

    assert label_of(pool.row) is None, \
        "a name the instance does not carry applied nothing, so the label must be absent"


# --------------------------------------- 3. the write is on the node, and only one per load

@pytest.mark.asyncio
async def test_the_load_writes_the_record_exactly_once(mission, no_write, tmp_path):
    """One load, one record write — the fix must not add a second statement beside the extension's."""
    presets = tmp_path / "presets.yaml"
    presets.write_text(AFTER, encoding="utf-8")
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool, extensions={"MizEdit": build_mizedit(presets)})

    await ServerImpl.loadMission(srv, mission, modify_mission=True, use_orig=False, preset="Fresh")

    assert len(record_writes(pool)) == 1, record_writes(pool)
    assert record_writes(pool)[0][1] == ("Fresh", False, SERVER)
