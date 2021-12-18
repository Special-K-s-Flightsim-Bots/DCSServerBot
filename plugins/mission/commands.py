# agent.py
import asyncio
import discord
import itertools
import re
import typing
from core import utils, const, Plugin
from discord.ext import commands, tasks


class Mission(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.update_mission_status.start()

    def __unload__(self):
        self.update_mission_status.cancel()
        super().__unload__()

    @commands.command(description='Send a chat message to a running DCS instance', usage='<message>', hidden=True)
    @utils.has_role('DCS')
    @commands.guild_only()
    async def chat(self, ctx, *args):
        server = await utils.get_server(self, ctx)
        if server:
            self.bot.sendtoDCS(server, {
                "command": "sendChatMessage",
                "channel": ctx.channel.id,
                "message": ' '.join(args),
                "from": ctx.message.author.display_name
            })

    @commands.command(description='Sends a popup to a coalition', usage='<coalition> <message>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def popup(self, ctx, to, *args):
        server = await utils.get_server(self, ctx)
        if server:
            if to not in ['all', 'red', 'blue']:
                await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}popup all|red|blue <message>")
            elif server['status'] == 'Running':
                self.bot.sendtoDCS(server, {
                    "command": "sendPopupMessage",
                    "channel": ctx.channel.id,
                    "message": ' '.join(args),
                    "from": ctx.message.author.display_name, "to": to.lower()
                })
                await ctx.send('Message sent.')
            else:
                await ctx.send(f"Mission is {server['status'].lower()}, message discarded.")

    @commands.command(description='Shows the active DCS mission', hidden=True)
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def mission(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if int(server['status_channel']) != ctx.channel.id:
                if server['status'] in ['Running', 'Paused']:
                    mission = await self.bot.sendtoDCSSync(server, {"command": "getRunningMission", "channel": 0})
                    await ctx.send(embed=utils.format_mission_embed(self, mission))
                else:
                    return await ctx.send('Server ' + server['server_name'] + ' is not running.')
            else:
                await ctx.message.delete()
                self.bot.sendtoDCS(server, {"command": "getRunningMission", "channel": ctx.channel.id})

    @commands.command(description='Shows briefing of the active DCS mission', aliases=['brief'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def briefing(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] in ['Running', 'Paused']:
                data = await self.bot.sendtoDCSSync(server, {"command": "getMissionDetails", "channel": ctx.message.id})
                embed = discord.Embed(title=data['current_mission'], color=discord.Color.blue())
                embed.description = data['mission_description'][:2048]
                await ctx.send(embed=embed)
            else:
                await ctx.send('There is currently no mission running on server "' + server['server_name'] + '"')

    @commands.command(description='Shows information of a specific airport', aliases=['weather', 'airport', 'airfield', 'ap'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def atis(self, ctx, *args):
        name = ' '.join(args)
        for server_name, server in self.bot.DCSServers.items():
            if server['status'] in ['Running', 'Paused']:
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
                        d, m, s, f = utils.DDtoDMS(airbase['lat'])
                        lat = ('N' if d > 0 else 'S') + '{:02d}°{:02d}\'{:02d}"'.format(int(abs(d)), int(m), int(s))
                        d, m, s, f = utils.DDtoDMS(airbase['lng'])
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
                        embed.add_field(name='Active Runways', value='\n'.join(utils.getActiveRunways(
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
                        await ctx.send(embed=embed)
                        break

    @commands.command(description='List the current players on this server', hidden=True)
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def players(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if int(server['status_channel']) != ctx.channel.id:
                await ctx.send('This command can only be used in the status channel.')
            else:
                await ctx.message.delete()
                self.bot.sendtoDCS(server, {"command": "getCurrentPlayers", "channel": ctx.channel.id})

    @commands.command(description='Restarts the current active mission', usage='[delay] [message]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def restart(self, ctx, delay: typing.Optional[int] = 120, *args):
        server = await utils.get_server(self, ctx)
        if server:
            msg = None
            if server['status'] not in ['Stopped', 'Shutdown']:
                if server['status'] == 'Running':
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
                        "from": ctx.message.author.display_name, "to": "all"
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

    @commands.command(description='Starts a mission by ID', usage='<ID>', aliases=['load'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def start(self, ctx, id):
        server = await utils.get_server(self, ctx)
        if server:
            self.bot.sendtoDCS(server, {"command": "startMission", "id": id, "channel": ctx.channel.id})
            await ctx.send(f'Loading mission {id}.')

    @staticmethod
    def format_mission_list(data, marker):
        embed = discord.Embed(title='Mission List', color=discord.Color.blue())
        ids = active = missions = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            active += 'Yes\n' if marker == (i + 1) else '_ _\n'
            mission = data[i]
            missions += mission[(mission.rfind('\\') + 1):] + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Active', value=active)
        embed.add_field(name='Mission', value=missions)
        embed.set_footer(text='Press a number to load the selected mission.')
        return embed

    @commands.command(description='Lists the current configured missions')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def list(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] in ['Running', 'Paused']:
                data = await self.bot.sendtoDCSSync(server, {"command": "listMissions", "channel": ctx.message.id})
                missions = data['missionList']
                n = await utils.selection_list(self, ctx, missions, self.format_mission_list, 5, data['listStartIndex'])
                if n >= 0:
                    await self.start(ctx, n + 1)
            else:
                return await ctx.send('Server ' + server['server_name'] + ' is not running.')

    @staticmethod
    def format_file_list(data, marker):
        embed = discord.Embed(title='Available Missions', color=discord.Color.blue())
        ids = missions = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            missions += data[i] + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Mission', value=missions)
        embed.add_field(name='_ _', value='_ _')
        embed.set_footer(text='Press a number to add the selected mission to the list.')
        return embed

    @commands.command(description='Adds a mission to the list', usage='<path>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def add(self, ctx, *path):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] in ['Running', 'Paused']:
                if len(path) == 0:
                    data = await self.bot.sendtoDCSSync(server, {"command": "listMizFiles", "channel": ctx.channel.id})
                    files = data['missions']
                    n = await utils.selection_list(self, ctx, files, self.format_file_list)
                    if n >= 0:
                        file = files[n]
                    else:
                        return
                else:
                    file = ' '.join(path)
                if file is not None:
                    self.bot.sendtoDCS(server, {"command": "addMission", "path": file, "channel": ctx.channel.id})
                    await ctx.send('Mission {} added.'.format(file))
                else:
                    await ctx.send('There is no file in the Missions directory of server {}.'.format(server['server_name']))
            else:
                return await ctx.send('Server ' + server['server_name'] + ' is not running.')

    @commands.command(description='Deletes a mission from the list', usage='<ID>', aliases=['del'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def delete(self, ctx, id):
        server = await utils.get_server(self, ctx)
        if server:
            self.bot.sendtoDCS(server, {"command": "deleteMission", "id": id, "channel": ctx.channel.id})
            await ctx.send('Mission {} deleted.'.format(id))

    @commands.command(description='Pauses the current running mission')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def pause(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] == 'Running':
                self.bot.sendtoDCS(server, {"command": "pause", "channel": ctx.channel.id})
                await ctx.send('Server "{}" paused.'.format(server['server_name']))
            else:
                await ctx.send('Server "{}" is not running.'.format(server['server_name']))

    @commands.command(description='Unpauses the current running mission')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unpause(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] == 'Paused':
                self.bot.sendtoDCS(server, {"command": "unpause", "channel": ctx.channel.id})
                await ctx.send('Server "{}" unpaused.'.format(server['server_name']))
            elif server['status'] == 'Running':
                await ctx.send('Server "{}" is already running.'.format(server['server_name']))
            elif server['status'] == 'Loading':
                await ctx.send('Server "{}" is still loading... please wait a bit and try again.'.format(server['server_name']))
            else:
                await ctx.send('Server "{}" is stopped or shut down. Please start the server first before unpausing.'.format(server['server_name']))

    @tasks.loop(minutes=5.0)
    async def update_mission_status(self):
        for server_name, server in self.bot.DCSServers.items():
            if server['status'] == 'Running':
                self.bot.sendtoDCS(server, {
                    "command": "getRunningMission",
                    "channel": server['status_channel']
                })
