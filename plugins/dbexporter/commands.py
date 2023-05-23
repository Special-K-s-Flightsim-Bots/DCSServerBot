import discord
import json
import os
from contextlib import closing
from core import Plugin, TEventListener, utils, command
from discord import app_commands
from discord.ext import tasks
from os import path
from services import DCSServerBot
from typing import Type


class DBExporter(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not path.exists('./export'):
            os.makedirs('./export')
        if 'config' in self.locals and 'autoexport' in self.locals['config'] and \
                self.locals['config']['autoexport'] is True:
            self.schedule.start()

    async def cog_unload(self):
        if 'config' in self.locals and 'autoexport' in self.locals['config'] and \
                self.locals['configs']['autoexport'] is True:
            self.schedule.cancel()
        await super().cog_unload()

    def do_export(self, table_filter: list[str]):
        with self.pool.connection() as conn:
            with conn.pipeline():
                with closing(conn.cursor()) as cursor:
                    for table in [x[0] for x in cursor.execute("""
                        SELECT table_name FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name not in ('pu_events_sdw', 'servers', 'message_persistence')
                    """).fetchall() if x[0] not in table_filter]:
                        cursor.execute(f'SELECT ROW_TO_JSON(t) FROM (SELECT * FROM {table}) t')
                        if cursor.rowcount > 0:
                            with open(f'export/{table}.json', 'w') as file:
                                file.writelines([json.dumps(x[0]) + '\n' for x in cursor.fetchall()])

    @command(description='Exports database tables as json.')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def export(self, interaction: discord.Interaction):
        await interaction.response.send_message("Exporting database tables ...")
        await interaction.response.defer(thinking=True, ephemeral=True)
        self.do_export([])
        await interaction.delete_original_response()
        await interaction.followup.send('Database dumped to ./export', ephemeral=True)

    @tasks.loop(hours=1.0)
    async def schedule(self):
        self.do_export(self.locals['config']['tablefilter'] if ('config' in self.locals and 'tablefilter' in self.locals['config']) else [])


async def setup(bot: DCSServerBot):
    await bot.add_cog(DBExporter(bot))
