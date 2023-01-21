import time

import discord
import json
import os
import psycopg2
import shutil
from contextlib import closing
from copy import deepcopy
from core import Plugin, DCSServerBot, PluginRequiredError, utils, PaginationReport, Report, Server, TEventListener
from discord import SelectOption
from discord.ext import commands, tasks
from os import path
from typing import Optional, Union, List, Type
from .listener import GreenieBoardEventListener


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
        with closing(conn.cursor()) as cursor:
            if ucids:
                for ucid in ucids:
                    cursor.execute('DELETE FROM greenieboard WHERE player_ucid = %s', (ucid,))
            elif days > 0:
                cursor.execute(f"DELETE FROM greenieboard WHERE time < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Greenieboard pruned.')

    @commands.command(description='Show carrier landing qualifications', usage='[member]', aliases=['traps'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def carrier(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        def format_landing(landing: dict) -> str:
            return f"{landing['time']:%y-%m-%d %H:%M:%S} - {landing['unit_type']}@{landing['place']}: {landing['grade']}"

        if not member:
            member = ctx.message.author
        if isinstance(member, discord.Member):
            ucid = self.bot.get_ucid_by_member(member)
            name = member.display_name
        else:
            name = member
            if len(params) > 0:
                name += ' ' + ' '.join(params)
            ucid, name = self.bot.get_ucid_by_name(name)
        landings = List[dict]
        num_landings = max(self.locals['configs'][0]['num_landings'], 25)
        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute("SELECT id, p.name, g.grade, g.unit_type, g.comment, g.place, g.wire, g.time, g.points, "
                               "g.trapsheet FROM greenieboard g, players p WHERE p.ucid = %s AND g.player_ucid = "
                               "p.ucid ORDER BY ID DESC LIMIT %s", (ucid, num_landings))
                if cursor.rowcount == 0:
                    await ctx.send('No carrier landings recorded for this user.',
                                   delete_after=timeout if timeout > 0 else None)
                    return
                landings = [dict(row) for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        report = Report(self.bot, self.plugin_name, 'traps.json')
        env = await report.render(ucid=ucid, name=name)
        n = await utils.selection(ctx, embed=env.embed, placeholder="Select a trap for details",
                                  options=[
                                      SelectOption(label=format_landing(x), value=str(idx))
                                      for idx, x in enumerate(landings)
                                  ])
        if n:
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'lsoRating.json',
                                      timeout if timeout > 0 else None)
            await report.render(landings=landings, start_index=int(n))
        await ctx.message.delete()

    @commands.command(description='Display the current greenieboard', usage='[num rows]', aliases=['greenie'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def greenieboard(self, ctx, num_rows: Optional[int] = 10):
        try:
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'greenieboard.json',
                                      timeout if timeout > 0 else None)
            await report.render(server_name=None, num_rows=num_rows)
        finally:
            await ctx.message.delete()

    @tasks.loop(hours=24.0)
    async def auto_delete(self):
        def do_delete(path: str, days: int):
            now = time.time()
            for f in [os.path.join(path, x) for x in os.listdir(path)]:
                if os.stat(f).st_mtime < (now - days * 86400):
                    if os.path.isfile(f):
                        os.remove(f)

        for server in self.bot.servers.values():
            config = self.get_config(server)
            basedir = os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'])
            if 'Moose.AIRBOSS' in config and 'delete_after' in config['Moose.AIRBOSS']:
                basedir += os.path.sep + config['Moose.AIRBOSS']['basedir'] if 'basedir' in config['Moose.AIRBOSS'] else ''
                do_delete(basedir, config['Moose.AIRBOSS']['delete_after'])
            elif 'FunkMan' in config and 'delete_after' in config['FunkMan']:
                basedir += os.path.sep + config['FunkMan']['basedir'] if 'basedir' in config['FunkMan'] else ''
                do_delete(basedir, config['FunkMan']['delete_after'])


async def setup(bot: DCSServerBot):
    if 'missionstats' not in bot.plugins:
        raise PluginRequiredError('missionstats')
    # make sure that we have a proper configuration, take the default one if none is there
    if not path.exists('config/greenieboard.json'):
        bot.log.info('No greenieboard.json found, copying the sample.')
        shutil.copyfile('config/greenieboard.json.sample', 'config/greenieboard.json')
    await bot.add_cog(GreenieBoard(bot, GreenieBoardEventListener))
