import aiohttp
import discord
import os
import psycopg

from core import Status, Plugin, utils, Server, ServiceRegistry, PluginInstallationError, Group, get_translation
from discord import SelectOption, app_commands, ButtonStyle, TextStyle
from discord.ui import View, Select, Button, Modal, TextInput
from services.bot import DCSServerBot
from services.modmanager import ModManagerService, Folder

_ = get_translation(__name__.split('.')[1])

WARNING_ICON = "https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/warning.png?raw=true"


async def get_installed_mods(service: ModManagerService, server: Server) -> list[tuple[Folder, str, str]]:
    installed = []
    for folder in Folder:
        reference = server if folder == Folder.SavedGames else server.node
        _mods = [(folder, x, y) for x, y in await service.get_installed_packages(reference, folder)]
        if _mods:
            installed.extend(_mods)
    return sorted(installed)


async def get_available_mods(
        interaction: discord.Interaction, service: ModManagerService, server: Server
) -> list[tuple[Folder, str, str]]:
    available = []
    config = service.get_config(server)
    for folder in Folder:
        if folder == Folder.RootFolder and utils.is_restricted(interaction):
            continue
        packages = []
        for x in os.listdir(os.path.expandvars(config[folder.value])):
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
    service = ServiceRegistry.get(ModManagerService)
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        return [
            app_commands.Choice(name=name + f'_v{version}', value=f"{folder.value}/{name}/{version}")
            for folder, name, version in sorted(await get_installed_mods(service, server))
            if not current or current.casefold() in name.casefold()
        ][:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def available_mods_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    service = ServiceRegistry.get(ModManagerService)
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        return [
            app_commands.Choice(name=name, value=f"{folder.value}/{name}")
            for folder, name in sorted(set(
                (folder, name) for folder, name, _ in await get_available_mods(interaction, service, server))
            )
            if not current or current.casefold() in name.casefold()
        ][:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def available_versions_autocomplete(interaction: discord.Interaction,
                                          current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    service = ServiceRegistry.get(ModManagerService)
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        try:
            folder, mod = utils.get_interaction_param(interaction, 'mod').split('/')
        except (ValueError, AttributeError):
            return []
        return [
            app_commands.Choice(name=version, value=version)
            for version in sorted(await service.get_available_versions(server, Folder(folder), mod), reverse=True)
            if not current or current.casefold() in version.casefold()
        ][:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def repo_version_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    service = ServiceRegistry.get(ModManagerService)
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
        return []


class ModManager(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        if (os.path.exists(os.path.join(self.node.config_dir, 'plugins', 'modmanager.yaml')) and
                not os.path.exists(os.path.join(self.node.config_dir, 'services', 'modmanager.yaml'))):
            self.log.warning(
                f"  => ModManager: your modmanager.yaml belongs into {self.node.config_dir}/services/modmanager.yaml, "
                f"not in {self.node.config_dir}/plugins!")
        self.service = ServiceRegistry.get(ModManagerService)
        if not self.service:
            raise PluginInstallationError(plugin=self.plugin_name, reason='ModManager service not loaded.')

    # New command group "/mods"
    mods = Group(name="mods", description=_("Commands to manage custom mods in your DCS server"))

    @mods.command(description=_('manage mods'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_roles(['Admin'])
    async def manage(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.SHUTDOWN])]):
        class PackageView(View):

            def __init__(derived, embed: discord.Embed, installed: list[tuple[Folder, str, str]],
                         available: list[tuple[Folder, str, str]]):
                super().__init__()
                derived.installed = installed
                derived.available = available
                derived.embed = embed

            async def shutdown(derived, interaction: discord.Interaction):
                # noinspection PyUnresolvedReferences
                await interaction.response.defer()
                derived.embed.set_footer(text=_("Shutting down {}, please wait ...").format(server.name))
                await interaction.edit_original_response(embed=derived.embed)
                await server.shutdown()
                await derived.render()
                await interaction.edit_original_response(embed=derived.embed, view=derived)

            async def render(derived):
                derived.embed.clear_fields()
                if derived.installed:
                    derived.embed.add_field(name='_ _', value=_('**The following mods are currently installed:**'),
                                            inline=False)
                    packages = versions = update = ''
                    for i in range(0, len(derived.installed)):
                        packages += derived.installed[i][1] + '\n'
                        versions += derived.installed[i][2] + '\n'
                        latest = await self.service.get_latest_version({"source": derived.installed[i][0],
                                                                        "name": derived.installed[i][1]})
                        if latest and latest != derived.installed[i][2].strip('v'):
                            update += latest + '\n'
                        else:
                            update += '_ _\n'
                    derived.embed.add_field(name=_('Mod'), value=packages)
                    derived.embed.add_field(name=_('Version'), value=versions)
                    derived.embed.add_field(name=_('Update'), value=update)
                else:
                    derived.embed.add_field(name='_ _', value=_('There are no mods installed.'), inline=False)

                derived.clear_items()
                if derived.available and server.status == Status.SHUTDOWN:
                    select = Select(placeholder=_("Select a mod to install / update"),
                                    options=[
                                        SelectOption(label=x[1] + '_' + x[2], value=str(idx))
                                        for idx, x in enumerate(derived.available)
                                        if idx < 25
                                    ],
                                    row=0)
                    select.callback = derived.install
                    derived.add_item(select)
                if derived.installed and server.status == Status.SHUTDOWN:
                    select = Select(placeholder=_("Select a mod to uninstall"),
                                    options=[
                                        SelectOption(label=x[1] + '_' + x[2], value=str(idx))
                                        for idx, x in enumerate(derived.installed)
                                        if idx < 25
                                    ],
                                    disabled=not derived.installed or server.status != Status.SHUTDOWN,
                                    row=1)
                    select.callback = derived.uninstall
                    derived.add_item(select)
                # noinspection PyTypeChecker
                button = Button(label=_("Download"), style=ButtonStyle.primary, row=2)
                button.callback = derived.download
                derived.add_item(button)
                if server.status != Status.SHUTDOWN:
                    # noinspection PyTypeChecker
                    button = Button(label=_("Shutdown"), style=ButtonStyle.secondary, row=2)
                    button.callback = derived.shutdown
                    derived.add_item(button)
                    derived.embed.set_footer(
                        text=_("Server {} needs to be shut down to change mods.").format(server.name),
                        icon_url=WARNING_ICON)
                else:
                    for i in range(1, len(derived.children)):
                        # noinspection PyUnresolvedReferences
                        if isinstance(derived.children[i], Button) and derived.children[i].label == "Shutdown":
                            derived.remove_item(derived.children[i])
                # noinspection PyTypeChecker
                button = Button(label=_("Quit"), style=ButtonStyle.red, row=2)
                button.callback = derived.cancel
                derived.add_item(button)

            async def install(derived, interaction: discord.Interaction):
                # noinspection PyUnresolvedReferences
                await interaction.response.defer()
                try:
                    folder, package, version = derived.available[int(interaction.data['values'][0])]
                    current = await self.service.get_installed_package(server, folder, package)
                    if current:
                        derived.embed.set_footer(text=_("Updating mod {}, please wait ...").format(package))
                        await interaction.edit_original_response(embed=derived.embed)
                        if not await self.service.uninstall_package(server, folder, package, current):
                            derived.embed.set_footer(
                                text=_("Mod {mod}_v{version} could not be uninstalled!").format(
                                    mod=package, version=version), icon_url=WARNING_ICON)
                            await interaction.edit_original_response(embed=derived.embed)
                        elif not await self.service.install_package(server, folder, package, version):
                            derived.embed.set_footer(
                                text=_("Mod {mod}_v{version} could not be installed!").format(
                                    mod=package, version=version), icon_url=WARNING_ICON)
                            await interaction.edit_original_response(embed=derived.embed)
                        else:
                            derived.embed.set_footer(text=_("Mod {} updated.").format(package))
                            derived.installed = await get_installed_mods(self.service, server)
                            derived.available = await get_available_mods(interaction, self.service, server)
                            await derived.render()
                    else:
                        derived.embed.set_footer(text=_("Installing mod {}, please wait ...").format(package))
                        await interaction.edit_original_response(embed=derived.embed)
                        if not await self.service.install_package(server, folder, package, version):
                            derived.embed.set_footer(text=_("Installation of mod {} failed.").format(package),
                                                     icon_url=WARNING_ICON)
                        else:
                            derived.embed.set_footer(text=_("Mod {} installed.").format(package))
                            derived.installed = await get_installed_mods(self.service, server)
                            derived.available = await get_available_mods(interaction, self.service, server)
                            await derived.render()
                    await interaction.edit_original_response(embed=derived.embed, view=derived)
                except Exception as ex:
                    self.log.exception(ex)

            async def uninstall(derived, interaction: discord.Interaction):
                # noinspection PyUnresolvedReferences
                await interaction.response.defer()
                folder, mod, version = derived.installed[int(interaction.data['values'][0])]
                derived.embed.set_footer(text=_("Uninstalling mod {}, please wait ...").format(mod))
                await interaction.edit_original_response(embed=derived.embed)
                if not await self.service.uninstall_package(server, folder, mod, version):
                    derived.embed.set_footer(
                        text=_("Mod {mod}_v{version} could not be uninstalled!").format(mod=mod, version=version),
                        icon_url=WARNING_ICON)
                else:
                    derived.embed.set_footer(text=_("Mod {} uninstalled.").format(mod))
                    derived.installed = await get_installed_mods(self.service, server)
                    derived.available = await get_available_mods(interaction, self.service, server)
                    await derived.render()
                await interaction.edit_original_response(embed=derived.embed, view=derived)

            async def download(derived, interaction: discord.Interaction):
                class UploadModal(Modal, title=_("Download a new Mod")):
                    # noinspection PyTypeChecker
                    url = TextInput(label=_("URL / GitHub Repo"), placeholder='https://github.com/...',
                                    style=TextStyle.short, required=True)
                    # noinspection PyTypeChecker
                    dest = TextInput(label=_("Destination (S=Saved Games / R=Root Folder)"), style=TextStyle.short,
                                     required=True, min_length=1, max_length=1)
                    # noinspection PyTypeChecker
                    version = TextInput(label=_("Version"), style=TextStyle.short, required=False, default='latest')

                    async def on_submit(_, interaction: discord.Interaction) -> None:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.defer()

                async def download(modal: UploadModal):
                    if utils.is_valid_url(modal.url.value):
                        folder = Folder.RootFolder if modal.dest.value == 'R' else Folder.SavedGames
                        if utils.is_github_repo(modal.url.value):
                            await self.service.download_from_repo(modal.url.value, folder, version=modal.version.value)
                        else:
                            await self.service.download(modal.url.value, folder)
                    else:
                        raise ValueError(_("Not a valid URL!"))

                modal = UploadModal()
                # noinspection PyUnresolvedReferences
                await interaction.response.send_modal(modal)
                if not await modal.wait():
                    if not utils.is_valid_url(modal.url.value):
                        derived.embed.set_footer(text=_("{} is not a valid URL!").format(modal.url.value),
                                                 icon_url=WARNING_ICON)
                    else:
                        derived.embed.set_footer(text=_("Downloading {} , please wait ...").format(modal.url.value))
                        for child in derived.children:
                            child.disabled = True
                        await interaction.edit_original_response(embed=derived.embed, view=derived)
                        try:
                            await download(modal)
                            embed.remove_footer()
                            derived.available = get_available_mods(interaction, self.service, server)
                        except aiohttp.client_exceptions.ClientResponseError as ex:
                            self.log.error(f"{ex.code}: {modal.url.value} {ex.message}")
                            embed.set_footer(text=f"{ex.code}: {ex.message}", icon_url=WARNING_ICON)
                        except Exception as ex:
                            embed.set_footer(text=_("Error: {}").format(ex.__class__.__name__), icon_url=WARNING_ICON)
                        for child in derived.children:
                            child.disabled = False
                        await derived.render()
                    await interaction.edit_original_response(embed=derived.embed, view=derived)

            async def cancel(derived, _: discord.Interaction):
                derived.stop()

        embed = discord.Embed(title=_("Mod Manager"), color=discord.Color.blue())
        embed.description = _("Install or uninstall mods to {}").format(server.name)
        view = PackageView(embed,
                           installed=await get_installed_mods(self.service, server),
                           available=await get_available_mods(interaction, self.service, server))
        await view.render()
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, view=view, ephemeral=utils.get_ephemeral(interaction))
        try:
            await view.wait()
        finally:
            await interaction.delete_original_response()

    @mods.command(name="install", description=_('Install mods'))
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin'])
    @app_commands.autocomplete(mod=available_mods_autocomplete)
    @app_commands.autocomplete(version=available_versions_autocomplete)
    async def _install(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                       mod: str, version: str):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status != Status.SHUTDOWN:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {} needs to be shut down to install mods.").format(server.name), ephemeral=True)
            return
        if '/' not in mod:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Mod {} not found!").format(mod), ephemeral=True)
            return
        _folder, package = mod.split('/')
        folder = Folder(_folder)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        reference = server if folder == Folder.SavedGames else server.node
        current = await self.service.get_installed_package(reference, folder, package)
        if current == version:
            await interaction.followup.send(
                _("Mod {mod}_v{version} is already installed!").format(mod=package, version=version), ephemeral=True)
            return
        if current:
            msg = await interaction.followup.send(
                _("Updating mod {mod} from {current_version} to {new_version}, please wait ...").format(
                    mod=package, current_version=current, new_version=version), ephemeral=ephemeral)
            if not await self.service.uninstall_package(server, folder, package, current):
                await msg.edit(content=_("Mod {mod}_v{version} could not be uninstalled!").format(
                    mod=package, version=version))
            elif not await self.service.install_package(server, folder, package, version):
                await msg.edit(content=_("Mod {mod}_v{version} could not be installed!").format(
                    mod=package, version=version))
            else:
                await msg.edit(content=_("Mod {mod} updated to version {version}.").format(
                    mod=package, version=version))
        else:
            msg = await interaction.followup.send(_("Installing mod {}, please wait ...").format(package),
                                                  ephemeral=ephemeral)
            if not await self.service.install_package(server, folder, package, version):
                await msg.edit(content=_("Installation of mod {} failed.").format(package))
            else:
                await msg.edit(content=_("Mod {mod} installed with version {version}.").format(
                    mod=package, version=version))

    @mods.command(description=_('Uninstall mods'))
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin'])
    @app_commands.autocomplete(mod=installed_mods_autocomplete)
    async def uninstall(self, interaction: discord.Interaction,
                        server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                        mod: str):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status != Status.SHUTDOWN:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {} needs to be shut down to uninstall mods.").format(server.name), ephemeral=True)
            return
        folder, package, version = mod.split('/')
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        msg = await interaction.followup.send(_("Uninstalling mod {}, please wait ...").format(package),
                                              ephemeral=ephemeral)
        if not await self.service.uninstall_package(server, Folder(folder), package, version):
            await msg.edit(content=_("Mod {mod}_v{version} could not be uninstalled!").format(mod=package,
                                                                                              version=version))
            return
        await msg.edit(content=_("Mod {} uninstalled.").format(package))

    @mods.command(name="list", description=_('List all installed mods'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin'])
    async def _list(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        installed: dict[Folder, list[tuple[str, str]]] = {}
        for folder in Folder:
            reference = server if folder == Folder.SavedGames else server.node
            installed[folder] = await self.service.get_installed_packages(reference, folder)
        if not len(installed[Folder.RootFolder]) and not len(installed[Folder.SavedGames]):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("No mod installed on server {}.").format(server.name),
                                                    ephemeral=True)
            return
        embed = discord.Embed(color=discord.Color.blue())
        embed.description = _("The following mods are installed on server {}:").format(server.name)
        for folder in Folder:
            if installed[folder]:
                embed.add_field(name=_("Folder"), value=folder.value)
                embed.add_field(name=_("Mod"), value='\n'.join([x[0] for x in installed[folder]]))
                embed.add_field(name=_("Version"), value='\n'.join([x[1] for x in installed[folder]]))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed)

    @mods.command(description=_('Download a mod'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_roles(['Admin'])
    @app_commands.describe(url=_("GitHub repo link or download URL"))
    @app_commands.autocomplete(version=repo_version_autocomplete)
    async def download(self, interaction: discord.Interaction, folder: Folder, url: str, version: str | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        if not utils.is_valid_url(url):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("{} is not a valid URL!").format(url), ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if utils.is_github_repo(url) and not version:
            try:
                version = await self.service.get_latest_repo_version(url)
            except aiohttp.ClientResponseError as ex:
                await interaction.followup.send(
                    _("Can't connect to {url}: {message}").format(url=url, message=ex.message), ephemeral=True
                )
                return
        if version:
            package_name = self.service.extract_repo_name(url).split('/')[-1]
            msg = await interaction.followup.send(
                _("Downloading {mod}_v{version} from GitHub ...").format(mod=package_name, version=version),
                ephemeral=ephemeral)
            try:
                await self.service.download_from_repo(url, folder, version=version)
            except FileExistsError:
                if not await utils.yn_question(interaction, _("File exists. Do you want to overwrite it?"),
                                               ephemeral=ephemeral):
                    await msg.edit(content=_("Aborted."))
                    return
                await self.service.download_from_repo(url, folder, version=version, force=True)
            except aiohttp.ClientResponseError as ex:
                await msg.edit(content=_("Error {code}: {mod}_v{version} {message}").format(
                    code=ex.status, mod=package_name, version=version, message=ex.message))
                return
            await msg.edit(content=_("{file} downloaded. Use {command} to install it.").format(
                file=f"{package_name}_v{version}",
                command=(await utils.get_command(self.bot, group='mods', name='install')).mention
            ))
        else:
            filename = url.split('/')[-1]
            msg = await interaction.followup.send(_("Downloading {} ...").format(filename), ephemeral=ephemeral)
            try:
                await self.service.download(url, folder)
            except FileExistsError:
                if not await utils.yn_question(interaction, _("File exists. Do you want to overwrite it?"),
                                               ephemeral=ephemeral):
                    return
                await self.service.download_from_repo(url, folder, version=version, force=True)
            await msg.edit(content=_("{file} downloaded. Use {command} to install it.").format(
                file=filename, command=(await utils.get_command(self.bot, group='mods', name='install')).mention))


async def setup(bot: DCSServerBot):
    await bot.add_cog(ModManager(bot))
