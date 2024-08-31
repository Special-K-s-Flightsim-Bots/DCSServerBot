import discord

from core import Plugin, get_translation, Group, Server, utils, Status, UninstallException, InstallException
from discord import app_commands
from extensions.tacview import Tacview as TacviewExt
from services.bot import DCSServerBot
from typing import Optional

_ = get_translation(__name__.split('.')[1])


class Tacview(Plugin):

    # New command group "/tacview"
    tacview = Group(name="tacview", description=_("Commands to manage Tacview"))

    async def _configure(self, interaction: discord.Interaction, *,
                         server: Server,
                         enabled: bool = None,
                         autoupdate: bool = None) -> Optional[dict]:
        config = server.instance.locals.get('extensions', {}).get('Tacview', {
            "tacviewRealTimeTelemetryPort": "42674",
            "tacviewRealTimeTelemetryPassword": "",
            "tacviewRemoteControlEnabled": "42675",
            "tacviewRemoteControlPassword": "",
            "tacviewPlaybackDelay": "0"
        })
        modal = utils.ConfigModal(title=_("Tacview Configuration"),
                                  config=TacviewExt.CONFIG_DICT,
                                  default=config)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return None
        tacviewRealTimeTelemetryPassword = modal.value.get('tacviewRealTimeTelemetryPassword')
        if tacviewRealTimeTelemetryPassword == '.':
            tacviewRealTimeTelemetryPassword = ""
        tacviewRemoteControlPassword = modal.value.get('tacviewRemoteControlPassword')
        if tacviewRemoteControlPassword == '.':
            tacviewRemoteControlPassword = ""
        return {
            "enabled": enabled or config.get('enabled', True),
            "autoupdate": autoupdate or config.get('autoupdate', False),
            "tacviewRealTimeTelemetryPort": modal.value.get('tacviewRealTimeTelemetryPort'),
            "tacviewRealTimeTelemetryPassword": tacviewRealTimeTelemetryPassword,
            "tacviewRemoteControlEnabled": True if modal.value.get('tacviewRemoteControlPort') else False,
            "tacviewRemoteControlPort": modal.value.get('tacviewRemoteControlPort'),
            "tacviewRemoteControlPassword": tacviewRemoteControlPassword,
            "tacviewPlaybackDelay": int(modal.value.get('tacviewPlaybackDelay')),
        }

    @tacview.command(description=_('Configure Tacview'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def configure(self, interaction: discord.Interaction,
                        server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                        enabled: Optional[bool] = None, autoupdate: Optional[bool] = None):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {} needs to be shut down to configure Tacview.").format(server.display_name),
                ephemeral=ephemeral)
            return
        config = await self._configure(interaction, server=server, enabled=enabled, autoupdate=autoupdate)
        if 'Tacview' not in await server.init_extensions():
            await interaction.followup.send(_("Tacview not installed on server {}!").format(server.display_name),
                                            ephemeral=ephemeral)
            return
        await server.config_extension("Tacview", config)
        await interaction.followup.send(
            _("Tacview configuration changed on server {}.").format(server.display_name), ephemeral=ephemeral)

    @tacview.command(name='install', description=_('Install Tacview'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def _install(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                       autoupdate: Optional[bool] = False):
        ephemeral = utils.get_ephemeral(interaction)
        config = await self._configure(interaction, server=server, enabled=True, autoupdate=autoupdate)
        msg = await interaction.followup.send(
            _("Installing Tacview on server {} ...").format(server.display_name), ephemeral=ephemeral)
        if 'Tacview' in await server.init_extensions():
            await msg.edit(content=_("Tacview already installed on server {}!").format(server.display_name))
            return

        if server.status in [Status.STOPPED, Status.SHUTDOWN]:
            try:
                await server.install_extension(name="Tacview", config=config)
                await msg.edit(content=_("Tacview installed on server {}.").format(server.display_name))
            except InstallException:
                await msg.edit(content=_("Tacview could not be installed on server {}!").format(server.display_name))
        else:
            await interaction.followup.send(
                _("Server {} needs to be shut down to install Tacview.").format(server.display_name),
                ephemeral=ephemeral)

    @tacview.command(name='uninstall', description=_('Uninstall Tacview'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def _uninstall(self, interaction: discord.Interaction,
                         server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        msg = await interaction.followup.send(
            _("Uninstalling Tacview on server {} ...").format(server.display_name), ephemeral=ephemeral)
        if 'Tacview' not in await server.init_extensions():
            await msg.edit(content=_("Tacview not installed on server {}!").format(server.display_name))
            return
        if server.status in [Status.STOPPED, Status.SHUTDOWN]:
            try:
                await server.uninstall_extension(name="Tacview")
                await msg.edit(content=_("Tacview uninstalled on server {}.").format(server.display_name))
            except UninstallException:
                await msg.edit(_("Tacview could not be uninstalled on server {}!").format(server.display_name))
        else:
            await interaction.followup.send(
                _("Server {} needs to be shut down to uninstall Tacview.").format(server.display_name),
                ephemeral=ephemeral)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Tacview(bot))
