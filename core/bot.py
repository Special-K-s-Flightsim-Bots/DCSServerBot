import asyncio
import discord
import json
import platform
import psycopg2
import socket
import string
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from core import utils
from core.const import Status
from discord.ext import commands
from .listener import EventListener
from socketserver import BaseRequestHandler, ThreadingUDPServer
from typing import Callable, Optional, Tuple, Any


class DCSServerBot(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.member = None
        self.version = kwargs['version']
        self.sub_version = kwargs['sub_version']
        self.listeners = {}
        self.eventListeners = []
        self.external_ip = None
        self.udp_server = None
        self.loop = asyncio.get_event_loop()
        self.globals = {}
        self.embeds = {}
        self.pool = kwargs['pool']
        self.log = kwargs['log']
        self.config = kwargs['config']
        plugins = self.config['BOT']['PLUGINS']
        if 'OPT_PLUGINS' in self.config['BOT']:
            plugins += ', ' + self.config['BOT']['OPT_PLUGINS']
        self.plugins = [p.strip() for p in plugins.split(',')]
        self.audit_channel = None
        self.player_data = None
        self.executor = ThreadPoolExecutor(max_workers=10)

    async def close(self):
        await super().close()
        self.log.debug('Shutting down...')
        if self.udp_server:
            self.udp_server.shutdown()
            self.udp_server.server_close()
        self.log.debug('- Listener stopped.')
        self.executor.shutdown(wait=True)
        self.log.debug('- Executor stopped.')
        self.log.info('Shutdown complete.')

    def init_servers(self):
        for server_name, installation in utils.findDCSInstallations():
            if installation in self.config:
                self.globals[server_name] = {
                    "server_name": server_name,
                    "installation": installation,
                    "status": Status.UNREGISTERED,
                    "host": self.config[installation]['DCS_HOST'],
                    "port": self.config[installation]['DCS_PORT'],
                    "chat_channel": self.config[installation]['CHAT_CHANNEL'],
                    "status_channel": self.config[installation]['STATUS_CHANNEL'],
                    "admin_channel": self.config[installation]['ADMIN_CHANNEL']
                }
                # TODO: can be removed if bug in net.load_next_mission() is fixed
                utils.changeServerSettings(server_name, 'listLoop', True)

    async def register_servers(self):
        self.log.info('- Searching for running DCS servers ...')
        for server_name, server in self.globals.items():
            try:
                # check if there is a running server already
                await self.sendtoDCSSync(server, {"command": "registerDCSServer"}, 5)
                self.log.info(f'  => Running DCS server "{server_name}" registered.')
            except asyncio.TimeoutError:
                server['status'] = Status.SHUTDOWN

    def load_plugin(self, plugin: str):
        try:
            self.load_extension(f'plugins.{plugin}.commands')
        except commands.ExtensionNotFound:
            self.log.error(f'- No commands.py found for plugin "{plugin}"')
        except commands.ExtensionFailed as ex:
            self.log.exception(ex)
            self.log.error(f'- Error during initialisation of plugin "{plugin}": {ex.original if ex.original else ex}')

    def unload_plugin(self, plugin: str):
        try:
            self.unload_extension(f'plugins.{plugin}.commands')
        except commands.ExtensionNotFound:
            self.log.debug(f'- No init.py found for plugin "{plugin}!"')
            pass

    def reload_plugin(self, plugin: str):
        self.unload_plugin(plugin)
        self.load_plugin(plugin)

    def run(self, *args: Any, **kwargs: Any) -> None:
        self.init_servers()
        super().run(*args, **kwargs)

    def check_channel(self, channel_id: int):
        channel = self.get_channel(channel_id)
        channel_name = channel.name.encode(encoding='ASCII', errors='replace').decode()
        # name changes of the status channel will only happen with the correct permission
        permissions = channel.permissions_for(self.member)
        if not permissions.view_channel:
            self.log.error(f'Permission "View Channel" missing for channel {channel_name}')
        if not permissions.send_messages:
            self.log.error(f'Permission "Send Messages" missing for channel {channel_name}')
        if not permissions.read_messages:
            self.log.error(f'Permission "Read Messages" missing for channel {channel_name}')
        if not permissions.read_message_history:
            self.log.error(f'Permission "Read Message History" missing for channel {channel_name}')
        if not permissions.add_reactions:
            self.log.error(f'Permission "Add Reactions" missing for channel {channel_name}')
        if not permissions.attach_files:
            self.log.error(f'Permission "Attach Files" missing for channel {channel_name}')
        if not permissions.embed_links:
            self.log.error(f'Permission "Embed Links" missing for channel {channel_name}')
        if not permissions.manage_messages:
            self.log.error(f'Permission "Manage Messages" missing for channel {channel_name}')

    def check_channels(self, installation: str):
        for c in ['ADMIN_CHANNEL', 'STATUS_CHANNEL', 'CHAT_CHANNEL']:
            channel_id = int(self.config[installation][c])
            if channel_id != -1:
                self.check_channel(channel_id)

    async def on_ready(self):
        if not self.external_ip:
            self.member = self.guilds[0].get_member(self.user.id)
            self.log.debug('- Checking channels ...')
            for server in self.globals.values():
                self.check_channels(server['installation'])
            self.log.info(f'- Logged in as {self.user.name} - {self.user.id}')
            self.external_ip = await utils.get_external_ip()
            self.remove_command('help')
            self.log.info('- Loading Plugins ...')
            for plugin in self.plugins:
                self.load_plugin(plugin.lower())
                self.log.info(f'  => {string.capwords(plugin)} loaded.')
            # start the UDP listener to accept commands from DCS
            self.loop.create_task(self.start_udp_listener())
            await self.register_servers()
            self.log.info('DCSServerBot started, accepting commands.')
        else:
            self.log.info('Discord connection reestablished.')
        return

    async def on_command_error(self, ctx: discord.ext.commands.Context, err: Exception):
        if isinstance(err, commands.CommandNotFound):
            pass
        elif isinstance(err, commands.NoPrivateMessage):
            await ctx.send('This command can\'t be used in a DM.')
        elif isinstance(err, commands.MissingRequiredArgument):
            await ctx.send('Parameter missing. Try !help')
        elif isinstance(err, commands.errors.CheckFailure):
            await ctx.send('You don\'t have the rights to use that command.')
        elif isinstance(err, asyncio.TimeoutError):
            await ctx.send('A timeout occured. Is the DCS server running?')
        else:
            await ctx.send(str(err))

    def reload(self, plugin: Optional[str]):
        if plugin:
            self.reload_plugin(plugin)
        else:
            for plugin in self.plugins:
                self.reload_plugin(plugin)

    def rename_server(self, old_name: str, new_name: str, update_settings: bool = False) -> None:
        if new_name not in self.globals:
            self.globals[new_name] = deepcopy(self.globals[old_name])
            self.globals[new_name]['server_name'] = new_name
        if old_name in self.globals:
            del self.globals[old_name]
        if old_name in self.embeds:
            self.embeds[new_name] = self.embeds[old_name].copy()
            del self.embeds[old_name]
        # call rename() in all Plugins
        for plugin in self.cogs.values():
            plugin.rename(old_name, new_name)
        # rename the entries in the main database tables
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE servers SET server_name = %s WHERE server_name = %s',
                               (new_name, old_name))
                cursor.execute('UPDATE message_persistence SET server_name = %s WHERE server_name = %s',
                               (new_name, old_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
        if update_settings:
            utils.changeServerSettings(old_name, 'name', new_name)

    async def audit(self, message, *, embed: Optional[discord.Embed] = None):
        if not self.audit_channel:
            if 'AUDIT_CHANNEL' in self.config['BOT']:
                self.audit_channel = self.get_channel(int(self.config['BOT']['AUDIT_CHANNEL']))
        if self.audit_channel:
            await self.audit_channel.send(message, embed=embed)

    def sendtoDCS(self, server: dict, message: dict):
        # As Lua does not support large numbers, convert them to strings
        for key, value in message.items():
            if type(value) == int:
                message[key] = str(value)
        msg = json.dumps(message)
        self.log.debug(f"HOST->{server['server_name']}: {msg}")
        dcs_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dcs_socket.sendto(msg.encode('utf-8'), (server['host'], int(server['port'])))

    async def sendtoDCSSync(self, server: dict, message: dict, timeout: Optional[int] = 5.0):
        future = self.loop.create_future()
        token = 'sync-' + str(uuid.uuid4())
        message['channel'] = token
        self.listeners[token] = future
        try:
            self.sendtoDCS(server, message)
            return await asyncio.wait_for(future, timeout)
        finally:
            del self.listeners[token]

    def sendtoBot(self, message: dict):
        message['channel'] = '-1'
        msg = json.dumps(message)
        self.log.debug('HOST->HOST: {}'.format(msg))
        dcs_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        host = self.config['BOT']['HOST']
        if host == '0.0.0.0':
            host = '127.0.0.1'
        dcs_socket.sendto(msg.encode('utf-8'), (host, int(self.config['BOT']['PORT'])))

    def get_bot_channel(self, data: dict, channel_type: Optional[str] = 'status_channel'):
        if data['channel'].startswith('sync') or int(data['channel']) == -1:
            return self.get_channel(int(self.globals[data['server_name']][channel_type]))
        else:
            return self.get_channel(int(data['channel']))

    async def setEmbed(self, data: dict, embed_name: str, embed: discord.Embed, file: Optional[discord.File] = None):
        server_name = data['server_name']
        server = self.globals[server_name]
        if server_name in self.embeds and embed_name in self.embeds[server_name]:
            message = self.embeds[server_name][embed_name]
        elif 'embeds' in server:
            if embed_name in server['embeds']:
                # load a persisted message, if it hasn't been done yet
                if server_name not in self.embeds:
                    self.embeds[server_name] = {}
                try:
                    message = self.embeds[server_name][embed_name] = \
                        await self.get_bot_channel(data).fetch_message(server['embeds'][embed_name])
                except discord.errors.NotFound:
                    message = None
            else:
                message = None
        else:
            # unlikely event when someone tries to send a message when the server isn't initialized yet
            return
        if message:
            try:
                await message.edit(embed=embed)
            except discord.errors.NotFound:
                message = None
        if not message:
            if server_name not in self.embeds:
                self.embeds[server_name] = {}
            message = self.embeds[server_name][embed_name] = \
                await self.get_bot_channel(data).send(embed=embed, file=file)
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('INSERT INTO message_persistence (server_name, embed_name, embed) VALUES (%s, %s, '
                                   '%s) ON CONFLICT (server_name, embed_name) DO UPDATE SET embed=%s',
                                   (server_name, embed_name, message.id, message.id))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)

    def register_eventListener(self, listener: EventListener):
        self.log.debug(f'- Registering EventListener {type(listener).__name__}')
        self.eventListeners.append(listener)

    def unregister_eventListener(self, listener: EventListener):
        self.eventListeners.remove(listener)
        self.log.debug(f'- EventListener {type(listener).__name__} unregistered.')

    def register_server(self, data) -> bool:
        installations = utils.findDCSInstallations(data['server_name'])
        if len(installations) == 0:
            self.log.error(f"No section found for server {data['server_name']} in your dcsserverbot.ini.\nPlease add a "
                           f"configuration for it!")
            return False
        _, installation = installations[0]
        self.log.debug(f"  => Registering DCS-Server \"{data['server_name']}\"")
        # check for protocol incompatibilities
        if data['hook_version'] != self.version:
            self.log.error('Server \"{}\" has wrong Hook version installed. Please update lua files and restart '
                           'server. Registration ignored.'.format(data['server_name']))
            return False
        # register the server in the internal datastructures
        if data['server_name'] in self.globals:
            self.globals[data['server_name']] = self.globals[data['server_name']] | data.copy()
            server = self.globals[data['server_name']]
        else:
            # a new server is to be registered
            server = self.globals[data['server_name']] = data.copy()
            server['chat_channel'] = self.config[installation]['CHAT_CHANNEL']
            server['status_channel'] = self.config[installation]['STATUS_CHANNEL']
            server['admin_channel'] = self.config[installation]['ADMIN_CHANNEL']
        server['installation'] = installation
        # set the PID
        process = utils.find_process('DCS.exe', server['installation'])
        if process:
            server['PID'] = process.pid
        # update the database and check for server name changes
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT server_name FROM servers WHERE agent_host=%s AND host=%s AND port=%s',
                               (platform.node(), data['host'], data['port']))
                if cursor.rowcount == 1:
                    server_name = cursor.fetchone()[0]
                    if server_name != data['server_name']:
                        if len(utils.findDCSInstallations(server_name)) == 0:
                            self.log.info(f"Auto-renaming server \"{server_name}\" to \"{data['server_name']}\"")
                            self.rename_server(server_name, data['server_name'])
                        else:
                            self.log.warning(
                                f"Registration of server \"{data['server_name']}\" aborted due to UDP port conflict.")
                            del self.globals[data['server_name']]
                            return False
                cursor.execute('INSERT INTO servers (server_name, agent_host, host, port) VALUES(%s, %s, %s, '
                               '%s) ON CONFLICT (server_name) DO UPDATE SET agent_host=%s, host=%s, port=%s',
                               (data['server_name'], platform.node(), data['host'], data['port'], platform.node(),
                                data['host'], data['port']))
                conn.commit()
                # read persisted messages for this server
                server['embeds'] = {}
                cursor.execute('SELECT server_name, embed_name, embed FROM message_persistence WHERE server_name '
                               'IN (SELECT server_name FROM servers WHERE server_name = %s AND agent_host = %s)',
                               (server['server_name'], platform.node()))
                for row in cursor.fetchall():
                    server['embeds'][row['embed_name']] = row['embed']
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
        if data['channel'].startswith('sync-'):
            server['status'] = Status.PAUSED if 'pause' in data and data['pause'] is True else Status.RUNNING
        else:
            server['status'] = Status.LOADING
        self.log.debug(f"Server {server['server_name']} initialized")
        return True

    async def start_udp_listener(self):
        class RequestHandler(BaseRequestHandler):

            def handle(s):
                data = json.loads(s.request[0].strip())
                # ignore messages not containing server names
                if 'server_name' not in data:
                    self.log.warning('Message without server_name retrieved: {}'.format(data))
                    return
                self.log.debug('{}->HOST: {}'.format(data['server_name'], json.dumps(data)))
                command = data['command']
                if command == 'registerDCSServer':
                    if not self.register_server(data):
                        return
                elif (data['server_name'] not in self.globals or
                      self.globals[data['server_name']]['status'] == Status.UNREGISTERED):
                    self.log.debug(f"Command {command} for unregistered server {data['server_name']} retrieved, ignoring.")
                    return
                if data['channel'].startswith('sync-'):
                    if data['channel'] in self.listeners:
                        f = self.listeners[data['channel']]
                        if not f.cancelled():
                            f.get_loop().call_soon_threadsafe(f.set_result, data)
                    if command != 'registerDCSServer':
                        return
                for listener in self.eventListeners:
                    asyncio.run_coroutine_threadsafe(listener.processEvent(data), self.loop)

        class MyThreadingUDPServer(ThreadingUDPServer):
            def __init__(self, server_address: Tuple[str, int], request_handler: Callable[..., BaseRequestHandler]):
                # enable reuse, in case the restart was too fast and the port was still in TIME_WAIT
                self.allow_reuse_address = True
                self.max_packet_size = 65504
                super().__init__(server_address, request_handler)

        host = self.config['BOT']['HOST']
        port = int(self.config['BOT']['PORT'])
        self.udp_server = MyThreadingUDPServer((host, port), RequestHandler)
        self.loop.run_in_executor(self.executor, self.udp_server.serve_forever)
        self.log.debug('- Listener started on interface {} port {} accepting commands.'.format(host, port))
