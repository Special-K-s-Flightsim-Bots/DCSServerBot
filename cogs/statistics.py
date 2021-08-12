# statistics.py
import asyncio
import concurrent
import discord
import matplotlib.pyplot as plt
import numpy as np
import string
import os
import psycopg2
import psycopg2.extras
import re
import util
from contextlib import closing, suppress
from datetime import timedelta
from discord.ext import commands
from matplotlib.patches import ConnectionPatch
from matplotlib.ticker import FuncFormatter


class Statistics(commands.Cog):

    WEEKDAYS = {
        0: 'Mon',
        1: 'Tue',
        2: 'Wed',
        3: 'Thu',
        4: 'Fri',
        5: 'Sat',
        6: 'Sun'
    }

    def __init__(self, bot):
        self.bot = bot
        plt.switch_backend('agg')
        self.servers = []
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT DISTINCT server_name FROM missions')
                for row in cursor.fetchall():
                    self.servers.append(row[0])
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Links a member to a DCS user', usage='<member> <ucid>')
    @commands.has_any_role('Admin', 'Moderator')
    @commands.guild_only()
    async def link(self, ctx, member: discord.Member, ucid):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET discord_id = %s WHERE ucid = %s', (member.id, ucid))
                conn.commit()
                await ctx.send('Member {} linked to ucid {}'.format(member.display_name, ucid))
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    def draw_playtime_planes(self, member, axis, server):
        SQL_PLAYTIME = 'SELECT s.slot, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime FROM statistics s, ' \
            'players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s AND s.hop_off IS NOT NULL AND s.mission_id = m.id '
        if (server is not None):
            SQL_PLAYTIME += 'AND m.server_name = \'{}\' '.format(server)
        SQL_PLAYTIME += 'GROUP BY s.slot ORDER BY 2'

        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_PLAYTIME, (member.id, ))
                labels = []
                values = []
                for row in cursor.fetchall():
                    labels.insert(0, row['slot'])
                    values.insert(0, row['playtime'] / 3600.0)
                axis.bar(labels, values, width=0.5, color='mediumaquamarine')
                # axis.set_xticklabels(axis.get_xticklabels(), rotation=45, ha='right')
                for label in axis.get_xticklabels():
                    label.set_rotation(30)
                    label.set_ha('right')
                axis.set_title('Flighttimes per Plane', color='white', fontsize=25)
                # axis.set_yscale('log')
                axis.set_yticks([])
                for i in range(0, len(values)):
                    axis.annotate('{:.1f} h'.format(values[i]), xy=(
                        labels[i], values[i]), ha='center', va='bottom', weight='bold')
                if (cursor.rowcount == 0):
                    axis.set_xticks([])
                    axis.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    def draw_server_time(self, member, axis, server):
        SQL_STATISTICS = 'SELECT trim(m.server_name) as server_name, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime '\
            'FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s AND m.id = s.mission_id AND s.hop_off IS NOT NULL '
        if (server is not None):
            SQL_STATISTICS += 'AND m.server_name = \'{}\' '.format(server)
        SQL_STATISTICS += 'GROUP BY trim(m.server_name)'

        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_STATISTICS, (member.id, ))
                if (cursor.rowcount > 0):
                    def func(pct, allvals):
                        absolute = int(round(pct/100.*np.sum(allvals)))
                        return '{:.1f}%\n({:s}h)'.format(pct, str(timedelta(seconds=absolute)))

                    labels = []
                    values = []
                    for row in cursor.fetchall():
                        labels.insert(0, re.sub(self.bot.config['FILTER']
                                                ['SERVER_FILTER'], '', row['server_name']).strip())
                        values.insert(0, row['playtime'])
                    patches, texts, pcts = axis.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                                    wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
                    plt.setp(pcts, color='black', fontweight='bold')
                    axis.set_title('Server Time', color='white', fontsize=25)
                    axis.axis('equal')
                else:
                    axis.set_visible(False)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    def draw_map_time(self, member, axis, server):
        SQL_STATISTICS = 'SELECT m.mission_theatre, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime '\
            'FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s AND m.id = s.mission_id AND s.hop_off IS NOT NULL '
        if (server is not None):
            SQL_STATISTICS += 'AND m.server_name = \'{}\' '.format(server)
        SQL_STATISTICS += 'GROUP BY m.mission_theatre'

        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_STATISTICS, (member.id, ))
                if (cursor.rowcount > 0):
                    def func(pct, allvals):
                        absolute = int(round(pct/100.*np.sum(allvals)))
                        return '{:.1f}%\n({:s}h)'.format(pct, str(timedelta(seconds=absolute)))

                    labels = []
                    values = []
                    for row in cursor.fetchall():
                        labels.insert(0, row['mission_theatre'])
                        values.insert(0, row['playtime'])
                    patches, texts, pcts = axis.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                                    wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
                    plt.setp(pcts, color='black', fontweight='bold')
                    axis.set_title('Time per Map', color='white', fontsize=25)
                    axis.axis('equal')
                else:
                    axis.set_visible(False)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    def draw_recent(self, member, axis, server):
        SQL_STATISTICS = 'SELECT TO_CHAR(s.hop_on, \'MM/DD\') as day, ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW()) - s.hop_on)))) AS playtime ' \
            'FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s AND s.hop_on > (DATE(NOW()) - integer \'7\') ' \
            'AND s.mission_id = m.id '
        if (server is not None):
            SQL_STATISTICS += 'AND m.server_name = \'{}\' '.format(server)
        SQL_STATISTICS += 'GROUP BY day'

        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                labels = []
                values = []
                cursor.execute(SQL_STATISTICS, (member.id, ))
                axis.set_title('Recent Activities', color='white', fontsize=25)
                axis.set_yticks([])
                for row in cursor.fetchall():
                    labels.append(row['day'])
                    values.append(row['playtime'] / 3600.0)
                axis.bar(labels, values, width=0.5, color='mediumaquamarine')
                for i in range(0, len(values)):
                    axis.annotate('{:.1f} h'.format(values[i]), xy=(
                        labels[i], values[i]), ha='center', va='bottom', weight='bold')
                if (cursor.rowcount == 0):
                    axis.set_xticks([])
                    axis.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    def draw_flight_performance(self, member, axis, server):
        SQL_STATISTICS = 'SELECT SUM(ejections) as ejections, SUM(crashes) as crashes, ' \
            'SUM(takeoffs) as takeoffs, SUM(landings) as landings FROM statistics s, ' \
            'players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s' \
            'AND s.mission_id = m.id '
        if (server is not None):
            SQL_STATISTICS += 'AND m.server_name = \'{}\''.format(server)

        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_STATISTICS, (member.id, ))
                if (cursor.rowcount > 0):
                    def func(pct, allvals):
                        absolute = int(round(pct/100.*np.sum(allvals)))
                        return '{:.1f}%\n({:d})'.format(pct, absolute)

                    labels = []
                    values = []
                    for item in dict(cursor.fetchone()).items():
                        if(item[1] is not None and item[1] > 0):
                            labels.append(string.capwords(item[0]))
                            values.append(item[1])
                    if (len(values) > 0):
                        patches, texts, pcts = axis.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                                        wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
                        plt.setp(pcts, color='black', fontweight='bold')
                        axis.set_title('Flying', color='white', fontsize=25)
                        axis.axis('equal')
                    else:
                        axis.set_visible(False)
                else:
                    axis.set_visible(False)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    def draw_kill_performance(self, member, axis, server):
        SQL_STATISTICS = 'SELECT COALESCE(SUM(kills), 0) as kills, COALESCE(SUM(deaths), 0) as deaths, COALESCE(SUM(teamkills), 0) as teamkills FROM statistics s, ' \
            'players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s AND s.mission_id = m.id '
        if (server is not None):
            SQL_STATISTICS += 'AND m.server_name = \'{}\''.format(server)

        retval = []
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_STATISTICS, (member.id, ))
                if (cursor.rowcount > 0):
                    def func(pct, allvals):
                        absolute = int(round(pct/100.*np.sum(allvals)))
                        return '{:.1f}%\n({:d})'.format(pct, absolute)

                    labels = []
                    values = []
                    explode = []
                    result = cursor.fetchone()
                    for item in dict(result).items():
                        if(item[1] is not None and item[1] > 0):
                            labels.append(string.capwords(item[0]))
                            values.append(item[1])
                            if (item[0] in ['deaths', 'kills']):
                                retval.append(item[0])
                                explode.append(0.1)
                            else:
                                explode.append(0.0)
                    if (len(values)):
                        angle1 = -180 * result[0]/np.sum(values)
                        angle2 = 180 - 180*result[1]/np.sum(values)
                        if (angle1 == 0):
                            angle = angle2
                        elif (angle2 == 180):
                            angle = angle1
                        else:
                            angle = angle1 + (angle2 + angle1) / 2

                        patches, texts, pcts = axis.pie(values, labels=labels, startangle=angle, explode=explode,
                                                        autopct=lambda pct: func(pct, values), colors=['lightgreen', 'darkorange', 'lightblue'],
                                                        wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
                        plt.setp(pcts, color='black', fontweight='bold')
                        axis.set_title('Kill/Death-Ratio', color='white', fontsize=25)
                        axis.axis('equal')
                    else:
                        axis.set_visible(False)
                else:
                    axis.set_visible(False)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)
        return retval

    def draw_kill_types(self, member, axis, server):
        SQL_STATISTICS = 'SELECT COALESCE(SUM(kills_planes), 0) as planes, COALESCE(SUM(kills_helicopters), 0) helicopters, COALESCE(SUM(kills_ships), 0) as ships, ' \
            'COALESCE(SUM(kills_sams), 0) as air_defence, COALESCE(SUM(kills_ground), 0) as ground FROM statistics s, ' \
            'players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s AND s.mission_id = m.id '
        if (server is not None):
            SQL_STATISTICS += 'AND m.server_name = \'{}\' '.format(server)

        retval = False
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_STATISTICS, (member.id, ))
                # if no data was found, return False as no chart was drawn
                if (cursor.rowcount > 0):
                    labels = []
                    values = []
                    for item in dict(cursor.fetchone()).items():
                        labels.append(string.capwords(item[0], sep='_').replace('_', ' '))
                        values.append(item[1])
                    xpos = 0
                    bottom = 0
                    width = 0.2
                    # there is something to be drawn
                    if (np.sum(values) > 0):
                        for i in range(len(values)):
                            height = values[i]/np.sum(values)
                            axis.bar(xpos, height, width, bottom=bottom)
                            ypos = bottom + axis.patches[i].get_height() / 2
                            bottom += height
                            if (int(values[i]) > 0):
                                axis.text(xpos, ypos, "%d%%" %
                                          (axis.patches[i].get_height() * 100), ha='center', color='black')

                        axis.set_title('Killed by\nPlayer', color='white', fontsize=15)
                        axis.axis('off')
                        axis.set_xlim(- 2.5 * width, 2.5 * width)
                        axis.legend(labels, fontsize=15, loc=3, ncol=5, mode='expand',
                                    bbox_to_anchor=(-2.4, -0.2, 2.8, 0.4), columnspacing=1, frameon=False)
                        # Chart was drawn, return True
                        retval = True
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)
        return retval

    def draw_death_types(self, member, axis, legend, server):
        SQL_STATISTICS = 'SELECT SUM(deaths_planes) as planes, SUM(deaths_helicopters) helicopters, SUM(deaths_ships) as ships, ' \
            'SUM(deaths_sams) as air_defence, SUM(deaths_ground) as ground FROM statistics s, ' \
            'players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = %s AND s.mission_id = m.id '
        if (server is not None):
            SQL_STATISTICS += 'AND m.server_name = \'{}\' '.format(server)

        retval = False
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_STATISTICS, (member.id, ))
                result = cursor.fetchone()
                # if no data was found, return False as no chart was drawn
                if (cursor.rowcount > 0):
                    labels = []
                    values = []
                    for item in dict(result).items():
                        labels.append(string.capwords(item[0], sep='_').replace('_', ' '))
                        values.append(item[1])
                    xpos = 0
                    bottom = 0
                    width = 0.2
                    # there is something to be drawn
                    if (np.sum(values) > 0):
                        for i in range(len(values)):
                            height = values[i]/np.sum(values)
                            axis.bar(xpos, height, width, bottom=bottom)
                            ypos = bottom + axis.patches[i].get_height() / 2
                            bottom += height
                            if (int(values[i]) > 0):
                                axis.text(xpos, ypos, "%d%%" %
                                          (axis.patches[i].get_height() * 100), ha='center', color='black')

                        axis.set_title('Player\nkilled by', color='white', fontsize=15)
                        axis.axis('off')
                        axis.set_xlim(- 2.5 * width, 2.5 * width)
                        if (legend is True):
                            axis.legend(labels, fontsize=15, loc=3, ncol=5, mode='expand',
                                        bbox_to_anchor=(0.6, -0.2, 2.8, 0.4), columnspacing=1, frameon=False)
                        # Chart was drawn, return True
                        retval = True
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)
        return retval

    @commands.command(description='Shows player statistics', usage='[member]', aliases=['stats'])
    @commands.has_role('DCS')
    @commands.guild_only()
    async def statistics(self, ctx, member: discord.Member = None, server=None):
        try:
            if (member is None):
                member = ctx.message.author
            plt.style.use('dark_background')
            plt.rcParams['axes.facecolor'] = '2C2F33'
            figure = plt.figure(figsize=(20, 20))
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                executor.submit(self.draw_playtime_planes, member=member,
                                axis=plt.subplot2grid((3, 3), (0, 0), colspan=2, fig=figure), server=server)
                executor.submit(self.draw_recent, member=member, axis=plt.subplot2grid(
                    (3, 3), (0, 2), colspan=1, fig=figure), server=server)
                executor.submit(self.draw_server_time, member=member,
                                axis=plt.subplot2grid((3, 3), (1, 0), colspan=1, fig=figure), server=server)
                executor.submit(self.draw_map_time, member=member,
                                axis=plt.subplot2grid((3, 3), (1, 1), colspan=1, fig=figure), server=server)
                executor.submit(self.draw_flight_performance, member=member,
                                axis=plt.subplot2grid((3, 3), (1, 2), colspan=1, fig=figure), server=server)
            ax1 = plt.subplot2grid((3, 3), (2, 0), colspan=1, fig=figure)
            ax2 = plt.subplot2grid((3, 3), (2, 1), colspan=1, fig=figure)
            ax3 = plt.subplot2grid((3, 3), (2, 2), colspan=1, fig=figure)
            retval = self.draw_kill_performance(member, ax2, server)
            i = 0
            if (('kills' in retval) and (self.draw_kill_types(member, ax3, server) is True)):
                # use ConnectionPatch to draw lines between the two plots
                # get the wedge data
                theta1, theta2 = ax2.patches[i].theta1, ax2.patches[i].theta2
                center, r = ax2.patches[i].center, ax2.patches[i].r
                bar_height = sum([item.get_height() for item in ax3.patches])

                # draw top connecting line
                x = r * np.cos(np.pi / 180 * theta2) + center[0]
                y = r * np.sin(np.pi / 180 * theta2) + center[1]
                con = ConnectionPatch(xyA=(-0.2 / 2, bar_height), coordsA=ax3.transData,
                                      xyB=(x, y), coordsB=ax2.transData)
                con.set_color('lightgray')
                con.set_linewidth(2)
                con.set_linestyle('dashed')
                ax3.add_artist(con)

                # draw bottom connecting line
                x = r * np.cos(np.pi / 180 * theta1) + center[0]
                y = r * np.sin(np.pi / 180 * theta1) + center[1]
                con = ConnectionPatch(xyA=(-0.2 / 2, 0), coordsA=ax3.transData,
                                      xyB=(x, y), coordsB=ax2.transData)
                con.set_color('lightgray')
                con.set_linewidth(2)
                con.set_linestyle('dashed')
                ax3.add_artist(con)
                i += 1
            else:
                ax3.set_visible(False)
            if (('deaths' in retval) and (self.draw_death_types(member, ax1, (i == 0), server) is True)):
                # use ConnectionPatch to draw lines between the two plots
                # get the wedge data
                theta1, theta2 = ax2.patches[i].theta1, ax2.patches[i].theta2
                center, r = ax2.patches[i].center, ax2.patches[i].r
                bar_height = sum([item.get_height() for item in ax1.patches])

                # draw top connecting line
                x = r * np.cos(np.pi / 180 * theta2) + center[0]
                y = r * np.sin(np.pi / 180 * theta2) + center[1]
                con = ConnectionPatch(xyA=(0.2 / 2, 0), coordsA=ax1.transData,
                                      xyB=(x, y), coordsB=ax2.transData)
                con.set_color('lightgray')
                con.set_linewidth(2)
                con.set_linestyle('dashed')
                ax1.add_artist(con)

                # draw bottom connecting line
                x = r * np.cos(np.pi / 180 * theta1) + center[0]
                y = r * np.sin(np.pi / 180 * theta1) + center[1]
                con = ConnectionPatch(xyA=(0.2 / 2, bar_height), coordsA=ax1.transData,
                                      xyB=(x, y), coordsB=ax2.transData)
                con.set_color('lightgray')
                con.set_linewidth(2)
                con.set_linestyle('dashed')
                ax1.add_artist(con)
            else:
                ax1.set_visible(False)

            plt.subplots_adjust(hspace=0.5, wspace=0.5)
            title = 'Statistics for {}'.format(member.display_name)
            if (server is not None):
                title += '\n_{}_'.format(server)
            else:
                title += '\n_- Overall -_'
            embed = discord.Embed(title=title, color=discord.Color.blue())
            filename = '{}.png'.format(member.id)
            figure.savefig(filename, bbox_inches='tight', facecolor='#2C2F33')
            plt.close(figure)
            file = discord.File(filename)
            embed.set_image(url='attachment://' + filename)
            embed.set_footer(text='Click on the image to zoom in.')
            message = None
            try:
                with suppress(Exception):
                    message = await ctx.send(file=file, embed=embed)
                os.remove(filename)
                if (message):
                    await message.add_reaction('◀️')
                    await message.add_reaction('▶️')
                    react = await util.wait_for_single_reaction(self, ctx, message)
                    await message.delete()
                    if (server is None):
                        prev = self.servers[-1]
                        next = self.servers[0]
                    else:
                        i = 0
                        prev = next = None
                        for s in self.servers:
                            if (s == server):
                                break
                            i += 1
                        if (i < len(self.servers) - 1):
                            next = self.servers[i + 1]
                        if (i > 0):
                            prev = self.servers[i - 1]

                    if (react.emoji == '◀️'):
                        await self.statistics(ctx, member, prev)
                    elif (react.emoji == '▶️'):
                        await self.statistics(ctx, member, next)
            except asyncio.TimeoutError:
                await message.clear_reactions()
        except (Exception) as error:
            self.bot.log.exception(error)

    def draw_highscore_playtime(self, ctx, axis, period, server):
        SQL_HIGHSCORE_PLAYTIME = 'SELECT p.discord_id, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime '\
            'FROM statistics s, players p, missions m WHERE p.ucid = s.player_ucid AND s.hop_off IS NOT NULL AND p.discord_id <> -1 AND s.mission_id = m.id'
        if (server):
            SQL_HIGHSCORE_PLAYTIME += ' AND m.server_name = \'{}\' '.format(server)
        if (period):
            SQL_HIGHSCORE_PLAYTIME += ' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {}\')'.format(period)
        SQL_HIGHSCORE_PLAYTIME += ' GROUP BY p.discord_id ORDER BY 2 DESC LIMIT 3'
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                labels = []
                values = []
                cursor.execute(SQL_HIGHSCORE_PLAYTIME)
                for row in cursor.fetchall():
                    member = ctx.message.guild.get_member(row[0])
                    name = member.display_name if (member is not None) else str(row[0])
                    labels.insert(0, name)
                    values.insert(0, row[1] / 3600)
                axis.barh(labels, values, color=['#CD7F32', 'silver', 'gold'], height=0.75)
                axis.set_xlabel('hours')
                axis.set_title('Longes Playtimes', color='white', fontsize=25)
                if (len(values) == 0):
                    axis.set_xticks([])
                    axis.set_yticks([])
                    axis.text(0, 0, 'No data available.', ha='center', va='center', size=15)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    def draw_highscore_kills(self, ctx, figure, period, server):
        SQL_PARTS = {
            'Air Targets': 'SUM(s.kills_planes+s.kills_helicopters)',
            'Ships': 'SUM(s.kills_ships)',
            'Air Defence': 'SUM(s.kills_sams)',
            'Ground Targets': 'SUM(s.kills_ground)',
            'Most Efficient Killers': 'SUM(s.kills) / (SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600)',
            'Most Wasteful Pilots': 'SUM(deaths) / (SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600)'
        }
        LABELS = {
            'Air Targets': 'kills',
            'Ships': 'kills',
            'Air Defence': 'kills',
            'Ground Targets': 'kills',
            'Most Efficient Killers': 'avg. kills / 24 hrs flighttime',
            'Most Wasteful Pilots': 'avg. airframes wasted / 24 hrs flighttime'
        }
        COLORS = ['#CD7F32', 'silver', 'gold']
        SQL_HIGHSCORE = {}
        for key in SQL_PARTS.keys():
            SQL_HIGHSCORE[key] = 'SELECT p.discord_id, {} FROM players p, statistics s, missions m WHERE s.player_ucid = p.ucid AND p.discord_id <> -1 AND s.mission_id = m.id'.format(
                SQL_PARTS[key])
            if (server):
                SQL_HIGHSCORE[key] += ' AND m.server_name = \'{}\' '.format(server)
            if (period):
                SQL_HIGHSCORE[key] += ' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {}\')'.format(period)
            SQL_HIGHSCORE[key] += ' GROUP BY p.discord_id HAVING {} > 0 ORDER BY 2 DESC LIMIT 3'.format(SQL_PARTS[key])

        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                keys = list(SQL_PARTS.keys())
                for j in range(0, len(keys)):
                    type = keys[j]
                    cursor.execute(SQL_HIGHSCORE[type])
                    result = cursor.fetchall()
                    axis = plt.subplot2grid((4, 2), (1+int(j/2), j % 2), colspan=1, fig=figure)
                    labels = []
                    values = []
                    for i in range(0, 3):
                        if (len(result) > i):
                            member = ctx.message.guild.get_member(result[i][0])
                            name = member.display_name if (member is not None) else str(result[i][0])
                            labels.insert(0, name)
                            values.insert(0, result[i][1])
                    axis.barh(labels, values, color=COLORS, label=type, height=0.75)
                    axis.set_title(type, color='white', fontsize=25)
                    axis.set_xlabel(LABELS[type])
                    if (len(values) == 0):
                        axis.set_xticks([])
                        axis.set_yticks([])
                        axis.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Shows actual highscores', usage='[period]', aliases=['hs'])
    @commands.has_role('DCS')
    @commands.guild_only()
    async def highscore(self, ctx, period=None, server=None):
        if (period and period not in ['day', 'week', 'month']):
            await ctx.send('Period must be one of day/week/month!')
            return
        try:
            plt.style.use('dark_background')
            plt.rcParams['axes.facecolor'] = '2C2F33'
            figure = plt.figure(figsize=(15, 20))
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                executor.submit(self.draw_highscore_playtime, ctx=ctx, axis=plt.subplot2grid(
                    (4, 2), (0, 0), colspan=2, fig=figure), period=period, server=server)
                executor.submit(self.draw_highscore_kills, ctx=ctx, figure=figure, period=period, server=server)
            plt.subplots_adjust(hspace=0.5, wspace=0.5)
            title = 'Highscores'
            if (period):
                title += ' of the ' + string.capwords(period)
            if (server is not None):
                title += '\n_{}_'.format(server)
            else:
                title += '\n_- Overall -_'
            embed = discord.Embed(title=title, color=discord.Color.blue())
            filename = 'highscore.png'
            figure.savefig(filename, bbox_inches='tight', facecolor='#2C2F33')
            plt.close(figure)
            file = discord.File(filename)
            embed.set_image(url='attachment://' + filename)
            embed.set_footer(text='Click on the image to zoom in.')
            message = None
            try:
                with suppress(Exception):
                    message = await ctx.send(file=file, embed=embed)
                os.remove(filename)
                if (message):
                    await message.add_reaction('◀️')
                    await message.add_reaction('▶️')
                    react = await util.wait_for_single_reaction(self, ctx, message)
                    await message.delete()
                    if (server is None):
                        prev = self.servers[-1]
                        next = self.servers[0]
                    else:
                        i = 0
                        prev = next = None
                        for s in self.servers:
                            if (s == server):
                                break
                            i += 1
                        if (i < len(self.servers) - 1):
                            next = self.servers[i + 1]
                        if (i > 0):
                            prev = self.servers[i - 1]

                    if (react.emoji == '◀️'):
                        await self.highscore(ctx, period, prev)
                    elif (react.emoji == '▶️'):
                        await self.highscore(ctx, period, next)
            except asyncio.TimeoutError:
                await message.clear_reactions()
        except (Exception) as error:
            self.bot.log.exception(error)

    @commands.command(description='Shows servers statistics', usage='[period]')
    @commands.has_any_role('Admin', 'Moderator')
    @commands.guild_only()
    async def serverstats(self, ctx, period=None, server=None):
        SQL_USER_BASE = 'SELECT COUNT(DISTINCT ucid) AS dcs_users, COUNT(DISTINCT discord_id)-1 AS discord_users FROM players WHERE ban = false'
        SQL_SERVER_USAGE = 'SELECT trim(m.server_name) as server_name, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime, ROUND(AVG(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 60) AS avg FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND m.id = s.mission_id AND s.hop_off IS NOT NULL'
        SQL_TOP3_MISSION_UPTIMES = 'SELECT mission_name, ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(mission_end, NOW()) - mission_start))) / 3600) AS total, ROUND(AVG(EXTRACT(EPOCH FROM (COALESCE(mission_end, NOW()) - mission_start))) / 3600) AS avg FROM missions'
        SQL_TOP5_MISSIONS_USAGE = 'SELECT m.mission_name, COUNT(distinct s.player_ucid) AS players FROM missions m, statistics s WHERE s.mission_id = m.id'
        SQL_LAST_14DAYS = 'SELECT d.date AS date, COUNT(DISTINCT s.player_ucid) AS players FROM statistics s, missions m, generate_series(DATE(NOW()) - INTERVAL \'2 weeks\', DATE(NOW()), INTERVAL \'1 day\') d WHERE d.date BETWEEN DATE(s.hop_on) AND DATE(s.hop_off) AND s.mission_id = m.id'
        SQL_MAIN_TIMES = 'SELECT to_char(s.hop_on, \'ID\') as weekday, to_char(h.time, \'HH24\') AS hour, COUNT(DISTINCT s.player_ucid) AS players FROM statistics s, missions m, generate_series(TIMESTAMP \'01.01.1970 00:00:00\', TIMESTAMP \'01.01.1970 23:00:00\', INTERVAL \'1 hour\') h WHERE date_part(\'hour\', h.time) BETWEEN date_part(\'hour\', s.hop_on) AND date_part(\'hour\', s.hop_off) AND s.mission_id = m.id'

        embed = discord.Embed(color=discord.Color.blue())
        embed.title = 'Server Statistics'
        if (server):
            SQL_SERVER_USAGE += ' AND m.server_name = \'{}\' '.format(server)
            SQL_TOP3_MISSION_UPTIMES += ' WHERE server_name = \'{}\' '.format(server)
            SQL_TOP5_MISSIONS_USAGE += ' AND m.server_name = \'{}\' '.format(server)
            SQL_LAST_14DAYS += ' AND m.server_name = \'{}\' '.format(server)
            SQL_MAIN_TIMES += ' AND m.server_name = \'{}\' '.format(server)
        if (period):
            SQL_SERVER_USAGE += ' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {}\')'.format(period)
            SQL_TOP3_MISSION_UPTIMES += ' WHERE date(mission_start) > (DATE(NOW()) - interval \'1 {}\')'.format(period)
            SQL_TOP5_MISSIONS_USAGE += ' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {}\')'.format(period)
            SQL_MAIN_TIMES += ' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {}\')'.format(period)
            embed.title = string.capwords(period if period != 'day' else 'dai') + 'ly ' + embed.title
        else:
            embed.title = 'Overall ' + embed.title
        SQL_SERVER_USAGE += ' GROUP BY trim(m.server_name)'
        SQL_TOP3_MISSION_UPTIMES += ' GROUP BY mission_name ORDER BY 2 DESC LIMIT 3'
        SQL_TOP5_MISSIONS_USAGE += ' GROUP BY m.mission_name ORDER BY 2 DESC LIMIT 5'
        SQL_LAST_14DAYS += ' GROUP BY d.date'
        SQL_MAIN_TIMES += ' GROUP BY 1, 2'

        if (server):
            embed.title += '\n_{}_'.format(server)

        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(SQL_USER_BASE)
                row = cursor.fetchone()
                embed.add_field(name='Unique Users on Servers', value=str(row[0]))
                embed.add_field(name='Including Discord Members', value=str(row[1]))
                embed.add_field(name='_ _', value='_ _')
                # Server Usages
                servers = ''
                playtimes = ''
                avgs = ''
                cursor.execute(SQL_SERVER_USAGE)
                for row in cursor.fetchall():
                    servers += re.sub(self.bot.config['FILTER']['SERVER_FILTER'], '', row['server_name']).strip() + '\n'
                    playtimes += '{:.0f}\n'.format(row['playtime'])
                    avgs += '{:.0f}\n'.format(row['avg'])
                if (len(servers) > 0):
                    embed.add_field(name='Server', value=servers)
                    embed.add_field(name='Total Playtime (h)', value=playtimes)
                    embed.add_field(name='AVG Playtime (m)', value=avgs)
                # TOP 3 Missions (uptime / avg runtime)
                missions = ''
                totals = ''
                avgs = ''
                cursor.execute(SQL_TOP3_MISSION_UPTIMES)
                for row in cursor.fetchall():
                    missions += re.sub(self.bot.config['FILTER']['MISSION_FILTER'],
                                       ' ', row['mission_name']).strip()[:20] + '\n'
                    totals += '{:.0f}\n'.format(row['total'])
                    avgs += '{:.0f}\n'.format(row['avg'])
                if (len(missions) > 0):
                    embed.add_field(name='Mission (Top 3)', value=missions)
                    embed.add_field(name='Total Uptime (h)', value=totals)
                    embed.add_field(name='AVG Uptime (h)', value=avgs)
                # TOP 5 Missions by Playerbase
                missions = ''
                players = ''
                cursor.execute(SQL_TOP5_MISSIONS_USAGE)
                for row in cursor.fetchall():
                    missions += re.sub(self.bot.config['FILTER']['MISSION_FILTER'],
                                       ' ', row['mission_name']).strip()[:20] + '\n'
                    players += str(row['players']) + '\n'
                if (len(missions) > 0):
                    embed.add_field(name='Mission (Top 5)', value=missions)
                    embed.add_field(name='Unique Players', value=players)
                    embed.add_field(name='_ _', value='_ _')
                # Draw charts
                plt.style.use('dark_background')
                plt.rcParams['axes.facecolor'] = '2C2F33'
                figure = plt.figure(figsize=(15, 10))
                # Last 7 days
                axis = plt.subplot2grid((2, 1), (0, 0), colspan=1, fig=figure)
                labels = []
                values = []
                cursor.execute(SQL_LAST_14DAYS)
                for row in cursor.fetchall():
                    labels.append(row['date'].strftime('%a %m/%d'))
                    values.append(row['players'])
                axis.bar(labels, values, width=0.5, color='dodgerblue')
                axis.set_title('Unique Players past 14 Days', color='white', fontsize=25)
                axis.set_yticks([])
                for label in axis.get_xticklabels():
                    label.set_rotation(30)
                    label.set_ha('right')
                for i in range(0, len(values)):
                    axis.annotate(values[i], xy=(
                        labels[i], values[i]), ha='center', va='bottom', weight='bold')
                if (len(values) == 0):
                    axis.set_xticks([])
                    axis.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
                # Times & Days
                axis = plt.subplot2grid((2, 1), (1, 0), colspan=1, fig=figure)
                values = np.zeros((24, 7))
                cursor.execute(SQL_MAIN_TIMES)
                for row in cursor.fetchall():
                    values[int(row['hour'])][int(row['weekday'])-1] = row['players']
                axis.imshow(values, cmap='cividis', aspect='auto')
                axis.set_title('Users per Day/Time (UTC)', color='white', fontsize=25)
                # axis.invert_yaxis()
                axis.xaxis.set_major_formatter(FuncFormatter(lambda x, pos: self.WEEKDAYS[np.clip(x, 0, 6)]))
                plt.subplots_adjust(hspace=0.5, wspace=0.0)
                filename = 'serverstats.png'
                figure.savefig(filename, bbox_inches='tight', facecolor='#2C2F33')
                plt.close(figure)
                file = discord.File(filename)
                embed.set_image(url='attachment://' + filename)
                embed.set_footer(text='Click on the image to zoom in.')
                message = None
                try:
                    with suppress(Exception):
                        message = await ctx.send(file=file, embed=embed)
                    os.remove(filename)
                    if (message):
                        await message.add_reaction('◀️')
                        await message.add_reaction('▶️')
                        react = await util.wait_for_single_reaction(self, ctx, message)
                        await message.delete()
                        if (server is None):
                            prev = self.servers[-1]
                            next = self.servers[0]
                        else:
                            i = 0
                            prev = next = None
                            for s in self.servers:
                                if (s == server):
                                    break
                                i += 1
                            if (i < len(self.servers) - 1):
                                next = self.servers[i + 1]
                            if (i > 0):
                                prev = self.servers[i - 1]

                        if (react.emoji == '◀️'):
                            await self.serverstats(ctx, period, prev)
                        elif (react.emoji == '▶️'):
                            await self.serverstats(ctx, period, next)
                except asyncio.TimeoutError:
                    await message.clear_reactions()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)


def setup(bot):
    bot.add_cog(Statistics(bot))
