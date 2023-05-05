import aiohttp
import asyncio
import discord
import json
import os
import platform
import shutil
import subprocess
from contextlib import closing
from core import utils, Plugin, Status, Server, Coalition
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Select, View, Button
from pathlib import Path
from services import DCSServerBot
from typing import List, Optional
from zipfile import ZipFile

from .listener import AdminEventListener


class Admin(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.update_pending = False
        if self.bot.config.getboolean('DCS', 'AUTOUPDATE'):
            self.check_for_dcs_update.start()

    async def cog_unload(self):
        if self.bot.config.getboolean('DCS', 'AUTOUPDATE'):
            self.check_for_dcs_update.cancel()
        await super().cog_unload()

    async def do_update(self, warn_times: List[int], interaction: Optional[discord.Interaction] = None):
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
        if interaction:
            await interaction.response.send('Shutting down DCS servers, warning users before ...', ephemeral=True)
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
        if tasks:
            await asyncio.gather(*tasks)
        if interaction:
            await interaction.followup.send(f"Updating {self.bot.config['DCS']['DCS_INSTALLATION']} ...\n"
                                            f"Please wait, this might take some time.", ephemeral=True)
        else:
            self.log.info(f"Updating {self.bot.config['DCS']['DCS_INSTALLATION']} ...")
        for plugin in self.bot.cogs.values():  # type: Plugin
            await plugin.before_dcs_update()
        # disable any popup on the remote machine
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
        startupinfo.wShowWindow = subprocess.SW_HIDE
        subprocess.run(['dcs_updater.exe', '--quiet', 'update'], executable=os.path.expandvars(
            self.bot.config['DCS']['DCS_INSTALLATION']) + '\\bin\\dcs_updater.exe', startupinfo=startupinfo)
        if self.bot.config.getboolean('BOT', 'DESANITIZE'):
            utils.desanitize(self)
        # run after_dcs_update() in all plugins
        for plugin in self.bot.cogs.values():  # type: Plugin
            await plugin.after_dcs_update()
        message = None
        if ctx:
            await ctx.send(f"{self.bot.config['DCS']['DCS_INSTALLATION']} updated to the latest version.")
            message = await ctx.send('Starting up DCS servers again ...')
        else:
            self.log.info(f"{self.bot.config['DCS']['DCS_INSTALLATION']} updated to the latest version. "
                          f"Starting up DCS servers again ...")
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

    @app_commands.command(description='Download config files or missions')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def download(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])]) -> None:
        view = View()
        msg = None
        config = self.get_config(server)
        choices: list[discord.SelectOption] = [discord.SelectOption(label=x['label']) for x in config['downloads']]
        select1 = Select(placeholder="What do you want to download?", options=choices)

        def zip_file(filename: str) -> str:
            with ZipFile(filename + '.zip', 'w') as zipfile:
                zipfile.write(filename)
            return zipfile.filename

        async def send_file(interaction: discord.Interaction, filename: str, target: str):
            zipped = False
            if not filename.endswith('.zip') and not filename.endswith('.miz') and not filename.endswith('acmi') and \
                    os.path.getsize(filename) >= 8 * 1024 * 1024:
                filename = await asyncio.to_thread(zip_file, filename)
                zipped = True
            await interaction.response.defer(thinking=True)
            if not target:
                dm_channel = await interaction.user.create_dm()
                for channel in [dm_channel, interaction.channel]:
                    try:
                        await channel.send(file=discord.File(filename))
                        if channel == dm_channel:
                            await interaction.followup.send('File sent as a DM.', ephemeral=True)
                        else:
                            await interaction.followup.send('Here is your file:', ephemeral=True)
                        break
                    except discord.HTTPException:
                        continue
                else:
                    await interaction.followup.send('File too large. You need a higher boost level for your server.',
                                                    ephemeral=True)
            elif target.startswith('<'):
                channel = self.bot.get_channel(int(target[4:-1]))
                try:
                    await channel.send(file=discord.File(filename))
                except discord.HTTPException:
                    await interaction.followup.send('File too large. You need a higher boost level for your server.',
                                                    ephemeral=True)
                if channel != interaction.channel:
                    await interaction.followup.send('File sent to the configured channel.', ephemeral=True)
                else:
                    await interaction.followup.send('Here is your file:', ephemeral=True)
            else:
                path = os.path.expandvars(target)
                shutil.copy2(filename, path)
                await interaction.followup.send('File copied to the specified location.', ephemeral=True)
            if zipped:
                os.remove(filename)
            await msg.delete()

        async def _choice(interaction: discord.Interaction):
            for download in config['downloads']:
                if download['label'] == select1.values[0]:
                    directory = Path(os.path.expandvars(download['directory'].format(server=server)))
                    pattern = download['pattern'].format(server=server)
                    target = download['target'].format(config=self.bot.config[server.installation],
                                                       server=server) if 'target' in download else None
                    break

            options: list[discord.SelectOption] = []
            files: dict[str, str] = {}
            for file in sorted(directory.glob(pattern), key=os.path.getmtime, reverse=True):
                files[file.name] = directory.__str__() + os.path.sep + file.name
                options.append(discord.SelectOption(label=file.name))
                if len(options) == 25:
                    break
            if not len(options):
                await interaction.response.send_message("No file found.", ephemeral=True)
                return
            if len(options) == 1:
                await send_file(interaction, files[options[0].value], target)
                return

            select2 = Select(placeholder="Select a file to download", options=options)

            async def _download(interaction: discord.Interaction):
                await send_file(interaction, files[select2.values[0]], target)

            select2.callback = _download
            view.clear_items()
            view.add_item(select2)
            view.add_item(button)
            await msg.edit(view=view)
            await interaction.response.defer()

        async def _cancel(interaction: discord.Interaction):
            await interaction.response.defer()
            view.stop()

        select1.callback = _choice
        button = Button(label='Cancel', style=discord.ButtonStyle.red)
        button.callback = _cancel
        view.add_item(select1)
        view.add_item(button)
        await interaction.response.send_message(view=view, ephemeral=True)
        msg = await interaction.original_response()
        try:
            await view.wait()
        finally:
            await msg.delete()

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

    class CleanupView(View):
        def __init__(self, ctx: commands.Context):
            super().__init__()
            self.ctx = ctx
            self.what = 'non-members'
            self.age = '180'
            self.command = None

        @discord.ui.select(placeholder="What to be pruned?", options=[
            discord.SelectOption(label='Non-member users (unlinked)', value='non-members', default=True),
            discord.SelectOption(label='Members and non-members', value='users'),
            discord.SelectOption(label='Data only (all users)', value='data')
        ])
        async def set_what(self, interaction: discord.Interaction, select: Select):
            self.what = select.values[0]
            await interaction.response.defer()

        @discord.ui.select(placeholder="Which age to be pruned?", options=[
            discord.SelectOption(label='Older than 90 days', value='90'),
            discord.SelectOption(label='Older than 180 days', value='180', default=True),
            discord.SelectOption(label='Older than 1 year', value='360 days')
        ])
        async def set_age(self, interaction: discord.Interaction, select: Select):
            self.age = select.values[0]
            await interaction.response.defer()

        @discord.ui.button(label='Prune', style=discord.ButtonStyle.danger, emoji='⚠')
        async def prune(self, interaction: discord.Interaction, button: Button):
            await interaction.response.defer()
            self.command = "prune"
            self.stop()

        @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
        async def cancel(self, interaction: discord.Interaction, button: Button):
            await interaction.response.defer()
            self.command = "cancel"
            self.stop()

        async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
            if interaction.user != self.ctx.author:
                await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
                return False
            else:
                return True

    @app_commands.command(description='Prune unused data in the database')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def prune(self, interaction: discord.Interaction):
        embed = discord.Embed(title=":warning: Database Prune :warning:")
        embed.description = "You are going to delete data from your database. Be advised.\n\n" \
                            "Please select the data to be pruned:"
        view = self.CleanupView()
        msg = await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            await view.wait()
        finally:
            await msg.delete()
        if view.command == "cancel":
            await interaction.followup.send('Aborted.', ephemeral=True)
            return

        with self.pool.connection() as conn:
            with conn.pipeline():
                with conn.transaction():
                    with closing(conn.cursor()) as cursor:
                        if view.what in ['users', 'non-members']:
                            sql = f"SELECT ucid FROM players WHERE last_seen < (DATE(NOW()) - interval '{view.age} days')"
                            if view.what == 'non-members':
                                sql += ' AND discord_id = -1'
                            ucids = [row[0] for row in cursor.execute(sql).fetchall()]
                            if not ucids:
                                await interaction.followup.send('No players to prune.', ephemeral=True)
                                return
                            if not await utils.yn_question(interaction, f"This will delete {len(ucids)} players incl. "
                                                                        f"their stats from the database.\n"
                                                                        f"Are you sure?"):
                                return
                            for plugin in self.bot.cogs.values():  # type: Plugin
                                await plugin.prune(conn, ucids=ucids)
                            for ucid in ucids:
                                cursor.execute('DELETE FROM players WHERE ucid = %s', (ucid, ))
                            await interaction.followup.send(f"{len(ucids)} players pruned.", ephemeral=True)
                        elif view.what == 'data':
                            days = int(view.age)
                            if not await utils.yn_question(interaction, f"This will delete all data older than {days} "
                                                                        f"days from the database.\nAre you sure?"):
                                return
                            for plugin in self.bot.cogs.values():  # type: Plugin
                                await plugin.prune(conn, days=days)
                            await interaction.followup.send(f"All data older than {days} days pruned.", ephemeral=True)
        await self.bot.audit(f'pruned the database', user=interaction.user)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        self.bot.log.debug(f'Member {member.display_name} has left the discord')
        ucid = self.bot.get_ucid_by_member(member)
        if ucid and self.bot.config.getboolean('BOT', 'AUTOBAN'):
            self.bot.log.debug(f'- Banning them on our DCS servers due to AUTOBAN')
            for server in self.bot.servers.values():
                server.ban(ucid, 'Player left discord.', 9999*86400)
        if self.bot.config.getboolean('BOT', 'WIPE_STATS_ON_LEAVE'):
            with self.pool.connection() as conn:
                with conn.transaction():
                    self.bot.log.debug(f'- Deleting their statistics due to WIPE_STATS_ON_LEAVE')
                    conn.execute("""
                        DELETE FROM statistics 
                        WHERE player_ucid IN (
                            SELECT ucid FROM players WHERE discord_id = %s
                        )
                        """, (member.id, ))

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, member: discord.Member):
        self.bot.log.debug(f"Member {member.display_name} has been banned.")
        ucid = self.bot.get_ucid_by_member(member)
        if ucid:
            for server in self.bot.servers.values():
                server.ban(ucid, 'Banned on discord.', 9999*86400)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self.bot.log.debug(f'Member {member.display_name} has joined guild {member.guild.name}')
        ucid = self.bot.get_ucid_by_member(member)
        if ucid:
            for server in self.bot.servers.values():
                server.unban(ucid)
        if 'GREETING_DM' in self.bot.config['BOT']:
            channel = await member.create_dm()
            await channel.send(self.bot.config['BOT']['GREETING_DM'].format(name=member.name, guild=member.guild.name))

    async def process_message(self, message: discord.Message) -> bool:
        async with aiohttp.ClientSession() as session:
            async with session.get(message.attachments[0].url) as response:
                if response.status == 200:
                    interaction = message.interaction
                    if message.attachments[0].filename.endswith('.json'):
                        data = await response.json(encoding="utf-8")
                        if 'configs' in data:
                            plugin = message.attachments[0].filename[:-5]
                            if plugin not in self.bot.plugins:
                                await message.channel.send(f"Plugin {plugin.title()} is not activated.")
                                return True
                            filename = f"config/{plugin}.json"
                            if os.path.exists(filename) and not \
                                    await utils.yn_question(interaction, f'Do you want to overwrite {filename} on '
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
                        if await utils.yn_question(interaction, f'Do you want to overwrite dcsserverbot.ini on '
                                                                f'node {platform.node()}?'):
                            with open('config/dcsserverbot.ini', 'w', encoding='utf-8') as outfile:
                                outfile.writelines('\n'.join((await response.text(encoding='utf-8')).splitlines()))
                            self.bot.config = utils.config = utils.reload()
                            await message.channel.send('dcsserverbot.ini updated.')
                            if await utils.yn_question(interaction, 'Do you want to restart the bot?'):
                                exit(-1)
                        else:
                            await message.channel.send('Aborted.')
                        return True
                else:
                    await message.channel.send(f'Error {response.status} while reading JSON file!')
                    return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
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
        if await self.bot.get_server(message) and await self.process_message(message):
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
    await bot.add_cog(Admin(bot, AdminEventListener))
