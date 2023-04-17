from __future__ import annotations
import asyncio
import concurrent
import json
import os
import platform
import psycopg
import shutil
import sys
from _operator import attrgetter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from core import Server, DataObjectFactory, utils, Status, ServerImpl, Autoexec, ServerProxy, EventListener
from core.services.base import Service
from core.services.registry import ServiceRegistry
from discord.ext import tasks
from pathlib import Path
from psycopg.types.json import Json
from queue import Queue
from shutil import copytree
from socketserver import BaseRequestHandler, ThreadingUDPServer
from typing import Tuple, Callable, Optional, cast


@ServiceRegistry.register("ServiceBus")
class ServiceBus(Service):

    def __init__(self, main):
        super().__init__(main)
        self.version = self.config['BOT']['VERSION']
        self.eventListeners: list[EventListener] = []
        self.servers: dict[str, Server] = dict()
        self.udp_server = None
        self.executor = None
        if self.config['BOT'].getboolean('DESANITIZE'):
            utils.desanitize(self)
        self.install_plugins()
        self.install_luas()
        self.loop = asyncio.get_event_loop()
        self.intercom.add_exception_type(psycopg.DatabaseError)

    async def start(self):
        self.log.info('- ServiceBus starting ...')
        await super().start()
        # cleanup the intercom channels
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM intercom WHERE agent = %s", (self.agent, ))
        self.executor = ThreadPoolExecutor(thread_name_prefix='ServiceBus', max_workers=20)
        await self.start_udp_listener()
        await self.init_servers()
        self.intercom.start()
        if self.master:
            await ServiceRegistry.get("Bot").bot.wait_until_ready()
        await self.register_servers()
        self.log.info('- ServiceBus started.')

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
        self.log.debug('- Intercom stopped.')
        await super().stop()
        self.log.info("- ServiceBus stopped.")

    @property
    def master(self) -> bool:
        return self.main.master

    def register_eventListener(self, listener: EventListener):
        self.log.debug(f'- Registering EventListener {type(listener).__name__}')
        self.eventListeners.append(listener)

    def unregister_eventListener(self, listener: EventListener):
        self.eventListeners.remove(listener)
        self.log.debug(f'- EventListener {type(listener).__name__} unregistered.')

    def install_plugins(self):
        for file in Path('plugins').glob('*.zip'):
            path = file.__str__()
            self.log.info('- Unpacking plugin "{}" ...'.format(os.path.basename(path).replace('.zip', '')))
            shutil.unpack_archive(path, '{}'.format(path.replace('.zip', '')))
            os.remove(path)

    def install_luas(self):
        self.log.info('- Configure DCS installations ...')
        for server_name, installation in utils.findDCSInstallations():
            if installation not in self.config or self.config[installation]['DCS_HOME'] == 'REMOTE':
                continue
            self.log.info(f'  => {installation}')
            dcs_path = os.path.expandvars(self.config[installation]['DCS_HOME'] + '\\Scripts')
            if not os.path.exists(dcs_path):
                os.mkdir(dcs_path)
            ignore = None
            if os.path.exists(dcs_path + r'\net\DCSServerBot'):
                self.log.debug('  - Updating Hooks ...')
                shutil.rmtree(dcs_path + r'\net\DCSServerBot')
                ignore = shutil.ignore_patterns('DCSServerBotConfig.lua.tmpl')
            else:
                self.log.debug('  - Installing Hooks ...')
            shutil.copytree('./Scripts', dcs_path, dirs_exist_ok=True, ignore=ignore)
            try:
                with open(r'Scripts/net/DCSServerBot/DCSServerBotConfig.lua.tmpl', 'r') as template:
                    with open(dcs_path + r'\net\DCSServerBot\DCSServerBotConfig.lua', 'w') as outfile:
                        for line in template.readlines():
                            s = line.find('{')
                            e = line.find('}')
                            if s != -1 and e != -1 and (e - s) > 1:
                                param = line[s + 1:e].split('.')
                                if len(param) == 2:
                                    if param[0] == 'BOT' and param[1] == 'HOST' and \
                                            self.config[param[0]][param[1]] == '0.0.0.0':
                                        line = line.replace('{' + '.'.join(param) + '}', '127.0.0.1')
                                    else:
                                        line = line.replace('{' + '.'.join(param) + '}',
                                                            self.config[param[0]][param[1]])
                                elif len(param) == 1:
                                    line = line.replace('{' + '.'.join(param) + '}',
                                                        self.config[installation][param[0]])
                            outfile.write(line)
            except KeyError as k:
                self.log.error(
                    f'! Your dcsserverbot.ini contains errors. You must set a value for {k}. See README for help.')
                raise k
            self.log.debug(f"  - Installing Plugin luas into {installation} ...")
            for plugin_name in self.main.plugins:
                source_path = f'./plugins/{plugin_name}/lua'
                if os.path.exists(source_path):
                    target_path = os.path.expandvars(self.config[installation]['DCS_HOME'] +
                                                     f'\\Scripts\\net\\DCSServerBot\\{plugin_name}\\')
                    copytree(source_path, target_path, dirs_exist_ok=True)
                    self.log.debug(f'    => Plugin {plugin_name.capitalize()} installed.')
            self.log.debug('  - Luas installed into {}.'.format(installation))

    async def init_servers(self):
        for server_name, installation in utils.findDCSInstallations():
            if installation not in self.config:
                continue
            server: ServerImpl = DataObjectFactory().new(
                Server.__name__, main=self, name=server_name, installation=installation,
                host=self.config[installation]['DCS_HOST'], port=self.config[installation]['DCS_PORT'],
                external_ip=self.config['BOT'].get('PUBLIC_IP', await utils.get_external_ip())
            )
            self.servers[server_name] = server
            # TODO: can be removed if bug in net.load_next_mission() is fixed
            if 'listLoop' not in server.settings or not server.settings['listLoop']:
                server.settings['listLoop'] = True

    async def send_init(self, server: Server):
        self.sendtoBot({
            "command": "init",
            "server_name": server.name,
            "external_ip": self.config['BOT'].get('PUBLIC_IP', await utils.get_external_ip()),
            "status": Status.UNREGISTERED.value,
            "installation": server.installation,
            "settings": server.settings,
            "options": server.options
        })

    async def register_servers(self):
        self.log.info('- Searching for running DCS servers (this might take a bit) ...')
        timeout = (5 * len(self.servers)) if self.config.getboolean('BOT', 'SLOW_SYSTEM') else (3 * len(self.servers))
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
        server: ServerImpl = cast(ServerImpl, self.servers[data['server_name']])
        # set the PID
        for exe in ['DCS_server.exe', 'DCS.exe']:
            server.process = utils.find_process(exe, server.installation)
            if server.process:
                break
        server.dcs_version = data['dcs_version']
        if 'players' not in data:
            server.status = Status.STOPPED
        elif data['pause']:
            server.status = Status.PAUSED
        else:
            server.status = Status.RUNNING
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
        self.log.info(f"  => Local DCS-Server \"{data['server_name']}\" registered.")
        return True

    def init_remote_server(self, data: dict) -> ServerProxy:
        proxy = self.servers.get(data['server_name'])
        if not proxy:
            proxy = ServerProxy(
                main=self,
                name=data['server_name'],
                installation=data['installation'],
                host=data['agent'],
                port=-1,
                external_ip=data['external_ip']
            )
            self.servers[data['server_name']] = proxy
            proxy.settings = data.get('settings')
            proxy.options = data.get('options')
        else:
            # IP might have changed, so update it
            proxy.external_ip = data['external_ip']
        proxy.status = Status(data['status'])
        return proxy

    def sendtoBot(self, data: dict, agent: Optional[str] = None):
        if self.master:
            if agent:
                self.log.debug('MASTER->{}: {}'.format(agent, json.dumps(data)))
                with self.pool.connection() as conn:
                    with conn.transaction():
                        conn.execute("INSERT INTO intercom (agent, data) VALUES (%s, %s)", (agent, Json(data)))
            else:
                self.udp_server.message_queue[data['server_name']].put(data)
        else:
            data['agent'] = self.agent
            with self.pool.connection() as conn:
                with conn.pipeline():
                    with conn.transaction():
                        conn.execute("INSERT INTO intercom (agent, data) VALUES ('Master', %s)", (Json(data),))
                        self.log.debug(f"HOST->MASTER: {json.dumps(data)}")

    async def handle_master(self, data: dict):
        self.log.debug(f"{data['agent']}->MASTER: {json.dumps(data)}")
        if data['command'] == 'init':
            server = self.init_remote_server(data)
            if server.name not in self.udp_server.message_queue:
                self.udp_server.message_queue[server.name] = Queue()
                self.executor.submit(self.udp_server.process, server)
        else:
            self.udp_server.message_queue[data['server_name']].put(data)

    async def handle_agent(self, data: dict):
        self.log.debug(f"MASTER->HOST: {json.dumps(data)}")
        if data['command'] == 'rpc':
            if data.get('object') == 'Server':
                obj = self.servers[data['server_name']]
            elif data.get('object') == 'Agent':
                obj = self
            else:
                self.log.warning('RPC command received for unknown object.')
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
                        for row in cursor.execute("SELECT id, data FROM intercom WHERE agent = %s",
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
                            else:
                                if 'players' not in data:
                                    server.status = Status.STOPPED
                                elif data['pause']:
                                    server.status = Status.PAUSED
                                else:
                                    server.status = Status.RUNNING
                                self.log.info(f"  => DCS-Server \"{server.name}\" from Agent {server.host} registered.")
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

        host = self.config['BOT']['HOST']
        port = int(self.config['BOT']['PORT'])
        self.udp_server = MyThreadingUDPServer((host, port), RequestHandler)
        self.executor.submit(self.udp_server.serve_forever)
        self.log.debug('- Listener started on interface {} port {} accepting commands.'.format(host, port))
