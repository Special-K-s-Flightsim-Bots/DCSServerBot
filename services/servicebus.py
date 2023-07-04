from __future__ import annotations
import asyncio
import concurrent
import json
import platform
import psycopg
import sys

from _operator import attrgetter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from core import Server, DataObjectFactory, utils, Status, ServerImpl, Autoexec, ServerProxy, EventListener, \
    InstanceProxy, NodeProxy
from core.services.base import Service
from core.services.registry import ServiceRegistry
from discord.ext import tasks
from psycopg.types.json import Json
from queue import Queue
from socketserver import BaseRequestHandler, ThreadingUDPServer
from typing import Tuple, Callable, Optional, cast, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Plugin
    from services import DCSServerBot


@ServiceRegistry.register("ServiceBus")
class ServiceBus(Service):

    def __init__(self, node, name: str):
        super().__init__(node, name)
        self.bot: Optional[DCSServerBot] = None
        self.version = self.node.bot_version
        self.eventListeners: list[EventListener] = []
        self.servers: dict[str, Server] = dict()
        self.udp_server = None
        self.executor = None
        if self.node.locals['DCS'].get('desanitize', True):
            utils.desanitize(self)
        self.loop = asyncio.get_event_loop()
        self.intercom.add_exception_type(psycopg.DatabaseError)

    async def start(self):
        await super().start()
        # cleanup the intercom channels
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM intercom WHERE node = %s", (self.node.name, ))
        self.executor = ThreadPoolExecutor(thread_name_prefix='ServiceBus', max_workers=20)
        await self.start_udp_listener()
        await self.init_servers()
        self.intercom.start()
        if self.master:
            self.bot = ServiceRegistry.get("Bot").bot
            await self.bot.wait_until_ready()
        await self.register_servers()

    async def stop(self):
        if self.udp_server:
            self.log.debug("- Processing unprocessed messages ...")
            await asyncio.to_thread(self.udp_server.shutdown)
            self.log.debug("- All messages processed.")
            self.udp_server.server_close()
        self.log.debug('- Listener stopped.')
        self.executor.shutdown(wait=True)
        self.log.debug('- Executor stopped.')
        self.intercom.cancel()
        self.log.debug('- Intercom stopped.')
        await super().stop()

    @property
    def master(self) -> bool:
        return self.node.master

    def register_eventListener(self, listener: EventListener):
        self.log.debug(f'- Registering EventListener {type(listener).__name__}')
        self.eventListeners.append(listener)

    def unregister_eventListener(self, listener: EventListener):
        self.eventListeners.remove(listener)
        self.log.debug(f'- EventListener {type(listener).__name__} unregistered.')

    async def init_servers(self):
        for instance in self.node.instances:
            server: ServerImpl = DataObjectFactory().new(
                Server.__name__, node=self.node, port=instance.bot_port, name=instance.configured_server)
            instance.server = server
            self.servers[server.name] = server
            # TODO: can be removed if bug in net.load_next_mission() is fixed
            if 'listLoop' not in server.settings or not server.settings['listLoop']:
                server.settings['listLoop'] = True

    async def send_init(self, server: Server):
        self.sendtoBot({
            "command": "rpc",
            "service": "ServiceBus",
            "method": "init_remote_server",
            "params": {
                "server_name": server.name,
                "public_ip": self.node.locals.get('public_ip', await utils.get_public_ip()),
                "status": server.status.value,
                "instance": server.instance.name,
                "settings": server.settings,
                "options": server.options
            }
        })

    async def register_servers(self):
        self.log.info('- Searching for running local DCS servers (this might take a bit) ...')
        timeout = (10 * len(self.servers)) if self.node.locals.get('slow_system', False) else (5 * len(self.servers))
        local_servers = [x for x in self.servers.values() if not x.is_remote]
        calls = []
        for server in local_servers:
            if server.is_remote:
                continue
            calls.append(server.sendtoDCSSync({"command": "registerDCSServer"}, timeout))
            if not self.master:
                server.status = Status.UNREGISTERED
                await self.send_init(server)
        ret = await asyncio.gather(*calls, return_exceptions=True)
        num = 0
        for i, server in enumerate(local_servers):
            if isinstance(ret[i], asyncio.TimeoutError):
                server.status = Status.SHUTDOWN
                self.log.debug(f'  => Timeout while trying to contact DCS server "{server.name}".')
                if not self.master:
                    await self.send_init(server)
            elif isinstance(ret[i], Exception):
                self.log.exception(ret[i])
            else:
                num += 1
        if num == 0:
            self.log.info('- No running local servers found.')

    def register_server(self, data: dict) -> bool:
        server_name = data['server_name']
        # check for protocol incompatibilities
        if data['hook_version'] != self.version:
            self.log.error(f'Server "{server_name}" has wrong Hook version installed. '
                           f'Please restart your DCS server. Registration aborted.')
            return False
        if server_name not in self.servers:
            self.log.error(f"Server {server_name} is not configured. Registration aborted.")
            return False
        self.log.debug(f'  => Registering DCS-Server "{server_name}"')
        server: ServerImpl = cast(ServerImpl, self.servers[server_name])
        # set the PID
        for exe in ['DCS_server.exe', 'DCS.exe']:
            server.process = utils.find_process(exe, server.instance.name)
            if server.process:
                break
        server.dcs_version = data['dcs_version']
        if data['channel'].startswith('sync-'):
#            if 'players' not in data:
#                server.status = Status.STOPPED
#            elif data['pause']:
#                server.status = Status.PAUSED
#            else:
#                server.status = Status.RUNNING
            server.init_extensions()
            for extension in server.extensions.values():
                if not extension.is_running():
                    asyncio.run_coroutine_threadsafe(extension.startup(), self.loop)

        # validate server ports
        dcs_ports: dict[int, str] = dict()
        webgui_ports: dict[int, str] = dict()
        webrtc_ports: dict[int, str] = dict()
        for server in self.servers.values():
            dcs_port = server.settings.get('port', 10308)
            if dcs_port in dcs_ports:
                self.log.error(f'Server "{server.name}" shares its DCS port with server '
                               f'"{dcs_ports[dcs_port]}"! Registration aborted.')
                return False
            else:
                dcs_ports[dcs_port] = server.name
            autoexec = Autoexec(server.instance)
            webgui_port = autoexec.webgui_port or 8088
            if webgui_port in webgui_ports:
                self.log.error(f'Server "{server.name}" shares its webgui_port with server '
                               f'"{webgui_ports[webgui_port]}"! Registration aborted.')
                return False
            else:
                webgui_ports[webgui_port] = server.name
            webrtc_port = autoexec.webrtc_port or 10309
            if webrtc_port in webrtc_ports:
                if server.settings['advanced'].get('voice_chat_server', False):
                    self.log.error(f'Server "{server.name}" shares its webrtc_port port with server '
                                   f'"{webrtc_ports[webrtc_port]}"! Registration aborted.')
                    return False
                else:
                    self.log.warning(f'Server "{server.name}" shares its webrtc_port port with server '
                                     f'"{webrtc_ports[webrtc_port]}", but voice chat is disabled.')
            else:
                webrtc_ports[webrtc_port] = server.name

        # update the database and check for server name changes
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT server_name FROM servers WHERE node=%s AND port=%s',
                               (platform.node(), data['port']))
                if cursor.rowcount == 1:
                    _server_name = cursor.fetchone()[0]
                    if _server_name != server_name:
                        if utils.findDCSInstances(_server_name) and not self.servers.get(_server_name):
                            self.log.info(f'Auto-renaming server "{_server_name}" to "{server_name}"')
                            server.rename(server_name)
                            if _server_name in self.servers:
                                del self.servers[_server_name]
                        else:
                            self.log.warning(f'Registration of server "{server_name}" aborted due to conflict.')
                            del self.servers[server_name]
                            return False
        self.log.info(f'  => Local DCS-Server "{server_name}" registered.')
        return True

    def rename(self, old_name: str, new_name: str):
        with self.pool.connection() as conn:
            with conn.transaction():
                # call rename() in all Plugins
                for plugin in self.bot.cogs.values():  # type: Plugin
                    plugin.rename(conn, old_name, new_name)
                self.servers[new_name] = self.servers[old_name]
                self.servers[new_name].rename(new_name, True)
                del self.servers[old_name]

    def init_remote_server(self, server_name: str, public_ip: str, status: str, instance: str, settings: dict,
                           options: dict, node: str):
        server = self.servers.get(server_name)
        node = NodeProxy(self.node, node)
        if not server:
            server = ServerProxy(
                node=node,
                port=-1,
                name=server_name
            )
            instance = InstanceProxy(name=instance, node=node)
            server.instance = instance
            self.servers[server_name] = server
            server.settings = settings
            server.options = options
            # add eventlistener queue
            if server.name not in self.udp_server.message_queue:
                self.udp_server.message_queue[server.name] = Queue()
                self.executor.submit(self.udp_server.process, server)
            self.log.info(f"  => DCS-Server \"{server.name}\" from Node {server.node.name} registered.")
        else:
            # IP might have changed, so update it
            server.public_ip = public_ip
        server.status = Status(status)

    def sendtoBot(self, data: dict, node: Optional[str] = None):
        if self.master:
            if node and node != platform.node():
                self.log.debug('MASTER->{}: {}'.format(node, json.dumps(data)))
                with self.pool.connection() as conn:
                    with conn.transaction():
                        conn.execute("INSERT INTO intercom (node, data) VALUES (%s, %s)", (node, Json(data)))
            else:
                self.udp_server.message_queue[data['server_name']].put(data)
        else:
            data['node'] = self.node.name
            with self.pool.connection() as conn:
                with conn.pipeline():
                    with conn.transaction():
                        conn.execute("INSERT INTO intercom (node, data) VALUES ('Master', %s)", (Json(data),))
                        self.log.debug(f"{self.node.name}->MASTER: {json.dumps(data)}")

    async def handle_master(self, data: dict):
        self.log.debug(f"{data['node']}->MASTER: {json.dumps(data)}")
        if data['command'] == 'rpc':
            if data['service'] == 'Node':
                obj = self.node
            else:
                obj = ServiceRegistry.get(data['service'])
            if not obj:
                self.log.warning('RPC command received for unknown object/service.')
                return
            rc = await self.rpc(obj, data)
            if rc:
                data['return'] = rc
                self.sendtoBot(data, node=data['node'])
        else:
            self.udp_server.message_queue[data['server_name']].put(data)

    async def handle_agent(self, data: dict):
        self.log.debug(f"MASTER->{data['node']}: {json.dumps(data)}")
        if data['command'] == 'rpc':
            if data.get('object') == 'Server':
                obj = self.servers[data['server_name']]
            elif data.get('object') == 'Instance':
                obj = self.servers[data['server_name']].instance
            elif data.get('object') == 'Node':
                obj = self.node
            else:
                obj = ServiceRegistry.get(data['service'])
            if not obj:
                self.log.warning('RPC command received for unknown object/service.')
                return
            rc = await self.rpc(obj, data)
            if rc:
                data['return'] = rc
                self.sendtoBot(data)
        else:
            server_name = data['server_name']
            if server_name not in self.servers:
                self.log.warning(
                    f"Command {data['command']} for unknown server {server_name} received, ignoring")
            else:
                server: Server = self.servers[server_name]
                server.sendtoDCS(data)

    @tasks.loop(seconds=1)
    async def intercom(self):
        with self.pool.connection() as conn:
            with conn.pipeline():
                with conn.transaction():
                    with closing(conn.cursor()) as cursor:
                        for row in cursor.execute("SELECT id, data FROM intercom WHERE node = %s",
                                                  ("Master" if self.master else platform.node(), )).fetchall():
                            data = row[1]
                            if sys.getsizeof(data) > 8 * 1024:
                                self.log.error("Packet is larger than 8 KB!")
                            try:
                                if self.master:
                                    await self.handle_master(data)
                                else:
                                    await self.handle_agent(data)
                            except Exception as ex:
                                self.log.exception(ex)
                            cursor.execute("DELETE FROM intercom WHERE id = %s", (row[0], ))

    @staticmethod
    async def rpc(obj: object, data: dict) -> Optional[dict]:
        func = attrgetter(data.get('method'))(obj)
        if not func:
            return
        kwargs = data.get('params', {})
        kwargs['node'] = data.get('node', platform.node())
        if asyncio.iscoroutinefunction(func):
            rc = await func(**kwargs) if kwargs else await func()
        else:
            rc = func(**kwargs) if kwargs else func()
        return rc

    async def start_udp_listener(self):
        class RequestHandler(BaseRequestHandler):

            def handle(derived):
                data: dict = json.loads(derived.request[0].strip())
                # ignore messages not containing server names
                if 'server_name' not in data:
                    self.log.warning('Message without server_name received: {}'.format(data))
                    return
                server_name = data['server_name']
                self.log.debug('{}->HOST: {}'.format(server_name, json.dumps(data)))
                server = self.servers.get(server_name) or None
                if not server:
                    self.log.debug(
                        f"Command {data['command']} for unregistered server {server_name} received, ignoring.")
                    return
                udp_server: MyThreadingUDPServer = cast(MyThreadingUDPServer, derived.server)
                if server.name not in udp_server.message_queue:
                    udp_server.message_queue[server.name] = Queue()
                    self.executor.submit(udp_server.process, server)
                udp_server.message_queue[server.name].put(data)

        class MyThreadingUDPServer(ThreadingUDPServer):
            def __init__(derived, server_address: Tuple[str, int], request_handler: Callable[..., BaseRequestHandler]):
                try:
                    # enable reuse, in case the restart was too fast and the port was still in TIME_WAIT
                    MyThreadingUDPServer.allow_reuse_address = True
                    MyThreadingUDPServer.max_packet_size = 65504
                    derived.message_queue: dict[str, Queue[dict]] = {}
                    super().__init__(server_address, request_handler)
                except Exception as ex:
                    self.log.exception(ex)

            def process(derived, server: Server):
                data: dict = derived.message_queue[server.name].get()
                while data:
                    try:
                        command = data['command']
                        if command == 'registerDCSServer':
                            if not server.is_remote:
                                if not self.register_server(data):
                                    self.log.error(f"Error while registering server {server.name}.")
                                    return
                                if not self.master:
                                    self.log.debug(f"Registering server {server.name} on Master node ...")
                        elif server.status == Status.UNREGISTERED:
                            self.log.debug(
                                f"Command {command} for unregistered server {server.name} received, ignoring.")
                            continue
                        if 'channel' in data and data['channel'].startswith('sync-'):
                            if data['channel'] in server.listeners:
                                f = server.listeners[data['channel']]
                                if not f.done():
                                    self.loop.call_soon_threadsafe(f.set_result, data)
                                if data['command'] != 'registerDCSServer':
                                    continue
                        if self.master:
                            concurrent.futures.wait(
                                [
                                    asyncio.run_coroutine_threadsafe(
                                        listener.processEvent(command, server, deepcopy(data)), self.loop
                                    )
                                    for listener in self.eventListeners
                                    if listener.has_event(command)
                                ]
                            )
                        else:
                            self.sendtoBot(data)
                    except Exception as ex:
                        self.log.exception(ex)
                    finally:
                        derived.message_queue[server.name].task_done()
                        data = derived.message_queue[server.name].get()

            def shutdown(derived):
                super().shutdown()
                try:
                    for server_name, queue in derived.message_queue.items():
                        if not queue.empty():
                            queue.join()
                        queue.put({})
                except Exception as ex:
                    self.log.exception(ex)

        host = self.node.listen_address
        port = self.node.listen_port
        self.udp_server = MyThreadingUDPServer((host, port), RequestHandler)
        self.executor.submit(self.udp_server.serve_forever)
        self.log.debug('- Listener started on interface {} port {} accepting commands.'.format(host, port))
