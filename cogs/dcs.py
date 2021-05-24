# dcs.py
import asyncio
import discord
import fnmatch
import json
import pandas as pd
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


class DCS(commands.Cog):

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
        self.host = bot.config['BOT']['HOST']
        self.port = int(bot.config['BOT']['PORT'])
        self.mission_embeds = {}
        self.players_embeds = {}
        self.player_data = {}
        self.banList = None
        self.listeners = {}
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(
                    'SELECT server_name, host, port, chat_channel, status_channel, admin_channel, mission_embed, players_embed FROM servers')
                self.bot.DCSServers = [dict(row) for row in cursor.fetchall()]
            self.bot.log.info('{} server(s) read from database.'.format(len(self.bot.DCSServers)))
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        self.bot.pool.putconn(conn)
        self.loop = asyncio.get_event_loop()
        self.start_listener.start()
        self.update_status.start()

    def cog_unload(self):
        self.update_status.cancel()
        self.server.shutdown()
        self.task.cancel()
        self.start_listener.cancel()

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

    def wait_for(self, command, token, timeout=10):
        future = self.loop.create_future()
        try:
            listeners = self.listeners[command]
        except KeyError:
            listeners = []
            self.listeners[command] = listeners
        listeners.append((future, token))
        return asyncio.wait_for(future, timeout)

    async def get_server(self, ctx):
        server = None
        for item in self.bot.DCSServers:
            if ((int(item['status_channel']) == ctx.channel.id) or
                (int(item['chat_channel']) == ctx.channel.id) or
                    (int(item['admin_channel']) == ctx.channel.id)):
                server = item
                break
        if (server is None):
            embed = discord.Embed(color=discord.Color.blue())
            for server in self.bot.DCSServers:
                embed.add_field(name='ID', value=self.bot.DCSServers.index(server) + 1)
                embed.add_field(name='Server', value=server['server_name'])
                embed.add_field(name='Port', value=server['port'])
            embed.set_footer(text='Select the server you\'d like to run that command on.')
            message = await ctx.send(embed=embed)
            for i in range(0, len(self.bot.DCSServers)):
                await message.add_reaction(chr(0x31 + i) + '\u20E3')

            def check_press(react, user):
                return ((react.message == message) & (user != self.bot.user))

            react, user = await self.bot.wait_for('reaction_add', check=check_press, timeout=120.0)
            await message.delete()
            message = None
            server = self.bot.DCSServers[ord(react.emoji[0]) - 0x31]
        return server

    def sendtoDCS(self, message, host, port):
        DCSSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        DCSSocket.sendto(message.encode('utf-8'), (host, port))

    def get_channel(self, data, type='status_channel'):
        if (int(data['channel']) == -1):
            return self.bot.get_channel(int(next(item for item in self.bot.DCSServers if item["server_name"] == data['server_name'])[type]))
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

    def get_player(self, server_name, id):
        df = self.player_data[server_name]
        return df[df['id'] == id].to_dict('records')[0]

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
        self.bot.pool.putconn(conn)
        return id

    def updatePlayerList(self, data):
        server = next(item for item in self.bot.DCSServers if item["server_name"] == data['server_name'])
        self.sendtoDCS('{"command":"getCurrentPlayers", "channel":"' +
                       str(data['channel']) + '"}', server['host'], server['port'])

    def updateMission(self, data):
        server = next(item for item in self.bot.DCSServers if item["server_name"] == data['server_name'])
        self.sendtoDCS('{"command":"getRunningMission", "channel":"' +
                       str(data['channel']) + '"}', server['host'], server['port'])

    def updateBans(self, data=None):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT ucid, discord_id FROM players WHERE ban = true')
                self.banList = [dict(row) for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        self.bot.pool.putconn(conn)
        if (data is not None):
            servers = [next(item for item in self.bot.DCSServers if item["server_name"] == data['server_name'])]
        else:
            servers = self.bot.DCSServers
        for server in servers:
            for ban in self.banList:
                self.sendtoDCS('{"command":"ban", "ucid":"' + ban['ucid'] + '", "channel":"' +
                               str(server['status_channel']) + '"}', server['host'], server['port'])

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
            self.bot.pool.putconn(conn)

    @commands.command(description='Send a chat message to a running DCS instance', usage='<message>', hidden=True)
    @commands.has_role('DCS')
    @commands.guild_only()
    async def chat(self, ctx, *args):
        server = await self.get_server(ctx)
        self.sendtoDCS('{"command":"sendChatMessage", "channel":"' + str(ctx.channel.id) +
                       '", "message": "' + ' '.join(args) + '", "from": "' +
                       ctx.message.author.display_name + '"}',
                       server['host'], server['port'])

    @commands.command(description='Lists the configured DCS servers')
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def status(self, ctx):
        embed = discord.Embed(title='Currently configured DCS servers:', color=discord.Color.blue())
        ids = servers = conns = ''
        for server in self.bot.DCSServers:
            ids += str(self.bot.DCSServers.index(server)) + '\n'
            servers += server['server_name'] + '\n'
            conns += '{}:{}'.format(server['host'], server['port']) + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Server', value=servers)
        embed.add_field(name='Connection', value=conns)
        await ctx.send(embed=embed)

    @commands.command(description='Shows the active DCS mission', hidden=True)
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def mission(self, ctx):
        server = await self.get_server(ctx)
        if (int(server['status_channel']) != ctx.channel.id):
            await ctx.send('This command can only be used in the status channel.')
        else:
            await ctx.message.delete()
            if (self.getCurrentMissionID(server['server_name']) != -1):
                self.sendtoDCS('{"command":"getRunningMission", "channel":"' +
                               str(ctx.channel.id) + '"}', server['host'], server['port'])
            else:
                msg = await ctx.send('There is currently no mission running on server "' + server['server_name'] + '"')
                await asyncio.sleep(5)
                await msg.delete()

    @commands.command(description='Shows briefing of the active DCS mission', aliases=['brief'])
    @commands.has_role('DCS')
    @commands.guild_only()
    async def briefing(self, ctx):
        server = await self.get_server(ctx)
        if (self.getCurrentMissionID(server['server_name']) != -1):
            self.sendtoDCS('{"command":"getMissionDetails", "channel":"' +
                           str(ctx.message.id) + '"}', server['host'], server['port'])
            data = await self.wait_for('getMissionDetails', str(ctx.message.id))
            embed = discord.Embed(title=data['current_mission'], color=discord.Color.blue())
            embed.description = data['mission_description'][:2048]
            await ctx.send(embed=embed)
        else:
            await ctx.send('There is currently no mission running on server "' + server['server_name'] + '"')

    @commands.command(description='List the current players on this server', usage='<server>', hidden=True)
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def players(self, ctx):
        server = await self.get_server(ctx)
        if (int(server['status_channel']) != ctx.channel.id):
            await ctx.send('This command can only be used in the status channel.')
        else:
            await ctx.message.delete()
            if (self.getCurrentMissionID(server['server_name']) != -1):
                self.sendtoDCS('{"command":"getCurrentPlayers", "channel":"' +
                               str(ctx.channel.id) + '"}', server['host'], server['port'])
            else:
                msg = await ctx.send('There is currently no mission running on server "' + server['server_name'] + '"')
                await asyncio.sleep(5)
                await msg.delete()

    @commands.command(description='Restarts the current active mission', usage='[delay] [message]')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def restart(self, ctx, *args):
        server = await self.get_server(ctx)
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
            self.sendtoDCS('{"command":"sendChatMessage", "channel":"' + str(ctx.channel.id) +
                           '", "message":"' + message + '", "from": "' +
                           ctx.message.author.display_name + '"}', server['host'], server['port'])
            await asyncio.sleep(delay)
            await msg.delete()
            self.sendtoDCS('{"command":"restartMission", "channel":"' +
                           str(ctx.channel.id) + '"}', server['host'], server['port'])
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
        self.sendtoDCS('{"command":"listMissions", "channel":"' +
                       str(ctx.message.id) + '"}', server['host'], server['port'])
        data = await self.wait_for('listMissions', str(ctx.message.id))
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
        return await ctx.send(embed=embed)

    @commands.command(description='Loads a mission by ID', usage='<ID>')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def load(self, ctx, id):
        server = await self.get_server(ctx)
        self.sendtoDCS('{"command":"loadMission", "id":' + id + ', "channel":"' +
                       str(ctx.channel.id) + '"}', server['host'], server['port'])
        await ctx.send('Loading mission ' + id + ' ...')

    @commands.command(description='Deletes a mission from the list', usage='<ID>')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def delete(self, ctx, id):
        server = await self.get_server(ctx)
        self.sendtoDCS('{"command":"deleteMission", "id":' + id + ', "channel":"' +
                       str(ctx.channel.id) + '"}', server['host'], server['port'])

    @commands.command(description='Adds a mission to the list', usage='<path>')
    @commands.has_role('DCS Admin')
    @commands.guild_only()
    async def add(self, ctx, *path):
        server = await self.get_server(ctx)
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
        self.sendtoDCS('{"command":"addMission", "path":"' + file + '", "channel":"' +
                       str(ctx.channel.id) + '"}', server['host'], server['port'])

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
                    cursor.execute('UPDATE players SET ban = true WHERE ucid = %s', (ucid, ))
                    for server in self.bot.DCSServers:
                        self.sendtoDCS('{"command":"ban", "ucid":"' + ucid + '", "channel":"' +
                                       str(ctx.channel.id) + '"}', server['host'], server['port'])
                conn.commit()
            await ctx.send('Player {} banned.'.format(user))
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        self.bot.pool.putconn(conn)

    @commands.command(description='Unbans a user by ucid or discord id', usage='<member / ucid>')
    @commands.has_any_role('Admin', 'Moderator')
    @commands.guild_only()
    async def unban(self, ctx, user):
        server = await self.get_server(ctx)
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
                    cursor.execute('UPDATE players SET ban = false WHERE ucid = %s', (ucid, ))
                    for server in self.bot.DCSServers:
                        self.sendtoDCS('{"command":"unban", "ucid":"' + ucid + '", "channel":"' +
                                       str(ctx.channel.id) + '"}', server['host'], server['port'])
                conn.commit()
            await ctx.send('Player {} unbanned.'.format(user))
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        self.bot.pool.putconn(conn)

    @commands.command(description='Shows active bans')
    @commands.has_any_role('Admin', 'Moderator')
    @commands.guild_only()
    async def bans(self, ctx):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT ucid, discord_id FROM players WHERE ban = true')
                rows = list(cursor.fetchall())
                if (rows is not None and len(rows) > 0):
                    embed = discord.Embed(title='List of Bans', color=discord.Color.blue())
                    ucids = discord_ids = discord_names = ''
                    for ban in rows:
                        if (ban['discord_id'] != -1):
                            user = await self.bot.fetch_user(ban['discord_id'])
                        else:
                            user = None
                        discord_names += (user.name if user else '<unknown>') + '\n'
                        ucids += ban['ucid'] + '\n'
                        discord_ids += str(ban['discord_id']) + '\n'
                    embed.add_field(name='Name', value=discord_names)
                    embed.add_field(name='UCID', value=ucids)
                    embed.add_field(name='Discord ID', value=discord_ids)
                    await ctx.send(embed=embed)
                else:
                    await ctx.send('No players are banned at the moment.')
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if (self.bot.config.getboolean('BOT', 'AUTOBAN') is True):
            self.bot.log.info(
                'Member {} has left guild {} - ban them on DCS servers and delete their stats.'.format(member.display_name, member.guild.name))
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('UPDATE players SET ban = 1 WHERE discord_id = %s', (member.id, ))
                    cursor.execute(
                        'DELETE FROM statistics WHERE player_ucid IN (SELECT ucid FROM players WHERE discord_id = %s)', (member.id, ))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.bot.log.exception(error)
                conn.rollback()
            self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if (self.bot.config.getboolean('BOT', 'AUTOBAN') is True):
            self.bot.log.info(
                'Member {} has joined guild {} - remove possible bans from DCS servers.'.format(member.display_name, member.guild.name))
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('UPDATE players SET ban = 0 WHERE discord_id = %s', (member.id, ))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.bot.log.exception(error)
                conn.rollback()
            self.bot.pool.putconn(conn)
            self.updateBans()

    @tasks.loop(minutes=10.0)
    async def update_status(self):
        for server in self.bot.DCSServers:
            if (self.getCurrentMissionID(server['server_name']) != -1):
                self.sendtoDCS('{"command":"getRunningMission", "channel":"' +
                               str(server['status_channel']) + '"}', server['host'], server['port'])
            if (server['server_name'] not in self.player_data):
                self.sendtoDCS('{"command":"getCurrentPlayers", "channel":"' +
                               str(server['status_channel']) + '"}', server['host'], server['port'])
        if (self.banList is None):
            self.updateBans()

    @tasks.loop()
    async def start_listener(self):
        for server in self.bot.DCSServers:
            channel = await self.bot.fetch_channel(server['status_channel'])
            if ('mission_embed' in server and server['mission_embed']):
                with suppress(Exception):
                    self.mission_embeds[server['server_name']] = await channel.fetch_message(server['mission_embed'])
            if ('players_embed' in server and server['players_embed']):
                with suppress(Exception):
                    self.players_embeds[server['server_name']] = await channel.fetch_message(server['players_embed'])
        self.task = await self.loop.run_in_executor(ThreadPoolExecutor(), self.listener)

    def listener(self):

        class UDPListener(socketserver.BaseRequestHandler):

            async def sendMessage(data):
                return await self.get_channel(data).send(data['message'])

            async def registerDCSServer(data):
                self.bot.log.info('Registering DCS-Server ' + data['server_name'])
                if (data['status_channel'].isnumeric() is True):
                    index = next((i for i, item in enumerate(self.bot.DCSServers)
                                  if item['server_name'] == data['server_name']), None)
                    if (index is not None):
                        self.bot.DCSServers[index] = data
                    else:
                        self.bot.DCSServers.append(data)
                    SQL = 'INSERT INTO servers (server_name, host, port, chat_channel, status_channel, admin_channel) VALUES(%s, %s, %s, %s, %s, %s) ON CONFLICT (server_name) DO UPDATE SET host=%s, port=%s, chat_channel=%s, status_channel=%s, admin_channel=%s'
                    conn = self.bot.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            cursor.execute(SQL, (data['server_name'], data['host'], data['port'],
                                                 data['chat_channel'], data['status_channel'], data['admin_channel'],
                                                 data['host'], data['port'],
                                                 data['chat_channel'], data['status_channel'], data['admin_channel']))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        self.bot.log.exception(error)
                        conn.rollback()
                    self.bot.pool.putconn(conn)
                else:
                    self.bot.log.error(
                        'Configuration mismatch. Please check settings in DCSServerBotConfig.lua!')

            async def onMissionLoadBegin(data):
                embed = discord.Embed(title='Loading ...', color=discord.Color.blue())
                embed.add_field(name='Name', value=data['current_mission'])
                embed.add_field(name='Map', value=data['current_map'])
                embed.add_field(name='Uptime', value='-:--:--')
                embed.add_field(name='Active Players', value='-')
                embed.add_field(name='Blue Slots', value='-')
                embed.add_field(name='Red Slots', value='-')
                return await self.setMissionEmbed(data, embed)

            async def onMissionLoadEnd(data):
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
                self.bot.pool.putconn(conn)
                await UDPListener.getRunningMission(data)
                self.updatePlayerList(data)
                self.updateBans(data)
                return None

            async def onSimulationStart(data):
                return None

            async def getRunningMission(data):
                embed = discord.Embed(title='Running', color=discord.Color.blue())
                embed.add_field(name='Name', value=data['current_mission'])
                embed.add_field(name='Map', value=data['current_map'])
                embed.add_field(name='Uptime', value=str(timedelta(seconds=int(data['mission_time']))))
                embed.add_field(name='Active Players', value=str(int(data['num_players']) - 1))
                embed.add_field(name='Blue Slots', value=data['num_slots_blue']
                                if ('num_slots_blue' in data) else '-')
                embed.add_field(name='Red Slots', value=data['num_slots_red'] if ('num_slots_red' in data) else '-')
                embed.set_footer(text='Updates every 10 minutes')
                return await self.setMissionEmbed(data, embed)

            async def getCurrentPlayers(data):
                self.player_data[data['server_name']] = pd.DataFrame.from_dict(data['players'])
                embed = discord.Embed(title='Active Players', color=discord.Color.blue())
                names = units = sides = '' if (len(data['players']) > 1) else '_ _'
                for player in data['players']:
                    if(player['id'] == 1):
                        continue
                    names += player['name'] + '\n'
                    units += (player['unit_type'] if (player['side'] != 0) else '_ _') + '\n'
                    sides += self.PLAYER_SIDES[player['side']] + '\n'
                embed.add_field(name='Name', value=names)
                embed.add_field(name='Unit', value=units)
                embed.add_field(name='Side', value=sides)
                return await self.setPlayersEmbed(data, embed)

            async def onPlayerConnect(data):
                if (data['id'] != 1):
                    await self.get_channel(data, 'chat_channel').send('{} connected to server'.format(data['name']))

            async def onPlayerStart(data):
                if (data['id'] != 1):
                    SQL_PLAYERS = 'INSERT INTO players (ucid, discord_id) VALUES(%s, %s) ON CONFLICT (ucid) DO UPDATE SET discord_id = %s WHERE players.discord_id = -1'
                    discord_user = self.find_discord_user(data)
                    discord_id = discord_user.id if (discord_user) else -1
                    conn = self.bot.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            cursor.execute(SQL_PLAYERS, (data['ucid'], discord_id, discord_id))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        self.bot.log.exception(error)
                        conn.rollback()
                    self.bot.pool.putconn(conn)
                    server = next(item for item in self.bot.DCSServers if item["server_name"] == data['server_name'])
                    if (discord_user is None):
                        self.sendtoDCS('{"command":"sendChatMessage", "message":"' + self.bot.config['DCS']['GREETING_MESSAGE_UNKNOWN'].format(data['name']) + '", "to": "' +
                                       str(data['id']) + '"}', server['host'], server['port'])
                        await self.get_channel(data, 'admin_channel').send('Player {} (ucid={}) can\'t be matched to a discord user.'.format(data['name'], data['ucid']))
                    else:
                        name = discord_user.nick if (discord_user.nick) else discord_user.name
                        self.sendtoDCS('{"command":"sendChatMessage", "message":"' + self.bot.config['DCS']['GREETING_MESSAGE_MEMBERS'].format(name, data['server_name']) + '" , "to": "' +
                                       str(data['id']) + '"}', server['host'], server['port'])
                    self.updatePlayerList(data)
                    self.updateMission(data)
                    return None

            async def onPlayerStop(data):
                return None

            async def onPlayerChangeSlot(data):
                if ('side' in data):
                    SQL_CLOSE_STATISTICS = 'UPDATE statistics SET hop_off = NOW() WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL'
                    SQL_INSERT_STATISTICS = 'INSERT INTO statistics (mission_id, player_ucid, slot) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING'
                    player = None
                    with suppress(Exception):
                        player = self.get_player(data['server_name'], data['id'])
                    if (data['side'] != self.SIDE_SPECTATOR):
                        conn = self.bot.pool.getconn()
                        try:
                            with closing(conn.cursor()) as cursor:
                                cursor.execute(SQL_CLOSE_STATISTICS, (self.getCurrentMissionID(
                                    data['server_name']), data['ucid']))
                                cursor.execute(SQL_INSERT_STATISTICS, (self.getCurrentMissionID(
                                    data['server_name']), data['ucid'], data['unit_type']))
                                conn.commit()
                        except (Exception, psycopg2.DatabaseError) as error:
                            self.bot.log.exception(error)
                            conn.rollback()
                        self.bot.pool.putconn(conn)
                        if (player is not None):
                            await self.get_channel(data, 'chat_channel').send('{} player {} occupied {} {}'.format(
                                self.PLAYER_SIDES[player['side'] if player['side'] != 0 else 3], data['name'],
                                self.PLAYER_SIDES[data['side']], data['unit_type']))
                    elif (player is not None):
                        await self.get_channel(data, 'chat_channel').send('{} player {} returned to Spectators'.format(
                            self.PLAYER_SIDES[player['side']], data['name']))
                    self.updatePlayerList(data)
                return None

            async def onGameEvent(data):
                if (data['eventName'] == 'mission_end'):
                    embed = discord.Embed(title='Shutdown', color=discord.Color.blue())
                    embed.add_field(name='Name', value='No mission running')
                    embed.add_field(name='Map', value='-')
                    embed.add_field(name='Uptime', value='-:--:--')
                    embed.add_field(name='Active Players', value='-')
                    embed.add_field(name='Blue Slots', value='-')
                    embed.add_field(name='Red Slots', value='-')
                    await self.setMissionEmbed(data, embed)
                    conn = self.bot.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            cursor.execute('UPDATE statistics SET hop_off = NOW() WHERE mission_id = %s AND hop_off IS NULL',
                                           (self.getCurrentMissionID(data['server_name']), ))
                            cursor.execute('UPDATE missions SET mission_end = NOW() WHERE id = %s',
                                           (self.getCurrentMissionID(data['server_name']), ))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        self.bot.log.exception(error)
                        conn.rollback()
                    self.bot.pool.putconn(conn)
                elif (data['eventName'] in ['connect', 'change_slot']):  # these events are handled differently
                    return None
                elif (data['eventName'] == 'disconnect'):
                    if (data['arg1'] != 1):
                        player = self.get_player(data['server_name'], data['arg1'])
                        conn = self.bot.pool.getconn()
                        try:
                            with closing(conn.cursor()) as cursor:
                                cursor.execute('UPDATE statistics SET hop_off = NOW() WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
                                               (self.getCurrentMissionID(data['server_name']), player['ucid']))
                                conn.commit()
                        except (Exception, psycopg2.DatabaseError) as error:
                            self.bot.log.exception(error)
                            conn.rollback()
                        self.bot.pool.putconn(conn)
                        player = self.get_player(data['server_name'], data['arg1'])
                        if (player['side'] == self.SIDE_SPECTATOR):
                            await self.get_channel(data, 'chat_channel').send('Player {} disconnected'.format(player['name']))
                        else:
                            await self.get_channel(data, 'chat_channel').send('{} player {} disconnected'.format(
                                self.PLAYER_SIDES[player['side']], player['name']))
                        self.updatePlayerList(data)
                        self.updateMission(data)
                elif (data['eventName'] == 'friendly_fire'):
                    player1 = self.get_player(data['server_name'], data['arg1'])
                    if (data['arg3'] != -1):
                        player2 = self.get_player(data['server_name'], data['arg3'])
                    else:
                        player2 = None
                    await self.get_channel(data, 'chat_channel').send(self.EVENT_TEXTS[data['eventName']].format(
                        self.PLAYER_SIDES[player1['side']], 'player ' + player1['name'],
                        ('player ' + player2['name']) if player2 is not None else 'AI',
                        data['arg2'] if (len(data['arg2']) > 0) else 'Cannon'))
                elif (data['eventName'] == 'kill'):
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
                                player1 = self.get_player(data['server_name'], data['arg1'])
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
                                player2 = self.get_player(data['server_name'], data['arg4'])
                                if (death_type in self.SQL_EVENT_UPDATES.keys()):
                                    cursor.execute(self.SQL_EVENT_UPDATES[death_type],
                                                   (self.getCurrentMissionID(data['server_name']), player2['ucid']))
                            else:
                                player2 = None
                            conn.commit()
                            await self.get_channel(data, 'chat_channel').send(self.EVENT_TEXTS[data['eventName']].format(
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
                    self.bot.pool.putconn(conn)
                elif (data['eventName'] in ['takeoff', 'landing', 'crash', 'eject', 'pilot_death']):
                    if (data['arg1'] != -1):
                        player = self.get_player(data['server_name'], data['arg1'])
                        if (data['eventName'] in ['takeoff', 'landing']):
                            await self.get_channel(data, 'chat_channel').send(self.EVENT_TEXTS[data['eventName']].format(
                                self.PLAYER_SIDES[player['side']], player['name'], data['arg3']))
                        else:
                            await self.get_channel(data, 'chat_channel').send(self.EVENT_TEXTS[data['eventName']].format(
                                self.PLAYER_SIDES[player['side']], player['name']))
                        if (data['eventName'] in self.SQL_EVENT_UPDATES.keys()):
                            conn = self.bot.pool.getconn()
                            try:
                                with closing(conn.cursor()) as cursor:
                                    cursor.execute(self.SQL_EVENT_UPDATES[data['eventName']],
                                                   (self.getCurrentMissionID(data['server_name']), player['ucid']))
                                    conn.commit()
                            except (Exception, psycopg2.DatabaseError) as error:
                                self.bot.log.exception(error)
                                conn.rollback()
                            self.bot.pool.putconn(conn)
                else:
                    self.bot.log.info('Unhandled event: ' + data['eventName'])
                return None

            async def onChatMessage(data):
                if ('from_id' in data and data['from_id'] != 1 and len(data['message']) > 0):
                    return await self.get_channel(data, 'chat_channel').send(data['from_name'] + ': ' + data['message'])
                return None

            def handle(s):
                data = s.request[0].strip()
                dt = json.loads(data)
                text = "{}: {}".format(
                    s.client_address[0], json.dumps(dt))
                self.bot.log.info(text)
                try:
                    command = dt['command']
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
                                    self.loop.call_soon_threadsafe(future.set_result, dt)
                                    removed.append(i)
                            if len(removed) == len(listeners):
                                self.listeners.pop(command)
                            else:
                                for idx in reversed(removed):
                                    del listeners[idx]
                            return
                    future = asyncio.run_coroutine_threadsafe(getattr(UDPListener, command)(dt), self.loop)
                    future.result()
                except (AttributeError) as error:
                    self.bot.log.exception(error)
                    self.bot.log.info('Method ' + dt['command'] + '() not implemented.')

        with socketserver.ThreadingUDPServer((self.host, self.port), UDPListener) as self.server:
            self.bot.log.info('UDP listener started on port ' + str(self.port) + ' accepting commands.')
            self.server.max_packet_size = 65504
            self.server.serve_forever()


def setup(bot):
    bot.add_cog(DCS(bot))
