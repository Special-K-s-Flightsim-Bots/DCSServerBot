import discord
import os
import shutil
import time

from contextlib import closing
from core import Plugin, PluginRequiredError, utils, PaginationReport, Report, TEventListener, Group, Server, \
    DEFAULT_TAG
from discord import SelectOption, app_commands
from discord.app_commands import Range
from discord.ext import tasks
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Optional, Union, Type

from .listener import GreenieBoardEventListener
from .views import TrapView


class GreenieBoard(Plugin):

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            self.log.info('No greenieboard.yaml found, copying the sample.')
            shutil.copyfile('config/samples/plugins/greenieboard.yaml', 'config/plugins/greenieboard.yaml')
            config = super().read_locals()
        return config

    def get_config(self, server: Optional[Server] = None, *, plugin_name: Optional[str] = None,
                   use_cache: Optional[bool] = True) -> dict:
        # retrieve the config from another plugin
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        if not server:
            return self.locals.get(DEFAULT_TAG, {})
        if server.instance.name not in self._config:
            default, specific = self.get_base_config(server)
            if 'persistent_board' in default:
                del default['persistent_board']
            if 'persistent_channel' in default:
                del default['persistent_channel']
            self._config[server.instance.name] = default | specific
        return self._config[server.instance.name]

    async def prune(self, conn, *, days: int = -1, ucids: list[str] = None):
        self.log.debug('Pruning Greenieboard ...')
        if ucids:
            for ucid in ucids:
                conn.execute('DELETE FROM greenieboard WHERE player_ucid = %s', (ucid,))
        elif days > -1:
            conn.execute(f"DELETE FROM greenieboard WHERE time < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Greenieboard pruned.')

    # New command group "/trape"
    traps = Group(name="traps", description="Commands to display and manage carrier traps")

    @traps.command(description='Show carrier landing qualifications')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def info(self, interaction: discord.Interaction,
                   user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]]):
        def format_landing(landing: dict) -> str:
            return f"{landing['time']:%y-%m-%d %H:%M:%S} - {landing['unit_type']}@{landing['place']}: {landing['grade']}"

        if not user:
            user = interaction.user
        if isinstance(user, str):
            ucid = user
            user = self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = self.bot.get_ucid_by_member(user)
            name = user.display_name
        num_landings = max(self.get_config().get('num_landings', 25), 25)
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute("SELECT id, p.name, g.grade, g.unit_type, g.comment, g.place, g.trapcase, g.wire, "
                               "g.time, g.points, g.trapsheet FROM greenieboard g, players p WHERE p.ucid = %s "
                               "AND g.player_ucid = p.ucid ORDER BY ID DESC LIMIT %s", (ucid, num_landings))
                if cursor.rowcount == 0:
                    await interaction.response.send_message('No carrier landings recorded for this user.',
                                                            ephemeral=True)
                    return
                landings = [dict(row) for row in cursor.fetchall()]
        report = Report(self.bot, self.plugin_name, 'traps.json')
        env = await report.render(ucid=ucid, name=utils.escape_string(name))
        n = await utils.selection(interaction, embed=env.embed, placeholder="Select a trap for details",
                                  options=[
                                      SelectOption(label=format_landing(x), value=str(idx))
                                      for idx, x in enumerate(landings)
                                  ])
        if n:
            report = PaginationReport(self.bot, interaction, self.plugin_name, 'lsoRating.json', keep_image=True)
            await report.render(landings=landings, start_index=int(n), formatter=format_landing)

    @traps.command(description='Display the current greenieboard')
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    @app_commands.rename(num_rows='rows')
    async def board(self, interaction: discord.Interaction, num_rows: Optional[Range[int, 5, 20]] = 10):
        report = PaginationReport(self.bot, interaction, self.plugin_name, 'greenieboard.json')
        await report.render(server_name=None, num_rows=num_rows)

    @traps.command(description='Adds a trap to the Greenieboard')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def add(self, interaction: discord.Interaction,
                  user: app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]):
        config = self.get_config()
        if 'ratings' not in config:
            await interaction.response.send_message(
                'You need to specify ratings in your greenieboard.json to use add_trap!', ephemeral=True)
            return

        view = TrapView(self.bot, config, user)
        await interaction.response.send_message(view=view)
        try:
            await view.wait()
            if view.success:
                await interaction.followup.send('Trap added.', ephemeral=True)
            else:
                await interaction.followup.send('Aborted.', ephemeral=True)
        finally:
            await interaction.delete_original_response()


async def setup(bot: DCSServerBot):
    if 'missionstats' not in bot.plugins:
        raise PluginRequiredError('missionstats')
    await bot.add_cog(GreenieBoard(bot, GreenieBoardEventListener))
