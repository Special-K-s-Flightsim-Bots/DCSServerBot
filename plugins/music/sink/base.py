import asyncio
import discord
import os
from abc import ABC
from contextlib import suppress, closing
from core import Server
from discord.ext import tasks
from discord.ui import Modal
from enum import Enum
from random import randrange
from services import DCSServerBot
from typing import Optional

__all__ = [
    "Mode",
    "Sink",
    "SinkInitError"
]


class Mode(Enum):
    REPEAT = 1
    SHUFFLE = 2


class DBConfig(dict):
    def __init__(self, bot: DCSServerBot, server: Server, sink_type: str, *, default: dict):
        super().__init__()
        self.bot = bot
        self.log = bot.log
        self.pool = bot.pool
        self.server = server
        self.sink_type = sink_type
        self.read()
        if len(self) == 0:
            self._load(default)

    def read(self):
        data = dict()
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                for row in cursor.execute(
                        'SELECT param, value FROM music_config WHERE sink_type = %s AND server_name = %s',
                        (self.sink_type, self.server.name)).fetchall():
                    data[row[0]] = row[1]
        if data:
            self.clear()
            self.update(data)

    def write(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    cursor.execute('DELETE FROM music_config WHERE sink_type = %s AND server_name = %s',
                                   (self.sink_type, self.server.name))
                    for name, value in self.items():
                        cursor.execute('INSERT INTO music_config (sink_type, server_name, param, value) '
                                       'VALUES (%s, %s, %s, %s)', (self.sink_type, self.server.name, name, value))

    def _load(self, new: dict):
        for k, v in new.items():
            self[k] = v

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if len(self):
            self.write()


class Sink(ABC):

    def __init__(self, bot: DCSServerBot, server: Server, config: dict, music_dir: str):
        self.bot = bot
        self.log = bot.log
        self.pool = bot.pool
        self.server = server
        self._config = DBConfig(bot, server, self.__class__.__name__, default=config)
        self.music_dir = music_dir
        self._current = None
        self._mode = Mode(int(self.config['mode']))
        self.songs: list[str] = []
        self._playlist = None
        self.playlist = self._get_active_playlist()
        self.idx = 0 if (self._mode == Mode.REPEAT or not len(self.songs)) else randrange(len(self.songs))

    def _get_active_playlist(self) -> Optional[str]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT playlist_name FROM music_servers WHERE server_name = %s',
                               (self.server.name,))
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
                        INSERT INTO music_servers (server_name, playlist_name) 
                        VALUES (%s, %s) ON CONFLICT (server_name) DO UPDATE 
                        SET playlist_name = excluded.playlist_name
                    """, (self.server.name, playlist))
                self._playlist = playlist
                self.songs = self._read_playlist()

    @property
    def config(self) -> dict:
        return self._config

    @config.setter
    def config(self, config: dict) -> None:
        self._config = config

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

    def render(self) -> discord.Embed:
        ...

    def edit(self) -> Modal:
        ...

    @tasks.loop(reconnect=True)
    async def queue_worker(self):
        while not self.queue_worker.is_being_cancelled():
            with suppress(Exception):
                await self.play(os.path.join(self.music_dir, self.songs[self.idx]))
            self._current = None
            if self._mode == Mode.SHUFFLE:
                self.idx = randrange(len(self.songs)) if self.songs else 0
            elif self._mode == Mode.REPEAT:
                self.idx += 1
                if self.idx == len(self.songs):
                    self.idx = 0
            await asyncio.sleep(1)


class SinkInitError(Exception):
    pass
