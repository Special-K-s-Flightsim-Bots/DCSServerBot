import discord
import matplotlib.pyplot as plt
import numpy as np
import psycopg2
import psycopg2.extras
from contextlib import closing
from core import report, utils
from matplotlib.axes import Axes
from matplotlib.patches import ConnectionPatch
from typing import Union
from .filter import StatisticsFilter


class PlaytimesPerPlane(report.GraphElement):

    def render(self, member: Union[discord.Member, str], server_name: str, period: str, flt: StatisticsFilter):
        sql = 'SELECT s.slot, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime FROM ' \
              'statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND ' \
              's.hop_off IS NOT NULL AND s.mission_id = m.id '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            self.env.embed.description = utils.escape_string(server_name)
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        self.env.embed.title = flt.format(self.env.bot, period, server_name) + ' ' + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)
        sql += ' GROUP BY s.slot ORDER BY 2'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                labels = []
                values = []
                for row in cursor.fetchall():
                    labels.insert(0, row['slot'])
                    values.insert(0, row['playtime'] / 3600.0)
                self.axes.bar(labels, values, width=0.5, color='mediumaquamarine')
                for label in self.axes.get_xticklabels():
                    label.set_rotation(30)
                    label.set_ha('right')
                self.axes.set_title('Airframe Hours per Aircraft', color='white', fontsize=25)
                self.axes.set_yticks([])
                for i in range(0, len(values)):
                    self.axes.annotate('{:.1f} h'.format(values[i]), xy=(
                        labels[i], values[i]), ha='center', va='bottom', weight='bold')
                if cursor.rowcount == 0:
                    self.axes.set_xticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class PlaytimesPerServer(report.GraphElement):

    def render(self, member: Union[discord.Member, str], server_name: str, period: str, flt: StatisticsFilter):
        sql = f"SELECT regexp_replace(m.server_name, '{self.bot.config['FILTER']['SERVER_FILTER']}', '', 'g') AS " \
              f"server_name, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime FROM statistics s, " \
              f"players p, missions m WHERE s.player_ucid = p.ucid AND m.id = s.mission_id AND " \
              f"s.hop_off IS NOT NULL "
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)
        sql += ' GROUP BY 1'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                if cursor.rowcount > 0:
                    def func(pct, allvals):
                        absolute = int(round(pct / 100. * np.sum(allvals)))
                        return utils.convert_time(absolute)

                    labels = []
                    values = []
                    for row in cursor.fetchall():
                        labels.insert(0, row['server_name'])
                        values.insert(0, row['playtime'])
                    patches, texts, pcts = self.axes.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                                    wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
                    plt.setp(pcts, color='black', fontweight='bold')
                    self.axes.set_title('Server Time', color='white', fontsize=25)
                    self.axes.axis('equal')
                else:
                    self.axes.set_visible(False)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class PlaytimesPerMap(report.GraphElement):

    def render(self, member: Union[discord.Member, str], server_name: str, period: str, flt: StatisticsFilter):
        sql = 'SELECT m.mission_theatre, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS ' \
              'playtime FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND ' \
              'm.id = s.mission_id AND s.hop_off IS NOT NULL '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)
        sql += ' GROUP BY m.mission_theatre'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                if cursor.rowcount > 0:
                    def func(pct, allvals):
                        absolute = int(round(pct / 100. * np.sum(allvals)))
                        return utils.convert_time(absolute)

                    labels = []
                    values = []
                    for row in cursor.fetchall():
                        labels.insert(0, row['mission_theatre'])
                        values.insert(0, row['playtime'])
                    patches, texts, pcts = self.axes.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                                    wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
                    plt.setp(pcts, color='black', fontweight='bold')
                    self.axes.set_title('Time per Map', color='white', fontsize=25)
                    self.axes.axis('equal')
                else:
                    self.axes.set_visible(False)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class RecentActivities(report.GraphElement):

    def render(self, member: Union[discord.Member, str], server_name: str, period: str, flt: StatisticsFilter):
        sql = 'SELECT TO_CHAR(s.hop_on, \'MM/DD\') as day, ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(' \
              's.hop_off, NOW()) - s.hop_on)))) AS playtime FROM statistics s, players p, missions m WHERE ' \
              's.player_ucid = p.ucid AND s.hop_on > (DATE(NOW()) - integer \'7\') ' \
              'AND s.mission_id = m.id '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)
        sql += ' GROUP BY day'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                labels = []
                values = []
                cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                self.axes.set_title('Recent Activities', color='white', fontsize=25)
                self.axes.set_yticks([])
                for row in cursor.fetchall():
                    labels.append(row['day'])
                    values.append(row['playtime'] / 3600.0)
                self.axes.bar(labels, values, width=0.5, color='mediumaquamarine')
                for i in range(0, len(values)):
                    self.axes.annotate('{:.1f} h'.format(values[i]), xy=(
                        labels[i], values[i]), ha='center', va='bottom', weight='bold')
                if cursor.rowcount == 0:
                    self.axes.set_xticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class FlightPerformance(report.GraphElement):

    def render(self, member: Union[discord.Member, str], server_name: str, period: str, flt: StatisticsFilter):
        sql = 'SELECT SUM(ejections) as "Ejections", SUM(crashes-ejections) as "Crashes\n(Pilot dead)", ' \
              'SUM(landings) as "Landings" FROM statistics s, ' \
              'players p, missions m WHERE s.player_ucid = p.ucid ' \
              'AND s.mission_id = m.id '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                if cursor.rowcount > 0:
                    def func(pct, allvals):
                        absolute = int(round(pct / 100. * np.sum(allvals)))
                        return f'{absolute}'

                    labels = []
                    values = []
                    for name, value in dict(cursor.fetchone()).items():
                        if value and value > 0:
                            labels.append(name)
                            values.append(value)
                    if len(values) > 0:
                        patches, texts, pcts = \
                            self.axes.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                          wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
                        plt.setp(pcts, color='black', fontweight='bold')
                        self.axes.set_title('Flying', color='white', fontsize=25)
                        self.axes.axis('equal')
                    else:
                        self.axes.set_visible(False)
                else:
                    self.axes.set_visible(False)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class KDRatio(report.MultiGraphElement):

    def draw_kill_performance(self, ax: Axes, member: Union[discord.Member, str], server_name: str, period: str,
                              flt: StatisticsFilter):
        sql = 'SELECT COALESCE(SUM(kills - pvp), 0) as "AI Kills", COALESCE(SUM(pvp), 0) as "Player Kills", ' \
              'COALESCE(SUM(deaths_planes + deaths_helicopters + deaths_ships + deaths_sams + deaths_ground - ' \
              'deaths_pvp), 0) as "Deaths by AI", COALESCE(SUM(deaths_pvp),0) as "Deaths by Player", COALESCE(SUM(' \
              'GREATEST(deaths, crashes) - deaths_planes - deaths_helicopters - deaths_ships - deaths_sams - ' \
              'deaths_ground), 0) AS "Selfkill", COALESCE(SUM(teamkills), 0) as "Teamkills" FROM statistics s, ' \
              'players p, missions m WHERE s.player_ucid = p.ucid AND s.mission_id = m.id '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)

        retval = []
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                if cursor.rowcount > 0:
                    def func(pct, allvals):
                        absolute = int(round(pct / 100. * np.sum(allvals)))
                        return f'{absolute}'

                    labels = []
                    values = []
                    explode = []
                    result = cursor.fetchone()
                    for name, value in dict(result).items():
                        if value and value > 0:
                            labels.append(name)
                            values.append(value)
                            retval.append(name)
                            explode.append(0.02)
                    if len(values) > 0:
                        angle1 = -180 * (result[0] + result[1]) / np.sum(values)
                        angle2 = 180 - 180 * (result[2] + result[3]) / np.sum(values)
                        if angle1 == 0:
                            angle = angle2
                        elif angle2 == 180:
                            angle = angle1
                        else:
                            angle = angle1 + (angle2 + angle1) / 2

                        patches, texts, pcts = ax.pie(values, labels=labels, startangle=angle, explode=explode,
                                                      autopct=lambda pct: func(pct, values),
                                                      colors=['lightgreen', 'darkorange', 'lightblue'],
                                                      wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'},
                                                      normalize=True)
                        plt.setp(pcts, color='black', fontweight='bold')
                        ax.set_title('Kill/Death-Ratio', color='white', fontsize=25)
                        ax.axis('equal')
                    else:
                        ax.set_visible(False)
                else:
                    ax.set_visible(False)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        return retval

    def draw_kill_types(self, ax: Axes, member: Union[discord.Member, str], server_name: str, period: str,
                        flt: StatisticsFilter):
        sql = 'SELECT COALESCE(SUM(kills_planes), 0) as planes, COALESCE(SUM(kills_helicopters), 0) helicopters, ' \
              'COALESCE(SUM(kills_ships), 0) as ships, COALESCE(SUM(kills_sams), 0) as air_defence, COALESCE(SUM(' \
              'kills_ground), 0) as ground FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND ' \
              's.mission_id = m.id '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)

        retval = False
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                # if no data was found, return False as no chart was drawn
                if cursor.rowcount > 0:
                    labels = []
                    values = []
                    for item in dict(cursor.fetchone()).items():
                        labels.append(item[0].replace('_', ' ').title())
                        values.append(item[1])
                    xpos = 0
                    bottom = 0
                    width = 0.2
                    # there is something to be drawn
                    _sum = np.sum(values)
                    if _sum > 0:
                        for i in range(len(values)):
                            height = values[i] / _sum
                            ax.bar(xpos, height, width, bottom=bottom)
                            ypos = bottom + ax.patches[i].get_height() / 2
                            bottom += height
                            if int(values[i]) > 0:
                                ax.text(xpos, ypos, f"{values[i]}", ha='center', color='black')

                        ax.set_title('Killed by\nPlayer', color='white', fontsize=15)
                        ax.axis('off')
                        ax.set_xlim(- 2.5 * width, 2.5 * width)
                        ax.legend(labels, fontsize=15, loc=3, ncol=6, mode='expand',
                                    bbox_to_anchor=(-2.4, -0.2, 2.8, 0.4), columnspacing=1, frameon=False)
                        # Chart was drawn, return True
                        retval = True
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        return retval

    def draw_death_types(self, ax: Axes, legend: bool, member: Union[discord.Member, str], server_name: str,
                         period: str, flt: StatisticsFilter):
        sql = 'SELECT SUM(deaths_planes) as planes, SUM(deaths_helicopters) helicopters, SUM(deaths_ships) as ships, ' \
              'SUM(deaths_sams) as air_defence, SUM(deaths_ground) as ground FROM statistics s, players p, ' \
              'missions m WHERE s.player_ucid = p.ucid AND s.mission_id = m.id '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)

        retval = False
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                result = cursor.fetchone()
                # if no data was found, return False as no chart was drawn
                if cursor.rowcount > 0:
                    labels = []
                    values = []
                    for item in dict(result).items():
                        labels.append(item[0].replace('_', ' ').title())
                        values.append(item[1])
                    xpos = 0
                    bottom = 0
                    width = 0.2
                    # there is something to be drawn
                    _sum = np.sum(values)
                    if _sum > 0:
                        for i in range(len(values)):
                            height = values[i] / _sum
                            ax.bar(xpos, height, width, bottom=bottom)
                            ypos = bottom + ax.patches[i].get_height() / 2
                            bottom += height
                            if int(values[i]) > 0:
                                ax.text(xpos, ypos, f"{values[i]}", ha='center', color='black')

                        ax.set_title('Player\nkilled by', color='white', fontsize=15)
                        ax.axis('off')
                        ax.set_xlim(- 2.5 * width, 2.5 * width)
                        if legend is True:
                            ax.legend(labels, fontsize=15, loc=3, ncol=6, mode='expand',
                                      bbox_to_anchor=(0.6, -0.2, 2.8, 0.4), columnspacing=1, frameon=False)
                        # Chart was drawn, return True
                        retval = True
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        return retval

    def render(self, member: Union[discord.Member, str], server_name: str, period: str, flt: StatisticsFilter):
        retval = self.draw_kill_performance(self.axes[1], member, server_name, period, flt)
        i = 0
        if ('AI Kills' in retval or 'Player Kills' in retval) and \
                (self.draw_kill_types(self.axes[2], member, server_name, period, flt) is True):
            # use ConnectionPatch to draw lines between the two plots
            # get the wedge data
            theta1 = self.axes[1].patches[i].theta1
            if 'AI Kills' in retval and 'Player Kills' in retval:
                i += 1
            theta2 = self.axes[1].patches[i].theta2
            center, r = self.axes[1].patches[i].center, self.axes[1].patches[i].r
            bar_height = sum([item.get_height() for item in self.axes[2].patches])

            # draw top connecting line
            x = r * np.cos(np.pi / 180 * theta2) + center[0]
            y = r * np.sin(np.pi / 180 * theta2) + center[1]
            con = ConnectionPatch(xyA=(-0.2 / 2, bar_height), coordsA=self.axes[2].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[2].add_artist(con)

            # draw bottom connecting line
            x = r * np.cos(np.pi / 180 * theta1) + center[0]
            y = r * np.sin(np.pi / 180 * theta1) + center[1]
            con = ConnectionPatch(xyA=(-0.2 / 2, 0), coordsA=self.axes[2].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[2].add_artist(con)
            i += 1
        else:
            self.axes[2].set_visible(False)
        if ('Deaths by AI' in retval or 'Deaths by Player' in retval) and \
                (self.draw_death_types(self.axes[0], (i == 0), member, server_name, period, flt) is True):
            # use ConnectionPatch to draw lines between the two plots
            # get the wedge data
            theta1 = self.axes[1].patches[i].theta1
            if 'Deaths by AI' in retval and 'Deaths by Player' in retval:
                i += 1
            theta2 = self.axes[1].patches[i].theta2
            center, r = self.axes[1].patches[i].center, self.axes[1].patches[i].r
            bar_height = sum([item.get_height() for item in self.axes[0].patches])

            # draw top connecting line
            x = r * np.cos(np.pi / 180 * theta2) + center[0]
            y = r * np.sin(np.pi / 180 * theta2) + center[1]
            con = ConnectionPatch(xyA=(0.2 / 2, 0), coordsA=self.axes[0].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[0].add_artist(con)

            # draw bottom connecting line
            x = r * np.cos(np.pi / 180 * theta1) + center[0]
            y = r * np.sin(np.pi / 180 * theta1) + center[1]
            con = ConnectionPatch(xyA=(0.2 / 2, bar_height), coordsA=self.axes[0].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[0].add_artist(con)
        else:
            self.axes[0].set_visible(False)
