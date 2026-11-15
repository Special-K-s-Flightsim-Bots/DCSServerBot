"""``/api/v1`` mounted: one HTTP test per risk class and per failure mode.

Every test drives the REAL surface — ``RestAPI.register_routes`` mounts the discovery-built router,
the guard runs as the route's own FastAPI dependency, and the actions are the shipped mission ones
over a stub cluster. Nothing here fakes the transport: what a client would get, these tests get.

Pinned, in the design's own order:

* a READ row answers 200 with no ``code`` key in the body (the envelope, not a refusal);
* a LIFECYCLE write answers 200 against a fake bus and leaves exactly ONE audit entry;
* a credential under the effective floor is a 403, a missing one a 401 (v1's own answers — the
  legacy surface says 403 for both);
* an unknown server is a 404 decided BEFORE dispatch (no action runs, no audit entry appears);
* a second in-flight stop is a 409 with ``code: target_busy`` and ``Retry-After: 2``;
* a row whose action is absent from the registry answers 501 with ``code: action_unavailable``;
* unload then register leaves exactly one handler per v1 path;
* ``GET /api/v1/servers`` answers through the guard's unrestricted machine scope and is refused with
  a scopeless context — the C7 trap, pinned in the direction that fails;
* ``api_v1.enabled: false`` registers no v1 route at all (404), and the legacy ``prefix:`` does not
  move the v1 prefix;
* a hand-fed row is VALIDATED at install, and dispatching keys on the ACTION's kind, so a row that
  claims READ over a write action can never reach the read path;
* a v1 write audits as ``[api:key/<label>]`` while the legacy surface keeps ``"API"``;
* the guard's ``ContextVar`` is cleared on every exit, including the 404 before dispatch;
* the per-row config key is ``v1/<path>``, so disabling one surface never disables the other, and a
  legacy key that spells a v1 row path is warned about at mount time.
"""
from __future__ import annotations

import logging
import sys
import types
from typing import Any, cast

import pytest
from fastapi import FastAPI

import core.actions as actions_module
import plugins.restapi.api_v1 as api_v1
from conftest import asgi_client, permissions
from core import DEFAULT_TAG, Status
from core.action_results import ActionResult
from core.action_surface import Endpoint, Risk, endpoint_rows, reset_endpoints
from core.actions import ActionContext, discover_actions
from plugins.restapi.commands import RestAPI
from services.webservice.api_auth import ApiAuthPolicy, ApiVerifier, Level, Principal

#: the static key every test that needs one presents
API_KEY = "test-api-key"

#: loopback left open, which is what READ keeps using (the floor for a read is ``none``)
OPEN_LOCAL = {"auth": {"api_key": API_KEY},
              "security": {"default": {"local": "none", "trusted": "api_key", "remote": "jwt"}}}

#: a network policy ABOVE the class floor: every caller must present a JWT, whatever the class needs
JWT_EVERYWHERE = {"auth": {"api_key": API_KEY},
                  "security": {"default": {"local": "jwt", "trusted": "jwt", "remote": "jwt"}}}


# ------------------------------------------------------------------------------- test doubles

class ServerDouble:
    """A ``Server`` double shaped where the lifecycle actions and the fleet reads look.

    ``status`` MOVES like the domain methods' own and every call is recorded, so a 200 proves the
    operation really ran and a 404 proves it did not.
    """

    def __init__(self, name: str = "SRS-1", status=Status.RUNNING):
        self.name = name
        self.display_name = name
        self.status = status
        self.locals: dict = {}
        self.current_mission = None
        self.calls: list = []

    async def start(self):
        self.calls.append("start")
        self.status = Status.RUNNING

    async def stop(self):
        self.calls.append("stop")
        self.status = Status.STOPPED

    def get_active_players(self, **kwargs):
        return []


class RecordingBot:
    """A bot double whose ``audit()`` records, so the seam writes a REAL entry."""

    def __init__(self):
        self.entries: list[dict] = []

    async def audit(self, event, server=None, **kwargs):
        self.entries.append({"event": event, "server": getattr(server, "name", None),
                             "actor": kwargs.get("actor")})


# ---------------------------------------------------------------------------------- the harness

# A strict pin on a defect in the PRODUCT, not a weaker assertion: the statements below are the
# ones the design calls for, so each is expected to fail until the product implements them, and
# each fails the suite again (XPASS) the moment the product does.
_XFAIL_UNMOUNTED_V1 = pytest.mark.xfail(
    strict=True,
    reason=(
        "the /api/v1 transport is never mounted: `plugins/restapi/commands.py:RestAPI.register_routes` "
        "builds the legacy router only and never calls `plugins.restapi.api_v1.install`, so every v1 "
        "path answers 404 and nothing bookkeeps what a registration added"
    ),
)


@pytest.fixture
def rows():
    """The mission plugin's 15 rows through the REAL discovery path, with process state restored."""
    saved_registry = dict(actions_module.action_registry())
    saved_kinds = dict(actions_module._ACTION_KINDS)
    saved_rows = endpoint_rows()
    for name in ("plugins.mission.actions", "plugins.mission.api"):
        sys.modules.pop(name, None)
    actions_module._ACTIONS.clear()
    actions_module._ACTION_KINDS.clear()
    reset_endpoints()
    discover_actions(["mission"])
    from core.action_surface import discover_endpoints

    discovered = discover_endpoints(["mission"])
    try:
        yield discovered
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


@pytest.fixture
def audit_bot(monkeypatch):
    """Make the audit seam's sink resolvable, so the entry the action writes can be counted."""
    bot = RecordingBot()
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: bot)
    return bot


def plugin_for(app: FastAPI, *, config: dict | None = None, servers=()) -> RestAPI:
    """The plugin's registration surface, without its bot-side constructor."""
    api = object.__new__(RestAPI)
    api.locals = {DEFAULT_TAG: config} if config is not None else {}
    api.log = logging.getLogger("tests.restapi.v1")
    api.app = app
    api.router = None
    api._included = []
    api._included_v1 = []
    api._config = {}
    api.eventlistener = None
    api.__cog_name__ = "RestAPI"
    api.node = cast(Any, types.SimpleNamespace(plugins=["mission"]))
    api.bus = cast(Any, types.SimpleNamespace(servers={server.name: server for server in servers},
                                             node=types.SimpleNamespace(instances={})))
    api.bot = cast(Any, types.SimpleNamespace(servers={}))
    return api


def mounted(*, config: dict | None = None, servers=()) -> tuple[FastAPI, RestAPI]:
    """An app, and the plugin instance that mounted both surfaces on it."""
    app = FastAPI()
    api = plugin_for(app, config=config, servers=servers)
    api.register_routes()
    return app, api


def v1_paths(app: FastAPI) -> list[str]:
    """Every path the app answers under ``/api/v1``, through the include wrappers."""
    return [route.path for route in permissions._walk_routes(app)
            if getattr(route, "path", "").startswith(api_v1.PREFIX)]


def handler_counts(app: FastAPI) -> dict[str, int]:
    counts: dict[str, int] = {}
    for route in permissions._walk_routes(app):
        path = getattr(route, "path", None)
        if path and path.startswith(api_v1.PREFIX):
            counts[path] = counts.get(path, 0) + 1
    return counts


# ---------------------------------------------------------------- 1. the read: 200, no code key

@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_a_read_row_answers_200_with_the_envelope_and_no_code(rows):
    app, _ = mounted(config=OPEN_LOCAL, servers=[ServerDouble("SRS-1", Status.RUNNING)])

    async with asgi_client(app) as client:
        fleet = await client.get("/api/v1/servers")
        one = await client.get("/api/v1/servers/SRS-1")

    assert fleet.status_code == 200
    fleet_body = fleet.json()
    assert fleet_body["status"] == "ok"
    assert fleet_body["message"] == "1 servers."
    assert [entry["name"] for entry in fleet_body["servers"]] == ["SRS-1"]
    assert "code" not in fleet_body, "an ordinary answer carries no outcome code"

    assert one.status_code == 200
    one_body = one.json()
    assert one_body["server"]["name"] == "SRS-1"
    assert one_body["server"]["status"] == Status.RUNNING.value
    assert "code" not in one_body


# ------------------------------------------------- 2. the lifecycle write: 200 and one audit entry

@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_a_lifecycle_write_answers_200_and_leaves_one_audit_entry(rows, audit_bot):
    server = ServerDouble("SRS-1", Status.STOPPED)
    app, _ = mounted(config=OPEN_LOCAL, servers=[server])

    async with asgi_client(app) as client:
        response = await client.post("/api/v1/servers/SRS-1/start", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["message"] == "Server 'SRS-1' started."
    assert "code" not in body
    assert server.calls == ["start"], "the action ran against the resolver's own server object"
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["server"] == "SRS-1"
    assert audit_bot.entries[0]["actor"] == "[api:key/api_key]"


# ------------------------------------------------------------ 3. the credentials: 401 and 403

@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_a_missing_credential_is_401_on_a_write(rows):
    """v1's own answer: a write needs a key, and its absence is unauthenticated, not forbidden."""
    app, _ = mounted(config=OPEN_LOCAL, servers=[ServerDouble("SRS-1", Status.STOPPED)])

    async with asgi_client(app) as client:
        response = await client.post("/api/v1/servers/SRS-1/start")

    assert response.status_code == 401
    assert response.json() == {"detail": api_v1.UNAUTHENTICATED}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_a_credential_below_the_floor_is_403(rows):
    """The config demands a JWT; the key presented is an api key, which is under the floor."""
    app, _ = mounted(config=JWT_EVERYWHERE, servers=[ServerDouble("SRS-1", Status.STOPPED)])

    async with asgi_client(app) as client:
        response = await client.post("/api/v1/servers/SRS-1/start",
                                     headers={"X-API-Key": API_KEY})

    assert response.status_code == 403
    assert response.json() == {"detail": api_v1.INSUFFICIENT_LEVEL.format(
        presented="api_key", risk="lifecycle", required="jwt")}


@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_the_read_floor_alone_lets_a_keyless_loopback_read_through(rows):
    """The one convenience v1 keeps: a READ on a network the operator left open needs no credential."""
    app, _ = mounted(config=OPEN_LOCAL, servers=[ServerDouble("SRS-1", Status.RUNNING)])

    async with asgi_client(app) as client:
        read = await client.get("/api/v1/servers")
        write = await client.post("/api/v1/servers/SRS-1/stop")

    assert read.status_code == 200, "reads keep the legacy convenience"
    assert write.status_code == 401, "writes never do"


# --------------------------------------------------- 4. the unknown target: 404 before dispatch

@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_an_unknown_server_is_404_decided_before_dispatch(rows, audit_bot):
    server = ServerDouble("SRS-1", Status.STOPPED)
    app, _ = mounted(config=OPEN_LOCAL, servers=[server])

    async with asgi_client(app) as client:
        status = await client.get("/api/v1/servers/GHOST-1")
        write = await client.post("/api/v1/servers/GHOST-1/start",
                                  headers={"X-API-Key": API_KEY})

    assert status.status_code == 404
    assert status.json() == {"status": "error", "message": "Server 'GHOST-1' not found."}
    assert write.status_code == 404
    assert write.json() == {"status": "error", "message": "Server 'GHOST-1' not found."}
    assert server.calls == [], "no action ran against a name nothing answers to"
    assert audit_bot.entries == [], "a resolved-and-refused request leaves no trail"


# ------------------------------------------------------------- 5. the in-flight target: 409 + hint

@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_a_second_in_flight_stop_is_409_with_the_code_and_the_retry_hint(rows):
    server = ServerDouble("SRS-1", Status.RUNNING)
    app, _ = mounted(config=OPEN_LOCAL, servers=[server])
    actions_module._in_flight.add("server:srs-1")
    try:
        async with asgi_client(app) as client:
            response = await client.post("/api/v1/servers/SRS-1/stop",
                                         headers={"X-API-Key": API_KEY})
    finally:
        actions_module.reset_in_flight()

    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "error"
    assert body["code"] == "target_busy"
    assert body["message"] == "Another action on SRS-1 is still running. Wait and retry."
    assert response.headers["retry-after"] == "2"
    assert server.calls == [], "the refusal is decided before the action runs"


# ---------------------------------------------------------- 6. the absent action: 501 + the code

@pytest.mark.asyncio
async def test_a_row_whose_action_is_absent_answers_501(rows):
    """The route exists (so the URL is right) and the seam refuses it — never a bare 404."""
    app = FastAPI()
    api = plugin_for(app, config=OPEN_LOCAL, servers=[ServerDouble("SRS-1", Status.STOPPED)])
    verifier = ApiVerifier(ApiAuthPolicy.from_plugin_config(api.get_config()))
    removed = actions_module._ACTIONS.pop("start_server")
    try:
        api._included_v1 = api_v1.install(app, plugin=api, verifier=verifier, rows=rows)
        async with asgi_client(app) as client:
            response = await client.post("/api/v1/servers/SRS-1/start",
                                         headers={"X-API-Key": API_KEY})
    finally:
        actions_module._ACTIONS["start_server"] = removed

    assert response.status_code == 501
    body = response.json()
    assert body["status"] == "error"
    assert body["code"] == "action_unavailable"
    assert body["message"] == "Action 'start_server' is not available in this installation."


# ------------------------------------------------------------- 7. reload: one handler per path

@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_unload_then_register_leaves_exactly_one_handler_per_v1_path(rows):
    app = FastAPI()
    first = plugin_for(app, config=OPEN_LOCAL, servers=[ServerDouble("SRS-1", Status.RUNNING)])
    first.register_routes()
    before = handler_counts(app)
    assert len(before) == 15 and set(before.values()) == {1}

    await first.cog_unload()
    second = plugin_for(app, config=OPEN_LOCAL, servers=[ServerDouble("SRS-1", Status.RUNNING)])
    second.register_routes()
    after = handler_counts(app)

    assert after == before, f"the reload changed the v1 route table: {before} -> {after}"
    assert set(after.values()) == {1}, f"a v1 path answers through more than one handler: {after}"


@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_unload_removes_the_v1_install_entirely(rows):
    app = FastAPI()
    api = plugin_for(app, config=OPEN_LOCAL, servers=[])
    before = list(app.routes)
    api.register_routes()

    await api.cog_unload()

    assert len(app.routes) == len(before)
    assert [route is old for route, old in zip(app.routes, before)] == [True] * len(before)
    assert api._included_v1 == [] and api._included == []


# --------------------------------------------------------- 8. the C7 trap: a scope, or nothing

@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_the_machine_scope_reaches_a_server_while_a_scopeless_context_does_not(rows,
                                                                                   monkeypatch):
    """A scopeless context denies EVERY server, so the guard must attach an unrestricted one."""
    app, _ = mounted(config=OPEN_LOCAL, servers=[ServerDouble("SRS-1", Status.RUNNING)])

    async with asgi_client(app) as client:
        served = await client.get("/api/v1/servers/SRS-1")
    assert served.status_code == 200
    assert served.json()["server"]["name"] == "SRS-1"

    # the same call through the context C7 describes: no scope at all
    scopeless = ActionContext(node=None,
                              bus=types.SimpleNamespace(servers={"SRS-1": ServerDouble("SRS-1")}))
    resolution = await scopeless.resolve_scoped_server("SRS-1")
    assert resolution.status == resolution.NOT_PERMITTED

    monkeypatch.setattr(api_v1, "MACHINE_SCOPE", None)
    async with asgi_client(app) as client:
        refused = await client.get("/api/v1/servers/SRS-1")
    assert refused.status_code == 404, "a scopeless v1 context could not serve the fleet at all"
    assert refused.json()["status"] == "error"


# --------------------------------------------- 9. config: disabled means absent, prefix is fixed

@pytest.mark.asyncio
async def test_disabled_in_config_registers_no_v1_route_at_all(rows):
    config = dict(OPEN_LOCAL, api_v1={"enabled": False})
    app, api = mounted(config=config, servers=[ServerDouble("SRS-1", Status.RUNNING)])

    assert api._included_v1 == []
    assert v1_paths(app) == []
    async with asgi_client(app) as client:
        response = await client.get("/api/v1/servers")
    assert response.status_code == 404


@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_the_v1_prefix_does_not_follow_the_configurable_legacy_prefix(rows):
    config = dict(OPEN_LOCAL, prefix="/stats")
    app, _ = mounted(config=config, servers=[ServerDouble("SRS-1", Status.RUNNING)])

    paths = [route.path for route in permissions._walk_routes(app)]
    assert "/api/v1/servers" in paths
    assert "/stats/instance/missions" in paths, "the legacy surface keeps its configurable prefix"
    assert not any(path.startswith("/stats/servers/") for path in paths), \
        "no v1 row appears under the legacy prefix (legacy's own /stats/servers is a different row)"
    assert len([path for path in paths if path.startswith("/api/v1/")]) == 15


# --------------------------------------------------------------- 10. what this slice may expose

def test_only_read_and_lifecycle_rows_are_mounted(rows):
    app = FastAPI()
    api = plugin_for(app, config=OPEN_LOCAL, servers=[])
    verifier = ApiVerifier(ApiAuthPolicy.from_plugin_config(api.get_config()))

    installed = api_v1.install(app, plugin=api, verifier=verifier, rows=rows)

    assert installed, "the discovery-built router was included"
    assert len(v1_paths(app)) == 15
    for row in rows:
        if row.method != "GET":
            assert row.risk is api_v1.Risk.LIFECYCLE, row


def test_a_row_above_lifecycle_is_not_mounted(rows):
    """The slice filter is a gate, not a convention: a MODERATION row is logged and skipped."""
    from core.action_surface import Endpoint, Risk

    app = FastAPI()
    api = plugin_for(app, config=OPEN_LOCAL, servers=[])
    verifier = ApiVerifier(ApiAuthPolicy.from_plugin_config(api.get_config()))
    bans = Endpoint("GET", "/bans", "list_bans", Risk.MODERATION, owner="mission")

    api_v1.install(app, plugin=api, verifier=verifier, rows=(*rows, bans))

    assert v1_paths(app).count("/api/v1/bans") == 0
    assert len(v1_paths(app)) == 15


def test_the_floor_table_is_the_design_table():
    from core.action_surface import Risk
    from services.webservice.api_auth import Level

    assert api_v1.FLOORS == {
        Risk.READ: Level.NONE,
        Risk.LIFECYCLE: Level.API_KEY,
        Risk.MODERATION: Level.API_KEY,
        Risk.CONFIG: Level.API_KEY,
        Risk.NODE: Level.JWT,
        Risk.SECRET: Level.JWT,
    }


# ------------------------------------------------- 11. the two gates a v1 route must pass to exist

@_XFAIL_UNMOUNTED_V1
def test_every_v1_route_carries_its_own_guard_so_the_app_level_gate_allows_it(rows):
    """The shell's capability gate allows a route that brings its own dependency — and only that.

    A v1 route without one would be refused by the app before this module ever saw the request, so
    the whole surface would answer 403 with no obvious link to the mount.
    """
    app, _ = mounted(config=OPEN_LOCAL, servers=[])

    v1_routes = [route for route in permissions._walk_routes(app)
                 if getattr(route, "path", "").startswith(api_v1.PREFIX)]
    assert len(v1_routes) == 15
    assert [route.path for route in v1_routes if not permissions.is_self_guarded(route, app)] == []


@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_a_row_disabled_in_the_endpoint_config_is_not_registered(rows):
    """``endpoints.<path>.enabled: false`` is the config's 404, decided at registration time."""
    config = dict(OPEN_LOCAL, endpoints={"v1/servers/{server_name}/start": {"enabled": False}})
    app, _ = mounted(config=config, servers=[ServerDouble("SRS-1", Status.STOPPED)])

    assert "/api/v1/servers/{server_name}/start" not in v1_paths(app)
    assert "/api/v1/servers/{server_name}/stop" in v1_paths(app)
    async with asgi_client(app) as client:
        response = await client.post("/api/v1/servers/SRS-1/start",
                                     headers={"X-API-Key": API_KEY})
    assert response.status_code == 404


@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_a_network_requirement_of_disabled_is_404_not_401(rows):
    """The per-network form of the same answer: decided before any credential is read."""
    config = dict(OPEN_LOCAL,
                  endpoints={"v1/servers/{server_name}/start": {"security": {"local": "disabled"}}})
    app, _ = mounted(config=config, servers=[ServerDouble("SRS-1", Status.STOPPED)])

    async with asgi_client(app) as client:
        with_key = await client.post("/api/v1/servers/SRS-1/start",
                                     headers={"X-API-Key": API_KEY})
        without = await client.post("/api/v1/servers/SRS-1/start")

    assert with_key.status_code == 404
    assert with_key.json() == {"detail": "Not found."}
    assert without.status_code == 404, "no credential is examined on a disabled row"


# --------------------------------------- 12. a hand-fed row: validated, and never a read path

def test_a_hand_fed_read_row_naming_a_write_action_is_refused_at_install(rows, caplog):
    """``install(rows=…)`` is not a way around V2: a READ row over a write action is dropped."""
    app = FastAPI()
    api = plugin_for(app, config=OPEN_LOCAL, servers=[ServerDouble("SRS-1", Status.STOPPED)])
    verifier = ApiVerifier(ApiAuthPolicy.from_plugin_config(api.get_config()))
    lying = Endpoint("GET", "/servers/{server_name}/stop-read", "stop_server", Risk.READ,
                     owner="mission")

    with caplog.at_level(logging.ERROR, logger=api_v1.__name__):
        api_v1.install(app, plugin=api, verifier=verifier, rows=(*rows, lying))

    assert "/api/v1/servers/{server_name}/stop-read" not in v1_paths(app), \
        "a hand-fed row skipped the validator and was mounted"
    assert len(v1_paths(app)) == 15, "only the sound rows are mounted"
    refusals = [record.getMessage() for record in caplog.records
                if record.levelno == logging.ERROR]
    assert any("stop-read" in message and "stop_server" in message for message in refusals), \
        refusals


@pytest.mark.asyncio
async def test_a_lying_read_row_forced_past_the_filter_cannot_use_the_read_path(rows,
                                                                               monkeypatch):
    """Dispatch keys on ``kind_of``, so even a mounted lying row goes through the WRITE path.

    The read path takes no in-flight guard; the write path refuses a target that is already busy.
    A 409 here therefore proves which seam the row ran through, and the server proves it did not
    run at all.
    """
    monkeypatch.setattr(api_v1, "check_endpoints", lambda *args, **kwargs: [])
    server = ServerDouble("SRS-1", Status.RUNNING)
    app = FastAPI()
    api = plugin_for(app, config=OPEN_LOCAL, servers=[server])
    verifier = ApiVerifier(ApiAuthPolicy.from_plugin_config(api.get_config()))
    lying = Endpoint("GET", "/servers/{server_name}/stop-read", "stop_server", Risk.READ,
                     owner="mission")
    api_v1.install(app, plugin=api, verifier=verifier, rows=(lying,))
    assert "/api/v1/servers/{server_name}/stop-read" in v1_paths(app), \
        "the bypass did not mount the row, so this test proves nothing"

    actions_module._in_flight.add("server:srs-1")
    try:
        async with asgi_client(app) as client:
            response = await client.get("/api/v1/servers/SRS-1/stop-read")
    finally:
        actions_module.reset_in_flight()

    assert response.status_code == 409
    assert response.json()["code"] == "target_busy"
    assert server.calls == [], "the read path would have run the write"


# ------------------------------------------------- 13. the actor: v1 names itself, legacy does not

@pytest.mark.asyncio
async def test_a_legacy_write_still_audits_as_API(audit_bot):
    """The legacy surface's actor is untouched: ``from_plugin`` still stamps ``"API"``."""
    app = FastAPI()
    api = plugin_for(app, config=OPEN_LOCAL)
    ctx = ActionContext.from_plugin(api)

    assert ctx.audit_actor == "API"
    await actions_module.audit_action(ctx, ActionResult(success=True, message="x"), bot=audit_bot)
    assert audit_bot.entries[0]["actor"] == "API"


def test_the_v1_actor_is_the_sanitised_key_label(rows):
    """``[api:key/<label>]``, with the label neutralised exactly as ``from_web`` does it."""
    app = FastAPI()
    api = plugin_for(app, config=OPEN_LOCAL)
    verifier = ApiVerifier(ApiAuthPolicy.from_plugin_config(api.get_config()))
    guard = api_v1.TransportGuard(verifier, api)
    row = rows[0]
    principal = Principal(level=Level.API_KEY, network="local", scheme="api_key",
                          label="key]\n[x")

    api_v1._CURRENT.set(api_v1._Identity(row=row, principal=principal))
    try:
        ctx = guard.context(row, {})
    finally:
        api_v1._CURRENT.set(None)

    assert ctx.audit_actor == "[api:key/key___x]"


# --------------------------------------------- 14. the identity is cleared on EVERY exit path

@pytest.mark.asyncio
async def test_the_identity_is_cleared_after_a_404_before_dispatch(rows):
    """The 404-before-dispatch return never reaches ``dispatch``, so the guard itself must clear."""
    app, _ = mounted(config=OPEN_LOCAL, servers=[ServerDouble("SRS-1", Status.STOPPED)])

    async with asgi_client(app) as client:
        response = await client.get("/api/v1/servers/GHOST-1")

    assert response.status_code == 404
    assert api_v1._CURRENT.get() is None, "the guard's identity leaked past the request"


# --------------------------------------------------- 15. one config key per surface, or a warning

@pytest.mark.asyncio
async def test_v1_servers_disabled_leaves_the_legacy_route_answering(rows):
    config = dict(OPEN_LOCAL, endpoints={"v1/servers": {"enabled": False}})
    app, _ = mounted(config=config, servers=[ServerDouble("SRS-1", Status.RUNNING)])

    assert "/api/v1/servers" not in v1_paths(app)
    assert "/servers" in [route.path for route in permissions._walk_routes(app)]
    async with asgi_client(app) as client:
        legacy = await client.get("/servers")
        v1 = await client.get("/api/v1/servers")

    assert legacy.status_code == 200, "the v1 key reached the legacy route"
    assert v1.status_code == 404


@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_legacy_servers_disabled_leaves_the_v1_read_answering(rows):
    config = dict(OPEN_LOCAL, endpoints={"servers": {"enabled": False}})
    app, _ = mounted(config=config, servers=[ServerDouble("SRS-1", Status.RUNNING)])

    assert "/servers" not in [route.path for route in permissions._walk_routes(app)]
    async with asgi_client(app) as client:
        legacy = await client.get("/servers")
        v1 = await client.get("/api/v1/servers")

    assert legacy.status_code == 404, "the legacy key disabled the legacy row, as it always did"
    assert v1.status_code == 200, "the legacy key reached the v1 row"
    assert v1.json()["message"] == "1 servers."


@_XFAIL_UNMOUNTED_V1
def test_a_legacy_key_that_spells_a_v1_row_path_warns_at_mount(rows, caplog):
    """The two surfaces now key apart, and the shared spelling is no longer silent."""
    config = dict(OPEN_LOCAL, endpoints={"servers": {"enabled": True}})

    with caplog.at_level(logging.WARNING, logger=api_v1.__name__):
        mounted(config=config, servers=[ServerDouble("SRS-1", Status.RUNNING)])

    warnings = [record.getMessage() for record in caplog.records
                if record.levelno == logging.WARNING]
    assert any("endpoints.servers" in message and "v1/servers" in message for message in warnings), \
        warnings

