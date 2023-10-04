import aiohttp
import discord
import os
import psycopg

from core import Status, Plugin, utils, Server, ServiceRegistry, PluginInstallationError, Group
from discord import SelectOption, TextStyle, app_commands
from discord.ui import View, Select, Button, Modal, TextInput

from services import DCSServerBot, OvGMEService
from typing import Tuple, cast
from urllib.parse import urlparse, unquote

OVGME_FOLDERS = ['RootFolder', 'SavedGames']


def get_installed(service: OvGMEService, server: Server) -> list[Tuple[str, str, str]]:
    installed = []
    for folder in OVGME_FOLDERS:
        _mods = [(folder, x, y) for x, y in service.get_installed_packages(server, folder)]
        if _mods:
            installed.extend(_mods)
    return installed


async def installed_mods(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    service: OvGMEService = cast(OvGMEService, ServiceRegistry.get("OvGME"))
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        return [
            app_commands.Choice(name=name + f'_v{version}', value=f"{folder}/{name}/{version}")
            for folder, name, version in sorted(get_installed(service, server))
            if not current or current.casefold() in name.casefold()
        ][:25]
    except Exception as ex:
        service.log.exception(ex)


def get_available(service: OvGMEService, server: Server) -> list[Tuple[str, str, str]]:
    available = []
    config = service.get_config(server)
    for folder in OVGME_FOLDERS:
        packages = []
        for x in os.listdir(os.path.expandvars(config[folder])):
            if x.startswith('.'):
                continue
            package, version = service.parse_filename(x)
            if package:
                packages.append((folder, package, version))
            else:
                service.log.warning(f"{x} could not be parsed!")
        if packages:
            available.extend(packages)
    return list(set(available) - set(get_installed(service, server)))


async def available_mods(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    service: OvGMEService = cast(OvGMEService, ServiceRegistry.get("OvGME"))
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        return [
            app_commands.Choice(name=name + f'_v{version}', value=f"{folder}/{name}/{version}")
            for folder, name, version in sorted(get_available(service, server))
            if not current or current.casefold() in name.casefold()
        ][:25]
    except Exception as ex:
        service.log.exception(ex)


class OvGME(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.service: OvGMEService = cast(OvGMEService, ServiceRegistry.get("OvGME"))
        if not self.service:
            raise PluginInstallationError(plugin=self.plugin_name, reason='OvGME service not loaded.')

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str):
        conn.execute('UPDATE ovgme_packages SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    # New command group "/mods"
    mods = Group(name="mods", description="Commands to manage custom mods in your DCS server")

    @mods.command(description='Install / uninstall / update mods')
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin'])
    async def manage(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.SHUTDOWN])]):
        class PackageView(View):

            def __init__(derived, embed: discord.Embed):
                super().__init__()
                derived.installed = get_installed(self.service, server)
                derived.available = get_available(self.service, server)
                derived.embed = embed
                derived.render()

            async def shutdown(derived, interaction: discord.Interaction):
                await interaction.response.defer()
                derived.embed.set_footer(text=f"Shutting down {server.name}, please wait ...")
                await interaction.edit_original_response(embed=derived.embed)
                await server.shutdown()
                derived.render()
                await interaction.edit_original_response(embed=derived.embed, view=derived)

            def render(derived):
                derived.embed.clear_fields()
                if derived.installed:
                    derived.embed.add_field(name='_ _', value='**The following mods are currently installed:**',
                                            inline=False)
                    packages = versions = update = ''
                    for i in range(0, len(derived.installed)):
                        packages += derived.installed[i][1] + '\n'
                        versions += derived.installed[i][2] + '\n'
                        latest = self.service.get_latest_version(derived.installed[i][0], derived.installed[i][1])
                        if latest != derived.installed[i][2]:
                            update += latest + '\n'
                        else:
                            update += '_ _\n'
                    derived.embed.add_field(name='Mod', value=packages)
                    derived.embed.add_field(name='Version', value=versions)
                    derived.embed.add_field(name='Update', value=update)
                else:
                    derived.embed.add_field(name='_ _', value='There are no mods installed.', inline=False)

                derived.clear_items()
                if derived.available and server.status == Status.SHUTDOWN:
                    select = Select(placeholder="Select a mod to install / update",
                                    options=[
                                        SelectOption(label=x[1] + '_' + x[2], value=str(idx))
                                        for idx, x in enumerate(derived.available)
                                        if idx < 25
                                    ],
                                    row=0)
                    select.callback = derived.install
                    derived.add_item(select)
                if derived.installed and server.status == Status.SHUTDOWN:
                    select = Select(placeholder="Select a mod to uninstall",
                                    options=[
                                        SelectOption(label=x[1] + '_' + x[2], value=str(idx))
                                        for idx, x in enumerate(derived.installed)
                                        if idx < 25
                                    ],
                                    disabled=not derived.installed or server.status != Status.SHUTDOWN,
                                    row=1)
                    select.callback = derived.uninstall
                    derived.add_item(select)
                button = Button(label="Add", style=discord.ButtonStyle.primary, row=2)
                button.callback = derived.add
                derived.add_item(button)
                if server.status != Status.SHUTDOWN:
                    button = Button(label="Shutdown", style=discord.ButtonStyle.secondary, row=2)
                    button.callback = derived.shutdown
                    derived.add_item(button)
                    derived.embed.set_footer(text=f"⚠️ Server {server.name} needs to be shut down to change mods.")
                else:
                    for i in range(1, len(derived.children)):
                        if isinstance(derived.children[i], Button) and derived.children[i].label == "Shutdown":
                            derived.remove_item(derived.children[i])
                button = Button(label="Quit", style=discord.ButtonStyle.red, row=2)
                button.callback = derived.cancel
                derived.add_item(button)

            async def install(derived, interaction: discord.Interaction):
                await interaction.response.defer()
                try:
                    folder, package, version = derived.available[int(interaction.data['values'][0])]
                    current = self.service.check_package(server, folder, package)
                    if current:
                        derived.embed.set_footer(text=f"Updating mod {package}, please wait ...")
                        await interaction.edit_original_response(embed=derived.embed)
                        if not await self.service.uninstall_package(server, folder, package, current):
                            derived.embed.set_footer(text=f"Mod {package}_v{version} could not be uninstalled!")
                            await interaction.edit_original_response(embed=derived.embed)
                        elif not await self.service.install_package(server, folder, package, version):
                            derived.embed.set_footer(text=f"Mod {package}_v{version} could not be installed!")
                            await interaction.edit_original_response(embed=derived.embed)
                        else:
                            derived.embed.set_footer(text=f"Mod {package} updated.")
                            derived.installed = get_installed(self.service, server)
                            derived.available = get_available(self.service, server)
                            derived.render()
                    else:
                        derived.embed.set_footer(text=f"Installing mod {package}, please wait ...")
                        await interaction.edit_original_response(embed=derived.embed)
                        if not await self.service.install_package(server, folder, package, version):
                            derived.embed.set_footer(text=f"Installation of mod {package} failed.")
                        else:
                            derived.embed.set_footer(text=f"Mod {package} installed.")
                            derived.installed = get_installed(self.service, server)
                            derived.available = get_available(self.service, server)
                            derived.render()
                    await interaction.edit_original_response(embed=derived.embed, view=derived)
                except Exception as ex:
                    self.log.exception(ex)

            async def uninstall(derived, interaction: discord.Interaction):
                await interaction.response.defer()
                folder, mod, version = derived.installed[int(interaction.data['values'][0])]
                derived.embed.set_footer(text=f"Uninstalling mod {mod}, please wait ...")
                await interaction.edit_original_response(embed=derived.embed)
                if not await self.service.uninstall_package(server, folder, mod, version):
                    derived.embed.set_footer(text=f"Mod {mod}_v{version} could not be uninstalled!")
                else:
                    derived.embed.set_footer(text=f"Mod {mod} uninstalled.")
                    derived.installed = get_installed(self.service, server)
                    derived.available = get_available(self.service, server)
                    derived.render()
                await interaction.edit_original_response(embed=derived.embed, view=derived)

            async def add(derived, interaction: discord.Interaction):
                class UploadModal(Modal, title="Enter the mod URL"):
                    url = TextInput(label="URL", placeholder='https://...', style=TextStyle.short, required=True)
                    filename = TextInput(label="Filename-override (optional)", placeholder="name_vX.Y.Z",
                                         style=TextStyle.short, required=False)
                    dest = TextInput(label="Destination (S=Saved Games / R=Root Folder)", style=TextStyle.short,
                                     required=True, min_length=1, max_length=1)

                    async def on_submit(_, interaction: discord.Interaction) -> None:
                        await interaction.response.defer()

                async def download(modal: UploadModal):
                    async with aiohttp.ClientSession() as session:
                        async with session.get(modal.url.value) as response:
                            if response.status == 200:
                                path = os.path.expandvars(self.get_config(server)[
                                                              OVGME_FOLDERS[0] if modal.dest.value == 'R' else
                                                              OVGME_FOLDERS[1]])
                                if modal.filename.value:
                                    filename = modal.filename.value
                                else:
                                    filename = os.path.basename(unquote(urlparse(modal.url.value).path))
                                self.log.debug(f"Downloading file {filename} from {modal.url.value} ...")
                                with open(os.path.join(path, filename), 'wb') as outfile:
                                    outfile.write(await response.read())
                                self.log.debug(f"File {filename} downloaded.")

                modal = UploadModal()
                await interaction.response.send_modal(modal)
                if not await modal.wait():
                    derived.embed.set_footer(text=f"Downloading {modal.url.value} , please wait ...")
                    for child in derived.children:
                        child.disabled = True
                    await interaction.edit_original_response(embed=derived.embed, view=derived)
                    await download(modal)
                    for child in derived.children:
                        child.disabled = False
                    embed.remove_footer()
                    derived.available = get_available(self.service, server)
                    derived.render()
                    await interaction.edit_original_response(embed=derived.embed, view=derived)

            async def cancel(derived, interaction: discord.Interaction):
                derived.stop()

        embed = discord.Embed(title="Mod Manager", color=discord.Color.blue())
        embed.description = f"Install or uninstall mods to {server.name}"
        view = PackageView(embed)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            await view.wait()
        finally:
            await interaction.delete_original_response()

    @mods.command(name="install", description='Install mods to your DCS server')
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin'])
    @app_commands.autocomplete(mod=available_mods)
    async def _install(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                       mod: str):
        folder, package, version = mod.split('/')
        await interaction.response.defer(ephemeral=True)
        current = self.service.check_package(server, folder, package)
        if current:
            await interaction.followup.send(f"Updating mod {package} from {current} to {version}, please wait ...",
                                            ephemeral=True)
            if not await self.service.uninstall_package(server, folder, package, current):
                await interaction.followup.send(f"Mod {package}_v{version} could not be uninstalled!",
                                                ephemeral=True)
            elif not await self.service.install_package(server, folder, package, version):
                await interaction.followup.send(f"Mod {package}_v{version} could not be installed!", ephemeral=True)
            else:
                await interaction.followup.send(f"Mod {package} updated to version {version}.", ephemeral=True)
        else:
            await interaction.followup.send(f"Installing mod {package}, please wait ...", ephemeral=True)
            if not await self.service.install_package(server, folder, package, version):
                await interaction.followup.send(f"Installation of mod {package} failed.", ephemeral=True)
            else:
                await interaction.followup.send(f"Mod {package} installed with version {version}.", ephemeral=True)

    @mods.command(description='Uninstall mods from your DCS server')
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin'])
    @app_commands.autocomplete(mod=installed_mods)
    async def uninstall(self, interaction: discord.Interaction,
                        server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                        mod: str):
        folder, package, version = mod.split('/')
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(f"Uninstalling mod {package}, please wait ...", ephemeral=True)
        if not await self.service.uninstall_package(server, folder, package, version):
            await interaction.followup.send(f"Mod {package}_v{version} could not be uninstalled!", ephemeral=True)
        else:
            await interaction.followup.send(f"Mod {package} uninstalled.", ephemeral=True)


async def setup(bot: DCSServerBot):
    await bot.add_cog(OvGME(bot))
