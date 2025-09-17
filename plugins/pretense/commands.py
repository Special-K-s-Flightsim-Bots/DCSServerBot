import discord
import json
import os

from core import Plugin, Status, PersistentReport, Channel, utils, Server, Report, get_translation, Group
from discord import app_commands
from discord.ext import tasks, commands
from discord.utils import MISSING
from services.bot import DCSServerBot
from typing import Literal

_ = get_translation(__name__.split('.')[1])


class Pretense(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.last_mtime = dict()

    async def cog_load(self) -> None:
        await super().cog_load()
        self.update_leaderboard.start()
        config = self.get_config()
        if config:
            interval = config.get('update_interval', 120)
            self.update_leaderboard.change_interval(seconds=interval)

    async def cog_unload(self) -> None:
        self.update_leaderboard.cancel()
        await super().cog_unload()

    # New command group "/mission"
    pretense = Group(name="pretense", description=_("Commands to manage Pretense missions"))

    @pretense.command(description=_('Display the Pretense stats'))
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    async def stats(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer]):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        config = self.get_config(server) or {}
        json_file_path = config.get('json_file_path',
                                    os.path.join(await server.get_missions_dir(), 'Saves', "player_stats.json"))
        json_file_path = os.path.expandvars(utils.format_string(json_file_path, instance=server.instance))
        json_file_path = os.path.expandvars(json_file_path)
        try:
            file_data = await server.node.read_file(json_file_path)
        except FileNotFoundError:
            await interaction.followup.send(
                _("No {} found on this server! Is Pretense active?").format(os.path.basename(json_file_path)),
                ephemeral=True
            )
            return
        content = file_data.decode(encoding='utf-8')
        data = json.loads(content)
        report = Report(self.bot, self.plugin_name, "pretense.json")
        env = await report.render(data=data, server=server)
        try:
            file = discord.File(fp=env.buffer, filename=env.filename) if env.filename else MISSING
            msg = await interaction.original_response()
            await msg.edit(embed=env.embed, attachments=[file],
                           delete_after=self.bot.locals.get('message_autodelete'))
        finally:
            if env.buffer:
                env.buffer.close()

    @pretense.command(description=_('Reset Pretense progress'))
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def reset(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[
                        Status.STOPPED, Status.SHUTDOWN])], what: Literal['persistence', 'statistics', 'both']):
        if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {} needs to be shut down to reset the Pretense progress!").format(server.display_name),
                ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        if not await utils.yn_question(interaction, _("Do you really want to reset the Pretense progress?")):
            await interaction.followup.send(_("Aborted."), ephemeral=ephemeral)
        if what == 'persistence' or what == 'both':
            path = os.path.join(await server.get_missions_dir(), 'Saves', "pretense_*.json")
            await server.node.remove_file(path)
            await interaction.followup.send(_("Pretense persistence reset."), ephemeral=ephemeral)
        if what == 'statistics' or what == 'both':
            path = os.path.join(await server.get_missions_dir(), 'Saves', "player_stats*.json")
            await server.node.remove_file(path)
            await interaction.followup.send(_("Pretense statistics reset."), ephemeral=ephemeral)

    @tasks.loop(seconds=120)
    async def update_leaderboard(self):
        for server in self.bot.servers.values():
            try:
                if server.status != Status.RUNNING:
                    continue
                config = self.get_config(server)
                if not config:
                    continue
                json_file_path = config.get(
                    'json_file_path',
                    os.path.join(await server.get_missions_dir(), 'Saves', "player_stats.json")
                )
                json_file_path = os.path.expandvars(utils.format_string(json_file_path, instance=server.instance))
                json_file_path = os.path.expandvars(json_file_path)
                try:
                    file_data = await server.node.read_file(json_file_path)
                except FileNotFoundError:
                    continue
                content = file_data.decode(encoding='utf-8')
                data = json.loads(content)
                report = PersistentReport(self.bot, self.plugin_name, "pretense.json", embed_name="leaderboard",
                                          channel_id=config.get('channel', server.channels[Channel.STATUS]),
                                          server=server)
                await report.render(data=data, server=server)
            except Exception as ex:
                self.log.exception(ex)

    @update_leaderboard.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages
        if message.author.bot:
            return
        if not message.attachments or not utils.check_roles(self.bot.roles['DCS Admin'], message.author):
            return
        server: Server = self.bot.get_server(message, admin_only=True)
        for attachment in message.attachments:
            if not (attachment.filename in ['player_stats.json', 'player_stats_v2.0.json'] or
                    (attachment.filename.startswith('pretense') and attachment.filename.endswith('.json'))):
                continue
            if not server:
                ctx = await self.bot.get_context(message)
                # check if there is a central admin channel configured
                admin_channel = self.bot.locals.get('channels', {}).get('admin')
                if not admin_channel or admin_channel != message.channel.id:
                    return
                try:
                    server = await utils.server_selection(self.bus, ctx,
                                                          title=_("To which server do you want to upload to?"))
                    if not server:
                        await ctx.send(_('Upload aborted.'))
                        return
                except Exception as ex:
                    self.log.exception(ex)
                    return
            try:
                filename = os.path.join(await server.get_missions_dir(), 'Saves', attachment.filename)
                await server.node.write_file(filename, attachment.url, overwrite=True)
                await message.channel.send(_('Pretense file {} uploaded.').format(attachment.filename))
            except Exception as ex:
                self.log.exception(ex)
                await message.channel.send(_('Pretense file {} could not be uploaded!').format(attachment.filename))
            finally:
                await message.delete()



async def setup(bot: DCSServerBot):
    await bot.add_cog(Pretense(bot))
