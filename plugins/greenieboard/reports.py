import discord
import os
import re

from contextlib import closing
from core import report, utils, EmbedElement, NothingToPlot
from datetime import datetime
from plugins.userstats.filter import StatisticsFilter
from psycopg.rows import dict_row
from typing import Optional

from . import ERRORS, DISTANCE_MARKS, GRADES, const
from .trapsheet import plot_trapsheet, read_trapsheet, parse_filename
from ..userstats.highscore import get_sides


class LSORating(report.EmbedElement):
    def render(self, landing: dict):
        grade = GRADES[landing['grade']]
        comment = landing['comment'].replace('/', '')

        self.add_field(name="Date/Time", value=f"{landing['time']:%y-%m-%d %H:%M:%S}")
        self.add_field(name="Plane", value=f"{landing['unit_type']}")
        self.add_field(name="Carrier", value=f"{landing['place']}")

        self.add_field(name="Case", value=f"{landing['trapcase']}")
        self.add_field(name="Wire", value=f"{landing['wire']}")
        self.add_field(name="Points", value=f"{landing['points']}")

        self.add_field(name="LSO Grade: {}".format(landing['grade'].replace('_', '\\_')), value=grade, inline=False)
        self.add_field(name="LSO Comment", value=comment.replace('_', '\\_'), inline=False)

        report.Ruler(self.env).render(ruler_length=28)
        # remove unnecessary blanks
        distance_marks = list(DISTANCE_MARKS.keys())
        elements = []
        for element in [e.strip() for e in comment.split()]:
            def merge(s1: str, s2: str):
                if '(' in s1 and '(' in s2:
                    pos = s1.find(')')
                    substr2 = s2[s2.find('(') + 1:s2.find(')')]
                    s1 = s1[:pos] + substr2 + s1[pos:]
                    s2 = s2.replace('(' + substr2 + ')', '')
                if '_' in s1 and '_' in s2:
                    pos = s1.rfind('_')
                    substr2 = s2[s2.find('_') + 1:s2.rfind('_')]
                    s1 = s1[:pos] + substr2 + s1[pos:]
                    s2 = s2.replace('_' + substr2 + '_', '')
                s1 += s2
                return s1

            if len(elements) == 0:
                elements.append(element)
            else:
                if not any(distance in elements[-1] for distance in distance_marks):
                    elements[-1] = merge(elements[-1], element)
                else:
                    elements.append(element)

        for mark, text in DISTANCE_MARKS.items():
            comments = ''
            for element in elements.copy():
                if mark in element:
                    elements.remove(element)
                    if mark != 'BC':
                        element = element.replace(mark, '')

                    def deflate_comment(_element: str) -> list[str]:
                        retval = []
                        while len(_element):
                            for error in ERRORS.keys():
                                if error in _element:
                                    retval.append(ERRORS[error])
                                    _element = _element.replace(error, '')
                                    break
                            else:
                                self.log.error(f'Element {_element} not found in LSO mapping!')
                                _element = ''
                        return retval

                    little = re.findall("\((.*?)\)", element)
                    if len(little):
                        for x in little:
                            for y in deflate_comment(x):
                                comments += '- ' + y + ' (a little)\n'
                            element = element.replace(f'({x})', '')
                        if not element:
                            continue
                    many = re.findall("_(.*?)_", element)
                    if len(many):
                        for x in many:
                            for y in deflate_comment(x):
                                comments += '- ' + y + ' (a lot!)\n'
                            element = element.replace(f'_{x}_', '')
                        if not element:
                            continue
                    ignored = re.findall("\[(.*?)\]", element)
                    if len(ignored):
                        for x in ignored:
                            for y in deflate_comment(x):
                                comments += '- ' + y + ' (ignored)\n'
                            element = element.replace(f'[{x}]', '')
                        if not element:
                            continue
                    for y in deflate_comment(element):
                        comments += '- ' + y + '\n'
            if len(comments) > 0:
                self.add_field(name=text, value=comments, inline=False)


class TrapSheet(report.MultiGraphElement):

    def render(self, landing: dict):
        if 'trapsheet' not in landing or not landing['trapsheet']:
            raise NothingToPlot()
        trapsheet = landing['trapsheet']
        if not os.path.exists(landing['trapsheet']):
            self.log.error(f"Can't read trapsheet {landing['trapsheet']}, file not found.")
            return
        if landing['trapsheet'].endswith('.csv'):
            ts = read_trapsheet(trapsheet)
            ps = parse_filename(trapsheet)
            plot_trapsheet(self.axes, ts, ps, trapsheet)
        elif landing['trapsheet'].endswith('.png'):
            self.env.filename = landing['trapsheet']
        else:
            self.log.error(f"Unsupported trapsheet format: {landing['trapsheet']}!")


class HighscoreTraps(report.GraphElement):

    def render(self, interaction: discord.Interaction, server_name: str, period: str, limit: int, flt: StatisticsFilter,
               include_bolters: bool = False, include_waveoffs: bool = False, bar_labels: Optional[bool] = True):
        sql = "SELECT p.discord_id, COALESCE(p.name, 'Unknown') AS name, COUNT(g.*) AS value " \
              "FROM greenieboard g, missions m, statistics s, players p " \
              "WHERE g.mission_id = m.id AND s.mission_id = m.id AND g.player_ucid = s.player_ucid " \
              "AND g.player_ucid = p.ucid AND g.unit_type = s.slot AND g.time BETWEEN s.hop_on AND s.hop_off "
        if server_name:
            sql += "AND m.server_name = %s"
            self.env.embed.description = utils.escape_string(server_name)
            if server_name in self.bot.servers:
                sql += ' AND s.side in (' + ','.join([str(x) for x in get_sides(interaction, self.bot.servers[server_name])]) + ')'
        if not include_bolters:
            sql += " AND g.grade <> 'B'"
        if not include_waveoffs:
            sql += " AND g.grade NOT LIKE 'WO%%'"
        self.env.embed.title = flt.format(self.env.bot, period, server_name) + ' ' + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)
        sql += f' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {limit}'

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                labels = []
                values = []
                if server_name:
                    rows = cursor.execute(sql, (server_name, )).fetchall()
                else:
                    rows = cursor.execute(sql).fetchall()
                for row in rows:
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, row['value'])
                self.axes.barh(labels, values, color=['#CD7F32', 'silver', 'gold'], label="Traps", height=0.75)
                if bar_labels:
                    for c in self.axes.containers:
                        self.axes.bar_label(c, fmt='%d', label_type='edge', padding=2)
                    self.axes.margins(x=0.1)
                self.axes.set_title("Traps", color='white', fontsize=25)
                self.axes.set_xlabel("traps")
                if len(values) == 0:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)


class GreenieBoard(EmbedElement):
    def render(self, server_name: str, num_rows: int):
        sql1 = 'SELECT g.player_ucid, p.name, g.points, MAX(g.time) AS time FROM (' \
               'SELECT player_ucid, ROW_NUMBER() OVER w AS rn, AVG(points) OVER w AS points, MAX(time) ' \
               'OVER w AS time FROM greenieboard'
        sql2 = 'SELECT TRIM(grade) as "grade", night FROM greenieboard WHERE player_ucid = %s'
        if server_name:
            self.embed.description = utils.escape_string(server_name)
            sql1 += f" WHERE mission_id in (SELECT id FROM missions WHERE server_name = '{server_name}')"
            sql2 += f" AND mission_id in (SELECT id FROM missions WHERE server_name = '{server_name}')"
        sql1 += ' WINDOW w AS (PARTITION BY player_ucid ORDER BY ID DESC ROWS BETWEEN CURRENT ROW AND 9 FOLLOWING)) ' \
                'g, players p WHERE g.player_ucid = p.ucid AND g.rn = 1 GROUP BY 1, 2, 3 ORDER BY 3 DESC LIMIT %s'
        sql2 += ' ORDER BY ID DESC LIMIT 10'

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                pilots = points = landings = ''
                max_time = datetime.fromisocalendar(1970, 1, 1)
                for row in cursor.execute(sql1, (num_rows, )).fetchall():
                    pilots += utils.escape_string(row['name']) + '\n'
                    points += f"{row['points']:.2f}\n"
                    cursor.execute(sql2, (row['player_ucid'], ))
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
                # if there is nothing to plot, don't do it
                if not landings:
                    return
                self.add_field(name='Pilot', value=pilots)
                self.add_field(name='Avg', value=points)
                self.add_field(name='|:one:|:two:|:three:|:four:|:five:|:six:|:seven:|:eight:|:nine:|:zero:|',
                               value=landings)
                footer = ''
                for grade, text in const.GRADES.items():
                    if grade not in ['WOP', 'OWO', 'TWO', 'WOFD']:
                        footer += const.DAY_EMOJIS[grade] + '\t' + grade.ljust(6) + '\t' + text + '\n'
                footer += '\nLandings are added at the front, meaning 1 is your latest landing.\n' \
                          'Night landings shown by round markers.'
                if max_time:
                    footer += f'\nLast recorded trap: {max_time:%y-%m-%d %H:%M:%S}'
                self.embed.set_footer(text=footer)
