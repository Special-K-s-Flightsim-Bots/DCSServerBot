import asyncio
import discord
import itertools
import math
import psycopg

from core import Plugin, utils, get_translation, Node, Group, Report
from discord import app_commands
from plugins.competitive import rating
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from trueskill import Rating, BETA, global_env

from .listener import CompetitiveListener

_ = get_translation(__name__.split('.')[1])


class Competitive(Plugin[CompetitiveListener]):

    async def install(self) -> bool:
        if not await super().install():
            return False
        asyncio.create_task(self.init_trueskill())
        return True

    async def init_trueskill(self):
        # we need to calculate the TrueSkill values for players
        self.log.warning("Calculating TrueSkill values for players... Please do NOT stop your bot!")
        ratings: dict[str, Rating] = dict()
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                size = 1000
                await cursor.execute("""
                    SELECT p1.discord_id AS init_discord_id, m.init_id, 
                           p2.discord_id AS target_discord_id, m.target_id 
                    FROM missionstats m, players p1, players p2
                    WHERE p1.ucid = m.init_id
                    AND p2.ucid = m.target_id 
                    AND event = 'S_EVENT_KILL' AND init_id != '-1' AND target_id != '-1'
                    AND init_side <> target_side
                    AND init_cat = 'Airplanes' AND target_cat = 'Airplanes'
                    ORDER BY id
                """)
                rows = await cursor.fetchmany(size=size)
                while len(rows) > 0:
                    for row in rows:
                        init_id = row['init_discord_id'] if row['init_discord_id'] != -1 else row['init_id']
                        target_id = row['target_discord_id'] if row['target_discord_id'] != -1 else row['target_id']
                        if init_id not in ratings:
                            ratings[init_id] = rating.create_rating()
                        if target_id not in ratings:
                            ratings[target_id] = rating.create_rating()
                        ratings[init_id], ratings[target_id] = rating.rate_1vs1(
                            ratings[init_id], ratings[target_id])
                    rows = await cursor.fetchmany(size=size)
            async with conn.transaction():
                for player_id, skill in ratings.items():
                    if isinstance(player_id, str):
                        await conn.execute("""
                            INSERT INTO trueskill (player_ucid, skill_mu, skill_sigma) 
                            VALUES (%s, %s, %s)
                            ON CONFLICT (player_ucid) DO UPDATE
                            SET skill_mu = EXCLUDED.skill_mu, skill_sigma = EXCLUDED.skill_sigma
                        """, (player_id, skill.mu, skill.sigma))
                    else:
                        cursor = await conn.execute("SELECT ucid FROM players WHERE discord_id = %s", (player_id, ))
                        rows = await cursor.fetchall()
                        for row in rows:
                            await conn.execute("""
                                INSERT INTO trueskill (player_ucid, skill_mu, skill_sigma) 
                                VALUES (%s, %s, %s)
                                ON CONFLICT (player_ucid) DO UPDATE
                                SET skill_mu = EXCLUDED.skill_mu, skill_sigma = EXCLUDED.skill_sigma
                            """, (row[0], skill.mu, skill.sigma))
        self.log.info("TrueSkill values calculated.")

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        await conn.execute('UPDATE trueskill SET player_ucid = %s WHERE player_ucid = %s', (new_ucid, old_ucid))

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
                      user: app_commands.Transform[discord.Member | str, utils.UserTransformer] | None = None):
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
        env = await report.render(ucid=ucid, name=name)
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


async def setup(bot: DCSServerBot):
    await bot.add_cog(Competitive(bot, CompetitiveListener))
