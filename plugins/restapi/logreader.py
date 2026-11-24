"""Read the bot's and the DCS servers' log files for the RestAPI, bounded, filtered and resumable.

This module is pure: it imports nothing from ``core`` and does no HTTP. The caller hands it the two
node primitives it needs, ``read_file_window`` and ``list_files`` (see :class:`core.Node`), so the
same code reads a file on the master or on an agent.

* A log is named by its SOURCE (the bot log of a node, the dcs.log of a server) and, optionally, by
  the NAME of one of its files as :meth:`LogReader.list_files` returned it. A request never carries a
  path: a name is matched against a fresh listing of the source's own directory, never joined to it.
* Every read is bounded: at most ``max_scan`` bytes are read from the file, at most ``max_return``
  bytes of text are returned, and a single line is cut at ``MAX_LINE_BYTES``.
* Lines are grouped into ENTRIES: a line that does not start with a timestamp (a traceback, a
  multi-line Lua error) belongs to the entry above it, so a level filter never cuts a traceback.
* A read returns a CURSOR, an opaque token that holds the position reached and a fingerprint of the
  file's first bytes. A later read with that cursor returns only what was written since, and follows
  the file through a rotation (see :meth:`LogReader.read`).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

__all__ = [
    "WINDOW_BYTES", "MAX_SCAN_BYTES", "MAX_RETURN_BYTES", "MAX_LINE_BYTES", "MAX_TERMS",
    "DEFAULT_LINES", "MAX_LINES", "LEVELS",
    "LogError", "LogNotFound", "LogRequestError",
    "LogFormat", "BOT_FORMAT", "DCS_FORMAT", "LogSource", "LogFile", "LogEntry", "LogFilter", "Cursor",
    "ReadResult", "LogReader", "parse_time", "build_filter"
]

#: the size of one window read; the node primitive clamps it to its own ceiling (1 MiB) anyway
WINDOW_BYTES = 1024 * 1024
#: the most bytes one request reads from the files
MAX_SCAN_BYTES = 16 * 1024 * 1024
#: the most bytes of text one response carries
MAX_RETURN_BYTES = 1024 * 1024
#: a longer line is cut
MAX_LINE_BYTES = 8 * 1024
#: a longer entry (a huge traceback) is cut
MAX_ENTRY_BYTES = 64 * 1024
#: the most ``contains`` terms a filter takes
MAX_TERMS = 10
DEFAULT_LINES = 200
MAX_LINES = 2000
#: how many bytes from the start of a file make its fingerprint
HEAD_BYTES = 256
#: how many older files are checked when looking for the predecessor of a rotated file
MAX_PREDECESSORS = 8

TRUNCATED = " [...truncated]"

#: the level names a filter accepts, lowest first, and their rank
LEVELS: dict[str, int] = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "error": 40,
    "critical": 50
}

#: the rank of the level words found in the files; an unknown word ranks as INFO
_LEVEL_RANK: dict[str, int] = {
    "TRACE": 5, "DEBUG": 10, "INFO": 20, "WARN": 30, "WARNING": 30, "ALERT": 40, "ERROR": 40,
    "CRITICAL": 50, "FATAL": 50
}

ReadWindow = Callable[..., Awaitable[tuple[bytes, int, Any]]]
ListFiles = Callable[..., Awaitable[list[tuple[str, int, float]]]]


class LogError(Exception):
    """Base class of the errors a read reports to its caller."""


class LogNotFound(LogError):
    """The log, or the named file, does not exist."""


class LogRequestError(LogError):
    """The request itself is wrong (a bad cursor, a bad level, a bad time)."""


@dataclass(frozen=True)
class LogFormat:
    """The shape of an entry's first line: a timestamp, a level word, the message."""
    name: str
    header: re.Pattern


#: ``2026-10-18 10:15:00.123 INFO\tmessage`` (see ``run.py``)
BOT_FORMAT = LogFormat("bot", re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) (?P<level>[A-Z]+)\t(?P<text>.*)$", re.S))

#: ``2026-10-18 10:15:00.123 INFO    ASYNCNET (Main): message`` (UTC)
DCS_FORMAT = LogFormat("dcs", re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) +(?P<level>[A-Z]+)\b[ \t]*(?P<text>.*)$", re.S))


@dataclass(frozen=True)
class LogSource:
    """One log: its files live in ``directory``, the one being written is ``live``."""
    kind: str
    key: str
    directory: str
    live: str
    patterns: tuple[str, ...]
    format: LogFormat
    #: whether the timestamps are UTC (else the local time of the node that writes them)
    utc: bool = True


@dataclass(frozen=True)
class LogFile:
    name: str
    size: int
    mtime: float
    live: bool


@dataclass
class LogEntry:
    time: str
    level: str
    text: str
    start: int = 0
    end: int = 0

    def size(self) -> int:
        return len(self.text) + len(self.time) + len(self.level)


@dataclass
class LogFilter:
    """Which entries a read keeps.

    There is deliberately no regular expression: Python's ``re`` has no timeout and holds the GIL, so
    one catastrophic pattern (``(a+)+$``) on one long line freezes the whole bot, whatever the size
    limits. Text terms cover what a diagnosis needs.
    """
    #: every term must appear in the entry (case-insensitive)
    contains: tuple[str, ...] = ()
    level: int = 0
    since: datetime | None = None
    until: datetime | None = None
    utc: bool = True

    def _time(self, entry: LogEntry) -> datetime | None:
        try:
            stamp = datetime.strptime(entry.time[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        return stamp.replace(tzinfo=timezone.utc) if self.utc else stamp.astimezone()

    def is_after(self, entry: LogEntry) -> bool:
        """Whether *entry* is newer than ``until`` (skipped, the read goes on)."""
        if not self.until or not entry.time:
            return False
        stamp = self._time(entry)
        return stamp is not None and stamp > self.until

    def is_before(self, entry: LogEntry) -> bool:
        """Whether *entry* is older than ``since`` (a backward read stops there)."""
        if not self.since or not entry.time:
            return False
        stamp = self._time(entry)
        return stamp is not None and stamp < self.since

    def matches(self, entry: LogEntry, inherit: bool | None = None) -> bool:
        """Whether *entry* passes the filter.

        An entry without a header (lines found before any timestamp: the tail of an entry the read
        did not see) has no level and no time of its own; *inherit* says whether the entry it
        belongs to passed. Unknown, it passes unless a level or time filter is set.
        """
        if not entry.level:
            if inherit is None:
                inherit = not (self.level or self.since or self.until)
            if not inherit:
                return False
        else:
            if self.level and _LEVEL_RANK.get(entry.level, 20) < self.level:
                return False
            if self.is_after(entry) or self.is_before(entry):
                return False
        if self.contains:
            haystack = f"{entry.time} {entry.level} {entry.text}".casefold()
            if not all(term in haystack for term in self.contains):
                return False
        return True

    def keeps_headless(self) -> bool:
        """Whether an entry without a header passes when nothing tells which entry it belongs to."""
        return not (self.level or self.since or self.until)


@dataclass(frozen=True)
class Cursor:
    """Where a read stopped: the source, the offset, and a fingerprint of the file's first bytes.

    The fingerprint, not the file's creation time, says whether a file is still the one the cursor was
    taken on: NTFS gives a file re-created under the same name within 15 seconds the creation time of
    the file it replaces (file system tunneling), which is exactly what a log rotation does. A log's
    first line carries a timestamp to the millisecond, so two successive files never start alike.
    """
    kind: str
    key: str
    offset: int
    head_length: int
    head_hash: str
    #: whether the last entry before ``offset`` passed the filter (for its continuation lines)
    kept: bool = True

    def encode(self) -> str:
        data = json.dumps({"v": 1, "k": self.kind, "s": self.key, "o": self.offset, "hl": self.head_length,
                           "h": self.head_hash, "m": int(self.kept)}, separators=(",", ":"))
        return base64.urlsafe_b64encode(data.encode("utf-8")).decode("ascii").rstrip("=")

    @staticmethod
    def decode(token: str) -> Cursor:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or data.get("v") != 1:
                raise ValueError("unknown cursor version")
            cursor = Cursor(kind=data["k"], key=data["s"], offset=data["o"], head_length=data["hl"],
                            head_hash=data["h"], kept=bool(data.get("m", 1)))
        except (binascii.Error, UnicodeDecodeError, ValueError, KeyError, TypeError) as ex:
            raise LogRequestError(f"Invalid cursor ({ex}).") from None
        if (not isinstance(cursor.kind, str) or not isinstance(cursor.key, str)
                or not isinstance(cursor.offset, int) or cursor.offset < 0
                or not isinstance(cursor.head_length, int) or not 0 <= cursor.head_length <= HEAD_BYTES
                or not isinstance(cursor.head_hash, str) or not re.fullmatch(r"[0-9a-f]{16}", cursor.head_hash)):
            raise LogRequestError("Invalid cursor.")
        return cursor


@dataclass
class ReadResult:
    file: str
    entries: list[LogEntry]
    cursor: str
    utc: bool
    #: the file was replaced since the cursor was taken
    rotated: bool = False
    #: ... and the end of the old file could not be found: lines may be missing
    gap: bool = False
    #: the index in ``entries`` of the first entry of the new file, once a rotated read reaches it
    new_file_at: int | None = None
    #: the read stopped on a limit: there may be more matching lines (older ones for a tail read,
    #: newer ones for a forward read, which the cursor continues)
    more: bool = False
    scanned_bytes: int = 0


def parse_time(value: str | None, *, now: datetime | None = None) -> datetime | None:
    """An ISO 8601 date (a naive one is UTC) or a relative duration (``30m``, ``2h``, ``1d``) ago."""
    if value is None or not str(value).strip():
        return None
    value = str(value).strip()
    now = now or datetime.now(timezone.utc)
    match = re.fullmatch(r"(\d{1,6})([smhd])", value)
    if match:
        unit = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[match.group(2)]
        try:
            return now - timedelta(**{unit: int(match.group(1))})
        except OverflowError:
            raise LogRequestError(f"Invalid time '{value}': too far back.") from None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LogRequestError(f"Invalid time '{value}': use ISO 8601 or a duration like 30m, 2h, 1d.") from None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def build_filter(*, contains: str | list[str] | None = None, level: str | None = None,
                 since: str | None = None, until: str | None = None, utc: bool = True) -> LogFilter:
    """A :class:`LogFilter` from request parameters; a bad value raises :class:`LogRequestError`."""
    terms = [contains] if isinstance(contains, str) else list(contains or [])
    terms = tuple(term.casefold() for term in terms if term)
    if len(terms) > MAX_TERMS:
        raise LogRequestError(f"At most {MAX_TERMS} 'contains' terms.")
    rank = 0
    if level:
        rank = LEVELS.get(level.strip().lower(), -1)
        if rank < 0:
            raise LogRequestError(f"Invalid level '{level}': use one of {', '.join(LEVELS)}.")
    return LogFilter(contains=terms, level=rank, since=parse_time(since), until=parse_time(until), utc=utc)


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _clip(raw: bytes) -> str:
    text = raw[:MAX_LINE_BYTES].decode("utf-8", errors="replace").rstrip("\r")
    return text + TRUNCATED if len(raw) > MAX_LINE_BYTES else text


@dataclass
class _Budget:
    """What one request may still read and return, shared by every file it reads."""
    lines: int
    scanned: int = 0
    returned: int = 0
    entries: list[LogEntry] = field(default_factory=list)

    def full(self, entry: LogEntry | None = None) -> bool:
        if len(self.entries) >= self.lines:
            return True
        return entry is not None and bool(self.entries) and self.returned + entry.size() > MAX_RETURN_BYTES

    def add(self, entry: LogEntry) -> None:
        self.entries.append(entry)
        self.returned += entry.size()


class _Builder:
    """Groups lines into entries."""

    def __init__(self, fmt: LogFormat):
        self.fmt = fmt

    def header(self, raw: bytes) -> LogEntry | None:
        match = self.fmt.header.match(_clip(raw))
        if not match:
            return None
        return LogEntry(time=match.group("time"), level=match.group("level"), text=match.group("text"))

    @staticmethod
    def extend(entry: LogEntry, raw: bytes, end: int) -> None:
        if len(entry.text) < MAX_ENTRY_BYTES:
            entry.text += "\n" + _clip(raw)
            if len(entry.text) >= MAX_ENTRY_BYTES:
                entry.text = entry.text[:MAX_ENTRY_BYTES] + TRUNCATED
        entry.end = end

    def entries(self, lines: list[tuple[int, int, bytes]]) -> tuple[list[tuple[int, int, bytes]], list[LogEntry]]:
        """``(orphans, entries)``: the lines before the first header, and the entries after it."""
        orphans: list[tuple[int, int, bytes]] = []
        found: list[LogEntry] = []
        for start, end, raw in lines:
            entry = self.header(raw)
            if entry:
                entry.start, entry.end = start, end
                found.append(entry)
            elif found:
                self.extend(found[-1], raw, end)
            else:
                orphans.append((start, end, raw))
        return orphans, found

    def orphan(self, lines: list[tuple[int, int, bytes]]) -> LogEntry:
        entry = LogEntry(time="", level="", text=_clip(lines[0][2]), start=lines[0][0], end=lines[0][1])
        for _, end, raw in lines[1:]:
            self.extend(entry, raw, end)
        return entry


def _split(buf: bytes, base: int) -> list[tuple[int, int, bytes]]:
    """The complete lines of *buf* as ``(start, end, line)``, offsets absolute (*buf* starts at *base*)."""
    out = []
    pos = 0
    while True:
        nl = buf.find(b"\n", pos)
        if nl == -1:
            return out
        out.append((base + pos, base + nl + 1, buf[pos:nl]))
        pos = nl + 1


class LogReader:
    """Lists and reads the files of one :class:`LogSource` through the node primitives."""

    def __init__(self, source: LogSource, read_window: ReadWindow, list_files: ListFiles, *,
                 window: int = WINDOW_BYTES, max_scan: int = MAX_SCAN_BYTES):
        self.source = source
        self.read_window = read_window
        self._list_files = list_files
        self.window = window
        self.max_scan = max_scan
        self.builder = _Builder(source.format)
        self._paths: dict[str, str] = {}

    # ------------------------------------------------------------------------------ files

    async def list_files(self) -> list[LogFile]:
        """The source's files, newest first. A missing directory is an empty list."""
        found = await self._list_files(self.source.directory, pattern=list(self.source.patterns))
        files = {}
        for path, size, mtime in found:
            name = os.path.basename(path)
            files.setdefault(name, (path, LogFile(name=name, size=size, mtime=mtime, live=name == self.source.live)))
        self._paths = {name: path for name, (path, _) in files.items()}
        return [entry for _, entry in files.values()]

    async def resolve(self, name: str | None) -> tuple[str, str]:
        """``(name, path)`` of the file called *name* in a FRESH listing (the live file if *name* is empty)."""
        wanted = name or self.source.live
        files = await self.list_files()
        if wanted not in {file.name for file in files}:
            if name:
                raise LogNotFound(f"No log file '{name}' (list the files to get their names).")
            raise LogNotFound(f"No log file yet (looked for '{self.source.live}').")
        return wanted, self._paths[wanted]

    # ------------------------------------------------------------------------------ reading

    async def read(self, name: str | None = None, *, flt: LogFilter | None = None, lines: int = DEFAULT_LINES,
                   tail: bool = True, cursor: str | None = None) -> ReadResult:
        """Read the file called *name* (the live file by default).

        * With a *cursor*: the entries written since the cursor was taken, oldest first. If the file
          no longer starts with the cursor's fingerprint, it was rotated: the end of the old file is
          looked for among the source's files (by its fingerprint) and read first, then the new file
          from its start. If the old file cannot be found, ``gap`` says lines may be missing.
        * Without a cursor, *tail*: the last *lines* entries that pass the filter, oldest first.
        * Without a cursor, not *tail*: the first *lines* entries that pass the filter.

        The returned cursor continues after the last entry read.
        """
        flt = flt or LogFilter(utc=self.source.utc)
        lines = max(1, min(int(lines or DEFAULT_LINES), MAX_LINES))
        file_name, path = await self.resolve(name)
        budget = _Budget(lines=lines)
        if cursor:
            return await self._follow(file_name, path, Cursor.decode(cursor), flt, budget)
        if tail:
            return await self._tail(file_name, path, flt, budget)
        offset, kept, more, head = await self._forward(path, 0, flt, budget, inherit=None)
        return self._result(file_name, budget, offset, kept, head, more=more)

    async def _head(self, path: str) -> tuple[bytes, int, Any]:
        try:
            return await self.read_window(path, length=HEAD_BYTES, offset=0)
        except FileNotFoundError:
            raise LogNotFound(f"The log file '{os.path.basename(path)}' disappeared.") from None

    def _result(self, file_name: str, budget: _Budget, offset: int, kept: bool, head: bytes, **flags) -> ReadResult:
        token = Cursor(kind=self.source.kind, key=self.source.key, offset=offset, head_length=len(head),
                       head_hash=_fingerprint(head), kept=kept).encode()
        return ReadResult(file=file_name, entries=budget.entries, cursor=token, utc=self.source.utc,
                          scanned_bytes=budget.scanned, **flags)

    async def _forward(self, path: str, start: int, flt: LogFilter, budget: _Budget,
                       inherit: bool | None) -> tuple[int, bool, bool, bytes]:
        """Read forward from *start*, adding matching entries to *budget*.

        Returns ``(offset, kept, more, head)``: the offset after the last entry consumed, whether that
        entry passed the filter, whether the read stopped on a limit, and the file's first bytes.
        """
        head, size, identity = await self._head(path)
        offset = min(start, size)
        kept = inherit if inherit is not None else flt.keeps_headless()
        current: LogEntry | None = None
        current_inherit: bool | None = inherit
        pos = offset
        carry = b""
        carry_start = offset
        replaced = False

        def consume(entry: LogEntry, entry_inherit: bool | None) -> bool:
            """Decide *entry*; False when the budget is full (the entry is NOT consumed)."""
            nonlocal offset, kept
            matched = flt.matches(entry, entry_inherit)
            if matched:
                if budget.full(entry):
                    return False
                budget.add(entry)
            offset, kept = entry.end, matched
            return True

        while True:
            if budget.scanned >= self.max_scan:
                break
            length = min(self.window, self.max_scan - budget.scanned)
            raw, size, current_identity = await self.read_window(path, length=length, offset=pos)
            if current_identity != identity:
                replaced = True
                break
            if not raw:
                break
            budget.scanned += len(raw)
            buf = carry + raw
            lines = _split(buf, carry_start)
            used = (lines[-1][1] - carry_start) if lines else 0
            carry, carry_start = buf[used:], carry_start + used
            if len(carry) > self.window:
                # a line longer than a window: take it as it is rather than never move past it
                lines.append((carry_start, carry_start + len(carry), carry))
                carry_start += len(carry)
                carry = b""
            pos += len(raw)
            for line_start, line_end, line in lines:
                entry = self.builder.header(line)
                if entry is None and current is not None:
                    self.builder.extend(current, line, line_end)
                    continue
                if current is not None:
                    if not consume(current, current_inherit):
                        return offset, kept, True, head
                    current_inherit = None
                if entry is None:
                    entry = self.builder.orphan([(line_start, line_end, line)])
                else:
                    entry.start, entry.end = line_start, line_end
                current = entry
            if pos >= size:
                break
        if replaced:
            # the file was replaced while we read it: the next read follows the rotation
            return offset, kept, True, head
        at_end = pos >= size
        if current is not None:
            # the last entry is complete at the end of the file (a follower gets any late continuation
            # line as a headless entry); before the end, it is read again next time
            if at_end or offset == current.start == start:
                if not consume(current, current_inherit):
                    return offset, kept, True, head
            else:
                return offset, kept, True, head
        return offset, kept, not at_end, head

    async def _tail(self, file_name: str, path: str, flt: LogFilter, budget: _Budget) -> ReadResult:
        """The last matching entries, read backwards window by window."""
        head, size, identity = await self._head(path)
        collected: list[LogEntry] = []
        pending = b""       # the bytes before the first header seen so far: their entry starts earlier
        pos = size
        end: int | None = None
        newest_kept: bool | None = None
        first = True
        more = False
        while True:
            if budget.scanned >= self.max_scan:
                more = pos > 0
                break
            length = min(self.window, self.max_scan - budget.scanned)
            raw, size_now, current_identity = await self.read_window(path, length=length, behind=pos)
            if current_identity != identity:
                break
            budget.scanned += len(raw)
            start = max(0, pos - len(raw))
            buf = raw + pending
            if first:
                # the trailing bytes without a newline are a line still being written
                cut = buf.rfind(b"\n") + 1
                buf = buf[:cut]
                end = start + cut
                first = False
            base = start
            if start > 0:
                nl = buf.find(b"\n")
                if nl == -1:
                    pending, pos = buf, start
                    continue
                lead, buf, base = buf[:nl + 1], buf[nl + 1:], start + nl + 1
            else:
                lead = b""
            orphans, entries = self.builder.entries(_split(buf, base))
            stop = False
            for entry in reversed(entries):
                if newest_kept is None:
                    newest_kept = flt.matches(entry)
                if flt.is_after(entry):
                    continue
                if flt.is_before(entry):
                    stop = True
                    break
                if flt.matches(entry):
                    if len(collected) >= budget.lines or (
                            collected and budget.returned + entry.size() > MAX_RETURN_BYTES):
                        more = True
                        stop = True
                        break
                    collected.append(entry)
                    budget.returned += entry.size()
            if stop:
                break
            if start == 0:
                if orphans:
                    entry = self.builder.orphan(orphans)
                    if flt.matches(entry) and len(collected) < budget.lines:
                        collected.append(entry)
                break
            pending = lead + b"".join(raw_line + b"\n" for _, _, raw_line in orphans)
            if len(pending) > 2 * self.window:
                # a huge block without any header: drop it rather than carry it forever
                pending = lead
            pos = start
        collected.reverse()
        budget.entries = collected
        return self._result(file_name, budget, end if end is not None else size,
                            flt.keeps_headless() if newest_kept is None else newest_kept,
                            head, more=more)

    async def _follow(self, file_name: str, path: str, cursor: Cursor, flt: LogFilter,
                      budget: _Budget) -> ReadResult:
        if cursor.kind != self.source.kind or cursor.key != self.source.key:
            raise LogRequestError("This cursor belongs to another log.")
        head, size, _ = await self._head(path)
        if self._same(head, size, cursor):
            offset, kept, more, head = await self._forward(path, cursor.offset, flt, budget, inherit=cursor.kept)
            return self._result(file_name, budget, offset, kept, head, more=more)
        # rotated: finish the old file first, if it can still be found
        previous = await self._predecessor(file_name, cursor)
        gap = previous is None
        if previous:
            offset, kept, more, old_head = await self._forward(previous, cursor.offset, flt, budget,
                                                               inherit=cursor.kept)
            if more:
                # the cursor stays on the old file: its fingerprint finds it again next time
                return self._result(file_name, budget, offset, kept, old_head, rotated=True, more=True)
        new_file_at = len(budget.entries)
        offset, kept, more, head = await self._forward(path, 0, flt, budget, inherit=None)
        return self._result(file_name, budget, offset, kept, head, rotated=True, gap=gap, more=more,
                            new_file_at=new_file_at)

    @staticmethod
    def _same(head: bytes, size: int, cursor: Cursor) -> bool:
        return (len(head) >= cursor.head_length and size >= cursor.offset
                and _fingerprint(head[:cursor.head_length]) == cursor.head_hash)

    async def _predecessor(self, current: str, cursor: Cursor) -> str | None:
        """The path of the file the cursor was taken on, looked for among the source's other files.

        Several files can match: the bot copies ``dcs.log`` at shutdown and DCS renames it at the
        next start. The biggest wins, it holds the most of the old file.
        """
        best: tuple[int, str] | None = None
        candidates = [file for file in await self.list_files() if file.name != current]
        for file in candidates[:MAX_PREDECESSORS]:
            path = self._paths[file.name]
            try:
                head, size, _ = await self.read_window(path, length=HEAD_BYTES, offset=0)
            except (FileNotFoundError, PermissionError):
                continue
            if self._same(head, size, cursor) and (best is None or size > best[0]):
                best = (size, path)
        return best[1] if best else None
