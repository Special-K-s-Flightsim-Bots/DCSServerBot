import re
import discord
import psycopg

from core import utils, Plugin, PluginRequiredError, Report, PaginationReport, Server, command, \
    ValueNotInRange
from datetime import datetime, timezone
from discord import app_commands
from discord.ext import tasks
from discord.utils import MISSING
from services.bot import DCSServerBot
from typing import Type, Optional, Union

from .listener import ServerStatsListener
from ..userstats.filter import StatisticsFilter, PeriodFilter, CampaignFilter, MissionFilter, PeriodTransformer, \
    SquadronFilter


class ServerLoadFilter(PeriodFilter):

    def __init__(self, period: str = 'hour'):
        super().__init__(period)

    @staticmethod
    def list(bot: DCSServerBot) -> list[str]:
        return ['hour', 'day', 'week', 'month', 'today', 'yesterday']

    @staticmethod
    def supports(bot: DCSServerBot, period: str) -> bool:
        return (period and period.startswith('period:')) or period in ServerLoadFilter.list(bot) or '-' in period

    def filter(self, bot: DCSServerBot) -> str:
        if self.period == 'yesterday':
            return "DATE_TRUNC('day', time) = current_date - 1"
        elif self.period == 'today':
            return "DATE_TRUNC('day', time) = current_date"
        elif self.period in ServerLoadFilter.list(bot):
            return f"time > ((now() AT TIME ZONE 'utc') - interval '1 {self.period}')"
        elif '-' in self.period:
            start, end = self.period.split('-')
            start = start.strip()
            end = end.strip()
            # avoid SQL injection
            pattern = re.compile(r'^\d+\s+(month|week|day|hour|minute)s?$')
            if pattern.match(end):
                return f"time > ((now() AT TIME ZONE 'utc') - interval '{end}')"
            else:
                start = self.parse_date(start) if start else datetime(year=1970, month=1, day=1)
                end = self.parse_date(end) if end else datetime.now(tz=timezone.utc)
                return (f"time >= '{start.strftime('%Y-%m-%d %H:%M:%S')}'::TIMESTAMP AND "
                        f"COALESCE(time, (now() AT TIME ZONE 'utc')) <= '{end.strftime('%Y-%m-%d %H:%M:%S')}'")
        else:
            return "1 = 1"

class ServerStats(Plugin[ServerStatsListener]):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[ServerStatsListener] = None):
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
                         period: Optional[app_commands.Transform[
                             StatisticsFilter, PeriodTransformer(flt=[ServerLoadFilter])]] = ServerLoadFilter(),
                         ):
        try:
            ephemeral = utils.get_ephemeral(interaction)
            if _server:
                await self.display_report(interaction, 'serverload.json', period, _server, ephemeral=ephemeral)
            else:
                # noinspection PyUnresolvedReferences
                await interaction.response.defer(ephemeral=ephemeral)
                report = PaginationReport(interaction, self.plugin_name, 'serverload.json')
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
            ephemeral = utils.get_ephemeral(interaction)
            if _server:
                await self.display_report(interaction, 'serverstats.json', period, _server, ephemeral=ephemeral)
            else:
                # noinspection PyUnresolvedReferences
                await interaction.response.defer(ephemeral=ephemeral)
                report = PaginationReport(interaction, self.plugin_name, 'serverstats.json')
                await report.render(period=period, server_name=None)
        except ValueNotInRange as ex:
            await interaction.followup.send(ex, ephemeral=utils.get_ephemeral(interaction))

    @command(description='Shows CPU topology')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def cpuinfo(self, interaction: discord.Interaction):
        p_core_affinity_mask = utils.get_p_core_affinity()
        e_core_affinity_mask = utils.get_e_core_affinity()
        buffer = utils.create_cpu_topology_visualization(utils.get_cpus_from_affinity(p_core_affinity_mask),
                                                         utils.get_cpus_from_affinity(e_core_affinity_mask),
                                                         utils.get_cache_info())
        try:
            discord.File(fp=buffer, filename='cpuinfo.png')
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(file=discord.File(fp=buffer, filename='cpuinfo.png'))
        finally:
            buffer.close()

    @tasks.loop(hours=12.0)
    async def cleanup(self):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM serverstats WHERE time < (CURRENT_TIMESTAMP - interval '1 month')")


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    await bot.add_cog(ServerStats(bot, ServerStatsListener))
