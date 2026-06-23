import os
import discord
import psycopg

from core import Plugin, utils, ServiceRegistry, PluginInstallationError, Group, get_translation, Server, \
    PaginationReport, ValueNotInRange, Node, Status, Report, PluginRequiredError
from discord import app_commands
from discord.ext import tasks
from discord.utils import MISSING
from pathlib import Path
from plugins.userstats.filter import StatisticsFilter, PeriodTransformer, PeriodFilter

from services.bot import DCSServerBot
from services.firewall import FirewallService

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])


class Firewall(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self._service = None

    async def cog_load(self) -> None:
        await super().cog_load()
        self.cleanup.add_exception_type(psycopg.DatabaseError)
        utils.safe_start(self.cleanup)

    async def cog_unload(self):
        await utils.safe_cancel(self.cleanup)
        await super().cog_unload()

    @property
    def service(self) -> FirewallService:
        if self._service is None:
            self._service = ServiceRegistry.get(FirewallService)
            if not self._service:
                raise PluginInstallationError(plugin=self.plugin_name, reason="FirewallService not loaded!")
        return self._service

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
        if not self.service.get_config().get('ddos_detection', {}).get('enabled', False):
            await interaction.response.send_message(
                'DDoS detection is not enabled.\n'
                'Add `ddos_detection.enabled: true` to config/services/firewall.yaml to enable it.',
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
        config_path = os.path.join(self.node.config_dir, 'services', 'firewall.yaml')
        if os.path.exists(config_path):
            data = yaml.load(Path(config_path).read_text(encoding='utf-8'))
            for key in data:
                ddos_cfg = data[key].setdefault('ddos_detection', {})
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
        config_path = os.path.join(self.node.config_dir, 'services', 'firewall.yaml')
        if os.path.exists(config_path):
            data = yaml.load(Path(config_path).read_text(encoding='utf-8'))
            for key in data:
                ddos_cfg = data[key].get('ddos_detection', {})
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
    await bot.add_cog(Firewall(bot))
