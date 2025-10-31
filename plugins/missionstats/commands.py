from datetime import datetime, timedelta
from io import BytesIO

import discord
import pandas as pd
import psycopg
from openpyxl.utils import get_column_letter
from psycopg.rows import dict_row

from core import Plugin, PluginRequiredError, utils, Report, Status, Server, command, get_translation
from discord import app_commands
from plugins.userstats.filter import StatisticsFilter, MissionStatisticsFilter, PeriodTransformer, PeriodFilter, \
    CampaignFilter, MissionFilter
from services.bot import DCSServerBot

from .listener import MissionStatisticsEventListener

_ = get_translation(__name__.split('.')[1])


async def player_modules_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:

    async def get_modules(ucid: str) -> list[str]:
        async with interaction.client.apool.connection() as conn:
            return [row[0] async for row in await conn.execute("""
                SELECT DISTINCT slot, usage FROM mv_statistics 
                WHERE player_ucid =  %s 
                AND slot NOT IN ('', '?', '''forward_observer', 'instructor', 'observer', 'artillery_commander') 
                ORDER BY 2 DESC
            """, (ucid, ))]

    try:
        user = await utils.UserTransformer().transform(interaction, utils.get_interaction_param(interaction, "user"))
        if not user:
            return []
        if isinstance(user, str):
            ucid = user
        else:
            ucid = await interaction.client.get_ucid_by_member(user)
        if not ucid:
            return []
        ret = [
            app_commands.Choice(name=x, value=x)
            for x in await get_modules(ucid)
            if not current or current.casefold() in x.casefold()
        ]
        return ret[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


class MissionStatistics(Plugin[MissionStatisticsEventListener]):

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: str | None = None) -> None:
        self.log.debug('Pruning Missionstats ...')
        if ucids:
            for ucid in ucids:
                await conn.execute('DELETE FROM missionstats WHERE init_id = %s', (ucid,))
        elif days > -1:
            await conn.execute("DELETE FROM missionstats WHERE time < (DATE(NOW()) - %s::interval)", (f'{days} days', ))
        if server:
            await conn.execute("""
                DELETE FROM missionstats WHERE mission_id in (
                    SELECT id FROM missions WHERE server_name = %s
                )
            """, (server, ))
            await conn.execute("""
                DELETE FROM missionstats WHERE mission_id NOT IN (
                    SELECT id FROM missions
                )
            """)
        self.log.debug('Missionstats pruned.')

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        await conn.execute("UPDATE missionstats SET init_id = %s WHERE init_id = %s", (new_ucid, old_ucid))
        await conn.execute("UPDATE missionstats SET target_id = %s WHERE target_id = %s", (new_ucid, old_ucid))

    @command(description=_('Display Mission Statistics'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def missionstats(self, interaction: discord.Interaction,
                           server: app_commands.Transform[Server, utils.ServerTransformer(
                               status=[Status.RUNNING, Status.PAUSED])]):
        stats = self.eventlistener.mission_stats.get(server.name)
        if not stats:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Mission statistics not initialized yet or not active for this server."), ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'missionstats.json')
        env = await report.render(stats=stats, mission_id=server.mission_id,
                                  sides=utils.get_sides(interaction.client, interaction, server))
        await interaction.followup.send(embed=env.embed, ephemeral=utils.get_ephemeral(interaction))

    @command(description=_('Display statistics about sorties'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def sorties(self, interaction: discord.Interaction,
                      user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None = None,
                      period: app_commands.Transform[
                                  StatisticsFilter,
                                  PeriodTransformer(flt=[MissionStatisticsFilter])
                              ] | None = MissionStatisticsFilter()):
        if not user:
            user = interaction.user
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'sorties.json')
        env = await report.render(ucid=ucid, member_name=name, flt=period)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_('Module statistics'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(module=player_modules_autocomplete)
    async def modulestats(self, interaction: discord.Interaction,
                          user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None = None,
                          module: str | None = None,
                          period: app_commands.Transform[
                              StatisticsFilter, PeriodTransformer(
                                  flt=[PeriodFilter, CampaignFilter, MissionFilter]
                              )] | None = PeriodFilter()):
        if not user:
            user = interaction.user
        if not module:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('You need to chose a module!'), ephemeral=True)
            return
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'modulestats.json')
        env = await report.render(member_name=name, ucid=ucid, module=module, flt=period)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_('Refueling statistics'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def refuelings(self, interaction: discord.Interaction,
                         user: app_commands.Transform[str |  discord.Member, utils.UserTransformer] | None = None,
                         period: app_commands.Transform[
                             StatisticsFilter,
                             PeriodTransformer(flt=[MissionStatisticsFilter])] | None = MissionStatisticsFilter()):
        if not user:
            user = interaction.user
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'refuelings.json')
        env = await report.render(ucid=ucid, member_name=name, flt=period)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_('Find who killed you most'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def nemesis(self, interaction: discord.Interaction,
                      user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None = None):
        if not user:
            user = interaction.user
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'nemesis.json')
        env = await report.render(ucid=ucid, member_name=name)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_("Find who you've killed the most"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def antagonist(self, interaction: discord.Interaction,
                         user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None = None):
        if not user:
            user = interaction.user
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'antagonist.json')
        env = await report.render(ucid=ucid, member_name=name)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_('Event History'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(start=_('Date in format YYYY-MM-DD, default: now - 30d'),
                           end=_('Date in format YYYY-MM-DD, default: now'))
    @app_commands.autocomplete(start=utils.date_autocomplete)
    @app_commands.autocomplete(end=utils.date_autocomplete)
    async def history(self, interaction: discord.Interaction,
                      user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None = None,
                      start: str | None = None, end: str | None = None):
        if isinstance(user, str):
            ucid = user
        elif not user:
            ucid = await self.bot.get_ucid_by_member(interaction.user)
        else:
            ucid = await self.bot.get_ucid_by_member(user)

        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Use {} to link your account.").format(
                (await utils.get_command(self.bot, name='linkme')).mention
            ), ephemeral=True)
            return

        start = datetime.strptime(start, '%Y-%m-%d') if start else (datetime.now() - timedelta(days=30)).date()
        end = datetime.strptime(end, '%Y-%m-%d') if end else datetime.now()

        ephemeral = not utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        async with interaction.client.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT m.time, m.event, 
                           COALESCE(p1.name, 'AI') AS init_name, m.init_id, 
                           CASE WHEN m.init_side::integer = 1 THEN 'red' ELSE 'blue' END AS init_side,
                           m.init_type, m.init_cat,
                           COALESCE(p2.name, 'AI / None') AS target_name, m.target_id,   
                           CASE WHEN m.target_side::integer = 1 THEN 'red' ELSE 'blue' END AS target_side,
                           m.target_type, m.target_cat,
                           m.weapon, m.place, m.comment
                    FROM missionstats m 
                    LEFT OUTER JOIN players p1 ON m.init_id = p1.ucid
                    LEFT OUTER JOIN players p2 ON m.target_id = p2.ucid
                    WHERE m.init_id = %(ucid)s or m.target_id = %(ucid)s
                    AND m.time BETWEEN %(start)s AND %(end)s
                    ORDER BY m.time DESC
                """, {"ucid": ucid, "start": start, "end": end})
                events_df = pd.DataFrame(await cursor.fetchall())

        if events_df.empty:
            await interaction.followup.send(_('No events found for this player in this timeframe.'),
                                            ephemeral=ephemeral)
            return

        # Create an in-memory binary stream
        excel_binary = BytesIO()

        # Define the desired column order
        columns_order = [
            'time',
            'event',
            'init_name',
            'init_id',
            'init_side',
            'init_type',
            'init_cat',
            'target_name',
            'target_id',
            'target_side',
            'target_type',
            'target_cat',
            'weapon',
            'place',
            'comment'
        ]

        # Write only the specified columns in the desired order
        existing_columns = [col for col in columns_order if col in events_df.columns]

        with pd.ExcelWriter(excel_binary, engine='openpyxl') as writer:
            events_df[existing_columns].to_excel(writer, sheet_name='Events', index=False)

            # Get the worksheet
            worksheet = writer.sheets['Events']

            for column in worksheet.columns:
                max_length = 0
                column_letter = get_column_letter(column[0].column)

                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass

                # Setting width with some padding
                adjusted_width = max_length + 2
                worksheet.column_dimensions[column_letter].width = adjusted_width

            # Add AutoFilter
            worksheet.auto_filter.ref = worksheet.dimensions

        excel_binary.seek(0)
        try:
            await interaction.followup.send(file=discord.File(excel_binary, filename=f'history-{ucid}.xlsx'),
                                            ephemeral=ephemeral)
        finally:
            excel_binary.close()


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    await bot.add_cog(MissionStatistics(bot, MissionStatisticsEventListener))
