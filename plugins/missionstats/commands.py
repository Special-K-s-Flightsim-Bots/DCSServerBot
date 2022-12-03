import discord
import psycopg2
from contextlib import closing
from core import DCSServerBot, Plugin, PluginRequiredError, utils, Report, PaginationReport, Status, Server
from discord.ext import commands
from plugins.userstats.commands import parse_params
from plugins.userstats.filter import StatisticsFilter, MissionStatisticsFilter
from typing import Optional, Union
from .listener import MissionStatisticsEventListener


class MissionStatisticsAgent(Plugin):
    @commands.command(description='Display Mission Statistics')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def missionstats(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            await ctx.send(f"Server {server.name} is not running.")
        elif server.name not in self.bot.mission_stats:
            await ctx.send("Mission statistics not initialized yet or not active for this server.")
        else:
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            stats = self.bot.mission_stats[server.name]
            report = Report(self.bot, self.plugin_name, 'missionstats.json')
            env = await report.render(stats=stats, mission_id=server.mission_id,
                                      sides=utils.get_sides(ctx.message, server))
            await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)


class MissionStatisticsMaster(MissionStatisticsAgent):

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None):
        self.log.debug('Pruning Missionstats ...')
        with closing(conn.cursor()) as cursor:
            if ucids:
                for ucid in ucids:
                    cursor.execute('DELETE FROM missionstats WHERE init_id = %s', (ucid,))
            elif days > 0:
                cursor.execute(f"DELETE FROM missionstats WHERE time < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Missionstats pruned.')

    @commands.command(description='Display statistics about sorties', usage='[user] [period]')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def sorties(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        try:
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            member, period = parse_params(self, ctx, member, *params)
            if not member:
                await ctx.send('No player found with that nickname.', delete_after=timeout if timeout > 0 else None)
                return
            flt = MissionStatisticsFilter()
            if period and not flt:
                await ctx.send('Please provide a valid period.')
                return
            if isinstance(member, str):
                ucid, member = self.bot.get_ucid_by_name(member)
            else:
                ucid = self.bot.get_ucid_by_member(member)
            report = Report(self.bot, self.plugin_name, 'sorties.json')
            env = await report.render(ucid=ucid,
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
        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
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
                    ucid = cursor.fetchone()[0] if cursor.rowcount > 0 else None
                else:
                    ucid, member = self.bot.get_ucid_by_name(member)
                if not ucid:
                    await ctx.send('This user is not linked correctly.')
                    return
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

    @commands.command(description='Refueling statistics', usage='[member] [period]')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def refuelings(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        try:
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            member, period = parse_params(self, ctx, member, *params)
            if not member:
                await ctx.send('No player found with that nickname.', delete_after=timeout if timeout > 0 else None)
                return
            flt = MissionStatisticsFilter()
            if period and not flt:
                await ctx.send('Please provide a valid period.')
                return
            if isinstance(member, str):
                ucid, member = self.bot.get_ucid_by_name(member)
            else:
                ucid = self.bot.get_ucid_by_member(member)
            report = Report(self.bot, self.plugin_name, 'refuelings.json')
            env = await report.render(ucid=ucid,
                                      member_name=member.display_name if isinstance(member, discord.Member) else member,
                                      period=period, flt=flt)
            await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)
        finally:
            await ctx.message.delete()


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(MissionStatisticsMaster(bot, MissionStatisticsEventListener))
    else:
        await bot.add_cog(MissionStatisticsAgent(bot, MissionStatisticsEventListener))
