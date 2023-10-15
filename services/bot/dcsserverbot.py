import asyncio
import discord
import re

from contextlib import closing
from core import NodeImpl, ServiceRegistry, EventListener, Server, Channel, utils, Player, Status
from datetime import datetime
from discord.ext import commands
from functools import lru_cache
from typing import Optional, Union, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..servicebus import ServiceBus

__all__ = ["DCSServerBot"]


class DCSServerBot(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.version: str = kwargs['version']
        self.sub_version: str = kwargs['sub_version']
        self.node: NodeImpl = kwargs['node']
        self.pool = self.node.pool
        self.log = self.node.log
        self.locals = kwargs['locals']
        self.plugins = self.node.plugins
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")
        self.eventListeners: list[EventListener] = self.bus.eventListeners
        self.audit_channel = None
        self.mission_stats = None
        self.member: Optional[discord.Member] = None
        self.lock: asyncio.Lock = asyncio.Lock()
        self.synced: bool = False
        self.tree.on_error = self.on_app_command_error

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        self.synced: bool = False
        await super().start(token, reconnect=reconnect)

    async def close(self):
        try:
            await self.audit(message="Discord Bot stopped.")
        except Exception:
            pass
        self.log.info('- Unloading Plugins ...')
        await super().close()
        self.log.info("- Stopping Services ...")

    @property
    def roles(self) -> dict[str, list[Union[str, int]]]:
        return {
            "Admin": ["Admin"],
            "DCS Admin": ["DCS Admin"],
            "DCS": ["DCS"],
            "GameMaster": ["GameMaster"]
        } | self.locals.get('roles', {})

    @property
    def filter(self) -> dict:
        return self.bus.filter

    @property
    def servers(self) -> dict[str, Server]:
        return self.bus.servers

    async def setup_hook(self) -> None:
        self.log.info('- Loading Plugins ...')
        for plugin in self.plugins:
            if not await self.load_plugin(plugin.lower()):
                self.log.info(f'  => {plugin.title()} NOT loaded.')
        # cleanup remote servers (if any)
        for key, value in self.bus.servers.copy().items():
            if value.is_remote:
                del self.bus.servers[key]

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
            self.log.exception(ex.original if ex.original else ex)
        except Exception as ex:
            self.log.exception(ex)
        return False

    async def unload_plugin(self, plugin: str) -> bool:
        try:
            await self.unload_extension(f'plugins.{plugin}.commands')
            return True
        except commands.ExtensionNotFound:
            self.log.debug(f'- No init.py found for plugin "{plugin}!"')
            pass
        except commands.ExtensionNotLoaded:
            pass
        return False

    async def reload_plugin(self, plugin: str) -> bool:
        if await self.unload_plugin(plugin):
            return await self.load_plugin(plugin)
        else:
            return False

    def check_roles(self, roles: list, server: Optional[Server] = None):
        config_roles = []
        base = server or self
        for role in roles:
            config_roles.extend(base.locals.get('roles', {}).get(role, [role]))
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

    def check_channels(self, server: Server):
        channels = ['status', 'chat']
        if not self.locals.get('admin_channel'):
            channels.append('admin')
        if server.locals.get('coalitions'):
            channels.extend(['red', 'blue'])
        for c in channels:
            channel_id = server.channels[Channel(c)]
            if channel_id != -1:
                self.check_channel(channel_id)

    async def on_ready(self):
        try:
            await self.wait_until_ready()
            if not self.synced:
                self.log.info(f'- Logged in as {self.user.name} - {self.user.id}')
                if len(self.guilds) > 1:
                    self.log.warning('  => YOUR BOT IS INSTALLED IN MORE THAN ONE GUILD. THIS IS NOT SUPPORTED!')
                    for guild in self.guilds:
                        self.log.warning(f'     - {guild.name}')
                    self.log.warning('  => Remove it from one guild and restart the bot.')
                self.member = self.guilds[0].get_member(self.user.id)
                self.log.info('- Checking Roles & Channels ...')
                self.check_roles(['Admin', 'DCS Admin', 'DCS', 'GameMaster'])
                if self.locals.get('admin_channel'):
                    self.check_channel(self.locals['admin_channel'])
                for server in self.servers.values():
                    if server.locals.get('coalitions'):
                        roles = []
                        roles.extend([x.strip() for x in server.locals['coalitions']['blue_role'].split(',')])
                        roles.extend([x.strip() for x in server.locals['coalitions']['red_role'].split(',')])
                        self.check_roles(roles, server)
                    self.check_channels(server)
                self.log.info('- Registering Discord Commands (this might take a bit) ...')
                self.tree.copy_global_to(guild=self.guilds[0])
                await self.tree.sync(guild=self.guilds[0])
                self.synced = True
                self.log.info('- Discord Commands registered.')
                if 'discord_status' in self.locals:
                    await self.change_presence(activity=discord.Game(name=self.locals['discord_status']))
                self.log.info('DCSServerBot MASTER started, accepting commands.')
                await self.audit(message="Discord Bot started.")
            else:
                self.log.warning('- Discord connection re-established.')
        except Exception as ex:
            self.log.exception(ex)

    async def on_command_error(self, ctx: commands.Context, err: Exception):
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
        else:
            self.log.exception(err)
            await ctx.send("An unknown exception occurred.")

    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CommandNotFound):
            pass
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if isinstance(error, discord.app_commands.NoPrivateMessage):
            await interaction.followup.send(f"{interaction.command.name} can't be used in a DM.")
        elif isinstance(error, discord.app_commands.CheckFailure):
            await interaction.followup.send(f"You don't have the permission to use {interaction.command.name}!",
                                            ephemeral=True)
        elif isinstance(error, asyncio.TimeoutError):
            await interaction.followup.send('A timeout occurred. Is the DCS server running?', ephemeral=True)
        elif isinstance(error, discord.app_commands.TransformerError):
            await interaction.followup.send(error, ephemeral=True)
        else:
            self.log.exception(error)
            await interaction.followup.send("An unknown exception occurred.", ephemeral=True)

    async def reload(self, plugin: Optional[str] = None) -> bool:
        if plugin:
            return await self.reload_plugin(plugin)
        else:
            rc = True
            for plugin in self.plugins:
                if not await self.reload_plugin(plugin):
                    rc = False
            return rc

    async def audit(self, message, *, user: Optional[Union[discord.Member, str]] = None,
                    server: Optional[Server] = None):
        if not self.audit_channel:
            if 'audit_channel' in self.locals:
                self.audit_channel = self.get_channel(int(self.locals['audit_channel']))
        if self.audit_channel:
            if isinstance(user, str):
                member = self.get_member_by_ucid(user) if utils.is_ucid(user) else None
            else:
                member = user
            embed = discord.Embed(color=discord.Color.blue())
            if member:
                embed.set_author(name=member.name, icon_url=member.avatar)
                embed.set_thumbnail(url=member.avatar)
                message = f'<@{member.id}> ' + message
            elif not user:
                embed.set_author(name=self.member.name, icon_url=self.member.avatar)
                embed.set_thumbnail(url=self.member.avatar)
            embed.description = message
            if isinstance(user, str):
                embed.add_field(name='UCID', value=user)
            if server:
                embed.add_field(name='Server', value=server.display_name)
            embed.set_footer(text=datetime.now().strftime("%d/%m/%y %H:%M:%S"))
            await self.audit_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))

    @lru_cache
    def get_channel(self, channel_id: int):
        return super().get_channel(channel_id) if channel_id != -1 else None

    def get_admin_channel(self, server: Server):
        admin_channel = self.locals.get('admin_channel')
        if not admin_channel:
            admin_channel = server.channels[Channel.ADMIN]
        return self.get_channel(admin_channel)

    def get_ucid_by_name(self, name: str) -> Tuple[Optional[str], Optional[str]]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                search = f'%{name}%'
                cursor.execute('SELECT ucid, name FROM players WHERE LOWER(name) like LOWER(%s) '
                               'ORDER BY last_seen DESC LIMIT 1', (search, ))
                if cursor.rowcount >= 1:
                    res = cursor.fetchone()
                    return res[0], res[1]
                else:
                    return None, None

    def get_member_or_name_by_ucid(self, ucid: str, verified: bool = False) -> Optional[Union[discord.Member, str]]:
        with self.pool.connection() as conn:
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

    def get_ucid_by_member(self, member: discord.Member, verified: Optional[bool] = False) -> Optional[str]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                sql = 'SELECT ucid FROM players WHERE discord_id = %s AND LENGTH(ucid) = 32 '
                if verified:
                    sql += 'AND manual IS TRUE '
                sql += 'ORDER BY last_seen DESC'
                cursor.execute(sql, (member.id, ))
                if cursor.rowcount >= 1:
                    return cursor.fetchone()[0]
                else:
                    return None

    def get_member_by_ucid(self, ucid: str, verified: Optional[bool] = False) -> Optional[discord.Member]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                sql = 'SELECT discord_id FROM players WHERE ucid = %s AND discord_id <> -1'
                if verified:
                    sql += ' AND manual IS TRUE'
                cursor.execute(sql, (ucid, ))
                if cursor.rowcount == 1:
                    return self.guilds[0].get_member(cursor.fetchone()[0])
                else:
                    return None

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
        tag_filter = self.filter.get('tag_filter')
        if isinstance(data, dict):
            if not rematch:
                member = self.get_member_by_ucid(data['ucid'])
                if member:
                    return member
            # we could not find the user, so try to match them
            dcs_name = re.sub(tag_filter, '', data['name']).strip() if tag_filter else data['name']
            # we do not match the default names
            if dcs_name in [
                'Player',
                'Joueur',
                'Spieler',
                'Игрок',
                'Jugador',
                '玩家',
                'Hráč',
                '플레이어'
            ]:
                return None
            # a minimum of 3 characters have to match
            max_weight = 3
            best_fit = list[discord.Member]()
            for member in self.get_all_members():  # type: discord.Member
                # don't match bot users
                if member.bot:
                    continue
                name = re.sub(tag_filter, '', member.name).strip() if tag_filter else member.name
                if member.display_name:
                    nickname = re.sub(tag_filter, '', member.display_name).strip() if tag_filter else member.display_name
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
            with self.pool.connection() as conn:
                sql = 'SELECT ucid, name from players'
                if rematch is False:
                    sql += ' WHERE discord_id = -1 AND name IS NOT NULL'
                for row in conn.execute(sql).fetchall():
                    name = re.sub(tag_filter, '', data.name).strip() if tag_filter else data.name
                    if data.display_name:
                        nickname = re.sub(tag_filter, '', data.display_name).strip() if tag_filter else data.display_name
                        weight = max(self.match(nickname, row['name']), self.match(name, row['name']))
                    else:
                        weight = self.match(name, row[1])
                    if weight > max_weight:
                        max_weight = weight
                        best_fit = row[0]
                return best_fit

    async def get_server(self, ctx: Union[discord.Interaction, discord.Message, str]) \
            -> Optional[Server]:
        if int(self.locals.get('admin_channel', 0)) == ctx.channel.id and len(self.servers) == 1:
            return list(self.servers.values())[0]
        for server_name, server in self.servers.items():
            if isinstance(ctx, commands.Context) or isinstance(ctx, discord.Interaction) \
                    or isinstance(ctx, discord.Message):
                if server.status == Status.UNREGISTERED:
                    continue
                for channel in [Channel.ADMIN, Channel.STATUS, Channel.EVENTS, Channel.CHAT,
                                Channel.COALITION_BLUE_EVENTS, Channel.COALITION_BLUE_CHAT,
                                Channel.COALITION_RED_EVENTS, Channel.COALITION_RED_CHAT]:
                    if int(server.locals['channels'].get(channel.value, -1)) != -1 and \
                            server.channels[channel] == ctx.channel.id:
                        return server
            else:
                if server_name == ctx:
                    return server
        return None

    async def setEmbed(self, *, embed_name: str, embed: discord.Embed, channel_id: Union[Channel, int] = Channel.STATUS,
                       file: Optional[discord.File] = None, server: Optional[Server] = None):
        async with self.lock:
            if server and isinstance(channel_id, Channel):
                channel_id = server.channels[channel_id]
            channel = self.get_channel(channel_id)
            if not channel:
                self.log.error(f"Channel {channel_id} not found, can't add or change an embed in there!")
                return

            with self.pool.connection() as conn:
                # check if we have a message persisted already
                row = conn.execute("""
                    SELECT embed FROM message_persistence 
                    WHERE server_name = %s AND embed_name = %s
                """, (server.name if server else 'Master', embed_name)).fetchone()

            message = None
            if row:
                try:
                    message = await channel.fetch_message(row[0])
                    if not file:
                        await message.edit(embed=embed)
                    else:
                        await message.edit(embed=embed, attachments=[file])
                except discord.errors.NotFound:
                    message = None
                except discord.errors.DiscordException as ex:
                    self.log.warning(f"Error during update of embed {embed_name}: " + str(ex))
                    return
            if not row or not message:
                message = await channel.send(embed=embed, file=file)
                with self.pool.connection() as conn:
                    with conn.transaction():
                        conn.execute("""
                            INSERT INTO message_persistence (server_name, embed_name, embed) 
                            VALUES (%s, %s, %s) 
                            ON CONFLICT (server_name, embed_name) 
                            DO UPDATE SET embed=excluded.embed
                        """, (server.name if server else 'Master', embed_name, message.id))
