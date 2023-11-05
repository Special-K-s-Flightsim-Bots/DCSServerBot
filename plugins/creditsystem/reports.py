import discord

from contextlib import closing
from core import report
from psycopg.rows import dict_row
from typing import Optional


class HighscoreTrueSkill(report.GraphElement):

    def render(self, interaction: discord.Interaction, limit: int, display_values: Optional[bool] = False):
        sql = f"""
            SELECT DISTINCT discord_id, COALESCE(name, 'Unknown') AS name, skill_mu::DECIMAL AS value
            FROM players 
            WHERE skill_mu IS NOT NULL
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
                if display_values:
                    for i in range(len(labels)):
                        self.axes.text(values[i], i, f"{values[i]:.2f}", ha='right', va='center', color='black')
                self.axes.set_title("PvP Highscore", color='white', fontsize=25)
                self.axes.set_xlabel("TrueSkill™️")
                if len(values) == 0:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
