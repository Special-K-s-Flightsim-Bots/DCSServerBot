from __future__ import annotations
import asyncio
import discord
import inspect
import json
import socket
import time
import uuid

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from core import Server, Mission, Node, Status, utils, Instance, FatalException, Port, PortType
from core.autoexec import Autoexec
from core.data.dataobject import DataObjectFactory
from core.data.impl.instanceimpl import InstanceImpl
from core.data.impl.serverimpl import ServerImpl
from core.data.proxy.serverproxy import ServerProxy
from core.process import ProcessManager
from core.pubsub import PubSub
from core.services.base import Service
from core.services.registry import ServiceRegistry
from core.utils import ThreadSafeDict
from core.utils.helper import default_serializer
from core.utils.performance import PerformanceLog
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import reduce
from psycopg.rows import dict_row
from psycopg.types.json import Json
from typing import cast, Any, TYPE_CHECKING, Callable

__all__ = [
    "ServiceBus"
]

if TYPE_CHECKING:
    from core import EventListener
    from ..bot.dcsserverbot import DCSServerBot

# these synchronous commands will be passed through to the event handler in any case
PASS_THROUGH_COMMANDS = ['registerDCSServer', 'getMissionUpdate']


@ServiceRegistry.register()
class ServiceBus(Service):

    def __init__(self, node):
        super().__init__(node)
        self.bot: DCSServerBot | None = None
        self.version = self.node.bot_version
        self.listeners: dict[str, asyncio.Future] = {}
        self.eventListeners: set[EventListener] = set()
        self.servers: dict[str, Server] = ThreadSafeDict()
        self.init_servers()
        self.udp_server = None
        self.executor = None

        if 'DCS' in self.locals and self.node.locals['DCS'].get('desanitize', True):
            if not self.node.locals['DCS'].get('cloud', False) or self.master:
                utils.desanitize(self)
        self.loop = asyncio.get_event_loop()

        cpool_url, lpool_url = self.node.get_database_urls()
        self.intercom_channel = PubSub(self.node, 'intercom', cpool_url, self.handle_rpc)
        self.broadcasts_channel = PubSub(self.node, 'broadcasts', lpool_url, self.handle_broadcast_event)
        self._lock = asyncio.Lock()

    async def start(self):
        await super().start()
        try:
            # Start the DCS listener
            self.executor = ThreadPoolExecutor(thread_name_prefix='ServiceBus',
                                               max_workers=100 if self.master else 20)
            await self.start_udp_listener()

            # clean up the intercom and broadcast channels
            await self.intercom_channel.clear()
            await self.broadcasts_channel.clear()
            # clean up the files
            async with self.node.cpool.connection() as conn:
                await conn.execute("""
                    DELETE FROM files 
                    WHERE guild_id = %s AND created < ((now() AT TIME ZONE 'utc') - interval '300 seconds')
                """, (self.node.guild_id, ))

            # subscribe to the intercom and broadcast channels
            asyncio.create_task(self.intercom_channel.subscribe())
            asyncio.create_task(self.broadcasts_channel.subscribe())
            # check master
            await self.switch()

        except Exception as ex:
            # we can't run without the servicebus, so better restart
            raise FatalException(repr(ex)) from ex

    async def switch(self):
        from ..bot.service import BotService

        if self.master:
            self.bot = ServiceRegistry.get(BotService).bot
            while not self.bot:
                await asyncio.sleep(1)
                self.bot = ServiceRegistry.get(BotService).bot
            await self.bot.wait_until_ready()
            await self.register_local_servers()
            for node in await self.node.get_active_nodes():
                await self.send_to_node({
                    "command": "rpc",
                    "service": self.__class__.__name__,
                    "method": "switch"
                }, node=node)
        else:
            await self.send_to_node({
                "command": "rpc",
                "service": self.__class__.__name__,
                "method": "register_remote_node",
                "params": {
                    "name": self.node.name,
                    "public_ip": self.node.public_ip,
                    "dcs_version": self.node.dcs_version
                }
            })

    async def stop(self):
        if self.udp_server:
            self.log.debug("- Processing unprocessed messages ...")
            self.udp_server.transport.close()
            self.log.debug("- All messages processed.")
        await self.broadcasts_channel.close()
        self.log.debug('- Listener stopped.')
        if self.executor:
            self.executor.shutdown(wait=True)
            self.log.debug('- Executor stopped.')
        if not self.master:
            await self.send_to_node({
                "command": "rpc",
                "service": self.__class__.__name__,
                "method": "unregister_remote_node",
                "params": {
                    "node": self.node.name
                }
            })
            self.log.debug('- Unregistered from Master node.')
        await self.intercom_channel.close()
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
        self.eventListeners.add(listener)

    def unregister_eventListener(self, listener: EventListener):
        self.eventListeners.discard(listener)
        self.log.debug(f'  - EventListener {type(listener).__name__} unregistered.')

    def init_servers(self):
        for instance in self.node.instances.values():
            try:
                if instance.server_name:
                    server: ServerImpl = DataObjectFactory().new(
                        ServerImpl, node=self.node, port=instance.bot_port, name=instance.server_name, bus=self)
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
            "service": self.__class__.__name__,
            "method": "init_remote_server",
            "params": {
                "server_name": server.name,
                "status": server.status.value,
                "instance": server.instance.name,
                "home": server.instance.home,
                "settings": server.settings,
                "options": server.options,
                "channels": server.locals.get('channels', {}),
                "node": self.node.name,
                "dcs_port": int(server.instance.dcs_port),
                "webgui_port": int(server.instance.webgui_port),
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
                self.log.info('- Searching for local DCS servers (this might take a bit) ...')
            else:
                return
            num = 0
            calls: dict[str, Any] = dict()
            for server in local_servers:
                try:
                    if not self.master:
                        await self.send_init(server)
                    if server.maintenance:
                        self.log.warning(f'  => Maintenance mode enabled for Server {server.name}')

                    if utils.is_open(server.instance.dcs_host, int(server.instance.webgui_port)):
                        calls[server.name] = asyncio.create_task(
                            server.send_to_dcs_sync({"command": "registerDCSServer"}, timeout)
                        )
                    else:
                        server.status = Status.SHUTDOWN
                        self.log.info(f"  => Local DCS-Server \"{server.name}\" registered as DOWN (no process).")
                        num += 1
                except Exception as ex:
                    self.log.error(f'Error while registering DCS-Server "{server.name}"', exc_info=ex)
            ret = await asyncio.gather(*(calls.values()), return_exceptions=True)
            for i, name in enumerate(calls.keys()):
                server = self.servers[name]
                if isinstance(ret[i], TimeoutError) or isinstance(ret[i], asyncio.TimeoutError):
                    self.log.debug(f'  => Timeout while trying to contact DCS server "{server.name}".')
                    server.status = Status.SHUTDOWN
                    self.log.info(f"  => Local DCS-Server \"{server.name}\" registered as DOWN (not responding).")
                    num += 1
                elif isinstance(ret[i], Exception):
                    self.log.error("  => Exception during registering: " + str(ret[i]), exc_info=ret[i])
                else:
                    self.log.info(f"  => Local DCS-Server \"{server.name}\" registered as UP.")
                    num += 1
            if not self.servers:
                self.log.warning('  => No local DCS servers configured!')
            else:
                self.log.info(f"- {num} local DCS servers registered.")

            # init profanity filter, if needed
            if not self.node.locals['DCS'].get('cloud', False) or self.master:
                if any(server.locals.get('profanity_filter', False) for server in local_servers):
                    utils.init_profanity_filter(self.node)

    async def register_remote_servers(self, node: Node):
        await self.send_to_node({
            "command": "rpc",
            "service": self.__class__.__name__,
            "method": "register_local_servers"
        }, node=node.name)
        self.log.info(f"- Remote node {node.name} registered.")

    async def register_remote_node(self, name: str, public_ip: str, dcs_version: str):
        from core import NodeProxy
        from ..bot.service import BotService

        # in case of a race condition during master takeovers, ignore this registration
        if name == self.node.name:
            return

        self.log.info(f"- Registering remote node {name} ...")
        node = NodeProxy(self.node, name, public_ip, dcs_version)
        self.node.all_nodes[node.name] = node
        while not self.bot or not ServiceRegistry.get(BotService):
            await asyncio.sleep(1)
            self.bot = ServiceRegistry.get(BotService).bot
        await self.bot.wait_until_ready()
        await self.register_remote_servers(node)

    async def unregister_remote_node(self, node: Node):
        # unregister event for a non-registered node received or for myself in case of a race condition, ignoring
        if not node or node.name == self.node.name:
            return
        self.log.info(f"- Unregistering remote node {node.name} and all its servers ...")
        for server_name, server in list(self.servers.items()):
            if server.is_remote and server.node == node:
                self.log.info(f"  => Remote DCS-server \"{server_name}\" unregistered.")
                server.status = Status.UNREGISTERED
                del self.servers[server_name]
        # we do not delete the node but set it to "None" to reactivate it later
        self.node.all_nodes[node.name] = None
        self.log.info(f"- Remote node {node.name} unregistered.")

    async def register_server(self, data: dict) -> bool:
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
            self.log.debug(f'  => No process of {server_name} found. Searching ...')
            server.process = await utils.find_process_async("DCS_server.exe|DCS.exe", server.instance.name)
            if not server.process:
                self.log.warning("Could not find active DCS process. Please check, if you have started DCS with -w!")
            else:
                ProcessManager().assign_process(
                    server.process,
                    min_cores=server.locals.get('auto_affinity', {}).get('min_cores', 1),
                    max_cores=server.locals.get('auto_affinity', {}).get('max_cores', 2),
                    quality=server.locals.get('auto_affinity', {}).get('quality', 3),
                    instance=server.instance.name
                )
                self.log.debug(f'  => Process of {server_name} found.')

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
        for s in self.servers.values():
            # only check ports of local servers
            if s.is_remote or s.status == Status.SHUTDOWN:
                continue
            dcs_port = int(s.settings.get('port', 10308))
            if dcs_port in dcs_ports:
                self.log.error(f'Server "{s.name}" shares its DCS port with server "{dcs_ports[dcs_port]}"!\n'
                               f'Registration aborted. Change it in your nodes.yaml!')
                return False
            else:
                dcs_ports[dcs_port] = s.name
            autoexec = Autoexec(cast(InstanceImpl, s.instance))
            webgui_port = autoexec.webgui_port or 8088
            if webgui_port in webgui_ports:
                self.log.error(f'Server "{s.name}" shares its webgui_port with server "{webgui_ports[webgui_port]}"!\n'
                               f'Registration aborted. Change it in your nodes.yaml!')
                return False
            else:
                webgui_ports[webgui_port] = s.name

        # check for DSMC
        if server.status == Status.RUNNING and data.get('dsmc_enabled', False) and 'DSMC' not in server.extensions:
            self.log.warning("  => DSMC is enabled for this server but DSMC extension is not loaded!")
            self.log.warning("     You need to configure DSMC on your own to prevent issues with the mission list.")

        # update the database and check for server name changes
        self.log.debug(f'  => Checking the database for server {server_name} ...')
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT server_name 
                FROM instances 
                WHERE node=%s AND port=%s AND server_name IS NOT NULL
            """, (self.node.name, data['port']))
            if cursor.rowcount == 1:
                _server_name = (await cursor.fetchone())[0]
                if _server_name != server_name:
                    if utils.findDCSInstances(_server_name) and not self.servers.get(_server_name):
                        self.log.info(f'Auto-renaming server "{_server_name}" to "{server_name}"')
                        await server.rename(server_name)
                    else:
                        self.log.warning(f'Registration of server "{server_name}" aborted due to conflict.')
                        self.servers.pop(server_name, None)
                        return False
        self.log.debug(f'  => Database for server {server_name} checked.')
        return True

    def rename_server(self, server: Server, new_name: str):
        self.servers[new_name] = server
        if server.name in self.servers:
            self.servers.pop(server.name, None)
        if server.name in self.udp_server.message_queue:
            self.udp_server.message_queue[server.name].put_nowait({})
            self.udp_server.message_queue[new_name] = asyncio.Queue()
            asyncio.create_task(self.udp_server.process_messages(new_name))

    async def ban(self, ucid: str, banned_by: str, reason: str = 'n/a', days: int | None = None):
        if days:
            until = datetime.now(tz=timezone.utc) + timedelta(days=days)
            until_str = until.strftime('%Y-%m-%d %H:%M') + ' (UTC)'
        else:
            until = datetime(year=9999, month=12, day=31)
            until_str = 'never'
        async with self.apool.connection() as conn:
            await conn.execute("""
                INSERT INTO bans (ucid, banned_by, reason, banned_until) 
                VALUES (%s, %s, %s, %s) 
                ON CONFLICT (ucid) DO UPDATE 
                SET banned_by = excluded.banned_by, reason = excluded.reason, 
                    banned_at = excluded.banned_at, banned_until = excluded.banned_until
            """, (ucid, banned_by, reason, until.replace(tzinfo=None)))
        for server in self.servers.values():
            if server.status not in [Status.PAUSED, Status.RUNNING, Status.STOPPED]:
                continue
            await server.send_to_dcs({
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
            await conn.execute("UPDATE bans SET banned_until = NOW() AT TIME ZONE 'UTC' WHERE ucid = %s", (ucid, ))
        for server in self.servers.values():
            if server.status not in [Status.PAUSED, Status.RUNNING, Status.STOPPED]:
                continue
            await server.send_to_dcs({
                "command": "unban",
                "ucid": ucid
            })
            player = server.get_player(ucid=ucid)
            if player:
                player.banned = False

    async def bans(self, *, expired: bool = False) -> list[dict]:
        if expired:
            where = ""
        else:
            where = "WHERE b.banned_until >= (now() AT TIME ZONE 'utc')"
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(f"""
                    SELECT b.ucid, COALESCE(p.discord_id, -1) AS discord_id, p.name, b.banned_by, b.reason, 
                           b.banned_at, b.banned_until 
                    FROM bans b LEFT OUTER JOIN players p on b.ucid = p.ucid 
                    {where}
                """)
                return [x async for x in cursor]

    async def is_banned(self, ucid: str) -> dict | None:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM bans WHERE ucid = %s AND banned_until >= (now() AT TIME ZONE 'utc')
                """, (ucid, ))
                return await cursor.fetchone()

    async def init_remote_server(self, server_name: str, status: str, instance: str, home: str,
                                 settings: dict, options: dict, node: Node, channels: dict, dcs_port: int,
                                 webgui_port: int, maintenance: bool) -> None:
        from core import InstanceProxy

        # init event for an unregistered remote node received or a race condition due to master switches, ignoring
        if not node or node == self.node:
            return
        try:
            server: ServerProxy = cast(ServerProxy, self.servers.get(server_name))
            if not server or not server.is_remote:
                server = ServerProxy(
                    node=node,
                    port=Port(-1, PortType.BOTH),
                    name=server_name,
                    bus=self
                )
                _instance = node.instances.get(instance)
                if not _instance:
                    # first time we see this instance, so register it
                    _instance = InstanceProxy(name=instance, node=node)
                    node.instances[instance] = _instance
                _instance.home = home
                server.instance = _instance
                server.instance.locals['dcs_port'] = dcs_port
                server.instance.locals['webgui_port'] = webgui_port
                self.servers[server_name] = server
            server.maintenance = maintenance
            server.status = Status(status)
            server.settings = settings
            server.options = options
            # to support remote channel configs (for remote testing)
            if not server.locals.get('channels'):
                server.locals['channels'] = channels
            # add eventlistener queue
            if server.name not in self.udp_server.message_queue:
                self.udp_server.message_queue[server.name] = asyncio.Queue()
                asyncio.create_task(self.udp_server.process_messages(server.name))
            self.log.info(f"  => Remote DCS-Server \"{server.name}\" registered.")
        except StopIteration:
            self.log.error(f"No configuration found for instance {instance} in config\\nodes.yaml")
        except Exception as ex:
            self.log.exception(str(ex), exc_info=True)

    async def send_to_node(self, data: dict, *, node: Node | str | None = None):
        if isinstance(node, Node):
            node = node.name
        if self.master:
            if node and node != self.node.name:
                self.log.debug('MASTER->{}: {}'.format(node, json.dumps(data, default=default_serializer)))
                if data.get('command', '') == 'rpc':
                    await self.intercom_channel.publish({
                        'guild_id': self.node.guild_id, 'node': node, 'data': Json(data)
                    })
                else:
                    await self.broadcasts_channel.publish({
                        'guild_id': self.node.guild_id, 'node': node, 'data': Json(data)
                    })
            elif data.get('command', '') != 'rpc':
                server_name = data['server_name']
                if server_name not in self.udp_server.message_queue:
                    self.log.debug(f"Message received for unregistered server {server_name}, ignoring.")
                else:
                    self.log.debug('{}->HOST: {}'.format(server_name, json.dumps(data, default=default_serializer)))
                    self.udp_server.message_queue[server_name].put_nowait(data)
            else:
                await self.handle_rpc(data)
        else:
            data['node'] = self.node.name
            if data.get('command', '') == 'rpc':
                await self.intercom_channel.publish({
                    'guild_id': self.node.guild_id, 'node': 'Master', 'data': Json(data)
                })
            else:
                await self.broadcasts_channel.publish({
                    'guild_id': self.node.guild_id, 'node': 'Master', 'data': Json(data)
                })
            self.log.debug(f"{self.node.name}->MASTER: {json.dumps(data, default=default_serializer)}")

    async def send_to_node_sync(self, message: dict, timeout: int | None = 30.0, *,
                                node: Node | str | None = None):
        cmd = message['command']
        if cmd == 'rpc':
            call = "RPC: {}.{}()".format(message.get('object', message.get('service')), message.get('method'))
        else:
            call = f"Remote: {cmd}()"
        with PerformanceLog(call):
            future = self.loop.create_future()
            token = 'sync-' + str(uuid.uuid4())
            message['channel'] = token
            self.listeners[token] = future
            try:
                await self.send_to_node(message, node=node)
                return await asyncio.wait_for(future, timeout)
            finally:
                # noinspection PyAsyncCall
                self.listeners.pop(token, None)

    def _serialize(self, obj: Any) -> Any:
        if hasattr(obj, 'to_dict'):
            return {'_class': f"{obj.__class__.__module__}.{obj.__class__.__name__}"} | obj.to_dict()
        elif isinstance(obj, Enum):
            return obj.value
        elif isinstance(obj, (Node, Server, Instance)):
            return obj.name
        elif isinstance(obj, dict):
            return {k: self._serialize(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._serialize(v) for v in obj]
        return obj

    def _deserialize(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            if '_class' in obj:
                cls_name = obj.pop('_class')
                cls = utils.str_to_class(cls_name)
                if cls:
                    if hasattr(cls, 'from_dict'):
                        return cls.from_dict(obj)
                    return cls(**obj)
            return {k: self._deserialize(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._deserialize(v) for v in obj]
        return obj

    async def handle_rpc(self, data: dict):
        # handle synchronous responses
        if 'return' in data and 'channel' in data and str(data['channel']).startswith('sync-'):
            self.log.debug(
                f"{data.get('node', 'MASTER')}->{self.node.name}: "
                f"{json.dumps(data, default=default_serializer)}"
            )
            f = self.listeners.get(data['channel'])
            if f and not f.done():
                if 'exception' in data:
                    ex = utils.rebuild_exception(data['exception'])
                    self.loop.call_soon_threadsafe(f.set_exception, ex)
                elif 'return' in data:
                    res = self._deserialize(data['return'])
                    self.loop.call_soon_threadsafe(utils.safe_set_result, f, res)
            return

        self.log.debug(f"RPC: {json.dumps(data, default=default_serializer)}")
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
                await self.send_to_node({
                    "command": "rpc",
                    "method": data['method'],
                    "channel": data['channel'],
                    "return": self._serialize(rc)
                }, node=data.get('node'))
        except Exception as ex:
            if not isinstance(ex, (TimeoutError, asyncio.TimeoutError, FileNotFoundError, ValueError,
                                   AttributeError, IndexError, discord.app_commands.CheckFailure)):
                self.log.exception(ex)
            elif isinstance(ex, (TimeoutError, asyncio.TimeoutError)):
                self.log.warning(f"Timeout error during an RPC call: {data['method']}!", exc_info=True)
            if data.get('channel', '').startswith('sync-'):
                await self.send_to_node({
                    "command": "rpc",
                    "method": data['method'],
                    "channel": data['channel'],
                    "return": None,
                    "exception": utils.exception_to_dict(ex)
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
            self.log.debug(f"Dropping stale event: {json.dumps(data, default=default_serializer)}")
            return
        self.log.debug(f"{data['node']}->MASTER: {json.dumps(data, default=default_serializer)}")
        server_name = data['server_name']
        if server_name not in self.udp_server.message_queue:
            self.log.debug(f"Broadcast: message ignored, server {server_name} not (yet) registered.")
            return

        # support sync responses though broadcast
        if 'channel' in data and str(data['channel']).startswith('sync-'):
            server: Server = self.servers.get(server_name)
            if not server:
                # we should never be here
                self.log.warning(f'Message received for unregistered server {server_name}, ignoring.')
                return

            f = server.listeners.get(data['channel'])
            if f and not f.done():
                self.loop.call_soon_threadsafe(utils.safe_set_result, f, data)

            if data['command'] not in PASS_THROUGH_COMMANDS:
                return

        self.udp_server.message_queue[server_name].put_nowait(data)

    async def handle_agent(self, data: dict):
        self.log.debug(f"MASTER->{self.node.name}: {json.dumps(data, default=default_serializer)}")
        server_name = data['server_name']
        server = self.servers.get(server_name)
        if not server:
            self.log.warning(
                f"Command {data['command']} for unknown server {server_name} received, ignoring")
            return

        await server.send_to_dcs(data)

    async def rpc(self, obj: object, data: dict) -> dict | None:
        if 'method' in data:
            method_name = data['method']
            func: Callable = reduce(lambda attr, part: getattr(attr, part, None), method_name.split('.'), obj)
            if not func:
                raise ValueError(f"Call to non-existing function {method_name}()")

            kwargs = data.get('params', {}).copy()

            func_signature = None
            if callable(func):
                func_signature = inspect.signature(func).parameters

            # check function signature
            if func_signature.get('kwargs', None):  # Handle functions with **kwargs
                valid_keys = set(func_signature.keys()) - {'kwargs'}
                explicit_kwargs = {key: value for key, value in kwargs.items() if key in valid_keys}
                extra_kwargs = {key: value for key, value in kwargs.items() if key not in valid_keys}
                kwargs = {**explicit_kwargs, **extra_kwargs}
            else:
                invalid_keys = set(kwargs.keys()) - set(func_signature.keys())
                if invalid_keys:
                    raise ValueError("RPC call {} onto non-matching function {}!".format(
                        "{}({})".format(method_name, ','.join(kwargs.keys())),
                        "{}({})".format(method_name, ','.join(func_signature.keys())))
                    )

            server_key = kwargs.get('server')
            if server_key and func_signature and func_signature['server'].annotation != 'str':
                kwargs['server'] = self.servers.get(server_key, None)

            instance_key = kwargs.get('instance')
            if instance_key and func_signature and func_signature['instance'].annotation != 'str':
                kwargs['instance'] = self.node.instances.get(instance_key)

            # Handle master-specific mappings
            if self.master:
                member_key = kwargs.get('member')
                if member_key:
                    try:
                        kwargs['member'] = self.bot.guilds[0].get_member(int(member_key.strip('<@>')))
                    except ValueError:
                        kwargs['member'] = None

                user_key = kwargs.get('user')
                if user_key and user_key.startswith('<@'):
                    try:
                        kwargs['user'] = self.bot.guilds[0].get_member(int(user_key.strip('<@>')))
                    except ValueError:
                        kwargs['user'] = None

                node_key = kwargs.get('node')
                if node_key and func_signature and func_signature['node'].annotation != 'str':
                    kwargs['node'] = self.node.all_nodes.get(node_key, None)

            # Log performance and execute the function
            with PerformanceLog(f"RPC: {obj.__class__.__name__}.{method_name}()"):
                if asyncio.iscoroutinefunction(func):
                    # If the function is asynchronous, await it directly
                    return await func(**kwargs)
                else:
                    # For synchronous functions, we need to execute them without to_thread() because of loop access
                    return func(**kwargs)

        elif 'params' in data:
            for key, value in data['params'].items():
                setattr(obj, key, value)

        return None

    async def propagate_event(self, command: str, data: dict, server: Server | None = None):
        tasks = [
            asyncio.create_task(listener.processEvent(command, server, deepcopy(data)))
            for listener in self.eventListeners
            if listener.has_event(command)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def start_udp_listener(self):
        MAX_WAIT = 30.0  # seconds – drop fragments that never finish
        CLEANUP_INTERVAL = 10.0  # seconds

        class FragmentBuffer:
            """
            Holds incomplete fragments keyed by (msg_id, server_name).
            """

            def __init__(derived):
                # key: (msg_id, port)
                # value: {"total": int, "parts": dict[int, bytes], "timestamp": float}
                derived._data: dict[tuple[str, int], dict] = {}
                derived._lock = asyncio.Lock()

            async def add_fragment(derived, msg_id: str, total: int, seq: int,
                                   payload: bytes, port: int) -> bool:
                """
                Add a fragment. Returns True when the whole message is now complete.
                """
                key = (msg_id, port)
                async with derived._lock:
                    buf = derived._data.setdefault(key, {
                        "total": total,
                        "parts": {},
                        "timestamp": time.time(),
                    })
                    # Sanity check – ignore out‑of‑range or duplicate fragments
                    if seq < 1 or seq > total:
                        self.log.debug("Ignoring out‑of‑range fragment %d/%d", seq, total)
                        return False
                    if seq in buf["parts"]:
                        self.log.debug("Duplicate fragment %d/%d", seq, total)
                        return False

                    buf["parts"][seq] = payload
                    buf["timestamp"] = time.time()

                    # Are we done yet?
                    if len(buf["parts"]) == total:
                        return True
                    return False

            async def get_full_message(derived, msg_id: str, port: int) -> bytes | None:
                """
                Retrieve and remove the fully assembled payload, or None if incomplete.
                """
                key = (msg_id, port)
                async with derived._lock:
                    buf = derived._data.get(key)
                    if not buf or len(buf["parts"]) != buf["total"]:
                        return None
                    # Reorder parts
                    parts = [buf["parts"][i] for i in range(1, buf["total"] + 1)]
                    full_payload = b"".join(parts)
                    # Clean up
                    del derived._data[key]
                    return full_payload

            async def cleanup(derived):
                """
                Drop any fragment set that has been idle longer than MAX_WAIT.
                """
                async with derived._lock:
                    now = time.time()
                    keys_to_remove = [
                        k for k, v in derived._data.items()
                        if now - v["timestamp"] > MAX_WAIT
                    ]
                    for k in keys_to_remove:
                        self.log.info("Fragment buffer timeout for %s", k)
                        del derived._data[k]

        class UDPProtocol(asyncio.DatagramProtocol):
            """
            The original protocol you already have, extended with reassembly.
            """

            def __init__(derived,):
                derived.transport = None
                derived.message_queue: dict[str, asyncio.Queue] = {}
                derived._frag_buf = FragmentBuffer()
                derived._cleanup_task = asyncio.create_task(derived._cleanup_loop())

            def connection_made(derived, transport):
                derived.transport = transport

            async def _cleanup_loop(derived):
                while True:
                    await asyncio.sleep(CLEANUP_INTERVAL)
                    await derived._frag_buf.cleanup()

            def datagram_received(derived, data: bytes, addr):
                """
                1. Try to split the header.
                2. If the header is present, store the fragment.
                3. If the message is complete, pass it to the normal handler.
                4. If not split, treat it as a normal JSON packet.
                """
                if not data:
                    self.log.warning(f"Empty request received from {addr} - ignoring.")
                    return

                if data[0:1] == b'\x01':
                    data = data[1:]

                    # Check for the split header
                    parts = data.split(b"|", 4)
                    if len(parts) == 5:
                        msg_id_b, port_b, total_b, seq_b, payload = parts
                        try:
                            msg_id = msg_id_b.decode("ascii")
                            port = int(port_b)
                            total = int(total_b)
                            seq = int(seq_b)
                        except Exception:
                            self.log.debug("Malformed header after magic byte – dropping packet")
                            return
                        else:
                            loop = asyncio.get_running_loop()
                            loop.create_task(derived._process_fragment(msg_id, port, total, seq, payload))
                            return  # early exit – we’ll process once all fragments are in

                # Normal (unsplit) JSON packet
                derived._handle_raw_payload(data)

            async def _process_fragment(derived, msg_id: str, port: int, total: int, seq: int, payload: bytes):
                """
                Called when a split fragment is received.  When the message
                is complete, the fully assembled payload is handed to
                _handle_raw_payload().
                """
                finished = await derived._frag_buf.add_fragment(
                    msg_id, total, seq, payload, port
                )

                if finished:
                    full_payload = await derived._frag_buf.get_full_message(msg_id, port)
                    if full_payload is None:
                        self.log.error("Buffer race – full payload vanished")
                        return
                    derived._handle_raw_payload(full_payload)

            def _handle_raw_payload(derived, payload: bytes):
                try:
                    msg_data = json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    self.log.warning(f"Invalid JSON {payload}")
                    return

                server_name = msg_data.get('server_name')
                if not server_name:
                    self.log.warning("Message without server_name received: %s", msg_data)
                    return

                self.log.debug(f"{server_name}->HOST: {json.dumps(msg_data, default=default_serializer)}")

                server = self.servers.get(server_name)
                if not server:
                    self.log.debug(
                        f"Command {msg_data.get('command')} received for unregistered server {server_name}, ignoring."
                    )
                    return

                server.last_seen = datetime.now(timezone.utc)

                # Handle sync channels
                if 'channel' in msg_data and str(msg_data['channel']).startswith('sync-'):
                    f = server.listeners.get(msg_data['channel'])
                    if f and not f.done():
                        self.loop.call_soon(utils.safe_set_result, f, msg_data)

                    if msg_data['command'] not in PASS_THROUGH_COMMANDS:
                        return

                # Create a queue if it doesn't exist and schedule processing
                if server_name not in derived.message_queue:
                    derived.message_queue[server_name] = asyncio.Queue()
                    asyncio.create_task(derived.process_messages(server_name))

                derived.message_queue[server_name].put_nowait(msg_data)

            async def process_messages(derived, server_name: str):
                try:
                    while True:
                        data = await derived.message_queue[server_name].get()

                        server: Server = self.servers.get(server_name)
                        if not server:
                            return

                        try:
                            command = data['command']
                            if command == 'registerDCSServer':
                                if not server.is_remote:
                                    if not await self.register_server(data):
                                        self.log.error(f"Error while registering server {server.name}.")
                                        return
                                    if not self.master:
                                        self.log.debug(f"Registering server {server.name} on Master node ...")
                            elif server.status == Status.UNREGISTERED and command not in ['getWeatherInfo',
                                                                                          'getAirbases',
                                                                                          'onSRSConnect']:
                                self.log.debug(
                                    f"Command {command} received for unregistered server {server.name}, ignoring.")
                                continue

                            if self.master:
                                tasks = []
                                for listener in self.eventListeners:
                                    if listener.has_event(command):
                                        task = asyncio.create_task(
                                            listener.processEvent(command, server, deepcopy(data))
                                        )
                                        tasks.append(task)

                                if tasks:
                                    try:
                                        results = await asyncio.gather(*tasks, return_exceptions=True)
                                        for listener, result in zip(self.eventListeners, results):
                                            if isinstance(result, Exception):
                                                self.log.error(
                                                    f"Exception in listener {listener.plugin_name}: {result!r}")
                                    except Exception as e:
                                        self.log.error(f"Catastrophic error in gather: {e!r}")
                            else:
                                await self.send_to_node(data)

                        except Exception as ex:
                            self.log.exception(ex)
                        finally:
                            derived.message_queue[server_name].task_done()

                finally:
                    self.log.debug(f"Listener for server {server_name} stopped.")
                    derived.message_queue.pop(server_name, None)

        # Start the UDP server
        host = self.node.listen_address
        port = self.node.listen_port.port

        class UDPSocket(socket.socket):
            def __init__(derived):
                super().__init__(socket.AF_INET, socket.SOCK_DGRAM)
                derived.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                # make them buffers huge
                derived.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 << 20)
                derived.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 << 20)

        sock = UDPSocket()
        sock.bind((host, port))

        transport, protocol = await self.loop.create_datagram_endpoint(
            UDPProtocol,
            sock=sock
        )

        self.udp_server = protocol
