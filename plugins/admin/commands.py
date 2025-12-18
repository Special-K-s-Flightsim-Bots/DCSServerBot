import aiofiles
import asyncio
import discord
import os
import psycopg
import shutil
import sys

from core import utils, Plugin, Server, command, Node, UploadStatus, Group, Instance, Status, PlayerType, \
    PaginationReport, get_translation, DISCORD_FILE_SIZE_LIMIT, DEFAULT_PLUGINS, ServiceRegistry, NodeTransformer
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import TextInput, Modal
from functools import partial
from io import BytesIO
from pathlib import Path
from plugins.admin.listener import AdminEventListener
from plugins.admin.views import CleanupView
from plugins.modmanager.commands import get_installed_mods
from plugins.scheduler.views import ConfigView
from services.bot import DCSServerBot
from services.modmanager import ModManagerService
from typing import Literal, Type
from zipfile import ZipFile, ZIP_DEFLATED

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])


async def bans_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    choices: list[app_commands.Choice[int]] = [
        app_commands.Choice(name=f"{x['name']} ({x['ucid']})" if x['name'] else x['ucid'], value=x['ucid'])
        for x in await interaction.client.bus.bans()
        if not current or (x['name'] and current.casefold() in x['name'].casefold()) or current.casefold() in x['ucid']
    ]
    return choices[:25]


async def available_modules_autocomplete(interaction: discord.Interaction,
                                         current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        node = await utils.NodeTransformer().transform(interaction, interaction.namespace.node)
        available_modules = (set(await node.get_available_modules()) -
                             set(await node.get_installed_modules()))
        return [
            app_commands.Choice(name=x, value=x)
            for x in available_modules
            if 0 < len(x) <= 100 and (not current or current.casefold() in x.casefold())
        ][:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def installed_modules_autocomplete(interaction: discord.Interaction,
                                         current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        node = await utils.NodeTransformer().transform(interaction, interaction.namespace.node)
        available_modules = await node.get_installed_modules()
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x, value=x)
            for x in available_modules
            if not current or current.casefold() in x.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def label_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction, interaction.namespace.server)
        if not server:
            return []
        config = interaction.client.cogs['Admin'].get_config(server)
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x['label'], value=x['label']) for x in config['downloads']
            if (
                    (not current or current.casefold() in x['label'].casefold()) and
                    (not x.get('discord') or utils.check_roles(x['discord'], interaction.user)) and
                    (not x.get('restricted', False) or not server.node.locals.get('restrict_commands', False))
            )
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def _mission_file_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        server: Server = await utils.ServerTransformer().transform(interaction, interaction.namespace.server)
        file_list = await server.getAllMissionFiles()
        exp_base = await server.get_missions_dir()
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=os.path.relpath(x[0], exp_base), value=os.path.relpath(x[1], exp_base))
            for x in file_list
            if not current or current.casefold() in os.path.relpath(x[0], exp_base).casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def file_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction, interaction.namespace.server)
        if not server:
            return []
        label = interaction.namespace.what
        # missions will be handled differently
        if label == 'Missions':
            return await _mission_file_autocomplete(interaction, current)
        config = interaction.client.cogs['Admin'].get_config(server)
        try:
            config = next(x for x in config['downloads'] if x['label'] == label)
        except StopIteration:
            return []

        # check if we are allowed to display the list
        if (
                (config.get('discord') and not utils.check_roles(config['discord'], interaction.user)) or
                (config.get('restricted', False) and server.node.locals.get('restrict_commands', False))
        ):
            return []

        base_dir = utils.format_string(config['directory'], server=server)
        exp_base, file_list = await server.node.list_directory(base_dir, pattern=config['pattern'], traverse=True)
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=os.path.relpath(x, exp_base), value=os.path.relpath(x, exp_base))
            for x in file_list
            if not current or current.casefold() in os.path.relpath(x, base_dir).casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def plugins_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    return [
        app_commands.Choice(name=x.capitalize(), value=x.lower())
        for x in sorted(interaction.client.cogs)
        if not current or current.casefold() in x.casefold()
    ]


async def uninstallable_plugins(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    installed = set([x for x in interaction.client.node.plugins]) - set(DEFAULT_PLUGINS)
    return [
        app_commands.Choice(name=x.capitalize(), value=x.lower())
        for x in sorted(installed)
        if not current or current.casefold() in x.casefold()
    ]


async def installable_plugins(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    installed = set([x for x in interaction.client.node.plugins])
    available = set([d for d in os.listdir('plugins') if d not in ['__pycache__'] and os.path.isdir(os.path.join('plugins', d))])
    return [
        app_commands.Choice(name=x.capitalize(), value=x)
        for x in sorted(available - installed)
        if not current or current.casefold() in x.casefold()
    ]


async def get_dcs_branches(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current_branch, _ = await interaction.client.node.get_dcs_branch_and_version()
    testing = (interaction.client.node.locals.get('DCS', {}).get('user') is not None)
    if 'dcs_server' not in current_branch:
        branches = [('Release', 'release')]
        if testing:
            branches.append(('Testing', 'testing'))
            branches.append(('Nightly', 'nightly'))
    else:
        branches = [('Release', 'dcs_server.release')]
        if testing:
            branches.append(('Testing', 'dcs_server.testing'))
            branches.append(('Nightly', 'dcs_server.nightly'))
    return [
        app_commands.Choice(name=x[0], value=x[1])
        for x in branches
        if not current or current.casefold() in x[0].casefold()
    ]


async def get_dcs_versions(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    branch = interaction.namespace.branch
    if not branch:
        node = await NodeTransformer().transform(interaction, interaction.namespace.node)
        branch, _ = await node.get_dcs_branch_and_version()
    versions = await interaction.client.node.get_available_dcs_versions(branch)
    return [
        app_commands.Choice(name=x, value=x)
        for x in versions[::-1][:25]
        if not current or current.casefold() in x.casefold()
    ]


async def all_servers_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute("""
            SELECT server_name FROM servers WHERE server_name ILIKE %s
        """, ('%' + current + '%', ))
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=row[0], value=row[0])
            async for row in cursor
        ]
        return choices[:25]


async def extensions_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    server: Server = await utils.ServerTransformer().transform(interaction, interaction.namespace.server)
    extensions = await server.list_extension()
    current = current.casefold()
    choices: list[app_commands.Choice[str]] = [
        app_commands.Choice(name=x, value=x)
        for x in extensions
        if not current or current in x.casefold()
    ]
    return choices[:25]


class Admin(Plugin[AdminEventListener]):

    async def cog_load(self):
        await super().cog_load()
        self.cleanup.add_exception_type(psycopg.DatabaseError)
        self.cleanup.start()

    async def cog_unload(self):
        self.cleanup.cancel()
        await super().cog_unload()

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            self.log.info('  - No admin.yaml found, copying the sample.')
            shutil.copyfile('samples/plugins/admin.yaml', os.path.join(self.node.config_dir, 'plugins', 'admin.yaml'))
            config = super().read_locals()
        return config

    dcs = Group(name="dcs", description=_("Commands to manage your DCS installations"))

    @dcs.command(description=_('Bans a user by name or ucid'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def ban(self, interaction: discord.Interaction,
                  user: app_commands.Transform[discord.Member | str, utils.UserTransformer(
                      sel_type=PlayerType.PLAYER)]):

        class BanModal(Modal):
            reason = TextInput(label=_("Reason"), max_length=80, required=True)
            period = TextInput(label=_("Days (empty = forever)"), required=False)

            def __init__(self, user: discord.Member | str):
                super().__init__(title=_("Ban Details"))
                self.user = user

            async def on_submit(derived, interaction: discord.Interaction):
                ephemeral = utils.get_ephemeral(interaction)
                days = int(derived.period.value) if derived.period.value else None
                if isinstance(derived.user, discord.Member):
                    ucid = await self.bot.get_ucid_by_member(derived.user)
                    if not ucid:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(
                            _("Member {} is not linked!").format(derived.user.display_name), ephemeral=True)
                        return
                    name = derived.user.display_name
                elif utils.is_ucid(derived.user):
                    ucid = derived.user
                    # check if we should ban a member
                    name = await self.bot.get_member_or_name_by_ucid(ucid)
                    if isinstance(name, discord.Member):
                        name = name.display_name
                    elif not name:
                        name = ucid
                else:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_("{} is not a valid UCID!").format(user), 
                                                            ephemeral=ephemeral)
                    return
                await self.bus.ban(ucid, interaction.user.display_name, derived.reason.value, days)
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("Player {} banned on all servers").format(name) +
                                                        (_(" for {} days.").format(days) if days else "."),
                                                        ephemeral=ephemeral)
                await self.bot.audit(f'banned player {name} (ucid={ucid} with reason "{derived.reason.value}"' +
                                     (f' for {days} days.' if days else ' permanently.'), user=interaction.user)

            async def on_error(derived, _: discord.Interaction, error: Exception) -> None:
                self.log.exception(error)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(BanModal(user))

    @dcs.command(description=_('Unbans a user by name or ucid'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(ucid="user")
    @app_commands.autocomplete(ucid=bans_autocomplete)
    async def unban(self, interaction: discord.Interaction, ucid: str):
        await self.bus.unban(ucid)
        name = await self.bot.get_member_or_name_by_ucid(ucid)
        if isinstance(name, discord.Member):
            name = name.display_name
        elif not name:
            name = ucid
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Player {} unbanned on all servers.").format(name),
                                                ephemeral=utils.get_ephemeral(interaction))
        await self.bot.audit(f'unbanned player {name} (ucid={ucid})', user=interaction.user)

    @dcs.command(description=_('Shows active bans'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(user=bans_autocomplete)
    async def bans(self, interaction: discord.Interaction, user: str):
        try:
            ban = next(x for x in await self.bus.bans() if x['ucid'] == user)
        except StopIteration:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("User with UCID {} is not banned.").format(user), ephemeral=True)
            return
        embed = discord.Embed(title=_('Ban Information'), color=discord.Color.blue())
        if ban['discord_id'] != -1:
            user = self.bot.get_user(ban['discord_id'])
        else:
            user = None
        embed.add_field(name=utils.escape_string(user.name if user else ban['name'] if ban['name'] else _('<unknown>')),
                        value=ban['ucid'])
        if ban['banned_until'].year == 9999:
            until = _('never')
        else:
            until = ban['banned_until'].strftime('%y-%m-%d %H:%M')
        embed.add_field(name=_("Banned by: {}").format(ban['banned_by']), value=_("Exp.: {}").format(until))
        embed.add_field(name=_('Reason'), value=ban['reason'])
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=utils.get_ephemeral(interaction))

    @dcs.command(description=_('Update your DCS installations'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(warn_time=_("Time in seconds to warn users before shutdown"))
    @app_commands.autocomplete(branch=get_dcs_branches)
    @app_commands.autocomplete(version=get_dcs_versions)
    async def update(self, interaction: discord.Interaction,
                     node: app_commands.Transform[Node, utils.NodeTransformer],
                     warn_time: app_commands.Range[int, 0] = 60,
                     announce: bool = True,
                     branch: str | None = None,
                     version: str | None = None,
                     force: bool | None = False):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        _branch, old_version = await node.get_dcs_branch_and_version()
        if not branch:
            branch = _branch
        try:
            new_version = version or await node.get_latest_version(branch)
        except Exception:
            await interaction.followup.send(_("Can't get version information from ED, possible auth-server outage!"),
                                            ephemeral=True)
            return

        if not force:
            if old_version == new_version and branch == _branch:
                await interaction.followup.send(
                    _('Your installed version {version} is the latest on branch {branch}.').format(version=old_version,
                                                                                                   branch=branch),
                    ephemeral=ephemeral)
                return
            elif new_version:
                if not await utils.yn_question(
                        interaction, _('Would you like to update from version {old_version}@{old_branch} to '
                                       '{new_version}@{new_branch}?\nAll running DCS servers will be shut down!'
                                       ).format(old_version=old_version, old_branch=_branch, new_version=new_version,
                                                new_branch=branch), ephemeral=ephemeral):
                    await interaction.followup.send(_("Aborted."))
                    return
            else:
                await interaction.followup.send(
                    _("Can't update branch {}. You might need to provide proper DCS credentials to do so.").format(branch),
                    ephemeral=ephemeral)
                return

        await self.bot.audit(f"started an update of all DCS servers on node {node.name}.", user=interaction.user)
        msg = await interaction.followup.send(_("Updating DCS World to the newest version, please wait ..."),
                                        ephemeral=ephemeral)
        try:
            rc = await node.dcs_update(warn_times=[warn_time] or [120, 60], branch=branch, version=new_version,
                                       announce=announce)
            if rc == 0:
                branch, new_version = await node.get_dcs_branch_and_version()
                await msg.edit(content=_("DCS updated to version {version}@{branch} on node {name}."
                                         ).format(version=new_version, branch=branch, name=node.name))
                await self.bot.audit(f"updated DCS from {old_version} to {new_version} on node {node.name}.",
                                     user=interaction.user)
            elif rc == 2:
                await msg.edit(
                    content=_("DCS World update on node {name} was aborted (check disk space)!").format(name=node.name)
                )
            elif rc in [3, 350]:
                branch, new_version = await node.get_dcs_branch_and_version()
                await msg.edit(
                    content=_("DCS World updated to version {version}@{branch} on node {name}.\n"
                              "The updater has requested a **reboot** of the system!").format(
                    version=new_version, branch=branch, name=node.name)
                )
            else:
                await msg.edit(
                    content=_("Error while updating DCS on node {name}, code={rc}, message={message}").format(
                        name=node.name, rc=rc, message=utils.get_win32_error_message(rc)))
        except (TimeoutError, asyncio.TimeoutError):
            await interaction.followup.send(
                content=_("The update takes longer than 10 minutes, please check back regularly, if it has finished."),
                ephemeral=True)

    @dcs.command(description=_('Repair your DCS installations'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(warn_time=_("Time in seconds to warn users before shutdown"))
    async def repair(self, interaction: discord.Interaction,
                     node: app_commands.Transform[Node, utils.NodeTransformer],
                     slow: bool | None = False, check_extra_files: bool | None = False,
                     warn_time: app_commands.Range[int, 0] = 60):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        await self.bot.audit(f"started a repair of DCS World on node {node.name}.", user=interaction.user)
        msg = await interaction.followup.send(_("Repairing DCS World, please wait ..."), ephemeral=ephemeral)
        try:
            rc = await node.dcs_repair(
                warn_times=[warn_time] or [120, 60],
                slow=slow,
                check_extra_files=check_extra_files
            )
            if rc == 0:
                await msg.edit(content=_("DCS World repaired on node {}.").format(node.name))
                await self.bot.audit(f"repaired DCS World on node {node.name}.", user=interaction.user)
            elif rc == 1:
                branch, version = await node.get_dcs_branch_and_version()
                tempfolder = f"DCS.{branch.replace('.', '')}"
                path = f"%TEMP%\\{tempfolder}\\autoupdate_templog.txt"
                try:
                    file = await node.read_file(path)
                except FileNotFoundError:
                    await msg.edit(
                        content=_("Error while repairing DCS World on node {name}. "
                                  "Check your autoupdater_log.txt").format(name=node.name))
                    return
                await msg.edit(content=_("Repair of DCS World failed on node {}.").format(node.name))
                await interaction.followup.send(file=discord.File(BytesIO(file), "autoupdate_templog.txt"),
                                                ephemeral=ephemeral)
            elif rc == 2:
                await msg.edit(
                    content=_("The repair of DCS World was cancelled on node {name}.").format(name=node.name))
            elif rc == -1:
                path = os.path.join("logs", "dcs_repair.log")
                try:
                    file = await node.read_file(path)
                except FileNotFoundError:
                    await msg.edit(
                        content=_("Error while repairing DCS World on node {name}. "
                                  "Check your dcs_repair.log").format(name=node.name))
                    return
                await msg.edit(content=_("Repair of DCS World failed on node {}.").format(node.name))
                await interaction.followup.send(file=discord.File(BytesIO(file), "dcs_repair.log"),
                                                ephemeral=ephemeral)
            elif rc == -2:
                await msg.edit(
                    content=_("You cannot run a DCS repair on a headless system without an active session!"))

        except (TimeoutError, asyncio.TimeoutError):
            await interaction.followup.send(
                content=_("The repair takes longer than 10 minutes. It will generate an audit message when finished."),
                ephemeral=True)
        except PermissionError as ex:
            await msg.edit(
                content=_("Could not repair DCS World on node {name} due to a permission error: {error}").format(
                    name=node.name, error=repr(ex)))

    @dcs.command(name='install', description=_('Install modules in your DCS server'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(module=available_modules_autocomplete)
    async def _install(self, interaction: discord.Interaction,
                       node: app_commands.Transform[Node, utils.NodeTransformer], module: str):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        num_servers = len([x for x in node.instances.values() if x.server and x.server.status != Status.SHUTDOWN])
        if num_servers and not await utils.yn_question(
                interaction, _("Shutdown all servers on node {} for the installation?").format(node.name),
                ephemeral=ephemeral):
            return
        await interaction.followup.send(
            _("Installing module {module} on node {node}, please wait ...").format(module=module, node=node.name),
            ephemeral=ephemeral)
        await node.handle_module('install', module)
        # use channel.send instead, as the webhook might be outdated
        await interaction.channel.send(_("Module {module} installed on node {node}.").format(
            module=module, node=node.name))

    @dcs.command(name='uninstall', description=_('Uninstall modules from your DCS server'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(module=installed_modules_autocomplete)
    async def _uninstall(self, interaction: discord.Interaction,
                         node: app_commands.Transform[Node, utils.NodeTransformer], module: str):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        num_servers = len([x for x in node.instances.values() if x.server and x.server.status != Status.SHUTDOWN])
        if num_servers and not await utils.yn_question(
                interaction, _("Shutdown all servers on node {} for the uninstallation?").format(node.name),
                ephemeral=ephemeral):
            await interaction.followup.send(_("Aborted."), ephemeral=ephemeral)
            return
        await node.handle_module('uninstall', module)
        await interaction.followup.send(
            _("Module {module} uninstalled on node {node}.").format(module=module, node=node.name), ephemeral=ephemeral)

    @dcs.command(name='info', description=_('Info about your DCS installation'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def info(self, interaction: discord.Interaction,
                   node: app_commands.Transform[Node, utils.NodeTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        modules = await node.get_installed_modules()
        if not modules:
            await interaction.followup.send(_("There are no modules installed on this server."), ephemeral=ephemeral)
            return
        embed = discord.Embed(color=discord.Color.blue())
        embed.description = _("Installed modules on node {}").format(node.name)
        embed.add_field(name=_("Module"), value='\n'.join([f'- {x}' for x in modules]))
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @command(description=_('Download files from your server'))
    @app_commands.guild_only()
    @utils.app_has_roles(['Admin', 'DCS Admin'])
    @app_commands.autocomplete(what=label_autocomplete)
    @app_commands.autocomplete(filename=file_autocomplete)
    async def download(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer],
                       what: str, filename: str) -> None:
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        config = next(x for x in self.get_config(server)['downloads'] if x['label'] == what)
        # double-check if that user can really download these files
        if (
                (config.get('discord') and not utils.check_roles(config['discord'], interaction.user)) or
                (config.get('restricted', False) and server.node.locals.get('restrict_commands', False))
        ):
            raise app_commands.CheckFailure()
        if what == 'Missions':
            base_dir = await server.get_missions_dir()
        else:
            base_dir = utils.format_string(config['directory'], server=server)
        try:
            # make sure nobody injected a wrong path
            utils.sanitize_filename(os.path.abspath(os.path.join(os.path.expandvars(base_dir), filename)),
                                    os.path.expandvars(base_dir))
            path = os.path.join(base_dir, filename)
        except ValueError:
            await self.bot.audit("User attempted a relative file injection!",
                                 user=interaction.user, base_dir=base_dir, file=filename)
            await interaction.followup.send(_("You have been reported for trying to inject a relative path!"))
            return
        # now continue to download
        if filename.endswith('.orig'):
            filename = filename[:-5]
        try:
            file = await server.node.read_file(path)
        except FileNotFoundError:
            self.log.error(f"File {path} not found.")
            await interaction.followup.send(content=_("File {file} not found.").format(file=filename),
                                            ephemeral=True)
            return
        target = config.get('target')
        if target:
            target = target.format(server=server)
        if not filename.endswith('.zip') and not filename.endswith('.miz') and not filename.endswith('.acmi') and \
                len(file) >= DISCORD_FILE_SIZE_LIMIT:
            zip_buffer = BytesIO()
            with ZipFile(zip_buffer, "a", ZIP_DEFLATED, False) as zip_file:
                zip_file.writestr(filename, file)
            file = zip_buffer.getvalue()
            filename += '.zip'
        if not target:
            dm_channel = await interaction.user.create_dm()
            for channel in [dm_channel, interaction.channel]:
                try:
                    await channel.send(file=discord.File(fp=BytesIO(file), filename=os.path.basename(filename)))
                    if channel == dm_channel:
                        await interaction.followup.send(_('File sent as a DM.'), ephemeral=ephemeral)
                    else:
                        await interaction.followup.send(_('Here is your file:'), ephemeral=ephemeral)
                    break
                except discord.HTTPException:
                    continue
            else:
                await interaction.followup.send(_('File too large. You need a higher boost level for your server.'),
                                                ephemeral=ephemeral)
                return
        elif target.startswith('<'):
            channel = self.bot.get_channel(int(target[4:-1]))
            if channel:
                try:
                    await channel.send(file=discord.File(fp=BytesIO(file), filename=os.path.basename(filename)))
                except discord.HTTPException:
                    await interaction.followup.send(_('File too large. You need a higher boost level for your server.'),
                                                    ephemeral=ephemeral)
                if channel != interaction.channel:
                    await interaction.followup.send(_('File sent to the configured channel.'), ephemeral=ephemeral)
                else:
                    await interaction.followup.send(_('Here is your file:'), ephemeral=ephemeral)
            else:
                await interaction.followup.send(_('Channel {} not found, check your admin.yaml!').format(target[4:-1]),
                                                ephemeral=ephemeral)
        else:
            target_file = os.path.join(os.path.expandvars(target), os.path.basename(filename))
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            async with aiofiles.open(target_file, mode='wb') as outfile:
                await outfile.write(file)
            await interaction.followup.send(_('File copied to the specified location.'), ephemeral=ephemeral)
        await self.bot.audit(f"downloaded {filename}", user=interaction.user, server=server)

    @command(name='prune', description=_('Prune unused data in the database'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(_server=all_servers_autocomplete)
    @app_commands.rename(_server="server")
    async def _prune(self, interaction: discord.Interaction,
                     user: app_commands.Transform[discord.Member | str,  utils.UserTransformer(
                         sel_type=PlayerType.PLAYER)] | None = None,
                     _server: str | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        if not _server and not user:
            embed = discord.Embed(title=_(":warning: Database Prune :warning:"))
            embed.description = _("You are going to delete data from your database. Be advised.\n\n"
                                  "Please select the data to be pruned:")
            view = CleanupView()
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
            try:
                await view.wait()
            finally:
                await interaction.delete_original_response()
            if view.cmd == "cancel":
                await interaction.followup.send(_('Aborted.'), ephemeral=ephemeral)
                return
        elif user and not await utils.yn_question(
                interaction, _("We are going to delete all data of user {}. Are you sure?").format(
                    user.display_name if isinstance(user, discord.Member) else user)):
            await interaction.followup.send(_('Aborted.'), ephemeral=ephemeral)
            return
        elif _server and not await utils.yn_question(
                interaction, _("We are going to delete all data of server {}. Are you sure?").format(_server)):
            await interaction.followup.send(_('Aborted.'), ephemeral=ephemeral)
            return

        async with self.apool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cursor:
                    if user:
                        if isinstance(user, discord.Member):
                            ucid = await self.bot.get_ucid_by_member(user, verified=True)
                            if not ucid:
                                await interaction.followup.send("Member {} is not linked!".format(user.display_name))
                                return
                        elif utils.is_ucid(user):
                            ucid = user
                        else:
                            await interaction.followup.send("{} is not a valid UCID!".format(user))
                            return
                        await cursor.execute('DELETE FROM players WHERE ucid = %s', (ucid, ))
                        if isinstance(user, discord.Member):
                            await interaction.followup.send(_("Data of user {} deleted.").format(user.display_name))
                        else:
                            await interaction.followup.send(_("Data of UCID {} deleted.").format(ucid))
                        return
                    elif _server:
                        await cursor.execute('DELETE FROM servers WHERE server_name = %s', (_server, ))
                        await interaction.followup.send(_("Data of server {} deleted.").format(_server))
                        return
                    elif view.what in ['users', 'non-members']:
                        sql = f"""
                            SELECT ucid FROM players 
                            WHERE last_seen < (DATE((now() AT TIME ZONE 'utc')) - interval '{view.age} days')
                        """
                        if view.what == 'non-members':
                            sql += ' AND discord_id = -1'
                        await cursor.execute(sql)
                        ucids = [row[0] async for row in cursor]
                        if not ucids:
                            await interaction.followup.send(_('No players to prune.'), ephemeral=ephemeral)
                            return
                        if not await utils.yn_question(
                                interaction, _("This will delete {} players incl. their stats from the database.\n"
                                               "Are you sure?").format(len(ucids)), ephemeral=ephemeral):
                            return
                        for ucid in ucids:
                            await cursor.execute('DELETE FROM players WHERE ucid = %s', (ucid, ))
                        await interaction.followup.send(f"{len(ucids)} players pruned.", ephemeral=ephemeral)
                    elif view.what == 'data':
                        days = int(view.age)
                        if not await utils.yn_question(
                                interaction, _("This will delete all data older than {} days from the database.\n"
                                               "Are you sure?").format(days), ephemeral=ephemeral):
                            return
                        # some plugins need to prune their data based on the provided days
                        for plugin in self.bot.cogs.values():  # type: Plugin
                            await plugin.prune(conn, days)
                        await interaction.followup.send(_("All data older than {} days pruned.").format(days),
                                                        ephemeral=ephemeral)
        await self.bot.audit(f'pruned the database', user=interaction.user)

    node_group = Group(name="node", description=_("Commands to manage your nodes"))

    @node_group.command(description=_('Statistics of your nodes'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def statistics(self, interaction: discord.Interaction,
                         node: app_commands.Transform[Node, utils.NodeTransformer] | None = None,
                         period: Literal['Hour', 'Day', 'Week', 'Month'] | None = 'Hour'):
        report = PaginationReport(interaction, self.plugin_name, 'nodestats.json')
        if not node:
            node = self.node
        await report.render(node=node.name, period=period)

    @node_group.command(name='list', description=_('Status of all nodes'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def _list(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        embed = discord.Embed(title=_("DCSServerBot Cluster Overview"), color=discord.Color.blue())
        for name, node in self.node.all_nodes.items():
            # ignore inactive nodes
            if not node:
                continue

            names = []
            instances = []
            status = []
            for server in [x for x in self.bot.servers.values() if x.node.name == node.name]:
                instances.append(server.instance.name)
                names.append(server.name)
                status.append(server.status.name)
            if names:
                # print the master in bold
                title = f"**[{name}]**" if name == self.node.name else f"[{name}]"
                if await node.upgrade_pending():
                    embed.set_footer(text=_("🆕 Update available"))
                    title += " 🆕"

                embed.add_field(name="▬" * 32, value=title, inline=False)
                embed.add_field(name=_("Instance"), value='\n'.join(instances))
                embed.add_field(name=_("Server"), value='\n'.join(names))
                embed.add_field(name=_("Status"), value='\n'.join(status))
            else:
                embed.add_field(name="▬" * 32, value=f"_[{name}]_", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    async def run_on_nodes(self, interaction: discord.Interaction, method: str, node: Node | None = None,
                           ephemeral: bool | None = True):
        if not node:
            question = _("Are you sure you want to proceed?")
            message = _("This will {} **all** nodes.").format(_(method))
        else:
            question = _("Are you sure you want to {} node `{}`?").format(_(method), node.name)
            message = None
        embed = discord.Embed(color=discord.Color.red())
        embed.description = message
        embed.set_thumbnail(
            url="https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/warning.png?raw=true")
        if not await utils.yn_question(interaction, question=question, embed=embed, ephemeral=ephemeral):
            await interaction.followup.send(_('Aborted.'), ephemeral=ephemeral)
            return
        if method != 'upgrade' or node:
            for n in await self.node.get_active_nodes():
                if not node or n == node.name:
                    await self.bus.send_to_node({
                        "command": "rpc",
                        "object": "Node",
                        "method": method
                    }, node=n)
                    await interaction.followup.send(_('Node {node} - {method} sent.').format(node=n, method=_(method)),
                                                    ephemeral=ephemeral)
        if not node or node.name == self.node.name:
            await interaction.followup.send(
                (_("All nodes are") if not node else _("Master is")) + _(' going to {} **NOW**.').format(_(method)),
                ephemeral=ephemeral)
            if method == 'shutdown':
                await self.node.shutdown()
            elif method == 'upgrade':
                await self.node.upgrade()
            elif method == 'restart':
                await self.node.restart()

    @node_group.command(description=_('Shuts a specific node down'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def shutdown(self, interaction: discord.Interaction,
                       node: app_commands.Transform[Node, utils.NodeTransformer] | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        await self.run_on_nodes(interaction, "shutdown", node, ephemeral=ephemeral)

    @node_group.command(description=_('Restarts a specific node'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def restart(self, interaction: discord.Interaction,
                      node: app_commands.Transform[Node, utils.NodeTransformer] | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        await self.run_on_nodes(interaction, "restart", node, ephemeral=ephemeral)

    @node_group.command(description=_('Shuts down all servers, enables maintenance'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.describe(shutdown=_('Shuts all servers down (default: on)'))
    async def offline(self, interaction: discord.Interaction,
                      node: app_commands.Transform[Node, utils.NodeTransformer] | None,
                      shutdown: bool | None = True):
        async def _node_offline(node_name: str):
            tasks = []
            if shutdown:
                msg = await interaction.followup.send(_("Shutting down all servers on node {} ...").format(node_name))
            for server in self.bot.servers.values():
                if server.node.name == node_name:
                    server.maintenance = True
                    if shutdown:
                        tasks.append(asyncio.create_task(server.shutdown()))
            if shutdown:
                await asyncio.gather(*tasks)
                await msg.edit(content=_("All servers on node {} were shut down.").format(node_name))
            await interaction.followup.send(_("Node {} is now offline.").format(node_name))
            await self.bot.audit(f"took node {node_name} offline.", user=interaction.user)

        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)

        if shutdown:
            question = _("Are you sure you want to proceed?")
            if not node:
                message = _("This will shutdown **all** servers on **all** nodes.")
            else:
                message = _("This will shutdown **all** servers on node `{}`.").format(node.name)
            embed = discord.Embed(color=discord.Color.red())
            embed.description = message
            embed.set_thumbnail(
                url="https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/warning.png?raw=true")
            if not await utils.yn_question(interaction, question=question, embed=embed, ephemeral=ephemeral):
                await interaction.followup.send(_('Aborted.'), ephemeral=ephemeral)
                return

        if node:
            await _node_offline(node.name)
        else:
            tasks = [_node_offline(node.name) for node in self.node.all_nodes.values()]
            tasks.append(_node_offline(self.node.name))
            await asyncio.gather(*tasks)

    @node_group.command(description=_('Clears the maintenance mode for all servers'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.describe(startup=_('Start all your servers (default: off)'))
    async def online(self, interaction: discord.Interaction,
                     node: app_commands.Transform[Node, utils.NodeTransformer] | None,
                     startup: bool | None = False):

        async def _startup(server: Server):
            try:
                await server.startup()
                server.maintenance = False
            except (TimeoutError, asyncio.TimeoutError):
                await interaction.followup.send(_("Timeout while starting server {}!").format(server.name),
                                                ephemeral=True)

        async def _node_online(node_name: str):
            next_startup = 0
            for server in [x for x in self.bot.servers.values() if x.node.name == node_name]:
                if startup:
                    self.loop.call_later(delay=next_startup,
                                         callback=partial(asyncio.create_task, _startup(server)))
                    next_startup += startup_delay
                else:
                    server.maintenance = False
            await interaction.followup.send(_("Node {} is now online.").format(node_name), ephemeral=ephemeral)
            await self.bot.audit(f"took node {node_name} online.", user=interaction.user)

        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        startup_delay = self.get_config(plugin_name='scheduler').get('startup_delay', 10)
        if node:
            await _node_online(node.name)
        else:
            for node in await self.node.get_active_nodes():
                await _node_online(node)
            await _node_online(self.node.name)

    @node_group.command(description=_('Upgrade DCSServerBot'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def upgrade(self, interaction: discord.Interaction,
                      node: app_commands.Transform[Node, utils.NodeTransformer] | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if not node:
            node = self.node
            cluster = True
        else:
            cluster = False
        if not await node.upgrade_pending():
            await interaction.followup.send(_("There is no upgrade available for ") +
                                            (_("your cluster") if cluster else _("node {}").format(node.name)),
                                            ephemeral=ephemeral)
            return
        if node and not node.master and not await utils.yn_question(
                interaction, _("You are trying to upgrade an agent node in a cluster. Are you really sure?"),
                ephemeral=ephemeral):
            await interaction.followup.send(_('Aborted'), ephemeral=ephemeral)
            return
        await self.run_on_nodes(interaction, "upgrade", node if not cluster else None, ephemeral=ephemeral)

    @node_group.command(description=_('Run a shell command on a node'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def shell(self, interaction: discord.Interaction,
                    node: app_commands.Transform[Node, utils.NodeTransformer],
                    cmd: str, timeout: app_commands.Range[int, 10, 300] | None = 60):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        try:
            await self.bot.audit(f"ran a shell command:\n```cmd\n{cmd}\n```", user=interaction.user)
            stdout, stderr = await node.shell_command(cmd, timeout)
            embed = discord.Embed(colour=discord.Color.blue())
            if stdout:
                embed.description = "```" + stdout[:4090] + "```"
            if stderr:
                embed.set_footer(text=stderr[:2048])
            if not stdout and not stderr:
                embed.description = _("```Command executed.```")
            await interaction.followup.send(embed=embed)
        except (TimeoutError, asyncio.TimeoutError):
            await interaction.followup.send(_("Timeout during shell command."))

    @node_group.command(description=_("Add/create an instance\n"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(name=utils.InstanceTransformer(unused=True).autocomplete)
    @app_commands.describe(name=_("Either select an existing instance or enter the name of a new one"))
    @app_commands.describe(template=_("Take this instance configuration as a reference"))
    async def add_instance(self, interaction: discord.Interaction,
                           node: app_commands.Transform[Node, utils.NodeTransformer], name: str,
                           template: app_commands.Transform[Instance, utils.InstanceTransformer] | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        instance = await node.add_instance(name, template=template.name if template else "")
        if instance:
            await self.bot.audit(f"added instance {instance.name} to node {node.name}.", user=interaction.user)
            server: Server = instance.server
            view = ConfigView(self.bot, server)
            embed = discord.Embed(title=_("Instance \"{}\" created.\n"
                                          "Do you want to configure a server for this instance?").format(name),
                                  color=discord.Color.blue())
            try:
                await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
            except Exception as ex:
                self.log.exception(ex)
            if not await view.wait() and not view.cancelled:
                channels = {
                    "status": server.locals.get('channels', {}).get('status', -1),
                    "chat": server.locals.get('channels', {}).get('chat', -1)
                }
                if not self.bot.locals.get('channels', {}).get('admin'):
                    channels['admin'] = server.locals.get('channels', {}).get('admin', -1)
                await server.update_channels(channels)
                server.status = Status.SHUTDOWN
                await interaction.followup.send(
                    _("Server {server} assigned to instance {instance}.").format(server=server.name,
                                                                                 instance=instance.name),
                    ephemeral=ephemeral)
                await interaction.followup.send(_("""
Instance {instance} added to node {node}.
Please make sure you forward the following ports:
```
- DCS Port:    {dcs_port}
- WebGUI Port: {webgui_port}
```
                """).format(instance=name, node=node.name, dcs_port=repr(instance.dcs_port),
                            webgui_port=repr(instance.webgui_port)), ephemeral=ephemeral)
            else:
                await instance.server.unlink()
                await interaction.followup.send(
                    _("Instance {} created blank with no server assigned.").format(instance.name), ephemeral=ephemeral)
        else:
            await interaction.followup.send(
                _("Instance {instance} could not be added to node {node}, see log.").format(instance=name,
                                                                                            node=node.name),
                ephemeral=ephemeral)

    @node_group.command(description=_("Delete an instance\n"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def delete_instance(self, interaction: discord.Interaction,
                              node: app_commands.Transform[Node, utils.NodeTransformer],
                              instance: app_commands.Transform[Instance, utils.InstanceTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        if instance.server:
            message = _("The instance is in use by server \"{}\".\nDo you really want to delete it?").format(
                instance.server.name)
        else:
            message = _("Do you really want to delete instance {}?").format(instance.name)
        if not await utils.yn_question(interaction, message, ephemeral=ephemeral):
            await interaction.followup.send(_("Aborted."), ephemeral=ephemeral)
            return
        if instance.server and instance.server.status in [Status.STOPPED, Status.RUNNING, Status.PAUSED]:
            await instance.server.shutdown(force=True)

        # uninstall mods
        mod_manager: ModManagerService = ServiceRegistry.get(ModManagerService)
        if mod_manager and mod_manager.is_running() and instance.server:
            for folder, package, version in await get_installed_mods(mod_manager, server=instance.server):
                await mod_manager.uninstall_package(instance.server, folder, package, version)

        remove_files = await utils.yn_question(
            interaction, _("Do you want to remove the directory\n{}?").format(instance.home), ephemeral=ephemeral)
        try:
            await node.delete_instance(instance, remove_files)
            await interaction.followup.send(
                _("Instance {instance} removed from node {node}.").format(instance=instance.name,
                                                                          node=node.name), ephemeral=ephemeral)
            await self.bot.audit(f"removed instance {instance.name} from node {node.name}.", user=interaction.user)
        except PermissionError:
            await interaction.followup.send(
                _("Instance {} could not be deleted, because the directory is in use.").format(instance.name),
                ephemeral=ephemeral)

    @node_group.command(description=_("Rename an instance\n"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def rename_instance(self, interaction: discord.Interaction,
                              node: app_commands.Transform[Node, utils.NodeTransformer],
                              instance: app_commands.Transform[Instance, utils.InstanceTransformer], new_name: str):
        ephemeral = utils.get_ephemeral(interaction)
        if instance.server and instance.server.status in [Status.STOPPED, Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {} has to be shut down before renaming the instance!").format(instance.server.name),
                ephemeral=ephemeral)
            return
        if not await utils.yn_question(interaction,
                                       _("Do you really want to rename instance {}?").format(instance.name),
                                       ephemeral=ephemeral):
            await interaction.followup.send(_('Aborted.'), ephemeral=ephemeral)
            return
        old_name = instance.name
        msg = await interaction.followup.send(
            _("Renaming instance {} to {}. This will take a bit, standby ...").format(old_name, new_name)
        )
        try:
            await node.rename_instance(instance, new_name)
            await msg.edit(content=_("Instance {old_name} renamed to {new_name}.").format(
                old_name=old_name, new_name=instance.name)
            )
            await self.bot.audit(f"renamed instance {old_name} to {instance.name}.", user=interaction.user)
        except PermissionError:
            await msg.edit(
                content=_("Instance {} could not be renamed, because the directory is in use.").format(old_name)
            )
        except FileExistsError:
            await msg.edit(
                content=_("Instance {} could not be renamed, because the directory already exist.").format(old_name)
            )
        except Exception as ex:
            await msg.edit(
                content=_("Instance {} could not be renamed: {}.").format(old_name, ex)
            )
            self.log.exception(ex)

    @node_group.command(description=_("Shows CPU topology"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @app_commands.check(lambda interaction: sys.platform == 'win32')
    @utils.app_has_role('Admin')
    async def cpuinfo(self, interaction: discord.Interaction,
                      node: app_commands.Transform[Node, utils.NodeTransformer]):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        image = await node.get_cpu_info()
        await interaction.followup.send(file=discord.File(fp=BytesIO(image), filename='cpuinfo.png'))

    plug = Group(name="plugin", description=_("Commands to manage your DCSServerBot plugins"))

    @plug.command(name='install', description=_("Install Plugin"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @app_commands.autocomplete(plugin=installable_plugins)
    @utils.app_has_role('Admin')
    async def _install(self, interaction: discord.Interaction, plugin: str):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if not await self.node.install_plugin(plugin):
            await interaction.followup.send(
                _("Plugin {} could not be installed, check the log for details.").format(plugin), ephemeral=ephemeral)
            return
        message = _("Plugin {} installed.").format(plugin)
        if os.path.exists(os.path.join('plugins', plugin.lower(), 'lua')):
            message += _('\nPlease restart your DCS servers to apply the change.')
        await interaction.followup.send(message, ephemeral=ephemeral)

    @plug.command(name='uninstall', description=_("Uninstall Plugin"))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @app_commands.autocomplete(plugin=uninstallable_plugins)
    @utils.app_has_role('Admin')
    async def _uninstall(self, interaction: discord.Interaction, plugin: str):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if not await self.node.uninstall_plugin(plugin):
            await interaction.followup.send(
                _("Plugin {} could not be uninstalled, check the log for details.").format(plugin),
                ephemeral=ephemeral)
            return
        await interaction.followup.send(
            _("Plugin {} uninstalled. Please restart your DCS servers to apply the change!").format(plugin),
            ephemeral=ephemeral)

    @plug.command(description=_('Reload Plugin'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(plugin=plugins_autocomplete)
    async def reload(self, interaction: discord.Interaction, plugin: str | None):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if plugin:
            if await self.bot.reload(plugin):
                await interaction.followup.send(_('Plugin {} reloaded.').format(plugin), ephemeral=ephemeral)
            else:
                await interaction.followup.send(
                    _('Plugin {} could not be reloaded, check the log for details.').format(plugin),
                    ephemeral=ephemeral)
        else:
            if await self.bot.reload():
                await interaction.followup.send(_('All plugins reloaded.'), ephemeral=ephemeral)
            else:
                await interaction.followup.send(
                    _('One or more plugins could not be reloaded, check the log for details.'), ephemeral=ephemeral)
        # for server in self.bot.servers.values():
        #    if server.status == Status.STOPPED:
        #        await server.send_to_dcs({"command": "reloadScripts"})

    ext = Group(name="extension", description=_("Commands to manage your DCSServerBot extensions"))

    @ext.command(description=_('Enable Extension'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(extension=extensions_autocomplete)
    async def enable(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer], extension: str):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        await interaction.followup.send(_("Enabling extension {}...").format(extension), ephemeral=ephemeral)
        # set enabled in the nodes.yaml
        if server.status in [Status.STOPPED, Status.SHUTDOWN]:
            config_file_path = Path(os.path.join(interaction.client.node.config_dir, 'nodes.yaml'))
            async with aiofiles.open(config_file_path, encoding='utf-8') as file:
                config_data = yaml.load(await file.read())
            ext = config_data[server.node.name].get('instances', {}).get(server.instance.name, {}).get('extensions', {}).get(extension)
            if not ext:
                await interaction.followup.send(_("You need to add a configuration for extension {} first.").format(
                    extension), ephemeral=ephemeral)
                return
            if not ext.get('enabled', True):
                ext['enabled'] = True
                with open(config_file_path, mode='w', encoding='utf-8') as outfile:
                    yaml.dump(config_data, outfile)
                await interaction.followup.send(_("Extension {} enabled on server {}.").format(
                    extension, server.display_name), ephemeral=ephemeral)
            else:
                await interaction.followup.send(_("Extension {} is already enabled on server {}.").format(
                    extension, server.display_name), ephemeral=ephemeral)
                return

        elif server.status in [Status.RUNNING, Status.PAUSED]:
            await server.config_extension(extension, {"enabled": True})
            # do we need to initialize the extension?
            try:
                await server.run_on_extension(extension=extension, method='enable')
            except ValueError:
                await server.init_extensions()
                try:
                    await server.run_on_extension(extension=extension, method='prepare')
                except ValueError as ex:
                    await interaction.followup.send(_("Failed enabling extension {} on server {}: {}").format(
                        extension, server.display_name, ex), ephemeral=ephemeral)
                    return
            is_running = await server.run_on_extension(extension=extension, method='is_running')
            if not is_running:
                await server.run_on_extension(extension=extension, method='startup')
            await interaction.followup.send(_("Extension {} enabled on server {} and started.").format(
                extension, server.display_name), ephemeral=ephemeral)

    @ext.command(description=_('Disable Extension'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(extension=extensions_autocomplete)
    async def disable(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer], extension: str):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        await interaction.followup.send(_("Disabling extension {}...").format(extension), ephemeral=ephemeral)
        # unset enabled in the nodes.yaml
        if server.status in [Status.STOPPED, Status.SHUTDOWN]:
            config_file_path = Path(os.path.join(interaction.client.node.config_dir, 'nodes.yaml'))
            async with aiofiles.open(config_file_path, encoding='utf-8') as file:
                config_data = yaml.load(await file.read())
            ext = config_data[server.node.name].get('instances', {}).get(server.instance.name, {}).get('extensions', {}).get(extension)
            if not ext:
                await interaction.followup.send(_("You need to add a configuration for extension {} first.").format(
                    extension), ephemeral=ephemeral)
                return
            if ext.get('enabled', True):
                ext['enabled'] = False
                with open(config_file_path, mode='w', encoding='utf-8') as outfile:
                    yaml.dump(config_data, outfile)
                await interaction.followup.send(_("Extension {} disabled in nodes.yaml").format(extension),
                                                ephemeral=ephemeral)
                return
            else:
                await interaction.followup.send(_("Extension {} is already disabled.").format(extension),
                                                ephemeral=ephemeral)
                return

        elif server.status in [Status.RUNNING, Status.PAUSED]:
            try:
                is_running = await server.run_on_extension(extension=extension, method='is_running')
                if not is_running:
                    await interaction.followup.send(_("Extension {} is not running on server {}.").format(
                        extension, server.name), ephemeral=True)
                    return
                await server.config_extension(extension, {"enabled": False})
                await server.run_on_extension(extension=extension, method='disable')
                await interaction.followup.send(_("Extension {} disabled and stopped on server {}.").format(
                    extension, server.display_name), ephemeral=ephemeral)
            except ValueError:
                await interaction.followup.send(_("Extension {} not found.").format(extension), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages or messages that do not contain YAML attachments
        if message.author.bot or not message.attachments or not message.attachments[0].filename.endswith('.yaml'):
            return
        # read the default config if there is any
        config = self.get_config().get('uploads', {})
        # check if upload is enabled
        if not config.get('enabled', True) or self.node.locals.get('restrict_commands'):
            return
        # check if the user has the correct role to upload, defaults to Admin
        if not utils.check_roles(config.get('discord', self.bot.roles['Admin']), message.author):
            return
        # check if the upload happens in the server's admin-channel (if provided)
        server: Server = self.bot.get_server(message, admin_only=True)
        ctx = await self.bot.get_context(message)
        if not server:
            # check if there is a central admin channel configured
            admin_channel = self.bot.locals.get('channels', {}).get('admin')
            if not admin_channel or admin_channel != message.channel.id:
                return
            try:
                server = await utils.server_selection(
                    self.bot, ctx, title=_("To which server do you want to upload this configuration to?"))
                if not server:
                    await ctx.send(_('Aborted.'))
                    return
            except Exception as ex:
                self.log.exception(ex)
                return
        att = message.attachments[0]
        name = att.filename[:-5]
        if name in ['main', 'nodes', 'servers'] or name.startswith('presets'):
            target_path = self.node.config_dir
            schema_path = os.path.join('schemas', name)
            plugin = False
        elif name in ['backup', 'bot']:
            target_path = os.path.join(self.node.config_dir, 'services')
            schema_path = os.path.join('services', name[:-4], 'schemas', name[:-4] + '_schema.yaml')
            plugin = False
        elif name in self.node.plugins:
            target_path = os.path.join(self.node.config_dir, 'plugins')
            schema_path = os.path.join('plugins', name[:-4], 'schemas', name[:-4] + '_schema.yaml')
            plugin = True
        else:
            return
        target_file = os.path.join(target_path, att.filename)
        # TODO: schema validation
        rc = await server.node.write_file(target_file, att.url, True)
        if rc != UploadStatus.OK:
            if rc == UploadStatus.WRITE_ERROR:
                await ctx.send(_('Error while uploading file to node {}!').format(server.node.name))
                return
            elif rc == UploadStatus.READ_ERROR:
                await ctx.send(_('Error while reading file from discord.'))
        if plugin:
            await self.bot.reload(name)
            await message.channel.send(_("Plugin {} re-loaded.").format(name.title()))
        else:
            await message.channel.send(
                _('To apply the new config by restarting a node or the whole cluster, use {}').format(
                    (await utils.get_command(self.bot, group=self.node_group.name, name=self.restart.name)).mention
                )
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self.bot.log.debug(f'Member {member.display_name} has joined guild {member.guild.name}')
        ucid = await self.bot.get_ucid_by_member(member)
        if ucid and self.bot.locals.get('autoban', False):
            await self.bus.unban(ucid)
        if self.bot.locals.get('greeting_dm'):
            try:
                channel = await member.create_dm()
                await channel.send(self.bot.locals['greeting_dm'].format(name=member.name, guild=member.guild.name))
            except discord.Forbidden:
                self.log.debug("Could not send greeting DM to user {} due to their Discord limitations.".format(
                    member.display_name))
        autorole = self.bot.locals.get('autorole', {}).get('on_join')
        if autorole:
            try:
                await member.add_roles(self.bot.get_role(autorole))
            except discord.Forbidden:
                await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
            except discord.NotFound:
                await self.bot.audit(f"Can't assign autorole {autorole}. This role does not exist.")
            except Exception as ex:
                self.log.exception(ex)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        self.bot.log.debug(f'Member {member.display_name} has left the discord')
        ucid = await self.bot.get_ucid_by_member(member)
        if ucid and self.bot.locals.get('autoban', False):
            self.bot.log.debug(f'- Banning them on our DCS servers due to AUTOBAN')
            await self.bus.ban(ucid, self.bot.member.display_name, 'Player left discord.')

    @tasks.loop(hours=12.0)
    async def cleanup(self):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM nodestats WHERE time < (CURRENT_TIMESTAMP - interval '1 month')")

    @cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    await bot.add_cog(Admin(bot, AdminEventListener))
