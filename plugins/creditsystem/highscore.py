import discord
import numpy as np

from core import report
from matplotlib import cm
from psycopg.rows import dict_row

from ..userstats.highscore import compute_font_size


class HighscoreCredits(report.GraphElement):

    async def render(self, interaction: discord.Interaction, limit: int, bar_labels: bool | None = True):
        sql = f"""
            SELECT DISTINCT p.discord_id, COALESCE(name, 'Unknown') AS name, c.points AS value
            FROM players p, credits c
            WHERE p.ucid = c.player_ucid
            ORDER BY 3 DESC 
            LIMIT {limit}
        """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                labels = []
                values = []
                await cursor.execute(sql)
                async for row in cursor:
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, float(row['value']))

        self.axes.set_title("Credits", color='white', fontsize=25)
        self.axes.set_xlabel("Credits")

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
