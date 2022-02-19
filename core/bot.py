import asyncio
import string
import discord
import json
import platform
import psycopg2
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, suppress
from core import utils
from core.const import Status
from discord.ext import commands
from .listener import EventListener
from socketserver import BaseRequestHandler, ThreadingUDPServer
from typing import Callable, Optional, Tuple


class DCSServerBot(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = kwargs['version']
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
        self.loop.create_task(self.start_udp_listener())

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

    def read_servers(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT server_name, host, port, chat_channel, status_channel, admin_channel FROM '
                               'servers WHERE agent_host = %s', (platform.node(),))
                for row in cursor.fetchall():
                    self.globals[row['server_name']] = dict(row)
                    self.globals[row['server_name']]['status'] = Status.UNKNOWN
                    self.globals[row['server_name']]['embeds'] = {}
                    # Initialize statistics with true unless we get other information from the server
                    self.globals[row['server_name']]['statistics'] = True
                cursor.execute('SELECT server_name, embed_name, embed FROM message_persistence WHERE server_name IN ('
                               'SELECT server_name FROM servers WHERE agent_host = %s)', (platform.node(),))
                for row in cursor.fetchall():
                    self.globals[row['server_name']]['embeds'][row['embed_name']] = row['embed']
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        self.log.debug('{} server(s) read from database.'.format(len(self.globals)))

    async def init_servers(self):
        self.log.info('- Searching for DCS servers ...')
        for server_name, server in self.globals.items():
            installation = utils.findDCSInstallations(server_name)[0]
            server['installation'] = installation
            channel = await self.fetch_channel(server['status_channel'])
            self.embeds[server_name] = {}
            for embed_name, embed_id in server['embeds'].items():
                with suppress(Exception):
                    self.embeds[server_name][embed_name] = await channel.fetch_message(embed_id)
            try:
                # check if there is a running server already
                await self.sendtoDCSSync(server, {"command": "registerDCSServer"})
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

    async def on_ready(self):
        if not self.external_ip:
            self.log.info(f'- Logged in as {self.user.name} - {self.user.id}')
            self.external_ip = await utils.get_external_ip()
            self.read_servers()
            self.remove_command('help')
            self.log.info('- Loading Plugins ...')
            for plugin in self.plugins:
                self.load_plugin(plugin.lower())
                self.log.info(f'  => {string.capwords(plugin)} loaded.')
            await self.init_servers()
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

    async def audit(self, message, *, embed: Optional[discord.Embed] = None):
        if not self.audit_channel:
            if 'AUDIT_CHANNEL' in self.config['BOT']:
                self.audit_channel = self.guilds[0].get_channel(int(self.config['BOT']['AUDIT_CHANNEL']))
        if self.audit_channel:
            await self.audit_channel.send(message, embed=embed)

    def sendtoDCS(self, server: dict, message: dict):
        # As Lua does not support large numbers, convert them to strings
        for key, value in message.items():
            if type(value) == int:
                message[key] = str(value)
        msg = json.dumps(message)
        self.log.debug('HOST->{}: {}'.format(server['server_name'], msg))
        dcs_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dcs_socket.sendto(msg.encode('utf-8'), (server['host'], int(server['port'])))

    def sendtoDCSSync(self, server: dict, message: dict, timeout: Optional[int] = 5):
        future = self.loop.create_future()
        token = 'sync-' + str(uuid.uuid4())
        message['channel'] = token
        self.sendtoDCS(server, message)
        self.listeners[token] = future
        return asyncio.wait_for(future, timeout)

    def get_bot_channel(self, data: dict, channel_type: Optional[str] = 'status_channel'):
        if int(data['channel']) == -1:
            return self.get_channel(int(self.globals[data['server_name']][channel_type]))
        else:
            return self.get_channel(int(data['channel']))

    async def setEmbed(self, server: dict, embed_name: str, embed: discord.Embed, file: Optional[discord.File] = None):
        server_name = server['server_name']
        message = self.embeds[server_name][embed_name] if (
            server_name in self.embeds and embed_name in self.embeds[server_name]) else None
        if message:
            try:
                await message.edit(embed=embed, file=file)
            except discord.errors.NotFound:
                message = None
        if not message:
            if server_name not in self.embeds:
                self.embeds[server_name] = {}
            message = await self.get_bot_channel(server).send(embed=embed, file=file)
            self.embeds[server_name][embed_name] = message
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('INSERT INTO message_persistence (server_name, embed_name, embed) VALUES (%s, %s, '
                                   '%s) ON CONFLICT (server_name, embed_name) DO UPDATE SET embed=%s', (server_name,
                                                                                                        embed_name,
                                                                                                        message.id,
                                                                                                        message.id))
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

    async def start_udp_listener(self):
        class RequestHandler(BaseRequestHandler):

            def handle(s):
                dt = json.loads(s.request[0].strip())
                # ignore messages not containing server names
                if 'server_name' not in dt:
                    self.log.warning('Message without server_name retrieved: {}'.format(dt))
                    return
                self.log.debug('{}->HOST: {}'.format(dt['server_name'], json.dumps(dt)))
                futures = []
                command = dt['command']
                if command != 'registerDCSServer' and \
                        (dt['server_name'] not in self.globals or
                         self.globals[dt['server_name']]['status'] == Status.UNKNOWN):
                    self.log.warning('Message for unregistered server retrieved, ignoring.')
                    return
                for listener in self.eventListeners:
                    futures.append(asyncio.run_coroutine_threadsafe(listener.processEvent(dt), self.loop))
                results = []
                for future in futures:
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                    except BaseException as ex:
                        self.log.exception(ex)
                if dt['channel'].startswith('sync') and dt['channel'] in self.listeners:
                    f = self.listeners[dt['channel']]
                    if not f.cancelled():
                        f.get_loop().call_soon_threadsafe(f.set_result, results[0] if len(results) > 0 else None)
                    del self.listeners[dt['channel']]

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
