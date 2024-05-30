import asyncio
import psycopg

from contextlib import suppress
from typing import Callable, Optional

from core.data.impl.nodeimpl import NodeImpl


class ConnectionManager:
    def __init__(self, parent):
        self.parent = parent
        self.conn: Optional[psycopg.Connection] = None

    async def __aenter__(self):
        self.conn = await self.parent.get_connection()
        return self.conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None and issubclass(exc_type, psycopg.DatabaseError):
            await self.parent.close_connection(self.conn)


class PubSub:

    def __init__(self, node: NodeImpl, name: str, url: str):
        self.node = node
        self.name = name
        self.log = node.log
        self.url = url
        self._stop_event = asyncio.Event()
        self.pub_conn: Optional[psycopg.AsyncConnection] = None

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
                async with ConnectionManager(self) as conn:
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
            await asyncio.sleep(1)

    async def get_connection(self):
        conn = None

        max_attempts = self.node.config.get("database", self.node.locals.get('database')).get('max_retries', 10)
        for attempt in range(max_attempts):
            try:
                conn = await psycopg.AsyncConnection.connect(conninfo=self.url, autocommit=True)
                await conn.execute("SELECT 1")
                break
            except OperationalError:
                if attempt == max_attempts:
                    raise
                self.log.warning("- Database not available, trying again in 5s ...")
                await asyncio.sleep(5)

        return conn

    async def close_connection(self, conn: psycopg.AsyncConnection):
        with suppress(psycopg.DatabaseError):
            await conn.close()

    async def publish(self, data: dict) -> None:
        try:
            if self.pub_conn is None:
                self.pub_conn = await self.get_connection()
            await self.pub_conn.execute(f"""
                    INSERT INTO {self.name} (guild_id, node, data) 
                    VALUES (%(guild_id)s, %(node)s, %(data)s)
                """, data)
        except psycopg.DatabaseError:
            await self.close_connection(self.pub_conn)
            self.pub_conn = None
            raise

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
        if self.pub_conn:
            await self.pub_conn.execute(f"NOTIFY {self.name}")
            await self.close_connection(self.pub_conn)
