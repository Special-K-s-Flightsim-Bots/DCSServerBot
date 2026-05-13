import asyncio
import zlib

from contextlib import suppress
from psycopg import sql, Connection, OperationalError, AsyncConnection, InternalError
from typing import Callable

from core import FatalException
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
        self.read_worker.add_done_callback(self._handle_worker_done)
        self.write_worker = asyncio.create_task(self._process_write())
        self.write_worker.add_done_callback(self._handle_worker_done)

    def _handle_worker_done(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except FatalException as ex:
            self.log.critical(ex)
            asyncio.create_task(self.node.shutdown(-1))
        except Exception as ex:
            self.log.exception(ex)
            asyncio.create_task(self.node.shutdown(-1))

    def observe_task(self, task: asyncio.Task) -> asyncio.Task:
        task.add_done_callback(self._handle_worker_done)
        return task

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
        delay = 1
        max_delay = 30
        retries = 0
        max_retries = 10
        pending_message = None

        while not self._stop_event.is_set():
            try:
                async with await AsyncConnection.connect(self.url, autocommit=True) as conn:
                    delay = 1
                    retries = 0

                    while not self._stop_event.is_set():
                        if pending_message is not None:
                            message = pending_message
                            pending_message = None
                            from_queue = False
                        else:
                            message = await self.write_queue.get()
                            from_queue = True

                        if not message:
                            if from_queue:
                                self.write_queue.task_done()
                            return

                        try:
                            query = sql.SQL("""
                                INSERT INTO {table} (guild_id, node, data) 
                                VALUES (%(guild_id)s, %(node)s, %(data)s)
                            """).format(table=sql.Identifier(self.name))
                            await conn.execute(query, message)
                            if from_queue:
                                self.write_queue.task_done()
                            retries = 0
                        except OperationalError as ex:
                            retries += 1
                            pending_message = message

                            if retries > max_retries:
                                raise FatalException(
                                    f"Too many operational errors while writing to {self.name}: {ex}"
                                )

                            self.log.warning(
                                f"Error while writing to {self.name}: {ex}. "
                                f"Retry {retries}/{max_retries} in {delay}s ..."
                            )
                            break
                        except Exception as ex:
                            self.log.exception(ex)
                            if from_queue:
                                self.write_queue.task_done()

            except asyncio.CancelledError:
                raise
            except OperationalError as ex:
                retries += 1

                if retries > max_retries:
                    raise FatalException(
                        f"Could not connect to {self.name} after {max_retries} retries: {ex}"
                    )

                self.log.warning(
                    f"Could not connect to {self.name}: {ex}. "
                    f"Retry {retries}/{max_retries} in {delay}s ..."
                )

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                delay = min(delay * 2, max_delay)

    async def _sleep_before_retry(self, delay: int) -> bool:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            return False
        except asyncio.TimeoutError:
            return True

    def _raise_too_many_retries(self, operation: str, retries: int, max_retries: int, ex: Exception) -> None:
        if retries > max_retries:
            raise FatalException(
                f"Too many operational errors while {operation} {self.name} "
                f"after {max_retries} retries: {ex}"
            )

    async def _process_read(self):
        delay = 1
        max_delay = 30
        retries = 0
        max_retries = 10

        async def do_read(conn: AsyncConnection):
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

        await asyncio.sleep(1)

        while not self._stop_event.is_set():
            try:
                async with await AsyncConnection.connect(self.url, autocommit=True) as conn:
                    delay = 1
                    retries = 0

                    while not self._stop_event.is_set():
                        try:
                            try:
                                # Read when notified, but also poll every 5 seconds.
                                message = await asyncio.wait_for(self.read_queue.get(), timeout=5.0)
                                from_queue = True
                            except (TimeoutError, asyncio.TimeoutError):
                                message = None
                                from_queue = False

                            if from_queue and not message:
                                self.read_queue.task_done()
                                return

                            try:
                                await do_read(conn)
                                if from_queue:
                                    self.read_queue.task_done()
                            except OperationalError as ex:
                                retries += 1
                                self._raise_too_many_retries("reading from", retries, max_retries, ex)
                                self.log.warning(
                                    f"Error while reading from {self.name}: {ex}. "
                                    f"Retry {retries}/{max_retries} in {delay}s ..."
                                )
                                break

                        except asyncio.CancelledError:
                            raise
                        except Exception as ex:
                            self.log.exception(ex)
                            if 'from_queue' in locals() and from_queue:
                                self.read_queue.task_done()

            except asyncio.CancelledError:
                raise
            except OperationalError as ex:
                retries += 1
                self._raise_too_many_retries("connecting to", retries, max_retries, ex)
                self.log.warning(
                    f"Could not connect to {self.name} for reading: {ex}. "
                    f"Retry {retries}/{max_retries} in {delay}s ..."
                )

            if not await self._sleep_before_retry(delay):
                return
            delay = min(delay * 2, max_delay)

    async def subscribe(self):
        delay = 1
        max_delay = 30
        retries = 0
        max_retries = 10

        while not self._stop_event.is_set():
            try:
                async with await AsyncConnection.connect(self.url, autocommit=True) as conn:
                    delay = 1
                    retries = 0

                    async with conn.cursor() as cursor:
                        await cursor.execute(sql.SQL("LISTEN {table}").format(table=sql.Identifier(self.name)))
                        gen = conn.notifies()

                        try:
                            async for n in gen:
                                if self._stop_event.is_set():
                                    self.log.debug(f'- {self.name.title()} stopped.')
                                    return

                                node = n.payload
                                if node == self.node.name or (self.node.master and node == 'Master'):
                                    self.read_queue.put_nowait(n.payload)
                        finally:
                            with suppress(Exception):
                                await gen.aclose()

            except asyncio.CancelledError:
                raise
            except OperationalError as ex:
                retries += 1
                self._raise_too_many_retries("subscribing to", retries, max_retries, ex)
                self.log.warning(
                    f"Error while subscribing to {self.name}: {ex}. "
                    f"Retry {retries}/{max_retries} in {delay}s ..."
                )

            if not await self._sleep_before_retry(delay):
                return
            delay = min(delay * 2, max_delay)

    async def publish(self, data: dict) -> None:
        """Add a message to the queue."""
        self.write_queue.put_nowait(data)

    async def clear(self):
        delay = 1
        max_delay = 10
        max_retries = 3

        for attempt in range(max_retries + 1):
            try:
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
                        await conn.execute(query, (self.node.guild_id, self.node.name))
                    return

            except asyncio.CancelledError:
                raise
            except OperationalError as ex:
                if attempt >= max_retries:
                    raise FatalException(
                        f"Could not clear {self.name} after {max_retries} retries: {ex}"
                    )

                self.log.warning(
                    f"Error while clearing {self.name}: {ex}. "
                    f"Retry {attempt + 1}/{max_retries} in {delay}s ..."
                )

                if not await self._sleep_before_retry(delay):
                    return
                delay = min(delay * 2, max_delay)

    async def close(self):
        self._stop_event.set()

        self.write_queue.put_nowait(None)
        self.read_queue.put_nowait(None)

        try:
            results = await asyncio.wait_for(
                asyncio.gather(self.write_worker, self.read_worker, return_exceptions=True),
                timeout=30
            )
        except asyncio.TimeoutError:
            self.write_worker.cancel()
            self.read_worker.cancel()
            results = await asyncio.gather(self.write_worker, self.read_worker, return_exceptions=True)

        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
            elif isinstance(result, FatalException):
                self.log.critical(result)
            elif isinstance(result, Exception):
                self.log.exception(result)
