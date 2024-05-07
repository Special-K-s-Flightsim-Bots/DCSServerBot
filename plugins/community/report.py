import pandas as pd

from core import report
from datetime import datetime, timezone
from psycopg.rows import dict_row


class CommunityStats(report.EmbedElement):

    async def render(self, bots: pd.DataFrame, plugins: pd.DataFrame, all_servers: list[dict]):
        groups = len(bots)
        num_nodes = bots['num_bots'].sum()
        num_servers = bots['num_servers'].sum()
        self.add_field(name="Groups", value=str(groups))
        self.add_field(name="Nodes", value=str(num_nodes))
        self.add_field(name="Servers", value=f"{num_servers} [of {len(all_servers)}]")


class BotVersions(report.GraphElement):
    async def render(self, bots: pd.DataFrame, plugins: pd.DataFrame):
        bots.groupby('bot_version').count()['guild_id'].plot.pie(ax=self.axes, title="DCSSB Versions", ylabel='',
                                                                 colormap="winter")


class DCSVersions(report.GraphElement):
    async def render(self, bots: pd.DataFrame, plugins: pd.DataFrame):
        bots.groupby('dcs_version').count()['guild_id'].plot.pie(ax=self.axes, title="DCS Versions", ylabel='',
                                                                 colormap="winter")


class PythonVersions(report.GraphElement):
    async def render(self, bots: pd.DataFrame, plugins: pd.DataFrame):
        bots.groupby('python_version').count()['guild_id'].plot.pie(ax=self.axes, title="Python Versions", ylabel='',
                                                                    colormap="winter")


class BotVariants(report.GraphElement):
    async def render(self, bots: pd.DataFrame, plugins: pd.DataFrame):
        bots.groupby('variant').count()['guild_id'].plot.pie(ax=self.axes, title="DCSSB Variants", ylabel='',
                                                             colormap="winter")


class Plugins(report.GraphElement):
    async def render(self, bots: pd.DataFrame, plugins: pd.DataFrame):
        df = plugins.groupby('plugin_name').count()
        df.plot.bar(ax=self.axes, y=['plugin_version'], title="Plugin Version", xlabel='', ylabel='', legend=False,
                    colormap="winter")
        self.axes.set_xticklabels(df.index, rotation=45, ha='right')
        for container in self.axes.containers:
            self.axes.bar_label(container, label_type='center', rotation=90)


class Top10Servers(report.EmbedElement):
    async def render(self):
        async with self.apool.connection() as conn:
            numbers = names = players = ""
            cursor = await conn.execute("""
                SELECT server_name, num_players FROM all_servers WHERE time = (
                    SELECT max(time) FROM all_servers
                ) ORDER BY 2 DESC LIMIT 10
            """)
            rows = await cursor.fetchall()
            for idx, row in enumerate(rows):
                numbers += str(idx + 1) + ".\n"
                names += row[0][:40] + "\n"
                players += str(row[1]) + "\n"
            self.embed.add_field(name="Top", value=numbers)
            self.embed.add_field(name="Server Name", value=names)
            self.embed.add_field(name="Players", value=players)
        self.embed.set_footer(text=f'\n\nLast updated: {datetime.now(timezone.utc):%y-%m-%d %H:%M:%S}')


class AllServers(report.GraphElement):
    async def render(self):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT time, count(*) AS "Servers", sum(num_players)-count(*) AS "Players" FROM all_servers 
                    WHERE time > (timezone('utc', now()) - interval '14 days') GROUP BY 1 ORDER BY 1 DESC
                """)
                if cursor.rowcount > 0:
                    series = pd.DataFrame.from_dict(await cursor.fetchall())
                    series.plot(ax=self.axes, x='time', y=['Servers'], title='All Servers', xlabel='', color='yellow')
                    self.axes.legend(loc='upper left')
                    ax2 = self.axes.twinx()
                    series.plot(ax=ax2, x='time', y=['Players'], color='lightblue')
                    ax2.legend(['Players'], loc='upper right')
                else:
                    self.axes.bar([], [])
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', size=20)
