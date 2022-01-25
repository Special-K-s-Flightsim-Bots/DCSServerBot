import discord
import matplotlib.pyplot as plt
import numpy as np
import string
import psycopg2
import psycopg2.extras
from contextlib import closing
from datetime import timedelta
from matplotlib.axes import Axes
from matplotlib.patches import ConnectionPatch
from core import report


class PlaytimesPerPlane(report.GraphElement):

    def render(self, member: discord.Member, server_name, period):
        sql = 'SELECT s.slot, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime FROM ' \
              'statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s AND ' \
              's.hop_off IS NOT NULL AND s.mission_id = m.id '
        if server_name:
            sql += f'AND m.server_name = \'{server_name}\' '
        if period:
            sql += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'
        sql += 'GROUP BY s.slot ORDER BY 2'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id,))
                labels = []
                values = []
                for row in cursor.fetchall():
                    labels.insert(0, row['slot'])
                    values.insert(0, row['playtime'] / 3600.0)
                self.axes.bar(labels, values, width=0.5, color='mediumaquamarine')
                for label in self.axes.get_xticklabels():
                    label.set_rotation(30)
                    label.set_ha('right')
                self.axes.set_title('Flighttimes per Plane', color='white', fontsize=25)
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

    def render(self, member: discord.Member, server_name, period):
        sql = f"SELECT regexp_replace(m.server_name, '{self.bot.config['FILTER']['SERVER_FILTER']}', '', 'g') AS " \
              f"server_name, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime FROM statistics s, " \
              f"players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s AND m.id = s.mission_id AND " \
              f"s.hop_off IS NOT NULL "
        if server_name:
            sql += f'AND m.server_name = \'{server_name}\' '
        if period:
            sql += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'
        sql += 'GROUP BY 1'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id,))
                if cursor.rowcount > 0:
                    def func(pct, allvals):
                        absolute = int(round(pct / 100. * np.sum(allvals)))
                        return '{:.1f}%\n({:s}h)'.format(pct, str(timedelta(seconds=absolute)))

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

    def render(self, member: discord.Member, server_name, period):
        sql = 'SELECT m.mission_theatre, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS ' \
              'playtime FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND ' \
              'p.discord_id = %s AND m.id = s.mission_id AND s.hop_off IS NOT NULL '
        if server_name:
            sql += f'AND m.server_name = \'{server_name}\' '
        if period:
            sql += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'
        sql += 'GROUP BY m.mission_theatre'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id,))
                if cursor.rowcount > 0:
                    def func(pct, allvals):
                        absolute = int(round(pct / 100. * np.sum(allvals)))
                        return '{:.1f}%\n({:s}h)'.format(pct, str(timedelta(seconds=absolute)))

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

    def render(self, member: discord.Member, server_name, period):
        sql = 'SELECT TO_CHAR(s.hop_on, \'MM/DD\') as day, ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(' \
                         's.hop_off, NOW()) - s.hop_on)))) AS playtime FROM statistics s, players p, missions m WHERE ' \
                         's.player_ucid = p.ucid AND p.discord_id = %s AND s.hop_on > (DATE(NOW()) - integer \'7\') ' \
                         'AND s.mission_id = m.id '
        if server_name:
            sql += f'AND m.server_name = \'{server_name}\' '
        if period:
            sql += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'
        sql += 'GROUP BY day'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                labels = []
                values = []
                cursor.execute(sql, (member.id,))
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

    def render(self, member: discord.Member, server_name, period):
        sql = 'SELECT SUM(ejections) as ejections, SUM(crashes) as crashes, ' \
              'SUM(takeoffs) as takeoffs, SUM(landings) as landings FROM statistics s, ' \
              'players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s' \
              'AND s.mission_id = m.id '
        if server_name:
            sql += f'AND m.server_name = \'{server_name}\''
        if period:
            sql += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql, (member.id,))
                if cursor.rowcount > 0:
                    def func(pct, allvals):
                        absolute = int(round(pct / 100. * np.sum(allvals)))
                        return '{:.1f}%\n({:d})'.format(pct, absolute)

                    labels = []
                    values = []
                    for item in dict(cursor.fetchone()).items():
                        if item[1] is not None and item[1] > 0:
                            labels.append(string.capwords(item[0]))
                            values.append(item[1])
                    if len(values) > 0:
                        patches, texts, pcts = self.axes.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
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

    def draw_kill_performance(self, ax: Axes, member: discord.Member, server_name: str, period: str):
        SQL_STATISTICS = 'SELECT COALESCE(SUM(kills), 0) as kills, COALESCE(SUM(deaths), 0) as deaths, COALESCE(SUM(' \
                         'teamkills), 0) as teamkills FROM statistics s, players p, missions m WHERE s.player_ucid = ' \
                         'p.ucid AND p.discord_id = %s AND s.mission_id = m.id '
        if server_name:
            SQL_STATISTICS += f'AND m.server_name = \'{server_name}\''
        if period:
            SQL_STATISTICS += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'

        retval = []
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_STATISTICS, (member.id,))
                if cursor.rowcount > 0:
                    def func(pct, allvals):
                        absolute = int(round(pct / 100. * np.sum(allvals)))
                        return '{:.1f}%\n({:d})'.format(pct, absolute)

                    labels = []
                    values = []
                    explode = []
                    result = cursor.fetchone()
                    for item in dict(result).items():
                        if item[1] is not None and item[1] > 0:
                            labels.append(string.capwords(item[0]))
                            values.append(item[1])
                            if item[0] in ['deaths', 'kills']:
                                retval.append(item[0])
                                explode.append(0.1)
                            else:
                                explode.append(0.0)
                    if len(values):
                        angle1 = -180 * result[0] / np.sum(values)
                        angle2 = 180 - 180 * result[1] / np.sum(values)
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


    def draw_kill_types(self, ax: Axes, member: discord.Member, server_name: str, period: str):
        SQL_STATISTICS = 'SELECT 0 AS self, COALESCE(SUM(kills_planes), 0) as planes, COALESCE(SUM(' \
                         'kills_helicopters), 0) helicopters, COALESCE(SUM(kills_ships), 0) as ships, COALESCE(SUM(' \
                         'kills_sams), 0) as air_defence, COALESCE(SUM(kills_ground), 0) as ground FROM statistics s, ' \
                         'players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s AND s.mission_id = ' \
                         'm.id '
        if server_name:
            SQL_STATISTICS += f'AND m.server_name = \'{server_name}\' '
        if period:
            SQL_STATISTICS += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'

        retval = False
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_STATISTICS, (member.id,))
                # if no data was found, return False as no chart was drawn
                if cursor.rowcount > 0:
                    labels = []
                    values = []
                    for item in dict(cursor.fetchone()).items():
                        labels.append(string.capwords(item[0], sep='_').replace('_', ' '))
                        values.append(item[1])
                    xpos = 0
                    bottom = 0
                    width = 0.2
                    # there is something to be drawn
                    if np.sum(values) > 0:
                        for i in range(len(values)):
                            height = values[i] / np.sum(values)
                            ax.bar(xpos, height, width, bottom=bottom)
                            ypos = bottom + ax.patches[i].get_height() / 2
                            bottom += height
                            if int(values[i]) > 0:
                                ax.text(xpos, ypos, "%d%%" %
                                          (ax.patches[i].get_height() * 100), ha='center', color='black')

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


    def draw_death_types(self, ax: Axes, legend: bool, member: discord.Member, server_name: str, period: str):
        SQL_STATISTICS = 'SELECT SUM(deaths - deaths_planes - deaths_helicopters - deaths_ships - deaths_sams - ' \
                         'deaths_ground) AS self, SUM(deaths_planes) as planes, SUM(deaths_helicopters) helicopters, ' \
                         'SUM(deaths_ships) as ships, SUM(deaths_sams) as air_defence, SUM(deaths_ground) as ground ' \
                         'FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s ' \
                         'AND s.mission_id = m.id '
        if server_name:
            SQL_STATISTICS += f'AND m.server_name = \'{server_name}\' '
        if period:
            SQL_STATISTICS += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'

        retval = False
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_STATISTICS, (member.id,))
                result = cursor.fetchone()
                # if no data was found, return False as no chart was drawn
                if cursor.rowcount > 0:
                    labels = []
                    values = []
                    for item in dict(result).items():
                        labels.append(string.capwords(item[0], sep='_').replace('_', ' '))
                        values.append(item[1])
                    xpos = 0
                    bottom = 0
                    width = 0.2
                    # there is something to be drawn
                    if np.sum(values) > 0:
                        for i in range(len(values)):
                            height = values[i] / np.sum(values)
                            ax.bar(xpos, height, width, bottom=bottom)
                            ypos = bottom + ax.patches[i].get_height() / 2
                            bottom += height
                            if int(values[i]) > 0:
                                ax.text(xpos, ypos, "%d%%" %
                                          (ax.patches[i].get_height() * 100), ha='center', color='black')

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

    def render(self, member: discord.Member, server_name, period):
        retval = self.draw_kill_performance(self.axes[1], member, server_name, period)
        i = 0
        if ('kills' in retval) and (self.draw_kill_types(self.axes[2], member, server_name, period) is True):
            # use ConnectionPatch to draw lines between the two plots
            # get the wedge data
            theta1, theta2 = self.axes[1].patches[i].theta1, self.axes[1].patches[i].theta2
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
        if ('deaths' in retval) and (self.draw_death_types(self.axes[0], (i == 0), member, server_name, period) is True):
            # use ConnectionPatch to draw lines between the two plots
            # get the wedge data
            theta1, theta2 = self.axes[1].patches[i].theta1, self.axes[1].patches[i].theta2
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
