import discord
import json
import os
import psycopg

from core import Plugin, utils, command, get_translation
from discord import app_commands
from discord.ext import tasks
from services.bot import DCSServerBot

_ = get_translation(__name__.split('.')[1])


class DBExporter(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        os.makedirs('export', exist_ok=True)
        if self.get_config().get('autoexport', False):
            self.schedule.add_exception_type(psycopg.Error)
            self.schedule.start()

    async def cog_unload(self):
        if self.get_config().get('autoexport', False):
            self.schedule.cancel()
        await super().cog_unload()

    async def do_export(self, table_filter: list[str]):
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name not in ('pu_events_sdw', 'servers', 'message_persistence')
            """)
            for table in [x[0] async for x in cursor if x[0] not in table_filter]:
                cursor = await conn.execute(f'SELECT ROW_TO_JSON(t) FROM (SELECT * FROM {table}) t')
                if cursor.rowcount > 0:
                    with open(os.path.join('export', f'{table}.json'), mode='w', encoding='utf-8') as file:
                        file.writelines([json.dumps(x[0]) + '\n' async for x in cursor])

    @command(description=_('Exports database tables as json.'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def export(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        await self.do_export([])
        await interaction.delete_original_response()
        await interaction.followup.send(_('Database dumped to ./export'), ephemeral=ephemeral)

    @tasks.loop(hours=1.0)
    async def schedule(self):
        await self.do_export(self.get_config().get('tablefilter', []))


async def setup(bot: DCSServerBot):
    bot.log.warning(_("The DBExporter plugin is deprecated. Please use Backup or RestAPI instead."))
    await bot.add_cog(DBExporter(bot))
