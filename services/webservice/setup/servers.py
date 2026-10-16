"""Step 5 of the tour — DCS servers: ONE screen with one row per detected Saved Games instance.

DETECTION IS READ FROM THE INSTALLER'S OWN HELPER, NEVER RE-DONE HERE. The instance list comes from
:func:`core.utils.findDCSInstances` — the same call ``install.py`` makes for its "DCS Server Setup"
block — and each instance's home is ``<SAVED_GAMES>/<instance>`` exactly as the installer builds it.
The scan is memoised in the tour's draft (one probe, read by every render), so the rail's decision to
keep or drop this step is a pure function of the answers.

THE ANSWERS ARE THE INSTALLER'S INPUTS. :class:`ServerRow` carries exactly what ``install.py``
collects per instance: the "manage this server?" answer, the server name, the three channel ids and
the autostart answer. :func:`instance_blocks` / :func:`servers_entries` / :func:`scheduler_entries`
turn them into the three shapes the installer writes (``nodes.yaml``'s ``instances`` entries through
the shared :func:`core.utils.setup_config.build_instance`, ``servers.yaml``'s channel blocks through
:func:`core.utils.setup_config.build_server_channels`, ``plugins/scheduler.yaml``'s autostart entries
through :func:`core.utils.setup_config.build_scheduler`). The per-index ports are the installer's own
formula (bot ports from 6666 up, counted over the MANAGED instances only).

NOTHING HERE WRITES A FILE and nothing here runs a schedule: the tour's draft
(:mod:`services.webservice.setup.state`) is the one destination, and the Review step builds the real
configuration from these answers.
"""

from __future__ import annotations

import logging
import os

from dataclasses import dataclass, field
from typing import Any, Mapping

from core import utils
from core.const import SAVED_GAMES
from core.utils import setup_config

__all__ = [
    "DEFAULT_BOT_PORT",
    "NO_CHANNEL",
    "MAX_CHANNEL_ID",
    "UNNAMED_INSTANCES",
    "ServerRow",
    "ServerAnswers",
    "detect_instances",
    "is_named",
    "answers_from_mapping",
    "non_secret_document",
    "managed_rows",
    "bot_ports",
    "instance_blocks",
    "servers_entries",
    "scheduler_entries",
    "parse_channel",
    "resolve_rows",
    "servername_duplicate",
]

log = logging.getLogger(__name__)

#: The first bot port the installer assigns to a managed instance.
DEFAULT_BOT_PORT = 6666

#: The installer's "no channel" id: the one negative value a channel field may carry, and what an
#: optional channel field parses an empty answer as.
NO_CHANNEL = -1

#: The largest Discord snowflake (an unsigned 64-bit id): the ceiling a channel id must stay under.
MAX_CHANNEL_ID = (1 << 64) - 1

#: The names the installer treats as "this instance has no server name yet" — the "give it a name"
#: case, whose row starts with an empty name field.
UNNAMED_INSTANCES = ("", "n/a", "DCS Server")


# --------------------------------------------------------------------------------------- the scan

def detect_instances() -> dict[str, str]:
    """The DCS server instances found on this host, as ``instance -> detected name`` pairs.

    A probe failure answers with an empty mapping so the step reports "nothing found" instead of
    failing to render.
    """
    try:
        return {str(name): str(server) for name, server in utils.findDCSInstances().items()}
    except Exception as ex:  # noqa: BLE001 - a probe never breaks a render
        log.warning("Setup tour: DCS instance detection failed (%s); reporting none.", ex)
        return {}


def is_named(name: str) -> bool:
    """Whether *name* is a real server name (the installer's "give it a name" case is not one)."""
    return str(name or "").strip() not in UNNAMED_INSTANCES


# ------------------------------------------------------------------------------------ the answers

@dataclass
class ServerRow:
    """One detected instance's row.

    The channel ids are kept AS TYPED: the field's value is what the operator wrote, and a row that
    fails validation must come back with it intact. They are read as numbers by :func:`parse_channel`
    when the step's answers are turned into configuration.
    """

    instance: str
    detected_name: str = ""
    manage: bool = True
    name: str = ""
    status: str = ""
    chat: str = ""
    admin: str = ""
    autostart: bool = True


@dataclass
class ServerAnswers:
    """Step 5's answers: the memoised scan and one row per detected instance."""

    #: whether the machine has been scanned (a draft without it has not seen this step yet)
    scanned: bool = False
    #: the scan result: ``instance -> detected name``, in the order the instances were found
    detected: dict[str, str] = field(default_factory=dict)
    #: one row per detected instance, in the same order
    rows: list[ServerRow] = field(default_factory=list)

    @property
    def has_instances(self) -> bool:
        """Whether anything was detected (the step leaves the rail when nothing was)."""
        return bool(self.detected)


def _as_str(value: Any) -> str:
    return "" if value in (None, False) else str(value)


def answers_from_mapping(data: Mapping | None, *, detected: Mapping | None = None) -> ServerAnswers:
    """Build the answers from the draft's ``servers`` block.

    A row exists only for an instance the scan found: an instance that disappeared between two
    scans loses its row (that is a machine fact, not an answer), while every still-detected
    instance keeps the answers it already carries. A detected instance's name defaults to the name
    the instance's own ``serverSettings.lua`` carries, and to an EMPTY field for the installer's
    "give it a name" case.
    """
    data = data if isinstance(data, Mapping) else {}
    scan = dict(detected if detected is not None else (data.get("detected") or {}))
    stored = data.get("rows")
    stored = stored if isinstance(stored, Mapping) else {}
    rows: list[ServerRow] = []
    for instance, detected_name in scan.items():
        instance = str(instance)
        detected_name = str(detected_name or "")
        previous = stored.get(instance)
        previous = previous if isinstance(previous, Mapping) else {}
        name = previous.get("name")
        rows.append(ServerRow(
            instance=instance,
            detected_name=detected_name,
            manage=bool(previous.get("manage", True)),
            name=_as_str(name) if name is not None else (
                detected_name if is_named(detected_name) else ""),
            status=_as_str(previous.get("status")),
            chat=_as_str(previous.get("chat")),
            admin=_as_str(previous.get("admin")),
            autostart=bool(previous.get("autostart", True)),
        ))
    return ServerAnswers(scanned=bool(data.get("scanned") or detected is not None),
                         detected={str(k): str(v or "") for k, v in scan.items()},
                         rows=rows)


def non_secret_document(answers: ServerAnswers) -> dict:
    """The draft-writable part of the answers (this step holds no secret)."""
    return {
        "scanned": bool(answers.scanned),
        "detected": dict(answers.detected),
        "rows": {row.instance: {"manage": row.manage, "name": row.name, "status": row.status,
                                "chat": row.chat, "admin": row.admin, "autostart": row.autostart}
                 for row in answers.rows},
    }


# ------------------------------------------------------------------------------------ the shapes

def managed_rows(answers: ServerAnswers) -> list[ServerRow]:
    """The rows the operator asked to manage, in scan order."""
    return [row for row in answers.rows if row.manage]


def bot_ports(answers: ServerAnswers) -> dict[str, int]:
    """The bot port per MANAGED instance — the installer's formula, counted over managed rows."""
    return {row.instance: DEFAULT_BOT_PORT + index
            for index, row in enumerate(managed_rows(answers))}


def instance_blocks(answers: ServerAnswers) -> dict[str, dict]:
    """The ``nodes.yaml`` ``instances`` entries, built by the installer's own builder.

    No extension keys are written here: the tour records only the node, its listen port and the
    detected instances. Extensions (SRS and the rest) are configured on the console's node page.
    """
    ports = bot_ports(answers)
    return {row.instance: setup_config.build_instance(
        home=os.path.join(SAVED_GAMES, row.instance), bot_port=ports[row.instance])
        for row in managed_rows(answers)}


def servers_entries(answers: ServerAnswers, *, discord: bool,
                    central_admin: bool = False) -> dict[str, dict]:
    """The ``servers.yaml`` entries, keyed by server name.

    On the no-Discord path a server carries no channels at all — the installer's own rule. A central
    admin channel (``bot.yaml``'s ``channels.admin``) also removes the per-server admin channel,
    which is why that decision is a parameter here.
    """
    if not discord:
        return {row.name: {} for row in managed_rows(answers)}

    def channels(row: ServerRow) -> dict:
        status = parse_channel(row.status) or 0
        chat = parse_channel(row.chat, none=NO_CHANNEL)
        admin = None if central_admin else parse_channel(row.admin, none=NO_CHANNEL)
        return {"channels": setup_config.build_server_channels(
            status=int(status), chat=int(NO_CHANNEL if chat is None else chat),
            admin=None if admin is None else int(admin))}

    return {row.name: channels(row) for row in managed_rows(answers)}


def scheduler_entries(answers: ServerAnswers) -> dict[str, dict]:
    """The ``plugins/scheduler.yaml`` entries: one per managed instance, autostart or empty.

    An unmanaged instance gets no entry at all (the installer builds the mapping only for managed
    instances).
    """
    return {row.instance: setup_config.build_scheduler(row.autostart)
            for row in managed_rows(answers)}


def parse_channel(value, *, none: int | None = None) -> int | None:
    """*value* as a channel id, ``None`` when empty (and ``none`` is not given), else the id.

    A channel id is the installer's reserved :data:`NO_CHANNEL` (``-1``) — accepted wherever a channel
    is optional — or a POSITIVE id no larger than :data:`MAX_CHANNEL_ID`. ``0``, any other negative
    value and anything past that ceiling is not a channel id and answers ``None``; so does text.
    """
    text = str(value or "").strip()
    if not text:
        return none
    try:
        number = int(text)
    except (TypeError, ValueError):
        return None
    if number == NO_CHANNEL or 0 < number <= MAX_CHANNEL_ID:
        return number
    return None


def servername_duplicate(rows: list[ServerRow]) -> str | None:
    """The first server name two managed rows share, or ``None``.

    ``servers.yaml`` is keyed by server name, so two managed rows with the same name would silently
    collapse into one entry.
    """
    seen: set[str] = set()
    for row in managed_rows(ServerAnswers(rows=rows)):
        if row.name in seen:
            return row.name
        seen.add(row.name)
    return None


# ---------------------------------------------------------------------------------- the resolving

def resolve_rows(values: Mapping, answers: ServerAnswers, *, validate: bool,
                 channels: bool = True) -> tuple[ServerAnswers | None, tuple[str, dict] | None]:
    """Turn the submitted row values into step 5's answers.

    With ``validate`` set, an incomplete managed row returns ``(None, (code, data))`` — the
    classified failure that keeps the step open. Without it the answers are best-effort, which is
    what the progressive-enhancement re-render uses so a mere switch never reports a false failure.

    ``channels`` mirrors whether the step RENDERED the three channel ids, so it is ON for both flows
    that do: the first-run Discord path, and the joining flow (which reads its Discord context from
    the existing installation's shared config). It is OFF on a no-Discord install, whose screens show
    no channel fields: a managed row is then complete with its name alone, and no answer is required
    that the operator was never asked for.
    """
    rows: list[ServerRow] = []
    for index, row in enumerate(answers.rows):
        rows.append(ServerRow(
            instance=row.instance,
            detected_name=row.detected_name,
            manage=values.get(f"row_manage_{index}") is not None,
            name=str(values.get(f"row_name_{index}") or "").strip(),
            status=str(values.get(f"row_status_{index}") or "").strip(),
            chat=str(values.get(f"row_chat_{index}") or "").strip(),
            admin=str(values.get(f"row_admin_{index}") or "").strip(),
            autostart=values.get(f"row_autostart_{index}") is not None,
        ))
    resolved = ServerAnswers(scanned=answers.scanned, detected=dict(answers.detected), rows=rows)
    if not validate:
        return resolved, None
    for index, row in enumerate(rows):
        if not row.manage:
            continue
        if not row.name:
            return None, ("server_name_required", {"instance": row.instance})
        if not channels:
            continue
        if parse_channel(row.status) is None:
            if str(row.status).strip():
                return None, ("channel_invalid", {"name": row.name, "field": "status"})
            return None, ("status_required", {"name": row.name})
        for field_name, text in (("chat", row.chat), ("admin", row.admin)):
            if str(text).strip() and parse_channel(text) is None:
                return None, ("channel_invalid", {"name": row.name, "field": field_name})
    duplicate = servername_duplicate(rows)
    if duplicate is not None:
        return None, ("server_name_duplicate", {"name": duplicate})
    return resolved, None
