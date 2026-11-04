"""The music plugin's ACTION layer — every playlist read, playlist write and radio write.

One ``@action`` per operation, reachable from the admin console, the REST surface and MCP alike. The
function resolves its target through the seam, asks the :class:`~services.music.service.MusicService`
to do the work and translates the answer into a typed result; the transport words the notice.

TWO FAMILIES, one rule apart:

* the PLAYLISTS are INSTALLATION-WIDE (the Discord commands are not per server either), so they name
  a playlist and no target. Their node is resolved from the radios that use the playlist — the files
  of a playlist are read where the disk is — and an unused playlist falls back to the caller's node.
  Because no server narrows a playlist write, the WRITES are admitted from the admin console only
  (:func:`_playlist_write_authorised`); the reads are not guarded that way.
* the RADIOS belong to ONE SERVER, so every radio write resolves its server through
  :meth:`~core.actions.ActionContext.resolve_scoped_server`: a caller without the server in its scope
  is refused HERE, by the action every transport calls. A transport that carries no scope (REST,
  MCP) is refused for the same reason — its context cannot grant a server.

AND ONE NODE WRITE that is neither: :func:`upload_music_track` puts a file into the LIBRARY of one
node, so it resolves a NODE through the caller's scope, refuses anything but bytes, and lets the
service on that node judge the folder against the music directory it holds.

THE SERVICE OWNS THE WORK: no statement, no tag read and no file write happens in this module. The
service answers FACTS (``status`` tokens), this module words them — so one sentence reaches an
operator whether the write came from the console or from MCP.

The module imports ``core`` and nothing else at import time: discovery imports this file on every
install, and a module that raised would register no action at all, silently removing every music
control.
"""
from __future__ import annotations

import asyncio
import logging
import math

from dataclasses import dataclass, field
from typing import Any, TypeVar

from core.action_results import ActionResult
from core.actions import (TRANSPORT_WEB, NodeResolution, ServerResolution, action, audit_action)

log = logging.getLogger(__name__)

#: the result type :func:`_audited` answers with — the action's own dataclass, unchanged
_Result = TypeVar("_Result", bound=ActionResult)

__all__ = [
    "CAPABILITY_VIEW", "CAPABILITY_WRITE", "CAPABILITY_CONTROL", "CAPABILITY_UPLOAD",
    "CONTROLS", "MODULATIONS", "COALITIONS", "MODES",
    "SERVICE_MISSING", "PLAYLIST_INVALID_MESSAGE", "PLAYLIST_TRANSPORT_REFUSAL",
    "MAX_TRACK_UPLOAD_BYTES",
    "MusicLibraryResult", "MusicPlaylistResult", "MusicPlaylistsResult", "MusicWriteResult",
    "MusicRadioResult", "MusicUploadResult",
    "list_music_playlists", "get_music_playlist", "read_music_library", "get_music_radios",
    "add_playlist_track", "remove_playlist_track", "reorder_playlist", "rename_playlist",
    "delete_playlist", "upload_music_track",
    "set_radio_playlist", "control_radio", "set_radio_config",
]

#: What the console declares for these surfaces (``services/webservice/permissions``). A plugin may
#: not import the console package, so the names are stated here once and the page reads them.
CAPABILITY_VIEW = "music.view"
CAPABILITY_WRITE = "music.write"
CAPABILITY_CONTROL = "music.control"
#: the upload's OWN capability: writing to an install's disk is a different power from editing a
#: playlist, and a capability is declared per verb class (the Missions tab's ``missions.upload``).
CAPABILITY_UPLOAD = "music.upload"

#: what a caller of an installation without the ``music`` plugin is told
SERVICE_MISSING = "Music is not installed: enable the music plugin."

#: the transport words :func:`control_radio` accepts, in the order a page offers them
CONTROLS: tuple[str, ...] = ("play", "stop", "skip", "mode")

#: the two modes a radio runs, as the wire words
MODES: tuple[str, ...] = ("repeat", "shuffle")

#: the values :func:`set_radio_config` accepts — SRS supports these two modulations and two sides
MODULATIONS: tuple[str, ...] = ("AM", "FM")
COALITIONS: tuple[str, ...] = ("1", "2")

#: the frequency and the volume a radio takes: a decimal string, in the SRS range
FREQUENCY_MAX = 10000.0
VOLUME_MAX = 1.0
#: the display name's bounds, as ``/music player``'s own edit modal states them
DISPLAY_NAME_MIN = 3
DISPLAY_NAME_MAX = 30

PLAYLIST_INVALID_MESSAGE = "A playlist name and at least one track are required."

#: The typed refusal every PLAYLIST WRITE answers a caller that is not the admin console with. A
#: playlist is INSTALLATION-WIDE and names no server, so an API key issued for ONE hosted server must
#: not rename, reorder, fill or delete a playlist every other server plays. The door is CLOSED: an
#: empty or unknown transport label is refused too, never fallen through.
PLAYLIST_TRANSPORT_REFUSAL = ("Playlist edits are accepted from the website only: a playlist is "
                              "installation-wide, so no REST, plugin, MCP or Discord transport may "
                              "change one.")


# ── the results ───────────────────────────────────────────────────────────
# Typed dataclasses rather than bare ActionResult values, so a transport reads the fields it words a
# notice with instead of parsing the sentence. Each carries the same facts in ``data`` (the shape
# every transport spreads), written once at construction so the two can never drift.


@dataclass
class MusicLibraryResult(ActionResult):
    """The outcome of a LIBRARY read: one node's music folder tree.

    ``folders`` is the tree, one entry per directory, each carrying its own files with their tags,
    its ``count`` and its total length in ``seconds``; ``scanned`` is the epoch the tags were read
    at (cached: a later render answers the same stamp until a read states ``refresh``).

    The node's music directory itself is NOT part of this result: it is an absolute path on the
    node's disk, and this payload reaches every caller that holds the read capability.
    """

    node_name: str = ""
    folders: list[dict[str, Any]] = field(default_factory=list)
    files: int = 0
    seconds: float = 0.0
    scanned: float = 0.0

    def __post_init__(self) -> None:
        for key in ("node_name", "folders", "files", "seconds", "scanned"):
            self.data.setdefault(key, getattr(self, key))


@dataclass
class MusicPlaylistResult(ActionResult):
    """The outcome of ONE playlist read: its tracks in playback order.

    Each track carries ``song_id`` (the row's identity, and what a reorder names), its ``file``, its
    tags, its ``duration``, its ``size`` and whether it is ``present`` on the node.
    """

    playlist: str = ""
    node_name: str = ""
    tracks: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    missing: int = 0

    def __post_init__(self) -> None:
        for key in ("playlist", "node_name", "tracks", "seconds", "missing"):
            self.data.setdefault(key, getattr(self, key))


@dataclass
class MusicPlaylistsResult(ActionResult):
    """The outcome of the PLAYLIST LIST: every playlist an installation has.

    Each row is ``name``, ``tracks``, ``seconds``, ``missing`` and ``used_by`` — the
    ``{"server", "radio"}`` pairs whose ``music_radios`` row assigns it.
    """

    node_name: str = ""
    playlists: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.data.setdefault("node_name", self.node_name)
        self.data.setdefault("playlists", self.playlists)


@dataclass
class MusicWriteResult(ActionResult):
    """The outcome of ONE playlist write.

    ``changed`` is how many rows the write touched, ``tracks`` what the playlist holds afterwards
    and ``unmapped`` how many radios lost their assignment because the playlist became empty.
    """

    playlist: str = ""
    tracks: int = 0
    changed: int = 0
    unmapped: int = 0

    def __post_init__(self) -> None:
        for key in ("playlist", "tracks", "changed", "unmapped"):
            self.data.setdefault(key, getattr(self, key))


@dataclass
class MusicRadioResult(ActionResult):
    """The outcome of ONE radio read or radio write: the radios of a server, as they stand after it."""

    server_name: str = ""
    radio_name: str = ""
    radios: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for key in ("server_name", "radio_name", "radios"):
            self.data.setdefault(key, getattr(self, key))


@dataclass
class MusicUploadResult(ActionResult):
    """The outcome of ONE library upload: the file, the folder it landed in and its size.

    ``folder`` is the music-directory-relative folder (``""`` is the music directory itself) and
    ``track`` the folder-relative-plus-name path a playlist row would carry.
    """

    node_name: str = ""
    folder: str = ""
    name: str = ""
    size: int = 0
    track: str = ""

    def __post_init__(self) -> None:
        for key in ("node_name", "folder", "name", "size", "track"):
            self.data.setdefault(key, getattr(self, key))


# ── the service and its vocabulary ────────────────────────────────────────


def _service() -> Any:
    """The process's ``MusicService``, or ``None`` when the plugin is not enabled.

    The import happens HERE and not at module level: the service module pulls the bot's HTTP, audio
    and filesystem stack, and an action module that failed to import would offer no action at all.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.music import MusicService

        return ServiceRegistry.get(MusicService)
    except Exception:
        log.exception("Music actions: the music service could not be resolved.")
        return None


def _text(value: Any) -> str:
    """*value* as a stripped string — a caller-supplied name or path, normalised once."""
    return str(value or "").strip()


def _identifiers(values: Any) -> list[int] | None:
    """The positive ``song_id`` set *values* names, deduplicated, or ``None`` when one is not one.

    A row id is the identity a reorder and a removal name.
    """
    if values is None:
        return None
    items = [values] if isinstance(values, (int, str)) else list(values)
    found: list[int] = []
    for item in items:
        if isinstance(item, bool):
            return None
        try:
            number = int(str(item).strip())
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        if number not in found:
            found.append(number)
    return found or None


def _track_paths(values: Any) -> tuple[list[str], str]:
    """``(paths, refusal)`` for the track paths a caller states — the service's containment rule.

    Every path is joined onto the node's music directory, so a value naming a LOCATION is refused
    before anything is written. The refusal names the value: it is the caller's own text.
    """
    try:
        from services.music.service import music_path
    except Exception:
        log.exception("Music actions: the path rule could not be imported.")
        return [], SERVICE_MISSING
    if values is None:
        return [], PLAYLIST_INVALID_MESSAGE
    items = [values] if isinstance(values, str) else list(values)
    accepted: list[str] = []
    for value in items:
        relative = music_path(value)
        if relative is None:
            return accepted, (f"'{_text(value)}' is not a track of the music directory: nothing was "
                              f"changed. A track path is relative to the music directory and cannot "
                              f"be absolute or leave the folder.")
        if relative not in accepted:
            accepted.append(relative)
    if not accepted:
        return [], PLAYLIST_INVALID_MESSAGE
    return accepted, ""


def _frequency(value: Any) -> str | None:
    """The frequency a radio takes — a decimal string in the SRS range — or ``None``."""
    text = _text(value)
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0 or number >= FREQUENCY_MAX:
        return None
    return text


def _modulation(value: Any) -> str | None:
    """``AM`` or ``FM``, upper-cased, or ``None``."""
    text = _text(value).upper()
    return text if text in MODULATIONS else None


def _coalition(value: Any) -> int | None:
    """``1`` (red) or ``2`` (blue), or ``None``."""
    text = _text(value)
    return int(text) if text in COALITIONS else None


def _volume(value: Any) -> str | None:
    """The playback volume — a decimal string from 0 to 1 — or ``None``."""
    text = _text(value)
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or number > VOLUME_MAX:
        return None
    return text


def _display_name(value: Any) -> str | None:
    """The name the radio shows in SRS, whitespace-collapsed, or ``None`` when it is outside the bounds."""
    text = " ".join(_text(value).split())
    if len(text) < DISPLAY_NAME_MIN or len(text) > DISPLAY_NAME_MAX:
        return None
    return text


# ── the seam's two resolutions ────────────────────────────────────────────


async def _node(ctx: Any, name: str) -> tuple[Any, str]:
    """``(node, "")`` for a node in the caller's view, or ``(None, refusal)``.

    A name the caller may not see and a name nothing answers to are DIFFERENT refusals: the first is
    the scope's (the caller learns nothing about the cluster), the second is the seam's own sentence,
    which the node page already renders.
    """
    resolution = await ctx.resolve_scoped_node(name)
    if resolution.is_found:
        return resolution.node, ""
    if resolution.status == NodeResolution.NOT_PERMITTED:
        return None, f"Not authorized: node '{name}' is outside your scope."
    return None, resolution.message


async def _server(ctx: Any, name: str) -> tuple[Any, str]:
    """``(server, "")`` for a server in the caller's scope, or ``(None, refusal)``."""
    resolution = await ctx.resolve_scoped_server(name)
    if resolution.is_found:
        return resolution.server, ""
    if resolution.status == ServerResolution.NOT_PERMITTED:
        return None, f"Not authorized: server '{name}' is outside your scope."
    return None, resolution.message


def _own_node(ctx: Any) -> Any:
    """The node this process runs on — where the library is read when a caller names no node."""
    return getattr(ctx, "node", None)


def _playlist_write_authorised(ctx: Any) -> str | None:
    """The typed refusal a PLAYLIST write answers with, or ``None`` when the caller may write.

    The console is the ONLY transport that reaches a playlist write (:data:`TRANSPORT_WEB`). Every
    other label — the REST/plugin one, the in-process ``service`` one (MCP) and an empty or unknown
    one — is refused with :data:`PLAYLIST_TRANSPORT_REFUSAL`: a playlist names no server, so the
    scope cannot narrow the write and a key issued for one hoster's server would otherwise edit a
    playlist every server plays. The playlist READS are deliberately not guarded this way: they
    return playlist names and the tracks of the library, which is what an operator reading them
    already sees, and the node they read on is resolved through the caller's scope.
    """
    if getattr(ctx, "transport", "") != TRANSPORT_WEB:
        return PLAYLIST_TRANSPORT_REFUSAL
    return None


async def _playlist_node(ctx: Any, pairs: list[dict[str, str]]) -> tuple[Any, str]:
    """``(node, "")`` for the node a playlist's files resolve on, or ``(None, refusal)``.

    A playlist carries no node of its own, and its files are read where a disk holds them, so the
    node is the one of the FIRST radio that assigns the playlist (a ``music_radios`` row's server
    names it). The pairing is resolved through the CALLER's scope, so a playlist assigned only on a
    server the caller cannot see is read on the caller's own node instead — the same answer an
    unassigned playlist gets, and no node name crosses the scope. A playlist no radio uses falls
    back to this process's own node: nothing is playing it, so the page shows the playlist as its
    author would see it.
    """
    for pair in sorted(pairs, key=lambda entry: (entry["server"], entry["radio"])):
        resolution = await ctx.resolve_scoped_server(pair["server"])
        if not resolution.is_found:
            continue
        node = getattr(resolution.server, "node", None)
        if node is not None:
            return node, ""
    node = _own_node(ctx)
    if node is None:
        return None, "This installation knows no node to read the music directory from."
    return node, ""


async def _usage(service: Any) -> tuple[dict[str, list[dict[str, str]]], str]:
    """``({playlist: [{"server", "radio"}]}, refusal)`` from the ``music_radios`` rows."""
    try:
        pairs = await service.playlist_usage()
    except Exception as ex:
        log.exception("Music actions: the radio assignments could not be read")
        return {}, f"The playlist assignments could not be read: {ex}"
    usage: dict[str, list[dict[str, str]]] = {}
    for pair in pairs:
        usage.setdefault(pair["playlist"], []).append({"server": pair["server"],
                                                       "radio": pair["radio"]})
    return usage, ""


def _flatten(usage: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    """Every ``(server, radio)`` pair once, in one order — the library's node is picked from these."""
    return [pair for pairs in usage.values() for pair in pairs]


async def _audited(ctx: Any, result: _Result, *, server: Any = None) -> _Result:
    """Write the trail for *result* — ONE entry per attempt, success or refusal — and return it."""
    await audit_action(ctx, result, server=server)
    return result


def _write(success: bool, message: str, *, playlist: str = "", tracks: int = 0, changed: int = 0,
           unmapped: int = 0, **data: Any) -> MusicWriteResult:
    """A :class:`MusicWriteResult` for one outcome, its extra facts merged into ``data``."""
    result = MusicWriteResult(success=success, message=message, playlist=playlist, tracks=tracks,
                              changed=changed, unmapped=unmapped)
    result.data.update(data)
    return result


def _radio(success: bool, message: str, *, server_name: str = "", radio_name: str = "",
           radios: Any = None, **data: Any) -> MusicRadioResult:
    """A :class:`MusicRadioResult` for one outcome."""
    result = MusicRadioResult(success=success, message=message, server_name=server_name,
                              radio_name=radio_name, radios=list(radios or []))
    result.data.update(data)
    return result


def _playlists(success: bool, message: str, *, node_name: str = "",
               playlists: Any = None) -> MusicPlaylistsResult:
    """A :class:`MusicPlaylistsResult` for one outcome."""
    return MusicPlaylistsResult(success=success, message=message, node_name=node_name,
                                playlists=list(playlists or []))


def _playlist(success: bool, message: str, *, playlist: str = "", node_name: str = "",
              found: dict[str, Any] | None = None) -> MusicPlaylistResult:
    """A :class:`MusicPlaylistResult` for one outcome, filled from the service's own answer."""
    answer = found or {}
    return MusicPlaylistResult(success=success, message=message,
                               playlist=str(answer.get("name") or playlist), node_name=node_name,
                               tracks=list(answer.get("tracks") or []),
                               seconds=float(answer.get("seconds") or 0.0),
                               missing=int(answer.get("missing") or 0))


def _library(success: bool, message: str, *, payload: dict | None = None) -> MusicLibraryResult:
    """A :class:`MusicLibraryResult` for one outcome, filled from the service's own answer."""
    answer = payload or {}
    return MusicLibraryResult(success=success, message=message,
                              node_name=str(answer.get("node") or ""),
                              folders=list(answer.get("folders") or []),
                              files=int(answer.get("files") or 0),
                              seconds=float(answer.get("seconds") or 0.0),
                              scanned=float(answer.get("scanned") or 0.0))


# ── the READS ─────────────────────────────────────────────────────────────


@action
async def list_music_playlists(ctx: Any, *, node_name: str = "",
                               refresh: bool = False) -> MusicPlaylistsResult:
    """List every playlist with its track count, its total length, its missing tracks and its radios.

    The playlist table is installation-wide, so no target is resolved: the NODE is, because the
    lengths and the missing flags are read from the files of the node that holds the disk. A
    ``node_name`` states which node; without one the first assigned radio's node answers. The
    answer's ``node_name`` is that node — one read is one node's view, so a playlist two nodes would
    read differently appears with the lengths of the node this answer was read on.

    A ``refresh`` re-scans that node's library instead of answering from the cache — tag reading is
    what makes a scan expensive, so a render uses the cache and the console's Rescan does not.
    """
    service = _service()
    if service is None:
        return _playlists(False, SERVICE_MISSING, node_name=_text(node_name))
    usage, refusal = await _usage(service)
    if refusal:
        return _playlists(False, refusal, node_name=_text(node_name))
    if _text(node_name):
        node, refusal = await _node(ctx, _text(node_name))
    else:
        node, refusal = await _playlist_node(ctx, _flatten(usage))
    if node is None:
        return _playlists(False, refusal, node_name=_text(node_name))
    label = str(getattr(node, "name", "") or _text(node_name))
    try:
        rows = await service.list_playlists(node, refresh=refresh)
    except (TimeoutError, asyncio.TimeoutError):
        return _playlists(False, f"Node '{label}' did not answer in time, so the playlists are "
                                 f"unknown.", node_name=label)
    except Exception as ex:
        log.exception("list_music_playlists: the playlists could not be read")
        return _playlists(False, f"The playlists could not be read on node '{label}': {ex}",
                          node_name=label)
    for row in rows:
        row["used_by"] = usage.get(str(row["name"]), [])
    if not rows:
        return _playlists(True, "No playlist exists yet.", node_name=label)
    return _playlists(True, f"{len(rows)} playlist(s) on node '{label}'.", node_name=label,
                      playlists=rows)


@action
async def get_music_playlist(ctx: Any, *, playlist: str, node_name: str = "",
                             refresh: bool = False) -> MusicPlaylistResult:
    """Read ONE playlist: its tracks in playback order, with the tags and the presence of each file.

    The node is resolved the same way :func:`list_music_playlists` resolves it — a playlist names no
    node, and its files are read where the disk is.
    """
    name = _text(playlist)
    service = _service()
    if service is None:
        return _playlist(False, SERVICE_MISSING, playlist=name, node_name=_text(node_name))
    if not name:
        return _playlist(False, "A playlist name is required.", node_name=_text(node_name))
    usage, refusal = await _usage(service)
    if refusal:
        return _playlist(False, refusal, playlist=name, node_name=_text(node_name))
    if _text(node_name):
        node, refusal = await _node(ctx, _text(node_name))
    else:
        node, refusal = await _playlist_node(ctx, _flatten(usage))
    if node is None:
        return _playlist(False, refusal, playlist=name, node_name=_text(node_name))
    label = str(getattr(node, "name", "") or _text(node_name))
    try:
        found = await service.get_playlist(node, name, refresh=refresh)
    except (TimeoutError, asyncio.TimeoutError):
        return _playlist(False, f"Node '{label}' did not answer in time, so playlist '{name}' is "
                                f"unknown.", playlist=name, node_name=label)
    except Exception as ex:
        log.exception("get_music_playlist: the playlist could not be read")
        return _playlist(False, f"Playlist '{name}' could not be read: {ex}", playlist=name,
                         node_name=label)
    if found is None:
        return _playlist(False, f"Playlist '{name}' not found.", playlist=name, node_name=label)
    return _playlist(True, f"Playlist '{found['name']}' holds {len(found['tracks'])} track(s).",
                     playlist=name, node_name=label, found=found)


@action
async def read_music_library(ctx: Any, *, node_name: str = "",
                             refresh: bool = False) -> MusicLibraryResult:
    """Read ONE node's music library: the folder tree with per-folder counts and the files with tags.

    A NODE read on purpose — the music directory belongs to the machine that holds the disk — and an
    expensive one: the tree is cached per directory, and only a ``refresh`` re-reads the tags.
    """
    service = _service()
    if service is None:
        return _library(False, SERVICE_MISSING)
    if _text(node_name):
        node, refusal = await _node(ctx, _text(node_name))
        if node is None:
            return _library(False, refusal)
    else:
        node = _own_node(ctx)
        if node is None:
            return _library(False, "This installation knows no node to read the music directory from.")
    label = str(getattr(node, "name", "") or _text(node_name))
    try:
        payload = await service.read_library(node, refresh=refresh)
    except (TimeoutError, asyncio.TimeoutError):
        return _library(False, f"Node '{label}' did not answer in time, so its music library is "
                               f"unknown.")
    except Exception as ex:
        log.exception("read_music_library: the library could not be read")
        return _library(False, f"The music library of node '{label}' could not be read: {ex}")
    answer = _library(True, f"{payload['files']} track(s) in {len(payload['folders'])} folder(s) on "
                            f"node '{label}'.", payload=payload)
    return answer


@action
async def get_music_radios(ctx: Any, *, server_name: str) -> MusicRadioResult:
    """Read every radio configured for ONE server: its settings, its state and its current track.

    Per server on purpose: a radio belongs to one server, so the caller's scope is applied to the
    name and a foreign server is refused before any node is asked. Configured-but-idle radios are
    reported too — a radio nobody has started is still a radio an operator may configure.
    """
    service = _service()
    server, refusal = await _server(ctx, _text(server_name))
    if server is None:
        return _radio(False, refusal, server_name=_text(server_name))
    if service is None:
        return _radio(False, SERVICE_MISSING, server_name=server.name)
    try:
        radios = await service.read_radios(server)
    except (TimeoutError, asyncio.TimeoutError):
        return _radio(False, f"Server '{server.name}' did not answer in time, so its radios are "
                             f"unknown.", server_name=server.name)
    except Exception as ex:
        log.exception("get_music_radios: the radios could not be read")
        return _radio(False, f"The radios of server '{server.name}' could not be read: {ex}",
                      server_name=server.name)
    if not radios:
        return _radio(True, f"No radio is configured for server '{server.name}'.", server_name=server.name)
    return _radio(True, f"{len(radios)} radio(s) on server '{server.name}'.", server_name=server.name,
                  radios=radios)


# ── the PLAYLIST WRITES ───────────────────────────────────────────────────


@action
async def add_playlist_track(ctx: Any, *, playlist: str, song_files: Any) -> MusicWriteResult:
    """Add track files to a playlist — creating the playlist with its first track.

    A playlist has no row of its own while it is empty (that is what ``/playlist add`` does today),
    so a draft a page holds is never written: the list exists once this action has a track to put in
    it. Every path is joined onto the music directory and refused when it leaves it.
    """
    refusal = _playlist_write_authorised(ctx)
    if refusal:
        return await _audited(ctx, _write(False, refusal, playlist=_text(playlist)))
    name = _text(playlist)
    if not name:
        return await _audited(ctx, _write(False, "A playlist name is required."))
    paths, refusal = _track_paths(song_files)
    if refusal:
        return await _audited(ctx, _write(False, refusal, playlist=name))
    service = _service()
    if service is None:
        return await _audited(ctx, _write(False, SERVICE_MISSING, playlist=name))
    try:
        answer = await service.add_tracks(name, paths)
    except Exception as ex:
        log.exception("add_playlist_track: the tracks could not be written")
        return await _audited(ctx, _write(False, f"The tracks could not be added to playlist "
                                                 f"'{name}': {ex}", playlist=name))
    result = answer.get("status")
    if result == "invalid":
        return await _audited(ctx, _write(False, PLAYLIST_INVALID_MESSAGE, playlist=name))
    label = str(answer.get("playlist") or name)
    added = int(answer.get("added") or 0)
    already = int(answer.get("already") or 0)
    if result == "duplicate" or not added:
        return await _audited(ctx, _write(
            False, f"Not done: every one of those {already or 0} track(s) is already in playlist "
                   f"'{label}'; nothing was changed.", playlist=label,
            tracks=answer.get("tracks", 0), already=already))
    if answer.get("created"):
        message = f"Playlist '{label}' created with {added} track(s)."
    else:
        message = f"{added} track(s) added to playlist '{label}'."
    if already:
        message += f" {already} track(s) were already in playlist '{label}'."
    return await _audited(ctx, _write(True, message, playlist=label, tracks=answer.get("tracks", 0),
                                      changed=added, created=answer.get("created"),
                                      already=already))


@action
async def remove_playlist_track(ctx: Any, *, playlist: str, song_ids: Any) -> MusicWriteResult:
    """Remove one or more tracks — named by ``song_id`` — from a playlist.

    ``song_ids`` is a list: the row strip removes one track, and the toolbar's "remove the missing
    ones" is the same write with every row it flagged, in ONE transaction. A removal that empties
    the playlist removes its ``music_radios`` rows too, which is the behaviour ``/playlist delete``
    already has: a radio must never point at a playlist that no longer holds a track.
    """
    refusal = _playlist_write_authorised(ctx)
    if refusal:
        return await _audited(ctx, _write(False, refusal, playlist=_text(playlist)))
    name = _text(playlist)
    if not name:
        return await _audited(ctx, _write(False, "A playlist name is required."))
    identifiers = _identifiers(song_ids)
    if identifiers is None:
        return await _audited(ctx, _write(False, "A playlist track is named by its track id: at "
                                                 "least one positive id is required.", playlist=name))
    service = _service()
    if service is None:
        return await _audited(ctx, _write(False, SERVICE_MISSING, playlist=name))
    try:
        answer = await service.remove_tracks(name, identifiers)
    except Exception as ex:
        log.exception("remove_playlist_track: the tracks could not be removed")
        return await _audited(ctx, _write(False, f"The tracks could not be removed from playlist "
                                                 f"'{name}': {ex}", playlist=name))
    status = answer.get("status")
    if status == "missing":
        return await _audited(ctx, _write(False, f"Playlist '{name}' not found.", playlist=name))
    if status == "invalid":
        return await _audited(ctx, _write(False, "A playlist track is named by its track id.",
                                          playlist=name))
    label = str(answer.get("playlist") or name)
    if not answer.get("removed"):
        return await _audited(ctx, _write(False, f"None of those tracks is in playlist '{label}'; "
                                                 f"nothing was changed.", playlist=label,
                                          tracks=answer.get("tracks", 0)))
    message = f"{answer['removed']} track(s) removed from playlist '{label}'."
    if answer.get("unmapped"):
        message += (f" The playlist is empty, so {answer['unmapped']} radio assignment(s) were "
                    f"removed with it.")
    return await _audited(ctx, _write(True, message, playlist=label, tracks=answer.get("tracks", 0),
                                      changed=answer.get("removed", 0),
                                      unmapped=answer.get("unmapped", 0)))


@action
async def reorder_playlist(ctx: Any, *, playlist: str, order: Any) -> MusicWriteResult:
    """Replace a playlist's playback order with the FULL new order — one transaction, one write.

    ``order`` is the complete list of ``song_id`` values in their new order, and it is REFUSED unless
    it is a permutation of the playlist's current rows: a page holding a stale list must be told to
    reload rather than have its order written over tracks it never saw. A reorder is what a radio
    hears, because ``song_id`` is the order the radio reads its playlist in.
    """
    refusal = _playlist_write_authorised(ctx)
    if refusal:
        return await _audited(ctx, _write(False, refusal, playlist=_text(playlist)))
    name = _text(playlist)
    if not name:
        return await _audited(ctx, _write(False, "A playlist name is required."))
    identifiers = _identifiers(order)
    if identifiers is None:
        return await _audited(ctx, _write(False, "A playlist order is the full list of track ids in "
                                                 "their new order: at least one positive id is "
                                                 "required.", playlist=name))
    service = _service()
    if service is None:
        return await _audited(ctx, _write(False, SERVICE_MISSING, playlist=name))
    try:
        answer = await service.reorder_tracks(name, identifiers)
    except Exception as ex:
        log.exception("reorder_playlist: the order could not be written")
        return await _audited(ctx, _write(False, f"The order of playlist '{name}' could not be "
                                                 f"saved: {ex}", playlist=name))
    status = answer.get("status")
    if status == "missing":
        return await _audited(ctx, _write(False, f"Playlist '{name}' not found.", playlist=name))
    if status == "invalid":
        return await _audited(ctx, _write(False, "A playlist order is the full list of track ids.",
                                          playlist=name))
    label = str(answer.get("playlist") or name)
    if status == "not-a-permutation":
        return await _audited(ctx, _write(
            False, f"Not done: that is not the current track list of playlist '{label}'. Reload the "
                   f"page and try again — nothing was changed.", playlist=label,
            tracks=answer.get("tracks", 0)))
    # ``song_ids`` travels with the answer: the rows are RENUMBERED by the write, so a page that keeps
    # its own copy of the ids must re-stamp them from this list or its next drop would name ids that
    # no longer exist. The service answers the ids in playback order; the page's order IS that order.
    return await _audited(ctx, _write(True, f"The order of playlist '{label}' was saved "
                                            f"({answer.get('tracks', 0)} track(s)).", playlist=label,
                                      tracks=answer.get("tracks", 0),
                                      changed=answer.get("tracks", 0),
                                      song_ids=list(answer.get("song_ids") or [])))


@action
async def rename_playlist(ctx: Any, *, playlist: str, name: str) -> MusicWriteResult:
    """Rename a playlist in ``music_playlists`` AND in ``music_radios``, in ONE transaction.

    A name another playlist already holds is refused: the two tables carry the name alone, so a
    duplicate would silently merge two playlists. The radios that assigned the old name play the new
    one afterwards — the assignment is moved with it.
    """
    refusal = _playlist_write_authorised(ctx)
    if refusal:
        return await _audited(ctx, _write(False, refusal, playlist=_text(playlist)))
    source = _text(playlist)
    target = _text(name)
    if not source or not target:
        return await _audited(ctx, _write(False, "A playlist name is required.", playlist=source))
    if source == target:
        return await _audited(ctx, _write(False, f"Playlist '{source}' already has that name; nothing "
                                                 f"was changed.", playlist=source))
    service = _service()
    if service is None:
        return await _audited(ctx, _write(False, SERVICE_MISSING, playlist=source))
    try:
        answer = await service.rename_playlist(source, target)
    except Exception as ex:
        log.exception("rename_playlist: the playlist could not be renamed")
        return await _audited(ctx, _write(False, f"Playlist '{source}' could not be renamed: {ex}",
                                          playlist=source))
    status = answer.get("status")
    if status == "missing":
        return await _audited(ctx, _write(False, f"Playlist '{source}' not found.", playlist=source))
    if status == "invalid":
        return await _audited(ctx, _write(False, "A playlist name is required.", playlist=source))
    if status == "exists":
        return await _audited(ctx, _write(False, f"Not done: a playlist named '{target}' already "
                                                 f"exists.", playlist=source))
    previous = str(answer.get("previous") or source)
    label = str(answer.get("playlist") or target)
    message = f"Playlist '{previous}' renamed to '{label}'."
    if answer.get("radios"):
        message += f" {answer['radios']} radio assignment(s) moved with it."
    return await _audited(ctx, _write(True, message, playlist=label, tracks=answer.get("tracks", 0),
                                      changed=answer.get("tracks", 0), previous=previous,
                                      radios=answer.get("radios", 0)))


@action
async def delete_playlist(ctx: Any, *, playlist: str) -> MusicWriteResult:
    """Delete a playlist: its tracks and the radio rows that assigned it."""
    refusal = _playlist_write_authorised(ctx)
    if refusal:
        return await _audited(ctx, _write(False, refusal, playlist=_text(playlist)))
    name = _text(playlist)
    if not name:
        return await _audited(ctx, _write(False, "A playlist name is required."))
    service = _service()
    if service is None:
        return await _audited(ctx, _write(False, SERVICE_MISSING, playlist=name))
    try:
        answer = await service.delete_playlist(name)
    except Exception as ex:
        log.exception("delete_playlist: the playlist could not be deleted")
        return await _audited(ctx, _write(False, f"Playlist '{name}' could not be deleted: {ex}",
                                          playlist=name))
    status = answer.get("status")
    if status == "missing":
        return await _audited(ctx, _write(False, f"Playlist '{name}' not found.", playlist=name))
    if status == "invalid":
        return await _audited(ctx, _write(False, "A playlist name is required.", playlist=name))
    label = str(answer.get("playlist") or name)
    message = f"Playlist '{label}' deleted ({answer.get('tracks', 0)} track(s))."
    if answer.get("unmapped"):
        message += f" {answer['unmapped']} radio assignment(s) were removed with it."
    return await _audited(ctx, _write(True, message, playlist=label,
                                      tracks=answer.get("tracks", 0),
                                      changed=answer.get("tracks", 0),
                                      unmapped=answer.get("unmapped", 0)))


# ── the LIBRARY UPLOAD ────────────────────────────────────────────────────
# ONE track, ONE folder, ONE write: the file goes into the folder of the music directory the caller
# is looking at, on the NODE that holds that disk. The console hands BYTES (a ``web`` transport may
# never name a path); the bytes are staged in the ``files`` table and only the row id crosses the
# wire, so the service on the owning node reads them back through its own writer.

#: The size cap for an uploaded track — ONE number, stated here and mirrored by the console's own
#: handler (which refuses the request before the multipart parser decides), so the two cannot drift.
#: 32 MiB: comfortably above a long ``.mp3`` and far below a mistake.
MAX_TRACK_UPLOAD_BYTES = 32 * 1024 * 1024


def _upload_cap_message() -> str:
    """The refusal for a track over :data:`MAX_TRACK_UPLOAD_BYTES`, read live so a test can patch it."""
    return (f"The track is larger than the {MAX_TRACK_UPLOAD_BYTES // (1024 * 1024)} MiB limit and "
            f"was not uploaded.")


async def _stage_track_bytes(node: Any, name: str, data: bytes) -> int:
    """Put *data* in the ``files`` table on *node*'s pool and return its row id.

    THE route bytes take to the owning node — the same table ``NodeProxy.write_file`` stages into
    (``core/data/proxy/nodeproxy.py``), read back there by ``Node.write_file(target, <id>)``. One
    route for a master-hosted folder and an agent-hosted one alike.
    """
    import psycopg

    async with node.apool.connection() as conn:
        cursor = await conn.execute("""
            INSERT INTO files (guild_id, name, data)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (node.guild_id, name, psycopg.Binary(data)))
        return (await cursor.fetchone())[0]


async def _discard_staged_track(node: Any, row_id: int) -> None:
    """Delete a staged row — IDEMPOTENT, so a write that consumed it is not a second case."""
    try:
        async with node.apool.connection() as conn:
            await conn.execute("DELETE FROM files WHERE id = %s", (row_id,))
    except Exception:
        log.warning("Music: the staged upload row %s could not be discarded.", row_id, exc_info=True)


def _upload(success: bool, message: str, *, node_name: str = "", folder: str = "", name: str = "",
            size: int = 0, track: str = "") -> MusicUploadResult:
    """A :class:`MusicUploadResult` for one outcome."""
    return MusicUploadResult(success=success, message=message, node_name=node_name, folder=folder,
                             name=name, size=size, track=track)


def _upload_refusal(reason: str, *, leaf: str, folder: str, label: str) -> str | None:
    """The sentence the service's own status earns, or ``None`` when it is ``TRACK_OK``.

    The reasons are the service's tokens, imported here so the two vocabularies cannot drift — the
    SAME shape :func:`_radio_refusal` has.
    """
    from services.music.service import (TRACK_EXISTS, TRACK_FOLDER_INVALID, TRACK_FOLDER_MISSING,
                                        TRACK_INVALID_NAME, TRACK_NOT_AUDIO, TRACK_NO_ROOT,
                                        TRACK_OK, TRACK_WRITE_ERROR)

    where = f"'{folder}' on node '{label}'" if folder else f"the music directory on node '{label}'"
    if reason == TRACK_OK:
        return None
    if reason == TRACK_INVALID_NAME:
        return (f"'{leaf}' is not a file name: an upload names ONE file, without a folder, a drive "
                f"or a path.")
    if reason == TRACK_NOT_AUDIO:
        return f"'{leaf}' was not uploaded: a track is a .mp3 or a .ogg file."
    if reason == TRACK_FOLDER_INVALID:
        return (f"'{folder}' is not a folder of the music directory: nothing was uploaded. The "
                f"folder is relative to the music directory and cannot leave it.")
    if reason == TRACK_FOLDER_MISSING:
        return f"The folder {where} does not exist, so '{leaf}' was not uploaded."
    if reason == TRACK_EXISTS:
        return f"Not uploaded: {where} already holds '{leaf}'. Nothing was overwritten."
    if reason == TRACK_NO_ROOT:
        return f"No music directory is configured on node '{label}', so '{leaf}' was not uploaded."
    if reason == TRACK_WRITE_ERROR:
        return f"'{leaf}' could not be written to {where}."
    return f"'{leaf}' was not uploaded: {reason}."


@action
async def upload_music_track(ctx: Any, *, folder: str = "", name: str = "", node_name: str = "",
                             source: Any = b"") -> MusicUploadResult:
    """Upload ONE ``.mp3``/``.ogg`` track into a folder of a node's music library.

    The order, and every gate is HERE so a caller cannot reach the disk without passing it:

    1. the NAME is a leaf (:func:`~services.music.service.upload_leaf`) and carries an extension the
       library offers (:func:`~services.music.service.track_suffix`) — refused by name otherwise;
    2. the SOURCE is BYTES. A ``str`` — a path, a URL — is refused for EVERY transport: the console
       passes the file it was given, and the service never fetches on a caller's word;
    3. an empty source and one over :data:`MAX_TRACK_UPLOAD_BYTES` each get their own sentence;
    4. the NODE is the caller's own unless one is named, resolved through the CALLER's scope, so a
       name outside it is refused before anything is staged;
    5. the bytes are STAGED and the service on that node writes them, refusing a folder that leaves
       the music directory, one that does not exist, and a file the folder already holds;
    6. the staged row is discarded UNCONDITIONALLY, so no outcome leaves one behind.

    The folder is judged by the SERVICE, not here: it is resolved against the music directory of the
    node that writes, which is the only place the answer can be known.
    """
    from services.music.service import track_suffix, upload_leaf

    typed = _text(name)
    leaf = upload_leaf(name)
    if leaf is None:
        message = ("A file name is required." if not typed else
                   f"'{typed}' is not a file name: an upload names ONE file, without a folder, a "
                   f"drive or a path.")
        return await _audited(ctx, _upload(False, message, name=typed, folder=_text(folder)))
    if track_suffix(leaf) is None:
        return await _audited(ctx, _upload(False, f"'{leaf}' was not uploaded: a track is a .mp3 or "
                                                  f"a .ogg file.", name=leaf, folder=_text(folder)))
    if not isinstance(source, (bytes, bytearray)):
        return await _audited(ctx, _upload(False, f"'{leaf}' must be uploaded as file content: a "
                                                  f"path or a URL is not accepted.", name=leaf,
                                           folder=_text(folder)))
    data = bytes(source)
    if not data:
        return await _audited(ctx, _upload(False, f"The file '{leaf}' is empty and was not uploaded.",
                                           name=leaf, folder=_text(folder)))
    if len(data) > MAX_TRACK_UPLOAD_BYTES:
        return await _audited(ctx, _upload(False, _upload_cap_message(), name=leaf,
                                           folder=_text(folder)))
    if _text(node_name):
        node, refusal = await _node(ctx, _text(node_name))
    else:
        node, refusal = _own_node(ctx), ""
        if node is None:
            refusal = "This installation knows no node to write the music directory on."
    if node is None:
        return await _audited(ctx, _upload(False, refusal, name=leaf, folder=_text(folder),
                                           node_name=_text(node_name)))
    label = str(getattr(node, "name", "") or _text(node_name))
    service = _service()
    if service is None:
        return await _audited(ctx, _upload(False, SERVICE_MISSING, node_name=label, name=leaf,
                                           folder=_text(folder)))
    try:
        row_id = await _stage_track_bytes(node, leaf, data)
    except Exception as ex:
        log.exception("upload_music_track: the bytes of '%s' could not be staged.", leaf)
        return await _audited(ctx, _upload(False, f"'{leaf}' could not be uploaded: {ex}",
                                           node_name=label, name=leaf, folder=_text(folder)))
    try:
        answer = await service.save_track(node, _text(folder), leaf, row_id)
    except (TimeoutError, asyncio.TimeoutError):
        return await _audited(ctx, _upload(False, f"Node '{label}' did not answer in time, so "
                                                  f"'{leaf}' was not uploaded.", node_name=label,
                                           name=leaf, folder=_text(folder)))
    except Exception as ex:
        log.exception("upload_music_track: the track could not be written")
        return await _audited(ctx, _upload(False, f"'{leaf}' could not be uploaded: {ex}",
                                           node_name=label, name=leaf, folder=_text(folder)))
    finally:
        # UNCONDITIONAL: the action owns every row it staged, so a refusal, a write error and a
        # success all leave none behind (a success's write has already consumed it).
        await _discard_staged_track(node, row_id)
    answer = answer if isinstance(answer, dict) else {}
    status = str(answer.get("status") or "")
    stored = _text(answer.get("folder"))
    track = _text(answer.get("track"))
    refusal = _upload_refusal(status, leaf=leaf, folder=stored or _text(folder), label=label)
    if refusal is not None:
        return await _audited(ctx, _upload(False, refusal, node_name=label, name=leaf,
                                           folder=stored, track=track))
    where = f"'{stored}' on node '{label}'" if stored else f"the music directory on node '{label}'"
    return await _audited(ctx, _upload(True, f"'{leaf}' uploaded to {where}.",
                                       node_name=label, folder=stored, name=leaf,
                                       size=int(answer.get("size") or 0), track=track))


# ── the RADIO WRITES ──────────────────────────────────────────────────────

def _radio_refusal(reason: str, server_name: str, radio_name: str, playlist: str = "") -> str:
    """The sentence a radio write's own answer earns — every reason has one.

    The reasons are the service's own tokens, imported here so the two vocabularies cannot drift.
    """
    from services.music.service import PLAYLIST_MISSING, RADIO_IDLE, RADIO_INVALID, RADIO_UNKNOWN

    if reason == RADIO_UNKNOWN:
        return (f"Radio '{radio_name}' is not configured for server '{server_name}': its block in "
                f"config/services/music.yaml is what defines a radio.")
    if reason == RADIO_IDLE:
        return (f"Radio '{radio_name}' has no live player on server '{server_name}' — its server is "
                f"not running, so there is nothing to play, stop or skip.")
    if reason == PLAYLIST_MISSING:
        return (f"Playlist '{playlist}' does not exist, so radio '{radio_name}' of server "
                f"'{server_name}' was not reassigned.")
    if reason == RADIO_INVALID:
        return "That is not a value a radio takes."
    return f"Radio '{radio_name}' of server '{server_name}' could not be changed: {reason}."


async def _radio_state(service: Any, server: Any, radio_name: str = "") -> list[dict]:
    """The radios of *server* after a write — what the result carries back to the page."""
    try:
        return await service.read_radios(server)
    except Exception:
        log.warning("Music actions: the radios could not be read back after a write.", exc_info=True)
        return []


@action
async def set_radio_playlist(ctx: Any, *, server_name: str, radio_name: str,
                             playlist: str) -> MusicRadioResult:
    """Assign a playlist to one radio AND reload it: a radio re-reads its song list only here.

    Writing the assignment alone would leave a playing radio on the list it loaded before the write,
    so the service goes through the radio's own setter (stop, assign, start) — and the edit an
    operator just made is audible at once instead of at the next restart.
    """
    name = _text(radio_name)
    target = _text(playlist)
    if not name:
        return await _audited(ctx, _radio(False, "A radio name is required.",
                                          server_name=_text(server_name)))
    if not target:
        return await _audited(ctx, _radio(False, "A playlist is required.", server_name=_text(server_name),
                                          radio_name=name))
    server, refusal = await _server(ctx, _text(server_name))
    if server is None:
        return await _audited(ctx, _radio(False, refusal, server_name=_text(server_name),
                                          radio_name=name))
    service = _service()
    if service is None:
        return await _audited(ctx, _radio(False, SERVICE_MISSING, server_name=server.name,
                                          radio_name=name), server=server)
    try:
        exists = await service.playlist_exists(target)
    except Exception as ex:
        log.exception("set_radio_playlist: the playlist could not be read")
        return await _audited(ctx, _radio(False, f"The playlist could not be read: {ex}",
                                          server_name=server.name, radio_name=name), server=server)
    if not exists:
        return await _audited(ctx, _radio(False, f"Playlist '{target}' not found.",
                                          server_name=server.name, radio_name=name), server=server)
    try:
        reason = await service.assign_playlist(server, name, target)
    except (TimeoutError, asyncio.TimeoutError):
        return await _audited(ctx, _radio(False, f"Server '{server.name}' did not answer in time; "
                                                 f"the radio was not reassigned.",
                                          server_name=server.name, radio_name=name), server=server)
    except Exception as ex:
        log.exception("set_radio_playlist: the assignment raised")
        return await _audited(ctx, _radio(False, f"Radio '{name}' could not be reassigned: {ex}",
                                          server_name=server.name, radio_name=name), server=server)
    if reason:
        return await _audited(ctx, _radio(False, _radio_refusal(reason, server.name, name, target),
                                          server_name=server.name, radio_name=name), server=server)
    radios = await _radio_state(service, server)
    return await _audited(ctx, _radio(True, f"Radio '{name}' on '{server.name}' now plays playlist "
                                            f"'{target}'.", server_name=server.name, radio_name=name,
                                      radios=radios), server=server)


@action
async def control_radio(ctx: Any, *, server_name: str, radio_name: str, control: str,
                        mode: str = "") -> MusicRadioResult:
    """Run ONE transport command on a radio: play, stop, skip, or set the mode.

    The control's own WORD is the parameter (`CONTROLS`), so one route serves the transport strip and
    a crafted body cannot move a button into another operation. ``mode`` is ``repeat`` or ``shuffle``
    and is read only when the control is ``mode``.
    """
    name = _text(radio_name)
    verb = _text(control).lower()
    if not name:
        return await _audited(ctx, _radio(False, "A radio name is required.",
                                          server_name=_text(server_name)))
    if verb not in CONTROLS:
        return await _audited(ctx, _radio(False, f"'{control}' is not a radio control: one of "
                                                 f"{', '.join(CONTROLS)} is.",
                                          server_name=_text(server_name), radio_name=name))
    server, refusal = await _server(ctx, _text(server_name))
    if server is None:
        return await _audited(ctx, _radio(False, refusal, server_name=_text(server_name),
                                          radio_name=name))
    service = _service()
    if service is None:
        return await _audited(ctx, _radio(False, SERVICE_MISSING, server_name=server.name,
                                          radio_name=name), server=server)
    chosen = _text(mode).lower()
    if verb == "mode" and chosen not in MODES:
        return await _audited(ctx, _radio(False, f"A radio mode is one of {', '.join(MODES)}.",
                                          server_name=server.name, radio_name=name), server=server)
    try:
        if verb == "play":
            reason = await service.start_radio(server, name)
        elif verb == "stop":
            reason = await service.stop_radio(server, name)
        elif verb == "skip":
            reason = await service.skip_radio(server, name)
        else:
            from services.music.radios import Mode

            reason = await service.set_mode(server, name, Mode[chosen.upper()])
    except (TimeoutError, asyncio.TimeoutError):
        return await _audited(ctx, _radio(False, f"Server '{server.name}' did not answer in time; "
                                                 f"the radio was not changed.",
                                          server_name=server.name, radio_name=name), server=server)
    except Exception as ex:
        log.exception("control_radio: the radio could not be driven")
        return await _audited(ctx, _radio(False, f"Radio '{name}' could not be changed: {ex}",
                                          server_name=server.name, radio_name=name), server=server)
    if reason:
        return await _audited(ctx, _radio(False, _radio_refusal(reason, server.name, name),
                                          server_name=server.name, radio_name=name), server=server)
    message = {
        "play": f"Radio '{name}' on '{server.name}' is playing.",
        "stop": f"Radio '{name}' on '{server.name}' stopped.",
        "skip": f"Radio '{name}' on '{server.name}' skipped the track.",
        "mode": f"Radio '{name}' on '{server.name}' is now in {chosen} mode.",
    }[verb]
    radios = await _radio_state(service, server)
    return await _audited(ctx, _radio(True, message, server_name=server.name, radio_name=name,
                                      radios=radios), server=server)


@action
async def set_radio_config(ctx: Any, *, server_name: str, radio_name: str, frequency: Any = "",
                           modulation: Any = "", coalition: Any = "", volume: Any = "",
                           display_name: Any = "") -> MusicRadioResult:
    """Change one radio's settings and apply them to the running player.

    Only the values the caller STATES are written: an empty field leaves the operator's value alone,
    which is what makes a dialog that posts one changed box safe. Each value is validated here
    against the one table of accepted values (:data:`MODULATIONS`, :data:`COALITIONS`, the frequency
    and volume ranges, the display name's bounds) before the file is touched.

    The write lands in ``config/services/music.yaml`` through the config primitive, so the file keeps
    its comments and its key order, and the radio is restarted so the SRS player runs on the new
    settings rather than finishing the queue on the old ones.
    """
    name = _text(radio_name)
    if not name:
        return await _audited(ctx, _radio(False, "A radio name is required.",
                                          server_name=_text(server_name)))
    changes: dict = {}
    stated = (
        ("frequency", frequency, _frequency),
        ("modulation", modulation, _modulation),
        ("coalition", coalition, _coalition),
        ("volume", volume, _volume),
        ("display_name", display_name, _display_name),
    )
    for key, raw, validator in stated:
        if not _text(raw):
            continue
        value = validator(raw)
        if value is None:
            return await _audited(ctx, _radio(False, _invalid_setting(key),
                                              server_name=_text(server_name), radio_name=name))
        changes[key] = value
    if not changes:
        return await _audited(ctx, _radio(False, "No radio setting was stated.",
                                          server_name=_text(server_name), radio_name=name))
    server, refusal = await _server(ctx, _text(server_name))
    if server is None:
        return await _audited(ctx, _radio(False, refusal, server_name=_text(server_name),
                                          radio_name=name))
    service = _service()
    if service is None:
        return await _audited(ctx, _radio(False, SERVICE_MISSING, server_name=server.name,
                                          radio_name=name), server=server)
    try:
        reason = await service.set_radio_config(server, name, changes)
    except (TimeoutError, asyncio.TimeoutError):
        return await _audited(ctx, _radio(False, f"Server '{server.name}' did not answer in time; "
                                                 f"the radio was not changed.",
                                          server_name=server.name, radio_name=name), server=server)
    except Exception as ex:
        log.exception("set_radio_config: the configuration could not be written")
        return await _audited(ctx, _radio(False, f"The settings of radio '{name}' could not be "
                                                 f"written: {ex}", server_name=server.name,
                                          radio_name=name), server=server)
    if reason:
        return await _audited(ctx, _radio(False, _radio_refusal(reason, server.name, name),
                                          server_name=server.name, radio_name=name), server=server)
    radios = await _radio_state(service, server)
    return await _audited(ctx, _radio(True, f"Radio '{name}' on '{server.name}' was updated: "
                                            f"{', '.join(sorted(changes))}.", server_name=server.name,
                                      radio_name=name, radios=radios, changed=sorted(changes)),
                          server=server)


def _invalid_setting(key: str) -> str:
    """The sentence a stated setting is refused with when its value is outside the accepted set."""
    if key == "frequency":
        return (f"A frequency is a positive number below {int(FREQUENCY_MAX)}, as the radio states "
                f"it (e.g. 30.0).")
    if key == "modulation":
        return f"A modulation is one of {', '.join(MODULATIONS)}."
    if key == "coalition":
        return f"A coalition is one of {', '.join(COALITIONS)} (1 = red, 2 = blue)."
    if key == "volume":
        return f"A volume is a number from 0 to {int(VOLUME_MAX)}."
    return (f"A display name is {DISPLAY_NAME_MIN} to {DISPLAY_NAME_MAX} characters long.")
