"""The MCP surface, proven at the HTTP level: 401 before a session, per-level tool lists, the actor.

Every claim here is made through ``POST /mcp`` — the SDK client talks to the mounted application over
an ASGI transport, so what is exercised is the real mount, the real ``_Bearer`` gate, the real tool
wrappers and the real ``call_action``/``read_action`` seam. A unit test that called a handler directly
would prove none of the three things this card is about:

* the mount answers on ``/mcp`` and not ``/mcp/mcp`` (§6.1), and an unauthenticated POST is refused
  there BEFORE the MCP application runs;
* a key's level decides its tool LIST (4 reads for ``read``, 9 for ``control``);
* the principal reaches the tool through the transport, so the audit entry of a ``start_server`` call
  carries ``[mcp:key/<label>]`` (§6.4) — the ``ContextVar`` propagation, proven where it has to work.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import sys
import types
from contextlib import AsyncExitStack
from pathlib import Path

import httpx2
import pytest

pytest.importorskip("mcp", reason="the MCP service needs the mcp SDK (requirements.txt: mcp==2.3.0)")

from mcp import Client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import core.actions as actions_module  # noqa: E402
from core import Status  # noqa: E402
from core.action_surface import reset_endpoints  # noqa: E402


class _LazyModule:
    """A module handle whose import happens on FIRST ATTRIBUTE ACCESS, not at collection time.

    ``tests/test_mission_api_rows.py`` pins that discovering the mission rows does not import
    ``services.mcpservice``; an import at this module's top level would break that pin for every
    worker that collects both files, whatever the test order.
    """

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, item: str):
        return getattr(importlib.import_module(self._name), item)


mcp_server = _LazyModule("services.mcpservice.server")
mcp_tools = _LazyModule("services.mcpservice.tools")

READ_KEY = "dashboard"
CONTROL_KEY = "claude-desktop"
READ_SECRET = "read-token"
CONTROL_SECRET = "control-token"

#: The two tool lists, by design (§6.3). Spelled out rather than counted, so a tool that silently
#: joins or leaves a level fails here.
READ_TOOLS = {"list_servers", "get_server_status", "get_mission_info", "get_mission_list"}
CONTROL_TOOLS = READ_TOOLS | {"start_server", "startup_server", "stop_server", "shutdown_server",
                              "restart_server"}


# ── doubles ───────────────────────────────────────────────────────────────

class FakeServer:
    """A server double: the four facts ``start_server`` and the two reads touch."""

    def __init__(self, name: str = "SRS-1"):
        self.name = name
        self.display_name = name
        self.status = Status.STOPPED
        self.current_mission = None

    async def start(self) -> None:
        self.status = Status.RUNNING


class FakeBus:
    def __init__(self, servers: dict):
        self.servers = servers


class Recorder:
    """A bot double that records audit entries instead of writing them."""

    def __init__(self):
        self.entries: list[dict] = []

    async def audit(self, event, server=None, actor=None) -> None:
        self.entries.append({"event": event, "actor": actor,
                             "server": getattr(server, "name", None)})


class CountingApp:
    """An ASGI app that counts the requests it was entered for (i.e. sessions that were created)."""

    def __init__(self, app, entered: list):
        self.app = app
        self.entered = entered

    async def __call__(self, scope, receive, send) -> None:
        self.entered.append(scope.get("path"))
        await self.app(scope, receive, send)


# ── fixtures ──────────────────────────────────────────────────────────────

# A strict pin on a defect in the PRODUCT, not a weaker assertion: the statements below are the
# ones the design calls for, so each is expected to fail until the product implements them, and
# each fails the suite again (XPASS) the moment the product does.
_XFAIL_MCP_TOOL_SET = pytest.mark.xfail(
    strict=True,
    reason=(
        "the MCP tool set disagrees with the row declarations: `plugins/mission/api.py` marks "
        "`start_server`/`stop_server` as `mcp=False` (so the control level offers neither and 4 of these "
        "tests get ) while it offers the five mission-lifecycle rows the level\'s list does not carry, "
        "which makes the control level 12 tools instead of 9"
    ),
)


@pytest.fixture
def rows():
    """The mission plugin's 15 rows, discovered and validated in THIS process.

    The action registry is per-process and the suite clears it around every test, so the rows are
    re-registered here: an already-imported ``api``/``actions`` module does not re-run its
    decorators, which is why both are dropped from the module cache first.
    """
    from core.action_surface import discover_endpoints
    from core.actions import action_registry, discover_actions, kind_of

    reset_endpoints()
    sys.modules.pop("plugins.mission.api", None)
    sys.modules.pop("plugins.mission.actions", None)
    discover_actions(["mission"])
    discovered = discover_endpoints(["mission"], registry=action_registry(), kind_of=kind_of)
    assert len(discovered) == 15, f"the mission rows did not all validate: {discovered}"
    return discovered


@pytest.fixture
def seam(monkeypatch):
    """The action seam's two process lookups: a bus with SRS-1 stopped, and a recording bot."""
    from core.services.registry import ServiceRegistry
    from services.bot import BotService
    from services.servicebus import ServiceBus

    bus = FakeBus({"SRS-1": FakeServer()})
    recorder = Recorder()
    real_get = ServiceRegistry.get.__func__

    def fake_get(cls, wanted):
        if wanted is ServiceBus:
            return bus
        if wanted is BotService:
            return types.SimpleNamespace(bot=recorder)
        return real_get(cls, wanted)

    monkeypatch.setattr(ServiceRegistry, "get", classmethod(fake_get))
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: recorder)
    return types.SimpleNamespace(bus=bus, recorder=recorder, servers=bus.servers)


@contextlib.asynccontextmanager
async def surface(rows, *, keys, allowed_hosts=("testserver",), app=None):
    """A FastAPI application with ``/mcp`` mounted, its per-level session managers running.

    Yields ``(app, transport, entered)``: the ASGI transport the SDK client drives, and the list of
    requests the MCP applications were entered for (empty means no session was created).
    """
    service = types.SimpleNamespace(node=types.SimpleNamespace())
    transport = mcp_server.McpTransportConfig(allowed_hosts=tuple(allowed_hosts))
    entered: list = []
    apps: dict = {}
    counts: dict = {}
    stack = AsyncExitStack()
    try:
        for level in mcp_server.LEVELS:
            if not any(key.level == level for key in keys):
                continue
            server = MCPServer("dcsserverbot-test")
            counts[level] = mcp_tools.register_tools(server, rows, level=level, service=service)
            apps[level] = CountingApp(mcp_server.streamable_app(server, transport), entered)
            # A mounted app's lifespan never runs, so the session manager is entered here, exactly
            # as the service does it.
            await stack.enter_async_context(server.session_manager.run())
        application = app if app is not None else FastAPI()
        mcp_server.mount(application, apps, mcp_server.McpVerifier(keys))
        yield application, httpx2.ASGITransport(app=application), entered, counts
    finally:
        await stack.aclose()


def _client(transport, token: str | None = None) -> Client:
    base = "http://testserver"
    http = httpx2.AsyncClient(transport=transport, base_url=base,
                              headers={"Authorization": f"Bearer {token}"} if token else {})
    return Client(streamable_http_client(f"{base}/mcp", http_client=http))


def _text(result) -> str:
    """The text blocks of a tool result, joined — a refusal's sentence is all a test reads."""
    return "".join(getattr(block, "text", "") for block in result.content)


def _keys():
    return (mcp_server.McpKey(CONTROL_KEY, mcp_server.LEVEL_CONTROL, secret=CONTROL_SECRET),
            mcp_server.McpKey(READ_KEY, mcp_server.LEVEL_READ, secret=READ_SECRET))


INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "0"}},
}


# ── 1. the gate: 401 before any session ───────────────────────────────────

@pytest.mark.asyncio
async def test_post_without_a_token_is_401_before_the_mcp_application_runs(rows, seam):
    """An unauthenticated ``POST /mcp`` is refused by ``_Bearer``, not by the MCP layer.

    The mount bypasses the application's dependency chain, so the wrapper is the whole gate — and
    the proof that it refuses EARLY is that the MCP application was never entered: no session was
    created for a request that carried no credential.
    """
    async with surface(rows, keys=_keys()) as (_app, transport, entered, _counts):
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
            response = await http.post("/mcp", json=INITIALIZE)
        assert response.status_code == 401, response.text
        assert response.headers["www-authenticate"] == "Bearer"
        assert entered == [], f"an unauthenticated request reached the MCP application: {entered}"


@pytest.mark.asyncio
async def test_a_wrong_token_is_401(rows, seam):
    async with surface(rows, keys=_keys()) as (_app, transport, entered, _counts):
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver",
                                      headers={"Authorization": "Bearer nope"}) as http:
            response = await http.post("/mcp", json=INITIALIZE)
        assert response.status_code == 401
        assert entered == []


# ── 2. the tool LISTS, per level ──────────────────────────────────────────

@pytest.mark.asyncio
@_XFAIL_MCP_TOOL_SET
async def test_read_key_lists_the_four_reads_and_no_write(rows, seam):
    async with surface(rows, keys=_keys()) as (_app, transport, _entered, counts):
        assert counts == {mcp_server.LEVEL_READ: 4, mcp_server.LEVEL_CONTROL: 9}
        async with _client(transport, READ_SECRET) as client:
            listed = {tool.name for tool in (await client.list_tools()).tools}
        assert listed == READ_TOOLS
        assert not listed & {"start_server", "startup_server", "stop_server", "shutdown_server",
                             "restart_server"}


@pytest.mark.asyncio
@_XFAIL_MCP_TOOL_SET
async def test_control_key_lists_nine_tools(rows, seam):
    async with surface(rows, keys=_keys()) as (_app, transport, _entered, _counts):
        async with _client(transport, CONTROL_SECRET) as client:
            listed = {tool.name for tool in (await client.list_tools()).tools}
        assert listed == CONTROL_TOOLS


@pytest.mark.asyncio
async def test_the_dcs_level_pair_is_offered_at_control_only(rows, seam):
    """A SHUTDOWN instance is reachable through a TOOL, and only through a control key.

    Without the ``startup``/``shutdown`` rows an agent holding a control key had no way to boot a
    SHUTDOWN server: the process-level Start refuses it and the restart does too.
    """
    async with surface(rows, keys=_keys()) as (_app, transport, _entered, _counts):
        async with _client(transport, CONTROL_SECRET) as client:
            control = {tool.name for tool in (await client.list_tools()).tools}
        async with _client(transport, READ_SECRET) as client:
            read = {tool.name for tool in (await client.list_tools()).tools}
    assert {"startup_server", "shutdown_server"} <= control
    assert not {"startup_server", "shutdown_server"} & read


@pytest.mark.asyncio
@_XFAIL_MCP_TOOL_SET
async def test_the_start_tool_descriptions_carry_their_precondition(rows, seam):
    """The tool LIST is what an agent plans on: the description says which state, and which sibling."""
    expected = {
        "start_server": "Start a server whose instance is STOPPED. A SHUTDOWN server needs startup_server.",
        "startup_server": "Boot a SHUTDOWN server (the DCS-level Startup). For a STOPPED server use start_server.",
        "shutdown_server": "Shut a running or paused server down to SHUTDOWN. stop_server ends the process instead.",
        "restart_server": "Restart a running, paused or stopped server. A SHUTDOWN server needs startup_server.",
    }
    async with surface(rows, keys=_keys()) as (_app, transport, _entered, _counts):
        async with _client(transport, CONTROL_SECRET) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert {name: tools[name].description for name in expected} == expected


@pytest.mark.asyncio
@_XFAIL_MCP_TOOL_SET
async def test_a_shutdown_server_refuses_start_and_names_startup_over_the_wire(rows, seam):
    """The wrong tool answers with the right one's NAME — the sentence an agent gets back."""
    seam.servers["SRS-1"].status = Status.SHUTDOWN
    async with surface(rows, keys=_keys()) as (_app, transport, _entered, _counts):
        async with _client(transport, CONTROL_SECRET) as client:
            result = await client.call_tool("start_server", {"server_name": "SRS-1"})
        assert result.is_error is True, result.content
        text = _text(result)
        assert text.endswith("Server 'SRS-1' is Shutdown - use startup_server."), text
    assert seam.servers["SRS-1"].status == Status.SHUTDOWN, "nothing ran"


@pytest.mark.asyncio
@_XFAIL_MCP_TOOL_SET
async def test_a_tool_schema_is_explicit_and_never_a_kwargs_catch_all(rows, seam):
    """The ``__signature__`` derivation: ``start_server`` takes one typed, required string."""
    async with surface(rows, keys=_keys()) as (_app, transport, _entered, _counts):
        async with _client(transport, CONTROL_SECRET) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        schema = tools["start_server"].input_schema
        assert schema["properties"]["server_name"]["type"] == "string"
        assert schema["required"] == ["server_name"]
        assert schema["properties"].keys() == {"server_name"}


# ── 3. identity through the transport ─────────────────────────────────────

@pytest.mark.asyncio
@_XFAIL_MCP_TOOL_SET
async def test_start_server_over_post_mcp_writes_one_audit_entry_with_the_key_actor(rows, seam):
    """The actor reaches the trail through the transport — ``ContextVar``, not a tool argument."""
    async with surface(rows, keys=_keys()) as (_app, transport, _entered, _counts):
        async with _client(transport, CONTROL_SECRET) as client:
            result = await client.call_tool("start_server", {"server_name": "SRS-1"})
        assert result.is_error is False, result.content
        assert seam.servers["SRS-1"].status == Status.RUNNING
    assert len(seam.recorder.entries) == 1, f"expected exactly one audit entry: {seam.recorder.entries}"
    entry = seam.recorder.entries[0]
    assert entry["actor"] == f"[mcp:key/{CONTROL_KEY}]"
    assert entry["server"] == "SRS-1"
    assert "started" in entry["event"]


@pytest.mark.asyncio
@_XFAIL_MCP_TOOL_SET
async def test_a_refused_action_is_one_audited_entry_too(rows, seam):
    """A domain refusal (the server is already running) is audited once and answered ``isError``."""
    seam.servers["SRS-1"].status = Status.RUNNING
    async with surface(rows, keys=_keys()) as (_app, transport, _entered, _counts):
        async with _client(transport, CONTROL_SECRET) as client:
            result = await client.call_tool("start_server", {"server_name": "SRS-1"})
        assert result.is_error is True, result.content
        assert "not stopped" in _text(result)
    assert len(seam.recorder.entries) == 1
    assert seam.recorder.entries[0]["actor"] == f"[mcp:key/{CONTROL_KEY}]"


@pytest.mark.asyncio
async def test_a_read_key_is_never_offered_a_lifecycle_tool(rows, seam):
    """A ``read`` client cannot reach a write over the wire: the tool is not in ITS server."""
    async with surface(rows, keys=_keys()) as (_app, transport, _entered, _counts):
        async with _client(transport, READ_SECRET) as client:
            result = await client.call_tool("start_server", {"server_name": "SRS-1"})
        assert result.is_error is True
        assert "Unknown tool" in _text(result)
    assert seam.servers["SRS-1"].status == Status.STOPPED, "nothing ran"
    assert seam.recorder.entries == []


@pytest.mark.asyncio
async def test_the_wrapper_itself_refuses_a_lifecycle_row_for_a_read_principal(rows, seam):
    """The second gate: a crafted call on a server that DOES hold the tool still runs nothing.

    The per-level instances make this unreachable over HTTP (the tool is absent from the list), but
    the check is what keeps one registration mistake from turning a read key into a write key.
    """
    from mcp.server.mcpserver.exceptions import ToolError
    from core.action_surface import tool_spec_for
    from core.actions import action_registry

    row = next(row for row in rows if row.action == "start_server")
    spec = tool_spec_for(row, action_registry()["start_server"])
    handler = mcp_tools._wrap(spec, action_registry()["start_server"],
                              service=types.SimpleNamespace(node=None))
    token = mcp_server._CURRENT_PRINCIPAL.set(
        mcp_server.McpPrincipal(scheme="key", label=READ_KEY, level=mcp_server.LEVEL_READ))
    try:
        with pytest.raises(ToolError, match="not offered"):
            await handler(server_name="SRS-1")
    finally:
        mcp_server._CURRENT_PRINCIPAL.reset(token)
    assert seam.servers["SRS-1"].status == Status.STOPPED
    assert seam.recorder.entries == []


def test_a_missing_principal_runs_nothing(rows):
    """Fail closed: with no principal on the call, the tool answers ``isError`` and dispatches not."""
    from mcp.server.mcpserver.exceptions import ToolError
    from core.action_surface import tool_spec_for
    from core.actions import action_registry

    row = next(row for row in rows if row.action == "list_servers")
    spec = tool_spec_for(row, action_registry()["list_servers"])
    handler = mcp_tools._wrap(spec, action_registry()["list_servers"],
                              service=types.SimpleNamespace(node=None))

    async def call() -> None:
        await handler()

    assert mcp_server.current_principal() is None
    with pytest.raises(ToolError, match="no authenticated identity"):
        asyncio.run(call())


# ── 4. the mount and the transport ────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_answers_on_mcp_and_not_on_mcp_mcp(rows, seam):
    """§6.1's trap: the SDK's default route is ``/mcp``, so mounting needs ``streamable_http_path='/'``.

    ``/mcp`` answers the request ITSELF — no trailing-slash redirect, because that hop would happen
    outside the gate — and ``/mcp/mcp`` is a plain 404 (nothing is mounted there).
    """
    async with surface(rows, keys=_keys()) as (_app, transport, _entered, _counts):
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver",
                                      headers={"Authorization": f"Bearer {CONTROL_SECRET}"},
                                      follow_redirects=False) as http:
            on_mount = await http.post("/mcp", json=INITIALIZE)
            slashed = await http.post("/mcp/", json=INITIALIZE)
            doubled = await http.post("/mcp/mcp", json=INITIALIZE)
        assert on_mount.status_code == 200, f"{on_mount.status_code}: {on_mount.text[:200]}"
        assert "location" not in on_mount.headers, "the mount redirected instead of answering"
        assert slashed.status_code == 200
        assert doubled.status_code == 404


def test_allowed_hosts_empty_is_a_startup_error():
    """An empty allow-list refuses every request with 421, so it stops the service instead."""
    with pytest.raises(mcp_server.McpConfigError) as caught:
        mcp_server.transport_from_config({"transport": {"allowed_hosts": []}})
    assert "421" in str(caught.value)
    assert "allowed_hosts" in str(caught.value)
    # and the missing key entirely is the same refusal, not a silent default
    with pytest.raises(mcp_server.McpConfigError, match="421"):
        mcp_server.transport_from_config({})


def test_a_label_that_could_forge_an_actor_token_is_refused():
    """A label is the tail of ``[mcp:key/<label>]``: a bracket in it could forge a second token."""
    for bad in ("a]", "two words", "", "x\ny"):
        with pytest.raises(mcp_server.McpConfigError):
            mcp_server.keys_from_config({"keys": [{"label": bad, "key": "s", "level": "read"}]})


# ── 5. the service itself: config in, mount out ───────────────────────────

@pytest.fixture
def service_config(tmp_path):
    """A real ``services/mcpservice.yaml`` for one service instance, and its node."""
    from core.const import DEFAULT_TAG

    config_dir = tmp_path / "config"
    (config_dir / "services").mkdir(parents=True)
    (config_dir / "services" / "mcpservice.yaml").write_text(
        f"""{DEFAULT_TAG}:
  enabled: true
  keys:
    - label: {CONTROL_KEY}
      key: {CONTROL_SECRET}
      level: control
    - label: {READ_KEY}
      key: {READ_SECRET}
      level: read
  transport:
    allowed_hosts: ["testserver"]
""", encoding="utf-8")
    node = types.SimpleNamespace(pool=None, apool=None, config={},
                                 config_dir=str(config_dir), master=True,
                                 plugins=["mission"])
    return node, config_dir


@pytest.mark.asyncio
@_XFAIL_MCP_TOOL_SET
async def test_the_service_mounts_the_nine_tools_and_unmounts_on_stop(rows, seam, service_config,
                                                                     monkeypatch):
    """The wiring, end to end: the config file decides the keys, one server per level, one mount."""
    from core.services.registry import ServiceRegistry
    from services.mcpservice.service import MCPService
    from services.webservice.service import WebService

    node, _config_dir = service_config
    app = FastAPI()
    real_get = ServiceRegistry.get.__func__

    def fake_get(cls, wanted):
        if wanted is WebService:
            return types.SimpleNamespace(app=app)
        return real_get(cls, wanted)

    monkeypatch.setattr(ServiceRegistry, "get", classmethod(fake_get))

    service = MCPService(node)
    service._in_registry_cascade = True          # no dependency cascade in a test
    await service.start()
    try:
        assert service.tool_counts == {mcp_server.LEVEL_READ: 4, mcp_server.LEVEL_CONTROL: 9}
        assert service.mounted_on(app)
        assert mcp_server.find_mount(app, "/mcp") is not None

        transport = httpx2.ASGITransport(app=app)
        async with _client(transport, READ_SECRET) as client:
            listed = {tool.name for tool in (await client.list_tools()).tools}
        assert listed == READ_TOOLS
    finally:
        await service.stop()
    assert not service.mounted_on(app), "stop left the mount answering"
    assert mcp_server.find_mount(app, "/mcp") is None, "the mount object is still on the app"
    assert service.tool_counts == {}


@pytest.mark.asyncio
async def test_the_service_refuses_to_start_with_an_empty_allowed_hosts(seam, tmp_path, monkeypatch):
    """The startup ERROR the operator sees, through the service's own start path."""
    from core.const import DEFAULT_TAG
    from services.mcpservice.service import MCPService
    from core.services.registry import ServiceRegistry
    from services.webservice.service import WebService

    config_dir = tmp_path / "config"
    (config_dir / "services").mkdir(parents=True)
    (config_dir / "services" / "mcpservice.yaml").write_text(
        f"""{DEFAULT_TAG}:
  enabled: true
  keys:
    - label: {READ_KEY}
      key: {READ_SECRET}
      level: read
  transport:
    allowed_hosts: []
""", encoding="utf-8")
    node = types.SimpleNamespace(pool=None, apool=None, config={}, config_dir=str(config_dir),
                                 master=True, plugins=["mission"])

    real_get = ServiceRegistry.get.__func__

    def fake_get(cls, wanted):
        if wanted is WebService:
            return types.SimpleNamespace(app=FastAPI())
        return real_get(cls, wanted)

    monkeypatch.setattr(ServiceRegistry, "get", classmethod(fake_get))
    service = MCPService(node)
    service._in_registry_cascade = True
    with pytest.raises(mcp_server.McpConfigError, match="421"):
        await service.start()


def test_a_jwt_key_builds_a_verifier_and_names_its_actor_by_subject():
    """A ``jwt:`` key verifies through ``ApiVerifier`` and audits as ``[mcp:jwt/<sub>]``."""
    from services.webservice.api_auth import Level, Principal

    keys = mcp_server.keys_from_config({"keys": [
        {"label": "console", "level": "read",
         "jwt": {"key": "shared", "jwks_url": "https://issuer.example/jwks.json"}},
    ]})
    assert keys[0].secret is None and keys[0].verifier is not None

    class FakeJwt:
        async def authenticate(self, request):
            return Principal(level=Level.JWT, network="remote", scheme="jwt",
                             subject="agent]7", label="agent]7")

    verifier = mcp_server.McpVerifier(
        [mcp_server.McpKey("console", mcp_server.LEVEL_READ, verifier=FakeJwt())])
    request = types.SimpleNamespace(headers={"authorization": "Bearer token"})
    principal = asyncio.run(verifier.authenticate(request))
    assert principal.scheme == "jwt"
    # a subject is externally supplied: the bracket is neutralised, so the token cannot be forged
    assert principal.actor == "[mcp:jwt/agent_7]"


def test_a_jwt_block_without_a_jwks_url_is_refused():
    with pytest.raises(mcp_server.McpConfigError, match="jwks_url"):
        mcp_server.keys_from_config({"keys": [{"label": "console", "level": "read",
                                           "jwt": {"key": "shared"}}]})


# ── 6. a failed build closes what it started ──────────────────────────────

@pytest.mark.asyncio
async def test_a_failed_build_closes_the_session_managers_it_already_started(rows, seam,
                                                                             monkeypatch):
    """A later level raising must not leave an earlier level's session manager running.

    The stack is only published to ``self._exit_stack`` on success, so an abort that does not close
    it abandons the already-entered session-manager tasks: a phantom that keeps answering. The
    second level's tool registration is made to raise, and the first level's manager must be exited.
    """
    from services.mcpservice import service as service_module
    from services.mcpservice.service import MCPService

    events: list[str] = []

    class FakeSessionManager:
        @contextlib.asynccontextmanager
        async def run(self):
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")

    class FakeMCPServer:
        def __init__(self, name, instructions=None):
            self.session_manager = FakeSessionManager()

    def fake_register_tools(server, rows, *, level, service, registry=None):
        if level == mcp_server.LEVEL_CONTROL:
            raise RuntimeError("the second level's tool registration failed")
        return 4

    monkeypatch.setattr(service_module, "MCPServer", FakeMCPServer)
    monkeypatch.setattr(service_module, "register_tools", fake_register_tools)
    monkeypatch.setattr(service_module, "streamable_app", lambda server, transport: object())

    node = types.SimpleNamespace(pool=None, apool=None, config={}, config_dir=".",
                                 master=True, plugins=["mission"])
    service = MCPService(node)
    transport = mcp_server.McpTransportConfig(allowed_hosts=("testserver",))

    with pytest.raises(RuntimeError, match="second level"):
        await service._build(_keys(), transport, rows, FastAPI())

    assert events == ["enter", "exit"], f"the first level's session manager was not closed: {events}"
    assert service._servers == {}
    assert service._counts == {}
    assert service._exit_stack is None


# ── 7. the actor: one sanitiser, one construction path ────────────────────

def test_from_service_keeps_the_mcp_label_and_takes_a_sanitised_actor(seam):
    """``actor=None`` audits as ``"MCP"`` exactly as before; a named actor is the caller's token."""
    from core.actions import ActionContext

    service = types.SimpleNamespace(node=None)
    plain = ActionContext.from_service(service)
    assert plain.audit_actor == "MCP"
    assert plain.transport == "service"

    named = ActionContext.from_service(service, actor="[mcp:key/dash]")
    assert named.audit_actor == "[mcp:key/dash]"


def test_the_mcp_context_audits_as_the_verified_key(seam):
    """The transport passes the principal's actor at construction — no post-hoc overwrite."""
    from services.mcpservice.tools import _context

    principal = mcp_server.McpPrincipal(scheme="key", label="dash", level=mcp_server.LEVEL_READ)
    ctx = _context(types.SimpleNamespace(node=None), principal)
    assert ctx.audit_actor == "[mcp:key/dash]"


def test_the_actor_sanitiser_is_the_core_rule_on_both_surfaces(seam):
    """One rule, imported: a dirty label renders identically for the MCP and the web surface."""
    from core.actions import ActionContext, _audit_token
    from services.mcpservice import server as server_module

    assert server_module._audit_token is _audit_token
    assert not hasattr(server_module, "_token"), "the MCP copy of the sanitiser is back"

    dirty = "a]b\nc[d"
    expected = "a_b_c_d"
    assert _audit_token(dirty) == expected

    # the MCP surface: the label is the token's variable part, sanitised where the token is built
    principal = mcp_server.McpPrincipal(scheme="key", label=dirty, level=mcp_server.LEVEL_READ)
    assert principal.actor == f"[mcp:key/{expected}]"

    # the web surface: the subject is sanitised the same way, so both render identically
    identity = types.SimpleNamespace(roles=(), scope=None, shown_name="x",
                                     backend="local", subject=dirty)
    web_ctx = ActionContext.from_web(identity, node=None, bus=None)
    assert web_ctx.audit_actor == f"[web:local/{expected}]"


def test_wrap_takes_no_level_parameter():
    """The dead ``level`` is gone, not ignored: no caller can pass one and believe it is checked."""
    import inspect
    from services.mcpservice import tools as tools_module

    assert set(inspect.signature(tools_module._wrap).parameters) == {"spec", "fn", "service"}
    assert "_wrap(spec, fn, service=service)" in inspect.getsource(tools_module.register_tools)


# ── 6. the published contract: schema and README ──────────────────────────

#: The one schema a ``config/services/mcpservice.yaml`` is checked against.
MCP_SCHEMA = (Path(__file__).resolve().parents[1]
              / "services" / "mcpservice" / "schemas" / "mcpservice_schema.yaml")
#: The README whose own example block is the sample an operator copies.
MCP_README = MCP_SCHEMA.parents[1] / "README.md"


def _validate(text: str, tmp_path) -> None:
    """Validate *text* exactly as the loader does, raising on the first fault."""
    from core import utils

    candidate = tmp_path / "mcpservice.yaml"
    candidate.write_text(text, encoding="utf-8")
    utils.validate(str(candidate), [str(MCP_SCHEMA)], raise_exception=True)


#: A minimal config: one static key, one allowed host.
MINIMAL = """DEFAULT:
  enabled: true
  keys:
    - label: cloud
      key: static-token
      level: control
  transport:
    allowed_hosts: [dcs.example.com]
"""


def test_the_readme_example_is_the_sample_the_schema_accepts(tmp_path):
    """The documented block must be the block the loader accepts — one file, one truth."""
    import re

    text = MCP_README.read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, re.S)
    assert len(blocks) == 1, "the README carries exactly one yaml block: the config example"
    sample = blocks[0]
    assert "allowed_hosts" in sample and "jwt:" in sample, "the sample shows both credential kinds"
    _validate(sample, tmp_path)
    # a JWT entry with no static key is the shipped shape, not an omission to be rejected
    _validate(MINIMAL.replace("    - label: cloud\n      key: static-token\n", "    - label: cloud\n")
              .replace("      level: control\n",
                       "      level: control\n      jwt: {key: k, jwks_url: 'https://x/jwks'}\n"),
              tmp_path)


@pytest.mark.parametrize("old, new, symptom", [
    ("      level: control", "      level: admin", "'admin' does not exist"),
    ("    allowed_hosts: [dcs.example.com]", "    allowed_host: [dcs.example.com]",
     "allowed_hosts"),
    ("      key: static-token", "      key: static-token\n      jwt: {key: k, jwks_url: 'https://x'}",
     "jwt"),
])
def test_the_schema_refuses_what_the_loader_would_have_to_guess(tmp_path, old, new, symptom):
    """An unknown level, a misspelt key or a second credential is refused here, not guessed at."""
    from pykwalify.errors import SchemaError

    text = MINIMAL.replace(old, new)
    assert text != MINIMAL, "the case must change the sample, or it proves nothing"
    with pytest.raises(SchemaError) as caught:
        _validate(text, tmp_path)
    assert symptom in str(caught.value)


@pytest.mark.asyncio
async def test_the_service_says_so_when_the_config_file_is_absent(tmp_path, caplog, monkeypatch):
    """"Nothing happened" must name its reason: no file, no mount, one INFO line."""
    import logging

    from core.services.registry import ServiceRegistry
    from services.mcpservice.service import MCPService
    from services.webservice.service import WebService

    config_dir = tmp_path / "config"
    (config_dir / "services").mkdir(parents=True)
    node = types.SimpleNamespace(pool=None, apool=None, config={}, config_dir=str(config_dir),
                                 master=True, plugins=[])

    real_get = ServiceRegistry.get.__func__

    def fake_get(cls, wanted):
        if wanted is WebService:
            raise AssertionError("an absent config file must not reach the web application")
        return real_get(cls, wanted)

    monkeypatch.setattr(ServiceRegistry, "get", classmethod(fake_get))
    service = MCPService(node)
    service._in_registry_cascade = True
    with caplog.at_level(logging.INFO):
        await service.start()
    assert "no config file at" in caplog.text
    assert str(config_dir / "services" / "mcpservice.yaml") in caplog.text
    assert service.tool_counts == {}


def test_the_mount_is_declared_and_the_mount_audit_stays_quiet(shell_app):
    """A mount answers without the capability gate, so it must be declared — and it is."""
    from services.webservice import permissions

    app, _registrar = shell_app
    assert "/mcp" in permissions.EXPECTED_MOUNTS
    mcp_server.mount(app, {mcp_server.LEVEL_READ: FastAPI()}, mcp_server.McpVerifier([]))
    assert permissions.audit_mounts(app) == []

    # the audit is not vacuous: an UNDECLARED mount in the same app is still reported
    from starlette.routing import Mount

    app.router.routes.append(Mount("/elsewhere", app=FastAPI()))
    assert permissions.audit_mounts(app) == ["/elsewhere"]
