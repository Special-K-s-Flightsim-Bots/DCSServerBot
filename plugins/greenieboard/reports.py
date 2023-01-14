import discord
import os
import psycopg2
import re
from contextlib import closing
from core import report, Coalition, Side, utils
from plugins.userstats.filter import StatisticsFilter
from . import ERRORS, DISTANCE_MARKS, GRADES
from .trapsheet import plot_trapsheet, read_trapsheet, parse_filename


class LSORating(report.EmbedElement):
    def render(self, landing: dict):
        grade = GRADES[landing['grade']]
        comment = landing['comment'].replace('/', '')
        wire = landing['wire']

        self.add_field(name="Date/Time", value=f"{landing['time']:%y-%m-%d %H:%M:%S}")
        self.add_field(name="Plane", value=f"{landing['unit_type']}")
        self.add_field(name="Carrier", value=f"{landing['place']}")

        self.add_field(name="LSO Grade: {}".format(landing['grade'].replace('_', '\\_')), value=grade)
        self.add_field(name="Wire", value=f"{wire}")
        self.add_field(name="Points", value=f"{landing['points']}")

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
                self.embed.add_field(name=text, value=comments, inline=False)


class TrapSheet(report.MultiGraphElement):

    def render(self, landing: dict):
        if 'trapsheet' not in landing or not landing['trapsheet']:
            return
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
    def render(self, server_name: str, period: str, limit: int, message: discord.Message, flt: StatisticsFilter,
               include_bolters: bool = False, include_waveoffs: bool = False):
        sql = "SELECT p.discord_id, COALESCE(p.name, 'Unknown') AS name, COUNT(g.*) AS value " \
              "FROM greenieboard g, missions m, statistics s, players p " \
              "WHERE g.mission_id = m.id AND s.mission_id = m.id AND g.player_ucid = s.player_ucid " \
              "AND g.player_ucid = p.ucid AND g.unit_type = s.slot AND g.time BETWEEN s.hop_on AND s.hop_off "
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
            if server_name in self.bot.servers:
                server = self.bot.servers[server_name]
                tmp = utils.get_sides(message, server)
                sides = [0]
                if Coalition.RED in tmp:
                    sides.append(Side.RED.value)
                if Coalition.BLUE in tmp:
                    sides.append(Side.BLUE.value)
                # in this specific case, we want to display all data, if in public channels
                if len(sides) == 0:
                    sides = [Side.SPECTATOR.value, Side.BLUE.value, Side.RED.value]
                sql += ' AND s.side in (' + ','.join([str(x) for x in sides]) + ')'
        if not include_bolters:
            sql += ' AND g.grade <> \'B\''
        if not include_waveoffs:
            sql += ' AND g.grade NOT LIKE \'WO%\''
        self.env.embed.title = flt.format(self.env.bot, period, server_name) + ' ' + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)
        sql += f' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {limit}'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                labels = []
                values = []
                self.log.debug(sql)
                cursor.execute(sql)
                for row in cursor.fetchall():
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, row['value'])
                self.axes.barh(labels, values, color=['#CD7F32', 'silver', 'gold'], label="Traps", height=0.75)
                self.axes.set_title("Traps", color='white', fontsize=25)
                self.axes.set_xlabel("traps")
                if len(values) == 0:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
