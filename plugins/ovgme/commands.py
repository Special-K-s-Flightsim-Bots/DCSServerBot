import aiohttp
import discord
import os
import psycopg

from core import Status, Plugin, utils, Server, command, ServiceRegistry, PluginInstallationError
from discord import SelectOption, TextStyle, app_commands
from discord.ui import View, Select, Button, Modal, TextInput

from services import DCSServerBot
from typing import Tuple, cast
from urllib.parse import urlparse, unquote

from services.ovgme import OvGMEService

OVGME_FOLDERS = ['RootFolder', 'SavedGames']


class OvGME(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.service: OvGMEService = cast(OvGMEService, ServiceRegistry.get("OvGME"))
        if not self.service:
            raise PluginInstallationError(plugin=self.plugin_name, reason='OvGME service not loaded.')

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str):
        conn.execute('UPDATE ovgme_packages SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    @command(description='Display installed packages')
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin'])
    async def packages(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(
                           status=[Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.SHUTDOWN])]):
        class PackageView(View):

            def __init__(derived, embed: discord.Embed):
                super().__init__()
                derived.installed = derived.get_installed()
                derived.available = derived.get_available()
                derived.embed = embed
                derived.render()

            def get_installed(derived) -> list[Tuple[str, str, str]]:
                installed = []
                for folder in OVGME_FOLDERS:
                    packages = [(folder, x, y) for x, y in self.service.get_installed_packages(server, folder)]
                    if packages:
                        installed.extend(packages)
                return installed

            def get_available(derived) -> list[Tuple[str, str, str]]:
                available = []
                config = self.service.get_config(server)
                for folder in OVGME_FOLDERS:
                    packages = []
                    for x in os.listdir(os.path.expandvars(config[folder])):
                        if x.startswith('.'):
                            continue
                        package, version = self.service.parse_filename(x)
                        if package:
                            packages.append((folder, package, version))
                        else:
                            self.log.warning(f"{x} could not be parsed!")
                    if packages:
                        available.extend(packages)
                return list(set(available) - set(derived.installed))

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
                    derived.embed.add_field(name='Package', value=packages)
                    derived.embed.add_field(name='Version', value=versions)
                    derived.embed.add_field(name='Update', value=update)
                else:
                    derived.embed.add_field(name='_ _', value='There are no mods installed.', inline=False)

                derived.clear_items()
                if derived.available and server.status == Status.SHUTDOWN:
                    select = Select(placeholder="Select a package to install / update",
                                    options=[SelectOption(label=x[1] + '_' + x[2], value=str(idx))
                                             for idx, x in enumerate(derived.available)],
                                    row=0)
                    select.callback = derived.install
                    derived.add_item(select)
                if derived.installed and server.status == Status.SHUTDOWN:
                    select = Select(placeholder="Select a package to uninstall",
                                    options=[SelectOption(label=x[1] + '_' + x[2], value=str(idx))
                                             for idx, x in enumerate(derived.installed)],
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
                        derived.embed.set_footer(text=f"Updating package {package}, please wait ...")
                        await interaction.edit_original_response(embed=derived.embed)
                        if not await self.service.uninstall_package(server, folder, package, current):
                            derived.embed.set_footer(text=f"Package {package}_v{version} could not be uninstalled!")
                            await interaction.edit_original_response(embed=derived.embed)
                        elif not await self.service.install_package(server, folder, package, version):
                            derived.embed.set_footer(text=f"Package {package}_v{version} could not be installed!")
                            await interaction.edit_original_response(embed=derived.embed)
                        else:
                            derived.embed.set_footer(text=f"Package {package} updated.")
                            derived.installed = derived.get_installed()
                            derived.available = derived.get_available()
                            derived.render()
                    else:
                        derived.embed.set_footer(text=f"Installing package {package}, please wait ...")
                        await interaction.edit_original_response(embed=derived.embed)
                        if not await self.service.install_package(server, folder, package, version):
                            derived.embed.set_footer(text=f"Installation of package {package} failed.")
                        else:
                            derived.embed.set_footer(text=f"Package {package} installed.")
                            derived.installed = derived.get_installed()
                            derived.available = derived.get_available()
                            derived.render()
                    await interaction.edit_original_response(embed=derived.embed, view=derived)
                except Exception as ex:
                    self.log.exception(ex)

            async def uninstall(derived, interaction: discord.Interaction):
                await interaction.response.defer()
                folder, package, version = derived.installed[int(interaction.data['values'][0])]
                derived.embed.set_footer(text=f"Uninstalling package {package}, please wait ...")
                await interaction.edit_original_response(embed=derived.embed)
                if not await self.service.uninstall_package(server, folder, package, version):
                    derived.embed.set_footer(text=f"Package {package}_v{version} could not be uninstalled!")
                else:
                    derived.embed.set_footer(text=f"Package {package} uninstalled.")
                    derived.installed = derived.get_installed()
                    derived.available = derived.get_available()
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
                    derived.available = derived.get_available()
                    derived.render()
                    await interaction.edit_original_response(embed=derived.embed, view=derived)

            async def cancel(derived, interaction: discord.Interaction):
                derived.stop()

        embed = discord.Embed(title="Package Manager", color=discord.Color.blue())
        embed.description = f"Install or uninstall mod packages to {server.name}"
        view = PackageView(embed)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            await view.wait()
        finally:
            await interaction.delete_original_response()


async def setup(bot: DCSServerBot):
    await bot.add_cog(OvGME(bot))
