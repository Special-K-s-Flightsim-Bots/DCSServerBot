import discord
import os

from core import Plugin, get_translation, Group, Server, utils, Status, UninstallException, InstallException
from datetime import datetime, timezone
from discord import app_commands
from extensions.tacview import TACVIEW_DEFAULT_DIR
from io import BytesIO
from services.bot import DCSServerBot

_ = get_translation(__name__.split('.')[1])


async def list_tacview_files(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        server: Server = await utils.ServerTransformer().transform(interaction, interaction.namespace.server)
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

    @tacview.command(name='download', description=_('Download a Tacview'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(file=list_tacview_files)
    async def download(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer],
                       file: str):
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(ephemeral=ephemeral)
        ext_config = (server.node.locals.get('extensions', {}).get('Tacview', {}) |
                      server.instance.locals.get('extensions', {}).get('Tacview', {}))
        path = ext_config.get('tacviewExportPath', TACVIEW_DEFAULT_DIR)
        config = self.get_config(server)
        file_data = await self.node.read_file(os.path.join(path, file))
        if config.get('upload', {}).get('channel'):
            channel_id = config['upload']['channel']
            if channel_id == -1:
                channel = await interaction.user.create_dm()
                await channel.send(file=discord.File(fp=BytesIO(file_data), filename=os.path.basename(file)))
                await interaction.followup.send(_("Tacview recording sent in a DM"), ephemeral=ephemeral)
            else:
                channel = self.bot.get_channel(channel_id)
                await channel.send(file=discord.File(fp=BytesIO(file_data), filename=os.path.basename(file)))
                await interaction.followup.send(_("Tacview recording uploaded to channel {}").format(channel.mention),
                                                ephemeral=ephemeral)
        else:
            await interaction.followup.send(file=discord.File(fp=BytesIO(file_data), filename=os.path.basename(file)))

    @tacview.command(name='record_start', description=_('Start realtime recording'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def record_start(self, interaction: discord.Interaction,
                           server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                           filename: str | None = "recording-Tacview-{ts}-{mission}"):
        ephemeral = utils.get_ephemeral(interaction)
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
        await interaction.response.defer(ephemeral=ephemeral)
        try:
            filename = await server.run_on_extension(extension='Tacview', method='stop_recording')
            await interaction.followup.send(_("Tacview recording stopped, file {} written.").format(filename))
        except Exception as ex:
            await interaction.followup.send(str(ex))


async def setup(bot: DCSServerBot):
    await bot.add_cog(Tacview(bot))
