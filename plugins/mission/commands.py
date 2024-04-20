import asyncio
import discord
import os
import psycopg
import random
import re

from core import utils, Plugin, Report, Status, Server, Coalition, Channel, Player, PluginRequiredError, MizFile, \
    Group, ReportEnv, UploadStatus, command, PlayerType, DataObjectFactory, Member, DEFAULT_TAG, get_translation
from datetime import datetime, timezone
from discord import Interaction, app_commands, SelectOption
from discord.app_commands import Range
from discord.ext import commands, tasks
from discord.ui import Modal, TextInput
from pathlib import Path
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Optional, Union, Literal

from .listener import MissionEventListener
from .views import ServerView, PresetView, InfoView

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])


async def mizfile_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        installed_missions = [os.path.expandvars(x) for x in await server.getMissionList()]
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=os.path.basename(x)[:-4], value=idx)
            for idx, x in enumerate(await server.listAvailableMissions())
            if x not in installed_missions and current.casefold() in os.path.basename(x).casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def orig_mission_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        orig_files = [os.path.basename(x)[:-9] for x in await server.node.list_directory(
            await server.get_missions_dir(), '*.orig')]
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=os.path.basename(x)[:-4], value=idx)
            for idx, x in enumerate(await server.getMissionList())
            if os.path.basename(x)[:-4] in orig_files and (not current or current.casefold() in x[:-4].casefold())
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def presets_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x.name[:-5], value=str(x))
            for x in Path(interaction.client.node.config_dir).glob('presets*.yaml')
            if not current or current.casefold() in x.name[:-5].casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


class Mission(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.update_channel_name.add_exception_type(AttributeError)
        self.update_channel_name.start()
        self.afk_check.start()
        self.check_for_unban.start()
        self.expire_token.add_exception_type(psycopg.DatabaseError)
        self.expire_token.start()
        if self.bot.locals.get('autorole', {}):
            self.check_roles.start()

    async def cog_unload(self):
        if self.bot.locals.get('autorole', {}):
            self.check_roles.stop()
        self.expire_token.cancel()
        self.check_for_unban.cancel()
        self.afk_check.cancel()
        self.update_channel_name.cancel()
        await super().cog_unload()

    def migrate(self, version: str) -> None:
        if version == '3.6':
            filename = os.path.join(self.node.config_dir, 'plugins', 'userstats.yaml')
            if not os.path.exists(filename):
                return
            data = yaml.load(Path(filename).read_text(encoding='utf-8'))

            def migrate_instance(cfg: dict) -> dict:
                ret = {}
                for name, instance in cfg.items():
                    if 'greeting_message_members' in instance:
                        if name not in ret:
                            ret[name] = {}
                        ret[name]['greeting_message_members'] = instance['greeting_message_members']
                    if 'greeting_message_unmatched' in instance:
                        if name not in ret:
                            ret[name] = {}
                        ret[name]['greeting_message_unmatched'] = instance['greeting_message_unmatched']
                return ret

            dirty = False
            if self.node.name in data:
                for node_name, node in data.items():
                    result = migrate_instance(node)
                    if result:
                        dirty = True
                        if node_name not in self.locals:
                            self.locals[node_name] = result
                        else:
                            self.locals[node_name] |= result
            else:
                result = migrate_instance(data)
                if result:
                    dirty = True
                    self.locals |= result
            if dirty:
                path = os.path.join(self.node.config_dir, 'plugins', f'{self.plugin_name}.yaml')
                with open(path, mode='w', encoding='utf-8') as outfile:
                    yaml.dump(self.locals, outfile)
                self.log.warning(f"New file {path} written, please check for possible errors.")

    async def rename(self, conn: psycopg.AsyncConnection, old_name: str, new_name: str):
        await conn.execute('UPDATE missions SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: Optional[str] = None) -> None:
        self.log.debug('Pruning Mission ...')
        if days > -1:
            # noinspection PyTypeChecker
            await conn.execute(f"""
                DELETE FROM missions 
                WHERE mission_end < (DATE((now() AT TIME ZONE 'utc')) - interval '{days} days')
            """)
        if server:
            await conn.execute("DELETE FROM missions WHERE server_name = %s", (server, ))
        self.log.debug('Mission pruned.')

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        await conn.execute("""
            UPDATE bans SET ucid = %s WHERE ucid = %s AND NOT EXISTS (SELECT 1 FROM bans WHERE ucid = %s)
        """, (new_ucid, old_ucid, new_ucid))

    # New command group "/mission"
    mission = Group(name="mission", description=_("Commands to manage a DCS mission"))

    @mission.command(description=_('Info about the running mission'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def info(self, interaction: Interaction, server: app_commands.Transform[Server, utils.ServerTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        report = Report(self.bot, self.plugin_name, 'serverStatus.json')
        env: ReportEnv = await report.render(server=server)
        try:
            file = discord.File(fp=env.buffer, filename=env.filename) if env.filename else discord.utils.MISSING
            await interaction.followup.send(embed=env.embed, file=file, ephemeral=ephemeral)
        finally:
            if env.buffer:
                env.buffer.close()

    @mission.command(description=_('Manage the active mission'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def manage(self, interaction: Interaction, server: app_commands.Transform[Server, utils.ServerTransformer]):
        view = ServerView(server)
        embed = await view.render(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, view=view, ephemeral=utils.get_ephemeral(interaction))
        try:
            await view.wait()
        finally:
            await interaction.delete_original_response()

    @mission.command(description=_('Information about a specific airport'))
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    @app_commands.rename(idx=_('airport'))
    @app_commands.describe(idx=_('Airport for ATIS information'))
    @app_commands.autocomplete(idx=utils.airbase_autocomplete)
    async def atis(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.RUNNING, Status.PAUSED])],
                   idx: int):
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.display_name),
                                                    ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        airbase = server.current_mission.airbases[idx]
        data = await server.send_to_dcs_sync({
            "command": "getWeatherInfo",
            "x": airbase['position']['x'],
            "y": airbase['position']['y'],
            "z": airbase['position']['z']
        })
        report = Report(self.bot, self.plugin_name, 'atis.json')
        env = await report.render(airbase=airbase, server_name=server.display_name, data=data)
        await interaction.followup.send(embed=env.embed)

    @mission.command(description=_('Shows briefing of the active mission'))
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    async def briefing(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(
                           status=[Status.RUNNING, Status.PAUSED])]):
        async def read_passwords() -> dict:
            async with self.apool.connection() as conn:
                cursor = await conn.execute('SELECT blue_password, red_password FROM servers WHERE server_name = %s',
                                            (server.name,))
                row = await cursor.fetchone()
                return {"blue": row[0], "red": row[1]}

        if server.status not in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.display_name),
                                                    ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        mission_info = await server.send_to_dcs_sync({
            "command": "getMissionDetails"
        })
        mission_info['passwords'] = await read_passwords()
        report = Report(self.bot, self.plugin_name, 'briefing.json')
        env = await report.render(mission_info=mission_info, server_name=server.name, interaction=interaction)
        await interaction.followup.send(embed=env.embed)

    @mission.command(description=_('Restarts the current active mission\n'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def restart(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                      delay: Optional[int] = 120, reason: Optional[str] = None, run_extensions: Optional[bool] = False):
        await self._restart(interaction, server, delay, reason, run_extensions, rotate=False)

    @mission.command(description=_('Rotates to the next mission\n'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def rotate(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                     delay: Optional[int] = 120, reason: Optional[str] = None, run_extensions: Optional[bool] = False):
        await self._restart(interaction, server, delay, reason, run_extensions, rotate=True)

    async def _restart(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                       delay: Optional[int] = 120, reason: Optional[str] = None, run_extensions: Optional[bool] = False,
                       rotate: Optional[bool] = False):
        what = "restart" if not rotate else "rotate"
        actions = {
            "restart": "restarted",
            "rotate": "rotated",
        }
        ephemeral = utils.get_ephemeral(interaction)
        if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Can't restart server {server} as it is {status}!").format(server=server.display_name,
                                                                             status=server.status.name), ephemeral=True)
            return
        if server.restart_pending and not await utils.yn_question(
                interaction, _('A restart is currently pending.\n'
                               'Would you still like to {} the mission?').format(_(what)),
                ephemeral=ephemeral):
            return
        else:
            server.on_empty = dict()
        if server.is_populated():
            result = await utils.populated_question(
                interaction, _("Do you really want to {} the mission?").format(_(what)), ephemeral=ephemeral)
            if not result:
                return
            elif result == 'later':
                server.on_empty = {"command": what, "user": interaction.user}
                server.restart_pending = True
                await interaction.followup.send(_('Mission will {}, when server is empty.').format(_(what)),
                                                ephemeral=ephemeral)
                return

        server.restart_pending = True
        # noinspection PyUnresolvedReferences
        if not interaction.response.is_done():
            # noinspection PyUnresolvedReferences
            await interaction.response.defer(ephemeral=ephemeral)
        if server.is_populated():
            if delay > 0:
                message = _("!!! Mission will be {what} in {when}!!!").format(what=_(actions.get(what)),
                                                                              when=utils.format_time(delay))
            else:
                message = _("!!! Mission will be {} NOW !!!").format(_(actions.get(what)))
            # have we got a message to present to the users?
            if reason:
                message += _(' Reason: {}').format(reason)

            msg = await interaction.followup.send(
                _('Mission will be {what} in {when} (warning users before)...').format(what=_(actions.get(what)),
                                                                                       when=utils.format_time(delay)),
                ephemeral=ephemeral)
            server.sendPopupMessage(Coalition.ALL, message, sender=interaction.user.display_name)
            await asyncio.sleep(delay)
            await msg.delete()
        try:
            msg = await interaction.followup.send(_('Mission will {} now, please wait ...').format(_(what)),
                                                  ephemeral=ephemeral)
            if rotate:
                await server.loadNextMission(modify_mission=run_extensions)
            else:
                await server.restart(modify_mission=run_extensions)
            await self.bot.audit(f'{actions.get(what)} mission', server=server, user=interaction.user)
            await msg.delete()
            await interaction.followup.send(_("Mission {}.").format(_(actions.get(what))), ephemeral=ephemeral)
        except (TimeoutError, asyncio.TimeoutError):
            await interaction.followup.send(
                _("Timeout while the mission {what}.\n"
                  "Please check with {command}, if the mission is running.").format(
                    what=_(actions.get(what)),
                    command=(await utils.get_command(self.bot, group='mission', name='info')).mention
                ), ephemeral=ephemeral)

    async def _load(self, interaction: discord.Interaction, server: Server, mission_id: int,
                    run_extensions: Optional[bool] = False):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Can't load mission on server {server} as it is {status}!").format(
                    server=server.display_name, status=server.status.name), ephemeral=True)
            return
        if server.restart_pending and not await utils.yn_question(
                interaction, _('A restart is currently pending.\n'
                               'Would you still like to {} the mission?').format(_("change")),
                ephemeral=ephemeral):
            return
        else:
            server.on_empty = dict()

        if server.is_populated():
            result = await utils.populated_question(interaction,
                                                    _("Do you really want to {} the mission?").format(_("change")),
                                                    ephemeral=ephemeral)
            if not result:
                return
        else:
            result = "yes"

        # noinspection PyUnresolvedReferences
        if not interaction.response.is_done():
            # noinspection PyUnresolvedReferences
            await interaction.response.defer(ephemeral=ephemeral)
        mission = (await server.getMissionList())[mission_id]
        if server.current_mission and mission == server.current_mission.filename:
            if result == 'later':
                server.on_empty = {"command": "restart", "user": interaction.user}
                await interaction.followup.send(_('Mission will {}, when server is empty.').format(_('restart')),
                                                ephemeral=ephemeral)
            else:
                await server.restart(modify_mission=run_extensions)
                await interaction.followup.send(_('Mission {}.').format(_('restarted')), ephemeral=ephemeral)
        else:
            name = os.path.basename(mission[:-4])
            if result == 'later':
                # make sure, we load that mission, independently on what happens to the server
                await server.setStartIndex(mission_id + 1)
                server.on_empty = {"command": "load", "id": mission_id + 1, "user": interaction.user}
                await interaction.followup.send(
                    _('Mission {} will be loaded when server is empty or on the next restart.').format(name),
                    ephemeral=ephemeral)
            else:
                tmp = await interaction.followup.send(_('Loading mission {} ...').format(utils.escape_string(name)),
                                                      ephemeral=ephemeral)
                try:
                    await server.loadMission(mission_id + 1, modify_mission=run_extensions)
                    await self.bot.audit(f"loaded mission {utils.escape_string(name)}", server=server,
                                         user=interaction.user)
                    await interaction.followup.send(_('Mission {} loaded.').format(name), ephemeral=ephemeral)
                except (TimeoutError, asyncio.TimeoutError):
                    await interaction.followup.send(_('Timeout while loading mission {}!').format(name),
                                                    ephemeral=ephemeral)
                finally:
                    await tmp.delete()

    @mission.command(description=_('Loads a mission\n'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=utils.mission_autocomplete)
    async def load(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.STOPPED, Status.RUNNING, Status.PAUSED])],
                   mission_id: int, run_extensions: Optional[bool] = False):
        await self._load(interaction, server, mission_id, run_extensions)

    @mission.command(description=_('Adds a mission to the list\n'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(idx=_("path"))
    @app_commands.autocomplete(idx=mizfile_autocomplete)
    async def add(self, interaction: discord.Interaction,
                  server: app_commands.Transform[Server, utils.ServerTransformer], idx: int,
                  autostart: Optional[bool] = False):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        all_missions = await server.listAvailableMissions()
        if idx >= len(all_missions):
            await interaction.followup.send(_('No mission found.'), ephemeral=True)
            return
        path = all_missions[idx]
        await server.addMission(path, autostart=autostart)
        name = os.path.basename(path)
        await interaction.followup.send(_('Mission "{}" added.').format(utils.escape_string(name)), ephemeral=ephemeral)
        mission_id = (await server.getMissionList()).index(path)
        if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED] or \
                not await utils.yn_question(interaction, _('Do you want to load this mission?'),
                                            ephemeral=ephemeral):
            return
        await self._load(interaction, server, mission_id, False)

    @mission.command(description=_('Deletes a mission from the list\n'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=utils.mission_autocomplete)
    async def delete(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer],
                     mission_id: int):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        missions = await server.getMissionList()
        if mission_id >= len(missions):
            await interaction.followup.send(_("No mission found."))
            return
        filename = missions[mission_id]
        if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED] and server.current_mission and \
                filename == server.current_mission.filename:
            await interaction.followup.send(_("You can't delete the running mission."), ephemeral=True)
            return
        name = filename[:-4]

        if await utils.yn_question(interaction,
                                   _('Delete mission "{}" from the mission list?').format(os.path.basename(name)),
                                   ephemeral=ephemeral):
            try:
                await server.deleteMission(mission_id + 1)
                await interaction.followup.send(_('Mission "{}" removed from list.').format(os.path.basename(name)),
                                                ephemeral=ephemeral)
                if await utils.yn_question(interaction, _('Delete "{}" also from disk?').format(name),
                                           ephemeral=ephemeral):
                    try:
                        await server.node.remove_file(filename)
                        await interaction.followup.send(_('Mission "{}" deleted.').format(name), ephemeral=ephemeral)
                    except FileNotFoundError:
                        await interaction.followup.send(_('Mission "{}" was already deleted.').format(name),
                                                        ephemeral=ephemeral)
            except (TimeoutError, asyncio.TimeoutError):
                await interaction.followup.send(_("Timeout while deleting mission.\n"
                                                  "Please reconfirm that the deletion was successful."),
                                                ephemeral=ephemeral)

    @mission.command(description=_('Pauses the current running mission'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def pause(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status == Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.defer(thinking=True, ephemeral=ephemeral)
            await server.current_mission.pause()
            await interaction.followup.send(_('Mission on server "{}" paused.').format(server.display_name),
                                            ephemeral=ephemeral)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Server {} is not running.').format(server.display_name),
                                                    ephemeral=ephemeral)

    @mission.command(description=_('Resumes the running mission'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def unpause(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.PAUSED])]):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status == Status.PAUSED:
            # noinspection PyUnresolvedReferences
            await interaction.response.defer(thinking=True, ephemeral=ephemeral)
            await server.current_mission.unpause()
            await interaction.followup.send(_('Mission on server "{}" resumed.').format(server.display_name),
                                            ephemeral=ephemeral)
        elif server.status == Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Server "{}" is not paused.').format(server.display_name),
                                                    ephemeral=ephemeral)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {server} is {status}, can't unpause.").format(server=server.display_name,
                                                                        status=server.status.name),
                ephemeral=ephemeral)

    @mission.command(description=_('Modify mission with a preset\n'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(presets_file=presets_autocomplete)
    @app_commands.describe(presets_file=_('Chose an alternate presets file'))
    async def modify(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.SHUTDOWN])],
                     presets_file: Optional[str] = 'config/presets.yaml'):
        ephemeral = utils.get_ephemeral(interaction)
        try:
            with open(presets_file, mode='r', encoding='utf-8') as infile:
                presets = yaml.load(infile)
        except FileNotFoundError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('No presets available, please configure them in {}.').format(presets_file), ephemeral=True)
            return
        try:
            options = [
                discord.SelectOption(label=k)
                for k, v in presets.items()
                if not isinstance(v, dict) or not v.get('hidden', False)
            ]
        except AttributeError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("There is an error in your {}. Please check the file structure.").format(presets_file),
                ephemeral=True)
            return
        if len(options) > 25:
            self.log.warning("You have more than 25 presets created, you can only choose from 25!")

        result = None
        if server.status in [Status.PAUSED, Status.RUNNING]:
            question = _('Do you want to restart the server for a mission change?')
            if server.is_populated():
                result = await utils.populated_question(interaction, question, ephemeral=ephemeral)
            else:
                result = await utils.yn_question(interaction, question, ephemeral=ephemeral)
            if not result:
                return

        view = PresetView(options[:25])
        # noinspection PyUnresolvedReferences
        if interaction.response.is_done():
            msg = await interaction.followup.send(view=view, ephemeral=ephemeral)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(view=view, ephemeral=ephemeral)
            msg = await interaction.original_response()
        try:
            if await view.wait() or view.result is None:
                return
        finally:
            try:
                await msg.delete()
            except discord.NotFound:
                pass
        if result == 'later':
            server.on_empty = {"command": "preset", "preset": view.result, "user": interaction.user}
            server.restart_pending = True
            await interaction.followup.send(_('Mission will be changed when server is empty.'), ephemeral=ephemeral)
        else:
            startup = False
            msg = await interaction.followup.send(_('Changing mission ...'), ephemeral=ephemeral)
            if not server.node.config.get('mission_rewrite', True) and server.status != Status.STOPPED:
                await server.stop()
                startup = True
            filename = await server.get_current_mission_file()
            new_filename = await server.modifyMission(filename, [utils.get_preset(self.node, x) for x in view.result])
            message = _('The following preset were applied: {}.').format(','.join(view.result))
            if new_filename != filename:
                self.log.info(f"  => New mission written: {new_filename}")
                await server.replaceMission(int(server.settings['listStartIndex']), new_filename)
            else:
                self.log.info(f"  => Mission {filename} overwritten.")
            if startup or server.status not in [Status.STOPPED, Status.SHUTDOWN]:
                try:
                    await server.restart(modify_mission=False)
                    message += _('\nMission reloaded.')
                    await self.bot.audit("changed preset {}".format(','.join(view.result)), server=server,
                                         user=interaction.user)
                    await msg.delete()
                except (TimeoutError, asyncio.TimeoutError):
                    message = _("Timeout during restart of mission!\n"
                                "Please check, if the mission is running or if it somehow got corrupted.")
            await interaction.followup.send(message, ephemeral=ephemeral)

    @mission.command(description=_('Save mission preset\n'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def save_preset(self, interaction: discord.Interaction,
                          server: app_commands.Transform[Server, utils.ServerTransformer(
                              status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                          name: str):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        miz = await asyncio.to_thread(MizFile, self.bot, server.current_mission.filename)
        if os.path.exists('config/presets.yaml'):
            with open('config/presets.yaml', mode='r', encoding='utf-8') as infile:
                presets = yaml.load(infile)
        else:
            presets = dict()
        if name in presets and \
                not await utils.yn_question(interaction,
                                            _('Do you want to overwrite the existing preset "{}"?').format(name),
                                            ephemeral=ephemeral):
            return
        presets[name] = {
            "start_time": miz.start_time,
            "date": miz.date.strftime('%Y-%m-%d'),
            "temperature": miz.temperature,
            "clouds": miz.clouds,
            "wind": miz.wind,
            "groundTurbulence": miz.groundTurbulence,
            "enable_dust": miz.enable_dust,
            "dust_density": miz.dust_density if miz.enable_dust else 0,
            "qnh": miz.qnh,
            "enable_fog": miz.enable_fog,
            "fog": miz.fog if miz.enable_fog else {"thickness": 0, "visibility": 0},
            "halo": miz.halo
        }
        with open(f'config/presets.yaml', mode='w', encoding='utf-8') as outfile:
            yaml.dump(presets, outfile)
        # noinspection PyUnresolvedReferences
        await interaction.followup.send(_('Preset "{}" added.').format(name), ephemeral=ephemeral)

    @mission.command(description=_('Rollback to the original mission file after any modifications'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=orig_mission_autocomplete)
    async def rollback(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer], mission_id: int):
        missions = await server.getMissionList()
        if mission_id >= len(missions):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("No mission found."))
            return
        filename = missions[mission_id]
        if server.status in [Status.RUNNING, Status.PAUSED] and filename == server.current_mission.filename:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Please stop your server first to rollback the running mission."),
                                                    ephemeral=True)
            return
        mission_folder = await server.get_missions_dir()
        miz_file = os.path.basename(filename)
        try:
            new_file = os.path.join(mission_folder, miz_file)
            old_file = new_file + '.orig'
            await server.node.rename_file(old_file, new_file, force=True)
        except FileNotFoundError:
            # we should never be here, but just in case
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('No ".orig" file there, the mission was never changed.'),
                                                    ephemeral=True)
            return
        if new_file != filename:
            await server.replaceMission(mission_id, new_file)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Mission {} has been rolled back.").format(miz_file[:-4]),
                                                ephemeral=utils.get_ephemeral(interaction))

    # New command group "/player"
    player = Group(name="player", description=_("Commands to manage DCS players"))

    @player.command(name='list', description=_('Lists the current players'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.display_name),
                                                    ephemeral=True)
            return
        report = Report(self.bot, self.plugin_name, 'players.json')
        env = await report.render(server=server, sides=utils.get_sides(interaction.client, interaction, server))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=env.embed, ephemeral=utils.get_ephemeral(interaction))

    @player.command(description=_('Kicks a player\n'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def kick(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)],
                   reason: Optional[str] = 'n/a') -> None:
        if not player:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Player not found."), ephemeral=True)
            return
        server.kick(player, reason)
        await self.bot.audit(f'kicked player {player.display_name} with reason "{reason}"', user=interaction.user)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(
            _("Player {name} (ucid={ucid}) kicked.").format(name=player.display_name, ucid=player.ucid),
            ephemeral=utils.get_ephemeral(interaction))

    @player.command(description=_('Bans an active player'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def ban(self, interaction: discord.Interaction,
                  server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                  player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)]):

        class BanModal(Modal, title=_("Ban Details")):
            reason = TextInput(label=_("Reason"), default=_("n/a"), max_length=80, required=False)
            period = TextInput(label=_("Days (empty = forever)"), required=False)

            async def on_submit(derived, interaction: discord.Interaction):
                days = int(derived.period.value) if derived.period.value else None
                await self.bus.ban(player.ucid, interaction.user.display_name, derived.reason.value, days)
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(
                    _("Player {} banned on all servers ").format(player.display_name) +
                    (_("for {} days.").format(days) if days else ""),
                    ephemeral=utils.get_ephemeral(interaction))
                await self.bot.audit(f'banned player {player.display_name} with reason "{derived.reason.value}"' +
                                     (f' for {days} days.' if days else ' permanently.'), user=interaction.user)
        if not player:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Player not found."), ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(BanModal())

    @player.command(description=_('Moves a player to spectators\n'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def spec(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)],
                   reason: Optional[str] = 'n/a') -> None:
        if not player:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Player not found."), ephemeral=True)
            return
        server.move_to_spectators(player)
        if reason:
            player.sendChatMessage(_("You have been moved to spectators. Reason: {}").format(reason),
                                   interaction.user.display_name)
        await self.bot.audit(f'moved player {player.name} to spectators with reason "{reason}".', user=interaction.user)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Player "{}" moved to spectators.').format(player.name),
                                                ephemeral=utils.get_ephemeral(interaction))

    @player.command(description=_('List of AFK players'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def afk(self, interaction: discord.Interaction,
                  server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                  minutes: Optional[int] = 10):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.display_name),
                                                    ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        afk: list[Player] = list()
        for s in self.bot.servers.values():
            if server and s != server:
                continue
            for ucid, dt in s.afk.items():
                player = s.get_player(ucid=ucid, active=True)
                if not player:
                    continue
                if (datetime.now(tz=timezone.utc) - dt).total_seconds() > minutes * 60:
                    afk.append(player)

        if afk:
            title = 'AFK Players'
            if server:
                title += f' on {server.name}'
            embed = discord.Embed(title=title, color=discord.Color.blue())
            embed.description = _('These players are AFK for more than {} minutes:').format(minutes)
            for player in sorted(afk, key=lambda x: x.server.name):
                embed.add_field(name=_('Name'), value=player.display_name)
                embed.add_field(name=_('Time'),
                                value=utils.format_time(int((datetime.now(timezone.utc) -
                                                             player.server.afk[player.ucid]).total_seconds())))
                if server:
                    embed.add_field(name='_ _', value='_ _')
                else:
                    embed.add_field(name=_('Server'), value=player.server.display_name)
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.followup.send(_("No player is AFK for more than {} minutes.").format(minutes),
                                            ephemeral=ephemeral)

    @player.command(description=_('Exempt player from AFK kicks'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def exempt(self, interaction: discord.Interaction,
                     user: app_commands.Transform[
                         Union[discord.Member, str], utils.UserTransformer(sel_type=PlayerType.PLAYER)
                     ]):
        ephemeral = utils.get_ephemeral(interaction)
        if isinstance(user, discord.Member):
            ucid = await self.bot.get_ucid_by_member(user)
        else:
            ucid = user
        config_file = os.path.join('config', 'plugins', self.plugin_name + '.yaml')
        if DEFAULT_TAG not in self.locals:
            self.locals[DEFAULT_TAG] = {}
        if 'afk_exemptions' not in self.locals[DEFAULT_TAG]:
            self.locals[DEFAULT_TAG]['afk_exemptions'] = []
        if ucid not in self.locals[DEFAULT_TAG]['afk_exemptions']:
            if not await utils.yn_question(interaction,
                                           _("Do you want to permanently add this user to the AFK exemption list?"),
                                           ephemeral=ephemeral):
                await interaction.followup.send("Aborted.", ephemeral=ephemeral)
                return
            self.locals[DEFAULT_TAG]['afk_exemptions'].append(ucid)
            await interaction.followup.send(_("User added to the exemption list."), ephemeral=ephemeral)
        else:
            if not await utils.yn_question(interaction,
                                           _("Player is on the list already. Do you want to remove them?")):
                await interaction.followup.send(_("Aborted."), ephemeral=ephemeral)
                return
            self.locals[DEFAULT_TAG]['afk_exemptions'].remove(ucid)
            await interaction.followup.send(_("User removed from the exemption list."), ephemeral=ephemeral)
        with open(config_file, 'w', encoding='utf-8') as outfile:
            yaml.dump(self.locals, outfile)

    @player.command(description=_('Sends a popup to a player\n'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def popup(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)],
                    message: str, time: Optional[Range[int, 1, 30]] = -1):
        if not player:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Player not found."), ephemeral=True)
            return
        player.sendPopupMessage(message, time, interaction.user.display_name)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Message sent.'), ephemeral=utils.get_ephemeral(interaction))

    @player.command(description=_('Sends a chat message to a player\n'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def chat(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)], message: str):
        if not player:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Player not found."), ephemeral=True)
            return
        player.sendChatMessage(message, interaction.user.display_name)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Message sent.'), ephemeral=utils.get_ephemeral(interaction))

    @player.command(description=_('Adds a player to the watchlist'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def watch(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    player: app_commands.Transform[Player, utils.PlayerTransformer(active=True, watchlist=False)]):
        if not player:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Player not found."), ephemeral=True)
            return
        player.watchlist = True
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Player {} is now on the watchlist.").format(player.display_name),
                                                ephemeral=utils.get_ephemeral(interaction))

    @player.command(description=_('Removes a player from the watchlist'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def unwatch(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                      player: app_commands.Transform[Player, utils.PlayerTransformer(active=True, watchlist=True)]):
        if not player:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Player not found."), ephemeral=True)
            return
        player.watchlist = False
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Player {} removed from watchlist.").format(player.display_name),
                                                ephemeral=utils.get_ephemeral(interaction))

    # New command group "/group"
    group = Group(name="group", description="Commands to manage DCS groups")

    @group.command(description=_('Sends a popup to a group\n'))
    @app_commands.guild_only()
    @app_commands.autocomplete(group=utils.group_autocomplete)
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def popup(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    group: str, message: str, time: Optional[Range[int, 1, 30]] = -1):
        server.sendPopupMessage(group, message, time, interaction.user.display_name)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Message sent.'), ephemeral=utils.get_ephemeral(interaction))

    @command(description=_("Links a member to a DCS user"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def link(self, interaction: discord.Interaction, member: discord.Member,
                   user: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer(
                       sel_type=PlayerType.PLAYER, linked=False)]
                   ):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        _member = DataObjectFactory().new(Member, name=member.name, node=self.node, member=member)
        if isinstance(user, discord.Member):
            _new_member = DataObjectFactory().new(Member, name=user.name, node=self.node, member=user)
            ucid = _new_member.ucid
            if ucid == _member.ucid:
                if _member.verified:
                    await interaction.followup.send(_("This member is linked to this UCID already."),
                                                    ephemeral=ephemeral)
                    return
            elif not await utils.yn_question(
                interaction, _("Member {name} is linked to another UCID ({ucid}) already. "
                               "Do you want to relink?").format(
                    name=utils.escape_string(user.display_name), ucid=ucid), ephemeral=ephemeral):
                return
            else:
                _new_member.unlink()
        else:
            ucid = user
        if _member.verified:
            if not await utils.yn_question(
                interaction, _("Member {name} is linked to another UCID ({ucid}) already. "
                               "Do you want to relink?").format(
                    name=utils.escape_string(member.display_name), ucid=_member.ucid), ephemeral=ephemeral):
                return
            else:
                _member.unlink()
        _member.link(ucid, verified=True)
        await interaction.followup.send(_('Member {name} linked to UCID {ucid}.').format(
            name=utils.escape_string(member.display_name), ucid=ucid), ephemeral=utils.get_ephemeral(interaction))
        await self.bot.audit(f'linked member {utils.escape_string(member.display_name)} to ucid {ucid}.',
                             user=interaction.user)
        # If autorole is enabled, give the user the role:
        autorole = self.bot.locals.get('autorole', {}).get('linked')
        if autorole:
            try:
                await member.add_roles(self.bot.get_role(autorole))
            except discord.Forbidden:
                await self.bot.audit(_('permission "Manage Roles" missing.'), user=self.bot.member)
        # Generate the onMemberLinked event
        for server_name, server in self.bot.servers.items():
            player = server.get_player(ucid=ucid, active=True)
            if player:
                player.member = self.bot.get_member_by_ucid(player.ucid)
                player.verified = True
                break
        else:
            server = None
        self.bot.bus.send_to_node({
            "command": "rpc",
            "service": "ServiceBus",
            "method": "propagate_event",
            "params": {
                "command": "onMemberLinked",
                "server": server.name if server else None,
                "data": {
                    "ucid": ucid,
                    "discord_id": member.id
                }
            }
        })

    @command(description=_('Unlinks a member or ucid'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(user=_('Name of player, member or UCID'))
    async def unlink(self, interaction: discord.Interaction,
                     user: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer(linked=True)]):

        async def unlink_member(member: discord.Member, ucid: str):
            # change the link status of that member if they are an active player
            for server_name, server in self.bot.servers.items():
                player = server.get_player(ucid=ucid, active=True)
                if player:
                    player.member = None
                    player.verified = False
                    break
            else:
                await conn.execute('UPDATE players SET discord_id = -1, manual = FALSE WHERE ucid = %s', (ucid,))
                server = None
            await interaction.followup.send(_('Member {name} unlinked from UCID {ucid}.').format(
                name=utils.escape_string(member.display_name), ucid=ucid), ephemeral=ephemeral)
            await self.bot.audit(
                f'unlinked member {utils.escape_string(member.display_name)} from ucid {ucid}',
                user=interaction.user)
            self.bot.bus.send_to_node({
                "command": "rpc",
                "service": "ServiceBus",
                "method": "propagate_event",
                "params": {
                    "command": "onMemberUnlinked",
                    "server": server.name if server else None,
                    "data": {
                        "ucid": ucid,
                        "discord_id": member.id
                    }
                }
            })

        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                if isinstance(user, discord.Member):
                    member = user
                    cursor = await conn.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id, ))
                    rows = await cursor.fetchall()
                    for row in rows:
                        ucid = row[0]
                        await unlink_member(user, ucid)
                elif utils.is_ucid(user):
                    ucid = user
                    member = self.bot.get_member_by_ucid(ucid)
                    if not member:
                        await interaction.followup.send(_('Player is not linked!'), ephemeral=True)
                        return
                    await unlink_member(member, ucid)
                else:
                    await interaction.followup.send(_('Unknown player / member provided'), ephemeral=True)
                    return

        # If autorole is enabled, remove the role from the user:
        autorole = self.bot.locals.get('autorole', {}).get('linked')
        if autorole:
            try:
                await member.remove_roles(self.bot.get_role(autorole))
            except discord.Forbidden:
                await self.bot.audit(_('permission "Manage Roles" missing.'), user=self.bot.member)

    async def _find(self, interaction: discord.Interaction, name: str):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT distinct ucid, name, max(last_seen) FROM (
                    SELECT ucid, name, last_seen FROM players
                    UNION
                    SELECT distinct ucid, name, time AS last_seen FROM players_hist
                ) x
                WHERE x.name ILIKE %s
                GROUP BY ucid, name
                ORDER BY 3 DESC
                LIMIT 25
            """, ('%' + name + '%', ))
            rows = await cursor.fetchall()
        # give back the database session
        last_seen_str = _('last seen')
        options = [
            SelectOption(label=f"{row[1]} ({last_seen_str}: {row[2]:%Y-%m-%d %H:%M})"[:100], value=str(idx))
            for idx, row in enumerate(rows)
        ]
        if not options:
            await interaction.followup.send(_("No user found."))
            return
        idx = await utils.selection(interaction, placeholder=_("Select a User"), options=options, ephemeral=ephemeral)
        if idx:
            await self._info(interaction, rows[int(idx)][0])

    @player.command(description=_('Find a player by name'))
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def find(self, interaction: discord.Interaction, name: str):
        await self._find(interaction, name)

    @command(description=_('Find a player by name'))
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def find(self, interaction: discord.Interaction, name: str):
        await self._find(interaction, name)

    async def _info(self, interaction: discord.Interaction, member: Union[discord.Member, str]):
        if not member:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("This user does not exist. Try {} to find them in the historic data.").format(
                    (await utils.get_command(self.bot, name='find')).mention
                ), ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        if not interaction.response.is_done():
            # noinspection PyUnresolvedReferences
            await interaction.response.defer(ephemeral=ephemeral)
        if isinstance(member, str):
            ucid = member
            member = self.bot.get_member_by_ucid(ucid)
        player: Optional[Player] = None
        for server in self.bot.servers.values():
            if isinstance(member, discord.Member):
                player = server.get_player(discord_id=member.id, active=True)
            else:
                player = server.get_player(ucid=ucid, active=True)
            if player:
                break
        else:
            server = None

        view = InfoView(member=member or ucid, bot=self.bot, ephemeral=ephemeral, player=player, server=server)
        embed = await view.render()
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        try:
            await view.wait()
        finally:
            try:
                await msg.delete()
            except discord.NotFound:
                pass

    @player.command(description=_('Shows player information'))
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def info(self, interaction: discord.Interaction,
                   member: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer]):
        await self._info(interaction, member)

    @command(description=_('Shows player information'))
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def info(self, interaction: discord.Interaction,
                   member: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer]):
        await self._info(interaction, member)

    @staticmethod
    def format_unmatched(data, marker, marker_emoji):
        embed = discord.Embed(title=_('Unlinked Players'), color=discord.Color.blue())
        embed.description = _('These players could be possibly linked:')
        ids = players = members = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            players += "{}\n".format(utils.escape_string(data[i]['name']))
            members += f"{data[i]['match'].display_name}\n"
        embed.add_field(name=_('ID'), value=ids)
        embed.add_field(name=_('DCS Player'), value=players)
        embed.add_field(name=_('Member'), value=members)
        embed.set_footer(text=_('Press a number to link this specific user.'))
        return embed

    @command(description=_('Show players that could be linked'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def linkcheck(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # check all unmatched players
                unmatched = []
                await cursor.execute("""
                    SELECT ucid, name FROM players 
                    WHERE discord_id = -1 AND name IS NOT NULL 
                    ORDER BY last_seen DESC
                                """)
                async for row in cursor:
                    matched_member = self.bot.match_user(dict(row), True)
                    if matched_member:
                        unmatched.append({"name": row['name'], "ucid": row['ucid'], "match": matched_member})
            if len(unmatched) == 0:
                await interaction.followup.send(_('No unmatched member could be matched.'), ephemeral=True)
                return
        n = await utils.selection_list(self.bot, interaction, unmatched, self.format_unmatched)
        if n != -1:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute('UPDATE players SET discord_id = %s, manual = TRUE WHERE ucid = %s',
                                       (unmatched[n]['match'].id, unmatched[n]['ucid']))
                    await self.bot.audit(
                        f"linked ucid {unmatched[n]['ucid']} to user {unmatched[n]['match'].display_name}.",
                        user=interaction.user)
                    await interaction.followup.send(
                        _("DCS player {player} linked to member {member}.").format(
                            player=utils.escape_string(unmatched[n]['name']),
                            member=unmatched[n]['match'].display_name), ephemeral=True)

    @staticmethod
    def format_suspicious(data, marker, marker_emoji):
        embed = discord.Embed(title=_('Possible Mislinks'), color=discord.Color.blue())
        embed.description = _('These players could be possibly mislinked:')
        ids = players = members = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            players += f"{data[i]['name']}\n"
            members += f"{data[i]['mismatch'].display_name}\n"
        embed.add_field(name=_('ID'), value=ids)
        embed.add_field(name=_('DCS Player'), value=players)
        embed.add_field(name=_('Member'), value=members)
        embed.set_footer(text=_('Press a number to unlink this specific user.'))
        return embed

    @command(description=_('Show possibly mislinked players'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def mislinks(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # check all matched members
                suspicious = []
                for member in self.bot.get_all_members():
                    # ignore bots
                    if member.bot:
                        continue
                    await cursor.execute("""
                        SELECT ucid, name FROM players 
                        WHERE discord_id = %s AND name IS NOT NULL AND manual = FALSE 
                        ORDER BY last_seen DESC
                    """, (member.id, ))
                    async for row in cursor:
                        matched_member = self.bot.match_user(dict(row), True)
                        if not matched_member:
                            suspicious.append({"name": row['name'], "ucid": row['ucid'], "mismatch": member})
                        elif matched_member.id != member.id:
                            suspicious.append({"name": row['name'], "ucid": row['ucid'], "mismatch": member,
                                               "match": matched_member})
                if len(suspicious) == 0:
                    await interaction.followup.send(_('No mislinked players found.'), ephemeral=True)
                    return
        n = await utils.selection_list(self.bot, interaction, suspicious, self.format_suspicious)
        if n != -1:
            ephemeral = utils.get_ephemeral(interaction)
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute('UPDATE players SET discord_id = %s, manual = %s WHERE ucid = %s',
                                       (suspicious[n]['match'].id if 'match' in suspicious[n] else -1,
                                        'match' in suspicious[n], suspicious[n]['ucid']))
                    await self.bot.audit(
                        f"unlinked ucid {suspicious[n]['ucid']} from user {suspicious[n]['mismatch'].display_name}.",
                        user=interaction.user)
                    if 'match' in suspicious[n]:
                        await self.bot.audit(
                            f"linked ucid {suspicious[n]['ucid']} to user {suspicious[n]['match'].display_name}.",
                            user=interaction.user)
                        await interaction.followup.send(
                            _("UCID {ucid} transferred from member {old_member} to member {new_member}.").format(
                                ucid=suspicious[n]['ucid'],
                                old_member=utils.escape_string(suspicious[n]['mismatch'].display_name),
                                new_member=utils.escape_string(suspicious[n]['match'].display_name)),
                            ephemeral=ephemeral)
                    else:
                        await interaction.followup.send(_("Member {name} unlinked from UCID {ucid}.").format(
                            name=utils.escape_string(suspicious[n]['mismatch'].display_name),
                            ucid=suspicious[n]['ucid']), ephemeral=ephemeral)

    @command(description=_('Link your DCS and Discord user'))
    @app_commands.guild_only()
    async def linkme(self, interaction: discord.Interaction):
        async def send_token(token: str):
            await interaction.followup.send(
                _("**Your secure TOKEN is: {token}**\n"
                  "To link your user, type in the following into the DCS chat of one of our servers:"
                  "```{prefix}linkme {token}```\n\n"
                  "**The TOKEN will expire in 2 days!**").format(token=token, prefix=self.eventlistener.prefix),
                ephemeral=True)

        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        member = DataObjectFactory().new(Member, name=interaction.user.name, node=self.node, member=interaction.user)
        if (utils.is_ucid(member.ucid) and member.verified and
                not await utils.yn_question(interaction,
                                            _("You already have a verified DCS account!\n"
                                              "Are you sure you want to re-link your account? "
                                              "(Ex: Switched from Steam to Standalone)"), ephemeral=True)):
            await interaction.followup.send(_('Aborted.'))
            return
        elif member.ucid and not utils.is_ucid(member.ucid):
            await send_token(member.ucid)
            return

        # generate the TOKEN
        async with self.apool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cursor:
                    # in the very unlikely event that we have generated the very same random number twice
                    while True:
                        try:
                            token = str(random.randrange(1000, 9999))
                            await cursor.execute("""
                                INSERT INTO players (ucid, discord_id, last_seen) 
                                VALUES (%s, %s, NOW())
                            """, (token, interaction.user.id))
                            break
                        except psycopg.DatabaseError:
                            pass
            await send_token(token)

    @player.command(description=_('Shows inactive users'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def inactive(self, interaction: discord.Interaction, period: Literal['days', 'weeks', 'months', 'years'],
                       number: Range[int, 1]):
        report = Report(self.bot, self.plugin_name, 'inactive.json')
        env = await report.render(period=f"{number} {period}")
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=env.embed, ephemeral=utils.get_ephemeral(interaction))

    @tasks.loop(hours=1)
    async def expire_token(self):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    DELETE FROM players 
                    WHERE LENGTH(ucid) = 4 AND last_seen < (DATE(now() AT TIME ZONE 'utc') - interval '2 days')
                """)

    @tasks.loop(minutes=1.0)
    async def check_for_unban(self):
        try:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    cursor = await conn.execute("""
                        SELECT ucid FROM bans WHERE banned_until < (NOW() AT TIME ZONE 'utc')
                    """)
                    rows = await cursor.fetchall()
                    for row in rows:
                        for server in self.bot.servers.values():
                            if server.status not in [Status.PAUSED, Status.RUNNING, Status.STOPPED]:
                                continue
                            server.send_to_dcs({
                                "command": "unban",
                                "ucid": row[0]
                            })
                        # delete unbanned accounts from the database
                        await conn.execute("DELETE FROM bans WHERE ucid = %s", (row[0], ))
        except Exception as ex:
            self.log.exception(ex)

    @check_for_unban.before_loop
    async def before_check_unban(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5.0)
    async def update_channel_name(self):
        # might happen during a restart
        if not self.bot.member:
            return
        for server_name, server in self.bot.servers.copy().items():
            if server.status == Status.UNREGISTERED:
                continue
            try:
                # channel = await self.bot.fetch_channel(int(server.locals['channels'][Channel.STATUS.value]))
                channel = self.bot.get_channel(server.channels[Channel.STATUS])
                if not channel:
                    channel = await self.bot.fetch_channel(server.channels[Channel.STATUS])
                # name changes of the status channel will only happen with the correct permission
                if channel.permissions_for(self.bot.member).manage_channels:
                    name = channel.name
                    # if the server owner leaves, the server is shut down
                    if server.status in [Status.STOPPED, Status.SHUTDOWN, Status.LOADING]:
                        if name.find('［') == -1:
                            name = name + '［-］'
                        else:
                            name = re.sub('［.*］', f'［-］', name)
                    else:
                        players = server.get_active_players()
                        current = len(players) + 1
                        max_players = server.settings.get('maxPlayers') or 0
                        if name.find('［') == -1:
                            name = name + f'［{current}／{max_players}］'
                        else:
                            name = re.sub('［.*］', f'［{current}／{max_players}］', name)
                    if name != channel.name:
                        await channel.edit(name=name)
            except Exception as ex:
                self.log.debug(f"Exception in update_channel_name() for server {server_name}", exc_info=str(ex))

    @update_channel_name.before_loop
    async def before_update_channel_name(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1.0)
    async def afk_check(self):
        try:
            for server in self.bot.servers.copy().values():
                max_time = server.locals.get('afk_time', -1)
                if max_time == -1:
                    continue
                for ucid, dt in server.afk.items():
                    player = server.get_player(ucid=ucid, active=True)
                    if (not player or player.has_discord_roles(['DCS Admin', 'GameMaster']) or
                            player.ucid in self.get_config(server).get('afk_exemptions', [])):
                        continue
                    if (datetime.now(timezone.utc) - dt).total_seconds() > max_time:
                        msg = self.get_config(server).get(
                            'message_afk', '{player.name}, you have been kicked for being AFK for more than {time}.'
                        ).format(player=player, time=utils.format_time(max_time))
                        server.kick(player, msg)
        except Exception as ex:
            self.log.exception(ex)

    @afk_check.before_loop
    async def before_afk_check(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5.0, count=2)
    async def check_roles(self):
        if not self.bot.is_ready():
            return

        role = self.bot.get_role(self.bot.locals.get('autorole', {}).get('online'))
        if role:
            online_members: set[discord.Member] = set()
            for server in self.bot.servers.copy().values():
                for player in server.get_active_players():
                    if player.member:
                        online_members.add(player.member)
            try:
                # check who needs to lose the role
                for member in (set(role.members) - online_members):
                    await member.remove_roles(role)
            except discord.Forbidden:
                await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
                return
        role = self.bot.get_role(self.bot.locals.get('autorole', {}).get('linked'))
        if role:
            linked_members: set[discord.Member] = set()
            async with self.apool.connection() as conn:
                async for row in await conn.execute("""
                    SELECT DISTINCT discord_id FROM players 
                    WHERE discord_id <> -1 AND manual IS TRUE
                """):
                    member = self.bot.guilds[0].get_member(row[0])
                    if member:
                        linked_members.add(member)
            for member in (linked_members - set(role.members)):
                await member.add_roles(role)
                self.log.debug(f"=> Member {member.display_name} is linked and got the {role.name} role.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages or messages that do not contain miz attachments
        if message.author.bot or not message.attachments or not message.attachments[0].filename.endswith('.miz'):
            return
        # read the default config, if there is any
        config = self.get_config().get('uploads', {})
        # check, if upload is enabled
        if not config.get('enabled', True):
            return
        # check if the user has the correct role to upload, defaults to DCS Admin
        if not utils.check_roles(config.get('discord', self.bot.roles['DCS Admin']), message.author):
            return
        # check if the upload happens in the servers admin channel (if provided)
        server: Server = self.bot.get_server(message, admin_only=True)
        ctx = await self.bot.get_context(message)
        if not server:
            # check if there is a central admin channel configured
            if self.bot.locals.get('admin_channel', 0) == message.channel.id:
                try:
                    server = await utils.server_selection(
                        self.bus, ctx, title=_("To which server do you want to upload this mission to?"))
                    if not server:
                        await ctx.send(_('Upload aborted.'))
                        return
                except Exception as ex:
                    self.log.exception(ex)
                    return
            else:
                return
        att = message.attachments[0]
        try:
            rc = await server.uploadMission(att.filename, att.url)
            if rc == UploadStatus.FILE_IN_USE:
                if not await utils.yn_question(ctx, _('A mission is currently active.\n'
                                                      'Do you want me to stop the DCS-server to replace it?')):
                    await message.channel.send(_('Upload aborted.'))
                    return
            elif rc == UploadStatus.FILE_EXISTS:
                if not await utils.yn_question(ctx, _('File exists. Do you want to overwrite it?')):
                    await message.channel.send(_('Upload aborted.'))
                    return
            if rc != UploadStatus.OK:
                if (await server.uploadMission(att.filename, att.url, force=True)) != UploadStatus.OK:
                    await message.channel.send(_('Error while uploading: {}').format(rc.name))
                    return

            name = utils.escape_string(os.path.basename(att.filename)[:-4])
            try:
                if server.locals.get('autoscan', False):
                    await message.channel.send(
                        _('Mission "{mission}" uploaded to server {server}.\n'
                          'As you have "autoscan" enabled, it might take some seconds to appear in your mission list.'
                          ).format(mission=name, server=server.display_name))
                    return
                # get the real filename after the upload
                filename = next((file for file in await server.getMissionList()
                                 if os.path.basename(file) == os.path.basename(att.filename)), None)
                if not filename:
                    await message.channel.send(_('Error while uploading: File not found in severSettings.lua!'))
                    return
                await message.channel.send(_('Mission "{mission}" uploaded to server {server} and added.').format(
                    mission=name, server=server.display_name))
            finally:
                await self.bot.audit(f'uploaded mission "{name}"', server=server, user=message.author)

            if (server.status != Status.SHUTDOWN and server.current_mission and
                    server.current_mission.filename != filename and
                    await utils.yn_question(ctx, _('Do you want to load this mission?'))):
                extensions = [
                    x.name for x in server.extensions.values()
                    if getattr(x, 'beforeMissionLoad').__module__ != 'core.extension'
                ]
                if len(extensions):
                    modify = await utils.yn_question(ctx, _("Do you want to apply extensions before mission start?"))
                else:
                    modify = False
                tmp = await message.channel.send(_('Loading mission {} ...').format(name))
                try:
                    await server.loadMission(filename, modify_mission=modify)
                except (TimeoutError, asyncio.TimeoutError):
                    await tmp.delete()
                    await message.channel.send(_("Timeout while trying to load the mission."))
                    await self.bot.audit(f"Timeout while trying to load mission {name}",
                                         server=server)
                    return
                await self.bot.audit(f"loaded mission {name}", server=server, user=message.author)
                await tmp.delete()
                await message.channel.send(_('Mission {} loaded.').format(name))
        except Exception as ex:
            self.log.exception(ex)
        finally:
            await message.delete()

    @commands.Cog.listener()
    async def on_member_ban(self, _: discord.Guild, member: discord.Member):
        self.bot.log.debug(f"Member {member.display_name} has been banned.")
        if not self.bot.locals.get('no_dcs_autoban', False):
            ucid = await self.bot.get_ucid_by_member(member)
            if ucid:
                await self.bus.ban(ucid, 'Discord',
                                   self.bot.locals.get('message_ban', 'User has been banned on Discord.'))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        ucid = await self.bot.get_ucid_by_member(member, verified=True)
        autorole = self.bot.locals.get('autorole', {}).get('linked')
        if ucid and autorole:
            try:
                role = self.bot.get_role(autorole)
                await member.add_roles(role)
                self.log.debug(f"=> Rejoined member {member.display_name} got their role {role.name} back.")
            except discord.Forbidden:
                await self.bot.audit(_('permission "Manage Roles" missing.'), user=self.bot.member)


async def setup(bot: DCSServerBot):
    if 'gamemaster' not in bot.plugins:
        raise PluginRequiredError('gamemaster')
    await bot.add_cog(Mission(bot, MissionEventListener))
