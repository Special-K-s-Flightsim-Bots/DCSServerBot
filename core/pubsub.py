import asyncio
import psycopg

from contextlib import suppress
from typing import Callable

from core.data.impl.nodeimpl import NodeImpl


class PubSub:

    def __init__(self, node: NodeImpl, name: str, url: str, handler: Callable):
        self.node = node
        self.name = name
        self.log = node.log
        self.url = url
        self.handler = handler
        self.queue = asyncio.Queue()
        self.lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._worker = asyncio.create_task(self._process_write())

    async def _process_write(self):
        await asyncio.sleep(1)  # Ensure the rest of __init__ has finished
        while not self._stop_event.is_set():
            with suppress(psycopg.OperationalError):
                async with await psycopg.AsyncConnection.connect(self.url, autocommit=True) as conn:
                    while not self._stop_event.is_set():
                        message = await self.queue.get()
                        if not message:
                            return
                        try:
                            await conn.execute(f"""
                                INSERT INTO {self.name} (guild_id, node, data) 
                                VALUES (%(guild_id)s, %(node)s, %(data)s)
                            """, message)
                        finally:
                            # Notify the queue that the message has been processed.
                            self.queue.task_done()

    async def _process_read(self, cursor: psycopg.AsyncCursor):
        async with self.lock:
            ids_to_delete = []
            await cursor.execute(f"""
                SELECT id, data 
                FROM {self.name} 
                WHERE guild_id = %(guild_id)s AND node = %(node)s 
                ORDER BY id
            """, {
                'guild_id': self.node.guild_id,
                'node': "Master" if self.node.master else self.node.name
            })
            async for row in cursor:
                try:
                    await self.handler(row[1])
                except Exception as ex:
                    self.log.exception("Could not execute remote call!", exc_info=True)
                finally:
                    ids_to_delete.append(row[0])
            if ids_to_delete:
                await cursor.execute(f"DELETE FROM {self.name} WHERE id = ANY(%s::int[])",
                                     (ids_to_delete,))

    async def subscribe(self):
        while not self._stop_event.is_set():
            with suppress(psycopg.OperationalError):
                async with await psycopg.AsyncConnection.connect(self.url, autocommit=True) as conn:
                    async with conn.cursor() as cursor:
                        # preprocess all rows that might be there
                        await cursor.execute(f"LISTEN {self.name}")
                        await self._process_read(cursor)
                        gen = conn.notifies()
                        async for n in gen:
                            if self._stop_event.is_set():
                                self.log.debug(f'- {self.name.title()} stopped.')
                                await gen.aclose()
                                return
                            node = n.payload
                            if node == self.node.name or (self.node.master and node == 'Master'):
                                # noinspection PyAsyncCall
                                asyncio.create_task(self._process_read(cursor))
            await asyncio.sleep(1)

    async def publish(self, data: dict) -> None:
        """Add a message to the queue."""
        self.queue.put_nowait(data)

    async def clear(self):
        async with self.node.apool.connection() as conn:
            try:
                await conn.set_autocommit(True)
                if self.node.master:
                    await conn.execute(f"""
                        DELETE FROM {self.name} 
                        WHERE time < ((now() AT TIME ZONE 'utc') - interval '300 seconds')
                    """)
                    await conn.execute(f"UPDATE {self.name} SET node = 'Master' WHERE node = %s", (self.node.name, ))
                else:
                    await conn.execute(f"DELETE FROM {self.name} WHERE guild_id = %s AND node = %s",
                                       (self.node.guild_id, self.node.name))
            finally:
                await conn.set_autocommit(False)

    async def close(self):
        self._stop_event.set()
        self.queue.put_nowait(None)
        await self._worker
