import aiohttp
import asyncio
import certifi
import discord
import os
import pandas as pd
import platform
import psycopg
import shutil
import ssl

from contextlib import suppress
from core import Plugin, utils, PaginationReport, Group, DEFAULT_TAG, PluginConfigurationError, \
    get_translation, command
from datetime import timedelta
from discord import app_commands, DiscordServerError
from discord.ext import commands, tasks
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from services.bot.dummy import DummyBot
from typing import Type, Any
from urllib.parse import quote

from .listener import CloudListener
from .logger import CloudLoggingHandler

_ = get_translation(__name__.split('.')[1])


class Cloud(Plugin[CloudListener]):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[CloudListener] = None):
        super().__init__(bot, eventlistener)
        if not len(self.locals):
            raise commands.ExtensionFailed(self.plugin_name, FileNotFoundError("No cloud.yaml available."))
        self.config = self.get_config()
        if not self.config:
            raise PluginConfigurationError(plugin=self.plugin_name, option=DEFAULT_TAG)
        self.base_url = None
        self._session = None
        self.client = None
        self.guild_bans = []

    @property
    def session(self):
        if not self._session:
            headers = {
                "Content-type": "application/json"
            }
            if 'token' in self.config:
                headers['Authorization'] = f"Bearer {self.config['token']}"
            self.client = {
                "guild_id": self.bot.guilds[0].id,
                "guild_name": self.bot.guilds[0].name,
                "owner_id": self.bot.owner_id
            }
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
                raise_for_status=True, headers=headers
            )
        return self._session

    async def cog_load(self):
        await super().cog_load()
        self.base_url = f"{self.config['protocol']}://{self.config['host']}:{self.config['port']}"
        self._session = None
        self.client = None
        if self.config.get('dcs-ban', False) or self.config.get('discord-ban', False):
            self.cloud_bans.add_exception_type(IndexError)
            self.cloud_bans.add_exception_type(aiohttp.ClientError)
            self.cloud_bans.add_exception_type(discord.Forbidden)
            self.cloud_bans.add_exception_type(psycopg.DatabaseError)
            self.cloud_bans.add_exception_type(DiscordServerError)
            self.cloud_bans.start()
        if 'token' in self.config:
            self.cloud_sync.add_exception_type(IndexError)
            self.cloud_sync.add_exception_type(aiohttp.ClientError)
            self.cloud_sync.add_exception_type(psycopg.DatabaseError)
            self.cloud_sync.start()
        if self.config.get('register', True):
            self.register.start()
        if self.config.get('upload_errors', True):
            cloud_logger = CloudLoggingHandler(node=self.node, url=self.base_url + '/errors/')
            self.log.root.addHandler(cloud_logger)

    async def cog_unload(self) -> None:
        if self.config.get('register', True):
            self.register.cancel()
        if self.config.get('upload_errors', True):
            for handler in self.log.root.handlers:
                if isinstance(handler, CloudLoggingHandler):
                    self.log.removeHandler(handler)
        if 'token' in self.config:
            self.cloud_sync.cancel()
        if self.config.get('dcs-ban', False) or self.config.get('discord-ban', False):
            self.cloud_bans.cancel()
        if self._session:
            asyncio.create_task(self._session.close())
        await super().cog_unload()

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            self.log.info('No cloud.yaml found, copying the sample.')
            shutil.copyfile('samples/plugins/cloud.yaml', os.path.join(self.node.config_dir, 'plugins', 'cloud.yaml'))
            config = super().read_locals()
        return config

    async def get(self, request: str) -> Any:
        url = f"{self.base_url}/{request}"
        async with self.session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
            return await response.json()

    async def post(self, request: str, data: Any) -> Any:
        async def send(element: dict):
            url = f"{self.base_url}/{request}/"
            async with self.session.post(
                    url, json=element, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                return await response.json()

        if isinstance(data, list):
            for line in data:
                await send(line)
        else:
            await send(data)

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        # we must not fail due to a cloud unavailability
        with suppress(Exception):
            await self.post('update_ucid', {"old_ucid": old_ucid, "new_ucid": new_ucid})

    # New command group "/cloud"
    cloud = Group(name="cloud", description="Commands to manage the DCSSB Cloud Service")

    @cloud.command(description=_('Test the cloud-connection'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def status(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Checking cloud connection ...'), ephemeral=ephemeral)
        try:
            await self.get('discord-bans')
            message = _('Cloud connection established.')
            if 'token' in self.config:
                try:
                    await self.get('verify')
                    message += _('\nCloud TOKEN configured and valid.')
                except aiohttp.ClientError:
                    message += _('\nCloud TOKEN configured, but invalid!')
            else:
                message += _("\nGet a cloud TOKEN, if you want to use cloud statistics!")
            await interaction.followup.send(message, ephemeral=ephemeral)
        except aiohttp.ClientError:
            await interaction.followup.send(_('Cloud not connected!'), ephemeral=ephemeral)
        finally:
            await interaction.delete_original_response()

    @cloud.command(description=_('Resync statistics with the cloud'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(member="user")
    async def resync(self, interaction: discord.Interaction,
                     member: app_commands.Transform[discord.Member | str, utils.UserTransformer] | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        if 'token' not in self.config:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('No cloud sync configured!'), ephemeral=True)
            return
        async with self.apool.connection() as conn:
            async with conn.transaction():
                sql = 'UPDATE players SET synced = false'
                if member:
                    if isinstance(member, str):
                        sql += ' WHERE ucid = %s'
                    else:
                        sql += ' WHERE discord_id = %s'
                        member = member.id
                    await conn.execute(sql, (member, ))
                else:
                    await conn.execute(sql)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Resync with cloud triggered.'), ephemeral=ephemeral)

    @cloud.command(description=_('Generate Cloud Statistics'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def statistics(self, interaction: discord.Interaction,
                         user: app_commands.Transform[discord.Member | str, utils.UserTransformer] | None):
        if 'token' not in self.config:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Cloud statistics are not activated in this Discord!'),
                                                    ephemeral=True)
            return
        if not user:
            user = interaction.user
        if isinstance(user, discord.Member):
            ucid = await self.bot.get_ucid_by_member(user)
            if not ucid:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("Use {} to link your account.").format(
                    (await utils.get_command(self.bot, name='linkme')).mention
                ), ephemeral=True)
                return
            name = user.display_name
        else:
            ucid = user
            name = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(name, discord.Member):
                name = name.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        try:
            response = await self.get(f'stats/{ucid}')
            if not len(response):
                await interaction.followup.send(_('No cloud-based statistics found for this user.'), ephemeral=True)
                return
            # TODO: support period
            df = pd.DataFrame(response)
            report = PaginationReport(interaction, self.plugin_name, 'cloudstats.json')
            await report.render(user=name, data=df, guild=None)
        except aiohttp.ClientError:
            await interaction.followup.send(_('Cloud not connected!'), ephemeral=True)

    @command(description=_('List registered DCS servers'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin') # TODO: change that to DCS
    async def serverlist(self, interaction: discord.Interaction, search: str | None = None):

        def format_servers(servers: list[dict], marker, marker_emoji) -> discord.Embed:
            embed = discord.Embed(title=_('DCS Servers'), color=discord.Color.blue())
            for idx, server in enumerate(servers):
                name = chr(0x31 + idx) + '\u20E3' + f" {utils.escape_string(server['server_name'])} [{server['num_players']}/{server['max_players']}]"
                name += (' 🔐' if server['password'] else ' 🔓') + '\n'
                value = f"IP/Port:  {server['ipaddr']}:{server['port']}\n"
                value += f"Map:      {server['theatre']}\n"
                value += f"Time:     {timedelta(seconds=server['time_in_mission'])}\n"
                if server['time_to_restart'] != -1:
                    value += f"Restart:  {timedelta(seconds=server['time_to_restart'])}\n"
                embed.add_field(name=name, value='```' + value + '```', inline=False)
            return embed

        async def display_server(server: dict):
            embed = discord.Embed(color=discord.Color.blue())
            embed.title = f"{utils.escape_string(server['server_name'])} [{server['num_players']}/{server['max_players']}]"
            embed.add_field(name=_("Address"), value=f"{server['ipaddr']}:{server['port']}", inline=False)
            embed.add_field(name=_("Map"), value=f"{server['theatre']}", inline=False)
            embed.add_field(name=_("Mission"), value=f"{utils.escape_string(server['mission'])}", inline=False)
            embed.add_field(name=_("Time"), value=f"{timedelta(seconds=server['time_in_mission'])}", inline=False)
            if server['time_to_restart'] != -1:
                embed.add_field(name=_("Restart in"), value=f"{timedelta(seconds=server['time_to_restart'])}", inline=False)
            msg = await interaction.original_response()
            await msg.edit(embed=embed, delete_after=self.bot.locals.get('message_autodelete'))

        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        try:
            query = f'serverlist?dcs_version={self.node.dcs_version}'
            if search:
                query += f'&wildcard={quote(search)}'
            else:
                query += f'&guild_id={self.node.guild_id}'
            response = await self.get(query)
            if not len(response):
                if not search:
                    await interaction.followup.send(_('No servers of this group are active.'), ephemeral=True)
                else:
                    await interaction.followup.send(
                        _('No server found with the name "*{search}*".').format(search=search), ephemeral=True)
                return
            n = await utils.selection_list(interaction, response, format_servers)
            if n >= 0:
                await display_server(response[n])
        except aiohttp.ClientError:
            await interaction.followup.send(_('Cloud not connected!'), ephemeral=True)


    @tasks.loop(minutes=15.0)
    async def cloud_bans(self):
        try:
            banlist = self.config.get('banlist', 'both').lower()
            if banlist == 'both':
                banlist = None
            if self.config.get('dcs-ban', False):
                dgsa_bans = {item['ucid']: item for item in await self.get('bans')}
                local_bans = {
                    item['ucid']: item for item in await self.bus.bans(expired=True)
                    if item['banned_by'] == self.plugin_name
                }
                # filter bans by scope
                to_ban: set = {
                    ucid for ucid, ban in dgsa_bans.items()
                    if (ban['scope'] == 'Both' or not banlist or ban['scope'].lower() == banlist)
                }
                # find UCIDs to ban (in DGsA bans but not in local bans)
                for ucid in to_ban - local_bans.keys():
                    reason = dgsa_bans[ucid]['reason']
                    await self.bus.ban(ucid=ucid, reason='DGSA: ' + reason, banned_by=self.plugin_name)
                # find UCIDs to unban (in local bans but not in DGSA bans)
                for ucid in local_bans.keys() - to_ban:
                    await self.bus.unban(ucid)
            elif self.config.get('watchlist_only', False):
                dgsa_bans = {item['ucid']: item for item in await self.get('bans')}
                # filter bans by scope
                to_ban: set = {
                    ucid for ucid, ban in dgsa_bans.items()
                    if (ban['scope'] == 'Both' or not banlist or ban['scope'].lower() == banlist)
                }
                async with self.apool.connection() as conn:
                    cursor = await conn.execute("SELECT player_ucid FROM watchlist WHERE created_by = 'DGSA'")
                    watches = set([row[0] for row in await cursor.fetchall()])
                    async with conn.transaction():
                        # find watches to add
                        for ucid in to_ban - watches:
                            reason = dgsa_bans[ucid]['reason']
                            await conn.execute("""
                                INSERT INTO watchlist (player_ucid, reason, created_by) 
                                VALUES (%s, %s, %s)
                                ON CONFLICT (player_ucid) DO NOTHING
                            """, (ucid, reason, 'DGSA'))
                        # find watches to remove
                        for ucid in watches - to_ban:
                            await conn.execute("DELETE FROM watchlist WHERE player_ucid = %s", (ucid,))
            if self.config.get('discord-ban', False):
                global_bans: dict = await self.get('discord-bans')
                global_ban_ids = {x['discord_id'] for x in global_bans}
                if not self.guild_bans:
                    self.guild_bans = [
                        x async for x in self.bot.guilds[0].bans(limit=None) if x.reason and x.reason.startswith('DGSA:')
                    ]
                banned_users = {x.user.id for x in self.guild_bans}

                guild = self.bot.guilds[0]
                # unban users that should not be banned anymore
                for user_id in banned_users - global_ban_ids:
                    user = await self.bot.fetch_user(user_id)
                    await guild.unban(user, reason='DGSA: ban revoked.')
                    self.guild_bans.remove(user)

                # ban users that were not banned yet (omit the owner, in case they are on the global banlist)
                for user_id in global_ban_ids - banned_users - {self.bot.owner_id}:
                    user = await self.bot.fetch_user(user_id)
                    reason = next(x['reason'] for x in global_bans if x['discord_id'] == user.id)
                    await guild.ban(user, reason='DGSA: ' + reason)
                    self.guild_bans.append(user)
        except aiohttp.ClientError:
            self.log.warning("Cloud service unavailable.")
        except discord.Forbidden:
            self.log.error('DCSServerBot needs the "Ban Members" permission.')
        except Exception as ex:
            self.log.exception(ex)

    @cloud_bans.before_loop
    async def before_cloud_bans(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=10)
    async def cloud_sync(self):
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT ucid FROM players 
                WHERE synced IS FALSE 
                ORDER BY last_seen DESC 
                LIMIT 10
            """)
            rows = await cursor.fetchall()
        # We do not want to block the connection pool for an unnecessary amount of time
        for row in rows:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor(row_factory=dict_row) as cursor:
                        await cursor.execute("""
                            SELECT s.player_ucid, m.mission_theatre, s.slot, 
                                   SUM(s.kills) as kills, SUM(s.pvp) as pvp, SUM(deaths) as deaths, 
                                   SUM(ejections) as ejections, SUM(crashes) as crashes, 
                                   SUM(teamkills) as teamkills, SUM(kills_planes) AS kills_planes, 
                                   SUM(kills_helicopters) AS kills_helicopters, SUM(kills_ships) AS kills_ships, 
                                   SUM(kills_sams) AS kills_sams, SUM(kills_ground) AS kills_ground, 
                                   SUM(deaths_pvp) as deaths_pvp, SUM(deaths_planes) AS deaths_planes, 
                                   SUM(deaths_helicopters) AS deaths_helicopters, SUM(deaths_ships) AS deaths_ships,
                                   SUM(deaths_sams) AS deaths_sams, SUM(deaths_ground) AS deaths_ground, 
                                   SUM(takeoffs) as takeoffs, SUM(landings) as landings, 
                                   ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))))::BIGINT AS playtime 
                            FROM statistics s, missions m 
                            WHERE s.player_ucid = %s AND s.hop_off IS NOT null AND s.mission_id = m.id 
                            GROUP BY 1, 2, 3
                        """, (row[0], ))
                        async for line in cursor:
                            try:
                                line['client'] = self.client
                                await self.post('upload', line)
                            except TypeError as ex:
                                self.log.warning(f"Could not replicate user {row[0]}: {ex}")
                        await cursor.execute('UPDATE players SET synced = TRUE WHERE ucid = %s', (row[0], ))

    @cloud_sync.before_loop
    async def before_cloud_sync(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def register(self):
        async with self.apool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT count(distinct node) as num_bots, count(distinct instance) as num_servers 
                    FROM instances WHERE last_seen > (DATE(now() AT TIME ZONE 'utc') - interval '1 week')
                """)
                if cursor.rowcount == 0:
                    num_bots = num_servers = 0
                else:
                    row = await cursor.fetchone()
                    num_bots = row[0]
                    num_servers = row[1]
        try:
            if 'DCS' in self.node.locals:
                _, dcs_version = await self.node.get_dcs_branch_and_version()
            else:
                dcs_version = ""
            # noinspection PyUnresolvedReferences
            bot = {
                "guild_id": self.bot.guilds[0].id,
                "guild_name": self.bot.guilds[0].name,
                "bot_version": f"{self.bot.version}.{self.bot.sub_version}",
                "variant": "DCSServerBot" if not isinstance(self.bot, DummyBot) else "No Bot",
                "dcs_version": dcs_version,
                "python_version": '.'.join(platform.python_version_tuple()),
                "num_bots": num_bots,
                "num_servers": num_servers,
                "plugins": [
                    {
                        "name": p.plugin_name,
                        "version": p.plugin_version
                    } for p in self.bot.cogs.values()
                ]
            }
            self.log.debug("Updating registration with this data: " + str(bot))
            await self.post('register', bot)
        except aiohttp.ClientError:
            self.log.debug('Cloud: Bot could not register due to service unavailability. Ignored.')
        except Exception:
            self.log.debug("Error while registering: ", exc_info=True)

    @register.before_loop
    async def before_register(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    await bot.add_cog(Cloud(bot, CloudListener))
