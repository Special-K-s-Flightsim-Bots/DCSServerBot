import discord
import json
import os
import shutil
import time

from contextlib import closing
from copy import deepcopy
from core import Plugin, PluginRequiredError, utils, PaginationReport, Report, Server, TEventListener
from discord import SelectOption, app_commands
from discord.app_commands import Range
from discord.ext import tasks
from os import path
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Optional, Union, Type

from .listener import GreenieBoardEventListener
from .views import TrapView


class GreenieBoard(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.auto_delete.start()

    async def cog_unload(self):
        self.auto_delete.cancel()

    def get_config(self, server: Server) -> Optional[dict]:
        if server.name not in self._config:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server.installation == element['installation']) or \
                                ('server_name' in element and server.name == element['server_name']):
                            specific = deepcopy(element)
                    else:
                        default = deepcopy(element)
                # we only specify the persistent channel in the server settings, if it is expicitely defined
                if 'persistent_channel' in default:
                    del default['persistent_channel']
                if default and not specific:
                    self._config[server.name] = default
                elif specific and not default:
                    self._config[server.name] = specific
                elif default and specific:
                    merged = default
                    # specific settings will overwrite default settings
                    for key, value in specific.items():
                        merged[key] = value
                    self._config[server.name] = merged
            else:
                return None
        return self._config[server.name] if server.name in self._config else None

    def migrate(self, version: str):
        if version != '1.3':
            return
        os.rename('config/greenieboard.json', 'config/greenieboard.bak')
        with open('config/greenieboard.bak') as infile:
            old: dict = json.load(infile)
        dirty = False
        for config in old['configs']:
            if 'ratings' in config and '---' in config['ratings']:
                config['ratings']['--'] = config['ratings']['---']
                del config['ratings']['---']
                dirty = True
        if dirty:
            with open('config/greenieboard.json', 'w') as outfile:
                json.dump(old, outfile, indent=2)
                self.log.info('  => config/greenieboard.json migrated to new format, please verify!')

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None):
        self.log.debug('Pruning Greenieboard ...')
        if ucids:
            for ucid in ucids:
                conn.execute('DELETE FROM greenieboard WHERE player_ucid = %s', (ucid,))
        elif days > 0:
            conn.execute(f"DELETE FROM greenieboard WHERE time < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Greenieboard pruned.')

    # New command group "/trape"
    traps = app_commands.Group(name="traps", description="Commands to display and manage carrier traps")

    @traps.command(description='Show carrier landing qualifications')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def info(self, interaction: discord.Interaction,
                   user: app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]):
        def format_landing(landing: dict) -> str:
            return f"{landing['time']:%y-%m-%d %H:%M:%S} - {landing['unit_type']}@{landing['place']}: {landing['grade']}"

        if isinstance(user, str):
            ucid = user
            user = self.bot.get_member_or_name_by_ucid(user)
        if isinstance(user, discord.Member):
            ucid = self.bot.get_ucid_by_member(user)
            name = user.display_name
        else:
            name = user
        num_landings = max(self.locals['configs'][0]['num_landings'], 25)
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
            report = PaginationReport(self.bot, interaction, self.plugin_name, 'lsoRating.json')
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
        config = self.locals['configs'][0]
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

    @tasks.loop(hours=24.0)
    async def auto_delete(self):
        def do_delete(path: str, days: int):
            now = time.time()
            for f in [os.path.join(path, x) for x in os.listdir(path)]:
                if os.stat(f).st_mtime < (now - days * 86400):
                    if os.path.isfile(f):
                        os.remove(f)

        try:
            for server in self.bot.servers.values():
                config = self.get_config(server)
                basedir = os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'])
                if 'Moose.AIRBOSS' in config and 'delete_after' in config['Moose.AIRBOSS']:
                    basedir += os.path.sep + config['Moose.AIRBOSS']['basedir'] if 'basedir' in config['Moose.AIRBOSS'] else ''
                    do_delete(basedir, config['Moose.AIRBOSS']['delete_after'])
                elif 'FunkMan' in config and 'delete_after' in config['FunkMan']:
                    basedir += os.path.sep + config['FunkMan']['basedir'] if 'basedir' in config['FunkMan'] else ''
                    do_delete(basedir, config['FunkMan']['delete_after'])
        except Exception as ex:
            self.log.exception(ex)


async def setup(bot: DCSServerBot):
    if 'missionstats' not in bot.plugins:
        raise PluginRequiredError('missionstats')
    # make sure that we have a proper configuration, take the default one if none is there
    if not path.exists('config/greenieboard.json'):
        bot.log.info('No greenieboard.json found, copying the sample.')
        shutil.copyfile('config/samples/greenieboard.json', 'config/greenieboard.json')
    await bot.add_cog(GreenieBoard(bot, GreenieBoardEventListener))
