"""A server's OWN DCS log (``{instance.home}/Logs/dcs.log``) — the pure half of the Log tab (L1).

This module is the read models' ``logtail.py`` for a *DCS* log rather than the bot's own log: it owns
the CONFIG-DRIVEN path resolution, the line format and the byte-window arithmetic. It is PURE — no
HTTP, no ``core`` object, nothing awaited — so the console's render can use it while the node read
that actually fetches the bytes lives in ``pages/server_detail`` (which may RPC a node).

WHY A WINDOW AND NOT THE WHOLE FILE. A running server's ``dcs.log`` is appended to and can grow to
many megabytes; the console's whole-file read is refused above its cap and would move the entire file
to show its tail. So the tab reads a bounded WINDOW (``WINDOW_BYTES``) at an offset — and this module
turns that window into COMPLETE LINES, holding back the trailing partial line DCS is still writing.

THE PATH IS CONFIG-DRIVEN. ``dcs_log_path`` resolves the instance's own LogAnalyser ``log`` setting,
defaulting to ``{instance.home}/Logs/dcs.log`` and ``expandvars``-ing the result — the SAME resolution
``extensions/loganalyser/extension.py``'s ``logfile`` property already uses. A server whose log lives
elsewhere is served without a code change.
"""
from __future__ import annotations

import os
import re

from .access import attr, text
# THE LEVEL FILTER'S ONE VOCABULARY (L2). The bot-log panel already owns it — ``LEVEL_CHOICES`` /
# ``LEVEL_FILTERS`` / ``LEVEL_LABELS`` / ``DEFAULT_LEVEL`` / ``normalise_level`` — shared between the
# config and the ``?level=`` URL so the two can never drift. The DCS log tab MIRRORS it by importing
# it rather than re-declaring a second vocabulary: the choices a reader is offered, and the levels
# each keeps, are literally the same objects, so the two tabs cannot spell a filter two ways.
from .logtail import (DEFAULT_LEVEL, LEVEL_CHOICES, LEVEL_CSS, LEVEL_FILTERS, LEVEL_LABELS, LogLine,
                      normalise_level)

__all__ = ["TAIL_LINES", "WINDOW_BYTES", "FOLLOW_SECONDS", "BEHIND_END", "MISSING_SENTENCE",
           "ROTATED_SENTENCE",
           "LEVEL_CHOICES", "LEVEL_FILTERS", "LEVEL_LABELS", "DEFAULT_LEVEL", "normalise_level",
           "DCS_ARTIFACT_PATTERNS", "EVENTS_ARTIFACT_PATTERNS", "EVENTS_LOG_NAME",
           "EVENTS_OLD_NAME", "EVENTS_WHICH", "ZIP_MEDIA_TYPE", "TEXT_MEDIA_TYPE",
           "dcs_log_path", "dcs_log_dir", "events_log_path", "artifact_id", "artifact_media_type",
           "keeps", "split_complete", "parse_line"]

#: how many lines the tab shows at rest (the same order of magnitude as the bot-log panel)
TAIL_LINES = 40

#: the most bytes ONE window read returns — the cap that keeps a huge log from moving whole. 64 KiB
#: is the bot-log panel's own ``MAX_TAIL_BYTES``, so the two log viewers read comparable chunks.
WINDOW_BYTES = 64 * 1024

#: the follow interval the page uses while the tab is visible (seconds)
FOLLOW_SECONDS = 3

#: an "end of file" position: passed as ``behind`` to read the LAST ``WINDOW_BYTES`` of a file, since
#: the node clamps it to the file's real size at read time.
BEHIND_END = 2 ** 63 - 1

#: Mark a string as translatable for the extractor; return it UNCHANGED (see ``i18n._``). This module
#: is a pure read model and imports nothing from the console, so it carries its OWN identity marker —
#: the same shape ``readmodels/model.py`` uses. The translation happens where the sentence is RENDERED.
def _(message: str) -> str:
    return message


#: the honest sentence for a server with no log file yet (``{path}``/``{node}`` filled in by the
#: caller). A TRANSLATABLE TEMPLATE: the sentence is translated first, the operator data interpolated
#: after (see the log-window route), so the node name and the path stay verbatim.
MISSING_SENTENCE = _("No log file yet for this server (looked for '{path}' on node '{node}').")

#: the honest sentence a PAGE-BACK answers when a rotation landed DURING the walk (L2-fix): the page
#: the walk gathered is the PREVIOUS file's alone (never spliced with the replacement), and the view
#: resets and says the log restarted.
ROTATED_SENTENCE = _("The log restarted while loading older lines — the lines shown are from the "
                     "previous file.")

#: ``2026-10-04 12:00:00.123 INFO    APP: …`` — the DCS log's own line shape. The level word follows
#: the timestamp and is separated by spaces, NOT a tab (unlike the bot log), so the bot-log
#: ``logtail.parse_line`` cannot read it; this is the DCS reader.
_DCS_LINE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL|FATAL)\b\s*(?P<text>.*)$"
)


def _extension_block(owner, name: str) -> dict:
    """The ``extensions.<name>`` block of a node or instance double/object, or ``{}``."""
    locals_ = attr(owner, "locals", None)
    if not isinstance(locals_, dict):
        return {}
    extensions = locals_.get("extensions")
    if not isinstance(extensions, dict):
        return {}
    block = extensions.get(name)
    return block if isinstance(block, dict) else {}


def dcs_log_path(server) -> str:
    """The server's own DCS log path — config-driven, ``expandvars``-ed, defaulting to ``Logs/dcs.log``.

    The setting is the instance's LogAnalyser ``log`` key (node block first, instance block over it),
    the SAME key ``extensions/loganalyser/extension.py`` reads; absent, the default is
    ``{instance.home}/Logs/dcs.log``. The result is ``expandvars``-ed so ``%USERPROFILE%`` / ``$HOME``
    style paths work on the node that opens the file.
    """
    instance = attr(server, "instance", None)
    home = text(attr(instance, "home", None))
    default = os.path.join(home, "Logs", "dcs.log") if home else os.path.join("Logs", "dcs.log")
    config = _extension_block(attr(server, "node", None), "LogAnalyser")
    config |= _extension_block(instance, "LogAnalyser")
    setting = config.get("log")
    return os.path.expandvars(str(setting) if setting else default)


# ------------------------------------------------------------------------------- the log artifacts
# THE DOWNLOAD LIST (L2). The naming below is what an INSTANCE'S log directory really holds, confirmed
# rather than guessed (see the card): ``plugins/admin`` ships the download definitions DCS operators
# use — ``{server.instance.home}/logs`` with patterns ``dcs*.log`` and ``dcs*.zip`` — so the DCS log
# tab offers exactly those, and the debug plugin's README writes ``events.log`` through DCS's own
# ``log.set_output('events', …)`` in ``autoexec.cfg``, which DCS writes to the SAME ``Logs`` directory
# as ``dcs.log`` (DCS's log directory is one per installation). ``dcs.log.old`` / ``events.log.old``
# are DCS's own rotation names for the previous file.

#: the DCS log tab's artifacts: every ``dcs*.log*`` (the live ``dcs.log``, the previous ``dcs.log.old``,
#: and any older ``dcs…log``) plus the crash archives (``dcs*.zip``).
DCS_ARTIFACT_PATTERNS: tuple[str, ...] = ("dcs*.log*", "dcs*.zip")

#: the Events tab's artifacts — ONLY the live ``events.log`` and its predecessor ``events.log.old``
#: (Frank's limit): no crash zips, no other logs there.
EVENTS_ARTIFACT_PATTERNS: tuple[str, ...] = ("events.log", "events.log.old")

EVENTS_LOG_NAME = "events.log"
EVENTS_OLD_NAME = "events.log.old"

#: the ``?which=`` value that selects the Events tab's file on the shared window/download routes.
EVENTS_WHICH = "events"

#: a crash archive is BINARY; a log is text. The download route picks by the artifact's own name.
ZIP_MEDIA_TYPE = "application/zip"
TEXT_MEDIA_TYPE = "text/plain; charset=utf-8"


def dcs_log_dir(server) -> str:
    """The DIRECTORY the server's own DCS log lives in — the config-driven path's parent.

    The artifacts the tab lists are enumerated from HERE, so a server whose log is configured
    elsewhere (the LogAnalyser ``log`` key) lists its siblings there too, never a hard-coded ``Logs``.
    """
    return os.path.dirname(dcs_log_path(server))


def events_log_path(server) -> str:
    """The server's ``events.log`` — the debug plugin's DCS-side log, a SIBLING of ``dcs.log``.

    DCS writes every ``log.set_output(name, …)`` file into the installation's ONE log directory, the
    same one that holds ``dcs.log`` (the debug plugin's README configures ``log.set_output('events',
    …)``), so ``events.log`` is resolved from the configured log directory rather than a second path.
    """
    return os.path.join(dcs_log_dir(server), EVENTS_LOG_NAME)


def artifact_id(path) -> str:
    """An artifact's IDENTITY for a download link — its file NAME, never a path.

    The route re-enumerates the directory and matches THIS name against the fresh listing, so a
    crafted id can only ever name a file the enumeration already produced; nothing a request states
    is joined to a directory.
    """
    return os.path.basename(str(path))


def artifact_media_type(name: str) -> str:
    """The content type a served artifact carries — a crash archive is binary, a log is text."""
    return ZIP_MEDIA_TYPE if str(name).lower().endswith(".zip") else TEXT_MEDIA_TYPE


def keeps(row: LogLine, allowed) -> bool:
    """Whether *row* survives the level filter. An UNPARSEABLE line (no level) ALWAYS survives — the
    same ruling the bot-log tail applies: dropping a line because its format surprised us is worse
    than showing it plainly. An empty *allowed* set means "no restriction" (the ``all`` filter)."""
    if not allowed:
        return True
    return not row.level or row.level in allowed


def split_complete(raw: bytes, *, start: int, drop_leading: bool) -> tuple[list[tuple[int, bytes]], bool]:
    """Split a raw window into COMPLETE newline-terminated lines — never a torn one.

    *start* is the window's ABSOLUTE offset in the file. *drop_leading* drops a leading partial line
    (the window began in the middle of a line — the case when paging BACKWARD past a byte bound).

    Returns ``(lines, partial)`` where each line is ``(absolute_offset, bytes)`` for the line's first
    byte, and *partial* says a TRAILING fragment was withheld because its newline has not arrived yet.
    A window with no complete line yields ``([], …)``; the caller keeps the follower's offset where it
    was, so the line is rendered only once it is whole.
    """
    body = raw
    base = start
    if drop_leading and body:
        first = body.find(b"\n")
        if first == -1:
            return [], False          # the whole window is one torn fragment: nothing complete
        base = start + first + 1
        body = body[first + 1:]
    out: list[tuple[int, bytes]] = []
    pos = base
    cursor = 0
    while True:
        nl = body.find(b"\n", cursor)
        if nl == -1:
            break
        out.append((pos, body[cursor:nl]))
        pos += (nl - cursor) + 1
        cursor = nl + 1
    return out, cursor < len(body)


def parse_line(raw: bytes) -> LogLine:
    """One DCS log line -> :class:`LogLine`. A line that does not match keeps its text verbatim."""
    line = raw.decode("utf-8", "replace").rstrip("\r\n")
    if not line:
        return LogLine(time="", level="", css="", text="")
    match = _DCS_LINE.match(line)
    if not match:
        return LogLine(time="", level="", css="", text=line)
    level = match.group("level")
    return LogLine(time=match.group("time"), level=level, css=LEVEL_CSS.get(level, ""),
                   text=match.group("text").rstrip())
