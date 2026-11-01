from __future__ import annotations

import asyncio
import os
import time

from abc import ABC
from core import Server, utils
from core.utils.configwrite import patch_config_file
from discord.ext import tasks
from enum import Enum
from random import shuffle
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.music import MusicService

__all__ = [
    "LOADING_CAP_SECONDS",
    "Mode",
    "Radio",
    "RadioInitError",
    "write_radio_config",
]

#: How long a :meth:`Radio.begin_loading` stamp counts as a load in flight. The cap ends the state
#: whatever happened, so a launch that never yields a track cannot leave a card loading forever.
LOADING_CAP_SECONDS = 20.0


def _now() -> float:
    """The clock a loading stamp is measured on — one indirection, so a test drives the cap."""
    return time.monotonic()


class Mode(Enum):
    REPEAT = 1
    SHUFFLE = 2


def _resolved_track(root: str, relative) -> tuple[str, str] | None:
    """``(relative, absolute)`` for one track row of *root*, or ``None``.

    The relative part is the lexical form, ``/`` separated; the absolute one has its symbolic links
    resolved and must sit inside *root* (``music_file``). A row whose link points outside, whose
    value names a location, or which is empty resolves to ``None``.
    """
    from services.music.service import music_file, music_path

    normalized = music_path(relative)
    if normalized is None:
        return None
    absolute = music_file(root, normalized)
    if absolute is None:
        return None
    return normalized, absolute


def write_radio_config(service: MusicService, server: Server, radio_name: str,
                       changes: dict) -> None:
    """Merge *changes* into one radio's block of the node's ``config/services/music.yaml``.

    The write goes through the config primitive, so the file keeps the operator's comments and key
    order. The block a radio reads its values from is the INSTANCE block — the instance's own name at
    the top level of the document, or below the document's own node key when it carries one — and the
    service's merged cache is dropped afterwards, which is what makes the new values the ones the
    radio reads next.

    A key a radio's block does not carry yet is written, an existing one is replaced: the file is the
    source of truth for every value a radio has.
    """
    path = os.path.join(server.node.config_dir, 'services', 'music.yaml')
    instance = server.instance.name

    def mutate(document) -> None:
        parent = document.get(server.node.name)
        if not isinstance(parent, dict):
            parent = document
        block = parent.get(instance)
        if not isinstance(block, dict):
            block = parent.setdefault(instance, {})
        radios = block.get('radios')
        if not isinstance(radios, dict):
            radios = block.setdefault('radios', {})
        current = radios.get(radio_name)
        if isinstance(current, dict):
            current.update(changes)
        else:
            radios[radio_name] = dict(changes)

    patch_config_file(path, mutate)
    service.reload()


class Radio(ABC):

    #: the audio subprocess while this radio plays through one, ``None`` while it is silent.
    #: A radio type that never plays through a process simply leaves it alone.
    process = None

    #: the moment a load began, in the clock of :func:`_now` — ``None`` when nothing is loading.
    _loading_since: float | None = None

    def __init__(self, service: MusicService, name: str, server: Server):
        self.name = name
        self.service = service
        self.log = self.service.log
        self.pool = self.service.pool
        self.apool = self.service.apool
        self.server = server
        self._current = None
        self._current_file = ""
        self._mode = Mode(int(self.config['mode']))
        self.songs: list[str] = []
        self._playlist = None
        # TODO: async
        self.playlist = self._get_active_playlist()
        self.idx = 0
        self.reset()

    def _get_active_playlist(self) -> str | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT playlist_name FROM music_radios WHERE server_name = %s AND radio_name = %s',
                               (self.server.name, self.name))
                return cursor.fetchone()[0] if cursor.rowcount > 0 else None

    def _read_playlist(self) -> list[str]:
        """The playlist's files IN PLAYBACK ORDER — ``song_id`` is the order, every time.

        A reordered playlist is only a reordered radio because this read sorts on the row id; an
        unordered read hands the files back in whatever order the server answers and the reorder
        control changes nothing audible.
        """
        playlist: list[str] = list()
        music_dir: str = self.service.music_dir
        with self.pool.connection() as conn:
            for row in conn.execute('SELECT song_file FROM music_playlists WHERE name = %s '
                                    'ORDER BY song_id', (self._playlist,)):
                tracked = _resolved_track(music_dir, row[0])
                if tracked is not None and os.path.isfile(tracked[1]):
                    playlist.append(tracked[0])
                else:
                    self.log.warning(f"Can't find music file {row[0]}, deleting from playlist {self._playlist}.")
                    conn.execute('DELETE FROM music_playlists WHERE name = %s AND song_file = %s',
                                 (self._playlist, row[0]))
        return playlist

    @property
    def playlist(self) -> str:
        return self._playlist

    @playlist.setter
    def playlist(self, playlist: str) -> None:
        if not playlist:
            return
        if self._playlist != playlist:
            with self.pool.connection() as conn:
                conn.execute("""
                             INSERT INTO music_radios (server_name, radio_name, playlist_name)
                             VALUES (%s, %s, %s)
                             ON CONFLICT (server_name, radio_name) DO UPDATE
                                 SET playlist_name = excluded.playlist_name
                             """, (self.server.name, self.name, playlist))
            self._playlist = playlist

        self.songs = self._read_playlist()
        self.reset()

    def _reset_index(self) -> int:
        if self.songs and self._mode == Mode.SHUFFLE:
            shuffle(self.songs)
        return 0

    @property
    def config(self) -> dict:
        return self.service.get_config(self.server, self.name)

    @config.setter
    def config(self, config: dict) -> None:
        write_radio_config(self.service, self.server, self.name, config)

    def is_running(self) -> bool:
        """Whether the radio's queue is turning — the state ``start``/``stop`` act on."""
        return self.queue_worker.is_running()

    def is_playing(self) -> bool:
        """Whether audio is coming out right now, whatever started it.

        A track can play WITHOUT the queue: ``play(file)`` is reachable on its own (a direct
        single-song play), and that path never starts the worker. The display must say on air for
        both, or a radio that is audibly playing shows Play enabled and Stop disabled — while the
        queue state stays the right answer for control flow (``start`` must not skip a worker that
        only a direct play exists).
        """
        process = getattr(self, "process", None)
        return bool(process is not None and process.is_running())

    def begin_loading(self) -> None:
        """Stamp a load in flight — set by the transport BEFORE it drives this radio."""
        self._loading_since = _now()

    def end_loading(self) -> None:
        """Clear the stamp: a track arrived, the radio stopped, or the cap has passed."""
        self._loading_since = None

    def is_loading(self) -> bool:
        """Whether a load is still in flight — ``False`` once :data:`LOADING_CAP_SECONDS` passed.

        The cap is evaluated on the read, so a stamp always ends even when nothing ever clears it.
        """
        since = self._loading_since
        if since is None:
            return False
        if _now() - since >= LOADING_CAP_SECONDS:
            self._loading_since = None
            return False
        return True

    async def stop(self) -> None:
        await utils.safe_cancel(self.queue_worker)
        self.end_loading()

    async def start(self) -> None:
        utils.safe_start(self.queue_worker)

    async def play(self, file: str) -> None:
        ...

    async def skip(self) -> None:
        return

    async def pause(self) -> None:
        return

    def reset(self) -> None:
        if self._mode == Mode.SHUFFLE and self.songs:
            shuffle(self.songs)
        self.idx = 0

    @property
    def current(self) -> str:
        return self._current or ""

    @current.setter
    def current(self, current) -> None:
        self._current = current

    @property
    def current_file(self) -> str:
        """The playing track as a music-directory-relative path, or ``""`` when nothing plays."""
        return self._current_file or ""

    @current_file.setter
    def current_file(self, current_file) -> None:
        self._current_file = current_file or ""

    @property
    def mode(self) -> Mode:
        return self._mode

    @mode.setter
    def mode(self, mode: Mode):
        self._mode = mode
        self.config['mode'] = mode.value

    @tasks.loop()
    async def queue_worker(self):
        music_dir: str = self.service.music_dir
        while not self.queue_worker.is_being_cancelled():
            if not self.songs:
                self._current = None
                self.current_file = ""
                await asyncio.sleep(1)
                continue

            if self.idx >= len(self.songs):
                self.idx = 0

            tracked = _resolved_track(music_dir, self.songs[self.idx])
            filename = tracked[1] if tracked is not None else None
            self.current_file = tracked[0] if tracked is not None else ""
            if filename and os.path.isfile(filename):
                await self.play(filename)
            else:
                self.log.warning(f"Can't play {self.songs[self.idx]} - file does not exist.")

            self._current = None
            self.current_file = ""
            if self._mode == Mode.SHUFFLE:
                last_song = self.songs[self.idx]
                self.idx += 1
                if self.idx >= len(self.songs):
                    while True:
                        shuffle(self.songs)
                        # Prevent the first song of the new shuffle from being
                        # the same as the last song of the previous shuffle.
                        if len(self.songs) <= 1 or self.songs[0] != last_song:
                            break
                    self.idx = 0
            elif self._mode == Mode.REPEAT:
                self.idx += 1
                if self.idx >= len(self.songs):
                    self.idx = 0
            await asyncio.sleep(1)


class RadioInitError(Exception):
    pass
