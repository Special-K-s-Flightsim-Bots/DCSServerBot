import aiofiles
import aiohttp
import discord
import json
import os

from core import Plugin, utils, Server, get_translation, Group, Coalition, Status, InstallException, UninstallException, \
    ServerUploadHandler
from discord import app_commands
from discord.ext import commands
from extensions.lotatc import LotAtc as LotAtcExt
from jsonschema import validate, ValidationError
from services.bot import DCSServerBot
from typing import Literal

from .listener import LotAtcEventListener

LOTATC_DIR = r"Mods\services\LotAtc\userdb\transponders\{}"

_ = get_translation(__name__.split('.')[1])


async def gci_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction, interaction.namespace.server)
        if not server:
            return []
        coalition: str = interaction.namespace.coalition
        listener = interaction.client.cogs['LotAtc'].eventlistener
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x, value=x)
            for x in listener.on_station.get(server.name, {}).get(coalition, {}).keys()
            if not current or current.casefold() in x.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


class LotAtc(Plugin[LotAtcEventListener]):

    @staticmethod
    def lotatc_server_filter(server: Server) -> bool:
        extensions = server.instance.locals.get('extensions')
        return 'LotAtc' in extensions if extensions is not None else False

    # New command group "/lotatc"
    lotatc = Group(name="lotatc", description=_("Commands to manage LotAtc"))

    @lotatc.command(description=_('Update LotAtc'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('DCS Admin')
    async def update(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.STOPPED, Status.SHUTDOWN])]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if 'LotAtc' not in await server.init_extensions():
            await interaction.followup.send(_("Extension LotAtc is not configured for server {}").format(
                server.display_name), ephemeral=True)
            return

        version = await server.run_on_extension(extension='LotAtc', method='check_for_updates')
        update_done = False
        if version:
            await interaction.followup.send(_("LotAtc update to version {} available!").format(version),
                                            ephemeral=ephemeral)
            if not await utils.yn_question(interaction, _("Do you want to update LotAtc now?"), ephemeral=ephemeral):
                await interaction.followup.send(_("Aborted."), ephemeral=ephemeral)
                return
            await server.run_on_extension(extension='LotAtc', method='do_update')
            update_done = True
            await interaction.followup.send(_("LotAtc updated to version {}.").format(version), ephemeral=ephemeral)

        if server.status in [Status.STOPPED, Status.SHUTDOWN]:
            if await server.run_on_extension(extension='LotAtc', method='update_instance', force=True):
                update_done = True
                await interaction.followup.send(
                    _("LotAtc updated in server {}.").format(server.display_name), ephemeral=ephemeral)
        else:
            await interaction.followup.send(
                _("Server {} needs to be shut down to update LotAtc.").format(server.display_name),
                ephemeral=ephemeral)

        if not update_done:
            await interaction.followup.send(_("No update for LotAtc available."), ephemeral=ephemeral)

    async def _configure(self, interaction: discord.Interaction,
                         server: Server,
                         enabled: bool = None,
                         autoupdate: bool = None) -> dict | None:
        config = server.instance.locals.get('extensions', {}).get('LotAtc', {})
        modal = utils.ConfigModal(title=_("LotAtc Configuration"),
                                  config=LotAtcExt.CONFIG_DICT,
                                  old_values=config)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return None
        return {
            "enabled": enabled or config.get('enabled', True),
            "autoupdate": autoupdate or config.get('autoupdate', False),
            "port": int(modal.value.get('port'))
        }

    @lotatc.command(description=_('Configure LotAtc'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('DCS Admin')
    async def configure(self, interaction: discord.Interaction,
                        server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                        enabled: bool | None = None, autoupdate: bool | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        if 'LotAtc' not in await server.init_extensions():
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("LotAtc not installed on server {}").format(server.display_name), ephemeral=ephemeral)
            return
        if server.status in [Status.STOPPED, Status.SHUTDOWN]:
            config = await self._configure(interaction, server, enabled, autoupdate)
            await server.config_extension("LotAtc", config)
            await interaction.followup.send(
                _("LotAtc configuration changed on server {}.").format(server.display_name), ephemeral=ephemeral)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {} needs to be shut down to configure LotAtc.").format(server.display_name),
                ephemeral=ephemeral)

    @lotatc.command(name='install', description=_('Install LotAtc'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def _install(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                       autoupdate: bool | None = False):
        ephemeral = utils.get_ephemeral(interaction)
        config = await self._configure(interaction, server, True, autoupdate)
        msg = await interaction.followup.send(_("Installing LotAtc on server {} ...").format(server.display_name),
                                              ephemeral=ephemeral)

        if 'LotAtc' in await server.init_extensions():
            await msg.edit(content=_("LotAtc already installed on server {}").format(server.display_name))
            return

        if 'LotAtc' not in server.node.extensions:
            await msg.edit(content=_("LotAtc is not configured on node {}").format(server.node.name))
            return

        if server.status in [Status.STOPPED, Status.SHUTDOWN]:
            try:
                await server.install_extension(name="LotAtc", config=config)
                await msg.edit(content=_("LotAtc installed on server {}.").format(server.display_name))
            except InstallException:
                await msg.edit(content=_("LotAtc could not be installed on server {}!").format(server.display_name))
        else:
            await msg.edit(content=_("Server {} needs to be shut down to install LotAtc.").format(server.display_name))

    @lotatc.command(name='uninstall', description=_('Uninstall LotAtc'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def _uninstall(self, interaction: discord.Interaction,
                         server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Uninstalling LotAtc from server {} ...").format(server.display_name),
                                                ephemeral=ephemeral)
        msg = await interaction.original_response()

        if 'LotAtc' not in await server.init_extensions():
            await msg.edit(content=_("LotAtc not installed on server {}").format(server.display_name))
            return
        if server.status in [Status.STOPPED, Status.SHUTDOWN]:
            try:
                await server.uninstall_extension(name="LotAtc")
                await msg.edit(content=_("LotAtc uninstalled on server {}.").format(server.display_name))
            except UninstallException:
                await msg.edit(content=_("LotAtc could not be uninstalled on server {}!").format(server.display_name))
        else:
            await msg.edit(content=_("Server {} needs to be shut down to uninstall LotAtc.").format(server.display_name))

    # New command group "/gci"
    gci = Group(name="gci", description=_("Commands to manage GCIs"))

    @gci.command(description=_('Info about a GCI'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(gci=gci_autocomplete)
    async def info(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   coalition: Literal['blue', 'red'], gci: str):
        sides = utils.get_sides(interaction.client, interaction, server)
        if Coalition(coalition) not in sides:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("You are not allowed to see the {} GCIs.").format(coalition))
            return
        gcis = self.eventlistener.on_station.get(server.name, {}).get(Coalition(coalition), {})
        if not gcis:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("There are no {coalition} GCIs active in server {server}").format(
                    coalition=coalition, server=server.name), ephemeral=True)
            return
        elif gci not in gcis:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("GCI {} not found.").format(gci), ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=self.eventlistener.create_gci_embed(gcis[gci]))

    @gci.command(name="list", description=_('List of GCIs'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    coalition: Literal['blue', 'red']):
        sides = utils.get_sides(interaction.client, interaction, server)
        if Coalition(coalition) not in sides:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("You are not allowed to see the {} GCIs.").format(coalition))
            return
        gcis = self.eventlistener.on_station.get(server.name, {}).get(Coalition(coalition), {})
        if not gcis:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("There are no {coalition} GCIs active in server {server}").format(
                    coalition=coalition, server=server.name), ephemeral=True)
            return
        embed = discord.Embed(color=discord.Color.blue())
        embed.title = _("List of active {} GCIs").format(coalition)
        embed.description = _("Server: {}").format(server.display_name)
        embed.add_field(name="Name", value='\n'.join(gcis.keys()))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        pattern = [r'\.json$']
        if not ServerUploadHandler.is_valid(message, pattern=pattern, roles=self.bot.roles['DCS Admin']):
            return
        try:
            server = await ServerUploadHandler.get_server(message, filter_func=self.lotatc_server_filter)
            if not server:
                await message.channel.send(_("LotAtc is not configured on any server."))
                return

            handler = ServerUploadHandler(server=server, message=message, pattern=pattern)
            async with aiofiles.open('plugins/lotatc/schemas/lotatc_schema.json', mode='r') as infile:
                schema = json.loads(await infile.read())

            for attachment in message.attachments:
                async with aiohttp.ClientSession() as session:
                    async with session.get(message.attachments[0].url, proxy=self.node.proxy,
                                           proxy_auth=self.node.proxy_auth) as response:
                        if response.status == 200:
                            data = await response.json(encoding="utf-8")
                            try:
                                validate(instance=data, schema=schema)
                            except ValidationError:
                                await message.channel.send(f"Could not upload file {attachment.filename} "
                                                           f"as it is no valid transponder file.")
                                continue

                root = server.instance.home
                base_dir = os.path.join(root, LOTATC_DIR.format("blue") if "blue" in attachment.filename else "red")
                await handler.upload(base_dir)
        except Exception as ex:
            self.log.exception(ex)
            await message.channel.send("Error while uploading. Check the DCSServerBot log.")
        finally:
            await message.delete()


async def setup(bot: DCSServerBot):
    await bot.add_cog(LotAtc(bot, LotAtcEventListener))
