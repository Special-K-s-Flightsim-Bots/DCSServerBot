from __future__ import annotations
import asyncio
import concurrent
import inspect
import json
import uuid

from _operator import attrgetter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from core import Server, Mission, Node, DataObjectFactory, Status, Autoexec, ServerProxy, utils, PubSub
from core.services.base import Service
from core.services.registry import ServiceRegistry
from core.data.impl.serverimpl import ServerImpl
from datetime import datetime, timedelta, timezone
from enum import Enum
from psycopg.rows import dict_row
from psycopg.types.json import Json
from queue import Queue
from socketserver import BaseRequestHandler, ThreadingUDPServer
from typing import Callable, Optional, cast, Union, Any, TYPE_CHECKING

from ..bot.service import BotService
from ..bot.dcsserverbot import DCSServerBot

__all__ = [
    "ServiceBus"
]

if TYPE_CHECKING:
    from core import EventListener


@ServiceRegistry.register()
class ServiceBus(Service):

    def __init__(self, node):
        super().__init__(node)
        self.bot: Optional[DCSServerBot] = None
        self.version = self.node.bot_version
        self.listeners: dict[str, asyncio.Future] = dict()
        self.eventListeners: list[EventListener] = []
        self.servers: dict[str, Server] = dict()
        self.udp_server = None
        self.executor = None
        if self.node.locals['DCS'].get('desanitize', True):
            if not self.node.locals['DCS'].get('cloud', False) or self.master:
                utils.desanitize(self)
        self.loop = asyncio.get_event_loop()
        # main.yaml database connection has priority for intercom
        url = self.node.config.get("database", self.node.locals.get('database'))['url']
        self.intercom_channel = PubSub(self.node, 'intercom', url)
        # nodes.yaml database connection has priority for broadcasts
        url = self.node.locals.get("database", self.node.config.get('database'))['url']
        self.broadcasts_channel = PubSub(self.node, 'broadcasts', url)
        self._lock = asyncio.Lock()

    async def start(self):
        await super().start()
        try:
            # Start the DCS listener
            self.executor = ThreadPoolExecutor(thread_name_prefix='ServiceBus', max_workers=20)
            await self.start_udp_listener()

            # cleanup the intercom and broadcast channels
            await self.intercom_channel.clear()
            await self.broadcasts_channel.clear()
            # cleanup the files
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        DELETE FROM files 
                        WHERE guild_id = %s AND created < ((now() AT TIME ZONE 'utc') - interval '300 seconds')
                    """, (self.node.guild_id, ))

            # subscribe to the intercom and broadcast channels
            # noinspection PyAsyncCall
            asyncio.create_task(self.intercom_channel.subscribe(self.handle_rpc))
            # noinspection PyAsyncCall
            asyncio.create_task(self.broadcasts_channel.subscribe(self.handle_broadcast_event))

            await self.init_servers()
            if self.master:
                self.bot = ServiceRegistry.get(BotService).bot
                while not self.bot:
                    await asyncio.sleep(1)
                    self.bot = ServiceRegistry.get(BotService).bot
                await self.bot.wait_until_ready()
                await self.register_local_servers()
            else:
                self.send_to_node({
                    "command": "rpc",
                    "service": "ServiceBus",
                    "method": "register_remote_node",
                    "params": {
                        "node": self.node.name
                    }
                })

        except Exception as ex:
            self.log.exception(ex)

    async def stop(self):
        await self.broadcasts_channel.close()
        await self.intercom_channel.close()
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
        async with self.apool.connection() as conn:
            for instance in self.node.instances:
                try:
                    cursor = await conn.execute("""
                        SELECT server_name FROM instances 
                        WHERE node=%s AND instance=%s AND server_name IS NOT NULL
                    """, (self.node.name, instance.name))
                    row = await cursor.fetchone()
                    # was there a server bound to this instance?
                    if row:
                        server: ServerImpl = DataObjectFactory().new(
                            ServerImpl, node=self.node, port=instance.bot_port, name=row[0])
                        instance.server = server
                        self.servers[server.name] = server
                    else:
                        self.log.warning(f"There is no server bound to instance {instance.name}!")
                except Exception as ex:
                    self.log.exception(ex)

    async def send_init(self, server: Server):
        timeout = 120 if self.node.locals.get('slow_system', False) else 60
        _, dcs_version = await self.node.get_dcs_branch_and_version()
        await self.send_to_node_sync({
            "command": "rpc",
            "service": "ServiceBus",
            "method": "init_remote_server",
            "params": {
                "server_name": server.name,
                "public_ip": self.node.locals.get('public_ip', await utils.get_public_ip()),
                "status": server.status.value,
                "instance": server.instance.name,
                "home": server.instance.home,
                "settings": server.settings,
                "options": server.options,
                "channels": server.locals.get('channels', {}),
                "node": self.node.name,
                "dcs_version": dcs_version,
                "maintenance": server.maintenance
            }
        }, timeout=timeout)

    async def register_local_servers(self):
        # we only run once
        if self._lock.locked():
            return
        async with self._lock:
            timeout = (10 * len(self.servers)) if self.node.locals.get('slow_system', False) else (5 * len(self.servers))
            local_servers = [x for x in self.servers.values() if not x.is_remote]
            if local_servers:
                self.log.info('- Searching for running local DCS servers (this might take a bit) ...')
            else:
                return
            calls: dict[str, Any] = dict()
            for server in local_servers:
                if not self.master:
                    await self.send_init(server)
                if server.maintenance:
                    self.log.warning(f'  => Maintenance mode enabled for Server {server.name}')
                if utils.is_open('127.0.0.1', server.instance.dcs_port):
                    calls[server.name] = asyncio.create_task(
                        server.send_to_dcs_sync({"command": "registerDCSServer"}, timeout)
                    )
                else:
                    server.status = Status.SHUTDOWN
            ret = await asyncio.gather(*(calls.values()), return_exceptions=True)
            num = 0
            for i, name in enumerate(calls.keys()):
                server = self.servers[name]
                if isinstance(ret[i], TimeoutError) or isinstance(ret[i], asyncio.TimeoutError):
                    self.log.debug(f'  => Timeout while trying to contact DCS server "{server.name}".')
                    server.status = Status.SHUTDOWN
                elif isinstance(ret[i], Exception):
                    self.log.error("  => Exception during registering: " + str(ret[i]), exc_info=True)
                else:
                    num += 1
            if num == 0:
                self.log.info('- No running local servers found.')

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
        if not server.process:
            server.process = utils.find_process("DCS_server.exe|DCS.exe", server.instance.name)
            if not server.process:
                self.log.warning("Could not find active DCS process. Please check, if you have started DCS with -w!")
        server.dcs_version = data['dcs_version']
        # if we are an agent, initialize the server
        if not self.master:
            if 'current_mission' in data:
                if not server.current_mission:
                    server.current_mission = DataObjectFactory().new(
                        Mission, node=server.node, server=server, map=data['current_map'],
                        name=data['current_mission'])
                server.current_mission.update(data)

        # validate server ports
        dcs_ports: dict[int, str] = dict()
        webgui_ports: dict[int, str] = dict()
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
        # check for DSMC
        if server.status == Status.RUNNING and data.get('dsmc_enabled', False) and 'DSMC' not in server.extensions:
            self.log.warning("  => DSMC is enabled for this server but DSMC extension is not loaded!")
            self.log.warning("     You need to configure DSMC on your own to prevent issues with the mission list.")

        # update the database and check for server name changes
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    'SELECT server_name FROM instances WHERE node=%s AND port=%s AND server_name IS NOT NULL',
                    (self.node.name, data['port'])
                )
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

    def rename_server(self, server: Server, new_name: str):
        self.servers[new_name] = server
        if server.name in self.servers:
            del self.servers[server.name]
        if server.name in self.udp_server.message_queue:
            self.udp_server.message_queue[server.name].put({})
            self.udp_server.message_queue[new_name] = Queue()
            self.executor.submit(self.udp_server.process, new_name)

    async def ban(self, ucid: str, banned_by: str, reason: str = 'n/a', days: Optional[int] = None):
        if days:
            until = datetime.now(tz=timezone.utc) + timedelta(days=days)
            until_str = until.strftime('%Y-%m-%d %H:%M') + ' (UTC)'
        else:
            until = datetime(year=9999, month=12, day=31)
            until_str = 'never'
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO bans (ucid, banned_by, reason, banned_until) 
                    VALUES (%s, %s, %s, %s) 
                    ON CONFLICT DO NOTHING
                """, (ucid, banned_by, reason, until.replace(tzinfo=None)))
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

    async def unban(self, ucid: str):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM bans WHERE ucid = %s", (ucid, ))
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

    async def bans(self) -> list[dict]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT b.ucid, COALESCE(p.discord_id, -1) AS discord_id, p.name, b.banned_by, b.reason, 
                           b.banned_until 
                    FROM bans b LEFT OUTER JOIN players p on b.ucid = p.ucid 
                    WHERE b.banned_until >= (now() AT TIME ZONE 'utc')
                """)
                return [x async for x in cursor]

    async def is_banned(self, ucid: str) -> Optional[dict]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM bans WHERE ucid = %s AND banned_until >= (now() AT TIME ZONE 'utc')
                """, (ucid, ))
                return await cursor.fetchone()

    def init_remote_server(self, server_name: str, public_ip: str, status: str, instance: str, home: str,
                           settings: dict, options: dict, node: str, channels: dict, dcs_version: str,
                           maintenance: bool) -> None:
        from core import NodeProxy

        try:
            server: ServerProxy = cast(ServerProxy, self.servers.get(server_name))
            if not server or not server.is_remote:
                node = NodeProxy(self.node, node, public_ip, self.node.config_dir)
                server = ServerProxy(
                    node=node,
                    port=-1,
                    name=server_name
                )
                _instance = next(x for x in node.instances if x.name == instance)
                _instance.home = home
                server.instance = _instance
                self.servers[server_name] = server
                server.settings = settings
                server.options = options
                server.dcs_version = dcs_version
                server.maintenance = maintenance
                # to support remote channel configs (for remote testing)
                if not server.locals.get('channels'):
                    server.locals['channels'] = channels
                # add eventlistener queue
                if server.name not in self.udp_server.message_queue:
                    self.udp_server.message_queue[server.name] = Queue()
                    self.executor.submit(self.udp_server.process, server.name)
                self.log.info(f"  => DCS-Server \"{server.name}\" from Node {server.node.name} registered.")
            else:
                # IP might have changed, so update it
                cast(NodeProxy, server.node).public_ip = public_ip
            server.status = Status(status)
        except Exception as ex:
            self.log.exception(str(ex), exc_info=True)

    def send_to_node(self, data: dict, *, node: Optional[Union[Node, str]] = None):
        if isinstance(node, Node):
            node = node.name
        if self.master:
            if node and node != self.node.name:
                self.log.debug('MASTER->{}: {}'.format(node, json.dumps(data)))
                with self.pool.connection() as conn:
                    with conn.transaction():
                        if data.get('command', '') == 'rpc':
                            self.intercom_channel.publish(conn, {
                                'guild_id': self.node.guild_id, 'node': node, 'data': Json(data)
                            })
                        else:
                            self.broadcasts_channel.publish(conn, {
                                'guild_id': self.node.guild_id, 'node': node, 'data': Json(data)
                            })
            elif data.get('command', '') != 'rpc':
                server_name = data['server_name']
                if server_name not in self.udp_server.message_queue:
                    self.log.debug(f"Message received for unregistered server {server_name}, ignoring.")
                else:
                    self.log.debug('{}->HOST: {}'.format(server_name, json.dumps(data)))
                    self.udp_server.message_queue[server_name].put(data)
            else:
                asyncio.create_task(self.handle_rpc(data))
        else:
            data['node'] = self.node.name
            with self.pool.connection() as conn:
                with conn.transaction():
                    if data.get('command', '') == 'rpc':
                        self.intercom_channel.publish(conn, {
                            'guild_id': self.node.guild_id, 'node': 'Master', 'data': Json(data)
                        })
                    else:
                        self.broadcasts_channel.publish(conn, {
                            'guild_id': self.node.guild_id, 'node': 'Master', 'data': Json(data)
                        })
            self.log.debug(f"{self.node.name}->MASTER: {json.dumps(data)}")

    async def send_to_node_sync(self, message: dict, timeout: Optional[int] = 30.0, *,
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
        # handle synchronous responses
        if data.get('channel', '').startswith('sync-') and 'return' in data:
            self.log.debug(f"{data.get('node', 'MASTER')}->{self.node.name}: {json.dumps(data)}")
            if data['channel'] in self.listeners:
                f = self.listeners[data['channel']]
                if not f.done():
                    if 'exception' in data:
                        try:
                            ex = utils.str_to_class(data['exception']['class'])(*data['exception']['args'],
                                                                                **data['exception']['kwargs'])
                        except Exception:
                            ex = PermissionError(data['exception']['args'])
                        self.loop.call_soon_threadsafe(f.set_exception, ex)
                    else:
                        # TODO: change to data['return']
                        self.loop.call_soon_threadsafe(f.set_result, data)
            return
        self.log.debug(f"RPC: {json.dumps(data)}")
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
            self.log.debug('RPC command received for unknown object/service.')
            return
        try:
            rc = await self.rpc(obj, data)
            if data.get('channel', '').startswith('sync-'):
                if isinstance(rc, Enum):
                    rc = rc.value
                self.send_to_node({
                    "command": "rpc",
                    "method": data['method'],
                    "channel": data['channel'],
                    "return": rc if rc is not None else ''
                }, node=data.get('node'))
        except Exception as ex:
            if isinstance(ex, TimeoutError) or isinstance(ex, asyncio.TimeoutError):
                self.log.warning(f"Timeout error during an RPC call: {data['method']}!", exc_info=True)
            if data.get('channel', '').startswith('sync-'):
                self.send_to_node({
                    "command": "rpc",
                    "method": data['method'],
                    "channel": data['channel'],
                    "return": '',
                    "exception": {
                        "class": f"{ex.__class__.__module__}.{ex.__class__.__name__}",
                        "args": ex.args,
                        "kwargs": getattr(ex, 'kwargs', {})
                    }
                }, node=data.get('node'))
            else:
                self.log.exception(ex)

    async def handle_broadcast_event(self, data: dict) -> None:
        if self.master:
            await self.handle_master(data)
        else:
            await self.handle_agent(data)

    async def handle_master(self, data: dict):
        if 'node' not in data:
            self.log.error(f"Event without Node: {json.dumps(data)}")
            self.log.error(f"Master is {self.master}")
            return
        self.log.debug(f"{data['node']}->MASTER: {json.dumps(data)}")
        server_name = data['server_name']
        if server_name not in self.udp_server.message_queue:
            self.log.debug(f"Intercom: message ignored, no server {server_name} registered.")
            return
        # support sync responses though intercom
        if 'channel' in data and data['channel'].startswith('sync-'):
            server: Server = self.servers.get(server_name)
            if not server:
                self.log.warning(f'Message received for unregistered server {server_name}, ignoring.')
                return
            f = server.listeners.get(data['channel'])
            if f and not f.done():
                self.loop.call_soon_threadsafe(f.set_result, data)
            if data['command'] not in ['registerDCSServer', 'getMissionUpdate']:
                return
        self.udp_server.message_queue[server_name].put(data)

    async def handle_agent(self, data: dict):
        self.log.debug(f"MASTER->{self.node.name}: {json.dumps(data)}")
        server_name = data['server_name']
        if server_name not in self.servers:
            self.log.warning(
                f"Command {data['command']} for unknown server {server_name} received, ignoring")
        else:
            server: Server = self.servers[server_name]
            server.send_to_dcs(data)

    async def rpc(self, obj: object, data: dict) -> Optional[dict]:
        if 'method' in data:
            func = attrgetter(data.get('method'))(obj)
            if not func:
                return
            kwargs = deepcopy(data.get('params', {}))
            parameters = inspect.signature(func).parameters
            # servers will be passed by name
            if kwargs.get('server') and parameters.get('server').annotation != 'str':
                kwargs['server'] = self.servers.get(kwargs['server'])
            if kwargs.get('instance') and parameters.get('instance').annotation != 'str':
                kwargs['instance'] = next(x for x in self.node.instances if x.name == kwargs['instance'])
            if self.master:
                if kwargs.get('member'):
                    kwargs['member'] = self.bot.guilds[0].get_member(int(kwargs['member'][2:-1]))
                if kwargs.get('user') and kwargs['user'].startswith('<@'):
                    kwargs['user'] = self.bot.guilds[0].get_member(int(kwargs['user'][2:-1]))
            if asyncio.iscoroutinefunction(func):
                rc = await func(**kwargs) if kwargs else await func()
            else:
                rc = func(**kwargs) if kwargs else func()
            return rc
        elif 'params' in data:
            for key, value in data['params'].items():
                setattr(obj, key, value)

    async def propagate_event(self, command: str, data: dict, server: Optional[Server] = None):
        tasks = [
            asyncio.create_task(listener.processEvent(command, server, deepcopy(data)))
            for listener in self.eventListeners
            if listener.has_event(command)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def start_udp_listener(self):
        class RequestHandler(BaseRequestHandler):

            def handle(derived):
                if not derived.request or not derived.request[0]:
                    self.log.warning(f"Empty request received on port {self.node.listen_port} - ignoring.")
                    return
                data: dict = json.loads(derived.request[0].strip())
                # ignore messages not containing server names
                if 'server_name' not in data:
                    self.log.warning('Message without server_name received: {}'.format(data))
                    return
                server_name = data['server_name']
                self.log.debug('{}->HOST: {}'.format(server_name, json.dumps(data)))
                server = self.servers.get(server_name)
                if not server:
                    self.log.debug(
                        f"Command {data['command']} received for unregistered server {server_name}, ignoring.")
                    return
                server.last_seen = datetime.now(timezone.utc)
                if 'channel' in data and data['channel'].startswith('sync-'):
                    if data['channel'] in server.listeners:
                        f = server.listeners.get(data['channel'])
                        if f and not f.done():
                            self.loop.call_soon_threadsafe(f.set_result, data)
                        if data['command'] not in ['registerDCSServer', 'getMissionUpdate']:
                            return
                udp_server: MyThreadingUDPServer = cast(MyThreadingUDPServer, derived.server)
                if server.name not in udp_server.message_queue:
                    udp_server.message_queue[server.name] = Queue()
                    self.executor.submit(udp_server.process, server.name)
                udp_server.message_queue[server.name].put(data)

        class MyThreadingUDPServer(ThreadingUDPServer):
            def __init__(derived, server_address: tuple[str, int], request_handler: Callable[..., BaseRequestHandler]):
                try:
                    # enable reuse, in case the restart was too fast and the port was still in TIME_WAIT
                    MyThreadingUDPServer.allow_reuse_address = True
                    MyThreadingUDPServer.max_packet_size = 65504
                    derived.message_queue: dict[str, Queue[dict]] = {}
                    super().__init__(server_address, request_handler)
                except Exception as ex:
                    self.log.exception(ex)

            def process(derived, server_name: str):
                try:
                    timeout = 120.0 if self.node.locals.get('slow_system', False) else 60.0
                    data: dict = derived.message_queue[server_name].get()
                    while data:
                        server: Server = self.servers.get(server_name)
                        if not server:
                            return
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
                                    f"Command {command} received for unregistered server {server.name}, ignoring.")
                                continue
                            if self.master:
                                futures = [
                                    asyncio.run_coroutine_threadsafe(
                                        listener.processEvent(command, server, deepcopy(data)), self.loop
                                    )
                                    for listener in self.eventListeners
                                    if listener.has_event(command)
                                ]
                                done, not_done = concurrent.futures.wait(
                                    futures, timeout=timeout if command != 'registerDCSServer' else None)

                                if not_done:
                                    # Logging the commands that could not be processed due to timeout
                                    self.log.warning(f"Command {data} was not processed due to a timeout.")
                                    listeners = [x for x in self.eventListeners if x.has_event(command)]
                                    for future in not_done:
                                        pos = futures.index(future)
                                        self.log.debug(f"Not processed: {listeners[pos].plugin_name}")
                                        future.cancel()
                            else:
                                self.send_to_node(data)
                        except Exception as ex:
                            self.log.exception(ex)
                        finally:
                            derived.message_queue[server.name].task_done()
                            data = derived.message_queue[server.name].get()
                finally:
                    self.log.debug(f"Listener for server {server_name} stopped.")
                    del derived.message_queue[server_name]

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
