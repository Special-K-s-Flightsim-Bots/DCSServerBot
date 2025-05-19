import asyncio
import discord
import os
import pandas as pd
import random
import shutil

from core import Plugin, Group, utils, get_translation, PluginRequiredError, Status, Coalition, yn_question, Server, \
    MizFile, DataObjectFactory, async_cache, Report
from datetime import datetime, timezone, timedelta
from discord import app_commands, TextChannel, CategoryChannel, NotFound
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
    if not await interaction.command._check_can_run(interaction):
        return []
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
                ORDER BY 1
            """, (tournament_id, ))
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

    async def render_info_embed(self, tournament_id: int, *,
                                phase: TOURNAMENT_PHASE = TOURNAMENT_PHASE.SIGNUP,
                                match_id: Optional[int] = None) -> None:
        tournament = await self.get_tournament(tournament_id)
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT COUNT(*) FROM tm_squadrons WHERE tournament_id = %s AND status = 'ACCEPTED'
            """, (tournament_id,))
            num_squadrons = (await cursor.fetchone())[0]

        embed = discord.Embed(color=discord.Color.blue(), title=f"Tournament {tournament['name']} Information")
        buffer = None

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
            embed.add_field(name=_("# Subscriptions"), value=str(num_squadrons))
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

        elif phase == TOURNAMENT_PHASE.MATCH_RUNNING:
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

        else: # TOURNAMENT_PHASE.FINISHED:
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

        #embed.set_thumbnail(url=self.bot.guilds[0].icon.url)
        embed.description = message

        if buffer:
            file = discord.File(fp=buffer, filename=f"tournament_{tournament_id}.png")
            embed.set_image(url=f"attachment://tournament_{tournament_id}.png")
        else:
            file = None
        try:
            # create a persistent message
            channel_id = self.get_config().get('channels', {}).get('info')
            await self.bot.setEmbed(embed_name=f"tournament_{tournament_id}", embed=embed, file=file,
                                    channel_id=channel_id)
        finally:
            if buffer:
                buffer.close()

    @tournament.command(description='Create a tournament')
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

        await self.render_info_embed(tournament_id, phase=TOURNAMENT_PHASE.SIGNUP)

    @staticmethod
    def reset_serversettings(server: Server):
        filename = os.path.join(server.instance.home, 'Config', 'serverSettings.lua')
        orig_file = filename + '.orig'
        if os.path.exists(orig_file):
            shutil.copy2(orig_file, filename)

    @tournament.command(description='Finish a tournament')
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

    @tournament.command(description='Delete a tournament')
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
            msg = await self.bot.fetch_embed(embed_name=f"tournament_{tournament_id}", channel=self.get_info_channel())
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

    @tournament.command(description='Signup to a tournament')
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete_admin)
    @utils.squadron_role_check()
    async def signup(self, interaction: discord.Interaction, tournament_id: int, squadron_id: int):
        modal = ApplicationModal()
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if await modal.wait():
            await interaction.followup.send(_("Aborted."), ephemeral=True)
            return
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
                    for x in await self.node.get_available_modules()
                    if x.endswith('_terrain')
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
                    """, (tournament_id, squadron_id, modal.application_text.value))
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

    @tournament.command(description='Signup to a tournament')
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @app_commands.autocomplete(squadron_id=reject_squadron_autocomplete)
    @utils.squadron_role_check()
    async def withdraw(self, interaction: discord.Interaction, tournament_id: int, squadron_id: int):
        tournament = await self.get_tournament(tournament_id)
        squadron = utils.get_squadron(self.node, squadron_id=squadron_id)
        if tournament['start'] <= datetime.now(timezone.utc):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("You can not withdraw from an active tournament.", ephemeral=True)
            return
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    DELETE FROM tm_squadrons 
                    WHERE tournament_id = %s AND squadron_id = %s
                    """, (tournament_id, squadron_id))

        await self.bot.audit(f"withdrew squadron {squadron['name']} from tournament {tournament['name']}.",
                             user=interaction.user)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(
            _("Your squadron has been withdrawn from tournament {}.").format(tournament['name']), ephemeral=True)

        admin_channel = self.get_admin_channel()
        if admin_channel:
            await admin_channel.send(
                _("Squadron {} withdrew from tournament {}.").format(squadron['name'], tournament['name']))

    @tournament.command(description='Verfiy Applications')
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @app_commands.autocomplete(squadron_id=all_squadron_autocomplete)
    @utils.has_role('GameMaster')
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

        view = ApplicationView(self, tournament_id=tournament_id, squadron_id=squadron_id)
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=utils.get_ephemeral(interaction))
        try:
           await view.wait()
        finally:
            try:
                await msg.delete()
            except discord.NotFound:
                pass

    @tournament.command(description='Generate bracket')
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    @utils.has_role('GameMaster')
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

    @tournament.command(description='Show squadron preferences')
    @app_commands.guild_only()
    @app_commands.rename(tournament_id="tournament")
    @app_commands.autocomplete(tournament_id=tournament_autocomplete)
    @utils.has_role('GameMaster')
    async def preferences(self, interaction: discord.Interaction, tournament_id: Optional[int] = None):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        report = Report(bot=self.bot, plugin=self.plugin_name, filename="preferences.json")
        env = await report.render(tournament_id=tournament_id)
        try:
            await interaction.followup.send(file=discord.File(env.buffer, filename=env.filename), ephemeral=True)
        finally:
            env.buffer.close()

    # New command group "/matches"
    match = Group(name="match", description="Commands to manage matches in a tournament")

    @match.command(description='Generate matches')
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
        squadrons: list[tuple[int, float]] = []
        servers: list[str] = []

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

            # read all squadrons and their ratings
            for row in await cursor.fetchall():
                rating = await Competitive.trueskill_squadron(self.node, row[0])
                squadrons.append((row[0], rating.mu - 3.0 * rating.sigma))

            # read all available servers
            async for row in await conn.execute("""
                SELECT server_name FROM campaigns_servers s
                JOIN campaigns c ON s.campaign_id = c.id
                JOIN tm_tournaments t ON c.name = t.campaign
                WHERE t.tournament_id = %s
            """, (tournament_id,)):
                servers.append(row[0])

            try:
                # create matches
                if stage == 'elimination':
                    groups = []
                    matches = create_elimination_matches(squadrons)
                else: # group stage
                    groups = create_groups(squadrons, num_groups)
                    matches = create_group_matches(groups)

                # assign the available servers to the matcheso
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
                        await conn.execute("""
                           INSERT INTO tm_matches(tournament_id, stage, server_name, squadron_red, squadron_blue)
                           VALUES (%s, %s, %s, %s, %s)
                        """, (tournament_id, level, server, match[0], match[1]))

                await self.bot.audit(f"generated matches for tournament {tournament['name']}.",
                                     user=interaction.user)
            except ValueError as ex:
                await interaction.followup.send(f"Error: {ex}")
                return

        asyncio.create_task(self.render_info_embed(tournament_id, phase=TOURNAMENT_PHASE.START_GROUP_PHASE))
        embed = await self.render_matches(tournament=tournament, unflown=True)
        await interaction.followup.send(_("{} matches generated:").format(len(matches)), embed=embed,
                                        ephemeral=utils.get_ephemeral(interaction))

    @match.command(description='Create a manual match')
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
        rounds = tournament['rounds']
        squadrons_blue = []
        squadrons_red = []
        status = []
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT * FROM tm_matches WHERE tournament_id = %s",
                                     (tournament['tournament_id'],))
                for row in await cursor.fetchall():
                    if row['winner_squadron_id'] and unflown:
                        continue
                    squadrons_blue.append(utils.get_squadron(self.node, squadron_id=row['squadron_blue'])['name'])
                    squadrons_red.append(utils.get_squadron(self.node, squadron_id=row['squadron_red'])['name'])
                    if row['round_number'] == 0:
                        status.append("Not started")
                    elif row['round_number'] <= rounds and row['winner_squadron_id'] is None:
                        status.append(_("Round: {}, {} : {}").format(row['round_number'],
                                                                     row['squadron_blue_rounds_won'],
                                                                     row['squadron_red_rounds_won']))
                    else:
                        winner = utils.get_squadron(self.node, squadron_id=row['winner_squadron_id'])
                        status.append(_("Winner: {}").format(winner['name']))
        # no data
        if not len(squadrons_blue):
            return None

        embed.add_field(name=_("Blue"), value='\n'.join(squadrons_blue), inline=True)
        embed.add_field(name=_("Red"), value='\n'.join(squadrons_red), inline=True)
        embed.add_field(name=_("Status"), value='\n'.join(status), inline=True)
        return embed

    @match.command(name="list", description='List matches')
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

    async def setup_server_for_match(self, msg: discord.Message, messages: list[str], server: Server, match: dict,
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
            "resume_mode": 0
        }

        # sanitize the server
        server.settings['require_pure_textures'] = True
        server.settings['require_pure_models'] = True
        server.settings['require_pure_clients'] = True
        server.settings['require_pure_scripts'] = True
        server.settings['listShuffle'] = False
        server.settings['listLoop'] = False
        if config.get('allow_exports', False) is False:
            advanced |= {
                "allow_ownship_export": False,
                "allow_object_export": False,
                "allow_sensor_export": False,
                "allow_players_pool": False,
                "disable_events": True,
                "resume_mode": 0,
                "pause_on_load": True,
                "sav_autosave": False,
                "maxPing": 300
            }

        results = config.get('channels', {}).get('results', -1)
        if results > 0:
            extensions = await server.list_extension()
            if 'Tacview' in extensions:
                await server.config_extension(
                    name='Tacview',
                    config={
                        "target": f"<id:{results}>"
                    }
                )

        # set coalition passwords
        if config.get('coalition_passwords'):
            messages.append(_("Setting coalition passwords..."))
            await msg.edit(content='\n'.join(messages))
            for coalition in [Coalition.BLUE, Coalition.RED]:
                password = str(random.randint(100000, 999999))
                await server.setCoalitionPassword(coalition, password)
                channel = self.bot.get_channel(channels[coalition.value])
                embed = discord.Embed(color=discord.Color.blue(), title=_("**Get your team ready!**\n"))
                if squadrons[coalition.value]['image_url']:
                    embed.set_thumbnail(url=squadrons[coalition.value]['image_url'])
                embed.add_field(name=_("Coalition"), value=coalition.value.upper(), inline=True)
                embed.add_field(name=_("Password"), value=password, inline=True)
                embed.set_footer(text=_("You must not share the password with anyone outside your squadron!\n"
                                        "You will stay on the {} side throughout the whole match.").format(
                    coalition.value))
                await channel.send(embed=embed)

        # assign all members of the respective squadrons to the respective side
        messages.append(_("Setting coalitions for players..."))
        await msg.edit(content='\n'.join(messages))
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

    async def prepare_mission(self, server: Server, match_id: int, mission_id: Optional[int] = None) -> str:
        config = self.get_config(server)
        # set startindex or use last mission
        if mission_id is not None and server.settings['listStartIndex'] != mission_id + 1:
            await server.setStartIndex(mission_id + 1)

        # load the presets
        preset_file = config.get('presets', {}).get('file', 'presets.yaml')
        with open(os.path.join(self.node.config_dir, preset_file), mode='r', encoding='utf-8') as infile:
            all_presets = yaml.load(infile)

        # change the mission
        filename = await server.get_current_mission_file()
        # create a writable mission
        new_filename = utils.create_writable_mission(filename)
        # get the orig file
        orig_filename = utils.get_orig_file(new_filename)
        # and copy the orig file over
        shutil.copy2(orig_filename, new_filename)

        miz = MizFile(new_filename)
        # apply the initial presets
        for preset in config.get('presets', {}).get('initial', []):
            self.log.debug(f"Applying preset {preset} ...")
            miz.apply_preset(all_presets[preset])

        async with self.apool.connection() as conn:
            async with conn.transaction():
                # apply the squadron presets
                for side in ['blue', 'red']:
                    async for row in await conn.execute(f"""
                        SELECT preset, num FROM tm_choices c 
                        JOIN tm_matches m ON c.match_id = m.match_id AND c.squadron_id = m.squadron_{side}
                        WHERE m.match_id = %(match_id)s
                    """, {"match_id": match_id}):
                        self.log.debug(f"Applying preset {row[0]} ...")
                        miz.apply_preset(all_presets[row[0]], side=side.upper(), num=row[1])

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

    @match.command(description='Start a match')
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

        if not round_number:
            round_number = match['round_number'] + 1

        if not await yn_question(
                interaction,
                _("Do you want to start round {} of the match between {} and {}??").format(
                    round_number, squadrons['blue']['name'], squadrons['red']['name']
                ), ephemeral=ephemeral):
            await interaction.followup.send(_("Aborted."), ephemeral=True)
            return

        # audit event
        await self.bot.audit(f"started a match for tournament {tournament['name']} "
                             f"between squadrons {squadrons['blue']['name']} and {squadrons['red']['name']}.",
                             user=interaction.user)

        # Start the next round
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE tm_matches SET round_number = %s, choices_blue_ack = FALSE, choices_red_ack = FALSE
                    WHERE match_id = %s
                """, (round_number, match_id))

        server = self.bot.servers.get(match['server_name'])
        if not server:
            await interaction.followup.send(_("Server {} not found.").format(match['server_name']), ephemeral=True)
            return

        messages = [_("Create the squadron channels ...")]
        msg = await interaction.followup.send('\n'.join(messages), ephemeral=ephemeral)
        try:
            # open the channels
            channels = await self.open_channel(match_id, server)
        except (ValueError, PermissionError) as ex:
            await interaction.followup.send(_("Error during opening the channels: {}").format(ex), ephemeral=True)
            return

        # inform the squadrons that they can choose
        messages.append(_("Inform the squadrons and wait for their initial choice ..."))
        await msg.edit(content='\n'.join(messages))
        self.eventlistener.tournaments[server.name] = tournament

        config = self.get_config(server)
        min_costs = min(choice['costs'] for choice in config['presets']['choices'].values())
        async with self.apool.connection() as conn:
            async with conn.transaction():
                for side in ['blue', 'red']:
                    squadron = await self.get_squadron(match_id, squadrons[side]['id'])
                    if squadron.points > min_costs:
                        channel = self.bot.get_channel(channels[side])
                        await channel.send(_("You can now use {} to chose your customizations!").format(
                            (await utils.get_command(self.bot, group=self.match.name,
                                                     name=self.customize.name)).mention))
                    else:
                        await conn.execute(f"UPDATE tm_matches SET choices_{side}_ack = TRUE WHERE match_id = %s",
                                           (match_id,))

        # wait until all choices are finished
        await self.eventlistener.wait_until_choices_finished(server)

        # preparing the server
        messages.append(_("Preparing server {} for the match ...").format(match['server_name']))
        await msg.edit(content='\n'.join(messages))

        # make sure the server is stopped
        if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
            messages.append(_("Shutting down server {} ...").format(match['server_name']))
            await msg.edit(content='\n'.join(messages))
            await server.shutdown()
            await interaction.followup.send(_("Server {} shut down.").format(match['server_name']), ephemeral=ephemeral)

        # change settings
        await self.setup_server_for_match(msg, messages, server, match, channels)
        # prepare the mission
        await self.prepare_mission(server, match_id, mission_id)
        # Starting the server up again
        messages.append(_("Starting server {} ...").format(match['server_name']))
        await msg.edit(content='\n'.join(messages))
        await server.startup(modify_mission=False, use_orig=False)
        messages.append(_("Server {} started. Inform squadrons ...").format(match['server_name']))
        await msg.edit(content='\n'.join(messages))
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
            asyncio.create_task(self.render_info_embed(tournament_id, phase=TOURNAMENT_PHASE.MATCH_RUNNING,
                                                       match_id=match_id))

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
                        read_message_history=True
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

    @match.command(description='Edit a match')
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

    @match.command(description='Customize the next round')
    @app_commands.guild_only()
    async def customize(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        for coalition in ['blue', 'red']:
            async with self.apool.connection() as conn:
                cursor = await conn.execute(f"""
                    SELECT m.match_id, squadron_{coalition}, choices_{coalition}_ack, server_name 
                    FROM tm_matches m 
                    JOIN tm_tournaments t ON m.tournament_id = t.tournament_id 
                    JOIN campaigns c ON t.campaign = c.name
                    WHERE c.start <= NOW() AT TIME ZONE 'UTC' AND squadron_{coalition}_channel = %s
                    AND COALESCE(c.stop, NOW() AT TIME ZONE 'UTC') >= NOW() AT TIME ZONE 'UTC'
                    AND m.round_number > 0 and m.winner_squadron_id IS NULL
                """, (interaction.channel.id, ))
                row = await cursor.fetchone()
                if row:
                    if row[2]:
                        await interaction.followup.send(_("You already made your choice. Wait for the next round!"),
                                                        ephemeral=True)
                        return
                    match_id = row[0]
                    squadron_id = row[1]
                    break
                else:
                    continue
        else:
            await interaction.followup.send("{} has to be used in the respective coalition channel.".format(
                (await utils.get_command(self.bot, group=self.match.name, name=self.customize.name)).mention))
            return

        admins = utils.get_squadron_admins(self.node, squadron_id)
        if interaction.user.id not in admins and not utils.check_roles(self.bot.roles['GameMaster'], interaction.user):
            await interaction.followup.send(
                f"You need to be an admin of the squadron {squadron_id} or a Game Master to use this command.",
                ephemeral=True
            )
            return
        view = ChoicesView(self, match_id=match_id, squadron_id=squadron_id,
                           config=self.get_config(self.bot.servers[row[3]]))
        embed = await view.render()
        # noinspection PyUnresolvedReferences
        if not view.children[0].options:
            await interaction.followup.send(_("You do not have enough squadron credits to buy a choice."),
                                            ephemeral=True)
            return
        msg = await interaction.followup.send(view=view, embed=embed, ephemeral=ephemeral)
        try:
           await view.wait()
        finally:
            try:
                await msg.delete()
            except discord.NotFound:
                pass


async def setup(bot: DCSServerBot):
    if 'competitive' not in bot.plugins:
        raise PluginRequiredError('competitive')

    await bot.add_cog(Tournament(bot, TournamentEventListener))
