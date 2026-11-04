"""The ``files`` drop-box: which row is an upload's staged bytes, and the startup sweep.

``files`` (``sql/cluster.sql``) is the one-shot transport every node uses to move bytes:
``id``, ``guild_id``, ``name``, ``data``, ``created``. It carries NO kind column and NO consumed
flag, so a row is recognised by its ``name``:

* the two upload actions stage under the destination LEAF — ``plugins/music/actions.py``
  ``_stage_track_bytes`` and ``plugins/mission/actions.py`` ``_stage_mission_bytes`` both insert
  ``name`` = the leaf the caller asked for (``track.mp3``, ``mission.miz``);
* every other writer inserts a full path or a sink token — ``NodeProxy.write_file`` and
  ``NodeImpl.write_file`` insert the destination PATH, ``NodeImpl.read_file`` /
  ``read_file_window`` the source PATH, ``NodeImpl.get_cpu_info`` ``cpuinfo`` / ``cpuinfo.json``.

A row is CONSUMED by the ``DELETE`` its reader performs, so a row still present was never consumed;
its ``created`` age is the only thing that separates a live upload from one whose process was
killed. :func:`is_staged_upload_name` is the ``name`` rule, :func:`sweep_staged_uploads` the one
implementation both upload kinds are swept by.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

__all__ = ["STAGED_UPLOAD_ORPHAN_SECONDS", "STAGED_UPLOAD_SUFFIXES", "is_staged_upload_name",
           "sweep_staged_uploads"]

#: The suffixes the two upload gates accept. ``.mp3`` / ``.ogg`` are
#: ``services.music.tags.AUDIO_SUFFIXES`` (the set ``track_suffix`` offers), ``.miz`` is
#: ``plugins.mission.actions._MIZ_SUFFIX``. Restated here because this module is imported by the
#: service bus at startup and must not reach into a plugin or the music service; a test pins the
#: three sets equal, so the copy cannot drift.
STAGED_UPLOAD_SUFFIXES: tuple[str, ...] = (".mp3", ".ogg", ".miz")

#: How old a staged row must be before the sweep may take it. An upload's write takes seconds; an
#: hour is far beyond any live one and well short of "leaks 32 MiB per killed process forever".
STAGED_UPLOAD_ORPHAN_SECONDS = 3600


def is_staged_upload_name(name: Any) -> bool:
    """Whether a ``files.name`` value is the LEAF the two upload actions stage under.

    A LEAF by the same rule their gates apply (``services.music.service.upload_leaf`` and
    ``plugins.mission.actions._refuse_upload``): a non-empty string with no NUL, no separator of
    either kind, no drive letter, that is its own basename and is neither ``.`` nor ``..`` —
    restricted to :data:`STAGED_UPLOAD_SUFFIXES`, so a leaf-named drop-box row (``cpuinfo.json``)
    and a path-named one (a write target) are both refused.
    """
    if not isinstance(name, str):
        return False
    text = name.strip()
    if not text or "\x00" in text:
        return False
    if text in (".", "..") or "/" in text or "\\" in text:
        return False
    if len(text) > 1 and text[1] == ":" and text[0].isalpha():
        return False
    if text != os.path.basename(text):
        return False
    return os.path.splitext(text)[1].lower() in STAGED_UPLOAD_SUFFIXES


async def sweep_staged_uploads(pool: Any, *, guild_id: int,
                               older_than: int = STAGED_UPLOAD_ORPHAN_SECONDS,
                               now: datetime | None = None) -> int:
    """Delete *guild_id*'s staged upload rows older than *older_than* seconds; return the count.

    *pool* is the pool the two upload actions inserted through (``Node.apool``), so the sweep reads
    the same ``files`` table the bytes went into. Every statement is bounded by *guild_id*, and the
    ``DELETE`` repeats that bound, so a row of another guild can never be named by this call.

    STARTUP ONLY, never from a request: the call names no caller and no row it staged, so a route
    wrapping it would let one caller's request delete every other caller's staged bytes. It is
    deliberately not an ``@action`` and takes its guild explicitly for that reason.
    """
    if not isinstance(guild_id, int) or isinstance(guild_id, bool):
        raise TypeError("sweep_staged_uploads needs the guild id it sweeps for.")
    moment = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = moment - timedelta(seconds=int(older_than))
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT id, name FROM files WHERE guild_id = %s AND created < %s",
            (guild_id, cutoff))
        rows = await cursor.fetchall()
        ids = [row[0] for row in rows if is_staged_upload_name(row[1])]
        if ids:
            await conn.execute("DELETE FROM files WHERE guild_id = %s AND id = ANY(%s)",
                               (guild_id, ids))
    return len(ids)
