import discord
import os

from core import Plugin, ServiceRegistry, command, utils, Node, YAMLError
from discord import app_commands
from pathlib import Path
from services import DCSServerBot, BackupService
from typing import cast

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError
yaml = YAML()


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


class Backup(Plugin):
    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.service = cast(BackupService, ServiceRegistry.get("Backup"))

    def read_locals(self) -> dict:
        if not os.path.exists('config/services/backup.yaml'):
            return {}
        try:
            return yaml.load(Path('config/services/backup.yaml').read_text(encoding='utf-8'))
        except (ParserError, ScannerError) as ex:
            raise YAMLError('config/services/backup.yaml', ex)

    @command(description='Backup your data')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(what=backup_autocomplete)
    async def backup(self, interaction: discord.Interaction, node: app_commands.Transform[Node, utils.NodeTransformer],
                     what: str):
        ephemeral = utils.get_ephemeral(interaction)
        if what == 'database' and not node.master:
            node = self.node
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        try:
            rc = await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Backup",
                "method": f"backup_{what}"
            }, node=node.name, timeout=120)
            assert rc['return'] is True
            await interaction.followup.send(f"Backup of {what} completed.", ephemeral=ephemeral)
        except Exception:
            await interaction.followup.send(f"Backup of {what} failed. Please check log for details",
                                            ephemeral=ephemeral)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Backup(bot))
