"""Which saved config file applies how, and the ONE door that applies it.

A save writes a file. Whether the RUNNING bot picks it up is decided by WHICH file was written, so the
mapping lives here and is derived from the resolved path — never from anything a request can name.
There are exactly three outcomes:

* ``config/plugins/<plugin>.yaml`` — reload THAT plugin in place. Nothing restarts.
* ``main.yaml``, ``servers.yaml``, ``nodes.yaml``, ``config/services/<service>.yaml`` — the BOT restarts
  on one node, because those values are read before the code that would re-read them exists. A change
  that names no node (``main.yaml``'s global keys, an un-scoped service block, adding or removing a
  plugin through ``opt_plugins``) restarts the MASTER node; ``nodes.yaml`` restarts the node whose block
  the page edited.
* everything else — a preset, a mission list, any file read when a mission is prepared — is applied by
  the next launch. It gets NO control and one sentence saying so.

Traps
-----
* The plugin NAME is never posted. It is the resolved path's stem, read at OFFER time and carried in
  the session's own offer record; a crafted form cannot reach another module's loader.
* The NODE is never posted either. It is read at offer time from the page that saved (the node page
  knows its node) and re-read from the offer record when the door runs, so a posted node name cannot
  retarget a restart.
* The reload/restart runs in the HTTP response's BACKGROUND: ending the process that is serving the
  answer would cut it off mid-flight.
* An offer is bound to the file's revision. A save that changed nothing offers no button, and an offer
  the file has outrun is refused rather than applied blind.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.background import BackgroundTask

from core.action_results import ActionResult
from core.actions import HEADLESS_BOT_MODULE, action_available, audit_action, call_action
from core.utils import configwrite

from . import i18n, permissions, session
from .pages import actions as actions_page

log = logging.getLogger(__name__)

__all__ = [
    "TARGET_PLUGIN", "TARGET_RESTART", "TARGET_NONE", "ApplyTarget",
    "APPLY_PATH", "APPLY_ASK_PATH", "APPLY_FIELD", "APPLY_KIND_FIELD", "APPLY_NAME_FIELD",
    "APPLY_ACTION_FIELD", "APPLY_ACTION_APPLY", "APPLY_ACTION_DECLINE", "APPLY_CONFIRM_TEMPLATE",
    "OFFER_KEY", "OFFER_MAX_PENDING", "APPLY_LABEL", "APPLY_TITLE", "CONFIRM_TITLE",
    "OWNER", "capabilities", "add_routes", "register",
    "resolve_target", "master_name", "node_label", "offer", "stored_offer", "take_offer",
    "target_of", "describe", "owed", "pending", "confirmation", "preflight", "execute",
    "audit_available", "in_flight", "reset_in_flight",
]

#: the two routes of the door. ``APPLY_ASK_PATH`` renders the confirmation; ``APPLY_PATH`` runs it.
#: Both are POST-only: a GET applies nothing, so there is no GET.
APPLY_PATH = "/settings/apply"
APPLY_ASK_PATH = APPLY_PATH + "/ask"

#: the three shapes an apply target can take.
TARGET_PLUGIN = "plugin"
TARGET_RESTART = "restart"
TARGET_NONE = "none"

#: the config sub-directory whose ``<stem>.yaml`` files are plugin configurations.
PLUGIN_DIR = "plugins"
#: the config sub-directory whose files are bot/service configuration (read at boot).
SERVICE_DIR = "services"
#: the bot-wide files: every one of them is read before the plugin layer exists.
BOOT_FILES = frozenset({"main.yaml", "servers.yaml", "nodes.yaml"})
#: the bot-wide file whose change names a NODE (every other one restarts the master).
NODE_FILE = "nodes.yaml"

#: the confirm form's own field names. The token is the console's ONE confirm-token contract
#: (``pages.actions.CONFIRM_FIELD``); the two identity fields re-read the OFFER, and the action field
#: tells the two press-buttons apart without a second form.
APPLY_FIELD = actions_page.CONFIRM_FIELD
APPLY_KIND_FIELD = "_apply_kind"
APPLY_NAME_FIELD = "_apply_name"
APPLY_ACTION_FIELD = "apply_action"
APPLY_ACTION_APPLY = "apply"
APPLY_ACTION_DECLINE = "decline"

APPLY_CONFIRM_TEMPLATE = "apply_confirm.html"

#: where the pending offers live in the session: ``{<kind>|<name>: {...}}``. Bounded, because the
#: store rides in the SIGNED COOKIE (see ``pages.actions.NOTICE_MAX_CHARS``) — a dropped offer can
#: only cause a refusal, never an application nobody asked for.
OFFER_KEY = "_apply_offers"
OFFER_MAX_PENDING = 4

#: the restarts this process has already started. A second press while one is in flight is refused
#: rather than queued: the process is about to end, so a queue would never drain.
_IN_FLIGHT: set[str] = set()
_IN_FLIGHT_LOCK = threading.Lock()


# --------------------------------------------------------------------------------- the wording
# Every sentence is an ``i18n._``-marked literal, composed per request by :func:`_say`; the template
# renders the finished strings. No name, path or node in these is ever a msgid.

APPLY_LABEL = i18n._("Apply now")
APPLY_TITLE = i18n._("Apply the change")
CONFIRM_TITLE = i18n._("Apply the change now")
DECLINE_LABEL = i18n._("Not now")
RELOAD_LINK = i18n._("Reload this page")

RELOAD_HEADING = i18n._("Reload the %(plugin)s plugin now?")
RELOAD_DETAIL = i18n._(
    "The plugin is unloaded and loaded again, and it reads its configuration file as it starts. "
    "Nothing restarts and no DCS server goes down.")
RELOAD_GO = i18n._("Reload the %(plugin)s plugin")

RESTART_HEADING = i18n._("Restart the bot on node %(node)s now?")
RESTART_WARNING = i18n._(
    "Every DCS server on %(node)s goes down with it. Nothing starts them again by itself: the "
    "launcher on that machine brings the bot back, and the servers with it.")
RESTART_DETAIL = i18n._(
    "The bot process ends and the launcher starts it again, so this change is read at boot.")
RESTART_GO = i18n._("Restart the bot on node %(node)s")
MASTER_NODE = i18n._("the master node")

#: the sentence a DECLINED apply leaves, so nobody believes a written change is live.
OWED_RELOAD = i18n._("The change is in the file and takes effect after the %(plugin)s plugin is "
                     "reloaded.")
OWED_RESTART = i18n._("The change is in the file and takes effect after a bot restart on %(node)s.")
OWED_LAUNCH = i18n._("The change is in the file and takes effect when the next mission is prepared "
                     "— nothing is reloaded for it.")

#: the state the landing page carries while the response's background work runs.
PENDING_RELOAD = i18n._("Reloading the %(plugin)s plugin — this page reloads once now.")
PENDING_RESTART = i18n._(
    "Restarting the bot on %(node)s — its DCS servers go down with it. This page reloads once now; "
    "if the console does not answer yet, reload it again in a moment.")

#: the refusals. Each one leaves the file as it was written and nothing restarted.
REFUSED_STALE = i18n._("Nothing was applied: this confirmation is no longer valid. Save the change "
                       "again and apply the fresh one.")
REFUSED_UNREACHABLE = i18n._("Nothing was reloaded or restarted: the bot process cannot be reached "
                             "from here.")
#: the REFUSED_UNREACHABLE detail: WHICH piece is missing, so the operator is not sent looking at a
#: bot that is running perfectly well. The reason itself is a technical phrase, logged verbatim.
REFUSED_UNREACHABLE_WHY = i18n._("Nothing was reloaded: the bot cannot be reached from here — "
                                 "%(reason)s.")
WHY_NO_SERVICE = "this console process has no bot service to ask"
WHY_NO_BOT = "its bot service holds no bot yet"
WHY_HEADLESS = "this installation runs without Discord (no_discord)"
WHY_NO_RELOAD = "this bot object has no reload"
#: the two outcomes of an INLINE plugin reload — the page the operator lands on states which one.
APPLIED_RELOAD = i18n._("The %(plugin)s plugin was reloaded, so the change is live.")
FAILED_RELOAD = i18n._(
    "The %(plugin)s plugin could NOT be reloaded — the file holds the change, the running bot does "
    "not. Restart the bot to apply it.")
REFUSED_NO_RESTART_ACTION = i18n._(
    "Nothing was restarted: this console cannot restart a node — the node operation it needs is not "
    "available in this process.")
REFUSED_PLUGIN_NOT_LOADED = i18n._(
    "Nothing was reloaded: the %(plugin)s plugin is not loaded in this process. Restart the bot to "
    "apply the change.")
REFUSED_UNKNOWN_NODE = i18n._("Nothing was restarted: this installation knows no node named "
                              "%(node)s.")
REFUSED_IN_FLIGHT = i18n._("Nothing was restarted: a restart of %(node)s is already in flight.")
REFUSED_LAUNCH_ONLY = i18n._("Nothing was applied: this file is read when a mission is prepared, so "
                             "it applies at the next launch.")
REFUSED_UNCLASSIFIED = i18n._("Nothing was applied: no apply action is defined for this file.")

#: the audit hole a ``no_discord`` install has (the headless bot's ``audit`` is a no-op), disclosed
#: rather than silent.
AUDIT_NOT_RECORDED_NOTE = i18n._("This action is not recorded in the audit log on this install.")


def _say(request, msgid: str, **params) -> str:
    """*msgid* in the request's language, its names interpolated after translation."""
    return i18n.translate(request, msgid, **params)


# ------------------------------------------------------------------------------- the mapping

@dataclass(frozen=True)
class ApplyTarget:
    """What applies ONE saved file: the kind, the plugin or the node it names, and — for a file that
    nothing applies — the sentence that says why.

    ``node`` is a NAME; the empty string means the MASTER node (this process's own), which is why it
    is never a valid node name on its own.
    """

    kind: str
    plugin: str = ""
    node: str = ""
    reason: str = ""

    @property
    def applies(self) -> bool:
        """Whether this file has an apply action at all."""
        return self.kind != TARGET_NONE

    @property
    def restarts(self) -> bool:
        """Whether applying this file ends a bot process."""
        return self.kind == TARGET_RESTART


def resolve_target(path, config_dir, *, node: str = "") -> ApplyTarget:
    """The apply target of ONE saved file, derived from its path under *config_dir*.

    *node* is the node the SAVING PAGE knew (its own page, or the block it edited); it is used only
    for the files whose change belongs to a node (``nodes.yaml``, ``config/services/*.yaml``). Every
    other decision is the path's: a plugin configuration is a plugin whose name is the file's stem, and
    a file outside the table is applied by its next launch, never by this door.
    """
    try:
        relative = Path(path).resolve().relative_to(Path(config_dir).resolve())
    except (ValueError, OSError):
        return ApplyTarget(TARGET_NONE, reason=REFUSED_UNCLASSIFIED)
    parts = relative.parts
    suffix = relative.suffix.lower()
    if len(parts) == 2 and parts[0] == PLUGIN_DIR and suffix in (".yaml", ".yml"):
        return ApplyTarget(TARGET_PLUGIN, plugin=relative.stem)
    if len(parts) == 2 and parts[0] == SERVICE_DIR and suffix in (".yaml", ".yml"):
        return ApplyTarget(TARGET_RESTART, node=node)
    if len(parts) == 1 and relative.name in BOOT_FILES:
        return ApplyTarget(TARGET_RESTART, node=node if relative.name == NODE_FILE else "")
    return ApplyTarget(TARGET_NONE, reason=REFUSED_LAUNCH_ONLY)


def master_name() -> str:
    """The name of the node this console runs on — the target of a change that names no node.

    Read from the service registry's own bus, tolerantly: a shell installed without a node (a test
    harness, a standalone render) answers an empty string, and the caller then says so in words
    (:func:`node_label`) instead of inventing a name.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        bus = ServiceRegistry.get(ServiceBus)
        return str(getattr(getattr(bus, "node", None), "name", "") or "")
    except Exception:                      # no registry, no bus, no node
        return ""


def node_label(request, node: str) -> str:
    """*node*, or the words for "the master node" when the target named none."""
    return node or _say(request, MASTER_NODE)


# --------------------------------------------------------------------------------- the offer
# ONE record per (kind, name) in the session. It is written where the save happens, read where the
# "Apply now" control is pressed, and re-read where the confirmed apply runs — so the plugin and the
# node never travel through the browser.

def _store(request) -> dict:
    """The pending offers of this session, as a plain dict. Never raises, never authored."""
    try:
        stored = request.session.get(OFFER_KEY)
    except (AssertionError, KeyError):     # no SessionMiddleware / no session on this request
        return {}
    return dict(stored) if isinstance(stored, dict) else {}


def _key(kind: str, name: str) -> str:
    """The store key of ONE file's offer: its kind and its route name, never a path."""
    return f"{kind}|{name}"


def _save_store(request, store: dict) -> None:
    try:
        request.session[OFFER_KEY] = store
    except (AssertionError, KeyError):     # pragma: no cover - guarded by the session's presence
        pass


def offer(request, *, path, config_dir, kind: str, name: str, node: str = "",
          back: str = "") -> dict | None:
    """Record what would apply ONE just-saved file and return the notice's apply block, or ``None``.

    ``None`` is the honest answer for a file this table cannot classify — and for a save that changed
    nothing, which the caller states by not calling this at all: no control, rather than a button that
    could only explain itself.

    The block carries what a SCRIPT-OFF form needs — the file's identity, the session's CSRF token —
    and the token itself, so the control is one plain POST.
    """
    target = resolve_target(path, config_dir, node=node)
    if not target.applies:
        return None
    token = secrets.token_urlsafe(24)
    store = _store(request)
    store[_key(kind, name)] = {"token": token, "revision": configwrite.config_revision(path),
                               "back": back, "file_kind": kind, "file_name": name,
                               "kind": target.kind, "plugin": target.plugin, "node": target.node}
    if len(store) > OFFER_MAX_PENDING:
        for stale in list(store)[:-OFFER_MAX_PENDING]:
            store.pop(stale, None)
    _save_store(request, store)
    # ``action``/``token`` are the ONE-CLICK door the page's question uses: the same apply route at
    # the same one-shot token the confirmation page would post, so "yes, apply it" is one press.
    # ``ask_action`` stays for the detail page, which reads the offer back from the session.
    return {"kind": kind, "name": name,
            "csrf_field": session.CSRF_FIELD, "csrf_token": session.get_csrf_token(request),
            "kind_field": APPLY_KIND_FIELD, "name_field": APPLY_NAME_FIELD,
            "token_field": APPLY_FIELD, "token": token,
            "action_field": APPLY_ACTION_FIELD, "action_value": APPLY_ACTION_APPLY,
            "action": APPLY_PATH, "ask_action": APPLY_ASK_PATH, "label": APPLY_LABEL}


def stored_offer(request, kind: str, name: str) -> dict | None:
    """The offer this session holds for ONE file, or ``None`` when there is none."""
    record = _store(request).get(_key(kind, name))
    if not isinstance(record, dict) or not record.get("token"):
        return None
    return record


def take_offer(request, kind: str, name: str) -> dict | None:
    """The offer for ONE file, REMOVED from the session — a one-shot, like every other token here."""
    store = _store(request)
    record = store.pop(_key(kind, name), None)
    _save_store(request, store)
    return record if isinstance(record, dict) else None


def target_of(record: dict) -> ApplyTarget:
    """The :class:`ApplyTarget` an offer record carries (its own server-side copy)."""
    return ApplyTarget(kind=str(record.get("kind") or TARGET_NONE),
                       plugin=str(record.get("plugin") or ""),
                       node=str(record.get("node") or ""))


def revision_of(record: dict) -> str:
    """The file revision the offer was recorded against."""
    return str(record.get("revision") or "")


def unchanged(record: dict, path) -> bool:
    """Whether the file is still the revision this offer was recorded against."""
    return revision_of(record) == configwrite.config_revision(path)


def describe(request, target: ApplyTarget) -> dict:
    """The plain-words copy for ONE target: heading, warning, detail, the go label, the target name.

    A plugin says what a reload does and that nothing goes down; a restart names the node and says
    its servers go down with it. The words never say "apply" alone.
    """
    if target.kind == TARGET_PLUGIN:
        values = {"plugin": target.plugin}
        return {"heading": _say(request, RELOAD_HEADING, **values), "warning": "",
                "detail": _say(request, RELOAD_DETAIL), "go": _say(request, RELOAD_GO, **values),
                "target": target.plugin}
    name = node_label(request, target.node or master_name())
    values = {"node": name}
    return {"heading": _say(request, RESTART_HEADING, **values),
            "warning": _say(request, RESTART_WARNING, **values),
            "detail": _say(request, RESTART_DETAIL), "go": _say(request, RESTART_GO, **values),
            "target": name}


def owed(request, target: ApplyTarget) -> str:
    """What is still owed after a DECLINED apply, in the target's own words."""
    if target.kind == TARGET_PLUGIN:
        return _say(request, OWED_RELOAD, plugin=target.plugin)
    if target.kind == TARGET_RESTART:
        return _say(request, OWED_RESTART, node=node_label(request, target.node or master_name()))
    return _say(request, OWED_LAUNCH)


def pending(request, target: ApplyTarget, *, audited: bool = True) -> str:
    """The state the landing page carries while the background work runs (and the audit disclosure)."""
    if target.kind == TARGET_PLUGIN:
        message = _say(request, PENDING_RELOAD, plugin=target.plugin)
    else:
        message = _say(request, PENDING_RESTART,
                       node=node_label(request, target.node or master_name()))
    if not audited:
        message = f"{message} {_say(request, AUDIT_NOT_RECORDED_NOTE)}"
    return message


# ------------------------------------------------------------------------------- the dialog

def confirmation(request, record: dict, *, back: str) -> dict:
    """Everything the confirm page reads: the consequence in plain words, the token, and the target."""
    from .pages import dashboard as dashboard_page

    target = target_of(record)
    copy = describe(request, target)
    return {
        "page_title": CONFIRM_TITLE,
        "crumb": dashboard_page.crumb(_say(request, APPLY_TITLE), target=copy["target"]),
        "back": back,
        "user": _user(request),
        "nav_groups": _nav_groups(request, back),
        "dialog": {
            **copy,
            "path": APPLY_PATH,
            "csrf_field": session.CSRF_FIELD,
            "csrf_token": session.get_csrf_token(request),
            "confirm_field": APPLY_FIELD,
            "confirm_token": str(record.get("token") or ""),
            "hidden": [(APPLY_KIND_FIELD, str(record.get("file_kind") or "")),
                       (APPLY_NAME_FIELD, str(record.get("file_name") or ""))],
            "decline": _say(request, DECLINE_LABEL),
            "note": "" if audit_available() else _say(request, AUDIT_NOT_RECORDED_NOTE),
        },
    }


def _user(request) -> Any:
    from .pages import dashboard as dashboard_page

    return dashboard_page.identity_summary(request)


def _nav_groups(request, current: str):
    from .pages import dashboard as dashboard_page

    registrar = getattr(getattr(request.app, "state", None), "webui_registrar", None)
    return dashboard_page.nav_groups(registrar, permissions.role_names_for(request), current=current,
                                     manager=permissions.manages_console(request))


# -------------------------------------------------------------------------------- the executor
# Runs in the response's background, i.e. AFTER the answer is on the wire.

def reset_in_flight() -> None:
    """Forget every restart this process started — test state, and the only safe reset."""
    with _IN_FLIGHT_LOCK:
        _IN_FLIGHT.clear()


def in_flight(node: str) -> bool:
    """Whether a restart of *node* has already been started by this process."""
    with _IN_FLIGHT_LOCK:
        return node in _IN_FLIGHT


def mark_in_flight(node: str) -> bool:
    """Claim *node* for one restart; ``False`` when one is already claimed."""
    with _IN_FLIGHT_LOCK:
        if node in _IN_FLIGHT:
            return False
        _IN_FLIGHT.add(node)
        return True


def _join_in_flight(node: str) -> None:
    with _IN_FLIGHT_LOCK:
        _IN_FLIGHT.discard(node)


def _bot() -> Any:
    """The running bot through the service registry, or ``None``. Never raises."""
    return _bot_diagnosis()[0]


def _bot_diagnosis() -> tuple[Any, str]:
    """``(bot, "")`` when a plugin can be reloaded in this process, else ``(None, why)``.

    ONE sentence cannot cover four different situations, and the operator can act on only one of
    them — so the reason is named: no service, no bot object in it, a bot that runs without Discord
    (a headless install cannot reload a plugin in process), or a bot with no reload at all. Each
    reason is a fixed msgid, never a repr of anything.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.bot.service import BotService
    except Exception:                          # no registry module at all
        return None, WHY_NO_SERVICE
    try:
        service = ServiceRegistry.get(BotService)
    except Exception:                          # a registry that cannot answer
        return None, WHY_NO_SERVICE
    if service is None:
        return None, WHY_NO_SERVICE
    bot = getattr(service, "bot", None)
    if bot is None:
        return None, WHY_NO_BOT
    if not _reload_capable(bot):
        if (getattr(type(bot), "__module__", "") or "").startswith(HEADLESS_BOT_MODULE):
            return None, WHY_HEADLESS
        return None, WHY_NO_RELOAD
    return bot, ""


def audit_available(bot: Any = None) -> bool:
    """Whether this process can POSITIVELY record an audit entry (a headless bot cannot)."""
    bot = bot if bot is not None else _bot()
    if bot is None:
        return False
    return not (getattr(type(bot), "__module__", "") or "").startswith(HEADLESS_BOT_MODULE)


def _loaded_extensions(bot) -> dict:
    """What the bot has LOADED, in the names this door asks for.

    The extension map is the first answer, but a bot that carries its plugins as COGS and no
    extension map (a headless bot, an install whose loader differs) would then look empty — and a
    refusal that says "the plugin is not loaded" about a plugin whose own page just rendered is a
    lie. So the cogs are the second answer, under the same naming.
    """
    loaded: dict = {}
    extensions = getattr(bot, "extensions", None)
    if isinstance(extensions, dict):
        loaded.update(extensions)
    cogs = getattr(bot, "cogs", None)
    if isinstance(cogs, dict):
        # BOTH, not either: a plugin loaded with ``add_cog`` is present without an extension entry,
        # and an extension map that holds OTHER plugins would otherwise answer "not loaded" about
        # one that is running right now.
        for name, cog in cogs.items():
            loaded.setdefault(_extension(str(name)), cog)
    return loaded


def _plugin_present(bot, plugin: str) -> bool:
    """Whether *plugin* is loaded in this process — by its extension name OR by its cog.

    A cog IS the loaded plugin, so the question is asked of the cogs first: matching an extension's
    NAME only answers "is it spelled the way I expect", and a bot that registers its plugins another
    way then looks empty about a plugin that is running right now.
    """
    cogs = getattr(bot, "cogs", None)
    if isinstance(cogs, dict) and any(str(name).lower() == plugin.lower() for name in cogs):
        return True
    # THE PAGE'S OWN LOOKUP, as a second witness: the tab resolves the plugin through the registry
    # and draws its plan, so a door that asks anything else can disagree with the screen in front of
    # the operator — and a refusal about a plugin whose plan is on screen is the worst answer to give.
    try:
        from .pages import server_detail

        found = server_detail._scheduler()
    except Exception:  # noqa: BLE001 - a page that cannot answer adds no witness
        found = None
    if found is not None and type(found).__name__.lower() == plugin.lower():
        return True
    return _extension(plugin) in _loaded_extensions(bot)


def _reload_capable(bot) -> bool:
    """Whether this bot can put ONE plugin through an unload and a load again."""
    if callable(getattr(bot, "reload_plugin", None)):
        return True
    return (callable(getattr(bot, "unload_plugin", None))
            and callable(getattr(bot, "load_plugin", None)))


def _extension(plugin: str) -> str:
    return f"plugins.{plugin.lower()}.commands"


def preflight(request, target: ApplyTarget, *, ctx: Any = None) -> str:
    """The refusal sentence for *target* BEFORE anything is scheduled, or ``""`` when it may run.

    Checked here rather than only in the executor because a refusal an operator never reads is a
    refusal nobody can act on: the file stays as written and nothing is scheduled.
    """
    if target.kind == TARGET_PLUGIN:
        bot, why = _bot_diagnosis()
        if bot is None:
            # THE SENTENCE NAMES THE CAUSE, and the log carries the same words: a refusal that says
            # only "cannot be reached" on an install whose bot is running is a dead end.
            log.warning("Apply: cannot reload the %s plugin - %s", target.plugin, why)
            return _say(request, REFUSED_UNREACHABLE_WHY, reason=why)
        loaded = _loaded_extensions(bot)
        if not _plugin_present(bot, target.plugin):
            # what the bot actually holds, so an absent entry is diagnosable from the log alone
            log.warning("Apply: %s is not loaded in this process. Bot holds %d extension(s) "
                        "[%s] and %d cog(s) [%s].", _extension(target.plugin), len(loaded),
                        ", ".join(sorted(str(name) for name in loaded)) or "-",
                        len(getattr(bot, "cogs", {}) or {}),
                        ", ".join(sorted(str(name) for name in (getattr(bot, "cogs", {}) or {})))
                        or "-")
            return _say(request, REFUSED_PLUGIN_NOT_LOADED, plugin=target.plugin)
        return ""
    if target.kind == TARGET_RESTART:
        node = target.node or master_name()
        if not node or (ctx is not None and ctx.resolve_node(node) is None):
            return _say(request, REFUSED_UNKNOWN_NODE, node=node_label(request, node))
        if not action_available("restart_node"):
            # THE OTHER SENTENCE THAT USED TO SAY "cannot be reached": on an install whose bot runs
            # perfectly well this means the node operation itself is not registered here, which is
            # a different fault and a different fix.
            log.warning("Apply: cannot restart node %s - the restart_node action is not registered "
                        "in this process (is the admin plugin loaded?)", node)
            return _say(request, REFUSED_NO_RESTART_ACTION)
        if in_flight(node):
            return _say(request, REFUSED_IN_FLIGHT, node=node)
        return ""
    return _say(request, target.reason or REFUSED_UNCLASSIFIED)


async def execute(target: ApplyTarget, ctx: Any) -> bool | None:
    """Apply ONE target; the plugin reload's outcome, or ``None`` for a restart.

    A RESTART runs in the response's background: it ends the process that is answering, so the answer
    has to be sent first. A PLUGIN RELOAD does not — it rebuilds one cog in this very process in a few
    milliseconds, so the apply route awaits it and the page it lands on already reads the new
    configuration. Sending the operator to a page that renders the OLD plan, and hoping they reload
    once the background work happens to be done, is a page that lies about its own state.
    """
    node = target.node or master_name() if target.kind == TARGET_RESTART else ""
    try:
        if target.kind == TARGET_PLUGIN:
            ok, _label = await _reload_plugin(target.plugin, ctx)
            return ok
        if target.kind == TARGET_RESTART:
            await _restart_node(node, ctx)
        return None
    except asyncio.CancelledError:         # pragma: no cover - the loop is ending anyway
        raise
    except Exception:
        log.exception("Apply: applying the change for %s failed.", target)
        return False if target.kind == TARGET_PLUGIN else None
    finally:
        if node:
            _join_in_flight(node)


async def _reload_plugin(plugin: str, ctx: Any) -> tuple[bool, str]:
    """Unload and load ONE plugin, then re-read the loaded set rather than trusting the call.

    A bot that offers its own ``reload_plugin`` is asked for it; one that only offers the two halves
    gets them called in order, which is exactly what the door offered the operator.
    """
    bot = _bot()
    if bot is None or not _reload_capable(bot):
        label = f"FAILED to reload the {plugin} plugin - this process has no bot to reload it"
        log.error("Apply: the %s plugin was NOT reloaded - this process has no bot to reload it.",
                  plugin)
        await audit_action(ctx, ActionResult(success=False, message=label), message=label)
        return False, label
    reload = getattr(bot, "reload_plugin", None)
    if callable(reload):
        reloaded = bool(await reload(plugin))
    else:
        reloaded = bool(await bot.unload_plugin(plugin)) and bool(await bot.load_plugin(plugin))
    present = _plugin_present(bot, plugin)
    ok = reloaded and present
    label = (f"reloaded the {plugin} plugin" if ok
             else f"FAILED to reload the {plugin} plugin")
    if not ok:
        log.error("Apply: %s.", label)
    await audit_action(ctx, ActionResult(success=ok, message=label), message=label)
    return ok, label


async def _restart_node(node: str, ctx: Any) -> None:
    """Restart the bot on ONE node through the node operation (which resolves and audits it)."""
    result = await call_action("restart_node", ctx, node_name=node)
    if not getattr(result, "success", False):
        log.error("Apply: node %s was NOT restarted: %s", node, getattr(result, "message", ""))


# --------------------------------------------------------------------------------- the door
# THE ONE CONTROL a save leaves. ``ASK`` renders the confirmation and ``APPLY`` runs it; both are
# POST-only (a GET applies nothing, so there is no GET), both take the session's CSRF token, and both
# are registered with the Settings WRITE capability — reloading a plugin or restarting a bot is not a
# lesser act than editing the file. THE TARGET IS NEVER POSTED: the offer the save recorded carries
# the kind, the plugin and the node (:func:`offer`), so a crafted form can neither reload another
# module's loader nor name another machine.

#: the registrar owner of the door's two paths.
OWNER = "apply"


def capabilities() -> dict[str, str]:
    """The capability declaration for the two paths this module registers."""
    from .pages import settings as settings_page

    capability = settings_page.SETTINGS_WRITE_CAPABILITY
    return {APPLY_PATH: capability, APPLY_ASK_PATH: capability}


def _settings():
    """The Settings page module, imported LAZILY: it imports this one for the offer it renders."""
    from .pages import settings as settings_page

    return settings_page


def _back(record) -> str:
    """Where a refusal returns to: the page the save came from, from the SESSION's own offer."""
    settings_page = _settings()
    return settings_page.readmodels.text((record or {}).get("back")) or settings_page.SETTINGS_PATH


def _record_of(request, form):
    """``(kind, name, record)`` for the file this POST names, the record read from the session."""
    settings_page = _settings()
    kind = settings_page.readmodels.text(form.get(APPLY_KIND_FIELD))
    name = settings_page.readmodels.text(form.get(APPLY_NAME_FIELD))
    return kind, name, stored_offer(request, kind, name)


def _file_path(request, kind: str, name: str):
    """The file an offer names, resolved from the console's OWN listing — never a posted path."""
    settings_page = _settings()
    if kind == settings_page.KIND_NODES:
        return (settings_page.config_dir(request) / settings_page.NODES_YAML_NAME
                if name == settings_page.NODES_YAML_NAME else None)
    try:
        return settings_page.resolve_file(request, kind, name).path
    except HTTPException:
        return None


def _refused(request, message: str, back: str) -> RedirectResponse:
    """Leave one refusal sentence and go back — the file untouched, nothing scheduled."""
    _settings()._notice(request, message, ok=False, refusal=True)
    return RedirectResponse(back, status_code=303)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the apply door's two POST routes — the confirmation and the apply itself."""

    @router.post(APPLY_ASK_PATH, dependencies=[Depends(session.csrf_protect)])
    async def apply_ask(request: Request):
        """Render the confirmation of ONE offered change — the page whose button applies it."""
        form = await request.form()
        kind, name, record = _record_of(request, form)
        back = _back(record)
        if record is None:
            return _refused(request, i18n.translate(request, REFUSED_STALE), back)
        path = _file_path(request, kind, name)
        if path is None or not unchanged(record, path):
            # the file moved on since the save: the offer is spent and the operator is told to redo it
            take_offer(request, kind, name)
            return _refused(request, i18n.translate(request, REFUSED_STALE), back)
        page = confirmation(request, record, back=back)
        # no-store: the page carries a one-shot token, so a cached copy would be a replay
        return HTMLResponse(i18n.render(request, APPLY_CONFIRM_TEMPLATE, **page),
                            headers={"Cache-Control": "no-store"})

    @router.post(APPLY_PATH,
                 dependencies=[Depends(session.csrf_protect_with(
                     _settings().SETTINGS_FORM_MAX_FIELDS))])
    async def apply_confirmed(request: Request):
        """Apply ONE offered change, or record that the operator declined it.

        The order is the operator's protection: the offer must still exist and still match the file,
        the confirm token must be the one its dialog minted, and the target must pass its pre-flight
        before anything is scheduled. A decline writes the "still owed" notice and starts nothing.

        The reload/restart runs in the RESPONSE's background: ending the process that is answering
        would cut the answer off mid-flight, and the page must land first.
        """
        settings_page = _settings()
        settings_page.require_file_capability(request, settings_page.KIND_MAIN, write=True)
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
        kind, name, record = _record_of(request, form)
        back = _back(record)
        if record is None:
            return _refused(request, i18n.translate(request, REFUSED_STALE), back)
        path = _file_path(request, kind, name)
        if path is None or not unchanged(record, path):
            take_offer(request, kind, name)
            return _refused(request, i18n.translate(request, REFUSED_STALE), back)
        target = target_of(record)
        if settings_page.readmodels.text(form.get(APPLY_ACTION_FIELD)) != APPLY_ACTION_APPLY:
            take_offer(request, kind, name)
            settings_page._notice(request, owed(request, target), ok=True, refusal=True)
            return RedirectResponse(back, status_code=303)
        posted = settings_page.readmodels.text(form.get(APPLY_FIELD))
        if not hmac.compare_digest(str(record.get("token") or ""), posted):
            return _refused(request, i18n.translate(request, REFUSED_STALE), back)
        identity = actions_page._identity(request)
        if identity is None:
            return _refused(request, i18n.translate(request, actions_page.REFUSAL_NO_IDENTITY), back)
        ctx = actions_page._context(request, identity)
        refusal = preflight(request, target, ctx=ctx)
        if refusal:
            take_offer(request, kind, name)
            return _refused(request, refusal, back)
        if target.restarts:
            node = target.node or master_name()
            if not mark_in_flight(node):
                take_offer(request, kind, name)
                return _refused(request,
                                i18n.translate(request, REFUSED_IN_FLIGHT, node=node), back)
        take_offer(request, kind, name)
        if not target.restarts:
            # A PLUGIN RELOAD IS AWAITED: it ends in this process and takes milliseconds, so the page
            # the operator lands on is rendered AFTER it and shows the configuration that is now live.
            ok = await execute(target, ctx)
            settings_page._notice(request,
                                  i18n.translate(request,
                                                 APPLIED_RELOAD if ok else FAILED_RELOAD,
                                                 plugin=target.plugin),
                                  ok=bool(ok), refusal=True)
            return RedirectResponse(back, status_code=303)
        settings_page._notice(request, pending(request, target, audited=audit_available()),
                              ok=True, refusal=True, pending=True, reload=back)
        response = RedirectResponse(back, status_code=303)
        response.background = BackgroundTask(execute, target, ctx)
        return response

    return router


def register(registrar) -> None:
    """Register the door with the registrar, under this module's own owner name and capability."""
    router = APIRouter()
    add_routes(router)
    registrar.register_pages(OWNER, router, capabilities=capabilities())
