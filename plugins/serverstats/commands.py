import discord
import icmplib
import os
import platform
import psutil
import psycopg2
from contextlib import closing
from core import utils, Plugin, DCSServerBot, TEventListener, Status, PluginRequiredError, Report, PaginationReport, \
    Server
from discord.ext import tasks, commands
from typing import Type, Optional, Tuple
from .listener import ServerStatsListener


class AgentServerStats(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.cleanup.start()
        self.schedule.start()
        self.io_counters = {}
        self.net_io_counters = None

    async def cog_unload(self):
        self.cleanup.cancel()
        self.schedule.cancel()
        await super().cog_unload()

    def rename(self, old_name: str, new_name: str):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE serverstats SET server_name = %s WHERE server_name = %s',
                               (new_name, old_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @staticmethod
    def get_params(*params) -> Tuple[bool, Optional[str]]:
        is_all = False
        period = None
        if len(params):
            for i in range(0, len(params)):
                if params[i] in ['hour', 'day', 'week', 'month', 'year']:
                    period = params[i]
                elif params[i].lower() == '-all':
                    is_all = True
        return is_all, period

    async def display_report(self, ctx, schema: str, period: str, server_name: str):
        report = Report(self.bot, self.plugin_name, schema)
        env = await report.render(period=period, server_name=server_name, agent_host=platform.node())
        file = discord.File(env.filename) if env.filename else None
        await ctx.send(embed=env.embed, file=file)
        if env.filename:
            os.remove(env.filename)

    @commands.command(description='Shows servers load', usage='[period]')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def serverload(self, ctx, *params):
        is_all, period = self.get_params(*params)
        server: Server = await self.bot.get_server(ctx)
        if not is_all and server:
            await self.display_report(ctx, 'serverload.json', period, server.name)

    @commands.command(description='Shows servers statistics', usage='[period]')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def serverstats(self, ctx, *params):
        is_all, period = self.get_params(*params)
        server: Server = await self.bot.get_server(ctx)
        if not is_all and server:
            await self.display_report(ctx, 'serverstats.json', period, server.name)

    @tasks.loop(minutes=1.0)
    async def schedule(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                for server_name, server in self.bot.servers.items():
                    if server.status not in [Status.RUNNING, Status.PAUSED]:
                        continue
                    users = len(server.get_active_players())
                    if not server.process or not server.process.is_running():
                        for exe in ['DCS_server.exe', 'DCS.exe']:
                            server.process = utils.find_process(exe, server.installation)
                            if server.process:
                                break
                        else:
                            self.log.warning(f"Could not find a running DCS instance for server {server_name}, "
                                             f"skipping server load gathering.")
                            continue
                    cpu = server.process.cpu_percent()
                    memory = server.process.memory_full_info()
                    io_counters = server.process.io_counters()
                    if server.process.pid not in self.io_counters:
                        write_bytes = read_bytes = 0
                    else:
                        write_bytes = io_counters.write_bytes - self.io_counters[server.process.pid].write_bytes
                        read_bytes = io_counters.read_bytes - self.io_counters[server.process.pid].read_bytes
                    self.io_counters[server.process.pid] = io_counters
                    net_io_counters = psutil.net_io_counters(pernic=False)
                    if not self.net_io_counters:
                        bytes_sent = bytes_recv = 0
                    else:
                        bytes_sent = int((net_io_counters.bytes_sent - self.net_io_counters.bytes_sent) / 7200)
                        bytes_recv = int((net_io_counters.bytes_recv - self.net_io_counters.bytes_recv) / 7200)
                    self.net_io_counters = net_io_counters
                    if self.bot.config.getboolean('BOT', 'PING_MONITORING'):
                        net_ping = icmplib.ping('1.1.1.1', count=1, privileged=False)
                        if not net_ping.packets_received:
                            ping = None
                        else:
                            ping = net_ping.avg_rtt
                    else:
                        ping = None
                    if server_name in self.eventlistener.fps:
                        cursor.execute('INSERT INTO serverstats (server_name, agent_host, mission_id, users, status, '
                                       'cpu, mem_total, mem_ram, read_bytes, write_bytes, bytes_sent, bytes_recv, '
                                       'fps, ping) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                                       (server_name, platform.node(), server.mission_id, users, server.status.name, cpu,
                                        memory.private, memory.rss, read_bytes, write_bytes, bytes_sent, bytes_recv,
                                        self.eventlistener.fps[server_name], ping))
                        conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @tasks.loop(hours=12.0)
    async def cleanup(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("DELETE FROM serverstats WHERE time < (CURRENT_TIMESTAMP - interval '1 month')")
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class MasterServerStats(AgentServerStats):

    @commands.command(description='Shows servers load', usage='[period]')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def serverload(self, ctx, *params):
        is_all, period = self.get_params(*params)
        server: Server = await self.bot.get_server(ctx)
        if not is_all:
            if server:
                await self.display_report(ctx, 'serverload.json', period, server.name)
        else:
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'serverload.json')
            await report.render(period=period, server_name=None)

    @commands.command(description='Shows servers statistics', usage='[period]')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def serverstats(self, ctx, *params):
        is_all, period = self.get_params(*params)
        server: Server = await self.bot.get_server(ctx)
        if not is_all:
            if server:
                await self.display_report(ctx, 'serverstats.json', period, server.name)
        else:
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'serverstats.json')
            await report.render(period=period, server_name=None)


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(MasterServerStats(bot, ServerStatsListener))
    else:
        await bot.add_cog(AgentServerStats(bot, ServerStatsListener))
