import asyncio
import discord
import math
import os
import platform
import psutil
import psycopg
from core import utils, Plugin, TEventListener, Status, PluginRequiredError, Report, PaginationReport, Server, command
from discord import app_commands
from discord.ext import tasks
from services import DCSServerBot
from typing import Type, Optional
from .listener import ServerStatsListener


class ServerStats(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.cleanup.add_exception_type(psycopg.DatabaseError)
        self.cleanup.start()
        self.schedule.add_exception_type(psycopg.DatabaseError)
        self.schedule.start()
        self.io_counters = {}
        self.net_io_counters = None

    async def cog_unload(self):
        self.cleanup.cancel()
        self.schedule.cancel()
        await super().cog_unload()

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str):
        conn.execute('UPDATE serverstats SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    async def display_report(self, interaction: discord.Interaction, schema: str, period: str, server_name: str):
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, schema)
        env = await report.render(period=period, server_name=server_name, node=platform.node())
        file = discord.File(env.filename) if env.filename else None
        await interaction.followup.send(embed=env.embed, file=file, ephemeral=True)
        if env.filename and os.path.exists(env.filename):
            await asyncio.to_thread(os.remove, env.filename)

    @command(description='Displays the load of your DCS servers')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def serverload(self, interaction: discord.Interaction,
                         server: Optional[app_commands.Transform[Server, utils.ServerTransformer]],
                         period: Optional[str]):
        if server:
            await self.display_report(interaction, 'serverload.json', period, server.name)
        else:
            report = PaginationReport(self.bot, interaction, self.plugin_name, 'serverload.json')
            await report.render(period=period, server_name=None)

    @command(description='Shows servers statistics')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def serverstats(self, interaction: discord.Interaction,
                          server: Optional[app_commands.Transform[Server, utils.ServerTransformer]],
                          period: Optional[str]):
        if server:
            await self.display_report(interaction, 'serverstats.json', period, server.name)
        else:
            report = PaginationReport(self.bot, interaction, self.plugin_name, 'serverstats.json')
            await report.render(period=period, server_name=None)

    @tasks.loop(minutes=1.0)
    async def schedule(self):
        for server_name, server in self.bot.servers.items():
            # TODO: support remote servers
            if server.is_remote or server.status not in [Status.RUNNING, Status.PAUSED]:
                continue
            users = len(server.get_active_players())
            if not server.process or not server.process.is_running():
                for exe in ['DCS_server.exe', 'DCS.exe']:
                    server.process = utils.find_process(exe, server.instance.name)
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
            ping = (self.bot.latency * 1000) if not math.isinf(self.bot.latency) else -1
            if server_name in self.eventlistener.fps:
                with self.pool.connection() as conn:
                    with conn.transaction():
                        conn.execute("""
                        INSERT INTO serverstats (server_name, node, mission_id, users, status, cpu, mem_total, 
                                                 mem_ram, read_bytes, write_bytes, bytes_sent, bytes_recv, fps, ping) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (server_name, platform.node(), server.mission_id, users, server.status.name, cpu,
                          memory.private, memory.rss, read_bytes, write_bytes, bytes_sent, bytes_recv,
                          self.eventlistener.fps[server_name], ping))

    @tasks.loop(hours=12.0)
    async def cleanup(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM serverstats WHERE time < (CURRENT_TIMESTAMP - interval '1 month')")


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    await bot.add_cog(ServerStats(bot, ServerStatsListener))
