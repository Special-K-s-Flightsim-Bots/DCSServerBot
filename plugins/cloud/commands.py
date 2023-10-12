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

from contextlib import closing
from core import Plugin, utils, TEventListener, PaginationReport, Group, DEFAULT_TAG, PluginConfigurationError
from discord import app_commands
from discord.ext import commands, tasks
from psycopg.rows import dict_row
from typing import Type, Any, Optional, Union

from services import DCSServerBot
from .listener import CloudListener


class CloudHandler(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not len(self.locals):
            raise commands.ExtensionFailed(self.plugin_name, FileNotFoundError("No cloud.yaml available."))
        self.config = self.get_config()
        if not self.config:
            raise PluginConfigurationError(plugin=self.plugin_name, option=DEFAULT_TAG)
        self.base_url = f"{self.config['protocol']}://{self.config['host']}:{self.config['port']}"
        self._session = None
        self.client = None
        if self.config.get('dcs-ban', False) or self.config.get('discord-ban', False):
            self.cloud_bans.add_exception_type(aiohttp.ClientError)
            self.cloud_bans.add_exception_type(discord.Forbidden)
            self.cloud_bans.add_exception_type(psycopg.DatabaseError)
            self.cloud_bans.start()
        if 'token' in self.config:
            self.cloud_sync.add_exception_type(IndexError)
            self.cloud_sync.add_exception_type(aiohttp.ClientError)
            self.cloud_sync.add_exception_type(psycopg.DatabaseError)
            self.cloud_sync.start()
        if self.config.get('register', True):
            self.register.start()

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

    async def cog_unload(self) -> None:
        if self.config.get('register', True):
            self.register.cancel()
        if 'token' in self.config:
            self.cloud_sync.cancel()
        if self.config.get('dcs-ban', False) or self.config.get('discord-ban', False):
            self.cloud_bans.cancel()
        asyncio.create_task(self.session.close())
        await super().cog_unload()

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            self.log.info('No cloud.yaml found, copying the sample.')
            shutil.copyfile('config/samples/plugins/cloud.yaml', 'config/plugins/cloud.yaml')
            config = super().read_locals()
        return config

    async def get(self, request: str) -> Any:
        url = f"{self.base_url}/{request}"
        async with self.session.get(url) as response:  # type: aiohttp.ClientResponse
            return await response.json()

    async def post(self, request: str, data: Any) -> Any:
        async def send(element):
            url = f"{self.base_url}/{request}/"
            async with self.session.post(url, json=element) as response:  # type: aiohttp.ClientResponse
                return await response.json()

        if isinstance(data, list):
            for line in data:
                await send(line)
        else:
            await send(data)

    # New command group "/cloud"
    cloud = Group(name="cloud", description="Commands to manage the DCSSB Cloud Service")

    @cloud.command(description='Test the cloud-connection')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def status(self, interaction: discord.Interaction):
        await interaction.response.send_message(f'Checking cloud connection ...', ephemeral=True)
        try:
            await self.get('verify')
            await interaction.followup.send(f'Cloud connection established.', ephemeral=True)
            return
        except aiohttp.ClientError:
            await interaction.followup.send(f'Cloud not connected.', ephemeral=True)
        finally:
            await interaction.delete_original_response()

    @cloud.command(description='Resync statistics with the cloud')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(member="user")
    async def resync(self, interaction: discord.Interaction,
                     member: Optional[app_commands.Transform[Union[discord.Member, str], utils.UserTransformer]] = None):
        if 'token' not in self.config:
            await interaction.response.send_message('No cloud sync configured.', ephemeral=True)
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                sql = 'UPDATE players SET synced = false'
                if member:
                    if isinstance(member, str):
                        sql += ' WHERE ucid = %s'
                    else:
                        sql += ' WHERE discord_id = %s'
                        member = member.id
                conn.execute(sql, (member, ))
                await interaction.response.send_message('Resync with cloud triggered.', ephemeral=True)

    @cloud.command(description='Generate Cloud Statistics')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def statistics(self, interaction: discord.Interaction,
                         user: Optional[app_commands.Transform[Union[discord.Member, str], utils.UserTransformer]],
                         period: Optional[str]):
        if 'token' not in self.config:
            await interaction.response.send_message('Cloud statistics are not activated in this Discord.',
                                                    ephemeral=True)
            return
        if not user:
            user = interaction.user
        if isinstance(user, discord.Member):
            member = user
            name = member.display_name
            ucid = self.bot.get_ucid_by_member(member)
        else:
            ucid = user
            member, name = self.bot.get_member_or_name_by_ucid(ucid)
        await interaction.response.defer()
        try:
            response = await self.get(f'stats/{ucid}')
            if not len(response):
                await interaction.followup.send('No cloud-based statistics found for this user.', ephemeral=True)
                return
            # TODO: support period
            df = pd.DataFrame(response)
            report = PaginationReport(self.bot, interaction, self.plugin_name, 'cloudstats.json')
            await report.render(user=name, data=df, guild=None)
        except aiohttp.ClientError:
            await interaction.followup.send('Cloud not connected.', ephemeral=True)

    @tasks.loop(minutes=15.0)
    async def cloud_bans(self):
        if self.config.get('dcs-ban', False):
            bans: list[str] = [x['ucid'] for x in self.bus.bans() if x['banned_by'] == self.plugin_name]
            for ban in await self.get('bans'):
                if ban['ucid'] not in bans:
                    self.bus.ban(ucid=ban['ucid'], reason='DGSA: ' + ban['reason'], banned_by=self.plugin_name)
                bans.remove(ban['ucid'])
            # we might need to unban someone that is no longer on the list
            for unban in bans:
                self.bus.unban(unban)
        if self.config.get('discord-ban', False):
            users_to_ban = [await self.bot.fetch_user(x['discord_id']) for x in await self.get('discord-bans')]
            guild = self.bot.guilds[0]
            guild_bans = [entry async for entry in guild.bans()]
            banned_users = [x.user for x in guild_bans if x.reason and x.reason.startswith('DGSA:')]
            # unban users that should not be banned anymore
            for user in [x for x in banned_users if x not in users_to_ban]:
                await guild.unban(user, reason='DGSA: ban revoked.')
            # ban users that were not banned yet
            for user in [x for x in users_to_ban if x not in banned_users]:
                if user.id == self.bot.owner_id:
                    continue
                reason = next(x['reason'] for x in bans if x['discord_id'] == user.id)
                await guild.ban(user, reason='DGSA: ' + reason)

    @tasks.loop(seconds=10)
    async def cloud_sync(self):
        with self.pool.connection() as conn:
            with conn.pipeline():
                with conn.transaction():
                    with closing(conn.cursor(row_factory=dict_row)) as cursor:
                        for row in cursor.execute("""
                            SELECT ucid FROM players 
                            WHERE synced IS FALSE 
                            ORDER BY last_seen DESC 
                            LIMIT 10
                        """).fetchall():
                            cursor.execute("""
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
                                       ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime 
                                FROM statistics s, missions m 
                                WHERE s.player_ucid = %s AND s.hop_off IS NOT null AND s.mission_id = m.id 
                                GROUP BY 1, 2, 3
                            """, (row['ucid'], ))
                            for line in cursor.fetchall():
                                line['client'] = self.client
                                await self.post('upload', line)
                            cursor.execute('UPDATE players SET synced = TRUE WHERE ucid = %s', (row['ucid'], ))

    @tasks.loop(hours=24)
    async def register(self):
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT count(distinct node) as num_bots, count(distinct server_name) as num_servers 
                    FROM servers WHERE last_seen > (DATE(NOW()) - interval '1 week')
                """)
                if cursor.rowcount == 0:
                    num_bots = num_servers = 0
                else:
                    row = cursor.fetchone()
                    num_bots = row[0]
                    num_servers = row[1]
        try:
            _, dcs_version = await self.bot.node.get_dcs_branch_and_version()
            bot = {
                "guild_id": self.bot.guilds[0].id,
                "bot_version": f"{self.bot.version}.{self.bot.sub_version}",
                "variant": "DCSServerBot",
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
        except Exception as error:
            self.log.debug("Error while registering: " + str(error))

    @register.before_loop
    async def before_register(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    if not os.path.exists('config/plugins/cloud.yaml'):
        bot.log.info('No cloud.yaml found, copying the sample.')
        shutil.copyfile('config/samples/plugins/cloud.yaml', 'config/plugins/cloud.yaml')
    await bot.add_cog(CloudHandler(bot, CloudListener))
