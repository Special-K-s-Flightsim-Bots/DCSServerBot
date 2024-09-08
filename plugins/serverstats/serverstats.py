import numpy as np
import pandas as pd
import seaborn as sns
import warnings

from core import const, report, EmbedElement, utils
import matplotlib.dates as mdates
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
                    servers += row['server_name'][:30] + '\n'
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


class TopTheatresPerServer(report.EmbedElement):

    async def render(self, server_name: Optional[str], period: StatisticsFilter):
        sql = f"""
            SELECT trim(regexp_replace(m.server_name, '{self.bot.filter['server_name']}', '', 'g')) AS server_name,
                   m.mission_theatre, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime 
            FROM missions m, statistics s
            WHERE m.id = s.mission_id
        """
        if server_name:
            sql += " AND m.server_name = %(server_name)s"
        sql += " AND " + period.filter(self.env.bot)
        sql += " GROUP BY 1, 2 ORDER BY 3 DESC"

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                servers = theatres = playtimes = ''
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    servers += row['server_name'][:30] + '\n'
                    theatres += row['mission_theatre'][:20] + '\n'
                    playtimes += '{:.0f}\n'.format(row['playtime'])

        if len(servers) > 0:
            if not server_name:
                self.add_field(name='Server', value=servers)
            self.add_field(name='TOP Theatre' if not server_name else f"TOP Theatres", value=theatres)
            self.add_field(name='Playtime (h)', value=playtimes)
            if server_name:
                self.add_field(name='_ _', value='_ _')


class TopMissionPerServer(report.EmbedElement):

    async def render(self, server_name: Optional[str], period: StatisticsFilter, limit: int):
        sql_left = """
            SELECT server_name, mission_name, playtime 
            FROM (
                SELECT server_name, mission_name, playtime, 
                       ROW_NUMBER() OVER(PARTITION BY server_name ORDER BY playtime DESC) AS rn 
                FROM (
        """
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
                    servers += row['server_name'][:30] + '\n'
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


class UniqueUsers(report.GraphElement):

    async def render(self, server_name: Optional[str], interval: Optional[str] = "1 month"):
        sql = f"""
            WITH players_join AS (
                SELECT 
                    player_ucid,
                    MIN(DATE(hop_on)) AS join_date
                FROM statistics
                GROUP BY player_ucid
            ),
            date_series AS (
                SELECT 
                    generate_series(DATE(NOW()) - INTERVAL '{interval}', DATE(NOW()), INTERVAL '1 day') AS date
            )
            SELECT 
                ds.date AS date, 
                COALESCE(COUNT(DISTINCT s.player_ucid), 0) AS total_players,
                COALESCE(COUNT(DISTINCT 
                    CASE WHEN pj.join_date = ds.date THEN s.player_ucid ELSE NULL END), 0) AS new_players
            FROM 
                date_series ds
                LEFT JOIN statistics s ON ds.date BETWEEN DATE(s.hop_on) AND DATE(s.hop_off)
                LEFT JOIN missions m ON s.mission_id = m.id
                LEFT JOIN players_join pj ON s.player_ucid = pj.player_ucid AND pj.join_date = ds.date
        """
        if server_name:
            sql += " WHERE m.server_name = %(server_name)s"
        sql += " GROUP BY ds.date ORDER BY ds.date"

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"server_name": server_name})
                data = await cursor.fetchall()

        dates = [row['date'] for row in data]
        total_players = [row['total_players'] for row in data]
        new_players = [row['new_players'] for row in data]

        self.axes.set_title('Unique Players | past {}'.format(interval.replace('1', '').strip()),
                            color='white', fontsize=25)
        if not dates:
            self.axes.text(0.5, 0.5, 'No data available.', ha='center', va='center', fontsize=15, color='white')
            self.axes.set_xticks([])
            self.axes.set_yticks([])
            return

        df = pd.DataFrame({
            'date': dates,
            'total_players': total_players,
            'new_players': new_players
        })
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%a %m-%d')

        total_bars = sns.barplot(x='date', y='total_players', data=df, ax=self.axes, color='dodgerblue',
                                 label='Total Players', edgecolor='white')
        new_bars = sns.barplot(x='date', y='new_players', data=df, ax=self.axes, color='orange',
                               label='New Players', edgecolor='white')

        self.axes.set_xlabel('')
        self.axes.set_ylabel('Players', color='white', fontsize=10)
        self.axes.set_xticklabels(df['date'], rotation=45, ha='right', color='white')
        self.axes.tick_params(axis='x', colors='white')
        self.axes.tick_params(axis='y', colors='white')
        self.axes.legend()

        # Add annotations for total players above the bars
        for bar in total_bars.patches[:len(dates)]:
            height = bar.get_height()
            if height > 0:
                self.axes.text(bar.get_x() + bar.get_width() / 2, height,
                               int(height), ha='center', va='bottom', color='white',
                               fontsize=10, weight='bold')

        # Add annotations for new players inside the bars
        for bar in new_bars.patches[-len(dates):]:
            height = bar.get_height()
            if height > 0:
                self.axes.text(bar.get_x() + bar.get_width() / 2, height / 2,
                               int(height), ha='center', va='center', color='black',
                               fontsize=10, weight='bold')

        for spine in self.axes.spines.values():
            spine.set_color('white')

        self.axes.set_facecolor('#303030')
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)


class UserRetention(report.GraphElement):

    async def render(self, server_name: Optional[str], interval: Optional[str] = "1 month"):

        # Extended SQL with date series to handle missing dates
        sql = f"""
            WITH RECURSIVE date_series AS (
                SELECT DATE(NOW()) - INTERVAL '{interval}' + INTERVAL '1 day' AS date
                UNION ALL
                SELECT date + INTERVAL '1 day'
                FROM date_series
                WHERE date + INTERVAL '1 day' <= DATE(NOW())
            ),
            first_visit AS (
                SELECT 
                    player_ucid, 
                    MIN(DATE(hop_on)) AS first_date
                FROM statistics
                GROUP BY player_ucid
                HAVING MIN(DATE(hop_on)) >= DATE(NOW()) - INTERVAL '{interval}'
            ),
            activity AS (
                SELECT 
                    fv.player_ucid, 
                    fv.first_date, 
                    DATE(hop_on) AS activity_date
                FROM first_visit fv
                JOIN statistics s ON fv.player_ucid = s.player_ucid
            )
            SELECT 
                ds.date AS first_date, 
                COALESCE(COUNT(DISTINCT fv.player_ucid), 0) AS new_users,
                COALESCE(COUNT(DISTINCT CASE WHEN a.activity_date > fv.first_date THEN a.player_ucid ELSE NULL END), 0) AS retained_users
            FROM date_series ds
            LEFT JOIN first_visit fv ON ds.date = fv.first_date
            LEFT JOIN activity a ON fv.player_ucid = a.player_ucid
            GROUP BY ds.date
            ORDER BY ds.date
        """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"server_name": server_name})
                data = await cursor.fetchall()

        first_dates = [row['first_date'] for row in data]
        retained_users = [row['retained_users'] for row in data]
        new_users = [row['new_users'] for row in data]

        self.axes.set_title(f'User Retention | past {interval.replace("1", "").strip()}',
                            color='white', fontsize=25)
        if (all(user_count == 0 for user_count in new_users) and
                all(user_count == 0 for user_count in retained_users)):
            self.axes.text(0.5, 0.5, 'No data available.', ha='center', va='center', fontsize=15, color='white')
            self.axes.set_xticks([])
            self.axes.set_yticks([])
            return

        df = pd.DataFrame({
            'first_date': pd.to_datetime(first_dates),
            'Retained Users': retained_users,
            'New Users': new_users
        })
        df['first_date'] = df['first_date'].dt.strftime('%a %m-%d')
        df_melted = df.melt(id_vars=['first_date'], value_vars=['Retained Users', 'New Users'],
                            var_name='User Type', value_name='Count')

        # Plot using seaborn
        sns.set(style="whitegrid")
        barplot = sns.barplot(x='first_date', y='Count', hue='User Type', data=df_melted, ax=self.axes,
                              palette=['dodgerblue', 'orange'])

        self.axes.set_xlabel('First Visit Date', color='white', fontsize=10)
        self.axes.set_ylabel('Number of Users', color='white', fontsize=10)
        self.axes.tick_params(axis='x', colors='white', rotation=45)
        self.axes.tick_params(axis='y', colors='white')

        # Annotate numbers on top of each bar
        for p in barplot.patches:
            height = p.get_height()
            if height > 0:
                barplot.annotate(f'{height}', (p.get_x() + p.get_width() / 2., height),
                                 ha='center', va='bottom', color='white', fontsize=10, weight='bold')

        for spine in self.axes.spines.values():
            spine.set_color('white')

        self.axes.set_facecolor('#303030')
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)
        handles, labels = self.axes.get_legend_handles_labels()
        self.axes.legend(handles, ['Retained Users', 'New Users'], loc='upper right', fontsize=12)


class UsersPerDayTime(report.GraphElement):

    async def render(self, server_name: Optional[str], interval: Optional[str] = '1 month'):
        sql = f"""
            SELECT to_char(s.hop_on, 'ID') as weekday, to_char(h.time, 'HH24') AS hour, 
                   COUNT(DISTINCT s.player_ucid) AS players 
            FROM statistics s, missions m, generate_series(current_date, current_date + 1, INTERVAL '1 hour') h 
            WHERE date_part('hour', h.time) BETWEEN date_part('hour', s.hop_on) AND date_part('hour', s.hop_off)
            AND s.hop_on > (NOW() at time zone 'UTC') - interval '{interval}' 
            AND s.mission_id = m.id
        """
        if server_name:
            sql += " AND m.server_name = %(server_name)s"
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
            self.axes.set_title('Users per Day/Time (UTC) | past {}'.format(interval.replace('1', '').strip()),
                                                                            color='white', fontsize=25)


class UsersPerMissionTime(report.GraphElement):

    async def render(self, server_name: Optional[str], interval: Optional[str] = '1 month'):

        inner_sql = f"""
            SELECT mission_time / 3600 AS time, users 
            FROM serverstats
            WHERE mission_time IS NOT NULL
            AND time > (now() at time zone 'UTC') - interval '{interval}'
        """
        if server_name:
            inner_sql += "AND server_name = %(server_name)s"
        sql = f"""
            SELECT time::INTEGER, avg(users)::DECIMAL AS users FROM (
                {inner_sql}
            ) s
            GROUP BY 1 ORDER BY 1
        """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"server_name": server_name})
                df = pd.DataFrame.from_dict(await cursor.fetchall())
        all_hours = pd.DataFrame({'time': np.arange(0, 24)})
        df = pd.merge(all_hours, df, on='time', how='left')
        df['users'] = df['users'].fillna(0)

        barplot = sns.barplot(x='time', y='users', data=df, ax=self.axes, color='dodgerblue')

        self.axes.set_title('Users per Mission-Time | past {}'.format(interval.replace('1', '').strip()),
                            color='white', fontsize=25)
        self.axes.set_xlabel('')
        self.axes.set_ylabel('Average Users', color='white', fontsize=10)

        self.axes.set_xticks(range(24))
        time_labels = [f"{hour:02d}h" for hour in range(24)]
        self.axes.set_xticklabels(time_labels, color='white')
        self.axes.tick_params(axis='y', colors='white')

        # Add annotations for user count above the bars
        for bar in barplot.patches:
            height = bar.get_height()
            if height > 0:
                self.axes.text(bar.get_x() + bar.get_width() / 2, height,
                               f'{height:.1f}', ha='center', va='bottom', color='white',
                               fontsize=10, weight='bold')

        for spine in self.axes.spines.values():
            spine.set_color('white')
        self.axes.set_facecolor('#303030')
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)


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

        # plot CPU and Users
        series.plot(ax=self.axes[0], x='time', y=['Users'], title='CPU / User', xticks=[], xlabel='', color='blue')
        ax2 = self.axes[0].twinx()
        series.plot(ax=ax2, x='time', y=['CPU'], xticks=[], xlabel='', color='yellow')

        # plot FPS and Users
        series.plot(ax=self.axes[1], x='time', y=['Users'], title='FPS / User', xticks=[], xlabel='', color='blue')
        ax3 = self.axes[1].twinx()
        series.plot(ax=ax3, x='time', y=['FPS'], xticks=[], xlabel='', color='lightgreen')

        users_avg = series['Users'].mean()
        cpu_avg = series['CPU'].mean()
        fps_avg = series['FPS'].mean()

        labels_ax0 = ['Users']
        values_ax0 = [users_avg]

        labels_ax2 = ['CPU']
        values_ax2 = [cpu_avg]

        labels_ax3 = ['FPS']
        values_ax3 = [fps_avg]

        leg = self.axes[0].legend(labels_ax0, loc='upper left')
        for line, text in zip(leg.get_lines(), leg.get_texts()):
            text.set_color("white")
            label = text.get_text()
            avg = values_ax0[labels_ax0.index(label)]
            text.set_text(f'{label}\n(Avg: {avg:.2f})')

        leg_ax2 = ax2.legend(labels_ax2, loc='upper right')
        for line, text in zip(leg_ax2.get_lines(), leg_ax2.get_texts()):
            text.set_color("white")
            label = text.get_text()
            avg = values_ax2[labels_ax2.index(label)]
            text.set_text(f'{label}\n(Avg: {avg:.2f}%)')

        leg_ax1 = self.axes[1].legend(labels_ax0, loc='upper left')
        for line, text in zip(leg_ax1.get_lines(), leg_ax1.get_texts()):
            text.set_color("white")
            label = text.get_text()
            avg = values_ax0[labels_ax0.index(label)]
            text.set_text(f'{label}\n(Avg: {avg:.2f})')

        leg_ax3 = ax3.legend(labels_ax3, loc='upper right')
        for line, text in zip(leg_ax3.get_lines(), leg_ax3.get_texts()):
            text.set_color("white")
            label = text.get_text()
            avg = values_ax3[labels_ax3.index(label)]
            text.set_text(f'{label}\n(Avg: {avg:.2f})')

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

        settings = {
            "Hour": {
                "major_locator": mdates.MinuteLocator(),
                "formatter": mdates.DateFormatter('%H:%M')
            },
            "Day": {
                "major_locator": mdates.HourLocator(),
                "minor_locator": mdates.MinuteLocator(byminute=[0, 30]),
                "formatter": mdates.DateFormatter('%H:%M')
            },
            "Week": {
                "major_locator": mdates.DayLocator(),
                "minor_locator": mdates.HourLocator(byhour=[0, 6, 12, 18]),
                "formatter": mdates.DateFormatter('%Y-%m-%d')
            },
            "Month": {
                "major_locator": mdates.DayLocator(),
                "formatter": mdates.DateFormatter('%Y-%m-%d')
            }
        }
        for ax in [self.axes[x] for x in range(0, 4)] + [ax2, ax3, ax4]:
            ax.xaxis.set_major_locator(settings[period]['major_locator'])
            if 'minor_locator' in settings[period]:
                ax.xaxis.set_minor_locator(settings[period]['minor_locator'])
                ax.tick_params(axis='x', which='minor', length=6)
            ax.xaxis.set_major_formatter(settings[period]['formatter'])
            ax.tick_params(axis='x', which='major', rotation=30)
