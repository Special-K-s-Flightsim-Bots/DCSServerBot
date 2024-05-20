import discord
import json
import os

from core import Plugin, Status, PersistentReport, Channel, utils, command, Server, Report, get_translation, Group
from discord import app_commands
from discord.ext import tasks
from discord.utils import MISSING
from services import DCSServerBot

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
            await interaction.followup.send(_("No player_stats.json found on this server! Is Pretense active?"),
                                            ephemeral=True)
            return
        content = file_data.decode(encoding='utf-8')
        data = json.loads(content)
        report = Report(self.bot, self.plugin_name, "pretense.json")
        env = await report.render(data=data, server=server)
        try:
            file = discord.File(fp=env.buffer, filename=env.filename) if env.filename else MISSING
            await interaction.followup.send(embed=env.embed, file=file)
        finally:
            if env.buffer:
                env.buffer.close()

    @pretense.command(description=_('Reset Pretense progress'))
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def reset(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[
                        Status.STOPPED, Status.SHUTDOWN])]):
        if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {} needs to be shut down to reset the Pretense progress!").format(server.display_name),
                ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        if not await utils.yn_question(interaction, _("Do you really want to reset the Pretense progress?")):
            await interaction.followup.send(_("Aborted."), ephemeral=ephemeral)
        path = os.path.join(await server.get_missions_dir(), 'Saves', "pretense_*.json")
        await server.node.remove_file(path)
        await interaction.followup.send(_("Pretense progress reset."))

    @tasks.loop(seconds=120)
    async def update_leaderboard(self):
        for server in self.bot.servers.copy().values():
            try:
                if server.status != Status.RUNNING:
                    continue
                config = self.get_config(server)
                if not config:
                    continue
                json_file_path = config.get('json_file_path',
                                            os.path.join(await server.get_missions_dir(), 'Saves', "player_stats.json"))
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


async def setup(bot: DCSServerBot):
    await bot.add_cog(Pretense(bot))
