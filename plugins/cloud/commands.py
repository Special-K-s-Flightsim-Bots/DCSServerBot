import aiohttp
import asyncio
import certifi
import discord
import os
import pandas as pd
import platform
import psycopg2
import shutil
import ssl
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
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
            raise_for_status=True, headers=headers
        )
        self.base_url = f"{self.config['protocol']}://{self.config['host']}:{self.config['port']}"
        self.client = {
            "guild_id": self.bot.guilds[0].id,
            "guild_name": self.bot.guilds[0].name,
            "owner_id": self.bot.owner_id
        }
        if 'dcs-ban' not in self.config or self.config['dcs-ban']:
            self.cloud_bans.start()

    async def cog_unload(self):
        if 'dcs-ban' not in self.config or self.config['dcs-ban']:
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

    @commands.command(description='Test the cloud-connection')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def cloud(self, ctx):
        message = await ctx.send(f'Node {platform.node()}: Checking cloud connection ...')
        try:
            await self.get('verify')
            await ctx.send(f'Node {platform.node()}: Cloud connection established.')
            return
        except aiohttp.ClientError:
            await ctx.send(f'Node {platform.node()}: Cloud not connected.')
        finally:
            await message.delete()

    @tasks.loop(minutes=15.0)
    async def cloud_bans(self):
        try:
            bans = await self.get('bans')
            for server in self.bot.servers.values():
                if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                    for ban in bans:
                        player = server.get_player(ucid=ban["ucid"], active=True)
                        if player:
                            server.sendtoDCS({
                                "command": "ban",
                                "ucid": ban["ucid"],
                                "reason": ban["reason"]
                            })
        except aiohttp.ClientError:
            self.log.warning('- Cloud service not responding.')


class CloudHandlerMaster(CloudHandlerAgent):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if self.config.get('dcs-ban', False) or self.config.get('discord-ban', False):
            self.master_bans.start()
        if 'token' in self.config:
            self.cloud_sync.add_exception_type(aiohttp.ClientError)
            self.cloud_sync.start()
        if self.config.get('register', True):
            self.register.start()

    async def cog_unload(self):
        if self.config.get('register', True):
            self.register.cancel()
        if 'token' in self.config:
            self.cloud_sync.cancel()
        if self.config.get('dcs-ban', False) or self.config.get('discord-ban', False):
            self.master_bans.cancel()
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

    @commands.command(description='Generate Cloud Statistics', usage='[@member|name|ucid]',
                      aliases=['cstats', 'globalstats', 'gstats'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def cloudstats(self, ctx, member: Optional[Union[discord.Member, str]] = None, *params):
        try:
            if 'token' not in self.config:
                await ctx.send('Cloud statistics are not activated on this server.')
                return
            if not member:
                member = ctx.message.author
            if isinstance(member, discord.Member):
                ucid = self.bot.get_ucid_by_member(member)
                if not ucid:
                    member = member.name
            if isinstance(member, str):
                if len(params):
                    member += ' ' + ' '.join(params)
                if utils.is_ucid(member):
                    ucid = member
                    member = self.bot.get_member_or_name_by_ucid(ucid)
                else:
                    ucid, member = self.bot.get_ucid_by_name(member)
            if not ucid:
                await ctx.send(f'This account is not known.')
                return
            response = await self.get(f'stats/{ucid}')
            if not len(response):
                await ctx.send('No cloud-based statistics found for this user.')
                return
            df = pd.DataFrame(response)
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'cloudstats.json',
                                      timeout if timeout > 0 else None)
            await report.render(user=utils.escape_string(member.display_name) if isinstance(member, discord.Member) else member,
                                data=df, guild=None)
        finally:
            await ctx.message.delete()

    @tasks.loop(minutes=15.0)
    async def master_bans(self):
        conn = self.pool.getconn()
        try:
            if self.config.get('dcs-ban', False):
                with closing(conn.cursor()) as cursor:
                    for ban in (await self.get('bans')):
                        cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s) '
                                       'ON CONFLICT DO NOTHING', (ban['ucid'], self.plugin_name, ban['reason']))
                conn.commit()
            if self.config.get('discord-ban', False):
                bans: list[dict] = await self.get('discord-bans')
                users_to_ban = [await self.bot.fetch_user(x['discord_id']) for x in bans]
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
        except aiohttp.ClientError:
            self.log.warning('- Cloud service not responding.')
        except discord.Forbidden:
            self.log.warn('- DCSServerBot does not have the permission to ban users.')
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @tasks.loop(seconds=10)
    async def cloud_sync(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute('SELECT ucid FROM players WHERE synced IS FALSE ORDER BY last_seen DESC LIMIT 10')
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

    @tasks.loop(hours=24)
    async def register(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                     SELECT count(distinct agent_host) as num_bots, count(distinct server_name) as num_servers 
                     FROM servers WHERE last_seen > (DATE(NOW()) - interval '1 week')
                 """)
                if cursor.rowcount == 0:
                    num_bots = 1
                    num_servers = 0
                else:
                    row = cursor.fetchone()
                    num_bots = row[0]
                    num_servers = row[1]
            _, dcs_version = utils.getInstalledVersion(self.bot.config['DCS']['DCS_INSTALLATION'])
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
            self.log.debug('Bot could not register due to service unavailability. Ignored.')
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.debug("Error while registering: " + str(error))
        finally:
            self.pool.putconn(conn)


async def setup(bot: DCSServerBot):
    if not os.path.exists('config/cloud.json'):
        bot.log.info('No cloud.json found, copying the sample.')
        shutil.copyfile('config/samples/cloud.json', 'config/cloud.json')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(CloudHandlerMaster(bot, CloudListener))
    else:
        await bot.add_cog(CloudHandlerAgent(bot, CloudListener))
