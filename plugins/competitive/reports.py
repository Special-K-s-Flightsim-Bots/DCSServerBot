import discord
import numpy as np

from core import report
from datetime import datetime, timezone
from matplotlib import cm
from psycopg.rows import dict_row
from typing import Optional

from ..userstats.filter import StatisticsFilter, CampaignFilter
from ..userstats.highscore import compute_font_size


class HighscoreTrueSkill(report.GraphElement):

    async def render(self, interaction: discord.Interaction, limit: int, flt: StatisticsFilter,
                     bar_labels: Optional[bool] = True):
        if isinstance(flt, CampaignFilter):
            if 'campaign:' in flt.period:
                campaign = flt.period.split(':')[1]
            else:
                campaign = flt.period
            inner_sql = """
                JOIN squadron_members sm ON sm.player_ucid = t.player_ucid
                JOIN tm_matches tm ON sm.squadron_id = tm.squadron_blue OR sm.squadron_id = tm.squadron_red
                JOIN tm_tournaments tt ON tm.tournament_id = tt.tournament_id
                JOIN campaigns c ON tt.campaign = c.name
                WHERE c.name = %(campaign)s
            """
        else:
            inner_sql = ""
            campaign = None

        sql = f"""
            SELECT DISTINCT p.discord_id, COALESCE(p.name, 'Unknown') AS name, t.skill_mu - 3 * t.skill_sigma AS value
            FROM players p 
            JOIN trueskill t ON p.ucid = t.player_ucid
            {inner_sql}
            ORDER BY 3 DESC 
            LIMIT {limit}
        """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                labels = []
                values = []
                await cursor.execute(sql, {"campaign": campaign})
                async for row in cursor:
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, float(row['value']))

                self.axes.set_title("PvP Highscore", color='white', fontsize=25)
                self.axes.set_xlabel("TrueSkill™️")

                num_bars = len(labels)
                if num_bars > 0:
                    fontsize = compute_font_size(num_bars)
                    bar_height = max(0.75, 3 / num_bars)

                    color_map = cm.get_cmap('viridis', num_bars)
                    colors = color_map(np.linspace(0, 1, num_bars))

                    self.axes.barh(labels, values, color=colors, label="TrueSkill", height=bar_height)
                    if bar_labels:
                        for c in self.axes.containers:
                            self.axes.bar_label(c, fmt='%.1f', label_type='edge', padding=2, fontsize=fontsize)
                        self.axes.margins(x=0.1)
                    self.axes.tick_params(axis='y', labelsize=fontsize)
                else:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)


class MatchLog(report.EmbedElement):

    async def render(self, match: dict):
        times = []
        logs = []
        for time, log in match['log'].items():
            times.append(datetime.fromisoformat(time).replace(tzinfo=timezone.utc))
            logs.append(log)
        self.add_field(name="Time", value="\n".join([f"<t:{int(t.timestamp())}:T>" for t in times]))
        self.add_field(name="Log", value="\n".join(logs))
