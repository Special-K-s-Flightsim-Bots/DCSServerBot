import asyncio
import zlib

from contextlib import suppress
from psycopg import sql, Connection, OperationalError, AsyncConnection, InternalError
from typing import Callable

from core.data.impl.nodeimpl import NodeImpl


class PubSub:

    def __init__(self, node: NodeImpl, name: str, url: str, handler: Callable):
        self.node = node
        self.name = name
        self.log = node.log
        self.url = url
        self.handler = handler
        self.create_table()
        self.read_queue = asyncio.Queue()
        self.write_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self.read_worker = asyncio.create_task(self._process_read())
        self.write_worker = asyncio.create_task(self._process_write())

    def create_table(self):
        lock_key = zlib.crc32(f"PubSubDDL:{self.name}".encode("utf-8"))

        with Connection.connect(self.url, autocommit=True) as conn:
            try:
                conn.execute("SELECT pg_advisory_lock(%s)", (lock_key,))

                query = sql.SQL("""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id SERIAL PRIMARY KEY, 
                        guild_id BIGINT NOT NULL, 
                        node TEXT NOT NULL, 
                        time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'), 
                        data JSONB
                    )
                """).format(table=sql.Identifier(self.name))
                conn.execute(query)
                query = sql.SQL("""
                    CREATE OR REPLACE FUNCTION {func}()
                    RETURNS trigger
                        AS $$
                    BEGIN
                        PERFORM pg_notify({name}, NEW.node);
                        RETURN NEW;
                    END;
                    $$ LANGUAGE plpgsql;
                """).format(func=sql.Identifier(self.name + '_notify'), name=sql.Literal(self.name))
                conn.execute(query)
                query = sql.SQL("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_trigger
                            WHERE tgname = {trigger_name}
                            AND tgrelid = {name}::regclass
                        ) THEN
                            CREATE TRIGGER {trigger}
                            AFTER INSERT OR UPDATE ON {table}
                            FOR EACH ROW
                            EXECUTE PROCEDURE {func}();
                        END IF;
                    END;
                    $$;
                """).format(table=sql.Identifier(self.name), trigger=sql.Identifier(self.name + '_trigger'),
                            func=sql.Identifier(self.name + '_notify'), name=sql.Literal(self.name),
                            trigger_name=sql.Literal(self.name + '_trigger'))
                conn.execute(query)

            except InternalError as ex:
                self.log.exception(ex)
                raise
            finally:
                with suppress(Exception):
                    conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))

    async def _process_write(self):
        await asyncio.sleep(1)  # Ensure the rest of __init__ has finished
        while not self._stop_event.is_set():
            with suppress(OperationalError):
                async with await AsyncConnection.connect(self.url, autocommit=True) as conn:
                    while not self._stop_event.is_set():
                        message = await self.write_queue.get()
                        if not message:
                            return
                        try:
                            query = sql.SQL("""
                                INSERT INTO {table} (guild_id, node, data) 
                                VALUES (%(guild_id)s, %(node)s, %(data)s)
                            """).format(table=sql.Identifier(self.name))
                            await conn.execute(query, message)
                        finally:
                            # Notify the queue that the message has been processed.
                            self.write_queue.task_done()

    async def _process_read(self):
        async def do_read():
            ids_to_delete = []
            query = sql.SQL("""
                SELECT id, data FROM {table} 
                WHERE guild_id = %(guild_id)s AND node = %(node)s 
                ORDER BY id
            """).format(table=sql.Identifier(self.name))
            cursor = await conn.execute(query, {
                'guild_id': self.node.guild_id,
                'node': "Master" if self.node.master else self.node.name
            })
            async for row in cursor:
                try:
                    asyncio.create_task(self.handler(row[1]))
                finally:
                    ids_to_delete.append(row[0])
            if ids_to_delete:
                query = sql.SQL("DELETE FROM {table} WHERE id = ANY(%s::int[])").format(table=sql.Identifier(self.name))
                await conn.execute(query, (ids_to_delete,))

        while not self._stop_event.is_set():
            # wait one second to clean up any issue
            await asyncio.sleep(1)
            with suppress(OperationalError):
                async with await AsyncConnection.connect(self.url, autocommit=True) as conn:
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
            with suppress(OperationalError):
                async with await AsyncConnection.connect(self.url, autocommit=True) as conn:
                    async with conn.cursor() as cursor:
                        # preprocess all rows that might be there
                        await cursor.execute(sql.SQL("LISTEN {table}").format(table=sql.Identifier(self.name)))
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
        async with await AsyncConnection.connect(self.url, autocommit=True) as conn:
            if self.node.master:
                query = sql.SQL("""
                    DELETE FROM {table} 
                    WHERE time < ((now() AT TIME ZONE 'utc') - interval '300 seconds')
                """).format(table=sql.Identifier(self.name))
                await conn.execute(query)
                query = sql.SQL("""
                    UPDATE {table} SET node = 'Master' WHERE node = %s
                """).format(table=sql.Identifier(self.name))
                await conn.execute(query, (self.node.name, ))
            else:
                query = sql.SQL("""
                    DELETE FROM {table} WHERE guild_id = %s AND node = %s
                """).format(table=sql.Identifier(self.name))
                await conn.execute(query,(self.node.guild_id, self.node.name))

    async def close(self):
        self._stop_event.set()
        self.write_queue.put_nowait(None)
        await self.write_worker
        self.read_queue.put_nowait(None)
        await self.read_worker