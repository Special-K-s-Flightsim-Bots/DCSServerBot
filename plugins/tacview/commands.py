import discord
import os

from core import Plugin, get_translation, Group, Server, utils, Status, UninstallException, InstallException
from datetime import datetime, timezone
from discord import app_commands
from extensions.tacview import Tacview as TacviewExt, TACVIEW_DEFAULT_DIR
from io import BytesIO
from services.bot import DCSServerBot

_ = get_translation(__name__.split('.')[1])


async def list_tacview_files(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        server: Server = await utils.ServerTransformer().transform(
            interaction, utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        config = (server.node.locals.get('extensions', {}).get('Tacview', {}) |
                  server.instance.locals.get('extensions', {}).get('Tacview', {}))
        path = config.get('tacviewExportPath', TACVIEW_DEFAULT_DIR)
        # single file per player
        if config.get('tacviewMultiplayerFlightsAsHost', 2) == 3:
            ucid = await interaction.client.get_ucid_by_member(interaction.user)
            if ucid:
                async with interaction.client.apool.connection() as conn:
                    cursor = await conn.execute("SELECT name FROM players WHERE ucid = %s", (ucid, ))
                    row = await cursor.fetchone()
                    if row:
                        name = row[0]
                path, files = await server.node.list_directory(os.path.join(path, name),
                                                            pattern='*.acmi', is_dir=False)
            else:
                files = []
        else:
            path, files = await server.node.list_directory(path, pattern='*.acmi', is_dir=False, traverse=True)

        # file per session
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=os.path.relpath(x, path), value=os.path.relpath(x, path))
            for x in files
            if not current or current.casefold() in x.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


class Tacview(Plugin):
    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.recorder = None

    # New command group "/tacview"
    tacview = Group(name="tacview", description=_("Commands to manage Tacview"))

    async def _configure(self, interaction: discord.Interaction, *,
                         server: Server,
                         enabled: bool = None,
                         autoupdate: bool = None) -> dict | None:
        config = server.instance.locals.get('extensions', {}).get('Tacview', {})
        modal = utils.ConfigModal(title=_("Tacview Configuration"),
                                  config=TacviewExt.CONFIG_DICT,
                                  old_values=config)
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
        ret = {
            "enabled": enabled or config.get('enabled', True),
            "autoupdate": autoupdate or config.get('autoupdate', False),
            "tacviewRealTimeTelemetryPort": modal.value.get('tacviewRealTimeTelemetryPort'),
            "tacviewRealTimeTelemetryPassword": tacviewRealTimeTelemetryPassword,
            "tacviewPlaybackDelay": int(modal.value.get('tacviewPlaybackDelay')),
        }
        if modal.value.get('tacviewRemoteControlPort'):
            ret |= {
                "tacviewRemoteControlEnabled": True,
                "tacviewRemoteControlPort": modal.value.get('tacviewRemoteControlPort'),
                "tacviewRemoteControlPassword": tacviewRemoteControlPassword
            }
        else:
            ret |= {
                "tacviewRemoteControlEnabled": False
            }
        return ret

    @tacview.command(description=_('Configure Tacview'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('DCS Admin')
    async def configure(self, interaction: discord.Interaction,
                        server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                        enabled: bool | None = None, autoupdate: bool | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {} needs to be shut down to configure Tacview.").format(server.display_name),
                ephemeral=True)
            return
        config = await self._configure(interaction, server=server, enabled=enabled, autoupdate=autoupdate)
        if 'Tacview' not in await server.init_extensions():
            await interaction.followup.send(_("Tacview not installed on server {}!").format(server.display_name),
                                            ephemeral=True)
            return
        await server.config_extension("Tacview", config)
        await interaction.followup.send(
            _("Tacview configuration changed on server {}.").format(server.display_name), ephemeral=ephemeral)

    @tacview.command(name='install', description=_('Install Tacview'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def _install(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                       autoupdate: bool | None = False):
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
                ephemeral=True)

    @tacview.command(name='download', description=_('Download a Tacview'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(file=list_tacview_files)
    async def download(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer],
                       file: str):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        config = (server.node.locals.get('extensions', {}).get('Tacview', {}) |
                  server.instance.locals.get('extensions', {}).get('Tacview', {}))
        path = config.get('tacviewExportPath', TACVIEW_DEFAULT_DIR)
        file_data = await self.node.read_file(os.path.join(path, file))
        await interaction.followup.send(file=discord.File(fp=BytesIO(file_data), filename=os.path.basename(file)))

    @tacview.command(name='record_start', description=_('Start realtime recording'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def record_start(self, interaction: discord.Interaction,
                           server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                           filename: str | None = "recording-Tacview-{ts}-{mission}"):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        filename = utils.format_string(
            filename,
            ts=datetime.now().astimezone(tz=timezone.utc).strftime("%Y%m%d_%H%M%S"),
            mission=utils.slugify(server.current_mission.name)
        )
        if not filename.endswith(".acmi"):
            filename = filename + ".acmi"
        try:
            await server.run_on_extension(extension='Tacview', method='start_recording', filename=filename)
            await interaction.followup.send(_("Tacview recording started."))
        except Exception as ex:
            await interaction.followup.send(str(ex))

    @tacview.command(name='record_stop', description=_('Stop realtime recording'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def record_stop(self, interaction: discord.Interaction,
                          server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        try:
            filename = await server.run_on_extension(extension='Tacview', method='stop_recording')
            await interaction.followup.send(_("Tacview recording stopped, file {} written.").format(filename))
        except Exception as ex:
            await interaction.followup.send(str(ex))


async def setup(bot: DCSServerBot):
    await bot.add_cog(Tacview(bot))
