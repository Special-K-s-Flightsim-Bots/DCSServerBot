import discord
import matplotlib.pyplot as plt
import numpy as np
from core import report, utils
from matplotlib.axes import Axes
from matplotlib.patches import ConnectionPatch
from psycopg.rows import dict_row
from typing import Union
from .filter import StatisticsFilter


class Header(report.EmbedElement):
    async def render(self, member: Union[discord.Member, str], server_name: str, flt: StatisticsFilter):
        sql = '''
            SELECT p.first_seen, p.last_seen, 
                   COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))), 0) AS playtime 
            FROM players p
            LEFT OUTER JOIN statistics s ON s.player_ucid = p.ucid AND s.hop_off IS NOT NULL 
            LEFT OUTER JOIN missions m ON s.mission_id = m.id
        '''
        if isinstance(member, discord.Member):
            sql += 'WHERE p.discord_id = %(member)s '
        else:
            sql += 'WHERE p.ucid = %(member)s '
        if server_name:
            self.env.embed.description = utils.escape_string(server_name)
            sql += "AND m.server_name = %(server_name)s"
        self.env.embed.title = flt.format(self.env.bot) + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot)
        sql += ' GROUP BY 1, 2'
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, {
                    "member": str(member.id if isinstance(member, discord.Member) else member,),
                    "server_name": server_name
                })
                row = await cursor.fetchone()
                if row:
                    self.add_datetime_field("First seen", row['first_seen'])
                    self.add_datetime_field("Last seen", row['last_seen'])
                    self.add_field(name="Playtime", value=utils.convert_time(row['playtime']))


class PlaytimesPerPlane(report.GraphElement):

    async def render(self, member: Union[discord.Member, str], server_name: str, flt: StatisticsFilter):
        sql = 'SELECT s.slot, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime FROM ' \
              'statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND ' \
              's.hop_off IS NOT NULL AND s.mission_id = m.id '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            self.env.embed.description = utils.escape_string(server_name)
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot)
        sql += ' GROUP BY s.slot ORDER BY 2'

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                labels = []
                values = []
                await cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                async for row in cursor:
                    labels.insert(0, row['slot'])
                    values.insert(0, float(row['playtime']) / 3600.0)

        self.axes.bar(labels, values, width=0.5, color='mediumaquamarine')
        for label in self.axes.get_xticklabels():
            label.set_rotation(30)
            label.set_ha('right')
        self.axes.set_title('Airframe Hours per Aircraft', color='white', fontsize=25)
        self.axes.set_yticks([])
        for i in range(0, len(values)):
            self.axes.annotate('{:.1f} h'.format(values[i]), xy=(
                labels[i], values[i]), ha='center', va='bottom', weight='bold')
        if cursor.rowcount == 0:
            self.axes.set_xticks([])
            self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)


class PlaytimesPerServer(report.GraphElement):

    async def render(self, member: Union[discord.Member, str], server_name: str, flt: StatisticsFilter):
        sql = f"SELECT regexp_replace(m.server_name, '{self.bot.filter['server_name']}', '', 'g') AS " \
              f"server_name, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime FROM statistics s, " \
              f"players p, missions m WHERE s.player_ucid = p.ucid AND m.id = s.mission_id AND " \
              f"s.hop_off IS NOT NULL "
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot)
        sql += ' GROUP BY 1'

        labels = []
        values = []
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                async for row in cursor:
                    labels.insert(0, row['server_name'])
                    values.insert(0, float(row['playtime']))

        if values:
            def func(pct, allvals):
                absolute = int(round(pct / 100. * np.sum(allvals)))
                return utils.convert_time(absolute)

            patches, texts, pcts = self.axes.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                                 wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
            plt.setp(pcts, color='black', fontweight='bold')
            self.axes.set_title('Server Time', color='white', fontsize=25)
            self.axes.axis('equal')
        else:
            self.axes.set_visible(False)


class PlaytimesPerMap(report.GraphElement):

    async def render(self, member: Union[discord.Member, str], server_name: str, flt: StatisticsFilter):
        sql = 'SELECT m.mission_theatre, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS ' \
              'playtime FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND ' \
              'm.id = s.mission_id AND s.hop_off IS NOT NULL '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot)
        sql += ' GROUP BY m.mission_theatre'

        labels = []
        values = []
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                async for row in cursor:
                    labels.insert(0, row['mission_theatre'])
                    values.insert(0, float(row['playtime']))

        if values:
            def func(pct, allvals):
                absolute = int(round(pct / 100. * np.sum(allvals)))
                return utils.convert_time(absolute)

            patches, texts, pcts = self.axes.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                                 wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
            plt.setp(pcts, color='black', fontweight='bold')
            self.axes.set_title('Time per Map', color='white', fontsize=25)
            self.axes.axis('equal')
        else:
            self.axes.set_visible(False)


class RecentActivities(report.GraphElement):

    async def render(self, member: Union[discord.Member, str], server_name: str, flt: StatisticsFilter):
        sql = """
            SELECT TO_CHAR(s.hop_on, 'MM/DD') as day, 
                   ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (now() AT TIME ZONE 'utc')) - s.hop_on)))) AS playtime 
            FROM statistics s, players p, missions m 
            WHERE s.player_ucid = p.ucid AND s.hop_on > (DATE((now() AT TIME ZONE 'utc')) - integer '7') 
            AND s.mission_id = m.id
        """
        if isinstance(member, discord.Member):
            sql += ' AND p.discord_id = %s'
        else:
            sql += ' AND p.ucid = %s'
        if server_name:
            sql += " AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot)
        sql += ' GROUP BY day'

        labels = []
        values = []
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                async for row in cursor:
                    labels.append(row['day'])
                    values.append(float(row['playtime']) / 3600.0)

        self.axes.set_title('Recent Activities', color='white', fontsize=25)
        self.axes.set_yticks([])
        self.axes.bar(labels, values, width=0.5, color='mediumaquamarine')
        if values:
            for i in range(0, len(values)):
                self.axes.annotate('{:.1f} h'.format(values[i]), xy=(
                    labels[i], values[i]), ha='center', va='bottom', weight='bold')
        else:
            self.axes.set_xticks([])
            self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)


class FlightPerformance(report.GraphElement):

    async def render(self, member: Union[discord.Member, str], server_name: str, flt: StatisticsFilter):
        sql = 'SELECT SUM(ejections) as "Ejections", SUM(crashes-ejections) as "Crashes\n(Pilot dead)", ' \
              'SUM(landings) as "Landings" FROM statistics s, ' \
              'players p, missions m WHERE s.player_ucid = p.ucid ' \
              'AND s.mission_id = m.id '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                if cursor.rowcount > 0:
                    labels = []
                    values = []
                    for name, value in dict(await cursor.fetchone()).items():
                        if value and int(value) > 0:
                            labels.append(name)
                            values.append(value)

        def func(pct, allvals):
            absolute = int(round(pct / 100. * np.sum(allvals)))
            return f'{absolute}'

        if len(values) > 0:
            patches, texts, pcts = \
                self.axes.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                              wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
            plt.setp(pcts, color='black', fontweight='bold')
            self.axes.set_title('Flying', color='white', fontsize=25)
            self.axes.axis('equal')
        else:
            self.axes.set_visible(False)


class KDRatio(report.MultiGraphElement):

    async def draw_kill_performance(self, ax: Axes, member: Union[discord.Member, str], server_name: str,
                                    flt: StatisticsFilter):
        sql = """
            SELECT COALESCE(SUM(kills - pvp), 0) as "AI Kills", 
                   COALESCE(SUM(pvp), 0) as "Player Kills", 
                   COALESCE(SUM(deaths_planes + deaths_helicopters + deaths_ships + deaths_sams + deaths_ground - deaths_pvp), 0) as "Deaths by AI", 
                   COALESCE(SUM(deaths_pvp),0) as "Deaths by Player", 
                   COALESCE(SUM(GREATEST(deaths, crashes) - deaths_planes - deaths_helicopters - deaths_ships - deaths_sams - deaths_ground), 0) AS "Selfkill", 
                   COALESCE(SUM(teamkills), 0) as "Teamkills" 
            FROM statistics s, players p, missions m 
            WHERE s.player_ucid = p.ucid 
            AND s.mission_id = m.id
        """
        if isinstance(member, discord.Member):
            sql += ' AND p.discord_id = %s '
        else:
            sql += ' AND p.ucid = %s '
        if server_name:
            sql += " AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot)

        retval = []
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                if cursor.rowcount > 0:
                    def func(pct, allvals):
                        absolute = int(round(pct / 100. * np.sum(allvals)))
                        return f'{absolute}'

                    labels = []
                    values = []
                    explode = []
                    result = await cursor.fetchone()
                    for name, value in dict(result).items():
                        if value and int(value) > 0:
                            labels.append(name)
                            values.append(value)
                            retval.append(name)
                            explode.append(0.02)

        if len(values) > 0:
            angle1 = -180 * (result['AI Kills'] + result['Player Kills']) / np.sum(values)
            angle2 = 180 - 180 * (result['Deaths by AI'] + result['Deaths by Player']) / np.sum(values)
            if angle1 == 0:
                angle = angle2
            elif angle2 == 180:
                angle = angle1
            else:
                angle = angle1 + (angle2 + angle1) / 2

            patches, texts, pcts = ax.pie(values, labels=labels, startangle=angle, explode=explode,
                                          autopct=lambda pct: func(pct, values),
                                          colors=['lightgreen', 'darkorange', 'lightblue'],
                                          wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'},
                                          normalize=True)
            plt.setp(pcts, color='black', fontweight='bold')
            ax.set_title('Kill/Death-Ratio', color='white', fontsize=25)
            ax.axis('equal')
        else:
            ax.set_visible(False)
        return retval

    async def draw_kill_types(self, ax: Axes, member: Union[discord.Member, str], server_name: str,
                              flt: StatisticsFilter) -> bool:
        sql = 'SELECT COALESCE(SUM(kills_planes), 0) as planes, COALESCE(SUM(kills_helicopters), 0) helicopters, ' \
              'COALESCE(SUM(kills_ships), 0) as ships, COALESCE(SUM(kills_sams), 0) as air_defence, COALESCE(SUM(' \
              'kills_ground), 0) as ground FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND ' \
              's.mission_id = m.id '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot)

        retval = False
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                # if no data was found, return False as no chart was drawn
                if cursor.rowcount > 0:
                    labels = []
                    values = []
                    for name, value in dict(await cursor.fetchone()).items():
                        labels.append(name.replace('_', ' ').title())
                        values.append(value)

        xpos = 0
        bottom = 0
        width = 0.2
        # there is something to be drawn
        _sum = np.sum(values)
        if _sum > 0:
            for i in range(len(values)):
                height = values[i] / _sum
                ax.bar(xpos, height, width, bottom=bottom)
                ypos = bottom + ax.patches[i].get_height() / 2
                bottom += height
                if int(values[i]) > 0:
                    ax.text(xpos, ypos, f"{values[i]}", ha='center', color='black')

            ax.set_title('Killed by\nPlayer', color='white', fontsize=15)
            ax.axis('off')
            ax.set_xlim(- 2.5 * width, 2.5 * width)
            ax.legend(labels, fontsize=15, loc=3, ncol=6, mode='expand',
                      bbox_to_anchor=(-2.4, -0.2, 2.8, 0.4), columnspacing=1, frameon=False)
            # Chart was drawn, return True
            retval = True
        return retval

    async def draw_death_types(self, ax: Axes, legend: bool, member: Union[discord.Member, str], server_name: str,
                               flt: StatisticsFilter) -> bool:
        sql = 'SELECT SUM(deaths_planes) as planes, SUM(deaths_helicopters) helicopters, SUM(deaths_ships) as ships, ' \
              'SUM(deaths_sams) as air_defence, SUM(deaths_ground) as ground FROM statistics s, players p, ' \
              'missions m WHERE s.player_ucid = p.ucid AND s.mission_id = m.id '
        if isinstance(member, discord.Member):
            sql += 'AND p.discord_id = %s '
        else:
            sql += 'AND p.ucid = %s '
        if server_name:
            sql += "AND m.server_name = '{}'".format(server_name.replace('\'', '\'\''))
        sql += ' AND ' + flt.filter(self.env.bot)

        retval = False
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, (member.id if isinstance(member, discord.Member) else member,))
                result = await cursor.fetchone()
                # if no data was found, return False as no chart was drawn
                if result:
                    labels = []
                    values = []
                    for name, value in dict(result).items():
                        labels.append(name.replace('_', ' ').title())
                        values.append(value)

        xpos = 0
        bottom = 0
        width = 0.2
        # there is something to be drawn
        _sum = np.sum(values)
        if _sum > 0:
            for i in range(len(values)):
                height = values[i] / _sum
                ax.bar(xpos, height, width, bottom=bottom)
                ypos = bottom + ax.patches[i].get_height() / 2
                bottom += height
                if int(values[i]) > 0:
                    ax.text(xpos, ypos, f"{values[i]}", ha='center', color='black')

            ax.set_title('Player\nkilled by', color='white', fontsize=15)
            ax.axis('off')
            ax.set_xlim(- 2.5 * width, 2.5 * width)
            if legend:
                ax.legend(labels, fontsize=15, loc=3, ncol=6, mode='expand',
                          bbox_to_anchor=(0.6, -0.2, 2.8, 0.4), columnspacing=1, frameon=False)
            # Chart was drawn, return True
            retval = True
        return retval

    async def render(self, member: Union[discord.Member, str], server_name: str, flt: StatisticsFilter):
        retval = await self.draw_kill_performance(self.axes[1], member, server_name, flt)
        i = 0
        if (('AI Kills' in retval or 'Player Kills' in retval) and
                ((await self.draw_kill_types(self.axes[2], member, server_name, flt)) is True)):
            # use ConnectionPatch to draw lines between the two plots
            # get the wedge data
            theta1 = self.axes[1].patches[i].theta1
            if 'AI Kills' in retval and 'Player Kills' in retval:
                i += 1
            theta2 = self.axes[1].patches[i].theta2
            center, r = self.axes[1].patches[i].center, self.axes[1].patches[i].r
            bar_height = sum([item.get_height() for item in self.axes[2].patches])

            # draw the top connecting line
            x = r * np.cos(np.pi / 180 * theta2) + center[0]
            y = r * np.sin(np.pi / 180 * theta2) + center[1]
            con = ConnectionPatch(xyA=(-0.2 / 2, bar_height), coordsA=self.axes[2].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[2].add_artist(con)

            # draw the bottom connecting line
            x = r * np.cos(np.pi / 180 * theta1) + center[0]
            y = r * np.sin(np.pi / 180 * theta1) + center[1]
            con = ConnectionPatch(xyA=(-0.2 / 2, 0), coordsA=self.axes[2].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[2].add_artist(con)
            i += 1
        else:
            self.axes[2].set_visible(False)
        if (('Deaths by AI' in retval or 'Deaths by Player' in retval) and
                ((await self.draw_death_types(self.axes[0], (i == 0), member, server_name, flt)) is True)):
            # use ConnectionPatch to draw lines between the two plots
            # get the wedge data
            theta1 = self.axes[1].patches[i].theta1
            if 'Deaths by AI' in retval and 'Deaths by Player' in retval:
                i += 1
            theta2 = self.axes[1].patches[i].theta2
            center, r = self.axes[1].patches[i].center, self.axes[1].patches[i].r
            bar_height = sum([item.get_height() for item in self.axes[0].patches])

            # draw the top connecting line
            x = r * np.cos(np.pi / 180 * theta2) + center[0]
            y = r * np.sin(np.pi / 180 * theta2) + center[1]
            con = ConnectionPatch(xyA=(0.2 / 2, 0), coordsA=self.axes[0].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[0].add_artist(con)

            # draw the bottom connecting line
            x = r * np.cos(np.pi / 180 * theta1) + center[0]
            y = r * np.sin(np.pi / 180 * theta1) + center[1]
            con = ConnectionPatch(xyA=(0.2 / 2, bar_height), coordsA=self.axes[0].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[0].add_artist(con)
        else:
            self.axes[0].set_visible(False)
