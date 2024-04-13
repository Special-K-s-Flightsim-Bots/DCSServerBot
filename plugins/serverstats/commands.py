import discord
import psycopg

from core import utils, Plugin, TEventListener, PluginRequiredError, Report, PaginationReport, Server, command, \
    ValueNotInRange
from discord import app_commands
from discord.ext import tasks
from discord.utils import MISSING
from services import DCSServerBot
from typing import Type, Optional, Literal, Union

from .listener import ServerStatsListener
from ..userstats.filter import StatisticsFilter, PeriodFilter, CampaignFilter, MissionFilter, PeriodTransformer, \
    SquadronFilter


class ServerStats(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.cleanup.add_exception_type(psycopg.DatabaseError)
        self.cleanup.start()
        self.io_counters = {}
        self.net_io_counters = None

    async def cog_unload(self):
        self.cleanup.cancel()
        await super().cog_unload()

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: Optional[str] = None) -> None:
        self.log.debug('Pruning Serverstats ...')
        if server:
            await conn.execute("DELETE FROM serverstats WHERE server_name = %s", (server, ))
        self.log.debug('Serverstats pruned.')

    async def rename(self, conn: psycopg.AsyncConnection, old_name: str, new_name: str):
        await conn.execute('UPDATE serverstats SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    async def display_report(self, interaction: discord.Interaction, schema: str, period: Union[str, StatisticsFilter],
                             server: Server, ephemeral: bool):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        report = Report(self.bot, self.plugin_name, schema)
        env = await report.render(period=period, server_name=server.name, node=server.node.name)
        try:
            file = discord.File(fp=env.buffer, filename=env.filename) if env.filename else MISSING
            await interaction.followup.send(embed=env.embed, file=file, ephemeral=ephemeral)
        finally:
            if env.buffer:
                env.buffer.close()

    @command(description='Displays the load of your DCS servers')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(_server="server")
    async def serverload(self, interaction: discord.Interaction,
                         _server: Optional[app_commands.Transform[Server, utils.ServerTransformer]],
                         period: Optional[Literal['Hour', 'Day', 'Week', 'Month']] = 'Hour'):
        try:
            if _server:
                await self.display_report(interaction, 'serverload.json', str(period), _server,
                                          ephemeral=utils.get_ephemeral(interaction))
            else:
                report = PaginationReport(self.bot, interaction, self.plugin_name, 'serverload.json')
                await report.render(period=period, server_name=None)
        except ValueNotInRange as ex:
            await interaction.followup.send(ex, ephemeral=utils.get_ephemeral(interaction))

    @command(description='Shows servers statistics')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.rename(_server="server")
    @app_commands.describe(period='Select one of the default periods or enter the name of a campaign or a mission')
    async def serverstats(self, interaction: discord.Interaction,
                          _server: Optional[app_commands.Transform[Server, utils.ServerTransformer]],
                          period: Optional[app_commands.Transform[
                              StatisticsFilter, PeriodTransformer(
                                  flt=[PeriodFilter, CampaignFilter, MissionFilter, SquadronFilter]
                              )]] = PeriodFilter()):
        try:
            if _server:
                await self.display_report(interaction, 'serverstats.json', period, _server,
                                          ephemeral=utils.get_ephemeral(interaction))
            else:
                report = PaginationReport(self.bot, interaction, self.plugin_name, 'serverstats.json')
                await report.render(period=period, server_name=None)
        except ValueNotInRange as ex:
            await interaction.followup.send(ex, ephemeral=utils.get_ephemeral(interaction))

    @tasks.loop(hours=12.0)
    async def cleanup(self):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM serverstats WHERE time < (CURRENT_TIMESTAMP - interval '1 month')")


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    await bot.add_cog(ServerStats(bot, ServerStatsListener))
