import discord
from core import TEventListener
from datetime import datetime
from discord import TextStyle
from discord.ui import Modal, TextInput
from typing import Type


class CampaignModal(Modal):
    name = TextInput(label="Name", required=True, style=TextStyle.short, min_length=3, max_length=80)
    start = TextInput(label="Start", placeholder="yyyy-mm-dd hh24:mi", default=datetime.now().strftime("%Y-%m-%d %H:%M"),
                      required=True)
    end = TextInput(label="End", placeholder="yyyy-mm-dd hh24:mi", required=False)
    description = TextInput(label="Description", required=False, style=TextStyle.long)

    def __init__(self, eventlistener: Type[TEventListener]):
        super().__init__(title="Campaign Info")
        self.eventlistener = eventlistener

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            self.start = datetime.strptime(self.start.value, '%Y-%m-%d %H:%M')
        except ValueError:
            await interaction.response.send_message("Wrong format for start, try again.", ephemeral=True)
            raise
        try:
            self.end = datetime.strptime(self.end.value, '%Y-%m-%d %H:%M') if self.end.value else None
        except ValueError:
            await interaction.response.send_message("Wrong format for end, try again.", ephemeral=True)
            raise
        await interaction.response.defer()
