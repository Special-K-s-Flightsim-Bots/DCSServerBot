import discord

from discord import app_commands
from core import Plugin, PluginRequiredError, TEventListener, PluginInstallationError, Group, get_translation, utils, \
    Server, Coalition, Status
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
        for player in self.eventlistener.srs_users.get(server.name, {}).values():
            if player['side'] != (1 if coalition == 'red' else 2):
                continue
            names += player['player_name'] + "\n"
            _radios = player.get('radios', [])
            radios += ', '.join([utils.format_frequency(x, band=False) for x in _radios[:2]]) + "\n"
        if names:
            embed.add_field(name=_("Name"), value=names)
            embed.add_field(name=_("Radios"), value=radios)
            embed.set_footer(text=_("Only the first 2 radios are displayed per user."))
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(embed=embed)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("No {} players on SRS.").format(coalition))

    @srs.command(description=_('Update DCS-SRS'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def update(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.LOADING, Status.STOPPED, Status.RUNNING, Status.PAUSED])]):
        ephemeral = utils.get_ephemeral(interaction)
        try:
            version = await server.run_on_extension(extension='SRS', method='check_for_updates')
        except ValueError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Extension SRS is not loaded on server {}").format(
                server.display_name), ephemeral=True)
            return
        if version:
            # noinspection PyUnresolvedReferences
            await interaction.response.defer(ephemeral=ephemeral)
            await interaction.followup.send(_("DCS-SRS update to version {} available!").format(version))
            if not utils.yn_question(interaction, _("Do you want to update DCS-SRS now?")):
                await interaction.followup.send(_("Aborted."))
                return
            await server.run_on_extension(extension='SRS', method='do_update')
            await interaction.followup.send(_("DCS-SRS updated to version {}.").format(version))
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("No update for DCS-SRS available."))


async def setup(bot: DCSServerBot):
    if 'missionstats' not in bot.plugins:
        raise PluginRequiredError('missionstats')
    await bot.add_cog(SRS(bot, SRSEventListener))
