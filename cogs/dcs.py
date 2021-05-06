# dcs.py
import asyncio
import discord
import json
import pandas as pd
import socket
import socketserver
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, suppress
from datetime import timedelta
from discord.ext import commands, tasks
from sqlite3 import Error


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
        'takeoff': 'UPDATE statistics SET takeoffs = takeoffs + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'landing': 'UPDATE statistics SET landings = landings + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'eject': 'UPDATE statistics SET ejections = ejections + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'crash': 'UPDATE statistics SET crashes = crashes + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'pilot_death': 'UPDATE statistics SET deaths = deaths + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'pvp': 'UPDATE statistics SET kills = kills + 1, pvp = pvp + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'teamkill': 'UPDATE statistics SET teamkills = teamkills + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'kill_planes': 'UPDATE statistics SET kills = kills + 1, kills_planes = kills_planes + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'kill_helicopters': 'UPDATE statistics SET kills = kills + 1, kills_helicopters = kills_helicopters + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'kill_ships': 'UPDATE statistics SET kills = kills + 1, kills_ships = kills_ships + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'kill_sams': 'UPDATE statistics SET kills = kills + 1, kills_sams = kills_sams + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'kill_ground': 'UPDATE statistics SET kills = kills + 1, kills_ground = kills_ground + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'teamdeath': 'UPDATE statistics SET deaths = deaths - 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'deaths_pvp': 'UPDATE statistics SET deaths_pvp = deaths_pvp + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'deaths_planes': 'UPDATE statistics SET deaths_planes = deaths_planes + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'deaths_helicopters': 'UPDATE statistics SET deaths_helicopters = deaths_helicopters + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'deaths_ships': 'UPDATE statistics SET deaths_ships = deaths_ships + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'deaths_sams': 'UPDATE statistics SET deaths_sams = deaths_sams + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
        'deaths_ground': 'UPDATE statistics SET deaths_ground = deaths_ground + 1 WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL'
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
        try:
            with closing(self.bot.conn.cursor()) as cursor:
                self.bot.DCSServers = [dict(row) for row in cursor.execute(
                    'SELECT server_name, host, port, chat_channel, status_channel, admin_channel, mission_embed, players_embed FROM servers').fetchall()]
            self.bot.log.info('{} server(s) read from database.'.format(len(self.bot.DCSServers)))
        except (Exception, Error) as error:
            self.bot.log.exception(error)
        self.start_listener.start()
        self.update_status.start()

    def cog_unload(self):
        self.update_status.cancel()
        self.server.shutdown()
        self.task.cancel()
        self.start_listener.cancel()

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
        try:
            with closing(self.bot.conn.cursor()) as cursor:
                result = cursor.execute('SELECT discord_id FROM players WHERE ucid = ? AND discord_id <> -1',
                                        (data['ucid'], )).fetchone()
                if (result is not None):
                    discord_id = result[0]
        except (Exception, Error) as error:
            self.bot.log.exception(error)
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
        try:
            with closing(self.bot.conn.cursor()) as cursor:
                id = cursor.execute(
                    'SELECT id FROM missions WHERE server_name = ? AND mission_end IS NULL', (server_name, )).fetchone()
                return id[0] if id else -1
        except (Exception, Error) as error:
            self.bot.log.exception(error)

    def updatePlayerList(self, data):
        server = next(item for item in self.bot.DCSServers if item["server_name"] == data['server_name'])
        self.sendtoDCS('{"command":"getCurrentPlayers", "channel":"' +
                       str(data['channel']) + '"}', server['host'], server['port'])

    def updateMission(self, data):
        server = next(item for item in self.bot.DCSServers if item["server_name"] == data['server_name'])
        self.sendtoDCS('{"command":"getRunningMission", "channel":"' +
                       str(data['channel']) + '"}', server['host'], server['port'])

    def updateBans(self, data=None):
        try:
            with closing(self.bot.conn.cursor()) as cursor:
                self.banList = [dict(row) for row in cursor.execute(
                    'SELECT ucid, discord_id FROM players WHERE ban = 1').fetchall()]
        except (Exception, Error) as error:
            self.bot.log.exception(error)
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
            except Exception:
                message = None
        if (message is None):
            self.players_embeds[data['server_name']] = await self.get_channel(data).send(embed=embed)
            try:
                with closing(self.bot.conn.cursor()) as cursor:
                    cursor.execute('UPDATE servers SET players_embed=? WHERE server_name=?',
                                   (self.players_embeds[data['server_name']].id, data['server_name']))
                    self.bot.conn.commit()
            except (Exception, Error) as error:
                self.bot.conn.rollback()
                self.bot.log.exception(error)

    async def setMissionEmbed(self, data, embed):
        message = self.mission_embeds[data['server_name']] if (
            data['server_name'] in self.mission_embeds) else None
        if (message is not None):
            try:
                await message.edit(embed=embed)
            except Exception:
                message = None
        if (message is None):
            self.mission_embeds[data['server_name']] = await self.get_channel(data).send(embed=embed)
            try:
                with closing(self.bot.conn.cursor()) as cursor:
                    cursor.execute('UPDATE servers SET mission_embed=? WHERE server_name=?',
                                   (self.mission_embeds[data['server_name']].id, data['server_name']))
                    self.bot.conn.commit()
            except (Exception, Error) as error:
                self.bot.conn.rollback()
                self.bot.log.exception(error)

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
                           str(ctx.channel.id) + '"}', server['host'], server['port'])
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
                       str(ctx.channel.id) + '"}', server['host'], server['port'])

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
        self.sendtoDCS('{"command":"addMission", "path":"' + ' '.join(path) + '", "channel":"' +
                       str(ctx.channel.id) + '"}', server['host'], server['port'])

    @commands.command(description='Bans a user by ucid or discord id', usage='<member / ucid>')
    @commands.has_any_role('Admin', 'Moderator')
    @commands.guild_only()
    async def ban(self, ctx, user):
        server = await self.get_server(ctx)
        try:
            if (user.startswith('<')):
                discord_id = user.replace('<@!', '').replace('>', '')
                with closing(self.bot.conn.cursor()) as cursor:
                    cursor.execute('UPDATE players SET ban = 1 WHERE discord_id = ?', (discord_id, ))
                    rows = cursor.execute('SELECT ucid FROM players WHERE discord_id = ?',
                                          (discord_id, )).fetchall()
                    for row in rows:
                        self.sendtoDCS('{"command":"ban", "ucid":"' + row[0] + '", "channel":"' +
                                       str(ctx.channel.id) + '"}', server['host'], server['port'])
                    self.bot.conn.commit()
            else:
                with closing(self.bot.conn.cursor()) as cursor:
                    cursor.execute('UPDATE players SET ban = 1 WHERE ucid = ?', (user, ))
                    self.sendtoDCS('{"command":"ban", "ucid":"' + user + '", "channel":"' +
                                   str(ctx.channel.id) + '"}', server['host'], server['port'])
                    self.bot.conn.commit()
            await ctx.send('Player {} banned.'.format(user))
        except (Exception, Error) as error:
            self.bot.conn.rollback()
            self.bot.log.exception(error)

    @commands.command(description='Unbans a user by ucid or discord id', usage='<member / ucid>')
    @commands.has_any_role('Admin', 'Moderator')
    @commands.guild_only()
    async def unban(self, ctx, user):
        server = await self.get_server(ctx)
        try:
            if (user.startswith('<')):
                discord_id = user.replace('<@!', '').replace('>', '')
                with closing(self.bot.conn.cursor()) as cursor:
                    cursor.execute('UPDATE players SET ban = 0 WHERE discord_id = ?', (discord_id, ))
                    rows = cursor.execute('SELECT ucid FROM players WHERE discord_id = ?',
                                          (discord_id, )).fetchall()
                    for row in rows:
                        self.sendtoDCS('{"command":"unban", "ucid":"' + row[0] + '", "channel":"' +
                                       str(ctx.channel.id) + '"}', server['host'], server['port'])
                    self.bot.conn.commit()
            else:
                with closing(self.bot.conn.cursor()) as cursor:
                    cursor.execute('UPDATE players SET ban = 0 WHERE ucid = ?', (user, ))
                    self.sendtoDCS('{"command":"unban", "ucid":"' + user + '", "channel":"' +
                                   str(ctx.channel.id) + '"}', server['host'], server['port'])
                    self.bot.conn.commit()
            await ctx.send('Player {} unbanned.'.format(user))
        except (Exception, Error) as error:
            self.bot.conn.rollback()
            self.bot.log.exception(error)

    @commands.command(description='Shows active bans')
    @commands.has_any_role('Admin', 'Moderator')
    @commands.guild_only()
    async def bans(self, ctx):
        try:
            with closing(self.bot.conn.cursor()) as cursor:
                rows = list(cursor.execute(
                    'SELECT ucid, discord_id FROM players WHERE ban = 1').fetchall())
                if (rows is not None and len(rows) > 0):
                    embed = discord.Embed(title='List of Bans', color=discord.Color.blue())
                    ucids = discord_ids = discord_names = ''
                    for ban in rows:
                        if (ban['discord_id'] != -1):
                            user = await self.bot.fetch_user(ban['discord_id'])
                        else:
                            user = None
                        discord_names += (user.name if user else '<unknown>') + '\n'
                        ucids += ban['ucid']
                        discord_ids += str(ban['discord_id'])
                    embed.add_field(name='Name', value=discord_names)
                    embed.add_field(name='UCID', value=ucids)
                    embed.add_field(name='Discord ID', value=discord_ids)
                    await ctx.send(embed=embed)
                else:
                    await ctx.send('No players are banned at the moment.')
        except (Exception, Error) as error:
            self.bot.log.exception(error)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        self.bot.log.info(
            'Member {} has left guild {} - ban them on DCS servers.'.format(member.display_name, member.guild.name))
        try:
            with closing(self.bot.conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET ban = 1 WHERE discord_id = ?', (member.id, ))
                self.bot.conn.commit()
        except (Exception, Error) as error:
            self.bot.conn.rollback()
            self.bot.log.exception(error)
        self.updateBans()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        self.bot.log.info(
            'Member {} has joined guild {} - remove possible bans from DCS servers.'.format(member.display_name, member.guild.name))
        try:
            with closing(self.bot.conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET ban = 0 WHERE discord_id = ?', (member.id, ))
                self.bot.conn.commit()
        except (Exception, Error) as error:
            self.bot.conn.rollback()
            self.bot.log.exception(error)
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
            if (server['mission_embed']):
                with suppress(Exception):
                    self.mission_embeds[server['server_name']] = await channel.fetch_message(server['mission_embed'])
            if (server['players_embed']):
                with suppress(Exception):
                    self.players_embeds[server['server_name']] = await channel.fetch_message(server['players_embed'])
        self.loop = asyncio.get_event_loop()
        self.task = await self.loop.run_in_executor(ThreadPoolExecutor(), self.listener)

    def listener(self):

        class UDPListener(socketserver.BaseRequestHandler):

            async def sendMessage(data):
                return await self.get_channel(data).send(data['message'])

            async def registerDCSServer(data):
                self.bot.log.info('Registering DCS-Server ' + data['server_name'])
                index = next((i for i, item in enumerate(self.bot.DCSServers)
                              if item['server_name'] == data['server_name']), None)
                if (index is not None):
                    self.bot.DCSServers[index] = data
                else:
                    self.bot.DCSServers.append(data)
                SQL = 'INSERT INTO servers (server_name, host, port, chat_channel, status_channel, admin_channel) VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT (server_name) DO UPDATE SET host=?, port=?, chat_channel=?, status_channel=?, admin_channel=?'
                try:
                    with closing(self.bot.conn.cursor()) as cursor:
                        cursor.execute(SQL, (data['server_name'], data['host'], data['port'],
                                             data['chat_channel'], data['status_channel'], data['admin_channel'],
                                             data['host'], data['port'],
                                             data['chat_channel'], data['status_channel'], data['admin_channel']))
                        self.bot.conn.commit()
                except (Exception, Error) as error:
                    self.bot.conn.rollback()
                    self.bot.log.exception(error)

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
                SQL_CLOSE_STATISTICS = 'UPDATE statistics SET hop_off = datetime(\'now\') WHERE mission_id IN (SELECT id FROM missions WHERE server_name =? AND mission_end IS NULL) AND hop_off IS NULL'
                SQL_CLOSE_MISSIONS = 'UPDATE missions SET mission_end = datetime(\'now\') WHERE server_name = ? AND mission_end IS NULL'
                SQL_START_MISSION = 'INSERT INTO missions (server_name, mission_name, mission_theatre) VALUES(?, ?, ?)'
                try:
                    with closing(self.bot.conn.cursor()) as cursor:
                        cursor.execute(SQL_CLOSE_STATISTICS, (data['server_name'],))
                        cursor.execute(SQL_CLOSE_MISSIONS, (data['server_name'],))
                        cursor.execute(SQL_START_MISSION, (data['server_name'],
                                                           data['current_mission'], data['current_map']))
                        self.bot.conn.commit()
                except (Exception, Error) as error:
                    self.bot.conn.rollback()
                    self.bot.log.exception(error)
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

            async def getMissionDetails(data):
                embed = discord.Embed(title=data['current_mission'], color=discord.Color.blue())
                embed.description = data['mission_description'][:2048]
                return await self.get_channel(data).send(embed=embed)

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
                return await self.get_channel(data).send(embed=embed)

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
                    SQL_PLAYERS = 'INSERT INTO players (ucid, discord_id) VALUES(?, ?) ON CONFLICT (ucid) DO UPDATE SET discord_id = ? WHERE discord_id = -1'
                    discord_user = self.find_discord_user(data)
                    discord_id = discord_user.id if (discord_user) else -1
                    try:
                        with closing(self.bot.conn.cursor()) as cursor:
                            cursor.execute(SQL_PLAYERS, (data['ucid'], discord_id, discord_id))
                            self.bot.conn.commit()
                    except (Exception, Error) as error:
                        self.bot.conn.rollback()
                        self.bot.log.exception(error)
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
                    SQL_CLOSE_STATISTICS = 'UPDATE statistics SET hop_off = datetime(\'now\') WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL'
                    SQL_INSERT_STATISTICS = 'INSERT INTO statistics (mission_id, player_ucid, slot) VALUES (?, ?, ?) ON CONFLICT DO NOTHING'
                    player = self.get_player(data['server_name'], data['id'])
                    if (data['side'] != self.SIDE_SPECTATOR):
                        try:
                            await self.get_channel(data, 'chat_channel').send('{} player {} occupied {} {}'.format(
                                self.PLAYER_SIDES[player['side'] if player['side'] != 0 else 3], data['name'],
                                self.PLAYER_SIDES[data['side']], data['unit_type']))
                            with closing(self.bot.conn.cursor()) as cursor:
                                cursor.execute(SQL_CLOSE_STATISTICS, (self.getCurrentMissionID(
                                    data['server_name']), data['ucid']))
                                cursor.execute(SQL_INSERT_STATISTICS, (self.getCurrentMissionID(
                                    data['server_name']), data['ucid'], data['unit_type']))
                                self.bot.conn.commit()
                        except (Exception, Error) as error:
                            self.bot.conn.rollback()
                            self.bot.log.exception(error)
                    else:
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
                    try:
                        with closing(self.bot.conn.cursor()) as cursor:
                            cursor.execute('UPDATE statistics SET hop_off = datetime(\'now\') WHERE mission_id = ? AND hop_off IS NULL',
                                           (self.getCurrentMissionID(data['server_name']), ))
                            cursor.execute('UPDATE missions SET mission_end = datetime(\'now\') WHERE id = ?',
                                           (self.getCurrentMissionID(data['server_name']), ))
                            self.bot.conn.commit()
                    except (Exception, Error) as error:
                        self.bot.conn.rollback()
                        self.bot.log.exception(error)
                elif (data['eventName'] in ['connect', 'change_slot']):  # these events are handled differently
                    return None
                elif (data['eventName'] == 'disconnect'):
                    if (data['arg1'] != 1):
                        player = self.get_player(data['server_name'], data['arg1'])
                        try:
                            with closing(self.bot.conn.cursor()) as cursor:
                                cursor.execute('UPDATE statistics SET hop_off = datetime(\'now\') WHERE mission_id = ? AND player_ucid = ? AND hop_off IS NULL',
                                               (self.getCurrentMissionID(data['server_name']), player['ucid']))
                                self.bot.conn.commit()
                        except (Exception, Error) as error:
                            self.bot.conn.rollback()
                            self.bot.log.exception(error)
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
                    try:
                        with closing(self.bot.conn.cursor()) as cursor:
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
                            self.bot.conn.commit()
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
                    except (Exception, Error) as error:
                        self.bot.conn.rollback()
                        self.bot.log.exception(error)
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
                            player = self.get_player(data['server_name'], data['arg1'])
                            try:
                                with closing(self.bot.conn.cursor()) as cursor:
                                    cursor.execute(self.SQL_EVENT_UPDATES[data['eventName']],
                                                   (self.getCurrentMissionID(data['server_name']), player['ucid']))
                                    self.bot.conn.commit()
                            except (Exception, Error) as error:
                                self.bot.conn.rollback()
                                self.bot.log.exception(error)
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
                    future = asyncio.run_coroutine_threadsafe(getattr(UDPListener, dt['command'])(dt), self.loop)
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
