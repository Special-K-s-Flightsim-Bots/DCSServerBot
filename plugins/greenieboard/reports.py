import discord
import io
import numpy as np
import re

from core import report, utils, EmbedElement, get_translation
from datetime import datetime
from matplotlib import cm
from plugins.userstats.filter import StatisticsFilter
from psycopg.rows import dict_row
from typing import Optional

from . import ERRORS, DISTANCE_MARKS, GRADES, const
from ..userstats.highscore import get_sides, compute_font_size

_ = get_translation(__name__.split('.')[1])


class LSORating(report.EmbedElement):
    async def render(self, landing: dict):
        grade = GRADES[landing['grade']]
        comment = landing['comment'].replace('/', '')

        self.add_field(name=_("Date/Time"), value=f"{landing['time']:%y-%m-%d %H:%M:%S}")
        self.add_field(name=_("Plane"), value=f"{landing['unit_type']}")
        self.add_field(name=_("Carrier"), value=f"{landing['place']}")

        self.add_field(name=_("Case"), value=f"{landing['trapcase']}")
        self.add_field(name=_("Wire"), value=f"{landing['wire'] or '-'}")
        self.add_field(name=_("Points"), value=f"{landing['points']}")

        self.add_field(name=_("LSO Grade: {}").format(landing['grade'].replace('_', '\\_')), value=grade, inline=False)
        self.add_field(name=_("LSO Comment"), value=comment.replace('_', '\\_'), inline=False)

        await report.Ruler(self.env).render(ruler_length=28)
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

                    little = re.findall(r"\((.*?)\)", element)
                    if len(little):
                        for x in little:
                            for y in deflate_comment(x):
                                comments += '- ' + y + _(' (a little)\n')
                            element = element.replace(f'({x})', '')
                        if not element:
                            continue
                    many = re.findall("_(.*?)_", element)
                    if len(many):
                        for x in many:
                            for y in deflate_comment(x):
                                comments += '- ' + y + _(' (a lot!)\n')
                            element = element.replace(f'_{x}_', '')
                        if not element:
                            continue
                    ignored = re.findall(r"\[(.*?)\]", element)
                    if len(ignored):
                        for x in ignored:
                            for y in deflate_comment(x):
                                comments += '- ' + y + _(' (ignored)\n')
                            element = element.replace(f'[{x}]', '')
                        if not element:
                            continue
                    for y in deflate_comment(element):
                        comments += '- ' + y + '\n'
            if len(comments) > 0:
                self.add_field(name=text, value=comments, inline=False)


class TrapSheet(report.EmbedElement):
    async def render(self, landing: dict):
        async with self.apool.connection() as conn:
            cursor = await conn.execute("SELECT trapsheet FROM traps WHERE id = %s", (landing['id'], ))
            trapsheet = (await cursor.fetchone())[0]
            if trapsheet:
                self.env.filename = 'trapsheet.png'
                self.env.buffer = io.BytesIO(trapsheet)
                self.embed.set_image(url='attachment://trapsheet.png')


class HighscoreTraps(report.GraphElement):

    async def render(self, interaction: discord.Interaction, server_name: str, limit: int,
                     flt: StatisticsFilter, include_bolters: bool = False, include_waveoffs: bool = False,
                     bar_labels: Optional[bool] = True):
        sql = "SELECT p.discord_id, COALESCE(p.name, 'Unknown') AS name, COUNT(g.*) AS value " \
              "FROM traps g, missions m, statistics s, players p " \
              "WHERE g.mission_id = m.id AND s.mission_id = m.id AND g.player_ucid = s.player_ucid " \
              "AND g.player_ucid = p.ucid AND g.unit_type = s.slot AND g.time BETWEEN s.hop_on AND s.hop_off "
        if server_name:
            sql += "AND m.server_name = %(server_name)s"
            self.env.embed.description = utils.escape_string(server_name)
            if server_name in self.bot.servers:
                sql += ' AND s.side in (' + ','.join([
                    str(x) for x in get_sides(interaction, self.bot.servers[server_name])
                ]) + ')'
        if not include_bolters:
            sql += " AND g.grade <> 'B'"
        if not include_waveoffs:
            sql += " AND g.grade NOT LIKE 'WO%%'"
        self.env.embed.title = flt.format(self.env.bot) + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot)
        sql += f' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {limit}'

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                labels = []
                values = []
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, row['value'])
                num_bars = len(labels)
                self.axes.set_title(_("Traps"), color='white', fontsize=25)
                self.axes.set_xlabel("traps")
                if num_bars > 0:
                    fontsize = compute_font_size(num_bars)
                    bar_height = max(0.75, 3 / num_bars)

                    color_map = cm.get_cmap('viridis', num_bars)
                    colors = color_map(np.linspace(0, 1, num_bars))
                    self.axes.barh(labels, values, color=colors, label="Traps", height=bar_height)
                    if bar_labels:
                        for c in self.axes.containers:
                            self.axes.bar_label(c, fmt='%d', label_type='edge', padding=2, fontsize=fontsize)
                        self.axes.margins(x=0.1)
                    self.axes.tick_params(axis='y', labelsize=fontsize)
                else:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, _('No data available.'), ha='center', va='center', rotation=45, size=15)


class GreenieBoard(EmbedElement):
    async def render(self, server_name: str, num_rows: int, squadron: Optional[dict] = None):
        sql1 = """
            SELECT g.player_ucid, p.name, g.points, MAX(g.time) AS time FROM (
                SELECT player_ucid, ROW_NUMBER() OVER w AS rn, 
                                    AVG(points) OVER w AS points, 
                                    MAX(time) OVER w AS time 
                FROM traps
        """
        sql2 = """
            SELECT TRIM(grade) as "grade", night FROM traps 
            WHERE player_ucid = %(player_ucid)s
        """
        if server_name:
            self.embed.description = utils.escape_string(server_name)
            sql1 += """
                WHERE mission_id in (
                    SELECT id FROM missions WHERE server_name = %(server_name)s
                )
            """
            sql2 += """
                AND mission_id in (
                    SELECT id FROM missions WHERE server_name = %(server_name)s
                )
            """
        if squadron:
            if self.embed.description:
                self.embed.description += '\n'
            else:
                self.embed.description = ""
            self.embed.description += f"Squadron \"{utils.escape_string(squadron['name'])}\""
            if server_name:
                sql1 += " AND "
            else:
                sql1 += " WHERE "
            sql1 += """
                player_ucid IN (
                    SELECT player_ucid FROM squadron_members WHERE squadron_id = %(squadron_id)s
                )
                """
        sql1 += """
                WINDOW w AS (
                    PARTITION BY player_ucid ORDER BY ID DESC ROWS BETWEEN CURRENT ROW AND 9 FOLLOWING
                )
            ) g, players p 
            WHERE g.player_ucid = p.ucid AND g.rn = 1 
            GROUP BY 1, 2, 3 
            ORDER BY 3 DESC LIMIT %(num_rows)s
        """
        sql2 += ' ORDER BY ID DESC LIMIT 10'

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                pilots = points = landings = ''
                max_time = datetime.fromisocalendar(1970, 1, 1)
                await cursor.execute(sql1, {
                    "server_name": server_name,
                    "num_rows": num_rows,
                    "squadron_id": squadron['id'] if squadron else None
                })
                rows = await cursor.fetchall()
                for row in rows:
                    member = self.bot.get_member_by_ucid(row['player_ucid'])
                    if member:
                        pilots += member.display_name + '\n'
                    else:
                        pilots += utils.escape_string(row['name']) + '\n'
                    points += f"{row['points']:.2f}\n"
                    await cursor.execute(sql2, {
                        "player_ucid": row['player_ucid'],
                        "server_name": server_name
                    })
                    i = 0
                    landings += '**|'
                    async for landing in cursor:
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
                self.add_field(name=_('Pilot'), value=pilots)
                self.add_field(name=_('Avg'), value=points)
                self.add_field(name='|:one:|:two:|:three:|:four:|:five:|:six:|:seven:|:eight:|:nine:|:zero:|',
                               value=landings)
                footer = ''
                for grade, text in const.GRADES.items():
                    if grade not in ['WOP', 'OWO', 'TWO', 'WOFD']:
                        footer += const.DAY_EMOJIS[grade] + '\t' + grade.ljust(6) + '\t' + text + '\n'
                footer += _('\nThe most recent landing is added at the front.\nNight landings have round markers.')
                if max_time:
                    footer += _('\nLast recorded trap: {time:%y-%m-%d %H:%M:%S}').format(time=max_time)
                self.embed.set_footer(text=footer)
