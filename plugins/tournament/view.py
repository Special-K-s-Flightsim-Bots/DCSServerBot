import discord
from discord import SelectOption
from discord.ui import Select

from core import Node


class ChoicesView(discord.ui.View):
    def __init__(self, node: Node, match_id: int, squadron_id: int, config: dict):
        super().__init__()
        self.node = node
        self.match_id = match_id
        self.squadron_id = squadron_id
        self.config = config

    async def get_squadron_credits(self):
        async with self.node.apool.connection() as conn:
            cursor = await conn.execute("SELECT points FROM squadron_credits WHERE squadron_id=%s", (self.squadron_id,))
            if cursor.rowcount == 1:
                return (await cursor.fetchone())[0]
            else:
                return 0

    async def render(self) -> discord.Embed:
        embed = discord.Embed(colour=discord.Colour.blue())
        embed.description = ("Here you can select the presets to change the upcoming mission to your request.\n"
                             "Please keep in mind, that you will have to pay credit points, "
                             "according to the requested presets price.")
        async with self.node.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT preset FROM tm_choices WHERE match_id = %s AND squadron_id = %s
            """, (self.match_id, self.squadron_id))
            choices = [x[0] for x in await cursor.fetchall()]
        if not choices:
            embed.add_field(name="Choices", value="No choices selected yet.", inline=False)
        else:
            presets = []
            costs = []
            for choice in choices:
                preset = choice
                presets.append(preset)
                cost = self.config.get('choices').get(preset)
                costs.append(cost)
            embed.add_field(name="Your selection", value="\n".join(presets))
            embed.add_field(name="Costs in Credits", value="\n".join([str(x) for x in costs]))
            embed.add_field(name="Total cost", value=f"{sum(costs)} credits", inline=False)
        embed.set_footer(text="Please make your choice!")
        # build the selections
        self.clear_items()
        if choices:
            select = Select(placeholder="Remove a choice",
                            options=[
                                SelectOption(label=x, value=x)
                                for idx, x in enumerate(choices)
                                if idx < 25
                            ],
                            min_values=1, max_values=len(choices))
            select.callback = self.remove_choices
            self.add_item(select)
        if len(choices) < len(self.config['choices']):
            select = Select(placeholder="Add a choice",
                            options=[
                                SelectOption(label=x, value=x)
                                for idx, x in enumerate(self.config['choices'].keys())
                                if idx < 25
                            ],
                            min_values=1, max_values=len(self.config['choices']))
            select.callback = self.add_choices
            self.add_item(select)
        return embed

    async def add_choices(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        choices = interaction.data['values']
        credits = await self.get_squadron_credits()
        costs = sum([costs for preset, costs in self.config['choices'].items() if preset in choices])
        if costs <= credits:
            async with self.node.apool.connection() as conn:
                async with conn.transaction():
                    for choice in choices:
                        await conn.execute("INSERT INTO tm_choices (match_id, squadron_id, preset) VALUES (%s, %s, %s)",
                                           (self.match_id, self.squadron_id, choice))
                    await conn.execute("UPDATE squadron_credits SET points = %s WHERE squadron_id = %s",
                                       (credits - costs, self.squadron_id))
        embed = await self.render()
        if costs > credits:
            embed.set_footer(text=f"Your choice exceeded your budget of {credits} credits.")
        await interaction.edit_original_response(embed=embed, view=self)

    async def remove_choices(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        choices = interaction.data['values']
        credits = await self.get_squadron_credits()
        costs = sum([costs for preset, costs in self.config['choices'].items() if preset in choices])
        async with self.node.apool.connection() as conn:
            async with conn.transaction():
                for choice in choices:
                    await conn.execute("DELETE FROM tm_choices WHERE match_id = %s AND squadron_id = %s AND preset = %s",
                                       (self.match_id, self.squadron_id, choice))
                await conn.execute("UPDATE squadron_credits SET points = %s WHERE squadron_id = %s",
                                   (credits + costs, self.squadron_id))
        await interaction.edit_original_response(embed=await self.render(), view=self)
