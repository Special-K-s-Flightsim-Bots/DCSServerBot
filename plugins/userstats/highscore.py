import discord
import math
import numpy as np

from core import report, utils, Side, Server, Coalition
from decimal import Decimal
from matplotlib import cm
from psycopg.rows import dict_row
from typing import Optional

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


def compute_font_size(num_bars):
    if num_bars <= 10:
        return 10  # Default font size for 10 or fewer bars
    elif num_bars <= 20:
        return 10 - (num_bars - 10) * (3 / 10)  # Linear reduction
    else:
        return 7  # Minimum font size for 20 or more bars


class HighscorePlaytime(report.GraphElement):

    async def render(self, interaction: discord.Interaction, server_name: str, limit: int,
                     flt: StatisticsFilter, bar_labels: Optional[bool] = True):
        sql = """
            SELECT p.discord_id, COALESCE(p.name, 'Unknown') AS name, 
                   ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on)))) AS playtime 
            FROM statistics s, players p, missions m 
            WHERE p.ucid = s.player_ucid AND s.mission_id = m.id
        """
        if server_name:
            sql += "AND m.server_name = %(server_name)s"
            self.env.embed.description = utils.escape_string(server_name)
            if server_name in self.bot.servers:
                sql += ' AND s.side in (' + ','.join([
                    str(x) for x in get_sides(interaction, self.bot.servers[server_name])
                ]) + ')'
        self.env.embed.title = flt.format(self.env.bot) + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot)
        sql += f' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {limit}'

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                labels = []
                values = []
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, row['playtime'] / 3600)

        num_bars = len(labels)
        self.axes.set_title('Longest Playtimes', color='white', fontsize=25)

        if num_bars > 0:
            fontsize = compute_font_size(num_bars)
            bar_height = max(0.75, 3 / num_bars)

            color_map = cm.get_cmap('viridis', num_bars)
            colors = color_map(np.linspace(0, 1, num_bars))

            self.axes.barh(labels, values, color=colors, height=bar_height)
            self.axes.set_xlabel('hours', fontsize=fontsize)

            if bar_labels:
                for c in self.axes.containers:
                    self.axes.bar_label(c, fmt='%.1f h', label_type='edge', padding=2,
                                        fontsize=fontsize)
                self.axes.margins(x=0.1)
            self.axes.tick_params(axis='y', labelsize=fontsize)

        else:
            self.axes.set_xticks([])
            self.axes.set_yticks([])
            xlim = self.axes.get_xlim()
            ylim = self.axes.get_ylim()
            self.axes.text(
                (xlim[1] - xlim[0]) / 2 + xlim[0],  # Midpoint of x-axis
                (ylim[1] - ylim[0]) / 2 + ylim[0],  # Midpoint of y-axis
                'No data available.',
                ha='center', va='center', size=15
            )


class HighscoreElement(report.GraphElement):

    async def render(self, interaction: discord.Interaction, server_name: str, limit: int, kill_type: str,
                     flt: StatisticsFilter, bar_labels: Optional[bool] = True):
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
            'Most Efficient Killers': "SUM(s.kills::DECIMAL) / (SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on)))::DECIMAL / 3600.0)",
            'Most Wasteful Pilots': "SUM(s.crashes::DECIMAL) / (SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on)))::DECIMAL / 3600.0)"
        }
        # create the WHERE conditions
        where_part = ""
        if server_name:
            where_part += "AND m.server_name = %(server_name)s"
            if server_name in self.bot.servers:
                where_part += ' AND s.side in (' + ','.join([
                    str(x) for x in get_sides(interaction, self.bot.servers[server_name])
                ]) + ')'
        # only flighttimes of over an hour count for most efficient / wasteful
        if not (flt.period and (flt.period in ['day', 'today', 'yesterday'] or flt.period.startswith('mission_id:'))) and kill_type in ['Most Efficient Killers', 'Most Wasteful Pilots']:
            where_part += f" AND EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on)) >= 3600"

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
        sql = f"""
            SELECT p.discord_id, COALESCE(p.name, 'Unknown') AS name, 
                   {sql_parts[kill_type]} AS value 
            FROM players p, statistics s, missions m 
            WHERE s.player_ucid = p.ucid AND s.mission_id = m.id
            {where_part}
            AND {flt.filter(self.env.bot)}
            GROUP BY 1, 2 
            HAVING {sql_parts[kill_type]} > 0
            ORDER BY 3 DESC LIMIT {limit}
        """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                labels = []
                values = []
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, row['value'])

        self.axes.set_title(kill_type, color='white', fontsize=25)
        self.axes.set_xlabel(xlabels[kill_type])
        if values and bar_labels:
            num_bars = len(labels)
            fontsize = compute_font_size(num_bars)
            bar_height = max(0.75, 3 / num_bars)

            color_map = cm.get_cmap('viridis', num_bars)
            colors = color_map(np.linspace(0, 1, num_bars))

            self.axes.barh(labels, values, color=colors, label=kill_type, height=bar_height)
            for c in self.axes.containers:
                self.axes.bar_label(c, fmt='%.2f' if isinstance(values[0], Decimal) else '%d', label_type='edge',
                                    padding=2, fontsize=fontsize)
            self.axes.margins(x=0.125)
            scale = range(0, math.ceil(max(values) + 1), math.ceil(max(values) / 10))
            self.axes.tick_params(axis='y', labelsize=fontsize)
            self.axes.set_xticks(scale)
        else:
            self.axes.set_xticks([])
            self.axes.set_yticks([])
            xlim = self.axes.get_xlim()
            ylim = self.axes.get_ylim()
            self.axes.text(
                (xlim[1] - xlim[0]) / 2 + xlim[0],  # Midpoint of x-axis
                (ylim[1] - ylim[0]) / 2 + ylim[0],  # Midpoint of y-axis
                'No data available.',
                ha='center', va='center', size=15, rotation=45
            )
