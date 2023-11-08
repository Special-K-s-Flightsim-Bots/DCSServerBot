import discord

from contextlib import closing
from core import report
from psycopg.rows import dict_row
from typing import Optional


class HighscoreTrueSkill(report.GraphElement):

    def render(self, interaction: discord.Interaction, limit: int, bar_labels: Optional[bool] = True):
        sql = f"""
            SELECT DISTINCT p.discord_id, COALESCE(name, 'Unknown') AS name, t.skill_mu::DECIMAL AS value
            FROM players p, trueskill t
            WHERE p.ucid = t.player_ucid AND skill_mu IS NOT NULL
            ORDER BY 3 DESC LIMIT {limit}
        """

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                labels = []
                values = []
                for row in cursor.execute(sql).fetchall():
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, float(row['value']))
                self.axes.barh(labels, values, color=['#CD7F32', 'silver', 'gold'], label="TrueSkill", height=0.75)
                if bar_labels:
                    for c in self.axes.containers:
                        self.axes.bar_label(c, fmt='%.1f', label_type='edge', padding=2)
                    self.axes.margins(x=0.1)
                self.axes.set_title("PvP Highscore", color='white', fontsize=25)
                self.axes.set_xlabel("TrueSkill™️")
                if len(values) == 0:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
