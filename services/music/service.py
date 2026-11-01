from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import sys
import time

from core import ServiceRegistry, Service, Server, Node, UploadStatus, proxy
from core.utils import resolve_within
from services.servicebus import ServiceBus

from .radios import Mode, Radio, write_radio_config
from .tags import AUDIO_SUFFIXES, read_tags

# we don't want any irrelevant log entries from eyed3
logging.getLogger(name='eyed3.mp3.headers').setLevel(logging.FATAL)

__all__ = [
    "MusicService",
    "PLAYLIST_OK",
    "PLAYLIST_MISSING",
    "PLAYLIST_EXISTS",
    "PLAYLIST_INVALID",
    "PLAYLIST_NOT_A_PERMUTATION",
    "PLAYLIST_DUPLICATE",
    "RADIO_OK",
    "RADIO_UNKNOWN",
    "RADIO_IDLE",
    "RADIO_INVALID",
    "TRACK_OK",
    "TRACK_INVALID_NAME",
    "TRACK_NOT_AUDIO",
    "TRACK_FOLDER_INVALID",
    "TRACK_FOLDER_MISSING",
    "TRACK_EXISTS",
    "TRACK_WRITE_ERROR",
    "TRACK_NO_ROOT",
    "library_key",
    "music_path",
    "music_file",
    "upload_leaf",
    "REFUSED_NAME_CHARS",
    "upload_destination",
    "track_suffix",
]

# ── the vocabulary a playlist write answers with ──────────────────────────
# The service reports FACTS; the action layer words them. One token per outcome, so a caller reads a
# status instead of parsing a sentence.
PLAYLIST_OK = "ok"
PLAYLIST_MISSING = "missing"                  # no playlist of that name
PLAYLIST_EXISTS = "exists"                    # the target name is another playlist's
PLAYLIST_INVALID = "invalid"                  # the caller's value is not a usable name or id set
PLAYLIST_NOT_A_PERMUTATION = "not-a-permutation"   # the new order is not the current row set
PLAYLIST_DUPLICATE = "duplicate"              # every named track is already in the playlist

#: the same, for a radio write ─────────────────────────────────────────────
RADIO_OK = ""
RADIO_UNKNOWN = "unknown-radio"    # config/services/music.yaml declares no such radio here
RADIO_IDLE = "radio-idle"          # the radio is configured but its server has no live radio
RADIO_INVALID = "invalid-value"    # the caller's value is not one of the accepted values

# ── the same, for a library UPLOAD ────────────────────────────────────────
TRACK_OK = "ok"
TRACK_INVALID_NAME = "invalid-name"     # the name is not one leaf file name
TRACK_NOT_AUDIO = "not-audio"           # its extension is not one the library offers
TRACK_FOLDER_INVALID = "invalid-folder"  # the folder is not one inside the music directory
TRACK_FOLDER_MISSING = "folder-missing"  # the folder does not exist on this node
TRACK_EXISTS = "exists"                 # the folder already holds a file of that name
TRACK_WRITE_ERROR = "write-error"       # the writer could not put the bytes on the disk
TRACK_NO_ROOT = "no-music-dir"          # this node's config states no music directory


#: The characters a LEAF name may never carry. The C0 controls and DEL (a NUL, a line break or a
#: tab is not a name), and the bidi embedding/override range U+202A-U+202E: those reorder how a name
#: RENDERS while the bytes keep their real extension, so ``x<U+202E>xe.mp3`` is drawn as an ``.exe``
#: that the library will happily store as audio. Ordinary non-ASCII (an accent, a CJK glyph, an em
#: dash) is accepted VERBATIM — the name on screen is the name on the disk.
REFUSED_NAME_CHARS: tuple[str, ...] = (tuple(chr(code) for code in range(0x20)) + ("\x7f",)
                                       + tuple(chr(code) for code in range(0x202A, 0x202F)))


def _text(value) -> str:
    """*value* as a stripped string."""
    return str(value or "").strip()


def music_path(value, root: str | None = None) -> str | None:
    """A caller-supplied track path as a RELATIVE path inside the music directory, or ``None``.

    Every track path a client sends is joined onto the node's music directory, so a value that names
    a LOCATION instead would read or write outside it. Refused here: an empty value, a NUL, an
    absolute path (a leading separator, a Windows drive letter, a UNC prefix) and any ``..``
    segment. Accepted: everything else, with ``/`` as the separator of record.

    With *root*, a value that resolves to a DIRECTORY inside it is refused too: a folder is not a
    track, so a name like ``Rock`` must not reach a player as one.
    """
    text = _text(value)
    if not text or "\x00" in text:
        return None
    if "/" not in text and "\\" not in text and text == "..":
        return None
    text = text.replace("\\", "/")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":" and text[0].isalpha()):
        return None
    segments = [segment for segment in text.split("/") if segment not in ("", ".")]
    if not segments or ".." in segments:
        return None
    if root is not None and os.path.isdir(os.path.join(root, *segments)):
        return None
    return "/".join(segments)


def library_key(file) -> str:
    """A track file's key in the library index: its music-directory-relative path, ``/`` separated.

    A row can carry the separator of the machine that wrote it (``os.path.relpath`` on Windows keeps
    the backslash), and the index is built with ``/``, so every lookup goes through this one rule.
    """
    return str(file or "").replace("\\", "/")


def music_file(root: str, relative: str) -> str | None:
    """The absolute file *relative* names inside *root*, or ``None`` when it leaves *root*.

    The last gate before a read or a write touches a path: the joined path is resolved and must sit
    inside the resolved music directory. ``music_path`` already refuses the shapes that name a
    location; this catches a symbolic link that points outside the folder.
    """
    if not relative:
        return None
    return resolve_within(root, os.path.join(root, *relative.split("/")))


def upload_leaf(name) -> str | None:
    """The LEAF file name *name* states, or ``None`` when it names anything but one file.

    The rule every upload inherits, whatever transport called: a non-empty string that carries no
    character of :data:`REFUSED_NAME_CHARS` (a NUL, any other C0 control, DEL, or a bidi override), no
    separator of either kind, no drive letter and no UNC prefix, that is its own basename, and is
    neither ``.`` nor ``..``. The value returned is the one that reaches the filesystem.
    """
    if not isinstance(name, str):
        return None
    text = name.strip()
    if not text:
        return None
    if any(char in text for char in REFUSED_NAME_CHARS):
        return None
    if text in (".", "..") or "/" in text or "\\" in text:
        return None
    if len(text) > 1 and text[1] == ":" and text[0].isalpha():
        return None
    if os.path.basename(text) != text:
        return None
    return text


def track_suffix(name) -> str | None:
    """The lower-cased suffix *name* carries when it is a track, or ``None``.

    Matched on the file's OWN extension, against the same pair the library scan offers
    (:data:`~services.music.tags.AUDIO_SUFFIXES`), so an upload can never add a file a scan skips.
    """
    suffix = os.path.splitext(str(name or "").strip())[1].lower()
    return suffix if suffix in AUDIO_SUFFIXES else None


def upload_destination(root: str, folder, name: str) -> tuple[str, str, str] | None:
    """``(absolute path, relative folder, relative track)`` for an upload, or ``None``.

    Both parts come from the client, so both are resolved against *root* here: *folder* goes through
    :func:`music_path` (separators folded, no escape; an empty value is the music directory itself)
    and the joined destination is then judged by :func:`core.utils.resolve_within` — the ONE
    containment rule — on its RESOLVED path, so a folder that is a symbolic link or a junction out of
    the library is refused exactly as ``..`` is. *name* is a leaf — :func:`upload_leaf` guarantees it.

    The path returned is the RESOLVED one: the writer receives the route that was checked, never a
    spelling that could resolve somewhere else.
    """
    base = _text(folder)
    relative = music_path(base) if base else ""
    if relative is None:
        return None
    leaf = upload_leaf(name)
    if leaf is None:
        return None
    joined = os.path.join(root, *relative.split("/"), leaf) if relative else os.path.join(root, leaf)
    target = resolve_within(root, joined)
    if target is None:
        return None
    track = "/".join(part for part in (relative, leaf) if part)
    return target, relative, track


def _mode(value) -> Mode | None:
    """The :class:`Mode` *value* names, or ``None`` — tolerant of the enum, its number and its name.

    A proxied call carries the enum as its VALUE, so the receiving side reads an int for a call that
    was made with ``Mode.REPEAT``.
    """
    if isinstance(value, Mode):
        return value
    try:
        return Mode(int(value))
    except (TypeError, ValueError):
        pass
    try:
        return Mode[str(value).strip().upper()]
    except (KeyError, ValueError):
        return None


def _mode_name(mode) -> str:
    """``repeat`` / ``shuffle`` for a :class:`Mode`; ``repeat`` for anything else (the default mode)."""
    return mode.name.lower() if isinstance(mode, Mode) else "repeat"


def scan_library(root: str) -> tuple[list[dict], dict]:
    """Walk *root*, returning ``(folders, index)``: the folder tree and its files by relative path.

    One folder entry per directory, ordered by path, carrying its OWN files with their tags, its
    count and its total length. ``index`` maps a file's music-directory-relative path (``/`` as the
    separator) to the same entry, so a playlist row resolves its tags without a second walk.
    """
    index: dict[str, dict] = {}
    folders: dict[str, dict] = {}
    for current, _dirs, files in os.walk(root):
        _dirs.sort()
        relative = os.path.relpath(current, root)
        relative = "" if relative == "." else relative.replace(os.sep, "/")
        folder = {
            "path": relative,
            "name": os.path.basename(os.path.normpath(current)),
            "depth": relative.count("/") + 1 if relative else 0,
            "count": 0,
            "seconds": 0.0,
            "files": [],
        }
        for name in sorted(files):
            if not name.lower().endswith(AUDIO_SUFFIXES):
                continue
            path = f"{relative}/{name}" if relative else name
            entry = read_tags(os.path.join(current, name))
            entry["name"] = name
            entry["path"] = path
            folder["files"].append(entry)
            folder["count"] += 1
            folder["seconds"] += entry["duration"]
            index[library_key(path)] = entry
        folders[relative] = folder
    return [folders[key] for key in sorted(folders)], index


def _track_stem(path: str) -> str:
    """The file's own name without its suffix — the title when nothing tags it."""
    return os.path.splitext(os.path.basename(path))[0] if path else ""


def _track_title(tags: dict, current: str, current_file: str) -> str:
    """The title a card prints for the playing track.

    Three sources, best first: the tag's title, the title the radio itself carries, and finally the
    FILE'S NAME — a track whose container carries no readable tag (or whose comment block has no
    title field) still has a name, and that is what the Discord client shows for the same file.
    Without the last fallback the card printed nothing at all for such a track.
    """
    return _text(tags.get("title")) or _text(current) or _track_stem(current_file)


def _radio_on_air(radio, current: str, current_file: str) -> bool:
    """Whether one radio is on air — the SAME fact its card prints.

    The card draws a track from ``current`` / ``current_file``; if either is set, something is
    playing and the state must say so, whatever the queue worker and the process handle report.
    Deriving the tag from a different signal let a card show a track while claiming idle, which
    offers Play (the one control that cannot act) and disables Stop and Skip (the two that can).
    """
    if radio is None:
        return False
    return bool(radio.is_running() or radio.is_playing() or current or current_file)


def _radio_loading(radio) -> bool:
    """Whether one radio has a load in flight — read from the SAME live object ``on_air`` comes from.

    A radio that is not live (its server has not registered) has nothing loading. A radio type that
    carries no loading stamp at all answers ``False`` instead of raising.
    """
    if radio is None:
        return False
    is_loading = getattr(radio, "is_loading", None)
    return bool(is_loading()) if callable(is_loading) else False


@ServiceRegistry.register(plugin='music', depends_on=[ServiceBus])
class MusicService(Service):

    def __init__(self, node):
        super().__init__(node=node, name="Music")
        self.bus = ServiceRegistry.get(ServiceBus)
        self.radios: dict[str, dict[str, Radio]] = dict()
        # the library scan per music directory: {"generation", "payload", "index"}
        self._library_cache: dict[str, dict] = {}
        self._library_generation: dict[str, int] = {}

    # ── configuration ─────────────────────────────────────────────────────
    def get_config(self, server: Server | None = None, radio_name: str | None = None) -> dict:
        if not radio_name:
            return super().get_config(server)
        else:
            return super().get_config(server)['radios'][radio_name]

    def reload(self) -> None:
        """Re-read ``config/services/music.yaml`` and drop every merged instance block."""
        super().reload()
        self._config.clear()

    def radios_declared(self, server: Server) -> dict:
        """The radio blocks ``config/services/music.yaml`` states for *server* (own block over DEFAULT)."""
        return dict(self.get_config(server).get('radios') or {})

    @property
    def music_dir(self) -> str:
        music_dir = self._music_root()
        if music_dir is None:
            raise KeyError('music_dir')
        if not os.path.exists(music_dir):
            os.makedirs(music_dir)
        return music_dir

    def _music_root(self) -> str | None:
        """The music directory the config states, expanded, or ``None``.

        Nothing is created and the path need not exist: a caller validating a value only needs the
        root to compare against.
        """
        return os.path.expandvars(self.get_config().get('music_dir') or '') or None

    async def get_music_dir(self) -> str:
        return self.music_dir

    async def stop(self):
        for server_name in self.radios.keys():
            for radio in self.radios[server_name].values():
                await radio.stop()
        await super().stop()

    # ── the library ───────────────────────────────────────────────────────
    def _generation_key(self, root: str) -> str:
        """The library cache's key for *root* — the ONE key a scan and an invalidation share."""
        return os.path.normcase(os.path.realpath(root))

    def _bump_generation(self, root: str | None = None) -> None:
        """Invalidate the cached scan of *root*, or of this node's music directory when it is ``None``."""
        key = self._generation_key(root if root is not None else self.music_dir)
        self._library_generation[key] = self._library_generation.get(key, 0) + 1

    def _library(self, refresh: bool = False) -> tuple[dict, dict]:
        """``(payload, index)`` for THIS process's music directory.

        The scan runs once per (music directory, generation): tag reading is far too slow to repeat
        on every render, and a page asks for the same tree several times. ``refresh`` bumps the
        directory's generation, which is what the console's Rescan asks for — and so does a
        successful upload.
        """
        root = self.music_dir
        key = self._generation_key(root)
        if refresh:
            self._bump_generation(root)
        generation = self._library_generation.get(key, 0)
        cached = self._library_cache.get(key)
        if cached is not None and cached["generation"] == generation:
            return cached["payload"], cached["index"]
        folders, index = scan_library(root)
        payload = {
            "node": self.node.name,
            "scanned": time.time(),
            "generation": generation,
            "folders": folders,
            "files": len(index),
            "seconds": sum(entry["duration"] for entry in index.values()),
        }
        self._library_cache[key] = {"generation": generation, "payload": payload, "index": index}
        return payload, index

    @proxy
    async def read_library(self, node: Node, refresh: bool = False) -> dict:
        """The music folder tree of ``node``, with the tags of every audio file below it.

        Runs on the node that holds the disk: the walk and the tag reads are local filesystem work,
        so only plain data crosses the wire. Cached per music directory (see :meth:`_library`).
        """
        payload, _index = self._library(refresh)
        return payload

    @proxy
    async def save_track(self, node: Node, folder: str, name: str, source: int) -> dict:
        """Write ONE uploaded track into a folder of THIS node's music directory.

        Runs where the disk is. *source* is the row id the caller staged the bytes in (the ``files``
        table), so an id — not the file — crosses the wire, and this node reads the row back through
        its OWN writer.

        Refused BEFORE anything is written: a name that is not one leaf, an extension the library
        does not offer, a folder that is not inside the music directory (judged on its RESOLVED path,
        so a symbolic link or a junction out of the library is refused) or does not exist on this
        node, and a name the folder already holds. The occupied name is refused twice on purpose: the
        check here is the early answer, and the writer's EXCLUSIVE CREATE is what makes it true for two
        uploads that arrive together. A successful write bumps the library's scan generation, so the
        track is in the next read without a rescan.
        """
        leaf = upload_leaf(name)
        if leaf is None:
            return {"status": TRACK_INVALID_NAME, "name": str(name or ""), "folder": _text(folder)}
        if track_suffix(leaf) is None:
            return {"status": TRACK_NOT_AUDIO, "name": leaf, "folder": _text(folder)}
        try:
            root = self.music_dir
        except KeyError:
            return {"status": TRACK_NO_ROOT, "name": leaf, "folder": _text(folder)}
        destination = upload_destination(root, _text(folder), leaf)
        if destination is None:
            return {"status": TRACK_FOLDER_INVALID, "name": leaf, "folder": _text(folder)}
        target, relative, track = destination
        if not os.path.isdir(os.path.dirname(target)):
            return {"status": TRACK_FOLDER_MISSING, "name": leaf, "folder": relative or _text(folder)}
        if os.path.exists(target):
            return {"status": TRACK_EXISTS, "name": leaf, "folder": relative, "track": track}
        try:
            outcome = await self.node.write_file(target, source)
        except FileExistsError:
            # the writer's exclusive create is the authority: a name that appeared between the check
            # above and the write is an occupied name, not a write error
            return {"status": TRACK_EXISTS, "name": leaf, "folder": relative, "track": track}
        except Exception:
            self.log.exception("Music: '%s' could not be written.", target)
            return {"status": TRACK_WRITE_ERROR, "name": leaf, "folder": relative, "track": track}
        if outcome == UploadStatus.FILE_EXISTS:
            return {"status": TRACK_EXISTS, "name": leaf, "folder": relative, "track": track}
        if outcome != UploadStatus.OK:
            return {"status": TRACK_WRITE_ERROR, "name": leaf, "folder": relative, "track": track}
        self._bump_generation(root)
        try:
            size = os.path.getsize(target)
        except OSError:
            size = 0
        return {"status": TRACK_OK, "name": leaf, "folder": relative, "track": track, "size": size}

    # ── the playlists ─────────────────────────────────────────────────────
    async def _stored_name(self, conn, name: str) -> str | None:
        """The playlist's OWN spelling of *name* inside *conn*'s transaction, or ``None``.

        ``music_playlists`` carries no unique constraint on ``name``, so one playlist is identified
        by its name alone: a spelling that differs only in case names the playlist that already
        exists, and the stored spelling is the one every write then uses.
        """
        wanted = _text(name)
        if not wanted:
            return None
        cursor = await conn.execute(
            'SELECT DISTINCT name FROM music_playlists WHERE lower(name) = lower(%s) ORDER BY name',
            (wanted,))
        found = [str(row[0]) for row in await cursor.fetchall()]
        if not found:
            return None
        for candidate in found:
            if candidate == wanted:
                return candidate
        return found[0]

    @proxy
    async def list_playlists(self, node: Node, refresh: bool = False) -> list[dict]:
        """Every playlist with its track count, its total length and how many tracks are missing.

        A node read: the counts are the database's, the lengths and the missing flags are the file
        tags of the node that holds the disk (:meth:`_library`).
        """
        _payload, index = self._library(refresh)
        async with self.apool.connection() as conn:
            cursor = await conn.execute(
                'SELECT name, song_id, song_file FROM music_playlists ORDER BY name, song_id')
            rows = await cursor.fetchall()
        playlists: dict[str, dict] = {}
        for name, _song_id, song_file in rows:
            entry = playlists.setdefault(str(name), {"name": str(name), "tracks": 0, "seconds": 0.0,
                                                     "missing": 0})
            entry["tracks"] += 1
            found = index.get(library_key(song_file))
            if found is None:
                entry["missing"] += 1
            else:
                entry["seconds"] += found["duration"]
        return list(playlists.values())

    @proxy
    async def get_playlist(self, node: Node, name: str, refresh: bool = False) -> dict | None:
        """ONE playlist, its tracks in playback order, or ``None`` when no playlist has that name.

        Each track carries its ``song_id`` (the row's identity, and what a reorder names), its file,
        its tags, its length, its size and whether the file is present on the node.
        """
        _payload, index = self._library(refresh)
        async with self.apool.connection() as conn:
            stored = await self._stored_name(conn, name)
            if stored is None:
                return None
            cursor = await conn.execute(
                'SELECT song_id, song_file FROM music_playlists WHERE name = %s ORDER BY song_id',
                (stored,))
            rows = await cursor.fetchall()
        tracks: list[dict] = []
        seconds = 0.0
        missing = 0
        for song_id, song_file in rows:
            relative = str(song_file)
            found = index.get(library_key(relative))
            if found is None:
                missing += 1
                tracks.append({"song_id": int(song_id), "file": relative, "present": False,
                               "title": posixpath.basename(relative), "artist": "", "album": "",
                               "duration": 0.0, "size": 0})
                continue
            seconds += found["duration"]
            tracks.append({"song_id": int(song_id), "file": relative, "present": True,
                           "title": found["title"], "artist": found["artist"],
                           "album": found["album"], "duration": found["duration"],
                           "size": found["size"]})
        return {"name": stored, "tracks": tracks, "seconds": seconds, "missing": missing}

    async def playlist_usage(self) -> list[dict]:
        """The ``music_radios`` rows: which (server, radio) plays which playlist."""
        async with self.apool.connection() as conn:
            cursor = await conn.execute(
                'SELECT playlist_name, server_name, radio_name FROM music_radios '
                'ORDER BY playlist_name, server_name, radio_name')
            rows = await cursor.fetchall()
        return [{"playlist": str(playlist), "server": str(server), "radio": str(radio)}
                for playlist, server, radio in rows]

    async def playlist_exists(self, name: str) -> bool:
        """Whether a playlist of that name (in any casing) holds at least one track."""
        async with self.apool.connection() as conn:
            stored = await self._stored_name(conn, name)
        return stored is not None

    async def add_tracks(self, playlist: str, files: list[str]) -> dict:
        """Append the track files *playlist* does not hold yet, creating it with its first track.

        The playlist needs no row of its own: it exists while it has tracks, so a draft is never
        written. Repeated entries within ONE call are collapsed. A track the playlist already holds
        is NOT added again — one ``(name, song_file)`` pair is one row (the table's unique
        constraint) — so ``added`` counts what was written and ``already`` what was skipped;
        ``added == 0`` is :data:`PLAYLIST_DUPLICATE`, never an error.

        The paths are this method's own input — a peer may call it with any string, not only the
        action — so each one is run through :func:`music_path` here and the whole call is refused
        when one names something that is not a track of the music directory.

        ``ON CONFLICT DO NOTHING`` is what makes the answer race-free: two writers adding the same
        track to one playlist write ONE row, and the loser's ``rowcount`` is the zero that makes it
        report the track as already there.
        """
        name = _text(playlist)
        root = self._music_root()
        wanted: list[str] = []
        for file in files or ():
            relative = music_path(file, root)
            if relative is None:
                return {"status": PLAYLIST_INVALID, "playlist": name, "added": 0, "already": 0,
                        "tracks": 0, "created": False}
            if relative not in wanted:
                wanted.append(relative)
        if not name or not wanted:
            return {"status": PLAYLIST_INVALID, "playlist": name, "added": 0, "already": 0,
                    "tracks": 0, "created": False}
        added = 0
        async with self.apool.connection() as conn:
            async with conn.transaction():
                stored = await self._stored_name(conn, name)
                target = stored if stored is not None else name
                for file in wanted:
                    cursor = await conn.execute(
                        "INSERT INTO music_playlists (name, song_id, song_file) "
                        "VALUES (%s, nextval('music_song_id_seq'), %s) "
                        "ON CONFLICT (name, song_file) DO NOTHING", (target, file))
                    added += max(0, cursor.rowcount)
                cursor = await conn.execute(
                    'SELECT count(*) FROM music_playlists WHERE name = %s', (target,))
                row = await cursor.fetchone()
        already = len(wanted) - added
        return {"status": PLAYLIST_OK if added else PLAYLIST_DUPLICATE, "playlist": target,
                "added": added, "already": already,
                "tracks": int(row[0]) if row else len(wanted), "created": stored is None}

    async def remove_tracks(self, playlist: str, song_ids: list[int]) -> dict:
        """Remove the named rows from *playlist*; an emptied playlist takes its radio rows with it."""
        name = _text(playlist)
        wanted = [int(song_id) for song_id in song_ids or ()]
        if not name or not wanted:
            return {"status": PLAYLIST_INVALID, "playlist": name, "removed": 0, "tracks": 0,
                    "unmapped": 0}
        async with self.apool.connection() as conn:
            async with conn.transaction():
                stored = await self._stored_name(conn, name)
                if stored is None:
                    return {"status": PLAYLIST_MISSING, "playlist": name, "removed": 0, "tracks": 0,
                            "unmapped": 0}
                cursor = await conn.execute(
                    'DELETE FROM music_playlists WHERE name = %s AND song_id = ANY(%s)',
                    (stored, wanted))
                removed = cursor.rowcount
                cursor = await conn.execute(
                    'SELECT count(*) FROM music_playlists WHERE name = %s', (stored,))
                row = await cursor.fetchone()
                tracks = int(row[0]) if row else 0
                unmapped = 0
                if not tracks:
                    cursor = await conn.execute(
                        'DELETE FROM music_radios WHERE playlist_name = %s', (stored,))
                    unmapped = cursor.rowcount
        return {"status": PLAYLIST_OK, "playlist": stored, "removed": max(0, removed),
                "tracks": tracks, "unmapped": max(0, unmapped)}

    async def reorder_tracks(self, playlist: str, order: list[int]) -> dict:
        """Replace *playlist*'s playback order with ``order`` — the FULL new order, one transaction.

        Refused unless ``order`` is a permutation of the playlist's current ``song_id`` set: a page
        that rendered a stale list must be refused rather than write an order over rows that are
        gone. The order IS ``song_id`` (that is what a radio reads), so the rows are renumbered from
        the sequence in the new order and every later insert still lands at the end.

        The answer carries ``song_ids``: the ids as they stand AFTER the write, in playback order. The
        rows are renumbered, so a page that keeps its own copy of the ids must re-stamp them from this
        list or its next drop would name ids that no longer exist (a ``not-a-permutation`` refusal).
        """
        name = _text(playlist)
        wanted = [int(song_id) for song_id in order or ()]
        if not name or not wanted:
            return {"status": PLAYLIST_INVALID, "playlist": name, "tracks": 0}
        async with self.apool.connection() as conn:
            async with conn.transaction():
                stored = await self._stored_name(conn, name)
                if stored is None:
                    return {"status": PLAYLIST_MISSING, "playlist": name, "tracks": 0}
                cursor = await conn.execute(
                    'SELECT song_id FROM music_playlists WHERE name = %s ORDER BY song_id',
                    (stored,))
                current = [int(row[0]) for row in await cursor.fetchall()]
                if sorted(wanted) != sorted(current):
                    return {"status": PLAYLIST_NOT_A_PERMUTATION, "playlist": stored,
                            "tracks": len(current)}
                if wanted == current:
                    return {"status": PLAYLIST_OK, "playlist": stored, "tracks": len(current),
                            "song_ids": current}
                cursor = await conn.execute(
                    "SELECT nextval('music_song_id_seq') FROM generate_series(1, %s)",
                    (len(wanted),))
                fresh = [int(row[0]) for row in await cursor.fetchall()]
                for song_id, new_id in zip(wanted, fresh):
                    await conn.execute(
                        'UPDATE music_playlists SET song_id = %s WHERE name = %s AND song_id = %s',
                        (new_id, stored, song_id))
        return {"status": PLAYLIST_OK, "playlist": stored, "tracks": len(wanted),
                "song_ids": fresh}

    async def rename_playlist(self, playlist: str, name: str) -> dict:
        """Rename *playlist* in ``music_playlists`` AND in ``music_radios``, in ONE transaction.

        A playlist is identified by its name, so a rename that moved only one table would leave the
        radios pointing at a playlist that no longer exists. A name another playlist already holds
        is refused; changing the case of the playlist's own name is a rename like any other.
        """
        source = _text(playlist)
        target = _text(name)
        if not source or not target:
            return {"status": PLAYLIST_INVALID, "playlist": source, "previous": source,
                    "tracks": 0, "radios": 0}
        async with self.apool.connection() as conn:
            async with conn.transaction():
                stored = await self._stored_name(conn, source)
                if stored is None:
                    return {"status": PLAYLIST_MISSING, "playlist": source, "previous": source,
                            "tracks": 0, "radios": 0}
                taken = await self._stored_name(conn, target)
                if taken is not None and taken != stored:
                    return {"status": PLAYLIST_EXISTS, "playlist": stored, "previous": stored,
                            "tracks": 0, "radios": 0}
                radios = 0
                if target != stored:
                    await conn.execute('UPDATE music_playlists SET name = %s WHERE name = %s',
                                       (target, stored))
                    cursor = await conn.execute(
                        'UPDATE music_radios SET playlist_name = %s WHERE playlist_name = %s',
                        (target, stored))
                    radios = max(0, cursor.rowcount)
                cursor = await conn.execute(
                    'SELECT count(*) FROM music_playlists WHERE name = %s', (target,))
                row = await cursor.fetchone()
        return {"status": PLAYLIST_OK, "playlist": target, "previous": stored,
                "tracks": int(row[0]) if row else 0, "radios": radios}

    async def delete_playlist(self, playlist: str) -> dict:
        """Remove every track of *playlist* together with the radio rows that assigned it."""
        name = _text(playlist)
        if not name:
            return {"status": PLAYLIST_INVALID, "playlist": name, "tracks": 0, "unmapped": 0}
        async with self.apool.connection() as conn:
            async with conn.transaction():
                stored = await self._stored_name(conn, name)
                if stored is None:
                    return {"status": PLAYLIST_MISSING, "playlist": name, "tracks": 0,
                            "unmapped": 0}
                cursor = await conn.execute(
                    'DELETE FROM music_playlists WHERE name = %s', (stored,))
                tracks = max(0, cursor.rowcount)
                cursor = await conn.execute(
                    'DELETE FROM music_radios WHERE playlist_name = %s', (stored,))
                unmapped = max(0, cursor.rowcount)
        return {"status": PLAYLIST_OK, "playlist": stored, "tracks": tracks, "unmapped": unmapped}

    # ── the radios ────────────────────────────────────────────────────────
    @proxy
    async def read_radios(self, server: Server, refresh: bool = False) -> list[dict]:
        """What every radio configured for *server* is doing, one entry per radio.

        The state and the current track belong to the live radio object, the assignment to
        ``music_radios`` and the track's tags to this node's library. A radio whose server has not
        registered yet is reported configured-but-idle, never omitted.
        """
        declared = self.radios_declared(server)
        live = self.radios.get(server.name, {})
        _payload, index = self._library(refresh)
        async with self.apool.connection() as conn:
            cursor = await conn.execute(
                'SELECT radio_name, playlist_name FROM music_radios WHERE server_name = %s',
                (server.name,))
            assigned = {str(radio): str(playlist) for radio, playlist in await cursor.fetchall()}
        entries: list[dict] = []
        for name, config in declared.items():
            radio = live.get(name)
            current = str(radio.current or "") if radio else ""
            current_file = str(getattr(radio, "current_file", "") or "") if radio else ""
            tags = index.get(library_key(current_file)) or {}
            # ON AIR IS THE SAME FACT THE CARD PRINTS — see _radio_on_air.
            on_air = _radio_on_air(radio, current, current_file)
            entries.append({
                "name": str(name),
                "type": str(config.get("type", "")),
                "frequency": str(config.get("frequency", "")),
                "modulation": str(config.get("modulation", "")),
                "coalition": str(config.get("coalition", "")),
                "volume": str(config.get("volume", "")),
                "display_name": str(config.get("display_name", "")),
                "mode": _mode_name(radio.mode if radio else _mode(config.get("mode"))),
                "on_air": on_air,
                # the load stamp of the SAME live radio object: True from the press until the track
                # is there, or until the cap ends it
                "loading": _radio_loading(radio),
                "playlist": assigned.get(str(name), ""),
                "tracks": len(radio.songs) if radio else 0,
                "current": {
                    "title": _track_title(tags, current, current_file),
                    "artist": tags.get("artist", ""),
                    "album": tags.get("album", ""),
                    "file": current_file,
                    "duration": tags.get("duration", 0.0),
                } if (current or current_file) else {},
            })
        return entries

    @proxy
    async def assign_playlist(self, server: Server, radio_name: str, playlist: str) -> str:
        """Point one radio at *playlist* and re-read its song list. Answers ``""`` or a reason.

        The assignment goes through the radio's own ``playlist`` property, which is the ONLY writer
        of the row and the only place ``songs`` is re-read: writing the row directly would leave a
        playing radio on its old list. A radio with no live object (its server is not registered)
        gets the row alone — it reads it when it starts.

        The playlist is validated HERE, because a peer process may name any string: a blank name and
        a name no playlist holds are both refused, and the row carries the playlist's OWN spelling —
        a radio reads its songs by that exact name. A refusal is never answered as ``RADIO_OK``.
        """
        name = _text(radio_name)
        if name not in self.radios_declared(server):
            return RADIO_UNKNOWN
        target = _text(playlist)
        if not target:
            return RADIO_INVALID
        async with self.apool.connection() as conn:
            stored = await self._stored_name(conn, target)
        if stored is None:
            return PLAYLIST_MISSING
        radio = self.radios.get(server.name, {}).get(name)
        if radio is None:
            with self.pool.connection() as conn:
                conn.execute("""
                             INSERT INTO music_radios (server_name, radio_name, playlist_name)
                             VALUES (%s, %s, %s)
                             ON CONFLICT (server_name, radio_name) DO UPDATE
                                 SET playlist_name = excluded.playlist_name
                             """, (server.name, name, stored))
            return RADIO_OK
        running = radio.is_running()
        if running:
            await radio.stop()
        radio.playlist = stored
        if running:
            await radio.start()
        return RADIO_OK

    @proxy
    async def start_radio(self, server: Server, radio_name: str) -> str:
        """Start one radio's queue. Answers ``""`` or a reason it could not."""
        name = _text(radio_name)
        if name not in self.radios_declared(server):
            return RADIO_UNKNOWN
        radio = self.radios.get(server.name, {}).get(name)
        if radio is None:
            return RADIO_IDLE
        if not radio.is_running():
            # STAMPED BEFORE THE DRIVE: the redirect render that follows this call is the only
            # chance to say a track is loading; play() clears the stamp when the track is there.
            radio.begin_loading()
            await radio.start()
        return RADIO_OK

    @proxy
    async def stop_radio(self, server: Server, radio_name: str) -> str:
        """Stop one radio's queue and its playback. Answers ``""`` or a reason it could not."""
        name = _text(radio_name)
        if name not in self.radios_declared(server):
            return RADIO_UNKNOWN
        radio = self.radios.get(server.name, {}).get(name)
        if radio is None:
            return RADIO_IDLE
        await radio.stop()
        return RADIO_OK

    @proxy
    async def skip_radio(self, server: Server, radio_name: str) -> str:
        """Skip the track one radio is playing. Answers ``""`` or a reason it could not."""
        name = _text(radio_name)
        if name not in self.radios_declared(server):
            return RADIO_UNKNOWN
        radio = self.radios.get(server.name, {}).get(name)
        if radio is None:
            return RADIO_IDLE
        # THE NEXT TRACK TAKES A MOMENT: stamped before the skip so the redirect render says so,
        # cleared by play() when the new track is there.
        radio.begin_loading()
        await radio.skip()
        # SKIP MEANS THE NEXT TRACK. A track can be playing with no queue behind it (started
        # directly, outside the worker); ending it then leaves the radio silent with the playlist
        # untouched, which reads as "skip stopped the music". The queue carries on from here.
        if radio.songs and not radio.is_running():
            await radio.start()
        return RADIO_OK

    @proxy
    async def set_radio_config(self, server: Server, radio_name: str, config: dict) -> str:
        """Merge *config* into the radio's block of ``music.yaml`` and restart it. ``""`` or a reason.

        The keys are written where the radio reads them: the file keeps its comments and its key
        order (``write_radio_config``), and the live radio is stopped and started so the running
        player picks the new value up instead of finishing on the old one.
        """
        name = _text(radio_name)
        changes = {str(key): value for key, value in (config or {}).items() if value is not None}
        if name not in self.radios_declared(server):
            return RADIO_UNKNOWN
        if not changes:
            return RADIO_INVALID
        radio = self.radios.get(server.name, {}).get(name)
        if radio is None:
            write_radio_config(self, server, name, changes)
            return RADIO_OK
        running = radio.is_running()
        if running:
            await radio.stop()
        radio.config = changes
        if running:
            await radio.start()
        return RADIO_OK

    @proxy
    async def set_mode(self, server: Server, radio_name: str, mode: Mode | int | str) -> str:
        """Set one radio's playback mode and persist it. Answers ``""`` or a reason it could not."""
        name = _text(radio_name)
        if name not in self.radios_declared(server):
            return RADIO_UNKNOWN
        chosen = _mode(mode)
        if chosen is None:
            return RADIO_INVALID
        radio = self.radios.get(server.name, {}).get(name)
        if radio is None:
            return RADIO_IDLE
        radio.mode = chosen
        write_radio_config(self, server, name, {"mode": chosen.value})
        radio.reset()
        return RADIO_OK

    # ── lifecycle of the radios of one server ─────────────────────────────
    @proxy
    async def init_radios(self, server: Server, radio_name: str | None = None) -> None:
        config = self.get_config(server)
        if not config or 'radios' not in config:
            self.log.debug(
                f"No config/services/music.yaml found or no entry for server {server.name} configured.")
            return
        for name, cfg in config['radios'].items():
            if radio_name and name != radio_name:
                continue
            if not self.radios.get(server.name):
                self.radios[server.name] = {}
            if not self.radios[server.name].get(name):
                radio: Radio = getattr(sys.modules['services.music.radios'], cfg['type'])(
                    service=self, name=name, server=server
                )
                self.radios[server.name][name] = radio

    @proxy
    async def start_radios(self, server: Server, radio_name: str | None = None) -> None:
        if server.name in self.radios:
            for name, radio in self.radios[server.name].items():
                if radio_name and name != radio_name:
                    continue
                if not radio.is_running():
                    await radio.start()

    @proxy
    async def stop_radios(
            self,
            server: Server,
            radio_name: str | None = None,
    ) -> None:
        """
        Stop one or more radios belonging to *server*.

        If *radio_name* is supplied only that radio is stopped;
        otherwise all radios for the server are stopped.
        """
        # No radios for this server → nothing to do
        if server.name not in self.radios:
            return

        stop_tasks = [
            radio.stop()
            for name, radio in self.radios[server.name].items()
            if not radio_name or name == radio_name
        ]

        if stop_tasks:
            await asyncio.gather(*stop_tasks)

    @proxy
    async def play_song(self, server: Server, radio_name: str, song: str) -> None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            asyncio.create_task(radio.play(song))

    @proxy
    async def skip_song(self, server: Server, radio_name: str) -> None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            asyncio.create_task(radio.skip())

    @proxy
    async def get_current_song(self, server: Server, radio_name: str) -> str | None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        return radio.current if radio else None

    @proxy
    async def get_current_file(self, server: Server, radio_name: str) -> str | None:
        """The playing track as a music-directory-relative path, or ``None`` when nothing plays.

        ``get_current_song`` answers the DISPLAY title (the tag's title, or the file's name), which
        is not something a tag reader can open. Anything that needs the file itself — reading its
        tags, locating it on disk — asks for this one and joins it onto the music directory.
        """
        radio = self.radios.get(server.name, {}).get(radio_name)
        return (radio.current_file or None) if radio else None

    @proxy
    async def set_playlist(self, server: Server, radio_name: str, playlist: str):
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            radio.playlist = playlist

    @proxy
    async def get_songs(self, server: Server, radio_name: str) -> list[str]:
        radio = self.radios.get(server.name, {}).get(radio_name)
        return radio.songs if radio else []

    @proxy
    async def get_mode(self, server: Server, radio_name: str) -> Mode | None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            return radio.mode
        return None

    @proxy
    async def set_config(self, server: Server, radio_name: str, config: dict) -> None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            radio.config = config
        return None

    @proxy
    async def reset_playlist(self, server: Server, radio_name: str) -> None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            radio.reset()
