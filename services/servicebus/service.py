from __future__ import annotations
import asyncio
import concurrent
import json
import platform
import psycopg
import sys
import uuid

from _operator import attrgetter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from core import Server, DataObjectFactory, Status, ServerImpl, Autoexec, ServerProxy, EventListener, \
    InstanceProxy, NodeProxy, Mission, Node, utils
from core.services.base import Service
from core.services.registry import ServiceRegistry
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
from enum import Enum
from psycopg.rows import dict_row
from psycopg.types.json import Json
from queue import Queue
from socketserver import BaseRequestHandler, ThreadingUDPServer
from typing import Tuple, Callable, Optional, cast, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from services import DCSServerBot

__all__ = [
    "ServiceBus"
]


@ServiceRegistry.register("ServiceBus")
class ServiceBus(Service):

    def __init__(self, node, name: str):
        super().__init__(node, name)
        self.bot: Optional[DCSServerBot] = None
        self.version = self.node.bot_version
        self.listeners: dict[str, asyncio.Future] = dict()
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
        try:
            # cleanup the intercom channels
            with self.pool.connection() as conn:
                with conn.transaction():
                    # conn.execute("DELETE FROM intercom WHERE node = %s", (self.node.name, ))
                    conn.execute("DELETE FROM files WHERE created < NOW() - interval '300 seconds'")
                    conn.execute("DELETE FROM intercom WHERE time < NOW() - interval '300 seconds'")
                    conn.execute("UPDATE intercom SET node = 'Master' WHERE node = %s", (self.node.name, ))
            self.executor = ThreadPoolExecutor(thread_name_prefix='ServiceBus', max_workers=20)
            await self.start_udp_listener()
            await self.init_servers()
            self.intercom.start()
            if self.master:
                self.bot = ServiceRegistry.get("Bot").bot
                await self.bot.wait_until_ready()
            await self.register_local_servers()
        except Exception as ex:
            self.log.exception(ex)

    async def stop(self):
        if self.udp_server:
            self.log.debug("- Processing unprocessed messages ...")
            await asyncio.to_thread(self.udp_server.shutdown)
            self.log.debug("- All messages processed.")
            self.udp_server.server_close()
        self.log.debug('- Listener stopped.')
        if self.executor:
            self.executor.shutdown(wait=True)
            self.log.debug('- Executor stopped.')
        if not self.master:
            self.send_to_node({
                "command": "rpc",
                "service": "ServiceBus",
                "method": "unregister_remote_node",
                "params": {
                    "node": self.node.name
                }
            })
        self.intercom.cancel()
        self.log.debug('- Intercom stopped.')
        await super().stop()

    @property
    def master(self) -> bool:
        return self.node.master

    @property
    def filter(self) -> dict:
        return {
            "server_name": "!.*",
            "mission_name": "!.*",
        } | self.node.config.get('filter', {})

    def register_eventListener(self, listener: EventListener):
        self.log.debug(f'  - Registering EventListener {type(listener).__name__}')
        self.eventListeners.append(listener)

    def unregister_eventListener(self, listener: EventListener):
        self.eventListeners.remove(listener)
        self.log.debug(f'  - EventListener {type(listener).__name__} unregistered.')

    async def init_servers(self):
        for instance in self.node.instances:
            if not instance.configured_server:
                continue
            server: ServerImpl = DataObjectFactory().new(
                Server.__name__, node=self.node, port=instance.bot_port, name=instance.configured_server)
            instance.server = server
            self.servers[server.name] = server
            # TODO: can be removed if bug in net.load_next_mission() is fixed
            if 'listLoop' not in server.settings or not server.settings['listLoop']:
                server.settings['listLoop'] = True

    async def send_init(self, server: Server):
        self.send_to_node({
            "command": "rpc",
            "service": "ServiceBus",
            "method": "init_remote_server",
            "params": {
                "server_name": server.name,
                "public_ip": self.node.locals.get('public_ip', await utils.get_public_ip()),
                "status": server.status.value,
                "instance": server.instance.name,
                "settings": server.settings,
                "options": server.options,
                "node": self.node.name
            }
        })

    async def register_local_servers(self):
        self.log.info('- Searching for running local DCS servers (this might take a bit) ...')
        timeout = (10 * len(self.servers)) if self.node.locals.get('slow_system', False) else (5 * len(self.servers))
        local_servers = [x for x in self.servers.values() if not x.is_remote]
        calls = []
        for server in local_servers:
            if server.is_remote:
                continue
            calls.append(server.send_to_dcs_sync({"command": "registerDCSServer"}, timeout))
            if not self.master:
                server.status = Status.UNREGISTERED
                await self.send_init(server)
        ret = await asyncio.gather(*calls, return_exceptions=True)
        num = 0
        for i, server in enumerate(local_servers):
            if isinstance(ret[i], asyncio.TimeoutError):
                self.log.debug(f'  => Timeout while trying to contact DCS server "{server.name}".')
                server.status = Status.SHUTDOWN
            elif isinstance(ret[i], Exception):
                self.log.exception(ret[i])
            else:
                num += 1
        if num == 0:
            self.log.debug('- No running local servers found.')

    async def register_remote_node(self, node: str):
        self.log.info(f"- Registering remote node {node}.")
        self.send_to_node({
            "command": "rpc",
            "service": "ServiceBus",
            "method": "register_local_servers"
        }, node=node)

    async def unregister_remote_node(self, node: str):
        self.log.info(f"- Unregistering remote node {node}.")
        for server in [x for x in self.servers.values() if x.is_remote]:
            if server.node.name == node:
                del self.servers[server.name]

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
        # if we are an agent, initialize the server
        if not self.master:
            if 'current_mission' in data:
                if not server.current_mission:
                    server.current_mission = DataObjectFactory().new(
                        Mission.__name__, node=server.node, server=server, map=data['current_map'],
                        name=data['current_mission'])
                server.current_mission.update(data)

        # validate server ports
        dcs_ports: dict[int, str] = dict()
        webgui_ports: dict[int, str] = dict()
        webrtc_ports: dict[int, str] = dict()
        for server in self.servers.values():
            # only check ports of local servers
            if server.is_remote or server.status == Status.SHUTDOWN:
                continue
            dcs_port = int(server.settings.get('port', 10308))
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
                            asyncio.run(server.rename(server_name))
                        else:
                            self.log.warning(f'Registration of server "{server_name}" aborted due to conflict.')
                            del self.servers[server_name]
                            return False
        self.log.info(f'  => Local DCS-Server "{server_name}" registered.')
        return True

    def ban(self, ucid: str, banned_by: str, reason: str = 'n/a', days: Optional[int] = None):
        if days:
            until: datetime = datetime.now() + timedelta(days=days)
            until_str = until.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M') + ' (UTC)'
        else:
            until = datetime(year=9999, month=12, day=31)
            until_str = 'never'
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO bans (ucid, banned_by, reason, banned_until) 
                    VALUES (%s, %s, %s, %s) 
                    ON CONFLICT DO NOTHING
                """, (ucid, banned_by, reason, until))
        for server in self.servers.values():
            if server.status not in [Status.PAUSED, Status.RUNNING, Status.STOPPED]:
                continue
            server.send_to_dcs({
                "command": "ban",
                "ucid": ucid,
                "reason": reason,
                "banned_until": until_str
            })
            player = server.get_player(ucid=ucid)
            if player:
                player.banned = True

    def unban(self, ucid: str):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM bans WHERE ucid = %s", (ucid, ))
        for server in self.servers.values():
            if server.status not in [Status.PAUSED, Status.RUNNING, Status.STOPPED]:
                continue
            server.send_to_dcs({
                "command": "unban",
                "ucid": ucid
            })
            player = server.get_player(ucid=ucid)
            if player:
                player.banned = False

    def bans(self) -> list[dict]:
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                return [
                    x for x in cursor.execute("""
                        SELECT b.ucid, COALESCE(p.discord_id, -1) AS discord_id, p.name, b.banned_by, b.reason, 
                               b.banned_until 
                        FROM bans b LEFT OUTER JOIN players p on b.ucid = p.ucid 
                        WHERE b.banned_until >= NOW()
                    """)
                ]

    def is_banned(self, ucid: str) -> Optional[dict]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM bans WHERE ucid = %s AND banned_until >= NOW()", (ucid, )).fetchone()

    def init_remote_server(self, server_name: str, public_ip: str, status: str, instance: str, settings: dict,
                           options: dict, node: str):
        server = self.servers.get(server_name)
        if not server:
            node = NodeProxy(self.node, node, public_ip)
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
            server.node.public_ip = public_ip
        server.status = Status(status)

    def send_to_node(self, data: dict, *, node: Optional[Union[Node, str]] = None):
        if isinstance(node, Node):
            node = node.name
        if self.master:
            if node and node != platform.node():
                self.log.debug('MASTER->{}: {}'.format(node, json.dumps(data)))
                with self.pool.connection() as conn:
                    with conn.transaction():
                        conn.execute("INSERT INTO intercom (node, data) VALUES (%s, %s)", (node, Json(data)))
            elif data['command'] != 'rpc':
                self.udp_server.message_queue[data['server_name']].put(data)
            else:
                asyncio.create_task(self.handle_rpc(data))
        else:
            data['node'] = self.node.name
            with self.pool.connection() as conn:
                with conn.pipeline():
                    with conn.transaction():
                        conn.execute("INSERT INTO intercom (node, data) VALUES ('Master', %s)", (Json(data),))
                        self.log.debug(f"{self.node.name}->MASTER: {json.dumps(data)}")

    async def send_to_node_sync(self, message: dict, timeout: Optional[int] = 10.0, *,
                                node: Optional[Union[Node, str]] = None):
        future = self.loop.create_future()
        token = 'sync-' + str(uuid.uuid4())
        message['channel'] = token
        self.listeners[token] = future
        try:
            self.send_to_node(message, node=node)
            return await asyncio.wait_for(future, timeout)
        finally:
            del self.listeners[token]

    async def handle_rpc(self, data: dict):
        self.log.debug(f"RPC: {json.dumps(data)}")
        # handle synchronous responses
        if data.get('channel', '').startswith('sync-') and 'return' in data:
            if data['channel'] in self.listeners:
                f = self.listeners[data['channel']]
                if not f.done():
                    if 'exception' in data:
                        self.loop.call_soon_threadsafe(
                            f.set_exception,
                            utils.str_to_class(data['exception']['class'])(data['exception']['message'])
                        )
                    else:
                        # TODO: change to data['return']
                        self.loop.call_soon_threadsafe(f.set_result, data)
            else:
                self.log.warning(f"This message should not have been sent to node {self.node.name}!")
            return
        obj = None
        if data.get('object') == 'Server':
            obj = self.servers.get(data.get('server_name', data.get('server')))
        elif data.get('object') == 'Instance':
            server = self.servers.get(data.get('server_name', data.get('server')))
            if server:
                obj = server.instance
        elif data.get('object') == 'Node':
            obj = self.node
        else:
            obj = ServiceRegistry.get(data['service'])
        if not obj:
            self.log.warning('RPC command received for unknown object/service.')
            return
        try:
            rc = await self.rpc(obj, data)
            if data.get('channel', '').startswith('sync-'):
                if isinstance(rc, Enum):
                    rc = rc.value
                elif isinstance(rc, bool):
                    rc = str(rc)
                self.send_to_node({
                    "command": "rpc",
                    "method": data['method'],
                    "channel": data['channel'],
                    "return": rc or ''
                }, node=data.get('node'))
        except Exception as ex:
            if data.get('channel', '').startswith('sync-'):
                self.send_to_node({
                    "command": "rpc",
                    "method": data['method'],
                    "channel": data['channel'],
                    "return": '',
                    "exception": {
                        "class": ex.__class__.__name__,
                        "message": ex.__repr__()
                    }
                }, node=data.get('node'))

    async def handle_master(self, data: dict):
        self.log.debug(f"{data['node']}->MASTER: {json.dumps(data)}")
        if data['server_name'] in self.udp_server.message_queue:
            self.udp_server.message_queue[data['server_name']].put(data)
        else:
            self.log.debug(f"Intercom: message ignored, no server {data['server_name']} registered.")

    async def handle_agent(self, data: dict):
        self.log.debug(f"MASTER->{self.node.name}: {json.dumps(data)}")
        server_name = data['server_name']
        if server_name not in self.servers:
            self.log.warning(
                f"Command {data['command']} for unknown server {server_name} received, ignoring")
        else:
            server: Server = self.servers[server_name]
            server.send_to_dcs(data)

    @tasks.loop(seconds=1)
    async def intercom(self):
        with self.pool.connection() as conn:
            with conn.pipeline():
                while True:
                    with conn.transaction():
                        with closing(conn.cursor()) as cursor:
                            # we read until there is no new data, then we wait for the next call (after 1 s)
                            rows = cursor.execute("SELECT id, data FROM intercom WHERE node = %s",
                                                  ("Master" if self.master else platform.node(), )).fetchall()
                            if not len(rows):
                                return
                            for row in rows:
                                data = row[1]
                                if sys.getsizeof(data) > 8 * 1024:
                                    self.log.error("Packet is larger than 8 KB!")
                                try:
                                    if data['command'] == 'rpc':
                                        asyncio.create_task(self.handle_rpc(data))
                                    elif self.master:
                                        asyncio.create_task(self.handle_master(data))
                                    else:
                                        asyncio.create_task(self.handle_agent(data))
                                except Exception as ex:
                                    self.log.exception(ex)
                                cursor.execute("DELETE FROM intercom WHERE id = %s", (row[0], ))

    async def rpc(self, obj: object, data: dict) -> Optional[dict]:
        if 'method' in data:
            func = attrgetter(data.get('method'))(obj)
            if not func:
                return
            kwargs = deepcopy(data.get('params', {}))
            # servers will be passed by name
            if kwargs.get('server'):
                kwargs['server'] = self.servers[kwargs['server']]
            if kwargs.get('instance'):
                kwargs['instance'] = next(x for x in self.node.instances if x.name == kwargs['instance'])
            if self.master:
                if kwargs.get('member'):
                    kwargs['member'] = self.bot.guilds[0].get_member(int(kwargs['member'][2:-1]))
                if kwargs.get('user'):
                    if kwargs['user'].startswith('<@'):
                        kwargs['user'] = self.bot.guilds[0].get_member(int(kwargs['user'][2:-1]))
            if asyncio.iscoroutinefunction(func):
                rc = await func(**kwargs) if kwargs else await func()
            else:
                rc = func(**kwargs) if kwargs else func()
            return rc
        elif 'params' in data:
            for key, value in data['params'].items():
                setattr(obj, key, value)

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
                if 'channel' in data and data['channel'].startswith('sync-'):
                    if data['channel'] in server.listeners:
                        f = server.listeners[data['channel']]
                        if not f.done():
                            self.loop.call_soon_threadsafe(f.set_result, data)
                        if data['command'] not in ['registerDCSServer', 'getMissionUpdate']:
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
                            self.send_to_node(data)
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
        self.log.debug('  - Listener started on interface {} port {} accepting commands.'.format(host, port))
