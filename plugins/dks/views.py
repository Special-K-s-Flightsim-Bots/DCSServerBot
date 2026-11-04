import discord

from discord import ButtonStyle
from discord.ui import View, Button

from core import get_translation

_ = get_translation(__name__.split('.')[1])


class RegisterView(View):
    def __init__(self, *, url: str):
        super().__init__()
        button = Button(label=_("Register"), style=ButtonStyle.primary, url=url)
        button.callback = self.register
        self.add_item(button)
        button = Button(label=_("Cancel"), style=ButtonStyle.secondary)
        button.callback = self.cancel
        self.add_item(button)

    async def register(self, interaction: discord.Interaction):
        await interaction.response.send_message(_("Registration sent."))
        self.stop()

    async def cancel(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()
