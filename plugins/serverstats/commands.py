import asyncio
import discord
import os
import platform
import psycopg
from core import utils, Plugin, TEventListener, PluginRequiredError, Report, PaginationReport, Server, command
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
        self.io_counters = {}
        self.net_io_counters = None

    async def cog_unload(self):
        self.cleanup.cancel()
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
    @app_commands.rename(_server="server")
    async def serverload(self, interaction: discord.Interaction,
                         _server: Optional[app_commands.Transform[Server, utils.ServerTransformer]],
                         period: Optional[str]):
        if _server:
            await self.display_report(interaction, 'serverload.json', period, _server.name)
        else:
            report = PaginationReport(self.bot, interaction, self.plugin_name, 'serverload.json')
            await report.render(period=period, server_name=None)

    @command(description='Shows servers statistics')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.rename(_server="server")
    async def serverstats(self, interaction: discord.Interaction,
                          _server: Optional[app_commands.Transform[Server, utils.ServerTransformer]],
                          period: Optional[str]):
        if _server:
            await self.display_report(interaction, 'serverstats.json', period, _server.name)
        else:
            report = PaginationReport(self.bot, interaction, self.plugin_name, 'serverstats.json')
            await report.render(period=period, server_name=None)

    @tasks.loop(hours=12.0)
    async def cleanup(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM serverstats WHERE time < (CURRENT_TIMESTAMP - interval '1 month')")


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    await bot.add_cog(ServerStats(bot, ServerStatsListener))
