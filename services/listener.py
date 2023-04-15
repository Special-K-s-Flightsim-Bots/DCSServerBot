import asyncio
import json
import os
import platform
import psycopg
from _operator import attrgetter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from core import Server, DataObjectFactory, utils, Status, ServerImpl, Autoexec
from core.services.base import Service
from core.services.registry import ServiceRegistry
from discord.ext import tasks
from psycopg.types.json import Json
from queue import Queue
from shutil import copytree
from socketserver import BaseRequestHandler, ThreadingUDPServer
from typing import Tuple, Callable, Optional


@ServiceRegistry.register("EventListener")
class EventListenerService(Service):

    def __init__(self, main):
        super().__init__(main)
        self.version = self.config['BOT']['VERSION']
        self.servers: dict[str, ServerImpl] = dict()
        self.udp_server = None
        self.executor = None
        plugins: str = self.config['BOT']['PLUGINS']
        if 'OPT_PLUGINS' in self.config['BOT']:
            plugins += ', ' + self.config['BOT']['OPT_PLUGINS']
        self.plugins: [str] = [p.strip() for p in list(dict.fromkeys(plugins.split(',')))]
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
                self.log.info(f"- Installing Plugins into {installation} ...")
                for plugin_name in self.plugins:
                    source_path = f'./plugins/{plugin_name}/lua'
                    if os.path.exists(source_path):
                        target_path = os.path.expandvars(self.config[installation]['DCS_HOME'] +
                                                         f'\\Scripts\\net\\DCSServerBot\\{plugin_name}\\')
                        copytree(source_path, target_path, dirs_exist_ok=True)
                        self.log.info(f'  => Plugin {plugin_name.capitalize()} installed.')

    async def register_servers(self):
        self.log.info('- Searching for running DCS servers (this might take a bit) ...')
        servers = list(self.servers.values())
        timeout = (5 * len(self.servers)) if self.config.getboolean('BOT', 'SLOW_SYSTEM') else (3 * len(self.servers))
        ret = await asyncio.gather(
            *[server.sendtoDCSSync({"command": "registerDCSServer"}, timeout) for server in servers],
            return_exceptions=True
        )
        num = 0
        for i, server in enumerate(self.servers.values()):
            if isinstance(ret[i], asyncio.TimeoutError):
                self.log.debug(f'  => Timeout while trying to contact DCS server "{server.name}".')
                server.status = Status.SHUTDOWN
            elif isinstance(ret[i], Exception):
                self.log.exception(ret[i])
            else:
                self.register_server(ret[i])
                self.log.info(f'  => Running DCS server "{servers[i].name}" registered.')
                num += 1
            self.log.debug(f"Registering server {server.name} on Master node ...")
            self.sendtoMaster({
                "command": "init",
                "server_name": server.name,
                "status": server.status.name,
                "installation": server.installation,
                "settings": server.settings,
                "options": server.options
            })
        if num == 0:
            self.log.info('- No running servers found.')
        self.log.info('DCSServerBot Agent started.')

    def register_server(self, data: dict) -> bool:
        installations = utils.findDCSInstallations(data['server_name'])
        if len(installations) == 0:
            self.log.error(f"No server {data['server_name']} found in any serverSettings.lua.\n"
                           f"Please check your server configurations!")
            return False
        _, installation = installations[0]
        if installation not in self.config:
            self.log.error(f"No section found for server {data['server_name']} in your dcsserverbot.ini.\n"
                           f"Please add a configuration for it!")
            return False
        self.log.debug(f"  => Registering DCS-Server \"{data['server_name']}\"")
        # check for protocol incompatibilities
        if data['hook_version'] != self.version:
            self.log.error('Server \"{}\" has wrong Hook version installed. Please update lua files and restart '
                           'server. Registration ignored.'.format(data['server_name']))
            return False
        # register the server in the internal datastructures
        if data['server_name'] in self.servers:
            server: Server = self.servers[data['server_name']]
        else:
            # a new server is to be registered
            server = self.servers[data['server_name']] = \
                DataObjectFactory().new(Server.__name__, bot=self, name=data['server_name'],
                                        installation=installation, host=self.config[installation]['DCS_HOST'],
                                        port=self.config[installation]['DCS_PORT'])
        # set the PID
        for exe in ['DCS_server.exe', 'DCS.exe']:
            server.process = utils.find_process(exe, server.installation)
            if server.process:
                break
        server.dcs_version = data['dcs_version']
        if server.status != Status.LOADING:
            server.status = Status.STOPPED
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
            autoexec = Autoexec(bot=self, installation=server.installation)
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
                else:
                    self.log.warning(f'Server "{server.name}" shares its webrtc_port port with server '
                                     f'"{webrtc_ports[webrtc_port]}", but voice chat is disabled.')
            else:
                webrtc_ports[webrtc_port] = server.name

        # update the database and check for server name changes
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT server_name FROM servers WHERE agent_host=%s AND host=%s AND port=%s',
                               (platform.node(), data['host'], data['port']))
                if cursor.rowcount == 1:
                    server_name = cursor.fetchone()[0]
                    if server_name != data['server_name']:
                        if len(utils.findDCSInstallations(server_name)) == 0:
                            self.log.info(f"Auto-renaming server \"{server_name}\" to \"{data['server_name']}\"")
                            server.rename(data['server_name'])
                            if server_name in self.servers:
                                del self.servers[server_name]
                        else:
                            self.log.warning(
                                f"Registration of server \"{data['server_name']}\" aborted due to UDP port conflict.")
                            del self.servers[data['server_name']]
                            return False
        self.log.debug(f"Server {server.name} initialized")
        return True

    def sendtoMaster(self, data: dict):
        data['agent'] = self.agent
        with self.pool.connection() as conn:
            with conn.pipeline():
                with conn.transaction():
                    conn.execute("INSERT INTO intercom (agent, data) VALUES ('Master', %s)", (Json(data), ))
                    self.log.debug(f"HOST->MASTER: {json.dumps(data)}")

    @tasks.loop(seconds=1)
    async def intercom(self):
        with self.pool.connection() as conn:
            with conn.pipeline():
                with conn.transaction():
                    with closing(conn.cursor()) as cursor:
                        for row in cursor.execute("SELECT id, data FROM intercom WHERE agent = %s",
                                                  (platform.node(), )).fetchall():
                            data = row[1]
                            self.log.debug(f'### SIZE: {len(data)}')
                            server_name = data['server_name']
                            self.log.debug(f"MASTER->HOST: {json.dumps(data)}")
                            command = data['command']
                            if server_name not in self.servers:
                                self.log.warning(
                                    f"Command {command} for unknown server {server_name} received, ignoring")
                            else:
                                server: ServerImpl = self.servers[server_name]
                                if command == 'rpc':
                                    if data.get('object') == 'Server':
                                        rc = await self.rpc(server, data)
                                        if rc:
                                            data['return'] = rc
                                            self.sendtoMaster(data)
                                    else:
                                        self.log.warning('RPC command received for unknown object.')
                                else:
                                    server.sendtoDCS(data)
                            cursor.execute("DELETE FROM intercom WHERE id = %s", (row[0], ))

    @staticmethod
    async def rpc(server: ServerImpl, data: dict) -> Optional[dict]:
        func = attrgetter(data.get('method'))(server)
        if not func:
            return
        kwargs = data.get('params', {})
        if asyncio.iscoroutinefunction(func):
            rc = await func(**kwargs) if kwargs else await func()
        else:
            rc = func(**kwargs) if kwargs else func()
        return rc

    async def start_udp_listener(self):
        class RequestHandler(BaseRequestHandler):

            def handle(s):
                data = json.loads(s.request[0].strip())
                # ignore messages not containing server names
                if 'server_name' not in data:
                    self.log.warning('Message without server_name received: {}'.format(data))
                    return
                server_name = data['server_name']
                command = data['command']
                server: ServerImpl = self.servers.get(server_name)
                if not server:
                    self.log.warning(f"Command {command} for unknown server {server_name} received, ignoring.")
                    return
                self.log.debug('{}->HOST: {}'.format(server.name, json.dumps(data)))
                if server.name not in s.server.message_queue:
                    s.server.message_queue[server.name] = Queue()
                    s.server.executor.submit(s.process, server)
                channel = data.get('channel')
                if channel and channel in server.listeners.keys():
                    f = server.listeners[channel]
                    if not f.done():
                        self.loop.call_soon_threadsafe(f.set_result, data)
#                        if command != 'registerDCSServer':
                    return
                s.server.message_queue[server.name].put(data)

            def process(s, server: Server):
                data = s.server.message_queue[server.name].get()
                while len(data):
                    try:
                        command = data['command']
                        if command == 'registerDCSServer':
                            if not self.register_server(data):
                                self.log.error(f"Error while registering server {server.name}.")
                                return
                            self.log.info(f"Registering server {server.name} on Master node ...")
                        elif server.status == Status.UNREGISTERED:
                            self.log.debug(
                                f"Command {command} for unregistered server {server.name} received, ignoring.")
                            continue
                        self.sendtoMaster(data)
                    except Exception as ex:
                        self.log.exception(ex)
                    finally:
                        s.server.message_queue[server.name].task_done()
                        data = s.server.message_queue[server.name].get()

        class MyThreadingUDPServer(ThreadingUDPServer):
            def __init__(self, server_address: Tuple[str, int], request_handler: Callable[..., BaseRequestHandler],
                         listener: EventListenerService):
                self.log = listener.log
                self.executor = listener.executor
                try:
                    # enable reuse, in case the restart was too fast and the port was still in TIME_WAIT
                    MyThreadingUDPServer.allow_reuse_address = True
                    MyThreadingUDPServer.max_packet_size = 65504
                    self.message_queue: dict[str, Queue[str]] = {}
                    super().__init__(server_address, request_handler)
                except Exception as ex:
                    self.log.exception(ex)

            def shutdown(self) -> None:
                super().shutdown()
                try:
                    for server_name, queue in self.message_queue.items():
                        if not queue.empty():
                            queue.join()
                        queue.put('')
                except Exception as ex:
                    self.log.exception(ex)

        host = self.config['BOT']['HOST']
        port = int(self.config['BOT']['PORT'])
        self.udp_server = MyThreadingUDPServer((host, port), RequestHandler, self)
        self.executor.submit(self.udp_server.serve_forever)
        self.log.debug('- Listener started on interface {} port {} accepting commands.'.format(host, port))
