import discord
import json
import os
import psycopg2
import shutil
from contextlib import closing
from core import Plugin, DCSServerBot, PluginRequiredError, utils, PaginationReport
from datetime import datetime
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

    @staticmethod
    def format_comments(data, marker, marker_emoji):
        embed = discord.Embed(title=f"Latest Carrier Landings for user {data[0]['name']}", color=discord.Color.blue())
        ids = landings = grades = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            landings += f"{data[i]['time']:%y-%m-%d %H:%M:%S} - {data[i]['unit_type']}@{data[i]['place']}\n"
            grade = data[i]['grade'].replace('_', '\\_')
            grades += f"{grade}\n"
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Landing', value=landings)
        embed.add_field(name='Grade', value=grades)
        embed.set_footer(text='Press a number to display details about that specific landing.')
        return embed

    @commands.command(description='Show carrier landing qualifications', usage='[member]')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def carrier(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        if not member:
            member = ctx.message.author
        elif isinstance(member, str):
            name = member
            if len(params) > 0:
                name += ' ' + ' '.join(params)
            ucid = self.bot.get_ucid_by_name(name)
        landings = List[dict]
        num_landings = self.locals['configs'][0]['num_landings']
        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                if isinstance(member, discord.Member):
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s ORDER BY last_seen DESC LIMIT 1',
                                   (member.id, ))
                    ucid = cursor.fetchone()['ucid']
                cursor.execute("SELECT p.name, g.grade, g.unit_type, g.comment, g.place, g.wire, g.time, g.points, "
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
        await ctx.message.delete()
        n = await utils.selection_list(self, ctx, landings, self.format_comments)
        if n != -1:
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'lsoRating.json',
                                      timeout if timeout > 0 else None)
            await report.render(landings=landings, start_index=n)

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
                        cursor.execute('SELECT grade, night FROM greenieboard WHERE player_ucid = %s ORDER BY ID DESC '
                                       'LIMIT 10', (row['player_ucid'], ))
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
