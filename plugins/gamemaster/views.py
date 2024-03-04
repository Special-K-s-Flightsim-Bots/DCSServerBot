import discord

from core import TEventListener, Server
from datetime import datetime, timezone
from discord.ui import Modal, TextInput
from typing import Type


class CampaignModal(Modal):
    name = TextInput(label="Name", required=True, style=discord.TextStyle.short, min_length=3, max_length=80)
    start = TextInput(label="Start (UTC)", placeholder="yyyy-mm-dd hh24:mi",
                      default=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                      required=True)
    end = TextInput(label="End (UTC)", placeholder="yyyy-mm-dd hh24:mi", required=False)
    description = TextInput(label="Description", required=False, style=discord.TextStyle.long)

    def __init__(self, eventlistener: Type[TEventListener]):
        super().__init__(title="Campaign Info")
        self.eventlistener = eventlistener

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            self.start = datetime.strptime(self.start.value, '%Y-%m-%d %H:%M')
        except ValueError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Wrong format for start, try again.", ephemeral=True)
            raise
        try:
            self.end = datetime.strptime(self.end.value, '%Y-%m-%d %H:%M') if self.end.value else None
        except ValueError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Wrong format for end, try again.", ephemeral=True)
            raise
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()


class ScriptModal(Modal):
    script = TextInput(label="Enter your script here", style=discord.TextStyle.long, required=True)

    def __init__(self, server: Server, ephemeral: bool):
        super().__init__(title="Lua Script")
        self.server = server
        self.ephemeral = ephemeral

    async def on_submit(self, interaction: discord.Interaction):
        self.server.send_to_dcs({
            "command": "do_script",
            "script": self.script.value
        })
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message('Script sent.', ephemeral=self.ephemeral)
