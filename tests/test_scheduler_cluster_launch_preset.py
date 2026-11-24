"""A scheduled stop-then-start launch on a CLUSTERED server records the weather it applied.

``Server.start`` states the weather a launch already applied so the server page can name it. On a
clustered install the server object is a ``ServerProxy``: the record is written by the OWNING NODE
(``ServerImpl.start`` / ``_record_launched_preset``), so a proxy ``start`` that keeps the value to itself
leaves the page showing the PREVIOUS launch's preset — a claim the launch did not make.

Contract pinned here, one section per acceptance item:

* a cluster-shaped scheduler stop-then-start launch writes the row the page reads, naming the presets
  that launch applied and not marking them temporary;
* a bare start (no preset stated) does not move the record, and one that states an empty set clears it;
* the wire: a start stating NO weather crosses exactly as ``start_server`` — byte-identical to the base
  ``Server.start`` — and a stated preset crosses as an RPC ``start`` whose params carry the preset and
  nothing else;
* the scheduler states its applied preset on the STOPPED branch through ``start``, and the other two
  branches (``startup`` / ``loadMission``) keep their shape;
* the weather is applied once (the scheduler's own ``modifyMission``), never again on the node's start.

The master is the real ``ServerProxy``; its bus stands in for the service bus and dispatches an RPC to a
real ``ServerImpl`` the way the node's RPC handler does (look the method up on the server object, pass
``params`` as keywords). The row the node's write lands on is fed to the page's OWN read model.
"""
from __future__ import annotations

import os

import asyncio
import copy
import json
import logging
import types

import pytest

from core import Status
from core.data.impl import serverimpl
from core.data.impl.serverimpl import ServerImpl
from core.data.proxy.serverproxy import ServerProxy
from core.data.server import Server
from plugins.scheduler.commands import Scheduler
from services.webservice import readmodels

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
    """The ``servers`` row this write lands on — the state the page's label reads."""

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


def record_writes(pool: RecordingPool) -> list[tuple]:
    return [s for s in pool.statements if s[0].startswith("UPDATE servers SET launched_preset")]


# --------------------------------------------------------------- the node: a real ServerImpl

def build_node(mission: str, *, pool: RecordingPool, status=Status.STOPPED) -> ServerImpl:
    """The REAL node-side ``ServerImpl`` wired with only the attributes its start path touches."""
    srv = object.__new__(ServerImpl)
    srv.name = SERVER
    # ``master=True`` keeps the status setter from broadcasting a status event: the broadcast is the
    # service bus's business, not this contract's, and the fake bus has no event loop.
    srv.node = types.SimpleNamespace(name=NODE, master=True, installation=None,
                                     locals={"DCS": {"desanitize": True}}, config_dir=os.path.abspath("/nonexistent"))
    srv.log = logging.getLogger("test.scheduler.cluster.node")
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
    srv.start_calls = []
    srv.dcs_messages = []

    async def noop(*args, **kwargs):
        return None

    async def send_to_dcs_sync(message, *args, **kwargs):
        srv.dcs_messages.append(copy.deepcopy(message))
        return {"result": 0 if message.get("command") == "start_server" else True}

    async def get_mission_list():
        return srv.settings["missionList"]

    async def modify_mission(filename, preset, use_orig=True):
        srv.modify_calls.append((filename, preset, use_orig))
        return filename

    async def start(*args, **kwargs):
        srv.start_calls.append(kwargs)
        return await ServerImpl.start(srv, *args, **kwargs)

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
    # the node's own start, reached through ``start`` above and by the proxy's RPC
    srv.start = start
    # a chain that ran here would write the record a second time
    srv.apply_mission_changes = lambda *a, **k: ServerImpl.apply_mission_changes(srv, *a, **k)
    srv.startup = lambda *a, **k: ServerImpl.startup(srv, *a, **k)
    srv.loadMission = lambda *a, **k: ServerImpl.loadMission(srv, *a, **k)
    return srv


# ------------------------------------------------------------------- the master: a real ServerProxy

class _DiscardingLoop:
    def create_task(self, coro):
        coro.close()
        return None


class FrameBus:
    """The master's bus: records every frame, and dispatches an RPC to the node's real ``Server``.

    The dispatch mirrors ``ServiceBus.rpc``: the method is looked up on the node's server object and
    ``params`` are passed as keywords, so an unknown method or an unknown keyword fails the way a node
    that predates it would.
    """

    def __init__(self, node_server):
        self.frames: list[tuple[str, dict]] = []
        self.node_server = node_server
        # the status setter schedules a status EVENT on the bus loop; that event is not the launch call
        # under test and this fake runs no event loop, so the coroutine is closed unrun
        self.loop = _DiscardingLoop()

    async def send_to_node_sync(self, message: dict, node: str | None = None, timeout: int | None = None):
        self.frames.append(("sync", copy.deepcopy(message)))
        assert message.get("command") == "rpc", f"only rpc crosses synchronously, got {message}"
        assert node == NODE, f"an RPC must be addressed to the owning node, got {node}"
        assert timeout is not None, "an RPC needs a timeout"
        method = getattr(self.node_server, message["method"], None)
        if method is None:
            raise ValueError(f"Call to non-existing function {message['method']}()")
        return await method(**message.get("params", {}))

    async def send_to_node(self, message: dict, node: str | None = None):
        self.frames.append(("send", copy.deepcopy(message)))
        return None


def build_master(node_server, *, status=Status.STOPPED) -> tuple[ServerProxy, FrameBus]:
    """The REAL ``ServerProxy`` the master holds for a server owned by ``node_server``'s node."""
    bus = FrameBus(node_server)
    proxy = object.__new__(ServerProxy)
    proxy.name = SERVER
    proxy.node = types.SimpleNamespace(name=NODE, slow_system=False, master=True, locals={})
    proxy.bus = bus
    proxy.is_remote = True
    proxy._status = status
    proxy._settings = {}
    proxy._options = {}
    proxy.status_change = asyncio.Event()
    proxy.log = logging.getLogger("test.scheduler.cluster.master")

    async def noop(*args, **kwargs):
        return None

    async def send_to_dcs_sync(message, *args, **kwargs):
        # the base start's path: the command leaves through the proxy's own ``send_to_dcs`` (recorded),
        # and DCS is assumed to have started the mission
        await proxy.send_to_dcs(message)
        proxy._status = Status.RUNNING
        return {"result": 0}

    proxy.send_to_dcs_sync = send_to_dcs_sync
    proxy.wait_for_status_change = noop
    return proxy, bus


def build_scheduler(config_dir) -> types.SimpleNamespace:
    cog = types.SimpleNamespace(
        node=types.SimpleNamespace(config_dir=str(config_dir), name=NODE, locals={}, master=True),
        log=logging.getLogger("test.scheduler.cluster.cog"))
    cog.__cog_name__ = "Scheduler"
    cog._preset_file_text = Scheduler._preset_file_text
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


# ------------------------------------- 1. the cluster-shaped scheduler launch records what it applied

@pytest.mark.asyncio
async def test_a_cluster_stop_then_start_launch_names_the_preset_it_applied(mission, presets_file):
    """A proxy that keeps the preset to itself leaves the row on the PREVIOUS launch's label."""
    pool = RecordingPool(RowState("Summer", False))
    node = build_node(mission, pool=pool, status=Status.STOPPED)
    master, _bus = build_master(node, status=Status.STOPPED)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, master, rconf(presets_file, "Configured"), 1, False)  # type: ignore[arg-type]

    assert node.modify_calls, "the configured weather must have been applied to the mission once"
    assert (pool.row.preset, pool.row.temporary) == ("Configured", False), \
        "the cluster launch must record the preset it applied instead of leaving the previous one"
    view = label_of(pool.row)
    assert view is not None and view.name == "Configured", \
        "the page must name this launch's preset, not the previous launch's label"
    assert len(record_writes(pool)) == 1, "the node writes the record exactly once"
    assert node.dcs_messages == [{"command": "start_server"}], \
        "the node, not the master, drives the start_server command"


@pytest.mark.asyncio
async def test_the_scheduler_states_its_preset_to_the_node_through_start(mission, presets_file):
    """The STOPPED branch reaches the node's own ``start`` with the applied names."""
    pool = RecordingPool()
    node = build_node(mission, pool=pool, status=Status.STOPPED)
    master, bus = build_master(node, status=Status.STOPPED)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, master, rconf(presets_file, "Configured", "Pair"), 1, False)  # type: ignore[arg-type]

    assert node.start_calls == [{"preset": ["Configured", "Pair"]}], \
        "the node's start must be told which presets this launch applied"
    assert pool.row.preset == "Configured, Pair"


def test_the_proxy_defines_its_own_start():
    """A proxy ``start`` that falls back to the base class cannot reach the node's record writer."""
    assert ServerProxy.start is not Server.start, \
        "ServerProxy must override start, or a stated preset never crosses to the owning node"


# ------------------------------------------------ 2. the wire: no weather, and only the preset key

@pytest.mark.asyncio
async def test_a_no_weather_start_keeps_the_pre_change_wire(mission):
    """The plain start crosses as ``start_server`` — byte-identical to what the base class sends."""
    pool = RecordingPool(RowState("Summer", False))
    node = build_node(mission, pool=pool, status=Status.STOPPED)

    master, bus = build_master(node, status=Status.STOPPED)
    assert await master.start() is True
    with_fix = bus.frames

    # the base implementation, called directly: what a proxy without an override sends
    base_only, base_bus = build_master(node, status=Status.STOPPED)
    assert await Server.start(base_only) is True
    before_fix = base_bus.frames

    assert with_fix == before_fix == [("send", {"command": "start_server", "server_name": SERVER})]
    assert json.dumps(with_fix) == json.dumps(before_fix)
    assert "preset" not in json.dumps(with_fix), "a start stating no weather must not carry the key"
    assert not any(frame.get("command") == "rpc" for _kind, frame in with_fix)
    assert record_writes(pool) == [], "a start stating no preset must not move the record"


@pytest.mark.asyncio
async def test_a_stated_preset_crosses_as_an_rpc_start_carrying_only_the_preset(mission):
    pool = RecordingPool()
    node = build_node(mission, pool=pool, status=Status.STOPPED)
    master, bus = build_master(node, status=Status.STOPPED)

    assert await master.start(preset=["Configured"]) is True

    assert bus.frames == [("sync", {
        "command": "rpc",
        "object": "Server",
        "method": "start",
        "server_name": SERVER,
        "params": {"preset": ["Configured"]}
    })], "the stated preset must travel as the node start's only parameter"
    assert pool.row.preset == "Configured"


@pytest.mark.asyncio
async def test_a_stated_preset_sent_to_a_node_without_the_parameter_fails_loudly(mission):
    """A node that predates the parameter rejects the call instead of starting and dropping the record.

    The fake bus passes ``params`` as keywords (the real one validates the signature first and raises
    ``ValueError``), so the mismatch surfaces as one of those two — never as a silent start.
    """
    pool = RecordingPool(RowState("Summer", False))
    node = build_node(mission, pool=pool, status=Status.STOPPED)
    master, bus = build_master(node, status=Status.STOPPED)

    async def start(*args, **kwargs):
        raise TypeError("start() got an unexpected keyword argument 'preset'")

    node.start = start

    with pytest.raises((TypeError, ValueError)):
        await master.start(preset=["Configured"])

    assert [kind for kind, _frame in bus.frames] == ["sync"], \
        "the master must not fall back to a bare start_server when the record cannot be written"


# ------------------------------------------------- 3. a start that states nothing / an empty set

@pytest.mark.asyncio
async def test_a_cluster_start_stating_no_preset_leaves_the_record_alone(mission):
    pool = RecordingPool(RowState("Summer", False))
    node = build_node(mission, pool=pool, status=Status.STOPPED)
    master, _bus = build_master(node, status=Status.STOPPED)

    await master.start()

    assert (pool.row.preset, pool.row.temporary) == ("Summer", False)


@pytest.mark.asyncio
async def test_a_cluster_start_stating_an_empty_set_clears_the_record(mission):
    """A launch that applied nothing states an empty set, which clears the previous label."""
    pool = RecordingPool(RowState("Summer", False))
    node = build_node(mission, pool=pool, status=Status.STOPPED)
    master, _bus = build_master(node, status=Status.STOPPED)

    await master.start(preset=[])

    assert label_of(pool.row) is None
    assert len(record_writes(pool)) == 1


# ----------------------------------------- 4. the other two scheduler launch shapes are unchanged

class SpyServer:
    """Records the launch call ``_run_with_presets`` makes for a given status."""

    def __init__(self, mission, status):
        self.status = status
        self.name = SERVER
        self.locals = {"mission_rewrite": True}
        self.current_mission = None
        self.calls: list[tuple[str, dict]] = []
        self._mission = mission

    async def getMissionList(self):
        return [self._mission]

    async def modifyMission(self, filename, preset, use_orig=True):
        return filename

    async def replaceMission(self, idx, path):
        return [self._mission]

    async def stop(self):
        self.calls.append(("stop", {}))

    async def start(self, **kwargs):
        self.calls.append(("start", kwargs))

    async def startup(self, *args, **kwargs):
        self.calls.append(("startup", {"args": args, **kwargs}))

    async def loadMission(self, *args, **kwargs):
        self.calls.append(("loadMission", {"args": args, **kwargs}))


@pytest.mark.asyncio
async def test_the_stopped_branch_states_the_preset_through_start(mission, presets_file):
    srv = SpyServer(mission, Status.STOPPED)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Configured"), 1, False)  # type: ignore[arg-type]

    assert srv.calls == [("start", {"preset": ["Configured"]})]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.SHUTDOWN, Status.LOADING])
async def test_the_down_branch_states_the_preset_through_startup(mission, presets_file, status):
    srv = SpyServer(mission, status)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Configured"), 1, False)  # type: ignore[arg-type]

    assert srv.calls == [("startup", {"args": (),
                                      "modify_mission": False, "use_orig": False,
                                      "preset": ["Configured"]})]


@pytest.mark.asyncio
async def test_the_running_branch_states_the_preset_through_loadmission(mission, presets_file):
    srv = SpyServer(mission, Status.RUNNING)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Configured"), 1, False)  # type: ignore[arg-type]

    assert srv.calls == [("loadMission", {"args": (mission,),
                                         "modify_mission": False, "use_orig": False,
                                         "no_reload": False, "preset": ["Configured"]})]


# ------------------------------------------- 5. positive control: the same launch on a single node

@pytest.mark.asyncio
async def test_the_same_launch_on_a_single_node_still_records(mission, presets_file):
    pool = RecordingPool(RowState("Summer", False))
    srv = build_node(mission, pool=pool, status=Status.STOPPED)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, srv, rconf(presets_file, "Configured"), 1, False)  # type: ignore[arg-type]

    assert (pool.row.preset, pool.row.temporary) == ("Configured", False)
    assert len(record_writes(pool)) == 1
    assert srv.dcs_messages == [{"command": "start_server"}]


# -------------------------------------- 6. the weather is applied once, on the scheduler's own call

@pytest.mark.asyncio
async def test_the_cluster_launch_applies_the_weather_once(mission, presets_file):
    pool = RecordingPool()
    node = build_node(mission, pool=pool, status=Status.STOPPED)
    master, _bus = build_master(node, status=Status.STOPPED)
    cog = build_scheduler(presets_file)

    await Scheduler._run_with_presets(cog, master, rconf(presets_file, "Configured"), 1, False)  # type: ignore[arg-type]

    assert len(node.modify_calls) == 1, "the mission file is rewritten once, by the scheduler's call"
    assert len(record_writes(pool)) == 1, "the record is written once, by the node's start"
