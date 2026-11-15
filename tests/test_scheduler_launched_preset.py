"""A scheduled load that applied the configured weather shows the launched-preset label.

The server page renders its "launched with weather preset" label from the ``servers`` row
(``launched_preset`` / ``launched_preset_temporary``), which the NODE writes through the ONE writer
``_record_launched_preset``. The scheduler's ``_run_with_presets`` applies the CONFIGURED presets to the
mission file itself (``server.modifyMission``) and then starts or loads with ``modify_mission=False``.

Contract pinned here, one section per acceptance item:

* a scheduled load whose configured weather was applied leaves the label naming those presets, not
  temporary — on every launch shape the scheduler uses (a running server, a down server, a stopped
  server);
* a scheduled load that applied nothing still leaves NO label;
* a manual pick (the dialog's own path) still records as before;
* the mission-modification chain (``apply_mission_changes`` / MizEdit's ``apply_presets``) does NOT run
  on the scheduler path — asserted through the real ``apply_mission_changes`` and a recorder standing
  in for the file writer;
* a bare ``start()`` (no launch-driven preset) does not touch the record;
* Discord's ``/mission modify`` sequence — apply itself, then load with ``modify_mission=False`` and no
  preset — keeps leaving no label.

The real ``Scheduler._run_with_presets`` is driven over the real ``ServerImpl`` methods it calls, wired
with only the attributes they touch, and a recording pool stands in for the node's ``asyncpg`` pool.
The row the pool keeps is fed to the page's OWN read model (``launched_preset_view``), so the assertion
is on the label, not on the branch that decides it.
"""
from __future__ import annotations

import os

import asyncio
import logging
import types

import pytest

from core import Status
from core.data.impl import serverimpl
from core.data.impl.serverimpl import ServerImpl
from plugins.scheduler.commands import Scheduler
from services.webservice import readmodels

from extensions.mizedit.extension import MizEdit

NODE = "dcs-a-01"
SERVER = "SRS-1"

PRESETS = """\
Configured:
  modify:
    weather:
      clouds:
        base: 1000
Pair:
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


def label_of(row: RowState):
    """The label the server page would render for this row — the page's OWN read model."""
    return readmodels.launched_preset_view(row.preset, row.temporary)


# ------------------------------------------------------------------------- the MizEdit file-writer spy

class Recorder:
    """Stands in for ``MizEdit.apply_presets`` — the file writer — and keeps what it was handed."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def __call__(self, server, filename, preset, debug=False):
        self.calls.append((server, filename, preset, debug))


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(MizEdit, "apply_presets", staticmethod(rec))
    return rec


def build_mizedit(presets_file: str) -> MizEdit:
    """A REAL ``MizEdit`` bound to a node whose preset file is ``presets_file``."""
    node = types.SimpleNamespace(config_dir=presets_file.rsplit("/", 1)[0],
                                 config={"validation": "off"}, pool=None)
    server = types.SimpleNamespace(status=Status.RUNNING, name=SERVER, node=node)
    return MizEdit(server, {"presets": presets_file, "settings": "Configured"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- the server / cog doubles

def build_server(mission: str, *, pool: RecordingPool, status=Status.RUNNING) -> ServerImpl:
    """The REAL ``ServerImpl`` wired with only the attributes its load path touches."""
    srv = object.__new__(ServerImpl)
    srv.name = SERVER
    srv.node = types.SimpleNamespace(name=NODE, master=True, installation=None,
                                     locals={"DCS": {"desanitize": True}}, config_dir=os.path.abspath("/nonexistent"))
    srv.log = logging.getLogger("test.scheduler.preset")
    srv.apool = pool
    srv._settings = {"missionList": [mission], "listStartIndex": 1}
    srv.current_mission = None
    srv._status = status
    srv.status_change = asyncio.Event()
    srv._lock = asyncio.Lock()
    srv.is_remote = False
    srv.bus = types.SimpleNamespace(loop=None)
    srv.locals = {}
    srv.extensions = {}
    srv.modify_calls = []

    async def noop(*args, **kwargs):
        return None

    async def send_to_dcs_sync(message, *args, **kwargs):
        return {"result": 0 if message.get("command") == "start_server" else True}

    async def get_mission_list():
        return srv.settings["missionList"]

    async def modify_mission(filename, preset, use_orig=True):
        srv.modify_calls.append((filename, preset, use_orig))
        return filename

    srv.wait_for_status_change = noop
    srv.send_to_dcs_sync = send_to_dcs_sync
    srv.getMissionList = get_mission_list
    srv.stop = noop
    srv.replaceMission = lambda idx, path: get_mission_list()
    srv._assert_resource_available = noop
    srv._assert_ports_free = noop
    srv.init_extensions = noop
    srv.prepare_extensions = noop
    srv._cleanup_sqlite = lambda: None
    srv.do_startup = lambda: None
    srv.modifyMission = modify_mission

    # the REAL node-side sink, so a load that ran the chain would reach MizEdit's writer
    srv.apply_mission_changes = lambda *a, **k: ServerImpl.apply_mission_changes(srv, *a, **k)

    # the scheduler calls these on the server, so they route to the REAL node-side methods
    srv.startup = lambda *a, **k: ServerImpl.startup(srv, *a, **k)
    srv.loadMission = lambda *a, **k: ServerImpl.loadMission(srv, *a, **k)
    srv.start = lambda *a, **k: ServerImpl.start(srv, *a, **k)
    return srv


def build_scheduler(config_dir) -> types.SimpleNamespace:
    cog = types.SimpleNamespace(
        node=types.SimpleNamespace(config_dir=str(config_dir), name=NODE, locals={}, master=True),
        log=logging.getLogger("test.scheduler.cog"))
    cog.__cog_name__ = "Scheduler"
    cog._preset_file_text = Scheduler._preset_file_text
    for name in ("_announce_refused_pick", "_run_without_presets"):
        setattr(cog, name, types.MethodType(getattr(Scheduler, name), cog))
    return cog


@pytest.fixture(autouse=True)
def desanitized(monkeypatch):
    monkeypatch.setattr(serverimpl.utils, "is_desanitized", lambda node: True)


@pytest.fixture
def mission(tmp_path):
    f = tmp_path / "Training.miz"
    f.write_bytes(b"MIZ")
    return str(f)


@pytest.fixture
def presets_file(tmp_path):
    f = tmp_path / "presets.yaml"
    f.write_text(PRESETS, encoding="utf-8")
    return str(f)


def rconf(presets_file, *names, **extra):
    conf = {"method": "load", "settings": list(names), "presets": presets_file, "use_orig": False}
    conf.update(extra)
    return conf


# ------------------------------- 1. a scheduled load that applied the configured weather labels

@pytest.mark.asyncio
async def test_a_scheduled_load_on_a_running_server_shows_the_label(mission, presets_file):
    """RED on the unfixed tree: the load clears the record, so the page shows no label.

    Failure mode pinned: after the scheduled load the row is empty although the configured weather WAS
    applied to the mission by ``modifyMission``.
    """
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Configured"), 1, False)  # type: ignore[arg-type]

    assert srv.modify_calls, "the configured weather must have been applied to the mission"
    assert label_of(pool.row) is not None, \
        "a scheduled load that applied the configured weather must label the server, not clear it"
    assert pool.row.preset == "Configured" and pool.row.temporary is False


@pytest.mark.asyncio
async def test_a_scheduled_load_on_a_down_server_shows_the_label(mission, presets_file):
    """The startup shape: the same label, and NOT marked temporary."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool, status=Status.SHUTDOWN)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Configured"), 1, False)  # type: ignore[arg-type]

    assert label_of(pool.row) is not None
    assert pool.row.preset == "Configured" and pool.row.temporary is False


@pytest.mark.asyncio
async def test_a_scheduled_load_on_a_stopped_server_shows_the_label(mission, presets_file):
    """The stop-then-start shape (mission_rewrite off): the label must name the applied preset too."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool, status=Status.STOPPED)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Configured"), 1, False)  # type: ignore[arg-type]

    assert label_of(pool.row) is not None
    assert pool.row.preset == "Configured" and pool.row.temporary is False


@pytest.mark.asyncio
async def test_a_combination_of_configured_presets_is_joined(mission, presets_file):
    pool = RecordingPool()
    srv = build_server(mission, pool=pool)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Configured", "Pair"), 1, False)  # type: ignore[arg-type]

    assert pool.row.preset == "Configured, Pair" and pool.row.temporary is False


# ------------------------------- 2. positive control: a scheduled load that applied nothing clears

@pytest.mark.asyncio
async def test_a_scheduled_load_that_applied_nothing_still_clears(mission, presets_file):
    """A name the instance does not carry applies nothing, so the label must be absent."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Nope"), 1, False)  # type: ignore[arg-type]

    assert srv.modify_calls == [], "nothing was found, so nothing may be applied"
    assert label_of(pool.row) is None, \
        "a scheduled load that applied no preset must not keep the previous label"


@pytest.mark.asyncio
async def test_a_scheduled_load_on_a_stopped_server_applying_nothing_still_clears(mission, presets_file):
    """The stop-then-start shape refreshes the record too, so it clears when nothing was applied."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool, status=Status.STOPPED)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Nope"), 1, False)  # type: ignore[arg-type]

    assert srv.modify_calls == []
    assert label_of(pool.row) is None


@pytest.mark.asyncio
async def test_a_scheduled_load_with_no_settings_at_all_still_clears_the_label(mission):
    """The plain rotate/restart shape (no settings key) keeps clearing its record."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool)
    cog = build_scheduler("/nonexistent")

    await Scheduler._run_without_presets(cog, srv, {"method": "rotate", "use_orig": False}, 1)  # type: ignore[arg-type]

    assert label_of(pool.row) is None


# ------------------------------- 3. the chain is NOT run on the scheduler path

@pytest.mark.asyncio
async def test_the_mission_modification_chain_does_not_run_on_the_scheduler_path(mission, presets_file,
                                                                                recorder):
    """Assert, do not assume: the real ``apply_mission_changes``/``apply_presets`` chain stays out.

    MizEdit is present and would write if the chain ran; the recorder would see it.
    """
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool)
    srv.extensions = {"MizEdit": build_mizedit(presets_file)}
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Configured"), 1, False)  # type: ignore[arg-type]

    assert recorder.calls == [], "the scheduler's load must not run the mission-modification chain"


# ------------------------------- 4. the dialog's pick, a bare start, and ``/mission modify``

@pytest.mark.asyncio
async def test_a_manual_pick_still_records_as_before(mission, presets_file, recorder):
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool)
    srv.extensions = {"MizEdit": build_mizedit(presets_file)}

    await ServerImpl.loadMission(srv, mission, modify_mission=True, use_orig=False, preset="Configured")

    assert len(recorder.calls) == 1, "the pick must still run the chain once"
    assert pool.row.preset == "Configured" and pool.row.temporary is False


@pytest.mark.asyncio
async def test_a_bare_start_leaves_the_record_untouched(mission):
    """A start nobody drove from a launch states no preset, so the record must not move."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool, status=Status.STOPPED)

    await ServerImpl.start(srv)

    assert pool.row.preset == "Summer" and pool.row.temporary is False


@pytest.mark.asyncio
async def test_the_mission_modify_sequence_does_not_label_a_server(mission, presets_file):
    """Discord's ``/mission modify`` applies presets itself, then loads with ``modify_mission=False``.

    It states no preset on that load, so it must keep leaving no label.
    """
    pool = RecordingPool(RowState("Summer", False))
    srv = build_server(mission, pool=pool)
    new_filename = await srv.modifyMission(mission, [{"modify": {}}], True)

    await ServerImpl.loadMission(srv, new_filename, modify_mission=False, use_orig=False)

    assert label_of(pool.row) is None
