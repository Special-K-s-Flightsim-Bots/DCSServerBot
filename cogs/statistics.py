# statistics.py
import discord
import matplotlib.pyplot as plt
import numpy as np
import string
import os
from contextlib import closing, suppress
from datetime import timedelta
from discord.ext import commands
from matplotlib.patches import ConnectionPatch
from matplotlib.ticker import FuncFormatter
from sqlite3 import Error


class Statistics(commands.Cog):

    SIDE_SPECTATOR = 0
    SIDE_RED = 1
    SIDE_BLUE = 2

    PLAYER_SIDES = {
        SIDE_SPECTATOR: 'Spectator',
        SIDE_RED: 'RED',
        SIDE_BLUE: 'BLUE'
    }

    def __init__(self, bot):
        self.bot = bot

    @commands.command(description='Links a member to a DCS user', usage='<member> <ucid>')
    @commands.has_any_role('Admin', 'Moderator')
    @commands.guild_only()
    async def link(self, ctx, member: discord.Member, ucid):
        try:
            with closing(self.bot.conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET discord_id = ? WHERE ucid = ?', (member.id, ucid))
                self.bot.conn.commit()
                await ctx.send('Member {} linked to ucid {}'.format(member.display_name, ucid))
        except (Exception, Error) as error:
            self.bot.conn.rollback()
            self.bot.log.exception(error)

    def draw_playtime_planes(self, member, axis):
        SQL_PLAYTIME = 'SELECT s.slot, ROUND(SUM(JULIANDAY(s.hop_off) - JULIANDAY(s.hop_on))*86400) AS playtime FROM statistics s, players p WHERE s.player_ucid = p.ucid AND p.discord_id = ? AND s.hop_off IS NOT NULL GROUP BY s.slot ORDER BY 2'
        with closing(self.bot.conn.cursor()) as cursor:
            result = cursor.execute(SQL_PLAYTIME, (member.id, )).fetchall()
            if (result is not None):
                labels = []
                values = []
                for row in result:
                    labels.insert(0, row['slot'])
                    values.insert(0, row['playtime'] / 3600.0)
                axis.bar(labels, values, width=0.5, color='lightskyblue')
                axis.set_title('Flighttime', color='white', fontsize=25)
                axis.set_ylabel('hours')
                # axis.set_yscale('log')
                axis.set_yticks([])
                for i in range(0, len(values)):
                    axis.annotate('{:.1f} h'.format(values[i]), xy=(
                        labels[i], values[i]), ha='center', va='bottom', weight='bold')
            else:
                axis.axis('off')

    def draw_server_time(self, member, axis):
        SQL_STATISTICS = 'SELECT m.server_name, ROUND(SUM(JULIANDAY(s.hop_off) - JULIANDAY(s.hop_on))*86400) AS playtime FROM statistics s, players p, missions m WHERE s.player_ucid = p.ucid AND p.discord_id = ? AND m.id = s.mission_id AND s.hop_off IS NOT NULL GROUP BY m.server_name'
        with closing(self.bot.conn.cursor()) as cursor:
            result = cursor.execute(SQL_STATISTICS, (member.id, )).fetchall()
            if (result is not None):
                def func(pct, allvals):
                    absolute = int(round(pct/100.*np.sum(allvals)))
                    return '{:.1f}%\n({:s}h)'.format(pct, str(timedelta(seconds=absolute)))

                labels = []
                values = []
                for row in result:
                    labels.insert(0, row['server_name'][17:])
                    values.insert(0, row['playtime'])
                patches, texts, pcts = axis.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                                wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
                plt.setp(pcts, color='black', fontweight='bold')
                axis.set_title('Server Time', color='white', fontsize=25)
                axis.axis('equal')
            else:
                axis.set_visible(False)

    def draw_flight_performance(self, member, axis):
        SQL_STATISTICS = 'SELECT SUM(ejections) as ejections, SUM(crashes) as crashes, ' \
            'SUM(takeoffs) as takeoffs, SUM(landings) as landings FROM statistics s, ' \
            'players p WHERE s.player_ucid = p.ucid AND p.discord_id = ?'
        with closing(self.bot.conn.cursor()) as cursor:
            result = cursor.execute(SQL_STATISTICS, (member.id, )).fetchone()
            if (result[0] is not None):
                def func(pct, allvals):
                    absolute = int(round(pct/100.*np.sum(allvals)))
                    return '{:.1f}%\n({:d})'.format(pct, absolute)

                labels = []
                values = []
                for item in dict(result).items():
                    if(item[1] is not None and item[1] > 0):
                        labels.append(string.capwords(item[0]))
                        values.append(item[1])
                patches, texts, pcts = axis.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                                wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
                plt.setp(pcts, color='black', fontweight='bold')
                axis.set_title('Flying', color='white', fontsize=25)
                axis.axis('equal')
            else:
                axis.set_visible(False)

    def draw_kill_performance(self, member, axis):
        SQL_STATISTICS = 'SELECT SUM(kills) as kills, SUM(deaths) as deaths, SUM(teamkills) as teamkills FROM statistics s, ' \
            'players p WHERE s.player_ucid = p.ucid AND p.discord_id = ?'
        with closing(self.bot.conn.cursor()) as cursor:
            result = cursor.execute(SQL_STATISTICS, (member.id, )).fetchone()
            retval = False
            if (result[0] is not None):
                def func(pct, allvals):
                    absolute = int(round(pct/100.*np.sum(allvals)))
                    return '{:.1f}%\n({:d})'.format(pct, absolute)

                labels = []
                values = []
                explode = []
                for item in dict(result).items():
                    if(item[1] is not None and item[1] > 0):
                        labels.append(string.capwords(item[0]))
                        values.append(item[1])
                        if (item[0] == 'kills'):
                            retval = True
                            explode.append(0.1)
                        else:
                            explode.append(0.0)
                angle = -180 * result[0]/np.sum(values)
                patches, texts, pcts = axis.pie(values, labels=labels, startangle=angle, explode=explode,
                                                autopct=lambda pct: func(pct, values),
                                                wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
                plt.setp(pcts, color='black', fontweight='bold')
                axis.set_title('Killing', color='white', fontsize=25)
                axis.axis('equal')
            else:
                axis.set_visible(False)
            return retval

    def draw_kill_types(self, member, axis):
        SQL_STATISTICS = 'SELECT SUM(kills_planes) as kills_planes, SUM(kills_helicopters) kills_helicopters, SUM(kills_ships) as kills_ships, ' \
            'SUM(kills_sams) as kills_air_defence, SUM(kills_ground) as kills_ground FROM statistics s, ' \
            'players p WHERE s.player_ucid = p.ucid AND p.discord_id = ?'
        with closing(self.bot.conn.cursor()) as cursor:
            result = cursor.execute(SQL_STATISTICS, (member.id, )).fetchone()
            if (result[0] is not None):
                def func(pct, allvals):
                    absolute = int(round(pct/100.*np.sum(allvals)))
                    return '{:.1f}%\n({:d})'.format(pct, absolute)

                labels = []
                values = []
                for item in dict(result).items():
                    if(item[1] is not None and item[1] > 0):
                        labels.append(string.capwords(item[0][6:], sep='_').replace('_', ' '))
                        values.append(item[1])
                xpos = 0
                bottom = 0
                width = .2
                for i in range(len(values)):
                    height = values[i]/np.sum(values)
                    axis.bar(xpos, height, width, bottom=bottom)
                    ypos = bottom + axis.patches[i].get_height() / 2
                    bottom += height
                    axis.text(xpos, ypos, "%d%%" % (axis.patches[i].get_height() * 100), ha='center', color='black')

                axis.set_title('Type of Kills', color='white', fontsize=25)
                axis.legend(labels)
                axis.axis('off')
                axis.set_xlim(- 2.5 * width, 2.5 * width)
            else:
                axis.set_visible(False)

    @commands.command(description='Shows player statistics', usage='[member]', aliases=['stats'])
    @commands.has_role('DCS')
    @commands.guild_only()
    async def statistics(self, ctx, member: discord.Member = None):
        try:
            if (member is None):
                member = ctx.message.author
            plt.style.use('dark_background')
            figure = plt.figure(figsize=(15, 20))
            self.draw_playtime_planes(member, plt.subplot2grid((3, 2), (0, 0), colspan=2, fig=figure))
            self.draw_server_time(member, plt.subplot2grid((3, 2), (1, 0), colspan=1, fig=figure))
            self.draw_flight_performance(member, plt.subplot2grid((3, 2), (1, 1), colspan=1, fig=figure))
            ax1 = plt.subplot2grid((3, 2), (2, 0), colspan=1, fig=figure)
            if (self.draw_kill_performance(member, ax1) is True):
                ax2 = plt.subplot2grid((3, 2), (2, 1), colspan=1, fig=figure)
                self.draw_kill_types(member, ax2)

                # use ConnectionPatch to draw lines between the two plots
                # get the wedge data
                theta1, theta2 = ax1.patches[0].theta1, ax1.patches[0].theta2
                center, r = ax1.patches[0].center, ax1.patches[0].r
                bar_height = sum([item.get_height() for item in ax2.patches])

                # draw top connecting line
                x = r * np.cos(np.pi / 180 * theta2) + center[0]
                y = r * np.sin(np.pi / 180 * theta2) + center[1]
                con = ConnectionPatch(xyA=(-0.2 / 2, bar_height), coordsA=ax2.transData,
                                      xyB=(x, y), coordsB=ax1.transData)
                con.set_color('lightgray')
                con.set_linewidth(2)
                con.set_linestyle('dashed')
                ax2.add_artist(con)

                # draw bottom connecting line
                x = r * np.cos(np.pi / 180 * theta1) + center[0]
                y = r * np.sin(np.pi / 180 * theta1) + center[1]
                con = ConnectionPatch(xyA=(-0.2 / 2, 0), coordsA=ax2.transData,
                                      xyB=(x, y), coordsB=ax1.transData)
                con.set_color('lightgray')
                con.set_linewidth(2)
                con.set_linestyle('dashed')
                ax2.add_artist(con)

            embed = discord.Embed(title='Statistics for {}'.format(member.display_name), color=discord.Color.blue())
            filename = '{}.png'.format(member.id)
            plt.savefig(filename, bbox_inches='tight', transparent=True)
            file = discord.File(filename)
            embed.set_image(url='attachment://' + filename)
            embed.set_footer(text='Click on the image to zoom in.')
            with suppress(Exception):
                await ctx.send(file=file, embed=embed)
            os.remove(filename)
        except (Exception, Error) as error:
            self.bot.log.exception(error)

    def draw_highscore_playtime(self, ctx, axis):
        SQL_HIGHSCORE_PLAYTIME = 'SELECT p.discord_id, ROUND(SUM(JULIANDAY(s.hop_off) - JULIANDAY(s.hop_on))*86400) AS playtime FROM statistics s, players p WHERE p.ucid = s.player_ucid AND s.hop_off IS NOT NULL GROUP BY p.discord_id ORDER BY 2 DESC LIMIT 3'
        with closing(self.bot.conn.cursor()) as cursor:
            result = cursor.execute(SQL_HIGHSCORE_PLAYTIME).fetchall()
            if (len(result) > 0):
                labels = []
                values = []
                for row in result:
                    labels.insert(0, ctx.message.guild.get_member(row[0]).display_name)
                    values.insert(0, row[1])
                axis.barh(labels, values, color=['#CD7F32', 'silver', 'gold'])
                axis.xaxis.set_major_formatter(FuncFormatter(lambda x, pos: str(timedelta(seconds=x))))
                axis.set_title('Players with longes playtimes', color='white', fontsize=25)
            else:
                axis.set_visible(False)

    def draw_highscore_kills(self, ctx, axis):
        SQL_HIGHSCORE_KILLS = 'SELECT p.discord_id, SUM(s.kills) FROM players p, statistics s WHERE s.player_ucid = p.ucid AND p.discord_id <> -1 GROUP BY p.discord_id ORDER BY 2 DESC LIMIT 3'
        with closing(self.bot.conn.cursor()) as cursor:
            result = cursor.execute(SQL_HIGHSCORE_KILLS).fetchall()
            if (len(result) > 0):
                labels = []
                values = []
                for row in result:
                    labels.insert(0, ctx.message.guild.get_member(row[0]).display_name)
                    values.insert(0, row[1])
                axis.barh(labels, values, color=['#CD7F32', 'silver', 'gold'])
                axis.set_title('Players with most kills', color='white', fontsize=25)
            else:
                axis.set_visible(False)

    @commands.command(description='Shows actual highscores', aliases=['hs'])
    @commands.has_role('DCS')
    @commands.guild_only()
    async def highscore(self, ctx):
        try:
            plt.style.use('dark_background')
            figure, axis = plt.subplots(2, 1, figsize=(10, 10))
            self.draw_highscore_playtime(ctx, axis[0])
            self.draw_highscore_kills(ctx, axis[1])
            embed = discord.Embed(title='Highscore', color=discord.Color.blue())
            filename = 'highscore.png'
            plt.savefig(filename, bbox_inches='tight', transparent=True)
            file = discord.File(filename)
            embed.set_image(url='attachment://' + filename)
            embed.set_footer(text='Click on the image to zoom in.')
            with suppress(Exception):
                await ctx.send(file=file, embed=embed)
            os.remove(filename)
        except (Exception, Error) as error:
            self.bot.log.exception(error)


def setup(bot):
    bot.add_cog(Statistics(bot))
