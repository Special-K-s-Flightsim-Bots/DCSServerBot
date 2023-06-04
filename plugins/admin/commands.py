import aiohttp
import asyncio
import discord
import json
import os
import platform
import shutil
from contextlib import closing
from core import utils, Plugin, Status, Server, command
from discord import app_commands
from discord.app_commands import Range
from discord.ext import commands
from discord.ui import Select, View, Button
from pathlib import Path
from services import DCSServerBot
from typing import Optional
from zipfile import ZipFile


class Admin(Plugin):

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            self.log.info('No admin.yaml found, copying the sample.')
            shutil.copyfile('config/samples/admin.yaml', 'config/plugins/admin.yaml')
            config = super().read_locals()
        return config

    @command(description='Update your DCS installations')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(node=utils.nodes_autocomplete)
    @app_commands.describe(warn_time="Time in seconds to warn users before shutdown")
    async def update(self, interaction: discord.Interaction, node: Optional[str], warn_time: Range[int, 0]):
        self.bus.sendtoBot({
            "command": "rpc",
            "service": "Monitoring",
            "method": "do_update",
            "params": {
                "warn_times": [warn_time]
            }
        }, node)
        await interaction.response.send_message(
            "Update command sent, DCS will update now (if a new version is available).")

    @command(description='Download config files or missions')
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
                    os.path.getsize(filename) >= 25 * 1024 * 1024:
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
                    target = download['target'].format(server=server) if 'target' in download else None
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

    class CleanupView(View):
        def __init__(self):
            super().__init__()
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

    @command(description='Prune unused data in the database')
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

    @command(description='Status of all registered servers')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def status(self, interaction: discord.Interaction):
        embed = discord.Embed(title=f"Server Status ({platform.node()})", color=discord.Color.blue())
        names = []
        status = []
        nodes = []
        for server in self.bot.servers.values():
            names.append(server.display_name)
            status.append(server.status.name.title())
            nodes.append(server.node.name)
        if len(names):
            embed.add_field(name='Server', value='\n'.join(names))
            embed.add_field(name='Status', value='\n'.join(status))
            embed.add_field(name='Node', value='\n'.join(nodes))
            embed.set_footer(text=f"Bot Version: v{self.bot.version}.{self.bot.sub_version}")
            await interaction.response.send_message(embed=embed)

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
        if not utils.check_roles(self.bot.roles['Admin'], message.author):
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

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self.bot.log.debug(f'Member {member.display_name} has joined guild {member.guild.name}')
        ucid = self.bot.get_ucid_by_member(member)
        if ucid and self.bot.locals.get('autoban', False):
            for server in self.bot.servers.values():
                server.unban(ucid)
        if self.bot.locals.get('greeting_dm'):
            channel = await member.create_dm()
            await channel.send(self.bot.locals['greeting_dm'].format(name=member.name, guild=member.guild.name))

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        self.bot.log.debug(f'Member {member.display_name} has left the discord')
        ucid = self.bot.get_ucid_by_member(member)
        if ucid and self.bot.locals.get('autoban', False):
            self.bot.log.debug(f'- Banning them on our DCS servers due to AUTOBAN')
            for server in self.bot.servers.values():
                server.ban(ucid, 'Player left discord.', 9999*86400)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Admin(bot))
