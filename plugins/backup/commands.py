import asyncio
import discord
import os
import re

from core import (Plugin, ServiceRegistry, command, utils, Node, YAMLError, get_translation, PluginInstallationError,
                  Status)
from discord import app_commands
from pathlib import Path
from services.bot import DCSServerBot
from services.backup import BackupService

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()

_ = get_translation(__name__.split('.')[1])


async def backup_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        config = interaction.client.cogs['Backup'].locals.get('backups')
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=key.title(), value=key) for key in config.keys()
            if not current or current.casefold() in key.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def date_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    async def get_all_dates(node: Node, target: str) -> list[str]:
        _, all_directories = await node.list_directory(target, is_dir=True, pattern=f"{node.name.lower()}_*")

        date_pattern = re.compile(rf"{node.name.lower()}_([0-9]{{8}})")
        dates = []
        for directory in all_directories:
            match = date_pattern.match(os.path.basename(directory))
            if match:
                dates.append(match.group(1))
        return dates

    if not await interaction.command._check_can_run(interaction):
        return []
    target = interaction.client.cogs['Backup'].locals.get('target')
    node = await utils.NodeTransformer().transform(interaction, utils.get_interaction_param(interaction, "node"))
    return [
        app_commands.Choice(name=date, value=date) for date in await get_all_dates(node, target)
        if not current or current in date
    ][:25]


class Backup(Plugin):
    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.service = ServiceRegistry.get(BackupService)
        if not self.locals:
            raise PluginInstallationError(reason=f"No config/services/{self.plugin_name}.yaml file found!",
                                          plugin=self.plugin_name)

    def read_locals(self) -> dict:
        config_file = os.path.join(self.node.config_dir, 'services', 'backup.yaml')
        if not os.path.exists(config_file):
            return {}
        try:
            return yaml.load(Path(config_file).read_text(encoding='utf-8'))
        except MarkedYAMLError as ex:
            raise YAMLError(config_file, ex)

    @command(description=_('Backup your data'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(what=backup_autocomplete)
    async def backup(self, interaction: discord.Interaction, node: app_commands.Transform[Node, utils.NodeTransformer],
                     what: str):
        ephemeral = utils.get_ephemeral(interaction)
        if what == 'database' and not node.master:
            node = self.node
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        msg = await interaction.followup.send(_("Backup of {} started ...").format(what.title()), ephemeral=ephemeral)
        try:
            rc = await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": BackupService.__name__,
                "method": f"backup_{what}"
            }, node=node.name, timeout=300)
            assert rc['return'] is True
            await msg.edit(content=_("Backup of {} completed.").format(what.title()))
        except Exception:
            await msg.edit(content=_("Backup of {} failed. Please check log for details.").format(what.title()))

    @command(description=_('Restore your data from existing backups'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(what=backup_autocomplete)
    @app_commands.autocomplete(date=date_autocomplete)
    async def restore(self, interaction: discord.Interaction, node: app_commands.Transform[Node, utils.NodeTransformer],
                      what: str, date: str):
        ephemeral = utils.get_ephemeral(interaction)
        if what == 'database' and not node.master:
            node = self.node
        if not await utils.yn_question(interaction,
                                       _("I am about to restore your {what} from {date}.\n"
                                         "This action will completely overwrite any existing data.\n"
                                         "Are you absolutely certain that you want to proceed?").format(
                                           what=what.title(), date=date), ephemeral=ephemeral):
            await interaction.followup.send(_("Aborted."), ephemeral=ephemeral)
            return
        try:
            if what == 'servers':
                tasks = []
                for server in self.bot.servers.values():
                    if server.instance.node.name == node.name and server.status != Status.SHUTDOWN:
                        tasks.append(server.shutdown())
                await asyncio.gather(*tasks, return_exceptions=True)

            await self.bus.send_to_node({
                "command": "rpc",
                "service": BackupService.__name__,
                "method": f"restore_{what}",
                "params": {
                    "date": date
                }
            }, node=node.name)
            await interaction.followup.send(_("Restore of {} triggered. The node will now restart.").format(
                what.title()), ephemeral=ephemeral)
        except Exception:
            await interaction.followup.send(
                _("Recovery of {} failed. Please check log for details.").format(what.title()), ephemeral=ephemeral)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Backup(bot))
