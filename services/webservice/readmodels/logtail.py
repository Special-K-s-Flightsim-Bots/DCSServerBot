"""The log tail: read the bot's own rotating log file and return its last N lines.

READ-ONLY, bounded, and never fatal:

* the file, not a live stream — :func:`tail` seeks to the end and reads at most
  :data:`MAX_TAIL_BYTES` from the back, so a 10 MB rotating log costs one short read per render and
  cannot be pulled into memory whole;
* a missing file, an unreadable file and an empty file each produce their OWN message
  (:data:`~.model.LOG_MISSING_MESSAGE`, :data:`~.model.LOG_EMPTY_MESSAGE`) rather than an empty
  list, because an empty container reaching the template is exactly the blank field the card's
  empty-state requirement forbids;
* a line that does not match the log format is still rendered, with no level — hiding a line
  because its format surprised us is worse than showing it plainly.

The format is the one ``run.py`` installs:
``%(asctime)s.%(msecs)03d %(levelname)s\\t%(message)s`` (datefmt ``%Y-%m-%d %H:%M:%S``), with the
level separated from the message by a TAB.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .model import LOG_EMPTY_MESSAGE, LOG_LEVEL_EMPTY_MESSAGE, LOG_MISSING_MESSAGE, LogLine, LogTail

__all__ = ["DEFAULT_LINES", "MAX_TAIL_BYTES", "LEVEL_CSS", "LEVEL_FILTERS", "LEVEL_LABELS",
           "LEVEL_CHOICES", "DEFAULT_LEVEL", "normalise_level", "tail", "parse_line", "log_tail"]

log = logging.getLogger(__name__)

#: how many lines the page shows by default
DEFAULT_LINES = 40

#: hard cap on how much of the file is read from the back
MAX_TAIL_BYTES = 64 * 1024

#: log level -> the stylesheet's class (``.log .i`` / ``.w`` / ``.e``)
LEVEL_CSS: dict[str, str] = {
    "DEBUG": "d",
    "INFO": "i",
    "WARNING": "w",
    "ERROR": "e",
    "CRITICAL": "e",
    "FATAL": "e",
}

# ------------------------------------------------------------------------------- level filter
# ONE vocabulary for the config (`log_level` in webservice.yaml) and the URL (`?level=`), so the
# default a reader gets and the filter they can pick can never be spelled two different ways.

#: the known filter keys -> the levels each one KEEPS. ``all`` keeps everything (the empty set is
#: "no restriction"), so adding a level never means editing every other entry.
LEVEL_FILTERS: dict[str, frozenset[str]] = {
    "info": frozenset({"INFO", "WARNING", "ERROR", "CRITICAL", "FATAL"}),
    "warning": frozenset({"WARNING", "ERROR", "CRITICAL", "FATAL"}),
    "all": frozenset(),
}

#: the label the panel shows for each filter key
LEVEL_LABELS: dict[str, str] = {"info": "INFO+", "warning": "WARNING+", "all": "ALL"}

#: the choices in the order the panel shows them (the default first)
LEVEL_CHOICES: tuple[tuple[str, str], ...] = tuple((key, LEVEL_LABELS[key]) for key in LEVEL_FILTERS)

#: what the panel shows when neither the URL nor the config says anything
DEFAULT_LEVEL = "info"


def normalise_level(value) -> str | None:
    """A known filter key, or ``None``. Nothing else is ever passed through to the filter.

    The URL is attacker-shaped input and the config is hand-edited, so BOTH go through here: an
    unknown value is refused (``None``) and the caller falls back to the configured default rather
    than the arbitrary string reaching :data:`LEVEL_FILTERS`.
    """
    key = "" if value is None else str(value).strip().lower()
    return key if key in LEVEL_FILTERS else None


def _keeps(row: LogLine, allowed: frozenset[str]) -> bool:
    """Whether *row* survives the filter. A line whose level could not be parsed ALWAYS survives:
    dropping a line because its format surprised us is worse than showing it plainly."""
    if not allowed:
        return True
    return not row.level or row.level in allowed


def parse_line(line: str) -> LogLine:
    """One log line -> :class:`LogLine`. A line that does not match keeps its text verbatim."""
    raw = line.rstrip("\n").rstrip("\r")
    if not raw:
        return LogLine(time="", level="", css="", text="")
    # "2026-09-24 21:33:12.123 INFO\tmessage"
    head, tab, message = raw.partition("\t")
    if not tab:
        return LogLine(time="", level="", css="", text=raw)
    stamp, _, level = head.rpartition(" ")
    level_word = level.strip().upper()
    return LogLine(time=(stamp or head).strip(),
                   level=level_word,
                   css=LEVEL_CSS.get(level_word, ""),
                   text=message.rstrip())


def tail(path: Path | str | None, lines: int = DEFAULT_LINES,
         level: str = DEFAULT_LEVEL) -> LogTail:
    """The last *lines* lines of *path* AT LEVEL, newest last. Never raises.

    THE FILTER DOES NOT SHRINK THE TAIL. The block read from the back is scanned BACKWARDS and up
    to *lines* MATCHING rows are collected; a "take the last N rows, then filter" would return
    almost nothing on a file whose tail is DEBUG-heavy, which is exactly the file a reader filters.
    """
    if lines is None or lines <= 0:
        lines = DEFAULT_LINES
    chosen = normalise_level(level) or DEFAULT_LEVEL
    allowed = LEVEL_FILTERS[chosen]

    def result(available: bool, message: str, rows: tuple[LogLine, ...], shown: str) -> LogTail:
        return LogTail(path=shown, available=available, message=message, lines=rows,
                       level=chosen, levels=LEVEL_CHOICES)

    if path is None:
        return result(False, LOG_MISSING_MESSAGE, (), "")
    target = Path(path)
    shown = str(target)
    if not target.is_file():
        return result(False, LOG_MISSING_MESSAGE, (), shown)
    try:
        with target.open("rb") as handle:
            size = handle.seek(0, 2)
            start = max(0, size - MAX_TAIL_BYTES)
            handle.seek(start)
            block = handle.read()
    except OSError as ex:
        log.warning("Web UI log tail: could not read '%s' (%s)", target, ex)
        return result(False, LOG_MISSING_MESSAGE, (), shown)
    text = block.decode("utf-8", errors="replace")
    # the first line of a partial block is a fragment: drop it unless we read the whole file
    if start > 0:
        _, sep, rest = text.partition("\n")
        text = rest if sep else ""
    rows = [parse_line(line) for line in text.splitlines()]
    rows = [row for row in rows if row.text or row.time]
    if not rows:
        return result(True, LOG_EMPTY_MESSAGE, (), shown)
    kept: list[LogLine] = []
    for row in reversed(rows):                      # BACKWARDS from the end of the file
        if _keeps(row, allowed):
            kept.append(row)
            if len(kept) >= lines:
                break
    kept.reverse()                                  # newest last, as the panel renders them
    if not kept:
        return result(True, LOG_LEVEL_EMPTY_MESSAGE, (), shown)
    return result(True, "", tuple(kept), shown)


def log_tail(source, lines: int = DEFAULT_LINES, level: str = DEFAULT_LEVEL) -> LogTail:
    """The tail of the source's log file (``source.log_path``), per :func:`tail`."""
    return tail(getattr(source, "log_path", None), lines, level)