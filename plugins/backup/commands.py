import discord
import os

from core import Plugin, ServiceRegistry, command, utils, Node
from discord import app_commands
from services import DCSServerBot, BackupService
from typing import cast

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


async def backup_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
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
        with open('config/services/backup.yaml') as infile:
            return yaml.load(infile)

    @command(description='Backup your data')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(what=backup_autocomplete)
    async def backup(self, interaction: discord.Interaction, node: app_commands.Transform[Node, utils.NodeTransformer],
                     what: str):
        if what == 'database' and not node.master:
            node = self.bot.node
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Backup",
                "method": f"backup_{what}"
            }, node=node.name)
            await interaction.followup.send(f"Backup of {what} completed.")
        except Exception as ex:
            self.log.exception(ex)
            await interaction.followup.send(f"Backup of {what} failed.")


async def setup(bot: DCSServerBot):
    await bot.add_cog(Backup(bot))
