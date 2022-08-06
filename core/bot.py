from __future__ import annotations
import asyncio
import discord
import json
import platform
import psycopg2
import re
import socket
import string
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from core import utils, Server, Status, Channel, DataObjectFactory
from datetime import datetime
from discord.ext import commands
from socketserver import BaseRequestHandler, ThreadingUDPServer
from typing import Callable, Optional, Tuple, Any, Union, TYPE_CHECKING
from .listener import EventListener

if TYPE_CHECKING:
    from discord.ext.commands.context import Context


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
        self.servers = dict[str, Server]()
        self.pool = kwargs['pool']
        self.log = kwargs['log']
        self.config = kwargs['config']
        plugins = self.config['BOT']['PLUGINS']
        if 'OPT_PLUGINS' in self.config['BOT']:
            plugins += ', ' + self.config['BOT']['OPT_PLUGINS']
        self.plugins = [p.strip() for p in plugins.split(',')]
        self.audit_channel = None
        self.mission_stats = None
        self.executor = ThreadPoolExecutor()

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
                server: Server = DataObjectFactory().new(
                    Server.__name__, bot=self, name=server_name, installation=installation,
                    host=self.config[installation]['DCS_HOST'], port=self.config[installation]['DCS_PORT'])
                self.servers[server_name] = server
                # TODO: can be removed if bug in net.load_next_mission() is fixed
                server.changeServerSettings('listLoop', True)

    async def register_servers(self):
        self.log.info('- Searching for running DCS servers ...')
        for server_name, server in self.servers.items():
            try:
                # check if there is a running server already
                timeout = 10 if self.config.getboolean('BOT', 'SLOW_SYSTEM') else 5
                server.sendtoDCS({"command": "registerDCSServer"})
                await server.wait_for_status_change([Status.RUNNING, Status.PAUSED, Status.STOPPED], timeout)
                self.log.info(f'  => Running DCS server "{server_name}" registered.')
            except asyncio.TimeoutError:
                self.log.debug(f'  => Timeout while trying to contact DCS server "{server_name}".')
                server.status = Status.SHUTDOWN

    def load_plugin(self, plugin: str) -> bool:
        try:
            self.load_extension(f'plugins.{plugin}.commands')
            return True
        except commands.ExtensionNotFound:
            self.log.error(f'  - No commands.py found for plugin "{plugin}"')
        except commands.ExtensionFailed as ex:
            self.log.error(f'  - {ex.original if ex.original else ex}')
        return False

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
        channels = ['ADMIN_CHANNEL', 'STATUS_CHANNEL', 'CHAT_CHANNEL']
        if self.config.getboolean(installation, 'COALITIONS'):
            channels.extend(['COALITION_BLUE_CHANNEL', 'COALITION_RED_CHANNEL'])
        for c in channels:
            channel_id = int(self.config[installation][c])
            if channel_id != -1:
                self.check_channel(channel_id)

    async def on_ready(self):
        if not self.external_ip:
            self.member = self.guilds[0].get_member(self.user.id)
            self.log.debug('- Checking channels ...')
            for server in self.servers.values():
                self.check_channels(server.installation)
            self.log.info(f'- Logged in as {self.user.name} - {self.user.id}')
            self.external_ip = await utils.get_external_ip()
            self.log.info('- Loading Plugins ...')
            for plugin in self.plugins:
                if self.load_plugin(plugin.lower()):
                    self.log.info(f'  => {string.capwords(plugin)} loaded.')
                else:
                    self.log.info(f'  => {string.capwords(plugin)} NOT loaded.')
            # start the UDP listener to accept commands from DCS
            self.loop.create_task(self.start_udp_listener())
            self.loop.create_task(self.register_servers())
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
            await ctx.send(f'Parameter missing. Try {ctx.prefix}help')
        elif isinstance(err, commands.errors.CheckFailure):
            await ctx.send('Your role does not allow you to use this command (in this channel).')
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

    async def audit(self, message, *, user: Optional[Union[discord.Member, str]] = None, server: Optional[Server] = None):
        if not self.audit_channel:
            if 'AUDIT_CHANNEL' in self.config['BOT']:
                self.audit_channel = self.get_channel(int(self.config['BOT']['AUDIT_CHANNEL']))
        if self.audit_channel:
            if isinstance(user, str):
                member = self.get_member_by_ucid(user)
            else:
                member = user
            embed = discord.Embed(color=discord.Color.blue())
            if member:
                embed.set_author(name=member.name + '#' + member.discriminator, icon_url=member.avatar_url)
                embed.set_thumbnail(url=member.avatar_url)
                message = f'<@{member.id}> ' + message
            elif not user:
                embed.set_author(name=self.member.name + '#' + self.member.discriminator,
                                 icon_url=self.member.avatar_url)
                embed.set_thumbnail(url=self.member.avatar_url)
            embed.description = message
            if isinstance(user, str):
                embed.add_field(name='UCID', value=user)
            if server:
                embed.add_field(name='Server', value=server.name)
            embed.set_footer(text=datetime.now().strftime("%d/%m/%y %H:%M:%S"))
            await self.audit_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))

    def sendtoBot(self, message: dict):
        message['channel'] = '-1'
        msg = json.dumps(message)
        self.log.debug('HOST->HOST: {}'.format(msg))
        dcs_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        host = self.config['BOT']['HOST']
        if host == '0.0.0.0':
            host = '127.0.0.1'
        dcs_socket.sendto(msg.encode('utf-8'), (host, int(self.config['BOT']['PORT'])))

    def get_channel(self, id: int):
        return super().get_channel(id) if id != -1 else None

    def get_ucid_by_name(self, name: str) -> Optional[str]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                search = f'%{name}%'
                cursor.execute('SELECT ucid FROM players WHERE LOWER(name) like LOWER(%s) ORDER BY last_seen DESC '
                               'LIMIT 1', (search, ))
                if cursor.rowcount >= 1:
                    return cursor.fetchone()[0]
                else:
                    return None
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def get_ucid_by_member(self, member: discord.Member, verified: Optional[bool] = False) -> Optional[str]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                sql = 'SELECT ucid FROM players WHERE discord_id = %s '
                if verified:
                    sql += 'AND manual IS TRUE '
                sql += 'ORDER BY last_seen DESC'
                cursor.execute(sql, (member.id, ))
                if cursor.rowcount >= 1:
                    return cursor.fetchone()[0]
                else:
                    return None
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def get_member_by_ucid(self, ucid: str, verified: Optional[bool] = False) -> Optional[discord.Member]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                sql = 'SELECT discord_id FROM players WHERE ucid = %s AND discord_id <> -1'
                if verified:
                    sql += ' AND manual IS TRUE'
                cursor.execute(sql, (ucid, ))
                if cursor.rowcount == 1:
                    return self.guilds[0].get_member(cursor.fetchone()[0])
                else:
                    return None
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @staticmethod
    def match(name1: str, name2: str) -> int:
        def compare_words(n1: str, n2: str) -> int:
            n1 = re.sub('|', '', n1)
            n1 = re.sub('[._-]', ' ', n1)
            n2 = re.sub('|', '', n2)
            n2 = re.sub('[._-]', ' ', n2)
            n1_words = n1.split()
            n2_words = n2.split()
            length = 0
            for w in n1_words:
                if w in n2_words:
                    if len(w) > 3 or length > 0:
                        length += len(w)
            return length

        if name1 == name2:
            return len(name1)
        # remove any tags
        n1 = re.sub('^[\[\<\(=-].*[-=\)\>\]]', '', name1).strip().casefold()
        if len(n1) == 0:
            n1 = name1.casefold()
        n2 = re.sub('^[\[\<\(=-].*[-=\)\>\]]', '', name2).strip().casefold()
        if len(n2) == 0:
            n2 = name2.casefold()
        # if the names are too short, return
        if (len(n1) <= 3 or len(n2) <= 3) and (n1 != n2):
            return 0
        length = max(compare_words(n1, n2), compare_words(n2, n1))
        if length > 0:
            return length
        # remove any special characters
        n1 = re.sub('[^a-zA-Z\d ]', '', n1).strip()
        n2 = re.sub('[^a-zA-Z\d ]', '', n2).strip()
        if (len(n1) == 0) or (len(n2) == 0):
            return 0
        # if the names are too short, return
        if len(n1) <= 3 or len(n2) <= 3:
            return 0
        length = max(compare_words(n1, n2), compare_words(n2, n1))
        if length > 0:
            return length
        # remove any numbers
        n1 = re.sub('[\d ]', '', n1).strip()
        n2 = re.sub('[\d ]', '', n2).strip()
        if (len(n1) == 0) or (len(n2) == 0):
            return 0
        # if the names are too short, return
        if (len(n1) <= 3 or len(n2) <= 3) and (n1 != n2):
            return 0
        return max(compare_words(n1, n2), compare_words(n2, n1))

    def match_user(self, data: Union[dict, discord.Member], rematch=False) -> Optional[discord.Member]:
        # try to match a DCS user with a Discord member
        tag_filter = self.config['FILTER']['TAG_FILTER'] if 'TAG_FILTER' in self.config['FILTER'] else None
        if isinstance(data, dict):
            if not rematch:
                member = self.get_member_by_ucid(data['ucid'])
                if member:
                    return member
            # we could not find the user, so try to match them
            dcs_name = re.sub(tag_filter, '', data['name']).strip() if tag_filter else data['name']
            # we do not match the default names
            if dcs_name in ['Player', 'Spieler', 'Jugador', 'Joueur']:
                return None
            # a minimum of 3 characters have to match
            max_weight = 3
            best_fit = list[discord.Member]()
            for member in self.get_all_members():  # type: discord.Member
                # don't match bot users
                if member.bot:
                    continue
                name = re.sub(tag_filter, '', member.name).strip() if tag_filter else member.name
                if member.nick:
                    nickname = re.sub(tag_filter, '', member.nick).strip() if tag_filter else member.nick
                    weight = max(self.match(dcs_name, nickname), self.match(dcs_name, name))
                else:
                    weight = self.match(dcs_name, name)
                if weight > max_weight:
                    max_weight = weight
                    best_fit = [member]
                elif weight == max_weight:
                    best_fit.append(member)
            if len(best_fit) == 1:
                return best_fit[0]
            # ambiguous matches
            elif len(best_fit) > 1 and not rematch:
                online_match = []
                gaming_match = []
                # check for online users
                for m in best_fit:
                    if m.status != discord.Status.offline:
                        online_match.append(m)
                        if isinstance(m.activity, discord.Game) and 'DCS' in m.activity.name:
                            gaming_match.append(m)
                if len(gaming_match) == 1:
                    return gaming_match[0]
                elif len(online_match) == 1:
                    return online_match[0]
            return None
        # try to match a Discord member with a DCS user that played on the servers
        else:
            max_weight = 0
            best_fit = None
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    sql = 'SELECT ucid, name from players'
                    if rematch is False:
                        sql += ' WHERE discord_id = -1 AND name IS NOT NULL'
                    cursor.execute(sql)
                    for row in cursor.fetchall():
                        name = re.sub(tag_filter, '', data.name).strip() if tag_filter else data.name
                        if data.nick:
                            nickname = re.sub(tag_filter, '', data.nick).strip() if tag_filter else data.nick
                            weight = max(self.match(nickname, row['name']), self.match(name, row['name']))
                        else:
                            weight = self.match(name, row[1])
                        if weight > max_weight:
                            max_weight = weight
                            best_fit = row[0]
                    return best_fit
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
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
            self.log.error(f"No section found for server {data['server_name']} in your dcsserverbot.ini.\n"
                           f"Please add a configuration for it!")
            return False
        _, installation = installations[0]
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
        server.process = utils.find_process('DCS.exe', server.installation)
        server.options = data['options']
        server.settings = data['serverSettings']
        server.dcs_version = data['dcs_version']
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
                            server.rename(server_name, data['server_name'])
                            del self.servers[server_name]
                        else:
                            self.log.warning(
                                f"Registration of server \"{data['server_name']}\" aborted due to UDP port conflict.")
                            del self.servers[data['server_name']]
                            return False
                cursor.execute('INSERT INTO servers (server_name, agent_host, host, port) VALUES(%s, %s, %s, '
                               '%s) ON CONFLICT (server_name) DO UPDATE SET agent_host=excluded.agent_host, '
                               'host=excluded.agent_host, port=excluded.port, last_seen=NOW()',
                               (data['server_name'], platform.node(), data['host'], data['port']))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
        self.log.debug(f"Server {server.name} initialized")
        return True

    async def get_server(self, ctx: Union[Context, discord.Message, str]) -> Optional[Server]:
        for server_name, server in self.servers.items():
            if isinstance(ctx, discord.ext.commands.context.Context) or isinstance(ctx, discord.Message):
                if server.status == Status.UNREGISTERED:
                    continue
                channels = [Channel.ADMIN, Channel.STATUS]
                if int(self.config[server.installation][Channel.CHAT.value]) != -1:
                    channels.append(Channel.CHAT)
                if int(self.config[server.installation][Channel.COALITION_BLUE.value]) != -1:
                    channels.append(Channel.COALITION_BLUE)
                if int(self.config[server.installation][Channel.COALITION_RED.value]) != -1:
                    channels.append(Channel.COALITION_RED)
                for channel in channels:
                    if server.get_channel(channel).id == ctx.channel.id:
                        return server
            else:
                if server_name == ctx:
                    return server
        return None

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
                elif (data['server_name'] not in self.servers or
                      self.servers[data['server_name']].status == Status.UNREGISTERED):
                    self.log.debug(f"Command {command} for unregistered server {data['server_name']} retrieved, "
                                   f"ignoring.")
                    return
                if data['channel'].startswith('sync-'):
                    if data['channel'] in self.listeners:
                        f = self.listeners[data['channel']]
                        if not f.cancelled():
                            self.loop.call_soon_threadsafe(f.set_result, data)
                    if command != 'registerDCSServer':
                        return
                for listener in self.eventListeners:
                    if command in listener.commands:
                        self.loop.call_soon_threadsafe(asyncio.create_task, listener.processEvent(data))

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
