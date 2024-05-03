import discord
import os
import psycopg
import shutil

from core import Plugin, PluginRequiredError, utils, PaginationReport, Report, Group, Server, DEFAULT_TAG, \
    get_translation
from discord import SelectOption, app_commands
from discord.app_commands import Range
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Optional, Union

from .listener import GreenieBoardEventListener
from .views import TrapView

_ = get_translation(__name__.split('.')[1])


class GreenieBoard(Plugin):

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

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: Optional[str] = None) -> None:
        self.log.debug('Pruning Greenieboard ...')
        if ucids:
            for ucid in ucids:
                await conn.execute('DELETE FROM greenieboard WHERE player_ucid = %s', (ucid,))
        elif days > -1:
            await conn.execute("DELETE FROM greenieboard WHERE time < (DATE(NOW()) - %s::interval)", (f'{days} days', ))
        self.log.debug('Greenieboard pruned.')

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        await conn.execute('UPDATE greenieboard SET player_ucid = %s WHERE player_ucid = %s', (new_ucid, old_ucid))

    # New command group "/traps"
    traps = Group(name="traps", description=_("Commands to display and manage carrier traps"))

    @traps.command(description=_('Show carrier landing qualifications'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def info(self, interaction: discord.Interaction,
                   user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]] = None):
        def format_landing(landing: dict) -> str:
            return (f"{landing['time']:%y-%m-%d %H:%M:%S} - "
                    f"{landing['unit_type']}@{landing['place']}: {landing['grade']}")

        ephemeral = utils.get_ephemeral(interaction)
        if not user:
            user = interaction.user
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        num_landings = max(self.get_config().get('num_landings', 25), 25)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT id, p.name, g.grade, g.unit_type, g.comment, g.place, g.trapcase, g.wire, 
                           g.time, g.points, g.trapsheet 
                    FROM greenieboard g, players p 
                    WHERE p.ucid = %s AND g.player_ucid = p.ucid ORDER BY ID DESC LIMIT %s
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
            report = PaginationReport(self.bot, interaction, self.plugin_name, 'lsoRating.json', keep_image=True)
            await report.render(landings=landings, start_index=int(n), formatter=format_landing)

    @traps.command(description=_('Display the current Greenieboard'))
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    @app_commands.rename(num_rows='rows')
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    async def board(self, interaction: discord.Interaction,
                    num_rows: Optional[Range[int, 5, 20]] = 10,
                    squadron_id: Optional[int] = None):
        report = PaginationReport(self.bot, interaction, self.plugin_name, 'greenieboard.json')
        squadron = (await utils.get_squadron(self.bot, squadron_id=squadron_id)) if squadron_id else None
        await report.render(server_name=None, num_rows=num_rows, squadron=squadron)

    @traps.command(description=_('Adds a trap to the Greenieboard'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def add(self, interaction: discord.Interaction,
                  user: app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        config = self.get_config()
        if 'ratings' not in config:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('You need to specify ratings in your greenieboard.yaml to use {}!').format(
                    (await utils.get_command(self.bot, group='traps', name='add')).mention
                ), ephemeral=True)
            return

        view = TrapView(self.bot, config, user)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(view=view)
        try:
            await view.wait()
            if view.success:
                await interaction.followup.send(_('Trap added.'), ephemeral=ephemeral)
            else:
                await interaction.followup.send(_('Aborted.'), ephemeral=ephemeral)
        finally:
            await interaction.delete_original_response()


async def setup(bot: DCSServerBot):
    if 'missionstats' not in bot.plugins:
        raise PluginRequiredError('missionstats')
    await bot.add_cog(GreenieBoard(bot, GreenieBoardEventListener))
