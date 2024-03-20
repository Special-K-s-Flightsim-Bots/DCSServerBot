import asyncio
import psycopg

from contextlib import suppress
from typing import Callable

from core.data.impl.nodeimpl import NodeImpl


class PubSub:

    def __init__(self, node: NodeImpl, name: str, url: str):
        self.node = node
        self.name = name
        self.log = node.log
        self.url = url
        self._stop_event = asyncio.Event()

    async def _process(self, cursor: psycopg.AsyncCursor, handler: Callable):
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
                # noinspection PyAsyncCall
                asyncio.create_task(handler(row[1]))
            except Exception as ex:
                self.log.exception(ex)
            finally:
                ids_to_delete.append(row[0])
        if ids_to_delete:
            await cursor.execute(f"DELETE FROM {self.name} WHERE id = ANY(%s::int[])",
                                 (ids_to_delete,))

    async def subscribe(self, handler: Callable):
        while True:
            with suppress(psycopg.OperationalError):
                async with self.node.apool.connection() as conn:
                    try:
                        await conn.set_autocommit(True)
                        async with conn.cursor() as cursor:
                            # preprocess all rows that might be there
                            await cursor.execute(f"LISTEN {self.name}")
                            await self._process(cursor, handler)
                            gen = conn.notifies()
                            async for n in gen:
                                if self._stop_event.is_set():
                                    self.log.debug(f'- {self.name.title()} stopped.')
                                    await gen.aclose()
                                    return
                                node = n.payload
                                if node == self.node.name or (self.node.master and node == 'Master'):
                                    await self._process(cursor, handler)
                    finally:
                        await conn.set_autocommit(False)

    # TODO: dirty, needs to be changed when we use AsyncPG in general
    def publish(self, conn: psycopg.Connection, data: dict) -> None:
        conn.execute(f"""
                INSERT INTO {self.name} (guild_id, node, data) 
                VALUES (%(guild_id)s, %(node)s, %(data)s)
            """, data)

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
        async with self.node.apool.connection() as conn:
            try:
                await conn.set_autocommit(True)
                self._stop_event.set()
                await conn.execute(f"NOTIFY {self.name}")
            finally:
                await conn.set_autocommit(False)
