import numpy as np
import pandas as pd
import psycopg2
from contextlib import closing
from core import const, report
from matplotlib.ticker import FuncFormatter
from typing import Optional


class ServerUsage(report.EmbedElement):

    def render(self, server_name: Optional[str], period: Optional[str]):
        sql = f"SELECT trim(regexp_replace(m.server_name, '{self.bot.config['FILTER']['SERVER_FILTER']}', '', 'g')) " \
              f"AS server_name, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime, " \
              f"COUNT(DISTINCT s.player_ucid) AS players, COUNT(DISTINCT p.discord_id) AS members FROM missions m, " \
              f"statistics s, players p WHERE m.id = s.mission_id AND s.player_ucid = p.ucid AND s.hop_off IS NOT NULL"
        if server_name:
            sql += f' AND m.server_name = \'{server_name}\' '
        if period:
            sql += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'
        sql += ' GROUP BY 1 ORDER BY 2 DESC'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                servers = playtimes = players = members = ''
                cursor.execute(sql)
                for row in cursor.fetchall():
                    servers += row['server_name'] + '\n'
                    playtimes += '{:.0f}\n'.format(row['playtime'])
                    players += '{:.0f}\n'.format(row['players'])
                    members += '{:.0f}\n'.format(row['members'])
                if len(servers) > 0:
                    if not server_name:
                        self.add_field(name='Server', value=servers)
                    self.add_field(name='Playtime (h)', value=playtimes)
                    self.add_field(name='Unique Players', value=players)
                    if server_name:
                        self.add_field(name='Discord Members', value=members)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class TopMissionPerServer(report.EmbedElement):

    def render(self, server_name: Optional[str], period: Optional[str], limit: int):
        sql_left = 'SELECT server_name, mission_name, playtime FROM (SELECT server_name, ' \
                                      'mission_name, playtime, ROW_NUMBER() OVER(PARTITION BY server_name ORDER BY ' \
                                      'playtime DESC) AS rn FROM ( '
        sql_inner = f"SELECT trim(regexp_replace(m.server_name, '{self.bot.config['FILTER']['SERVER_FILTER']}', '', " \
                    f"'g')) AS server_name, trim(regexp_replace(m.mis" \
                    f"sion_name, '{self.bot.config['FILTER']['MISSION_FILTER']}', ' ', 'g')) AS mission_name, " \
                    f"ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime FROM missions m, " \
                    f"statistics s WHERE m.id = s.mission_id AND s.hop_off IS NOT NULL "
        sql_right = ') AS x) AS y WHERE rn {} ORDER BY 3 DESC'
        if server_name:
            sql_inner += f' AND m.server_name = \'{server_name}\' '
        if period:
            sql_inner += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'
        sql_inner += ' GROUP BY 1, 2'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                servers = missions = playtimes = ''
                cursor.execute(sql_left + sql_inner + sql_right.format(
                    '= 1' if not server_name else f'<= {limit}'))
                for row in cursor.fetchall():
                    servers += row['server_name'] + '\n'
                    missions += row['mission_name'][:20] + '\n'
                    playtimes += '{:.0f}\n'.format(row['playtime'])
                if len(servers) > 0:
                    if not server_name:
                        self.add_field(name='Server', value=servers)
                    self.add_field(name='TOP Mission' if not server_name else f"TOP {limit} Missions", value=missions)
                    self.add_field(name='Playtime (h)', value=playtimes)
                    if server_name:
                        self.add_field(name='_ _', value='_ _')
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class TopModulesPerServer(report.EmbedElement):

    def render(self, server_name: Optional[str], period: Optional[str], limit: int):
        sql = 'SELECT s.slot, COUNT(s.slot) AS num_usage, COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - ' \
              's.hop_on))) / 3600),0) AS playtime, COUNT(DISTINCT s.player_ucid) AS players FROM missions m, ' \
              'statistics s WHERE m.id = s.mission_id '
        if server_name:
            sql += ' AND m.server_name = \'{}\' '.format(server_name)
        if period:
            sql += ' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {}\')'.format(period)
        sql += f" GROUP BY s.slot ORDER BY 3 DESC LIMIT {limit}"

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                modules = playtimes = players = ''
                cursor.execute(sql)
                for row in cursor.fetchall():
                    modules += row['slot'] + '\n'
                    playtimes += '{:.0f}\n'.format(row['playtime'])
                    players += '{:.0f} ({:.0f})\n'.format(row['players'], row['num_usage'])
                if len(modules) > 0:
                    self.add_field(name=f"TOP {limit} Modules", value=modules)
                    self.add_field(name='Playtime (h)', value=playtimes)
                    self.add_field(name='Players (# uses)', value=players)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class UniquePast14(report.GraphElement):

    def render(self, server_name: Optional[str]):
        sql = 'SELECT d.date AS date, COUNT(DISTINCT s.player_ucid) AS players FROM statistics s, ' \
              'missions m, generate_series(DATE(NOW()) - INTERVAL \'2 weeks\', DATE(NOW()), INTERVAL \'1 ' \
              'day\') d WHERE d.date BETWEEN DATE(s.hop_on) AND DATE(s.hop_off) AND s.mission_id = m.id '
        if server_name:
            sql += f" AND m.server_name = '{server_name}' "
        sql += ' GROUP BY d.date'

        labels = []
        values = []
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql)
                for row in cursor.fetchall():
                    labels.append(row['date'].strftime('%a %m/%d'))
                    values.append(row['players'])
                self.axes.bar(labels, values, width=0.5, color='dodgerblue')
                self.axes.set_title('Unique Players past 14 Days', color='white', fontsize=25)
                self.axes.set_yticks([])
                for label in self.axes.get_xticklabels():
                    label.set_rotation(30)
                    label.set_ha('right')
                for i in range(0, len(values)):
                    self.axes.annotate(values[i], xy=(
                        labels[i], values[i]), ha='center', va='bottom', weight='bold')
                if len(values) == 0:
                    self.axes.set_xticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class UsersPerDayTime(report.GraphElement):

    def render(self, server_name: Optional[str], period: Optional[str]):
        sql = 'SELECT to_char(s.hop_on, \'ID\') as weekday, to_char(h.time, \'HH24\') AS hour, ' \
              'COUNT(DISTINCT s.player_ucid) AS players FROM statistics s, missions m, generate_series(' \
              'TIMESTAMP \'01.01.1970 00:00:00\', TIMESTAMP \'01.01.1970 23:00:00\', INTERVAL \'1 hour\') ' \
              'h WHERE date_part(\'hour\', h.time) BETWEEN date_part(\'hour\', s.hop_on) AND date_part(' \
              '\'hour\', s.hop_off) AND s.mission_id = m.id '
        if server_name:
            sql += f" AND m.server_name = '{server_name}' "
        if period:
            sql += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'
        sql += ' GROUP BY 1, 2'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                values = np.zeros((24, 7))
                cursor.execute(sql)
                for row in cursor.fetchall():
                    values[int(row['hour'])][int(row['weekday']) - 1] = row['players']
                self.axes.imshow(values, cmap='cividis', aspect='auto')
                self.axes.set_title('Users per Day/Time (UTC)', color='white', fontsize=25)
                self.axes.xaxis.set_major_formatter(FuncFormatter(lambda x, pos: const.WEEKDAYS[int(np.clip(x, 0, 6))]))
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class ServerLoad(report.MultiGraphElement):

    def render(self, server_name: Optional[str], period: str, agent_host: Optional[str]):
        sql = f"select date_trunc('minute', time) AS time, SUM(users) AS users, SUM(cpu) AS cpu, " \
              f"SUM(mem_total-mem_ram)/(1024*1024) AS mem_swap, SUM(mem_ram)/(1024*1024) AS mem_ram, " \
              f"SUM(read_bytes)/1024 AS read_bytes, SUM(write_bytes)/1024 AS write_bytes, ROUND(AVG(bytes_sent)) " \
              f"AS bytes_sent, ROUND(AVG(bytes_recv)) AS bytes_recv, ROUND(AVG(fps), 2) AS fps FROM serverstats " \
              f"WHERE time > (CURRENT_TIMESTAMP - interval '1 {period}')"
        if server_name:
            sql += f" AND server_name = '{server_name}' "
        if agent_host:
            sql += f" AND agent_host = '{agent_host}' "
        sql += " GROUP BY 1"
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute(sql, (agent_host, ))
                if cursor.rowcount > 0:
                    series = pd.DataFrame.from_dict(cursor.fetchall())
                    ax2 = self.axes[0].twinx()
                    series.plot(ax=self.axes[0], x='time', y=['fps', 'cpu'], title='Users/CPU/FPS', xticks=[], xlabel='', ylim=(0, 100))
                    self.axes[0].legend(['FPS', 'CPU'])
                    series.plot(ax=ax2, x='time', y=['users'], xticks=[], xlabel='', color='blue')
                    ax2.legend(['Users'])
                    series.plot(ax=self.axes[1], x='time', y=['mem_ram', 'mem_swap'], title='Memory', xticks=[], xlabel="", ylabel='Memory (MB)', kind='bar', stacked=True)
                    self.axes[1].legend(['Memory (RAM)', 'Memory (paged)'])
                    series.plot(ax=self.axes[2], x='time', y=['read_bytes', 'write_bytes'], title='Disk', logy=True, xticks=[], xlabel='', ylabel='KB', kind='bar', grid=True)
                    self.axes[2].legend(['Read', 'Write'])
                    series.plot(ax=self.axes[3], x='time', y=['bytes_sent', 'bytes_recv'], title='Network', logy=True, xlabel='', ylabel='KB/s', grid=True)
                    self.axes[3].legend(['Sent', 'Recv'])
                else:
                    for i in range(0, 4):
                        self.axes[i].set_xticks([])
                        self.axes[i].set_yticks([])
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
