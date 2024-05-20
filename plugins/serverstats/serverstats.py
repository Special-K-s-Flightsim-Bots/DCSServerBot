import numpy as np
import pandas as pd
import seaborn as sns
import warnings

from core import const, report, EmbedElement, utils
from psycopg.rows import dict_row
from typing import Optional

from plugins.userstats.filter import StatisticsFilter

# ignore pandas warnings (log scale et al)
warnings.filterwarnings("ignore", category=UserWarning)


class ServerUsage(report.EmbedElement):

    async def render(self, server_name: Optional[str], period: StatisticsFilter):
        sql = f"""
            SELECT trim(regexp_replace(m.server_name, '{self.bot.filter['server_name']}', '', 'g')) AS server_name, 
                   ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime, 
                   COUNT(DISTINCT s.player_ucid) AS players, 
                   COUNT(DISTINCT p.discord_id) AS members 
            FROM missions m, statistics s, players p 
            WHERE m.id = s.mission_id AND s.player_ucid = p.ucid AND s.hop_off IS NOT NULL
        """
        if server_name:
            sql += " AND m.server_name = %(server_name)s"
        sql += ' AND ' + period.filter(self.env.bot)
        sql += ' GROUP BY 1 ORDER BY 2 DESC'

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                servers = playtimes = players = members = ''
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
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


class TopMissionPerServer(report.EmbedElement):

    async def render(self, server_name: Optional[str], period: StatisticsFilter, limit: int):
        sql_left = 'SELECT server_name, mission_name, playtime FROM (SELECT server_name, ' \
                                      'mission_name, playtime, ROW_NUMBER() OVER(PARTITION BY server_name ORDER BY ' \
                                      'playtime DESC) AS rn FROM ('
        sql_inner = f"""
            SELECT trim(regexp_replace(m.server_name, '{self.bot.filter['server_name']}', '', 'g')) AS server_name, 
                   trim(regexp_replace(m.mission_name, '{self.bot.filter['mission_name']}', ' ', 'g')) AS mission_name, 
                   ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime 
            FROM missions m, statistics s 
            WHERE m.id = s.mission_id AND s.hop_off IS NOT NULL
        """
        sql_right = ") AS x) AS y WHERE rn {} ORDER BY 3 DESC"
        if server_name:
            sql_inner += " AND m.server_name = %(server_name)s"
        sql_inner += ' AND ' + period.filter(self.env.bot)
        sql_inner += " GROUP BY 1, 2"

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                servers = missions = playtimes = ''
                await cursor.execute(sql_left + sql_inner + sql_right.format(
                    '= 1' if not server_name else f'<= {limit}'), {"server_name": server_name})
                async for row in cursor:
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


class TopModulesPerServer(report.EmbedElement):

    async def render(self, server_name: Optional[str], period: StatisticsFilter, limit: int):
        sql = """
            SELECT s.slot, COUNT(s.slot) AS num_usage, 
                   COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600),0) AS playtime, 
                   COUNT(DISTINCT s.player_ucid) AS players 
            FROM missions m, statistics s 
            WHERE m.id = s.mission_id
        """
        if server_name:
            sql += " AND m.server_name = %(server_name)s"
        sql += ' AND ' + period.filter(self.env.bot)
        sql += f" GROUP BY s.slot ORDER BY 3 DESC LIMIT {limit}"

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                modules = playtimes = players = ''
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    modules += row['slot'] + '\n'
                    playtimes += '{:.0f}\n'.format(row['playtime'])
                    players += '{:.0f} ({:.0f})\n'.format(row['players'], row['num_usage'])

        if len(modules) > 0:
            self.add_field(name=f"TOP {limit} Modules", value=modules)
            self.add_field(name='Playtime (h)', value=playtimes)
            self.add_field(name='Players (# uses)', value=players)


class UniquePast14(report.GraphElement):

    async def render(self, server_name: Optional[str]):
        sql = """
            SELECT d.date AS date, COUNT(DISTINCT s.player_ucid) AS players 
            FROM statistics s, missions m, 
                 generate_series(DATE(NOW()) - INTERVAL '2 weeks', DATE(NOW()), INTERVAL '1 day') d 
            WHERE d.date BETWEEN DATE(s.hop_on) AND DATE(s.hop_off) 
            AND s.mission_id = m.id
        """
        if server_name:
            sql += f" AND m.server_name = %(server_name)s"
        sql += ' GROUP BY d.date'

        labels = []
        values = []
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    labels.append(row['date'].strftime('%a %m-%d'))
                    values.append(row['players'])

        sns.barplot(x=labels, y=values, color='dodgerblue', ax=self.axes)
        self.axes.set_title('Unique Players past 14 Days', fontsize=25)
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)
        self.axes.spines['bottom'].set_visible(False)
        self.axes.spines['left'].set_visible(False)
        self.axes.set_yticks([])
        for i, value in enumerate(values):
            self.axes.text(i, value, int(value), ha='center', va='bottom', weight='bold')
        if not values:
            self.axes.set_xticks([])
            self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)


class UsersPerDayTime(report.GraphElement):

    async def render(self, server_name: Optional[str], period: StatisticsFilter):
        sql = """
            SELECT to_char(s.hop_on, 'ID') as weekday, to_char(h.time, 'HH24') AS hour, 
                   COUNT(DISTINCT s.player_ucid) AS players 
            FROM statistics s, missions m, generate_series(current_date, current_date + 1, INTERVAL '1 hour') h 
            WHERE date_part('hour', h.time) BETWEEN date_part('hour', s.hop_on) AND date_part('hour', s.hop_off) 
            AND s.mission_id = m.id
        """
        if server_name:
            sql += " AND m.server_name = %(server_name)s"
        sql += ' AND ' + period.filter(self.env.bot)
        sql += " GROUP BY 1, 2"

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                values = np.zeros((24, 7))
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    values[int(row['hour'])][int(row['weekday']) - 1] = row['players']

            sns.heatmap(values, cmap='viridis', cbar=False, annot=False,
                        yticklabels=[f'{i:02d}h' for i in range(24)],
                        xticklabels=[const.WEEKDAYS[i] for i in range(7)],
                        ax=self.axes)
            self.axes.invert_yaxis()
            self.axes.set_yticklabels(self.axes.get_yticklabels(), rotation=0)
            self.axes.set_title('Users per Day/Time (UTC)', color='white', fontsize=25)


class ServerLoadHeader(EmbedElement):
    async def render(self, node: str, server_name: Optional[str] = None):
        self.env.embed.description = \
            f"Node: {node}" if not server_name else f"Server: {utils.escape_string(server_name)}"


class ServerLoad(report.MultiGraphElement):

    async def render(self, node: str, period: str, server_name: Optional[str] = None):
        sql = """
            SELECT date_trunc('minute', time) AS time, AVG(users) AS users, AVG(cpu) AS cpu, 
                   AVG(CASE WHEN mem_total-mem_ram < 0 THEN 0 ELSE mem_total-mem_ram END)/(1024*1024) AS mem_paged,  
                   AVG(mem_ram)/(1024*1024) AS mem_ram, 
                   SUM(read_bytes)/1024 AS read, 
                   SUM(write_bytes)/1024 AS write, 
                   ROUND(AVG(bytes_sent)) AS sent, 
                   ROUND(AVG(bytes_recv)) AS recv, 
                   ROUND(AVG(fps), 2) AS fps, 
                   ROUND(AVG(ping), 2) AS ping 
            FROM serverstats 
            WHERE time > ((NOW() AT TIME ZONE 'UTC') - ('1 ' || %(period)s)::interval)
            AND node = %(node)s 
        """
        if server_name:
            sql += " AND server_name = %(server_name)s GROUP BY 1"
        if not server_name:
            sql = f"""
                SELECT time, SUM(users) AS users, SUM(cpu) AS cpu, SUM(mem_paged) AS mem_paged, SUM(mem_ram) AS mem_ram, 
                             SUM(read) AS read, SUM(write) AS write, ROUND(AVG(sent)) AS sent, ROUND(AVG(recv)) AS recv, 
                             ROUND(AVG(fps), 2) AS fps, ROUND(AVG(ping), 2) AS ping        
                FROM ({sql} GROUP BY 1, server_name) x
                GROUP BY 1
            """
        sql += " ORDER BY 1"
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"node": node, "server_name": server_name, "period": period})
                if cursor.rowcount == 0:
                    for i in range(0, 4):
                        self.axes[i].bar([], [])
                        self.axes[i].set_xticks([])
                        self.axes[i].set_yticks([])
                        self.axes[i].text(0, 0, 'No data available.', ha='center', va='center', size=20)
                    return
                else:
                    series = pd.DataFrame.from_dict(await cursor.fetchall())

        series.columns = ['time', 'Users', 'CPU', 'Memory (paged)', 'Memory (RAM)', 'Read', 'Write', 'Sent',
                          'Recv', 'FPS', 'Ping']
        for column in [
            'CPU', 'FPS', 'Ping', 'Read', 'Recv', 'Sent', 'Users', 'Write', 'Memory (RAM)', 'Memory (paged)'
        ]:
            series[column] = series[column].astype(float)
        series.plot(ax=self.axes[0], x='time', y=['CPU'], title='CPU / User', xticks=[], xlabel='')
        self.axes[0].legend(loc='upper left')
        ax2 = self.axes[0].twinx()
        series.plot(ax=ax2, x='time', y=['Users'], xticks=[], xlabel='', color='blue')
        ax2.legend(['Users'], loc='upper right')
        series.plot(ax=self.axes[1], x='time', y=['FPS'], title='FPS / User', xticks=[], xlabel='')
        self.axes[1].legend(loc='upper left')
        ax3 = self.axes[1].twinx()
        series.plot(ax=ax3, x='time', y=['Users'], xticks=[], xlabel='', color='blue')
        ax3.legend(['Users'], loc='upper right')
        series.plot(ax=self.axes[2], x='time', y=['Memory (RAM)', 'Memory (paged)'], title='Memory',
                    xticks=[], xlabel="", ylabel='Memory (MB)', kind='area', stacked=True)
        self.axes[2].legend(loc='upper left')
        series.plot(ax=self.axes[3], x='time', y=['Read', 'Write'], title='Disk', logy=True, xticks=[],
                    xlabel='', ylabel='KB', grid=True)
        self.axes[3].legend(loc='upper left')
        series.plot(ax=self.axes[4], x='time', y=['Sent', 'Recv'], title='Network', logy=True, xlabel='',
                    ylabel='KB/s', grid=True)
        self.axes[4].legend(['Sent', 'Recv'], loc='upper left')
        ax4 = self.axes[4].twinx()
        series.plot(ax=ax4, x='time', y=['Ping'], xlabel='', ylabel='ms', color='yellow')
        ax4.legend(['Ping'], loc='upper right')
