import discord
import json
import os
import psycopg2
import shutil
from contextlib import closing
from core import Plugin, DCSServerBot, PluginRequiredError, utils, PaginationReport, Report
from datetime import datetime
from discord import SelectOption
from discord.ext import commands
from os import path
from typing import Optional, Union, List
from . import const
from .listener import GreenieBoardEventListener


class GreenieBoard(Plugin):

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
            return f"{landing['time']:%y-%m-%d %H:%M:%S} - {landing['unit_type']}@{landing['place']} ({landing['grade']})"

        if not member:
            member = ctx.message.author
        if isinstance(member, discord.Member):
            ucid = self.bot.get_ucid_by_member(member)
            name = member.display_name
        else:
            name = member
            if len(params) > 0:
                name += ' ' + ' '.join(params)
            ucid = self.bot.get_ucid_by_name(name)
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

    def render_board(self):
        conn = self.pool.getconn()
        try:
            num_rows = self.locals['configs'][0]['num_rows']
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute('SELECT g.player_ucid, p.name, g.points, MAX(g.time) AS time FROM (SELECT player_ucid, '
                               'ROW_NUMBER() OVER w AS rn, AVG(points) OVER w AS points, MAX(time) OVER w AS time '
                               'FROM greenieboard WINDOW w AS (PARTITION BY player_ucid ORDER BY ID DESC ROWS BETWEEN '
                               'CURRENT ROW AND 9 FOLLOWING)) g, players p WHERE g.player_ucid = p.ucid AND g.rn = 1 '
                               'GROUP BY 1, 2, 3 ORDER BY 3 DESC LIMIT %s', (num_rows, ))
                if cursor.rowcount > 0:
                    embed = discord.Embed(title=f"Greenieboard (TOP {num_rows})",
                                          color=discord.Color.blue())
                    pilots = points = landings = ''
                    max_time = datetime.fromisocalendar(1970, 1, 1)
                    for row in cursor.fetchall():
                        pilots += row['name'] + '\n'
                        points += f"{row['points']:.2f}\n"
                        cursor.execute('SELECT TRIM(grade) as "grade", night FROM greenieboard WHERE player_ucid = %s '
                                       'ORDER BY ID DESC LIMIT 10', (row['player_ucid'], ))
                        i = 0
                        landings += '**|'
                        for landing in cursor.fetchall():
                            if landing['night']:
                                landings += const.NIGHT_EMOJIS[landing['grade']] + '|'
                            else:
                                landings += const.DAY_EMOJIS[landing['grade']] + '|'
                            i += 1
                        for i in range(i, 10):
                            landings += const.DAY_EMOJIS[None] + '|'
                        landings += '**\n'
                        if row['time'] > max_time:
                            max_time = row['time']
                    embed.add_field(name='Pilot', value=pilots)
                    embed.add_field(name='Avg', value=points)
                    embed.add_field(name='|:one:|:two:|:three:|:four:|:five:|:six:|:seven:|:eight:|:nine:|:zero:|',
                                    value=landings)
                    footer = ''
                    for grade, text in const.GRADES.items():
                        if grade not in ['WOP', 'OWO', 'TWO', 'WOFD']:
                            footer += const.DAY_EMOJIS[grade] + ' ' + text + '\n'
                    footer += '\nLandings are added at the front, meaning 1 is your latest landing.\nNight landings ' \
                              'shown by round markers.'
                    if max_time:
                        footer += f'\nLast update: {max_time:%y-%m-%d %H:%M:%S}'
                    embed.set_footer(text=footer)
                    return embed
                else:
                    return None
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Display the current greenieboard', aliases=['greenie'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def greenieboard(self, ctx):
        try:
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            embed = self.render_board()
            if embed:
                await ctx.send(embed=embed, delete_after=timeout if timeout > 0 else None)
            else:
                await ctx.send('No carrier landings recorded yet.', delete_after=timeout if timeout > 0 else None)
        finally:
            await ctx.message.delete()


async def setup(bot: DCSServerBot):
    if 'missionstats' not in bot.plugins:
        raise PluginRequiredError('missionstats')
    # make sure that we have a proper configuration, take the default one if none is there
    if not path.exists('config/greenieboard.json'):
        bot.log.info('No greenieboard.json found, copying the sample.')
        shutil.copyfile('config/greenieboard.json.sample', 'config/greenieboard.json')
    await bot.add_cog(GreenieBoard(bot, GreenieBoardEventListener))
