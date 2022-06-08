import discord
import psycopg2
from contextlib import closing
from core import DCSServerBot, Plugin, PluginRequiredError, utils, Report, PaginationReport
from core.const import Status
from discord.ext import commands
from plugins.userstats.commands import parse_params
from plugins.userstats.filter import StatisticsFilter
from typing import Optional, Union
from .listener import MissionStatisticsEventListener


class MissionStatisticsAgent(Plugin):
    @commands.command(description='Display Mission Statistics')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def missionstats(self, ctx):
        server = await utils.get_server(self, ctx)
        if not server:
            return
        mission_id = server['mission_id'] if 'mission_id' in server else -1
        if server['status'] not in [Status.RUNNING, Status.PAUSED]:
            await ctx.send(f"Server {server['server_name']} is not running.")
        elif server['server_name'] not in self.bot.mission_stats:
            await ctx.send("Mission statistics not initialized yet or not active for this server.")
        else:
            timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
            stats = self.bot.mission_stats[server['server_name']]
            report = Report(self.bot, self.plugin_name, 'missionstats.json')
            env = await report.render(stats=stats, mission_id=mission_id, sides=utils.get_sides(ctx.message, server))
            await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)


class MissionStatisticsMaster(MissionStatisticsAgent):

    @commands.command(description='Display statistics about sorties', usage='[user] [period]')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def sorties(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        try:
            timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
            member, period = parse_params(self, ctx, member, *params)
            if not member:
                await ctx.send('No player found with that nickname.', delete_after=timeout if timeout > 0 else None)
                return
            flt = StatisticsFilter.detect(self.bot, period)
            if period and not flt:
                await ctx.send('Please provide a valid period or campaign name.')
                return
            report = Report(self.bot, self.plugin_name, 'sorties.json')
            env = await report.render(member=member if isinstance(member, discord.Member) else utils.get_ucid_by_name(self, member),
                                      member_name=member.display_name if isinstance(member, discord.Member) else member,
                                      period=period, flt=flt)
            await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)
        finally:
            await ctx.message.delete()

    @staticmethod
    def format_modules(data, marker, marker_emoji):
        embed = discord.Embed(title=f"Select a module from the list", color=discord.Color.blue())
        ids = modules = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            modules += f"{data[i]}\n"
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Module', value=modules)
        embed.add_field(name='_ _', value='_ _')
        embed.set_footer(text='Press a number to display detailed stats about that specific module.')
        return embed

    @commands.command(description='Module statistics', usage='[user] [period]', aliases=['modstats'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def modulestats(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
        member, period = parse_params(self, ctx, member, *params)
        if not member:
            await ctx.send('No player found with that nickname.', delete_after=timeout if timeout > 0 else None)
            return
        flt = StatisticsFilter.detect(self.bot, period)
        if period and not flt:
            await ctx.send('Please provide a valid period or campaign name.')
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(member, discord.Member):
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s ORDER BY last_seen DESC LIMIT 1',
                                   (member.id, ))
                    ucid = cursor.fetchone()[0]
                else:
                    ucid = utils.get_ucid_by_name(self, member)
                cursor.execute("SELECT DISTINCT slot, COUNT(*) FROM statistics WHERE player_ucid =  %s AND slot NOT "
                               "IN ('forward_observer', 'instructor', 'observer', 'artillery_commander') GROUP BY 1 "
                               "ORDER BY 2 DESC", (ucid, ))
                if cursor.rowcount == 0:
                    await ctx.send('No statistics found for this user.', delete_after=timeout if timeout > 0 else None)
                    return
                modules = [row[0] for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        await ctx.message.delete()
        n = await utils.selection_list(self, ctx, modules, self.format_modules)
        if n != -1:
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'modulestats.json', timeout if timeout > 0 else None)
            await report.render(member_name=member.display_name if isinstance(member, discord.Member) else member,
                                ucid=ucid, period=period, start_index=n, modules=modules, flt=flt)

    @commands.command(description='Refuelling statistics', usage='[member] [period]')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def refuellings(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        try:
            timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
            member, period = parse_params(self, ctx, member, *params)
            if not member:
                await ctx.send('No player found with that nickname.', delete_after=timeout if timeout > 0 else None)
                return
            flt = StatisticsFilter.detect(self.bot, period)
            if period and not flt:
                await ctx.send('Please provide a valid period or campaign name.')
                return
            report = Report(self.bot, self.plugin_name, 'refuellings.json')
            env = await report.render(member=member if isinstance(member, discord.Member) else utils.get_ucid_by_name(self, member),
                                      member_name=member.display_name if isinstance(member, discord.Member) else member,
                                      period=period, flt=flt)
            await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)
        finally:
            await ctx.message.delete()


def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(MissionStatisticsMaster(bot, MissionStatisticsEventListener))
    else:
        bot.add_cog(MissionStatisticsAgent(bot, MissionStatisticsEventListener))
