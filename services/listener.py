import asyncio
import json
import platform
import psycopg
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from core import Server, DataObjectFactory, utils, Status, ServerImpl
from core.services.base import Service
from core.services.registry import ServiceRegistry
from discord.ext import tasks
from psycopg.types.json import Json
from socketserver import BaseRequestHandler, ThreadingUDPServer
from typing import Tuple, Callable


@ServiceRegistry.register("EventListener")
class EventListenerService(Service):

    def __init__(self, main):
        super().__init__(main)
        self.servers: dict[str, ServerImpl] = dict()
        self.udp_server = None
        self.executor = None
        self.loop = asyncio.get_event_loop()
        self.intercom.add_exception_type(psycopg.DatabaseError)

    async def start(self):
        await super().start()
        self.executor = ThreadPoolExecutor(thread_name_prefix='EventExecutor', max_workers=20)
        await self.start_udp_listener()
        self.init_servers()
        self.intercom.start()
        await self.register_servers()

    async def stop(self):
        self.log.info('Graceful shutdown ...')
        if self.udp_server:
            self.log.debug("- Processing unprocessed messages ...")
            await asyncio.to_thread(self.udp_server.shutdown)
            self.log.debug("- All messages processed.")
            self.udp_server.server_close()
        self.log.debug('- Listener stopped.')
        self.executor.shutdown(wait=True)
        self.log.debug('- Executor stopped.')
        self.intercom.cancel()
        self.log.info('- Intercom stopped.')
        await super().stop()
        self.log.info("DCSServerBot Agent stopped.")

    def is_master(self) -> bool:
        return False

    def init_servers(self):
        for server_name, installation in utils.findDCSInstallations():
            if installation in self.config:
                server: ServerImpl = DataObjectFactory().new(
                    Server.__name__, bot=self, name=server_name, installation=installation,
                    host=self.config[installation]['DCS_HOST'], port=self.config[installation]['DCS_PORT'])
                self.servers[server_name] = server
                # TODO: can be removed if bug in net.load_next_mission() is fixed
                if 'listLoop' not in server.settings or not server.settings['listLoop']:
                    server.settings['listLoop'] = True

    async def register_servers(self):
        self.log.info('- Searching for running DCS servers (this might take a bit) ...')
        servers = list(self.servers.values())
        timeout = (5 * len(self.servers)) if self.config.getboolean('BOT', 'SLOW_SYSTEM') else (3 * len(self.servers))
        ret = await asyncio.gather(
            *[server.sendtoDCSSync({"command": "registerDCSServer"}, timeout) for server in servers],
            return_exceptions=True
        )
        num = 0
        for i in range(0, len(servers)):
            if isinstance(ret[i], asyncio.TimeoutError):
                servers[i].status = Status.SHUTDOWN
                self.log.debug(f'  => Timeout while trying to contact DCS server "{servers[i].name}".')
            elif isinstance(ret[i], Exception):
                self.log.exception(ret[i])
            else:
                self.log.info(f'  => Running DCS server "{servers[i].name}" registered.')
                num += 1
        if num == 0:
            self.log.info('- No running servers found.')
        self.log.info('DCSServerBot Agent started.')

    def sendtoMaster(self, data: dict):
        with self.pool.connection() as conn:
            with conn.pipeline():
                with conn.transaction():
                    conn.execute("INSERT INTO intercom (agent, data) VALUES ('Master', %s)", (Json(data), ))

    @tasks.loop(seconds=1)
    async def intercom(self):
        with self.pool.connection() as conn:
            with conn.pipeline():
                with conn.transaction():
                    with closing(conn.cursor()) as cursor:
                        for row in cursor.execute("SELECT id, data FROM intercom WHERE agent = %s",
                                                  (platform.node(), )).fetchall():
                            data = row[1]
                            server_name = data['server_name']
                            if server_name not in self.servers:
                                self.log.warning(
                                    f"Command {data['command']} for unknown server {server_name} received, ignoring")
                            else:
                                server: ServerImpl = self.servers[server_name]
                                server.sendtoDCS({"command": "registerDCSServer"})
                            cursor.execute("DELETE FROM intercom WHERE id = %s", (row[0], ))

    async def start_udp_listener(self):
        class RequestHandler(BaseRequestHandler):

            def handle(s):
                data = json.loads(s.request[0].strip())
                # ignore messages not containing server names
                if 'server_name' not in data:
                    self.log.warning('Message without server_name received: {}'.format(data))
                    return
                server_name = data['server_name']
                if server_name not in self.servers:
                    self.log.warning(f"Command {data['command']} for unknown server {server_name} received.")
                    return
                self.log.debug('{}->HOST: {}'.format(server_name, json.dumps(data)))
                if 'channel' in data and data['channel'].startswith('sync-'):
                    server: ServerImpl = self.servers[server_name]
                    if data['channel'] in server.listeners:
                        f = server.listeners[data['channel']]
                        if not f.done():
                            self.loop.call_soon_threadsafe(f.set_result, data)
                        if data['command'] != 'registerDCSServer':
                            return
                self.sendtoMaster(data)

        class MyThreadingUDPServer(ThreadingUDPServer):
            def __init__(self, server_address: Tuple[str, int], request_handler: Callable[..., BaseRequestHandler],
                         listener: EventListenerService):
                self.log = listener.log
                try:
                    # enable reuse, in case the restart was too fast and the port was still in TIME_WAIT
                    MyThreadingUDPServer.allow_reuse_address = True
                    MyThreadingUDPServer.max_packet_size = 65504
                    super().__init__(server_address, request_handler)
                except Exception as ex:
                    self.log.exception(ex)

        host = self.config['BOT']['HOST']
        port = int(self.config['BOT']['PORT'])
        self.udp_server = MyThreadingUDPServer((host, port), RequestHandler, self)
        self.executor.submit(self.udp_server.serve_forever)
        self.log.debug('- Listener started on interface {} port {} accepting commands.'.format(host, port))
