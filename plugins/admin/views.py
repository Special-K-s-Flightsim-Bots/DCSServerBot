import discord

from discord import ButtonStyle
from discord.ui import View, Button, Select


class CleanupView(View):
    def __init__(self):
        super().__init__()
        self.what = 'non-members'
        self.age = '180'
        self.cmd = None

    @discord.ui.select(placeholder="What to be pruned?", options=[
        discord.SelectOption(label='Non-member users (unlinked)', value='non-members', default=True),
        discord.SelectOption(label='Members and non-members', value='users'),
        discord.SelectOption(label='Data only (for all users)', value='data')
    ])
    async def set_what(self, interaction: discord.Interaction, select: Select):
        self.what = select.values[0]
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()

    @discord.ui.select(placeholder="Which age to be pruned?", options=[
        discord.SelectOption(label='Everything', value='0'),
        discord.SelectOption(label='Older than 90 days', value='90'),
        discord.SelectOption(label='Older than 180 days', value='180', default=True),
        discord.SelectOption(label='Older than 1 year', value='360')
    ])
    async def set_age(self, interaction: discord.Interaction, select: Select):
        self.age = select.values[0]
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()

    # noinspection PyTypeChecker
    @discord.ui.button(label='Prune', style=ButtonStyle.danger, emoji='⚠')
    async def prune(self, interaction: discord.Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.cmd = "prune"
        self.stop()

    # noinspection PyTypeChecker
    @discord.ui.button(label='Cancel', style=ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.cmd = "cancel"
        self.stop()
