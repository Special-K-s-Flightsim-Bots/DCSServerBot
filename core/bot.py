import asyncio
import concurrent
import discord
import json
import platform
import psycopg2
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from core import utils, Server, Status, Channel, DataObjectFactory, Player, Autoexec
from datetime import datetime
from discord.ext import commands
from queue import Queue
from socketserver import BaseRequestHandler, ThreadingUDPServer
from typing import Callable, Optional, Tuple, Union
from .listener import EventListener


class DCSServerBot(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.member: Optional[discord.Member] = None
        self.version: str = kwargs['version']
        self.sub_version: str = kwargs['sub_version']
        self.listeners = {}
        self.eventListeners: list[EventListener] = []
        self.external_ip: Optional[str] = None
        self.udp_server = None
        self.servers: dict[str, Server] = dict()
        self.pool = kwargs['pool']
        self.log = kwargs['log']
        self.config = kwargs['config']
        self.master: bool = self.config.getboolean('BOT', 'MASTER')
        self.master_only: bool = self.config.getboolean('BOT', 'MASTER_ONLY')
        plugins: str = self.config['BOT']['PLUGINS']
        if 'OPT_PLUGINS' in self.config['BOT']:
            plugins += ', ' + self.config['BOT']['OPT_PLUGINS']
        self.plugins: [str] = [p.strip() for p in list(dict.fromkeys(plugins.split(',')))]
        # make sure, cloud is loaded last
        if 'cloud' in self.plugins:
            self.plugins.remove('cloud')
            self.plugins.append('cloud')
        if not self.config.getboolean('BOT', 'USE_DASHBOARD'):
            self.plugins.remove('dashboard')
        self.audit_channel = None
        self.mission_stats = None
        self.synced: bool = not self.master
        self.tree.on_error = self.on_app_command_error
        self.executor = ThreadPoolExecutor(thread_name_prefix='BotExecutor', max_workers=20)

    async def close(self):
        await self.audit(message="DCSServerBot stopped.")
        self.log.info('Graceful shutdown ...')
        if self.udp_server:
            self.log.debug("- Processing unprocessed messages ...")
            await asyncio.to_thread(self.udp_server.shutdown)
            self.log.debug("- All messages processed.")
            self.udp_server.server_close()
        self.log.debug('- Listener stopped.')
        self.executor.shutdown(wait=True)
        self.log.debug('- Executor stopped.')
        self.log.info('- Unloading Plugins ...')
        await super().close()
        self.log.info('Shutdown complete.')

    def is_master(self) -> bool:
        return self.master

    def init_servers(self):
        for server_name, installation in utils.findDCSInstallations():
            if installation in self.config:
                server: Server = DataObjectFactory().new(
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
            else:
                self.log.info(f'  => Running DCS server "{servers[i].name}" registered.')
                num += 1
        if num == 0:
            self.log.info('- No running servers found.')
        self.log.info('DCSServerBot started, accepting commands.')
        await self.audit(message="DCSServerBot started.")

    async def load_plugin(self, plugin: str) -> bool:
        try:
            await self.load_extension(f'plugins.{plugin}.commands')
            return True
        except ModuleNotFoundError:
            self.log.error(f'  - Plugin "{plugin}" not found!')
        except commands.ExtensionNotFound:
            self.log.error(f'  - No commands.py found for plugin "{plugin}"!')
        except commands.ExtensionAlreadyLoaded:
            self.log.warning(f'  - Plugin "{plugin} was already loaded"')
        except commands.ExtensionFailed as ex:
            self.log.error(f'  - {ex.original if ex.original else ex}')
            self.log.debug(ex)
        except Exception as ex:
            self.log.error(f'  - {ex}')
            self.log.debug(ex)
        return False

    async def unload_plugin(self, plugin: str):
        try:
            await self.unload_extension(f'plugins.{plugin}.commands')
        except commands.ExtensionNotFound:
            self.log.debug(f'- No init.py found for plugin "{plugin}!"')
            pass
        except commands.ExtensionNotLoaded:
            pass

    async def reload_plugin(self, plugin: str):
        await self.unload_plugin(plugin)
        await self.load_plugin(plugin)

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        self.init_servers()
        await super().start(token, reconnect=reconnect)

    def check_roles(self, roles: list, server: Optional[Server] = None):
        for role in roles:
            config_roles = [x.strip() for x in self.config['ROLES' if not server else server.installation][role].split(',')]
            for discord_role in self.guilds[0].roles:
                if discord_role.name in config_roles:
                    config_roles.remove(discord_role.name)
            for bad_role in config_roles:
                self.log.error(f"  => Role {bad_role} not found in your Discord!")

    def check_channel(self, channel_id: int) -> bool:
        channel = self.get_channel(channel_id)
        if not channel:
            self.log.error(f'No channel with ID {channel_id} found!')
            return False
        channel_name = channel.name.encode(encoding='ASCII', errors='replace').decode()
        # name changes of the status channel will only happen with the correct permission
        ret = True
        permissions = channel.permissions_for(self.member)
        if not permissions.view_channel:
            self.log.error(f'  => Permission "View Channel" missing for channel {channel_name}')
            ret = False
        if not permissions.send_messages:
            self.log.error(f'  => Permission "Send Messages" missing for channel {channel_name}')
            ret = False
        if not permissions.read_messages:
            self.log.error(f'  => Permission "Read Messages" missing for channel {channel_name}')
            ret = False
        if not permissions.read_message_history:
            self.log.error(f'  => Permission "Read Message History" missing for channel {channel_name}')
            ret = False
        if not permissions.add_reactions:
            self.log.error(f'  => Permission "Add Reactions" missing for channel {channel_name}')
            ret = False
        if not permissions.attach_files:
            self.log.error(f'  => Permission "Attach Files" missing for channel {channel_name}')
            ret = False
        if not permissions.embed_links:
            self.log.error(f'  => Permission "Embed Links" missing for channel {channel_name}')
            ret = False
        if not permissions.manage_messages:
            self.log.error(f'  => Permission "Manage Messages" missing for channel {channel_name}')
            ret = False
        return ret

    def check_channels(self, installation: str):
        channels = ['ADMIN_CHANNEL', 'STATUS_CHANNEL', 'CHAT_CHANNEL']
        if self.config.getboolean(installation, 'COALITIONS'):
            channels.extend(['COALITION_BLUE_CHANNEL', 'COALITION_RED_CHANNEL'])
        for c in channels:
            channel_id = int(self.config[installation][c])
            if channel_id != -1:
                self.check_channel(channel_id)

    async def on_ready(self):
        try:
            await self.wait_until_ready()
            if not self.external_ip:
                self.log.info(f'- Logged in as {self.user.name} - {self.user.id}')
                if len(self.guilds) > 1:
                    self.log.warning('  => YOUR BOT IS INSTALLED IN MORE THAN ONE GUILD. THIS IS NOT SUPPORTED!')
                    for guild in self.guilds:
                        self.log.warning(f'     - {guild.name}')
                    self.log.warning('  => Remove it from one guild and restart the bot.')
                self.member = self.guilds[0].get_member(self.user.id)
                self.external_ip = await utils.get_external_ip() if 'PUBLIC_IP' not in self.config['BOT'] else self.config['BOT']['PUBLIC_IP']
                self.log.info('- Checking Roles & Channels ...')
                self.check_roles(['Admin', 'DCS Admin', 'DCS', 'GameMaster'])
                for server in self.servers.values():
                    if self.config.getboolean(server.installation, 'COALITIONS'):
                        self.check_roles(['Coalition Red', 'Coalition Blue'], server)
                    self.check_channels(server.installation)
                self.log.info('- Loading Plugins ...')
                for plugin in self.plugins:
                    if not await self.load_plugin(plugin.lower()):
                        self.log.info(f'  => {plugin.title()} NOT loaded.')
                if not self.synced:
                    self.log.info('- Registering Discord Commands (this might take a bit) ...')
                    self.tree.copy_global_to(guild=self.guilds[0])
                    await self.tree.sync(guild=self.guilds[0])
                    self.synced = True
                    self.log.info('- Discord Commands registered.')
                if 'DISCORD_STATUS' in self.config['BOT']:
                    await self.change_presence(activity=discord.Game(name=self.config['BOT']['DISCORD_STATUS']))
                # start the UDP listener to accept commands from DCS
                self.loop.create_task(self.start_udp_listener())
                self.loop.create_task(self.register_servers())
            else:
                self.log.warning('- Discord connection re-established.')
                # maybe our external IP has changed...
                self.external_ip = await utils.get_external_ip() if 'PUBLIC_IP' not in self.config['BOT'] else self.config['BOT']['PUBLIC_IP']
        except Exception as ex:
            self.log.exception(ex)

    async def on_command_error(self, ctx: commands.Context, err: Exception):
        if isinstance(err, commands.CommandInvokeError):
            err = err.original
        if isinstance(err, commands.CommandNotFound):
            pass
        elif isinstance(err, commands.NoPrivateMessage):
            await ctx.send(f"{ctx.command.name} can't be used in a DM.")
        elif isinstance(err, commands.MissingRequiredArgument):
            await ctx.send(f"Usage: {ctx.prefix}{ctx.command.name} {ctx.command.signature}")
        elif isinstance(err, commands.errors.CheckFailure):
            await ctx.send(f"You don't have the permission to use {ctx.command.name}!")
        elif isinstance(err, commands.DisabledCommand):
            pass
        elif isinstance(err, asyncio.TimeoutError):
            await ctx.send('A timeout occurred. Is the DCS server running?')
        elif isinstance(err, commands.MemberNotFound):
            await ctx.send(str(err))
        else:
            self.log.exception(err)
            await ctx.send("An unknown exception occurred.")

    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CommandNotFound):
            pass
        if isinstance(error, discord.app_commands.NoPrivateMessage):
            await interaction.response.send_message(f"{interaction.command.name} can't be used in a DM.")
        elif isinstance(error, discord.app_commands.CheckFailure):
            await interaction.response.send_message(f"You don't have the permission to use {interaction.command.name}!")
        elif isinstance(error, asyncio.TimeoutError):
            await interaction.response.send_message('A timeout occurred. Is the DCS server running?')
        else:
            self.log.exception(error)
            await interaction.response.send_message("An unknown exception occurred.")

    async def reload(self, plugin: Optional[str]):
        if plugin:
            await self.reload_plugin(plugin)
        else:
            for plugin in self.plugins:
                await self.reload_plugin(plugin)

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
                embed.set_author(name=member.name + '#' + member.discriminator, icon_url=member.avatar)
                embed.set_thumbnail(url=member.avatar)
                message = f'<@{member.id}> ' + message
            elif not user:
                embed.set_author(name=self.member.name + '#' + self.member.discriminator,
                                 icon_url=self.member.avatar)
                embed.set_thumbnail(url=self.member.avatar)
            embed.description = message
            if isinstance(user, str):
                embed.add_field(name='UCID', value=user)
            if server:
                embed.add_field(name='Server', value=server.display_name)
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
        dcs_socket.close()

    def get_channel(self, channel_id: int):
        return super().get_channel(channel_id) if channel_id != -1 else None

    def get_ucid_by_name(self, name: str) -> Tuple[Optional[str], Optional[str]]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                search = f'%{name}%'
                cursor.execute('SELECT ucid, name FROM players WHERE name ILIKE %s '
                               'ORDER BY last_seen DESC LIMIT 1', (search, ))
                if cursor.rowcount >= 1:
                    res = cursor.fetchone()
                    return res[0], res[1]
                else:
                    return None, None
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def get_member_or_name_by_ucid(self, ucid: str, verified: bool = False) -> Optional[Union[discord.Member, str]]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                sql = 'SELECT discord_id, name FROM players WHERE ucid = %s'
                if verified:
                    sql += ' AND discord_id <> -1 AND manual IS TRUE'
                cursor.execute(sql, (ucid, ))
                if cursor.rowcount == 1:
                    row = cursor.fetchone()
                    return self.guilds[0].get_member(row[0]) or row[1]
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

    def get_player_by_ucid(self, ucid: str, active: Optional[bool] = True) -> Optional[Player]:
        for server in self.servers.values():
            player = server.get_player(ucid=ucid, active=active)
            if player:
                return player
        return None

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
        n1 = re.sub(r'^[\[\<\(=-].*[-=\)\>\]]', '', name1).strip().casefold()
        if len(n1) == 0:
            n1 = name1.casefold()
        n2 = re.sub(r'^[\[\<\(=-].*[-=\)\>\]]', '', name2).strip().casefold()
        if len(n2) == 0:
            n2 = name2.casefold()
        # if the names are too short, return
        if (len(n1) <= 3 or len(n2) <= 3) and (n1 != n2):
            return 0
        length = max(compare_words(n1, n2), compare_words(n2, n1))
        if length > 0:
            return length
        # remove any special characters
        n1 = re.sub(r'[^a-zA-Z\d ]', '', n1).strip()
        n2 = re.sub(r'[^a-zA-Z\d ]', '', n2).strip()
        if (len(n1) == 0) or (len(n2) == 0):
            return 0
        # if the names are too short, return
        if len(n1) <= 3 or len(n2) <= 3:
            return 0
        length = max(compare_words(n1, n2), compare_words(n2, n1))
        if length > 0:
            return length
        # remove any numbers
        n1 = re.sub(r'[\d ]', '', n1).strip()
        n2 = re.sub(r'[\d ]', '', n2).strip()
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
            if dcs_name in ['Player', 'Spieler', 'Jugador', 'Joueur', 'Игрок']:
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

    def register_server(self, data: dict) -> bool:
        installations = utils.findDCSInstallations(data['server_name'])
        if len(installations) == 0:
            self.log.error(f"No server {data['server_name']} found in any serverSettings.lua.\n"
                           f"Please check your server configurations!")
            return False
        for _, installation in installations:
            if installation in self.config:
                break
        else:
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
        if data['channel'].startswith('sync-'):
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
            if server.status in [Status.UNREGISTERED, Status.SHUTDOWN]:
                continue
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
                            server.rename(data['server_name'])
                            if server_name in self.servers:
                                del self.servers[server_name]
                        else:
                            self.log.warning(
                                f"Registration of server \"{data['server_name']}\" aborted due to UDP port conflict.")
                            del self.servers[data['server_name']]
                            return False
                cursor.execute('INSERT INTO servers (server_name, agent_host, host, port) VALUES(%s, %s, %s, '
                               '%s) ON CONFLICT (server_name) DO UPDATE SET agent_host=excluded.agent_host, '
                               'host=excluded.host, port=excluded.port, last_seen=NOW()',
                               (data['server_name'], platform.node(), data['host'], data['port']))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
        self.log.debug(f"Server {server.name} initialized")
        return True

    async def get_server(self, ctx: Union[commands.Context, discord.Interaction, discord.Message, str]) -> Optional[Server]:
        if self.master and len(self.servers) == 1 and self.master_only:
            return list(self.servers.values())[0]
        for server_name, server in self.servers.items():
            if isinstance(ctx, commands.Context) or isinstance(ctx, discord.Interaction) \
                    or isinstance(ctx, discord.Message):
                if server.status == Status.UNREGISTERED:
                    continue
                channels = [Channel.ADMIN, Channel.STATUS]
                if int(self.config[server.installation].get(Channel.EVENTS.value, '-1')) != -1:
                    channels.append(Channel.EVENTS)
                if int(self.config[server.installation][Channel.CHAT.value]) != -1:
                    channels.append(Channel.CHAT)
                if int(self.config[server.installation].get(Channel.COALITION_BLUE_EVENTS.value, '-1')) != -1:
                    channels.append(Channel.COALITION_BLUE_EVENTS)
                if int(self.config[server.installation][Channel.COALITION_BLUE_CHAT.value]) != -1:
                    channels.append(Channel.COALITION_BLUE_CHAT)
                if int(self.config[server.installation].get(Channel.COALITION_RED_EVENTS.value, '-1')) != -1:
                    channels.append(Channel.COALITION_RED_EVENTS)
                if int(self.config[server.installation][Channel.COALITION_RED_CHAT.value]) != -1:
                    channels.append(Channel.COALITION_RED_CHAT)
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
                    self.log.warning('Message without server_name received: {}'.format(data))
                    return
                self.log.debug('{}->HOST: {}'.format(data['server_name'], json.dumps(data)))
                if 'channel' in data and data['channel'].startswith('sync-'):
                    if data['channel'] in self.listeners:
                        f = self.listeners[data['channel']]
                        if not f.done():
                            self.loop.call_soon_threadsafe(f.set_result, data)
                        if data['command'] != 'registerDCSServer':
                            return
                server_name = data['server_name']
                if server_name not in s.server.message_queue:
                    s.server.message_queue[server_name] = Queue()
                    s.server.executor.submit(s.process, server_name)
                s.server.message_queue[server_name].put(data)

            def process(s, server_name: str):
                data = s.server.message_queue[server_name].get()
                server: Server = self.servers[server_name]
                while len(data):
                    try:
                        command = data['command']
                        if command == 'registerDCSServer':
                            if not self.register_server(data):
                                self.log.error(f"Error while registering server {server_name}.")
                                return
                        elif server_name not in self.servers or server.status == Status.UNREGISTERED:
                            self.log.debug(
                                f"Command {command} for unregistered server {server_name} received, ignoring.")
                            continue
                        concurrent.futures.wait(
                            [
                                asyncio.run_coroutine_threadsafe(listener.processEvent(command, server, deepcopy(data)),
                                                                 self.loop)
                                for listener in self.eventListeners
                                if listener.has_event(command)
                            ]
                        )
                    except Exception as ex:
                        self.log.exception(ex)
                    finally:
                        s.server.message_queue[server_name].task_done()
                        data = s.server.message_queue[server_name].get()

        class MyThreadingUDPServer(ThreadingUDPServer):
            def __init__(self, server_address: Tuple[str, int], request_handler: Callable[..., BaseRequestHandler],
                         bot: DCSServerBot):
                self.bot = bot
                self.log = bot.log
                self.executor = bot.executor
                # enable reuse, in case the restart was too fast and the port was still in TIME_WAIT
                MyThreadingUDPServer.allow_reuse_address = True
                MyThreadingUDPServer.max_packet_size = 65504
                self.message_queue: dict[str, Queue[str]] = {}
                super().__init__(server_address, request_handler)

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
