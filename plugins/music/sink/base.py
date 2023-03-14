import asyncio
import discord
import psycopg2
from abc import ABC
from contextlib import suppress, closing
from core import DCSServerBot, Server
from discord.ext import tasks
from discord.ui import Modal
from enum import Enum
from queue import Queue
from random import choice
from typing import Optional, Any

__all__ = [
    "Mode",
    "Sink",
    "SinkInitError"
]


class Mode(Enum):
    ONCE = 1
    REPEAT = 2
    SHUFFLE = 3
    SHUFFLE_REPEAT = 4


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
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT param, value FROM music_config WHERE sink_type = %s AND server_name = %s',
                               (self.sink_type, self.server.name))
                for row in cursor.fetchall():
                    data[row[0]] = row[1]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        if data:
            self.clear()
            self.update(data)

    def write(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('DELETE FROM music_config WHERE sink_type = %s AND server_name = %s',
                               (self.sink_type, self.server.name))
                for name, value in self.items():
                    cursor.execute('INSERT INTO music_config (sink_type, server_name, param, value) '
                                   'VALUES (%s, %s, %s, %s)', (self.sink_type, self.server.name, name, value))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    def _load(self, new: dict):
        for k, v in new.items():
            self[k] = v

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if len(self):
            self.write()


class Sink(ABC):

    class DBBackedQueue:
        def __init__(self, bot: DCSServerBot, server: Server, sink_type: str):
            self.log = bot.log
            self.pool = bot.pool
            self.server = server
            self.sink_type = sink_type
            self._queue = Queue()
            # initialize the playlist if there is one stored in the database
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('SELECT song_file FROM music_playlists WHERE sink_type = %s and server_name = %s '
                                   'ORDER BY song_id', (self.sink_type, self.server.name))
                    for row in cursor.fetchall():
                        self._queue.put(row[0])
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)

        def empty(self) -> bool:
            return self._queue.empty()

        def qsize(self) -> int:
            return self._queue.qsize()

        @property
        def queue(self) -> Any:
            return self._queue.queue

        def put(self, item, block: bool = True, timeout: Optional[float] = None):
            ret = self._queue.put(item, block, timeout)
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute("INSERT INTO music_playlists (sink_type, server_name, song_id, song_file) "
                                   "VALUES (%s, %s, nextval('music_song_id_seq'), %s)",
                                   (self.sink_type, self.server.name, item))
                conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)
            return ret

        def get(self, block: bool = True, timeout: Optional[float] = None):
            item = self._queue.get(block, timeout)
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('DELETE FROM music_playlists WHERE sink_type = %s AND server_name = %s '
                                   'AND song_file = %s', (self.sink_type, self.server.name, item))
                conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)
            return item

        def clear(self):
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('DELETE FROM music_playlists WHERE sink_type = %s AND server_name = %s ',
                                   (self.sink_type, self.server.name))
                conn.commit()
                self.queue.clear()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)

    def __init__(self, bot: DCSServerBot, server: Server, config: dict):
        self.bot = bot
        self.log = bot.log
        self.pool = bot.pool
        self.server = server
        self._config = DBConfig(bot, server, self.__class__.__name__, default=config)
        self._current = None
        self._mode = Mode(int(self.config['mode']))
        self.queue = self.DBBackedQueue(bot, server, self.__class__.__name__)
        self.queue_worker.start()

    @property
    def config(self) -> dict:
        return self._config

    @config.setter
    def config(self, config: dict) -> None:
        self._config = config

    def can_play(self) -> bool:
        return True

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

    def add(self, file: str):
        self.queue.put(file)

    def clear(self):
        self.queue.clear()

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
            if self.can_play():
                if self._mode == Mode.ONCE:
                    while not self.queue.empty():
                        if self.queue_worker.is_being_cancelled():
                            break
                        file = self.queue.get()
                        with suppress(Exception):
                            await self.play(file)
                elif self._mode == Mode.REPEAT:
                    for i in range(0, self.queue.qsize()):
                        if self.queue_worker.is_being_cancelled():
                            break
                        file = self.queue.queue[i]
                        with suppress(Exception):
                            await self.play(file)
                elif self._mode in [Mode.SHUFFLE, Mode.SHUFFLE_REPEAT]:
                    for i in range(0, self.queue.qsize()):
                        if self.queue_worker.is_being_cancelled():
                            break
                        file = choice(self.queue.queue)
                        with suppress(Exception):
                            await self.play(file)
                        if self.mode == Mode.SHUFFLE:
                            del self.queue.queue[self.queue.queue.index(file)]
                self._current = None
            await asyncio.sleep(1)


class SinkInitError(Exception):
    pass
