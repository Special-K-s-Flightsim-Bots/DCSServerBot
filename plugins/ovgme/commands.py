import aiohttp
import discord
import os
import psycopg

from core import Status, Plugin, utils, Server, ServiceRegistry, PluginInstallationError, Group
from discord import SelectOption, TextStyle, app_commands
from discord.ui import View, Select, Button, Modal, TextInput

from services import DCSServerBot, OvGMEService
from typing import Tuple, cast, Optional, Literal

OVGME_FOLDERS = ['RootFolder', 'SavedGames']


async def get_installed_mods(service: OvGMEService, server: Server) -> list[Tuple[str, str, str]]:
    installed = []
    for folder in OVGME_FOLDERS:
        _mods = [(folder, x, y) for x, y in await service.get_installed_packages(server, folder)]
        if _mods:
            installed.extend(_mods)
    return sorted(installed)


async def get_available_mods(service: OvGMEService, server: Server) -> list[Tuple[str, str, str]]:
    available = []
    config = service.get_config(server)
    for folder in OVGME_FOLDERS:
        packages = []
        for x in os.listdir(os.path.expandvars(config[folder])):
            if x.startswith('.') or x.casefold() in ['desktop.ini']:
                continue
            package, version = service.parse_filename(x)
            if package:
                packages.append((folder, package, version))
            else:
                service.log.warning(f"{x} could not be parsed!")
        if packages:
            available.extend(packages)
    return sorted(set(available) - set(await get_installed_mods(service, server)))


async def installed_mods_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    service: OvGMEService = cast(OvGMEService, ServiceRegistry.get("OvGME"))
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        return [
            app_commands.Choice(name=name + f'_v{version}', value=f"{folder}/{name}/{version}")
            for folder, name, version in sorted(await get_installed_mods(service, server))
            if not current or current.casefold() in name.casefold()
        ][:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def available_mods_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    service: OvGMEService = cast(OvGMEService, ServiceRegistry.get("OvGME"))
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        return [
            app_commands.Choice(name=name, value=f"{folder}/{name}")
            for folder, name in sorted(set((folder, name) for folder, name, _ in await get_available_mods(service, server)))
            if not current or current.casefold() in name.casefold()
        ][:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def available_versions_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    service: OvGMEService = cast(OvGMEService, ServiceRegistry.get("OvGME"))
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        try:
            folder, mod = utils.get_interaction_param(interaction, 'mod').split('/')
        except AttributeError:
            return []
        return [
            app_commands.Choice(name=version, value=version)
            for version in sorted(await service.get_available_versions(server, folder, mod), reverse=True)
            if not current or current.casefold() in version.casefold()
        ][:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def repo_version_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    service: OvGMEService = cast(OvGMEService, ServiceRegistry.get("OvGME"))
    try:
        repo = utils.get_interaction_param(interaction, 'url')

        if not repo or not utils.is_github_repo(repo):
            return []
        return [
            app_commands.Choice(name=version, value=version)
            for version in sorted(await service.get_repo_versions(repo), reverse=True)
            if not current or current.casefold() in version.casefold()
        ][:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


class OvGME(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        if os.path.exists(os.path.join(self.node.config_dir, 'plugins', 'ovgme.yaml')):
            self.log.warning(f"  => OvGME: your ovgme.yaml belongs into {self.node.config_dir}/services/ovgme.yaml, "
                             f"not in {self.node.config_dir}/plugins!")
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

            def __init__(derived, embed: discord.Embed, installed: list[Tuple[str, str, str]],
                         available: list[Tuple[str, str, str]]):
                super().__init__()
                derived.installed = installed
                derived.available = available
                derived.embed = embed

            async def shutdown(derived, interaction: discord.Interaction):
                await interaction.response.defer()
                derived.embed.set_footer(text=f"Shutting down {server.name}, please wait ...")
                await interaction.edit_original_response(embed=derived.embed)
                await server.shutdown()
                await derived.render()
                await interaction.edit_original_response(embed=derived.embed, view=derived)

            async def render(derived):
                derived.embed.clear_fields()
                if derived.installed:
                    derived.embed.add_field(name='_ _', value='**The following mods are currently installed:**',
                                            inline=False)
                    packages = versions = update = ''
                    for i in range(0, len(derived.installed)):
                        packages += derived.installed[i][1] + '\n'
                        versions += derived.installed[i][2] + '\n'
                        latest = await self.service.get_latest_version({"source": derived.installed[i][0],
                                                                        "name": derived.installed[i][1]})
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
                button = Button(label="Download", style=discord.ButtonStyle.primary, row=2)
                button.callback = derived.download
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
                    current = self.service.get_installed_package(server, folder, package)
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
                            derived.installed = await get_installed_mods(self.service, server)
                            derived.available = await get_available_mods(self.service, server)
                            await derived.render()
                    else:
                        derived.embed.set_footer(text=f"Installing mod {package}, please wait ...")
                        await interaction.edit_original_response(embed=derived.embed)
                        if not await self.service.install_package(server, folder, package, version):
                            derived.embed.set_footer(text=f"Installation of mod {package} failed.")
                        else:
                            derived.embed.set_footer(text=f"Mod {package} installed.")
                            derived.installed = await get_installed_mods(self.service, server)
                            derived.available = await get_available_mods(self.service, server)
                            await derived.render()
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
                    derived.installed = await get_installed_mods(self.service, server)
                    derived.available = await get_available_mods(self.service, server)
                    await derived.render()
                await interaction.edit_original_response(embed=derived.embed, view=derived)

            async def download(derived, interaction: discord.Interaction):
                class UploadModal(Modal, title="Download a new Mod"):
                    url = TextInput(label="URL / GitHub Repo", placeholder='https://github.com/...', style=TextStyle.short,
                                    required=True)
                    dest = TextInput(label="Destination (S=Saved Games / R=Root Folder)", style=TextStyle.short,
                                     required=True, min_length=1, max_length=1)
                    version = TextInput(label="Version", style=TextStyle.short, required=False, default='latest')

                    async def on_submit(_, interaction: discord.Interaction) -> None:
                        await interaction.response.defer()

                async def download(modal: UploadModal):
                    if utils.is_valid_url(modal.url.value):
                        folder = OVGME_FOLDERS[0 if modal.dest.value == 'R' else 1]
                        if utils.is_github_repo(modal.url.value):
                            await self.service.download_from_repo(modal.url.value, folder, version=modal.version.value)
                        else:
                            await self.service.download(modal.url.value, folder)
                    else:
                        raise ValueError("Not a valid URL!")

                modal = UploadModal()
                await interaction.response.send_modal(modal)
                if not await modal.wait():
                    if not utils.is_valid_url(modal.url.value):
                        derived.embed.set_footer(text=f"{modal.url.value} is not a valid URL")
                    else:
                        derived.embed.set_footer(text=f"Downloading {modal.url.value} , please wait ...")
                        for child in derived.children:
                            child.disabled = True
                        await interaction.edit_original_response(embed=derived.embed, view=derived)
                        try:
                            await download(modal)
                            embed.remove_footer()
                            derived.available = get_available_mods(self.service, server)
                        except aiohttp.client_exceptions.ClientResponseError as ex:
                            self.log.error(f"{ex.code}: {modal.url.value} {ex.message}")
                            embed.set_footer(text=f"{ex.code}: {ex.message}")
                        except Exception as ex:
                            embed.set_footer(text=f"Error: {ex.__class__.__name__}")
                        for child in derived.children:
                            child.disabled = False
                        await derived.render()
                    await interaction.edit_original_response(embed=derived.embed, view=derived)

            async def cancel(derived, interaction: discord.Interaction):
                derived.stop()

        embed = discord.Embed(title="Mod Manager", color=discord.Color.blue())
        embed.description = f"Install or uninstall mods to {server.name}"
        view = PackageView(embed,
                           installed=await get_installed_mods(self.service, server),
                           available=await get_available_mods(self.service, server))
        await view.render()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=utils.get_ephemeral(interaction))
        try:
            await view.wait()
        finally:
            await interaction.delete_original_response()

    @mods.command(name="install", description='Install mods to your DCS server')
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin'])
    @app_commands.autocomplete(mod=available_mods_autocomplete)
    @app_commands.autocomplete(version=available_versions_autocomplete)
    async def _install(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                       mod: str, version: str):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status != Status.SHUTDOWN:
            await interaction.response.send_message(f"Server {server.name} needs to be shut down to install mods.")
            return
        if '/' not in mod:
            await interaction.response.send_message(f"Mod {mod} not found.")
            return
        folder, package = mod.split('/')
        await interaction.response.defer(ephemeral=ephemeral)
        current = self.service.get_installed_package(server, folder, package)
        if current == version:
            await interaction.followup.send(f"Package {package}_v{version} is already installed.")
            return
        if current:
            msg = await interaction.followup.send(
                f"Updating mod {package} from {current} to {version}, please wait ...", ephemeral=ephemeral)
            if not await self.service.uninstall_package(server, folder, package, current):
                await msg.edit(content=f"Mod {package}_v{version} could not be uninstalled!")
            elif not await self.service.install_package(server, folder, package, version):
                await msg.edit(content=f"Mod {package}_v{version} could not be installed!")
            else:
                await msg.edit(content=f"Mod {package} updated to version {version}.")
        else:
            msg = await interaction.followup.send(f"Installing mod {package}, please wait ...", ephemeral=ephemeral)
            if not await self.service.install_package(server, folder, package, version):
                await msg.edit(content=f"Installation of mod {package} failed.")
            else:
                await msg.edit(content=f"Mod {package} installed with version {version}.")

    @mods.command(description='Uninstall mods from your DCS server')
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin'])
    @app_commands.autocomplete(mod=installed_mods_autocomplete)
    async def uninstall(self, interaction: discord.Interaction,
                        server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                        mod: str):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status != Status.SHUTDOWN:
            await interaction.response.send_message(f"Server {server.name} needs to be shut down to uninstall mods.")
            return
        folder, package, version = mod.split('/')
        await interaction.response.defer(ephemeral=ephemeral)
        msg = await interaction.followup.send(f"Uninstalling mod {package}, please wait ...", ephemeral=ephemeral)
        if not await self.service.uninstall_package(server, folder, package, version):
            await msg.edit(content=f"Mod {package}_v{version} could not be uninstalled!")
            return
        await msg.edit(content=f"Mod {package} uninstalled.")

    @mods.command(name="list", description='List all mods that are installed on your DCS server')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin'])
    async def _list(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        installed: dict[str, list[Tuple[str, str]]] = dict()
        for folder in OVGME_FOLDERS:
            installed[folder] = await self.service.get_installed_packages(server, folder)
        if not len(installed[OVGME_FOLDERS[0]]) and not len(installed[OVGME_FOLDERS[1]]):
            await interaction.response.send_message(f"No mod installed on server {server.name}.", ephemeral=ephemeral)
            return
        embed = discord.Embed(color=discord.Color.blue())
        embed.description = f"The following mods are installed on server {server.name}:"
        for folder in OVGME_FOLDERS:
            if installed[folder]:
                embed.add_field(name="Folder", value=folder)
                embed.add_field(name="Mod", value='\n'.join([x[0] for x in installed[folder]]))
                embed.add_field(name="Version", value='\n'.join([x[1] for x in installed[folder]]))
        await interaction.response.send_message(embed=embed)

    @mods.command(description='Download a mod to your installation directory')
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin'])
    @app_commands.describe(url="GitHub repo link or download URL")
    @app_commands.autocomplete(version=repo_version_autocomplete)
    async def download(self, interaction: discord.Interaction, folder: Literal['SavedGames', 'RootDir'], url: str,
                       version: Optional[str]):
        ephemeral = utils.get_ephemeral(interaction)
        if not utils.is_valid_url(url):
            await interaction.response.send_message("{url} is not a valid URL.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=ephemeral)
        if utils.is_github_repo(url) and not version:
            version = await self.service.get_latest_repo_version(url)
        if version:
            package_name = self.service.extract_repo_name(url).split('/')[-1]
            msg = await interaction.followup.send(f"Downloading {package_name}_v{version} from GitHub ...",
                                            ephemeral=ephemeral)
            try:
                await self.service.download_from_repo(url, folder, version=version)
            except FileExistsError:
                if not await utils.yn_question(interaction, f"File exists. Do you want to overwrite it?",
                                               ephemeral=ephemeral):
                    await msg.edit(content="Aborted.")
                    return
                await self.service.download_from_repo(url, folder, version=version, force=True)
            except aiohttp.ClientResponseError as ex:
                await msg.edit(content=f"Error {ex.status}: {package_name}_v{version} {ex.message}")
                return
            await msg.edit(content=f"{package_name}_v{version} downloaded. Use `/mods install` to install it.")
        else:
            filename = url.split('/')[-1]
            msg = await interaction.followup.send(f"Downloading {filename} ...", ephemeral=ephemeral)
            try:
                await self.service.download(url, folder)
            except FileExistsError:
                if not await utils.yn_question(interaction, f"File exists. Do you want to overwrite it?",
                                               ephemeral=ephemeral):
                    return
                await self.service.download_from_repo(url, folder, version=version, force=True)
            await msg.edit(content=f"{filename} downloaded. Use `/mods install` to install it.")


async def setup(bot: DCSServerBot):
    await bot.add_cog(OvGME(bot))
