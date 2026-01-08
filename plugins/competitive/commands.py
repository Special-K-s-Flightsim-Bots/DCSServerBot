import asyncio
import discord
import itertools
import math

from core import Plugin, utils, get_translation, Node, Group, Report
from datetime import datetime
from discord import app_commands
from plugins.competitive import rating
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from trueskill import Rating, BETA, global_env

from .listener import CompetitiveListener
from ..userstats.filter import MissionStatisticsFilter, PeriodTransformer, StatisticsFilter

_ = get_translation(__name__.split('.')[1])


class Competitive(Plugin[CompetitiveListener]):

    async def install(self) -> bool:
        if not await super().install():
            return False
        # only generate TrueSkill values if there are statistics already
        async with self.apool.connection() as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM missionstats")
            count = (await cursor.fetchone())[0]
        if count > 0:
            asyncio.create_task(self.init_trueskill())
        return True

    async def init_trueskill(self, user: str | None = None) -> None:
        # we need to calculate the TrueSkill values for players
        self.log.warning("Calculating TrueSkill values for players... Please do NOT stop your bot!")
        ratings: dict[str, Rating] = {}

        batch_size = 10000  # missionstats rows per batch

        last_id = 0
        total_processed = 0

        if user:
            where = f"AND (init_id = '{user}' OR target_id = '{user}')"
        else:
            where = ""

        while True:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor(row_factory=dict_row) as cursor:
                        await cursor.execute(f"""
                            SELECT id, init_id, target_id, time
                            FROM missionstats
                            WHERE event = 'S_EVENT_KILL'
                              AND init_id  != '-1'
                              AND target_id != '-1'
                              AND init_side <> target_side
                              AND init_cat   = 'Airplanes'
                              AND target_cat = 'Airplanes'
                              AND id > %s
                              {where}
                            ORDER BY id
                            LIMIT %s
                        """, (last_id, batch_size))
                        rows = await cursor.fetchall()
                        if not rows:
                            break

                        # Compute rating changes and collect upserts for this batch
                        insert_params: list[tuple[str, float, float, datetime]] = []
                        for row in rows:
                            init_id = row['init_id']
                            target_id = row['target_id']

                            if init_id not in ratings:
                                ratings[init_id] = rating.create_rating()
                            if target_id not in ratings:
                                ratings[target_id] = rating.create_rating()

                            ratings[init_id], ratings[target_id] = rating.rate_1vs1(
                                ratings[init_id], ratings[target_id]
                            )

                            init_rating = ratings[init_id]
                            target_rating = ratings[target_id]
                            t = row['time']

                            if not user or user == init_id:
                                insert_params.append((init_id, float(init_rating.mu), float(init_rating.sigma), t))
                            if not user or user == target_id:
                                insert_params.append((target_id, float(target_rating.mu), float(target_rating.sigma), t))

                            last_id = row['id']

                        if insert_params:
                            await cursor.executemany("""
                                INSERT INTO trueskill (player_ucid, skill_mu, skill_sigma, time)
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (player_ucid) DO UPDATE
                                SET skill_mu    = EXCLUDED.skill_mu,
                                    skill_sigma = EXCLUDED.skill_sigma,
                                    time        = EXCLUDED.time
                            """, insert_params)

                        total_processed += len(rows)
                        self.log.info(f"- {total_processed} missionstats rows processed (up to id {last_id})")

        self.log.info("TrueSkill values calculated.")

    async def _trueskill_player(self, interaction: discord.Interaction, user: discord.Member | str) -> None:
        if not user:
            user = interaction.user
        elif not utils.check_roles(self.bot.roles['DCS Admin'], interaction.user):
            raise discord.app_commands.CheckFailure()
        if isinstance(user, discord.Member):
            ucid = await self.bot.get_ucid_by_member(user)
        else:
            ucid = user
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Use {} to link your account.").format(
                (await utils.get_command(self.bot, name='linkme')).mention
            ), ephemeral=True)
            return
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name, t.skill_mu, t.skill_sigma
                    FROM players p LEFT OUTER JOIN trueskill t ON (p.ucid = t.player_ucid) 
                    WHERE p.ucid = %s
                """, (ucid, ))
                row = await cursor.fetchone()
        r = rating.create_rating()
        skill_mu = float(row['skill_mu']) if row['skill_mu'] else r.mu
        skill_sigma = float(row['skill_sigma']) if row['skill_sigma'] else r.sigma
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(
            _("TrueSkill:tm: rating of player {name}: {rating:.2f}.").format(name=row['name'],
                                                                             rating=skill_mu - 3.0 * skill_sigma),
            ephemeral=True)

    @staticmethod
    def calculate_rating(r: Rating) -> float:
        return r.mu - 3.0 * r.sigma

    @staticmethod
    def win_probability(team1: list[Rating], team2: list[Rating]):
        if not team1 or not team2:
            return 0.5  # 50% chance when either team is empty

        delta_mu = sum(r.mu for r in team1) - sum(r.mu for r in team2)
        sum_sigma = sum(r.sigma ** 2 for r in itertools.chain(team1, team2))
        size = len(team1) + len(team2)
        denom = math.sqrt(size * (BETA * BETA) + sum_sigma)
        ts = global_env()
        return ts.cdf(delta_mu / denom)

    @staticmethod
    def calculate_squadron_rating(player_ratings: list[Rating]) -> Rating:
        if not player_ratings:
            return rating.create_rating()

        # Total mu (skill) of the squadron
        total_mu = sum(r.mu for r in player_ratings)

        # Calculate sigma considering team size and interactions
        sum_sigma_squared = sum(r.sigma ** 2 for r in player_ratings)
        n_players = len(player_ratings)

        # Scale sigma based on team size and interactions
        # Using similar scaling as in win probability calculation
        adjusted_sigma = math.sqrt(
            (sum_sigma_squared + (n_players * BETA * BETA)) / n_players
        )

        # Average mu for the squadron
        squadron_mu = total_mu / n_players

        return Rating(mu=squadron_mu, sigma=adjusted_sigma)

    @staticmethod
    async def read_squadron_member_ratings(node: Node, squadron_id: int) -> list[Rating]:
        ratings = []
        async with node.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                async for row in await cursor.execute("""
                    SELECT s.name,
                         COALESCE(t.skill_mu, 25.0)          AS "skill_mu",
                         COALESCE(t.skill_sigma, 25.0 / 3.0) AS "skill_sigma"
                    FROM squadron_members sm
                           LEFT OUTER JOIN trueskill t ON (sm.player_ucid = t.player_ucid)
                           JOIN squadrons s ON (s.id = sm.squadron_id)
                    WHERE s.id = %s
                """, (squadron_id,)):
                    ratings.append(Rating(mu=float(row['skill_mu']), sigma=float(row['skill_sigma'])))
        return ratings

    @staticmethod
    async def trueskill_squadron(node: Node, squadron_id: int) -> Rating:
        ratings = await Competitive.read_squadron_member_ratings(node, squadron_id)
        return Competitive.calculate_squadron_rating(ratings)

    # New command group "/trueskill"
    trueskill = Group(name="trueskill", description="Commands to manage TrueSkill:tm: ratings")

    @trueskill.command(description=_('Display TrueSkill:tm: ratings'))
    @utils.app_has_role('DCS')
    @app_commands.rename(squadron_id='squadron')
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.guild_only()
    async def rating(self, interaction: discord.Interaction,
                     user: app_commands.Transform[discord.Member | str, utils.UserTransformer] | None = None,
                     squadron_id: int | None = None):
        if squadron_id:
            r = await self.trueskill_squadron(self.node, squadron_id)
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("TrueSkill:tm: rating: {rating:.2f}.").format(
                rating=self.calculate_rating(r)), ephemeral=True)
        else:
            await self._trueskill_player(interaction, user)

    @trueskill.command(description=_('Display TrueSkill:tm: history'))
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    async def history(self, interaction: discord.Interaction,
                      user: app_commands.Transform[discord.Member | str, utils.UserTransformer] | None = None,
                      period: app_commands.Transform[
                                  StatisticsFilter,
                                  PeriodTransformer(flt=[MissionStatisticsFilter])
                              ] | None = MissionStatisticsFilter()):
        if not user:
            user = interaction.user

        if isinstance(user, discord.Member):
            ucid = await self.bot.get_ucid_by_member(user)
            if not ucid:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("User {} is not linked.").format(user.display_name),
                                                        ephemeral=True)
                return
            name = user.display_name
        else:
            ucid = user
            member = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(member, discord.Member):
                name = member.display_name
            else:
                name = member

        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        report = Report(self.bot, self.plugin_name, 'trueskill_hist.json')
        env = await report.render(ucid=ucid, name=name, flt=period)
        try:
            file = discord.File(fp=env.buffer, filename=env.filename)
            await interaction.followup.send(embed=env.embed, file=file, ephemeral=ephemeral)
        finally:
            if env.buffer:
                env.buffer.close()

    @trueskill.command(description=_('Delete TrueSkill:tm: ratings'))
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def delete(self, interaction: discord.Interaction,
                     user: app_commands.Transform[discord.Member | str, utils.UserTransformer] | None = None):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        if isinstance(user, discord.Member):
            ucid = await self.bot.get_ucid_by_member(user)
            if not ucid:
                await interaction.followup.send(_("User {} is not linked.").format(user.display_name), ephemeral=True)
                return
        else:
            ucid = user

        if user and not await utils.yn_question(
                interaction, _("Do you really want to delete TrueSkill:tm: ratings for this user?")):
            await interaction.followup.send(_("Aborted."), ephemeral=True)
            return
        elif not user and not await utils.yn_question(
                interaction, _("Do you really want to delete the TrueSkill:tm: ratings for all users?")):
            await interaction.followup.send(_("Aborted."), ephemeral=True)
            return

        async with self.apool.connection() as conn:
            async with conn.transaction():
                if user:
                    await conn.execute("DELETE FROM trueskill WHERE player_ucid = %s", (ucid, ))
                else:
                    await conn.execute("TRUNCATE trueskill CASCADE")
        # noinspection PyUnresolvedReferences
        await interaction.followup.send(_("TrueSkill:tm: ratings deleted."), ephemeral=True)

    @trueskill.command(description=_('Regenerate TrueSkill:tm: ratings'))
    @utils.app_has_role('Admin')
    @app_commands.guild_only()
    async def regenerate(self, interaction: discord.Interaction,
                         user: app_commands.Transform[discord.Member | str, utils.UserTransformer] | None = None):
        if user:
            message = _("Do you want to regenerate the TrueSkill:tm: ratings for this user?")
        else:
            message = _("Do you really want to regenerate **all** TrueSkill:tm: ratings?")
        if not await utils.yn_question(interaction, question=message, message=_("This can take a while.")):
            await interaction.followup.send(_("Aborted."), ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                if user:
                    await conn.execute("DELETE FROM trueskill WHERE player_ucid = %s", (user, ))
                    await conn.execute("DELETE FROM trueskill_hist WHERE player_ucid = %s", (user, ))
                else:
                    await conn.execute("TRUNCATE trueskill CASCADE")
                    await conn.execute("TRUNCATE trueskill_hist CASCADE")
        await interaction.followup.send(_("TrueSkill:tm: ratings deleted.\nGenerating new values now ..."),
                                        ephemeral=ephemeral)
        channel = interaction.channel
        await self.init_trueskill(user)
        await channel.send(_("TrueSkill:tm: ratings regenerated."))


async def setup(bot: DCSServerBot):
    await bot.add_cog(Competitive(bot, CompetitiveListener))
