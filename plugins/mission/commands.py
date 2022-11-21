# noinspection PyPackageRequirements
import aiohttp
import asyncio
import discord
import os
import psycopg2
import re
import shutil
from contextlib import closing
from core import utils, DCSServerBot, Plugin, Report, Status, Server, Coalition, Channel
from datetime import datetime
from discord import SelectOption, Interaction
from discord.ext import commands, tasks
from discord.ui import Select, View, Button
from os import path
from typing import Optional, cast
from .listener import MissionEventListener


class Mission(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.hung = dict[str, int]()
        self.update_mission_status.start()
        self.update_channel_name.start()

    async def cog_unload(self):
        self.update_channel_name.cancel()
        self.update_mission_status.cancel()
        await super().cog_unload()

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

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None):
        self.log.debug('Pruning Mission ...')
        with closing(conn.cursor()) as cursor:
            if days > 0:
                cursor.execute(f"DELETE FROM missions WHERE mission_end < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Mission pruned.')

    @commands.command(description='Shows the active DCS mission')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def mission(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if server.get_channel(Channel.STATUS).id != ctx.channel.id:
            if server.status in [Status.RUNNING, Status.PAUSED]:
                players = server.get_active_players()
                num_players = len(players) + 1
                report = Report(self.bot, self.plugin_name, 'serverStatus.json')
                env = await report.render(server=server, num_players=num_players)
                await ctx.send(embed=env.embed)
            else:
                await ctx.send(f'There is no mission running on server {server.name}')
                return
        else:
            server.sendtoDCS({"command": "getMissionUpdate", "channel": ctx.channel.id})

    @staticmethod
    def format_briefing_list(data: list[Server], marker, marker_emoji):
        embed = discord.Embed(title='Briefing', color=discord.Color.blue())
        embed.description = 'Select the server you want to get a briefing for:'
        ids = servers = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            servers += data[i].name + '\n'
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

        server: Server = await self.bot.get_server(ctx)
        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
        if not server:
            servers: list[Server] = list()
            for server_name, server in self.bot.servers.items():
                if server.status in [Status.RUNNING, Status.PAUSED]:
                    servers.append(server)
            if len(servers) == 0:
                await ctx.send('No running mission found.', delete_after=timeout if timeout > 0 else None)
                return
            else:
                server_name = await utils.selection(ctx, placeholder='Select the server you want to get a briefing for',
                                                    options=[SelectOption(label=x.name) for x in servers])
                if not server_name:
                    return
                server = self.bot.servers[server_name]
        elif server.status not in [Status.RUNNING, Status.PAUSED]:
            await ctx.send('No running mission found.', delete_after=timeout if timeout > 0 else None)
            return
        mission_info = await server.sendtoDCSSync({
            "command": "getMissionDetails",
            "channel": ctx.message.id
        })
        mission_info['passwords'] = read_passwords(server.name)
        report = Report(self.bot, self.plugin_name, 'briefing.json')
        env = await report.render(mission_info=mission_info, server_name=server.name, message=ctx.message)
        await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @commands.command(description='Shows information of a specific airport', aliases=['weather'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def atis(self, ctx, *args):
        name = ' '.join(args)
        for server_name, server in self.bot.servers.items():
            if server.status not in [Status.RUNNING, Status.PAUSED]:
                continue
            for airbase in server.current_mission.airbases:
                if (name.casefold() in airbase['name'].casefold()) or (name.upper() == airbase['code']):
                    data = await server.sendtoDCSSync({
                        "command": "getWeatherInfo",
                        "x": airbase['position']['x'],
                        "y": airbase['position']['y'],
                        "z": airbase['position']['z']
                    })
                    report = Report(self.bot, self.plugin_name, 'atis.json')
                    env = await report.render(airbase=airbase, data=data)
                    timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
                    await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)
                    break

    @commands.command(description='List the current players on this server')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def players(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            await ctx.send('Server ' + server.name + ' is not running.',
                           delete_after=timeout if timeout > 0 else None)
            return
        report = Report(self.bot, self.plugin_name, 'players.json')
        env = await report.render(server=server, sides=utils.get_sides(ctx.message, server))
        await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @commands.command(description='Restarts the current active mission', usage='[delay] [message]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def restart(self, ctx, delay: Optional[int] = 120, *args):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if server.restart_pending and not await utils.yn_question(ctx, 'A restart is currently pending.\n'
                                                                       'Would you still like to restart the mission?'):
            return
        else:
            server.on_empty = dict()
        if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
            if server.is_populated():
                result = await utils.populated_question(ctx, "Do you really want to restart the mission?")
                if not result:
                    await ctx.send('Aborted.')
                    return
                elif result == 'later':
                    server.on_empty = {"command": "restart", "user": ctx.message.author}
                    server.restart_pending = True
                    await ctx.send('Restart postponed when server is empty.')
                    return

            server.restart_pending = True
            if server.is_populated():
                if delay > 0:
                    message = f'!!! Server will be restarted in {utils.format_time(delay)}!!!'
                else:
                    message = '!!! Server will be restarted NOW !!!'
                # have we got a message to present to the users?
                if len(args):
                    message += ' Reason: {}'.format(' '.join(args))

                if server.get_channel(Channel.STATUS).id == ctx.channel.id:
                    await ctx.message.delete()
                msg = await ctx.send(f'Restarting mission in {utils.format_time(delay)} (warning users before)...')
                server.sendPopupMessage(Coalition.ALL, message, sender=ctx.message.author.display_name)
                await asyncio.sleep(delay)
                await msg.delete()

            msg = await ctx.send('Mission will restart now, please wait ...')
            await server.current_mission.restart()
            await self.bot.audit("restarted mission", server=server, user=ctx.message.author)
            await msg.delete()
            msg = await ctx.send('Mission restarted.')
        else:
            msg = await ctx.send('There is currently no mission running on server "' + server.name + '"')
        if (msg is not None) and (server.get_channel(Channel.STATUS).id == ctx.channel.id):
            await asyncio.sleep(5)
            await msg.delete()

    class LoadView(View):
        def __init__(self, ctx, *, placeholder: str, options: list):
            super().__init__()
            self.ctx = ctx
            select: Select = cast(Select, self.children[0])
            select.placeholder = placeholder
            select.options = options
            self.result = None

        @discord.ui.select()
        async def callback(self, interaction: Interaction, select: Select):
            self.result = select.values[0]
            self.clear_items()
            await interaction.response.edit_message(view=self)
            self.stop()

        @discord.ui.button(label='Restart', style=discord.ButtonStyle.primary, emoji='🔁')
        async def restart(self, interaction: Interaction, button: Button):
            self.result = "restart"
            await interaction.response.defer()
            self.stop()

        @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary, emoji='❌')
        async def cancel(self, interaction: Interaction, button: Button):
            await interaction.response.defer()
            self.stop()

        async def interaction_check(self, interaction: Interaction, /) -> bool:
            if interaction.user != self.ctx.author:
                await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
                return False
            else:
                return True

    @commands.command(description='(Re-)Loads a mission', aliases=['list'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def load(self, ctx: commands.Context):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            return await ctx.send(f"Server {server.name} is {server.status.name}.")

        if server.restart_pending and not await utils.yn_question(ctx, 'A restart is currently pending.\n'
                                                                       'Would you still like to change the mission?'):
            return
        else:
            server.on_empty = dict()

        question = f"Do you really want to change the mission?"
        if server.is_populated():
            result = await utils.populated_question(ctx, question)
            if not result:
                await ctx.send('Aborted.')
                return
        else:
            result = None

        data = await server.sendtoDCSSync({"command": "listMissions"})
        missions = data['missionList']
        if len(missions) == 0:
            await ctx.send(f'No missions registered with this server, please "{ctx.prefix}add" one.')
            return

        embed = discord.Embed(title=f"{server.name}", colour=discord.Colour.blue())
        embed.description = "Load / reload missions."
        embed.add_field(name="Mission Name", value=server.current_mission.name)
        embed.add_field(name="# Players", value=str(len(server.get_active_players())))
        embed.add_field(name='▬' * 27, value='_ _', inline=False)
        view = self.LoadView(ctx, placeholder="Select a mission to load",
                             options=[SelectOption(label=os.path.basename(x)[:-4]) for x in missions[:25]])
        msg = await ctx.send(embed=embed, view=view)
        try:
            if await view.wait():
                return
            elif not view.result:
                await ctx.send('Aborted.')
                return
            msg = await msg.edit(suppress=True)
            name = view.result
            if name == "restart":
                if result == 'later':
                    server.on_empty = {"command": "restart", "user": ctx.message.author}
                    server.restart_pending = True
                    await ctx.send(f'Mission {server.current_mission.name} will be restarted when server is empty.')
                else:
                    await server.current_mission.restart()
                    await ctx.send(f'Mission {server.current_mission.name} restarted.')
            else:
                for mission in missions:
                    if name == os.path.basename(mission)[:-4]:
                        if result == 'later':
                            server.on_empty = {"command": "load", "id": missions.index(mission) + 1,
                                               "user": ctx.message.author}
                            server.restart_pending = True
                            await ctx.send(
                                f'Mission {name} will be loaded when server is empty.')
                        else:
                            tmp = await ctx.send(f'Loading mission {name} ...')
                            await server.loadMission(missions.index(mission) + 1)
                            await self.bot.audit("loaded mission", server=server, user=ctx.message.author)
                            await tmp.delete()
                            await ctx.send(f'Mission {name} loaded.')
                        break
        finally:
            await ctx.message.delete()
            await msg.delete()

    @commands.command(description='Adds a mission to the list', usage='[path]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def add(self, ctx, *filename):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            if len(filename) == 0:
                data = await server.sendtoDCSSync({"command": "listMissions"})
                installed = [mission[(mission.rfind('\\') + 1):] for mission in data['missionList']]
                data = await server.sendtoDCSSync({"command": "listMizFiles"})
                available = data['missions']
                files: list = list(set(available) - set(installed))
                if len(files) == 0:
                    await ctx.send('No (new) mission found to add.')
                    return
                file = await utils.selection(ctx, placeholder="Select a file to be added to the mission list.",
                                             options=[SelectOption(label=x[:-4], value=x) for x in files[:25]])
                if not file:
                    return
            else:
                file = os.path.normpath(' '.join(filename))
            if file is not None:
                if '\\' in file and not os.path.exists(file):
                    await ctx.send(f'The file {file} does not exists. Aborting.')
                    return
                server.sendtoDCS({"command": "addMission", "path": file})
                name = file[:-4]
                await ctx.send(f'Mission "{name}" added.')
                if await utils.yn_question(ctx, 'Do you want to load this mission?'):
                    data = await server.sendtoDCSSync({"command": "listMissions"})
                    missions = data['missionList']
                    for idx, mission in enumerate(missions):
                        if os.path.basename(mission) == file:
                            tmp = await ctx.send(f'Loading mission {name} ...')
                            await server.loadMission(idx + 1)
                            await self.bot.audit("loaded mission", server=server, user=ctx.message.author)
                            await tmp.delete()
                            await ctx.send(f'Mission {name} loaded.')
                            break
            else:
                await ctx.send('There is no file in the Missions directory of server {}.'.format(server.name))
        else:
            return await ctx.send('Server ' + server.name + ' is not running.')

    @commands.command(description='Deletes a mission from the list', aliases=['del'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def delete(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            data = await server.sendtoDCSSync({"command": "listMissions"})
            original: list[str] = data['missionList']
            missions = original.copy()
            # remove the active mission as we can't delete it
            missions.pop(data['listStartIndex'] - 1)
            if not missions:
                await ctx.send("You can't delete the (only) running mission.")
                return

            name = await utils.selection(ctx,
                                         placeholder="Select the mission to delete",
                                         options=[SelectOption(label=x[(x.rfind('\\') + 1):-4]) for x in missions[:25]])
            if not name:
                return

            for mission in missions:
                if name in mission:
                    if await utils.yn_question(ctx, f'Delete mission "{name}" from the mission list?'):
                        server.sendtoDCS({"command": "deleteMission", "id": original.index(mission) + 1})
                        await ctx.send(f'Mission "{name}" removed from list.')
                        if await utils.yn_question(ctx, f'Delete mission "{name}" also from disk?'):
                            os.remove(mission)
                            await ctx.send(f'Mission "{name}" deleted.')
                    break
        else:
            return await ctx.send('Server ' + server.name + ' is not running.')

    @commands.command(description='Pauses the current running mission')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def pause(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if server.status == Status.RUNNING:
            await server.current_mission.pause()
            await ctx.send('Server "{}" paused.'.format(server.name))
        else:
            await ctx.send('Server "{}" is not running.'.format(server.name))

    @commands.command(description='Unpauses the current running mission')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unpause(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if server.status == Status.PAUSED:
            await server.current_mission.unpause()
            await ctx.send('Server "{}" unpaused.'.format(server.name))
        elif server.status == Status.RUNNING:
            await ctx.send('Server "{}" is already running.'.format(server.name))
        elif server.status == Status.LOADING:
            await ctx.send('Server "{}" is still loading... please wait a bit and try again.'.format(server.name))
        else:
            await ctx.send('Server "{}" is stopped or shut down. Please start the server first before unpausing.'.format(server.name))

    @tasks.loop(minutes=1.0)
    async def update_mission_status(self):
        async def warn_admins(s: Server, message: str) -> None:
            mentions = ''
            for role_name in [x.strip() for x in self.bot.config['ROLES']['DCS Admin'].split(',')]:
                role: discord.Role = discord.utils.get(self.bot.guilds[0].roles, name=role_name)
                if role:
                    mentions += role.mention
            logdir = os.path.expandvars("%USERPROFILE%\\Saved Games\\" + s.installation + "\\logs\\")
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            shutil.copy2(logdir + 'dcs.log', logdir + f"dcs.{timestamp}.log")
            await s.get_channel(Channel.ADMIN).send(mentions + ' ' + message +
                                                    f"\nLatest dcs.log can be pulled with "
                                                    f"{self.bot.config['BOT']['COMMAND_PREFIX']}download of "
                                                    f"dcs.{timestamp}.log\nIf the scheduler is configured for this "
                                                    f"server, it will relaunch it automatically.")

        for server_name, server in self.bot.servers.items():
            if server.status in [Status.UNREGISTERED, Status.SHUTDOWN]:
                continue
            elif server.status in [Status.LOADING, Status.STOPPED]:
                if server.process and not server.process.is_running():
                    server.status = Status.SHUTDOWN
                    server.process = None
                continue
            try:
                # we set a longer timeout in here because, we don't want to risk false restarts
                timeout = 20 if self.bot.config.getboolean('BOT', 'SLOW_SYSTEM') else 10
                data = await server.sendtoDCSSync({"command": "getMissionUpdate"}, timeout)
                # remove any hung flag, if the server has responded
                if server.name in self.hung:
                    del self.hung[server.name]
                if data['pause'] and server.status != Status.PAUSED:
                    server.status = Status.PAUSED
                elif not data['pause'] and server.status != Status.RUNNING:
                    server.status = Status.RUNNING
                server.current_mission.mission_time = data['mission_time']
                server.current_mission.real_time = data['real_time']
                data['channel'] = server.get_channel(Channel.STATUS).id
                self.eventlistener._display_mission_embed(server)
            except asyncio.TimeoutError:
                # check if the server process is still existent
                max_hung_minutes = int(self.bot.config['DCS']['MAX_HUNG_MINUTES'])
                if max_hung_minutes > 0 and (server.process and server.process.is_running()):
                    self.log.warning(f"Server \"{server.name}\" is not responding.")
                    # process might be in a hung state, so try again for a specified amount of times
                    if server.name in self.hung and self.hung[server.name] >= (max_hung_minutes - 1):
                        if server.process:
                            message = f"Can't reach server \"{server.name}\" for more than {max_hung_minutes} minutes. Killing ..."
                            self.log.warning(message)
                            server.process.kill()
                            server.process = None
                            await self.bot.audit("Server killed due to a hung state.", server=server)
                        else:
                            message = f"Server \"{server.name}\" died. Setting state to SHUTDOWN."
                            self.log.warning(message)
                            await self.bot.audit("Server set to SHUTDOWN due to a hung state.", server=server)
                        del self.hung[server.name]
                        server.status = Status.SHUTDOWN
                        await warn_admins(server, message)
                    elif server.name not in self.hung:
                        self.hung[server.name] = 1
                    else:
                        self.hung[server.name] += 1
                else:
                    message = f"Server \"{server.name}\" died. Setting state to SHUTDOWN."
                    self.log.warning(message)
                    server.status = Status.SHUTDOWN
                    await warn_admins(server, message)
            except Exception as ex:
                self.log.debug("Exception in update_mission_status(): " + str(ex))

    @update_mission_status.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5.0)
    async def update_channel_name(self):
        for server_name, server in self.bot.servers.items():
            if server.status == Status.UNREGISTERED:
                continue
            channel = server.get_channel(Channel.STATUS)
            # name changes of the status channel will only happen with the correct permission
            if channel.permissions_for(self.bot.member).manage_channels:
                name = channel.name
                # if the server owner leaves, the server is shut down
                if server.status in [Status.STOPPED, Status.SHUTDOWN, Status.LOADING]:
                    if name.find('［') == -1:
                        name = name + '［-］'
                    else:
                        name = re.sub('［.*］', f'［-］', name)
                else:
                    players = server.get_active_players()
                    current = len(players) + 1
                    max_players = server.settings['maxPlayers']
                    if name.find('［') == -1:
                        name = name + f'［{current}／{max_players}］'
                    else:
                        name = re.sub('［.*］', f'［{current}／{max_players}］', name)
                try:
                    if name != channel.name:
                        await channel.edit(name=name)
                except Exception as ex:
                    self.log.debug("Exception in update_channel_name(): " + str(ex))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages or messages that does not contain miz attachments
        if message.author.bot or not message.attachments or not message.attachments[0].filename.endswith('.miz'):
            return
        server: Server = await self.bot.get_server(message)
        # only DCS Admin role is allowed to upload missions in the servers admin channel
        if not server or not utils.check_roles([x.strip() for x in self.bot.config['ROLES']['DCS Admin'].split(',')], message.author):
            return
        att = message.attachments[0]
        filename = path.expandvars(self.bot.config[server.installation]['DCS_HOME']) + '\\Missions\\' + att.filename
        try:
            ctx = utils.ContextWrapper(message)
            stopped = False
            exists = False
            if path.exists(filename):
                exists = True
                if await utils.yn_question(ctx, 'File exists. Do you want to overwrite it?') is False:
                    await message.channel.send('Upload aborted.')
                    return
                if server.status in [Status.RUNNING, Status.PAUSED] and \
                        path.normpath(server.current_mission.filename) == path.normpath(filename):
                    if await utils.yn_question(ctx, 'Mission is currently active.\nDo you want me to stop the DCS '
                                                    'Server to replace it?') is True:
                        await server.stop()
                        stopped = True
                    else:
                        await message.channel.send('Upload aborted.')
                        return
            async with aiohttp.ClientSession() as session:
                async with session.get(att.url) as response:
                    if response.status == 200:
                        with open(filename, 'wb') as outfile:
                            outfile.write(await response.read())
                    else:
                        await message.channel.send(f'Error {response.status} while reading MIZ file!')
            if not exists:
                server.sendtoDCS({"command": "addMission", "path": os.path.basename(filename)})
            name = os.path.basename(filename)[:-4]
            await message.channel.send(f"Mission {name} uploaded and added." if not exists else f"Mission {name} replaced.")
            await self.bot.audit(f"uploaded mission {name}", server=server, user=message.author)
            if stopped:
                await server.start()
            elif await utils.yn_question(ctx, 'Do you want to load this mission?'):
                data = await server.sendtoDCSSync({"command": "listMissions"})
                missions = data['missionList']
                for idx, mission in enumerate(missions):
                    if os.path.basename(mission) == os.path.basename(filename):
                        tmp = await message.channel.send(f'Loading mission {name} ...')
                        await server.loadMission(idx + 1)
                        await self.bot.audit("loaded mission", server=server, user=message.author)
                        await tmp.delete()
                        await message.channel.send(f'Mission {name} loaded.')
                        break
        except Exception as ex:
            self.log.exception(ex)
        finally:
            await message.delete()


async def setup(bot: DCSServerBot):
    await bot.add_cog(Mission(bot, MissionEventListener))
