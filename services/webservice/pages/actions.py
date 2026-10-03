from __future__ import annotations

import asyncio
import html
import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.action_results import ActionResult
from core.actions import (AUDIT_NOT_RECORDED, ActionContext, NodeResolution, ServerResolution,
                          action_available, audit_action, bound_text, call_action,
                          in_flight_targets, read_action)
from core.data.const import Status
# the engine's power-off RECORD, read by :func:`node_power_states` to gate the node row's power pair
# (the operation that took a node's servers down records what it did, so the way back can revert
# exactly that). It is the engine's own accessor — this module neither keeps nor
# copies the record, and it never writes one. ``ServerMaintenanceManager.in_service`` is the SAME one
# definition of "may this server's process be up" that the engine's power halves use, which is what
# makes the node route's PREDICTION follow the operation rather than a second rule.
from core.data.maintenance import ServerMaintenanceManager, power_record

from .. import permissions, readmodels, session
# THE CONFIG FIELD DECLARATION, the same one the page renders from, so the form and this submit path
# cannot disagree (``readmodels`` stays free of ``core``).
from ..readmodels import serverconfig as server_config
# THE ONE url encoder for a server's own page: this module's redirect back and the row's own link
# build the same URL from the same name.
from ..readmodels.servers import server_url
# THE CACHED UPGRADE SIGNAL: the node row's Upgrade control is gated on it, and only the
# poller in the webservice service ever calls the node. This module only READS the cache.
from .. import upgrade as upgrade_signal
from ..auth import Identity
from . import dashboard as dashboard_page

__all__ = [
    "OWNER", "WRITE_ROLES", "NODE_ROLES", "PAUSE_PATH", "UNPAUSE_PATH", "PAUSE_CAPABILITY",
    "UNPAUSE_CAPABILITY",
    "MESSAGE_PATH", "MESSAGE_CAPABILITY", "MESSAGE_FIELD_MAX", "MESSAGE_DEFAULT_MODE",
    "SERVER_MAINTENANCE_CAPABILITY", "SERVER_CLEAR_MAINTENANCE_CAPABILITY",
    "NOTICE_MAX_CHARS", "CONFIRM_SUFFIX", "CONFIRM_FIELD", "CONFIRM_TEMPLATE", "CONFIRM_MAX_PENDING",
    "ORIGIN_FIELD", "origin_path",
    "ROW_STRIP", "ROW_MENU",
    "NODE_RESTART_CAPABILITY", "NODE_SHUTDOWN_CAPABILITY", "NODE_UPGRADE_CAPABILITY",
    "NODE_OFFLINE_CAPABILITY", "NODE_ONLINE_CAPABILITY",
    "NODE_IN_SERVICE", "NODE_MAINTENANCE", "NODE_POWER_OFF", "NODE_POWER_ON",
    "NOTE_NO_CHECK",
    "WriteAction", "PlayerAction", "PlayerButton", "NodeAction", "NodeOption",
    "SERVER_ACTIONS", "PLAYER_ACTIONS", "NODE_ACTIONS",
    "declare", "capabilities", "register", "add_routes",
    "server_controls", "player_controls", "node_controls", "controls_for", "state_of", "node_state",
    "node_states", "node_power_states",
    "target_key", "target_is_busy",
    "AWAIT_CHANGE_SECONDS", "AWAIT_MAX_PENDING",
    "AWAIT_OBSERVABLE_STATUS", "AWAIT_OBSERVABLE_MAINTENANCE", "AWAIT_OBSERVABLE_CONFIG",
    "SERVER_CONFIG_ACTIONS", "SERVER_CONFIG_ACTION_KEY", "SERVER_CONFIG_PATH",
    "SERVER_CONFIG_CAPABILITY", "SERVER_CONFIG_FIELD", "SERVER_CONFIG_CLEAR_FIELD",
    "SERVER_CONFIG_REVERT_FIELD", "config_values_from_form",
    # the channels write: its own declaration, path, diff and pulse
    "SERVER_CHANNEL_ACTIONS", "SERVER_CHANNEL_ACTION_KEY", "SERVER_CHANNELS_PATH",
    "SERVER_CHANNELS_CAPABILITY",
    "channels_values_from_form", "channels_pulse", "channels_write_available",
    # the coalition face: its own declaration, path, read helper and diff
    "SERVER_COALITION_ACTIONS", "SERVER_COALITION_ACTION_KEY", "SERVER_COALITIONS_PATH",
    "SERVER_COALITIONS_CAPABILITY",
    "coalition_values", "coalition_values_from_form", "coalitions_write_available",
    "mission_list",
    "download_mission",
    # the mission-list writes: their declarations, paths, fields and read helper
    "MISSION_ACTIONS", "MISSIONS_ADD_CAPABILITY", "MISSIONS_DELETE_CAPABILITY",
    "MISSIONS_LOAD_CAPABILITY", "MISSIONS_UPLOAD_CAPABILITY",
    "MISSIONS_ADD_PATH", "MISSIONS_DELETE_PATH", "MISSIONS_LOAD_PATH", "MISSIONS_UPLOAD_PATH",
    "MISSIONS_REORDER_CAPABILITY", "MISSIONS_REORDER_PATH", "MISSIONS_BULK_DELETE_PATH",
    "MISSIONS_BULK_DELETE_STATES", "MISSION_DELETE_LOADING_REFUSAL",
    "MISSIONS_ACTIVATE_CAPABILITY", "MISSIONS_ACTIVATE_PATH", "MISSIONS_ACTIVATE_STATES",
    "MISSION_ACTIVE_LOADING_REFUSAL", "MISSIONS_ACTIVATE_SELECTION_REFUSAL",
    "MISSION_DIRECTION_FIELD", "MISSION_LIST_OFFLINE_STATES", "MISSION_LIST_OFFLINE_ONLY",
    "MISSION_FIELD", "MISSION_NAME_FIELD", "MISSION_PATH_FIELD", "MISSION_FILE_FIELD",
    "MISSION_AUTOSTART_FIELD", "MISSION_LOAD_FIELD", "MISSION_DISK_FIELD",
    "MISSIONS_UPLOAD_MAX_BYTES", "mission_index_required",
    "MISSION_DIALOG_KEYS", "MISSION_LOAD_STATES", "MISSION_LOAD_SETS_START_STATES",
    "dialog_options",
    "addable_missions", "mission_write_available", "missions_write_available",
    "mission_load_allowed", "mission_confirm_context",
    "REVERT_MAX_CHARS", "REVERT_TOO_LONG_SENTENCE",
    "awaiting_store", "reset_awaiting_changes", "remember_awaiting_change",
    "forget_awaiting_change", "awaiting_change", "awaiting_action",
    "NODE_MOVE_ACTIONS", "node_moved_names",
    "pop_notice", "NOTICE_KEY",
    "confirm_targets", "confirm_dialog", "node_confirm_context", "node_load",
]

log = logging.getLogger(__name__)

#: the registrar owner of the write surface. Its OWN owner name (the shell owns the pages): a path
#: claimed twice is refused at registration time, and this module's declaration is where a reader
#: looks for what its routes need.
OWNER = "actions"

WRITE_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")
NODE_ROLES: tuple[str, ...] = ("Admin",)

NODE_RESTART_CAPABILITY = "nodes.restart"
NODE_SHUTDOWN_CAPABILITY = "nodes.shutdown"
NODE_UPGRADE_CAPABILITY = "nodes.upgrade"
NODE_OFFLINE_CAPABILITY = "nodes.offline"
NODE_ONLINE_CAPABILITY = "nodes.online"
NODE_IN_SERVICE = "in-service"
NODE_MAINTENANCE = "maintenance"
NODE_POWER_OFF = "servers-up"
NODE_POWER_ON = "servers-down"
NOTE_NO_CHECK = upgrade_signal.NOTE_NO_CHECK

PAUSE_PATH = "/actions/server/pause"
UNPAUSE_PATH = "/actions/server/unpause"
PAUSE_CAPABILITY = "missions.pause"
UNPAUSE_CAPABILITY = "missions.unpause"

MESSAGE_PATH = "/actions/player/message"
MESSAGE_CAPABILITY = "players.message"

MESSAGE_DEFAULT_MODE = "popup"

SERVER_MAINTENANCE_CAPABILITY = "servers.maintenance"
SERVER_CLEAR_MAINTENANCE_CAPABILITY = "servers.clear_maintenance"

#: THE DCS config write — the write half of the Configuration tab (its read half is the page's
#: ``servers.config``, declared by ``pages/server_detail.py``). ``("Admin",)`` WITH a scope grant: an
#: Admin changes everything, and a MANAGER (an identity whose scope holds a server's ``managed_by``)
#: may change every setting EXCEPT the ones the manager deny-list names (``core.server_config`` — the
#: port today). The deny-list is applied inside the action, on top of the scope grant. A ``DCS Admin``
#: is refused: ``allows`` needs the ``Admin`` role or a managing scope, and its scope is unscoped.
SERVER_CONFIG_CAPABILITY = "servers.config.dcs"
SERVER_CONFIG_PATH = "/actions/server/config"
SERVER_CONFIG_ACTION_KEY = "config"
#: The hidden field a Save carries, holding the JSON map of OLD values a REVERT re-submits through the
#: SAME action (a revert is another write: audited, validated and locked like any other).
SERVER_CONFIG_REVERT_FIELD = "_revert"
#: The programmatic CLEAR field, still ACCEPTED by the route though no control renders it: a password
#: is normally cleared by EMPTYING the field, so this is only kept for a crafted caller that posts it.
SERVER_CONFIG_CLEAR_FIELD = "password_clear"
#: The single hidden field the whole form posts its target under — the same name every server write
#: uses (``server``), so one resolution path serves the config write and the row strip.
SERVER_CONFIG_FIELD = "server"

#: THE CHANNELS WRITE — the bot face of the same tab (``servers.yaml``), its own declaration, route
#: and action (``set_server_channels``). ``servers.config.channels`` is ``("Admin",)`` with NO scope
#: grant: the channels write is denied to a manager as a whole. The channel rows are also named in the
#: manager deny-list (``core.server_config.CHANNELS_ITEM``), so the page OMITS the whole card and
#: the action re-checks a direct caller. Its observable is the config revision, like the DCS Save.
SERVER_CHANNELS_CAPABILITY = "servers.config.channels"
SERVER_CHANNEL_ACTION_KEY = "channels"
SERVER_CHANNELS_PATH = "/actions/server/channels"

#: THE COALITION WRITE — the third face of the Configuration tab (the bot database). Its capability
#: ``servers.config.coalitions`` is ``("Admin",)`` WITH a scope grant, so a manager reaches the coalition
#: passwords of THEIR OWN servers (and nobody else's — the scope is applied by ``resolve_scoped_server``),
#: the same rule the DCS config write carries. The WRITE calls the bot's own ``setCoalitionPassword``.
SERVER_COALITIONS_CAPABILITY = "servers.config.coalitions"
SERVER_COALITION_ACTION_KEY = "coalitions"
SERVER_COALITIONS_PATH = "/actions/server/coalitions"

#: THE MISSION-LIST WRITES (card M3) — four controls on the Missions tab that mirror
#: ``plugins/mission/commands.py``'s ``/mission add|delete|load`` and ``plugins/mission/upload.py``.
#: Each is ``WRITE_ROLES`` (``Admin`` + ``DCS Admin``) WITH a scope grant, so a MANAGER stays inside
#: THEIR OWN servers (``resolve_scoped_server`` applies the scope; a foreign server is refused the
#: console's 403 without disclosing the target). One declaration each, read by the tab control, the
#: capability map and the route, exactly like the config faces above.
MISSIONS_ADD_CAPABILITY = "missions.add"
MISSIONS_DELETE_CAPABILITY = "missions.delete"
MISSIONS_LOAD_CAPABILITY = "missions.load"
MISSIONS_UPLOAD_CAPABILITY = "missions.upload"
MISSIONS_ADD_PATH = "/actions/server/missions/add"
MISSIONS_DELETE_PATH = "/actions/server/missions/delete"
MISSIONS_LOAD_PATH = "/actions/server/missions/load"
MISSIONS_UPLOAD_PATH = "/actions/server/missions/upload"
#: The form fields the mission writes read: the LOGICAL target (an index or name) the row's Load /
#: Remove controls post, the missions-dir-relative path the Add picker posts, the uploaded file, and
#: the two booleans the Add / Upload controls carry.
MISSION_FIELD = "mission"
#: THE STALE-PAGE COMPANION: the row's LOGICAL mission name, posted BESIDE its 1-based index so the
#: action can refuse a write whose list changed between render and POST. The value is
#: ``"<index>=<logical name>"`` (one per row; the console's rows and dialogs post it).
MISSION_NAME_FIELD = "mission_name"
MISSION_PATH_FIELD = "path"
MISSION_FILE_FIELD = "file"
MISSION_AUTOSTART_FIELD = "autostart"
MISSION_LOAD_FIELD = "load"
MISSION_DISK_FIELD = "delete_from_disk"

#: The size cap the console applies to an UPLOAD, as ONE named constant so the route, its early
#: refusal and its streaming read cannot disagree. It MUST equal the action's own
#: ``plugins.mission.actions.MAX_MISSION_UPLOAD_BYTES`` — the console keeps its own copy so the
#: generic web surface never imports a plugin module — and ``test_webui_missions_dialog`` pins the
#: two together so they cannot drift. 100 MiB: comfortably above a real ``.miz``.
MISSIONS_UPLOAD_MAX_BYTES = 100 * 1024 * 1024

#: THE M5 MISSION-LIST WRITES. The REORDER replaces the ORDER of ``missionList`` in ONE write through
#: the bot's own ``Server.setMissionList`` — no DCS-side function (the card's ruling: the idle states'
#: file write is the whole answer) — and is OFFLINE ONLY. BULK REMOVE is the row's own delete,
#: repeated: it calls the bot's own per-mission ``deleteMission``, one per selected mission,
#: DESCENDING, in every state but ``LOADING``. Both re-use ``missions.delete``, each has its own path,
#: and each has its own state gate the tab reads and the action enforces.
MISSIONS_REORDER_CAPABILITY = "missions.reorder"
MISSIONS_REORDER_PATH = "/actions/server/missions/reorder"
MISSIONS_BULK_DELETE_PATH = "/actions/server/missions/delete_bulk"
#: THE ACTIVATE WRITE (M7): make a CONFIGURED mission the START mission — through the bot's own
#: ``Server.setStartIndex``. Its own capability (``missions.activate``) and path; it works in every
#: state EXCEPT ``LOADING`` (see ``MISSIONS_ACTIVATE_STATES``).
MISSIONS_ACTIVATE_CAPABILITY = "missions.activate"
MISSIONS_ACTIVATE_PATH = "/actions/server/missions/activate"
#: The form field the reorder control posts (``up`` / ``down``).
MISSION_DIRECTION_FIELD = "direction"
#: The states a mission-list write is ACCEPTED in — the file-write states, i.e. the DCS process is
#: DOWN. ``STOPPED`` is NOT one of them (DCS is up in ``{LOADING, STOPPED, PAUSED, RUNNING}`` and owns
#: ``net.missionlist`` in memory): a write made then is reverted (`plugins/mission/actions.py`). This
#: gate is the OFFLINE-ONLY move's (``mission_reorder``); bulk remove uses ``MISSIONS_BULK_DELETE_STATES``.
MISSION_LIST_OFFLINE_STATES: tuple[str, ...] = ("UNREGISTERED", "SHUTDOWN", "SHUTTING_DOWN")

#: The sentence the OFFLINE-ONLY move refuses with — the reason the tab shows where it omits its
#: control. Owned by ``plugins.mission.actions.MISSION_LIST_OFFLINE_ONLY``; the console keeps its OWN
#: copy so the generic web surface never imports a plugin module (a test pins them equal).
MISSION_LIST_OFFLINE_ONLY = "Missions can only be changed while the server is not running."

#: BULK REMOVE'S STATE GATE (M8): every state EXCEPT ``LOADING``. The bot's ``deleteMission`` routes
#: to DCS in ``{STOPPED, PAUSED, RUNNING}`` and edits the mission file otherwise; in ``LOADING`` the
#: process is up (DCS owns ``net.missionlist``) but the method would edit the file, so the removal is
#: reverted — the action refuses it and the tab omits the control there. The other five states stick.
MISSIONS_BULK_DELETE_STATES: tuple[str, ...] = (
    "UNREGISTERED", "SHUTDOWN", "SHUTTING_DOWN", "STOPPED", "PAUSED", "RUNNING")

#: The sentence the bulk remove refuses a ``LOADING`` POST with — owned by
#: ``plugins.mission.actions.MISSION_DELETE_LOADING_REFUSAL``; the console keeps its OWN copy so the
#: generic web surface never imports a plugin module (a test pins the two equal).
MISSION_DELETE_LOADING_REFUSAL = "Missions cannot be removed while the server is loading."

#: THE ACTIVATE WRITE'S STATE GATE (M7): every state EXCEPT ``LOADING``. The bot's ``setStartIndex``
#: routes to DCS in ``{STOPPED, PAUSED, RUNNING}`` and writes the file otherwise; in ``LOADING`` the
#: process is up (DCS owns ``net.missionlist``) but the method would write the file, so the change is
#: reverted — the action refuses it and the tab omits the control there. The other five states stick.
MISSIONS_ACTIVATE_STATES: tuple[str, ...] = (
    "UNREGISTERED", "SHUTDOWN", "SHUTTING_DOWN", "STOPPED", "PAUSED", "RUNNING")

#: The sentence the activate write refuses a ``LOADING`` POST with — owned by
#: ``plugins.mission.actions.MISSION_ACTIVE_LOADING_REFUSAL``; the console keeps its OWN copy so the
#: generic web surface never imports a plugin module (a test pins the two equal).
MISSION_ACTIVE_LOADING_REFUSAL = "The start mission cannot be changed while the server is loading."

#: The sentence the bar's ``Set as start`` refuses a selection that is not EXACTLY ONE mission with —
#: the tick IS its target (M11), so NONE names nothing and SEVERAL names no single mission. Owned by
#: the console (no plugin copy: the plugin's action takes ONE mission and never sees a selection), and
#: stated where the count already reads, so the client's own reason and this answer are one rule.
MISSIONS_ACTIVATE_SELECTION_REFUSAL = (
    "Select exactly one mission to set as the start mission.")



def mission_index_required(value: Any) -> str:
    """The sentence a NON-POSITIVE numeric mission target gets on EVERY console surface.

    The console's mission indexes are 1-BASED (the number its own tab shows and its row links post),
    so ``0`` — and anything below it — names nothing. Said plainly, in ONE place, so the download
    route's 404 and the load route's notice read the same.
    """
    return f"The mission index is 1-based; '{readmodels.text(value)}' is not a mission index."


def _upload_cap_message() -> str:
    """The console's over-cap refusal — the SAME sentence the action answers with, so an operator
    cannot tell which surface refused (the two caps are pinned equal by a test)."""
    return (f"The mission file is larger than the "
            f"{MISSIONS_UPLOAD_MAX_BYTES // (1024 * 1024)} MiB limit and was not uploaded.")

MESSAGE_FIELD_MAX = 1024

NOTICE_KEY = "_action_notice"

NOTICE_MAX_CHARS = 300

#: The most characters the one-shot REVERT payload may occupy in the session cookie. Bounded well
#: below the browser's ~4 KiB cookie ceiling, and large enough to carry the action's per-field maximum
#: (``description`` ≤ 2000) plus its JSON overhead. A payload that exceeds this is dropped WHOLE, never
#: truncated — a string cut mid-value cannot parse and would make the Revert silently inert.
REVERT_MAX_CHARS = 2800

#: The sentence a notice carries when a Revert could not be offered because its payload cannot be
#: carried whole — so the operator learns WHY there is no Revert rather than being shown a dead one.
REVERT_TOO_LONG_SENTENCE = ("The previous values are too long to offer a one-shot Revert here. "
                            "Re-open the page to edit them back by hand.")

AWAIT_MAX_PENDING = 8
AWAIT_CHANGE_SECONDS = 120

_AWAIT_LOCK = threading.Lock()
_AWAIT: dict[str, dict] = {}

AWAIT_OBSERVABLE_STATUS = "status"
AWAIT_OBSERVABLE_MAINTENANCE = "maintenance"
#: The THIRD observable: a config write moves neither ``status`` nor ``maintenance``, so neither of the
#: two above can end its pulse. Its signal is the server's CONFIG REVISION (the settings ``mtime``
#: hashed with the ``servers.yaml`` mtime, read without an RPC — :func:`config_revision`). On an AGENT
#: node the expectation can end on the background poller's tick rather than on this response, bounded
#: by :data:`AWAIT_CHANGE_SECONDS` like every other action.
AWAIT_OBSERVABLE_CONFIG = "config"

CONFIRM_SUFFIX = "/confirm"
CONFIRM_FIELD = "_confirm_token"
CONFIRM_TEMPLATE = "confirm.html"

ORIGIN_FIELD = "_origin"
CONFIRM_KEY = "_action_confirms"
CONFIRM_MAX_PENDING = 8

ROW_STRIP = "strip"
ROW_MENU = "menu"

MENU_NOTE = ("Start and Stop live only here: they act on fewer states than Startup and Shutdown, so "
             "they do not earn a place in the strip.")


@dataclass(frozen=True, slots=True)
class NodeOption:
    """ONE checkbox a control's DIALOG carries — the command's own option, as a form field.

    SHARED by the NODE row's operations (``/node offline``'s ``maintenance``) and the SERVER row's
    Shutdown/Startup (the flag each sets or clears by default). It lives above every declaration
    because it is READ while the action tuples are built (``NodeOption(...)`` is a runtime call), and
    because its shape is one thing, not one per surface.

    The option is NOT decoration: ``/node offline`` stops the servers by default and ``/node online``
    does not start them by default, and Discord's ``/server shutdown|startup`` set/clear the
    maintenance flag by default — so the choice is part of the operation and the dialog is where the
    operator makes it.

    ``field`` is BOTH the form field's name and the ACTION parameter's name — one string, so the
    route cannot post one thing and the action read another. ``on``/``off`` are the two values the
    control sends, and the companion ``off`` input is rendered BEFORE the checkbox (a checked box
    sends no field of its own, and this FastAPI resolves a repeated field to the LAST value): see
    :func:`option_inputs`.
    """

    field: str
    label: str
    default: bool = False
    help: str = ""
    on: str = "1"
    off: str = "0"


@dataclass(frozen=True, slots=True)
class WriteAction:
    """One write of the console: what it needs, what it calls, and which state it applies to.

    Data, not code, on purpose: the route table, the capability map and the page
    control are three readings of ONE declaration, so a write cannot exist without a declared
    capability and a declared action.

    ``statuses`` is the state the control applies to. It is not a second authorization: it *** the
    page from offering a button whose only possible answer is the action's own state guard
    (``"Server 'X' is Stopped, not running."``). The guard remains the authority — a stale page or a
    crafted POST reaches it and is refused with a typed result. An EMPTY ``statuses`` means "every
    state": the maintenance pair is the one control set that applies in all of them, because a FLAG is
    not a power state — it can be set or cleared while the process is up, down or between.

    ``when_maintenance`` is the flag pair's own state gate, exactly as ``PlayerAction.when_muted`` is
    the mute pair's: ``set_maintenance`` is offered only on a server the read model says is NOT
    flagged, ``clear_maintenance`` only on one it says is. It is the ONLY per-row server state the
    strip consults beyond ``statuses``, and it is read from the same scoped source the row renders
    from — so the two halves of the pair are never both offered and never both withheld.

    The PRESENTATION half (``key`` the glyph, ``tip`` the native tooltip, ``aria`` the accessible
    name, ``row`` where the control sits, ``danger`` the hover tint, ``confirm`` whether it goes
    through its dialog, and the dialog's own ``warning``/``detail``/``go`` copy) is here rather than
    in a template for the same reason: one declaration, read by the row, by the dialog page and by
    the tests. ``tip``/``aria``/``warning``/``detail``/``go_title`` carry ``{server}``/``players``
    placeholders, formatted by :func:`_control` and :func:`confirm_dialog` from the RESOLVED object
    — never from anything the browser sent.
    """
    key: str
    capability: str
    qualname: str
    path: str
    label: str
    statuses: tuple[str, ...]
    row: str = ROW_STRIP
    danger: bool = False
    confirm: bool = False
    when_maintenance: bool | None = None
    #: the command's own OPTION, when the action has one: a checkbox with its ``off`` companion,
    # drawn by the action's DIALOG (``templates/confirm.html`` via :func:`option_inputs`).
    # Shutdown sets the maintenance flag, Startup clears it (``plugins.mission.actions``'s own contract),
    # and the choice is part of the operation, so the dialog is where the operator makes it —
    # which is what makes these two controls reach their dialog even when they are not destructive
    # (see :attr:`dialog`).
    option: NodeOption | None = None
    #: THE OTHER OPTIONS a control's DIALOG draws, when ONE checkbox is not enough — the Missions
    #: tab's ADD flow, whose dialog owns both the load-at-next-start and the load-now choices. Same
    #: shape as ``option`` (a :class:`NodeOption`) and drawn by the SAME template (:func:`option_inputs`
    #: reads ``option`` then ``options``); ``option`` stays the single-option spelling the server and
    #: node rows use, and a control may use either. A mission route parses its own option fields
    #: (``_mission_add_handler``), so ``_handler``'s single-option parse is untouched by this.
    options: tuple[NodeOption, ...] = ()
    #: WHICH observable this action MOVES, and therefore which signal ends the pulse it starts.
    # The default — the row's status/state — is what every power action and the
    #: mission pair change; ``AWAIT_OBSERVABLE_MAINTENANCE`` is the flag pair's own, because setting or
    #: clearing ``server.maintenance`` moves no status at all. It lives on the DECLARATION for the same
    #: reason every other fact about a control does: the route that records the expectation and the
    #: read that ends it are two readings of ONE declaration, never a second list of "which actions
    #: are flag actions" that could drift from the pair below.
    observable: str = AWAIT_OBSERVABLE_STATUS
    #: THE STATE ITS WRITE IS WORKING TOWARD: the row reads one of these when the action has SETTLED,
    #: and only then is its pulse spent. The end condition is not simply "the status moved", which a
    #: real DCS boot defeats — a launch goes ``SHUTDOWN`` -> ``LOADING`` (most of the boot) ->
    #: ``RUNNING`` (``core/data/impl/serverimpl.py``), so the expectation would be spent one or two
    #: seconds in and the pulse gone before anyone saw it. ``LOADING`` is deliberately in NO settled
    #: set — it is the state the person is waiting THROUGH — and while the row sits there with a
    #: pending start/restart its control stays on the row, busy (see :func:`server_controls`).
    #:
    #: * ``startup`` / ``start`` / ``restart`` settle at ``RUNNING``/``PAUSED``;
    #: * ``shutdown`` / ``stop`` settle at ``SHUTDOWN``/``STOPPED``;
    #: * the mission pair settles at the state it moves to (``PAUSED`` / ``RUNNING``).
    #:
    #: AN EMPTY tuple means "any change of the observable ends it" — the rule kept for an action that
    #: names no target state — and the flag pair, whose observable is a boolean, ignores this field
    #: entirely (its value either moved or it did not). ``restart`` is why the read needs more than
    #: ``state in settled``: it is SUBMITTED in a settled state (``RUNNING``), so the read also
    #: requires that the row LEFT that state first — see :func:`awaiting_change`.
    settled: tuple[str, ...] = ()
    tip: str = ""
    aria: str = ""
    hint: str = ""
    warning: str = ""
    detail: str = ""
    go: str = ""
    go_title: str = ""

    @property
    def dialog(self) -> bool:
        """Whether this control is reached through its DIALOG (``confirm.html``) rather than posting
        the action directly.

        The node row's own rule, applied to the server row (``_node_control``): a control that
        CONFIRMS posts to ``<path>/confirm`` so the action's route can refuse a POST that skipped
        the dialog, and a control that CARRIES AN OPTION posts there too because the option is a
        form field and has nowhere else to live. Startup is the second case: it is not destructive
        (nothing to confirm), but its maintenance box is drawn — and chosen — on the dialog.

        This is the ONE place the question is answered: the control's path (``_control``), the
        dialog route's registration and the capability map (``capabilities``) are three readings of
        it, so a control cannot post directly while its option is rendered on a page nobody reaches.
        """
        return bool(self.confirm or self.option is not None or self.options)


#: The SERVER row's controls. The three process/DCS-level writes
#: (``startup_server`` / ``shutdown_server`` / ``start_server`` / ``stop_server``) are
#: ``plugins/mission/actions.py``'s own actions; ``pause_mission`` / ``unpause_mission`` are the
#: mission plugin's. The ``__qualname__`` is the registry's key (``core/actions.py``), never the
#: function NAME.
#:
#: * **stop stays the filled square**, **start the play triangle** (it never appears beside startup:
#:   the states are disjoint);
#: * the player-row glyphs (popup filled, kick simplified).
SERVER_ACTIONS: tuple[WriteAction, ...] = (
    # Startup's maintenance option: the same box the node row's ``offline`` carries,
    # mirroring Discord's ``/server startup`` — ON by default, meaning CLEAR any flag so the
    # scheduler may start the server again. It rides on the DIALOG (``WriteAction.dialog``), which
    # is why this non-destructive control opens a form instead of posting directly: the checkbox is
    # a form field with nowhere else to live.
    WriteAction(key="startup", capability="servers.startup", qualname="startup_server",
                path="/actions/server/startup", label="Startup", statuses=("SHUTDOWN",),
                settled=("RUNNING", "PAUSED"),
                option=NodeOption(
                    field="maintenance",
                    label="End maintenance so the server is scheduled again",
                    default=True,
                    help="On by default, exactly like Discord's /server startup. An unflagged "
                         "server simply stays unflagged."),
                tip="Startup — bring this server up from SHUTDOWN",
                aria="Startup server {server}",
                detail="It comes back up and rejoins the rotation: with the box below ticked (the "
                       "default) any maintenance is ended, so the scheduler may start it again. A "
                       "server that was not in maintenance simply stays that way. This is the "
                       "DCS-level bring-up, not the process-level Start in the row's menu.",
                go="Start up server",
                go_title="Start up {server} — ends maintenance so the scheduler may start it"),
    WriteAction(key="start", capability="servers.start", qualname="start_server",
                path="/actions/server/start", label="Start", statuses=("STOPPED",),
                settled=("RUNNING", "PAUSED"),
                row=ROW_MENU,
                tip="Start — start a STOPPED server",
                aria="Start server {server}",
                hint="start a STOPPED server — Startup is the usual one"),
    WriteAction(key="pause", capability=PAUSE_CAPABILITY, qualname="pause_mission",
                path=PAUSE_PATH, label="Pause", statuses=("RUNNING",),
                settled=("PAUSED",),
                tip="Pause — freeze the mission; players stay connected",
                aria="Pause the mission on {server}"),
    WriteAction(key="unpause", capability=UNPAUSE_CAPABILITY, qualname="unpause_mission",
                path=UNPAUSE_PATH, label="Unpause", statuses=("PAUSED",),
                settled=("RUNNING",),
                tip="Unpause — resume the frozen mission",
                aria="Unpause the mission on {server}"),
    WriteAction(key="restart", capability="servers.restart", qualname="restart_server",
                path="/actions/server/restart", label="Restart", statuses=("RUNNING", "PAUSED"),
                settled=("RUNNING", "PAUSED"),
                danger=True, confirm=True,
                tip="Restart — players currently flying lose their sortie",
                aria="Restart server {server}",
                warning="<b>Players currently flying lose their sortie.</b> The mission restarts "
                        "from the beginning — mid-mission progress is gone.",
                detail="A restart stops the mission, reloads it from the start and the players "
                       "reconnect to a fresh round. It cannot be undone from here.",
                go="Restart server",
                go_title="Restart {server} — players flying lose their sortie"),
    WriteAction(key="shutdown", capability="servers.shutdown", qualname="shutdown_server",
                path="/actions/server/shutdown", label="Shutdown", statuses=("RUNNING", "PAUSED"),
                settled=("SHUTDOWN", "STOPPED"),
                danger=True, confirm=True,
                option=NodeOption(
                    field="maintenance",
                    label="Set maintenance so the scheduler does not restart it",
                    default=True,
                    help="On by default, exactly like Discord's /server shutdown: without it a "
                         "scheduled start brings the server straight back."),
                tip="Shutdown — stop the DCS server; everyone on it is disconnected",
                aria="Shutdown server {server}",
                warning="<b>{players} players are disconnected</b> and nobody can join until "
                        "somebody starts the server up again. The box below is ticked by default "
                        "and puts the server into maintenance — it stays out of service, so the "
                        "scheduler does not bring it back up. Clear it only to shut the server down "
                        "without keeping it down.",
                detail="It stays SHUTDOWN on every page until a Startup brings it back — this is "
                       "the graceful stop, not the process-level Stop in the row's menu.",
                go="Shut down",
                go_title="Shut down {server} — {players} players are disconnected"),
    WriteAction(key="stop", capability="servers.stop", qualname="stop_server",
                path="/actions/server/stop", label="Stop", statuses=("RUNNING", "PAUSED"),
                settled=("SHUTDOWN", "STOPPED"),
                row=ROW_MENU, danger=True, confirm=True,
                tip="Stop — stop the server process; players are disconnected",
                aria="Stop server {server}",
                hint="stop the server process — players are disconnected",
                warning="<b>{players} players are disconnected.</b> Stop ends the server process, "
                        "and nobody can join until a Start brings it back.",
                detail="This is the process-level pair (Stop/Start), not the DCS-level pair "
                       "(Shutdown/Startup).",
                go="Stop server process",
                go_title="Stop {server} — {players} players are disconnected"),
    # ── the MAINTENANCE flag pair ──────────────────────────
    # ONE HOME PER CONCEPT: the per-server flag, set and cleared here and NOWHERE ELSE on
    # the node row — the node row's pair is power and no longer carries a flag. The two halves are
    # the flags' own state pair, gated by ``when_maintenance`` (the ``when_muted`` shape), and they
    # are offered in EVERY server state: a flag is not a power state, so neither the server's
    # ``status`` nor its process being up has anything to say about whether it may be flagged.
    #
    # NO CONFIRMATION, and that is the mockup's own rule: neither
    # half kills a process, disconnects a player or loses state — the flag only decides whether the
    # SCHEDULER may start the server. Setting it while a restart is pending aborts that restart, and
    # the ACTION says so in its message; the console does not put a dialog in front of a reversible
    # state change when the row next to it (Shutdown) already confirms the irreversible ones.
    WriteAction(key="maintenance", capability=SERVER_MAINTENANCE_CAPABILITY,
                qualname="set_maintenance", path="/actions/server/maintenance",
                label="Maintenance", statuses=(), when_maintenance=False,
                observable=AWAIT_OBSERVABLE_MAINTENANCE,
                tip="Maintenance — keep this server out of service; the scheduler will not start it",
                aria="Put server {server} into maintenance",
                hint="keep the server out of service — it is not stopped by this"),
    WriteAction(key="end_maintenance", capability=SERVER_CLEAR_MAINTENANCE_CAPABILITY,
                qualname="clear_maintenance", path="/actions/server/end-maintenance",
                label="End maintenance", statuses=(), when_maintenance=True,
                observable=AWAIT_OBSERVABLE_MAINTENANCE,
                tip="End maintenance — let the scheduler start this server again",
                aria="Take server {server} out of maintenance",
                hint="the server may be started again — nothing is started by this"),
)


#: THE DCS CONFIG WRITE — a declaration of its own, NOT a ``SERVER_ACTIONS`` row: it is not a
#: row-strip control (its body is a MAPPING of changed settings, not a target). It is still ONE
#: :class:`WriteAction`, so the route table (``add_routes``), the capability map (``capabilities``)
#: and the page control are three readings of THIS one declaration. ``statuses`` is the same state
#: gate the page renders from (the action refuses a running server itself): Save is offered only where
#: the file may be written.
SERVER_CONFIG_ACTIONS: tuple[WriteAction, ...] = (
    WriteAction(key=SERVER_CONFIG_ACTION_KEY, capability=SERVER_CONFIG_CAPABILITY,
                qualname="set_server_config", path=SERVER_CONFIG_PATH, label="Save",
                statuses=("SHUTDOWN", "STOPPED", "UNREGISTERED"),
                observable=AWAIT_OBSERVABLE_CONFIG,
                tip="Save — write the DCS configuration; it applies when the server next starts",
                aria="Save the configuration of {server}"),
)


#: THE CHANNELS WRITE — the bot face of the Configuration tab (``servers.yaml``). A declaration of its
#: own, NOT a ``SERVER_CONFIG_ACTIONS`` row: its body is a MAPPING of changed channel rows, applied
#: IMMEDIATELY through the bot's ``Server.update_channels``, so there is NO state gate (``statuses=()``
#: means every server state). Its observable is the config revision, so its Save pulses on the same
#: signal. Its capability (``servers.config.channels``) is its own Admin-only one, denied to a manager.
SERVER_CHANNEL_ACTIONS: tuple[WriteAction, ...] = (
    WriteAction(key=SERVER_CHANNEL_ACTION_KEY, capability=SERVER_CHANNELS_CAPABILITY,
                qualname="set_server_channels", path=SERVER_CHANNELS_PATH, label="Save channels",
                statuses=(),
                observable=AWAIT_OBSERVABLE_CONFIG,
                tip="Save channels — write the Discord channels; applied immediately, no restart",
                aria="Save the Discord channels of {server}"),
)


#: THE COALITION WRITE — the third face of the Configuration tab (the bot's database). Its OWN action
#: (``set_coalition_password``), which calls the bot's own ``Server.setCoalitionPassword`` — never the
#: hash, never a direct file/database write. ``statuses`` is its OWN gate: unlike the DCS Save, the
#: coalition write works WHILE the server is up (the bot's method tells DCS live), so RUNNING and PAUSED
#: are offered. There is NO pulse: no observable moves reliably for this write (the config revision
#: moves only on the file branch), so the one-shot notice carries the outcome alone.
SERVER_COALITION_ACTIONS: tuple[WriteAction, ...] = (
    WriteAction(key=SERVER_COALITION_ACTION_KEY, capability=SERVER_COALITIONS_CAPABILITY,
                qualname="set_coalition_password", path=SERVER_COALITIONS_PATH,
                label="Save coalition passwords",
                statuses=("SHUTDOWN", "STOPPED", "RUNNING", "PAUSED"),
                tip="Save coalition passwords — the bot sends them to DCS and keeps them in its database",
                aria="Save the coalition passwords of {server}"),
)


#: THE MISSION-LIST WRITES (card M3) — four declarations of their own, NOT ``SERVER_ACTIONS`` rows:
#: their controls live on the Missions TAB (a list, an add form and an upload form), not on the
#: server row strip. Each is still ONE :class:`WriteAction`, so the route table (``add_routes``), the
#: capability map (``capabilities``) and the tab control are three readings of ONE declaration.
#: ``load``'s ``statuses`` IS the gate ``load_mission`` applies (RUNNING / PAUSED / STOPPED); the tab
#: omits the control where the action would refuse. ``add`` / ``delete`` / ``upload`` work in every
#: state (``addMission`` / ``deleteMission`` route through DCS or edit the file directly), so their
#: ``statuses`` is empty — as does the M8 BULK REMOVE, except in ``LOADING``
#: (``MISSIONS_BULK_DELETE_STATES``).
MISSION_ACTIONS: tuple[WriteAction, ...] = (
    # ADD is reached through its DIALOG (``MISSION_DIALOG_KEYS``): the picker and the load choices
    # both live there, so choosing a mission and confirming happen in ONE dialog — Frank's
    # "decisive" rule. It carries NO confirm token: adding to the rotation is not destructive
    # (nothing is copied, nothing is deleted), so its dialog is the OPTIONS form, like Startup's.
    WriteAction(key="mission_add", capability=MISSIONS_ADD_CAPABILITY, qualname="add_mission",
                path=MISSIONS_ADD_PATH, label="Add to the rotation", statuses=(),
                options=(
                    NodeOption(
                        field=MISSION_AUTOSTART_FIELD,
                        label="Load it when the server next starts",
                        default=False,
                        help="Arms the rotation so this mission is the next one the server loads."),
                    NodeOption(
                        field=MISSION_LOAD_FIELD,
                        label="Load it now",
                        default=False,
                        help="Loads it right away — only honoured while the server is running, "
                             "paused or stopped."),
                ),
                tip="Add — put a mission that is ALREADY on this server into its rotation list",
                aria="Add a mission to {server}",
                detail="This mission is already on the server: adding it changes the ROTATION LIST "
                       "only. No file is copied and nothing is deleted — that is what Upload does.",
                go="Add to the rotation",
                go_title="Add the mission to {server}'s rotation list"),
    # REMOVE is destructive, so it CONFIRMS through the same dialog component and, carrying an
    # option, also collects the "delete the real file?" choice there — the two questions Discord's
    # ``/mission delete`` asks, in ONE dialog. The one-shot token is what makes the dialog a control
    # rather than a screen (see ``_mission_confirm_handler`` / ``_mission_delete_handler``).
    WriteAction(key="mission_delete", capability=MISSIONS_DELETE_CAPABILITY,
                qualname="delete_mission", path=MISSIONS_DELETE_PATH, label="Remove", statuses=(),
                danger=True, confirm=True,
                option=NodeOption(
                    field=MISSION_DISK_FIELD,
                    label="Also delete the mission file from disk",
                    default=False,
                    help="Removes the primary file, its .dcssb copy and the .orig. This cannot be "
                         "undone."),
                tip="Remove — take the mission out of the rotation list; optionally delete it from "
                    "disk too",
                aria="Remove a mission from {server}",
                warning="<b>The mission is removed from the rotation list and stops being loaded.</b> "
                        "The box below is off by default: with it ticked the mission file itself — "
                        "the primary, its .dcssb copy and the .orig — is deleted from the server, and "
                        "that cannot be undone.",
                detail="Removing it from the list changes the rotation only; the file stays on the "
                       "server unless you tick the box. Discord's /mission delete asks the same two "
                       "questions.",
                go="Remove mission",
                go_title="Remove the mission from {server}'s rotation list"),
    WriteAction(key="mission_load", capability=MISSIONS_LOAD_CAPABILITY, qualname="load_mission",
                path=MISSIONS_LOAD_PATH, label="Load",
                statuses=("RUNNING", "PAUSED", "STOPPED"),
                tip="Load — start this mission now on {server}",
                aria="Load a mission on {server}"),
    WriteAction(key="mission_upload", capability=MISSIONS_UPLOAD_CAPABILITY,
                qualname="upload_mission", path=MISSIONS_UPLOAD_PATH, label="Upload mission",
                statuses=(),
                tip="Upload — send a .miz FILE from your own computer into {server}'s missions "
                    "directory and add it",
                aria="Upload a mission to {server}"),
    # BULK REMOVE (M5; the gate reworked by M8): several missions out of the rotation, one row's
    # delete repeated. It RE-USES ``missions.delete`` (it is still delete) and goes through its DIALOG
    # — the console's ONE dialog component, which here IS the selection (a checkbox per configured
    # mission, the running one omitted with the reason stated in the warning) plus the same "delete
    # the real file?" box and the one-shot confirm token. Its ``statuses`` is every state EXCEPT
    # ``LOADING`` (``MISSIONS_BULK_DELETE_STATES``): the bot's ``deleteMission`` sticks in the
    # process-up trio (through DCS) and the file-write trio (by the file), and only ``LOADING``
    # reverts it.
    WriteAction(key="mission_delete_bulk", capability=MISSIONS_DELETE_CAPABILITY,
                qualname="delete_missions", path=MISSIONS_BULK_DELETE_PATH,
                label="Remove missions", statuses=MISSIONS_BULK_DELETE_STATES,
                danger=True, confirm=True,
                option=NodeOption(
                    field=MISSION_DISK_FIELD,
                    label="Also delete the mission files from disk",
                    default=False,
                    help="Removes the primary file, its .dcssb copy and the .orig of EVERY selected "
                         "mission. This cannot be undone. Left OFF, the mission only leaves the "
                         "rotation — and while the server's mission auto-scan is on, a mission file "
                         "left on disk is re-added to the list, so the removal is not permanent."),
                tip="Remove — take SEVERAL missions out of the rotation list at once; optionally "
                    "delete their files from disk too",
                aria="Remove missions from {server}",
                warning="<b>The selected missions are removed from the rotation list and stop being "
                        "loaded.</b> The box below is off by default: with it ticked the mission "
                        "files themselves are deleted from the server, and that cannot be undone.",
                detail="The chosen missions leave the rotation list; their files stay on the server "
                       "unless you tick the box.",
                go="Remove missions",
                go_title="Remove the selected missions from {server}'s rotation list"),
    # MOVE UP / DOWN (M5): reorder ONE mission in the rotation list. Two small buttons per row open
    # no dialog — they post directly — and the offline-only gate is its ``statuses``.
    WriteAction(key="mission_reorder", capability=MISSIONS_REORDER_CAPABILITY,
                qualname="reorder_mission", path=MISSIONS_REORDER_PATH, label="Move",
                statuses=MISSION_LIST_OFFLINE_STATES,
                tip="Move — reorder a mission in {server}'s rotation list (only while the server is "
                    "not running)",
                aria="Move a mission in {server}'s rotation list"),
    # ACTIVATE (M7): make ONE configured mission the START mission — through the bot's own
    # ``setStartIndex``. Offered in every state EXCEPT ``LOADING`` (``MISSIONS_ACTIVATE_STATES``); the
    # action refuses that one state, where the write would be silently reverted. It posts directly —
    # not destructive, no options — like the move pair. ITS LABEL IS THE OPERATOR'S WORD (M9):
    # ``Set as start``, matching the ``START`` marker on the row it names; it sets the rotation
    # pointer and does NOT load the mission now (that is ``Load``, a different control and a
    # different write).
    WriteAction(key="mission_activate", capability=MISSIONS_ACTIVATE_CAPABILITY,
                qualname="set_active_mission", path=MISSIONS_ACTIVATE_PATH, label="Set as start",
                statuses=MISSIONS_ACTIVATE_STATES,
                tip="Set as start — make this mission the one {server} loads when it starts (the "
                    "rotation pointer); it does not load it now",
                aria="Set the start mission on {server}"),
)

#: The mission controls reached through their DIALOG (``confirm.html``): the ADD flow, whose picker
#: and load choices live on the dialog, the single REMOVE (its tab control is gone, but the route
#: remains), and the M5 bulk REMOVE — whose dialog shows the LIST'S selection READ-ONLY. All post
#: through ``<path>/confirm``; the capability map declares that path (``capabilities``) and
#: ``add_routes`` registers it. ``mission_delete`` and ``mission_delete_bulk`` also declare ``confirm``,
#: so their own routes demand the token.
MISSION_DIALOG_KEYS: frozenset[str] = frozenset(
    {"mission_add", "mission_delete", "mission_delete_bulk"})


@dataclass(frozen=True, slots=True)
class PlayerButton:
    """ONE button of a player control that offers several — the chat/popup pair.

    The mode is the button's own ``name``/``value`` pair, so one form carries both modes and the
    browser sends the mode the person clicked; nothing is inferred from the label, and there is no
    second form to keep in step. ``icon``/``aria``/``tip`` name the MODE because the two buttons of
    one control must be told apart by a screen reader (two identical accessible names on one row is
    the same failure as an unlabelled icon).
    """
    mode: str
    icon: str
    tip: str = ""
    aria: str = ""


@dataclass(frozen=True, slots=True)
class PlayerAction:
    """One write of the console that acts on a PLAYER row.

    Its own record rather than a second :class:`WriteAction`, because a player write carries what a
    server write does not: the UCID that completes its target, the MODES it offers and, with them, a
    field the person fills in. Same discipline as its server twin — data, not code: the route table,
    the capability map, the page control and the dialog are four readings of ONE declaration.

    ``buttons`` is the control's own button set, in the order the control draws it: the message
    control's two modes (chat, popup), each with its own glyph and accessible name. A control with
    NO buttons renders one button carrying the action's own ``icon``/``tip``/``aria``.

    ``params`` is the translation from FORM field name to the ACTION's parameter name, which is the
    only thing the route does with a body (``{"message": "message", "mode": "mode"}``); ``sender``
    marks the actions that are told who is acting, so the copy reads the identity rather than a field
    the browser sent.

    ``field``/``field_label``/``field_max`` describe the text field the DIALOG offers (a kick reason,
    a ban reason). A control with ``confirm`` never carries a field on the row: the field lives in
    the dialog, where the mockup draws it.

    ``when_muted`` is the mute pair's own state gate: ``unmute`` is offered only on a player the read
    model says is muted, ``mute`` only on one it does not. It is the ONLY per-row player state the
    control consults, and it is read from the same scoped source the row renders from.
    """
    key: str
    capability: str
    qualname: str
    path: str
    label: str
    icon: str = ""
    danger: bool = False
    confirm: bool = False
    #: record EVERY attempt at this action in the audit, including a refusal the ROUTE made before
    #: any action ran.
    audit_refusals: bool = False
    field: str = ""
    field_label: str = ""
    field_max: int = 0
    field_help: str = ""
    buttons: tuple[PlayerButton, ...] = ()
    params: tuple[tuple[str, str], ...] = ()
    sender: bool = False
    when_muted: bool | None = None
    tip: str = ""
    aria: str = ""
    hint: str = ""
    warning: str = ""
    detail: str = ""
    go: str = ""
    go_title: str = ""


#: The player row's controls: kick · ban · chat · popup · mute. Every one of them posts to an
#: action that EXISTS — ``message_player`` (its two modes drawn as the chat/popup pair) and the three
#: mission actions in ``plugins/mission/actions.py``. The ``__qualname__`` is the registry's key
#: (``core/actions.py``), never the function NAME.
#:
#: CAPABILITIES: ``players.kick`` / ``players.ban`` / ``players.mute`` alongside ``players.message``
#: — all four declared here with ``scope_grants=True``, so a
#: manager reaches them on their own servers and on nobody else's, which is the same rule the
#: mockup's ``03-permissions-…`` picture shows (chat+popup for a manager's own players).
#:
#: THE WORDS are in the user's terms: the consequence first, in bold, then
#: what actually happens. ``{player}`` and ``{server}`` are formatted from the RESOLVED object.
PLAYER_ACTIONS: tuple[PlayerAction, ...] = (
    PlayerAction(key="kick", capability="players.kick", qualname="kick_player",
                 path="/actions/player/kick", label="Kick", icon="kick",
                 danger=True, confirm=True, audit_refusals=True,
                 field="reason", field_label="Reason", field_max=200,
                 field_help="Shown to the player in game.",
                 params=(("reason", "reason"),),
                 tip="Kick — disconnected now; they may rejoin immediately",
                 aria="Kick {player} from {server}",
                 hint="disconnected now — a kick is not a ban",
                 warning="<b>{player} is disconnected from {server} now.</b> They can rejoin "
                         "immediately — a kick is not a ban and leaves no block behind.",
                 detail="A kick ends their session; it blocks nothing and expires with it. Nothing "
                        "is written to the ban list, so they may be back in the air in a minute.",
                 go="Kick player",
                 go_title="Kick {player} from {server} — they may rejoin immediately"),
    PlayerAction(key="ban", capability="players.ban", qualname="ban_player",
                 path="/actions/player/ban", label="Ban", icon="ban",
                 danger=True, confirm=True, audit_refusals=True,
                 field="reason", field_label="Reason", field_max=80,
                 field_help="Kept on the ban list.",
                 params=(("reason", "reason"), ("days", "days")), sender=True,
                 tip="Ban — removed and blocked from rejoining",
                 aria="Ban {player} from {server}",
                 hint="removed and blocked from rejoining",
                 warning="<b>{player} is removed from {server} and cannot rejoin it</b> for as long "
                         "as the ban stands. Empty duration means it never expires on its own.",
                 detail="The ban is written to the ban list every installation syncs, so it holds "
                        "wherever they connect from — not only on this server.",
                 go="Ban player",
                 go_title="Ban {player} — empty duration is permanent"),
    PlayerAction(key="message", capability=MESSAGE_CAPABILITY, qualname="message_player",
                 path=MESSAGE_PATH, label="Message",
                 field="message", field_label="Message", field_max=MESSAGE_FIELD_MAX,
                 params=(("message", "message"), ("mode", "mode")), sender=True,
                 buttons=(
                     PlayerButton(mode="chat", icon="chat",
                                  tip="Chat — a chat line in game, visible to everyone",
                                  aria="Send a chat line to {player} on {server}"),
                     PlayerButton(mode="popup", icon="popup",
                                  tip="Popup — a private message in game",
                                  aria="Send a popup to {player} on {server}"),
                 )),
    PlayerAction(key="mute", capability="players.mute", qualname="mute_player",
                 path="/actions/player/mute", label="Mute", icon="mute", when_muted=False,
                 tip="Mute — they can no longer send chat",
                 aria="Mute {player} on {server}",
                 hint="they can no longer send chat"),
    PlayerAction(key="unmute", capability="players.mute", qualname="unmute_player",
                 path="/actions/player/unmute", label="Unmute", icon="unmute", when_muted=True,
                 tip="Unmute — restore their chat",
                 aria="Unmute {player} on {server}",
                 hint="restore their chat"),
)


@dataclass(frozen=True, slots=True)
class NodeAction:
    """One write of the console that acts on a NODE row.

    Its own record rather than a third :class:`WriteAction`, because a node write states what a
    server write does not: the target is a NODE (in the body, as ``node``), the state gates are the
    node's MAINTENANCE state and the heartbeat's verdict rather than a ``Status`` member, and the
    dialog may carry an OPTION. Same discipline as its two siblings — data, not code: the route
    table, the capability map, the page control and the dialog are four readings of ONE declaration.

    ``states`` is the maintenance state(s) this control applies to (:data:`NODE_IN_SERVICE` /
    :data:`NODE_MAINTENANCE`), or empty for "every state" — the lifecycle trio changes a node's
    process, which maintenance does not make meaningless. It is the same kind of gate as
    ``WriteAction.statuses``: a control whose only possible answer is a refusal is not offered.

    ``heading`` is the dialog's own title, because the three-then-two operations do not all read well
    as "<label> <node>?" (``Take servers offline dcs-a-01?`` is not a sentence); the template's
    default is only a fallback.

    ``confirm`` says whether the ACTION's route requires the one-shot confirm token — i.e. whether the
    operation is destructive enough that a POST skipping its dialog must be refused. ``danger`` tints
    the row's control and its dialog button; the maintenance pair's two halves differ exactly there,
    and the mockup agrees (offline confirms, online does not).

    ``master_note`` is the one piece of copy that only the MASTER node's dialog carries (see
    :func:`node_confirm_context`): the console runs inside the master's process, so restarting or
    shutting it down kills the process serving the page. It applies to the LIFECYCLE trio only — the
    maintenance pair touches no service, so the master's row needs no such sentence (an empty
    ``master_note`` is how that is declared, never a special case in the template).
    """

    key: str
    capability: str
    qualname: str
    path: str
    label: str
    confirm: bool = True
    danger: bool = True
    #: record EVERY attempt at this action in the audit, including a refusal the ROUTE made before
    #: any action ran. For these five it is
    #: unconditional: taking a whole node — or every server on it — out of service is exactly the
    #: destructive class that ruling is about.
    audit_refusals: bool = True
    states: tuple[str, ...] = ()
    #: WHETHER THIS CONTROL IS GATED ON THE CACHED UPGRADE CHECK. Only the Upgrade
    #: control declares it. When True the control is offered **only while the cache says an update
    #: is pending** (``services.webservice.upgrade.pending``), the row says ``"no update check yet"``
    #: while the value is UNKNOWN, and the dialog reports when the value was last checked. The check
    #: itself is a node call and lives in the poller — a render NEVER issues it (see the module
    #: docstring of ``services/webservice/upgrade.py``).
    check: bool = False
    option: NodeOption | None = None
    heading: str = ""
    tip: str = ""
    aria: str = ""
    hint: str = ""
    warning: str = ""
    detail: str = ""
    master_note: str = ""
    go: str = ""
    go_title: str = ""


#: The NODE row's controls: **restart · shut down · upgrade** and the
#: maintenance pair **offline · online**.
#:
#: THE GLYPHS ARE SHARED WITH THE SERVER ROW, deliberately: ``restart`` and ``shutdown`` are the same
#: two operations one level up, and a second spelling of the same word is the drift this file exists
#: to prevent. ``upgrade`` is this row's own. The maintenance pair uses the NODE ROW's own reviewed
#: pair from the mockup's legend (``_icons.html``: the box with the arrow up, and the box with the
#: arrow down) — the mockup drew them for exactly these two words.
#:
#: WHAT EACH OPERATION MIRRORS (``plugins/admin/actions.py``): ``Node.restart()``, ``Node.shutdown()``
#: and ``Node.upgrade()`` for the trio, and ``take_node_offline``/``bring_node_online`` — the
#: ``server.maintenance`` pair — for offline/online.
#:
#: THE WORDS name the real consequence, per action and per target: every server on the node goes down
#: with a lifecycle operation, the numbers come from the RESOLVED node (never from the request), and a
#: node that is down cannot be started from a browser — which is the one honest thing the shutdown
#: copy has to say. The maintenance pair's words name the SERVER effect (maintenance, stop, start) and
#: never the node's power: the node's services, this console included, keep running.
#:
#: UPGRADE IS OFFERED ONLY WHILE THE NODE REPORTS AN UPDATE PENDING, read from the CACHED
#: background check. ``Node.upgrade_pending()`` is an async git/HTTP check on the local node and an
#: RPC on a remote one, so a page render must not ask it (``readmodels/nodes.py``: no RPC on render):
#: the poller in the webservice service asks instead and caches the answer, and this control carries
#: ``check=True`` so it is offered only while that cache says True (and the row says "no update check
#: yet" while the value is unknown). See the Upgrade entry's ``detail`` and
#: ``services/webservice/upgrade.py``.
NODE_ACTIONS: tuple[NodeAction, ...] = (
    NodeAction(key="restart", capability=NODE_RESTART_CAPABILITY, qualname="restart_node",
               path="/actions/node/restart", label="Restart",
               tip="Restart node — every server on {node} goes down while the node restarts",
               aria="Restart node {node}",
               hint="every server on the node goes down",
               warning="<b>Every server on {node} goes down with it</b> — {servers} server(s) and "
                       "{players} player(s) are on it right now.",
               detail="A node restart ends the bot process on that machine; the launcher there "
                      "brings the node back, and its servers do not: they stay down until somebody "
                      "starts them again.",
               master_note="<b>This console runs inside that node</b> — restarting it ends the "
                           "process serving this page, and this window has to be reloaded once the "
                           "node is back.",
               go="Restart node",
               go_title="Restart {node} — every server on it goes down"),
    NodeAction(key="shutdown", capability=NODE_SHUTDOWN_CAPABILITY, qualname="shutdown_node",
               path="/actions/node/shutdown", label="Shut down",
               tip="Shut down node — every server on {node} goes down and nothing brings it back",
               aria="Shut down node {node}",
               hint="every server on the node goes down, for good",
               warning="<b>{servers} server(s) on {node} go down and {players} player(s) are "
                       "disconnected, and nothing brings the node back.</b>",
               detail="A node shutdown ends the bot process on that machine for good: somebody has "
                      "to start the node THERE, at the machine. A node that is down cannot be "
                      "started from this browser, and its servers stay down with it.",
               master_note="<b>This console runs inside that node</b> — shutting it down ends the "
                           "process serving this page, and nothing brings it back until somebody "
                           "starts the node on that machine.",
               go="Shut down node",
               go_title="Shut down {node} — every server on it goes down"),
    NodeAction(key="upgrade", capability=NODE_UPGRADE_CAPABILITY, qualname="upgrade_node",
               path="/actions/node/upgrade", label="Upgrade", check=True,
               tip="Upgrade node — update DCSServerBot on {node} and restart it; every server on "
                   "it goes down",
               aria="Upgrade node {node}",
               hint="update and restart, every server goes down",
               warning="<b>Every server on {node} goes down with it</b> — {servers} server(s) and "
                       "{players} player(s) are on it right now.",
               detail="The node is updated and restarted. The console offers Upgrade ONLY while "
                      "the node itself reports an update pending — it asks every node in the "
                      "background, never while rendering a page, and this control is gone again "
                      "once there is nothing to upgrade.",
               master_note="<b>This console runs inside that node</b> — upgrading it ends the "
                           "process serving this page, and this window has to be reloaded once the "
                           "node is back.",
               go="Upgrade node",
               go_title="Upgrade {node} — every server on it goes down"),
    # ── the POWER pair ───────────────────────────────────
    # NOT a flag control any more. "offline" means THE SERVERS, never the node's own process (that is
    # *Shut down*, above): these two change the SERVERS the node carries and touch no service, so
    # this console survives an "offline" and can bring the node back. The words therefore name the
    # SERVER effect, never the node's power — the row's own ONLINE/OFFLINE tag is the heartbeat's
    # verdict and would otherwise contradict the control beside it.
    #
    # What each half is (``plugins/admin/actions.py`` for the code):
    # ``offline`` shuts the node's in-service servers down through the engine's popup chain and —
    # unless its ``maintenance`` option is cleared — marks them so a scheduled start cannot bring
    # them back; ``online`` reverts EXACTLY what that operation did: it clears only the flags the
    # operation set and starts only the servers it stopped. A flag set by hand is never touched in
    # either direction, which is why the flag's own controls live on the SERVER row.
    NodeAction(key="offline", capability=NODE_OFFLINE_CAPABILITY, qualname="take_node_offline",
               path="/actions/node/offline", label="Take servers offline",
               states=(NODE_POWER_OFF,),
               option=NodeOption(field="maintenance", label="Also mark them as maintenance",
                                 default=True,
                                 help="On by default: without it a scheduled start can bring the "
                                      "servers back while the node is \"offline\"."),
               heading="Take the servers on {node} offline?",
               tip="Take servers offline — every server on {node} that is up is marked as "
                   "maintenance and stopped; its players are disconnected, while the node's "
                   "services stay up",
               aria="Take the servers on {node} out of service",
               hint="marks the node's servers as maintenance and stops them",
               warning="<b>{servers} server(s) on {node} go down and {players} player(s) are "
                       "disconnected.</b> The box below is ticked by default and marks those servers "
                       "as maintenance — leave it ticked unless you mean to let a scheduled start "
                       "bring them back while the node is \"offline\".",
               detail="Only the SERVERS on the node change. The node itself keeps running, so its "
                      "services — including this console — stay up, and bringing the node back is a "
                      "job this browser CAN do. With the box ticked the servers are flagged as "
                      "maintenance before they stop, so they stay out of service until somebody "
                      "ends that; clear the box and no flag is written at all — the servers stop "
                      "and nothing keeps the scheduler from starting them again.",
               go="Take servers offline",
               go_title="Take {node} offline — {servers} server(s) go down, {players} player(s) "
                        "are disconnected"),
    # NO OPTION: bringing the servers back IS the operation. Clearing the
    # flags this node's power-off set and starting the servers it stopped is not a choice to offer —
    # it is what "online" means. There used to be a ``startup`` checkbox here, and it is exactly what
    # turned a flag toggle into a power operation; it is gone.
    NodeAction(key="online", capability=NODE_ONLINE_CAPABILITY, qualname="bring_node_online",
               path="/actions/node/online", label="Bring servers online",
               states=(NODE_POWER_ON,), confirm=False, danger=False,
               heading="Bring the servers on {node} back online?",
               tip="Bring servers online — clear the maintenance flags this node's power-off set "
                   "and start the servers it stopped; a flag set by hand is left alone",
               aria="Bring the servers on {node} back into service",
               hint="ends the maintenance this node's power-off set, and starts what it stopped",
               warning="",
               detail="This reverts EXACTLY the last power-off on this node: it clears only the "
                      "maintenance flags that operation set and starts only the servers it stopped. "
                      "A flag somebody set by hand is never cleared, and a server that is still in "
                      "maintenance is never started. If the bot has restarted since the power-off "
                      "the record is gone, and this then starts every server that is down and not "
                      "in maintenance, clearing no flag at all — the message says which of the two "
                      "happened. The node's services were never touched.",
               go="Bring servers online",
               go_title="Bring {node} online — the flags its power-off set are cleared, what it "
                        "stopped is started"),
)


def declare() -> None:
    """Declare every write capability. Idempotent for the same roles AND scope rule.

    ``scope_grants=True`` is expressed in the console's existing mechanism: a
    MANAGER (an identity whose resolved scope holds a server's ``managed_by``) may pause a mission —
    or message a player — ON THEIR OWN SERVERS, and the scope inside the seam holds them to exactly
    that. Node and instance writes stay Admin-only and must not be granted to managers; they are not
    in this map, and adding one means adding it here with the reason.

    Called at import AND from :func:`capabilities`, because the capability table is process state
    and a test fixture that snapshots/restores it can drop this declaration between two installs.
    """
    for action in SERVER_ACTIONS:
        permissions.declare_capability(action.capability, WRITE_ROLES, scope_grants=True)
    # THE MAINTENANCE PAIR IS DECLARED BY THE LOOP ABOVE, deliberately, and that IS §10.9's
    # decision made visible: ``servers.maintenance`` / ``servers.clear_maintenance`` carry the
    # console's server-write rule — ``Admin`` + ``DCS Admin`` WITH a manager's scope grant — which is
    # Discord's ``/scheduler maintenance`` / ``/scheduler clear`` (``DCS Admin``,
    # ``plugins/scheduler/commands.py``) widened by the console's own convention (``Admin`` holds
    # every server-row write here, §4.1) and narrowed back per server by the SCOPE. A manager may
    # flag their own server and nobody else's: the same predicate that hides the control
    # (``permissions.allows``) is the one the gate runs, and the scope is applied by
    # ``resolve_scoped_server``. NODE and INSTANCE writes stay Admin-only with NO scope grant
    # (:data:`NODE_ROLES`, below) — a manager's scope is a set of SERVERS and must never widen into
    # a node, which is why flagging a server is here and taking a node offline is not.
    for action in PLAYER_ACTIONS:
        permissions.declare_capability(action.capability, WRITE_ROLES, scope_grants=True)
    # the node writes: Admin only, and NO scope grant — see NODE_ROLES for why (a manager's scope is
    # a set of servers, and "one of mine runs there" must not become "let me stop that machine").
    for action in NODE_ACTIONS:
        permissions.declare_capability(action.capability, NODE_ROLES)
    # THE DCS CONFIG WRITE: ``("Admin",)`` WITH a scope grant, so a manager may reach it; a DCS Admin
    # is still refused (its scope is unscoped). The manager deny-list (``core.server_config``) is
    # applied inside the action, on top of this.
    for action in SERVER_CONFIG_ACTIONS:
        permissions.declare_capability(action.capability, NODE_ROLES, scope_grants=True)
    # THE CHANNELS WRITE: its OWN capability, Admin only, NO scope grant — a manager may not write the
    # channels at all. The deny-list also names the channels, so the page OMITS the whole card and
    # the action re-checks a direct caller.
    for action in SERVER_CHANNEL_ACTIONS:
        permissions.declare_capability(action.capability, NODE_ROLES)
    # THE COALITION WRITE: ``("Admin",)`` WITH a scope grant, so a manager reaches the coalition
    # passwords of THEIR OWN servers — the same rule the DCS config write carries, and the same
    # refusal a foreign server meets (``resolve_scoped_server``'s bare 403). The action's own
    # ``_coalition_authorised`` re-checks it server-side and additionally refuses the REST/MCP
    # transports, which do not reach a console page.
    for action in SERVER_COALITION_ACTIONS:
        permissions.declare_capability(action.capability, NODE_ROLES, scope_grants=True)
    # THE MISSION-LIST WRITES: ``WRITE_ROLES`` WITH a scope grant — the console's server-write rule,
    # so a manager adds / removes / loads / uploads missions on THEIR OWN servers and nobody else's.
    for action in MISSION_ACTIONS:
        permissions.declare_capability(action.capability, WRITE_ROLES, scope_grants=True)


declare()


def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers (one literal path each).

    An action with a DIALOG (``WriteAction.dialog``: it confirms OR carries an option) contributes
    TWO paths — its own POST and the POST that renders its dialog — and both declare the SAME
    capability: opening a dialog is not a lesser right than performing the action, and the dialog's
    own refusal would be unusable if the door to it were narrower.
    """
    declare()
    capabilities_ = {action.path: action.capability for action in SERVER_ACTIONS}
    capabilities_.update({action.path + CONFIRM_SUFFIX: action.capability
                          for action in SERVER_ACTIONS if action.dialog})
    capabilities_.update({action.path: action.capability for action in PLAYER_ACTIONS})
    capabilities_.update({action.path + CONFIRM_SUFFIX: action.capability
                          for action in PLAYER_ACTIONS if action.confirm})
    # the node writes contribute both of their paths, exactly like the two surfaces above: opening a
    # dialog is not a lesser right than performing the action. The dialog path is declared for EVERY
    # node action — the maintenance pair's two halves both collect their option there, even though only
    # ``offline`` requires the one-shot token (``confirm``).
    capabilities_.update({action.path: action.capability for action in NODE_ACTIONS})
    capabilities_.update({action.path + CONFIRM_SUFFIX: action.capability
                          for action in NODE_ACTIONS})
    # THE DCS CONFIG WRITE and THE CHANNELS WRITE: one path each, no dialog (each posts its whole
    # form directly).
    capabilities_.update({action.path: action.capability for action in SERVER_CONFIG_ACTIONS})
    capabilities_.update({action.path: action.capability for action in SERVER_CHANNEL_ACTIONS})
    capabilities_.update({action.path: action.capability for action in SERVER_COALITION_ACTIONS})
    # THE MISSION-LIST WRITES: each posts its own form/body directly; the ADD and REMOVE controls
    # additionally contribute their DIALOG path (``MISSION_DIALOG_KEYS``), so opening one is gated by
    # the SAME capability as performing it — a dialog is not a lesser right than the write.
    capabilities_.update({action.path: action.capability for action in MISSION_ACTIONS})
    capabilities_.update({action.path + CONFIRM_SUFFIX: action.capability
                          for action in MISSION_ACTIONS if action.key in MISSION_DIALOG_KEYS})
    return capabilities_


# --------------------------------------------------------------------------------- the controls

def state_of(value: Any) -> str:
    """The state a control's ``statuses`` is matched against: the ``Status`` member's NAME when the
    raw value names one, else the folded word the page shows (``UNKNOWN`` for anything unmapped).

    It is NOT ``status_view(...).word``, and that is the whole point: the read model deliberately
    folds ``Shutting down`` and ``Unregistered`` onto the SHUTDOWN word so a reader acts on four
    words (``readmodels/model.py``), but neither is the state a Startup applies to —
    ``startup_server`` guards on ``Status.SHUTDOWN`` itself. A control whose only possible answer is
    the action's own state refusal is not offered, and a row between two states gets no
    control at all. ``Status`` is the authority: when the mockup's state table and the enum disagree,
    the enum wins.
    """
    raw = (readmodels.status_view(value).raw or "").strip().lower()
    for member in Status:
        if member.value.lower() == raw:
            return member.name
    return readmodels.status_view(value).word


# --------------------------------------------------------- the control that is RUNNING

def target_key(kind: str, name: str | None = "") -> str:
    """The in-flight GUARD's key for a ``(kind, name)`` target.

    A thin re-statement of ``core/actions._normalise_target``'s key shape — strip, case-fold,
    prefix the KIND (``server:<folded name>`` / ``node:<folded name>``) — kept HERE because the
    console must not reach for a private name in the seam. It is what lets a page ask the guard's
    OWN set (:func:`target_is_busy`) about a row it is rendering, so the pulse a person sees and the
    refusal a second press meets are ONE fact, never two that disagree.

    It is pinned to the guard by ``tests/test_webui_busy_control.py``, which puts a target in flight
    through the REAL :func:`core.actions.call_action` and asserts the rendered control goes busy: if
    the guard's key shape ever moves, that pin fails rather than the pulse silently never lighting.
    """
    return f"{kind}:{str(name or '').strip().casefold()}"


def target_is_busy(kind: str, name: str | None, running: frozenset[str] | None = None) -> bool:
    """Whether an action is running on the ``(kind, name)`` target RIGHT NOW.

    Read from the seam's own in-flight set (``core.actions.in_flight_targets``), and — the scope
    rule this must not break — the console only ever asks about a target it is ALREADY
    rendering a row for. There is no list of in-flight targets on any page and no count, so a
    viewer who cannot see a server cannot learn from any rendered output that it is mid-action.
    ``running`` is an already-read snapshot, so a page reads the set once for the whole table.
    """
    return target_key(kind, name) in (in_flight_targets() if running is None else running)


# ------------------------------------- the control that is AWAITING A CHANGE

def _row_state(server: Any) -> str:
    """The state ONE row's control matches on — the SAME reading :func:`server_controls` uses.

    Kept as one function so the state a write is SUBMITTED under and the state a later render
    COMPARES against cannot drift: both call this, both get :func:`state_of` of the server's own
    ``status`` (the ``Status`` member's name, never the folded word).
    """
    return state_of(readmodels.safe(lambda: getattr(server, "status", None)))


def awaiting_store() -> dict:
    """A SNAPSHOT of the pending expectations, keyed by target — never the live mapping.

    A COPY, taken behind the store's lock, so a reader can walk the whole store (the tests reach an
    entry's stamp this way) without holding the lock a route writes it under, and without being able
    to mutate the store through the dict it was handed.
    """
    with _AWAIT_LOCK:
        return {key: dict(entry) for key, entry in _AWAIT.items()}


def reset_awaiting_changes() -> None:
    """Drop every pending expectation — PROCESS state's test seam, this store's ``reset_in_flight``.

    ``tests/conftest.py`` clears it after every test for the same reason ``reset_in_flight`` exists:
    an expectation left by one test is a pulse some later render never asked for.
    """
    with _AWAIT_LOCK:
        _AWAIT.clear()


def _observable_value(action: WriteAction, state: str, flagged: bool, revision: str = "") -> Any:
    """The current value of the observable *action* MOVES — the signal whose move ends its pulse.

    The ONE translation from the declaration's ``observable`` to a value a later render can compare:
    the maintenance flag for the flag pair (a boolean, so ``False`` is a real
    value and not an absent one), the CONFIG REVISION for the config write (``revision``), the row's
    state for everything else. Kept beside the store that records it and the read that spends it, so a
    third observable is one branch in ONE place.
    """
    observable = getattr(action, "observable", AWAIT_OBSERVABLE_STATUS)
    if observable == AWAIT_OBSERVABLE_MAINTENANCE:
        return bool(flagged)
    if observable == AWAIT_OBSERVABLE_CONFIG:
        return str(revision)
    return str(state)


def remember_awaiting_change(kind: str, name: str | None, action: WriteAction,
                             state: str, flagged: bool, *, revision: str = "") -> None:
    """Record that *action*'s write on ``(kind, name)`` is ON ITS WAY, and what its observable read.

    WRITTEN BEFORE THE ACTION RUNS: the route calls this once the target is resolved and
    the observable has been read, and only then calls the action — so the fresh render the browser
    lands on (issued at the same moment as the POST) already finds the expectation, with no cookie
    round-trip in the way. A write that then FAILS or is REFUSED drops it again
    (:func:`forget_awaiting_change`), so a failed start never pulses while the notice says it failed.

    THIS is the pulse's authority — "someone started something through THIS control and the glyph it
    moves has not swapped yet" — held PROCESS-side, keyed by target (see the store's note above for
    why it left the session). IT NAMES THE ACTION: the seam's in-flight guard
    keys only the TARGET and cannot say WHICH control was pressed, but this record is written by the
    ROUTE, which knows exactly which action it is running, so :func:`awaiting_change` answers ``True``
    only for that same control — pressing *Maintenance* must not blink *Startup*.

    IT NAMES THE OBSERVABLE TOO: ``action.observable`` decides whether the
    expectation ends on the row's status or on the maintenance flag, and the value that observable
    holds AT SUBMISSION is stored beside it. The flag pair therefore spends its expectation on the
    very next render (the flag lands before the response returns), never a 120-second pulse re-armed
    by every further press.

    A SECOND PRESS REPLACES the entry — the key is the TARGET, so there is exactly one expectation
    per target and the newest press owns it (never stacking, never re-arming a spent one: a spent
    entry is dropped by :func:`awaiting_change` the moment it is read). An empty/unreadable target
    records nothing. The store is bounded by count (:data:`AWAIT_MAX_PENDING`), oldest dropped first,
    so a long-lived process cannot grow it without limit.
    """
    if not str(name or "").strip():
        return
    key = target_key(kind, name)
    entry = {"action": str(getattr(action, "key", "")),
             "observable": getattr(action, "observable", AWAIT_OBSERVABLE_STATUS),
             "value": _observable_value(action, state, flagged, revision),
             # the state the write works TOWARD: the read spends the expectation when the
             # row reads one of these AND has left the submitted state — see :func:`awaiting_change`.
             # Empty for an action that names no target state ("any change" ends it).
             "settled": tuple(getattr(action, "settled", ()) or ()),
             "departed": False,
             "at": time.monotonic()}
    with _AWAIT_LOCK:
        _AWAIT[key] = entry
        if len(_AWAIT) > AWAIT_MAX_PENDING:
            for stale in list(_AWAIT)[:-AWAIT_MAX_PENDING]:
                _AWAIT.pop(stale, None)


def forget_awaiting_change(kind: str, name: str | None) -> None:
    """Drop the expectation for ``(kind, name)`` — a write that FAILED or was REFUSED.

    A failed start must not pulse for two minutes while the notice says it failed, so the
    route forgets the expectation at the same moment it remembers the failure notice. Removing an
    absent key is a no-op.
    """
    with _AWAIT_LOCK:
        _AWAIT.pop(target_key(kind, name), None)


def _evaluate(entry: dict, state: str, flagged: bool, revision: str = "") -> str:
    """Whether a stored expectation has ENDED against the row's current observable — ``"end"``/``"pending"``.

    The ONE place the end condition lives, read by both :func:`awaiting_change` (does THIS
    control pulse?) and :func:`awaiting_action` (which control is pending at all?). Three shapes:

    * the flag observable (``AWAIT_OBSERVABLE_MAINTENANCE``) — ENDED as soon as the boolean differs
      from the value it held at submission;
    * a STATUS observable WITH a settled set — ENDED when the row reads one of the settled
      states AND it has LEFT the state it was submitted in. The ``departed`` latch is what makes a
      ``restart`` work: it is SUBMITTED in a settled state (``RUNNING``), so the bare
      ``state in settled`` test would spend it on the first render; only once the row has been seen
      in a NON-settled state (``LOADING``, ``STOPPED``) does the return to ``RUNNING`` end it. A
      non-settled state does NOT end it: ``LOADING`` is the wait, not the finish. The latch is
      persisted on the entry, so the two renders that bracket the boot agree.
    * a STATUS observable with NO settled set — the rule kept for an action that names no target
      state: any change ends it.

    It MUTATES the entry it is given (the ``departed`` latch), which is why it is called under the
    store's lock by both readers.
    """
    if entry.get("observable") == AWAIT_OBSERVABLE_MAINTENANCE:
        return "end" if bool(flagged) != entry.get("value") else "pending"
    if entry.get("observable") == AWAIT_OBSERVABLE_CONFIG:
        # the CONFIG REVISION moved (the settings write and/or the servers.yaml override landed):
        # the pulse is SPENT. A revision that has not moved keeps waiting, under the ceiling.
        return "end" if str(revision) != str(entry.get("value")) else "pending"
    now_state = str(state)
    settled = tuple(entry.get("settled") or ())
    if not settled:                                   # no declared target: any change ends it
        return "end" if now_state != str(entry.get("value")) else "pending"
    if now_state in settled:
        # settled — but a restart is submitted IN one, so require that the row left first
        if entry.get("departed") or now_state != str(entry.get("value")):
            return "end"
        return "pending"
    entry["departed"] = True                          # transitional (LOADING/STOPPED): keep waiting
    return "pending"


def _pending_key_locked(key: str, state: str, flagged: bool, now: float | None,
                        revision: str = "") -> str | None:
    """The action key of the pending expectation at *key*, evaluated against the row — or ``None``.

    Runs the ceiling, the end condition and the drop in ONE place, under the store's lock, so a spent
    entry is removed WHOEVER reads it and the two public readers cannot drift. Returns the
    pending action's KEY (never a bool), because the caller needs to know WHICH control is pending
    in order to keep it on screen while the state does not match its own gate.
    """
    entry = _AWAIT.get(key)
    if not isinstance(entry, dict):
        return None
    if _evaluate(entry, state, flagged, revision) == "end":
        _AWAIT.pop(key, None)     # the glyph swapped: SPENT — drop it, so it can never re-arm
        return None
    try:
        stamped = float(entry.get("at", 0.0))
    except (TypeError, ValueError):
        _AWAIT.pop(key, None)
        return None
    moment = time.monotonic() if now is None else now
    if (moment - stamped) >= AWAIT_CHANGE_SECONDS:
        _AWAIT.pop(key, None)     # the ceiling: a start that never came up must not pulse forever
        return None
    return str(entry.get("action") or "") or None


def awaiting_change(kind: str, name: str | None, action_key: str,
                    state: str, flagged: bool, *, now: float | None = None,
                    revision: str = "") -> bool:
    """Whether the write by the control *action_key* on ``(kind, name)`` is still waiting for its glyph.

    ``True`` while ALL of these hold:

    * an expectation exists for this target (a write was issued and has not ended), and
    * the entry was submitted by THIS control — ``action_key`` is the key the record carries, so a
      SIBLING control of the same row never pulses for an action it did not run, and
    * the observable THAT ACTION MOVES has not reached the END its action declares — the row's status
      reaching the action's ``settled`` state for a power action, the maintenance flag
      moving for ``servers.maintenance`` / ``servers.clear_maintenance`` — see
      :func:`_evaluate`, and
    * the expectation is younger than :data:`AWAIT_CHANGE_SECONDS` (the ceiling that keeps a start
      that never comes up from pulsing forever).
    """
    with _AWAIT_LOCK:
        pending = _pending_key_locked(target_key(kind, name), state, flagged, now, revision)
    # still pending: only the control the record NAMES pulses (a sibling never does)
    return pending is not None and pending == str(action_key)


def awaiting_action(kind: str, name: str | None, state: str, flagged: bool, *,
                    now: float | None = None, revision: str = "") -> str | None:
    """The action KEY whose write on ``(kind, name)`` is still pending against the row — or ``None``.

    Like every read of the store it is consulted only for a server already in the caller's scoped
    source — it yields a property of a row the caller is rendering, never a list of busy targets.
    """
    with _AWAIT_LOCK:
        return _pending_key_locked(target_key(kind, name), state, flagged, now, revision)


# ------------------------------------- the SERVERS a NODE action will move

#: The SERVER action a node power operation's per-server effect takes its semantics from. The node
#: route describes each affected server through the SERVER_ACTIONS record — the observable and the
#: settled state it declares — so the seeding has NO second rule engine: it reads the very declaration
#: the server row reads. ``offline`` stops (``shutdown``: settled at SHUTDOWN/STOPPED), ``online``
#: starts (``startup``: settled at RUNNING/PAUSED).
NODE_MOVE_ACTIONS: dict[str, str] = {"offline": "shutdown", "online": "startup"}


def _server_action(key: str) -> WriteAction:
    """The :class:`WriteAction` a SERVER control KEY names — the observable/settled the seed borrows."""
    return next(action for action in SERVER_ACTIONS if action.key == key)


def node_moved_names(request: Request, node_name: str,
                     action: NodeAction, record: Any = None) -> list[str]:
    """Seed the expectation on the SERVERS a node power operation WILL move.

    ``record`` is the engine's power-off RECORD (``core.data.maintenance.power_record``), which the
    CALLER reads BEFORE the action runs and passes here — *because the action CLEARS it as it reverts
    the power-off*: a read taken after the action would find nothing and mistake a recorded *online*
    for the no-record fallback. It is used only for ``online``; the caller passes ``None`` elsewhere.

    WHICH SERVERS — the PREDICTION RULE, stated in full:

    * **``offline``** ("Take servers offline") will STOP the servers that are IN SERVICE — not
      ``SHUTDOWN`` and not ``UNREGISTERED``, the engine's OWN test
      (:attr:`core.data.maintenance.ServerMaintenanceManager.in_service`), the same fact a per-server
      write would be about. A server already down is not in service and the operation skips it.
    * **``online``** ("Bring servers online") will START the servers the last power-off STOPPED, as the
      power-off RECORD names them (*record* above — the SAME record the node row's power gate reads).
      With NO record the operation falls back to its RULE ("every server that is down and not in
      maintenance", the online copy's own words), so the prediction is that same set. In both cases the
      engine applies ``skip_running`` and ``skip_flagged``: a server already up, or still carrying a
      flag the operation does not own, is NOT started — so it is not seeded either.

    IT IS A PREDICTION, never authority. The action re-resolves its servers from the CALLER's view at
    run time; if the bot ends up touching a server the console did not predict, that row only LOSES a
    pulse (never the reverse — a row is seeded only when the action's OWN semantics say it will move),
    and the ceiling still ends anything the operation turns out not to finish. A server whose
    observable ALREADY equals the SETTLED state the action declares is not seeded at all — nothing is
    pending for it, e.g. a server already up when "bring online" runs or one already SHUTDOWN/STOPPED
    when "take offline" runs.

    SCOPED, exactly as the rows are: the servers are enumerated from
    :func:`services.webservice.pages.dashboard.request_source` — the SAME scoped source the page
    renders from (the non-disclosure rule) — matched on the node's own name. A node a caller
    cannot see carries no server in their view, so nothing is seeded for them, and only the actor sees
    these expectations at all. Seeding the SAME server twice REPLACES its entry (the key is the
    target), exactly as the per-server path already does.

    Returns the names seeded, in name order, so a caller may report or test what it predicted; the
    store remains the authority.
    """
    server_key = NODE_MOVE_ACTIONS.get(action.key)
    if server_key is None:
        return []            # the lifecycle trio touches servers only as a side effect of the node's
                             # own process; only the POWER pair moves servers as its operation
    source = dashboard_page.request_source(request)
    wanted = readmodels.text(node_name)
    servers = [server for server in (getattr(source, "servers", ()) or ())
               if readmodels.text(readmodels.safe(
                   lambda: getattr(getattr(server, "node", None), "name", ""))) == wanted]
    servers.sort(key=lambda server: readmodels.text(
        readmodels.safe(lambda: getattr(server, "name", ""))))
    server_action = _server_action(server_key)
    settled = tuple(getattr(server_action, "settled", ()) or ())
    recorded = {str(name).casefold() for name in (getattr(record, "stopped", None) or ())} \
        if record is not None else None
    seeded: list[str] = []
    for server in servers:
        name = readmodels.text(readmodels.safe(lambda: getattr(server, "name", "")))
        if not name:
            continue
        if action.key == "offline":
            # the servers the operation will STOP: in service (the engine's own test). Anything down
            # is skipped by the operation and must not pulse.
            if not ServerMaintenanceManager.in_service(server):
                continue
        else:
            # the servers the operation will START: down (not in service) and NOT flagged, and — when
            # a record exists — only the ones it stopped. This is ``power_on``'s own filter.
            if ServerMaintenanceManager.in_service(server):
                continue
            if bool(readmodels.safe(lambda: getattr(server, "maintenance", False), False)):
                continue
            if recorded is not None and name.casefold() not in recorded:
                continue
        state = _row_state(server)
        if settled and state in settled:
            continue         # nothing is pending for it: the observable already reads the settled state
        remember_awaiting_change("server", name, server_action, state, False)
        seeded.append(name)
    return seeded


def server_controls(request: Request, origin: str) -> dict[str, dict]:
    """The row STRIP each server row may render, keyed by server name — and nothing for a row whose
    controls may not be offered.

    ``origin`` is the PAGE this strip is being rendered on (its own path constant), and it travels
    into every control as a hidden field so the write can return there: the
    Dashboard (``/``) and ``/servers`` render the SAME strip, so a Startup pressed on either must
    keep the person on the page they were on. The route re-checks it against the registry — this
    value is a rendering aid, never the authority (see :func:`origin_path`).

    Three conditions, each read from the ONE place that decides it; a control is
    OMITTED — never rendered disabled — when any of them is false:

    * **the capability**: ``permissions.allows``, the same predicate the access gate runs, so
      ``offered ⊆ authorised`` holds by construction;
    * **the scope**: the servers come from :func:`services.webservice.pages.dashboard.request_source`
      — the SCOPED source every page renders from, which is the ONE owner of "which servers are in
      this caller's view" (pinned by ``tests/test_webui_scope_view.py``). A server outside the
      caller's scope is not in the source, so no control is built for it at all — and a crafted POST
      naming it is refused by ``resolve_scoped_server`` with the same bare 403 as a name that
      does not exist;
    * **the action**: ``action_available`` — an installation whose ``mission`` plugin is not loaded
      offers no button that could only answer a refusal.

    ``statuses`` then decides WHICH controls (Startup on a SHUTDOWN server, Pause on a RUNNING one)
    for the same reason: a button whose only answer is a state refusal is not offered. The state is
    read by :func:`state_of` — the ``Status`` member's name, not the folded word — so
    ``SHUTTING_DOWN`` and ``UNREGISTERED`` (both rendered as SHUTDOWN) offer nothing.

    Each control ALSO carries ``busy`` — and it is the EXPECTATION half of the busy rule and NOTHING
    ELSE: ``True`` only while THIS session issued a write by
    THAT control (the record names the action's key) and the observable the action moves has not
    moved yet — the row's status for a power action, the maintenance flag for the flag
    pair — bounded by :data:`AWAIT_CHANGE_SECONDS`. So pressing *Maintenance*
    makes the *Maintenance* glyph pulse and leaves *Startup* alone, and vice versa. The record is the
    ACTOR's own expectation, held in the session: a second viewer of the same server never sees it —
    see :func:`remember_awaiting_change` for why that is the honest reading rather than a disclosure
    the scope rule would forbid.

    The seam's IN-FLIGHT half (:func:`target_is_busy`) is NOT stamped on a control any more. It keys
    the TARGET, so it cannot say which action is running, and stamping every offered control of the
    row with it is exactly the defect (a *Startup* that blinks when *Maintenance*
    was pressed). It travels instead as ``in_flight`` on the ROW record — a marker on the strip
    container, never a pulsing glyph (``templates/_strip.html``). It is still consulted only for a
    server already in ``source``, so it remains a property of a row the caller can see and never a
    channel that could disclose one they cannot.

    The value is ONE record per row, because the row is ONE component at two widths
    (``templates/_strip.html``): ``strip`` are the icons on the row, ``menu`` the controls the row's
    overflow trigger opens, and ``wide`` says whether there is a trigger at all. A row whose single
    applicable control lives in the menu keeps that control's GLYPH rather than paying a click for a
    one-item menu — so a STOPPED row shows the Start glyph and no trigger.
    """
    source = dashboard_page.request_source(request)
    roles = permissions.role_names_for(request)
    manager = permissions.manages_console(request)
    token = session.get_csrf_token(request)
    # the seam's own in-flight set, read ONCE for the whole table. It is consulted only
    # for a server already in ``source`` — the caller's SCOPED view — so the pulse can never be a
    # second list of busy targets that discloses a row the caller cannot see; it is a property of a
    # row they are looking at.
    running = in_flight_targets()
    controls: dict[str, dict] = {}
    for index, server in enumerate(getattr(source, "servers", ()) or ()):
        name = readmodels.text(readmodels.safe(lambda: getattr(server, "name", "")))
        if not name:
            continue
        state = _row_state(server)
        # the flag pair's own per-row state: read from the SAME scoped object the row is built
        # from, exactly as ``_muted_state`` reads a player's mute flag. Unreadable counts as NOT
        # flagged — which offers *Maintenance*; the action's own "already in maintenance mode" guard
        # is the authority a stale page meets, and a wrong guess costs one typed refusal.
        flagged = bool(readmodels.safe(lambda: getattr(server, "maintenance", False), False))
        offered = tuple(action for action in SERVER_ACTIONS
                        if (not action.statuses or state in action.statuses)
                        and (action.when_maintenance is None
                             or action.when_maintenance == flagged)
                        and permissions.allows(action.capability, roles, manager=manager)
                        and action_available(action.qualname))
        # WHICH control is pending for this row: read ONCE through the SAME ceiling and
        # end rule as each control's own read (both call ``_pending_key_locked``), so the row and the
        # controls can never disagree about what is running.
        pending = awaiting_action("server", name, state, flagged)
        # busy is the EXPECTATION half ONLY: this session issued a write by THIS
        # control and the observable it moves has not reached the end its action declares.
        # The record names the action, so ONE control pulses — the one
        # that was pressed — not stamped across the row's whole strip. It is the actor's own view,
        # held PROCESS-side, and bounded by AWAIT_CHANGE_SECONDS.
        #
        # The seam's in-flight half does NOT live here: it keys the TARGET and cannot name the
        # action, so it must not light a glyph (that is the defect). It is reported ONCE per row, as
        # a marker on the strip container — see ``in_flight`` in the record below and
        # ``templates/_strip.html``.
        in_flight = target_is_busy("server", name, running)
        # A CONTROL WHOSE OPERATION IS PENDING STAYS ON THE ROW. This is what makes
        # the pulse survive the STARTING state: a real DCS launch reads ``LOADING`` for most of the
        # boot, and NO control is gated on ``LOADING`` — so without this the row renders nothing and
        # there is no glyph to pulse. While the row is
        # transitional and a start/restart is pending, the pending control's OWN glyph is rendered,
        # busy: the honest affordance is "this server is starting". It is DISABLED — not an
        # invitation — because a second start through the UI is meaningless while the first boots.
        stuck = _pending_row_control(pending, offered, name, token, origin, roles, manager)
        if not offered and stuck is None:
            continue
        strip = tuple(_control(action, name, token, origin, action.key == pending)
                      for action in offered if action.row == ROW_STRIP)
        menu = tuple(_control(action, name, token, origin, action.key == pending)
                     for action in offered if action.row == ROW_MENU)
        if len(offered) == 1 and not strip:
            strip, menu = menu, ()
        if stuck is not None:
            strip = (stuck,) + strip
        controls[name] = {"strip": strip, "menu": menu, "wide": bool(strip) and bool(menu),
                          "menu_id": f"act-menu-{index}", "server": name,
                          "in_flight": in_flight,
                          "aria": f"More controls for {name}",
                          "menu_note": MENU_NOTE}
    return controls


def _pending_row_control(pending: str | None, offered: tuple[WriteAction, ...], name: str,
                         token: str, origin: str, roles: frozenset[str], manager: bool) -> dict | None:
    """The busy, DISABLED control a row shows for a pending action its own gate no longer matches.

    ``pending`` is the action key :func:`awaiting_action` read for this row; it
    returns ``None`` when there is nothing pending, when the action is ALREADY among ``offered`` (so
    the normal path renders it, precision-pulsed — never a second copy), or when it is not a SERVER
    power action the caller may run. The clipped gate (``observable == AWAIT_OBSERVABLE_STATUS``)
    keeps this to the server row's power controls — the node and player rows never reach it — and a
    pending action the caller is not authorised for renders nothing, so ``offered ⊆ authorised`` still
    holds. The returned control is ``busy`` (it pulses) and ``disabled`` (it is not an invitation).
    """
    if not pending or any(action.key == pending for action in offered):
        return None
    action = next((candidate for candidate in SERVER_ACTIONS
                   if candidate.key == pending
                   and candidate.observable == AWAIT_OBSERVABLE_STATUS
                   and permissions.allows(candidate.capability, roles, manager=manager)
                   and action_available(candidate.qualname)), None)
    if action is None:
        return None
    control = _control(action, name, token, origin, busy=True)
    control["disabled"] = True
    return control


def _control(action: WriteAction, name: str, token: str, origin: str, busy: bool = False) -> dict:
    """One rendered control, as data: where it posts, its glyph, its two names, and the target.

    The target travels in the request BODY, never in a URL: there is no path or query
    parameter a person can edit to point an action at another server.

    The ORIGIN travels in the body too, beside the target: the page this control
    is rendered on, so the route can 303 back to it rather than to the section's own page. It is a
    hidden field and not a URL parameter for the same reason the target is — and the route accepts it
    only by lookup against the registry (:func:`origin_path`), so it is a candidate, not a choice.

    A CONFIRM-REQUIRED control posts to its DIALOG's path, not to the action's (see
    :data:`CONFIRM_SUFFIX`): the action's own route refuses a POST that did not come through the
    dialog, so the row could not reach it directly even if it wanted to. A control that merely
    CARRIES AN OPTION does too (:attr:`WriteAction.dialog`): the checkbox is chosen on the
    dialog, so the row posts there to collect it.

    ``busy`` is the busy truth for THIS control's own action, read at render time: ``True``
    only when THIS session issued a write THROUGH THIS CONTROL and the observable that action moves has
    not moved yet (the glyph has not swapped), bounded by :data:`AWAIT_CHANGE_SECONDS`. The record
    carries it, the strip renders ``aria-busy`` and the animating class from it, and it clears by
    itself when the observable moves or the ceiling is reached. The seam's in-flight half is NOT here:
    it cannot name the action, so it travels as the ROW marker (``server_controls``'s ``in_flight``).

    WHY THE BUSY SENTENCE NAMES NO ACTION. The sentence is the ROW's fact — "an action on this server
    is running" — because the guard keys the TARGET, not the operation, and its own refusal says
    "Another action on SRS-1 is still running". The control still names ITSELF through ``action.label``
    (the title/aria are prefixed with the label), so what the sentence claims is true of the row while
    what it is ABOUT is the control the person pressed. That is the honest reading of a signal whose
    only authority knows the target: it never claims a sibling performed an operation it did not.
    """
    title = action.tip.format(server=name)
    aria = action.aria.format(server=name)
    if busy:
        title = f"{action.label} — an action on {name} is running; this can take a moment"
        aria = f"{action.label} — an action on {name} is running"
    return {"path": action.path + CONFIRM_SUFFIX if action.dialog else action.path,
            "label": action.label, "icon": action.key, "title": title,
            "aria": aria, "hint": action.hint,
            "danger": action.danger, "server": name, "busy": busy,
            "direct": not action.dialog,
            # the transitional busy control a row keeps for a pending action is DISABLED —
            # it is a statement ("this server is starting"), not an invitation to press it again. Every
            # other control leaves this False and stays a real, focusable button (the seam's guard, not
            # a disabled attribute, is what refuses a second press).
            "disabled": False,
            "hidden": (("server", name), (ORIGIN_FIELD, origin)), "buttons": (),
            "field": "", "field_label": "", "field_max": 0, "field_help": "", "field_id": "",
            "csrf_field": session.CSRF_FIELD, "csrf_token": token}


def node_state(source, node_name: str) -> str:
    """The MAINTENANCE state of *node_name* in the caller's view: ``in-service`` / ``maintenance`` / ``""``.

    Read from the SAME scoped source the rows render from — ``source.servers``, the caller's own view
    — so a state can never describe a server the caller cannot see. It is NOT the row's ONLINE/OFFLINE
    tag: that comes from ``readmodels/nodes`` and is the HEARTBEAT's verdict (``node is not None``),
    while this reads the servers' own ``maintenance`` flag, which ``/node offline`` sets and
    ``/node online`` clears (``plugins/admin/actions.py``). The two can disagree in both directions —
    a heartbeating node whose servers are all under maintenance, and a node the heartbeat cannot reach
    whose servers are still listed.

    THREE answers, one of them deliberate:

    * ``maintenance`` — at least ONE server on the node is under maintenance. "Any" rather than "all"
      because a partial state is exactly the state an operator must be able to leave: ``/node offline``
      marks a node's servers as they are at that moment, and a server added or restarted later would
      otherwise strand the row on "offline" forever with no way back from the console;
    * ``in-service`` — the node carries servers and none of them is under maintenance;
    * ``""`` — the node carries NO server at all, in the caller's view. There is then nothing to take
      out of service and nothing to bring back, so NEITHER half of the pair is offered: a control whose
      only possible effect is nothing is not a control.

    Anything unreadable counts as not-under-maintenance: a broken attribute must cost a state, never
    the page.
    """
    found = False
    for server in getattr(source, "servers", ()) or ():
        name = readmodels.text(readmodels.safe(
            lambda: getattr(getattr(server, "node", None), "name", "")))
        if name != node_name:
            continue
        found = True
        if bool(readmodels.safe(lambda: getattr(server, "maintenance", False), False)):
            return NODE_MAINTENANCE
    return NODE_IN_SERVICE if found else ""


def node_power_states(source, node_name: str) -> frozenset[str]:
    """The POWER state of *node_name*, in the caller's view — what the power pair is offered on.

    THE NODE'S OWN STATE DECIDES, not what its servers happen to be doing. Asked
    in this order:

    * **the heartbeat** — ``source.nodes[node_name] is None`` means the cluster cannot reach the
      node, and it is in NEITHER state. The tag :func:`readmodels.nodes` renders is this same verdict,
      and a row the cluster cannot reach is offered no control at all by :func:`node_controls`; the
      check is here so this function answers correctly on its own;
    * **at least one server in the caller's view** — the scoped view the row counts. A node with no
      server is in neither state: a control whose only possible report is "0 server(s)" is not a
      control.

    Given both, the POWER-OFF RECORD alone decides which half is offered — the engine's own
    ``core.data.maintenance.power_record`` for the operation that took the servers down:

    * **no record** — :data:`NODE_POWER_OFF` only: *Take servers offline*. The node is IN SERVICE, so
      the operation is available whether or not anything currently runs. (The old gate added *offline*
      only while some server's process was up and *online* while one was down and unflagged, so an
      ONLINE node with every server stopped offered the online half and never the offline one —
      inverted from the node's own state);
    * **a record exists** — :data:`NODE_POWER_ON` only: *Bring servers online*, the way back to what
      the operation actually took down (the flags it set and the servers it stopped).

    DELIBERATE CONSEQUENCE, so the next reader knows this is a choice and not an oversight: there is
    NO "some server is down" arm any more, and §5(a) keeps the record IN MEMORY. *After a bot
    restart* the bring-online control is therefore NOT offered on a heartbeating node that has no
    record — even when its servers are down. Pressing *Take servers offline* re-creates a record
    (a no-op stop for the servers already down) and the way back appears. Re-deriving "something is
    down, therefore this is a power-on" is exactly the arm that produced the defect.

    The RECORD is the engine's (``core.data.maintenance.power_record``), read here rather than in a
    read model because it is PROCESS state and not a figure about the cluster: it is what makes the
    console's two halves of ONE operation two HTTP requests. It is read by KEY and never awaited, so
    the render path keeps its no-RPC rule.
    """
    entries = getattr(source, "nodes", None)
    if entries is not None and entries.get(node_name) is None:
        return frozenset()          # unreachable: the heartbeat's verdict, neither half
    found = False
    for server in getattr(source, "servers", ()) or ():
        name = readmodels.text(readmodels.safe(
            lambda: getattr(getattr(server, "node", None), "name", "")))
        if name == node_name:
            found = True
            break
    if not found:
        return frozenset()          # no server in the caller's view: neither half
    if power_record(node_name) is not None:
        return frozenset({NODE_POWER_ON})
    return frozenset({NODE_POWER_OFF})


def node_states(source, node_name: str) -> frozenset[str]:
    """EVERY state *node_name* is in, in the caller's view — the ONE gate a node control matches.

    The union of the two vocabularies the row needs, each computed by its own owner:

    * the POWER states (:func:`node_power_states`) — what the power pair is gated on, from the node's
      OWN state;
    * the FLAG state (:func:`node_state`) — ``in-service`` / ``maintenance`` / ``""``. Nothing
      declares it today (the flag's own controls are on the SERVER row, §10.4), and it is in the set
      anyway so that a future node-row control about the flag declares :data:`NODE_IN_SERVICE` /
      :data:`NODE_MAINTENANCE` and works — and so that this function is the ONE place a node control's
      gate is computed rather than two that could drift.

    An action declaring several states is offered when ANY of them is present: the states are not
    mutually exclusive facts (a node can be ``in-service`` AND have a power-off on record), which is
    why the gate is a set and not a single value.
    """
    states = set(node_power_states(source, node_name))
    flag_state = node_state(source, node_name)
    if flag_state:
        states.add(flag_state)
    return frozenset(states)


def node_controls(request: Request, origin: str) -> dict[str, dict]:
    """The row STRIP each NODE row may render, keyed by node name — and nothing for a row that may
    not be offered one.

    ``origin`` is the PAGE this strip is being rendered on, carried into every control as a hidden
    field so the write returns there — the registry re-checks it
    (:func:`origin_path`).

    FIVE conditions, each read from the ONE place that decides it; a control is
    OMITTED — never rendered disabled — when any of them is false:

    * **the capability**: ``permissions.allows``, the same predicate the access gate runs. Node
      writes are Admin-only with no scope grant, so a MANAGER is offered none even on the node their
      own servers run on;
    * **the reachability**: ``node is not None`` in the caller's view of the cluster — the heartbeat's
      verdict, the same fact ``readmodels.nodes`` renders as ONLINE/OFFLINE. A node the cluster
      cannot reach has no object to call, so a control for it could only answer the action's own
      "offline or unknown" refusal, and a button whose only answer is a refusal is not offered. This
      applies to the MAINTENANCE PAIR as well, and for a second reason: its servers are read from the
      caller's view, so an unreachable node would answer "0 server(s)" while its servers may still be
      listed;
    * **the scope**: the nodes come from the caller's own ``_Cluster`` (built from the SCOPED
      source), so a node outside the caller's view is not in the mapping at all;
    * **the action**: ``action_available`` — an installation whose ``admin`` plugin is not loaded
      offers no button that could only answer a refusal;
    * **the state**: an action declaring ``states`` is offered only while at least one of them holds,
      as computed by :func:`node_states` over the caller's own view. The power pair's gate is the
      POWER state — *Take servers offline* on a heartbeating node that carries servers and has NO
      power-off on record, *Bring servers online* on a node whose power-off IS on record
      (:func:`node_power_states`; the gate is off the servers' statuses) — because the pair is a power
      control and not a flag toggle. A node carrying no server
      is in no state at all and is offered neither half.

    THE MASTER'S OWN ROW IS NOT SPECIAL-CASED HERE, and that is the decision:
    the console's process lives inside the master, so restarting it or shutting it
    down kills the process serving this page — but refusing would make the master the ONE node an
    Admin cannot restart from the console, and hiding the control would be a per-row inconsistency
    nobody can see. The warning belongs in the dialog, one sentence longer for that row
    (:func:`node_confirm_context`), which is where the consequence is read before it is chosen. The
    MAINTENANCE pair needs no such note: it stops no service, which is why it is on this row at all.

    The value is ONE record per row, in the SAME shape :func:`server_controls` builds — the row is
    ONE component at two widths (``templates/_strip.html``). The node controls are a strip with no
    overflow menu: they are one equally consequential family, and folding any of them into a menu
    would make one look rarer than its neighbours for no reason.
    """
    source = dashboard_page.request_source(request)
    roles = permissions.role_names_for(request)
    manager = permissions.manages_console(request)
    token = session.get_csrf_token(request)
    # THE LOG DOWNLOAD's owner is the nodes PAGE module (it owns the route and the capability);
    # imported here, inside the function, beside the other page imports this module makes lazily.
    from . import nodes as nodes_page
    controls: dict[str, dict] = {}
    for index, (name, node) in enumerate(
            (getattr(source, "nodes", None) or {}).items()):
        key = readmodels.text(name)
        if not key:
            continue
        # THE ONE READ CONTROL on the row: *Download log*, offered on the LOG PANEL's own
        # capability — the same predicate the route's gate runs. It needs a node the cluster can
        # REACH (``read_file`` is a call), so an OFFLINE row is offered none, exactly like its
        # write controls: the row already says the node is offline, and a control that could only
        # answer "offline or unknown" is not offered.
        download = nodes_page.log_download_control(key, roles, manager) if node is not None else None
        if node is None:        # OFFLINE: the cluster knows it and cannot reach it
            continue
        states = node_states(source, key)
        offered = tuple(action for action in NODE_ACTIONS
                        if (not action.states or (set(action.states) & states))
                        and _node_offered(action, key, roles, manager))
        # THE HONEST STATEMENT for a check-gated control whose value is UNKNOWN: the
        # control is NOT offered, and the row says why rather than leaving the person to guess.
        note = _upgrade_note(key, roles, manager)
        # A row is kept when it has ANYTHING to render: an offered write, the unknown-check note, or
        # the log download. The download needs no plugin, so an install whose ``admin`` actions are
        # absent still hands over a node's log — it is a READ of a file the Node API owns.
        if not offered and not note and download is None:
            continue
        controls[key] = {
            "strip": tuple(_node_control(action, key, token, origin) for action in offered),
            "menu": (), "wide": False, "menu_id": f"act-menu-n{index}",
            "node": key, "aria": f"Controls for node {key}", "menu_note": "",
            # the row-level statement ``_table.html`` renders beside the strip (`no update check yet`)
            "note": note,
            # the row's ONE READ control (``pages/nodes.log_download_control``): a LINK, rendered
            # by ``_table.html`` beside the strip, or ``None`` for a caller who may not use it.
            "download": download,
        }
    return controls


def _node_offered(action: NodeAction, name: str, roles: frozenset[str], manager: bool) -> bool:
    """Whether ONE node control may be offered on *name* — capability, availability and its gate.

    Three readings of the ONE declaration, exactly like the filter it replaces: the capability
    (``permissions.allows``, the predicate the access gate runs), the action's availability
    (``action_available``), and — for a CHECK-GATED control — the
    CACHED upgrade value. The value is read from ``services.webservice.upgrade`` and NEVER by asking
    a node: the poller owns every call, and a render must issue none. A value that is ``False`` OR
    UNKNOWN withholds the control ("offered only while the cached value is True"): a control whose
    only possible answer is the action's own "no upgrade available" refusal is not offered.
    """
    if not permissions.allows(action.capability, roles, manager=manager):
        return False
    if not action_available(action.qualname):
        return False
    if getattr(action, "check", False) and upgrade_signal.pending(name) is not True:
        return False
    return True


def _upgrade_note(name: str, roles: frozenset[str], manager: bool) -> str:
    """The row's honest statement while a CHECK-GATED control's cached value is UNKNOWN.

    Empty unless the caller could otherwise be offered such a control (same capability and
    availability predicates as :func:`_node_offered`) AND its value is UNKNOWN — never checked, or
    the last check failed. The wording is :data:`NOTE_NO_CHECK` ("no update check yet"). A value
    that is known (``True`` or ``False``) carries no note: True offers the control, and False simply
    offers nothing.
    """
    for action in NODE_ACTIONS:
        if not getattr(action, "check", False):
            continue
        if (permissions.allows(action.capability, roles, manager=manager)
                and action_available(action.qualname)
                and upgrade_signal.pending(name) is None):
            return NOTE_NO_CHECK
    return ""


def node_load(source, node_name: str) -> tuple[int, int]:
    """``(servers, players)`` on *node_name* in the caller's view — what an operation will take down.

    Read from the SAME scoped source the page renders from, so the numbers in a dialog describe what
    the caller can already see on that page (and never more). Nothing is awaited and no RPC is
    issued: the servers are in-process objects, exactly as the read models' counters are. Anything
    unreadable counts as 0 — a figure is decoration here, and a broken attribute must not cost the
    dialog.
    """
    servers = 0
    players = 0
    for server in getattr(source, "servers", ()) or ():
        name = readmodels.text(readmodels.safe(
            lambda: getattr(getattr(server, "node", None), "name", "")))
        if name != node_name:
            continue
        servers += 1
        players += players_on(server)
    return servers, players


def _node_control(action: NodeAction, name: str, token: str, origin: str) -> dict:
    """One rendered NODE control, as data: where it posts, its glyph, its two names, its target.

    The target travels in the request BODY, never in a URL, under the field name the
    node route reads (``node``) — one string, so a name cannot point an action at another machine.
    The ORIGIN travels beside it, the page this control is rendered on.

    EVERY node control posts to its DIALOG's path (see :data:`CONFIRM_SUFFIX`) — the LIFECYCLE trio
    because the confirmation is what protects a destructive operation, and the MAINTENANCE pair
    because both of its halves carry the command's own option, which is a form field and has nowhere
    else to live. ``action.confirm`` decides the other half of the difference: whether the ACTION's
    own route demands the one-shot token (offline does; online, which only clears a state, does not).
    """
    return {"path": action.path + CONFIRM_SUFFIX,
            "label": action.label, "icon": action.key, "title": action.tip.format(node=name),
            "aria": action.aria.format(node=name), "hint": action.hint,
            "danger": action.danger, "node": name,
            "direct": False,
            "hidden": (("node", name), (ORIGIN_FIELD, origin)), "buttons": (),
            "field": "", "field_label": "", "field_max": 0, "field_help": "", "field_id": "",
            "csrf_field": session.CSRF_FIELD, "csrf_token": token}


def controls_for(request: Request, table: str, origin: str) -> dict:
    """The row controls the table *table* may render — ONE call a list page makes for its own table.

    A list page asks for controls by the table it renders, so the page never decides which map
    applies and a page that renders no rows cannot be handed another page's controls. A table with
    no write at all (instances) gets ``{}`` — no header cell, no empty column.

    ``origin`` is the path of the PAGE that is rendering the table, and it travels into every control
    as a hidden field so a write returns there: the caller states which page it
    is (``page.path`` for a list page, the Dashboard's ``/`` for the console), which is the one thing
    this module cannot read off the request — the live stream renders the dashboard's tables through
    its own ``/api/…`` request, so the request path is not the page.
    """
    if table == "players":
        return player_controls(request, origin)
    if table == "servers":
        return server_controls(request, origin)
    if table == "nodes":
        return node_controls(request, origin)
    return {}


def _muted_state(source) -> dict[tuple[str, str], bool]:
    """The MUTE flag of every ACTIVE player in the caller's view, keyed by ``(server, ucid)``.

    Read from the SAME SCOPED SOURCE the rows come from, so the flag can never describe a player the
    row does not: it is the control's one piece of per-row state, and the read model's ``PlayerView``
    does not carry it (the read model is the ROW; a control's state is this module's business). An
    unreadable player or flag answers ``False`` — "not muted" — which offers the Mute control; the
    action's own ``already muted`` guard is the authority a stale page meets, and a wrong guess costs
    one typed refusal rather than a wrong write.
    """
    out: dict[tuple[str, str], bool] = {}
    for server in getattr(source, "servers", ()) or ():
        server_name = readmodels.text(readmodels.safe(lambda: getattr(server, "name", "")))
        if not server_name:
            continue
        players = readmodels.safe(lambda: getattr(server, "players", None), {}) or {}
        for player in (players or {}).values():
            ucid = readmodels.text(readmodels.safe(lambda: getattr(player, "ucid", "")))
            if not ucid:
                continue
            out[(server_name, ucid)] = bool(readmodels.safe(
                lambda: getattr(player, "muted", False), False))
    return out


def player_controls(request: Request, origin: str) -> dict[tuple[str, str], dict]:
    """The controls each PLAYER row may render, keyed by ``(server name, ucid)`` — and nothing for a
    row whose control may not be offered.

    ``origin`` is the PAGE this strip is being rendered on, carried into every control as a hidden
    field so the write returns there — the registry re-checks it (:func:`origin_path`).

    Same three conditions as :func:`server_controls`, each read from the ONE place that decides it,
    and the control is OMITTED — never rendered disabled — when any is false:

    * **the capability**: ``permissions.allows``, the same predicate the access gate runs;
    * **the scope**: the rows come from :func:`readmodels.players_online` over
      :func:`services.webservice.pages.dashboard.request_source` — the SCOPED source, and the very
      read model the Players table renders from, so ``controls ⊆ rows`` holds by construction. A
      player on a server outside the caller's scope is not in the source at all, and a crafted POST
      naming them is refused by ``resolve_scoped_server`` with the same bare 403 as a name that does
      not exist;
    * **the action**: ``action_available`` — an installation whose ``mission`` plugin is not loaded
      offers no control that could only answer a refusal.

    Two further conditions decide WHICH control, never whether the row may be acted on:

    * ``when_muted`` — the Mute pair: the read model's own mute flag picks which of the two is
      drawn, so a muted player is offered Unmute, and never both;
    * the player's own existence — there is none to add: the read model lists ACTIVE players only,
      and the action's ``get_player(active=True)`` lookup is the authority a stale page or a crafted
      POST meets (it answers its own typed refusal).

    The value is ONE record per row, in the SAME shape :func:`server_controls` builds — the row is
    ONE component at two widths (``templates/_strip.html``), and the player row shares it rather than
    growing a second. A player row has no overflow menu: its five glyphs go
    to the strip, and folding five icons into a menu would be a different design.
    """
    source = dashboard_page.request_source(request)
    roles = permissions.role_names_for(request)
    manager = permissions.manages_console(request)
    token = session.get_csrf_token(request)
    muted = _muted_state(source)
    controls: dict[tuple[str, str], dict] = {}
    for index, view in enumerate(readmodels.players_online(source)):
        server_name = readmodels.text(view.server_name)
        ucid = readmodels.text(view.ucid)
        if not server_name or not ucid:
            continue
        is_muted = bool(muted.get((server_name, ucid), False))
        name = readmodels.text(view.name) or ucid
        offered = tuple(_player_control(action, server_name, ucid, name, token, origin)
                        for action in PLAYER_ACTIONS
                        if (action.when_muted is None or action.when_muted == is_muted)
                        and permissions.allows(action.capability, roles, manager=manager)
                        and action_available(action.qualname))
        if offered:
            controls[(server_name, ucid)] = {
                "strip": offered, "menu": (), "wide": False,
                "menu_id": f"act-menu-p{index}", "server": server_name, "ucid": ucid,
                "aria": f"Controls for {name} on {server_name}"}
    return controls


def _player_control(action: PlayerAction, server_name: str, ucid: str, player_name: str,
                    token: str, origin: str) -> dict:
    """One rendered PLAYER control, as data: path, glyph, the two names, modes, field, target, token.

    BOTH halves of the target travel in the request BODY — there is no URL a person
    can edit to point a write at another server or another player — and the ORIGIN travels beside
    them, the page this control is rendered on.

    A CONFIRM-REQUIRED control posts to its DIALOG's path, not to the action's (see
    :data:`CONFIRM_SUFFIX`): the action's own route refuses a POST that did not come through the
    dialog, so the row could not reach it directly even if it wanted to.
    """
    values = {"server": server_name, "player": player_name}
    return {
        "path": action.path + CONFIRM_SUFFIX if action.confirm else action.path,
        "label": action.label, "icon": action.icon,
        "title": action.tip.format(**values), "aria": action.aria.format(**values),
        "hint": action.hint, "danger": action.danger,
        "server": server_name, "ucid": ucid,
        "direct": not action.confirm,
        "hidden": (("server", server_name), ("ucid", ucid), (ORIGIN_FIELD, origin)),
        "buttons": tuple({"mode": button.mode, "icon": button.icon,
                          "title": button.tip.format(**values),
                          "aria": button.aria.format(**values)}
                         for button in action.buttons),
        "field": action.field, "field_label": action.field_label, "field_max": action.field_max,
        "field_help": action.field_help, "field_id": f"{action.field}-{ucid}" if action.field else "",
        "csrf_field": session.CSRF_FIELD, "csrf_token": token,
    }


# ------------------------------------------------------------------------------- the confirm step

def _confirm_key(path: str, name: str) -> str:
    """The store key of ONE pending confirmation: the action's path and the resolved target."""
    return f"{path}|{name}"


def _confirm_store(request: Request) -> dict:
    """The pending confirmations in this session, as a plain dict. Never raises, never authored."""
    try:
        stored = request.session.get(CONFIRM_KEY)
    except (AssertionError, KeyError):       # no SessionMiddleware / no session on this request
        return {}
    return dict(stored) if isinstance(stored, dict) else {}


def mint_confirm_token(request: Request, path: str, name: str) -> str:
    """Mint the one-shot token the dialog's form carries, and remember it for this target.

    Bounded (``CONFIRM_MAX_PENDING``): the store rides in the SIGNED COOKIE (see
    ``NOTICE_MAX_CHARS``), so opening dialogs must never grow the session without limit. The oldest
    entries are dropped once the cap is reached — a dropped token can only cause a refusal, never a
    confirmation that was not asked for.
    """
    token = secrets.token_urlsafe(24)
    store = _confirm_store(request)
    store[_confirm_key(path, name)] = token
    if len(store) > CONFIRM_MAX_PENDING:
        for stale in list(store)[:-CONFIRM_MAX_PENDING]:
            store.pop(stale, None)
    try:
        request.session[CONFIRM_KEY] = store
    except (AssertionError, KeyError):       # pragma: no cover - guarded by the session's presence
        return token
    return token


def consume_confirm_token(request: Request, path: str, name: str, posted: str) -> bool:
    """Whether *posted* is this session's pending token for (*path*, *name*) — and SPEND it.

    The token is removed on the way through, so a replayed dialog form is refused rather than
    re-running a destructive action. Comparison is constant-time (``hmac.compare_digest``) because
    the value is a secret the server minted.
    """
    store = _confirm_store(request)
    expected = store.pop(_confirm_key(path, name), None)
    try:
        request.session[CONFIRM_KEY] = store
    except (AssertionError, KeyError):       # pragma: no cover - guarded by the session's presence
        pass
    return bool(expected) and bool(posted) and hmac.compare_digest(str(expected), str(posted))


def players_on(server: Any) -> int:
    """How many players are on *server* right now, read tolerantly from the RESOLVED object.

    The dialog's numbers come from the server the caller resolved, never from a field the browser
    sent. Anything unreadable answers 0: a figure is decoration here, and a broken
    attribute must not cost the dialog.
    """
    try:
        active = getattr(server, "get_active_players", None)
        if callable(active):
            found: Any = active()
            return len(found) if found else 0
    except Exception:
        log.exception("Could not count the players on '%s'", getattr(server, "name", "?"))
    try:
        return len(getattr(server, "players", None) or {})
    except Exception:
        return 0


def confirm_context(request: Request, action: WriteAction, name: str, players: int,
                    token: str, origin: str) -> dict:
    """Everything the confirm dialog's page reads, in one value.

    ``origin`` is the page the row that opened this dialog was rendered on: it
    is re-emitted in the dialog's own form (so the action's route can 303 back there), and it is what
    the dialog's ``Cancel`` and its ``Esc`` go back to — cancelling a Dashboard dialog must not
    strand the person on the Servers page either.

    The MARKUP of the warning is assembled HERE and not in the template, with the interpolated values
    HTML-escaped (``html.escape``): the two halves of that copy — the consequence in bold, the detail
    under it — are one sentence pair that must not drift, and the server name is config data that
    must not be able to inject markup into the dialog. The template marks the field safe because the
    module built it; nothing user-supplied reaches it unescaped.
    """
    registrar = getattr(request.app.state, "webui_registrar", None)
    state = readmodels.overview(dashboard_page.request_source(request),
                                level=dashboard_page.log_level(request))
    values = {"server": html.escape(str(name)), "players": str(int(players))}
    dialog = {
        "heading": f"{action.label} {name}?",
        "warning": action.warning.format(**values),
        "detail": action.detail.format(**values),
        "go": action.go,
        "go_title": action.go_title.format(**values),
        "path": action.path,
        "server": name,
        #: the CONSEQUENCE's target, as the dialog's own sentence names it (``pages/actions``): the
        #: resolved object's label, never a field the browser sent. A server dialog targets the
        #: server; a player dialog targets the player on it.
        "target": name,
        #: the form's hidden fields — the whole target and the page it was pressed on
        "hidden": (("server", name), (ORIGIN_FIELD, origin)),
        #: the fields the dialog asks for, drawn before the button: the command's own OPTION when
        #: the action declares one (Shutdown/Startup's maintenance box), otherwise none — the
        #: destructive trio need no input, which is why this row exists at all.
        "inputs": option_inputs(action),
        #: no BODY fields: the server dialog lives entirely in the footer form (M9's bulk-remove
        #: dialog is the one that adds them).
        "body_inputs": (),
        #: the template's own chrome. A DESTRUCTIVE write confirms (red button, "cannot be undone");
        #: an action that only CARRIES AN OPTION (Startup) renders the plain form its sibling
        #: ``online`` does on the node row — driven by the declaration, never by an action's name.
        "tag": "confirm" if action.confirm else "options",
        "irreversible": bool(action.confirm),
        "danger": bool(action.danger),
        "confirm_field": CONFIRM_FIELD,
        "confirm_token": token,
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
    }
    return {
        "title": f"{action.label} — confirm",
        "page_title": f"{action.label} {name}?",
        "crumb": f"{dashboard_page.CRUMB_GROUP} / {action.label}",
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                               current=origin,
                                               manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
        "back": origin,
        "dialog": dialog,
    }


def _player_target(server_name: str, ucid: str) -> str:
    """The confirm store's key for a PLAYER write: both halves of the target, as one string.

    A server's key is its name; a player's must carry the server too, or two players with one ucid
    (or an empty one) on different servers would share a pending confirmation.
    """
    return f"{server_name}|{ucid}"


def _player_name(player: Any, ucid: str) -> str:
    """The label a player dialog shows — the RESOLVED object's own name, never a browser field.

    The console must not import the ``mission`` plugin (its pages have to render on an install whose
    plugin is absent), so this is the console's own tolerant read of the same two attributes the
    plugin's actions use (``display_name`` then ``name``), falling back to the ucid so a dialog can
    never name nobody.
    """
    for attribute in ("display_name", "name"):
        value = readmodels.text(readmodels.safe(lambda: getattr(player, attribute, "")))
        if value:
            return value
    return ucid


def player_confirm_context(request: Request, action: PlayerAction, server: Any, player: Any,
                           server_name: str, ucid: str, token: str, origin: str) -> dict:
    """Everything a PLAYER confirm dialog's page reads — the twin of :func:`confirm_context`.

    Same discipline, one difference that is the whole point: the dialog carries the SECOND half of
    the target (the ucid) and the fields the destructive action needs (a reason, an optional number
    of days), which is why the mockup draws kick and ban as dialogs with a message field while the
    three server dialogs ask for nothing. ``origin`` is the page the row was rendered on, re-emitted
    in the dialog's form and used for its ``Cancel``/``Esc``.

    The player's label is read off the RESOLVED ``Player`` — never a hidden field the browser sent
 — and the warning is assembled here with :func:`html.escape` for the same reason
    the server dialog's is: the target can be config data, and the copy must not be able to inject
    markup.
    """
    registrar = getattr(request.app.state, "webui_registrar", None)
    state = readmodels.overview(dashboard_page.request_source(request),
                                level=dashboard_page.log_level(request))
    label = html.escape(_player_name(player, ucid))
    values = {"server": html.escape(str(server_name)), "player": label}
    inputs: list[dict] = []
    if action.field:
        inputs.append({"name": action.field, "id": f"{action.field}-dialog", "type": "text",
                       "label": action.field_label or action.label,
                       "max": action.field_max, "required": True, "value": "",
                       "help": action.field_help, "placeholder": ""})
    if action.key == "ban":
        # The days field the mockup draws: optional, empty = permanent. It is
        # typed as text with a numeric keyboard rather than ``type="number"`` because the ACTION —
        # not the browser — is the validator, and a number input that silently refuses a value would
        # hide the action's own typed refusal behind a browser tooltip.
        inputs.append({"name": "days", "id": "days-dialog", "type": "text",
                       "label": "Days (optional)", "max": 5, "required": False, "value": "",
                       "help": "Empty = permanent — the ban does not expire on its own. 7 means "
                               "seven days from now.",
                       "placeholder": ""})
    dialog = {
        "heading": f"{action.label} {label}?",
        "warning": action.warning.format(**values),
        "detail": action.detail.format(**values),
        "go": action.go,
        "go_title": action.go_title.format(**values),
        "path": action.path,
        "server": server_name,
        "target": label,
        "hidden": (("server", server_name), ("ucid", ucid), (ORIGIN_FIELD, origin)),
        "inputs": tuple(inputs),
        "body_inputs": (),
        "tag": "confirm",
        "irreversible": True,
        "danger": bool(action.danger),
        "confirm_field": CONFIRM_FIELD,
        "confirm_token": token,
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
    }
    return {
        "title": f"{action.label} — confirm",
        "page_title": f"{action.label} {label}?",
        "crumb": f"{dashboard_page.CRUMB_GROUP} / {action.label}",
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                               current=origin,
                                               manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
        "back": origin,
        "dialog": dialog,
    }


def dialog_options(action: WriteAction | NodeAction) -> tuple[NodeOption, ...]:
    """EVERY checkbox ONE control's dialog draws, in order — ``option`` then ``options``.

    The server row and the node row declare a single ``option``; the Missions tab's ADD flow declares
    its ``options`` (the load pair). One accessor, so :func:`option_inputs` and the mission dialog
    context cannot disagree about what a declaration carries.
    """
    found: list[NodeOption] = []
    option = getattr(action, "option", None)
    if option is not None:
        found.append(option)
    found.extend(getattr(action, "options", ()) or ())
    return tuple(found)


def _option_fields(options) -> tuple[dict, ...]:
    """The dialog fields a list of :class:`NodeOption` is drawn as — the OFF companion then the box.

    TWO records for one checkbox, and the ORDER is the substance: the hidden companion carrying the
    OFF value comes FIRST and the checkbox second. A checkbox sends no field of its own when it is
    cleared, so without the companion the OFF value is indistinguishable from the form not stating
    the field — and this FastAPI resolves a repeated field to the LAST value it sees, so the pair must
    be OFF then ON (the other order makes a ticked box save OFF). The checkbox's ``checked``
    attribute is the DECLARATION's default, which is how the browser shows the operator which way the
    default points. Shared by :func:`option_inputs` and the mission dialog context.
    """
    fields: list[dict] = []
    for option in options:
        fields.append({"type": "hidden", "name": option.field, "value": option.off, "id": "",
                       "label": "", "max": 0, "required": False, "checked": False, "help": "",
                       "placeholder": ""})
        fields.append({"type": "checkbox", "name": option.field, "value": option.on,
                       "id": f"{option.field}-dialog", "label": option.label, "max": 0,
                       "required": False, "checked": option.default, "help": option.help,
                       "placeholder": ""})
    return tuple(fields)


def option_inputs(action: WriteAction | NodeAction) -> tuple[dict, ...]:
    """The dialog fields ONE action's own OPTION(s) are drawn as — or none when it has no option.

    Shared by the SERVER dialog (``confirm_context``: Shutdown/Startup's maintenance box), the
    NODE dialog (``node_confirm_context``: ``offline``'s box) and the MISSION dialogs, because the
    option is ONE shape (``NodeOption``) read by ONE template (``confirm.html``). A declaration may
    carry a single ``option`` or several ``options`` (:func:`dialog_options`), and the order is the
    declaration's.
    """
    return _option_fields(dialog_options(action))


def _checked_text(when) -> str:
    """A cache timestamp as the dialog's own words: ``YYYY-MM-DD HH:MM UTC``.

    Read off the check record's aware datetime, rendered in UTC so the value is unambiguous across
    the machine's timezone (the bot logs in UTC by default). Anything unreadable answers ``""`` —
    the sentence is dropped rather than rendered half-written.
    """
    try:
        return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (AttributeError, ValueError):
        return ""


def node_confirm_context(request: Request, action: NodeAction, name: str, node: Any,
                         token: str, origin: str) -> dict:
    """Everything a NODE dialog's page reads — the third twin of :func:`confirm_context`.

    Same discipline, three differences that are the whole point of a node dialog:

    * the FIGURES are the node's load, not one server's — ``{servers}`` and ``{players}`` are what the
      operation takes down with it, counted from the caller's own scoped source
      (:func:`node_load`), never from a field the browser sent;
    * the MASTER row's dialog carries one more sentence (``master_note``). The console runs INSIDE
      the master's process (``services/webservice/service.py``), so restarting or shutting that node
      down ends the very request that is being served — a consequence the machine cannot escape and
      the operator has to be told before choosing: warn and allow, never hide,
      never refuse). The test for it is the ABSENCE of that sentence on an agent's dialog, so the
      wording cannot silently spread to every node;
    * the MAINTENANCE pair's own OPTION is rendered here (:func:`option_inputs`), which is what
      makes this page a form rather than a yes/no. The dialog a POST that skipped it is refused
      against is still this one (``action.confirm``), and for the pair's non-destructive half it is a
      form that runs the operation directly — the mockup's own rule (offline confirms, online does
      not).

    The warning is assembled HERE with :func:`html.escape`, as its two siblings' are: the node name
    is config data and the copy must not be able to inject markup. A node action with NO warning
    (the maintenance pair's online half) renders no warning block at all — the template's own
    condition, driven by this value rather than by an action name.
    """
    registrar = getattr(request.app.state, "webui_registrar", None)
    source = dashboard_page.request_source(request)
    state = readmodels.overview(source, level=dashboard_page.log_level(request))
    servers, players = node_load(source, name)
    label = html.escape(str(name))
    values = {"node": label, "servers": str(int(servers)), "players": str(int(players))}
    detail = action.detail.format(**values)
    if bool(readmodels.safe(lambda: getattr(node, "master", False), False)) and action.master_note:
        detail = f"{detail} {action.master_note.format(**values)}"
    if getattr(action, "check", False):
        when = upgrade_signal.checked_at(name)
        if when is not None:
            # WHEN THE CACHED VALUE WAS PRODUCED: a stale answer must read as stale
            # rather than authoritative-looking. The value is the poller's own timestamp, never a
            # field the browser sent, and it is present only for a check-gated control.
            detail = f"{detail} Last update check: {_checked_text(when)}."
    dialog = {
        "heading": (action.heading.format(**values) if action.heading
                    else f"{action.label} {label}?"),
        "warning": action.warning.format(**values),
        "detail": detail,
        "go": action.go,
        "go_title": action.go_title.format(**values),
        "path": action.path,
        "server": label,
        "target": f"{label} (node)",
        "hidden": (("node", name), (ORIGIN_FIELD, origin)),
        # the action's own option, when it has one; otherwise the dialog asks for nothing, which is
        # why the mockup draws the lifecycle trio as plain confirmations
        "inputs": option_inputs(action),
        "body_inputs": (),
        "confirm_field": CONFIRM_FIELD,
        "confirm_token": token,
        # the dialog's own chrome, driven by the DECLARATION rather than by an action's name: a
        # destructive operation wears a red button and says it cannot be undone; a form that only
        # clears a state says neither.
        "tag": "confirm" if action.confirm else "options",
        "irreversible": bool(action.confirm),
        "danger": bool(action.danger),
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
    }
    return {
        "title": f"{action.label} — confirm",
        "page_title": f"{action.label} {label}?",
        "crumb": f"{dashboard_page.CRUMB_GROUP} / {action.label}",
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                               current=origin,
                                               manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
        "back": origin,
        "dialog": dialog,
    }


# ------------------------------------------------------------------------------- the notice

def _remember_notice(request: Request, action: WriteAction | PlayerAction | NodeAction,
                     result: ActionResult, *, revert: str = "", revert_omitted: str = "") -> None:
    """Store the outcome of one write as a ONE-SHOT notice, for the page the redirect lands on.

    The typed result is read for its FIELDS (``success``, ``message``, ``data["audit"]``), never
    re-parsed from prose: the page renders what the action reported, and a copy change in the action
    cannot make the banner lie.

    The MESSAGE is BOUNDED here, at the point it enters the session, not at each render: the value
    lives in the signed cookie, so its size is the cookie's size. The clip is VISIBLE — ``bound_text``
    appends the truncation marker — so a refusal clipped for a crafted target does not read as a whole
    one.
    """
    data = getattr(result, "data", None) or {}
    audit = readmodels.text(data.get("audit")) if isinstance(data, dict) else ""
    request.session[NOTICE_KEY] = {
        "message": bound_text(readmodels.text(getattr(result, "message", "")), NOTICE_MAX_CHARS),
        "ok": bool(getattr(result, "success", False)),
        "audit": audit,
        # an audit that could not be recorded is VISIBLE — never a green banner over a lost trail
        "audit_warning": audit == AUDIT_NOT_RECORDED,
        "action": action.label,
        # THE ONE-SHOT REVERT: the OLD values of the keys just written, JSON-encoded and bounded, so
        # the page can offer a Revert that re-submits them through the SAME action. Empty for every
        # write that offers none. ``revert_omitted`` is the visible reason a Revert could not be
        # offered (a payload too large to carry) — never a silently inert button.
        "revert": revert,
        "revert_omitted": revert_omitted,
    }


def pop_notice(request: Request) -> dict | None:
    """The outcome of the last write, or ``None``. POPPED: it is shown exactly once.

    A session value is attacker-controlled data in the sense that it survives a cookie round trip,
    so the shape is re-validated here rather than trusted by the template.
    """
    try:
        stored = request.session.pop(NOTICE_KEY, None)
    except (AssertionError, KeyError):       # no SessionMiddleware / no session on this request
        return None
    if not isinstance(stored, dict) or not stored.get("message"):
        return None
    return stored


#: The wording a ROUTE-recorded out-of-scope refusal carries. It is deliberately the same
#: claim the console's own 403 makes and names NOTHING about the target, so the notice cannot become
#: a second, softer channel for the enumeration the bare refusal exists to prevent: a name that
#: exists and a name that does not are answered identically, in the page and in the notice alike.
REFUSAL_OUT_OF_SCOPE = "Not authorized (this target is outside your scope)."


def _refusal_notice(request: Request, action: WriteAction | PlayerAction | NodeAction,
                    message: str) -> None:
    """Store the ONE-SHOT refusal *message* for the page the person lands on.

    A refusal the ROUTE makes BEFORE any action runs used to be a bare ``HTTPException`` — which a
    person never read, because ``static/submit.js`` hands the page over to a fresh render the moment
    it fires the POST. This uses the console's OWN answer to "where is a refusal seen": the one-shot
    notice, the mechanism every accepted write's outcome already travels on
    (:func:`_remember_notice`), rendered where the person lands and on the live path
    (``pages/live.py``'s ``notice`` target).

    THE STATUS DOES NOT MOVE. A refused write is still the 403 it has always been — the wire contract
    for the API face and the refusal page for a browser are untouched — and the notice only ADDS a
    way for the refusal to be read once the background submit has handed the page over. It is the
    SAME store and the SAME partial as a success's notice, so a refusal can never read as one.
    """
    _remember_notice(request, action, ActionResult(success=False, message=message))


# ------------------------------------------------------------------------------- the routes

def add_routes(router: APIRouter) -> APIRouter:
    """Add one POST route per write, each with the session's CSRF dependency.

    A confirm-required write contributes TWO routes: the action's own (which refuses a POST that did
    not come through its dialog) and the POST that renders that dialog. Both are POSTs — the console
    has one way to send a target, and it is the body of a request, never a URL.
    """
    for action in SERVER_ACTIONS:
        router.add_api_route(action.path, _handler(action), methods=["POST"],
                             dependencies=[Depends(session.csrf_protect)],
                             name=f"action-{action.key}")
        if action.dialog:
            # the DIALOG route: the destructive controls confirm through it, and Startup collects
            # its own maintenance option there — see ``_confirm_handler`` and
            # ``WriteAction.dialog``.
            router.add_api_route(action.path + CONFIRM_SUFFIX, _confirm_handler(action),
                                 methods=["POST"],
                                 dependencies=[Depends(session.csrf_protect)],
                                 name=f"action-{action.key}-confirm")
    for action in PLAYER_ACTIONS:
        router.add_api_route(action.path, _player_handler(action), methods=["POST"],
                             dependencies=[Depends(session.csrf_protect)],
                             name=f"action-player-{action.key}")
        if action.confirm:
            router.add_api_route(action.path + CONFIRM_SUFFIX, _player_confirm_handler(action),
                                 methods=["POST"],
                                 dependencies=[Depends(session.csrf_protect)],
                                 name=f"action-player-{action.key}-confirm")
    for action in NODE_ACTIONS:
        router.add_api_route(action.path, _node_handler(action), methods=["POST"],
                             dependencies=[Depends(session.csrf_protect)],
                             name=f"action-node-{action.key}")
        # the DIALOG route, for every node action: the trio confirms through it, and the maintenance
        # pair collects its own option there — see ``_node_control`` and ``node_option_inputs``.
        router.add_api_route(action.path + CONFIRM_SUFFIX, _node_confirm_handler(action),
                             methods=["POST"],
                             dependencies=[Depends(session.csrf_protect)],
                             name=f"action-node-{action.key}-confirm")
    # THE DCS CONFIG WRITE: one POST, no dialog (the whole form is the input).
    for action in SERVER_CONFIG_ACTIONS:
        router.add_api_route(action.path, _config_handler(action), methods=["POST"],
                             dependencies=[Depends(session.csrf_protect)],
                             name=f"action-{action.key}")
    # THE CHANNELS WRITE: one POST, no dialog.
    for action in SERVER_CHANNEL_ACTIONS:
        router.add_api_route(action.path, _channels_handler(action), methods=["POST"],
                             dependencies=[Depends(session.csrf_protect)],
                             name=f"action-{action.key}")
    # THE COALITION WRITE: one POST, no dialog.
    for action in SERVER_COALITION_ACTIONS:
        router.add_api_route(action.path, _coalition_handler(action), methods=["POST"],
                             dependencies=[Depends(session.csrf_protect)],
                             name=f"action-{action.key}")
    # THE MISSION-LIST WRITES: one POST each. The upload posts a MULTIPART body (the file plus its
    # load options); the other three post their own small form. The ADD and REMOVE controls ALSO
    # contribute their DIALOG route (``MISSION_DIALOG_KEYS``), which RENDERS ``confirm.html`` — the
    # row/openers post THERE, and the delete route refuses a POST that skipped it (see
    # ``_mission_confirm_handler`` / ``_mission_delete_handler``).
    _mission_handlers = {
        "mission_add": _mission_add_handler,
        "mission_delete": _mission_delete_handler,
        "mission_delete_bulk": _mission_bulk_delete_handler,
        "mission_reorder": _mission_reorder_handler,
        "mission_activate": _mission_activate_handler,
        "mission_load": _mission_load_handler,
        "mission_upload": _mission_upload_handler,
    }
    for action in MISSION_ACTIONS:
        router.add_api_route(action.path, _mission_handlers[action.key](action), methods=["POST"],
                             dependencies=[Depends(session.csrf_protect)],
                             name=f"action-{action.key}")
        if action.key in MISSION_DIALOG_KEYS:
            router.add_api_route(action.path + CONFIRM_SUFFIX, _mission_confirm_handler(action),
                                 methods=["POST"],
                                 dependencies=[Depends(session.csrf_protect)],
                                 name=f"action-{action.key}-confirm")
    return router


def register(registrar) -> None:
    """Register the write surface with the registrar, under this module's own owner name."""
    router = APIRouter()
    add_routes(router)
    registrar.register_pages(OWNER, router, capabilities=capabilities())


# ------------------------------------------------------------------------------- the config write

#: What a bool form field posts when it is ON (a checkbox that is unticked posts nothing at all, which
#: is how "changed to false" is told from "not stated" — the form always renders the checkbox).
_CONFIG_TRUE = ("1", "true", "on", "yes")


def _as_bool(value) -> bool:
    """The boolean a setting's current value or a posted field means (anything unreadable -> False)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _CONFIG_TRUE


def _choice_value(field, chosen: str):
    """The declared OPTION a SELECT posted, by its rendered spelling — else the raw text.

    A select's options are the field's ``choices`` (:class:`server_config.ConfigField`), so the
    submitted value is matched back to the declared one and handed to the action with its own TYPE
    (``resume_mode``'s options are ints). A value that matches nothing is passed through unchanged,
    so the ACTION refuses it with a typed reason rather than this route dropping it silently.
    """
    for choice in field.choices:
        if str(choice) == chosen:
            return choice
    return chosen


def _unprotected_password(settings, form, key: str, values: dict) -> None:
    """An UNPROTECTED password's keep / clear / replace — the plain STRING diff.

    The field ships holding the STORED value, so a posted value equal to it is not a change, an
    EMPTIED field is the explicit clear (``None`` — the action reads a blank as unchanged), and
    anything else is the new password. A form that does not STATE the field clears nothing.
    """
    if not _form_has(form, key):
        return
    current = server_config.current_setting(settings, key)
    current_text = "" if current is None else str(current)
    stated = _last_form_value(form, key)
    if not stated.strip():
        if current_text.strip():
            values[key] = None                # emptied: the explicit clear
    elif stated != current_text:
        values[key] = stated                  # a typed password IS the new password


def _protected_password(settings, form, key: str, values: dict) -> None:
    """A PROTECTED password's keep / clear / replace — with NO value ever on the page.

    The field NEVER holds the value: it holds :data:`serverconfig.PROTECTED_SENTINEL` while one is set.
    An untouched field (that sentinel) keeps the value, an EMPTIED field clears it (``None``), and
    anything else replaces it. The current value is read only to decide whether an emptied field has
    anything to clear — never to render it.
    """
    if not _form_has(form, key):
        return
    stated = _last_form_value(form, key)
    if stated == server_config.PROTECTED_SENTINEL:
        return                                # the untouched placeholder: keep
    if stated.strip():
        values[key] = stated                  # a typed password IS the new password
        return
    current = server_config.current_setting(settings, key)
    if current is not None and str(current).strip():
        values[key] = None                    # emptied: the explicit clear


def config_values_from_form(settings, form) -> dict:
    """The CHANGED editable fields a submitted form carries — and ONLY those.

    The whole form is posted (no JavaScript required); each field's submitted value is diffed against
    the value the page SHOWED (through :func:`serverconfig.current_setting`, the SAME read the tab's
    rows use), and an unchanged field is left out, so the action is handed exactly the changed keys.

    THE PASSWORD FIELDS ARE DIFFED BY THEIR DECLARATION (``ConfigField.protection``):
    an UNPROTECTED one ships the stored value and is diffed as a string; a PROTECTED one never carries
    the value (it ships :data:`serverconfig.PROTECTED_SENTINEL` while one is set) and its keep / clear /
    replace is read from that placeholder. Either way an EMPTIED field is the explicit clear (``None``),
    which the action reads as a clear, and a blank field means unchanged to the action.

    A value present but UNPARSEABLE for its declared kind (a number field holding letters) is passed
    THROUGH as text: the ACTION validates and refuses it with a typed reason, never a silent drop.
    """
    values: dict[str, Any] = {}
    for key, field in server_config.EDITABLE_BY_KEY.items():
        if field.protection:
            continue                          # every password field is handled below, by declaration
        posted = _last_form_value(form, key)
        present = _form_has(form, key)
        current = server_config.current_setting(settings, key)
        if field.choices:
            # A SELECT (``ConfigField.choices``): the form renders a leading EMPTY "unchanged" option
            # that a browser picks exactly when nothing was chosen, so an UNTOUCHED select posts ""
            # and must NEVER read as a change. A chosen option is handed on with its declared type.
            if not present:
                continue                         # not stated by this form: unchanged
            chosen = posted.strip()
            if chosen == "":
                continue                         # the "unchanged" placeholder: not a change
            current_text = "" if current is None else str(current)
            if chosen != current_text:
                values[key] = _choice_value(field, chosen)
            continue
        if field.kind == server_config.KIND_BOOL:
            posted_bool = posted.strip().lower() in _CONFIG_TRUE
            if posted_bool != _as_bool(current):
                values[key] = posted_bool
        elif field.kind == server_config.KIND_INT:
            if not present:
                continue                         # not stated by this form: unchanged
            stripped = posted.strip()
            if stripped == "":
                if current is None:
                    continue                     # nothing was there and nothing was posted
                values[key] = stripped           # cleared: the action refuses it, visibly
                continue
            try:
                number = int(stripped)
            except ValueError:
                values[key] = stripped           # not a number: the action's validation says so
                continue
            if not (isinstance(current, int) and not isinstance(current, bool) and number == current):
                values[key] = number
        else:
            if not present:
                continue                         # not stated by this form: unchanged
            current_text = "" if current is None else str(current)
            if posted != current_text:
                values[key] = posted
    # THE PASSWORD FIELDS, by their own declaration: one branch per value of ``protection``.
    for key, field in server_config.EDITABLE_BY_KEY.items():
        if not field.protection:
            continue
        if field.protection == server_config.PROTECTED:
            _protected_password(settings, form, key, values)
        else:
            _unprotected_password(settings, form, key, values)
    if _as_bool(_last_form_value(form, SERVER_CONFIG_CLEAR_FIELD)):
        values["password"] = None                # the programmatic CLEAR (accepted; no control renders it)
    return values


def _revert_values(payload: str) -> dict:
    """The OLD values a one-shot Revert re-submits, decoded from the hidden field — or ``{}``.

    The action returns ``applied`` as ``{key: {"from":…, "to":…}}`` and the console offers a Revert that
    re-submits the ``from`` side through the SAME action, so it is audited, validated and locked like
    any write. Only EDITABLE keys survive, and a PASSWORD key (either branch of ``protection``) never
    does: its ``from`` side is a redaction sentinel, not a value, so there is nothing to restore. A
    malformed payload yields nothing to write (never a crash).
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items()
            if key in server_config.EDITABLE_BY_KEY
            and not server_config.EDITABLE_BY_KEY[key].protection}


def _revert_offer(result) -> tuple[str, str]:
    """The one-shot Revert to offer for a config write — ``(payload, omitted_sentence)``.

    ``payload`` is the JSON map of OLD values, built from the CONFIG action's own result
    (``ServerConfigResult.applied``: ``{key: {"from":…, "to":…}}``). Only EDITABLE keys survive, and a
    PASSWORD key (either branch of ``protection``) never does (its ``from`` is a redaction sentinel,
    not a value — nothing to restore). When there is nothing to restore the payload is ``""`` with an
    empty sentence.

    THE PAYLOAD IS NEVER TRUNCATED: it is bounded by :data:`REVERT_MAX_CHARS`, and a map that will not
    fit is dropped ENTIRELY with a VISIBLE reason (:data:`REVERT_TOO_LONG_SENTENCE`) — a JSON string cut
    mid-value cannot parse and would leave an inert Revert.
    """
    applied = getattr(result, "applied", None) or {}
    if not isinstance(applied, dict):
        return "", ""
    out: dict[str, Any] = {}
    for key, change in applied.items():
        field = server_config.EDITABLE_BY_KEY.get(key)
        if field is None or field.protection:
            continue
        if isinstance(change, dict) and "from" in change:
            out[key] = change["from"]
    if not out:
        return "", ""
    try:
        payload = json.dumps(out)
    except (TypeError, ValueError):
        return "", ""
    if len(payload) > REVERT_MAX_CHARS:
        return "", REVERT_TOO_LONG_SENTENCE
    return payload, ""


def _servers_yaml_mtime() -> float | None:
    """The ``servers.yaml`` mtime on THIS master, or ``None`` — read WITHOUT an RPC.

    Read off the process's own node's ``config_dir`` (the live ``ServiceBus`` node, exactly as
    :func:`_context` reads the seam's node): a plain attribute, no node round-trip. A console installed
    without a bot (a test, a standalone render) has no node, so the mtime is ``None`` and the revision
    is the settings ``mtime`` alone — still correct for the DCS face.
    """
    node = _node()
    config_dir = getattr(node, "config_dir", None)
    if not config_dir:
        return None
    try:
        return os.path.getmtime(os.path.join(str(config_dir), "servers.yaml"))
    except OSError:
        return None


def config_revision_for(server) -> str:
    """The config revision for *server* — the value the pulse records and a later render compares."""
    return server_config.config_revision(server, servers_yaml_mtime=_servers_yaml_mtime())


def config_pulse(server, server_name: str,
                 action_key: str = SERVER_CONFIG_ACTION_KEY) -> bool:
    """Whether the Save control for *server* is still pulsing its last config write.

    Read through the SAME store and end rule as every other control (:func:`awaiting_change`): it is
    ``True`` only while THIS session issued a config write on *server*, the CONFIG REVISION has not
    moved, and the expectation is younger than :data:`AWAIT_CHANGE_SECONDS`. A refusal or a failure
    dropped the expectation already, so a failed Save never pulses while its notice says it failed.
    """
    state = _row_state(server)
    flagged = bool(readmodels.safe(lambda: getattr(server, "maintenance", False), False))
    return awaiting_change("server", server_name, action_key, state, flagged,
                           revision=config_revision_for(server))


def _config_back_path(name: str) -> str:
    """Where the config write returns: the server's own page, Configuration tab (a GET, no POST).

    Built by the ONE encoder (``readmodels.servers.server_url``) so this redirect and the row's own
    link build the SAME URL for the same name — a name carrying a space, ``&`` or ``#`` lands on the
    page instead of truncating the path or opening a query string.
    """
    return server_url(name, tab="configuration")


def _post_write_name(result, server, canonical: str) -> str:
    """The server's name AFTER the config write — the redirect target.

    A config write may RENAME the server, and 303-ing to the name read BEFORE the action would land
    the browser on the OLD name's URL — a 404 that also swallows the success notice and the Revert.
    The ACTION is the authority on what it renamed, so its ``applied['name']['to']`` is read first; a
    write that renamed nothing falls back to the resolved object's own ``name`` (updated in place by
    the rename), then to the name the route came in with.
    """
    applied = getattr(result, "applied", None)
    if isinstance(applied, dict):
        rename = applied.get("name")
        if isinstance(rename, dict):
            new_name = readmodels.text(rename.get("to"))
            if new_name:
                return new_name
    return readmodels.text(readmodels.safe(lambda: getattr(server, "name", ""))) or canonical


def config_write_available() -> bool:
    """Whether the ONE config action is registered in THIS process (the ``action_available`` rule).

    The console does not offer a control whose only possible answer is the action's own
    "not available in this installation" refusal: an install without the ``scheduler``
    plugin ships no config write, so the tab shows the read and no Save.
    """
    return action_available(SERVER_CONFIG_ACTIONS[0].qualname)


def _config_handler(action: WriteAction):
    """The route for THE DCS CONFIG WRITE — one POST, no dialog.

    Same order as the other write routes (identity, form, resolution, action, notice, redirect), with
    three differences: the body is the form of settings, diffed to the CHANGED keys here
    (:func:`config_values_from_form`) — or, for a one-shot Revert, the OLD values decoded from the
    hidden field (:func:`_revert_values`) — and the ONE action ``set_server_config`` is called either
    way; the expectation is recorded with the THIRD observable (:data:`AWAIT_OBSERVABLE_CONFIG`) and
    the revision read BEFORE the action; and it 303s back to the server's own page.

    No ``audit_result``: the config action writes its OWN trail, once, for every outcome
    (``plugins/scheduler/actions.py``). A refusal or a failure forgets the expectation at once.
    """
    async def handle(request: Request) -> RedirectResponse:
        identity = _identity(request)
        if identity is None:
            _refusal_notice(request, action, "Not authorized (no signed-in identity).")
            raise HTTPException(status_code=403,
                                detail="Not authorized (no signed-in identity).")
        form = await request.form()
        name = readmodels.text(form.get(SERVER_CONFIG_FIELD))
        ctx = _context(request, identity)
        resolution = await ctx.resolve_scoped_server(name)
        if resolution.status == ServerResolution.NOT_PERMITTED:
            _refusal_notice(request, action, REFUSAL_OUT_OF_SCOPE)
            raise dashboard_page.out_of_scope_refusal()
        if not resolution.is_found:
            # authorized, but the name matches nothing: the honest answer is the seam's typed refusal,
            # rendered through the one-shot notice — never a 404 and never a silent success.
            result = await audit_action(ctx, ActionResult(success=False,
                                                          message=resolution.message))
            _remember_notice(request, action, result)
            return RedirectResponse(_servers_path(), status_code=303)
        server = resolution.server
        canonical = readmodels.text(readmodels.safe(lambda: getattr(server, "name", ""))) or name
        revert = readmodels.text(form.get(SERVER_CONFIG_REVERT_FIELD))
        if revert:
            values = _revert_values(revert)
        else:
            values = config_values_from_form(
                readmodels.safe(lambda: getattr(server, "settings", None)), form)
        # THE EXPECTATION BEFORE THE ACTION: the fresh render the async submit lands on must already
        # find it (the store is process-side, recorded here).
        remember_awaiting_change("server", canonical, action, _row_state(server), False,
                                 revision=config_revision_for(server))
        result = await call_action(action.qualname, ctx, server_name=canonical, values=values)
        revert_payload, revert_omitted = _revert_offer(result)
        _remember_notice(request, action, result, revert=revert_payload,
                         revert_omitted=revert_omitted)
        if not bool(getattr(result, "success", False)):
            forget_awaiting_change("server", canonical)
        # THE REDIRECT NAMES THE SERVER AS IT IS **AFTER** THE WRITE: a rename moves the name, so the
        # pre-action name would be a 404 — and the notice and Revert ride this redirect.
        return RedirectResponse(_config_back_path(_post_write_name(result, server, canonical)),
                                status_code=303)

    return handle


# ------------------------------------------------------------------------------- the channels write
# The bot face of the Configuration tab: ONE extra POST (``set_server_channels``), the same shape as
# the DCS config write (identity, form, scoped resolution, action, notice, redirect) but with its own
# capability. It differs in what the body is: a set of CHANGED CHANNEL ROWS, diffed here against the
# server's own ``locals['channels']`` so only what the operator moved is submitted.


def _channel_int(value) -> int:
    """A stored channel value as an int for diffing: the id, or :data:`serverconfig.CHANNEL_UNSET`.

    An absent value and an unreadable one both read as UNSET, exactly as
    :func:`serverconfig.channel_value_text` renders them, so "what the page showed" and "what the
    write compares against" are one reading of the file.
    """
    if value is None or isinstance(value, bool):
        return server_config.CHANNEL_UNSET
    try:
        return int(value)
    except (TypeError, ValueError):
        return server_config.CHANNEL_UNSET


def channels_values_from_form(channels, form) -> dict:
    """The CHANGED channel rows a submitted form carries — and ONLY those.

    ``channels`` is the server's current per-server channel map (``server.locals['channels']``); each
    rendered select posts its CURRENT value when untouched (so it diffs to nothing), the explicit
    ``not set`` value (:data:`serverconfig.CHANNEL_UNSET_VALUE`) for a clear, or a channel id for a
    new pick. The rules, stated so the template and this cannot disagree:

    * a row the form does not STATE is untouched;
    * a posted value that equals the current value is unchanged;
    * ``not set`` on a SET row is the explicit CLEAR — written as :data:`serverconfig.CHANNEL_UNSET`
      (the bot's own ``-1``), never a blank;
    * anything else is a new channel id — parsed to an int here, or passed through as text when it is
      not numeric so the ACTION refuses it with a typed reason rather than this route dropping it.
    """
    current = channels if isinstance(channels, dict) else {}
    values: dict[str, Any] = {}
    for key in server_config.CHANNEL_KEYS:
        field = f"channels.{key}"
        if not _form_has(form, field):
            continue
        posted = _last_form_value(form, field).strip()
        old = _channel_int(current.get(key))
        if posted == server_config.CHANNEL_UNSET_VALUE:
            if old != server_config.CHANNEL_UNSET:
                values[key] = server_config.CHANNEL_UNSET
            continue
        if posted == "":
            continue                             # the untouched marker (a set value not among options)
        try:
            number = int(posted)
        except ValueError:
            values[key] = posted                 # not a number: the action's validation says so
            continue
        if number != old:
            values[key] = number
    return values


def channels_pulse(server, server_name: str,
                   action_key: str = SERVER_CHANNEL_ACTION_KEY) -> bool:
    """Whether the channels Save for *server* is still pulsing its last write.

    Read through the SAME store and end rule as the config pulse (``awaiting_change``): ``True`` only
    while THIS session issued a channels write on *server*, the CONFIG REVISION has not moved, and the
    expectation is younger than :data:`AWAIT_CHANGE_SECONDS`. A refusal or a failure dropped it
    already, so a failed Save never pulses while its notice says it failed.
    """
    state = _row_state(server)
    flagged = bool(readmodels.safe(lambda: getattr(server, "maintenance", False), False))
    return awaiting_change("server", server_name, action_key, state, flagged,
                           revision=config_revision_for(server))


def channels_write_available() -> bool:
    """Whether the channels action is registered in THIS process (the ``action_available`` rule).

    The console does not offer a control whose only possible answer is the action's own
    "not available in this installation" refusal: an install without the ``scheduler``
    plugin ships no channels write, so the page shows the rows read-only and no Save.
    """
    return action_available(SERVER_CHANNEL_ACTIONS[0].qualname)


def _channels_handler(action: WriteAction):
    """The route for THE CHANNELS WRITE — one POST, no dialog.

    Same order as the config write (identity, form, scoped resolution, action, notice, redirect), with
    the same two guarantees and one difference:

    * the body is a set of CHANGED CHANNEL ROWS, diffed here against the server's own
      ``locals['channels']`` (:func:`channels_values_from_form`) — so an untouched form submits
      nothing and stays a no-op, and "not set" is an explicit change rather than a blank field;
    * the expectation is recorded with the THIRD observable (:data:`AWAIT_OBSERVABLE_CONFIG`), the
      same signal the DCS Save uses;
    * it 303s back to the SERVER'S OWN PAGE — there is NO rename here, so the target's own name is
      the redirect (a write that re-rendered itself would re-POST on refresh).
    """
    async def handle(request: Request) -> RedirectResponse:
        identity = _identity(request)
        if identity is None:
            _refusal_notice(request, action, "Not authorized (no signed-in identity).")
            raise HTTPException(status_code=403,
                                detail="Not authorized (no signed-in identity).")
        form = await request.form()
        name = readmodels.text(form.get(SERVER_CONFIG_FIELD))
        ctx = _context(request, identity)
        resolution = await ctx.resolve_scoped_server(name)
        if resolution.status == ServerResolution.NOT_PERMITTED:
            _refusal_notice(request, action, REFUSAL_OUT_OF_SCOPE)
            raise dashboard_page.out_of_scope_refusal()
        if not resolution.is_found:
            result = await audit_action(ctx, ActionResult(success=False,
                                                          message=resolution.message))
            _remember_notice(request, action, result)
            return RedirectResponse(_servers_path(), status_code=303)
        server = resolution.server
        canonical = readmodels.text(readmodels.safe(lambda: getattr(server, "name", ""))) or name
        local_channels = readmodels.safe(
            lambda: (getattr(server, "locals", None) or {}).get("channels"), None)
        values = channels_values_from_form(local_channels, form)
        # THE EXPECTATION BEFORE THE ACTION, exactly like the DCS config write: the fresh render the
        # async submit lands on must already find it, and it ends when the config revision moves.
        remember_awaiting_change("server", canonical, action, _row_state(server), False,
                                 revision=config_revision_for(server))
        result = await call_action(action.qualname, ctx, server_name=canonical, channels=values)
        _remember_notice(request, action, result)
        if not bool(getattr(result, "success", False)):
            forget_awaiting_change("server", canonical)
        return RedirectResponse(_config_back_path(canonical), status_code=303)

    return handle


# ---------------------------------------------------------------------------- the coalition write
# The THIRD face of the Configuration tab: the blue/red coalition passwords, whose cleartext lives in
# the bot's database and whose write goes through the bot's own ``setCoalitionPassword``. Same shape as
# the two faces above (identity, form, scoped resolution, action, notice, redirect), with two
# differences: its READ is an ACTION (the cleartext is not in ``server.settings``, so the page cannot
# read it in-process), and there is NO pulse (no observable moves for this write).


async def coalition_values(request: Request, server_name: str, *, ctx: ActionContext | None = None):
    """The plaintext coalition passwords for *server_name*, through the READ action — or ``None``.

    The console reads the cleartext with an ACTION, not a bare SELECT, for the repo's \"one home per
    operation\" reason: the write action already lives in the registry, and a console-side fourth SELECT
    would be a fourth place that knows the query. It goes through ``read_action`` (the UNGUARDED
    twin), so a render is never refused by the in-flight guard of a power action running on the same
    server.

    Returns the action's ``data`` mapping (``{"blue": …, "red": …, "*_hash_set": …}``) on success, and
    ``None`` for an absent action or any refusal — a page that cannot read renders NO coalition card
    rather than a card that reads as \"no password\".
    """
    if ctx is None:
        identity = _identity(request)
        if identity is None:
            return None
        ctx = _context(request, identity)
    result = await read_action("get_server_coalitions", ctx, server_name=server_name)
    if not bool(getattr(result, "success", False)):
        return None
    data = getattr(result, "data", None)
    return data if isinstance(data, dict) else None


async def mission_list(request: Request, server_name: str, *, ctx: ActionContext | None = None):
    """The mission list of *server_name*, through the ``get_mission_list`` READ action — or ``None``.

    The console reads the list with an ACTION, never by touching a mission file, a ``.dcssb`` copy or
    ``missionList``: the action is the ONE resolver of the newest copy and the ONE reader of the
    rotation state, so the tab, the download route and every other transport cannot disagree about
    which mission is "current". It goes through ``read_action`` (the UNGUARDED twin), so a render is
    never refused by the in-flight guard of a power action running on the same server.

    Returns the action's own RESULT (an ``ActionResult`` / ``MissionListResult``), whose ``success``,
    ``message`` and ``data`` the caller reads: ``data`` carries ``missions`` / ``listStartIndex`` /
    ``current`` / ``next`` on success, and a refused result carries the action's own sentence (the
    server could not be reached, the missions directory is missing) for an EXPLAINED empty state.
    ``None`` only for an identity the request cannot resolve (the gate refuses that case anyway).
    """
    if ctx is None:
        identity = _identity(request)
        if identity is None:
            return None
        ctx = _context(request, identity)
    return await read_action("get_mission_list", ctx, server_name=server_name)


async def download_mission(request: Request, server_name: str, mission, *,
                           ctx: ActionContext | None = None):
    """The bytes of ONE mission of *server_name*, through the ``download_mission`` READ action.

    ``mission`` is an INDEX (1-based) or a NAME — NEVER a path: the action resolves the newest copy
    and validates the resolved path against ``missions_dir`` itself, so the console hands over a
    named mission and nothing that a request could turn into an arbitrary file read. A READ: it goes
    through ``read_action`` (unguarded, unaudited) and returns the action's own result — ``content``
    (bytes) and ``filename`` (the LOGICAL ``.miz`` name) on success, a typed refusal otherwise.
    ``None`` only for an identity the request cannot resolve.
    """
    if ctx is None:
        identity = _identity(request)
        if identity is None:
            return None
        ctx = _context(request, identity)
    return await read_action("download_mission", ctx, server_name=server_name, mission=mission)


# ------------------------------------------------------------------------ the mission-list writes
# The FOUR controls of the Missions tab: add (with the file picker), remove, load and upload. Same
# shape as the config faces above — identity, form, scoped resolution, action, one-shot notice,
# redirect back to the SERVER'S OWN missions tab — with the ONE difference that this tab posts to
# four different actions, each with its own capability, so the tab can OMIT a control its viewer may
# not use (the console's rule: omit, never draw disabled).


async def addable_missions(request: Request, server_name: str, *, ctx: ActionContext | None = None):
    """The ADD picker's list — through the ``get_addable_missions`` READ action, or ``None``.

    The console reads the pickable files with an ACTION, so it never walks ``missions_dir`` itself and
    never re-implements the exclude rule (already-installed, ``.dcssb``, ``ignore_dirs``): the action
    is the ONE owner of that rule, shared with ``/mission add``'s autocomplete. Returns the action's
    result; a refused result carries the action's own sentence for an EXPLAINED empty picker.
    """
    if ctx is None:
        identity = _identity(request)
        if identity is None:
            return None
        ctx = _context(request, identity)
    return await read_action("get_addable_missions", ctx, server_name=server_name)


def mission_write_available(kind: str) -> bool:
    """Whether the mission action *kind* ('add' / 'delete' / 'load' / 'upload') is registered HERE.

    The ``action_available`` rule: an installation whose ``mission`` plugin is not loaded ships no
    mission write, so the tab does not offer a control whose only possible answer is the action's own
    \"not available in this installation\" refusal.
    """
    action = next((x for x in MISSION_ACTIONS if x.key == f"mission_{kind}"), None)
    return action is not None and action_available(action.qualname)


def missions_write_available() -> bool:
    """Whether ANY mission write is registered — the tab draws the write cards only then."""
    return any(action_available(x.qualname) for x in MISSION_ACTIONS)


def _mission_back_path(name: str) -> str:
    """The Missions tab of *name* — the ONE encoder, so a rename-safe redirect and the tab's own URL agree."""
    return server_url(name, tab="missions")


def _mission_expected_names(values) -> dict[int, str]:
    """The ``{index: logical name}`` map a mission form posts beside its selection — the STALE-PAGE
    cross-check the action runs.

    Each value is ``"<1-based index>=<logical name>"`` (see :data:`MISSION_NAME_FIELD`); a value that
    does not parse, or names no mission, is dropped — it cannot identify a row. An empty map means the
    request carried no names (an older page, or a crafted POST), and the action then runs no check.
    """
    expected: dict[int, str] = {}
    for value in values:
        index_text, sep, name = readmodels.text(value).partition("=")
        if sep and name and index_text.lstrip("-").isdigit():
            expected[int(index_text)] = name
    return expected


def _mission_expected_name(value) -> str:
    """The ONE logical name a row form posts (``"<index>=<name>"``) — ``""`` when none is carried."""
    index_text, sep, name = readmodels.text(value).partition("=")
    return name if sep else readmodels.text(value)


async def _resolve_mission_write(request: Request, form, action: WriteAction):
    """Shared prologue of the four mission writes: identity, scoped target, canonical name.

    Returns ``(ctx, canonical)`` on success, or a ``RedirectResponse`` when the target is authorized
    but unknown (a one-shot notice is stored first) — so every mission write refuses an out-of-scope
    target with the console's OWN 403 (never disclosing whether the name exists, which RAISES) and an
    unknown target with a notice + redirect, exactly like the config faces.
    """
    identity = _identity(request)
    if identity is None:
        _refusal_notice(request, action, "Not authorized (no signed-in identity).")
        raise HTTPException(status_code=403,
                            detail="Not authorized (no signed-in identity).")
    name = readmodels.text(form.get(SERVER_CONFIG_FIELD))
    ctx = _context(request, identity)
    resolution = await ctx.resolve_scoped_server(name)
    if resolution.status == ServerResolution.NOT_PERMITTED:
        _refusal_notice(request, action, REFUSAL_OUT_OF_SCOPE)
        raise dashboard_page.out_of_scope_refusal()
    if not resolution.is_found:
        result = await audit_action(ctx, ActionResult(success=False, message=resolution.message))
        _remember_notice(request, action, result)
        return RedirectResponse(_servers_path(), status_code=303)
    canonical = readmodels.text(readmodels.safe(lambda: getattr(resolution.server, "name", ""))) or name
    return ctx, canonical


def _mission_add_handler(action: WriteAction):
    """The route for ``missions.add`` — the picker's path, its two load options, one POST."""
    async def handle(request: Request) -> RedirectResponse:
        form = await request.form()
        resolved = await _resolve_mission_write(request, form, action)
        if isinstance(resolved, RedirectResponse):
            return resolved
        ctx, canonical = resolved
        result = await call_action(
            action.qualname, ctx, audit_result=True, server_name=canonical,
            path=readmodels.text(form.get(MISSION_PATH_FIELD)),
            autostart=_as_bool(_last_form_value(form, MISSION_AUTOSTART_FIELD)),
            load=_as_bool(_last_form_value(form, MISSION_LOAD_FIELD)))
        _remember_notice(request, action, result)
        return RedirectResponse(_mission_back_path(canonical), status_code=303)

    return handle


def _mission_delete_handler(action: WriteAction):
    """The route for ``missions.delete`` — the running-mission refusal is the ACTION's own sentence.

    DESTRUCTIVE, so it refuses a POST that did not come through its dialog: the control that opens it
    (the API/legacy path — the Missions tab's per-row trashcan was removed in M9) posts to
    ``<path>/confirm``, that dialog's form carries the one-shot token minted for THIS (server, mission),
    and a caller who skips the dialog has no token — a confirmation curl can bypass is decoration. The
    token is spent by the first POST.
    """
    async def handle(request: Request) -> RedirectResponse:
        form = await request.form()
        resolved = await _resolve_mission_write(request, form, action)
        if isinstance(resolved, RedirectResponse):
            return resolved
        ctx, canonical = resolved
        target = readmodels.text(form.get(MISSION_FIELD))
        if action.confirm and not consume_confirm_token(
                request, action.path, _mission_target(canonical, target),
                readmodels.text(form.get(CONFIRM_FIELD))):
            _refusal_notice(request, action,
                            "Not authorized (this action must be confirmed through its dialog).")
            raise HTTPException(
                status_code=403,
                detail="Not authorized (this action must be confirmed through its dialog).")
        mission = int(target) if target.isdigit() else target
        result = await call_action(
            action.qualname, ctx, audit_result=True, server_name=canonical, mission=mission,
            expected_name=_mission_expected_name(form.get(MISSION_NAME_FIELD)),
            delete_from_disk=_as_bool(_last_form_value(form, MISSION_DISK_FIELD)))
        _remember_notice(request, action, result)
        return RedirectResponse(_mission_back_path(canonical), status_code=303)

    return handle


def _mission_bulk_delete_handler(action: WriteAction):
    """The route for ``delete_missions`` (M5) — the SELECTION rides repeated ``mission`` fields.

    The dialog is the selection (a checkbox per configured mission, ``name="mission"``, each value
    the 1-based index), so the confirm POST can carry several. DESTRUCTIVE, so it refuses a POST that
    did not come through its dialog: the token minted for THIS (server, bulk) target must be spent
    here — a confirmation curl can bypass is decoration. A selection that names no index is handed to
    the ACTION as an empty list, which answers its own no-op sentence (never an invented one here).
    """
    async def handle(request: Request) -> RedirectResponse:
        form = await request.form()
        resolved = await _resolve_mission_write(request, form, action)
        if isinstance(resolved, RedirectResponse):
            return resolved
        ctx, canonical = resolved
        if not consume_confirm_token(request, action.path, _mission_target(canonical, "bulk"),
                                     readmodels.text(form.get(CONFIRM_FIELD))):
            _refusal_notice(request, action,
                            "Not authorized (this action must be confirmed through its dialog).")
            raise HTTPException(
                status_code=403,
                detail="Not authorized (this action must be confirmed through its dialog).")
        selected: list[int] = []
        for value in form.getlist(MISSION_FIELD):
            text = readmodels.text(value)
            if text.lstrip("-").isdigit():
                selected.append(int(text))
        result = await call_action(
            action.qualname, ctx, audit_result=True, server_name=canonical, missions=selected,
            expected_names=_mission_expected_names(form.getlist(MISSION_NAME_FIELD)),
            delete_from_disk=_as_bool(_last_form_value(form, MISSION_DISK_FIELD)))
        _remember_notice(request, action, result)
        return RedirectResponse(_mission_back_path(canonical), status_code=303)

    return handle


def _mission_reorder_handler(action: WriteAction):
    """The route for ``reorder_mission`` (M5) — one row's up/down button, posted directly.

    The 1-based ``missionList`` index rides the body beside the direction (``up`` / ``down``), both
    under their own field names, so a control can never be pointed at another mission by editing a
    URL. The action owns the bounds (an up on the first row is a no-op) and the offline-only gate.
    """
    async def handle(request: Request) -> RedirectResponse:
        form = await request.form()
        resolved = await _resolve_mission_write(request, form, action)
        if isinstance(resolved, RedirectResponse):
            return resolved
        ctx, canonical = resolved
        target = readmodels.text(form.get(MISSION_FIELD))
        mission = int(target) if target.isdigit() else target
        result = await call_action(
            action.qualname, ctx, audit_result=True, server_name=canonical, mission=mission,
            direction=readmodels.text(form.get(MISSION_DIRECTION_FIELD)),
            expected_name=_mission_expected_name(form.get(MISSION_NAME_FIELD)))
        _remember_notice(request, action, result)
        return RedirectResponse(_mission_back_path(canonical), status_code=303)

    return handle


def _mission_activate_handler(action: WriteAction):
    """The route for ``set_active_mission`` (M7/M11) — the bar's ``Set as start``, posted directly.

    M11 MOVED this control from the mission row to the selection bar (Frank's ruling: tick the mission,
    press the bar), so its target is the TICK — the same selection form the bulk remove uses, sent here
    by a ``formaction``. The route therefore reads the REPEATED ``mission`` field and serves ONLY a
    selection of EXACTLY ONE: none names nothing and several names no single mission, so BOTH are
    refused HERE, in the console's own sentence (:data:`MISSIONS_ACTIVATE_SELECTION_REFUSAL`) — the
    answer a no-JavaScript submit and a crafted POST both meet, so the path can never depend on
    ``static/mission-select.js``.

    The 1-based ``missionList`` index and, for the STALE-PAGE cross-check, the chosen row's LOGICAL name
    ride the body. The bar posts the ``<index>=<logical name>`` map one entry per row, so the name for
    the CHOSEN index is read off that map (``_mission_expected_names``) — never a single last value,
    which would be the wrong row. The action owns the resolve, the ``LOADING`` refusal and the
    ``setStartIndex`` call.
    """
    async def handle(request: Request) -> RedirectResponse:
        form = await request.form()
        resolved = await _resolve_mission_write(request, form, action)
        if isinstance(resolved, RedirectResponse):
            return resolved
        ctx, canonical = resolved
        chosen = [readmodels.text(value) for value in form.getlist(MISSION_FIELD)
                  if readmodels.text(value)]
        if len(chosen) != 1:
            # NONE or SEVERAL: the tick names no single mission. The console's OWN sentence, stored as
            # the one-shot notice so the honest reason is read on the Missions tab they land back on.
            result = await audit_action(ctx, ActionResult(
                success=False, message=MISSIONS_ACTIVATE_SELECTION_REFUSAL))
            _remember_notice(request, action, result)
            return RedirectResponse(_mission_back_path(canonical), status_code=303)
        target = chosen[0]
        mission = int(target) if target.isdigit() else target
        expected_name = ""
        if target.isdigit():
            expected_name = _mission_expected_names(
                form.getlist(MISSION_NAME_FIELD)).get(int(target), "")
        result = await call_action(
            action.qualname, ctx, audit_result=True, server_name=canonical, mission=mission,
            expected_name=expected_name)
        _remember_notice(request, action, result)
        return RedirectResponse(_mission_back_path(canonical), status_code=303)

    return handle


def _mission_load_handler(action: WriteAction):
    """The route for ``missions.load`` — ``load_mission``'s own int parameter is 0-BASED, while the
    tab SHOWS the 1-based ``missionList`` index, so a numeric target is converted here (a name is
    passed through and resolved by the action). No other translation: the action owns the load.

    A NON-POSITIVE numeric target is refused HERE, in the 1-based sentence
    (:func:`mission_index_required`) — converting ``"0"`` to ``-1`` would hand the action an index
    the operator never typed and answer "Mission index -1 out of range", which is not a message
    about what they asked for.
    """
    async def handle(request: Request) -> RedirectResponse:
        form = await request.form()
        resolved = await _resolve_mission_write(request, form, action)
        if isinstance(resolved, RedirectResponse):
            return resolved
        ctx, canonical = resolved
        target = readmodels.text(form.get(MISSION_FIELD))
        if target.isdigit() and int(target) < 1:
            result = await audit_action(ctx, ActionResult(
                success=False, message=mission_index_required(target)))
            _remember_notice(request, action, result)
            return RedirectResponse(_mission_back_path(canonical), status_code=303)
        mission = (int(target) - 1) if target.isdigit() else target
        result = await call_action(action.qualname, ctx, audit_result=True,
                                   server_name=canonical, mission=mission,
                                   expected_name=_mission_expected_name(
                                       form.get(MISSION_NAME_FIELD)))
        _remember_notice(request, action, result)
        return RedirectResponse(_mission_back_path(canonical), status_code=303)

    return handle


#: The multipart envelope a one-file upload carries around the file itself: the boundary lines, the
#: part headers and the small text fields. Bounded far above the real thing (a few hundred bytes) and
#: far below any real ``.miz``, so the declared-body check can never refuse a file that FITS.
_MULTIPART_ENVELOPE_BYTES = 64 * 1024

#: The chunk the upload is read in, so the cap is enforced AS the bytes arrive.
_UPLOAD_CHUNK = 64 * 1024


async def _read_upload_capped(upload: Any, limit: int) -> bytes | None:
    """Read an ``UploadFile`` in chunks, returning ``None`` the moment it crosses *limit*.

    The accumulated buffer never exceeds ``limit + one chunk``, so an oversized upload cannot make
    the console hold it in memory first — the read STOPS at the failure instead of completing and
    being measured afterwards.
    """
    buffer = bytearray()
    while True:
        piece = await upload.read(_UPLOAD_CHUNK)
        if not piece:
            return bytes(buffer)
        buffer.extend(piece)
        if len(buffer) > limit:
            return None


def _mission_upload_handler(action: WriteAction):
    """The route for ``missions.upload`` — a MULTIPART form (the file + its two load options).

    The console hands the file to the ``upload_mission`` ACTION as BYTES (the ``web`` transport may
    never name a path), so the action's traversal gate, size cap and ONE writer all apply unchanged.
    The upload's two load options (``autostart`` = load at next start, ``load`` = load now) ride the
    same POST and are handed to the action, which owns the load handling.

    THE CAP IS REFUSED AS THE SIZE CROSSES IT, never after the whole file has been buffered:

    1. the request's DECLARED ``Content-Length`` is checked BEFORE the body is parsed — a body that
       cannot fit the cap (plus the multipart envelope) is refused with a 413 without the console
       ever reading it;
    2. the parsed part's own size (``UploadFile.size``, the bytes Starlette counted for the part) is
       checked before the file is read at all — an oversized file is refused, never ``read()``;
    3. the bytes are then read in CHUNKS with a running total, so a body that lied about its length
       (or arrived chunked, with no ``Content-Length``) is refused the moment the total crosses the
       cap instead of being accumulated whole first.

    Every cap refusal is the ACTION's own sentence (:func:`_upload_cap_message`), so an operator
    cannot tell which of the two surfaces refused.
    """
    async def handle(request: Request) -> RedirectResponse:
        declared_body = request.headers.get("content-length")
        if declared_body and declared_body.isdigit() \
                and int(declared_body) > MISSIONS_UPLOAD_MAX_BYTES + _MULTIPART_ENVELOPE_BYTES:
            # The BODY alone cannot fit the cap: refuse BEFORE ``request.form()`` buffers it. No
            # server name is known yet (it is a field IN the body), so this is the one mission
            # refusal that cannot be a notice on the tab.
            raise HTTPException(status_code=413, detail=_upload_cap_message())
        form = await request.form()
        resolved = await _resolve_mission_write(request, form, action)
        if isinstance(resolved, RedirectResponse):
            return resolved
        ctx, canonical = resolved
        upload = form.get(MISSION_FILE_FIELD)
        is_file = upload is not None and not isinstance(upload, str)
        declared_size = getattr(upload, "size", None) if is_file else None
        data: bytes | None = None
        over_cap = False
        if is_file:
            if isinstance(declared_size, int) and declared_size > MISSIONS_UPLOAD_MAX_BYTES:
                # REFUSE BY WHAT IS DECLARED: an oversized part is never read into memory.
                over_cap = True
            else:
                data = await _read_upload_capped(upload, MISSIONS_UPLOAD_MAX_BYTES)
                over_cap = data is None
        if over_cap:
            result = await audit_action(ctx, ActionResult(
                success=False, message=_upload_cap_message()))
            _remember_notice(request, action, result)
            return RedirectResponse(_mission_back_path(canonical), status_code=303)
        if not is_file:
            # NO file part at all is the console's own "required" sentence; an EMPTY file is handed
            # to the action, which owns the empty-content refusal (one sentence, one owner).
            result = await audit_action(ctx, ActionResult(
                success=False, message="A mission file (.miz) is required to upload."))
            _remember_notice(request, action, result)
            return RedirectResponse(_mission_back_path(canonical), status_code=303)
        filename = readmodels.text(getattr(upload, "filename", "") or "")
        result = await call_action(
            action.qualname, ctx, audit_result=True, server_name=canonical, name=filename,
            source=bytes(data or b""),
            autostart=_as_bool(_last_form_value(form, MISSION_AUTOSTART_FIELD)),
            load=_as_bool(_last_form_value(form, MISSION_LOAD_FIELD)))
        _remember_notice(request, action, result)
        return RedirectResponse(_mission_back_path(canonical), status_code=303)

    return handle


# -------------------------------------------- the mission DIALOGS (add / remove), one component
# The two mission controls reached through ``confirm.html`` (``MISSION_DIALOG_KEYS``). BOTH use the
# console's ONE dialog component and the SAME prologue as the mission writes: identity, scoped
# resolution, the action's own refusals. The ADD dialog carries the file picker AND the load options
# (choosing and confirming in one dialog — Frank's "decisive" rule); the REMOVE dialog carries the
# "also delete the file?" checkbox and, being destructive, a one-shot confirm token.

#: The states ``load_mission`` accepts — the ONLY states in which the ADD dialog's "load it now" box
#: is offered, the SAME gate the tab's per-row Load control uses.
MISSION_LOAD_STATES: tuple[str, ...] = ("RUNNING", "PAUSED", "STOPPED")

#: The state in which the bot's ``loadMission`` ALSO moves the rotation pointer — its ``STOPPED``
#: branch sets ``listStartIndex``/``current`` to the loaded mission and STARTS the server
#: (``core/data/impl/serverimpl.py:1384-1388``). In ``RUNNING``/``PAUSED`` it merely sends the load
#: (``startMission``) and leaves the start index alone. Read here so the tab can word its per-row
#: ``Load`` control honestly IN THE STATE THE ROW IS RENDERED IN: at ``STOPPED`` a load becomes the
#: start mission; while the server is up it does not.
MISSION_LOAD_SETS_START_STATES: tuple[str, ...] = ("STOPPED",)


def _mission_target(server_name: str, mission) -> str:
    """The confirm store's key for a mission write: the server AND the mission, as one string.

    A server's key is its name; a mission's must carry the mission too, or two pending removals on one
    server (two rows, two dialogs) would share a single token — and spending one would refuse the
    other.
    """
    return f"{server_name}|{readmodels.text(mission)}"


def mission_load_allowed(request: Request, server: Any) -> bool:
    """Whether the ADD dialog may offer the "load it now" box for *server*.

    The SAME predicate ``_missions_write_context`` runs for the tab's Load control: the capability,
    the action being registered here, and the state ``load_mission`` accepts. Kept beside the dialog
    so the box and the control cannot disagree about when a load may be asked for.
    """
    roles = permissions.role_names_for(request)
    manager = permissions.manages_console(request)
    state = state_of(getattr(server, "status", None))
    return (permissions.allows(MISSIONS_LOAD_CAPABILITY, roles, manager=manager)
            and mission_write_available("load")
            and state in MISSION_LOAD_STATES)


async def _mission_picker(request: Request, server_name: str, ctx: ActionContext) -> list[dict]:
    """The ADD dialog's picker options — the UNCONFIGURED files, read through the action (never the
    request), each as ``{"value": path, "label": path}``."""
    found = await addable_missions(request, server_name, ctx=ctx)
    options: list[dict] = []
    if found is None or not bool(getattr(found, "success", False)):
        return options
    data = getattr(found, "data", None)
    for entry in (data.get("missions") or [] if isinstance(data, dict) else []):
        if not isinstance(entry, dict):
            continue
        path = readmodels.text(entry.get("path"))
        if path:
            options.append({"value": path, "label": path})
    return options


async def _mission_label_for(request: Request, server_name: str, target: Any,
                             ctx: ActionContext) -> str:
    """The logical name of the mission *target* (a 1-based index or a name) names — or ``\"\"``.

    Read through the ``get_mission_list`` ACTION, so the dialog's copy never trusts a name the browser
    sent: the mission named in the warning is the server's own.
    """
    result = await mission_list(request, server_name, ctx=ctx)
    if result is None or not bool(getattr(result, "success", False)):
        return ""
    data = getattr(result, "data", None)
    entries = data.get("missions") if isinstance(data, dict) else None
    wanted = readmodels.text(target).strip().casefold()
    for entry in (entries or []):
        if not isinstance(entry, dict):
            continue
        name = readmodels.text(entry.get("name"))
        index = readmodels.text(entry.get("index"))
        if index == wanted or name.casefold() == wanted \
                or os.path.basename(name).casefold() == wanted:
            return name
    return ""


def _mission_selected_indexes(values) -> list[int]:
    """The 1-based mission indexes a selection POST carried, in order, de-duplicated.

    The tab's selection is a repeated ``mission`` field (one per ticked row); the dialog opener reads
    the SAME field to show what was chosen. A value that is not a positive integer names nothing and
    is dropped — the action owns the range check, and an empty selection is an honest no-op.
    """
    selected: list[int] = []
    for value in values:
        text = readmodels.text(value)
        if text.lstrip("-").isdigit():
            index = int(text)
            if index >= 1 and index not in selected:
                selected.append(index)
    return selected


async def _mission_selection(request: Request, server_name: str, indexes: list[int],
                             ctx: ActionContext) -> tuple[list[str], str]:
    """The CHOSEN missions as ``(names, running_name)`` — resolved through the read action.

    ``names`` are the LOGICAL names the server's own list puts at each chosen 1-based index, in the
    selection's order (never a name the browser sent), so the dialog's read-only list is the server's
    truth and not a posting's. An index the list does not hold is shown as its ``#index`` so the
    dialog never silently drops a choice. ``running_name`` is the running (loaded) mission's name
    when it is among the chosen ones, else ``""`` — the dialog states that it cannot be removed.
    ``([], "")`` when the list cannot be read (the dialog then carries only the box + token).
    """
    result = await mission_list(request, server_name, ctx=ctx)
    if result is None or not bool(getattr(result, "success", False)):
        return [], ""
    data = getattr(result, "data", None)
    entries = data.get("missions") if isinstance(data, dict) else None
    by_index = {int(entry["index"]): entry for entry in (entries or [])
                if isinstance(entry, dict) and entry.get("index") is not None}
    names: list[str] = []
    running = ""
    for index in indexes:
        entry = by_index.get(index)
        if entry is None:
            names.append(f"#{index}")
            continue
        name = readmodels.text(entry.get("name"))
        names.append(name)
        if entry.get("current"):
            running = name
    return names, running


async def mission_confirm_context(request: Request, action: WriteAction, server: Any,
                                  server_name: str, form, ctx: ActionContext) -> dict:
    """Everything a MISSION dialog's page reads — the fourth sibling of :func:`confirm_context`.

    Same discipline, and the differences are the whole point of these two dialogs:

    * the ADD dialog (:attr:`mission_add`) renders the PICKER (the ``get_addable_missions`` READ action,
      never the request) and the load pair (``action.options``) — so choosing a mission and confirming
      happen in ONE dialog, with the load/autostart choice owned by it. The picker and the load pair sit
      in the BODY, beside what they modify (M11): they are form-associated to the footer's form, so the
      footer keeps exactly one cancel and one primary — the SAME shape M9 gave the removal dialog (a
      field drawn in the footer's form ahead of the button pushed the primary out of line). It mints NO
      token (adding is not destructive) and wears the OPTIONS chrome, like Startup's dialog;
    * the REMOVE dialog (:attr:`mission_delete`) names the mission (read through the ``get_mission_list``
      action, never a posted name) and carries the "also delete the file?" checkbox
      (``action.option``). It is DESTRUCTIVE, so it mints the one-shot token its own route refuses a
      POST without, and wears the confirmation chrome.

    ``back`` is the server's Missions tab (``_mission_back_path``), so the dialog's Cancel and its Esc
    return there — the mission writes always redirect to that tab, so this is the one place to go back
    to.
    """
    registrar = getattr(request.app.state, "webui_registrar", None)
    state = readmodels.overview(dashboard_page.request_source(request),
                                level=dashboard_page.log_level(request))
    label = html.escape(str(server_name))
    back = _mission_back_path(server_name)
    inputs: list[dict] = []
    # THE DIALOG BODY'S OWN FIELDS (M9): the fields rendered in ``.bd`` rather than the footer form —
    # the read-only selection ``list`` and the "delete the files from disk?" box that belongs beside
    # it. They are form-associated to the footer's form (``form="dlg-form"``), so the body carries the
    # choice and the footer still carries exactly one primary and one cancel. Empty for every other
    # dialog, whose markup is therefore unchanged.
    body_inputs: list[dict] = []
    #: the button's own words: the action's, except for the bulk dialog, whose label carries the COUNT
    #: so the label and the read-only list can never disagree.
    go = action.go
    hidden: list[tuple[str, str]] = [("server", server_name)]
    if action.key == "mission_add":
        picker = await _mission_picker(request, server_name, ctx)
        # THE PICKER AND THE LOAD PAIR LIVE IN THE BODY (M11): drawn in the dialog's `.bd` and
        # form-associated to the footer's `dlg-form` (the template's `field_input(field, 'dlg-form')`),
        # so the footer carries exactly one cancel and one primary — the options are NOT posted ahead of
        # the primary button, which is what pushed the primary out of the footer row's line.
        body_inputs.append({"type": "select", "name": MISSION_PATH_FIELD, "id": "path-dialog",
                            "label": "Mission file already on this server", "options": picker,
                            "required": True, "max": 0, "checked": False, "value": "",
                            "help": "Only files NOT already in the rotation are offered."})
        options = [option for option in action.options
                   if option.field != MISSION_LOAD_FIELD or mission_load_allowed(request, server)]
        body_inputs.extend(_option_fields(options))
        mission_label = ""
        heading = f"Add a mission to {label}?"
        target = label
        running_note = ""
        token_target = _mission_target(server_name, form.get(MISSION_FIELD))
    elif action.key == "mission_delete_bulk":
        # THE DIALOG IS A CONFIRMATION, NOT THE SELECTION (M9): the selection is made in the LIST
        # (a checkbox per row) and rides the POST as repeated ``mission`` fields. The dialog shows the
        # CHOSEN missions READ-ONLY (the new ``list`` input shape, the names the server's own list
        # resolves, never a posting) and the "delete the files from disk?" box beside it — the ONE
        # decision this dialog asks. The selection travels on as hidden fields, because the read-only
        # list is presentation and the write reads the fields.
        selected = _mission_selected_indexes(form.getlist(MISSION_FIELD))
        names, running = await _mission_selection(request, server_name, selected, ctx)
        body_inputs.append({"type": "list", "name": "", "value": "", "id": "", "label": "",
                            "max": 0, "required": False, "checked": False, "help": "",
                            "placeholder": "", "lines": tuple(names)})
        if action.option is not None:
            body_inputs.extend(_option_fields([action.option]))
        for index in selected:
            hidden.append((MISSION_FIELD, str(index)))
        # THE STALE-PAGE MAP: forward the ``<index>=<logical name>`` pair the TAB posted for every
        # row, so the bulk route can refuse a selection whose list changed between render and POST.
        for value in form.getlist(MISSION_NAME_FIELD):
            hidden.append((MISSION_NAME_FIELD, readmodels.text(value)))
        mission_label = ""
        count = len(selected)
        if count == 1 and names:
            heading = f"Remove {html.escape(names[0])} from the rotation?"
            go = "Remove mission"
        else:
            heading = f"Remove {count} missions from {label}?"
            go = f"Remove {count} missions" if count else "Remove missions"
        target = label
        running_note = (f" <b>The running mission ({html.escape(running)}) cannot be removed.</b>"
                        if running else "")
        token_target = _mission_target(server_name, "bulk")
    else:
        mission = form.get(MISSION_FIELD)
        hidden.append((MISSION_FIELD, readmodels.text(mission)))
        # THE STALE-PAGE CROSS-CHECK for the single REMOVE: the row control posts the mission's LOGICAL
        # name beside its index on the TAB, and it must travel THROUGH this dialog (the row form posts
        # HERE, and this dialog's form posts the write) so the delete route can refuse a page whose
        # list changed between the tab render and the confirm POST — the SAME guard the bulk dialog
        # and the reorder/load rows carry.
        hidden.append((MISSION_NAME_FIELD, readmodels.text(form.get(MISSION_NAME_FIELD))))
        mission_label = html.escape(await _mission_label_for(request, server_name, mission, ctx))
        if action.option is not None:
            inputs.extend(_option_fields([action.option]))
        heading = f"Remove {mission_label} from the rotation?"
        target = mission_label or readmodels.text(mission)
        running_note = ""
        token_target = _mission_target(server_name, form.get(MISSION_FIELD))
    escape_values = {"server": label, "mission": mission_label}
    token = mint_confirm_token(request, action.path, token_target) if action.confirm else ""
    dialog = {
        "heading": heading,
        "warning": action.warning.format(**escape_values) + running_note,
        "detail": action.detail.format(**escape_values),
        "go": go,
        "go_title": action.go_title.format(**escape_values),
        "path": action.path,
        "server": server_name,
        "target": target,
        "hidden": tuple(hidden),
        "inputs": tuple(inputs),
        "body_inputs": tuple(body_inputs),
        "tag": "confirm" if action.confirm else "options",
        "irreversible": bool(action.confirm),
        "danger": bool(action.danger),
        "confirm_field": CONFIRM_FIELD,
        "confirm_token": token,
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
    }
    return {
        "title": f"{action.label} — confirm",
        "page_title": heading,
        "crumb": f"{dashboard_page.CRUMB_GROUP} / {action.label}",
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                               current=_servers_path(),
                                               manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
        "back": back,
        "dialog": dialog,
    }


def _mission_confirm_handler(action: WriteAction):
    """The route that RENDERS a MISSION action's dialog (``confirm.html``). It changes nothing.

    The twin of :func:`_confirm_handler` for the two mission controls that go through their dialog.
    The target is resolved HERE through the same scoped seam the action uses, with the same refusals —
    a name outside the caller's scope is the console's 403, the SAME answer as a name that does not
    exist, and a name that genuinely does not exist is an honest 404.
    """
    async def handle(request: Request) -> HTMLResponse:
        identity = _identity(request)
        if identity is None:
            raise HTTPException(status_code=403,
                                detail="Not authorized (no signed-in identity).")
        form = await request.form()
        name = readmodels.text(form.get(SERVER_CONFIG_FIELD))
        ctx = _context(request, identity)
        resolution = await ctx.resolve_scoped_server(name)
        if resolution.status == ServerResolution.NOT_PERMITTED:
            raise dashboard_page.out_of_scope_refusal()
        if not resolution.is_found:
            raise HTTPException(status_code=404, detail=f"No server named '{name}'.")
        environment = getattr(request.app.state, "webui_templates", None)
        if environment is None:  # pragma: no cover - installed by the shell
            raise HTTPException(status_code=503,
                                detail="The admin web UI templates are not installed.")
        canonical = readmodels.text(readmodels.safe(
            lambda: getattr(resolution.server, "name", ""))) or name
        dialog = await mission_confirm_context(request, action, resolution.server, canonical, form, ctx)
        # no-store: the dialog is a console page too, and a browser must re-render it rather than
        # serve it from its cache (the console's pages set this for themselves; `live.py` does the
        # same for its poll).
        return HTMLResponse(environment.get_template(CONFIRM_TEMPLATE).render(**dialog),
                            headers={"Cache-Control": "no-store"})

    return handle


def coalition_values_from_form(current, form) -> dict:
    """The CHANGED coalition passwords a submitted form carries — and ONLY those.

    ``current`` is what :func:`coalition_values` read for the page (the cleartext the field was rendered
    with, and whether each hash is set); each row's submitted value is diffed against it, so an
    untouched form submits nothing and the action is handed exactly the changed coalitions. The rules,
    stated so the template and this cannot disagree:

    * a row the form does not STATE is untouched;
    * an EMPTIED field that shipped HOLDING a cleartext is the explicit clear (``None``) — the same
      way the DCS server password is removed;
    * an EMPTIED field that shipped EMPTY does NOTHING (blank is a no-op — Frank's standing rule), so
      a password the bot knows only as a hash (its field renders empty) cannot be wiped by a blank;
    * a value equal to the cleartext the page showed is unchanged;
    * anything else is the new password, submitted verbatim.
    """
    current = current if isinstance(current, dict) else {}
    values: dict[str, Any] = {}
    for token in server_config.COALITION_TOKENS:
        key = f"{token}Password"
        known = current.get(token)
        known_text = "" if known is None else str(known)
        if not _form_has(form, key):
            continue                                  # not stated by this form: unchanged
        posted = _last_form_value(form, key)
        if not posted.strip():
            if known_text.strip():
                values[key] = None                    # emptied a field that held a value: the clear
            continue                                  # a field that shipped empty: blank is a no-op
        if known_text and posted == known_text:
            continue                                  # the value the page showed: unchanged
        values[key] = posted
    return values


def coalitions_write_available() -> bool:
    """Whether the coalition action is registered in THIS process (the ``action_available`` rule)."""
    return action_available(SERVER_COALITION_ACTIONS[0].qualname)


def _coalition_handler(action: WriteAction):
    """The route for THE COALITION WRITE — one POST, no dialog.

    Same order as the channels write (identity, form, scoped resolution, action, notice, redirect),
    with the same two guarantees and one difference: the body is a set of CHANGED coalition passwords,
    diffed here against what the page read (:func:`coalition_values_from_form`), so an untouched form
    submits nothing; and NO pulse is recorded — no observable moves for this write, so the one-shot
    notice carries the outcome alone.
    """
    async def handle(request: Request) -> RedirectResponse:
        identity = _identity(request)
        if identity is None:
            _refusal_notice(request, action, "Not authorized (no signed-in identity).")
            raise HTTPException(status_code=403,
                                detail="Not authorized (no signed-in identity).")
        form = await request.form()
        name = readmodels.text(form.get(SERVER_CONFIG_FIELD))
        ctx = _context(request, identity)
        resolution = await ctx.resolve_scoped_server(name)
        if resolution.status == ServerResolution.NOT_PERMITTED:
            _refusal_notice(request, action, REFUSAL_OUT_OF_SCOPE)
            raise dashboard_page.out_of_scope_refusal()
        if not resolution.is_found:
            result = await audit_action(ctx, ActionResult(success=False,
                                                          message=resolution.message))
            _remember_notice(request, action, result)
            return RedirectResponse(_servers_path(), status_code=303)
        server = resolution.server
        canonical = readmodels.text(readmodels.safe(lambda: getattr(server, "name", ""))) or name
        current = await coalition_values(request, canonical, ctx=ctx)
        values = coalition_values_from_form(current, form)
        result = await call_action(action.qualname, ctx, server_name=canonical, values=values)
        _remember_notice(request, action, result)
        return RedirectResponse(_config_back_path(canonical), status_code=303)

    return handle


def _handler(action: WriteAction):
    """The route for one write. One implementation, so every write is refused the same way."""

    async def handle(request: Request) -> RedirectResponse:
        identity = _identity(request)
        if identity is None:
            # The gate let the request in, so this is a resolver that answered without an identity
            # (a role resolver that cannot see one). Refuse rather than act as anybody. The refusal
            # is STORED for the page the person lands on: the write was submitted in the
            # background, so an exception alone would be seen by nobody.
            _refusal_notice(request, action, "Not authorized (no signed-in identity).")
            raise HTTPException(status_code=403,
                                detail="Not authorized (no signed-in identity).")
        form = await request.form()
        name = readmodels.text(form.get("server"))
        if action.confirm and not consume_confirm_token(request, action.path, name,
                                                        readmodels.text(form.get(CONFIRM_FIELD))):
            # A confirm-required write REFUSES a POST that did not come through its dialog:
            # the row's control posts to the dialog, the dialog's form carries the one-shot
            # token minted for this target, and a caller who skips the dialog has no token. A
            # confirmation curl can bypass is decoration, so this refusal is the control.
            #
            # The token is spent by the first POST, so the second takes this branch — and since the
            # dialog closes on press, the person is looking at the page they came from rather than at
            # this answer. The one-shot notice is what makes the refusal visible (see :func:`_refusal_notice`).
            _refusal_notice(request, action,
                            "Not authorized (this action must be confirmed through its dialog).")
            raise HTTPException(
                status_code=403,
                detail="Not authorized (this action must be confirmed through its dialog).")
        ctx = _context(request, identity)
        resolution = await ctx.resolve_scoped_server(name)
        if resolution.status == ServerResolution.NOT_PERMITTED:
            # The console's refusal for a named target outside the caller's scope. With the scoped
            # source this route feeds the seam, an out-of-scope name is simply absent from the bus,
            # so the answer here is the SAME as for a name that does not exist (that is the point —
            # a hoster cannot enumerate the fleet by probing names). The branch stays because the
            # ANSWER belongs to the route, not to the seam: a caller that ever hands it a broader
            # view must get the console's refusal rather than an empty message.
            #
            # This refusal happens BEFORE any action, so it carries no ActionResult and is NOT part
            # of the audit step below — the trail records the ACTION's own refusals (one entry per
            # attempt), and a scope refusal is the route's answer. The
            # notice names no target (the wording discloses nothing), and it is stored so a
            # background submit's refusal is SEEN.
            _refusal_notice(request, action, REFUSAL_OUT_OF_SCOPE)
            raise dashboard_page.out_of_scope_refusal()
        # the row's status AS SUBMITTED, and the flag AS SUBMITTED, read BEFORE the
        # action runs: the action may change them in the same call (pause flips the status immediately,
        # ``set_maintenance`` flips the flag immediately) while a DCS startup leaves the status
        # SHUTDOWN for about a minute — and it is that later change, the glyph swap, that the pulse
        # waits for. WHICH of the two the expectation watches is the ACTION's own declaration
        # (``WriteAction.observable``), so a power action waits on the status and the flag pair waits
        # on the flag: a status-keyed expectation for *Maintenance* was never satisfied (the status
        # never moves).
        submitted = _row_state(resolution.server) if resolution.is_found else ""
        submitted_flag = bool(readmodels.safe(
            lambda: getattr(resolution.server, "maintenance", False), False)) if resolution.is_found \
            else False
        # THE EXPECTATION IS RECORDED **BEFORE** THE ACTION RUNS, and that ordering is the
        # point: ``static/submit.js`` hands the page over the moment it fires the POST, so the fresh
        # render is issued at the same time as this request and must already find the expectation —
        # there is no cookie round-trip to wait for any more, because the store is process-side. A
        # write that then fails or is refused drops it again below, so the pulse never outlives a
        # failed write's notice.
        if resolution.is_found:
            remember_awaiting_change("server", name, action, submitted, submitted_flag)
        # THE COMMAND'S OWN OPTION, when the action declares one: Shutdown's and
        # Startup's ``maintenance`` box, parsed out of the same body and handed to the action as its
        # own parameter — one name for the field and the parameter (``NodeOption.field``), so the
        # route cannot post one thing and the action read another. A body stating an option this
        # console never sends is refused (never guessed at), exactly as the node routes do.
        params: dict[str, Any] = {"server_name": name}
        if action.option is not None:
            try:
                params[action.option.field] = option_value(action, form)
            except ValueError as ex:
                forget_awaiting_change("server", name)   # nothing will run: no expectation to keep
                raise HTTPException(status_code=400, detail=str(ex))
        if resolution.is_found:
            # ``audit_result=True``: the seam writes the ONE audit entry for this attempt, with the
            # resolved target as the server — see ``core/actions.call_action``. The trail belongs on
            # the ACTION, but the pilot's action is the ``mission`` plugin's own function, which this
            # route may not modify: without the flag the route would audit a result the seam had
            # already reported as unaudited, and the log would claim a trail that exists. When the
            # plugin's actions take the trail over, the flag goes away with the need.
            result = await call_action(action.qualname, ctx, audit_result=True, **params)
        else:
            # The request WAS authorized; the name genuinely does not exist. The honest answer is
            # the seam's typed refusal, rendered inline — never a 404 and never a silent success.
            # No action ran, so the trail is written here (one entry per ATTEMPT) — and it
            # never changes the outcome: a success stays a success even when the trail could not be
            # written.
            result = await audit_action(ctx, ActionResult(success=False,
                                                          message=resolution.message))
        _remember_notice(request, action, result)
        # ...AND A WRITE THAT DID NOT SUCCEED DROPS IT AT ONCE. The pulse is "you
        # started something and it has not moved yet" — a FAILED or REFUSED write has nothing to wait
        # for and must not blink while the notice beside it says it failed. A SUCCESSFUL write keeps
        # the entry recorded above (the observable it names is what ends it: the glyph swap, or the
        # ceiling — so only the control that was pressed pulses).
        if not (resolution.is_found and bool(getattr(result, "success", False))):
            forget_awaiting_change("server", name)
        # 303 back to the PAGE the control was rendered on — its own constant when the form carried a
        # known one, else the section default. A POST that re-rendered a table would re-POST on
        # refresh; the page re-reads its data, so it shows the state the action produced.
        return RedirectResponse(origin_path(request, form.get(ORIGIN_FIELD), _servers_path()),
                                status_code=303)

    return handle


def _confirm_handler(action: WriteAction):
    """The route that RENDERS one action's dialog. It changes nothing.

    TWO readers: a DESTRUCTIVE action's confirmation (Restart/Shutdown/Stop) and the FORM
    that collects a non-destructive action's OPTION (Startup). Both resolve the target here through
    the same scoped seam the action uses, and the numbers in the warning come from the resolved
    object: ``{players}`` is read off the live server, never echoed from the request.
    Only ``action.confirm`` mints the one-shot token — an option-only dialog has nothing for the
    action's route to refuse, and a token nothing spends is the dead knob the console forbids.

    THE REFUSALS ARE THE ACTION'S OWN, so a person cannot learn about a server by comparing the two
    pages: a name outside the caller's scope is the console's 403 — the SAME answer
    as a name that does not exist — and a name that genuinely does not exist is the honest 404 an
    unscoped caller gets from ``resolve_scoped_server``.
    """
    async def handle(request: Request) -> HTMLResponse:
        identity = _identity(request)
        if identity is None:
            raise HTTPException(status_code=403,
                                detail="Not authorized (no signed-in identity).")
        form = await request.form()
        name = readmodels.text(form.get("server"))
        ctx = _context(request, identity)
        resolution = await ctx.resolve_scoped_server(name)
        if resolution.status == ServerResolution.NOT_PERMITTED:
            raise dashboard_page.out_of_scope_refusal()
        if not resolution.is_found:
            raise HTTPException(status_code=404, detail=f"No server named '{name}'.")
        environment = getattr(request.app.state, "webui_templates", None)
        if environment is None:  # pragma: no cover - installed by the shell
            raise HTTPException(status_code=503,
                                detail="The admin web UI templates are not installed.")
        token = mint_confirm_token(request, action.path, name) if action.confirm else ""
        # the page the row was rendered on travels THROUGH the dialog: the row
        # posts here with the origin hidden field, and the dialog's own form re-emits it so the
        # action's route can 303 back there. Validated here too, so the dialog never echoes an
        # unknown page onward — but the route re-checks, because this field is attacker-modifiable.
        origin = origin_path(request, form.get(ORIGIN_FIELD), _servers_path())
        dialog = confirm_context(request, action, name, players_on(resolution.server), token, origin)
        # no-store: the dialog is a console page too, and a browser must re-render it rather than
        # serve it from its cache (the console's pages set this for themselves; `live.py` does the
        # same for its poll).
        return HTMLResponse(environment.get_template(CONFIRM_TEMPLATE).render(**dialog),
                            headers={"Cache-Control": "no-store"})

    return handle


async def _audit_refusal(ctx: ActionContext, action: WriteAction | PlayerAction | NodeAction,
                         why: str, target: str) -> None:
    """Record ONE audit entry for an attempt the ROUTE refused before any action ran.

    A scope-refused or permission-refused POST used to leave no audit row; that was acceptable for
    PAUSE but had to be revisited once the console carries DESTRUCTIVE writes. This is that revisit,
    and it is DELIBERATELY NARROW: only the actions that declare ``audit_refusals`` (the player pair
    and all three node operations) are recorded here.

    ``target`` is the caller's own spelling of what was aimed at — ``server 'X', ucid=Y`` or
    ``node 'X'`` — assembled by the calling route, so one message shape serves every surface. The
    message names the attempt and its target so an operator can see a probe in the trail; it
    discloses nothing to the CALLER, whose answer stays the console's own bare refusal — the audit is
    the operator's log, not a second response. A capability refusal never reaches this function at
    all: the app-level gate answers it before the handler, so no route code — this one included — can
    observe it.
    """
    if not getattr(action, "audit_refusals", False):
        return
    await audit_action(ctx, ActionResult(
        success=False,
        message=f"refused {action.qualname}: {why} ({target})"))


def _last_form_value(form, name: str) -> str:
    """The LAST value a repeated form field carries, as text — ``""`` when the field is absent.

    LAST, not first, and that is the whole point (``write-form-state-contract.md`` §8): a checkbox
    sends the companion's ``off`` value and its own ``on`` value only when it is ticked, so the pair
    is read as one field whose LAST occurrence wins — which is also how the framework resolves a
    repeated field for a typed parameter. ``getlist`` is the MultiDict's own accessor; a form-like
    double that only answers ``get`` still works.
    """
    getlist: Any = getattr(form, "getlist", None)
    if callable(getlist):
        repeated = [value for value in (getlist(name) or ())]
        if repeated:
            return readmodels.text(repeated[-1])
    return readmodels.text(form.get(name))


def _form_has(form, name: str) -> bool:
    """Whether *name* was STATED by the submitted form at all — the difference between "unchanged"
    and "changed to empty" for a text/number field.

    A checkbox that is unchecked states NOTHING (the browser sends no field), which is why a bool is
    read from presence-as-truthiness instead; a text box the operator emptied still sends the field
    WITH an empty value, and that is a change. ``in`` covers both the MultiDict the framework hands the
    route and a plain dict double.
    """
    try:
        return name in form
    except TypeError:  # pragma: no cover - a form-like double without __contains__
        return form.get(name) is not None


def option_value(action: WriteAction | NodeAction, form) -> bool:
    """The boolean ONE action's option was submitted as — the command's own flag, parsed.

    Parsed, never truthiness-tested: ``bool('0')`` is ``True`` and a form sends strings, so a
    truthiness test would read the OFF value as ON (``write-form-state-contract.md`` §8). An ABSENT
    or empty value is "the form did not state it" and falls back to the declaration's ``default`` —
    the same value the checkbox was rendered checked with — while anything outside the known
    spellings raises :class:`ValueError`, which the route turns into a 400 rather than a guess.
    """
    option = action.option
    assert option is not None, "option_value is only called for an action with an option"
    posted = _last_form_value(form, option.field)
    if posted == "":
        return option.default
    if posted in (option.on, "true", "on", "yes"):
        return True
    if posted in (option.off, "false", "off", "no"):
        return False
    raise ValueError(f"the '{option.field}' option was not one this console sent")


def _node_handler(action: NodeAction):
    """The route for one NODE write — the third twin of :func:`_handler`.

    Same shape, same order (identity, form, confirm step, resolution, action, notice, redirect), and
    TWO differences, one per node family:

    * the target: a node names its machine in the field ``node``, and the resolution goes through
      :meth:`core.actions.ActionContext.resolve_scoped_node` — the seam's node twin, which keeps the
      not-found/not-permitted distinction for the same reason the server one does;
    * the OPTION: an action declaring one (``NodeAction.option``) reads it out of the same body and
      hands it to the action as its own parameter (``shutdown`` / ``startup``) — one name for the
      field and the parameter, so the route cannot post one thing and the action read another.

    ``action.confirm`` decides whether this route demands the one-shot token: the LIFECYCLE trio and
    the pair's offline half do, so a POST that skipped the dialog is refused; the online half does
    not, because it only clears a state — the mockup's own rule.
    """
    async def handle(request: Request) -> RedirectResponse:
        identity = _identity(request)
        if identity is None:
            _refusal_notice(request, action, "Not authorized (no signed-in identity).")
            raise HTTPException(status_code=403,
                                detail="Not authorized (no signed-in identity).")
        form = await request.form()
        name = readmodels.text(form.get("node"))
        if action.confirm and not consume_confirm_token(request, action.path, name,
                                                        readmodels.text(form.get(CONFIRM_FIELD))):
            # Same refusal as the server and player twins: the row's control posts to the dialog, the
            # dialog's form carries the one-shot token minted for THIS node, and a caller who skips
            # the dialog has no token. A confirmation curl can bypass is decoration.
            _refusal_notice(request, action,
                            "Not authorized (this action must be confirmed through its dialog).")
            raise HTTPException(
                status_code=403,
                detail="Not authorized (this action must be confirmed through its dialog).")
        ctx = _context(request, identity)
        params: dict[str, Any] = {"node_name": name}
        if action.option is not None:
            try:
                params[action.option.field] = option_value(action, form)
            except ValueError as ex:
                # A body stating an option this console never sends is not a form submission: refuse
                # it (never guess a boolean from it) and leave a row — these operations are in the
                # class that must leave a trail.
                await _audit_refusal(ctx, action, f"invalid option ({ex})", f"node '{name}'")
                raise HTTPException(status_code=400, detail=str(ex))
        resolution = await ctx.resolve_scoped_node(name)
        if resolution.status == NodeResolution.NOT_PERMITTED:
            # The console's refusal for a named target outside the caller's view — the SAME answer as
            # a name that does not exist. For these destructive operations the attempt is recorded
            # first, and the actor, the action and the target are named
            # so a probe is visible in the operator's trail. The person's own page gets the one-shot
            # notice, whose wording discloses nothing about the target.
            await _audit_refusal(ctx, action, "not permitted (outside your scope)",
                                 f"node '{name}'")
            _refusal_notice(request, action, REFUSAL_OUT_OF_SCOPE)
            raise dashboard_page.out_of_scope_refusal()
        # THE SERVERS THE OPERATION WILL MOVE, PREDICTED BEFORE THE ACTION RUNS. The
        # prediction reads the caller's own SCOPED view and — for ``online`` — the engine's power-off
        # RECORD, which the action CLEARS as part of reverting it: read it HERE, at the same moment the
        # action's own ``power_on`` reads it, or the post-action read would find nothing and predict
        # the no-record fallback. Nothing is seeded yet — a refused or failed write must seed nothing —
        # so the prediction is captured now and spent only once the write is accepted, below.
        canonical = readmodels.text(getattr(resolution.node, "name", "")) or name \
            if resolution.is_found else ""
        predicted_record = power_record(canonical) \
            if resolution.is_found and action.key == "online" else None
        if resolution.is_found:
            # No ``audit_result``: every node action writes its own trail, once, for every outcome
            # — including the refusals it produces itself.
            result = await call_action(action.qualname, ctx, **params)
        else:
            # The request WAS authorized; the node is offline or the name matches nothing. The honest
            # answer is the seam's typed refusal, rendered inline — never a 404 and never a silent
            # success. No action ran, so the trail is written here (one entry per ATTEMPT).
            result = await audit_action(ctx, ActionResult(success=False,
                                                          message=resolution.message))
        _remember_notice(request, action, result)
        # AN ACCEPTED node POWER write seeds the per-server expectation on the servers it started or
        # stopped, so their rows pulse on the servers page and the dashboard exactly as a per-server
        # write's do — and end by the identical rules (settled state / failure / ceiling). A refused or
        # failed node write seeds NOTHING, and the enumeration is the caller's own SCOPED view, so only
        # the actor's rows ever light up. The prediction rule (and its honesty) is in
        # :func:`node_moved_names`.
        if resolution.is_found and bool(getattr(result, "success", False)):
            node_moved_names(request, canonical, action, predicted_record)
        # "IMMEDIATELY AFTER AN UPGRADE IS ACCEPTED": a successful check-gated action
        # (Upgrade) re-checks THAT node at once, so the cache does not wait for the next sample. It
        # is FIRE-AND-FORGET — the check is an RPC and must not delay the person's redirect — and
        # the poller's own failure rule applies to it (a node already restarting keeps the last
        # known True, so the control is not withdrawn mid-upgrade).
        if resolution.is_found and bool(getattr(result, "success", False)) \
                and getattr(action, "check", False):
            asyncio.create_task(upgrade_signal.check_node(resolution.node))
        # 303 back to the PAGE the control was rendered on — the Dashboard's
        # Nodes tab and /nodes render the same row strip, so the write must not re-orient the person;
        # a POST that re-rendered a table would re-POST on refresh, and the page re-reads its data so
        # it shows the state the operation produced (for the master's own row that state may be
        # "gone", which is exactly what the dialog warned about).
        return RedirectResponse(origin_path(request, form.get(ORIGIN_FIELD), _nodes_path()),
                                status_code=303)

    return handle


def _node_confirm_handler(action: NodeAction):
    """The route that RENDERS one node action's dialog. It changes nothing.

    The twin of :func:`_confirm_handler`. The target is resolved HERE through the same scoped seam
    the action uses, so the figures in the warning come from the resolved node's load and never from
    the request — and the MASTER's own row gets the one extra sentence that says the
    console dies with it (:func:`node_confirm_context`).

    THE ONE-SHOT TOKEN IS MINTED ONLY FOR A CONFIRM-REQUIRED ACTION (``action.confirm``): the token
    exists so the ACTION's route can refuse a POST that skipped this page, and an operation that only
    clears a state has nothing to refuse (offline confirms, online does
    not). Minting one anyway would put a token in the session that nothing ever spends — the dead
    knob this console's own rules forbid, in the cookie that is size-bounded on purpose.

    THE REFUSALS ARE THE ACTION'S OWN, so a person cannot learn about a node by comparing the two
    pages: a node outside the caller's view is the console's 403 — the SAME answer as a name that
    does not exist — while a node the caller may see but which is OFFLINE answers 404 with the
    seam's own "offline or unknown" sentence, because there is no object to act on either way.
    """
    async def handle(request: Request) -> HTMLResponse:
        identity = _identity(request)
        if identity is None:
            raise HTTPException(status_code=403,
                                detail="Not authorized (no signed-in identity).")
        form = await request.form()
        name = readmodels.text(form.get("node"))
        ctx = _context(request, identity)
        resolution = await ctx.resolve_scoped_node(name)
        if resolution.status == NodeResolution.NOT_PERMITTED:
            await _audit_refusal(ctx, action, "not permitted (outside your scope)",
                                 f"node '{name}'")
            raise dashboard_page.out_of_scope_refusal()
        if not resolution.is_found:
            raise HTTPException(status_code=404, detail=resolution.message)
        environment = getattr(request.app.state, "webui_templates", None)
        if environment is None:  # pragma: no cover - installed by the shell
            raise HTTPException(status_code=503,
                                detail="The admin web UI templates are not installed.")
        token = mint_confirm_token(request, action.path, name) if action.confirm else ""
        # the page the row was rendered on travels THROUGH the dialog: the row
        # posts here with the origin hidden field, and this dialog re-emits it so the route can 303
        # back there. For the online half (no token) the dialog's POST runs the action directly, and
        # it still carries the origin — the person stays where they were either way.
        origin = origin_path(request, form.get(ORIGIN_FIELD), _nodes_path())
        dialog = node_confirm_context(request, action, name, resolution.node, token, origin)
        # no-store: the dialog is a console page too, and a browser must re-render it rather than
        # serve it from its cache (the console's pages set this for themselves; `live.py` does the
        # same for its poll).
        return HTMLResponse(environment.get_template(CONFIRM_TEMPLATE).render(**dialog),
                            headers={"Cache-Control": "no-store"})

    return handle


def _player_handler(action: PlayerAction):
    """The route for one PLAYER write. One implementation, so every player write is refused the
    same way as every server write — resolution first, then the action, then the one-shot notice.

    TWO differences from the server twin, both of them about destructive writes:

    * a CONFIRM-REQUIRED player write (kick, ban) refuses a POST that did not come through its
      dialog, exactly as the server writes do — and the token is keyed on BOTH halves of the target;
    * a refusal this route makes on behalf of a destructive action leaves an audit entry
      (:func:`_audit_refusal`); every other refusal is not audited here.
    """
    async def handle(request: Request) -> RedirectResponse:
        identity = _identity(request)
        if identity is None:
            _refusal_notice(request, action, "Not authorized (no signed-in identity).")
            raise HTTPException(status_code=403,
                                detail="Not authorized (no signed-in identity).")
        form = await request.form()
        name = readmodels.text(form.get("server"))
        ucid = readmodels.text(form.get("ucid"))
        ctx = _context(request, identity)
        if action.confirm and not consume_confirm_token(
                request, action.path, _player_target(name, ucid),
                readmodels.text(form.get(CONFIRM_FIELD))):
            # A confirm-required write REFUSES a POST that did not come through its dialog. The row's
            # control posts to the dialog, the dialog's form carries the one-shot token minted for THIS
            # (server, ucid), and a caller who skips the dialog has no token. A confirmation curl can
            # bypass is decoration, so this refusal is the control.
            _refusal_notice(request, action,
                            "Not authorized (this action must be confirmed through its dialog).")
            raise HTTPException(
                status_code=403,
                detail="Not authorized (this action must be confirmed through its dialog).")
        resolution = await ctx.resolve_scoped_server(name)
        if resolution.status == ServerResolution.NOT_PERMITTED:
            # The console's refusal for a named target outside the caller's scope — the SAME answer
            # as a name that does not exist, so a hoster cannot enumerate the fleet.
            # It happens before any action, so it carries no ActionResult; for the DESTRUCTIVE pair
            # the attempt is recorded first, and for every other player write the trail
            # is the ACTION's own (one entry per attempt). The person's page gets the
            # one-shot notice, whose wording discloses nothing about the target.
            await _audit_refusal(ctx, action, "not permitted (outside your scope)",
                                 f"server '{name}', ucid={ucid or '?'}")
            _refusal_notice(request, action, REFUSAL_OUT_OF_SCOPE)
            raise dashboard_page.out_of_scope_refusal()
        if resolution.is_found:
            # No ``audit_result``: every action this route reaches writes its own trail, once, for
            # every outcome — the flag belongs to a plugin action this route may not modify.
            params: dict[str, Any] = {target: readmodels.text(form.get(source))
                                      for source, target in action.params}
            if action.sender:
                params["sender"] = _sender(identity)
            result = await call_action(action.qualname, ctx, server_name=name, ucid=ucid,
                                       **params)
        else:
            # The request WAS authorized; the name genuinely does not exist. The honest answer is
            # the seam's typed refusal, rendered inline — never a 404 and never a silent success.
            # No action ran, so the trail is written here (one entry per ATTEMPT).
            result = await audit_action(ctx, ActionResult(success=False,
                                                          message=resolution.message))
        _remember_notice(request, action, result)
        # 303 back to the PAGE the control was rendered on — the Dashboard's
        # Players tab and /players render the same row strip, so the write must not re-orient the
        # person; never a re-render of a POST.
        return RedirectResponse(origin_path(request, form.get(ORIGIN_FIELD), _players_path()),
                                status_code=303)

    return handle


def _player_confirm_handler(action: PlayerAction):
    """The route that RENDERS one destructive PLAYER action's dialog. It changes nothing.

    The twin of :func:`_confirm_handler`, and it exists for the reason the mockup draws kick and ban
    as dialogs at all: those two need a REASON (and the ban a duration), so the dialog is where the
    person types while looking at the consequence — the field is asked for next to the warning that
    explains it, not on the row.

    THE REFUSALS ARE THE ACTION'S OWN, so a person cannot learn about a server by comparing the two
    pages: a name outside the caller's scope is the console's 403 — the SAME answer
    as a name that does not exist — and a server that genuinely does not exist is the honest 404 an
    unscoped caller gets. The player is resolved ON the scoped server, so a ucid that belongs
    elsewhere is simply not here.
    """
    async def handle(request: Request) -> HTMLResponse:
        identity = _identity(request)
        if identity is None:
            raise HTTPException(status_code=403,
                                detail="Not authorized (no signed-in identity).")
        form = await request.form()
        name = readmodels.text(form.get("server"))
        ucid = readmodels.text(form.get("ucid"))
        ctx = _context(request, identity)
        resolution = await ctx.resolve_scoped_server(name)
        if resolution.status == ServerResolution.NOT_PERMITTED:
            await _audit_refusal(ctx, action, "not permitted (outside your scope)",
                                 f"server '{name}', ucid={ucid or '?'}")
            raise dashboard_page.out_of_scope_refusal()
        if not resolution.is_found:
            raise HTTPException(status_code=404, detail=f"No server named '{name}'.")
        player = readmodels.safe(
            lambda: resolution.server.get_player(ucid=ucid, active=True), None)
        if player is None:
            raise HTTPException(status_code=404,
                                detail=f"No active player '{ucid}' on server '{name}'.")
        environment = getattr(request.app.state, "webui_templates", None)
        if environment is None:  # pragma: no cover - installed by the shell
            raise HTTPException(status_code=503,
                                detail="The admin web UI templates are not installed.")
        token = mint_confirm_token(request, action.path, _player_target(name, ucid))
        origin = origin_path(request, form.get(ORIGIN_FIELD), _players_path())
        dialog = player_confirm_context(request, action, resolution.server, player, name, ucid,
                                        token, origin)
        # no-store: the dialog is a console page too, and a browser must re-render it rather than
        # serve it from its cache (the console's pages set this for themselves; `live.py` does the
        # same for its poll).
        return HTMLResponse(environment.get_template(CONFIRM_TEMPLATE).render(**dialog),
                            headers={"Cache-Control": "no-store"})

    return handle


def _sender(identity: Identity) -> str:
    """The label an in-game message appears to come from: the identity's own shown name.

    The twin of what the Discord commands pass (``interaction.user.display_name``), read off the
    identity rather than from the form — a person cannot sign somebody else's name onto a message.
    """
    return readmodels.text(getattr(identity, "shown_name", "")
                           or getattr(identity, "display_name", ""))


def _identity(request: Request) -> Identity | None:
    """The signed-in identity of this request, or ``None`` (see the caller)."""
    manager = getattr(getattr(request.app, "state", None), "webui_auth", None)
    if manager is None:
        return None
    return manager.authenticate(request)


def _context(request: Request, identity: Identity) -> ActionContext:
    """The seam's context for this request: the caller's identity against the console's own cluster.

    ``bus`` is :class:`_Cluster` rather than the ``ServiceBus`` itself, and deliberately: the
    console's data has ONE seam (:func:`services.webservice.readmodels.console_source` — the live
    registry, or whatever a deployment pinned on ``app.state.webui_source_provider``), so a write
    resolves its target against the SAME cluster the page it came from rendered. The seam only ever
    reads ``bus.servers`` (``core/actions.py``), which is exactly what that carries.

    The SCOPE is the caller's own (``from_web`` reads it off the identity), and it is applied by
    ``resolve_scoped_server`` — the route never resolves a server itself.
    """
    # THE SCOPED SOURCE, through the ONE function pages may read it from (pinned by
    # tests/test_webui_scope_view.py: ``request_source`` is the only caller of the source seam in
    # this package). The seam's own resolution re-checks the scope, so a name the caller cannot see
    # is refused — and, because the bus only holds the caller's view, a name that does not exist and
    # a name outside the scope produce the SAME answer, which is the point: a hoster
    # cannot enumerate the fleet by probing names.
    source = dashboard_page.request_source(request)
    return ActionContext.from_web(identity, _node(), _Cluster(source))


def _node() -> Any:
    """The process's own node, when there is one. Read by no action; the seam's contract carries it."""
    try:
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        bus = ServiceRegistry.get(ServiceBus)
        return getattr(bus, "node", None) if bus is not None else None
    except Exception:
        return None


def _servers_path() -> str:
    """Where a SERVER write redirects back to when the form named no known page: the Servers page's
    own constant, never a typed string."""
    from . import servers as servers_page

    return servers_page.SERVERS_PATH


def origin_path(request: Request, posted: Any, section_default: str) -> str:
    """The page a write returns to: *posted* when the REGISTRY knows it, else *section_default*.

    THE ONE place the browser's destination is chosen by request data, and it is an ALLOW-LIST, never
    a redirect to raw input. The candidate travels in the form
    as :data:`ORIGIN_FIELD` (never in a URL), and it is accepted only if it EQUALS a path the registrar
    already knows — ``Registrar.page_paths``, the pages' own nav declarations, so no second mapping
    table can drift from the routes. The value RETURNED is that registry constant, not the posted
    string: a request can cause a redirect to a page of this console and to nothing else, so a crafted
    ``_origin=//evil.example`` or ``_origin=https://evil.example`` is discarded and the section default
    is used instead.

    A missing or unknown origin is not an error — it is the section default (``/servers`` for a server
    write, ``/nodes`` for a node write, ``/players`` for a player write), which is what every write
    redirects to and stays the honest answer for a caller that never sent one.
    """
    wanted = readmodels.text(posted)
    registrar = getattr(getattr(request, "app", None), "state", None)
    registrar = getattr(registrar, "webui_registrar", None)
    for path in tuple(getattr(registrar, "page_paths", ()) or ()):
        if path == wanted:
            return path
    return section_default


def _players_path() -> str:
    """Where a PLAYER write redirects back to: the Players page's own constant."""
    from . import players as players_page

    return players_page.PLAYERS_PATH


def _nodes_path() -> str:
    """Where a NODE write redirects back to: the Nodes page's own constant, never a typed string."""
    from . import nodes as nodes_page

    return nodes_page.NODES_PATH


class _Cluster:
    """The ``bus`` the seam is given: the servers the console reads, keyed by name — and the nodes.

    Duck-typed like every other seam in this package: ``core/actions.py`` reads ``bus.servers`` and
    ``bus.nodes`` and nothing else. A server with no readable name is skipped rather than keyed as
    ``""``, so it can never be the target of a write.

    ``nodes`` is the SAME SCOPED VIEW the page renders its node rows from, ``None`` values included:
    a node the cluster knows and cannot reach stays in the mapping as ``None``, which is exactly the
    fact the seam's :class:`~core.actions.NodeResolution` reports as NOT_FOUND ("offline or
    unknown"). For a caller whose view is restricted that mapping only holds the nodes carrying their
    own servers, so "not in the mapping" IS "not permitted" — the scope is applied once, by the
    source, and the resolver never re-implements it.
    """

    __slots__ = ("_source",)

    def __init__(self, source):
        self._source = source

    @property
    def servers(self) -> dict:
        out: dict = {}
        for server in getattr(self._source, "servers", ()) or ():
            name = readmodels.text(readmodels.safe(lambda: getattr(server, "name", "")))
            if name:
                out[name] = server
        return out

    @property
    def nodes(self) -> dict:
        """``{name: node-or-None}`` as the console's own source carries it — never filtered twice."""
        entries = getattr(self._source, "nodes", None)
        return dict(entries) if isinstance(entries, dict) else {}
