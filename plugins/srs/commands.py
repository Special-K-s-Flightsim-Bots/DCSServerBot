import discord

from discord import app_commands
from core import Plugin, PluginRequiredError, TEventListener, PluginInstallationError, Group, get_translation, utils, \
    Server, Coalition, Status, Side
from services import DCSServerBot
from typing import Type, Literal

from .listener import SRSEventListener

_ = get_translation(__name__.split('.')[1])


class SRS(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not self.get_config(plugin_name='missionstats').get('enabled', True):
            raise PluginInstallationError(plugin=self.plugin_name, reason="MissionStats not enabled!")

    srs = Group(name="srs", description=_("Commands to manage DCS-SRS"))

    @srs.command(name="list", description=_('List of active SRS users'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    coalition: Literal['blue', 'red']):
        sides = utils.get_sides(interaction.client, interaction, server)
        if Coalition(coalition) not in sides:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("You are not allowed to see the {} players.").format(coalition))
            return
        embed = discord.Embed(color=discord.Color.blue())
        embed.title = _("{} Players on SRS").format(coalition.title())
        names = ""
        radios = ""
        for player in server.get_active_players(side=Side.BLUE if coalition == 'blue' else Side.RED):
            names += player.display_name + "\n"
            _radios = self.eventlistener.srs_users.get(server.name, {}).get(player.name, {}).get('radios', [])
            radios += ', '.join([utils.format_frequency(x, band=False) for x in _radios[:2]]) + "\n"
        if names:
            embed.add_field(name=_("DCS-Name"), value=names)
            embed.add_field(name=_("Radios"), value=radios)
            embed.set_footer(text=_("Only the first 2 radios are displayed."))
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(embed=embed)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("No {} players on SRS.").format(coalition))


async def setup(bot: DCSServerBot):
    if 'missionstats' not in bot.plugins:
        raise PluginRequiredError('missionstats')
    await bot.add_cog(SRS(bot, SRSEventListener))
