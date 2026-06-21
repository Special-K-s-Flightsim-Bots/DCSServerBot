import os
import discord
import psycopg
import re

from core import utils, Plugin, PluginRequiredError, Report, PaginationReport, Server, Node, command, Group, \
    ValueNotInRange, ServiceRegistry, PluginInstallationError, get_translation, Status
from datetime import datetime, timezone
from discord import app_commands
from discord.ext import tasks
from discord.utils import MISSING
from pathlib import Path
from psycopg.errors import UniqueViolation
from services.bot import DCSServerBot
from services.monitoring import MonitoringService
from typing import Type

from .listener import MonitoringListener
from ..userstats.filter import StatisticsFilter, PeriodFilter, CampaignFilter, MissionFilter, PeriodTransformer, \
    SquadronFilter

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])


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

    def __init__(self, bot: DCSServerBot, eventlistener: Type[MonitoringListener]):
        super().__init__(bot, eventlistener)
        self._service = None
        self.io_counters = {}
        self.net_io_counters = None

    async def cog_load(self) -> None:
        await super().cog_load()
        self.cleanup.add_exception_type(psycopg.DatabaseError)
        utils.safe_start(self.cleanup)

    async def cog_unload(self):
        await utils.safe_cancel(self.cleanup)
        await super().cog_unload()

    @property
    def service(self) -> MonitoringService:
        if self._service is None:
            self._service = ServiceRegistry.get(MonitoringService)
            if not self._service:
                raise PluginInstallationError(plugin=self.plugin_name, reason="MonitoringService not loaded!")
        return self._service

    async def install(self) -> bool:
        if await super().install():
            try:
                async with self.apool.connection() as conn:
                    cursor = await conn.execute("SELECT version FROM plugins WHERE plugin = 'serverstats'")
                    row = await cursor.fetchone()
                    if row:
                        await conn.execute("UPDATE plugins SET version = %s WHERE plugin = 'monitoring'",
                                           (row[0], ))
                        await conn.execute("DELETE FROM plugins WHERE plugin = 'serverstats'")
                        self.log.info("  => Migrating serverstats to monitoring. Restart triggered ...")
                        await self.node.restart()
                return True
            except UniqueViolation:
                pass
        return False

    async def migrate(self, new_version: str, conn: psycopg.AsyncConnection | None = None) -> None:
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
                                    'message': value.get(
                                        'message',
                                        _('The FPS of server {server.name} are below {min_fps} '
                                          'for longer than {period} minutes!'))
                                }
                            }
                        )
                os.remove(plugin_yaml)
                self.locals = self.read_locals()
            if service:
                yaml.dump(service, Path(service_yaml).open('w', encoding='utf-8'))
                await ServiceRegistry.get(MonitoringService).stop()
                await ServiceRegistry.get(MonitoringService).start()

    def get_config(self, server: Server | None = None, *, plugin_name: str | None = None,
                   use_cache: bool | None = True) -> dict:
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        return self.service.get_config(server)

    async def display_report(self, interaction: discord.Interaction, schema: str, period: str | StatisticsFilter,
                             server: Server, ephemeral: bool):
        await interaction.response.defer(ephemeral=ephemeral)
        report = Report(self.bot, self.plugin_name, schema)
        env = await report.render(period=period, server_name=server.name, node=server.node.name)
        try:
            file = discord.File(fp=env.buffer, filename=env.filename) if env.filename else MISSING
            await interaction.followup.send(embed=env.embed, file=file, ephemeral=ephemeral)
        finally:
            if env.buffer:
                env.buffer.close()

    @command(description=_('Displays the load of your DCS servers'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(_server="server")
    async def serverload(self, interaction: discord.Interaction,
                         _server: app_commands.Transform[Server, utils.ServerTransformer] | None = None,
                         period: app_commands.Transform[
                             StatisticsFilter, PeriodTransformer(flt=[ServerLoadFilter])] = ServerLoadFilter(),
                         ):
        try:
            ephemeral = utils.get_ephemeral(interaction)
            if _server:
                await self.display_report(interaction, 'serverload.json', period, _server, ephemeral=ephemeral)
            else:
                await interaction.response.defer(ephemeral=ephemeral)
                report = PaginationReport(interaction, self.plugin_name, 'serverload.json')
                await report.render(period=period, server_name=None)
        except ValueNotInRange as ex:
            await interaction.followup.send(ex, ephemeral=utils.get_ephemeral(interaction))

    @command(description=_('Shows servers statistics'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.rename(_server="server")
    @app_commands.describe(period='Select one of the default periods or enter the name of a campaign or a mission')
    async def serverstats(self, interaction: discord.Interaction,
                          _server: app_commands.Transform[Server, utils.ServerTransformer] | None,
                          period: app_commands.Transform[
                              StatisticsFilter, PeriodTransformer(
                                  flt=[PeriodFilter, CampaignFilter, MissionFilter, SquadronFilter]
                              )] = PeriodFilter()):
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

    # Create the /ddos command group at module level
    ddos_group = Group(
        name='ddos',
        description=_('DDoS detection and protection commands'),
    )

    @ddos_group.command(name='status', description=_('Shows DDoS detection status and statistics'))
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(_server="server")
    @app_commands.describe(period='Select a time period to view DDoS data')
    async def ddos_status(self, interaction: discord.Interaction,
                          _server: app_commands.Transform[Server, utils.ServerTransformer] | None,
                          period: app_commands.Transform[
                              StatisticsFilter, PeriodTransformer(flt=[PeriodFilter])] = PeriodFilter()):
        ephemeral = utils.get_ephemeral(interaction)
        if not self.service.get_config().get('ddos_detect', False):
            await interaction.response.send_message(
                'DDoS detection is not enabled.\n'
                'Add `ddos_detect: true` to config/services/monitoring.yaml to enable it.',
                ephemeral=ephemeral)
            return
        try:
            if _server:
                await self.display_report(interaction, 'ddos.json', period, _server, ephemeral=ephemeral)
            else:
                await interaction.response.defer(ephemeral=ephemeral)
                report = PaginationReport(interaction, self.plugin_name, 'ddos.json')
                await report.render(period=period, server_name=None)
        except ValueNotInRange as ex:
            await interaction.followup.send(str(ex), ephemeral=ephemeral)

    # Create the /ddos test subgroup
    ddos_test_group = Group(
        name='test',
        description=_('DDoS simulation commands'),
        parent=ddos_group,
    )

    @ddos_test_group.command(name='start', description=_('Simulate a DDoS attack for testing'))
    @utils.app_has_role('Admin')
    @app_commands.rename(_server='server')
    @app_commands.describe(
        _server='Server to simulate DDoS on',
        protocol='Protocol to simulate (udp or tcp)',
        port='Port number (default: server\'s DCS port)',
        duration='Seconds before auto-stop (0 = manual stop only, default: 30)',
    )
    async def ddos_test_start(self, interaction: discord.Interaction,
                              _server: app_commands.Transform[Server, utils.ServerTransformer],
                              protocol: str = 'udp',
                              port: int | None = None,
                              duration: int = 30):
        """Simulate a DDoS attack for testing detection and blocking."""
        await interaction.response.defer(ephemeral=True)

        # Default to server's DCS port if not specified
        if port is None:
            port = _server.instance.dcs_port or 10308

        result = await self.service.simulate_ddos(_server, port, protocol, duration)

        if result['status'] == 'already_active':
            await interaction.followup.send(
                f"⚠️ A DDoS simulation is already active on {_server.name} "
                f"({protocol}/{port}). Use `/ddos test stop` first.",
                ephemeral=True
            )
        else:
            auto = f" (auto-stops in {duration}s)" if duration > 0 else " (manual stop only)"
            await interaction.followup.send(
                f"🔴 DDoS simulation started on {_server.name} ({protocol}/{port}){auto}\n"
                f"Use `/ddos test stop server={_server.name}` to stop early.",
                ephemeral=True
            )

    @ddos_test_group.command(name='stop', description=_('Stop a running DDoS simulation'))
    @utils.app_has_role('Admin')
    @app_commands.rename(_server='server')
    @app_commands.describe(_server='Server to stop simulation on')
    async def ddos_test_stop(self, interaction: discord.Interaction,
                             _server: app_commands.Transform[Server, utils.ServerTransformer]):
        """Stop a running DDoS simulation."""
        await interaction.response.defer(ephemeral=True)

        # Try stopping UDP first, then TCP
        result = await self.service.stop_simulate(_server, 'udp')
        if result['status'] == 'not_found':
            result = await self.service.stop_simulate(_server, 'tcp')

        if result['status'] == 'not_found':
            await interaction.followup.send(f"⚠️ No active DDoS simulation found on {_server.name}.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"🟢 DDoS simulation stopped on {_server.name} ({result['protocol']}/{result['port']}).",
                ephemeral=True
            )

    # Whitelist subgroup
    ddos_whitelist_group = Group(
        name='whitelist',
        description=_('Manage DDoS whitelist'),
        parent=ddos_group,
    )

    @ddos_whitelist_group.command(name='add', description=_('Add an IP to the DDoS whitelist'))
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(ip='IPv4 address to whitelist')
    async def ddos_whitelist_add(self, interaction: discord.Interaction, ip: str):
        """Add an IP to the DDoS whitelist on all nodes."""
        await interaction.response.defer(ephemeral=True)

        # Update config file once (on the master)
        config_path = os.path.join(self.node.config_dir, 'services', 'monitoring.yaml')
        if os.path.exists(config_path):
            data = yaml.load(Path(config_path).read_text(encoding='utf-8'))
            for key in data:
                ddos_cfg = data[key].setdefault('thresholds', {}).setdefault('DDoS', {})
                whitelist = ddos_cfg.setdefault('whitelist', [])
                if ip not in whitelist:
                    whitelist.append(ip)
            with open(config_path, mode='w', encoding='utf-8') as outfile:
                yaml.dump(data, outfile)

        # Propagate to all nodes
        results = []
        for node_name, node in self.bot.node.all_nodes.items():
            if node is None:
                continue
            try:
                result = await self.service.ddos_whitelist(node, ip)
                results.append(f"**{node_name}**: {result}")
            except Exception as ex:
                results.append(f"**{node_name}**: ❌ {ex}")

        await interaction.followup.send(
            f"✅ IP **{ip}** whitelisted on {len(results)} node(s):\n" + "\n".join(results),
            ephemeral=True
        )

    @ddos_whitelist_group.command(name='remove', description=_('Remove an IP from the DDoS whitelist'))
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(ip='IPv4 address to remove from whitelist')
    async def ddos_whitelist_remove(self, interaction: discord.Interaction, ip: str):
        """Remove an IP from the DDoS whitelist on all nodes."""
        await interaction.response.defer(ephemeral=True)

        # Update config file once (on the master)
        config_path = os.path.join(self.node.config_dir, 'services', 'monitoring.yaml')
        if os.path.exists(config_path):
            data = yaml.load(Path(config_path).read_text(encoding='utf-8'))
            for key in data:
                ddos_cfg = data[key].setdefault('thresholds', {}).setdefault('DDoS', {})
                whitelist = ddos_cfg.get('whitelist', [])
                if ip in whitelist:
                    whitelist.remove(ip)
            with open(config_path, mode='w', encoding='utf-8') as outfile:
                yaml.dump(data, outfile)

        # Propagate to all nodes
        results = []
        for node_name, node in self.bot.node.all_nodes.items():
            if node is None:
                continue
            try:
                result = await self.service.ddos_unwhitelist(node, ip)
                results.append(f"**{node_name}**: {result}")
            except Exception as ex:
                results.append(f"**{node_name}**: ❌ {ex}")

        await interaction.followup.send(
            f"✅ IP **{ip}** removed from whitelist on {len(results)} node(s):\n" + "\n".join(results),
            ephemeral=True
        )

    # Blacklist subgroup
    ddos_blacklist_group = Group(
        name='blacklist',
        description=_('Manage DDoS blacklist'),
        parent=ddos_group,
    )

    @ddos_blacklist_group.command(name='add', description=_('Permanently block an IP address'))
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(ip='IPv4 address to block')
    async def ddos_blacklist_add(self, interaction: discord.Interaction, ip: str):
        """Permanently block an IP address via Windows Firewall on all nodes."""
        await interaction.response.defer(ephemeral=True)

        # Propagate to all nodes
        results = []
        for node_name, node in self.bot.node.all_nodes.items():
            if node is None:
                continue
            try:
                result = await self.service.ddos_blacklist(node, ip)
                results.append(f"**{node_name}**: {result}")
            except Exception as ex:
                results.append(f"**{node_name}**: ❌ {ex}")

        await interaction.followup.send(
            f"🔒 IP **{ip}** blocked on {len(results)} node(s):\n" + "\n".join(results),
            ephemeral=True
        )

    @ddos_blacklist_group.command(name='remove', description=_('Remove an IP from the DDoS blacklist'))
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(ip='IPv4 address to unblock')
    async def ddos_blacklist_remove(self, interaction: discord.Interaction, ip: str):
        """Remove an IP from the permanent blocklist on all nodes."""
        await interaction.response.defer(ephemeral=True)

        # Propagate to all nodes
        results = []
        for node_name, node in self.bot.node.all_nodes.items():
            if node is None:
                continue
            try:
                result = await self.service.ddos_unblacklist(node, ip)
                results.append(f"**{node_name}**: {result}")
            except Exception as ex:
                results.append(f"**{node_name}**: ❌ {ex}")

        await interaction.followup.send(
            f"🔓 IP **{ip}** unblocked on {len(results)} node(s):\n" + "\n".join(results),
            ephemeral=True
        )

    @ddos_group.command(name='block', description=_('Manually trigger DDoS blocking for a server or the whole node'))
    @utils.app_has_role('Admin')
    @app_commands.describe(
        node='Node to block (used for node-wide block)',
        server='Server to block (omit for node-wide block)',
        protocols='Which protocol(s) to block',
    )
    @app_commands.choices(protocols=[
        app_commands.Choice(name='TCP + UDP (both)', value='both'),
        app_commands.Choice(name='TCP only', value='tcp'),
        app_commands.Choice(name='UDP only', value='udp'),
    ])
    async def ddos_block(
            self,
            interaction: discord.Interaction,
            node: app_commands.Transform[Node, utils.NodeTransformer] | None = None,
            server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.PAUSED, Status.RUNNING])] | None = None,
            protocols: app_commands.Choice[str] | None = None
    ):
        """Manually trigger DDoS blocking. Requires action=block in config."""
        await interaction.response.defer(ephemeral=True)

        # Confirm before blocking
        if server is None:
            msg = f"Are you sure you want to block ALL running servers on node **{node.name}**?"
        else:
            msg = f"Activate DDoS blocking for **{server.name}**?"
        if not await utils.yn_question(interaction, msg):
            return

        proto_list = [protocols.value] if protocols else None

        if node:
            result = await self.service.activate_node_block(node)
        elif server:
            result = await self.service.activate_ddos_block(server, protocols=proto_list)
        else:
            await interaction.followup.send(_("No server or node specified"), ephemeral=True)
            return
        await interaction.followup.send(f"🔒 {result}", ephemeral=True)

    @ddos_group.command(name='unblock', description=_('Deactivate DDoS blocking for a server or the whole node'))
    @utils.app_has_role('Admin')
    @app_commands.describe(
        node='Node to unblock (used for node-wide unblock)',
        server='Server to unblock (omit for node-wide unblock)',
    )
    async def ddos_unblock(
            self,
            interaction: discord.Interaction,
            node: app_commands.Transform[Node, utils.NodeTransformer] | None = None,
            server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.PAUSED, Status.RUNNING])] | None = None
    ):
        """Manually deactivate DDoS blocking. Requires action=block in config."""
        await interaction.response.defer(ephemeral=True)

        # Confirm before unblocking
        if node:
            msg = f"Deactivate DDoS blocking for ALL servers on node **{node.name}**?"
        elif server:
            msg = f"Deactivate DDoS blocking for **{server.name}**?"
        else:
            await interaction.followup.send(_("No server or node specified"), ephemeral=True)
            return

        if not await utils.yn_question(interaction, msg):
            return

        if node:
            result = await self.service.deactivate_node_block(node)
        else:
            result = await self.service.deactivate_ddos_block(server)
        await interaction.followup.send(f"🔓 {result}", ephemeral=True)

    @tasks.loop(hours=12.0)
    async def cleanup(self):
        async with self.apool.connection() as conn:
            await conn.execute("DELETE FROM serverstats WHERE time < (CURRENT_TIMESTAMP - interval '1 month')")
            await conn.execute("DELETE FROM port_traffic WHERE time < (CURRENT_TIMESTAMP - interval '1 month')")


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    await bot.add_cog(Monitoring(bot, MonitoringListener))
