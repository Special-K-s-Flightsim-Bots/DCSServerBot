import asyncio
import discord
import itertools
import psutil
import psycopg2
import re
from contextlib import closing
from core import utils, const, DCSServerBot, Plugin, Report
from core.const import Status
from discord.ext import commands, tasks
from typing import Optional
from .listener import MissionEventListener


MAX_HUNG_MINUTES = 3


class Mission(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.update_mission_status.start()
        self.update_channel_name.start()

    def cog_unload(self):
        self.update_channel_name.stop()
        self.update_mission_status.cancel()
        super().cog_unload()

    def rename(self, old_name: str, new_name: str):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE missions SET server_name = %s WHERE server_name = %s', (new_name, old_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Shows the active DCS mission', hidden=True)
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def mission(self, ctx):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        if int(server['status_channel']) != ctx.channel.id:
            if server['status'] in [Status.RUNNING, Status.PAUSED]:
                players = self.bot.player_data[server['server_name']]
                num_players = len(players[players['active'] == True]) + 1
                report = Report(self.bot, self.plugin_name, 'serverStatus.json')
                env = await report.render(server=server, num_players=num_players)
                await ctx.send(embed=env.embed)
            else:
                await ctx.send('Server ' + server['server_name'] + ' is not running.')
                return
        else:
            await ctx.message.delete()
            self.bot.sendtoDCS(server, {"command": "getMissionUpdate", "channel": ctx.channel.id})

    @staticmethod
    def format_briefing_list(data: list[dict], marker, marker_emoji):
        embed = discord.Embed(title='Briefing', color=discord.Color.blue())
        embed.description = 'Select the server you want to get a briefing for:'
        ids = servers = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            servers += data[i]['server_name'] + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Server', value=servers)
        embed.add_field(name='_ _', value='_ _')
        embed.set_footer(text='Press a number to select a server.')
        return embed

    @commands.command(description='Shows briefing of the active mission', aliases=['brief'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def briefing(self, ctx):
        def read_passwords(server_name: str) -> dict:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('SELECT blue_password, red_password FROM servers WHERE server_name = %s',
                                   (server_name,))
                    row = cursor.fetchone()
                    return {"Blue": row[0], "Red": row[1]}
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)

        await ctx.message.delete()
        server = await utils.get_server(self, ctx)
        timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
        if not server:
            servers = []
            for server_name, server in self.globals.items():
                if server['status'] in [Status.RUNNING, Status.PAUSED]:
                    servers.append(server)
            if len(servers) == 0:
                await ctx.send('No running mission found.', delete_after=timeout if timeout > 0 else None)
                return
            elif len(servers) == 1:
                server = servers[0]
            else:
                n = await utils.selection_list(self, ctx, servers, self.format_briefing_list)
                if n < 0:
                    return
                server = servers[n]
        elif server['status'] not in [Status.RUNNING, Status.PAUSED]:
            await ctx.send('No running mission found.', delete_after=timeout if timeout > 0 else None)
            return
        mission_info = await self.bot.sendtoDCSSync(server, {"command": "getMissionDetails",
                                                             "channel": ctx.message.id})
        mission_info['passwords'] = read_passwords(server['server_name'])
        report = Report(self.bot, self.plugin_name, 'briefing.json')
        env = await report.render(mission_info=mission_info, server_name=server['server_name'], message=ctx.message)
        await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @commands.command(description='Shows information of a specific airport', aliases=['weather', 'airport', 'airfield', 'ap'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def atis(self, ctx, *args):
        name = ' '.join(args)
        for server_name, server in self.globals.items():
            if server['status'] not in [Status.RUNNING, Status.PAUSED]:
                continue
            await ctx.message.delete()
            for airbase in server['airbases']:
                if (name.casefold() in airbase['name'].casefold()) or (name.upper() == airbase['code']):
                    data = await self.bot.sendtoDCSSync(server, {
                        "command": "getWeatherInfo",
                        "channel": ctx.message.id,
                        "lat": airbase['lat'],
                        "lng": airbase['lng'],
                        "alt": airbase['alt']
                    })
                    embed = discord.Embed(
                        title=f'{server_name}\nReport for "{airbase["name"]}"', color=discord.Color.blue())
                    d, m, s, f = utils.dd_to_dms(airbase['lat'])
                    lat = ('N' if d > 0 else 'S') + '{:02d}°{:02d}\'{:02d}"'.format(int(abs(d)), int(m), int(s))
                    d, m, s, f = utils.dd_to_dms(airbase['lng'])
                    lng = ('E' if d > 0 else 'W') + '{:03d}°{:02d}\'{:02d}"'.format(int(abs(d)), int(m), int(s))
                    embed.add_field(name='Code', value=airbase['code'])
                    embed.add_field(name='Position', value=f'{lat}\n{lng}')
                    embed.add_field(name='Altitude', value='{} ft'.format(
                        int(airbase['alt'] * const.METER_IN_FEET)))
                    embed.add_field(name='▬' * 30, value='_ _', inline=False)
                    embed.add_field(name='Tower Frequencies', value='\n'.join(
                        '{:.3f} MHz'.format(x/1000000) for x in airbase['frequencyList']))
                    embed.add_field(name='Runways', value='\n'.join(airbase['runwayList']))
                    embed.add_field(name='Heading', value='{}°\n{}°'.format(
                        (airbase['rwy_heading'] + 180) % 360, airbase['rwy_heading']))
                    embed.add_field(name='▬' * 30, value='_ _', inline=False)
                    weather = data['weather']
                    embed.add_field(name='Active Runways', value='\n'.join(utils.get_active_runways(
                        airbase['runwayList'], weather['wind']['atGround'])))
                    embed.add_field(name='Surface Wind', value='{}° @ {} kts'.format(int(weather['wind']['atGround']['dir'] + 180) % 360, int(
                        weather['wind']['atGround']['speed'])))
                    visibility = weather['visibility']['distance']
                    if weather['enable_fog'] is True:
                        visibility = weather['fog']['visibility']
                    embed.add_field(name='Visibility', value='{:,} m'.format(
                        int(visibility)) if visibility < 10000 else '10 km (+)')
                    if 'clouds' in data:
                        if 'preset' in data['clouds']:
                            readable_name = data['clouds']['preset']['readableName']
                            metar = readable_name[readable_name.find('METAR:') + 6:]
                            embed.add_field(name='Cloud Cover',
                                            value=re.sub(' ', lambda m, c=itertools.count(): m.group() if not next(c) % 2 else '\n', metar))
                        else:
                            embed.add_field(name='Clouds', value='Base:\u2002\u2002\u2002\u2002 {:,} ft\nThickness: {:,} ft'.format(
                                int(data['clouds']['base'] * const.METER_IN_FEET + 0.5), int(data['clouds']['thickness'] * const.METER_IN_FEET + 0.5)))
                    else:
                        embed.add_field(name='Clouds', value='n/a')
                    embed.add_field(name='Temperature', value='{:.2f}° C'.format(data['temp']))
                    embed.add_field(name='QFE', value='{} hPa\n{:.2f} inHg\n{} mmHg'.format(
                        int(data['pressureHPA']), data['pressureIN'], int(data['pressureMM'])))
                    timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
                    await ctx.send(embed=embed, delete_after=timeout if timeout > 0 else None)
                    break

    @commands.command(description='List the current players on this server')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def players(self, ctx):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        ctx.message.delete()
        timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
        if server['status'] not in [Status.RUNNING, Status.PAUSED]:
            await ctx.send('Server ' + server['server_name'] + ' is not running.',
                           delete_after=timeout if timeout > 0 else None)
            return
        report = Report(self.bot, self.plugin_name, 'players.json')
        env = await report.render(server=server, sides=utils.get_sides(ctx.message, server))
        await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @commands.command(description='Restarts the current active mission', usage='[delay] [message]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def restart(self, ctx, delay: Optional[int] = 120, *args):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        if 'restart_pending' in server:
            await ctx.send('A restart is currently pending. Ignored.')
            return
        if server['status'] not in [Status.STOPPED, Status.SHUTDOWN]:
            server['restart_pending'] = True
            if server['status'] == Status.RUNNING:
                if delay > 0:
                    message = '!!! Server will be restarted in {} seconds !!!'.format(delay)
                else:
                    message = '!!! Server will be restarted NOW !!!'
                # have we got a message to present to the users?
                if len(args):
                    message += ' Reason: {}'.format(' '.join(args))

                if int(server['status_channel']) == ctx.channel.id:
                    await ctx.message.delete()
                msg = await ctx.send('Restarting mission in {} seconds (warning users before)...'.format(delay))
                self.bot.sendtoDCS(server, {
                    "command": "sendPopupMessage",
                    "channel": ctx.channel.id,
                    "message": message,
                    "from": ctx.message.author.display_name,
                    "to": "all",
                    "time": self.config['BOT']['MESSAGE_TIMEOUT']
                })
                await asyncio.sleep(delay)
                await msg.delete()
            self.bot.sendtoDCS(server, {"command": "restartMission", "channel": ctx.channel.id})
            msg = await ctx.send('Restart command sent. Mission will restart now.')
        else:
            msg = await ctx.send('There is currently no mission running on server "' + server['server_name'] + '"')
        if (msg is not None) and (int(server['status_channel']) == ctx.channel.id):
            await asyncio.sleep(5)
            await msg.delete()

    @staticmethod
    def format_mission_list(data, marker, marker_emoji):
        embed = discord.Embed(title='Mission List', color=discord.Color.blue())
        ids = missions = ''
        for i in range(0, len(data)):
            mission = data[i]
            mission = mission[(mission.rfind('\\') + 1):-4]
            if marker == (i + 1):
                ids += marker_emoji + '\n'
                missions += f'**{mission}**\n'
            else:
                ids += (chr(0x31 + i) + '\u20E3' + '\n')
                missions += f'{mission}\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Mission', value=missions)
        embed.add_field(name='_ _', value='_ _')
        if marker > -1:
            embed.set_footer(text='Press a number to load a new mission or 🔄 to reload the current one.')
        else:
            embed.set_footer(text='Press a number to delete this mission.')
        return embed

    @commands.command(description='Lists the current configured missions', aliases=['load'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def list(self, ctx, num: Optional[int] = None):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        if server['status'] in [Status.RUNNING, Status.PAUSED]:
            data = await self.bot.sendtoDCSSync(server, {"command": "listMissions", "channel": ctx.message.id})
            missions = data['missionList']
            if len(missions) == 0:
                await ctx.send('No missions registered with this server, please add one.')
            if not num:
                num = await utils.selection_list(self, ctx, missions, self.format_mission_list, 5, data['listStartIndex'], '🔄')
            if num >= 0:
                mission = missions[num]
                mission = mission[(mission.rfind('\\') + 1):-4]
                # make sure that the Scheduler doesn't interfere
                server['restart_pending'] = True
                self.bot.sendtoDCS(server, {"command": "startMission", "id": num + 1, "channel": ctx.channel.id})
                await ctx.send(f'Loading mission "{mission}" ...')
        else:
            return await ctx.send(f"Server {server['server_name']} is {server['status'].name}.")

    @staticmethod
    def format_file_list(data, marker, marker_emoji):
        embed = discord.Embed(title='Available Missions', color=discord.Color.blue())
        ids = missions = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            missions += data[i][:-4] + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Mission', value=missions)
        embed.add_field(name='_ _', value='_ _')
        embed.set_footer(text='Press a number to add the selected mission to the list.')
        return embed

    @commands.command(description='Adds a mission to the list', usage='[path]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def add(self, ctx, *path):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        if server['status'] in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            if len(path) == 0:
                data = await self.bot.sendtoDCSSync(server, {"command": "listMissions", "channel": ctx.message.id})
                installed = [mission[(mission.rfind('\\') + 1):] for mission in data['missionList']]
                data = await self.bot.sendtoDCSSync(server, {"command": "listMizFiles", "channel": ctx.channel.id})
                available = data['missions']
                files = list(set(available) - set(installed))
                if len(files) == 0:
                    await ctx.send('No (new) mission found to add.')
                    return
                n = await utils.selection_list(self, ctx, files, self.format_file_list)
                if n >= 0:
                    file = files[n]
                else:
                    return
            else:
                file = ' '.join(path)
            if file is not None:
                self.bot.sendtoDCS(server, {"command": "addMission", "path": file, "channel": ctx.channel.id})
                await ctx.send(f'Mission "{file[:-4]}" added.')
            else:
                await ctx.send('There is no file in the Missions directory of server {}.'.format(server['server_name']))
        else:
            return await ctx.send('Server ' + server['server_name'] + ' is not running.')

    @commands.command(description='Deletes a mission from the list', aliases=['del'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def delete(self, ctx):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        if server['status'] in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            data = await self.bot.sendtoDCSSync(server, {"command": "listMissions", "channel": ctx.message.id})
            missions = data['missionList']
            n = await utils.selection_list(self, ctx, missions, self.format_mission_list, 5, data['listStartIndex'], '❌')
            if n == (data['listStartIndex'] - 1):
                await ctx.send('The running mission can\'t be deleted.')
            elif n >= 0:
                mission = missions[n]
                mission = mission[(mission.rfind('\\') + 1):-4]
                self.bot.sendtoDCS(server, {"command": "deleteMission", "id": n + 1, "channel": ctx.channel.id})
                await ctx.send(f'Mission "{mission}" deleted.')
        else:
            return await ctx.send('Server ' + server['server_name'] + ' is not running.')

    @commands.command(description='Pauses the current running mission')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def pause(self, ctx):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        if server['status'] == Status.RUNNING:
            self.bot.sendtoDCS(server, {"command": "pauseMission", "channel": ctx.channel.id})
            await ctx.send('Server "{}" paused.'.format(server['server_name']))
        else:
            await ctx.send('Server "{}" is not running.'.format(server['server_name']))

    @commands.command(description='Unpauses the current running mission')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unpause(self, ctx):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        if server['status'] == Status.PAUSED:
            self.bot.sendtoDCS(server, {"command": "unpauseMission", "channel": ctx.channel.id})
            await ctx.send('Server "{}" unpaused.'.format(server['server_name']))
        elif server['status'] == Status.RUNNING:
            await ctx.send('Server "{}" is already running.'.format(server['server_name']))
        elif server['status'] == Status.LOADING:
            await ctx.send('Server "{}" is still loading... please wait a bit and try again.'.format(server['server_name']))
        else:
            await ctx.send('Server "{}" is stopped or shut down. Please start the server first before unpausing.'.format(server['server_name']))

    @tasks.loop(minutes=1.0)
    async def update_mission_status(self):
        for server_name, server in self.globals.items():
            if server['status'] in [Status.UNREGISTERED, Status.SHUTDOWN]:
                continue
            elif server['status'] in [Status.LOADING, Status.STOPPED]:
                if 'PID' in server and not psutil.pid_exists(server['PID']):
                    server['status'] = Status.SHUTDOWN
                continue
            try:
                data = await self.bot.sendtoDCSSync(server, {
                    "command": "getMissionUpdate"
                })
                # remove any hung flag, if the server has responded
                if 'hung' in server:
                    del server['hung']
                server['status'] = Status.PAUSED if data['pause'] is True else Status.RUNNING
                server['mission_time'] = data['mission_time']
                server['real_time'] = data['real_time']
                data['channel'] = server['status_channel']
                await self.eventlistener.displayMissionEmbed(data)
            except asyncio.TimeoutError:
                # check if the server process is still existent
                if 'PID' not in server or psutil.pid_exists(server['PID']):
                    self.log.warning(f"Server \"{server['server_name']}\" is not responding.")
                    # process might be in a hung state, so try again for a specified amount of times
                    if 'hung' in server and server['hung'] >= (MAX_HUNG_MINUTES - 1):
                        if 'PID' in server:
                            self.log.warning(f"Killing server \"{server['server_name']}\" after {MAX_HUNG_MINUTES} retries")
                            psutil.Process(server['PID']).kill()
                        else:
                            self.log.warning(f"Server \"{server['server_name']}\" considered dead after {MAX_HUNG_MINUTES} retries")
                        del server['hung']
                        server['status'] = Status.SHUTDOWN
                    elif 'hung' not in server:
                        server['hung'] = 1
                    else:
                        server['hung'] += 1
                else:
                    self.log.warning(f"Server \"{server['server_name']}\" died. Setting state to SHUTDOWN.")
                    server['status'] = Status.SHUTDOWN

    @update_mission_status.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5.0)
    async def update_channel_name(self):
        for server_name, server in self.globals.items():
            if server['status'] == Status.UNREGISTERED:
                continue
            channel = self.bot.get_channel(int(server['status_channel']))
            # name changes of the status channel will only happen with the correct permission
            if channel.permissions_for(self.bot.member).manage_channels:
                name = channel.name
                # if the server owner leaves, the server is shut down
                if server['status'] in [Status.STOPPED, Status.SHUTDOWN, Status.LOADING]:
                    if name.find('［') == -1:
                        name = name + '［-］'
                    else:
                        name = re.sub('［.*］', f'［-］', name)
                elif server_name in self.bot.player_data:
                    players = self.bot.player_data[server_name]
                    players = players[players['active'] == True]
                    current = len(players) + 1
                    max_players = server['serverSettings']['maxPlayers']
                    if name.find('［') == -1:
                        name = name + f'［{current}／{max_players}］'
                    else:
                        name = re.sub('［.*］', f'［{current}／{max_players}］', name)
                await channel.edit(name=name)


def setup(bot: DCSServerBot):
    bot.add_cog(Mission(bot, MissionEventListener))
