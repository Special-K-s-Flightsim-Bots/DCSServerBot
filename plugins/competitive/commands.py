import discord
import psycopg

from core import Plugin, command, utils, get_translation
from discord import app_commands
from plugins.competitive import rating
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from trueskill import Rating
from typing import Optional, Union

from .listener import CompetitiveListener

_ = get_translation(__name__.split('.')[1])


class Competitive(Plugin):

    async def install(self) -> bool:
        if not await super().install():
            return False
        # we need to calculate the TrueSkill values for players
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
                        """, (player_id, skill.mu, skill.sigma))
                    else:
                        cursor = await conn.execute("SELECT ucid FROM players WHERE discord_id = %s", (player_id, ))
                        rows = await cursor.fetchall()
                        for row in rows:
                            await conn.execute("""
                                INSERT INTO trueskill (player_ucid, skill_mu, skill_sigma) 
                                VALUES (%s, %s, %s)
                            """, (row[0], skill.mu, skill.sigma))
        return True

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        await conn.execute('UPDATE trueskill SET player_ucid = %s WHERE player_ucid = %s', (new_ucid, old_ucid))

    @command(description=_('Display your TrueSkill:tm: rating'))
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    async def trueskill(self, interaction: discord.Interaction,
                        user: Optional[app_commands.Transform[Union[discord.Member, str], utils.UserTransformer]]):
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


async def setup(bot: DCSServerBot):
    await bot.add_cog(Competitive(bot, CompetitiveListener))
