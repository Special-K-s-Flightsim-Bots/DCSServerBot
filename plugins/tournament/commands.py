import asyncio
import discord
import os
import pandas as pd
import psycopg
import random
import shutil

from core import Plugin, Group, utils, get_translation, PluginRequiredError, Status, Coalition, yn_question, Server, \
    MizFile, DataObjectFactory, async_cache, Report, TRAFFIC_LIGHTS
from datetime import datetime, timezone, timedelta
from discord import app_commands, TextChannel, CategoryChannel, NotFound
from discord.ext import tasks
from io import BytesIO
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from time import time
from typing import Optional, Literal

from .const import TOURNAMENT_PHASE
from .listener import TournamentEventListener
from .utils import create_versus_image, create_elimination_matches, create_group_matches, create_groups, \
    create_tournament_sheet, render_groups, create_winner_image
from .view import ChoicesView, ApplicationModal, ApplicationView, TournamentModal, SignupView
from ..competitive.commands import Competitive
from ..creditsystem.squadron import Squadron

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])


async def tournament_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute("""
            SELECT tournament_id, campaign 
            FROM tm_tournaments 
            WHERE campaign ILIKE %s ORDER BY campaign
        """, ('%' + current + '%', ))
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=row[1], value=row[0])
            async for row in cursor
        ]
        return choices[:25]


async def active_tournament_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute("""
            SELECT t.tournament_id, t.campaign 
            FROM tm_tournaments t JOIN campaigns c ON t.campaign = c.name 
            WHERE campaign ILIKE %s
            AND c.start <= NOW() AT TIME ZONE 'UTC'
            AND COALESCE(c.stop, NOW() AT TIME ZONE 'UTC') >= NOW() AT TIME ZONE 'UTC'
            ORDER BY campaign
        """, ('%' + current + '%', ))
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=row[1], value=row[0])
            async for row in cursor
        ]
        return choices[:25]


async def squadron_autocomplete(interaction: discord.Interaction, current: str,
                                config: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    tournament_id = utils.get_interaction_param(interaction, "tournament")
    if config == 'all':
        sub_query = ""
    elif config == 'reject':
        sub_query = "AND ts.status IN ('PENDING', 'ACCEPTED')"
    elif config == 'accepted':
        sub_query = "AND ts.status = 'ACCEPTED'"
    else:
        return []
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute(f"""
            SELECT ts.squadron_id, sq.name, ts.status  
            FROM tm_squadrons ts JOIN squadrons sq ON ts.squadron_id = sq.id  
            WHERE ts.tournament_id = %s {sub_query} AND sq.name ILIKE %s 
            ORDER BY CASE status
                WHEN 'PENDING' THEN 1
                WHEN 'REJECTED' THEN 2
                WHEN 'ACCEPTED' THEN 3
                ELSE 4
            END, sq.name
        """, (tournament_id, '%' + current + '%'))
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=f"{row[1]} ({row[2]})", value=row[0])
            async for row in cursor
        ]
        return choices[:25]

async def all_squadron_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    return await squadron_autocomplete(interaction, current, 'all')


async def reject_squadron_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    return await squadron_autocomplete(interaction, current, 'reject')


async def valid_squadron_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    return await squadron_autocomplete(interaction, current, 'accepted')


async def stage_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    tournament_id = utils.get_interaction_param(interaction, "tournament")
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute("""
            SELECT COALESCE(MAX(stage), 1) 
            FROM tm_matches 
            WHERE tournament_id = %s
        """, (tournament_id,))
        stage = (await cursor.fetchone())[0]
    return [
        app_commands.Choice(name=stage, value=stage),
        app_commands.Choice(name=stage+1, value=stage+1),
    ]


async def server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    tournament_id = utils.get_interaction_param(interaction, "tournament")
    async with interaction.client.apool.connection() as conn:
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=row[0], value=row[0])
            async for row in await conn.execute("""
                SELECT s.server_name 
                FROM tm_tournaments t 
                     JOIN campaigns c ON t.campaign = c.name
                     JOIN campaigns_servers s ON c.id = s.campaign_id 
                WHERE t.tournament_id = %s
                AND c.start <= NOW() AT TIME ZONE 'UTC'
                AND COALESCE(c.stop, NOW() AT TIME ZONE 'UTC') >= NOW() AT TIME ZONE 'UTC'
                AND s.server_name ILIKE %s
                ORDER BY s.server_name
            """, (tournament_id, '%' + current + '%', ))
        ]
        return choices[:25]


async def all_matches_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    tournament_id = utils.get_interaction_param(interaction, "tournament")
    async with interaction.client.apool.connection() as conn:
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=row[1] + ' vs ' + row[2], value=row[0])
            async for row in await conn.execute("""
                SELECT m.match_id, s1.name, s2.name 
                FROM tm_tournaments t
                     JOIN tm_matches m ON t.tournament_id = m.tournament_id 
                     JOIN squadrons s1 ON s1.id = m.squadron_blue
                     JOIN squadrons s2 ON s2.id = m.squadron_red
                WHERE t.tournament_id = %s
                ORDER BY 1
            """, (tournament_id, ))
            if not current or current.casefold() in row[1].casefold() or current.casefold() in row[2].casefold()
        ]
        return choices[:25]


async def active_matches_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    tournament_id = utils.get_interaction_param(interaction, "tournament")
    async with interaction.client.apool.connection() as conn:
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=row[1] + ' vs ' + row[2], value=row[0])
            async for row in await conn.execute("""
                SELECT m.match_id, s1.name, s2.name 
                FROM tm_tournaments t
                     JOIN tm_matches m ON t.tournament_id = m.tournament_id 
                     JOIN squadrons s1 ON s1.id = m.squadron_blue
                     JOIN squadrons s2 ON s2.id = m.squadron_red
                WHERE t.tournament_id = %s AND m.winner_squadron_id IS NULL
                AND (
                    m.round_number > 0 OR m.server_name NOT IN (
                        SELECT server_name FROM tm_matches
                        WHERE tournament_id = t.tournament_id
                        AND round_number > 0 
                        AND winner_squadron_id IS NULL
                    )
                )
                ORDER BY 1
            """, (tournament_id,))
            if not current or current.casefold() in row[1].casefold() or current.casefold() in row[2].casefold()
        ]
        return choices[:25]


async def match_squadrons_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    match_id = utils.get_interaction_param(interaction, "match")
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute("SELECT squadron_blue, squadron_red FROM tm_matches WHERE match_id = %s",
                                    (match_id, ))
        squadron_blue_id, squadron_red_id = await cursor.fetchone()
        squadron_blue = utils.get_squadron(interaction.client.node, squadron_id=squadron_blue_id)
        squadron_red = utils.get_squadron(interaction.client.node, squadron_id=squadron_red_id)
        return [
            app_commands.Choice(name=squadron_blue['name'], value=squadron_blue_id),
            app_commands.Choice(name=squadron_red['name'], value=squadron_red_id)
        ]


async def mission_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    match_id = utils.get_interaction_param(interaction, "match")
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute(f"""
            SELECT server_name FROM tm_matches WHERE match_id = %s
        """, (match_id,))
        server_name = (await cursor.fetchone())[0]
    if server_name:
        interaction.data['options'][0]['options'].append({"name": "server", "type": 4, "value": server_name})
        return await utils.mission_autocomplete(interaction, current)
    return []


async def date_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    now = int(time())
    day_start = now - (now % 86400)
    return [
        app_commands.Choice(
            name=(datetime.fromtimestamp(day_start + (i * 86400), tz=timezone.utc)).strftime("%Y-%m-%d"),
            value=day_start + (i * 86400)
        )
        for i in range(0, 25)
    ]


async def time_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    tournament_id = utils.get_interaction_param(interaction, "tournament")
    async with interaction.client.apool.connection() as conn:
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=row[0].strftime("%H:%M"), value=row[0].hour * 3600 + row[0].minute * 60)
            async for row in await conn.execute("""
                SELECT start_time FROM tm_available_times WHERE tournament_id = %s
            """, (tournament_id, ))
        ]
        return choices[:25]


class Tournament(Plugin[TournamentEventListener]):

    async def cog_load(self) -> None:
        await super().cog_load()
        if self.get_config().get('autostart_matches', False):
            self.match_scheduler.start()

    async def cog_unload(self) -> None:
        if self.get_config().get('autostart_matches', False):
            self.match_scheduler.cancel()
        await super().cog_unload()

    async def rename(self, conn: psycopg.AsyncConnection, old_name: str, new_name: str):
        await conn.execute('UPDATE tm_matches SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    def get_admin_channel(self):
        config = self.get_config()
        channel_id = config.get('channels', {}).get('admin')
        if channel_id:
            channel = self.bot.get_channel(channel_id)
        else:
            channel = self.bot.get_admin_channel()
        return channel

    async def get_tournament(self, tournament_id: int) -> Optional[dict]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT t.tournament_id, c.name, c.description,
                           c.start AT TIME ZONE 'UTC' AS start, c.stop AT TIME ZONE 'UTC' AS stop, 
                           t.rounds, t.num_players
                    FROM campaigns c JOIN tm_tournaments t
                    ON c.name = t.campaign
                    WHERE t.tournament_id = %s
                """, (tournament_id, ))
                return await cursor.fetchone()

    async def get_match(self, match_id: int) -> Optional[dict]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM tm_matches WHERE match_id = %s
                """, (match_id, ))
                return await cursor.fetchone()

    def get_info_channel(self) -> Optional[discord.TextChannel]:
        config = self.get_config()
        channel_id = config.get('channels', {}).get('info')
        if channel_id and self.bot.check_channel(channel_id):
            return self.bot.get_channel(channel_id)
        return None

    async def get_squadron_channel(self, match_id: int, side: str) -> Optional[TextChannel]:
        async with self.apool.connection() as conn:
            cursor = await conn.execute(f"""
                SELECT squadron_{side}_channel FROM tm_matches WHERE match_id = %s
            """, (match_id, ))
            row = await cursor.fetchone()
            if row:
                channel_id = row[0]
                return self.bot.get_channel(channel_id)
        return None

    @async_cache
    async def get_squadron(self, match_id: int, squadron_id: int) -> Squadron:
        squadron = utils.get_squadron(node=self.node, squadron_id=squadron_id)
        async with self.node.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT c.id FROM campaigns c 
                JOIN tm_tournaments t ON t.campaign = c.name
                JOIN tm_matches tm ON tm.tournament_id = t.tournament_id
                WHERE tm.match_id = %s
            """, (match_id, ))
            campaign_id = (await cursor.fetchone())[0]
        return DataObjectFactory().new(Squadron, node=self.node, name=squadron['name'], campaign_id=campaign_id)

    async def inform_squadron(self, *, tournament_id: int, squadron_id: int, message: Optional[str] = None,
                              embed: Optional[discord.Embed] = None):
        async with self.apool.connection() as conn:
            async for row in await conn.execute("""
                SELECT p.discord_id
                FROM players p JOIN squadron_members m ON p.ucid = m.player_ucid
                WHERE m.squadron_id = %s AND m.admin IS TRUE
            """, (squadron_id,)):
                user = self.bot.get_user(row[0])
                if user:
                    tournament = await self.get_tournament(tournament_id)
                    squadron = utils.get_squadron(node=self.node, squadron_id=squadron_id)
                    dm_channel = await user.create_dm()
                    if message:
                        message = message.format(squadron=squadron['name'], tournament=tournament['name'])
                    await dm_channel.send(content=message, embed=embed)

    # New command group "/tournament"
    tournament = Group(name="tournament", description="Commands to manage tournaments")

    async def render_groups_image(self, tournament_id: int) -> BytesIO:
        groups: list[list[int]] = []
        async with self.apool.connection() as conn:
            async for row in await conn.execute("""
                SELECT group_number, squadron_id 
                FROM tm_squadrons 
                WHERE tournament_id = %s
                ORDER BY group_number
            """, (tournament_id,)):
                if row[0] > len(groups):
                    groups.append([])
                groups[row[0] - 1].append(row[1])

        all_squadron_ids = [squad_id for group in groups for squad_id in group]
        squadron_data = {
            squad_id: utils.get_squadron(self.node, squadron_id=squad_id)
            for squad_id in all_squadron_ids
        }

        buffer = await render_groups([
            [
                (squadron_data[squad_id]['name'], squadron_data[squad_id]['image_url'])
                for squad_id in group
            ]
            for group in groups
        ])
        return buffer

    async def render_status_embed(self, tournament_id: int, *,
                                  phase: TOURNAMENT_PHASE = TOURNAMENT_PHASE.START_GROUP_PHASE,
                                  match_id: Optional[int] = None) -> None:
        tournament = await self.get_tournament(tournament_id)
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT COUNT(*) FROM tm_squadrons WHERE tournament_id = %s AND status = 'ACCEPTED'
            """, (tournament_id,))
            num_squadrons = (await cursor.fetchone())[0]

            embed = discord.Embed(color=discord.Color.blue(), title=f"Tournament {tournament['name']} Overview")
            buffer = None

            if match_id:
                match = await self.get_match(match_id=match_id)
                # read group number
                cursor = await conn.execute("""
                    SELECT group_number FROM tm_squadrons 
                    WHERE squadron_id = (
                        SELECT squadron_blue 
                        FROM tm_matches 
                        WHERE match_id = %s
                    )""", (match_id,))
                group_number = (await cursor.fetchone())[0]
                if match['stage'] == 1 and group_number:
                    phase = TOURNAMENT_PHASE.START_GROUP_PHASE
                else:
                    phase = TOURNAMENT_PHASE.START_ELIMINATION_PHASE

        if phase == TOURNAMENT_PHASE.SIGNUP:
            message = _("## :warning: Attention all Squadron Leaders! :warning:\n"
                        "A new tournament has been created:\n"
                        "\n"
                        "```{}```").format(tournament['description'])
            message += _("\nYou can use {} to sign up.").format(
                (await utils.get_command(self.bot, group=self.tournament.name, name=self.signup.name)).mention)
            embed.add_field(name=utils.print_ruler(ruler_length=27), value="_ _", inline=False)
            embed.add_field(name=_("Start Date"), value=f"<t:{int(tournament['start'].timestamp())}>")
            embed.add_field(name=_("# Players per Side"), value=str(tournament['num_players']))
            embed.add_field(name=_("# Signups"), value=str(num_squadrons))
            embed.set_footer(text=_("You need to be an admin of the respective squadron to sign up."))

        elif phase == TOURNAMENT_PHASE.START_GROUP_PHASE:
            message = _("The group phase is now running.")
            tmp = await self.render_matches(tournament=tournament)
            for field in tmp.fields:
                embed.add_field(name=field.name, value=field.value, inline=field.inline)
            buffer = await self.render_groups_image(tournament_id)

        elif phase == TOURNAMENT_PHASE.START_ELIMINATION_PHASE:
            message = _("The eliminiation phase is now running.")
            tmp = await self.render_matches(tournament=tournament)
            for field in tmp.fields:
                embed.add_field(name=field.name, value=field.value, inline=field.inline)

        elif TOURNAMENT_PHASE.TOURNAMENT_FINISHED:
            embed.title = _("THE TOURNAMENT HAS FINISHED!")
            embed.set_thumbnail(url=self.bot.guilds[0].icon.url)
            async with self.apool.connection() as conn:
                # check if we have flown matches already
                cursor = await conn.execute("""
                    SELECT winner_squadron_id FROM tm_matches 
                    WHERE tournament_id = %s 
                    ORDER BY stage DESC LIMIT 1
                """, (tournament_id,))
                winner_id = (await cursor.fetchone())[0]
            winner = utils.get_squadron(node=self.node, squadron_id=winner_id)
            winner_image = winner['image_url']
            if winner_image:
                buffer = await create_winner_image(winner_image)
            message = _("### Stand proud, {}!\n"
                        "_Through fire and thunder you prevailed,\n"
                        "When others faltered, you stood strong,\n"
                        "Victory is where you belong!_").format(winner['name'])

        else:
            return

        embed.description = message
        if buffer:
            file = discord.File(fp=buffer, filename=f"tournament_{tournament_id}.png")
            embed.set_image(url=f"attachment://tournament_{tournament_id}.png")
        else:
            file = None
        try:
            # create a persistent message
            channel_id = self.get_config().get('channels', {}).get('info')
            await self.bot.setEmbed(embed_name=f"tournament_status_{tournament_id}", embed=embed, file=file,
                                    channel_id=channel_id)
        finally:
            if buffer:
                buffer.close()

    async def render_info_embed(self, tournament_id: int, *,
                                phase: TOURNAMENT_PHASE = TOURNAMENT_PHASE.SIGNUP,
                                match_id: Optional[int] = None) -> None:
        tournament = await self.get_tournament(tournament_id)
        embed = discord.Embed(color=discord.Color.blue(), title=f"Tournament {tournament['name']} Information")
        buffer = None

        if phase == TOURNAMENT_PHASE.MATCH_RUNNING:
            match = await self.get_match(match_id=match_id)
            message = _("A match is running on server {}!").format(utils.escape_string(match['server_name']))
            squadron_blue = utils.get_squadron(node=self.node, squadron_id=match['squadron_blue'])
            squadron_red = utils.get_squadron(node=self.node, squadron_id=match['squadron_red'])
            blue_image = squadron_blue['image_url']
            red_image = squadron_red['image_url']
            if blue_image and red_image:
                buffer = await create_versus_image(blue_image, red_image)

            embed.add_field(name=_("Blue"), value=squadron_blue['name'])
            ratings_blue = await Competitive.read_squadron_member_ratings(self.node, match['squadron_blue'])
            ratings_red = await Competitive.read_squadron_member_ratings(self.node, match['squadron_red'])
            win_propability = Competitive.win_probability(ratings_blue, ratings_red)
            embed.add_field(name=_("Win propability"), value=f"{win_propability * 100.0:.2f}%")
            embed.add_field(name=_("Red"), value=squadron_red['name'])

            embed.add_field(name=_("Round"), value=f"{match['round_number']} of {tournament['rounds']}")
            embed.add_field(name=_("Blue Wins"), value=str(match['squadron_blue_rounds_won']))
            embed.add_field(name=_("Red Wins"), value=str(match['squadron_red_rounds_won']))

        elif phase == TOURNAMENT_PHASE.MATCH_FINISHED:
            match = await self.get_match(match_id=match_id)
            winner = "blue" if match['winner_squadron_id'] == match['squadron_blue'] else "red"
            message = _("The match is over: {} won!").format(winner.upper())
            squadron_blue = utils.get_squadron(node=self.node, squadron_id=match['squadron_blue'])
            squadron_red = utils.get_squadron(node=self.node, squadron_id=match['squadron_red'])
            blue_image = squadron_blue['image_url']
            red_image = squadron_red['image_url']
            if blue_image and red_image:
                buffer = await create_versus_image(blue_image, red_image, winner)

            embed.add_field(name=_("Round"), value=f"{match['round_number']} of {tournament['rounds']}")
            embed.add_field(name=_("Blue Wins"), value=str(match['squadron_blue_rounds_won']))
            embed.add_field(name=_("Red Wins"), value=str(match['squadron_red_rounds_won']))

        elif TOURNAMENT_PHASE.TOURNAMENT_FINISHED:
            # remove the status embed
            channel_id = self.get_config().get('channels', {}).get('info')
            channel = self.bot.get_channel(channel_id)
            message = await self.bot.fetch_embed(embed_name=f"tournament_info_{tournament_id}", channel=channel)
            if message:
                await message.delete()
            return

        else:
            return

        embed.description = message
        if buffer:
            file = discord.File(fp=buffer, filename=f"tournament_{tournament_id}.png")
            embed.set_image(url=f"attachment://tournament_{tournament_id}.png")
        else:
            file = None
        try:
            # create a persistent message
            channel_id = self.get_config().get('channels', {}).get('info')
            await self.bot.setEmbed(embed_name=f"tournament_info_{tournament_id}", embed=embed, file=file,
                                    channel_id=channel_id)
        finally:
            if buffer:
                buffer.close()

    @tournament.command(description=_('Create a tournament'))
    @app_commands.guild_only()
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    @utils.app_has_role('Admin')
    async def create(self, interaction: discord.Interaction, campaign: str):
        ephemeral = utils.get_ephemeral(interaction)

        modal = TournamentModal()
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if await modal.wait():
            await interaction.followup.send(_("Aborted."), ephemeral=ephemeral)
            return
        if modal.error:
            await interaction.followup.send(modal.error, ephemeral=ephemeral)
            return

        campaign_details = await utils.get_campaign(self, campaign)
        if campaign_details['stop']:
            await interaction.followup.send(_("Error: This campaign is already stopped!"), ephemeral=True)
            return
        elif campaign_details['start'] < datetime.now(tz=timezone.utc):
            if not await yn_question(interaction, _("The provided campaign is already started.\n"
                                                    "Are you sure you want to create a tournament for it now?")):
                await interaction.followup.send(_("Aborted."), ephemeral=True)
                return

        try:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    cursor = await conn.execute("""
                        INSERT INTO tm_tournaments (campaign, rounds, num_players) 
                        VALUES (%s, %s, %s)
                        RETURNING tournament_id
                    """, (campaign, modal.num_rounds, modal.num_players))
                    tournament_id = (await cursor.fetchone())[0]
                    for time in modal.times:
                        async with conn.transaction():
                            await conn.execute("""
                                INSERT INTO tm_available_times (tournament_id, start_time)
                                VALUES (%s, %s::time)
                            """, (tournament_id, time))
            await self.bot.audit(f"created tournament {campaign}.", user=interaction.user)
            await interaction.followup.send(_("Tournament {} created.").format(campaign), ephemeral=ephemeral)
        except UniqueViolation:
            await interaction.followup.send(_("Tournament {} exists already!").format(campaign), ephemeral=True)
            return

        # inform players
        channel = self.get_info_channel()
        if not channel:
            return

        if not await yn_question(interaction, _("Do you want to inform players about the new tournament now?"),
                                 ephemeral=ephemeral):
            await interaction.followup.send(_("Aborted."), ephemeral=True)
            return

        await self.render_status_embed(tournament_id, phase=TOURNAMENT_PHASE.SIGNUP)

    @staticmethod
    def reset_serversettings(server: Server):
        filename = os.path.join(server.instance.home, 'Config', 'serverSettings.lua')
        orig_file = filename + '.orig'
        if os.path.exists(orig_file):
            shutil.copy2(orig_file, filename)

    @tournament.command(description=_('Finish a tournament'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    async def finish(self, interaction: discord.Interaction, tournament_id: int):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)

        messages = []
        async with self.apool.connection() as conn:
            # checking tournament
            cursor = await conn.execute("""
                SELECT COUNT(*) FROM tm_matches 
                WHERE tournament_id = %s
                AND winner_squadron_id IS NULL
            """, (tournament_id,))
            count = (await cursor.fetchone())[0]
            if count > 0 and not await yn_question(interaction, _("There are unfinished matches. "
                                                                  "Do you really want to finish this tournament?")):
                await interaction.followup.send(_("Aborted."), ephemeral=True)
                return

            # reset serverSettings.lua
            messages.append(_("Resetting all amended serverSettings.lua files ..."))
            msg = await interaction.followup.send(messages[0], ephemeral=utils.get_ephemeral(interaction))
            async for row in await conn.execute("""
                SELECT server_name
                FROM campaigns_servers s
                         JOIN campaigns c ON s.campaign_id = c.id
                         JOIN tm_tournaments t ON c.name = t.campaign
                WHERE t.tournament_id = %s
            """, (tournament_id,)):
                server = self.bot.servers[row[0]]
                self.reset_serversettings(server)

            # close campaign
            messages.append(_("Closing the underlying campaign ..."))
            await msg.edit(content='\n'.join(messages))
            async with conn.transaction():
                cursor = await conn.execute("""
                    UPDATE campaigns SET stop = NOW() AT TIME ZONE 'UTC' 
                    WHERE name = (
                        SELECT name FROM tm_tournaments WHERE tournament_id = %s
                    )
                    RETURNING name
                """, (tournament_id,))
                name = (await cursor.fetchone())[0]

        await self.bot.audit(f"finished tournament {name} and closed the underlying campaign.",
                             user=interaction.user)
        messages.append(_("The tournament {} is finished.").format(name))
        await msg.edit(content='\n'.join(messages))

    @tournament.command(description=_('Delete a tournament'))
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    async def delete(self, interaction: discord.Interaction, tournament_id: int):
        if not await yn_question(interaction, _("Do you really want to delete this tournament and all its data?"),
                                 ephemeral=utils.get_ephemeral(interaction)):
            await interaction.followup.send(_("Aborted."), ephemeral=True)
            return
        try:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor(row_factory=dict_row) as cursor:
                        # delete all temp channels
                        async for row in await cursor.execute("""
                            SELECT squadron_blue_channel, squadron_red_channel 
                            FROM tm_matches
                            WHERE tournament_id = %s
                        """, (tournament_id,)):
                            for side in ['blue', 'red']:
                                try:
                                    channel = self.bot.get_channel(row[f'squadron_{side}_channel'])
                                    if channel:
                                        await channel.delete()
                                except NotFound:
                                    pass
                        # delete all data
                        await cursor.execute("""
                            DELETE FROM tm_tournaments 
                            WHERE tournament_id = %s
                            RETURNING *
                        """, (tournament_id, ))
                        row = await cursor.fetchone()

            # delete the info embed
            for what in ['status', 'info']:
                msg = await self.bot.fetch_embed(embed_name=f"tournament_{what}_{tournament_id}", channel=self.get_info_channel())
                try:
                    if msg:
                        await msg.delete()
                except discord.NotFound:
                    pass

            await self.bot.audit(f"deleted tournament {row['campaign']}.", user=interaction.user)
            # noinspection PyUnresolvedReferences
            await interaction.followup.send(_("Tournament {} deleted.").format(row['campaign']),)
        except Exception as ex:
            # noinspection PyUnresolvedReferences
            await interaction.followup.send(_("Error deleting tournament: {}").format(ex), ephemeral=True)

    @tournament.command(description=_('Signup to a tournament'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete_admin)
    @utils.squadron_role_check()
    async def signup(self, interaction: discord.Interaction, tournament_id: int, squadron_id: int):
        config = self.get_config()
        if config.get('use_signup_form', False):
            modal = ApplicationModal()
            # noinspection PyUnresolvedReferences
            await interaction.response.send_modal(modal, ephemeral=True)
            if await modal.wait():
                await interaction.followup.send(_("Aborted."), ephemeral=True)
                return
        else:
            modal = None
            # noinspection PyUnresolvedReferences
            await interaction.response.defer()

        try:
            squadron = utils.get_squadron(self.node, squadron_id=squadron_id)
            if not squadron['role']:
                # noinspection PyUnresolvedReferences
                await interaction.followup.send(
                    _(":warning: To participate in the tournament, your squadron must have an assigned role.\n"
                      "Contact the tournament host to receive your role assignment."), ephemeral=True)

            tournament = await self.get_tournament(tournament_id)
            async with self.apool.connection() as conn:
                cursor = await conn.execute("SELECT COUNT(*) FROM squadron_members WHERE squadron_id = %s",
                                            (squadron_id,))
                row = await cursor.fetchone()
                if row[0] < tournament['num_players']:
                    # noinspection PyUnresolvedReferences
                    await interaction.followup.send(
                        _(":warning: Your squadron does not have enough players to participate in the tournament yet."),
                        ephemeral=True)

                # read the preferred times
                times_options: list[discord.SelectOption] = []
                async for row in await conn.execute("""
                    SELECT time_id, start_time FROM tm_available_times WHERE tournament_id = %s
                """, (tournament_id,)):
                    times_options.append(discord.SelectOption(label=str(row[1]), value=str(row[0])))

                # get available maps
                terrain_options = [
                    discord.SelectOption(label=x, value=x)
                    for x in await self.node.get_installed_modules()
                    if x.endswith('_terrain') and x not in ['CAUCASUS_terrain', 'MARIANAISLANDS_terrain']
                ]
                view = SignupView(times_options, terrain_options)
                msg = await interaction.followup.send(view=view, ephemeral=True)
                try:
                    await view.wait()
                finally:
                    await msg.delete()

                if view.times is None:
                    await interaction.followup.send(_("Aborted."), ephemeral=True)
                    return

                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO tm_squadrons(tournament_id, squadron_id, application) VALUES (%s, %s, %s)
                    """, (tournament_id, squadron_id, modal.application_text.value if modal else None))
                    for value in view.times:
                        await conn.execute("""
                            INSERT INTO tm_squadron_time_preferences (tournament_id, squadron_id, available_time_id)
                            VALUES (%s, %s, %s)
                        """, (tournament_id, squadron_id, int(value)))
                    for value in view.terrains:
                        await conn.execute("""
                            INSERT INTO tm_squadron_terrain_preferences (tournament_id, squadron_id, terrain)
                            VALUES (%s, %s, %s)
                        """, (tournament_id, squadron_id, value))
            # noinspection PyUnresolvedReferences
            await interaction.followup.send(
                _("Squadron application handed in for tournament {}.").format(tournament['name']), ephemeral=True)
            admin_channel = self.get_admin_channel()
            if admin_channel:
                await admin_channel.send(_("Squadron {} signed up for tournament {}, you can now {} them.").format(
                    squadron['name'], tournament['name'],
                    (await utils.get_command(self.bot, group='tournament', name='verify')).mention))
        except UniqueViolation:
            # noinspection PyUnresolvedReferences
            await interaction.followup.send(_("Squadron already signed up for tournament."), ephemeral=True)

    @tournament.command(description=_('Withdraw from a tournament'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @app_commands.autocomplete(squadron_id=reject_squadron_autocomplete)
    @utils.squadron_role_check()
    async def withdraw(self, interaction: discord.Interaction, tournament_id: int, squadron_id: int):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()

        tournament = await self.get_tournament(tournament_id)
        if tournament['start'] <= datetime.now(timezone.utc) and not await utils.yn_question(
                interaction, _("You are going to withdraw from a running tournament!\n"
                               "All already scheduled matches will be marked as lost.")):
            # noinspection PyUnresolvedReferences
            await interaction.followup.send("Abort.", ephemeral=True)
            return

        squadron = utils.get_squadron(self.node, squadron_id=squadron_id)
        admin_channel = self.get_admin_channel()
        if admin_channel:
            await admin_channel.send(
                _("Squadron {} forfeit and withdrew from tournament {}.").format(
                    squadron['name'], tournament['name']))

        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE tm_squadrons SET status = 'WITHDRAW' 
                    WHERE tournament_id = %s AND squadron_id = %s
                    """, (tournament_id, squadron_id))
                for side in ['red', 'blue']:
                    opposite = 'blue' if side == 'red' else 'red'
                    cursor = await conn.execute(f"""
                        UPDATE tm_matches SET winner_squadron_id = squadron_{opposite} 
                        WHERE tournament_id = %s AND squadron_{side} = %s AND winner_squadron_id IS NULL
                        RETURNING match_id, winner_squadron_id
                    """, (tournament_id, squadron_id))
                    for match_id, winner_squadron_id  in await cursor.fetchall():
                        if admin_channel:
                            winner_squadron = utils.get_squadron(self.node, squadron_id=winner_squadron_id)
                            await admin_channel.send(_("- Match {} vs {} was marked as won by {} due to a forfeit.").format(
                                squadron['name'], winner_squadron['name'], winner_squadron['name']))

        await self.bot.audit(f"withdrew squadron {squadron['name']} from tournament {tournament['name']}.",
                             user=interaction.user)
        await interaction.followup.send(
            _("Your squadron has been withdrawn from tournament {}.").format(tournament['name']), ephemeral=True)
        await self.eventlistener.check_tournament_finished(tournament_id)

    @tournament.command(description=_('Verfiy Applications'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @app_commands.autocomplete(squadron_id=all_squadron_autocomplete)
    @utils.app_has_role('GameMaster')
    async def verify(self, interaction: discord.Interaction, tournament_id: int, squadron_id: int):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT t.*, s.name, s.image_url, s.role, s.description, COALESCE(member_count, 0) as member_count
                    FROM tm_squadrons t 
                    JOIN squadrons s ON t.squadron_id = s.id 
                    LEFT JOIN (
                        SELECT squadron_id, COUNT(*) as member_count 
                        FROM squadron_members 
                        GROUP BY squadron_id
                    ) sm ON sm.squadron_id = s.id
                    WHERE t.squadron_id = %s
                """, (squadron_id, ))
                row = await cursor.fetchone()
                if not row:
                    await interaction.followup.send(_("Squadron ID not found."), ephemeral=True)
                    return

                embed = discord.Embed(color=discord.Color.blue(), title=_('Application for Squadron "{}"').format(
                    utils.escape_string(row['name'])))
                embed.description = row['application']
                if row['image_url']:
                    embed.set_thumbnail(url=row['image_url'])
                embed.add_field(name=_("# Members"), value=str(row['member_count']))
                embed.add_field(name=_("Role"), value=self.bot.get_role(row['role']).name if row['role'] else _("n/a"))
                embed.add_field(name=_("State"), value=row['status'])

                terrains = []
                async for row in await cursor.execute("""
                    SELECT terrain FROM tm_squadron_terrain_preferences 
                    WHERE tournament_id = %s AND squadron_id = %s
                """, (tournament_id, squadron_id)):
                    terrains.append('- ' + row['terrain'])
                if terrains:
                    embed.add_field(name=_("Terrain Preferences"), value='\n'.join(terrains), inline=False)

                times = []
                async for row in await cursor.execute("""
                    SELECT tp.available_time_id, tt.start_time 
                    FROM tm_squadron_time_preferences tp JOIN tm_available_times tt ON tt.time_id = tp.available_time_id 
                    WHERE tp.tournament_id = %s AND tp.squadron_id = %s
                """, (tournament_id, squadron_id)):
                    times.append('- ' + row['start_time'].replace(tzinfo=timezone.utc).strftime('%H:%M'))
                if times:
                    embed.add_field(name=_("Time Preferences"), value='\n'.join(times), inline=False)

        view = ApplicationView(self, tournament_id=tournament_id, squadron_id=squadron_id)
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=utils.get_ephemeral(interaction))
        try:
           await view.wait()
        finally:
            try:
                await msg.delete()
            except discord.NotFound:
                pass

    @tournament.command(description=_('Generate bracket'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    @utils.app_has_role('GameMaster')
    async def bracket(self, interaction: discord.Interaction, tournament_id: int):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT * FROM tm_matches WHERE tournament_id = %s", (tournament_id,))
                matches_df = pd.DataFrame(await cursor.fetchall())
                await cursor.execute("""
                    SELECT ts.squadron_id, ts.group_number, s.name 
                    FROM tm_squadrons ts JOIN squadrons s ON ts.squadron_id = s.id
                    WHERE ts.tournament_id = %s
                    AND ts.status = 'ACCEPTED'
                """, (tournament_id,))
                squadrons_df = pd.DataFrame(await cursor.fetchall())

        buffer = create_tournament_sheet(squadrons_df, matches_df, tournament_id)
        filename = f"tournament_{tournament_id}.xlsx"
        try:
            file = discord.File(buffer, filename=filename)
            # noinspection PyUnresolvedReferences
            await interaction.followup.send(file=file, ephemeral=utils.get_ephemeral(interaction))
        finally:
            buffer.close()

    @tournament.command(description=_('Show squadron preferences'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    @utils.app_has_role('GameMaster')
    async def preferences(self, interaction: discord.Interaction, tournament_id: Optional[int] = None):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        report = Report(bot=self.bot, plugin=self.plugin_name, filename="preferences.json")
        env = await report.render(tournament_id=tournament_id)
        try:
            await interaction.followup.send(file=discord.File(env.buffer, filename=env.filename), ephemeral=True)
        finally:
            env.buffer.close()

    # New command group "/match"
    match = Group(name="match", description=_("Commands to manage matches in a tournament"))

    @match.command(description=_('Generate matches'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=active_tournament_autocomplete)
    @utils.app_has_role('GameMaster')
    async def generate(self, interaction: discord.Interaction, tournament_id: int,
                       stage: Literal['group', 'elimination'], num_groups: Optional[int] = 4):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        tournament = await self.get_tournament(tournament_id)
        if not await yn_question(interaction,
                                 _("Do you want to generate {} matches for tournament {}?\n"
                                   "This will overwrite any existing **unflown** matches!").format(
                                     stage, tournament['name'])):
            await interaction.followup.send(_("Aborted."), ephemeral=True)
            return

        async with self.apool.connection() as conn:
            # check if we have flown matches already
            cursor = await conn.execute("""
                SELECT COUNT(*), 
                       COALESCE(SUM(CASE WHEN winner_squadron_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS flown,
                       COALESCE(MAX(stage), 0) AS stage
                FROM tm_matches 
                WHERE tournament_id = %s            
            """, (tournament_id,))
            all_matches, flown_matches, level = await cursor.fetchone()

            # initial setup
            if all_matches == 0 or stage == 'group':
                if flown_matches > 0:
                    await interaction.followup.send(
                        _("Can't restart group phase, there are already flown matches in this tournament."),
                        ephemeral=True)
                    return
                elif all_matches > 0 and not await yn_question(
                        interaction, _("Do you really want to generate new groups?")):
                    await interaction.followup.send(_("Aborted."), ephemeral=True)
                    return
                cursor = await conn.execute("""
                    SELECT squadron_id FROM tm_squadrons WHERE tournament_id = %s AND status = 'ACCEPTED'
                """, (tournament_id,))
            elif all_matches > flown_matches:
                await interaction.followup.send(
                    _("Can't generate new matches, not all matches have been flown yet."), ephemeral=True)
                return
            else:
                cursor = await conn.execute("""
                    WITH squadron_stats AS (
                        SELECT 
                            s.squadron_id,
                            s.group_number,
                            m.stage,
                            COUNT(CASE WHEN m.winner_squadron_id = s.squadron_id THEN 1 END) as matches_won,
                            SUM(CASE WHEN m.winner_squadron_id = m.squadron_blue THEN m.squadron_blue_rounds_won 
                                     WHEN m.winner_squadron_id = m.squadron_red THEN m.squadron_red_rounds_won 
                                     ELSE 0 END) as total_rounds_won,
                            ROW_NUMBER() OVER (
                                PARTITION BY s.group_number 
                                ORDER BY 4, 5
                            ) as rank
                        FROM tm_matches m
                        LEFT JOIN tm_squadrons s ON (m.tournament_id = s.tournament_id AND s.squadron_id = m.winner_squadron_id)
                        WHERE m.tournament_id = %s
                        AND m.winner_squadron_id IS NOT NULL
                        GROUP BY s.squadron_id, s.group_number, m.stage
                    )
                    SELECT squadron_id 
                    FROM squadron_stats
                    WHERE rank <= 2 OR stage > 1
                    ORDER BY group_number, matches_won DESC, total_rounds_won DESC;
                """, (tournament_id,))

            if cursor.rowcount == 2 and all_matches == flown_matches and flown_matches % 2 == 1:
                await interaction.followup.send(_("This tournament is finished already!"))
                return

            await interaction.followup.send(_("Generating {} matches for this tournament ...").format(stage))

            squadrons: list[tuple[int, float]] = []
            # read all squadrons and their ratings
            for row in await cursor.fetchall():
                rating = await Competitive.trueskill_squadron(self.node, row[0])
                squadrons.append((row[0], Competitive.calculate_rating(rating)))

            # read all available servers
            servers: list[str] = []
            async for row in await conn.execute("""
                SELECT server_name FROM campaigns_servers s
                JOIN campaigns c ON s.campaign_id = c.id
                JOIN tm_tournaments t ON c.name = t.campaign
                WHERE t.tournament_id = %s
            """, (tournament_id,)):
                servers.append(row[0])

            # read the available times
            times: list[datetime] = []
            async for row in await conn.execute("SELECT start_time FROM tm_available_times WHERE tournament_id = %s",
                                                (tournament_id,)):
                times.append(row[0])

            try:
                # create matches
                if stage == 'elimination':
                    groups = []
                    matches = create_elimination_matches(squadrons)
                    phase = TOURNAMENT_PHASE.START_ELIMINATION_PHASE
                else: # group stage
                    groups = create_groups(squadrons, num_groups)
                    matches = create_group_matches(groups)
                    phase = TOURNAMENT_PHASE.START_GROUP_PHASE

                # assign the groups to the squadrons
                async with conn.transaction():
                    if stage == 'group':
                        # update the groups, if any
                        for idx, group in enumerate(groups):
                            for squadron_id in group:
                                await conn.execute("""
                                    UPDATE tm_squadrons SET group_number = %s 
                                    WHERE tournament_id = %s AND squadron_id = %s
                                """, (idx + 1, tournament_id, squadron_id))

                    # delete old matches
                    cursor = await conn.execute("""
                        DELETE FROM tm_matches
                        WHERE tournament_id = %s AND winner_squadron_id IS NULL
                        RETURNING *
                    """, (tournament_id, ))
                    # we have not deleted anything, so we are creating a new stage
                    if cursor.rowcount == 0:
                        level += 1

                    # store new matches in the database
                    for idx, match in enumerate(matches):
                        server = servers[idx % len(servers)]
                        time = times[idx % len(times)]
                        await conn.execute("""
                           INSERT INTO tm_matches(tournament_id, stage, server_name, squadron_red, squadron_blue)
                           VALUES (%s, %s, %s, %s, %s)
                        """, (tournament_id, level, server, match[0], match[1]))

                await self.bot.audit(f"generated matches for tournament {tournament['name']}.",
                                     user=interaction.user)
            except ValueError as ex:
                await interaction.followup.send(f"Error: {ex}")
                return

        asyncio.create_task(self.render_status_embed(tournament_id, phase=phase))
        embed = await self.render_matches(tournament=tournament, unflown=True)
        await interaction.followup.send(_("{} matches generated:").format(len(matches)), embed=embed,
                                        ephemeral=utils.get_ephemeral(interaction))

    @match.command(description=_('Create a manual match'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.describe(stage=_("The level of your match. If all matches on one level are finished, increase the level by one."))
    @app_commands.autocomplete(tournament_id=active_tournament_autocomplete)
    @app_commands.autocomplete(stage=stage_autocomplete)
    @app_commands.autocomplete(server_name=server_autocomplete)
    @app_commands.autocomplete(squadron_blue=valid_squadron_autocomplete)
    @app_commands.autocomplete(squadron_red=valid_squadron_autocomplete)
    @app_commands.autocomplete(day=date_autocomplete)
    @app_commands.autocomplete(time=time_autocomplete)
    @utils.app_has_role('GameMaster')
    async def create(self, interaction: discord.Interaction, tournament_id: int, stage: app_commands.Range[int, 1, 7],
                     server_name: str, squadron_blue: int, squadron_red: int, day: int, time: int):
        match_time = datetime.fromtimestamp(day, tz=timezone.utc) + timedelta(seconds=time)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO tm_matches(tournament_id, stage, match_time, server_name, squadron_red, squadron_blue) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (tournament_id, stage, match_time, server_name, squadron_red, squadron_blue))

        tournament = await self.get_tournament(tournament_id)
        blue = utils.get_squadron(self.node, squadron_id=squadron_blue)
        red = utils.get_squadron(self.node, squadron_id=squadron_red)
        await self.bot.audit(f"created a match for tournament {tournament['name']} "
                               f"between squadrons {blue['name']} and {red['name']}.", user=interaction.user)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Match created."), ephemeral=utils.get_ephemeral(interaction))

    async def render_matches(self, tournament: dict, unflown: Optional[bool] = False) -> Optional[discord.Embed]:
        embed = discord.Embed(color=discord.Color.blue())
        embed.title = _("Matches for Tournament {}").format(tournament['name'])#
        embed.set_thumbnail(url=self.bot.guilds[0].icon.url)
        squadrons_blue = []
        squadrons_red = []
        status = []
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM tm_matches WHERE tournament_id = %s 
                    ORDER BY winner_squadron_id DESC, round_number DESC, match_time
                """, (tournament['tournament_id'],))
                for row in await cursor.fetchall():
                    if row['winner_squadron_id'] and unflown:
                        continue
                    squadrons_blue.append(utils.get_squadron(self.node, squadron_id=row['squadron_blue'])['name'])
                    squadrons_red.append(utils.get_squadron(self.node, squadron_id=row['squadron_red'])['name'])
                    if row['winner_squadron_id']:
                        winner = utils.get_squadron(self.node, squadron_id=row['winner_squadron_id'])
                        status.append(_("Winner: {}").format(winner['name']))
                    elif row['round_number'] == 0:
                        status.append("Not started" if row['match_time'] is None else
                                      f"<t:{int(row['match_time'].replace(tzinfo=timezone.utc).timestamp())}:R>")
                    else:
                        status.append(_("Round: {}, {} : {}").format(row['round_number'],
                                                                     row['squadron_blue_rounds_won'],
                                                                     row['squadron_red_rounds_won']))
        # no data
        if not len(squadrons_blue):
            return None

        embed.add_field(name=_("Blue"), value='\n'.join(squadrons_blue), inline=True)
        embed.add_field(name=_("Red"), value='\n'.join(squadrons_red), inline=True)
        embed.add_field(name=_("Status"), value='\n'.join(status), inline=True)
        return embed

    @match.command(name="list", description=_('List matches'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=active_tournament_autocomplete)
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction, tournament_id: int, unflown: bool = False):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        tournament = await self.get_tournament(tournament_id)
        if not tournament:
            await interaction.followup.send(_("Tournament not found."), ephemeral=True)
            return

        embed = await self.render_matches(tournament, unflown)
        if not embed:
            await interaction.followup.send(_("No matches for tournament {} found.").format(tournament['name']),
                                            ephemeral=True)
            return
        await interaction.followup.send(embed=embed)

    async def setup_server_for_match(self, msg: discord.Message, embed: discord.Embed, server: Server, match: dict,
                                     channels: dict):
        config = self.get_config(server)
        squadrons = {
            'blue': utils.get_squadron(self.node, squadron_id=match['squadron_blue']),
            'red': utils.get_squadron(self.node, squadron_id=match['squadron_red'])
        }

        # backup the serversettings.lua
        filename = os.path.join(server.instance.home, 'Config', 'serverSettings.lua')
        orig_file = filename + '.orig'
        if not os.path.exists(orig_file):
            shutil.copy2(filename, orig_file)

        # enable / overwrite any coalition settings
        server.locals["coalitions"] = {
            "lock_time": "1 day",
            "allow_players_pool": False,
            "blue_role": squadrons['blue']['role'],
            "red_role": squadrons['red']['role']
        }

        # set coalition channels
        server.locals['channels']['blue'] = channels['blue']
        server.locals['channels']['red'] = channels['red']

        # setup streamer channel (replicates all events from red and blue)
        streamer_channel = config.get('channels', {}).get('streamer')
        if streamer_channel:
            server.locals['channels']['blue_events'] = streamer_channel
            server.locals['channels']['red_events'] = streamer_channel

        # dirty but works
        server._channels.clear()

        # Server should start paused
        advanced = server.settings['advanced']
        advanced |= {
            "resume_mode": 0,
            "pause_on_load": True,
            "sav_autosave": False,
            "maxPing": 300
        }

        # sanitize the server
        server.settings['require_pure_textures'] = True
        server.settings['require_pure_models'] = True
        server.settings['require_pure_clients'] = True
        server.settings['require_pure_scripts'] = True
        server.settings['listShuffle'] = False
        server.settings['listLoop'] = False
        if not config.get('allow_exports', False):
            advanced |= {
                "allow_ownship_export": False,
                "allow_object_export": False,
                "allow_sensor_export": False,
                "allow_players_pool": False,
                "disable_events": True
            }

        # set coalition passwords
        if config.get('coalition_passwords'):
            embed.description += _("\n- Setting coalition passwords...")
            await msg.edit(embed=embed)
            for coalition in [Coalition.BLUE, Coalition.RED]:
                password = str(random.randint(100000, 999999))
                await server.setCoalitionPassword(coalition, password)
                channel = self.bot.get_channel(channels[coalition.value])
                _embed = discord.Embed(color=discord.Color.blue(), title=_("**Get your team ready!**\n"))
                if squadrons[coalition.value]['image_url']:
                    _embed.set_thumbnail(url=squadrons[coalition.value]['image_url'])
                _embed.add_field(name=_("Coalition"), value=coalition.value.upper(), inline=True)
                _embed.add_field(name=_("Password"), value=password, inline=True)
                _embed.set_footer(text=_("You must not share the password with anyone outside your squadron!\n"
                                        "You will stay on the {} side throughout the whole match.").format(
                    coalition.value))
                await channel.send(embed=_embed)

        # assign all members of the respective squadrons to the respective side
        embed.description += _("\n- Setting coalitions for players...")
        await msg.edit(embed=embed)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM coalitions WHERE server_name = %s", (server.name, ))
                for coalition in ['blue', 'red']:
                    await conn.execute(f"""
                        INSERT INTO coalitions(server_name, player_ucid, coalition) 
                            SELECT %s, s.player_ucid, '{coalition}' 
                            FROM squadron_members s JOIN tm_matches m ON s.squadron_id = m.squadron_{coalition}
                            WHERE m.match_id = %s
                        ON CONFLICT (server_name, player_ucid) DO UPDATE SET coalition = '{coalition}'
                    """, (server.name, match['match_id']))

    async def prepare_mission(self, server: Server, match_id: int, round_number: int,
                              mission_id: Optional[int] = None) -> str:
        config = self.get_config(server)
        # set startindex or use last mission
        if mission_id is not None and server.settings['listStartIndex'] != mission_id + 1:
            await server.setStartIndex(mission_id + 1)

        # change the mission
        filename = await server.get_current_mission_file()
        # create a writable mission
        new_filename = utils.create_writable_mission(filename)
        # get the orig file
        orig_filename = utils.get_orig_file(new_filename)
        # and copy the orig file over
        shutil.copy2(orig_filename, new_filename)

        miz = MizFile(new_filename)
        preset_file = os.path.join(self.node.config_dir, config.get('presets', {}).get('file', 'presets.yaml'))
        # apply the initial presets
        for preset in config.get('presets', {}).get('initial', []):
            self.log.debug(f"Applying initial preset: {preset} ...")
            miz.apply_preset(utils.get_preset(self.node, preset, filename=preset_file))

        if round_number %2 == 0:
            for preset in config.get('presets', {}).get('even', []):
                self.log.debug(f"Applying even-round preset: {preset} ...")
                miz.apply_preset(utils.get_preset(self.node, preset, filename=preset_file))
        else:
            for preset in config.get('presets', {}).get('uneven', []):
                self.log.debug(f"Applying uneven-round preset: {preset} ...")
                miz.apply_preset(utils.get_preset(self.node, preset, filename=preset_file))

        async with self.apool.connection() as conn:
            async with conn.transaction():
                # apply the squadron presets
                for side in ['blue', 'red']:
                    # apply persistent presets
                    async for row in await conn.execute(f"""
                        SELECT preset, config FROM tm_persistent_choices c
                        JOIN tm_matches m ON c.match_id = m.match_id AND c.squadron_id = m.squadron_{side}
                        WHERE m.match_id = %s 
                    """, (match_id,)):
                        self.log.debug(f"Applying persistent preset for side {side}: {row[0]} ...")
                        miz.apply_preset(utils.get_preset(self.node, row[0], filename=preset_file), side=side, **row[1])
                    # apply choices
                    async for row in await conn.execute(f"""
                        SELECT preset, config FROM tm_choices c 
                        JOIN tm_matches m ON c.match_id = m.match_id AND c.squadron_id = m.squadron_{side}
                        WHERE m.match_id = %(match_id)s AND m.choices_{side}_ack = TRUE
                    """, {"match_id": match_id}):
                        self.log.debug(f"Applying custom preset for side {side}: {row[0]} ...")
                        miz.apply_preset(utils.get_preset(self.node, row[0], filename=preset_file), side=side, **row[1])

                # delete the choices from the database and update the acknoledgement
                await conn.execute("DELETE FROM tm_choices WHERE match_id = %s", (match_id,))
                await conn.execute("""
                    UPDATE tm_matches 
                    SET choices_blue_ack=FALSE, choices_red_ack=FALSE 
                    WHERE match_id = %s
                """, (match_id,))

        miz.save(new_filename)
        if new_filename != filename:
            self.log.info(f"  => New mission written: {new_filename}")
            await server.replaceMission(int(server.settings['listStartIndex']), new_filename)
        return new_filename

    async def change_tacview_output(self, server: Server, results: int):
        extensions = await server.list_extension()
        if 'Tacview' in extensions:
            await server.run_on_extension(
                extension='Tacview',
                method='change_config',
                config={
                    "target": f"<id:{results}>"
                }
            )

    async def start_match(self, server: Server, tournament_id: int, match_id: int, mission_id: Optional[int] = None,
                          round_number: Optional[int] = None):
        tournament = await self.get_tournament(tournament_id)
        match = await self.get_match(match_id)

        squadrons = {
            'blue': utils.get_squadron(self.node, squadron_id=match['squadron_blue']),
            'red': utils.get_squadron(self.node, squadron_id=match['squadron_red'])
        }

        # set the correct round number
        if not round_number:
            if match['round_number'] == 0:
                round_number = 1
            else:
                round_number = match['round_number'] if not match['winner_squadron_id'] else match['round_number'] + 1

        # Start the next round
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE tm_matches 
                    SET round_number = %s, 
                        match_time = NOW() AT TIME ZONE 'UTC',
                        choices_blue_ack = FALSE, 
                        choices_red_ack = FALSE
                    WHERE match_id = %s
                """, (round_number, match_id))

        channel_id = self.get_config(server).get('channels', {}).get('admin')
        channel = self.bot.get_channel(channel_id) or self.bot.get_admin_channel(server)
        if not server:
            await channel.send(_("Server {} not found.").format(match['server_name']))
            return

        embed = discord.Embed(color=discord.Color.blue(), title=_("Match Setup"))
        embed.description = _("- Creating the squadron channels ...")
        embed.set_thumbnail(url=TRAFFIC_LIGHTS['red'])
        msg = await channel.send(embed=embed)
        try:
            # open the channels
            channels = await self.open_channel(match_id, server)
        except (ValueError, PermissionError) as ex:
            await channel.send(_("Error during opening the channels: {}").format(ex))
            return

        # inform the squadrons that they can choose
        embed.description += _("\n- Inform the squadrons and wait for their initial choice ...")
        await msg.edit(embed=embed)
        self.eventlistener.tournaments[server.name] = tournament

        config = self.get_config(server)
        min_costs = min(choice['costs'] for choice in config['presets']['choices'].values())
        async with self.apool.connection() as conn:
            async with conn.transaction():
                for side in ['blue', 'red']:
                    squadron = await self.get_squadron(match_id, squadrons[side]['id'])
                    if squadron.points > min_costs:
                        channel = self.bot.get_channel(channels[side])
                        await channel.send(_("You can now use {} to chose your customizations!\n"
                                             "If you do not want to change anything, "
                                             "please run it and say 'Skip this round'").format(
                            (await utils.get_command(self.bot, group=self.match.name,
                                                     name=self.customize.name)).mention))
                    else:
                        await conn.execute(f"UPDATE tm_matches SET choices_{side}_ack = TRUE WHERE match_id = %s",
                                           (match_id,))

        # wait until all choices are finished
        await self.eventlistener.wait_until_choices_finished(server)

        # preparing the server
        embed.description += _("\n- Preparing server {} for the match ...").format(match['server_name'])
        await msg.edit(embed=embed)

        # make sure the server is stopped
        if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
            embed.description += _("\n- Shutting down server {} ...").format(match['server_name'])
            await msg.edit(embed=embed)
            await server.shutdown()
            await channel.send(_("Server {} shut down.").format(match['server_name']))

        # change settings
        await self.setup_server_for_match(msg, embed, server, match, channels)
        # find mission
        if not mission_id:
            mission = config.get('mission')
            if isinstance(mission, int):
                mission_id = mission
            elif isinstance(mission, str):
                for idx, mission_file in enumerate(await server.getMissionList()):
                    if mission in mission_file:
                        mission_id = idx
                        break
        # prepare the mission
        await self.prepare_mission(server, match_id, round_number, mission_id)
        # Starting the server up again
        embed.description += _("\n- Starting server {} ...").format(match['server_name'])
        embed.set_thumbnail(url=TRAFFIC_LIGHTS['amber'])
        await msg.edit(embed=embed)
        for i in range(0, 3):
            try:
                await server.startup(modify_mission=False, use_orig=False)
                break
            except (TimeoutError, asyncio.TimeoutError):
                addon = '. Retrying in 5 seconds ...' if i < 2 else '. Giving up.'
                self.log.warning(f"Timeout while starting server {server.name}{addon}")
                if i < 2:
                    await asyncio.sleep(5)
        else:
            embed.description = _("## Error during the startup of server\n{}: Timeout").format(server.name)
            embed.set_thumbnail(url=TRAFFIC_LIGHTS['red'])
            await msg.edit(embed=embed)
            return
        # Check if we need to forward Tacview
        results = config.get('channels', {}).get('results', -1)
        if results > 0:
            await self.change_tacview_output(server, results)
        embed.description += _("\n- Server {} started. Inform squadrons ...").format(match['server_name'])
        embed.set_thumbnail(url=TRAFFIC_LIGHTS['green'])
        await msg.edit(embed=embed)
        # inform everyone
        for side in ['blue', 'red']:
            channel: TextChannel = self.bot.get_channel(channels[side])
            embed = discord.Embed(color=discord.Color.blue(), title=_("The match is starting NOW!"))
            if squadrons[side]['image_url']:
                embed.set_thumbnail(url=squadrons[side]['image_url'])
            embed.add_field(name=_("Server"), value=server.name)
            embed.description = _("You can **now** join the server.")
            embed.add_field(name=_("IP:Port"), value=f"{server.node.public_ip}:{server.settings['port']}")
            embed.add_field(name=_("Password"), value=server.settings.get('password', ''))
            embed.set_footer(text=_("Please keep in mind that you can only use {} planes!").format(
                tournament['num_players']))
            await channel.send(embed=embed)
        info = self.get_info_channel()
        if info:
            asyncio.create_task(self.render_status_embed(tournament_id, phase=TOURNAMENT_PHASE.MATCH_RUNNING,
                                                         match_id=match_id))
            asyncio.create_task(self.render_info_embed(tournament_id, phase=TOURNAMENT_PHASE.MATCH_RUNNING,
                                                       match_id=match_id))

    @match.command(description=_('Start a match'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=active_tournament_autocomplete)
    @app_commands.rename(match_id="match")
    @app_commands.autocomplete(match_id=active_matches_autocomplete)
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=mission_autocomplete)
    @utils.app_has_role('GameMaster')
    async def start(self, interaction: discord.Interaction, tournament_id: int, match_id: int,
                    mission_id: Optional[int] = None, round_number: Optional[int] = None):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        match = await self.get_match(match_id)
        if match['winner_squadron_id']:
            await interaction.followup.send(_("This match is over already. You can not start another round."),
                                            ephemeral=True)
            return

        tournament = await self.get_tournament(tournament_id)
        squadrons = {
            'blue': utils.get_squadron(self.node, squadron_id=match['squadron_blue']),
            'red': utils.get_squadron(self.node, squadron_id=match['squadron_red'])
        }

        # set the correct round number
        if not round_number:
            if match['round_number'] == 0:
                round_number = 1
            else:
                round_number = match['round_number'] if not match['winner_squadron_id'] else match['round_number'] + 1

        if not await yn_question(
                interaction,
                _("Do you want to start round {} of the match between\n{} and {}??").format(
                    round_number, squadrons['blue']['name'], squadrons['red']['name']
                ), ephemeral=ephemeral):
            await interaction.followup.send(_("Aborted."), ephemeral=True)
            return

        server = self.bot.servers.get(match['server_name'])
        if not server:
            await interaction.followup.send(_("Server {} not found.").format(match['server_name']), ephemeral=True)
            return

        # start the match
        await self.start_match(server, tournament_id, match_id, mission_id, round_number)

        # audit event
        await self.bot.audit(f"started a match for tournament {tournament['name']} "
                             f"between squadrons {squadrons['blue']['name']} and {squadrons['red']['name']}.",
                             user=interaction.user)

    async def open_channel(self, match_id: int, server: Server) -> dict[str, int]:
        config = self.get_config(server)
        match = await self.get_match(match_id)
        category: CategoryChannel = self.bot.get_channel(config['channels']['category'])
        channels = {}
        for side in ['blue', 'red']:
            squadron = utils.get_squadron(self.node, squadron_id=match[f'squadron_{side}'])
            if not squadron['role']:
                raise ValueError(f"Squadron {squadron['name']} does not have a role set.")
            role = self.bot.get_role(squadron['role'])
            if not role:
                raise ValueError(f"Squadron {squadron['name']} has an invalid role.")

            async with self.apool.connection() as conn:
                cursor = await conn.execute(f"""
                    SELECT squadron_{side}_channel FROM tm_matches WHERE match_id = %s
                """, (match_id, ))
                channel_id = (await cursor.fetchone())[0]
                if channel_id == -1 or not self.bot.get_channel(channel_id):
                    opponent = utils.get_squadron(
                        self.node, squadron_id=match['squadron_red' if side == 'blue' else 'squadron_blue'])
                    channel_name = f"{squadron['name']} vs {opponent['name']}"
                    channel: TextChannel = await category.create_text_channel(name=channel_name)
                    channel_id = channel.id
                    overwrite = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        embed_links=True,
                        attach_files=True,
                        read_message_history=True,
                        use_application_commands=True
                    )
                    try:
                        await channel.set_permissions(role, overwrite=overwrite)
                        await channel.send(role.mention + _(", this is your private channel during the upcoming match!"))
                    except discord.Forbidden:
                        raise PermissionError("You need to give the bot the Manage Roles permission!")

                    async with conn.transaction():
                        await conn.execute(f"""
                            UPDATE tm_matches SET squadron_{side}_channel = %s 
                            WHERE match_id = %s
                        """, (channel_id, match_id))

            channels[side] = channel_id

        return channels

    async def close_channel(self, match_id: int):
        match = await self.get_match(match_id)
        for side in ['blue', 'red']:
            squadron = utils.get_squadron(self.node, squadron_id=match[f'squadron_{side}'])
            if not squadron['role']:
                raise ValueError(f"Squadron {squadron['name']} does not have a role set.")
            role = self.bot.get_role(squadron['role'])
            if not role:
                raise ValueError(f"Squadron {squadron['name']} has an invalid role.")
            try:
                channel = await self.get_squadron_channel(match_id, side)
                if channel:
                    await channel.edit(name=channel.name + " (closed)")
                await channel.set_permissions(role, overwrite=None)
            except discord.Forbidden:
                raise PermissionError("You need to give the bot the Manage Roles permission!")

    @match.command(description=_('Edit a match'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=active_tournament_autocomplete)
    @app_commands.rename(match_id="match")
    @app_commands.autocomplete(match_id=all_matches_autocomplete)
    @app_commands.autocomplete(winner_squadron_id=match_squadrons_autocomplete)
    @utils.app_has_role('GameMaster')
    async def edit(self, interaction: discord.Interaction, tournament_id: int, match_id: int,
                   winner_squadron_id: Optional[int] = None):
        match = await self.get_match(match_id)
        modal = utils.ConfigModal(title="Match Results", config={
            "squadron_blue_rounds_won": {
                "type": int,
                "label": _("Squadron blue rounds won"),
                "required": True
            },
            "squadron_red_rounds_won": {
                "type": int,
                "label": _("Squadron red rounds won"),
                "required": True
            },
            "match_time": {
                "type": datetime,
                "label": _("Match time (yyyy-mm-dd hh:mm:ss)"),
                "required": False
            }
        }, old_values=match)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if await modal.wait():
            await interaction.followup.send(_("Aborted."), ephemeral=True)
            return

        if modal.value.get('squadron_blue_rounds_won') > modal.value.get('squadron_red_rounds_won'):
            winner_squadron_id = match['squadron_blue']
        elif modal.value.get('squadron_red_rounds_won') > modal.value.get('squadron_blue_rounds_won'):
            winner_squadron_id = match['squadron_red']

        match_time = modal.value.get('match_time')
        try:
            match_time = datetime.strptime(match_time, "%Y-%m-%d %H:%M:%S") if match_time else None
        except ValueError:
            await interaction.followup.send(_("Invalid date format. Use yyyy-mm-dd hh:mm:ss."), ephemeral=True)
            return
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE tm_matches 
                    SET squadron_blue_rounds_won = %s, squadron_red_rounds_won = %s, match_time = %s 
                    WHERE match_id = %s
                """, (modal.value.get('squadron_blue_rounds_won'),
                      modal.value.get('squadron_red_rounds_won'),
                      match_time, match_id))

                if winner_squadron_id:
                    squadron = utils.get_squadron(self.node, squadron_id=winner_squadron_id)
                    if await utils.yn_question(
                            interaction, _("Do you really want to finish the match and make {} the winner?").format(
                                squadron['name']), ephemeral=True):
                        cursor = await conn.execute("""
                            UPDATE tm_matches SET winner_squadron_id = %s WHERE match_id = %s
                            RETURNING server_name
                        """, (winner_squadron_id, match_id))
                        server_name = (await cursor.fetchone())[0]
                        server = self.bot.servers[server_name]
                        self.reset_serversettings(server)
                    else:
                        await interaction.followup.send(_("Winner not updated."), ephemeral=True)

        if match['winner_squadron_id'] != winner_squadron_id \
            or match['squadron_blue_rounds_won'] != modal.value.get('squadron_blue_rounds_won') \
            or match['squadron_red_rounds_won'] != modal.value.get('squadron_red_rounds_won')\
            or match['match_time'] != match_time:
            await self.bot.audit(f"updated match {match_id} for tournament {tournament_id}.",
                                 user=interaction.user)
            await interaction.followup.send(_("Match updated."), ephemeral=True)
        else:
            await interaction.followup.send(_("Match not updated."), ephemeral=True)

        if await self.eventlistener.check_tournament_finished(tournament_id):
            await interaction.followup.send(_("You just finished the tournament!"), ephemeral=True)
        elif winner_squadron_id:
            await self.render_info_embed(tournament_id, phase=TOURNAMENT_PHASE.MATCH_FINISHED, match_id=match_id)
        await self.render_status_embed(tournament_id, match_id=match_id)

    @match.command(description=_('Customize the next round'))
    @app_commands.guild_only()
    async def customize(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                for coalition in ['blue', 'red']:
                    await cursor.execute(f"""
                        SELECT m.tournament_id, m.match_id, 
                               squadron_{coalition} AS squadron_id, choices_{coalition}_ack AS ack, 
                               server_name 
                        FROM tm_matches m 
                        JOIN tm_tournaments t ON m.tournament_id = t.tournament_id 
                        JOIN campaigns c ON t.campaign = c.name
                        WHERE c.start <= NOW() AT TIME ZONE 'UTC' AND squadron_{coalition}_channel = %s
                        AND COALESCE(c.stop, NOW() AT TIME ZONE 'UTC') >= NOW() AT TIME ZONE 'UTC'
                        AND m.round_number > 0 and m.winner_squadron_id IS NULL
                    """, (interaction.channel.id, ))
                    row = await cursor.fetchone()
                    if not row:
                        continue
                    if row['ack']:
                        await interaction.followup.send(_("You already made your choice. Wait for the next round!"),
                                                        ephemeral=True)
                        return
                    tournament_id = row['tournament_id']
                    match_id = row['match_id']
                    squadron_id = row['squadron_id']
                    break
                else:
                    await interaction.followup.send(_("{} has to be used in the respective coalition channel.").format(
                        (await utils.get_command(self.bot, group=self.match.name, name=self.customize.name)).mention),
                        ephemeral=True)
                    return

        # check if a match is running
        server = self.bus.servers[row['server_name']]
        if server.status == Status.RUNNING:
            await interaction.followup.send(_("You can not choose during a running match!"), ephemeral=True)
            return

        admins = utils.get_squadron_admins(self.node, squadron_id)
        if interaction.user.id not in admins and not utils.check_roles(self.bot.roles['GameMaster'], interaction.user):
            await interaction.followup.send(
                f"You need to be an admin of the squadron {squadron_id} or a Game Master to use this command.",
                ephemeral=True
            )
            return

        view = ChoicesView(self, tournament_id=tournament_id, match_id=match_id, squadron_id=squadron_id,
                           config=self.get_config(server))
        embed = await view.render()
        # noinspection PyUnresolvedReferences
        if not view.children[0].options:
            await interaction.followup.send(_("You do not have enough squadron credits to buy a choice."),
                                            ephemeral=True)
            return
        msg = await interaction.followup.send(view=view, embed=embed, ephemeral=ephemeral)
        try:
            if await view.wait():
                return

            if view.acknowledged is True:
                embed = await view.render()
                embed.description = None
                embed.set_footer(text=None)

            if view.acknowledged is None or (view.acknowledged is True and not await utils.yn_question(
                    interaction, _("Are you sure?\nYour settings will be directly applied to the next round."),
                    embed=embed, ephemeral=True)):
                await interaction.followup.send(
                    _("Your choices were saved.\n"
                      "If you want them to be applied to the next round, press 'Confirm & Buy'."))
                return

            async with self.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        UPDATE tm_matches
                        SET 
                            choices_blue_ack = CASE
                                WHEN squadron_blue = %(squadron_id)s THEN true
                                ELSE choices_blue_ack
                            END,
                            choices_red_ack  = CASE
                                WHEN squadron_red = %(squadron_id)s THEN true
                                ELSE choices_red_ack
                            END
                        WHERE 
                            (squadron_blue = %(squadron_id)s OR squadron_red = %(squadron_id)s)
                            AND match_id = %(match_id)s
                    """, {"match_id": match_id, "squadron_id": squadron_id})
            if not view.acknowledged:
                await interaction.followup.send(_("You decided to not buy any customizations in this round."))
            else:
                embed.title = _("Invoice")
                embed.set_footer(text=_("Thank you for your purchase!"))
                await interaction.followup.send(embed=embed)
        finally:
            try:
                await msg.delete()
            except discord.NotFound:
                pass

    # New command group "/tickets"
    tickets = Group(name="tickets", description=_("Commands to manage tickets in a tournament"))

    @tickets.command(name="list", description=_('List tickets of a squadron'))
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=active_tournament_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @app_commands.autocomplete(squadron_id=valid_squadron_autocomplete)
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction, tournament_id: int, squadron_id: int):
        embed = discord.Embed(colour=discord.Colour.blue(), title=_("Your Tickets"))
        ticket_names = []
        ticket_counts = []
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                async for row in await cursor.execute(
                        "SELECT * FROM tm_tickets WHERE tournament_id = %s AND squadron_id = %s",
                        (tournament_id, squadron_id)):
                    ticket_names.append(row['ticket_name'])
                    ticket_counts.append(row['ticket_count'])
        if not ticket_names:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Your squadron does not have any tickets."), ephemeral=True)
            return

        embed.add_field(name=_("Tickets"),
                        value="\n".join([f"{y} x {x}" for x, y in zip(ticket_names, ticket_counts)]))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @tasks.loop(minutes=1.0)
    async def match_scheduler(self):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # for all active tournaments
                await cursor.execute("""
                    SELECT t.tournament_id, t.campaign AS name
                    FROM tm_tournaments t JOIN campaigns c ON t.campaign = c.name 
                    WHERE c.start <= NOW() AT TIME ZONE 'UTC'
                    AND COALESCE(c.stop, NOW() AT TIME ZONE 'UTC') >= NOW() AT TIME ZONE 'UTC'
                    ORDER BY campaign
                """)
                tournaments = await cursor.fetchall()
                for tournament in tournaments:
                    # warn squadrons prior to their matches
                    await cursor.execute("""
                        SELECT match_id, match_time, squadron_blue, squadron_red
                        FROM tm_matches m 
                        WHERE tournament_id = %s
                        AND round_number = 0
                        AND winner_squadron_id IS NULL
                        AND match_time IS NOT NULL
                        AND 
                        (
                            DATE_TRUNC('minute', match_time) = DATE_TRUNC('minute', NOW() AT TIME ZONE 'UTC' - interval '24 hours')
                        ) OR (
                            DATE_TRUNC('minute', match_time) = DATE_TRUNC('minute', NOW() AT TIME ZONE 'UTC' - interval '1 hour')
                        )
                    """, (tournament['tournament_id'], ))
                    for match in await cursor.fetchall():
                        match_time = int(match['match_time'].replace(tzinfo=timezone.utc).timestamp())
                        squadron_blue = utils.get_squadron(self.node, squadron_id=match['squadron_blue'])
                        squadron_red = utils.get_squadron(self.node, squadron_id=match['squadron_red'])
                        await self.inform_squadron(
                            tournament_id=tournament['tournament_id'],
                            squadron_id=match['squadron_blue'],
                            message=_("Your next match against {name} starts <t:{time}:R>!").format(
                                name=squadron_red['name'], time=match_time))
                        await self.inform_squadron(
                            tournament_id=tournament['tournament_id'],
                            squadron_id=match['squadron_red'],
                            message=_("Your next match against {name} starts <t:{time}:R>!").format(
                                name=squadron_blue['name'], time=match_time))

                    # start scheduled matches if there is no one running already
                    await cursor.execute("""
                        SELECT match_id, server_name, squadron_blue, squadron_red
                        FROM tm_matches m 
                        WHERE tournament_id = %s
                        AND round_number = 0
                        AND winner_squadron_id IS NULL
                        AND match_time IS NOT NULL
                        AND match_time < NOW() AT TIME ZONE 'UTC'
                        AND server_name NOT IN (
                            SELECT server_name 
                            FROM tm_matches
                            WHERE tournament_id = m.tournament_id
                            AND round_number > 0
                            AND winner_squadron_id IS NULL
                        )
                    """, (tournament['tournament_id'], ))
                    server_names = []
                    for match in await cursor.fetchall():
                        # we can only start ONE match per server at a time
                        if match['server_name'] in server_names:
                            continue
                        server_names.append(match['server_name'])
                        server = self.bus.servers[match['server_name']]
                        # we must not start a match if the server is (still?) running
                        if server.status == Status.RUNNING:
                            continue

                        # start the match
                        await self.start_match(server, tournament_id=tournament['tournament_id'],
                                               match_id=match['match_id'])
                        # audit event
                        squadron_blue = utils.get_squadron(self.node, squadron_id=match['squadron_blue'])
                        squadron_red = utils.get_squadron(self.node, squadron_id=match['squadron_red'])
                        await self.bot.audit(f"Scheduler started a match for tournament {tournament['name']} "
                                             f"between squadrons {squadron_blue['name']} and {squadron_red['name']}.")

    @match_scheduler.before_loop
    async def before_match_scheduler(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    if 'competitive' not in bot.plugins:
        raise PluginRequiredError('competitive')

    await bot.add_cog(Tournament(bot, TournamentEventListener))
