import discord
import math
from contextlib import closing
from core import report, utils, Side, Server, Coalition
from psycopg.rows import dict_row
from .filter import StatisticsFilter


def get_sides(interaction: discord.Interaction, server: Server) -> list[Side]:
    if not interaction:
        return [Side.SPECTATOR.value, Side.BLUE.value, Side.RED.value]
    tmp = utils.get_sides(interaction.client, interaction, server)
    sides = [0]
    if Coalition.RED in tmp:
        sides.append(Side.RED.value)
    if Coalition.BLUE in tmp:
        sides.append(Side.BLUE.value)
    # in this specific case, we want to display all data, if in public channels
    if len(sides) == 0:
        sides = [Side.SPECTATOR.value, Side.BLUE.value, Side.RED.value]
    return sides


class HighscorePlaytime(report.GraphElement):

    def render(self, interaction: discord.Interaction, server_name: str, period: str, limit: int, flt: StatisticsFilter):
        sql = "SELECT p.discord_id, COALESCE(p.name, 'Unknown') AS name, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - " \
              "s.hop_on)))) AS playtime FROM statistics s, players p, missions m WHERE p.ucid = s.player_ucid AND " \
              "s.hop_off IS NOT NULL AND s.mission_id = m.id "
        if server_name:
            sql += "AND m.server_name = %s"
            self.env.embed.description = utils.escape_string(server_name)
            if server_name in self.bot.servers:
                sql += ' AND s.side in (' + ','.join([str(x) for x in get_sides(interaction, self.bot.servers[server_name])]) + ')'
        self.env.embed.title = flt.format(self.env.bot, period, server_name) + ' ' + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)
        sql += f' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {limit}'

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                labels = []
                values = []
                if server_name:
                    rows = cursor.execute(sql, (server_name, )).fetchall()
                else:
                    rows = cursor.execute(sql).fetchall()
                for row in rows:
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, row['playtime'] / 3600)
                self.axes.barh(labels, values, color=['#CD7F32', 'silver', 'gold'], height=0.75)
                self.axes.set_xlabel('hours')
                self.axes.set_title('Longest Playtimes', color='white', fontsize=25)
                if len(values) == 0:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', size=15)


class HighscoreElement(report.GraphElement):

    def render(self, interaction: discord.Interaction, server_name: str, period: str, limit: int, kill_type: str,
               flt: StatisticsFilter):
        sql_parts = {
            'Air Targets': 'SUM(s.kills_planes+s.kills_helicopters)',
            'Ships': 'SUM(s.kills_ships)',
            'Air Defence': 'SUM(s.kills_sams)',
            'Ground Targets': 'SUM(s.kills_ground)',
            'KD-Ratio': 'CASE WHEN SUM(deaths_planes + deaths_helicopters + deaths_ships + deaths_sams + '
                        'deaths_ground) = 0 THEN SUM(s.kills) ELSE SUM(s.kills::DECIMAL)/SUM((deaths_planes + '
                        'deaths_helicopters + deaths_ships + deaths_sams + deaths_ground)::DECIMAL) END',
            'PvP-KD-Ratio': 'CASE WHEN SUM(s.deaths_pvp) = 0 THEN SUM(s.pvp) ELSE SUM(s.pvp::DECIMAL)/SUM('
                            's.deaths_pvp::DECIMAL) END',
            'Most Efficient Killers': 'SUM(s.kills) / (SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600.0)',
            'Most Wasteful Pilots': 'SUM(s.crashes) / (SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600.0)'
        }
        xlabels = {
            'Air Targets': 'kills',
            'Ships': 'kills',
            'Air Defence': 'kills',
            'Ground Targets': 'kills',
            'KD-Ratio': 'K/D-ratio',
            'PvP-KD-Ratio': 'K/D-ratio',
            'Most Efficient Killers': 'kills / h',
            'Most Wasteful Pilots': 'airframes wasted / h'
        }
        colors = ['#CD7F32', 'silver', 'gold']
        sql = f"SELECT p.discord_id, COALESCE(p.name, 'Unknown') AS name, {sql_parts[kill_type]} AS value FROM " \
              f"players p, statistics s, missions m WHERE s.player_ucid = p.ucid AND s.mission_id = m.id "
        if server_name:
            sql += "AND m.server_name = %s"
            if server_name in self.bot.servers:
                sql += ' AND s.side in (' + ','.join([str(x) for x in get_sides(interaction, self.bot.servers[server_name])]) + ')'
        sql += ' AND ' + flt.filter(self.env.bot, period, server_name)
        sql += f' AND s.hop_off IS NOT NULL GROUP BY 1, 2 HAVING {sql_parts[kill_type]} > 0'
        if kill_type in ['Most Efficient Killers', 'Most Wasteful Pilots']:
            sql += f' AND SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) > 1800'
        sql += f' ORDER BY 3 DESC LIMIT {limit}'

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                labels = []
                values = []
                if server_name:
                    rows = cursor.execute(sql, (server_name, )).fetchall()
                else:
                    rows = cursor.execute(sql).fetchall()
                for row in rows:
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, row['value'])
                self.axes.barh(labels, values, color=colors, label=kill_type, height=0.75)
                self.axes.set_title(kill_type, color='white', fontsize=25)
                self.axes.set_xlabel(xlabels[kill_type])
                if len(values) == 0:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
                else:
                    scale = range(0, math.ceil(max(values) + 1), math.ceil(max(values) / 10))
                    self.axes.set_xticks(scale)
