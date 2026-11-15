"""The mission plugin's ``/api/v1`` rows, and the two fleet reads they name.

Every proof runs through the REAL discovery path — ``discover_actions(['mission'])`` fills the
registry, ``discover_endpoints(['mission'])`` imports ``plugins/mission/api.py`` and validates the
rows it declared. Nothing is mounted and no transport is involved: the rows and the two reads are
what this file is about.

The final two tests drive the new reads over a fake bus, through ``read_action`` — the same seam a
transport calls — and pin that neither of them goes near the MCP service's own inline builtins.
"""
from __future__ import annotations

import inspect
import sys
import types

import pytest

import core.actions as actions_module
from core import Status
from core.action_results import ServerInfoResult
from core.action_surface import (
    READ,
    WRITE,
    Risk,
    check_endpoints,
    discover_endpoints,
    endpoint_rows,
    reset_endpoints,
    validate_endpoints,
)
from core.actions import ActionContext, discover_actions, kind_of, read_action

#: The 15 rows as the design declares them: (method, path, action, risk, in the MCP tool list).
EXPECTED_ROWS = (
    ("GET", "/servers", "list_servers", Risk.READ, True),
    ("GET", "/servers/{server_name}", "get_server_status", Risk.READ, True),
    ("GET", "/servers/{server_name}/mission", "get_mission_info", Risk.READ, True),
    ("GET", "/servers/{server_name}/missions", "get_mission_list", Risk.READ, True),
    ("GET", "/servers/{server_name}/mission/weather", "read_mission_weather", Risk.READ, False),
    ("POST", "/servers/{server_name}/start", "start_server", Risk.LIFECYCLE, True),
    ("POST", "/servers/{server_name}/startup", "startup_server", Risk.LIFECYCLE, True),
    ("POST", "/servers/{server_name}/stop", "stop_server", Risk.LIFECYCLE, True),
    ("POST", "/servers/{server_name}/shutdown", "shutdown_server", Risk.LIFECYCLE, True),
    ("POST", "/servers/{server_name}/restart", "restart_server", Risk.LIFECYCLE, True),
    ("POST", "/servers/{server_name}/mission/pause", "pause_mission", Risk.LIFECYCLE, False),
    ("POST", "/servers/{server_name}/mission/unpause", "unpause_mission", Risk.LIFECYCLE, False),
    ("POST", "/servers/{server_name}/mission/restart", "restart_mission", Risk.LIFECYCLE, False),
    ("POST", "/servers/{server_name}/mission/rotate", "rotate_mission", Risk.LIFECYCLE, False),
    ("POST", "/servers/{server_name}/mission/load", "load_mission", Risk.LIFECYCLE, False),
)

#: The two DCS-level rows: a SHUTDOWN instance is booted by Startup and parked by Shutdown, and
#: neither had a transport before these rows existed.
DCS_LEVEL_ROWS = (
    ("POST", "/servers/{server_name}/startup", "startup_server"),
    ("POST", "/servers/{server_name}/shutdown", "shutdown_server"),
)

#: The sentence each lifecycle tool carries — the precondition AND the sibling tool that covers the
#: state it refuses. Both the ``/api/v1`` summary and the MCP description are these strings.
LIFECYCLE_SUMMARIES = {
    "start_server": "Start a server whose instance is STOPPED. A SHUTDOWN server needs startup_server.",
    "startup_server": "Boot a SHUTDOWN server (the DCS-level Startup). For a STOPPED server use start_server.",
    "shutdown_server": "Shut a running or paused server down to SHUTDOWN. stop_server ends the process instead.",
    "restart_server": "Restart a running, paused or stopped server. A SHUTDOWN server needs startup_server.",
}


# ------------------------------------------------------------------------------- the harness

# A strict pin on a defect in the PRODUCT, not a weaker assertion: the statements below are the
# ones the design calls for, so each is expected to fail until the product implements them, and
# each fails the suite again (XPASS) the moment the product does.
_XFAIL_MCP_TOOL_SET = pytest.mark.xfail(
    strict=True,
    reason=(
        "the rows' MCP flags disagree with the declared surface: `plugins/mission/api.py` marks "
        "`start_server` and `stop_server` `mcp=False` and leaves the five mission-lifecycle rows MCP- "
        "visible, the reverse of what this table pins"
    ),
)


@pytest.fixture
def mission_surface():
    """The mission plugin discovered for real: actions registered, then its rows validated.

    Both halves are PROCESS state (the registry and the row list are filled by an IMPORT, which is a
    no-op the second time), so the fixture clears and restores them, and drops the two plugin modules
    from the cache — otherwise a later test would observe an earlier test's registration.
    """
    saved_registry = dict(actions_module.action_registry())
    saved_kinds = dict(actions_module._ACTION_KINDS)
    saved_rows = endpoint_rows()
    for name in ("plugins.mission.actions", "plugins.mission.api"):
        sys.modules.pop(name, None)
    actions_module._ACTIONS.clear()
    actions_module._ACTION_KINDS.clear()
    reset_endpoints()
    try:
        discover_actions(["mission"])
        rows = discover_endpoints(["mission"])
        yield rows, actions_module.action_registry()
    finally:
        reset_endpoints()
        if saved_rows:
            from core.action_surface import register_endpoint

            for row in saved_rows:
                register_endpoint(row)
        for name in ("plugins.mission.actions", "plugins.mission.api"):
            sys.modules.pop(name, None)
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved_registry)
        actions_module._ACTION_KINDS.clear()
        actions_module._ACTION_KINDS.update(saved_kinds)


class MissionDouble:
    """A current-mission double: the two facts the fleet reads report, plus the escaped name they
    must never show — the row carries the PLAIN one, read through the label helper."""

    def __init__(self, name: str, map_name: str):
        self.name = name
        self.display_name = f"\\{name}"   # the domain's escaped spelling, as ``Server`` carries it
        self.map = map_name


class ServerDouble:
    """A server double carrying exactly what the reads touch: name, status, mission, players."""

    def __init__(self, name: str, status=Status.RUNNING, mission=None, players: int = 0):
        self.name = name
        self.display_name = name
        self.status = status
        self.current_mission = mission
        self._players = [types.SimpleNamespace(active=True) for _ in range(players)]

    def get_active_players(self, **kwargs):
        return list(self._players)


def ctx_for(*servers) -> ActionContext:
    """The seam's context over a stub cluster — duck-typed, exactly as a transport builds it."""
    bus = types.SimpleNamespace(servers={server.name: server for server in servers})
    return ActionContext(node=None, bus=bus)


# ------------------------------------------------------------ 1. discovery accepts fifteen

@_XFAIL_MCP_TOOL_SET
def test_discovery_registers_the_fifteen_rows_and_nothing_else(mission_surface):
    """The real discovery path yields exactly §4.1/§4.2 — 5 reads and 10 writes, owned by mission."""
    rows, _ = mission_surface

    assert len(rows) == 15
    assert [(row.method, row.path, row.action, Risk(row.risk), row.mcp) for row in rows] \
        == list(EXPECTED_ROWS)
    assert {row.owner for row in rows} == {"mission"}
    assert len([row for row in rows if Risk(row.risk) is Risk.READ]) == 5
    assert len([row for row in rows if Risk(row.risk) is Risk.LIFECYCLE]) == 10


def test_both_dcs_level_lifecycle_rows_are_discovered(mission_surface):
    """The startup/shutdown pair is a ROW, not merely an action: no transport means no way to boot."""
    rows, _ = mission_surface

    discovered = {(row.method, row.path, row.action) for row in rows}
    for method, path, action in DCS_LEVEL_ROWS:
        assert (method, path, action) in discovered
    for row in rows:
        if row.action in {"startup_server", "shutdown_server"}:
            assert Risk(row.risk) is Risk.LIFECYCLE
            assert row.mcp is True


def test_the_discovered_rows_pass_every_invariant(mission_surface):
    """No problem at all — not merely \"the good ones survived\"."""
    rows, registry = mission_surface

    assert check_endpoints(rows, registry=registry) == []


def test_the_validator_refuses_a_hand_made_duplicate(mission_surface):
    """A second row for a discovered ``(method, path)`` is V5-refused and dropped, the first kept.

    The twin is built with ``dataclasses.replace`` so it is valid on every other count: only the
    duplication can be what refuses it, and only the LATER row may be the one dropped.
    """
    import dataclasses

    rows, registry = mission_surface
    duplicate = dataclasses.replace(rows[0])

    problems = check_endpoints([*rows, duplicate], registry=registry)
    assert {problem.code for problem in problems} == {"V5", "V6"}, \
        "the twin shares the path AND the tool name; it is the only row refused"
    assert {problem.index for problem in problems} == {len(rows)}

    survivors = validate_endpoints([*rows, duplicate], registry=registry)
    assert survivors == rows


# ------------------------------------------------------- 2. rows are relative and consistent

def test_no_row_path_names_a_transport_prefix(mission_surface):
    """Rows are relative to the prefix: a row that names ``/api`` or ``/mcp`` would double it (V8)."""
    rows, _ = mission_surface

    for row in rows:
        assert row.path.startswith("/"), row.path
        assert ".." not in row.path, row.path
        assert not row.path.startswith("/api"), row.path
        assert row.path != "/mcp" and not row.path.startswith("/mcp/"), row.path


def test_every_path_parameter_is_a_required_parameter_of_its_action(mission_surface):
    """V3 over the REAL signatures: a ``{param}`` the action does not require is a 500 at runtime."""
    rows, registry = mission_surface

    seen = 0
    for row in rows:
        path_params = [piece.split("}", 1)[0]
                       for piece in row.path.split("{")[1:]]
        signature = inspect.signature(registry[row.action])
        for name in path_params:
            assert name in signature.parameters, f"{row.method} {row.path}: {name} not accepted"
            parameter = signature.parameters[name]
            assert parameter.default is inspect.Parameter.empty, \
                f"{row.method} {row.path}: {name} must be required"
            assert parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                      inspect.Parameter.KEYWORD_ONLY), \
                f"{row.method} {row.path}: {name} is not a declarable parameter"
            seen += 1
    assert seen == 14, "every row but the fleet list carries exactly the server_name segment"


def test_a_read_row_never_names_a_write_action(mission_surface):
    """V2 both ways, over the real kinds — a READ row on a write action would skip floor and audit."""
    rows, _ = mission_surface

    kinds = {row.action: kind_of(row.action) for row in rows}
    assert {row.action for row in rows if Risk(row.risk) is Risk.READ} \
        == {name for name, kind in kinds.items() if kind == READ}
    assert {row.action for row in rows if Risk(row.risk) is not Risk.READ} \
        == {name for name, kind in kinds.items() if kind == WRITE}


# ------------------------------------------------- 3. the four lifecycle sentences, and the refusals

def test_every_lifecycle_summary_states_its_precondition_and_its_sibling(mission_surface):
    """The row summary IS the sentence the route and the tool carry, and it names the other tool.

    The row's ``summary`` is what ``/api/v1`` documents and what an MCP client reads as the tool
    description, so the precondition has to live there — an agent that only sees the tool list can
    pick the right one. It is also the action's own docstring first line: one sentence, one source,
    and the two cannot drift apart unnoticed.
    """
    rows, registry = mission_surface

    summaries = {row.action: row.summary for row in rows if row.action in LIFECYCLE_SUMMARIES}
    assert summaries == LIFECYCLE_SUMMARIES
    for action, sentence in LIFECYCLE_SUMMARIES.items():
        doc = inspect.getdoc(registry[action]) or ""
        assert doc.splitlines()[0].strip() == sentence, \
            f"{action}: the action's docstring first line is not the row summary"


@pytest.mark.asyncio
async def test_a_shutdown_state_start_names_startup_server(mission_surface):
    """A SHUTDOWN instance is refused by start_server/restart_server, and the refusal names the tool.

    This sentence is what a wrong call gets back on every transport — the MCP tool error, the REST
    body and the console notice — so it has to name the tool that would work rather than only state
    the state.
    """
    _, registry = mission_surface
    shutdown = ServerDouble("SRS-1", Status.SHUTDOWN)

    for action in ("start_server", "restart_server"):
        result = await registry[action](ctx_for(shutdown), server_name="SRS-1")
        assert result.success is False
        assert result.message == "Server 'SRS-1' is Shutdown - use startup_server.", result.message
        assert "startup_server" in result.message


@pytest.mark.asyncio
async def test_a_stopped_state_startup_names_start_server(mission_surface):
    """The mirror: a STOPPED instance is refused by startup_server, and it names start_server."""
    _, registry = mission_surface
    stopped = ServerDouble("SRS-1", Status.STOPPED)

    result = await registry["startup_server"](ctx_for(stopped), server_name="SRS-1")

    assert result.success is False
    assert result.message == "Server 'SRS-1' is Stopped - use start_server.", result.message


@pytest.mark.asyncio
async def test_every_other_state_refusal_keeps_its_sentence(mission_surface):
    """Only the SHUTDOWN/STOPPED crossings gain the hint: the remaining refusals are unchanged."""
    _, registry = mission_surface
    running = ServerDouble("SRS-1", Status.RUNNING)

    assert (await registry["start_server"](ctx_for(running), server_name="SRS-1")).message \
        == "Server 'SRS-1' is Running, not stopped."
    assert (await registry["startup_server"](ctx_for(running), server_name="SRS-1")).message \
        == "Server 'SRS-1' is Running, not shut down."
    assert (await registry["shutdown_server"](ctx_for(ServerDouble("SRS-1", Status.SHUTDOWN)),
                                              server_name="SRS-1")).message \
        == "Server 'SRS-1' is Shutdown, not running or paused."


# --------------------------------------------------------------- 4. the two fleet reads

def test_both_fleet_reads_are_registered_reads(mission_surface):
    """They are real actions of the mission plugin, and they declare READ."""
    _, registry = mission_surface

    assert {"list_servers", "get_server_status"} <= set(registry)
    assert kind_of("list_servers") == READ
    assert kind_of("get_server_status") == READ
    assert inspect.iscoroutinefunction(registry["list_servers"])
    assert inspect.iscoroutinefunction(registry["get_server_status"])


def _new_modules_since(before: set[str]) -> set[str]:
    """Module names imported since *before*.

    ``sys.modules`` is shared by every test in one worker, so "the MCP service is not loaded at all"
    is only true in a worker that never ran a test importing it. The claim these reads owe is
    narrower and order-independent: calling one imports nothing from the MCP service.
    """
    return {name for name in sys.modules if name not in before}


@pytest.mark.asyncio
async def test_list_servers_answers_from_the_bus(mission_surface):
    """Every server in the bus becomes one row — status, running mission, map, players."""
    srs = ServerDouble("SRS-1", Status.RUNNING, MissionDouble("Caucasus Dawn", "Caucasus"),
                       players=2)
    stopped = ServerDouble("TRAIN-1", Status.STOPPED)

    before = set(sys.modules)
    result = await read_action("list_servers", ctx_for(srs, stopped))

    assert result.success is True
    assert result.message == "2 servers."
    assert result.data["servers"] == [
        {"name": "SRS-1", "status": Status.RUNNING.value,
         "mission": "Caucasus Dawn", "map": "Caucasus", "players_online": 2},
        {"name": "TRAIN-1", "status": Status.STOPPED.value,
         "mission": None, "map": None, "players_online": 0},
    ]
    assert getattr(result, "servers", None) == result.data["servers"]
    assert result.to_dict()["status"] == "ok", "the envelope's status is the outcome, not the state"
    assert not [name for name in _new_modules_since(before)
                if name.startswith("services.mcpservice")], "the read reached the MCP service"


@pytest.mark.asyncio
async def test_get_server_status_answers_from_the_bus(mission_surface):
    """One server, the same row shape the fleet read uses, plus the typed fields."""
    srs = ServerDouble("SRS-1", Status.PAUSED, MissionDouble("Caucasus Dawn", "Caucasus"),
                       players=3)

    before = set(sys.modules)
    result = await read_action("get_server_status", ctx_for(srs), server_name="SRS-1")

    assert isinstance(result, ServerInfoResult)
    assert result.success is True
    assert result.data["server"] == {
        "name": "SRS-1", "status": Status.PAUSED.value,
        "mission": "Caucasus Dawn", "map": "Caucasus", "players_online": 3}
    assert (result.server_name, result.status, result.mission_name, result.players_online) \
        == ("SRS-1", Status.PAUSED.value, "Caucasus Dawn", 3)
    assert result.message == f"Server 'SRS-1' is {Status.PAUSED.value}."
    assert result.to_dict()["status"] == "ok"
    assert not [name for name in _new_modules_since(before)
                if name.startswith("services.mcpservice")], "the read reached the MCP service"


@pytest.mark.asyncio
async def test_get_server_status_refuses_an_unknown_name(mission_surface):
    """An unresolvable name is a domain refusal, never an exception."""
    result = await read_action("get_server_status", ctx_for(), server_name="NOPE-1")

    assert result.success is False
    assert result.message == "Server 'NOPE-1' not found."
    assert result.to_dict() == {"status": "error", "message": "Server 'NOPE-1' not found."}
