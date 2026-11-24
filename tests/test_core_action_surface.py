"""The endpoint surface in ``core``: the row type, the registry, the validator, the builder.

Every proof here runs over the row LIST — the static checks need no FastAPI boot, and the builder is
exercised through a FastAPI ``TestClient`` with a fake guard, so no real action, no plugin row and no
authentication stack is involved.

The rows in this file exist only in the test: ``core/`` itself declares none, which the last tests
pin both at runtime and over the source tree.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
from dataclasses import dataclass

import pytest

from core.action_results import ActionResult, MissionControlResult, ServerControlResult
from core.action_surface import (
    ACTION_FAILED,
    ACTION_UNAVAILABLE,
    READ,
    TARGET_BUSY,
    WRITE,
    Endpoint,
    Risk,
    api_v1,
    body_of,
    build_router,
    build_route,
    check_endpoints,
    discover_endpoints,
    endpoint,
    endpoint_rows,
    envelope_model_for,
    parameter_docs,
    parameters_for,
    register_endpoint,
    reset_endpoints,
    status_for,
    tool_spec_for,
    validate_endpoints,
)
from core.actions import ServerResolution

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── action doubles ────────────────────────────────────────────────────────

async def start_server(ctx, server_name: str) -> ServerControlResult:
    """Start a stopped DCS server."""
    return ServerControlResult(success=True, server_name=server_name)


async def restart_mission(ctx, server_name: str, modify_mission: bool = True,
                          use_orig: bool = True) -> MissionControlResult:
    """Restart the current mission on a server.

    Args:
        server_name: the server to act on.
        modify_mission: rewrite the mission before restarting.
        use_orig: restart from the original file.
    """
    return MissionControlResult(success=True, server_name=server_name)


async def list_servers(ctx) -> ServerControlResult:
    """List every server this process knows."""
    return ServerControlResult(success=True)


async def read_weather(ctx, server_name: str, mission: str = "",
                       original: bool = False) -> MissionControlResult:
    """Read a mission's weather."""
    return MissionControlResult(success=True)


async def needs_a_body(ctx, server_name: str, mission: str) -> MissionControlResult:
    """A read that cannot complete from a query string."""
    return MissionControlResult(success=True)


async def returns_bytes(ctx, server_name: str) -> bytes:
    """Not an ActionResult."""
    return b""


REGISTRY = {
    "start_server": start_server,
    "restart_mission": restart_mission,
    "list_servers": list_servers,
    "read_weather": read_weather,
    "needs_a_body": needs_a_body,
    "returns_bytes": returns_bytes,
}

KINDS = {
    "start_server": WRITE,
    "restart_mission": WRITE,
    "list_servers": READ,
    "read_weather": READ,
    "needs_a_body": READ,
    "returns_bytes": READ,
}


def kind_of(qualname: str) -> str:
    return KINDS[qualname]


def validate(rows):
    """Validate against the test registry and kinds."""
    return validate_endpoints(rows, registry=REGISTRY, kind_of=kind_of)


def codes(rows) -> list[str]:
    return [problem.code for problem in check_endpoints(rows, registry=REGISTRY, kind_of=kind_of)]


GOOD_WRITE = Endpoint("POST", "/servers/{server_name}/start", "start_server", Risk.LIFECYCLE,
                      owner="mission")
GOOD_READ = Endpoint("GET", "/servers/{server_name}/weather", "read_weather", Risk.READ,
                     owner="mission")
GOOD_LIST = Endpoint("GET", "/servers", "list_servers", Risk.READ, owner="mission")
GOOD_RESTART = Endpoint("POST", "/servers/{server_name}/mission/restart", "restart_mission",
                        Risk.LIFECYCLE, owner="mission")


@pytest.fixture(autouse=True)
def no_rows():
    """Every test starts and ends with an empty registry."""
    reset_endpoints()
    yield
    reset_endpoints()


# ── the row type ──────────────────────────────────────────────────────────

def test_the_method_is_upper_case_and_one_of_the_four():
    for method in ("GET", "POST", "PUT", "DELETE"):
        assert Endpoint(method, "/x", "start_server", Risk.LIFECYCLE).method == method
    for method in ("get", "POST ", "PATCH", ""):
        with pytest.raises(ValueError):
            Endpoint(method, "/x", "start_server", Risk.LIFECYCLE)


def test_the_owner_is_not_part_of_equality():
    """A row is the SAME row before and after discovery stamps its owner."""
    stamped = Endpoint("POST", "/servers/{server_name}/start", "start_server", Risk.LIFECYCLE,
                       owner="mission")
    assert GOOD_WRITE == stamped


# ── the validator ─────────────────────────────────────────────────────────

def test_good_rows_survive():
    assert validate([GOOD_WRITE, GOOD_READ, GOOD_LIST]) == (GOOD_WRITE, GOOD_READ, GOOD_LIST)
    assert codes([GOOD_WRITE, GOOD_READ, GOOD_LIST]) == []


def test_v1_an_unknown_action_is_refused_and_the_good_rows_survive():
    unknown = Endpoint("POST", "/servers/{server_name}/unknown", "no_such_action",
                       Risk.LIFECYCLE, owner="mission")
    assert validate([GOOD_WRITE, unknown]) == (GOOD_WRITE,)
    assert codes([GOOD_WRITE, unknown]) == ["V1"]


def test_v7_a_write_row_without_a_risk_class_is_refused():
    without_risk = Endpoint("POST", "/servers/{server_name}/riskless", "start_server", None,
                            owner="mission")
    assert validate([GOOD_WRITE, without_risk]) == (GOOD_WRITE,)
    assert codes([without_risk]) == ["V7"]


def test_v7_an_unknown_risk_class_is_refused():
    bogus = Endpoint("POST", "/servers/{server_name}/bogus", "start_server", "dangerous",
                     owner="mission")
    assert codes([bogus]) == ["V7"]


def test_v7_a_secret_row_never_reaches_an_agent():
    secret = Endpoint("POST", "/servers/{server_name}/secret", "start_server", Risk.SECRET,
                      owner="mission")
    assert codes([secret]) == ["V7"]
    assert secret not in validate([GOOD_WRITE, secret])


def test_v2_a_read_row_never_names_a_write_action():
    mislabelled = Endpoint("GET", "/servers/{server_name}/state", "start_server", Risk.READ,
                           owner="mission")
    assert codes([GOOD_READ, mislabelled]) == ["V2"]
    assert validate([GOOD_READ, mislabelled]) == (GOOD_READ,)


def test_v2_a_write_row_never_names_a_read_action():
    """The other direction: a read under the in-flight guard and a floor it does not need."""
    mislabelled = Endpoint("POST", "/servers/list", "list_servers", Risk.LIFECYCLE,
                           owner="mission")
    assert codes([GOOD_WRITE, mislabelled]) == ["V2"]
    assert validate([GOOD_WRITE, mislabelled]) == (GOOD_WRITE,)


def test_v5_no_two_rows_share_method_and_path_across_owners():
    second = Endpoint("POST", "/servers/{server_name}/start", "restart_mission", Risk.LIFECYCLE,
                      owner="admin")
    problems = check_endpoints([GOOD_WRITE, second], registry=REGISTRY, kind_of=kind_of)
    assert [problem.code for problem in problems] == ["V5"]
    assert problems[0].index == 1, "the LATER row is the offending one"
    assert "admin" in str(problems[0])
    assert validate([GOOD_WRITE, second]) == (GOOD_WRITE,)


def test_v5_the_same_path_under_another_method_is_fine():
    other = Endpoint("GET", "/servers/{server_name}/start", "read_weather", Risk.READ,
                     owner="mission")
    assert codes([GOOD_WRITE, other]) == []


def test_v6_no_two_rows_share_a_tool_name():
    alias = Endpoint("GET", "/servers/{server_name}/start/again", "start_server", Risk.LIFECYCLE,
                     owner="mission")
    assert codes([GOOD_WRITE, alias]) == ["V6"]
    # the same action on a row that is NOT an MCP tool does not collide
    silent = Endpoint("GET", "/servers/{server_name}/start/again", "start_server", Risk.LIFECYCLE,
                      owner="mission", mcp=False)
    assert codes([GOOD_WRITE, silent]) == []


def test_v3_a_path_segment_must_be_a_required_parameter_of_the_action():
    not_a_param = Endpoint("POST", "/servers/{server_name}/nodes/{node}", "start_server",
                           Risk.LIFECYCLE, owner="mission")
    assert codes([not_a_param]) == ["V3"]

    defaulted = Endpoint("POST", "/servers/{server_name}/missions/{use_orig}",
                         "restart_mission", Risk.LIFECYCLE, owner="mission")
    assert codes([defaulted]) == ["V3"]


def test_v4_a_get_cannot_require_a_parameter_it_has_no_room_for():
    body_less = Endpoint("GET", "/servers/{server_name}/needs", "needs_a_body", Risk.READ,
                         owner="mission")
    assert codes([GOOD_READ, body_less]) == ["V4"]
    assert validate([GOOD_READ, body_less]) == (GOOD_READ,)


def test_v8_the_path_is_relative_to_the_prefix():
    for path in ("servers", "/../servers", "/api/v1/servers", "/mcp/tools"):
        row = Endpoint("GET", path, "list_servers", Risk.READ, owner="mission")
        assert "V8" in codes([row]), path


def test_v10_a_row_without_an_owner_is_refused():
    orphan = Endpoint("GET", "/servers", "list_servers", Risk.READ)
    assert codes([orphan]) == ["V10"]
    assert validate([orphan]) == ()


def test_v11_a_response_class_must_be_an_action_result():
    wrong_return = Endpoint("GET", "/servers/{server_name}/bytes", "returns_bytes", Risk.READ,
                            owner="mission")
    assert "V11" in codes([wrong_return])

    wrong_declared = Endpoint("GET", "/servers", "list_servers", Risk.READ, owner="mission",
                              response=dict)
    assert "V11" in codes([wrong_declared])


def test_v9_a_row_must_not_decorate_an_action():
    async def stub() -> None:
        """A declaration stub carrying a real action's name."""

    stub.__qualname__ = "list_servers"
    stub_result = endpoint("GET", "/servers/all", action="read_weather", risk=Risk.READ)(stub)
    assert stub_result is stub, "the decorator hands the stub back"
    stub_row = endpoint_rows()[0]
    assert "V9" in [problem.code for problem in
                    check_endpoints([stub_row], registry=REGISTRY, kind_of=kind_of)]


def test_the_default_kind_of_fails_safe_to_write():
    """``core.actions`` carries no read/write metadata yet, so every action answers WRITE.

    A READ row is then refused rather than served unguarded: a write exposed as a read would skip
    both the level floor and the audit.
    """
    from core.action_surface import _default_kind_of

    assert _default_kind_of()("start_server") == WRITE
    assert validate([GOOD_WRITE]) == (GOOD_WRITE,)


# ── the decorator form ────────────────────────────────────────────────────

def test_the_decorator_appends_one_row_and_returns_the_stub():
    @api_v1.post("/servers/{server_name}/start", action="start_server", risk=Risk.LIFECYCLE,
                 tags=("lifecycle",))
    async def _start() -> None:
        """Start a stopped DCS server."""

    rows = endpoint_rows()
    assert len(rows) == 1
    assert rows[0] == Endpoint("POST", "/servers/{server_name}/start", "start_server",
                               Risk.LIFECYCLE, summary="Start a stopped DCS server.",
                               tags=("lifecycle",), owner="")
    assert callable(_start), "the stub is returned unchanged, never replaced by the row"


def test_the_four_verbs_bind_the_method():
    async def _stub() -> None:
        """A declaration stub."""

    for verb, method in (("get", "GET"), ("post", "POST"), ("put", "PUT"), ("delete", "DELETE")):
        reset_endpoints()
        getattr(api_v1, verb)("/thing", action="start_server", risk=Risk.LIFECYCLE)(_stub)
        assert endpoint_rows()[0].method == method


def test_registration_order_is_the_registry_order():
    register_endpoint(GOOD_WRITE)
    register_endpoint(GOOD_READ)
    assert endpoint_rows() == (GOOD_WRITE, GOOD_READ)


# ── discovery ─────────────────────────────────────────────────────────────

def test_discovery_is_quiet_about_a_plugin_without_an_api_module():
    assert discover_endpoints(["this_plugin_has_no_api_module"], registry=REGISTRY,
                              kind_of=kind_of) == ()
    assert endpoint_rows() == ()


def test_discovery_stamps_the_owner_of_the_rows_it_imported(monkeypatch):
    """``owner`` comes from the package the rows were imported out of, never from the row."""

    class _OneModuleImportlib:
        """``importlib`` that runs the stub plugin's declaration instead of importing it."""

        def import_module(self, name: str):
            assert name == "plugins.stubplugin.api"
            register_endpoint(Endpoint("GET", "/servers", "list_servers", Risk.READ))
            return object()

    monkeypatch.setattr("core.action_surface.importlib", _OneModuleImportlib())
    assert discover_endpoints(["stubplugin"], registry=REGISTRY, kind_of=kind_of) == (
        Endpoint("GET", "/servers", "list_servers", Risk.READ, owner="stubplugin"),)


# ── the derivation ────────────────────────────────────────────────────────

def test_parameters_for_is_explicit_and_typed():
    params = parameters_for(GOOD_RESTART, restart_mission)
    assert [param.name for param in params] == ["server_name", "modify_mission", "use_orig"]
    assert all(param.kind is not param.VAR_KEYWORD for param in params)
    assert params[0].annotation is str
    assert params[1].annotation is bool
    assert params[0].default is params[0].empty, "the path segment is required"
    assert params[1].default is True


def test_parameters_for_never_returns_a_catch_all():
    """A ``**kwargs`` on the action is skipped, never projected onto the wire."""
    async def loose(ctx, server_name: str, **kwargs) -> ServerControlResult:
        """A loose action."""
        return ServerControlResult(success=True)

    row = Endpoint("POST", "/servers/{server_name}/loose", "loose", Risk.LIFECYCLE,
                   owner="mission")
    params = parameters_for(row, loose)
    assert [param.name for param in params] == ["server_name"]
    assert not any(param.kind is param.VAR_KEYWORD for param in params)


def test_parameters_for_resolves_string_annotations_to_real_types():
    """A plugin module under ``from __future__ import annotations`` yields strings; the wire cannot."""
    params = parameters_for(GOOD_READ, read_weather)
    assert [param.name for param in params] == ["server_name", "mission", "original"]
    assert params[1].annotation is str
    assert params[2].annotation is bool


def test_the_tool_spec_carries_the_name_the_description_and_the_parameters():
    spec = tool_spec_for(GOOD_RESTART, restart_mission)
    assert spec.name == "restart_mission"
    assert spec.description == "Restart the current mission on a server."
    assert spec.risk is Risk.LIFECYCLE
    assert [param.name for param in spec.parameters] == ["server_name", "modify_mission",
                                                         "use_orig"]
    assert all(isinstance(param.annotation, type) for param in spec.parameters)


def test_the_tool_spec_falls_back_to_the_action_docstring():
    assert tool_spec_for(GOOD_LIST, list_servers).description == (
        "List every server this process knows.")


def test_parameter_docs_are_read_from_the_args_block():
    assert parameter_docs(restart_mission) == {
        "server_name": "the server to act on.",
        "modify_mission": "rewrite the mission before restarting.",
        "use_orig": "restart from the original file.",
    }


# ── the route builder ─────────────────────────────────────────────────────

class FakeContext:
    """The one method the route handler asks its context for."""

    def __init__(self, found: bool = True) -> None:
        self.found = found
        self.looked_up: list[str] = []

    async def resolve_scoped_server(self, name: str) -> ServerResolution:
        self.looked_up.append(name)
        if self.found:
            return ServerResolution(ServerResolution.FOUND, name=name, server=object())
        return ServerResolution(ServerResolution.NOT_FOUND, name=name)


class FakeGuard:
    def __init__(self, context: FakeContext, result: ActionResult) -> None:
        self.context_ = context
        self.result = result
        self.calls: list[dict] = []

    def dependency(self, row: Endpoint):
        def _noop() -> None:
            return None

        return _noop

    def context(self, row: Endpoint, params):
        return self.context_

    async def dispatch(self, row: Endpoint, ctx, params):
        self.calls.append(dict(params))
        return self.result


def _app(rows, guard, prefix="/api/v1"):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(build_router(rows, guard=guard, prefix=prefix, registry=REGISTRY))
    return app


def _client(result: ActionResult, rows=(GOOD_WRITE,)):
    """A ``TestClient`` answering every request with *result*."""
    from fastapi.testclient import TestClient

    return TestClient(_app(list(rows), FakeGuard(FakeContext(), result)))


def _post(result: ActionResult):
    """The rendered HTTP response of the write row, answered with *result*."""
    return _client(result).post("/api/v1/servers/SRS-1/start")


def test_the_builder_produces_one_route_per_row_with_the_declared_method_and_path():
    from fastapi import APIRouter

    guard = FakeGuard(FakeContext(), ServerControlResult(success=True))
    router = APIRouter()
    for row in (GOOD_WRITE, GOOD_READ, GOOD_LIST):
        build_route(router, row, fn=REGISTRY[row.action], guard=guard, prefix="/api/v1")

    assert [(route.path, sorted(route.methods)) for route in router.routes] == [
        ("/api/v1/servers/{server_name}/start", ["POST"]),
        ("/api/v1/servers/{server_name}/weather", ["GET"]),
        ("/api/v1/servers", ["GET"]),
    ]


def test_a_row_whose_action_is_absent_is_not_registered():
    guard = FakeGuard(FakeContext(), ServerControlResult(success=True))
    unknown = Endpoint("GET", "/gone", "no_such_action", Risk.READ, owner="mission")
    assert list(build_router([unknown], guard=guard, registry=REGISTRY).routes) == []


def test_a_write_posts_a_flat_json_body_and_never_a_query_string():
    from fastapi.testclient import TestClient

    ctx = FakeContext()
    guard = FakeGuard(ctx, ServerControlResult(success=True, server_name="SRS-1"))
    client = TestClient(_app([GOOD_RESTART], guard))

    response = client.post("/api/v1/servers/SRS-1/mission/restart",
                           json={"modify_mission": False, "use_orig": False})
    assert response.status_code == 200
    assert guard.calls == [{"server_name": "SRS-1", "modify_mission": False, "use_orig": False}]
    assert ctx.looked_up == ["SRS-1"], "the target is resolved before dispatch"

    # the same parameter in the QUERY string is not the body: the default is what arrives
    client.post("/api/v1/servers/SRS-1/mission/restart?modify_mission=false")
    assert guard.calls[-1]["modify_mission"] is True

    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/servers/{server_name}/mission/restart"]["post"]
    assert [(param["name"], param["in"]) for param in operation["parameters"]] == [
        ("server_name", "path")]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    body_name = body["$ref"].rsplit("/", 1)[-1]
    assert sorted(spec["components"]["schemas"][body_name]["properties"]) == [
        "modify_mission", "use_orig"]


def test_a_read_takes_its_parameters_from_the_query_string():
    from fastapi.testclient import TestClient

    guard = FakeGuard(FakeContext(), MissionControlResult(success=True))
    client = TestClient(_app([GOOD_READ], guard))
    response = client.get("/api/v1/servers/SRS-1/weather?mission=3&original=true")
    assert response.status_code == 200
    assert guard.calls == [{"server_name": "SRS-1", "mission": "3", "original": True}]

    operation = client.get("/openapi.json").json()["paths"][
        "/api/v1/servers/{server_name}/weather"]["get"]
    assert [(param["name"], param["in"]) for param in operation["parameters"]] == [
        ("server_name", "path"), ("mission", "query"), ("original", "query")]


def test_an_unknown_target_is_a_404_before_the_action_runs():
    from fastapi.testclient import TestClient

    guard = FakeGuard(FakeContext(found=False), ServerControlResult(success=True))
    client = TestClient(_app([GOOD_WRITE], guard))
    response = client.post("/api/v1/servers/ghost/start")
    assert response.status_code == 404
    assert response.json() == {"status": "error", "message": "Server 'ghost' not found."}
    assert guard.calls == [], "no action runs against a name nothing answers to"


# ── the rendered body: one envelope, `code` only when the result carries one ──

def test_a_success_answers_200_without_a_code_key():
    response = _post(ServerControlResult(success=True))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": ""}
    assert "code" not in response.json(), "an ordinary answer must not gain the key"


def test_a_success_keeps_its_data_keys_and_gains_no_code_key():
    response = _post(ActionResult(success=True, message="Started.",
                                  data={"server_name": "SRS-1"}))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Started.", "server_name": "SRS-1"}


def test_action_unavailable_is_a_501_carrying_the_code():
    response = _post(ActionResult(success=False, message="No such action.",
                                  code=ACTION_UNAVAILABLE))
    assert response.status_code == 501
    assert response.json() == {"status": "error", "message": "No such action.",
                               "code": ACTION_UNAVAILABLE}
    assert "Retry-After" not in response.headers


def test_target_busy_is_a_409_carrying_the_code_and_the_retry_hint():
    response = _post(ActionResult(success=False, message="Wait and retry.", code=TARGET_BUSY))
    assert response.status_code == 409
    assert response.headers["Retry-After"] == "2"
    assert response.json() == {"status": "error", "message": "Wait and retry.",
                               "code": TARGET_BUSY}


def test_action_failed_is_a_500_carrying_the_code():
    response = _post(ActionResult(success=False, message="It raised.", code=ACTION_FAILED))
    assert response.status_code == 500
    assert response.json() == {"status": "error", "message": "It raised.",
                               "code": ACTION_FAILED}


def test_a_domain_refusal_is_a_400_without_a_code_key():
    response = _post(ServerControlResult(success=False, message="Not stopped."))
    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "Not stopped."}
    assert "code" not in response.json()


def test_every_status_class_speaks_the_same_envelope_shape():
    """``status`` and ``message`` always, ``code`` exactly when the result carries one."""
    for result, expected in ((ServerControlResult(success=True), 200),
                             (ServerControlResult(success=False, message="no"), 400),
                             (ActionResult(success=False, code=TARGET_BUSY), 409),
                             (ActionResult(success=False, code=ACTION_UNAVAILABLE), 501),
                             (ActionResult(success=False, code=ACTION_FAILED), 500)):
        response = _post(result)
        body = response.json()
        assert response.status_code == expected
        assert set(body) >= {"status", "message"}
        assert body["status"] == ("ok" if result.success else "error")
        assert ("code" in body) is bool(result.code)
        assert body.get("code", result.code) == result.code


def test_the_code_sits_between_the_message_and_the_data_keys():
    """§8 order, on the dict and through json: ``status``, ``message``, ``code``, then ``data``."""
    busy = ActionResult(success=False, message="Busy.", code=TARGET_BUSY,
                        data={"server_name": "SRS-1"})
    assert list(body_of(busy)) == ["status", "message", "code", "server_name"]
    assert list(_post(busy).json()) == ["status", "message", "code", "server_name"]


def test_status_for_reads_the_code_and_not_the_sentence():
    assert status_for(ServerControlResult(success=True)) == (200, {})
    assert status_for(ServerControlResult(success=False, message="no")) == (400, {})
    assert status_for(ActionResult(success=False, code=TARGET_BUSY)) == (409,
                                                                       {"Retry-After": "2"})
    assert status_for(ActionResult(success=False, code=ACTION_UNAVAILABLE)) == (501, {})
    assert status_for(ActionResult(success=False, code=ACTION_FAILED)) == (500, {})


def test_the_outcome_codes_are_the_ones_action_results_declares():
    """The wire strings are owned by ``core.action_results``; this module must not restate them."""
    from core import action_results
    from core import action_surface

    assert action_surface.ACTION_UNAVAILABLE == action_results.ACTION_UNAVAILABLE
    assert action_surface.TARGET_BUSY == action_results.TARGET_BUSY
    assert action_surface.ACTION_FAILED == action_results.ACTION_FAILED
    assert action_results.ACTION_UNAVAILABLE == "action_unavailable"
    assert action_results.TARGET_BUSY == "target_busy"
    assert action_results.ACTION_FAILED == "action_failed"


def test_the_route_name_and_the_tag_are_pinned():
    """The operation id and the generated body-model name derive from a STABLE name."""
    from fastapi import APIRouter

    router = APIRouter()
    build_route(router, GOOD_WRITE, fn=start_server,
                guard=FakeGuard(FakeContext(), ServerControlResult(success=True)))
    route = router.routes[0]
    assert route.name == "v1_start_server"
    assert route.endpoint.__name__ == "v1_start_server"
    assert route.tags == ["Mission"]


def test_the_envelope_model_documents_the_code_key():
    """The declared model is the documented shape of every status the route answers."""
    schema = envelope_model_for(MissionControlResult).model_json_schema()
    assert sorted(schema["properties"]) == ["code", "message", "status"]
    assert "code" not in schema["required"], "an ordinary answer omits it"
    assert schema["properties"]["code"]["description"]
    assert schema["additionalProperties"] is True, "the flattened data is an extra property"


def test_every_documented_status_names_the_code_key():
    """200, 400, 404, 409, 500 and 501 all reference the one envelope model that declares it."""
    client = _client(ActionResult(success=False, code=TARGET_BUSY))
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/servers/{server_name}/start"]["post"]
    reference = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    for status in ("200", "400", "404", "409", "500", "501"):
        assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"] \
            == reference, status
    schema = spec["components"]["schemas"][reference.rsplit("/", 1)[-1]]
    assert "code" in schema["properties"]
    assert "code" not in schema["required"]


def test_the_body_carries_the_code_without_the_response_model_and_the_model_documents_it(
        monkeypatch):
    """The handler answers a ``JSONResponse``, so the model never serialises the body.

    A model that omits ``code`` therefore cannot filter the key out — the key arrives from
    ``body_of`` either way; what the declaration fixes is the SCHEMA, where a client generated from
    the documented shape would otherwise never learn the key exists.
    """
    from pydantic import ConfigDict, create_model

    bare = create_model("BareEnvelope", __config__=ConfigDict(extra="allow"),
                        status=(str, ...), message=(str, ""))
    monkeypatch.setattr("core.action_surface.envelope_model_for", lambda cls: bare)
    client = _client(ActionResult(success=False, message="Gone.", code=ACTION_UNAVAILABLE))
    response = client.post("/api/v1/servers/SRS-1/start")
    assert response.status_code == 501
    assert response.json()["code"] == ACTION_UNAVAILABLE, "the body, not the model, carries it"

    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/servers/{server_name}/start"]["post"]
    name = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    schema = spec["components"]["schemas"][name.rsplit("/", 1)[-1]]
    assert sorted(schema["properties"]) == ["message", "status"], (
        "without the declaration the key is invisible to a client generated from the schema")


def test_the_documented_default_never_reaches_the_wire():
    """The model declares ``code`` with an empty default; the rendered 200 must still omit it."""
    response = _post(ServerControlResult(success=True))
    assert response.json() == {"status": "ok", "message": ""}
    assert envelope_model_for(ServerControlResult).model_fields["code"].default == ""


def test_a_result_that_declares_its_own_status_field_still_builds():
    """A result may carry a field called ``status``; the envelope's own key wins, it does not collide."""
    from core.action_surface import envelope_model_for

    @dataclass
    class StatusBearingResult(ActionResult):
        status: int = 200

    model = envelope_model_for(StatusBearingResult)
    assert model.model_fields["status"].annotation is str
    assert isinstance(StatusBearingResult(success=False).to_dict()["status"], str)


# ── core declares zero rows ───────────────────────────────────────────────

def test_core_registers_no_endpoint_row_at_runtime():
    assert endpoint_rows() == ()
    assert discover_endpoints([], registry=REGISTRY, kind_of=kind_of) == ()
    assert endpoint_rows() == ()


def test_no_module_under_core_declares_a_row():
    """The only ``Endpoint(`` construction under ``core/`` is the one inside the decorator."""
    offenders: list[str] = []
    factory_lines = 0
    for path in sorted((REPO_ROOT / "core").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if path.name == "action_surface.py":
                # ``action_surface`` owns the type and the factory: exactly one construction.
                if getattr(func, "id", None) == "Endpoint":
                    factory_lines += 1
                continue
            name = getattr(func, "id", None)
            if name == "Endpoint":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} Endpoint(...)")
            elif name == "endpoint":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} endpoint(...)")
            elif isinstance(func, ast.Attribute) and getattr(func.value, "id", None) == "api_v1":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} @api_v1.{func.attr}")
    assert not offenders, "core/ must declare no endpoint row:\n  " + "\n  ".join(offenders)
    assert factory_lines == 1, (
        "core/action_surface.py must construct exactly one Endpoint, inside the decorator factory "
        f"(found {factory_lines})")


def test_core_imports_without_the_web_stack():
    """The bot imports ``core`` and serves no HTTP, so FastAPI is imported by the builder only."""
    program = (
        "import sys\n"
        "class Blocker:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'fastapi' or name.startswith('fastapi.'):\n"
        "            raise ModuleNotFoundError('blocked: ' + name, name=name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "from core.action_surface import endpoint_rows, Risk, Endpoint, parameters_for\n"
        "assert endpoint_rows() == ()\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", program], cwd=REPO_ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout.strip().endswith("ok")
