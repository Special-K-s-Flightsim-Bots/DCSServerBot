"""Step 5 of the tour — DCS servers: ONE screen with one row per Saved Games instance.

DETECTION IS READ FROM THE INSTALLER'S OWN HELPER, NEVER RE-DONE HERE. The instance list comes from
:func:`core.utils.findDCSInstances` — the same call ``install.py`` makes for its "DCS Server Setup"
block — and each instance's home is ``<SAVED_GAMES>/<instance>`` exactly as the installer builds it.
The scan is memoised in the tour's draft (one probe, read by every render), so the rail's decision to
keep or drop this step is a pure function of the answers.

AN EMPTY SCAN STILL ASKS. A machine whose DCS has no Saved Games instance yet has nothing to detect,
and the operator's only other way to add one is the command line — so the step keeps its place on the
rail and offers ONE blank row the operator fills in. That row is an ordinary :class:`ServerRow` whose
``instance`` is the NAME the operator types (a hand-added instance has no detected folder name, so the
one field carries both), and it leaves through the same builders as a detected row. Such a row is
named in :attr:`ServerAnswers.added`; its instance is the operator's to edit, which is the only thing
the render has to know about it.

THE ANSWERS ARE THE INSTALLER'S INPUTS. :class:`ServerRow` carries exactly what ``install.py``
collects per instance: the "manage this server?" answer, the server name, the three channel ids and
the autostart answer. :func:`instance_blocks` / :func:`servers_entries` / :func:`scheduler_entries`
turn them into the three shapes the installer writes (``nodes.yaml``'s ``instances`` entries through
the shared :func:`core.utils.setup_config.build_instance`, ``servers.yaml``'s channel blocks through
:func:`core.utils.setup_config.build_server_channels`, ``plugins/scheduler.yaml``'s autostart entries
through :func:`core.utils.setup_config.build_scheduler`). The per-index ports are the installer's own
formula (bot ports from :data:`DEFAULT_BOT_PORT` up, counted over the MANAGED instances only) from a
base that clears every ``bot_port`` the ``nodes.yaml`` already on disk carries — see
:func:`bot_port_base`; a first run with no such file starts at the installer's own 6666.

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
    "existing_bot_ports",
    "bot_port_base",
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
    """Step 5's answers: the memoised scan, the detected rows and the hand-added ones."""

    #: whether the machine has been scanned (a draft without it has not seen this step yet)
    scanned: bool = False
    #: the scan result: ``instance -> detected name``, in the order the instances were found
    detected: dict[str, str] = field(default_factory=dict)
    #: one row per detected instance in scan order, then one per hand-added instance in the order it
    #: was typed. Every row is the same object; :attr:`added` is what marks an instance as the
    #: operator's own (its name is editable and it has no detected folder name).
    rows: list[ServerRow] = field(default_factory=list)
    #: the instance names the operator added by hand — a SUFFIX of :attr:`rows`, in row order
    added: list[str] = field(default_factory=list)


def _as_str(value: Any) -> str:
    return "" if value in (None, False) else str(value)


def _row_from(instance: str, detected_name: str, stored: Mapping) -> ServerRow:
    """One :class:`ServerRow` from the stored answers for *instance* (``{}`` when there are none).

    A row with no stored name defaults to the name the instance's own ``serverSettings.lua`` carries,
    and to an EMPTY field for the installer's "give it a name" case.
    """
    previous = stored.get(instance)
    previous = previous if isinstance(previous, Mapping) else {}
    name = previous.get("name")
    return ServerRow(
        instance=instance,
        detected_name=detected_name,
        manage=bool(previous.get("manage", True)),
        name=_as_str(name) if name is not None else (
            detected_name if is_named(detected_name) else ""),
        status=_as_str(previous.get("status")),
        chat=_as_str(previous.get("chat")),
        admin=_as_str(previous.get("admin")),
        autostart=bool(previous.get("autostart", True)),
    )


def answers_from_mapping(data: Mapping | None, *, detected: Mapping | None = None) -> ServerAnswers:
    """Build the answers from the draft's ``servers`` block.

    A row exists only for an instance the scan found or the operator added: an instance that
    disappeared between two scans loses its row (that is a machine fact, not an answer), while every
    still-detected instance and every hand-added one keeps the answers it already carries. An added
    name the scan also reports is not an addition — it is that same instance, and the detected row
    already covers it.
    """
    data = data if isinstance(data, Mapping) else {}
    scan = dict(detected if detected is not None else (data.get("detected") or {}))
    stored = data.get("rows")
    stored = stored if isinstance(stored, Mapping) else {}
    rows = [_row_from(str(instance), str(detected_name or ""), stored)
            for instance, detected_name in scan.items()]
    added: list[str] = []
    names = data.get("added")
    names = names if isinstance(names, (list, tuple)) else []
    for name in names:
        name = str(name or "").strip()
        if not name or name in scan or any(row.instance == name for row in rows):
            continue
        rows.append(_row_from(name, "", stored))
        added.append(name)
    return ServerAnswers(scanned=bool(data.get("scanned") or detected is not None),
                         detected={str(k): str(v or "") for k, v in scan.items()},
                         rows=rows, added=added)


def non_secret_document(answers: ServerAnswers) -> dict:
    """The draft-writable part of the answers (this step holds no secret)."""
    return {
        "scanned": bool(answers.scanned),
        "detected": dict(answers.detected),
        "rows": {row.instance: {"manage": row.manage, "name": row.name, "status": row.status,
                                "chat": row.chat, "admin": row.admin, "autostart": row.autostart}
                 for row in answers.rows},
        "added": list(answers.added),
    }


# ------------------------------------------------------------------------------------ the shapes

def existing_bot_ports(nodes: Mapping | None) -> list[int]:
    """Every ``bot_port`` a ``nodes.yaml`` document already assigns, over ALL of its nodes.

    The document is read as a mapping (``node name -> block``); anything that is not one, or an
    instance whose ``bot_port`` is missing or not a number, contributes nothing.
    """
    ports: list[int] = []
    for block in (nodes or {}).values():
        instances = block.get("instances") if isinstance(block, Mapping) else None
        for instance in (instances or {}).values() if isinstance(instances, Mapping) else ():
            port = instance.get("bot_port") if isinstance(instance, Mapping) else None
            try:
                ports.append(int(str(port)))
            except (TypeError, ValueError):
                continue
    return ports


def bot_port_base(nodes: Mapping | None) -> int:
    """The first bot port this tour may assign, given the ``nodes.yaml`` already on disk.

    The installer's formula counts from :data:`DEFAULT_BOT_PORT` and knows nothing about instances a
    configuration already carries, so it reuses their ports; an install that already has instances
    (the joining case) would hand a new one a port another instance already holds. The base therefore
    starts ABOVE the highest ``bot_port`` the document names, and at the installer's own number when
    the document names none.
    """
    ports = existing_bot_ports(nodes)
    return max(ports) + 1 if ports else DEFAULT_BOT_PORT


def managed_rows(answers: ServerAnswers) -> list[ServerRow]:
    """The rows the operator asked to manage, in scan order."""
    return [row for row in answers.rows if row.manage]


def bot_ports(answers: ServerAnswers, *, base: int = DEFAULT_BOT_PORT) -> dict[str, int]:
    """The bot port per MANAGED instance — the installer's formula from *base*, over managed rows."""
    return {row.instance: base + index
            for index, row in enumerate(managed_rows(answers))}


def instance_blocks(answers: ServerAnswers, *, base: int = DEFAULT_BOT_PORT) -> dict[str, dict]:
    """The ``nodes.yaml`` ``instances`` entries, built by the installer's own builder.

    No extension keys are written here: the tour records only the node, its listen port and the
    detected instances. Extensions (SRS and the rest) are configured on the console's node page.

    *base* must be the SAME value the step rendered its ports from (:func:`bot_port_base` over the
    ``nodes.yaml`` on disk), or the port the page showed is not the port the commit writes.
    """
    ports = bot_ports(answers, base=base)
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

    A HAND-ADDED ROW IS A ROW WHOSE NAME IS THE INSTANCE. Its name field is the operator's instance
    name, so an ADDED row whose name came back empty is not an incomplete row — it is the row the
    operator removed, and it is dropped. The blank row the step offers on an empty scan (the trailing
    index, one past the rows the answers carry) is that same rule: it becomes a row only when it was
    given a name, because it is always on the screen and a tick alone must not hold the step open.
    """
    added_rows: list[ServerRow] = []
    detected_rows: list[ServerRow] = []
    for index, row in enumerate(answers.rows):
        name = str(values.get(f"row_name_{index}") or "").strip()
        by_hand = row.instance in answers.added
        if by_hand and not name:
            continue
        resolved_row = ServerRow(
            instance=name if by_hand else row.instance,
            detected_name=row.detected_name,
            manage=values.get(f"row_manage_{index}") is not None,
            name=name,
            status=str(values.get(f"row_status_{index}") or "").strip(),
            chat=str(values.get(f"row_chat_{index}") or "").strip(),
            admin=str(values.get(f"row_admin_{index}") or "").strip(),
            autostart=values.get(f"row_autostart_{index}") is not None,
        )
        (added_rows if by_hand else detected_rows).append(resolved_row)
    if not answers.detected:
        index = len(answers.rows)
        name = str(values.get(f"row_name_{index}") or "").strip()
        if name:
            added_rows.append(ServerRow(
                instance=name,
                detected_name="",
                manage=values.get(f"row_manage_{index}") is not None,
                name=name,
                status=str(values.get(f"row_status_{index}") or "").strip(),
                chat=str(values.get(f"row_chat_{index}") or "").strip(),
                admin=str(values.get(f"row_admin_{index}") or "").strip(),
                autostart=values.get(f"row_autostart_{index}") is not None,
            ))
    rows = detected_rows + added_rows
    resolved = ServerAnswers(scanned=answers.scanned, detected=dict(answers.detected), rows=rows,
                             added=[row.instance for row in added_rows])
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
