import aiohttp
import asyncio
import discord
import json
import os
import platform
import psycopg2
import psycopg2.extras
import shlex
import shutil
import subprocess
from contextlib import closing
from core import utils, DCSServerBot, Plugin, Player, Status, Server, Coalition
from discord import Interaction, SelectOption
from discord.ext import commands, tasks
from discord.ui import Select, View, Button, Modal, TextInput
from pathlib import Path
from typing import Union, List, Optional
from zipfile import ZipFile
from .listener import AdminEventListener


STATUS_EMOJI = {
    Status.LOADING: '🔄',
    Status.PAUSED: '⏸️',
    Status.RUNNING: '▶️',
    Status.STOPPED: '⏹️'
}


class Agent(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.update_pending = False
        if self.bot.config.getboolean('DCS', 'AUTOUPDATE') is True:
            self.check_for_dcs_update.start()

    async def cog_unload(self):
        if self.bot.config.getboolean('DCS', 'AUTOUPDATE') is True:
            self.check_for_dcs_update.cancel()
        await super().cog_unload()

    async def do_update(self, warn_times: List[int], ctx=None):
        async def shutdown_with_warning(server: Server):
            if server.is_populated():
                shutdown_in = max(warn_times) if len(warn_times) else 0
                while shutdown_in > 0:
                    for warn_time in warn_times:
                        if warn_time == shutdown_in:
                            server.sendPopupMessage(Coalition.ALL, f'Server is going down for a DCS update in '
                                                                   f'{utils.format_time(warn_time)}!')
                    await asyncio.sleep(1)
                    shutdown_in -= 1
            await server.shutdown()

        self.update_pending = True
        if ctx:
            await ctx.send('Shutting down DCS servers, warning users before ...')
        else:
            self.log.info('Shutting down DCS servers, warning users before ...')
        servers = []
        tasks = []
        for server_name, server in self.bot.servers.items():
            if server.status in [Status.UNREGISTERED, Status.SHUTDOWN]:
                continue
            if server.maintenance:
                servers.append(server)
            else:
                server.maintenance = True
                tasks.append(asyncio.create_task(shutdown_with_warning(server)))
        # wait for DCS servers to shut down
        if len(tasks):
            await asyncio.gather(*tasks)
        if ctx:
            await ctx.send('Updating DCS World. Please wait, this might take some time ...')
        else:
            self.log.info('Updating DCS World ...')
        for plugin in self.bot.cogs.values():  # type: Plugin
            await plugin.before_dcs_update()
        # disable any popup on the remote machine
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
        startupinfo.wShowWindow = subprocess.SW_HIDE
        subprocess.run(['dcs_updater.exe', '--quiet', 'update'], executable=os.path.expandvars(
            self.bot.config['DCS']['DCS_INSTALLATION']) + '\\bin\\dcs_updater.exe', startupinfo=startupinfo)
        utils.desanitize(self)
        # run after_dcs_update() in all plugins
        for plugin in self.bot.cogs.values():  # type: Plugin
            await plugin.after_dcs_update()
        message = None
        if ctx:
            await ctx.send('DCS World updated to the latest version.')
            message = await ctx.send('Starting up DCS servers again ...')
        else:
            self.log.info('DCS World updated to the latest version.\nStarting up DCS servers again ...')
        for server in self.bot.servers.values():
            if server not in servers:
                # let the scheduler do its job
                server.maintenance = False
            else:
                try:
                    # the server was running before (being in maintenance mode), so start it again
                    await server.startup()
                except asyncio.TimeoutError:
                    await ctx.send(f'Timeout while starting {server.display_name}, please check it manually!')
        self.update_pending = False
        if message:
            await message.delete()
            await ctx.send('DCS servers started (or Scheduler taking over in a bit).')

    @commands.command(description='Update a DCS Installation')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def update(self, ctx, param: Optional[str] = None):
        if self.update_pending:
            await ctx.send('An update is already running, please wait ...')
            return
        # check versions
        branch, old_version = utils.getInstalledVersion(self.bot.config['DCS']['DCS_INSTALLATION'])
        new_version = await utils.getLatestVersion(branch)
        if old_version == new_version:
            await ctx.send('Your installed version {} is the latest on branch {}.'.format(old_version, branch))
        elif new_version or param and param == '-force':
            if await utils.yn_question(ctx, 'Would you like to update from version {} to {}?\nAll running DCS servers '
                                            'will be shut down!'.format(old_version, new_version)) is True:
                await self.bot.audit(f"started an update of all DCS servers on node {platform.node()}.",
                                     user=ctx.message.author)
                await self.do_update([120, 60], ctx)
        else:
            await ctx.send("Can't check the latest version on the DCS World website. Try again later.")

    @commands.command(description='Change the password of a DCS server', aliases=['passwd'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def password(self, ctx, coalition: Optional[str] = None):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if not coalition:
            if server.status in [Status.SHUTDOWN, Status.STOPPED]:
                password = await utils.input_value(self.bot, ctx, 'Please enter the new password (. for none):', True)
                server.settings['password'] = password if password else ''
                await self.bot.audit(f"changed password", user=ctx.message.author, server=server)
                await ctx.send('Password has been changed.')
            else:
                await ctx.send(f"Server \"{server.display_name}\" has to be stopped or shut down to change the password.")
        elif coalition.casefold() in ['red', 'blue']:
            if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
                password = await utils.input_value(self.bot, ctx, 'Please enter the new password (. for none):', True)
                server.sendtoDCS({
                    "command": "setCoalitionPassword",
                    ("redPassword" if coalition.casefold() == 'red' else "bluePassword"): password or ''
                })
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('UPDATE servers SET {} = %s WHERE server_name = %s'.format('blue_password' if coalition.casefold() == 'blue' else 'red_password'), (password, server.name))
                        conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)
                await self.bot.audit(f"changed password for coalition {coalition}",
                                     user=ctx.message.author, server=server)
                if server.status != Status.STOPPED and \
                        await utils.yn_question(ctx, "Password has been changed.\nDo you want the servers to be "
                                                     "restarted for the change to take effect?"):
                    await server.restart()
                    await ctx.send('Server restarted.')
                    await self.bot.audit('restarted the server', server=server, user=ctx.message.author)
            else:
                await ctx.send(f"Server \"{server.display_name}\" must not be shut down to change coalition "
                               f"passwords.")
        else:
            await ctx.send(f"Usage: {ctx.prefix}password [red|blue]")

    @commands.command(description='Change the configuration of a DCS server', aliases=['conf'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def config(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if server.status in [Status.RUNNING, Status.PAUSED]:
            if await utils.yn_question(ctx, question='Server has to be stopped to change its configuration.\n'
                                                     'Do you want to stop it?'):
                await server.stop()
            else:
                await ctx.send('Aborted.')
                return

        class ConfigModal(Modal, title="Server Configuration"):
            name = TextInput(label="Name", default=server.name, max_length=80, required=True)
            description = TextInput(label="Description", style=discord.TextStyle.long,
                                    default=server.settings['description'], max_length=2000, required=False)
            password = TextInput(label="Password", placeholder="n/a", default=server.settings['password'],
                                 max_length=20, required=False)
            max_player = TextInput(label="Max Players", default=server.settings['maxPlayers'], max_length=3,
                                   required=True)

            async def on_submit(s, interaction: discord.Interaction):
                if s.name.value != server.name:
                    old_name = server.name
                    server.rename(new_name=s.name.value, update_settings=True)
                    self.bot.servers[s.name.value] = server
                    del self.bot.servers[old_name]
                server.settings['description'] = s.description.value
                server.settings['password'] = s.password.value
                server.settings['maxPlayers'] = int(s.max_player.value)
                await interaction.response.send_message(
                    f'Server configuration for server "{server.display_name}" updated.')

        class ConfigView(View):
            @discord.ui.button(label='Yes', style=discord.ButtonStyle.green, custom_id='cfg_yes')
            async def on_yes(self, interaction: Interaction, button: Button):
                modal = ConfigModal()
                await interaction.response.send_modal(modal)
                self.stop()

            @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red, custom_id='cfg_cancel')
            async def on_cancel(self, interaction: Interaction, button: Button):
                await interaction.response.send_message('Aborted.')
                self.stop()

            async def interaction_check(self, interaction: Interaction, /) -> bool:
                if interaction.user != ctx.author:
                    await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
                    return False
                else:
                    return True

        view = ConfigView()
        embed = discord.Embed(title=f'Do you want to change the configuration of server\n"{server.display_name}"?')
        msg = await ctx.send(embed=embed, view=view)
        await view.wait()
        await msg.delete()

    @commands.command(description='Kick a user by name', usage='<name>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def kick(self, ctx: commands.Context, *args) -> None:
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return

        name = ' '.join(args)
        # find that player
        if server.status != Status.RUNNING:
            await ctx.send(f'Server is {server.status.name.lower()}.')
            return
        players = [x for x in server.get_active_players() if name.casefold() in x.name.casefold()]
        if len(players) > 25:
            await ctx.send(f'Usage: {ctx.prefix}kick <user>')
            return
        elif len(players) == 0:
            await ctx.send(f"No player \"{name}\" found.")
            return

        class KickModal(Modal, title="Reason for kicking"):
            reason = TextInput(label="Reason", placeholder="n/a", max_length=80, required=True)

            def __init__(self, player: Player):
                super().__init__()
                self.player = player

            async def on_submit(self, interaction: discord.Interaction):
                reason = self.reason.value or 'n/a'
                server.kick(self.player, reason)
                await server.bot.audit(f"kicked player {self.player.display_name}" +
                                       (f' with reason "{self.reason}".' if reason != 'n/a' else '.'),
                                       user=interaction.user)
                await interaction.response.send_message(f"Kicked player {self.player.display_name}.")

        class KickView(View):
            @discord.ui.select(placeholder="Select a player to be kicked",
                               options=[SelectOption(label=x.name,
                                                     value=str(players.index(x))) for x in players])
            async def callback(self, interaction: Interaction, select: Select):
                modal = KickModal(players[int(select.values[0])])
                await interaction.response.send_modal(modal)
                self.stop()

            @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
            async def cancel(self, interaction: Interaction, button: Button):
                await interaction.response.send_message('Aborted.')
                self.stop()

            async def interaction_check(self, interaction: Interaction, /) -> bool:
                if interaction.user != ctx.author:
                    await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
                    return False
                else:
                    return True

        view = KickView()
        msg = await ctx.send(view=view)
        await view.wait()
        await msg.delete()

    @commands.command(description='Bans a user by ucid or discord id', usage='<member|ucid> [reason]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def ban(self, ctx, user: Union[discord.Member, str], *args):
        if len(args) > 0:
            reason = ' '.join(args)
        else:
            reason = 'n/a'
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(user, discord.Member):
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # ban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    for server in self.bot.servers.values():
                        server.sendtoDCS({
                            "command": "ban",
                            "ucid": ucid,
                            "reason": reason
                        })
                        player = server.get_player(ucid=ucid)
                        if player:
                            player.banned = True
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Unbans a user by ucid or discord id', usage='<member|ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unban(self, ctx, user: Union[discord.Member, str]):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(user, discord.Member):
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # unban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    for server in self.bot.servers.values():
                        server.sendtoDCS({"command": "unban", "ucid": ucid})
                        player = server.get_player(ucid=ucid)
                        if player:
                            player.banned = False
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Moves a user to spectators', usage='<name>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def spec(self, ctx, name, *args):
        server: Server = await self.bot.get_server(ctx)
        if server:
            reason = ' '.join(args) if len(args) > 0 else None
            player = server.get_player(name=name, active=True)
            if player:
                server.move_to_spectators(player)
                if reason:
                    player.sendChatMessage(f"You have been moved to spectators. Reason: {reason}",
                                           ctx.message.author.display_name)
                await ctx.send(f'User "{name}" moved to spectators.')
                await self.bot.audit(f'moved player {name} to spectators' + (f' with reason "{reason}".' if reason != 'n/a' else '.'),
                                     user=ctx.message.author)
            else:
                await ctx.send(f"Player {name} not found.")

    @commands.command(description='Download config files or missions', aliases=['dcslog', 'botlog'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def download(self, ctx: commands.Context) -> None:
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return

        view = View()
        msg = None
        config = self.get_config(server)
        choices: list[discord.SelectOption] = [discord.SelectOption(label=x['label']) for x in config['downloads']]
        select1 = Select(placeholder="What do you want to download?", options=choices)

        def zip_file(filename: str) -> str:
            with ZipFile(filename + '.zip', 'w') as zipfile:
                zipfile.write(filename)
            return zipfile.filename

        async def send_file(interaction: Interaction, filename: str, target: str):
            zipped = False
            if not filename.endswith('.zip') and not filename.endswith('.miz') and not filename.endswith('acmi') and \
                    os.path.getsize(filename) >= 8 * 1024 * 1024:
                filename = await asyncio.to_thread(zip_file, filename)
                zipped = True
            await interaction.response.defer(thinking=True)
            if not target:
                dm_channel = await interaction.user.create_dm()
                for channel in [dm_channel, ctx.channel]:
                    try:
                        await channel.send(file=discord.File(filename))
                        if channel == dm_channel:
                            await interaction.followup.send('File sent as a DM.')
                        else:
                            await interaction.followup.send('Here is your file:')
                        break
                    except discord.HTTPException:
                        continue
                else:
                    await interaction.followup.send('File too large. You need a higher boost level for your server.')
            elif target.startswith('<'):
                channel = self.bot.get_channel(int(target[4:-1]))
                try:
                    await channel.send(file=discord.File(filename))
                except discord.HTTPException:
                    await interaction.followup.send('File too large. You need a higher boost level for your server.')
                if channel != ctx.channel:
                    await interaction.followup.send('File sent to the configured channel.')
                else:
                    await interaction.followup.send('Here is your file:')
            else:
                path = os.path.expandvars(target)
                shutil.copy2(filename, path)
                await interaction.followup.send('File copied to the specified location.')
            if zipped:
                os.remove(filename)
            await msg.delete()

        async def _choice(interaction: Interaction):
            for download in config['downloads']:
                if download['label'] == select1.values[0]:
                    directory = Path(os.path.expandvars(download['directory'].format(server=server)))
                    pattern = download['pattern'].format(server=server)
                    target = download['target'].format(config=self.bot.config[server.installation], server=server) if 'target' in download else None
                    break

            options: list[discord.SelectOption] = []
            files: dict[str, str] = {}
            for file in sorted(directory.glob(pattern), key=os.path.getmtime, reverse=True):
                files[file.name] = directory.__str__() + os.path.sep + file.name
                options.append(discord.SelectOption(label=file.name))
                if len(options) == 25:
                    break
            if not len(options):
                await interaction.response.send_message("No file found.")
                return
            if len(options) == 1:
                await send_file(interaction, files[options[0].value], target)
                return

            select2 = Select(placeholder="Select a file to download", options=options)

            async def _download(interaction: Interaction):
                await send_file(interaction, files[select2.values[0]], target)

            select2.callback = _download
            view.clear_items()
            view.add_item(select2)
            view.add_item(button)
            await msg.edit(view=view)
            await interaction.response.defer()

        async def _cancel(interaction: Interaction):
            await msg.delete()
            await interaction.response.defer()

        select1.callback = _choice
        button = Button(label='Cancel', style=discord.ButtonStyle.red)
        button.callback = _cancel
        view.add_item(select1)
        view.add_item(button)
        msg = await ctx.send(view=view)

    @commands.command(description='Runs a shell command', hidden=True)
    @utils.has_role('Admin')
    @commands.guild_only()
    async def shell(self, ctx, *params):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if len(params):
                cmd = shlex.split(' '.join(params))
                await self.bot.audit("executed a shell command: ```{}```".format(' '.join(cmd)), server=server,
                                     user=ctx.message.author)
                try:
                    p = subprocess.run(cmd, shell=True, capture_output=True, timeout=300)
                    await ctx.send('```' + p.stdout.decode('cp1252', 'ignore') + '```')
                except subprocess.TimeoutExpired:
                    await ctx.send('Timeout.')
            else:
                await ctx.send(f"Usage: {ctx.prefix}shell <command>")

    @tasks.loop(minutes=5.0)
    async def check_for_dcs_update(self):
        # don't run, if an update is currently running
        if self.update_pending:
            return
        try:
            branch, old_version = utils.getInstalledVersion(self.bot.config['DCS']['DCS_INSTALLATION'])
            new_version = await utils.getLatestVersion(branch)
            if new_version and old_version != new_version:
                self.log.info('A new version of DCS World is available. Auto-updating ...')
                await self.do_update([300, 120, 60])
        except Exception as ex:
            self.log.debug("Exception in check_for_dcs_update(): " + str(ex))

    @check_for_dcs_update.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def process_message(self, message) -> bool:
        async with aiohttp.ClientSession() as session:
            async with session.get(message.attachments[0].url) as response:
                if response.status == 200:
                    ctx = utils.ContextWrapper(message=message)
                    if message.attachments[0].filename.endswith('.json'):
                        data = await response.json(encoding="utf-8")
                        if 'configs' in data:
                            plugin = message.attachments[0].filename[:-5]
                            if plugin not in self.bot.plugins:
                                await message.channel.send(f"Plugin {plugin.title()} is not activated.")
                                return True
                            filename = f"config/{plugin}.json"
                            if os.path.exists(filename) and not \
                                    await utils.yn_question(ctx, f'Do you want to overwrite {filename} on '
                                                                 f'node {platform.node()}?'):
                                await message.channel.send('Aborted.')
                                return True
                            with open(filename, 'w', encoding="utf-8") as outfile:
                                json.dump(data, outfile, indent=2)
                            await self.bot.reload(plugin)
                            await message.channel.send(f"Plugin {plugin.title()} re-configured.")
                            return True
                        else:
                            return False
                    else:
                        if await utils.yn_question(ctx, f'Do you want to overwrite dcsserverbot.ini on '
                                                        f'node {platform.node()}?'):
                            with open('config/dcsserverbot.ini', 'w', encoding='utf-8') as outfile:
                                outfile.writelines('\n'.join((await response.text(encoding='utf-8')).splitlines()))
                            self.bot.config = utils.config = utils.reload()
                            await message.channel.send('dcsserverbot.ini updated.')
                            if await utils.yn_question(ctx, 'Do you want to restart the bot?'):
                                exit(-1)
                        else:
                            await message.channel.send('Aborted.')
                        return True
                else:
                    await message.channel.send(f'Error {response.status} while reading JSON file!')
                    return True

    @commands.Cog.listener()
    async def on_message(self, message):
        # ignore bot messages or messages that does not contain json attachments
        if message.author.bot or not message.attachments or \
                not (
                        message.attachments[0].filename.endswith('.json') or
                        message.attachments[0].filename == 'dcsserverbot.ini'
                ):
            return
        # only Admin role is allowed to upload json files in channels
        if not await self.bot.get_server(message) or not utils.check_roles(['Admin'], message.author):
            return
        if await self.process_message(message):
            await message.delete()


class Master(Agent):

    class CleanupView(View):
        def __init__(self, ctx: commands.Context):
            super().__init__()
            self.ctx = ctx
            self.what = 'non-members'
            self.age = '180'
            self.command = None

        @discord.ui.select(placeholder="What to be pruned?", options=[
            SelectOption(label='Non-member users (unlinked)', value='non-members', default=True),
            SelectOption(label='Members and non-members', value='users'),
            SelectOption(label='Data only (all users)', value='data')
        ])
        async def set_what(self, interaction: Interaction, select: Select):
            self.what = select.values[0]
            await interaction.response.defer()

        @discord.ui.select(placeholder="Which age to be pruned?", options=[
            SelectOption(label='Older than 90 days', value='90'),
            SelectOption(label='Older than 180 days', value='180', default=True),
            SelectOption(label='Older than 1 year', value='360 days')
        ])
        async def set_age(self, interaction: Interaction, select: Select):
            self.age = select.values[0]
            await interaction.response.defer()

        @discord.ui.button(label='Prune', style=discord.ButtonStyle.danger, emoji='⚠')
        async def prune(self, interaction: Interaction, button: Button):
            await interaction.response.defer()
            self.command = "prune"
            self.stop()

        @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
        async def cancel(self, interaction: Interaction, button: Button):
            await interaction.response.defer()
            self.command = "cancel"
            self.stop()

        async def interaction_check(self, interaction: Interaction, /) -> bool:
            if interaction.user != self.ctx.author:
                await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
                return False
            else:
                return True

    @commands.command(description='Prune unused data in the database', hidden=True, aliases=['prune'])
    @utils.has_role('Admin')
    @commands.guild_only()
    async def cleanup(self, ctx):
        embed = discord.Embed(title=":warning: Database Prune :warning:")
        embed.description = "You are going to delete data from your database. Be advised.\n\n" \
                            "Please select the data to be pruned:"
        view = self.CleanupView(ctx)
        msg = await ctx.send(embed=embed, view=view)
        await view.wait()
        await msg.delete()
        if view.command == "cancel":
            await ctx.send('Aborted.')
            return

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if view.what in ['users', 'non-members']:
                    sql = f"SELECT ucid FROM players WHERE last_seen < (DATE(NOW()) - interval '{view.age} days')"
                    if view.what == 'non-members':
                        sql += ' AND discord_id = -1'

                    cursor.execute(sql)
                    ucids = [row[0] for row in cursor.fetchall()]
                    if not ucids:
                        await ctx.send('No players to prune.')
                        return
                    if not await utils.yn_question(ctx, f"This will delete {len(ucids)} players incl. their stats "
                                                        f"from the database.\nAre you sure?"):
                        return
                    for plugin in self.bot.cogs.values():  # type: Plugin
                        await plugin.prune(conn, ucids=ucids)
                    for ucid in ucids:
                        cursor.execute('DELETE FROM players WHERE ucid = %s', (ucid, ))
                    await ctx.send(f"{len(ucids)} players pruned.")
                elif view.what == 'data':
                    days = int(view.age)
                    if not await utils.yn_question(ctx, f"This will delete all data older than {days} days from the "
                                                        f"database.\nAre you sure?"):
                        return
                    for plugin in self.bot.cogs.values():  # type: Plugin
                        await plugin.prune(conn, days=days)
                    await ctx.send(f"All data older than {days} days pruned.")
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)
        await self.bot.audit(f'pruned the database', user=ctx.message.author)

    @commands.command(description='Bans a user by ucid or discord id', usage='<member|ucid> [reason]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def ban(self, ctx, user: Union[discord.Member, str], *args):
        if len(args) > 0:
            reason = ' '.join(args)
        else:
            reason = 'n/a'
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(user, discord.Member):
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # ban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s)',
                                   (ucid, ctx.message.author.display_name, reason))
                conn.commit()
                await super().ban(self, ctx, user, *args)
            if isinstance(user, discord.Member):
                await ctx.send('Member {} banned.'.format(utils.escape_string(user.display_name)))
            else:
                await ctx.send(f'Player {user} banned.')
            await self.bot.audit('banned ' +
                                 ('member {}'.format(utils.escape_string(user.display_name)) if isinstance(user, discord.Member) else f' ucid {user}') +
                                 (f' with reason "{reason}"' if reason != 'n/a' else ''),
                                 user=ctx.message.author)
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Unbans a user by ucid or discord id', usage='<member|ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unban(self, ctx, user: Union[discord.Member, str]):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(user, discord.Member):
                    # a player can have multiple ucids
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id, ))
                    ucids = [row[0] for row in cursor.fetchall()]
                else:
                    # unban a specific ucid only
                    ucids = [user]
                for ucid in ucids:
                    cursor.execute('DELETE FROM bans WHERE ucid = %s', (ucid, ))
                conn.commit()
                await super().unban(self, ctx, user)
            if isinstance(user, discord.Member):
                await ctx.send('Member {} unbanned.'.format(utils.escape_string(user.display_name)))
            else:
                await ctx.send(f'Player {user} unbanned.')
            await self.bot.audit(f'unbanned ' +
                                 ('member {}'.format(utils.escape_string(user.display_name)) if isinstance(user, discord.Member) else f' ucid {user}'),
                                 user=ctx.message.author)
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    def format_bans(self, rows):
        embed = discord.Embed(title='List of Bans', color=discord.Color.blue())
        ucids = names = reasons = ''
        for ban in rows:
            if ban['discord_id'] != -1:
                user = self.bot.get_user(ban['discord_id'])
            else:
                user = None
            names += utils.escape_string(user.name if user else ban['name'] if ban['name'] else '<unknown>') + '\n'
            ucids += ban['ucid'] + '\n'
            reasons += ban['reason'] + '\n'
        embed.add_field(name='UCID', value=ucids)
        embed.add_field(name='Name', value=names)
        embed.add_field(name='Reason', value=reasons)
        return embed

    @commands.command(description='Shows active bans')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def bans(self, ctx):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT b.ucid, COALESCE(p.discord_id, -1) AS discord_id, p.name, b.banned_by, '
                               'b.reason FROM bans b LEFT OUTER JOIN players p on b.ucid = p.ucid')
                rows = list(cursor.fetchall())
                await utils.pagination(self.bot, ctx, rows, self.format_bans, 20)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    def update_bans(self, data: Optional[dict] = None):
        banlist = []
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT ucid, reason FROM bans')
                banlist = [dict(row) for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        if data is not None:
            servers = [self.bot.servers[data['server_name']]]
        else:
            servers = self.bot.servers.values()
        for server in servers:
            for ban in banlist:
                server.sendtoDCS({
                    "command": "ban",
                    "ucid": ban['ucid'],
                    "reason": ban['reason']
                })

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        self.bot.log.debug(f'Member {member.display_name} has left the discord')
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if self.bot.config.getboolean('BOT', 'AUTOBAN'):
                    self.bot.log.debug(f'- Auto-ban member {member.display_name} on the DCS servers')
                    cursor.execute('INSERT INTO bans SELECT ucid, \'DCSServerBot\', \'Player left guild.\' FROM '
                                   'players WHERE discord_id = %s ON CONFLICT DO NOTHING', (member.id, ))
                    self.update_bans()
                self.bot.log.debug(f'- Delete stats of member {member.display_name}')
                cursor.execute('DELETE FROM statistics WHERE player_ucid IN (SELECT ucid FROM players WHERE '
                               'discord_id = %s)', (member.id, ))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, member: discord.Member):
        self.bot.log.debug(f"Member {member.display_name} has been banned.")
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                self.bot.log.debug(f'- Ban member {member.display_name} on the DCS servers.')
                cursor.execute('INSERT INTO bans SELECT ucid, \'DCSServerBot\', \'Player left guild.\' FROM '
                               'players WHERE discord_id = %s ON CONFLICT DO NOTHING', (member.id, ))
                self.update_bans()
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        self.bot.log.debug('Member {} has joined guild {}'.format(member.display_name, member.guild.name))
        if self.bot.config.getboolean('BOT', 'AUTOBAN') is True:
            self.bot.log.debug('Remove possible bans from DCS servers.')
            conn = self.bot.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    # auto-unban them if they were auto-banned
                    cursor.execute('DELETE FROM bans WHERE ucid IN (SELECT ucid FROM players WHERE '
                                   'discord_id = %s)', (member.id, ))
                    self.update_bans()
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.bot.log.exception(error)
                conn.rollback()
            finally:
                self.bot.pool.putconn(conn)
        if 'GREETING_DM' in self.bot.config['BOT']:
            channel = await member.create_dm()
            await channel.send(self.bot.config['BOT']['GREETING_DM'].format(name=member.name, guild=member.guild.name))

    @commands.Cog.listener()
    async def on_message(self, message):
        # ignore bot messages or messages that does not contain json attachments
        if message.author.bot or not message.attachments or \
                not (
                        message.attachments[0].filename.endswith('.json') or
                        message.attachments[0].filename == 'dcsserverbot.ini'
                ):
            return
        # only Admin role is allowed to upload json files in channels
        if not utils.check_roles(['Admin'], message.author):
            return
        if await self.bot.get_server(message) and await super().process_message(message):
            await message.delete()
            return
        if not message.attachments[0].filename.endswith('.json'):
            return
        async with aiohttp.ClientSession() as session:
            async with session.get(message.attachments[0].url) as response:
                if response.status == 200:
                    data = await response.json(encoding="utf-8")
                    if 'configs' not in data:
                        embed = utils.format_embed(data)
                        msg = None
                        if 'message_id' in data:
                            try:
                                msg = await message.channel.fetch_message(int(data['message_id']))
                                await msg.edit(embed=embed)
                            except discord.errors.NotFound:
                                msg = None
                            except discord.errors.DiscordException as ex:
                                self.log.exception(ex)
                                await message.channel.send(f'Error while updating embed!')
                                return
                        if not msg:
                            await message.channel.send(embed=embed)
                        await message.delete()
                else:
                    await message.channel.send(f'Error {response.status} while reading JSON file!')


async def setup(bot: DCSServerBot):
    if not os.path.exists('config/admin.json'):
        bot.log.info('No admin.json found, copying the sample.')
        shutil.copyfile('config/samples/admin.json', 'config/admin.json')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(Master(bot, AdminEventListener))
    else:
        await bot.add_cog(Agent(bot, AdminEventListener))
