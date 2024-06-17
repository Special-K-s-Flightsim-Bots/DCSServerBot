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
        self.read_queue = asyncio.Queue()
        self.write_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self.read_worker = asyncio.create_task(self._process_read())
        self.write_worker = asyncio.create_task(self._process_write())

    async def _process_write(self):
        await asyncio.sleep(1)  # Ensure the rest of __init__ has finished
        while not self._stop_event.is_set():
            with suppress(psycopg.OperationalError):
                async with await psycopg.AsyncConnection.connect(self.url, autocommit=True) as conn:
                    while not self._stop_event.is_set():
                        message = await self.write_queue.get()
                        if not message:
                            return
                        try:
                            await conn.execute(f"""
                                INSERT INTO {self.name} (guild_id, node, data) 
                                VALUES (%(guild_id)s, %(node)s, %(data)s)
                            """, message)
                        finally:
                            # Notify the queue that the message has been processed.
                            self.write_queue.task_done()

    async def _process_read(self):
        async def do_read():
            ids_to_delete = []
            cursor = await conn.execute(f"""
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
                    # noinspection PyAsyncCall
                    asyncio.create_task(self.handler(row[1]))
                except Exception as ex:
                    self.log.exception(ex)
                finally:
                    ids_to_delete.append(row[0])
            if ids_to_delete:
                await conn.execute(f"DELETE FROM {self.name} WHERE id = ANY(%s::int[])", (ids_to_delete,))

        await asyncio.sleep(1)  # Ensure the rest of __init__ has finished
        while not self._stop_event.is_set():
            with suppress(psycopg.OperationalError):
                async with await psycopg.AsyncConnection.connect(self.url, autocommit=True) as conn:
                    while not self._stop_event.is_set():
                        try:
                            # we will read every 5s independent if there is data in the queue or not
                            if not await asyncio.wait_for(self.read_queue.get(), timeout=5.0):
                                return
                            try:
                                await do_read()
                            finally:
                                # Notify the queue that the message has been processed.
                                self.read_queue.task_done()
                        except (TimeoutError, asyncio.TimeoutError):
                            await do_read()

    async def subscribe(self):
        while not self._stop_event.is_set():
            with suppress(psycopg.OperationalError):
                async with await psycopg.AsyncConnection.connect(self.url, autocommit=True) as conn:
                    async with conn.cursor() as cursor:
                        # preprocess all rows that might be there
                        await cursor.execute(f"LISTEN {self.name}")
                        gen = conn.notifies()
                        async for n in gen:
                            if self._stop_event.is_set():
                                self.log.debug(f'- {self.name.title()} stopped.')
                                await gen.aclose()
                                return
                            node = n.payload
                            if node == self.node.name or (self.node.master and node == 'Master'):
                                self.read_queue.put_nowait(n.payload)
            await asyncio.sleep(1)

    async def publish(self, data: dict) -> None:
        """Add a message to the queue."""
        self.write_queue.put_nowait(data)

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
        self.write_queue.put_nowait(None)
        await self.write_worker
        self.read_queue.put_nowait(None)
        await self.read_worker
