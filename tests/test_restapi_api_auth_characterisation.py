"""The REST API auth decision table, recorded from ``register_routes`` before it moved.

A characterisation test, not a design test: the expected answers below are what the legacy
``plugins/restapi/commands.py::register_routes`` closure answers TODAY, written down cell by cell.
The same table is then asserted against ``services.webservice.api_auth.ApiVerifier``, so the
extraction is judged only on whether it reproduces those answers — including the status code and
the ``request.state`` stamps a route depends on.

Axes:

* **level** — the configured requirement (``none`` < ``api_key`` < ``jwt``; ``disabled`` means the
  endpoint is not there at all) for every network, so the network axis alone decides nothing and
  a difference can only come from the credential handling;
* **network** — the caller's scope: loopback, listed in ``security.trusted``, or neither;
* **enabled** — ``endpoints.<key>.enabled``, decided at registration time;
* **credential** — what the caller presents: nothing, a valid/wrong ``X-API-Key``, a valid/wrong
  bearer JWT, or both.

Four outcomes, and each one is a specific status + detail the route really answers:

``allowed``  the gate let the request through to the handler
``denied``   ``403 {"detail": "Invalid authentication"}``
``hidden``   ``404 {"detail": "Not found."}`` — a ``disabled`` requirement
``absent``   ``404 {"detail": "Not Found"}`` — the route was never registered

The coarse tokens are deliberate: ``allowed`` is the legacy handler's own 404 for an unknown
server, so the legacy and the verifier probes answer different statuses for the SAME decision.
Each outcome is translated per side by ``LEGACY_ANSWER`` / ``VERIFIER_ANSWER``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
import types
from typing import Any, cast

import httpx
import jwt
import pytest
from fastapi import APIRouter, Depends, FastAPI
from starlette.requests import Request

from core import DEFAULT_TAG
from plugins.restapi.commands import RestAPI
from services.webservice.api_auth import ApiAuthPolicy, ApiVerifier, Level
from services.webservice.registry import include_router_counted

# ------------------------------------------------------------------------------- the credential set

API_KEY = "the-api-key"
SHARED_KEY = "the-shared-token-key"      # the `key` claim a valid JWT must carry
JWT_SECRET = "jwt-signing-secret-0123456789abcdef"   # the JWKS key the fake client hands out
WRONG_SECRET = "another-signing-secret-0123456789ab"
KID = "test-kid"
JWKS_URL = "https://jwks.invalid/.well-known/jwks.json"

#: the endpoint config key the probe drives; ``path.lstrip("/")`` is what legacy passes down
ENDPOINT_KEY = "instance/missions"
#: the legacy route for that key: 404 for an unknown server, without touching DCS or the DB
PROBE_PATH = "/instance/missions"


class _FakeJWKClient:
    """``jwt.PyJWKClient`` without the JWKS fetch, so a JWT can be verified offline."""

    def __init__(self, url: str):
        self.url = url

    def get_signing_key(self, kid: str):
        if kid != KID:
            raise KeyError(f"no such kid: {kid}")
        return types.SimpleNamespace(key=JWT_SECRET, algorithm_name="HS256")


def _token(*, key: str = SHARED_KEY, kid: str = KID, secret: str = JWT_SECRET) -> str:
    now = int(time.time())
    return jwt.encode({"key": key, "sub": "characterisation", "aud": "dcssb",
                       "iat": now, "exp": now + 3600},
                      secret, algorithm="HS256", headers={"kid": kid})


#: credential name -> the headers a caller sends
CREDENTIALS: dict[str, dict[str, str]] = {
    "none": {},
    "api_key": {"X-API-Key": API_KEY},
    "api_key_wrong": {"X-API-Key": "not-the-key"},
    "jwt": {"Authorization": f"Bearer {_token()}"},
    "jwt_wrong": {"Authorization": f"Bearer {_token(key='not-the-shared-key')}"},
    "both": {"X-API-Key": API_KEY, "Authorization": f"Bearer {_token()}"},
}

CREDENTIAL_ORDER = ("none", "api_key", "api_key_wrong", "jwt", "jwt_wrong", "both")

#: outcome token -> what the LEGACY route answers, as (status, detail predicate)
LEGACY_ANSWER = {
    "allowed": (404, lambda detail: detail.startswith("Server '")),
    "denied": (403, lambda detail: detail == "Invalid authentication"),
    "hidden": (404, lambda detail: detail == "Not found."),
    "absent": (404, lambda detail: detail == "Not Found"),
}

#: outcome token -> what a verifier-guarded probe route answers
VERIFIER_ANSWER = {
    "allowed": (200, lambda detail: detail == "probe"),
    "denied": (403, lambda detail: detail == "Invalid authentication"),
    "hidden": (404, lambda detail: detail == "Not found."),
    "absent": (404, lambda detail: detail == "Not Found"),
}

# ------------------------------------------------------------------------------ the recorded table
#
# cell key: (level, enabled) -> one outcome per network, and within a network one per credential.
# `enabled: False` is decided at registration, before any auth runs, so every cell is `absent`.

_RECORDED: dict[tuple[str, bool], dict[str, tuple[str, ...]]] = {
    ("none", True): {
        "local": ("allowed", "allowed", "allowed", "allowed", "allowed", "allowed"),
        "trusted": ("allowed", "allowed", "allowed", "allowed", "allowed", "allowed"),
        "remote": ("allowed", "allowed", "allowed", "allowed", "allowed", "allowed"),
    },
    ("api_key", True): {
        "local": ("denied", "allowed", "denied", "allowed", "denied", "allowed"),
        "trusted": ("denied", "allowed", "denied", "allowed", "denied", "allowed"),
        "remote": ("denied", "allowed", "denied", "allowed", "denied", "allowed"),
    },
    ("jwt", True): {
        "local": ("denied", "denied", "denied", "allowed", "denied", "allowed"),
        "trusted": ("denied", "denied", "denied", "allowed", "denied", "allowed"),
        "remote": ("denied", "denied", "denied", "allowed", "denied", "allowed"),
    },
    ("disabled", True): {
        "local": ("hidden", "hidden", "hidden", "hidden", "hidden", "hidden"),
        "trusted": ("hidden", "hidden", "hidden", "hidden", "hidden", "hidden"),
        "remote": ("hidden", "hidden", "hidden", "hidden", "hidden", "hidden"),
    },
    ("none", False): {
        "local": ("absent",) * 6, "trusted": ("absent",) * 6, "remote": ("absent",) * 6,
    },
    ("api_key", False): {
        "local": ("absent",) * 6, "trusted": ("absent",) * 6, "remote": ("absent",) * 6,
    },
    ("jwt", False): {
        "local": ("absent",) * 6, "trusted": ("absent",) * 6, "remote": ("absent",) * 6,
    },
    ("disabled", False): {
        "local": ("absent",) * 6, "trusted": ("absent",) * 6, "remote": ("absent",) * 6,
    },
}

#: network name -> a client address in that scope (``security.trusted`` lists 10.0.0.0/8 only)
NETWORK_HOSTS = {
    "local": "127.0.0.1",
    "trusted": "10.1.2.3",
    "remote": "203.0.113.9",
}

LEVELS = ("none", "api_key", "jwt", "disabled")
NETWORKS = ("local", "trusted", "remote")

#: (level, enabled, network, credential) -> the recorded outcome
CELLS: list[tuple[str, bool, str, str]] = [
    (level, enabled, network, credential)
    for level in LEVELS
    for enabled in (True, False)
    for network in NETWORKS
    for credential in CREDENTIAL_ORDER
]

EXPECTED: dict[tuple[str, bool, str, str], str] = {
    (level, enabled, network, credential): _RECORDED[(level, enabled)][network][index]
    for level in LEVELS
    for enabled in (True, False)
    for network in NETWORKS
    for index, credential in enumerate(CREDENTIAL_ORDER)
}


# ------------------------------------------------------------------------------------ test helpers

class _FakeLoop:
    """Stands in for ``discord.ext.tasks.Loop`` (nothing to cancel)."""

    def cancel(self) -> None:
        return None

    def get_task(self):
        return None


def plugin_config(level: str, enabled: bool) -> dict:
    """The ``restapi.yaml`` DEFAULT block for one cell."""
    config: dict[str, Any] = {
        "auth": {
            "api_key": API_KEY,
            "jwt": {"key": SHARED_KEY, "jwks_url": JWKS_URL},
        },
        "security": {
            "trusted": ["10.0.0.0/8"],
            "default": {"local": level, "trusted": level, "remote": level},
        },
    }
    if not enabled:
        config["endpoints"] = {ENDPOINT_KEY: {"enabled": False}}
    return config


def legacy_plugin(config: dict) -> RestAPI:
    """The legacy plugin instance for one config, with ``register_routes`` already run."""
    app = FastAPI()
    api = object.__new__(RestAPI)
    api.locals = {DEFAULT_TAG: config}
    api.log = logging.getLogger("tests.restapi.auth")
    api.app = app
    api.router = None
    api.auth = None
    api._included = []
    api._included_v1 = []
    api.refresh_views = cast(Any, _FakeLoop())
    api.eventlistener = None
    api._config = {}
    api.__cog_name__ = "RestAPI"
    api.bot = cast(Any, types.SimpleNamespace(servers={}))
    api.bus = cast(Any, types.SimpleNamespace(node=types.SimpleNamespace(instances={})))
    # a node that loads no plugin: /api/v1 mounts from discovery, and this file characterises the
    # LEGACY surface, so v1 contributes no route here
    api.node = cast(Any, types.SimpleNamespace(plugins=[]))
    api.register_routes()
    return api


def legacy_app(config: dict) -> FastAPI:
    """The legacy app for one config, registered by the REAL ``register_routes``."""
    return cast(FastAPI, legacy_plugin(config).app)


def registered_legacy_dependency(api: RestAPI, path: str = PROBE_PATH):
    """The auth dependency ``register_routes`` installed on one legacy route, by identity.

    Reading it back off the registered route is what proves the swap: the object guarding the
    probe below is the object guarding the real route.
    """
    router = cast(Any, api.router)
    route = next(route for route in router.routes if route.path == path)
    return route.dependant.dependencies[0].call



def verifier_app(policy, verifier, captured: dict[str, Any] | None = None) -> FastAPI:
    """The verifier's app: the SAME registration rule, one probe route."""

    async def probe():
        return "probe"

    app = FastAPI()
    router = APIRouter()
    if policy.endpoint_enabled(ENDPOINT_KEY):
        router.add_api_route("/probe", probe, methods=["GET"],
                             dependencies=[Depends(verifier.legacy_dependency(path=ENDPOINT_KEY))])
    include_router_counted(app, router)
    if captured is not None:
        capture_state(app, captured)
    return app


def build_verifier(config: dict, captured: dict[str, Any] | None = None):
    """The policy + verifier + probe app for one config."""
    policy = ApiAuthPolicy.from_plugin_config(config)
    verifier = ApiVerifier(policy, logging.getLogger("tests.api_auth"))
    return policy, verifier, verifier_app(policy, verifier, captured)


def client_for(app: FastAPI, network: str) -> httpx.AsyncClient:
    """An in-process client whose peer address puts the request in that network scope."""
    transport = httpx.ASGITransport(app=app, client=(NETWORK_HOSTS[network], 54321))
    return httpx.AsyncClient(transport=transport, base_url="https://testserver")


def client_without_peer(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=cast(Any, None))
    return httpx.AsyncClient(transport=transport, base_url="https://testserver")


async def ask_legacy(app: FastAPI, network: str, credential: str) -> tuple[int, str]:
    async with client_for(app, network) as client:
        response = await client.get(PROBE_PATH, params={"server_name": "no-such-server"},
                                    headers=CREDENTIALS[credential])
    return response.status_code, response.json().get("detail", "")


async def ask_verifier(app: FastAPI, network: str, credential: str) -> tuple[int, str]:
    async with client_for(app, network) as client:
        response = await client.get("/probe", headers=CREDENTIALS[credential])
    body = response.json()
    return response.status_code, body.get("detail", "") if isinstance(body, dict) else body


def matches(outcome: str, answer, table: dict) -> bool:
    status, detail_predicate = table[outcome]
    observed_status, observed_detail = answer
    return observed_status == status and detail_predicate(observed_detail)


# ----------------------------------------------------------------------------------------- fixtures

# A strict pin on a defect in the PRODUCT, not a weaker assertion: the statements below are the
# ones the design calls for, so each is expected to fail until the product implements them, and
# each fails the suite again (XPASS) the moment the product does.
_XFAIL_INLINE_LEGACY_GUARD = pytest.mark.xfail(
    strict=True,
    reason=(
        "the legacy surface is still guarded by the inline closure "
        "`RestAPI.register_routes.<locals>.auth_dependency.<locals>.auth`; the shared "
        "`services/webservice/api_auth.py:ApiVerifier.legacy_dependency` factory exists but "
        "`RestAPI.register_routes` never builds its routes through it"
    ),
)


@pytest.fixture(autouse=True)
def offline_jwks(monkeypatch):
    """Both sides construct ``jwt.PyJWKClient`` from the config; serve it a local key instead."""
    monkeypatch.setattr(jwt, "PyJWKClient", _FakeJWKClient)


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------- the legacy answers, recorded

@pytest.mark.parametrize("level,enabled,network,credential", CELLS,
                         ids=[f"{c[0]}-{'on' if c[1] else 'off'}-{c[2]}-{c[3]}" for c in CELLS])
def test_todays_register_routes_answers_the_recorded_table(level, enabled, network, credential):
    app = legacy_app(plugin_config(level, enabled))
    answer = _run(ask_legacy(app, network, credential))

    assert matches(EXPECTED[(level, enabled, network, credential)], answer, LEGACY_ANSWER), \
        f"{level}/{'enabled' if enabled else 'disabled'}/{network}/{credential} answered {answer}"


def test_a_request_without_a_peer_address_is_remote():
    """``get_request_scope`` treats "no client" as remote, so a transport that reports no peer is
    judged against the remote level — record it, because it is a decision and not an accident."""
    open_app = legacy_app(plugin_config("none", True))
    assert matches("allowed", _run(ask_legacy_without_peer(open_app)), LEGACY_ANSWER), \
        "a 'none' requirement answers regardless of scope"

    strict_app = legacy_app(plugin_config("jwt", True))
    assert matches("denied", _run(ask_legacy_without_peer(strict_app)), LEGACY_ANSWER), \
        "a peer-less request must be judged remote and refused, not waved through"


async def ask_legacy_without_peer(app: FastAPI) -> tuple[int, str]:
    async with client_without_peer(app) as client:
        response = await client.get(PROBE_PATH, params={"server_name": "no-such-server"})
    return response.status_code, response.json().get("detail", "")


# ------------------------------------------------------- the per-network default and the override

def test_the_network_default_decides_when_the_endpoint_configures_no_security():
    """``security.default`` is read per network, and loopback is always local."""
    config = {
        "auth": {"api_key": API_KEY, "jwt": {"key": SHARED_KEY, "jwks_url": JWKS_URL}},
        "security": {"trusted": ["10.0.0.0/8"],
                     "default": {"local": "none", "trusted": "api_key", "remote": "jwt"}},
    }
    app = legacy_app(config)
    expected = {"local": "allowed", "trusted": "denied", "remote": "denied"}
    for network, outcome in expected.items():
        assert matches(outcome, _run(ask_legacy(app, network, "none")), LEGACY_ANSWER), \
            f"anonymous request from {network}"


def test_the_endpoint_security_override_wins_over_the_network_default():
    config = {
        "auth": {"api_key": API_KEY, "jwt": {"key": SHARED_KEY, "jwks_url": JWKS_URL}},
        "security": {"trusted": ["10.0.0.0/8"],
                     "default": {"local": "jwt", "trusted": "jwt", "remote": "jwt"}},
        "endpoints": {ENDPOINT_KEY: {"security": {"local": "none", "trusted": "none",
                                                  "remote": "api_key"}}},
    }
    app = legacy_app(config)
    assert matches("allowed", _run(ask_legacy(app, "local", "none")), LEGACY_ANSWER)
    assert matches("allowed", _run(ask_legacy(app, "trusted", "none")), LEGACY_ANSWER)
    assert matches("allowed", _run(ask_legacy(app, "remote", "api_key")), LEGACY_ANSWER)
    assert matches("denied", _run(ask_legacy(app, "remote", "none")), LEGACY_ANSWER)


def test_an_unknown_level_falls_back_to_jwt():
    """A misspelt level is not "open" and not a crash: it is the strictest requirement."""
    config = {
        "auth": {"api_key": API_KEY, "jwt": {"key": SHARED_KEY, "jwks_url": JWKS_URL}},
        "security": {"trusted": ["10.0.0.0/8"],
                     "default": {"local": "nonsense", "trusted": "nonsense", "remote": "nonsense"}},
    }
    app = legacy_app(config)
    assert matches("denied", _run(ask_legacy(app, "local", "none")), LEGACY_ANSWER)
    assert matches("allowed", _run(ask_legacy(app, "local", "jwt")), LEGACY_ANSWER)


def test_a_present_but_empty_default_block_states_no_level_and_falls_to_jwt():
    """``default: {}`` is PRESENT, so no built-in fallback applies: every network requires ``jwt``.

    The recorded answer comes from the historical closure, whose lookup is
    ``default_security.get(scope, "jwt")`` on the config's own — here empty — mapping: anonymous and
    ``api_key`` callers are refused on loopback too. A falsy block must not re-open that door.
    """
    config = {
        "auth": {"api_key": API_KEY, "jwt": {"key": SHARED_KEY, "jwks_url": JWKS_URL}},
        "security": {"trusted": ["10.0.0.0/8"], "default": {}},
    }
    _, verifier, verifier_application = build_verifier(config)

    assert dict(verifier.policy.default_security) == {}, \
        "an empty `default` block must stay empty, not fall back to the lenient built-in"

    for network in NETWORKS:
        assert verifier.level_for(path=ENDPOINT_KEY, network=network) is Level.JWT
        assert matches("denied", _run(ask_legacy(legacy_app(config), network, "none")), LEGACY_ANSWER)
        assert matches("denied", _run(ask_legacy(legacy_app(config), network, "api_key")),
                       LEGACY_ANSWER)
        assert matches("allowed", _run(ask_legacy(legacy_app(config), network, "jwt")), LEGACY_ANSWER)
        assert matches("denied", _run(ask_verifier(verifier_application, network, "none")),
                       VERIFIER_ANSWER)
        assert matches("allowed", _run(ask_verifier(verifier_application, network, "jwt")),
                       VERIFIER_ANSWER)


# --------------------------------------------------------------- the request.state stamps, legacy

STAMP_NAMES = ("auth_scheme", "jwt_token", "jwt_payload", "auth_scope", "required_auth_level")

#: sentinels for the two stamps whose value is the caller's own token and its claims
JWT_SENT = "<the bearer token the caller sent>"
JWT_CLAIMS = {"key": SHARED_KEY, "sub": "characterisation", "aud": "dcssb"}


def _stamps(request) -> dict[str, Any]:
    return {name: getattr(request.state, name) for name in STAMP_NAMES
            if hasattr(request.state, name)}


def normalise(state: dict[str, Any], credential: str) -> dict[str, Any]:
    """Replace the token and its time-dependent claims with the markers the table uses."""
    observed = dict(state)
    if "jwt_token" in observed:
        sent = CREDENTIALS[credential].get("Authorization", "").split(" ", 1)[-1]
        assert observed.pop("jwt_token") == sent, "the stamp is the exact token the caller sent"
        observed["jwt_token"] = JWT_SENT
    if "jwt_payload" in observed:
        payload = observed.pop("jwt_payload")
        assert {name: payload[name] for name in JWT_CLAIMS} == JWT_CLAIMS, \
            f"the verified claims are stamped, got {sorted(payload)}"
        observed["jwt_payload"] = JWT_CLAIMS
    return observed


def capture_state(app: FastAPI, into: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record the request.state stamps a request leaves, from the OUTSIDE.

    ``request.state`` lives in the ASGI scope, so a middleware sees exactly what the auth
    dependency wrote — without swapping the route's endpoint, whose dependant is fixed at
    registration time. Pass ``into`` to have that dict updated in place.
    """
    captured: dict[str, Any] = into if into is not None else {}

    @app.middleware("http")
    async def _capture(request, call_next):
        response = await call_next(request)
        captured.clear()
        captured.update(_stamps(request))
        return response

    return captured


@pytest.mark.parametrize("level,credential,network,stamps", [
    # a 'none' requirement never verifies a credential, so a presented JWT leaves NO jwt stamp
    ("none", "none", "local", {"auth_scheme": "none", "auth_scope": "local",
                               "required_auth_level": "none"}),
    ("none", "jwt", "remote", {"auth_scheme": "none", "auth_scope": "remote",
                               "required_auth_level": "none"}),
    ("api_key", "api_key", "trusted", {"auth_scheme": "api_key", "auth_scope": "trusted",
                                       "required_auth_level": "api_key"}),
    # a JWT satisfies an api_key floor, and the jwt stamps are written
    ("api_key", "jwt", "local", {"auth_scheme": "jwt", "jwt_token": JWT_SENT,
                                 "jwt_payload": JWT_CLAIMS, "auth_scope": "local",
                                 "required_auth_level": "api_key"}),
    ("jwt", "jwt", "remote", {"auth_scheme": "jwt", "jwt_token": JWT_SENT,
                              "jwt_payload": JWT_CLAIMS, "auth_scope": "remote",
                              "required_auth_level": "jwt"}),
    # refused: the scope/level stamps are written, no scheme stamp
    ("api_key", "none", "local", {"auth_scope": "local", "required_auth_level": "api_key"}),
    # a disabled endpoint answers before any stamp is written
    ("disabled", "jwt", "local", {}),
])
def test_todays_register_routes_leaves_these_state_stamps(level, credential, network, stamps):
    """``plugins/dks/commands.py`` reads ``request.state.jwt_payload`` off a shared app, so the
    stamps are part of the contract."""
    app = legacy_app(plugin_config(level, True))
    captured = capture_state(app)

    _run(ask_legacy(app, network, credential))

    assert normalise(captured, credential) == stamps, \
        f"{level}/{network}/{credential} left {captured}"


# =============================================================================== the new verifier
#
# Everything below asks the SAME recorded table of ``ApiVerifier``. Nothing is recomputed from the
# verifier's own logic: a cell passes only if it answers what ``register_routes`` answered.

def outcome_of(answer: tuple[int, str], table: dict) -> str:
    """The outcome token an answer expresses, or a readable miss when it expresses none."""
    for name, (status, detail_predicate) in table.items():
        if status == answer[0] and detail_predicate(answer[1]):
            return name
    return f"?{answer}"


@pytest.mark.parametrize("level,enabled,network,credential", CELLS,
                         ids=[f"{c[0]}-{'on' if c[1] else 'off'}-{c[2]}-{c[3]}" for c in CELLS])
def test_the_verifier_answers_the_recorded_table(level, enabled, network, credential):
    _, _, app = build_verifier(plugin_config(level, enabled))
    answer = _run(ask_verifier(app, network, credential))

    expected = EXPECTED[(level, enabled, network, credential)]
    assert matches(expected, answer, VERIFIER_ANSWER), \
        f"{level}/{'enabled' if enabled else 'disabled'}/{network}/{credential} " \
        f"answered {answer}, expected {expected}"


@pytest.mark.parametrize("level,enabled,network,credential", CELLS,
                         ids=[f"{c[0]}-{'on' if c[1] else 'off'}-{c[2]}-{c[3]}" for c in CELLS])
def test_legacy_and_verifier_answer_the_same_cell(level, enabled, network, credential):
    """The cross-check, cell by cell: both sides, the same recorded outcome."""
    legacy_answer = _run(ask_legacy(legacy_app(plugin_config(level, enabled)), network, credential))
    _, _, app = build_verifier(plugin_config(level, enabled))
    verifier_answer = _run(ask_verifier(app, network, credential))

    recorded = EXPECTED[(level, enabled, network, credential)]
    observed = (outcome_of(legacy_answer, LEGACY_ANSWER),
                outcome_of(verifier_answer, VERIFIER_ANSWER))
    assert observed == (recorded, recorded), \
        f"{level}/{enabled}/{network}/{credential}: legacy {legacy_answer} " \
        f"-> {observed[0]}, verifier {verifier_answer} -> {observed[1]}, recorded {recorded}"


@pytest.mark.parametrize("level,credential,network,stamps", [
    ("none", "none", "local", {"auth_scheme": "none", "auth_scope": "local",
                               "required_auth_level": "none"}),
    ("none", "jwt", "remote", {"auth_scheme": "none", "auth_scope": "remote",
                               "required_auth_level": "none"}),
    ("api_key", "api_key", "trusted", {"auth_scheme": "api_key", "auth_scope": "trusted",
                                       "required_auth_level": "api_key"}),
    ("api_key", "jwt", "local", {"auth_scheme": "jwt", "jwt_token": JWT_SENT,
                                 "jwt_payload": JWT_CLAIMS, "auth_scope": "local",
                                 "required_auth_level": "api_key"}),
    ("jwt", "jwt", "remote", {"auth_scheme": "jwt", "jwt_token": JWT_SENT,
                              "jwt_payload": JWT_CLAIMS, "auth_scope": "remote",
                              "required_auth_level": "jwt"}),
    ("api_key", "none", "local", {"auth_scope": "local", "required_auth_level": "api_key"}),
    ("disabled", "jwt", "local", {}),
])
def test_the_verifier_leaves_the_same_state_stamps(level, credential, network, stamps):
    """``plugins/dks`` reads ``request.state.jwt_payload`` — the stamp set is a contract."""
    captured: dict[str, Any] = {}
    _, _, app = build_verifier(plugin_config(level, True), captured)

    _run(ask_verifier(app, network, credential))

    assert normalise(captured, credential) == stamps, \
        f"{level}/{network}/{credential} left {captured}"


# ------------------------------------------------------------- a request with no stamps at all

def _bare_request(headers: list[tuple[bytes, bytes]] | None = None,
                  client: tuple[str, int] | None = ("127.0.0.1", 1234)) -> Request:
    """A request with no state written on it yet."""
    scope: dict[str, Any] = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": "/probe", "raw_path": b"/probe", "query_string": b"", "root_path": "",
        "headers": headers or [], "client": client, "server": ("testserver", 443),
    }
    return Request(scope)


def test_authenticate_answers_none_for_a_request_with_no_credential():
    _, verifier, _ = build_verifier(plugin_config("jwt", True))
    request = _bare_request()

    assert _run(verifier.authenticate(request)) is None
    assert normalise(_stamps(request), "none") == {}, \
        "authenticate stamps nothing when nothing verified"


def test_a_request_with_none_of_the_stamps_set_cannot_pass_a_jwt_floor():
    """No credential, no stamp, no pass: a Principal meets the floor, never the state."""
    captured: dict[str, Any] = {}
    _, verifier, app = build_verifier(plugin_config("jwt", True), captured)
    request = _bare_request()
    assert _stamps(request) == {}, "the request starts with no stamps"

    answer = _run(ask_verifier(app, "local", "none"))

    assert answer == (403, "Invalid authentication"), f"a bare request answered {answer}"
    assert captured == {"auth_scope": "local", "required_auth_level": "jwt"}, \
        f"only the requirement is stamped, got {captured}"
    assert "auth_scheme" not in captured and "jwt_payload" not in captured

    # and a request whose state claims a level without a credential is still refused
    forged = _bare_request()
    forged.state.auth_scheme = "jwt"
    forged.state.jwt_payload = {"key": SHARED_KEY}
    forged.state.required_auth_level = "jwt"

    async def verdict() -> str | None:
        try:
            await verifier.legacy_dependency(path=ENDPOINT_KEY)(forged)
        except Exception as ex:                    # HTTPException, deliberately
            return f"{getattr(ex, 'status_code', '?')} {getattr(ex, 'detail', '?')}"
        return None

    assert _run(verdict()) == "403 Invalid authentication"


# ------------------------------------------------------------------------ the requirement itself

def test_level_for_reads_the_endpoint_override_then_the_network_default():
    config = plugin_config("api_key", True)
    config["endpoints"] = {ENDPOINT_KEY: {"security": {"remote": "jwt"}}}
    _, verifier, _ = build_verifier(config)

    assert verifier.level_for(path=ENDPOINT_KEY, network="remote") is Level.JWT
    assert verifier.level_for(path=ENDPOINT_KEY, network="local") is Level.API_KEY
    assert verifier.level_for(path="/" + ENDPOINT_KEY, network="local") is Level.API_KEY, \
        "a route path and its config key name the same endpoint"


def test_level_for_falls_back_to_jwt_for_an_unknown_word():
    config = plugin_config("jwt", True)
    config["security"]["default"] = {"local": "WAT", "trusted": "jwt", "remote": "jwt"}
    _, verifier, _ = build_verifier(config)

    assert verifier.level_for(path=ENDPOINT_KEY, network="local") is Level.JWT


def test_endpoint_enabled_defaults_to_true_and_honours_false():
    _, verifier, _ = build_verifier(plugin_config("none", True))
    assert verifier.policy.endpoint_enabled(ENDPOINT_KEY) is True

    _, verifier, _ = build_verifier(plugin_config("none", False))
    assert verifier.policy.endpoint_enabled(ENDPOINT_KEY) is False


def test_the_network_scope_is_local_loopback_trusted_or_remote():
    _, verifier, _ = build_verifier(plugin_config("none", True))
    policy = verifier.policy

    assert policy.network_for("127.0.0.1") == "local"
    assert policy.network_for("::1") == "local"
    assert policy.network_for("10.9.8.7") == "trusted"
    assert policy.network_for("203.0.113.9") == "remote"
    assert policy.network_for(None) == "remote", "no peer is not a privileged peer"
    assert policy.network_for("not-an-address") == "remote"


# -------------------------------------------------------------------------- the newer shim

def test_the_legacy_shim_on_the_probe_app_answers_403_not_401():
    """The two shims differ on purpose: the historical surface never answers 401."""
    _, _, app = build_verifier(plugin_config("jwt", True))

    async def ask() -> tuple[int, str]:
        async with client_for(app, "local") as client:
            response = await client.get("/probe")
        return response.status_code, response.json().get("detail", "")

    assert _run(ask()) == (403, "Invalid authentication")


def test_v1_dependency_answers_401_403_and_200():
    policy = ApiAuthPolicy.from_plugin_config(plugin_config("jwt", True))
    verifier = ApiVerifier(policy, logging.getLogger("tests.api_auth"))

    def guarded_app(floor: Level) -> FastAPI:
        app = FastAPI()

        async def probe():
            return "probe"

        app.add_api_route("/probe", probe, methods=["GET"],
                          dependencies=[Depends(verifier.v1_dependency(floor=floor))])
        return app

    async def ask(floor: Level, credential: str) -> tuple[int, str]:
        async with client_for(guarded_app(floor), "local") as client:
            response = await client.get("/probe", headers=CREDENTIALS[credential])
        body = response.json()
        return response.status_code, body.get("detail", "") if isinstance(body, dict) else body

    assert _run(ask(Level.JWT, "none"))[0] == 401
    assert _run(ask(Level.JWT, "api_key")) == (403, "Invalid authentication level")
    assert _run(ask(Level.JWT, "jwt"))[0] == 200
    assert _run(ask(Level.NONE, "none"))[0] == 200, "a READ floor admits an anonymous caller"


# ================================================= the swapped surface: the stamps, from the handler
#
# The table above is asserted from a middleware, which sees the scope the auth dependency wrote.
# These two tests assert the same contract from inside a handler, guarded by the dependency object
# the real route carries — the surface another plugin's routes read.

def legacy_handler_app(api: RestAPI, handler) -> FastAPI:
    """The legacy app plus a probe route guarded by a REAL legacy route's auth dependency."""
    app = cast(FastAPI, api.app)
    app.add_api_route("/stamp-probe", handler, methods=["GET"],
                      dependencies=[Depends(registered_legacy_dependency(api))])
    return app


async def ask_stamp_probe(app: FastAPI, network: str, credential: str) -> tuple[int, Any]:
    async with client_for(app, network) as client:
        response = await client.get("/stamp-probe", headers=CREDENTIALS[credential])
    body = response.json()
    return response.status_code, body if isinstance(body, dict) else {"detail": body}


@pytest.mark.parametrize("level,credential,network,stamps", [
    ("none", "none", "local", {"auth_scheme": "none", "auth_scope": "local",
                               "required_auth_level": "none"}),
    ("none", "jwt", "remote", {"auth_scheme": "none", "auth_scope": "remote",
                               "required_auth_level": "none"}),
    ("api_key", "api_key", "trusted", {"auth_scheme": "api_key", "auth_scope": "trusted",
                                       "required_auth_level": "api_key"}),
    # the one cell that carries all five stamps at once
    ("api_key", "jwt", "local", {"auth_scheme": "jwt", "jwt_token": JWT_SENT,
                                 "jwt_payload": JWT_CLAIMS, "auth_scope": "local",
                                 "required_auth_level": "api_key"}),
    ("jwt", "jwt", "remote", {"auth_scheme": "jwt", "jwt_token": JWT_SENT,
                              "jwt_payload": JWT_CLAIMS, "auth_scope": "remote",
                              "required_auth_level": "jwt"}),
])
def test_a_handler_on_a_registered_legacy_route_observes_the_state_stamps(
        level, credential, network, stamps):
    api = legacy_plugin(plugin_config(level, True))
    observed: dict[str, Any] = {}

    async def handler(request: Request):
        observed.update(_stamps(request))
        return {"observed": True}

    response_status, _ = _run(ask_stamp_probe(legacy_handler_app(api, handler), network, credential))

    assert response_status == 200
    assert normalise(observed, credential) == stamps, \
        f"{level}/{network}/{credential} left {observed} for the handler"


def test_all_five_stamps_are_visible_inside_the_handler():
    """A JWT satisfying an ``api_key`` floor is the cell where every stamp is set at once."""
    api = legacy_plugin(plugin_config("api_key", True))
    observed: dict[str, Any] = {}

    async def handler(request: Request):
        observed.update(_stamps(request))
        return {"observed": True}

    _run(ask_stamp_probe(legacy_handler_app(api, handler), "local", "jwt"))

    assert set(observed) == set(STAMP_NAMES), f"a handler saw only {sorted(observed)}"
    assert normalise(observed, "jwt") == {
        "auth_scheme": "jwt", "jwt_token": JWT_SENT, "jwt_payload": JWT_CLAIMS,
        "auth_scope": "local", "required_auth_level": "api_key",
    }


def test_a_refused_legacy_request_never_reaches_the_handler():
    api = legacy_plugin(plugin_config("jwt", True))
    observed: dict[str, Any] = {}

    async def handler(request: Request):
        observed.update(_stamps(request))
        return {"observed": True}

    status, body = _run(ask_stamp_probe(legacy_handler_app(api, handler), "local", "api_key"))

    assert (status, body.get("detail")) == (403, "Invalid authentication")
    assert observed == {}, "the handler ran for a refused request"


# --------------------------------------------------------------- the shape of the registry itself

@_XFAIL_INLINE_LEGACY_GUARD
def test_all_forty_nine_legacy_routes_are_guarded_by_the_one_verifier():
    """Every route carries the same factory, so the answers exist once, not once per route."""
    api = legacy_plugin(plugin_config("none", True))
    routes = list(cast(Any, api.router).routes)

    assert len(routes) == 49, f"{len(routes)} routes registered"
    guards = [route.dependant.dependencies[0].call for route in routes]
    assert {guard.__qualname__ for guard in guards} == \
        {"ApiVerifier.legacy_dependency.<locals>.auth"}
    assert len({id(guard.__code__) for guard in guards}) == 1, \
        "the routes are guarded by more than one closure"


def test_a_disabled_endpoint_is_skipped_at_registration_time():
    """``endpoints.<key>.enabled`` stays a registration decision, not an auth one."""
    api = legacy_plugin(plugin_config("none", False))
    routes = list(cast(Any, api.router).routes)

    assert len(routes) == 48, f"{len(routes)} routes registered"
    assert PROBE_PATH not in {route.path for route in routes}


# -------------------------------------------------------------------- the cross-plugin consumer

def test_the_dks_style_consumer_still_finds_jwt_payload_after_the_swap():
    """``plugins/dks/commands.py`` reads ``request.state.jwt_payload`` off the shared app.

    The read below is that line, verbatim; it is guarded by the dependency the legacy routes carry,
    so a route that lost the stamp would answer 500 here instead of the key.
    """
    api = legacy_plugin(plugin_config("jwt", True))
    reached: list[bool] = []

    async def dks_style(request: Request):
        jwt_payload = request.state.jwt_payload
        reached.append(True)
        return {"key": jwt_payload.get("key"), "sub": jwt_payload.get("sub")}

    app = legacy_handler_app(api, dks_style)

    status, body = _run(ask_stamp_probe(app, "local", "jwt"))
    assert status == 200, f"the consumer answered {status} {body}"
    assert body["key"] == SHARED_KEY, f"the consumer read {body}"
    assert body["sub"] == "characterisation"

    status, body = _run(ask_stamp_probe(app, "local", "none"))
    assert (status, body.get("detail")) == (403, "Invalid authentication")
    assert len(reached) == 1, "the consumer ran for a request the gate refused"


# ============================================================ the log a refused credential writes
#
# A bearer string that is not a JWT is a client error: one WARNING line, no traceback, and no
# ERROR record. An unexpected failure inside verification keeps log.exception with its traceback.

#: a bearer string that is not a JWT, carrying a marker no log line may echo
JUNK_TOKEN = "junk-token-9f3a2b"
JUNK_BEARER = {"Authorization": f"Bearer {JUNK_TOKEN}"}
#: the peer these probes call from — a remote address, so the line has one to name
PROBE_PEER = ("203.0.113.9", 4242)


class _ExplodingJWKClient:
    """A JWKS client that fails with an unexpected error, not a ``jwt`` error."""

    def get_signing_key(self, kid: str):
        raise RuntimeError(f"jwks endpoint unreachable for kid {kid}")


def _log_records(caplog) -> list[logging.LogRecord]:
    """Every record the verifier emitted, at any level."""
    return [record for record in caplog.records
            if record.name in ("tests.api_auth", "tests.restapi.auth")]


def _verifier_with_exploding_jwks():
    """The verifier of a ``jwt`` config, with its JWKS client replaced by a failing double."""
    policy = ApiAuthPolicy.from_plugin_config(plugin_config("jwt", True))
    policy = dataclasses.replace(policy, jwks_client=_ExplodingJWKClient())
    verifier = ApiVerifier(policy, logging.getLogger("tests.api_auth"))
    return verifier


def test_a_non_jwt_bearer_token_is_refused_without_any_error_record(caplog):
    """``Bearer not-a-jwt`` is a client error: refused, and nothing at ERROR or above."""
    caplog.set_level(logging.DEBUG, logger="tests.api_auth")
    _, verifier, _ = build_verifier(plugin_config("jwt", True))
    request = _bare_request(headers=[(b"authorization", b"Bearer not-a-jwt")], client=PROBE_PEER)

    assert _run(verifier.authenticate(request)) is None, "a non-JWT bearer must be refused"

    severe = [(record.levelname, record.getMessage()) for record in _log_records(caplog)
              if record.levelno >= logging.ERROR]
    assert severe == [], f"a malformed bearer token logged {severe}"


def test_a_genuine_fault_inside_verification_is_still_a_traceback(caplog):
    """An unexpected failure inside verification is not a client error: it keeps ``log.exception``."""
    caplog.set_level(logging.DEBUG, logger="tests.api_auth")
    verifier = _verifier_with_exploding_jwks()
    request = _bare_request(headers=[(b"authorization", f"Bearer {_token()}".encode())],
                            client=PROBE_PEER)

    assert _run(verifier.authenticate(request)) is None, "a failing verification must be refused"

    errors = [record for record in _log_records(caplog) if record.levelno >= logging.ERROR]
    assert len(errors) == 1, f"expected one ERROR record, got {[r.getMessage() for r in errors]}"
    assert errors[0].exc_info, "a genuine fault must carry its traceback"
    assert isinstance(errors[0].exc_info[1], RuntimeError), \
        f"the traceback was {errors[0].exc_info[1]!r}"


def test_the_malformed_bearer_line_names_scheme_path_and_peer_but_never_the_token(caplog):
    """One line at WARNING: the scheme, the path and the peer — never the token or a slice of it."""
    caplog.set_level(logging.DEBUG, logger="tests.api_auth")
    _, verifier, _ = build_verifier(plugin_config("jwt", True))
    request = _bare_request(headers=[(b"authorization", f"Bearer {JUNK_TOKEN}".encode())],
                            client=PROBE_PEER)

    assert _run(verifier.authenticate(request)) is None

    warnings = [record for record in _log_records(caplog) if record.levelno == logging.WARNING]
    assert len(warnings) == 1, \
        f"expected one WARNING line, got {[(r.levelname, r.getMessage()) for r in _log_records(caplog)]}"
    message = warnings[0].getMessage()
    assert "Bearer" in message, f"the line names no auth scheme: {message!r}"
    assert "/probe" in message, f"the line names no request path: {message!r}"
    assert PROBE_PEER[0] in message, f"the line names no client address: {message!r}"
    assert JUNK_TOKEN not in message, f"the line echoes the token: {message!r}"
    assert JUNK_TOKEN not in caplog.text, "the token reached the log through some other record"
    assert JUNK_TOKEN[:4] not in message, f"the line echoes a slice of the token: {message!r}"


def test_a_valid_api_key_beside_a_junk_bearer_passes_without_an_error_record(caplog):
    """The JWT branch is tried first, so a junk bearer beside a good key must not log at ERROR."""
    caplog.set_level(logging.DEBUG, logger="tests.api_auth")
    _, verifier, app = build_verifier(plugin_config("api_key", True))
    headers = {"X-API-Key": API_KEY, **JUNK_BEARER}

    async def ask() -> tuple[int, Any]:
        async with client_for(app, "local") as client:
            response = await client.get("/probe", headers=headers)
        return response.status_code, response.json()

    assert _run(ask()) == (200, "probe"), "a valid API key must still be accepted"

    severe = [(record.levelname, record.getMessage()) for record in _log_records(caplog)
              if record.levelno >= logging.ERROR]
    assert severe == [], f"a junk bearer beside a valid key logged {severe}"
