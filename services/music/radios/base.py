import asyncio
import os

from abc import ABC
from contextlib import suppress, closing
from core import Server, ServiceRegistry
from discord.ext import tasks
from enum import Enum
from random import randrange
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..service import MusicService

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

__all__ = [
    "Mode",
    "Radio",
    "RadioInitError"
]


class Mode(Enum):
    REPEAT = 1
    SHUFFLE = 2


class Radio(ABC):

    def __init__(self, name: str, server: Server):
        self.name = name
        self.service: MusicService = ServiceRegistry.get("Music")
        self.log = self.service.log
        self.pool = self.service.pool
        self.server = server
        self._current = None
        self._mode = Mode(int(self.config['mode']))
        self.songs: list[str] = []
        self._playlist = None
        self.playlist = self._get_active_playlist()
        self.idx = 0 if (self._mode == Mode.REPEAT or not len(self.songs)) else randrange(len(self.songs))

    def _get_active_playlist(self) -> Optional[str]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT playlist_name FROM music_radios WHERE server_name = %s AND radio_name = %s',
                               (self.server.name, self.name))
                return cursor.fetchone()[0] if cursor.rowcount > 0 else None

    def _read_playlist(self) -> list[str]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                return [
                    x[0] for x in cursor.execute('SELECT song_file FROM music_playlists WHERE name = %s',
                                                 (self._playlist,)).fetchall()
                ]

    @property
    def playlist(self) -> str:
        return self._playlist

    @playlist.setter
    def playlist(self, playlist: str) -> None:
        if playlist:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute("""
                        INSERT INTO music_radios (server_name, radio_name, playlist_name) 
                        VALUES (%s, %s, %s) 
                        ON CONFLICT (server_name, radio_name) DO UPDATE 
                        SET playlist_name = excluded.playlist_name
                    """, (self.server.name, self.name, playlist))
                self._playlist = playlist
                self.songs = self._read_playlist()

    @property
    def config(self) -> dict:
        return self.service.get_config(self.server, self.name)

    @config.setter
    def config(self, config: dict) -> None:
        configs = self.service.locals
        if not configs[self.server.instance.name]['radios'].get(self.name):
            configs[self.server.instance.name]['radios'][self.name] = config
        else:
            configs[self.server.instance.name]['radios'][self.name] |= config
        with open(os.path.join('config', 'services', 'music.yaml'), 'w', encoding='utf-8') as outfile:
            yaml.dump(configs, outfile)

    def is_running(self) -> bool:
        return self.queue_worker.is_running()

    async def stop(self) -> None:
        if self.queue_worker.is_running():
            self.queue_worker.cancel()
            while self.queue_worker.is_running():
                await asyncio.sleep(0.5)

    async def start(self) -> None:
        if not self.queue_worker.is_running():
            self.queue_worker.start()

    async def play(self, file: str) -> None:
        ...

    async def skip(self) -> None:
        return

    async def pause(self) -> None:
        return

    @property
    def current(self) -> str:
        return self._current or ""

    @current.setter
    def current(self, current) -> None:
        self._current = current

    @property
    def mode(self) -> Mode:
        return self._mode

    @mode.setter
    def mode(self, mode: Mode):
        self._mode = mode
        self.config['mode'] = mode.value

    @tasks.loop(reconnect=True)
    async def queue_worker(self):
        while not self.queue_worker.is_being_cancelled():
            with suppress(Exception):
                await self.play(os.path.join(await self.service.get_music_dir(), self.songs[self.idx]))
            self._current = None
            if self._mode == Mode.SHUFFLE:
                self.idx = randrange(len(self.songs)) if self.songs else 0
            elif self._mode == Mode.REPEAT:
                self.idx += 1
                if self.idx == len(self.songs):
                    self.idx = 0
            await asyncio.sleep(1)


class RadioInitError(Exception):
    pass
