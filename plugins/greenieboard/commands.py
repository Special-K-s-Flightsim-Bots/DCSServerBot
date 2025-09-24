from pathlib import Path

import aiofiles
import asyncio
import discord
import io
import os
import psycopg
import shutil

from contextlib import suppress
from core import Plugin, PluginRequiredError, utils, PaginationReport, Report, Group, Server, DEFAULT_TAG, \
    get_translation
from discord import SelectOption, app_commands
from discord.app_commands import Range
from matplotlib import pyplot as plt
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from typing import Optional, Union, Literal

from . import GRADES
from .listener import GreenieBoardEventListener
from .trapsheet import read_trapsheet, parse_filename, plot_trapsheet
from .views import TrapView

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])


async def trap_users_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        show_ucid = utils.check_roles(interaction.client.roles['DCS Admin'], interaction.user)
        async with interaction.client.apool.connection() as conn:
            choices: list[app_commands.Choice[str]] = [
                app_commands.Choice(name=row[0] + (' (' + row[1] + ')' if show_ucid else ''), value=row[1])
                async for row in await conn.execute("""
                    SELECT DISTINCT p.name, p.ucid 
                    FROM players p 
                    JOIN traps t ON p.ucid = t.player_ucid 
                    ORDER BY 1
                """)
                if not current or current.casefold() in row[0].casefold() or current.casefold() in row[1].casefold()
            ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


class GreenieBoard(Plugin[GreenieBoardEventListener]):

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            self.log.info('No greenieboard.yaml found, copying the sample.')
            shutil.copyfile('samples/plugins/greenieboard.yaml',
                            os.path.join(self.node.config_dir, 'plugins', 'greenieboard.yaml'))
            config = super().read_locals()
        return config

    def get_config(self, server: Optional[Server] = None, *, plugin_name: Optional[str] = None,
                   use_cache: Optional[bool] = True) -> dict:
        # retrieve the config from another plugin
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        if not server:
            return self.locals.get(DEFAULT_TAG, {})
        if server.node.name not in self._config:
            self._config[server.node.name] = {}
        if server.instance.name not in self._config[server.node.name] or not use_cache:
            default, specific = self.get_base_config(server)
            if 'persistent_board' in default:
                del default['persistent_board']
            if 'persistent_channel' in default:
                del default['persistent_channel']
            self._config[server.node.name][server.instance.name] = default | specific
        return self._config[server.node.name][server.instance.name]

    @staticmethod
    def plot_trapheet(filename: str) -> bytes:
        ts = read_trapsheet(filename)
        ps = parse_filename(filename)
        fig, axs = plt.subplots(3, 1, sharex=True, facecolor="#404040", dpi=150)
        fig.set_size_inches(8, 6)
        plot_trapsheet(axs, ts, ps, filename)
        buf = io.BytesIO()
        try:
            plt.savefig(buf, format='png')
            return buf.getvalue()
        finally:
            buf.close()
            plt.close(fig)

    async def migrate(self, new_version: str, conn: Optional[psycopg.AsyncConnection] = None) -> None:
        if new_version == '3.2':
            self.log.info(f'  => Migrating {self.plugin_name.title()} to version {new_version}. This may take a bit.')
            # migrate all trapsheets from the old greenieboard table
            filenames = []
            async with conn.cursor(row_factory=dict_row) as cursor:
                async for row in await cursor.execute("SELECT * FROM greenieboard"):
                    filename = row['trapsheet']
                    try:
                        if filename and os.path.exists(filename):
                            if filename.endswith('.png'):
                                async with aiofiles.open(filename, mode='rb') as file:
                                    row['trapsheet'] = psycopg.Binary(await file.read())
                            elif filename.endswith('.csv'):
                                row['trapsheet'] = psycopg.Binary(await asyncio.to_thread(self.plot_trapheet, filename))
                        else:
                            row['trapsheet'] = filename = None
                        await conn.execute("""
                            INSERT INTO traps (mission_id, player_ucid, unit_type, grade, comment, place, trapcase, 
                                               wire, night, points, trapsheet, time)
                            VALUES (%(mission_id)s, %(player_ucid)s, %(unit_type)s, %(grade)s, %(comment)s, 
                                    %(place)s, %(trapcase)s, %(wire)s, %(night)s, %(points)s, %(trapsheet)s, %(time)s)
                        """, row)
                        if filename:
                            filenames.append(filename)
                    except Exception as ex:
                        if filename:
                            self.log.error(f"Error while migrating file {filename}: {ex}", exc_info=True)
                        raise
            for filename in filenames:
                with suppress(Exception):
                    os.remove(filename)
            await conn.execute("DROP TABLE greenieboard")
        elif new_version == '3.3':
            def change_instance(instance: dict):
                if 'ratings' in instance:
                    ratings = instance.pop('ratings')
                    grades = GRADES
                    for key, value in grades.items():
                        value['rating'] = ratings.get(key, 0)
                    instance['grades'] = grades

            config = os.path.join(self.node.config_dir, 'plugins', f'{self.plugin_name}.yaml')
            data = yaml.load(Path(config).read_text(encoding='utf-8'))
            if self.node.name in data.keys():
                for name, node in data.items():
                    if name == DEFAULT_TAG:
                        change_instance(node)
                        continue
                    for instance in node.values():
                        change_instance(instance)
            else:
                for instance in data.values():
                    change_instance(instance)
            with open(config, mode='w', encoding='utf-8') as outfile:
                yaml.dump(data, outfile)
            self.locals = self.read_locals()

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: Optional[str] = None) -> None:
        self.log.debug('Pruning Greenieboard ...')
        if ucids:
            for ucid in ucids:
                await conn.execute('DELETE FROM traps WHERE player_ucid = %s', (ucid,))
        elif days > -1:
            await conn.execute("""
                DELETE FROM traps WHERE time < (DATE(NOW() AT TIME ZONE 'UTC') - %s::interval)
            """,(f'{days} days', ))
        self.log.debug('Greenieboard pruned.')

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        await conn.execute('UPDATE traps SET player_ucid = %s WHERE player_ucid = %s', (new_ucid, old_ucid))

    # New command group "/traps"
    traps = Group(name="traps", description=_("Commands to display and manage carrier traps"))

    @traps.command(description=_('Show carrier landing qualifications'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(user=trap_users_autocomplete)
    async def info(self, interaction: discord.Interaction, user: str = None):
        def format_landing(landing: dict) -> str:
            return (f"{landing['time']:%y-%m-%d %H:%M:%S} - "
                    f"{landing['unit_type']}@{landing['place']}: {landing['grade']}")

        ephemeral = utils.get_ephemeral(interaction)
        if not user:
            ucid = await self.bot.get_ucid_by_member(interaction.user)
            name = interaction.user.display_name
        else:
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        num_landings = min(self.get_config().get('num_landings', 25), 25)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT g.id, p.name, g.grade, g.unit_type, g.comment, g.place, g.trapcase, g.wire, g.time, 
                           g.points
                    FROM traps g, players p 
                    WHERE p.ucid = %s AND g.player_ucid = p.ucid 
                    ORDER BY 1 DESC LIMIT %s
                """, (ucid, num_landings))
                if cursor.rowcount == 0:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('No carrier landings recorded for this user.'),
                                                            ephemeral=True)
                    return
                landings = [dict(row) async for row in cursor]
        report = Report(self.bot, self.plugin_name, 'traps.json')
        env = await report.render(ucid=ucid, name=utils.escape_string(name))
        n = await utils.selection(interaction, embed=env.embed, placeholder=_("Select a trap for details"),
                                  options=[
                                      SelectOption(label=format_landing(x), value=str(idx), default=(idx == 0))
                                      for idx, x in enumerate(landings)
                                  ], ephemeral=ephemeral)
        if n:
            report = PaginationReport(interaction, self.plugin_name, 'lsoRating.json', keep_image=True)
            await report.render(landings=landings, start_index=int(n), formatter=format_landing)

    @traps.command(description=_('Display the current Greenieboard'))
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    @app_commands.rename(num_rows='rows')
    @app_commands.rename(num_landings='landings')
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @app_commands.describe(landings_rtl=_("Draw landings right to left (default: True)"))
    async def board(self, interaction: discord.Interaction,
                    num_rows: Optional[Range[int, 5, 20]] = 10,
                    num_landings: Optional[Range[int, 1, 30]] = 30,
                    theme: Optional[Literal['light', 'dark']] = 'dark',
                    landings_rtl: Optional[bool] = True,
                    squadron_id: Optional[int] = None):
        report = PaginationReport(interaction, self.plugin_name, 'greenieboard.json')
        squadron = utils.get_squadron(self.node, squadron_id=squadron_id) if squadron_id else None
        await report.render(server_name=None, num_rows=num_rows, num_landings=num_landings, theme=theme,
                            landings_rtl=landings_rtl, squadron=squadron)

    @traps.command(description=_('Adds a trap to the Greenieboard'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def add(self, interaction: discord.Interaction,
                  user: app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        config = self.get_config()
        if 'grades' not in config:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('You need to specify grades in your greenieboard.yaml to use {}!').format(
                    (await utils.get_command(self.bot, group='traps', name='add')).mention
                ), ephemeral=True)
            return

        view = TrapView(self.bot, config, user)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(view=view, ephemeral=ephemeral)
        try:
            await view.wait()
            if view.success:
                await interaction.followup.send(_('Trap added.'), ephemeral=ephemeral)
            else:
                await interaction.followup.send(_('Aborted.'), ephemeral=True)
        finally:
            await interaction.delete_original_response()

    @traps.command(description=_('Resets all traps'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def reset(self, interaction: discord.Interaction,
                    user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]] = None):
        ephemeral = utils.get_ephemeral(interaction)
        if not user:
            message = _('Do you want to reset all traps?')
            sql = 'DELETE FROM traps'
            ucid = None
        else:
            if isinstance(user, discord.Member):
                ucid = await self.bot.get_ucid_by_member(user)
                if not ucid:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('User {} is not linked!').format(user.display_name),
                                                            ephemeral=ephemeral)
                    return
            else:
                ucid = user
            message = _('Do you want to reset all traps for user {}').format(
                user.display_name if isinstance(user, discord.Member) else user)
            sql = 'DELETE FROM traps WHERE player_ucid = %(ucid)s'
        if not await utils.yn_question(interaction, message, ephemeral=ephemeral):
            await interaction.followup.send(_('Aborted'), ephemeral=ephemeral)
            return
        async with self.node.apool.connection() as conn:
            await conn.execute(sql, {"ucid": ucid})
        await interaction.followup.send(_('All traps reset.'), ephemeral=ephemeral)


async def setup(bot: DCSServerBot):
    if 'missionstats' not in bot.plugins:
        raise PluginRequiredError('missionstats')
    await bot.add_cog(GreenieBoard(bot, GreenieBoardEventListener))
