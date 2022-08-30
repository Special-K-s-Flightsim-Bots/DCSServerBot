# noinspection PyPackageRequirements
import aiohttp
import asyncio
import discord
import pandas as pd
import psycopg2
from contextlib import closing
from core import Plugin, DCSServerBot, utils, TEventListener, PaginationReport, Status
from discord.ext import commands, tasks
from typing import Type, Any, Optional, Union
from .listener import CloudListener


class CloudHandlerAgent(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not len(self.read_locals()):
            raise commands.ExtensionFailed(self.plugin_name, FileNotFoundError("No cloud.json available."))
        self.config = self.locals['configs'][0]
        headers = {
            "Content-type": "application/json"
        }
        if 'token' in self.config:
            headers['Authorization'] = f"Bearer {self.config['token']}"

        self.session = aiohttp.ClientSession(raise_for_status=True, headers=headers)
        self.base_url = f"{self.config['protocol']}://{self.config['host']}:{self.config['port']}"
        self.client = {
            "guild_id": self.bot.guilds[0].id,
            "guild_name": self.bot.guilds[0].name,
            "owner_id": self.bot.owner_id
        }
        self.cloud_bans.start()

    async def cog_unload(self):
        self.cloud_bans.cancel()
        asyncio.create_task(self.session.close())
        await super().cog_unload()

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

    @commands.command(description='Checks the connection to the DCSServerBot cloud')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def cloud(self, ctx):
        message = await ctx.send('Checking cloud connection ...')
        try:
            await self.get('verify')
            await ctx.send('Cloud connection established.')
            return
        except aiohttp.ClientError:
            await ctx.send('Cloud not connected.')
        finally:
            await message.delete()

    @tasks.loop(minutes=15.0)
    async def cloud_bans(self):
        try:
            for ban in (await self.get('bans')):
                for server in self.bot.servers.values():
                    if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                        server.sendtoDCS({
                            "command": "ban",
                            "ucid": ban["ucid"],
                            "reason": ban["reason"]
                        })
        except aiohttp.ClientError:
            self.log.error('- Cloud service not responding.')


class CloudHandlerMaster(CloudHandlerAgent):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if 'token' in self.config:
            self.cloud_sync.start()

    async def cog_unload(self):
        if 'token' in self.config:
            self.cloud_sync.cancel()
        await super().cog_unload()

    @commands.command(description='Resync all statistics with the cloud', usage='[ucid / @member]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def resync(self, ctx, member: Optional[Union[str, discord.Member]] = None):
        if 'token' not in self.config:
            await ctx.send('No cloud sync configured.')
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                sql = 'UPDATE players SET synced = false'
                if member:
                    if isinstance(member, str):
                        sql += ' WHERE ucid = %s'
                    else:
                        sql += ' WHERE discord_id = %s'
                        member = member.id
                cursor.execute(sql, (member, ))
                conn.commit()
                await ctx.send('Resync with cloud triggered.')
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Generate Cloud Statistics', usage='[@member]', aliases=['cstats', 'globalstats', 'gstats'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def cloudstats(self, ctx, member: Optional[discord.Member] = None):
        try:
            if 'token' not in self.config:
                await ctx.send('Cloud statistics are not activated on this server.')
                return
            if not member:
                member = ctx.message.author
            ucid = self.bot.get_ucid_by_member(member)
            if not ucid:
                await ctx.send(f'The account is not properly linked. Use {ctx.prefix}linkme to link your Discord and DCS accounts.')
                return
            response = await self.get(f'stats/{ucid}')
            if not len(response):
                await ctx.send('No cloud-based statistics found for this user.')
                return
            df = pd.DataFrame(response)
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'cloudstats.json', timeout if timeout > 0 else None)
            await report.render(member=member, data=df, guild=None)
        finally:
            await ctx.message.delete()

    @tasks.loop(minutes=1.0)
    async def cloud_sync(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute('SELECT ucid FROM players WHERE synced IS FALSE AND discord_id <> -1 ORDER BY '
                               'last_seen DESC LIMIT 10')
                for row in cursor.fetchall():
                    cursor.execute('SELECT s.player_ucid, m.mission_theatre, s.slot, SUM(s.kills) as kills, '
                                   'SUM(s.pvp) as pvp, SUM(deaths) as deaths, SUM(ejections) as ejections, '
                                   'SUM(crashes) as crashes, SUM(teamkills) as teamkills, SUM(kills_planes) AS '
                                   'kills_planes, SUM(kills_helicopters) AS kills_helicopters, SUM(kills_ships) AS '
                                   'kills_ships, SUM(kills_sams) AS kills_sams, SUM(kills_ground) AS kills_ground, '
                                   'SUM(deaths_pvp) as deaths_pvp, SUM(deaths_planes) AS deaths_planes, '
                                   'SUM(deaths_helicopters) AS deaths_helicopters, SUM(deaths_ships) AS deaths_ships, '
                                   'SUM(deaths_sams) AS deaths_sams, SUM(deaths_ground) AS deaths_ground, '
                                   'SUM(takeoffs) as takeoffs, SUM(landings) as landings, ROUND(SUM( '
                                   'EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime FROM statistics s, '
                                   'missions m WHERE s.player_ucid = %s AND s.hop_off IS NOT null AND s.mission_id = '
                                   'm.id GROUP BY 1, 2, 3', (row['ucid'], ))
                    for line in cursor.fetchall():
                        line['client'] = self.client
                        await self.post('upload', line)
                    cursor.execute('UPDATE players SET synced = TRUE WHERE ucid = %s', (row['ucid'], ))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)


async def setup(bot: DCSServerBot):
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(CloudHandlerMaster(bot, CloudListener))
    else:
        await bot.add_cog(CloudHandlerAgent(bot, CloudListener))
