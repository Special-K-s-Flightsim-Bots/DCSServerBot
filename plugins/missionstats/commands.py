import asyncio
import discord
import pandas as pd
import psycopg

from core import Plugin, PluginRequiredError, utils, Report, Status, Server, command, get_translation
from datetime import datetime, timedelta
from discord import app_commands
from io import BytesIO
from openpyxl.utils import get_column_letter
from plugins.userstats.filter import StatisticsFilter, MissionStatisticsFilter, PeriodTransformer, PeriodFilter, \
    CampaignFilter, MissionFilter
from psycopg.rows import dict_row
from services.bot import DCSServerBot

from .listener import MissionStatisticsEventListener

_ = get_translation(__name__.split('.')[1])


async def player_modules_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:

    async def get_modules(ucid: str, current: str | None = None) -> list[str]:
        if current:
            where = "AND slot ILIKE %(current)s"
        else:
            where = ''

        query = f"""
            SELECT DISTINCT slot FROM mv_statistics 
            WHERE player_ucid = %(ucid)s 
            AND slot NOT IN ('', '?', '''forward_observer', 'instructor', 'observer', 'artillery_commander')
            {where}
            LIMIT 25
        """

        async with interaction.client.apool.connection() as conn:
            return [
                row[0] async for row in await conn.execute(
                    query, {"ucid": ucid, "current": f"%{current}%" if current else None}
                )
            ]

    try:
        user = await utils.UserTransformer().transform(interaction, interaction.namespace.user)
        if not user:
            return []
        if isinstance(user, str):
            ucid = user
        else:
            ucid = await interaction.client.get_ucid_by_member(user)
        if not ucid:
            return []
        return [
            app_commands.Choice[str](name=x, value=x)
            for x in await get_modules(ucid, current)
        ]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


class MissionStatistics(Plugin[MissionStatisticsEventListener]):

    async def migrate(self, new_version: str, conn: psycopg.AsyncConnection | None = None) -> None:
        if new_version == '3.5':
            asyncio.create_task(self.generate_missing_shots())

    async def merge_shots_hits(self, shots: dict[str, list[dict]], hits: dict[str, list[dict]]) -> list[dict]:
        updated_shots = []
        for init_id, player_shots in shots.items():
            player_hits = hits.get(init_id, [])
            if not player_hits:
                continue

            # 1. Filter out pre-matched events
            unmatched_shots = []
            for shot in player_shots:
                if shot.get('target_type'):
                    # Remove the corresponding hit from consideration
                    for i, hit in enumerate(player_hits):
                        if (
                                hit.get('target_id') == shot['target_id']
                                and hit.get('weapon') == shot.get('weapon')
                                and hit['id'] > shot['id']
                        ):
                            player_hits.pop(i)
                            break
                else:
                    unmatched_shots.append(shot)

            if not unmatched_shots or not player_hits:
                continue

            # 2. Match remaining shots
            for shot in unmatched_shots:
                # Filter hits by weapon and time (hit must be after the shot)
                valid_hits = [
                    h for h in player_hits
                    if (
                            h.get('weapon') == shot.get('weapon')
                            and h.get('target_cat') in ['Airplanes', 'Helicopters']
                            and h['id'] > shot['id']
                    )
                ]

                if not valid_hits:
                    continue

                # Preference logic: Try 'Airplanes' or 'Helicopters' first
                preferred_hits = [h for h in valid_hits if h.get('target_cat') in ['Airplanes', 'Helicopters']]
                candidates = preferred_hits if preferred_hits else valid_hits

                # Select the hit with the smallest time difference
                best_hit = min(candidates, key=lambda h: (h['time'] - shot['time']).total_seconds())

                shot.update({
                    'target_id': best_hit['target_id'],
                    'target_side': best_hit['target_side'],
                    'target_type': best_hit['target_type'],
                    'target_cat': best_hit['target_cat']
                })
                updated_shots.append(shot)
                player_hits.remove(best_hit)

        return updated_shots

    async def generate_missing_shots(self, init_update: bool = True):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                if init_update:
                    self.log.info("MissionStats: Marking stale SHOT events ...")
                    async with conn.transaction():
                        await conn.execute("""
                            UPDATE missionstats 
                            SET comment = 'unsupported' 
                            WHERE event = 'S_EVENT_SHOT'
                            AND target_type IS NULL
                            AND weapon_id IS NULL
                        """)
                await cursor.execute("""
                    SELECT id 
                    FROM missions 
                    ORDER BY id
                """)
                missions = [x['id'] for x in await cursor.fetchall()]

        async def _fix_mission_shots(mission: int):
            async with self.apool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cursor:
                    shots = {}
                    hits = {}
                    async for row in await cursor.execute("""
                        SELECT * FROM missionstats 
                        WHERE mission_id = %s 
                          AND init_id IS NOT NULL
                          AND event IN ('S_EVENT_SHOT', 'S_EVENT_HIT')
                          AND (weapon LIKE 'AIM%%' OR weapon LIKE 'P\_%%' ESCAPE '\' 
                               OR weapon LIKE 'PL-%%' OR weapon ILIKE 'R-%%' OR weapon = 'SD-10')
                          AND weapon_id IS NULL
                        ORDER BY ID
                    """, (mission, )):
                        init_id = row['init_id']
                        if row['event'] == 'S_EVENT_SHOT':
                            if init_id not in shots:
                                shots[init_id] = []
                            shots[init_id].append(row.copy())
                        elif row['event'] == 'S_EVENT_HIT':
                            if init_id not in hits:
                                hits[init_id] = []
                            hits[init_id].append(row.copy())

                    if not hits:
                        return
                    updated_shots = await self.merge_shots_hits(shots, hits)
                    if not updated_shots:
                        return

                    self.log.info(f"MissionStats: Fixing {len(updated_shots)} shot events for mission {mission} ...")
                    async with conn.transaction():
                        for shot in updated_shots:
                            await conn.execute("""
                                UPDATE missionstats 
                                   SET target_id = %(target_id)s, 
                                       target_side = %(target_side)s, 
                                       target_type = %(target_type)s, 
                                       target_cat = %(target_cat)s
                                WHERE id = %(id)s 
                            """, shot)

        semaphore = asyncio.Semaphore(5)

        async def semaphore_wrapper(mission_id):
            async with semaphore:
                await _fix_mission_shots(mission_id)

        self.log.info(f"MissionStats: Fixing stale shot events. This can take a while ...")
        await asyncio.gather(*[semaphore_wrapper(m) for m in missions])
        self.log.info(f"MissionStats: Stale shot events fixed.")

    async def prune(self, conn: psycopg.AsyncConnection, days: int) -> None:
        self.log.debug('Pruning Missionstats ...')
        await conn.execute("DELETE FROM missionstats WHERE time < (DATE(NOW()) - %s::interval)",
                           (f'{days} days', ))
        self.log.debug('Missionstats pruned.')

#    @command(description=_('Fix stale SHOT events'))
#    @app_commands.guild_only()
#    @utils.app_has_role('Admin')
#    async def fix_shots(self, interaction: discord.Interaction, init_update: bool = False):
#        await interaction.response.defer(ephemeral=True)
#        await interaction.followup.send("Fixing stale shot events. This can take a while. Check the bot log for progress.")
#        await self.generate_missing_shots(init_update)
#        await interaction.followup.send("Shot events fixed.")

    @command(description=_('Display Mission Statistics'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def missionstats(self, interaction: discord.Interaction,
                           server: app_commands.Transform[Server, utils.ServerTransformer(
                               status=[Status.RUNNING, Status.PAUSED])]):
        stats = self.eventlistener.mission_stats.get(server.name)
        if not stats:
            await interaction.response.send_message(
                _("Mission statistics not initialized yet or not active for this server."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'missionstats.json')
        env = await report.render(stats=stats, mission_id=server.mission_id,
                                  sides=utils.get_sides(interaction.client, interaction, server),
                                  title='Mission Statistics')
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
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'sorties.json')
        env = await report.render(ucid=ucid, member_name=name, flt=period)
        try:
            file = discord.File(fp=env.buffer, filename=env.filename)
            await interaction.followup.send(embed=env.embed, file=file, ephemeral=True)
        finally:
            if env.buffer:
                env.buffer.close()

    @command(description=_('Generate an After Action Report'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(user=_("Report user, default is caller"))
    @app_commands.describe(period=_("Time period, default is quarter"))
    async def aar(self, interaction: discord.Interaction,
                  user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None = None,
                  period: app_commands.Transform[
                              StatisticsFilter,
                              PeriodTransformer(flt=[MissionStatisticsFilter])
                          ] | None = MissionStatisticsFilter("quarter")):
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
        await interaction.response.defer(ephemeral=True)
        from .reports import AAR
        report = AAR(self.bot, ucid, name, period)
        file = await report.render()
        if file:
            await interaction.followup.send(
                _("Here is your After Action Report, {}").format(name),
                file=file, ephemeral=utils.get_ephemeral(interaction)
            )
        else:
            await interaction.followup.send(_("No data found to generate an After Action Report."), ephemeral=True)

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
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'refuelings.json')
        env = await report.render(ucid=ucid, member_name=name, flt=period)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_('Find who killed you most'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def nemesis(self, interaction: discord.Interaction,
                      user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None = None,
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
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'nemesis.json')
        env = await report.render(ucid=ucid, member_name=name, flt=period)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_("Find who you've killed the most"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def antagonist(self, interaction: discord.Interaction,
                         user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None = None,
                         period: app_commands.Transform[
                                     StatisticsFilter,
                                     PeriodTransformer(
                                         flt=[MissionStatisticsFilter])] | None = MissionStatisticsFilter()):
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
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'antagonist.json')
        env = await report.render(ucid=ucid, member_name=name, flt=period)
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
            await interaction.response.send_message(_("Use {} to link your account.").format(
                (await utils.get_command(self.bot, name='linkme')).mention
            ), ephemeral=True)
            return

        start = datetime.strptime(start, '%Y-%m-%d') if start else (datetime.now() - timedelta(days=30)).date()
        end = datetime.strptime(end, '%Y-%m-%d') if end else datetime.now()

        ephemeral = not utils.get_ephemeral(interaction)
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
                    WHERE (m.init_id = %(ucid)s or m.target_id = %(ucid)s)
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

        # Escape potential Excel formula characters in name columns
        for col in ['init_name', 'target_name']:
            if col in events_df.columns:
                events_df[col] = events_df[col].apply(
                    lambda x: f"'{x}" if isinstance(x, str) and x.startswith(('=', '+', '-', '@')) else x
                )

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
