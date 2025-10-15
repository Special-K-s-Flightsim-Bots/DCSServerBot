import discord

from core import (Plugin, PluginRequiredError, PluginInstallationError, Group, get_translation, utils, Server,
                  Coalition, Status)
from discord import app_commands
from extensions.srs import SRS as SRSExt
from services.bot import DCSServerBot
from typing import Type, Literal

from .listener import SRSEventListener

_ = get_translation(__name__.split('.')[1])


class SRS(Plugin[SRSEventListener]):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[SRSEventListener] = None):
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
            await interaction.response.send_message(
                _("You are not allowed to see the {} players.").format(coalition), ephemeral=True
            )
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
            await interaction.response.send_message(
                embed=embed, delete_after=self.bot.locals.get('message_autodelete')
            )
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("No {} players on SRS.").format(coalition), ephemeral=True)

    @srs.command(description=_('Update DCS-SRS'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
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
            if not await utils.yn_question(
                    interaction,
                    question=_("Do you want to update DCS-SRS now?"),
                    message=_("This will terminate all DCS-SRS servers on node {}!").format(server.node.name)
            ):
                await interaction.followup.send(_("Aborted."))
                return
            try:
                await server.run_on_extension(extension='SRS', method='do_update', version=version)
                await interaction.followup.send(_("DCS-SRS updated to version {}.").format(version))
            except Exception:
                await interaction.followup.send(_("Failed to update DCS-SRS. See log for defails."))
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("No update for DCS-SRS available."))

    async def _configure(self, interaction: discord.Interaction,
                         server: Server,
                         enabled: bool = None,
                         autoconnect: bool = None) -> dict | None:
        config = server.instance.locals.get('extensions', {}).get('SRS', {})
        modal = utils.ConfigModal(title=_("SRS Configuration"),
                                  config=SRSExt.CONFIG_DICT,
                                  old_values=config)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return None
        blue_password = modal.value.get('blue_password')
        if blue_password == '.':
            blue_password = ""
        red_password = modal.value.get('red_password')
        if red_password == '.':
            red_password = ""
        return {
            "enabled": enabled or config.get('enabled', True),
            "autoconnect": autoconnect or config.get('autoconnect', True),
            "port": int(modal.value.get('port')),
            "blue_password": blue_password,
            "red_password": red_password
        }

    @srs.command(description=_('Configure SRS'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('DCS Admin')
    async def configure(self, interaction: discord.Interaction,
                        server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                        enabled: bool | None = None, autoconnect: bool | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        if 'SRS' not in await server.init_extensions():
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("SRS not installed on server {}").format(server.display_name), ephemeral=True)
            return
        if server.status in [Status.STOPPED, Status.SHUTDOWN]:
            config = await self._configure(interaction, server, enabled, autoconnect)
            await server.config_extension("SRS", config)
            await interaction.followup.send(
                _("SRS configuration changed on server {}.").format(server.display_name), ephemeral=ephemeral)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {} needs to be shut down to configure SRS.").format(server.display_name),
                ephemeral=True)

    @srs.command(description=_('Repair DCS-SRS'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('DCS Admin')
    async def repair(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.LOADING, Status.STOPPED, Status.RUNNING, Status.PAUSED])]):
        ephemeral = utils.get_ephemeral(interaction)
        try:
            data = await server.run_on_extension(extension='SRS', method='render')
        except ValueError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Extension SRS is not loaded on server {}").format(
                server.display_name), ephemeral=True)
            return
        except NotImplementedError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Extension SRS is not active on server {}").format(
                server.display_name), ephemeral=True)
            return

        version = data['version']
        if not await utils.yn_question(
                interaction,
                question=_("Do you want to repair DCS-SRS now?"),
                message=_("This will terminate all DCS-SRS servers on node {}!").format(server.node.name)
        ):
            await interaction.followup.send(_("Aborted."))
            return
        try:
            await server.run_on_extension(extension='SRS', method='do_update', version=version)
            await interaction.followup.send(_("DCS-SRS repaired on node {}.").format(server.node.name))
        except Exception:
            await interaction.followup.send(_("Failed to update DCS-SRS. See log for defails."))


async def setup(bot: DCSServerBot):
    if 'missionstats' not in bot.plugins:
        raise PluginRequiredError('missionstats')
    await bot.add_cog(SRS(bot, SRSEventListener))
