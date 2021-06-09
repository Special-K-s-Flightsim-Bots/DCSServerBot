# agent.py
import aiohttp
import asyncio
import datetime
import discord
import fnmatch
import json
import pandas as pd
import platform
import psycopg2
import psycopg2.extras
import socket
import socketserver
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, suppress
from datetime import timedelta
from discord.ext import commands, tasks
from os import listdir
from os.path import expandvars


class Agent(commands.Cog):

    SIDE_UNKNOWN = -1
    SIDE_SPECTATOR = 0
    SIDE_RED = 1
    SIDE_BLUE = 2
    SIDE_NEUTRAL = 3

    PLAYER_SIDES = {
        SIDE_UNKNOWN: 'UNKNOWN',
        SIDE_SPECTATOR: 'SPECTATOR',
        SIDE_RED: 'RED',
        SIDE_BLUE: 'BLUE',
        SIDE_NEUTRAL: 'NEUTRAL'
    }

    STATUS_IMG = {
        'Loading': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe48ed915d1592000048/traffic-light-amber.jpg',
        'Paused': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe48ed915d1592000048/traffic-light-amber.jpg',
        'Running': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe3e40f0b6156700004f/traffic-light-green.jpg',
        'Shutdown': 'https://assets.digital.cabinet-office.gov.uk/media/559fbe1940f0b6156700004d/traffic-light-red.jpg'
    }

    SQL_EVENT_UPDATES = {
        'takeoff': 'UPDATE statistics SET takeoffs = takeoffs + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'landing': 'UPDATE statistics SET landings = landings + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'eject': 'UPDATE statistics SET ejections = ejections + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'crash': 'UPDATE statistics SET crashes = crashes + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'pilot_death': 'UPDATE statistics SET deaths = deaths + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'pvp': 'UPDATE statistics SET kills = kills + 1, pvp = pvp + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'teamkill': 'UPDATE statistics SET teamkills = teamkills + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'kill_planes': 'UPDATE statistics SET kills = kills + 1, kills_planes = kills_planes + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'kill_helicopters': 'UPDATE statistics SET kills = kills + 1, kills_helicopters = kills_helicopters + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'kill_ships': 'UPDATE statistics SET kills = kills + 1, kills_ships = kills_ships + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'kill_sams': 'UPDATE statistics SET kills = kills + 1, kills_sams = kills_sams + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'kill_ground': 'UPDATE statistics SET kills = kills + 1, kills_ground = kills_ground + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'teamdeath': 'UPDATE statistics SET deaths = deaths - 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_pvp': 'UPDATE statistics SET deaths_pvp = deaths_pvp + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_planes': 'UPDATE statistics SET deaths_planes = deaths_planes + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_helicopters': 'UPDATE statistics SET deaths_helicopters = deaths_helicopters + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_ships': 'UPDATE statistics SET deaths_ships = deaths_ships + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_sams': 'UPDATE statistics SET deaths_sams = deaths_sams + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_ground': 'UPDATE statistics SET deaths_ground = deaths_ground + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL'
    }

    EVENT_TEXTS = {
        'takeoff': '{} player {} took off from {}.',
        'landing': '{} player {} landed at {}.',
        'eject': '{} player {} ejected.',
        'crash': '{} player {} crashed.',
        'pilot_death': '{} player {} died.',
        'kill': '{} {} in {} killed {} {} in {} with {}.',
        'friendly_fire': '{} {} FRIENDLY FIRE onto {} with {}.'
    }

    def __init__(self, bot):
        self.bot = bot
        self.mission_embeds = {}
        self.players_embeds = {}
        self.player_data = {}
        self.banList = []
        self.listeners = {}
        self.lock = asyncio.Lock()
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(
                    'SELECT server_name, host, port, chat_channel, status_channel, admin_channel, mission_embed, players_embed, \'Pending...\' AS status FROM servers WHERE agent_host = %s', (platform.node(), ))
                for row in cursor.fetchall():
                    self.bot.DCSServers[row['server_name']] = dict(row)
            self.bot.log.info('{} server(s) read from database.'.format(len(self.bot.DCSServers)))
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)
        self.loop = asyncio.get_event_loop()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.loop.create_task(self.handleUDPRequests())
        self.loop.create_task(self.init_status())

    def cog_unload(self):
        self.update_mission_status.cancel()
        self.server.shutdown()
        self.server.server_close()
        self.executor.shutdown(wait=True)

    async def init_status(self):
        await self.lock.acquire()
        try:
            # TODO: move that to the lua code
            self.external_ip = await self.get_external_ip()
            for server_name, server in self.bot.DCSServers.items():
                channel = await self.bot.fetch_channel(server['status_channel'])
                if ('mission_embed' in server and server['mission_embed']):
                    with suppress(Exception):
                        self.mission_embeds[server_name] = await channel.fetch_message(server['mission_embed'])
                if ('players_embed' in server and server['players_embed']):
                    with suppress(Exception):
                        self.players_embeds[server_name] = await channel.fetch_message(server['players_embed'])
                try:
                    # check for any registration updates (channels, etc)
                    await self.sendtoDCSSync(server, {"command": "registerDCSServer", "channel": -1})
                    # preload players list
                    await self.sendtoDCSSync(server, {"command": "getCurrentPlayers", "channel": server['status_channel']})
                except asyncio.TimeoutError:
                    server['status'] = 'Shutdown'
        finally:
            self.lock.release()
        self.update_mission_status.start()

    async def wait_for_single_reaction(self, ctx, message):
        def check_press(react, user):
            return (react.message.channel == ctx.message.channel) & (user == ctx.message.author) & (react.message.id == message.id)

        pending_tasks = [self.bot.wait_for('reaction_add', check=check_press, timeout=300.0),
                         self.bot.wait_for('reaction_remove', check=check_press, timeout=300.0)]
        done_tasks, pending_tasks = await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)
        react, user = done_tasks.pop().result()
        # kill the remaining task
        pending_tasks.pop().cancel()
        return react

    async def get_server(self, ctx):
        server = None
        for key, item in self.bot.DCSServers.items():
            if ((int(item['status_channel']) == ctx.channel.id) or
                (int(item['chat_channel']) == ctx.channel.id) or
                    (int(item['admin_channel']) == ctx.channel.id)):
                server = item
                break
        return server

    def sendtoDCS(self, server, message):
        # As Lua does not support large numbers, convert them to strings
        for key, value in message.items():
            if (type(value) == int):
                message[key] = str(value)
        msg = json.dumps(message)
        self.bot.log.info('HOST->{}: {}'.format(server['server_name'], msg))
        DCSSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        DCSSocket.sendto(msg.encode('utf-8'), (server['host'], server['port']))

    def sendtoDCSSync(self, server, message, timeout=5):
        self.sendtoDCS(server, message)
        future = self.loop.create_future()
        try:
            listeners = self.listeners[message['command']]
        except KeyError:
            listeners = []
            self.listeners[message['command']] = listeners
        listeners.append((future, str(message['channel'])))
        return asyncio.wait_for(future, timeout)

    def get_channel(self, data, type='status_channel'):
        if (int(data['channel']) == -1):
            return self.bot.get_channel(int(self.bot.DCSServers[data['server_name']][type]))
        else:
            return self.bot.get_channel(int(data['channel']))

    def find_discord_user(self, data):
        # check if we have the user already
        discord_id = -1
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT discord_id FROM players WHERE ucid = %s AND discord_id <> -1', (data['ucid'], ))
                result = cursor.fetchone()
                if (result is not None):
                    discord_id = result[0]
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)
        dcs_name = data['name']
        for member in self.bot.get_all_members():
            if ((discord_id != -1) and (member.id == discord_id)):
                return member
            if (member.nick):
                if (dcs_name.lower() in member.nick.lower()) or (member.nick.lower() in dcs_name.lower()):
                    return member
            if (dcs_name.lower() in member.name.lower()) or (member.name.lower() in dcs_name.lower()):
                return member
        return None

    # TODO: cache that
    def getCurrentMissionID(self, server_name):
        conn = self.bot.pool.getconn()
        id = -1
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT id FROM missions WHERE server_name = %s AND mission_end IS NULL', (server_name, ))
                if (cursor.rowcount > 0):
                    id = cursor.fetchone()[0]
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)
        return id

    def updatePlayerList(self, data):
        server = self.bot.DCSServers[data['server_name']]
        self.sendtoDCS(server, {"command": "getCurrentPlayers", "channel": data['channel']})

    def updateMission(self, data):
        server = self.bot.DCSServers[data['server_name']]
        self.sendtoDCS(server, {"command": "getRunningMission", "channel": data['channel']})

    def updateBans(self, data=None):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT ucid, discord_id FROM players WHERE ban = true')
                self.banList = [dict(row) for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)
        if (data is not None):
            servers = [self.bot.DCSServers[data['server_name']]]
        else:
            servers = self.bot.DCSServers.values()
        for server in servers:
            for ban in self.banList:
                self.sendtoDCS(server, {"command": "ban", "ucid": ban['ucid'], "channel": server['status_channel']})

    async def setPlayersEmbed(self, data, embed):
        message = self.players_embeds[data['server_name']] if (
            data['server_name'] in self.players_embeds) else None
        if (message is not None):
            try:
                await message.edit(embed=embed)
            except discord.errors.NotFound:
                message = None
        if (message is None):
            self.players_embeds[data['server_name']] = await self.get_channel(data).send(embed=embed)
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('UPDATE servers SET players_embed=%s WHERE server_name=%s',
                                   (self.players_embeds[data['server_name']].id, data['server_name']))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.bot.log.exception(error)
                conn.rollback()
            finally:
                self.bot.pool.putconn(conn)

    async def setMissionEmbed(self, data, embed):
        message = self.mission_embeds[data['server_name']] if (
            data['server_name'] in self.mission_embeds) else None
        if (message is not None):
            try:
                await message.edit(embed=embed)
            except discord.errors.NotFound:
                message = None
        if (message is None):
            self.mission_embeds[data['server_name']] = await self.get_channel(data).send(embed=embed)
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('UPDATE servers SET mission_embed=%s WHERE server_name=%s',
                                   (self.mission_embeds[data['server_name']].id, data['server_name']))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.bot.log.exception(error)
                conn.rollback()
            finally:
                self.bot.pool.putconn(conn)

    async def get_external_ip(self):
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.ipify.org') as resp:
                return await resp.text()

    def format_mission_embed(self, mission):
        server = self.bot.DCSServers[mission['server_name']]
        plugins = []
        embed = discord.Embed(title='{} [{}/{}]\n{}'.format(mission['server_name'],
                                                            mission['num_players'], server['serverSettings']['maxPlayers'],
                                                            ('"' + mission['current_mission'] + '"') if server['status'] in ['Running', 'Paused'] else ('_' + server['status'] + '_')),
                              color=discord.Color.blue())

        embed.set_thumbnail(url=self.STATUS_IMG[server['status']])
        embed.add_field(name='Map', value=mission['current_map'])
        embed.add_field(name='Server-IP / Port', value=server['serverSettings']
                        ['external_ip'] + ':' + str(server['serverSettings']['port']))
        if (len(server['serverSettings']['password']) > 0):
            embed.add_field(name='Password', value=server['serverSettings']['password'])
        else:
            embed.add_field(name='Password', value='_ _')
        uptime = int(mission['mission_time'])
        embed.add_field(name='Runtime', value=str(timedelta(seconds=uptime)))
        if ('start_time' in mission):
            if (mission['date']['Year'] >= 1970):
                date = datetime.datetime(mission['date']['Year'], mission['date']['Month'],
                                         mission['date']['Day'], 0, 0).timestamp()
                real_time = date + mission['start_time'] + uptime
                value = str(datetime.datetime.fromtimestamp(real_time))
            else:
                value = '{}-{:02d}-{:02d} {}'.format(mission['date']['Year'], mission['date']['Month'],
                                                     mission['date']['Day'], timedelta(seconds=mission['start_time'] + uptime))
        else:
            value = '-'
        embed.add_field(name='Date/Time in Mission', value=value)
        embed.add_field(name='Avail. Slots',
                        value='🔹 {}  |  {} 🔸'.format(mission['num_slots_blue'] if 'num_slots_blue' in mission else '-', mission['num_slots_red'] if 'num_slots_red' in mission else '-'))
        embed.add_field(name='▬' * 25, value='_ _', inline=False)
        if ('SRSSettings' in server):
            plugins.append('SRS')
            if ('EXTERNAL_AWACS_MODE' in server['SRSSettings'] and server['SRSSettings']['EXTERNAL_AWACS_MODE'] is True):
                value = '🔹 Pass: {}\n🔸 Pass: {}'.format(
                    server['SRSSettings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD'], server['SRSSettings']['EXTERNAL_AWACS_MODE_RED_PASSWORD'])
            else:
                value = '_ _'
            embed.add_field(name='SRS [{}]'.format(
                server['SRSSettings']['SERVER_SRS_PORT']), value=value)
        if ('lotAtcSettings' in server):
            plugins.append('LotAtc')
            embed.add_field(name='LotAtc [{}]'.format(server['lotAtcSettings']['port']), value='🔹 Pass: {}\n🔸 Pass: {}'.format(
                server['lotAtcSettings']['blue_password'], server['lotAtcSettings']['red_password']))
        if ('Tacview' in server['options']['plugins']):
            name = 'Tacview'
            if (('tacviewModuleEnabled' in server['options']['plugins']['Tacview'] and server['options']['plugins']['Tacview']['tacviewModuleEnabled'] is False) or ('tacviewFlightDataRecordingEnabled' in server['options']['plugins']['Tacview'] and server['options']['plugins']['Tacview']['tacviewFlightDataRecordingEnabled'] is False)):
                value = 'disabled'
            else:
                plugins.append('Tacview')
                value = ''
                tacview = server['options']['plugins']['Tacview']
                if ('tacviewRealTimeTelemetryEnabled' in tacview and tacview['tacviewRealTimeTelemetryEnabled'] is True):
                    name += ' RT'
                    if ('tacviewRealTimeTelemetryPassword' in tacview and len(tacview['tacviewRealTimeTelemetryPassword']) > 0):
                        value += 'Password: {}\n'.format(tacview['tacviewRealTimeTelemetryPassword'])
                elif ('tacviewHostTelemetryPassword' in tacview and len(tacview['tacviewHostTelemetryPassword']) > 0):
                    value += 'Password: "{}"\n'.format(tacview['tacviewHostTelemetryPassword'])
                if ('tacviewRealTimeTelemetryPort' in tacview and len(tacview['tacviewRealTimeTelemetryPort']) > 0):
                    name += ' [{}]'.format(tacview['tacviewRealTimeTelemetryPort'])
                if ('tacviewRemoteControlEnabled' in tacview and tacview['tacviewRemoteControlEnabled'] is True):
                    value += '**Remote Ctrl [{}]**\n'.format(tacview['tacviewRemoteControlPort'])
                    if ('tacviewRemoteControlPassword' in tacview and len(tacview['tacviewRemoteControlPassword']) > 0):
                        value += 'Password: {}'.format(tacview['tacviewRemoteControlPassword'])
                if (len(value) == 0):
                    value = 'enabled'
            embed.add_field(name=name, value=value)
        footer = ''
        if (len(plugins) > 0):
            footer += 'The IP address of '
            if (len(plugins) == 1):
                footer += plugins[0]
            else:
                footer += ', '.join(plugins[0:len(plugins) - 1]) + ' and ' + plugins[len(plugins) - 1]
            footer += ' is the same as the server.'
        embed.set_footer(text=footer)
        return embed

    @commands.command(description='Lists the registered DCS servers')
    @commands.has_role('DCS')
    @commands.guild_only()
    async def servers(self, ctx):
        if (len(self.bot.DCSServers) > 0):
            await ctx.send('Servers on {}-Node {}:'.format('Master' if self.bot.config.getboolean('BOT',
                                                                                                  'MASTER') is True else 'Slave', platform.node()))
            for server_name, server in self.bot.DCSServers.items():
                if (server['status'] == 'Running'):
                    mission = await self.sendtoDCSSync(server, {"command": "getRunningMission", "channel": 0})
                    await ctx.send(embed=self.format_mission_embed(mission))
        else:
            await ctx.send('No server running on host {}'.format(platform.node()))

    @commands.command(description='Send a chat message to a running DCS instance', usage='<message>', hidden=True)
    @commands.has_role('DCS')
    @commands.guild_only()
    async def chat(self, ctx, *args):
        server = await self.get_server(ctx)
        if (server is not None):
            self.sendtoDCS(server, {"command": "sendChatMessage", "channel": ctx.channel.id, "message": ' '.join(args),
                                    "from": ctx.message.author.display_name})

    @commands.command(description='Shows the active DCS mission', hidden=True)
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def mission(self, ctx):
        server = await self.get_server(ctx)
        if (server is not None):
            if (int(server['status_channel']) != ctx.channel.id):
                mission = await self.sendtoDCSSync(server, {"command": "getRunningMission", "channel": 0})
                await ctx.send(embed=self.format_mission_embed(mission))
            else:
                await ctx.message.delete()
                if (self.getCurrentMissionID(server['server_name']) != -1):
                    self.sendtoDCS(server, {"command": "getRunningMission", "channel": ctx.channel.id})
                else:
                    msg = await ctx.send('There is currently no mission running on server "' + server['server_name'] + '"')
                    await asyncio.sleep(5)
                    await msg.delete()

    @commands.command(description='Shows briefing of the active DCS mission', aliases=['brief'])
    @commands.has_role('DCS')
    @commands.guild_only()
    async def briefing(self, ctx):
        server = await self.get_server(ctx)
        if (server is not None):
            if (self.getCurrentMissionID(server['server_name']) != -1):
                embed = await self.sendtoDCSSync(server, {"command": "getMissionDetails", "channel": ctx.message.id})
                await ctx.send(embed=embed)
            else:
                await ctx.send('There is currently no mission running on server "' + server['server_name'] + '"')

    @commands.command(description='List the current players on this server', hidden=True)
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def players(self, ctx):
        server = await self.get_server(ctx)
        if (server is not None):
            if (int(server['status_channel']) != ctx.channel.id):
                await ctx.send('This command can only be used in the status channel.')
            else:
                await ctx.message.delete()
                if (self.getCurrentMissionID(server['server_name']) != -1):
                    self.sendtoDCS(server, {"command": "getCurrentPlayers", "channel": ctx.channel.id})
                else:
                    msg = await ctx.send('There is currently no mission running on server "' + server['server_name'] + '"')
                    await asyncio.sleep(5)
                    await msg.delete()

    @commands.command(description='Restarts the current active mission', usage='[delay] [message]')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def restart(self, ctx, *args):
        server = await self.get_server(ctx)
        if (server is not None):
            delay = 120
            msg = None
            if (self.getCurrentMissionID(server['server_name']) != -1):
                i = 0
                if (len(args)):
                    # check for delay parameter
                    if (args[0].isnumeric()):
                        delay = int(args[0])
                        i += 1
                message = '!!! Server is RESTARTING in {} seconds.'.format(delay)
                # have we got a message to present to the users?
                if (len(args) > i):
                    message += ' Reason: {}'.format(' '.join(args[i:]))

                if ((int(server['status_channel']) == ctx.channel.id)):
                    await ctx.message.delete()
                msg = await ctx.send('Restarting mission in {} seconds (warning users before)...'.format(delay))
                self.sendtoDCS(server, {"command": "sendChatMessage", "channel": ctx.channel.id,
                                        "message": message, "from": ctx.message.author.display_name})
                await asyncio.sleep(delay)
                await msg.delete()
                self.sendtoDCS(server, {"command": "restartMission", "channel": ctx.channel.id})
                msg = await ctx.send('Restart command sent. Server will restart now.')
            else:
                msg = await ctx.send('There is currently no mission running on server "' + server['server_name'] + '"')
            if ((msg is not None) and (int(server['status_channel']) == ctx.channel.id)):
                await asyncio.sleep(5)
                await msg.delete()

    @commands.command(description='Lists the current configured missions')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def list(self, ctx):
        server = await self.get_server(ctx)
        if (server is not None):
            with suppress(asyncio.TimeoutError):
                embed = await self.sendtoDCSSync(server, {"command": "listMissions", "channel": ctx.message.id})
                return await ctx.send(embed=embed)
            return await ctx.send('Server ' + server['server_name'] + ' is not running.')

    @commands.command(description='Loads a mission by ID', usage='<ID>')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def load(self, ctx, id):
        server = await self.get_server(ctx)
        if (server is not None):
            self.sendtoDCS(server, {"command": "loadMission", "id": id, "channel": ctx.channel.id})
            await ctx.send('Loading mission ' + id + ' ...')

    @commands.command(description='Deletes a mission from the list', usage='<ID>')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def delete(self, ctx, id):
        server = await self.get_server(ctx)
        if (server is not None):
            self.sendtoDCS(server, {"command": "deleteMission", "id": id, "channel": ctx.channel.id})

    @commands.command(description='Adds a mission to the list', usage='<path>')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def add(self, ctx, *path):
        server = await self.get_server(ctx)
        if (server is not None):
            if (len(path) == 0):
                j = 0
                message = None
                dcs_path = expandvars(self.bot.config['DCS']['DCS_HOME'] + '\\Missions')
                files = fnmatch.filter(listdir(dcs_path), '*.miz')
                try:
                    while (True):
                        embed = discord.Embed(title='Available Missions', color=discord.Color.blue())
                        ids = missions = ''
                        max_i = (len(files) % 5) if (len(files) - j * 5) < 5 else 5
                        for i in range(0, max_i):
                            ids += (chr(0x31 + i) + '\u20E3' + '\n')
                            missions += files[i+j*5] + '\n'
                        embed.add_field(name='ID', value=ids)
                        embed.add_field(name='Mission', value=missions)
                        embed.add_field(name='_ _', value='_ _')
                        embed.set_footer(text='Press a number to add the selected mission to the list.')
                        message = await ctx.send(embed=embed)
                        if (j > 0):
                            await message.add_reaction('◀️')
                        for i in range(1, max_i + 1):
                            await message.add_reaction(chr(0x30 + i) + '\u20E3')
                        await message.add_reaction('⏹️')
                        if (((j + 1) * 5) < len(files)):
                            await message.add_reaction('▶️')
                        react = await self.wait_for_single_reaction(ctx, message)
                        await message.delete()
                        if (react.emoji == '◀️'):
                            j -= 1
                            message = None
                        elif (react.emoji == '▶️'):
                            j += 1
                            message = None
                        if (react.emoji == '⏹️'):
                            return
                        elif ((len(react.emoji) > 1) and ord(react.emoji[0]) in range(0x31, 0x36)):
                            file = files[(ord(react.emoji[0]) - 0x31) + j * 5]
                            break
                except asyncio.TimeoutError:
                    await message.delete()
            else:
                file = ' '.join(path)
            self.sendtoDCS(server, {"command": "addMission", "path": file, "channel": ctx.channel.id})

    @commands.command(description='Bans a user by ucid or discord id', usage='<member / ucid>')
    @commands.has_any_role('Admin', 'Moderator')
    @commands.guild_only()
    async def ban(self, ctx, user):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if (user.startswith('<')):
                    discord_id = user.replace('<@!', '').replace('>', '')
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (discord_id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # ban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    for server in self.bot.DCSServers.values():
                        self.sendtoDCS(server, {"command": "ban", "ucid": ucid, "channel": ctx.channel.id})
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Unbans a user by ucid or discord id', usage='<member / ucid>')
    @commands.has_any_role('Admin', 'Moderator')
    @commands.guild_only()
    async def unban(self, ctx, user):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if (user.startswith('<')):
                    discord_id = user.replace('<@!', '').replace('>', '')
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (discord_id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # unban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    for server in self.bot.DCSServers.values():
                        self.sendtoDCS(server, {"command": "unban", "ucid": ucid, "channel": ctx.channel.id})
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Unregisters the server from this instance')
    @commands.has_role('Admin')
    @commands.guild_only()
    async def unregister(self, ctx, node=platform.node()):
        server = await self.get_server(ctx)
        if (server is not None):
            server_name = server['server_name']
            await self.mission_embeds[server_name].delete()
            self.mission_embeds.pop(server_name)
            await self.players_embeds[server_name].delete()
            self.players_embeds.pop(server_name)
            self.bot.DCSServers.pop(server_name)
            await ctx.send('Server {} unregistered.'.format(server_name))

    @commands.command(description='Pauses the current running mission')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def pause(self, ctx):
        server = await self.get_server(ctx)
        if (server is not None):
            if (server['status'] == 'Running'):
                self.sendtoDCS(server, {"command": "pause", "channel": ctx.channel.id})
                await ctx.send('Server "{}" paused.'.format(server['server_name']))
            else:
                await ctx.send('Server "{}" is not running.'.format(server['server_name']))

    @commands.command(description='Unpauses the current running mission')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def unpause(self, ctx):
        server = await self.get_server(ctx)
        if (server is not None):
            if (server['status'] == 'Paused'):
                self.sendtoDCS(server, {"command": "unpause", "channel": ctx.channel.id})
                await ctx.send('Server "{}" unpaused.'.format(server['server_name']))
            else:
                await ctx.send('Server "{}" is already running.'.format(server['server_name']))

    @tasks.loop(minutes=10.0)
    async def update_mission_status(self):
        for server_name, server in self.bot.DCSServers.items():
            if (self.getCurrentMissionID(server_name) != -1):
                self.sendtoDCS(server, {"command": "getRunningMission",
                                        "channel": server['status_channel']})

    async def handleUDPRequests(self):

        class UDPListener(socketserver.BaseRequestHandler):

            def get_player(self, server_name, id):
                df = self.player_data[server_name]
                return df[df['id'] == id].to_dict('records')[0]

            async def sendMessage(data):
                return await self.get_channel(data).send(data['message'])

            async def registerDCSServer(data):
                self.bot.log.info('Registering DCS-Server ' + data['server_name'])
                # check for protocol incompatibilities
                if (data['hook_version'] != self.bot.version):
                    self.bot.log.error(
                        'Server {} has wrong Hook version installed. Please update lua files and restart server. Registration ignored.'.format(data['server_name']))
                    return
                if (data['status_channel'].isnumeric() is True):
                    SQL_INSERT = 'INSERT INTO servers (server_name, agent_host, host, port, chat_channel, status_channel, admin_channel) VALUES(%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (server_name) DO UPDATE SET agent_host=%s, host=%s, port=%s, chat_channel=%s, status_channel=%s, admin_channel=%s'
                    SQL_SELECT = 'SELECT server_name, host, port, chat_channel, status_channel, admin_channel, mission_embed, players_embed, \'Running\' AS status FROM servers WHERE server_name = %s'
                    conn = self.bot.pool.getconn()
                    try:
                        with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                            cursor.execute(SQL_INSERT, (data['server_name'], platform.node(), data['host'], data['port'],
                                                        data['chat_channel'], data['status_channel'], data['admin_channel'],
                                                        platform.node(), data['host'], data['port'],
                                                        data['chat_channel'], data['status_channel'], data['admin_channel']))
                            cursor.execute(SQL_SELECT, (data['server_name'], ))
                            self.bot.DCSServers[data['server_name']] = dict(cursor.fetchone())
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        self.bot.log.exception(error)
                        conn.rollback()
                    finally:
                        self.bot.pool.putconn(conn)
                    # Store server configuration
                    # Backwards compatibility check
                    if ('statistics' in data):
                        self.bot.DCSServers[data['server_name']]['statistics'] = data['statistics']
                    else:
                        self.bot.DCSServers[data['server_name']]['statistics'] = True
                    self.bot.DCSServers[data['server_name']]['serverSettings'] = data['serverSettings']
                    self.bot.DCSServers[data['server_name']]['serverSettings']['external_ip'] = self.external_ip
                    self.bot.DCSServers[data['server_name']]['options'] = data['options']
                    if ('SRSSettings' in data):
                        self.bot.DCSServers[data['server_name']]['SRSSettings'] = data['SRSSettings']
                    if ('lotAtcSettings' in data):
                        self.bot.DCSServers[data['server_name']]['lotAtcSettings'] = data['lotAtcSettings']
                    self.updateBans(data)
                else:
                    self.bot.log.error(
                        'Configuration mismatch. Please check settings in DCSServerBotConfig.lua on server {}!'.format(data['server_name']))

            async def getServerSettings(data):
                return data

            async def getRunningMission(data):
                if (int(data['channel']) != 0):
                    if ('pause' in data):
                        self.bot.DCSServers[data['server_name']
                                            ]['status'] = 'Paused' if data['pause'] is True else 'Running'
                    embed = self.format_mission_embed(data)
                    return await self.setMissionEmbed(data, embed)
                else:
                    return data

            async def getCurrentPlayers(data):
                if (int(data['channel']) != 0):
                    self.player_data[data['server_name']] = pd.DataFrame.from_dict(data['players'])
                    embed = discord.Embed(title='Active Players', color=discord.Color.blue())
                    names = units = sides = '' if (len(data['players']) > 1) else '_ _'
                    for player in data['players']:
                        side = player['side']
                        if(player['id'] == 1):
                            continue
                        names += player['name'] + '\n'
                        units += (player['unit_type'] if (side != 0) else '_ _') + '\n'
                        sides += self.PLAYER_SIDES[side] + '\n'
                    embed.add_field(name='Name', value=names)
                    embed.add_field(name='Unit', value=units)
                    embed.add_field(name='Side', value=sides)
                    return await self.setPlayersEmbed(data, embed)
                else:
                    return data

            async def listMissions(data):
                embed = discord.Embed(title='Mission List', color=discord.Color.blue())
                ids = active = missions = ''
                for i in range(0, len(data['missionList'])):
                    ids += (chr(0x31 + i) + '\u20E3' + '\n')
                    active += ('Yes\n' if data['listStartIndex'] == (i + 1) else '_ _\n')
                    mission = data['missionList'][i]
                    missions += mission[(mission.rfind('\\') + 1):] + '\n'
                embed.add_field(name='ID', value=ids)
                embed.add_field(name='Active', value=active)
                embed.add_field(name='Mission', value=missions)
                return embed

            async def getMissionDetails(data):
                if (int(data['channel']) != 0):
                    embed = discord.Embed(title=data['current_mission'], color=discord.Color.blue())
                    embed.description = data['mission_description'][:2048]
                    return embed
                else:
                    return data

            async def onMissionLoadBegin(data):
                self.bot.DCSServers[data['server_name']]['status'] = 'Loading'
                await UDPListener.getRunningMission(data)

            async def onMissionLoadEnd(data):
                self.bot.DCSServers[data['server_name']]['status'] = 'Paused'
                if (self.bot.DCSServers[data['server_name']]['statistics'] is True):
                    SQL_CLOSE_STATISTICS = 'UPDATE statistics SET hop_off = NOW() WHERE mission_id IN (SELECT id FROM missions WHERE server_name = %s AND mission_end IS NULL) AND hop_off IS NULL'
                    SQL_CLOSE_MISSIONS = 'UPDATE missions SET mission_end = NOW() WHERE server_name = %s AND mission_end IS NULL'
                    SQL_START_MISSION = 'INSERT INTO missions (server_name, mission_name, mission_theatre) VALUES(%s, %s, %s)'
                    conn = self.bot.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            cursor.execute(SQL_CLOSE_STATISTICS, (data['server_name'],))
                            cursor.execute(SQL_CLOSE_MISSIONS, (data['server_name'],))
                            cursor.execute(SQL_START_MISSION, (data['server_name'],
                                                               data['current_mission'], data['current_map']))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        self.bot.log.exception(error)
                        conn.rollback()
                    finally:
                        self.bot.pool.putconn(conn)
                await UDPListener.getRunningMission(data)
                self.updatePlayerList(data)
                return None

            async def onSimulationStart(data):
                return None

            async def onSimulationStop(data):
                data['num_players'] = 0
                data['current_map'] = '-'
                data['mission_time'] = 0
                self.bot.DCSServers[data['server_name']]['status'] = 'Shutdown'
                await UDPListener.getRunningMission(data)
                data['players'] = []
                await UDPListener.getCurrentPlayers(data)
                if (self.bot.DCSServers[data['server_name']]['statistics'] is True):
                    conn = self.bot.pool.getconn()
                    try:
                        mission_id = self.getCurrentMissionID(data['server_name'])
                        with closing(conn.cursor()) as cursor:
                            cursor.execute('UPDATE statistics SET hop_off = NOW() WHERE mission_id = %s AND hop_off IS NULL',
                                           (mission_id, ))
                            cursor.execute('UPDATE missions SET mission_end = NOW() WHERE id = %s',
                                           (mission_id, ))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        self.bot.log.exception(error)
                        conn.rollback()
                    finally:
                        self.bot.pool.putconn(conn)

            async def onSimulationPause(data):
                self.bot.DCSServers[data['server_name']]['status'] = 'Paused'
                self.updateMission(data)

            async def onSimulationResume(data):
                self.bot.DCSServers[data['server_name']]['status'] = 'Running'
                self.updateMission(data)

            async def onPlayerConnect(data):
                if (data['id'] != 1):
                    chat_channel = self.get_channel(data, 'chat_channel')
                    if (chat_channel is not None):
                        await chat_channel.send('{} connected to server'.format(data['name']))

            async def onPlayerStart(data):
                if (data['id'] != 1):
                    SQL_PLAYERS = 'INSERT INTO players (ucid, discord_id) VALUES(%s, %s) ON CONFLICT (ucid) DO UPDATE SET discord_id = %s WHERE players.discord_id = -1'
                    discord_user = self.find_discord_user(data)
                    discord_id = discord_user.id if (discord_user) else -1
                    if (self.bot.DCSServers[data['server_name']]['statistics'] is True):
                        conn = self.bot.pool.getconn()
                        try:
                            with closing(conn.cursor()) as cursor:
                                cursor.execute(SQL_PLAYERS, (data['ucid'], discord_id, discord_id))
                                conn.commit()
                        except (Exception, psycopg2.DatabaseError) as error:
                            self.bot.log.exception(error)
                            conn.rollback()
                        finally:
                            self.bot.pool.putconn(conn)
                    server = self.bot.DCSServers[data['server_name']]
                    if (discord_user is None):
                        self.sendtoDCS(server, {"command": "sendChatMessage", "message": self.bot.config['DCS']['GREETING_MESSAGE_UNKNOWN'].format(
                            data['name']), "to": data['id']})
                        # only warn for unknown users if it is a non-public server
                        if (len(self.bot.DCSServers[data['server_name']]['password']) > 0):
                            await self.get_channel(data, 'admin_channel').send('Player {} (ucid={}) can\'t be matched to a discord user.'.format(data['name'], data['ucid']))
                    else:
                        name = discord_user.nick if (discord_user.nick) else discord_user.name
                        self.sendtoDCS(server, {"command": "sendChatMessage", "message": self.bot.config['DCS']['GREETING_MESSAGE_MEMBERS'].format(
                            name, data['server_name']), "to": data['id']})
                    self.updateMission(data)
                    self.updatePlayerList(data)
                    return None

            async def onPlayerStop(data):
                return None

            async def onPlayerChangeSlot(data):
                if ('side' in data):
                    SQL_CLOSE_STATISTICS = 'UPDATE statistics SET hop_off = NOW() WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL'
                    SQL_INSERT_STATISTICS = 'INSERT INTO statistics (mission_id, player_ucid, slot) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING'
                    player = None
                    with suppress(Exception):
                        player = UDPListener.get_player(self, data['server_name'], data['id'])
                    if (data['side'] != self.SIDE_SPECTATOR):
                        if (self.bot.DCSServers[data['server_name']]['statistics'] is True):
                            conn = self.bot.pool.getconn()
                            try:
                                mission_id = self.getCurrentMissionID(data['server_name'])
                                with closing(conn.cursor()) as cursor:
                                    cursor.execute(SQL_CLOSE_STATISTICS, (mission_id, data['ucid']))
                                    cursor.execute(SQL_INSERT_STATISTICS, (mission_id, data['ucid'], data['unit_type']))
                                    conn.commit()
                            except (Exception, psycopg2.DatabaseError) as error:
                                self.bot.log.exception(error)
                                conn.rollback()
                            finally:
                                self.bot.pool.putconn(conn)
                        if (player is not None):
                            chat_channel = self.get_channel(data, 'chat_channel')
                            if (chat_channel is not None):
                                await chat_channel.send('{} player {} occupied {} {}'.format(
                                    self.PLAYER_SIDES[player['side'] if player['side'] != 0 else 3], data['name'],
                                    self.PLAYER_SIDES[data['side']], data['unit_type']))
                    elif (player is not None):
                        chat_channel = self.get_channel(data, 'chat_channel')
                        if (chat_channel is not None):
                            await chat_channel.send('{} player {} returned to Spectators'.format(
                                self.PLAYER_SIDES[player['side']], data['name']))
                    self.updateMission(data)
                    self.updatePlayerList(data)
                return None

            async def onGameEvent(data):
                # make sure we don't accept game events before the bot is not set up correctly
                await self.lock.acquire()
                try:
                    if (data['eventName'] == 'mission_end'):
                        None
                    elif (data['eventName'] in ['connect', 'change_slot']):  # these events are handled differently
                        return None
                    elif (data['eventName'] == 'disconnect'):
                        if (data['arg1'] != 1):
                            player = UDPListener.get_player(self, data['server_name'], data['arg1'])
                            if (self.bot.DCSServers[data['server_name']]['statistics'] is True):
                                conn = self.bot.pool.getconn()
                                try:
                                    with closing(conn.cursor()) as cursor:
                                        cursor.execute('UPDATE statistics SET hop_off = NOW() WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
                                                       (self.getCurrentMissionID(data['server_name']), player['ucid']))
                                        conn.commit()
                                except (Exception, psycopg2.DatabaseError) as error:
                                    self.bot.log.exception(error)
                                    conn.rollback()
                                finally:
                                    self.bot.pool.putconn(conn)
                            chat_channel = self.get_channel(data, 'chat_channel')
                            if (chat_channel is not None):
                                if (player['side'] == self.SIDE_SPECTATOR):
                                    await chat_channel.send('Player {} disconnected'.format(player['name']))
                                else:
                                    await chat_channel.send('{} player {} disconnected'.format(
                                        self.PLAYER_SIDES[player['side']], player['name']))
                            self.updateMission(data)
                            self.updatePlayerList(data)
                    elif (data['eventName'] == 'friendly_fire'):
                        player1 = UDPListener.get_player(self, data['server_name'], data['arg1'])
                        if (data['arg3'] != -1):
                            player2 = UDPListener.get_player(self, data['server_name'], data['arg3'])
                        else:
                            player2 = None
                        chat_channel = self.get_channel(data, 'chat_channel')
                        if (chat_channel is not None):
                            await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                                self.PLAYER_SIDES[player1['side']], 'player ' + player1['name'],
                                ('player ' + player2['name']) if player2 is not None else 'AI',
                                data['arg2'] if (len(data['arg2']) > 0) else 'Cannon'))
                    elif (data['eventName'] == 'kill'):
                        if (self.bot.DCSServers[data['server_name']]['statistics'] is True):
                            conn = self.bot.pool.getconn()
                            try:
                                with closing(conn.cursor()) as cursor:
                                    # Player is not an AI
                                    if (data['arg1'] != -1):
                                        if (data['arg4'] != -1):
                                            if (data['arg1'] == data['arg4']):  # self kill
                                                kill_type = 'self_kill'
                                            elif (data['arg3'] == data['arg6']):  # teamkills
                                                kill_type = 'teamkill'
                                            elif (data['victimCategory'] in ['Planes', 'Helicopters']):  # pvp
                                                kill_type = 'pvp'
                                        elif (data['victimCategory'] == 'Planes'):
                                            kill_type = 'kill_planes'
                                        elif (data['victimCategory'] == 'Helicopters'):
                                            kill_type = 'kill_helicopters'
                                        elif (data['victimCategory'] == 'Ships'):
                                            kill_type = 'kill_ships'
                                        elif (data['victimCategory'] == 'Air Defence'):
                                            kill_type = 'kill_sams'
                                        elif (data['victimCategory'] in ['Unarmed', 'Armor', 'Infantry' 'Fortification', 'Artillery', 'MissilesSS']):
                                            kill_type = 'kill_ground'
                                        else:
                                            kill_type = 'kill_other'  # Static objects
                                        # Update database
                                        player1 = UDPListener.get_player(self, data['server_name'], data['arg1'])
                                        if (kill_type in self.SQL_EVENT_UPDATES.keys()):
                                            cursor.execute(self.SQL_EVENT_UPDATES[kill_type],
                                                           (self.getCurrentMissionID(data['server_name']), player1['ucid']))
                                    else:
                                        player1 = None

                                    # Victim is not an AI
                                    if (data['arg4'] != -1):
                                        if (data['arg1'] != -1):
                                            if (data['arg1'] == data['arg4']):  # self kill
                                                death_type = 'self_kill'
                                            elif (data['arg3'] == data['arg6']):  # killed by team member - no death counted
                                                death_type = 'teamdeath'
                                            elif (data['killerCategory'] in ['Planes', 'Helicopters']):  # pvp
                                                death_type = 'deaths_pvp'
                                        elif (data['killerCategory'] == 'Planes'):
                                            death_type = 'deaths_planes'
                                        elif (data['killerCategory'] == 'Helicopters'):
                                            death_type = 'deaths_helicopters'
                                        elif (data['killerCategory'] == 'Ships'):
                                            death_type = 'deaths_ships'
                                        elif (data['killerCategory'] == 'Air Defence'):
                                            death_type = 'deaths_sams'
                                        elif (data['killerCategory'] in ['Armor', 'Infantry' 'Fortification', 'Artillery', 'MissilesSS']):
                                            death_type = 'deaths_ground'
                                        player2 = UDPListener.get_player(self, data['server_name'], data['arg4'])
                                        if (death_type in self.SQL_EVENT_UPDATES.keys()):
                                            cursor.execute(self.SQL_EVENT_UPDATES[death_type],
                                                           (self.getCurrentMissionID(data['server_name']), player2['ucid']))
                                    else:
                                        player2 = None
                                    conn.commit()
                                    chat_channel = self.get_channel(data, 'chat_channel')
                                    if (chat_channel is not None):
                                        await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                                            self.PLAYER_SIDES[data['arg3']],
                                            ('player ' + player1['name']) if player1 is not None else 'AI',
                                            data['arg2'], self.PLAYER_SIDES[data['arg6']],
                                            ('player ' + player2['name']) if player2 is not None else 'AI',
                                            data['arg5'], data['arg7']))
                                    # report teamkills from unknown players to admins
                                    if ((player1 is not None) and (kill_type == 'teamkill')):
                                        discord_user = self.find_discord_user(player1)
                                        if (discord_user is None):
                                            await self.get_channel(data, 'admin_channel').send('Unknown player {} (ucid={}) is killing team members. Please investigate.'.format(player1['name'], player1['ucid']))
                            except (Exception, psycopg2.DatabaseError) as error:
                                self.bot.log.exception(error)
                                conn.rollback()
                            finally:
                                self.bot.pool.putconn(conn)
                    elif (data['eventName'] in ['takeoff', 'landing', 'crash', 'eject', 'pilot_death']):
                        if (data['arg1'] != -1):
                            player = UDPListener.get_player(self, data['server_name'], data['arg1'])
                            chat_channel = self.get_channel(data, 'chat_channel')
                            if (chat_channel is not None):
                                if (data['eventName'] in ['takeoff', 'landing']):
                                    await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                                        self.PLAYER_SIDES[player['side']], player['name'], data['arg3']))
                                else:
                                    await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                                        self.PLAYER_SIDES[player['side']], player['name']))
                            if (data['eventName'] in self.SQL_EVENT_UPDATES.keys()):
                                if (self.bot.DCSServers[data['server_name']]['statistics'] is True):
                                    conn = self.bot.pool.getconn()
                                    try:
                                        with closing(conn.cursor()) as cursor:
                                            cursor.execute(self.SQL_EVENT_UPDATES[data['eventName']],
                                                           (self.getCurrentMissionID(data['server_name']), player['ucid']))
                                            conn.commit()
                                    except (Exception, psycopg2.DatabaseError) as error:
                                        self.bot.log.exception(error)
                                        conn.rollback()
                                    finally:
                                        self.bot.pool.putconn(conn)
                    else:
                        self.bot.log.info('Unhandled event: ' + data['eventName'])
                finally:
                    self.lock.release()
                return None

            async def onChatMessage(data):
                chat_channel = self.get_channel(data, 'chat_channel')
                if (chat_channel is not None):
                    if ('from_id' in data and data['from_id'] != 1 and len(data['message']) > 0):
                        return await chat_channel.send(data['from_name'] + ': ' + data['message'])
                return None

            def handle(s):
                dt = json.loads(s.request[0].strip())
                # ignore messages not containing server names
                if ('server_name' not in dt):
                    self.bot.log.warn('Message without server_name retrieved: {}'.format(dt))
                    return
                self.bot.log.info('{}->HOST: {}'.format(dt['server_name'], json.dumps(dt)))
                try:
                    command = dt['command']
                    future = asyncio.run_coroutine_threadsafe(getattr(UDPListener, command)(dt), self.loop)
                    result = future.result()
                    if (command.startswith('on') is False):
                        listeners = self.listeners.get(command)
                        if (listeners):
                            removed = []
                            for i, (future, token) in enumerate(listeners):
                                if future.cancelled():
                                    removed.append(i)
                                    continue
                                if (token == dt['channel']):
                                    # set result with call_soon_threadsafe as we are in a different thread
                                    self.loop.call_soon_threadsafe(future.set_result, result)
                                    removed.append(i)
                            if len(removed) == len(listeners):
                                self.listeners.pop(command)
                            else:
                                for idx in reversed(removed):
                                    del listeners[idx]
                except (AttributeError) as error:
                    self.bot.log.exception(error)
                    self.bot.log.info('Method ' + dt['command'] + '() not implemented.')

        class MyThreadingUDPServer(socketserver.ThreadingUDPServer):
            def __init__(self, server_address, RequestHandlerClass):
                # enable reuse, in case the restart was too fast and the port was still in TIME_WAIT
                self.allow_reuse_address = True
                self.max_packet_size = 65504
                super().__init__(server_address, RequestHandlerClass)

        await self.lock.acquire()
        try:
            host = self.bot.config['BOT']['HOST']
            port = int(self.bot.config['BOT']['PORT'])
            self.server = MyThreadingUDPServer((host, port), UDPListener)
            self.loop.run_in_executor(self.executor, self.server.serve_forever)
            self.bot.log.info('UDP listener started on interface {} port {} accepting commands.'.format(host, port))
        finally:
            self.lock.release()


def setup(bot):
    bot.add_cog(Agent(bot))
