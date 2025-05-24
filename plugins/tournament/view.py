import discord
import numpy as np
import re

from psycopg.types.json import Json

from core import get_translation, utils
from discord import SelectOption
from discord.ui import Select, Button, Modal, TextInput, View
from typing import TYPE_CHECKING, cast, Optional

from .const import TOURNAMENT_PHASE

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
        if self.num_rounds % 2 == 0:
            raise ValueError(_("Number of rounds needs to be an odd value."))
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


class SignupView(View):
    def __init__(self, times_options: list[SelectOption], terrain_options: list[SelectOption]):
        super().__init__()
        # set times options
        select = cast(Select, self.children[0])
        select.options = times_options
        select.max_values = len(times_options)
        # set map options
        select = cast(Select, self.children[1])
        select.options = terrain_options
        select.max_values = len(terrain_options)
        self.times = []
        self.terrains = []

    @discord.ui.select(placeholder=_("Select your preferred mach times"), min_values=1)
    async def times_callback(self, interaction: discord.Interaction, select: Select):
        self.times = select.values
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()

    @discord.ui.select(placeholder=_("Select the DCS terrains you own"), min_values=1)
    async def maps_callback(self, interaction: discord.Interaction, select: Select):
        self.terrains = select.values
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()

    @discord.ui.button(label=_("Signup"), style=discord.ButtonStyle.green)
    async def signup(self, interaction: discord.Interaction, button: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label=_("Cancel"), style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.times = self.terrains = None
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
    def __init__(self, choice: str, costs: int, points: int, max_value: Optional[int] = None):
        super().__init__(title=_("How many {} do you want?").format(choice[:20]))
        self.costs = costs
        self.points = points
        self.max_value = max_value
        self.textinput = TextInput(label=_("Count"), placeholder=_("Enter a number"), default="1",
                                   style=discord.TextStyle.short, required=True)
        self.add_item(self.textinput)
        self.result = 0
        self.error = None

    async def on_submit(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        value = int(self.textinput.value)
        if self.max_value and value > self.max_value:
            raise ValueError(_("There are only {} items available to buy.").format(self.max_value))
        if value * self.costs > self.points:
            raise ValueError(_("You do not have enough credits to buy {} items.").format(value))
        self.result = value
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        self.error = str(error)
        self.stop()


class ChoicesView(View):
    def __init__(self, plugin: "Tournament", tournament_id: int, match_id: int, squadron_id: int, config: dict):
        super().__init__()
        self.plugin = plugin
        self.tournament_id = tournament_id
        self.match_id = match_id
        self.squadron_id = squadron_id
        self.config = config
        self.acknowledged = None

    async def get_tickets(self) -> dict[str, int]:
        async with self.plugin.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT ticket_name, ticket_count
                FROM tm_tickets
                WHERE tournament_id = %s AND squadron_id = %s
            """, (self.tournament_id, self.squadron_id))
            tickets = {x[0]: x[1] for x in await cursor.fetchall()}
        return tickets

    async def render(self) -> discord.Embed:
        squadron = await self.plugin.get_squadron(self.match_id, self.squadron_id)
        embed = discord.Embed(colour=discord.Colour.blue(),
                              title=_("You have {} credit points left to spend.").format(squadron.points))
        embed.description = ("Here you can select the presets to change the upcoming mission to your request.\n"
                             "Please keep in mind, that you will have to pay credit points, "
                             "according to the requested presets price.")
        async with self.plugin.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT preset, config FROM tm_choices WHERE match_id = %s AND squadron_id = %s
            """, (self.match_id, self.squadron_id))
            already_selected = [x for x in await cursor.fetchall()]
        if not already_selected:
            embed.add_field(name="Choices", value="No choices selected yet.", inline=False)
        else:
            presets = []
            costs = []
            number = []
            for choice in already_selected:
                preset = choice[0]
                presets.append(preset)
                cost = self.config['presets']['choices'][preset]['costs']
                costs.append(cost)
                number.append(choice[1]['num'])
            embed.add_field(name="Your selection", value="\n".join(presets))
            embed.add_field(name="Costs in Credits", value="\n".join([str(x) for x in costs]))
            embed.add_field(name="Count", value="\n".join([str(x) for x in number]))
            embed.add_field(name="Total cost", value=f"{np.sum(np.array(costs) * np.array(number))} credits",
                            inline=False)
        embed.set_footer(text="Please make your choice!")
        # build the selections
        self.clear_items()
        choices = self.config['presets']['choices']
        tickets = await self.get_tickets()
        if already_selected:
            select = Select(placeholder="Remove a choice",
                            options=[
                                SelectOption(label=x[0], value=x[0])
                                for idx, x in enumerate(already_selected)
                                if idx < 25
                            ],
                            min_values=1, max_values=1)
            select.callback = self.remove_choice
            self.add_item(select)
        if len(already_selected) < len(choices):
            select = Select(
                placeholder="Add a choice",
                options=[
                    SelectOption(
                        label="{name} ({costs}{ticket})".format(
                            name=x, costs=f"Costs={choices[x]['costs']}" if choices[x].get('costs', 0) else "",
                            ticket="{}Ticket".format(',' if choices[x].get('costs', 0) else '') if tickets.get(choices[x].get('ticket')) else ""),
                        value=x
                    )
                    for idx, x in enumerate(choices.keys())
                    if idx < 25
                       and choices[x].get('costs', 0) <= squadron.points
                       and tickets.get(choices[x].get('ticket'), 1) > 0
                       and x not in [x[0] for x in already_selected]
                ], min_values=1, max_values=1)
            select.callback = self.add_choice
            self.add_item(select)
        if already_selected:
            button = Button(label="Confirm & Buy", style=discord.ButtonStyle.green)
            button.callback = self.save
            self.add_item(button)
            button = Button(label="Save & Close", style=discord.ButtonStyle.red)
            button.callback = self.cancel
            self.add_item(button)
        else:
            button = Button(label="Skip this round", style=discord.ButtonStyle.primary)
            button.callback = self.no_change
            self.add_item(button)
        return embed

    async def add_choice(self, interaction: discord.Interaction):
        choice = interaction.data['values'][0]
        squadron = await self.plugin.get_squadron(self.match_id, self.squadron_id)
        costs = self.config['presets']['choices'][choice]['costs']
        tickets = await self.get_tickets()
        ticket_name = self.config['presets']['choices'][choice].get('ticket')
        ticket_count = tickets.get(ticket_name, 99)

        max_num = min(self.config['presets']['choices'][choice].get('max', 99), ticket_count)
        if not max_num or max_num > 1:
            modal = NumbersModal(choice, costs, squadron.points, max_num)
            # noinspection PyUnresolvedReferences
            await interaction.response.send_modal(modal)
            if await modal.wait():
                return
            num = modal.result
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.defer()
            modal = None
            num = 1

        if num and costs * num <= squadron.points:
            async with self.plugin.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO tm_choices (match_id, squadron_id, preset, config) 
                        VALUES (%s, %s, %s, %s)
                    """, (self.match_id, self.squadron_id, choice, Json({"num": num})))
                    if ticket_name:
                        # invalidate the ticket
                        await conn.execute("""
                            UPDATE tm_tickets SET ticket_count = ticket_count - %s
                            WHERE tournament_id = %s AND squadron_id = %s AND ticket_name = %s
                        """, (num, self.tournament_id, self.squadron_id, ticket_name))
            squadron.points -= costs * num
            squadron.audit(event='match_choice', points=-costs * num,
                           remark=f'Bought {num} of {choice} during a match choice.')
        embed = await self.render()
        if modal and modal.error:
            embed.set_footer(text=modal.error, icon_url=WARNING_ICON)
        await interaction.edit_original_response(embed=embed, view=self)

    async def remove_choice(self, interaction: discord.Interaction):
        choice = interaction.data['values'][0]
        squadron = await self.plugin.get_squadron(self.match_id, self.squadron_id)
        costs = self.config['presets']['choices'][choice]['costs']
        ticket_name = self.config['presets']['choices'][choice].get('ticket')

        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        async with self.plugin.apool.connection() as conn:
            async with conn.transaction():
                cursor = await conn.execute("""
                    DELETE FROM tm_choices 
                    WHERE match_id = %s AND squadron_id = %s AND preset = %s
                    RETURNING config
                """, (self.match_id, self.squadron_id, choice))
                num = (await cursor.fetchone())[0]['num']
                if ticket_name:
                    # return the ticket
                    await conn.execute("""
                        UPDATE tm_tickets SET ticket_count = ticket_count + %s
                        WHERE tournament_id = %s AND squadron_id = %s AND ticket_name = %s
                    """, (num, self.tournament_id, self.squadron_id, ticket_name))

        squadron.points += costs * num
        squadron.audit(event='match_choice', points=costs * num,
                       remark=f'Cancelled {num} of {choice} during a match choice.')
        await interaction.edit_original_response(embed=await self.render(), view=self)

    async def save(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.acknowledged = True
        self.stop()

    async def cancel(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()

    async def no_change(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.acknowledged = False
        self.stop()


class ApplicationView(View):

    def __init__(self, plugin: "Tournament", tournament_id: int, squadron_id: int):
        super().__init__()
        self.plugin = plugin
        self.tournament_id = tournament_id
        self.squadron_id = squadron_id
        self.squadron = utils.get_squadron(self.plugin.node, squadron_id=self.squadron_id)

    async def inform_squadron(self, *, message: Optional[str] = None, embed: Optional[discord.Embed] = None):
        async with self.plugin.apool.connection() as conn:
            async for row in await conn.execute("""
                SELECT p.discord_id
                FROM players p JOIN squadron_members m ON p.ucid = m.player_ucid
                WHERE m.squadron_id = %s AND m.admin IS TRUE
            """, (self.squadron_id,)):
                user = self.plugin.bot.get_user(row[0])
                if user:
                    tournament = await self.plugin.get_tournament(self.tournament_id)
                    dm_channel = await user.create_dm()
                    if message:
                        message = message.format(squadron=self.squadron['name'], tournament=tournament['name'])
                    await dm_channel.send(content=message, embed=embed)

    @discord.ui.button(label=_("Accept"), style=discord.ButtonStyle.green)
    async def on_accept(self, interaction: discord.Interaction, button: Button):
        tournament = await self.plugin.get_tournament(self.tournament_id)
        embed = discord.Embed(color=discord.Color.green(), title=_("Your Squadron has been accepted!"))
        embed.description = _("Congratulations, you will be part of our upcoming tournament!")
        embed.add_field(name=_("Tournament"), value=tournament['name'])
        embed.add_field(name=_("Start Date"), value=f"<t:{int(tournament['start'].timestamp())}:f>")
        async with self.plugin.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE tm_squadrons SET status = 'ACCEPTED' WHERE tournament_id = %s AND squadron_id = %s
                """, (self.tournament_id, self.squadron_id))
                # create the tickets if there are any
                tickets = self.plugin.get_config().get('presets', {}).get('tickets', {})
                if tickets:
                    embed.description += _(
                        "\n\nYou can use the following tickets during the tournament to buy special customizations:"
                    )
                    await conn.execute("DELETE FROM tm_tickets WHERE tournament_id = %s AND squadron_id = %s",
                                       (self.tournament_id, self.squadron_id))
                    ticket_names = []
                    ticket_counts = []
                    for name, count in tickets.items():
                        await conn.execute("""
                            INSERT INTO tm_tickets(tournament_id, squadron_id, ticket_name, ticket_count)
                            VALUES (%s, %s, %s, %s)
                        """, (self.tournament_id, self.squadron_id, name, count))
                        ticket_names.append(name)
                        ticket_counts.append(count)
                    embed.add_field(name=_("Tickets"),
                                    value="\n".join([f"{y} x {x}" for x, y in zip(ticket_names, ticket_counts)]))
                    embed.set_footer(text=_("You can use each ticket only once, so use them wisely!"))

        # update the info embed
        channel_id = self.plugin.get_config().get('channels', {}).get('info')
        if channel_id:
            await self.plugin.render_status_embed(self.tournament_id, phase=TOURNAMENT_PHASE.SIGNUP)
        await self.plugin.bot.audit(
            f"accepted squadron {self.squadron['name']} for tournament {tournament['name']}.",
            user=interaction.user
        )
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Squadron {} accepted.").format(self.squadron['name']))
        await self.inform_squadron(embed=embed)
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
            await self.plugin.render_info_embed(self.tournament_id)

        # update the info embed
        channel_id = self.plugin.get_config().get('channels', {}).get('info')
        if channel_id:
            await self.plugin.render_status_embed(self.tournament_id, phase=TOURNAMENT_PHASE.SIGNUP)

        tournament = await self.plugin.get_tournament(self.tournament_id)
        await self.plugin.bot.audit(
            f"rejected squadron {self.squadron['name']} from tournament {tournament['name']}.",
            user=interaction.user
        )
        await interaction.followup.send(_("Squadron {} rejected.").format(self.squadron['name']))
        embed = discord.Embed(color=discord.Color.red(), title=_("Your Squadron has been rejected!"))
        embed.description = _("Your squadron {squadron} was rejected from tournament {tournament}.").format(
            squadron=self.squadron['name'], tournament=tournament['name']
        )
        if reason:
            embed.add_field(name=_("Reason"), value=reason)
        await self.inform_squadron(embed=embed)
        self.stop()

    @discord.ui.button(label=_("Cancel"), style=discord.ButtonStyle.secondary)
    async def on_cancel(self, interaction: discord.Interaction, button: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()
