import asyncio
import discord

from datetime import datetime
from discord import app_commands
from core import Plugin, utils, Server, Coalition, Report, Status, Group
from services import DCSServerBot
from typing import Optional


class ECW(Plugin):
    # New command group "/ecw"
    ecw = Group(name="ecw", description="ECW-specific commands")

    @ecw.command(description='Restart the ECW mission')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def reload(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.RUNNING, Status.PAUSED])]):
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Server {server.display_name} is not running.", ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        server.sendPopupMessage(Coalition.ALL, "!!!Staff has triggered a mission restart.!!!\n"
                                               "A tick will be performed followed by a restart in 30s.", 30)
        server.send_to_dcs({
            "command": "do_script",
            "script": "timer.setFunctionTime(te.id , timer.getTime() + 1)"
        })
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message("Players warned, waiting 30s to restart mission.", ephemeral=ephemeral)
        await asyncio.sleep(30)
        await server.restart()
        await interaction.followup.send("Mission restarted")
        
    @ecw.command(description="A/G stats, params from 'YYYY-MM-DD' to 'YYYY-MM-DD'")
    @utils.app_has_role('Admin')
    @app_commands.guild_only()
    @app_commands.rename(_from="from")
    async def aircraftstatsag(self, interaction: discord.Interaction, _from: str, to: Optional[str] = None):
        try:
            hop_on = datetime.strptime(_from, '%Y-%m-%d')
            hop_off = datetime.strptime(to, '%Y-%m-%d') if to else datetime.now()
        except ValueError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Dates have to be in format YYYY-MM-DD!", ephemeral=True)
            return
        report = Report(self.bot, self.plugin_name, 'aircraftstatsag.json')
        env = await report.render(hop_off=hop_off, hop_on=hop_on)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=env.embed, ephemeral=utils.get_ephemeral(interaction))
        
    @ecw.command(description="PvP stats, params from 'YYYY-MM-DD' to 'YYYY-MM-DD'")
    @utils.app_has_role('Admin')
    @app_commands.guild_only()
    @app_commands.rename(_from="from")
    async def aircraftstatspvp(self, interaction: discord.Interaction, _from: str, to: Optional[str] = None):
        try:
            hop_on = datetime.strptime(_from, '%Y-%m-%d')
            hop_off = datetime.strptime(to, '%Y-%m-%d') if to else datetime.now()
        except ValueError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Dates have to be in format YYYY-MM-DD!", ephemeral=True)
            return
        report = Report(self.bot, self.plugin_name, 'aircraftstatspvp.json')
        env = await report.render(hop_off=hop_off, hop_on=hop_on)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=env.embed, ephemeral=utils.get_ephemeral(interaction))


async def setup(bot: DCSServerBot):
    await bot.add_cog(ECW(bot))
