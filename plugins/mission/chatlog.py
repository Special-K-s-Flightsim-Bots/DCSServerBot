"""The per-server chat history: a bounded in-memory ring of the lines the listener receives.

The mission plugin's ``onChatMessage`` listener hands every line here; the console's Chat tab reads
it back through the ``chat_history`` action and follows it on an event stream. Nothing is written to
disk: DCS is the source of the events and the listener is the sink, so there is no file to rotate,
no path to configure and nothing to clean up.

A record carries ``seq``, ``ts``, ``ucid``, ``name``, ``side``, ``audience`` and ``text``.
``audience`` is one of :data:`AUDIENCES`; ``side`` is the player's side in lowercase, or an empty
string for a line an operator wrote. ``seq`` increases monotonically per server, so a follower can
ask for the lines after the last one it saw.

The ring is keyed by the server's NAME and lives in the process. Listeners run on the master and
every server's events reach it, so the master's web service reads the same object the listeners
write to. The history is the CURRENT process's — a restart starts it empty, by design.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server

log = logging.getLogger(__name__)

#: The audiences a record may name. ``all`` is the default text colour.
AUDIENCES: tuple[str, ...] = ("all", "red", "blue")

#: How many lines one server keeps. The oldest fall off the front; the tab's window is smaller.
MAX_LINES = 500

#: The ``seq`` the first record of a server carries.
FIRST_SEQ = 1

#: Covers the seq allocation and the append as one step: event listeners are awaited from the bot's
#: event loop and a line can also arrive from a worker thread, so two appends must not interleave.
_LOCK = threading.Lock()

#: server name -> deque of records, oldest first
_HISTORY: dict[str, deque] = {}

#: server name -> the seq the next record will carry
_NEXT_SEQ: dict[str, int] = {}


def append(server: Server, *, ucid: str, name: str, side: str, audience: str, text: str) -> dict | None:
    """Record one line for *server* and return it; ``None`` when there is nothing to record.

    A line with no text is dropped: DCS sends empty keep-alives and a censored line comes back as an
    empty string. ``audience`` is clamped to :data:`AUDIENCES` so a bad value cannot reach the
    template's colour class. A listener must never raise into the event loop, so a failure here is
    logged and swallowed.
    """
    key = str(getattr(server, "name", "") or "")
    if not key or not text:
        return None
    spoken = str(audience or "all").strip().lower()
    if spoken not in AUDIENCES:
        spoken = "all"
    try:
        with _LOCK:
            seq = _NEXT_SEQ.get(key, FIRST_SEQ)
            _NEXT_SEQ[key] = seq + 1
            ring = _HISTORY.setdefault(key, deque(maxlen=MAX_LINES))
            record = {
                "seq": seq,
                "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "ucid": str(ucid or ""),
                "name": str(name or ""),
                "side": str(side or ""),
                "audience": spoken,
                "text": str(text),
            }
            ring.append(record)
        return record
    except Exception:
        log.exception("chat history for %s could not be recorded", key)
        return None


def lines(server_name: str, *, after: int = 0) -> list[dict]:
    """The recorded lines for *server_name*, oldest first, carrying a ``seq`` greater than *after*."""
    key = str(server_name or "")
    if not key:
        return []
    with _LOCK:
        ring = _HISTORY.get(key)
        if not ring:
            return []
        if after <= 0:
            return list(ring)
        return [record for record in ring if record["seq"] > after]


def last_seq(server_name: str) -> int:
    """The ``seq`` of the newest line for *server_name*; ``0`` when nothing is recorded."""
    key = str(server_name or "")
    if not key:
        return 0
    with _LOCK:
        ring = _HISTORY.get(key)
        return ring[-1]["seq"] if ring else 0


def clear(server_name: str | None = None) -> None:
    """Forget one server's history, or every server's when *server_name* is ``None``."""
    with _LOCK:
        if server_name is None:
            _HISTORY.clear()
            _NEXT_SEQ.clear()
            return
        key = str(server_name)
        _HISTORY.pop(key, None)
        _NEXT_SEQ.pop(key, None)
