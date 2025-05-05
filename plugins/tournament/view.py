import discord
import numpy as np
import re

from core import Node, get_translation, utils
from discord import SelectOption
from discord.ui import Select, Button, Modal, TextInput, View
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .commands import Tournament

_ = get_translation(__name__.split('.')[1])

WARNING_ICON = "https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/warning.png?raw=true"


class TournamentModal(Modal):
    _num_rounds = TextInput(label=_("Number of rounds"), style=discord.TextStyle.short, min_length=1, max_length=2,
                            default="3", required=True)
    _num_players = TextInput(label=_("Number of players"), style=discord.TextStyle.short, min_length=1, max_length=2,
                             default="4", required=True)
    _times = TextInput(label=_("Preferred times (UTC)"), style=discord.TextStyle.short, required=False,
                       placeholder=_("Match times, comma separated in format HH:MM"))

    def __init__(self):
        super().__init__(title=_("Create a new tournament"))
        self.num_rounds = 3
        self.num_players = 2
        self.times = []
        self.error = None

    async def on_submit(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        try:
            self.num_rounds = int(self._num_rounds.value)
        except ValueError:
            raise ValueError(_("Number of rounds needs to be a number."))
        if self.num_rounds < 3:
            raise ValueError(_("Number of rounds needs to be at least 3."))
        try:
            self.num_players = int(self._num_players.value)
        except ValueError:
            raise ValueError(_("Number of players needs to be a number."))
        if self.num_players < 1:
            raise ValueError(_("Number of players needs to be at least 1."))
        if self._times.value.strip():  # Only process if times is not empty
            for time_str in self._times.value.split(','):
                time_str = time_str.strip()
                if not time_str:
                    continue

                if not re.match(r'^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$', time_str):
                    raise ValueError(f"Invalid time format: {time_str}. Please use HH:MM format (e.g., 13:45)")

                self.times.append(time_str)
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.error = error
        self.stop()


class ApplicationModal(Modal, title=_("Apply to a tournament")):
    application_text = TextInput(label=_("Application"), style=discord.TextStyle.long,
                                 placeholder=_("Please enter a short summary of your group and why you want to "
                                               "participate in this tournament."), required=True)

    async def on_submit(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()


class TimesSelectView(View):
    def __init__(self, options: list[SelectOption]):
        super().__init__()
        select = cast(Select, self.children[0])
        select.options = options
        select.max_values = len(options)
        self.result = None

    @discord.ui.select(placeholder=_("Select your preferred mach times"), min_values=1)
    async def callback(self, interaction: discord.Interaction, select: Select):
        self.result = select.values
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()


class RejectModal(Modal, title=_("Reject a squadron")):
    reason = TextInput(label=_("Reason"), style=discord.TextStyle.long,
                       placeholder=_("Please enter a reason why you decided to reject this squadron."),
                       required=True)

    async def on_submit(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()


class NumbersModal(Modal):
    def __init__(self, choice: str, costs: int, credits: int):
        super().__init__(title=_("How many {} do you want?").format(choice[:20]))
        self.costs = costs
        self.credits = credits
        self.textinput = TextInput(label=_("Count"), placeholder=_("Enter a number"), default="1",
                                   style=discord.TextStyle.short, required=True)
        self.add_item(self.textinput)
        self.result = 0
        self.error = None

    async def on_submit(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        if not self.textinput.value.isdigit():
            raise ValueError(_("Field needs to be a number."))
        value = int(self.textinput.value)
        if value * self.costs > self.credits:
            raise ValueError(_("You do not have enough credits to buy {} items.").format(value))
        self.result = value
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        self.error = str(error)
        self.stop()


class ChoicesView(View):
    def __init__(self, node: Node, match_id: int, squadron_id: int, config: dict):
        super().__init__()
        self.node = node
        self.match_id = match_id
        self.squadron_id = squadron_id
        self.config = config

    async def get_squadron_credits(self):
        async with self.node.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT sc.points FROM squadron_credits sc 
                JOIN campaigns c ON sc.campaign_id = c.id 
                JOIN tm_tournaments t ON t.campaign = c.name
                JOIN tm_matches tm ON tm.tournament_id = t.tournament_id
                WHERE squadron_id=%s AND tm.match_id = %s
            """, (self.squadron_id,self.match_id))
            if cursor.rowcount == 1:
                return (await cursor.fetchone())[0]
            else:
                return 0

    async def render(self) -> discord.Embed:
        credits = await self.get_squadron_credits()
        embed = discord.Embed(colour=discord.Colour.blue(), title=_("You have {} credit points.").format(credits))
        embed.description = ("Here you can select the presets to change the upcoming mission to your request.\n"
                             "Please keep in mind, that you will have to pay credit points, "
                             "according to the requested presets price.")
        async with self.node.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT preset, num FROM tm_choices WHERE match_id = %s AND squadron_id = %s
            """, (self.match_id, self.squadron_id))
            choices = [x for x in await cursor.fetchall()]
        if not choices:
            embed.add_field(name="Choices", value="No choices selected yet.", inline=False)
        else:
            presets = []
            costs = []
            number = []
            for choice in choices:
                preset = choice[0]
                presets.append(preset)
                cost = self.config['presets']['choices'][preset]
                costs.append(cost)
                number.append(choice[1])
            embed.add_field(name="Your selection", value="\n".join(presets))
            embed.add_field(name="Costs in Credits", value="\n".join([str(x) for x in costs]))
            embed.add_field(name="Count", value="\n".join([str(x) for x in number]))
            embed.add_field(name="Total cost", value=f"{np.sum(np.array(costs) * np.array(number))} credits",
                            inline=False)
        embed.set_footer(text="Please make your choice!")
        # build the selections
        self.clear_items()
        if choices:
            select = Select(placeholder="Remove a choice",
                            options=[
                                SelectOption(label=x[0], value=x[0])
                                for idx, x in enumerate(choices)
                                if idx < 25
                            ],
                            min_values=1, max_values=1)
            select.callback = self.remove_choice
            self.add_item(select)
        if len(choices) < len(self.config['presets']['choices']):
            select = Select(placeholder="Add a choice",
                            options=[
                                SelectOption(label=f"{x} (Cost={self.config['presets']['choices'][x]})", value=x)
                                for idx, x in enumerate(self.config['presets']['choices'].keys())
                                if idx < 25 and self.config['presets']['choices'][x] <= credits
                                   and x not in [x[0] for x in choices]
                            ], min_values=1, max_values=1)
            select.callback = self.add_choice
            self.add_item(select)
        button = Button(label="Save", style=discord.ButtonStyle.green)
        button.callback = self.save
        self.add_item(button)
        button = Button(label="Cancel", style=discord.ButtonStyle.red)
        button.callback = self.cancel
        self.add_item(button)
        return embed

    async def add_choice(self, interaction: discord.Interaction):
        choice = interaction.data['values'][0]
        credits = await self.get_squadron_credits()
        costs = self.config['presets']['choices'][choice]

        modal = NumbersModal(choice, costs, credits)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if not await modal.wait():
            num = modal.result
            if num and costs * num <= credits:
                async with self.node.apool.connection() as conn:
                    async with conn.transaction():
                        await conn.execute("""
                            INSERT INTO tm_choices (match_id, squadron_id, preset, num) 
                            VALUES (%s, %s, %s, %s)
                        """, (self.match_id, self.squadron_id, choice, num))
                        cursor = await conn.execute("""
                            UPDATE squadron_credits SET points = %s 
                            WHERE squadron_id = %s
                            AND campaign_id = (
                                SELECT campaign_id FROM tm_tournaments t JOIN tm_matches m 
                                ON t.tournament_id = m.tournament_id WHERE m.match_id = %s
                            )
                            RETURNING campaign_id
                        """, (credits - costs * num, self.squadron_id, self.match_id))
                        campaign_id = (await cursor.fetchone())[0]
                        await conn.execute("""
                            INSERT INTO squadron_credits_log (campaign_id, event, squadron_id, 
                                                              old_points, new_points, remark)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (campaign_id, 'match_choice', self.squadron_id, credits,
                              credits - costs * num, f'Bought {num} of {choice} during a match choice.', ))
            embed = await self.render()
            if modal.error:
                embed.set_footer(text=modal.error, icon_url=WARNING_ICON)
            await interaction.edit_original_response(embed=embed, view=self)

    async def remove_choice(self, interaction: discord.Interaction):
        choice = interaction.data['values'][0]
        credits = await self.get_squadron_credits()
        costs = self.config['presets']['choices'][choice]

        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        async with self.node.apool.connection() as conn:
            async with conn.transaction():
                cursor = await conn.execute("""
                    DELETE FROM tm_choices 
                    WHERE match_id = %s AND squadron_id = %s AND preset = %s
                    RETURNING num
                """, (self.match_id, self.squadron_id, choice))
                num = (await cursor.fetchone())[0]
                cursor = await conn.execute("""
                    UPDATE squadron_credits SET points = %s 
                    WHERE squadron_id = %s
                    AND campaign_id = (
                        SELECT campaign_id FROM tm_tournaments t JOIN tm_matches m 
                        ON t.tournament_id = m.tournament_id WHERE m.match_id = %s
                    )
                    RETURNING campaign_id
                """, (credits + costs * num, self.squadron_id, self.match_id))
                campaign_id = (await cursor.fetchone())[0]
                await conn.execute("""
                   INSERT INTO squadron_credits_log (campaign_id, event, squadron_id,
                                                     old_points, new_points, remark)
                   VALUES (%s, %s, %s, %s, %s, %s)
               """, (campaign_id, 'match_choice', self.squadron_id, credits,
                     credits + costs * num, f'Cancelled {num} of {choice} during a match choice.',))
        await interaction.edit_original_response(embed=await self.render(), view=self)

    async def save(self, interaction: discord.Interaction):
        async with self.node.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE tm_matches
                    SET 
                        choices_blue_ack = CASE 
                            WHEN squadron_blue = %(squadron_id)s THEN true 
                            ELSE choices_blue_ack 
                        END,
                        choices_red_ack = CASE 
                            WHEN squadron_red = %(squadron_id)s THEN true 
                            ELSE choices_red_ack 
                        END
                    WHERE 
                        (squadron_blue = %(squadron_id)s OR squadron_red = %(squadron_id)s)
                        AND match_id = %(match_id)s
                """, {"match_id": self.match_id, "squadron_id": self.squadron_id})
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Your selection will be applied to the next round."),
                                                ephemeral=True)
        self.stop()

    async def cancel(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()


class ApplicationView(View):

    def __init__(self, plugin: "Tournament", tournament_id: int, squadron_id: int):
        super().__init__()
        self.plugin = plugin
        self.tournament_id = tournament_id
        self.squadron_id = squadron_id

    async def inform_squadron(self, message: str):
        async with self.plugin.apool.connection() as conn:
            async for row in await conn.execute("""
                SELECT p.discord_id
                FROM players p JOIN squadron_members m ON p.ucid = m.player_ucid
                WHERE m.squadron_id = %s AND m.admin IS TRUE
            """, (self.squadron_id,)):
                user = self.plugin.bot.get_user(row[0])
                if user:
                    tournament = await self.plugin.get_tournament(self.tournament_id)
                    squadron = utils.get_squadron(self.plugin.node, squadron_id=self.squadron_id)
                    dm_channel = await user.create_dm()
                    await dm_channel.send(message.format(squadron=squadron['name'], tournament=tournament['name']))

    @discord.ui.button(label=_("Accept"), style=discord.ButtonStyle.green)
    async def on_accept(self, interaction: discord.Interaction, button: Button):
        async with self.plugin.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE tm_squadrons SET status = 'ACCEPTED' WHERE tournament_id = %s AND squadron_id = %s
                """, (self.tournament_id, self.squadron_id))

        # update the info embed
        channel_id = self.plugin.get_config().get('channels', {}).get('info')
        if channel_id:
            embed = await self.plugin.render_info_embed(self.tournament_id)
            # create a persistent message
            await self.plugin.bot.setEmbed(embed_name=f"tournament_{self.tournament_id}", embed=embed,
                                           channel_id=channel_id)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Squadron accepted."))
        await self.inform_squadron(_("Your squadron {squadron} was accepted for tournament {tournament}."))
        self.stop()

    @discord.ui.button(label=_("Reject"), style=discord.ButtonStyle.red)
    async def on_reject(self, interaction: discord.Interaction, button: Button):
        async with self.plugin.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE tm_squadrons SET status = 'REJECTED' WHERE tournament_id = %s AND squadron_id = %s
                """, (self.tournament_id, self.squadron_id))
        modal = RejectModal()
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if await modal.wait():
            reason = ""
        else:
            reason = modal.reason.value

        # update the info embed
        channel_id = self.plugin.get_config().get('channels', {}).get('info')
        if channel_id:
            embed = await self.plugin.render_info_embed(self.tournament_id)
            # create a persistent message
            await self.plugin.bot.setEmbed(embed_name=f"tournament_{self.tournament_id}", embed=embed,
                                           channel_id=channel_id)

        await interaction.followup.send(_("Squadron rejected."))
        message = _("Your squadron {squadron} was rejected from tournament {tournament}.")
        if reason:
            message += _("\nReason: {}").format(reason)
        await self.inform_squadron(message)
        self.stop()

    @discord.ui.button(label=_("Cancel"), style=discord.ButtonStyle.secondary)
    async def on_cancel(self, interaction: discord.Interaction, button: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()
