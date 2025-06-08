import os
import discord
import psycopg
import re

from core import utils, Plugin, PluginRequiredError, Report, PaginationReport, Server, command, \
    ValueNotInRange, ServiceRegistry
from datetime import datetime, timezone
from discord import app_commands
from discord.ext import tasks
from discord.utils import MISSING
from pathlib import Path
from psycopg.errors import UniqueViolation
from services.bot import DCSServerBot
from services.monitoring import MonitoringService
from typing import Type, Optional, Union

from .listener import MonitoringListener
from ..userstats.filter import StatisticsFilter, PeriodFilter, CampaignFilter, MissionFilter, PeriodTransformer, \
    SquadronFilter

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


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

class Monitoring(Plugin[MonitoringListener]):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[MonitoringListener] = None):
        super().__init__(bot, eventlistener)
        self.service = ServiceRegistry.get(MonitoringService)
        self.io_counters = {}
        self.net_io_counters = None

    async def cog_load(self) -> None:
        await super().cog_load()
        self.cleanup.add_exception_type(psycopg.DatabaseError)
        self.cleanup.start()

    async def cog_unload(self):
        self.cleanup.cancel()
        await super().cog_unload()

    async def install(self) -> bool:
        if await super().install():
            try:
                async with self.apool.connection() as conn:
                    await conn.execute("""
                        UPDATE plugins 
                        SET version = (
                            SELECT version FROM plugins WHERE plugin = 'serverstats'
                        ) 
                        WHERE plugin = 'monitoring'
                        """)
                    cursor = await conn.execute("DELETE FROM plugins WHERE plugin = 'serverstats' RETURNING plugin")
                    row = await cursor.fetchone()
                    if row:
                        self.log.info("  => Migrating serverstats to monitoring. Restart triggered ...")
                        await self.node.restart()
                return True
            except UniqueViolation:
                pass
        return False

    async def migrate(self, new_version: str, conn: Optional[psycopg.AsyncConnection] = None) -> None:
        if new_version == '3.3':
            # Check if we had serverstats loaded in main.yaml and if yes, remove it.
            config = os.path.join(self.node.config_dir, 'main.yaml')
            data = yaml.load(Path(config).read_text(encoding='utf-8'))
            if 'serverstats' in data.get('opt_plugins', []):
                data['opt_plugins'].remove('serverstats')
                if not data['opt_plugins']:
                    del data['opt_plugins']
                with open(config, mode='w', encoding='utf-8') as outfile:
                    yaml.dump(data, outfile)
            # Rewrite the configuration
            service_yaml = os.path.join(self.node.config_dir, 'services', 'monitoring.yaml')
            if os.path.exists(service_yaml):
                service = yaml.load(Path(service_yaml).read_text(encoding='utf-8'))
            else:
                service = {}
            for key, value in service.copy().items():
                if 'drive_warn_threshold' in value:
                    service[key].setdefault('thresholds', {}).update(
                        {
                            'Drive': {
                                'warn': value.get('drive_warn_threshold', 10),
                                'alert': value.get('drive_alert_threshold', 5)
                            }
                        }
                    )
                    service[key].pop('drive_warn_threshold', None)
                    service[key].pop('drive_alert_threshold', None)
            plugin_yaml = os.path.join(self.node.config_dir, 'plugins', 'serverstats.yaml')
            if os.path.exists(plugin_yaml):
                plugin = yaml.load(Path(plugin_yaml).read_text(encoding='utf-8'))
                for key, value in plugin.items():
                    if key not in service:
                        service[key] = {}
                    if 'min_fps' in value:
                        service[key].setdefault('thresholds', {}).update(
                            {
                                'FPS': {
                                    'min': value.get('min_fps', 30),
                                    'period': value.get('period', 5),
                                    'mentioning': value.get('mentioning', True),
                                    'message': value.get('message', 'The FPS of server {server.name} are below {min_fps} for longer than {period} minutes!')
                                }
                            }
                        )
                os.remove(plugin_yaml)
            if service:
                yaml.dump(service, Path(service_yaml).open('w', encoding='utf-8'))

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: Optional[str] = None) -> None:
        self.log.debug('Pruning Monitoring ...')
        if server:
            await conn.execute("DELETE FROM serverstats WHERE server_name = %s", (server, ))
        self.log.debug('Monitoring pruned.')

    async def rename(self, conn: psycopg.AsyncConnection, old_name: str, new_name: str):
        await conn.execute('UPDATE serverstats SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    def get_config(self, server: Optional[Server] = None, *, plugin_name: Optional[str] = None,
                   use_cache: Optional[bool] = True) -> dict:
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        return self.service.get_config(server)

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

    @tasks.loop(hours=12.0)
    async def cleanup(self):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM serverstats WHERE time < (CURRENT_TIMESTAMP - interval '1 month')")


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    await bot.add_cog(Monitoring(bot, MonitoringListener))
