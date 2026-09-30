"""
ActionContext — shared context for action functions.

Actions are transport-agnostic domain logic functions. They take typed parameters,
resolve servers via ActionContext, perform the operation, and return a result
dataclass. Discord commands, REST API routes, and MCP tools all delegate to the
same action functions and wrap the results for their transport.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from core.action_results import ActionResult

if TYPE_CHECKING:
    from core import NodeImpl, Server

log = logging.getLogger(__name__)

# ── the action registry ────────────────────────────────────────────────────
# Functions decorated with @action are the ONE declaration every transport reads: a Discord command,
# a REST call and an MCP tool all reach the same function through it. Plugins simply decorate their
# action functions — no hard-coded imports needed.

_ACTIONS: dict[str, Callable] = {}


def action(fn: Callable) -> Callable:
    """Decorator: register a function as an ACTION this installation can offer.

    Usage in any plugin's actions.py::

        from core.actions import action

        @action
        async def start_server(ctx: Any, server_name: str) -> ServerControlResult:
            ...

    The declaration is transport-neutral: the MCP service, the admin web console and the Discord
    commands all read the same registry this fills, so adding a plugin's action needs no edit on any
    of them. Discovery scans loaded plugins' actions.py modules and auto-registers every decorated
    function.
    """
    _ACTIONS[fn.__qualname__] = fn
    return fn


def action_registry() -> dict[str, Callable]:
    """Return a copy of the current action registry."""
    return dict(_ACTIONS)


def _actions_module_is_absent(ex: ModuleNotFoundError, module_name: str) -> bool:
    """Whether this ``ModuleNotFoundError`` means the ACTIONS MODULE is not there — nothing else.

    The distinction is the whole point of this function. ``importlib.import_module`` raises the same
    exception for "there is no ``plugins.<name>.actions``" (ordinary: the plugin ships no actions, or
    is not deployed) and for "the module is there and something IT imports is missing" (a failure:
    the console would silently offer no write control). The two owe opposite answers, and reading
    them as one is how a broken install looks identical to a plugin that never had actions.

    ``ModuleNotFoundError.name`` carries the module that was not found, so the absent case is exactly
    the actions module itself — or the plugin package that would have to contain it.
    """
    missing = ex.name or ""
    return missing in (module_name, module_name.rpartition(".")[0])


def discover_actions(plugin_names: list[str]) -> dict[str, Callable]:
    """Import each plugin's actions.py to trigger @action registration,
    then return the full registry.

    Called by each transport before it uses the actions (the MCP service and the admin web console
    both call it). Importing the module is what runs the decorators and populates the registry.

    A plugin with NO actions module is ordinary and stays quiet; a plugin whose actions module EXISTS
    and fails to import is logged at WARNING, with its name and the traceback, because on a live
    install that failure is otherwise invisible — no control is offered and nothing says why, which
    is indistinguishable from "the files were never deployed". One broken plugin never costs the
    others their discovery: the failure is per-plugin and the loop continues.
    """
    for plugin_name in plugin_names:
        module_name = f"plugins.{plugin_name}.actions"
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError as ex:
            if _actions_module_is_absent(ex, module_name):
                continue  # plugin has no actions module — fine
            log.warning("Action discovery: the actions module of plugin '%s' (%s) exists but could "
                        "not be imported - its actions are NOT registered and no console control "
                        "will be offered for them. The remaining plugins are unaffected.",
                        plugin_name, module_name, exc_info=True)
        except Exception:
            log.warning("Action discovery: the actions module of plugin '%s' (%s) raised while being "
                        "imported - its actions are NOT registered and no console control will be "
                        "offered for them. The remaining plugins are unaffected.",
                        plugin_name, module_name, exc_info=True)
    return action_registry()


@dataclass(frozen=True)
class ServerResolution:
    """The outcome of resolving a NAME for an action, with the caller's scope applied.

    THREE outcomes and not two (``WRITE-ACTIONS-DESIGN.md`` §2.4), because the two failures owe
    DIFFERENT answers and collapsing them would lie:

    * :data:`FOUND` — the server, in the caller's scope;
    * :data:`NOT_FOUND` — no such server: the action's own typed refusal
      (:attr:`message`), rendered inline;
    * :data:`NOT_PERMITTED` — the ROUTE owes the console's refusal (403 / refusal page /
      ``{"detail": …}``). It carries NO message on purpose: the answer belongs to the route, and
      a value here would be a second place to word it.

    ``name`` and ``server`` are excluded from equality: the ANSWER is the ``status``, so two
    refusals for different names compare equal. That is exactly what makes "the caller learns
    nothing from the difference" checkable instead of merely intended.
    """

    FOUND = "found"
    NOT_FOUND = "not-found"
    NOT_PERMITTED = "not-permitted"

    status: str
    name: str = field(default="", compare=False)
    server: Any = field(default=None, repr=False, compare=False)

    @property
    def is_found(self) -> bool:
        return self.status == self.FOUND

    @property
    def message(self) -> str:
        """The typed refusal copy for a name that does not exist — and nothing for the other two.

        The wording is the one the mission actions already use (``plugins/mission/actions.py:36``),
        so a console refusal and an MCP refusal read the same.
        """
        if self.status == self.NOT_FOUND:
            return f"Server '{self.name}' not found."
        return ""


def _managed_by(server) -> tuple:
    """A server's declared ``managed_by`` values, read tolerantly.

    The TWIN of ``services.webservice.scope.server_managed_by``, and deliberately not an import of
    it: ``core`` may not reach a service package (``tests/test_layering_direction.py``), and the
    RULE itself — what a ``managed_by`` list means — is not restated here: it stays in
    ``core.utils.discord.holds_scope``, which the caller's scope value applies. This is the config
    read only. (Consolidating the two readers into ``core`` next to ``holds_scope`` is a
    follow-up: this card may only add to ``core/actions.py``.)
    """
    locals_ = getattr(server, "locals", None)
    if locals_ is None or not hasattr(locals_, "get"):
        return ()
    return tuple(locals_.get("managed_by") or ())


@dataclass(frozen=True)
class NodeResolution:
    """The outcome of resolving a NODE name for an action, with the caller's view applied.

    The deliberate TWIN of :class:`ServerResolution`, same three outcomes for the same reason
    (``WRITE-ACTIONS-DESIGN.md`` §2.4), because a node write owes the caller the same answer shape a
    server write does:

    * :data:`FOUND` — the node, reachable from this installation (its object is in the caller's
      view, which for a node means the cluster's registry said it is ALIVE: ``all_nodes[name] is
      None`` is the heartbeat's verdict that it is not);
    * :data:`NOT_FOUND` — no node object to act on, either because the name matches nothing or
      because the node is offline. Both are the action's own typed refusal, rendered inline;
    * :data:`NOT_PERMITTED` — the ROUTE owes the console's refusal. It carries NO message on
      purpose: the answer belongs to the route.

    ``name`` and ``node`` are excluded from equality for the same reason their server twins are:
    the ANSWER is the ``status``, so two refusals for different names compare equal — which is what
    makes "the caller learns nothing from the difference" checkable rather than merely intended.

    Nodes are resolved from the caller's own view of the cluster (the console's ``_Cluster`` carries
    ``nodes``, the Discord/MCP transports fall back to ``node.all_nodes``), so this is a registry
    read: no RPC is issued, which is the same rule the node page's render obeys.
    """

    FOUND = "found"
    NOT_FOUND = "not-found"
    NOT_PERMITTED = "not-permitted"

    status: str
    name: str = field(default="", compare=False)
    node: Any = field(default=None, repr=False, compare=False)

    @property
    def is_found(self) -> bool:
        return self.status == self.FOUND and self.node is not None

    @property
    def message(self) -> str:
        """The typed refusal copy for a node that cannot be acted on — nothing for the other two.

        ONE wording for both spellings of "not reachable": a name nothing answers to and a node the
        cluster knows about but cannot reach. A caller cannot tell them apart from the answer, which
        is the point — the node page already renders the heartbeat verdict, so neither is a secret.
        """
        if self.status == self.NOT_FOUND:
            return f"Node '{self.name}' is offline or unknown."
        return ""


def _node_entries(container) -> dict:
    """The ``{name: node-or-None}`` mapping a container carries, or an empty one.

    Two shapes answer to the same contract: the console's ``_Cluster`` (a per-request, SCOPED view)
    and the process's own node (``all_nodes``, the master's in-process registry). A container that
    carries neither is simply empty — never an exception.
    """
    nodes = getattr(container, "nodes", None)
    if isinstance(nodes, dict):
        return nodes
    return dict(getattr(container, "all_nodes", None) or {})


def _find_node(entries: dict, name: str) -> tuple[bool, Any]:
    """``(present, node)`` for *name* in *entries*, by exact then case-insensitive match.

    ``present`` with a ``None`` node is the meaningful third case — the cluster knows the node and
    cannot reach it — which :func:`NodeResolution.message` and the read models both report. The
    case fold mirrors :meth:`ActionContext.resolve_server`, so ``DCS-A-01`` and ``dcs-a-01`` name
    ONE node for the guard, the resolver and the action alike.
    """
    wanted = str(name or "")
    if not wanted:
        return False, None
    if wanted in entries:
        return True, entries[wanted]
    folded = wanted.casefold()
    for key, node in entries.items():
        if str(key).casefold() == folded:
            return True, node
    return False, None


# ── the dispatcher: one implementation, keyed on ``qualname`` ──────────────
# ``@action`` keys the registry on ``fn.__qualname__``, and that is the key a console dispatcher
# must use too — ``fn.__name__`` collides across plugins (services/mcpservice/server.py:120 uses
# the NAME for the MCP tool; the two vocabularies are not the same one).

#: the servers an action is running against RIGHT NOW, keyed by the KIND-SCOPED, CASE-FOLDED target
#: (see ``_normalise_target``): the in-flight guard (design §6 row 6b) is per TARGET and per process
#: — a second request for the same server is refused rather than queued, and a restart (which
#: internally stops and starts ONE server) is not refused by its own halves. The kind is part of the
#: key so a NODE and a SERVER that happen to share a name cannot lock each other out.
_in_flight: set[str] = set()


def in_flight_targets() -> frozenset[str]:
    """The target keys an action is running against right now (the guard's own view).

    Each key carries its KIND — ``server:<folded name>`` or ``node:<folded name>`` — because the two
    target vocabularies are separate: a node named like a server must not be refused by it.
    """
    return frozenset(_in_flight)


def reset_in_flight() -> None:
    """Forget every in-flight target. For a rebuilt application, and for tests."""
    _in_flight.clear()


def _normalise_target(params) -> tuple[str | None, str | None]:
    """The target a call is about: ``(value, key)``, or ``(None, None)`` when it states none.

    ONE normalisation serves both readers, and that is the point (review M-4): the guard keys the
    target and the ACTION resolves it, so the two must be handed the same string — a raw ``"Real "``
    used to reach the action as a name ``resolve_server`` answers NOT_FOUND for, while the guard had
    already stripped it.

    * the value is the parameter with surrounding whitespace removed, written BACK into ``params``
      so the action receives the normalised form (a string that is only whitespace normalises to
      ``""``, which is no target at all);
    * the key is that value CASE-FOLDED and prefixed with the target's KIND, because the lookups fold
      case (``resolve_server``, ``_find_node``): ``SRS-1`` and ``srs-1`` name ONE server, so they
      must not run concurrently (review M-1), while ``server:SRS-1`` and ``node:SRS-1`` are two
      different things that may.

    Both vocabularies are read here — ``server_name``/``server`` and ``node_name``/``node`` — so a
    node write gets the same single-flight protection a server write has, which is what a
    cluster-wide destructive action most needs.
    """
    for kind, names in (("server", ("server_name", "server")), ("node", ("node_name", "node"))):
        for name in names:
            value = params.get(name)
            if isinstance(value, str):
                value = value.strip()
                params[name] = value
                if value:
                    return value, f"{kind}:{value.casefold()}"
    return None, None


def action_available(qualname: str) -> bool:
    """Whether an action is registered in this process (design §2.5 / §3.6).

    Discovery is the existing one (``discover_actions``); this answers from the registry it
    fills, so a page can OMIT a control whose action is absent instead of offering a button that
    could only answer a refusal.
    """
    return qualname in _ACTIONS


async def call_action(qualname: str, ctx: ActionContext, /, *, audit_result: bool = False,
                      **params) -> ActionResult:
    """Invoke the action *qualname* on *ctx* — the only way a console write reaches an action.

    Four guarantees, each of which is a failure mode this console must not have:

    * an ABSENT action (its plugin is not loaded) is a typed :class:`ActionResult` refusal, never
      a 500 and never a silent success;
    * a SECOND concurrent call against the same target is refused with a typed refusal — the
      in-flight guard (design §6 row 6b). No queueing: the caller is told to retry. The target is
      normalised ONCE here (``_normalise_target``), so the guard's key and the name the action
      resolves are the same string, whatever the caller's casing or stray whitespace;
    * an action that RAISES is typed as well. The action functions already catch their own domain
      errors and return a result (``plugins/mission/actions.py``); anything that escapes them is a
      bug, and a web route must still answer with a result rather than a stack trace;
    * the AUDIT is the ACTION's job (design §5.3 D1) and a convention nothing checks is not a
      mechanism, so a write that forgets the trail is visible in the log instead of silent. It is a
      WARNING and never a failure: the operation has already happened, and turning its outcome into
      an error would report a completed change as a failed one. The seam's own refusals (an absent
      action, an in-flight target) ran no action and are not warned about.

    ``audit_result`` is for the caller whose action CANNOT audit yet. The design puts the trail on
    the action so that one operation audits the same way from every transport; but where the action
    is a plugin function this card may not modify (the W2 pause pilot — ``pause_mission`` /
    ``unpause_mission``), the only alternative was for the ROUTE to audit a result the seam had
    already reported as unaudited — a false "the operation has no trail" in the log of a write that
    IS audited a microsecond later. Passing ``audit_result=True`` makes the SEAM write that entry
    itself (:func:`audit_action`, with the resolved target as the server), so the trail is still
    written once, by one mechanism, and the warning is reserved for a caller that audits nothing at
    all. When the plugin's actions take the trail over (the W-R4 follow-up card), the flag comes off
    with them and the default keeps the I-2 warning intact.
    """
    fn = _ACTIONS.get(qualname)
    if fn is None:
        return ActionResult(
            success=False,
            message=f"Action '{qualname}' is not available in this installation.",
        )
    target, key = _normalise_target(params)
    if key is not None and key in _in_flight:
        return ActionResult(
            success=False,
            message=f"Another action on {target} is still running. Wait and retry.",
        )
    if key is not None:
        _in_flight.add(key)
    try:
        result = await fn(ctx, **params)
    except Exception as ex:
        log.exception("Action '%s' failed", qualname)
        return ActionResult(success=False, message=f"Action '{qualname}' failed: {ex}")
    finally:
        if key is not None:
            _in_flight.discard(key)
    if not isinstance(result, ActionResult):
        result = ActionResult(success=bool(result), message=str(result))
    if not _records_an_audit(result):
        if audit_result:
            result = await audit_action(ctx, result,
                                        server=ctx.resolve_server(target) if target else None)
        else:
            log.warning("Action '%s' returned no audit entry (data['audit']) - the operation has no "
                        "trail. The action must call audit_action(ctx, result) before returning.",
                        qualname)
    return result


def _records_an_audit(result: ActionResult) -> bool:
    """Whether a result carries the audit seam's marker — ``data["audit"]``, whatever its value.

    The VALUE is the audit helper's business (:data:`AUDIT_RECORDED` / :data:`AUDIT_NOT_RECORDED`,
    both honest); what :func:`call_action` notices is only that the action reported *something*.
    """
    data = getattr(result, "data", None)
    return isinstance(data, dict) and "audit" in data


# ── the audit (design §5.3) ────────────────────────────────────────────────
#
# The trail is the CORE ``audit`` table plus the audit channel, written by ``bot.audit()``
# (``services/bot/dcsserverbot.py:473-546``, ``sql/tables.sql:31-40``). The logbook plugin CONSUMES
# it and stores none of it, so logbook-absent changes nothing here — do not build a logbook adapter.
#
# The actor: the ``audit`` row holds ``discord_id`` and ``ucid`` and nothing else, so a web caller
# is named by the declared ``[web:backend/subject]`` PREFIX in the event text (greppable:
# ``WHERE event LIKE '[web:%'``) — NEVER by passing a display name (it would land in ``ucid`` and
# corrupt player data) and never as a ucid.

#: what ``ActionResult.data["audit"]`` carries, on the one outcome that matters to the page
AUDIT_RECORDED = "recorded"
AUDIT_NOT_RECORDED = "not-recorded"

#: the module of the HEADLESS bot: its ``audit()`` is a no-op that returns normally, so "the entry
#: was recorded" cannot be confirmed by the call returning (design §5.3 D4).
HEADLESS_BOT_MODULE = "services.bot.dummy.bot"

#: The most characters of an action's OUTCOME the AUDIT event will ever carry. The event text is
#: built from a typed refusal that can QUOTE a caller-supplied name (``Server '<name>' not found.``),
#: so without a bound a crafted name grows the audit table without limit (review W2 I-1). The bound
#: lives HERE, at the one seam that builds the event, so every action — present and future —
#: inherits it instead of each route having to remember to clip.
AUDIT_EVENT_MAX_CHARS = 500

#: What a clipped value carries in place of its tail, so a reader SEES that a string was truncated
#: rather than reading a silently cut value as the whole of it (review W2 I-1: "mark the
#: truncation").
TRUNCATION_MARKER = " ... [truncated]"


def bound_text(value: Any, limit: int = AUDIT_EVENT_MAX_CHARS) -> str:
    """*value* as a string, clipped to *limit* characters with :data:`TRUNCATION_MARKER` if it was.

    The ONE place the clip and its marker are defined, so every bound in the seam agrees. The result
    is never longer than *limit* — the marker is INSIDE the budget — so a caller can assert the total
    against the constant instead of the constant plus a marker it forgot to account for.
    """
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:max(0, limit - len(TRUNCATION_MARKER))] + TRUNCATION_MARKER


def _audit_token(value) -> str:
    """A value safe to sit inside the ``[web:backend/subject]`` prefix: ONE line, no brackets.

    The prefix is the greppable actor token (``WHERE event LIKE '[web:%'``), so anything in it that
    can close the token early or break the line it is written on defeats the whole point — a subject
    carrying ``]`` could forge a second ``[web:…]`` prefix and a newline could mangle the event it
    prefixes (review M-2). The subjects are operator/Discord-controlled today, so this is hygiene
    rather than exploitability; the value is NEUTRALISED here rather than refused, because a mangled
    actor is visible in the trail while a refused identity is an outage.

    Every character outside the safe set — ``[``, ``]``, and anything non-printable (a newline, a
    tab, a control character) — is replaced by ``_`` and never dropped: the token stays recognisable
    and two different subjects cannot silently collapse onto one another.
    """
    cleaned = "".join("_" if (char in "[]" or not char.isprintable()) else char
                      for char in str(value or ""))
    return cleaned.strip()


def _can_confirm_audit(bot) -> bool:
    """Whether *bot* can POSITIVELY record an entry — a bot that is present and is not headless."""
    if bot is None:
        return False
    module = getattr(type(bot), "__module__", "") or ""
    return not module.startswith(HEADLESS_BOT_MODULE)


def _resolve_bot(ctx) -> Any:
    """The running bot, through the service registry, or ``None``. Never raises."""
    try:
        from core.services.registry import ServiceRegistry
        from services.bot.service import BotService

        return getattr(ServiceRegistry.get(BotService), "bot", None)
    except Exception:
        return None


async def audit_action(ctx: ActionContext, result: ActionResult, *, server: Any = None,
                       message: str | None = None, bot: Any = None) -> ActionResult:
    """Record ONE audit entry for an action's outcome, and report whether it was recorded.

    Called by the ACTION, once, whatever the transport (design §5.3 D1): auditing at the route
    would mean the same operation audits from Discord and not from the web — the divergence this
    seam exists to prevent. A REFUSED action is audited too (one entry per attempt, which is what
    an operator wants after an incident).

    Returns *result* with ``data["audit"]`` set to :data:`AUDIT_RECORDED` or
    :data:`AUDIT_NOT_RECORDED`. A failed, refused or unconfirmable audit is **reported, never
    silent** — and it never changes the operation's outcome: a success stays a success, which is
    also what keeps a failing audit channel from turning a completed operation into a 500.

    The event text is BOUNDED (:data:`AUDIT_EVENT_MAX_CHARS`) before it reaches the bot, because the
    refusal it carries can quote a caller-supplied name (``Server '<name>' not found.``) and a
    crafted name must not grow the audit table without limit (review W2 I-1). The actor prefix leads
    the text, so clipping the tail never loses who made the attempt.
    """
    event = bound_text(f"{ctx.audit_actor} {message or result.message or ''}".strip())
    target = bot if bot is not None else _resolve_bot(ctx)
    if not isinstance(getattr(result, "data", None), dict):
        result.data = {}
    if not _can_confirm_audit(target):
        log.error("Audit entry NOT recorded (actor %s, server %s): %s - no bot that can record it "
                  "is available in this process.", ctx.audit_actor or "unknown",
                  getattr(server, "name", None) or "-", event)
        result.data["audit"] = AUDIT_NOT_RECORDED
        return result
    try:
        await target.audit(event, server=server)
    except Exception:
        log.exception("Audit entry NOT recorded (actor %s, server %s): %s - the audit write "
                      "raised.", ctx.audit_actor or "unknown",
                      getattr(server, "name", None) or "-", event)
        result.data["audit"] = AUDIT_NOT_RECORDED
        return result
    result.data["audit"] = AUDIT_RECORDED
    return result


@dataclass
class ActionContext:
    """Resolves servers regardless of which transport initiated the call.

    Beyond the server registry this carries an IDENTITY, as far as it is needed to make the same
    decision on every transport (``WRITE-ACTIONS-DESIGN.md`` §2.2): the caller's role NAMES, its
    per-server SCOPE, and the audit's actor. ``_scope`` is DUCK-TYPED — the console's ``Scope``
    value is held, never its type — because ``core`` may not import a service package
    (``tests/test_layering_direction.py``).
    """
    node: Any          # NodeImpl
    bus: Any           # ServiceBus

    # Populated by from_* helpers; used for audit logging
    _audit_user: str = field(default="", repr=False)

    #: the caller's role names — the console's capability vocabulary. Populated by ``from_web``
    #: and, for the Discord transport, by ``from_interaction``, so a permission check inside an
    #: ACTION would make the same decision on both transports (design §9 Q4). Nothing reads them
    #: today: the route is still the only authorization for a console write.
    _roles: frozenset = field(default_factory=frozenset, repr=False)

    #: the caller's per-server scope value, or ``None`` for a caller that states none — which is
    #: the STRICT answer (see :meth:`resolve_scoped_server`). Only ``.allows(managed_by)`` and the
    #: ``unrestricted`` flag are read off it.
    _scope: Any = field(default=None, repr=False)

    #: the audit's actor for a WEB caller: its backend and its subject, written as the declared
    #: ``[web:backend/subject]`` prefix (design §5.3 D2). Empty for every other transport, whose
    #: actor is ``_audit_user``.
    _audit_backend: str = field(default="", repr=False)
    _audit_subject: str = field(default="", repr=False)

    @classmethod
    def from_interaction(cls, interaction) -> ActionContext:
        """Create context from a Discord interaction."""
        from services.servicebus import ServiceBus
        from core.services.registry import ServiceRegistry

        bot = interaction.client
        ctx = cls(node=bot.node, bus=ServiceRegistry.get(ServiceBus))
        if hasattr(interaction, 'user') and interaction.user:
            ctx._audit_user = interaction.user.display_name
            # No behaviour changes here today (nothing reads the roles yet); populating them is
            # what lets a future in-action check answer the SAME question for Discord and the
            # console (design §2.2 / §9 Q4). Role NAMES, because that is the vocabulary the
            # capability map is declared in.
            ctx._roles = frozenset(_role_names(interaction.user))
        return ctx

    @classmethod
    def from_service(cls, service) -> ActionContext:
        """Create context from a Service (e.g. MCPService)."""
        from services.servicebus import ServiceBus
        from core.services.registry import ServiceRegistry

        ctx = cls(node=service.node, bus=ServiceRegistry.get(ServiceBus))
        ctx._audit_user = "MCP"  # or pass explicitly per-call
        return ctx

    @classmethod
    def from_plugin(cls, plugin) -> ActionContext:
        """Create context from a Plugin instance."""
        from services.servicebus import ServiceBus
        from core.services.registry import ServiceRegistry

        ctx = cls(node=plugin.node, bus=ServiceRegistry.get(ServiceBus))
        ctx._audit_user = "API"
        return ctx

    @classmethod
    def from_web(cls, identity, node, bus) -> ActionContext:
        """Create context from a request of the admin web UI.

        `identity` is DUCK-TYPED and is never imported: the attributes read here are the ones the
        console's signed-in principal already carries — `roles`, `scope`, `shown_name`, and
        `backend`/`subject` for the audit. `core` must not reach a service package (pinned by
        `tests/test_layering_direction.py`), so the contract is ATTRIBUTES, not a class; a test
        hands in a `SimpleNamespace` with the same four and the seam cannot tell the difference.

        `node` and `bus` are the process's own (the web caller resolves them from the same service
        registry the other constructors use), never anything read off the request.

        `backend` and `subject` are normalised into the audit's TOKEN SAFE set here, at the ONE
        place the prefix is built from (``_audit_token``): the subject is operator/Discord-controlled
        text, and a bracket or a newline in it would forge a second ``[web:…]`` actor or break the
        event line (review M-2). The actor is read by nothing else, so sanitising it here covers
        every reader.
        """
        ctx = cls(node=node, bus=bus)
        ctx._roles = frozenset(getattr(identity, "roles", ()) or ())
        ctx._scope = getattr(identity, "scope", None)
        label = getattr(identity, "shown_name", None) or getattr(identity, "display_name", None)
        ctx._audit_user = str(label or getattr(identity, "subject", "") or "")
        ctx._audit_backend = _audit_token(getattr(identity, "backend", ""))
        ctx._audit_subject = _audit_token(getattr(identity, "subject", ""))
        return ctx

    @property
    def audit_actor(self) -> str:
        """The actor token the audit records: the declared web prefix, else the transport's label.

        ``[web:local/specialk]`` (or ``[web:discord/<id>]``, ``[web:breakglass/<user>]``) for a web
        caller — greppable and needing no schema change — and the display-name label for Discord,
        MCP and the REST surface, exactly as before.
        """
        if self._audit_backend:
            return f"[web:{self._audit_backend}/{self._audit_subject}]"
        return self._audit_user

    def resolve_server(self, name: str) -> Any | None:
        """Resolve a server name to a Server object. Handles exact and partial matches."""
        # Exact match first
        server = self.bus.servers.get(name)
        if server:
            return server
        # Case-insensitive match
        name_lower = name.lower()
        for key, srv in self.bus.servers.items():
            if key.lower() == name_lower:
                return srv
            if hasattr(srv, 'display_name') and srv.display_name.lower() == name_lower:
                return srv
        return None

    def resolve_node(self, name: str) -> Any | None:
        """Resolve a NODE name to the node object this caller's view carries, or ``None``.

        The sibling of :meth:`resolve_server`, and the ONE lookup a node action uses, so the action
        is transport-agnostic exactly as the server ones are: the console hands in its own per-request
        ``_Cluster`` (which carries ``nodes`` from the SCOPED source), Discord/MCP hand in the real
        ``ServiceBus`` (which carries none, so the process's own ``node.all_nodes`` is read).

        ``None`` covers both "no such name" and "the cluster knows it and cannot reach it": the two
        are the same answer to an action, and :func:`NodeResolution.message` words it once. Case is
        folded like every other name in this seam.
        """
        _present, node = _find_node(_node_entries(self.bus), name)
        if node is None:
            # a container that carries neither shape falls back to the process's own registry, so a
            # transport whose bus is the bare ServiceBus still resolves nodes
            _present, node = _find_node(_node_entries(self.node), name)
        return node

    async def resolve_scoped_node(self, name: str) -> NodeResolution:
        """Resolve *name* to a NODE and apply THIS caller's view (design §2.4).

        The deliberate twin of :meth:`resolve_scoped_server`, with the distinction kept for the same
        reason (``ARCHITECT-PERMISSIONS-ACTIONS.md`` §10.5: never a 404 that leaks the existence of a
        name):

        * a name that resolves to a REACHABLE node is :data:`NodeResolution.FOUND`;
        * a name the caller's view contains but no object answers to — the cluster knows it and its
          heartbeat verdict is "offline" — is :data:`NodeResolution.NOT_FOUND`, the honest answer,
          for EVERY caller: the node page already renders that verdict, so it discloses nothing new;
        * everything else is :data:`NodeResolution.NOT_PERMITTED` when the caller's view is
          restricted, and :data:`NodeResolution.NOT_FOUND` when it is unrestricted. One answer for a
          name that exists and one that does not, so a hoster cannot enumerate the cluster by probing.

        Fail closed on the scope exactly as the server twin does: the presence test IS the scope test
        here, because the console's ``_Cluster`` is built from the request's SCOPED source — a node
        outside the caller's view is not in the mapping at all. There is no second scope check to
        drift from it.
        """
        entries = _node_entries(self.bus)
        present, node = _find_node(entries, name)
        if node is not None:
            return NodeResolution(NodeResolution.FOUND, name=str(name), node=node)
        if present or self._scope_unrestricted():
            return NodeResolution(NodeResolution.NOT_FOUND, name=str(name))
        return NodeResolution(NodeResolution.NOT_PERMITTED, name=str(name))

    async def resolve_scoped_server(self, name: str) -> ServerResolution:
        """Resolve *name* to a Server AND apply THIS caller's scope (design §2.4).

        Sibling of :meth:`resolve_server`, not a replacement: the plain lookup stays the
        transport-agnostic name resolver every action already uses, and this one is what a caller
        that carries an identity asks instead.

        THE RULE, and both halves of it are deliberate:

        * a name that resolves AND is in the caller's scope is :data:`ServerResolution.FOUND`;
        * for a caller whose scope is UNSCOPED (``Admin``, break-glass, a local account that
          declares none — it sees every server) a name that does not resolve is
          :data:`ServerResolution.NOT_FOUND`, the honest answer;
        * for EVERY OTHER caller — one whose view is restricted, or unable to resolve, or that
          states no scope at all — anything it cannot see is
          :data:`ServerResolution.NOT_PERMITTED`, **whether or not the server exists**. Answering
          "not found" for the missing name and 403 for the real one would let a hoster enumerate
          the fleet by probing names (``ARCHITECT-PERMISSIONS-ACTIONS.md`` §10.5: never a 404 that
          leaks the existence of a name), so the two cases are deliberately the SAME answer.

        Fail closed at every step: a scope that raises, one that cannot be resolved, and no scope
        at all each deny. The target is re-resolved HERE, server-side, on every request — the
        confirm step and the page are warnings, never locks.
        """
        server = self.resolve_server(name)
        if server is not None and self._scope_allows(server):
            return ServerResolution(ServerResolution.FOUND, name=str(name), server=server)
        if self._scope_unrestricted():
            return ServerResolution(ServerResolution.NOT_FOUND, name=str(name))
        return ServerResolution(ServerResolution.NOT_PERMITTED, name=str(name))

    def _scope_allows(self, server) -> bool:
        """Whether this caller's scope holds *server*. A missing/failing scope denies."""
        if self._scope is None:
            return False
        try:
            return bool(self._scope.allows(_managed_by(server)))
        except Exception:
            log.exception("Action context: the scope could not be applied to '%s', refusing.",
                          getattr(server, "name", "?"))
            return False

    def _scope_unrestricted(self) -> bool:
        """Whether this caller sees every server (the scope's own ``unrestricted`` flag)."""
        return self._scope is not None and bool(getattr(self._scope, "unrestricted", False))

    @property
    def servers(self) -> dict:
        return self.bus.servers


def _role_names(member) -> set[str]:
    """The role NAMES a Discord member holds, read tolerantly (no API call, no import)."""
    names: set[str] = set()
    for role in getattr(member, "roles", None) or ():
        name = getattr(role, "name", None)
        if isinstance(name, str) and name:
            names.add(name)
    return names

