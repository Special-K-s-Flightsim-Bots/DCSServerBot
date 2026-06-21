import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import seaborn as sns
import warnings

from core import const, report, EmbedElement, utils
from plugins.userstats.filter import StatisticsFilter
from psycopg.rows import dict_row

# ignore pandas warnings (log scale et al.)
warnings.filterwarnings("ignore", category=UserWarning)


class ServerUsage(report.EmbedElement):

    async def render(self, server_name: str | None, period: StatisticsFilter):

        where_clause = "AND m.server_name = %(server_name)s" if server_name else ""
        sql = f"""
            SELECT trim(regexp_replace(m.server_name, '{self.bot.filter['server_name']}', '', 'g')) AS server_name, 
                   ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime, 
                   COUNT(DISTINCT s.player_ucid) AS players, 
                   COUNT(DISTINCT p.discord_id) AS members 
            FROM missions m, statistics s, players p 
            WHERE m.id = s.mission_id 
            AND s.player_ucid = p.ucid 
            AND s.hop_off IS NOT NULL
            {where_clause}
            AND {period.filter(self.env.bot)}
            GROUP BY 1 
            ORDER BY 2 DESC
        """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                servers = playtimes = players = members = ''
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    servers += utils.escape_string(row['server_name'])[:30] + '\n'
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

    async def render(self, server_name: str | None, period: StatisticsFilter):

        where_clause = "AND m.server_name = %(server_name)s" if server_name else ""
        sql = f"""
            SELECT m.mission_theatre, 
                   COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600), 0) AS playtime 
            FROM missions m, statistics s
            WHERE m.id = s.mission_id
            {where_clause}
            AND {period.filter(self.env.bot)}
            GROUP BY 1
            ORDER BY 2 DESC
        """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                theatres = playtimes = ''
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    theatres += utils.escape_string(row['mission_theatre'])[:20] + '\n'
                    playtimes += '{:.0f}\n'.format(row['playtime'])

        if len(theatres) > 0:
            self.add_field(name='TOP Theatre' if not server_name else f"TOP Theatres", value=theatres)
            self.add_field(name='Playtime (h)', value=playtimes)
            self.add_field(name='_ _', value='_ _')


class TopMissionPerServer(report.EmbedElement):

    async def render(self, server_name: str | None, period: StatisticsFilter, limit: int):

        where_clause = "AND m.server_name = %(server_name)s" if server_name else ""
        sql = f"""
            SELECT trim(regexp_replace(m.mission_name, '{self.bot.filter['mission_name']}', ' ', 'g')) AS mission_name, 
                   ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime 
            FROM missions m, statistics s 
            WHERE m.id = s.mission_id 
            AND s.hop_off IS NOT NULL
            {where_clause}
            AND {period.filter(self.env.bot)}
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT {limit}
        """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                missions = playtimes = ''
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    missions += row['mission_name'][:20] + '\n'
                    playtimes += '{:.0f}\n'.format(row['playtime'])

        if len(missions) > 0:
            self.add_field(name='TOP Mission' if not server_name else f"TOP {limit} Missions", value=missions)
            self.add_field(name='Playtime (h)', value=playtimes)
            self.add_field(name='_ _', value='_ _')


class TopModulesPerServer(report.EmbedElement):

    async def render(self, server_name: str | None, period: StatisticsFilter, limit: int):

        where_clause = "AND m.server_name = %(server_name)s" if server_name else ""
        sql = f"""
            SELECT s.slot, COUNT(s.slot) AS num_usage, 
                   COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600),0) AS playtime, 
                   COUNT(DISTINCT s.player_ucid) AS players 
            FROM missions m, statistics s 
            WHERE m.id = s.mission_id
            {where_clause}
            AND {period.filter(self.env.bot)}
            GROUP BY s.slot 
            ORDER BY 3 DESC 
            LIMIT {limit}
        """

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

    async def render(self, server_name: str | None, interval: str | None = "1 month"):

        where_clause = "AND m.server_name = %(server_name)s" if server_name else ""
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
                    generate_series(DATE(NOW()) - INTERVAL '{interval}', DATE(NOW()), INTERVAL '1 day')::DATE AS date
            )
            SELECT 
                ds.date AS date, 
                COALESCE(COUNT(DISTINCT s.player_ucid), 0) AS total_players,
                COALESCE(COUNT(DISTINCT 
                    CASE WHEN pj.join_date = ds.date THEN s.player_ucid ELSE NULL END), 0) AS new_players
            FROM 
                date_series ds
                LEFT JOIN (
                    SELECT s.*, m.id AS mission_id
                    FROM statistics s
                    INNER JOIN missions m ON s.mission_id = m.id {where_clause}
                ) s ON ds.date BETWEEN DATE(s.hop_on) AND DATE(s.hop_off)
                LEFT JOIN players_join pj ON s.player_ucid = pj.player_ucid
            GROUP BY ds.date
            ORDER BY ds.date
        """

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
        self.axes.legend(loc='upper right', facecolor='#303030', edgecolor='white', labelcolor='white')

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

    async def render(self, server_name: str | None, interval: str | None = "1 month"):

        where_clause = "AND m.server_name = %(server_name)s" if server_name else ""
        sql = f"""
            WITH date_series AS (
                SELECT 
                    generate_series(DATE(NOW()) - INTERVAL '{interval}', DATE(NOW()), INTERVAL '1 day') AS date
            ),
            first_visit AS (
                SELECT 
                    player_ucid, 
                    MIN(DATE(hop_on)) AS first_date
                FROM statistics s 
                JOIN missions m ON s.mission_id = m.id {where_clause}
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
                JOIN missions m ON s.mission_id = m.id {where_clause}
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

        period = interval.replace("1", "").strip()
        self.axes.set_title(f'Player Retention | past {period}',
                            color='white', fontsize=25)
        if (all(user_count == 0 for user_count in new_users) and
                all(user_count == 0 for user_count in retained_users)):
            self.axes.text(0.5, 0.5, f'No new players joined in the past {period}.', ha='center', va='center',
                           fontsize=15, color='white')
            self.axes.set_xticks([])
            self.axes.set_yticks([])
            return

        df = pd.DataFrame({
            'first_date': pd.to_datetime(first_dates),
            'Retained Users': retained_users,
            'New Users': new_users
        })
        df['first_date'] = df['first_date'].dt.strftime('%a %m-%d')

        bar1 = self.axes.bar(df['first_date'], df['New Users'], label='New Users', color='orange', edgecolor='white')
        bar2 = self.axes.bar(df['first_date'], df['Retained Users'], bottom=df['New Users'], label='Retained Users',
                             color='dodgerblue', edgecolor='white')

        self.axes.set_xlabel('', color='white', fontsize=10)
        self.axes.set_ylabel('Number of Users', color='white', fontsize=10)
        self.axes.tick_params(axis='x', colors='white', rotation=45)
        self.axes.tick_params(axis='y', colors='white')

        self.axes.set_facecolor('#303030')
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)

        self.axes.set_xlim([-0.5, len(df) - 0.5])
        self.axes.set_xticks(range(len(df)))
        self.axes.set_xticklabels(df['first_date'], rotation=45, ha='right')

        # Annotate the bars with counts, inside the bars with a black color
        for bar in bar1:
            height = bar.get_height()
            if height > 0:
                self.axes.annotate(f'{int(height)}',
                                   xy=(bar.get_x() + bar.get_width() / 2, bar.get_y() + height / 2),
                                   ha='center', va='center', color='black', fontsize=10, weight='bold')

        for bar in bar2:
            height = bar.get_height()
            bottom = bar.get_y()
            if height > 0:
                self.axes.annotate(f'{int(height)}',
                                   xy=(bar.get_x() + bar.get_width() / 2, bottom + height / 2),
                                   ha='center', va='center', color='black', fontsize=10, weight='bold')

        handles, labels = self.axes.get_legend_handles_labels()
        self.axes.legend(handles, ['New Users', 'Retained Users'], loc='upper right', fontsize=12)


class UserEngagement(report.GraphElement):

    async def render(self, server_name: str | None, interval: str | None = "1 month"):
        where_clause = "AND m.server_name = %(server_name)s" if server_name else ""
        sql = f"""
            WITH first_seen AS (
                SELECT 
                    s.player_ucid,
                    MIN(s.hop_on) AS first_seen
                FROM 
                    statistics s
                GROUP BY s.player_ucid
            ),
            player_days AS (
                SELECT 
                    s.player_ucid,
                    COUNT(DISTINCT DATE(s.hop_on)) AS days_present,
                    CASE WHEN f.first_seen >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{interval}' THEN true ELSE false END AS is_new
                FROM 
                    statistics s
                    LEFT JOIN missions m ON s.mission_id = m.id
                    LEFT JOIN first_seen f ON s.player_ucid = f.player_ucid
                WHERE s.hop_on >= NOW() - INTERVAL '{interval}'
                {where_clause}
                GROUP BY s.player_ucid, f.first_seen
            ),
            retention AS (
                SELECT
                    days_present,
                    COUNT(player_ucid) FILTER (WHERE is_new) AS new_player_count,
                    COUNT(player_ucid) FILTER (WHERE NOT is_new) AS returning_player_count
                FROM player_days
                GROUP BY days_present
            ),
            date_series AS (
                SELECT generate_series(1, (
                    SELECT DATE_PART('day', (NOW() AT TIME ZONE 'UTC') - ((NOW() AT TIME ZONE 'UTC') - INTERVAL '{interval}'))::INTEGER)
                ) AS date
            )
            SELECT
                ds.date AS days_present,
                COALESCE(r.new_player_count, 0) AS new_player_count,
                COALESCE(r.returning_player_count, 0) AS returning_player_count
            FROM
                date_series ds
                LEFT JOIN retention r ON r.days_present = ds.date
            ORDER BY ds.date
         """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"server_name": server_name})
                data = await cursor.fetchall()

        if not data:
            self.axes.set_title('No Data Available', color='white', fontsize=25)
            self.axes.text(0.5, 0.5, 'No data available for the selected server or interval.', ha='center', va='center',
                           fontsize=15, color='white')
            self.axes.set_xticks([])
            self.axes.set_yticks([])
            return

        days_present = [row['days_present'] for row in data]
        new_player_count = [row['new_player_count'] for row in data]
        returning_player_count = [row['returning_player_count'] for row in data]

        self.axes.set_title(f'Player Engagement | past {interval.replace("1", "").strip()}', color='white', fontsize=25)

        df = pd.DataFrame({
            'days_present': days_present,
            'new_player_count': new_player_count,
            'returning_player_count': returning_player_count
        })

        df['days_present'] = df['days_present'].astype(str)

        bar1 = self.axes.bar(df['days_present'], df['new_player_count'], label='New Players', color='orange',
                             edgecolor='white')
        bar2 = self.axes.bar(df['days_present'], df['returning_player_count'], bottom=df['new_player_count'],
                             label='Returning Players', color='dodgerblue', edgecolor='white')

        self.axes.set_xlabel('Server Days', color='white', fontsize=10)
        self.axes.set_ylabel('Players', color='white', fontsize=10)
        self.axes.set_xlim([-0.5, len(df) - 0.5])
        self.axes.set_xticks(range(len(df)))
        self.axes.set_xticklabels(df['days_present'], ha='right', color='white')
        self.axes.tick_params(axis='x', colors='white')
        self.axes.tick_params(axis='y', colors='white')

        self.axes.set_facecolor('#303030')
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)

        for spine in self.axes.spines.values():
            spine.set_color('white')

        for i in range(len(bar1)):
            bar1_height = bar1[i].get_height()
            bar2_height = bar2[i].get_height()
            total_height = bar1_height + bar2_height

            # Annotate inside the orange bar (new players)
            if bar1_height > 0:
                self.axes.text(bar1[i].get_x() + bar1[i].get_width() / 2, bar1_height / 2,
                               int(bar1_height), ha='center', va='center', color='black',
                               fontsize=10, weight='bold')

            # Annotate on top of the total height (new and returning players)
            if total_height > 0:
                self.axes.text(bar1[i].get_x() + bar1[i].get_width() / 2, total_height,
                               int(total_height), ha='center', va='bottom', color='white',
                               fontsize=10, weight='bold')

        handles, labels = self.axes.get_legend_handles_labels()
        self.axes.legend(handles, ['New Players', 'Returning Players'], loc='upper right', fontsize=12)


class UsersPerDayTime(report.GraphElement):

    async def render(self, server_name: str | None, interval: str | None = '1 month'):

        where_clause = "AND m.server_name = %(server_name)s" if server_name else ""
        sql = f"""
            SELECT to_char(s.hop_on, 'ID') as weekday, to_char(h.time, 'HH24') AS hour, 
                   COUNT(DISTINCT s.player_ucid) AS players 
            FROM statistics s, missions m, generate_series(current_date, current_date + 1, INTERVAL '1 hour') h 
            WHERE date_part('hour', h.time) BETWEEN date_part('hour', s.hop_on) AND date_part('hour', s.hop_off)
            AND s.hop_on > (NOW() at time zone 'UTC') - interval '{interval}' 
            AND s.mission_id = m.id
            {where_clause}
            GROUP BY 1, 2
        """

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

    async def render(self, server_name: str | None, interval: str | None = '1 month'):
        """
        Render a bar plot to visualize the number of average users per hour within a given interval of time.

        Parameters:
            server_name (str | None): The name of the server to filter data by.
            interval (str | None): The time interval for filtering mission times (default is '1 month').

        """
        where_clause = "AND server_name = %(server_name)s" if server_name else ""
        inner_sql = f"""
            SELECT mission_time / 3600 AS time, users 
            FROM serverstats
            WHERE mission_time IS NOT NULL
            AND time > (now() at time zone 'UTC') - interval '{interval}'
            {where_clause}
        """
        sql = f"""
            SELECT time::INTEGER, avg(users)::DECIMAL AS users FROM (
                {inner_sql}
            ) s
            GROUP BY 1 ORDER BY 1
        """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"server_name": server_name})
                query_results = await cursor.fetchall()

        if query_results:
            df = pd.DataFrame.from_records(query_results)
        else:
            # Create an empty DataFrame with "time" and "users" columns if the query returned nothing
            df = pd.DataFrame(columns=['time', 'users'])

        all_hours = pd.DataFrame({'time': np.arange(0, 24)})  # Ensure "time" covers all hours in a day
        df['time'] = df.get('time', pd.Series(dtype=int))  # In case 'time' is missing, create an empty column
        df['users'] = df.get('users', pd.Series(dtype=float))  # Ensure 'users' column exists

        # Merge data with all possible hours (left join) and fill missing user counts with 0
        merged_df = pd.merge(all_hours, df, on='time', how='left')
        merged_df['users'] = merged_df['users'].astype(float).fillna(0)

        # Step 4: Create the bar plot
        barplot = sns.barplot(x='time', y='users', data=merged_df, ax=self.axes, color='dodgerblue')

        # Step 5: Customize plot appearance
        self.axes.set_title(
            f'Average Users per Mission-Time | past {interval.replace("1", "").strip()}',
            color='white',
            fontsize=25
        )
        self.axes.set_xlabel('')
        self.axes.set_ylabel('Average Users', color='white', fontsize=10)
        self.axes.set_xticks(range(24))
        self.axes.set_xticklabels([f"{hour:02d}h" for hour in range(24)], color='white')
        self.axes.tick_params(axis='y', colors='white')

        # Add annotations for the user-count above the bars
        for bar in barplot.patches:
            height = bar.get_height()
            if height > 0:
                self.axes.text(bar.get_x() + bar.get_width() / 2, height,
                               f'{height:.1f}', ha='center', va='bottom',
                               color='white', fontsize=10, weight='bold')

        # Adjust plot aesthetics (spines, background, etc.)
        for spine in self.axes.spines.values():
            spine.set_color('white')
        self.axes.set_facecolor('#303030')
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)


class ServerLoadHeader(EmbedElement):
    async def render(self, node: str, server_name: str | None = None):
        self.env.embed.description = \
            f"Node: {node}" if not server_name else f"Server: {utils.escape_string(server_name)}"


class ServerLoad(report.MultiGraphElement):

    async def render(self, node: str, period: StatisticsFilter, server_name: str | None = None):

        self.env.embed.title = f"Server Load ({period.period.title()})"
        inner_sql = f"""
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
            WHERE {period.filter(self.env.bot)}
            and time < (now() at time zone 'UTC')
        """
        if node:
            inner_sql += "AND node = %(node)s"
        if server_name:
            sql = f"""
                {inner_sql}
                AND server_name = %(server_name)s 
                GROUP BY 1 ORDER BY 1
            """
        else:
            sql = f"""
                SELECT time, SUM(users) AS users, SUM(cpu) AS cpu, SUM(mem_paged) AS mem_paged, SUM(mem_ram) AS mem_ram, 
                             SUM(read) AS read, SUM(write) AS write, ROUND(AVG(sent)) AS sent, ROUND(AVG(recv)) AS recv, 
                             ROUND(AVG(fps), 2) AS fps, ROUND(AVG(ping), 2) AS ping        
                FROM (
                    {inner_sql} 
                    GROUP BY 1, server_name
                ) x
                GROUP BY 1 ORDER BY 1
            """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"node": node, "server_name": server_name})
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
        series.plot(ax=ax4, x='time', y=['Ping'], xlabel='', ylabel='ms', color='red')
        ax4.legend(['Ping'], loc='upper right')

        # find the right time span the data covers
        series['time'] = pd.to_datetime(series['time'])
        time_start = series['time'].min()
        time_end = series['time'].max()
        time_span = time_end - time_start


        if time_span <= pd.Timedelta(hours=1):
            _period = "Hour"
        elif time_span <= pd.Timedelta(days=1):
            _period = "Day"
        elif time_span <= pd.Timedelta(weeks=1):
            _period = "Week"
        elif time_span <= pd.Timedelta(days=31):
            _period = "Month"
        else:
            _period = "Month"

        settings = {
            "Hour": {
                "major_locator": mdates.MinuteLocator(),
                "formatter": mdates.DateFormatter('%H:%M')
            },
            "Day": {
                "major_locator": mdates.HourLocator(),
                "minor_locator": mdates.MinuteLocator(byminute=range(0,60,15)),
                "formatter": mdates.DateFormatter('%H:%M')
            },
            "Week": {
                "major_locator": mdates.DayLocator(),
                "minor_locator": mdates.HourLocator(byhour=range(0,24,4)),
                "formatter": mdates.DateFormatter('%Y-%m-%d')
            },
            "Month": {
                "major_locator": mdates.DayLocator(),
                "formatter": mdates.DateFormatter('%Y-%m-%d')
            }
        }
        for ax in [self.axes[x] for x in range(0, 4)] + [ax2, ax3, ax4]:
            ax.xaxis.set_major_locator(settings[_period]['major_locator'])
            if 'minor_locator' in settings[_period]:
                ax.xaxis.set_minor_locator(settings[_period]['minor_locator'])
                ax.tick_params(axis='x', which='minor', length=6)
            ax.xaxis.set_major_formatter(settings[_period]['formatter'])
            ax.tick_params(axis='x', which='major', rotation=30)


class DDoSStats(EmbedElement):

    async def render(self, server_name: str | None, period: StatisticsFilter):
        where_clause = "AND server_name = %(server_name)s" if server_name else ""
        sql = f"""
            SELECT time, port, protocol, connections, unique_ips, players,
                   non_player_udp_ips, under_attack
            FROM port_traffic
            WHERE {period.filter(self.env.bot)}
            {where_clause}
            ORDER BY time DESC
            LIMIT 500
        """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"server_name": server_name})
                rows = await cursor.fetchall()

        if not rows:
            self.add_field(name='No port traffic data available.',
                           value='Enable ddos_detect in config/services/monitoring.yaml to start collecting.')
            return

        # Current state: latest row per (port, protocol)
        latest: dict[tuple[int, str], dict] = {}
        for row in rows:
            key = (row['port'], row['protocol'])
            if key not in latest:
                latest[key] = row

        # Build status lines
        status_lines = []
        for (port, proto), row in sorted(latest.items()):
            state = '🔴 UNDER ATTACK' if row['under_attack'] else '🟢 Normal'
            if proto == 'udp':
                # UDP: show non-player source IPs (from scapy sniff), unique_ips is always 0
                line = (
                    f"**{proto.upper()}/{port}** — {state}\n"
                    f"├ Connections: {row['connections']} │ Players: {row['players']} │ "
                    f"Non-player UDP: {row['non_player_udp_ips']}"
                )
            else:
                # TCP: show unique remote IPs
                line = (
                    f"**{proto.upper()}/{port}** — {state}\n"
                    f"├ Connections: {row['connections']} │ Players: {row['players']} │ "
                    f"Unique IPs: {row['unique_ips']}"
                )
            status_lines.append(line)

        self.add_field(name='Current DDoS State', value='\n'.join(status_lines) or 'No data', inline=False)

        # Attack summary: count attack rows vs. normal rows in a period
        attack_rows = [r for r in rows if r['under_attack']]
        normal_rows = [r for r in rows if not r['under_attack']]

        if attack_rows:
            attack_times = sorted(set(r['time'] for r in attack_rows))
            first_attack = min(attack_times)
            last_attack = max(attack_times)
            peak = max(attack_rows, key=lambda r: r['connections'])

            summary = (
                f"**Attack Periods:** {len(attack_times)} tick(s) with attack flag\n"
                f"├ First: {first_attack.strftime('%Y-%m-%d %H:%M')}\n"
                f"├ Last: {last_attack.strftime('%Y-%m-%d %H:%M')}\n"
                f"├ Peak connections: {peak['connections']} "
                f"({peak['protocol'].upper()}/{peak['port']})\n"
                f"└ Total rows: {len(normal_rows)} normal, {len(attack_rows)} attack"
            )
            self.add_field(name='Attack Summary', value=summary, inline=False)
        else:
            self.add_field(name='Attack Summary',
                           value=f'✅ No attacks detected in the selected period.\n'
                                 f'({len(normal_rows)} data points collected)',
                           inline=False)

        # Connection stats per port/protocol (avg over non-attack rows)
        non_attack = [r for r in rows if not r['under_attack']]
        if non_attack:
            port_agg: dict[tuple[int, str], dict] = {}
            for r in non_attack:
                key = (r['port'], r['protocol'])
                if key not in port_agg:
                    port_agg[key] = {'connections': [], 'unique_ips': [], 'non_player_udp_ips': []}
                port_agg[key]['connections'].append(r['connections'])
                port_agg[key]['unique_ips'].append(r['unique_ips'])
                port_agg[key]['non_player_udp_ips'].append(r['non_player_udp_ips'])

            baseline_lines = []
            for (port, proto), data in sorted(port_agg.items()):
                avg_conn = sum(data['connections']) / len(data['connections'])
                if proto == 'udp':
                    avg_udp = sum(data['non_player_udp_ips']) / len(data['non_player_udp_ips'])
                    baseline_lines.append(
                        f"**{proto.upper()}/{port}** — avg {avg_conn:.1f} conns, {avg_udp:.1f} non-player UDP"
                    )
                else:
                    avg_ips = sum(data['unique_ips']) / len(data['unique_ips'])
                    baseline_lines.append(
                        f"**{proto.upper()}/{port}** — avg {avg_conn:.1f} conns, {avg_ips:.1f} unique IPs"
                    )
            self.add_field(name='Baseline (non-attack)',
                           value='\n'.join(baseline_lines) or 'No baseline data',
                           inline=False)
            await report.Ruler(self.env).render(ruler_length=25)


class DDoSGraph(report.MultiGraphElement):

    async def render(self, server_name: str | None, period: StatisticsFilter):
        where_clause = "AND server_name = %(server_name)s" if server_name else ""
        sql = f"""
            SELECT time, port, protocol, connections, unique_ips, players,
                   non_player_udp_ips, under_attack
            FROM port_traffic
            WHERE {period.filter(self.env.bot)}
            {where_clause}
            ORDER BY time ASC
            LIMIT 500
        """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {"server_name": server_name})
                rows = await cursor.fetchall()

        if not rows:
            self.env.figure = None
            return

        # --- Axis 0: TCP connections ---
        tcp_rows = [r for r in rows if r['protocol'] == 'tcp']
        if tcp_rows:
            df_tcp = pd.DataFrame(tcp_rows)
            df_tcp['time'] = pd.to_datetime(df_tcp['time'])

            self.axes[0].set_title('TCP Connections', color='white', fontsize=14)
            self.axes[0].set_ylabel('Connections', color='white', fontsize=10)
            self.axes[0].tick_params(axis='x', colors='white', rotation=30)
            self.axes[0].tick_params(axis='y', colors='white')
            self.axes[0].set_facecolor('#303030')
            for spine in self.axes[0].spines.values():
                spine.set_color('white')
            self.axes[0].spines['top'].set_visible(False)
            self.axes[0].spines['right'].set_visible(False)

            # Plot normal points
            normal_tcp = df_tcp[~df_tcp['under_attack']]
            attack_tcp = df_tcp[df_tcp['under_attack']]

            if not normal_tcp.empty:
                sns.lineplot(data=normal_tcp, x='time', y='connections',
                             ax=self.axes[0], color='dodgerblue', label='Normal')
            if not attack_tcp.empty:
                sns.scatterplot(data=attack_tcp, x='time', y='connections',
                                ax=self.axes[0], color='red', s=60, zorder=5,
                                label='Under Attack')

            # Highlight attack background regions
            attack_times = sorted(attack_tcp['time'].unique()) if not attack_tcp.empty else []
            if attack_times:
                # Group consecutive attack times into regions
                regions = []
                region_start = attack_times[0]
                prev = attack_times[0]
                for t in attack_times[1:]:
                    if (t - prev).total_seconds() > 120:  # gap > 2 min = new region
                        regions.append((region_start, prev))
                        region_start = t
                    prev = t
                regions.append((region_start, prev))
                for start, end in regions:
                    self.axes[0].axvspan(start, end, alpha=0.15, color='red', zorder=0)

            self.axes[0].legend(loc='upper left', fontsize=9)
        else:
            self.axes[0].text(0.5, 0.5, 'No TCP data.', ha='center', va='center',
                              fontsize=15, color='white')
            self.axes[0].set_xticks([])
            self.axes[0].set_yticks([])

        # --- Axis 1: UDP non-player source IPs ---
        udp_rows = [r for r in rows if r['protocol'] == 'udp']
        if udp_rows:
            df_udp = pd.DataFrame(udp_rows)
            df_udp['time'] = pd.to_datetime(df_udp['time'])

            self.axes[1].set_title('UDP Non-Player Sources', color='white', fontsize=14)
            self.axes[1].set_ylabel('Unique IPs', color='white', fontsize=10)
            self.axes[1].tick_params(axis='x', colors='white', rotation=30)
            self.axes[1].tick_params(axis='y', colors='white')
            self.axes[1].set_facecolor('#303030')
            for spine in self.axes[1].spines.values():
                spine.set_color('white')
            self.axes[1].spines['top'].set_visible(False)
            self.axes[1].spines['right'].set_visible(False)

            normal_udp = df_udp[~df_udp['under_attack']]
            attack_udp = df_udp[df_udp['under_attack']]

            if not normal_udp.empty:
                sns.lineplot(data=normal_udp, x='time', y='non_player_udp_ips',
                             ax=self.axes[1], color='orange', label='Normal')
            if not attack_udp.empty:
                sns.scatterplot(data=attack_udp, x='time', y='non_player_udp_ips',
                                ax=self.axes[1], color='red', s=60, zorder=5,
                                label='Under Attack')

            # Highlight attack background regions
            attack_times_udp = sorted(attack_udp['time'].unique()) if not attack_udp.empty else []
            if attack_times_udp:
                regions = []
                region_start = attack_times_udp[0]
                prev = attack_times_udp[0]
                for t in attack_times_udp[1:]:
                    if (t - prev).total_seconds() > 120:
                        regions.append((region_start, prev))
                        region_start = t
                    prev = t
                regions.append((region_start, prev))
                for start, end in regions:
                    self.axes[1].axvspan(start, end, alpha=0.15, color='red', zorder=0)

            self.axes[1].legend(loc='upper left', fontsize=9)
        else:
            self.axes[1].text(0.5, 0.5, 'No UDP data.', ha='center', va='center',
                              fontsize=15, color='white')
            self.axes[1].set_xticks([])
            self.axes[1].set_yticks([])
