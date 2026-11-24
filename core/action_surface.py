"""The endpoint surface: one declarative row per exposed action.

A row is a frozen :class:`Endpoint` — ``method``, ``path``, ``action`` qualname, ``risk`` class —
declared in a plugin's ``api.py``. The row declares no parameters: the parameter list is DERIVED
from the action's own signature (:func:`parameters_for`), so the HTTP route and the MCP tool can
never describe one action differently.

What lives here, in order:

* :class:`Risk` — the six classes, each answering "what does a mistake cost";
* :class:`Endpoint` — the row;
* the registry and its two spellings (:func:`register_endpoint` for a tuple of rows,
  :func:`endpoint` / ``@api_v1.get`` for the decorator form);
* :func:`check_endpoints` / :func:`validate_endpoints` — the static proof over the row list;
* :func:`parameters_for` / :func:`tool_spec_for` — the one derivation both transports consume;
* :func:`build_router` — the rows as a FastAPI ``APIRouter``.

This module holds ZERO rows. :func:`endpoint_rows` answers ``()`` until a plugin's ``api.py`` is
imported by :func:`discover_endpoints`.

FastAPI is imported inside :func:`build_router` (and nowhere at module level): the bot process
imports ``core`` and serves no HTTP, so this module must import without the web stack installed.
"""
from __future__ import annotations

import dataclasses
import importlib
import inspect
import logging
import typing
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence, TypeVar

from core.action_results import ActionResult

log = logging.getLogger(__name__)

#: The action kinds a row can name. An action is READ (changes nothing) or WRITE.
READ = "read"
WRITE = "write"

#: The only verbs a row may carry — upper case, exactly as FastAPI spells them.
METHODS = ("GET", "POST", "PUT", "DELETE")

#: The parameter name every action takes as its context. It is never part of a wire shape.
CTX = "ctx"

#: What follows a path segment in a row's ``path``, e.g. ``/servers/{server_name}``.
OPEN_BRACE, CLOSE_BRACE = "{", "}"


class Risk(str, Enum):
    """What a mistake with this row costs — not which subsystem the row touches.

    READ       changes nothing; a wrong call wastes a round trip.
    LIFECYCLE  a server stops serving players, reversibly, inside one process.
    MODERATION acts on a person (their session or their record).
    CONFIG     writes a file that outlives the process.
    NODE       takes a whole machine, and every server on it, out of service.
    SECRET     carries or writes a credential; a leak cannot be rolled back.
    """

    READ = "read"
    LIFECYCLE = "lifecycle"
    MODERATION = "moderation"
    CONFIG = "config"
    NODE = "node"
    SECRET = "secret"


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One action bound to one HTTP shape.

    ``action`` is the registry QUALNAME as a STRING and never a function object: the row must be
    importable in a process where the action is not registered, so the validator can report it
    instead of the module failing to import.

    ``owner`` is stamped by :func:`discover_endpoints` from the package the row was imported out of
    — it is never authored, which is what makes "a row cannot claim another plugin's action"
    checkable rather than conventional.

    ``response`` is the row's own envelope class; ``None`` means the action's return annotation.
    """

    method: str
    path: str
    action: str
    risk: Risk
    summary: str = ""
    tags: tuple[str, ...] = ()
    mcp: bool = True
    response: Any = None
    owner: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise ValueError(
                f"endpoint {self.path!r}: method must be one of {METHODS}, not {self.method!r}")

    def __str__(self) -> str:
        return f"{self.method} {self.path} -> {self.action} [{self.risk}]"


@dataclass(frozen=True, slots=True)
class Problem:
    """One refusal of one row: the invariant code, the row's position, and the sentence.

    ``V1``-``V11`` are the statically provable invariants; ``index`` is the row's position in the
    list that was checked, which is what :func:`validate_endpoints` drops on.
    """

    code: str
    index: int
    row: Endpoint
    message: str

    def __str__(self) -> str:
        owner = f"{self.row.owner}: " if self.row.owner else ""
        return (f"{self.code} {owner}{self.row.method} {self.row.path} "
                f"(action {self.row.action!r}): {self.message}")


# ── the registry ──────────────────────────────────────────────────────────

_ROWS: list[Endpoint] = []
#: ``(method, path) -> the qualname of the function the decorator form decorated``. The decorator
#: form delegates its declaration to a stub function that is NOT an action; this map is what makes
#: that a check (V9) instead of a convention.
_STUBS: dict[tuple[str, str], str] = {}


def endpoint_rows() -> tuple[Endpoint, ...]:
    """Every row registered in this process, in registration order."""
    return tuple(_ROWS)


def reset_endpoints() -> None:
    """Forget every registered row. For a rebuilt application, and for tests."""
    _ROWS.clear()
    _STUBS.clear()


def register_endpoint(row: Endpoint, *, stub: str = "") -> Endpoint:
    """Append *row* to the registry and return it.

    *stub* is the qualname of the function the decorator form decorated; it is recorded so
    :func:`validate_endpoints` can refuse a row whose declaration stub is itself an action.
    """
    _ROWS.append(row)
    if stub:
        _STUBS[(row.method, row.path)] = stub
    return row


F = TypeVar("F", bound=Callable[..., Any])


def endpoint(method: str, path: str, *, action: str, risk: Risk, summary: str = "",
             tags: Sequence[str] = (), mcp: bool = True,
             response: Any = None) -> Callable[[F], F]:
    """The decorator form of a row — sugar over :func:`register_endpoint`.

        @endpoint("POST", "/servers/{server_name}/stop", action="stop_server", risk=Risk.LIFECYCLE)
        async def _stop() -> None:
            \"\"\"Stop a running or paused DCS server.\"\"\"

    The decorated callable is a DECLARATION STUB and is returned unchanged: it is never called, and
    giving it the name of a registered action is refused by the validator (V9). ``summary`` defaults
    to the stub's docstring first line — the sentence the route and the tool both show.
    """
    def decorate(fn: F) -> F:
        register_endpoint(
            Endpoint(method, path, action, risk,
                     summary=summary or _first_line(inspect.getdoc(fn)),
                     tags=tuple(tags), mcp=mcp, response=response),
            stub=getattr(fn, "__qualname__", ""),
        )
        return fn

    return decorate


class _Verbs:
    """``api_v1.get / .post / .put / .delete`` — the decorator with the verb already bound."""

    def get(self, path: str, **kwargs: Any) -> Callable[[F], F]:
        return endpoint("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Callable[[F], F]:
        return endpoint("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Callable[[F], F]:
        return endpoint("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Callable[[F], F]:
        return endpoint("DELETE", path, **kwargs)


#: The decorator spelling a plugin's ``api.py`` normally uses.
api_v1 = _Verbs()


def _module_is_absent(ex: ModuleNotFoundError, module_name: str) -> bool:
    """Whether this ``ModuleNotFoundError`` means the module ITSELF is not there — nothing else.

    ``importlib.import_module`` raises the same exception for "there is no ``plugins.<name>.api``"
    (ordinary: the plugin declares no rows) and for "the module is there and something IT imports is
    missing" (a failure that must be logged, because the rows are silently absent otherwise).
    ``ex.name`` carries the module that was not found, so the absent case is exactly the module or
    the package that would have to contain it.
    """
    missing = ex.name or ""
    return missing in (module_name, module_name.rpartition(".")[0])


def discover_endpoints(plugin_names: Sequence[str], *,
                       registry: Mapping[str, Callable] | None = None,
                       kind_of: Callable[[str], str] | None = None) -> tuple[Endpoint, ...]:
    """Import each plugin's ``api.py`` to register its rows, stamp their owner, and validate.

    Importing the module is what runs the declaration; the rows added by one import are stamped
    with that plugin's name, so a row cannot claim another plugin's action. A plugin with no
    ``api.py`` is ordinary and stays quiet; a plugin whose ``api.py`` EXISTS and fails to import is
    logged at ERROR — its rows are absent, and silence is indistinguishable from "no rows".

    Discovery is once per process: an already-imported module does not re-run its declarations, so a
    second call sees the rows the first one registered. :func:`reset_endpoints` drops the rows for
    tests and for a rebuilt application.
    """
    for plugin_name in plugin_names:
        module_name = f"plugins.{plugin_name}.api"
        before = len(_ROWS)
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError as ex:
            if _module_is_absent(ex, module_name):
                continue
            log.error("API surface: the api module of plugin '%s' (%s) exists but could not be "
                      "imported - its endpoint rows are NOT registered. The remaining plugins "
                      "are unaffected.", plugin_name, module_name, exc_info=True)
            continue
        except Exception:
            log.error("API surface: the api module of plugin '%s' (%s) raised while being imported "
                      "- its endpoint rows are NOT registered. The remaining plugins are "
                      "unaffected.", plugin_name, module_name, exc_info=True)
            continue
        for index in range(before, len(_ROWS)):
            if not _ROWS[index].owner:
                _ROWS[index] = dataclasses.replace(_ROWS[index], owner=plugin_name)
    return validate_endpoints(tuple(_ROWS), registry=registry, kind_of=kind_of)


# ── the static validation ─────────────────────────────────────────────────

def _default_registry() -> Mapping[str, Callable]:
    """The action registry of this process, read lazily (``core.actions`` is imported on demand)."""
    from core import actions

    return actions.action_registry()


def _default_kind_of() -> Callable[[str], str]:
    """The read/write kind of an action: ``core.actions``' accessor when it exists.

    Until ``@action`` carries a kind, ``core.actions`` registers none — and an action whose kind is
    unknown answers WRITE, the fail-safe direction: a write exposed as a read would skip both the
    level floor and the audit, while a read exposed as a write only demands more than it needs.
    """
    from core import actions

    accessor = getattr(actions, "kind_of", None)
    if callable(accessor):
        return accessor
    kinds = getattr(actions, "_ACTION_KINDS", None)
    if isinstance(kinds, Mapping):
        return lambda qualname: kinds.get(qualname, WRITE)
    return lambda qualname: WRITE


def _path_params(path: str) -> tuple[str, ...]:
    """The ``{name}`` segments of *path*, in order."""
    names: list[str] = []
    for piece in path.split(OPEN_BRACE)[1:]:
        name = piece.split(CLOSE_BRACE, 1)[0]
        if name:
            names.append(name)
    return tuple(names)


def _signature_of(fn: Callable) -> inspect.Signature | None:
    """The action's signature, or ``None`` when it cannot be read."""
    try:
        return inspect.signature(fn)
    except (TypeError, ValueError):
        return None


def _hints_of(fn: Callable) -> dict[str, Any]:
    """Resolved annotations of *fn* — string annotations evaluated in the action's own module."""
    try:
        return typing.get_type_hints(fn)
    except Exception:
        return {}


def _return_class(fn: Callable) -> Any:
    """The action's return annotation as an object, or ``None`` when it cannot be resolved."""
    annotation = _hints_of(fn).get("return")
    if annotation is not None:
        return annotation
    signature = _signature_of(fn)
    if signature is None or signature.return_annotation is inspect.Signature.empty:
        return None
    annotation = signature.return_annotation
    return None if isinstance(annotation, str) else annotation


def _is_action_result(value: Any) -> bool:
    """Whether *value* is an :class:`ActionResult` subclass."""
    return isinstance(value, type) and issubclass(value, ActionResult)


def _row_problems(row: Endpoint, index: int, registry: Mapping[str, Callable],
                  kind_of: Callable[[str], str]) -> list[Problem]:
    """Every invariant ONE row violates on its own (V1-V4, V7-V11)."""
    def problem(code: str, message: str) -> Problem:
        return Problem(code, index, row, message)

    problems: list[Problem] = []

    # V8 — the path is relative to the transport's prefix and cannot escape it.
    if not row.path.startswith("/"):
        problems.append(problem("V8", "the path must start with '/'"))
    if ".." in row.path:
        problems.append(problem("V8", "the path must not contain '..'"))
    if row.path.startswith("/api") or row.path == "/mcp" or row.path.startswith("/mcp/"):
        problems.append(problem("V8", "the path must not name a prefix of its own "
                                      "('/api...' or '/mcp')"))

    # V10 — an owner names the package the row was imported out of.
    if not row.owner:
        problems.append(problem("V10", "the row has no owner; rows are stamped by discovery"))

    # V7 — a risk class is present and valid, and SECRET never reaches an agent.
    risk: Risk | None
    try:
        risk = Risk(row.risk)
    except (TypeError, ValueError):
        risk = None
        problems.append(problem("V7", f"the risk class {row.risk!r} is not one of "
                                      f"{[member.value for member in Risk]}"))
    if risk is not None and risk is Risk.SECRET and row.mcp:
        problems.append(problem("V7", "a SECRET row must declare mcp=False"))

    # V11 — the response model is an ActionResult subclass.
    response = row.response if row.response is not None else None
    if response is not None and not _is_action_result(response):
        problems.append(problem("V11", f"the declared response {response!r} is not an "
                                       "ActionResult subclass"))

    fn = registry.get(row.action)
    if fn is None:
        # V1 — an action that is not registered in THIS process cannot be served.
        problems.append(problem("V1", "names an action that is not registered in this process; "
                                      "its plugin is not loaded or its actions module failed to "
                                      "import"))
        return problems

    # V9 — the decorator's stub is not itself an action.
    stub = _STUBS.get((row.method, row.path))
    if stub and stub in registry:
        problems.append(problem("V9", f"the declaration stub {stub!r} is a registered action; "
                                      "a row must not decorate an action"))

    # V2 — the row's class agrees with the action's own kind, in BOTH directions.
    kind = kind_of(row.action)
    if risk is Risk.READ and kind != READ:
        problems.append(problem("V2", f"a READ row names the {kind} action '{row.action}'"))
    elif risk is not None and risk is not Risk.READ and kind == READ:
        problems.append(problem("V2", f"a {risk.value} row names the {kind} action '{row.action}'"))

    signature = _signature_of(fn)
    if signature is None:
        problems.append(problem("V3", "the action's signature cannot be read, so the row's "
                                      "parameters cannot be derived"))
        return problems
    params = {name: param for name, param in signature.parameters.items()
              if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                inspect.Parameter.KEYWORD_ONLY)}

    # V3 — every path segment is a REQUIRED parameter of the action.
    path_params = _path_params(row.path)
    for name in path_params:
        param = params.get(name)
        if param is None:
            problems.append(problem("V3", f"the path segment {{{name}}} is not a parameter of "
                                          "the action"))
        elif param.default is not inspect.Parameter.empty:
            problems.append(problem("V3", f"the path segment {{{name}}} must be a REQUIRED "
                                          "parameter of the action"))

    # V4 — a GET completes from its path and its query, so every other parameter has a default.
    if row.method == "GET":
        for name, param in params.items():
            if name in path_params or name == CTX:
                continue
            if param.default is inspect.Parameter.empty:
                problems.append(problem("V4", f"a GET cannot require the parameter '{name}'; "
                                              "give it a default or move the row to a write verb"))

    # V11 — the return annotation is an ActionResult subclass (an unresolvable one is not judged).
    if response is None:
        returned = _return_class(fn)
        if returned is not None and not _is_action_result(returned):
            problems.append(problem("V11", f"the action returns {returned!r}, which is not an "
                                           "ActionResult subclass"))
    return problems


def check_endpoints(rows: Sequence[Endpoint], *, registry: Mapping[str, Callable] | None = None,
                    kind_of: Callable[[str], str] | None = None) -> list[Problem]:
    """Every invariant violation in *rows*, as a list of :class:`Problem`. Pure: no framework import.

    The per-row proofs are V1-V4 and V7-V11; the two cross-row proofs are V5 (no two rows share
    ``(method, path)``, whoever owns them) and V6 (no two rows share a TOOL name, since MCP has one
    namespace). For both, the row that keeps the key is the FIRST one in the list and the later row
    is the offending one — the order in which FastAPI matches, so the verdict and the behaviour
    agree.
    """
    resolved_registry = registry if registry is not None else _default_registry()
    resolved_kind_of = kind_of if kind_of is not None else _default_kind_of()
    problems: list[Problem] = []
    paths: dict[tuple[str, str], Endpoint] = {}
    tools: dict[str, Endpoint] = {}
    for index, row in enumerate(rows):
        problems.extend(_row_problems(row, index, resolved_registry, resolved_kind_of))
        key = (row.method, row.path)
        if key in paths:
            problems.append(Problem("V5", index, row,
                                    f"duplicates the path of the row owned by "
                                    f"{paths[key].owner or '<no owner>'}"))
        else:
            paths[key] = row
        if row.mcp:
            if row.action in tools:
                problems.append(Problem("V6", index, row,
                                        f"duplicates the tool name of the row owned by "
                                        f"{tools[row.action].owner or '<no owner>'}"))
            else:
                tools[row.action] = row
    return problems


def validate_endpoints(rows: Sequence[Endpoint], *, registry: Mapping[str, Callable] | None = None,
                       kind_of: Callable[[str], str] | None = None) -> tuple[Endpoint, ...]:
    """The rows that survive every invariant, in order; each refused row is logged at ERROR.

    A broken row is dropped and NAMED — owner, method, path, action and the sentence — because a row
    that is present-but-broken must be loud, and the transports then serve the rows that are sound
    instead of failing the whole surface.
    """
    ordered = tuple(rows)
    problems = check_endpoints(ordered, registry=registry, kind_of=kind_of)
    refused = {problem.index for problem in problems}
    for problem in problems:
        log.error("API surface: row refused - %s", problem)
    return tuple(row for index, row in enumerate(ordered) if index not in refused)


# ── the derivation: parameters, and the tool spec ─────────────────────────

def parameter_docs(fn: Callable) -> dict[str, str]:
    """``{parameter: description}`` from the ``Args:`` block of *fn*'s docstring, or ``{}``.

    ONE parser for both transports: the route's OpenAPI entry and the MCP tool schema word a
    parameter the same way because they read the same sentence.
    """
    docs: dict[str, str] = {}
    doc = inspect.getdoc(fn)
    if not doc:
        return docs
    in_args = False
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(("args:", "arguments:")):
            in_args = True
            continue
        if in_args:
            if not stripped or stripped.startswith(("Returns:", "Raises:", "Yields:")):
                break
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                if key and not key.startswith("-") and " " not in key:
                    docs[key] = value.strip()
    return docs


def _first_line(doc: str | None) -> str:
    """The first non-empty line of *doc*, or ``""``."""
    for line in (doc or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def parameters_for(row: Endpoint, fn: Callable) -> list[inspect.Parameter]:
    """The wire parameter list of *row*, derived from *fn*'s own signature.

    Every parameter of the action except ``ctx`` becomes one parameter, in the action's order, with
    the action's annotation RESOLVED to a real type (a plugin module under ``from __future__ import
    annotations`` yields strings, and a transport cannot build a schema out of a string). An
    unresolvable annotation becomes ``Any`` — never a string, and never a silent drop.

    ``*args``/``**kwargs`` are not parameters a transport can declare; they are skipped, so the
    derived signature is explicit by construction.
    """
    signature = _signature_of(fn)
    if signature is None:
        return []
    hints = _hints_of(fn)
    docs = parameter_docs(fn)
    derived: list[inspect.Parameter] = []
    for name, param in signature.parameters.items():
        if name == CTX or param.kind in (inspect.Parameter.VAR_POSITIONAL,
                                         inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = hints.get(name, param.annotation)
        if annotation is inspect.Parameter.empty or isinstance(annotation, str):
            annotation = Any
        default = param.default
        derived.append(inspect.Parameter(
            name, inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=default if default is not inspect.Parameter.empty else inspect.Parameter.empty,
            annotation=annotation,
        ))
    return derived


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """The row projected onto what an MCP tool needs — a plain dataclass, no ``mcp`` import.

    ``name`` is the row's action qualname: the tool name AND the registry key it dispatches on, so
    the tool list and the registry cannot disagree. ``parameters`` is :func:`parameters_for` with no
    transport annotation on it, so the consumer decides whether a parameter is a path segment, a
    query value or a body field.
    """

    name: str
    description: str
    parameters: tuple[inspect.Parameter, ...]
    risk: Risk


def tool_spec_for(row: Endpoint, fn: Callable) -> ToolSpec:
    """The tool spec of *row* — the whole MCP contract, derived from the row and the action."""
    return ToolSpec(
        name=row.action,
        description=row.summary or _first_line(inspect.getdoc(fn)),
        parameters=tuple(parameters_for(row, fn)),
        risk=Risk(row.risk),
    )


# ── the route builder ─────────────────────────────────────────────────────

#: The outcome codes :class:`ActionResult` may carry, read from ``core.action_results`` when it
#: declares them. The literals are the wire contract; the constants are the single source.
def _outcome_code(name: str, fallback: str) -> str:
    from core import action_results

    return getattr(action_results, name, fallback)


ACTION_UNAVAILABLE = _outcome_code("ACTION_UNAVAILABLE", "action_unavailable")
TARGET_BUSY = _outcome_code("TARGET_BUSY", "target_busy")
ACTION_FAILED = _outcome_code("ACTION_FAILED", "action_failed")

#: The status a result's outcome maps to, and the headers that go with it.
_STATUS_FOR_CODE: dict[str, tuple[int, dict[str, str]]] = {
    ACTION_UNAVAILABLE: (501, {}),
    TARGET_BUSY: (409, {"Retry-After": "2"}),
    ACTION_FAILED: (500, {}),
}


def status_for(result: ActionResult) -> tuple[int, dict[str, str]]:
    """``(status, headers)`` for one result: 200/400 from ``success``, else from the code.

    The mapping keys on the MACHINE-readable ``code`` and never on the message: a domain refusal is
    a 400, an absent action a 501, an in-flight target a 409 (with the retry hint the guard's own
    sentence gives), and a raised action a 500.
    """
    code = getattr(result, "code", "") or ""
    if code in _STATUS_FOR_CODE:
        return _STATUS_FOR_CODE[code]
    return (200 if result.success else 400), {}


def envelope_model_for(result_cls: type) -> Any:
    """The pydantic model of the transport ENVELOPE, ``{"status", "message", "code", **data}``.

    ``status`` (``ok``/``error``) and ``message`` are on every body; ``code`` is DECLARED with an
    empty default so every documented status names the key a client switches on — the handler
    renders :func:`body_of` itself and omits an empty code, so the default never reaches the wire,
    and the declaration is what stops the key from being schema-invisible.

    Everything else rides as an extra property: the keys of ``data``, plus any field a subclass adds
    in its own ``to_dict``. A subclass FIELD is deliberately not documented, for two reasons that
    both come from the wire: ``to_dict`` emits the envelope and not the subclass fields, and a
    result may declare a field of its own called ``status`` — numbering the envelope's own key
    would collide with it.
    """
    from pydantic import ConfigDict, Field, create_model

    return create_model(f"{result_cls.__name__}Envelope",
                        __config__=ConfigDict(extra="allow"),
                        status=(str, ...), message=(str, ""),
                        code=(str, Field(default="", description=(
                            "machine-readable outcome code; absent when the result carries none"))))


def body_of(result: ActionResult) -> dict[str, Any]:
    """The wire body of *result*: its envelope plus ``code`` when the result carries one.

    ``to_dict`` is shared with the console and the Discord renderer and never emits ``code``, so the
    transport adds it here — after ``status`` and ``message``, before the flattened ``data`` keys,
    which is the shape a client switching on the code reads. An empty code adds nothing: an ordinary
    answer is exactly ``{"status", "message", **data}``.
    """
    body = result.to_dict()
    code = getattr(result, "code", "") or ""
    if not code:
        return body
    envelope: dict[str, Any] = {}
    for key in ("status", "message"):
        if key in body:
            envelope[key] = body[key]
    envelope["code"] = code
    for key, value in body.items():
        if key not in envelope:
            envelope[key] = value
    return envelope


def response_class_for(row: Endpoint, fn: Callable) -> type | None:
    """The declared response class of *row*, else the action's return annotation.

    ``None`` when neither is an ``ActionResult`` subclass — an unresolvable annotation documents no
    schema rather than a wrong one.
    """
    declared = row.response if row.response is not None else _return_class(fn)
    return declared if _is_action_result(declared) else None


def _http_parameters(row: Endpoint, fn: Callable) -> list[inspect.Parameter]:
    """The wire parameters annotated for FastAPI: ``Path`` in the path, ``Query``/``Body`` beside it.

    A GET's non-path parameters are query values; every other verb's are BODY fields. The split is
    not cosmetic: an unannotated parameter on a POST lands in the query string, so a client posting
    the documented JSON body would be refused for a parameter it did send.
    """
    from fastapi import Body, Path, Query

    path_params = set(_path_params(row.path))
    docs = parameter_docs(fn)
    annotated: list[inspect.Parameter] = []
    for param in parameters_for(row, fn):
        description = docs.get(param.name) or None
        if param.name in path_params:
            marker: Any = Path(description=description)
        elif row.method == "GET":
            marker = Query(description=description)
        else:
            marker = Body(description=description)
        annotated.append(param.replace(annotation=typing.Annotated[param.annotation, marker]))
    return annotated


class RouteGuard:
    """What a transport must provide for :func:`build_route` — three methods, duck-typed.

    ``dependency(row)``      a FastAPI dependency that authenticates and authorises the CALLER;
    ``context(row, params)`` the transport's ``ActionContext`` for this request;
    ``dispatch(row, ctx, params)`` runs the action and returns its ``ActionResult``.

    ONE guard serves every request, so per-request state (the verified principal, the request's
    context) reaches ``context``/``dispatch`` through the dependency — a ``ContextVar`` set there and
    reset after the call. ``core`` names the contract and never implements it: authentication is a
    service's business.
    """

    def dependency(self, row: Endpoint) -> Callable[..., Any]:  # pragma: no cover - protocol
        raise NotImplementedError

    def context(self, row: Endpoint, params: Mapping[str, Any]) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def dispatch(self, row: Endpoint, ctx: Any,
                       params: Mapping[str, Any]) -> ActionResult:  # pragma: no cover
        raise NotImplementedError


def build_route(router: Any, row: Endpoint, *, fn: Callable, guard: RouteGuard,
                prefix: str = "") -> None:
    """Append ONE route for *row* to *router*.

    The route handler is generic and its SIGNATURE IS SYNTHETIC (``__signature__`` from
    :func:`_http_parameters`), so FastAPI generates an explicit schema — path segment, query value or
    body field, by name and type — and never a catch-all. Its stable name (``v1_<action>``) pins the
    generated body-model name and the OpenAPI operation id.

    Per request, in this order: build the context, resolve a ``server_name`` path segment BEFORE
    dispatch (an unknown target is a 404 with the resolver's own sentence, so no action runs against
    a name nothing answers to), dispatch, then map the result to a status and render
    :func:`body_of` — the envelope plus ``code`` when the result carries one. The handler answers a
    ``JSONResponse`` for every outcome, so the declared envelope model documents the schema and the
    keys on the wire come from :func:`body_of` alone.
    """
    from fastapi import Depends
    from fastapi.responses import JSONResponse

    async def handler(**kwargs: Any) -> Any:
        ctx = guard.context(row, kwargs)
        if "server_name" in kwargs:
            resolution = await ctx.resolve_scoped_server(kwargs["server_name"])
            if not resolution.is_found:
                return JSONResponse(status_code=404,
                                    content={"status": "error", "message": resolution.message})
        result = await guard.dispatch(row, ctx, dict(kwargs))
        status, headers = status_for(result)
        return JSONResponse(status_code=status, content=body_of(result), headers=headers or None)

    handler.__name__ = handler.__qualname__ = f"v1_{row.action}"
    handler.__signature__ = inspect.Signature(parameters=_http_parameters(row, fn))  # type: ignore[attr-defined]
    handler.__doc__ = row.summary or _first_line(inspect.getdoc(fn))

    response_cls = response_class_for(row, fn)
    model = envelope_model_for(response_cls) if response_cls is not None else None
    responses = {} if model is None else {
        status: {"model": model} for status in (400, 404, 409, 500, 501)
    }
    router.add_api_route(
        f"{prefix}{row.path}", handler, methods=[row.method], name=f"v1_{row.action}",
        response_model=model, responses=responses or None,
        summary=row.summary or None, description=handler.__doc__,
        tags=list(row.tags or (row.owner.replace("_", " ").title(),)),
        dependencies=[Depends(guard.dependency(row))],
    )


def build_router(rows: Sequence[Endpoint], *, guard: RouteGuard, prefix: str = "",
                 registry: Mapping[str, Callable] | None = None) -> Any:
    """The rows as an ``APIRouter``: one route per row whose action is registered.

    *prefix* is applied to every row's own path (a row's path is RELATIVE to the transport's
    prefix). A row whose action is absent from this process is skipped with a WARNING rather than
    registered: its route could only answer a refusal.

    The rows are not re-validated here; :func:`validate_endpoints` runs once at discovery.
    """
    from fastapi import APIRouter

    actions = registry if registry is not None else _default_registry()
    router = APIRouter()
    for row in rows:
        fn = actions.get(row.action)
        if fn is None:
            log.warning("API surface: %s is not registered - route %s %s is not installed.",
                        row.action, row.method, row.path)
            continue
        build_route(router, row, fn=fn, guard=guard, prefix=prefix)
    return router
