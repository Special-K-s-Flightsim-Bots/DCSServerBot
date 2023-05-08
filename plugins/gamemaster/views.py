import discord
import psycopg
from core import Server, TEventListener
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

    def __init__(self, eventlistener: Type[TEventListener], servers: list[Server]):
        super().__init__(title="Campaign Info")
        self.eventlistener = eventlistener
        self.servers = servers

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            start = datetime.strptime(self.start.value, '%Y-%m-%d %H:%M')
        except ValueError:
            await interaction.response.send_message("Wrong format for start, try again.", ephemeral=True)
            return
        try:
            end = datetime.strptime(self.end.value, '%Y-%m-%d %H:%M') if self.end.value else None
        except ValueError:
            await interaction.response.send_message("Wrong format for end, try again.", ephemeral=True)
            return
        try:
            self.eventlistener.campaign('add', servers=self.servers, name=self.name.value,
                                        description=self.description.value, start=start, end=end)
            await interaction.response.send_message(f"Campaign {self.name.value} added.")
        except psycopg.errors.ExclusionViolation:
            await interaction.response.send_message(f"A campaign is already configured for this timeframe!",
                                                    ephemeral=True)
        except psycopg.errors.UniqueViolation:
            await interaction.response.send_message(f"A campaign with this name already exists!", ephemeral=True)
