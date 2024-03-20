import discord

from core import TEventListener, Server, get_translation
from datetime import datetime, timezone
from discord.ui import Modal, TextInput
from typing import Type

_ = get_translation(__name__.split('.')[1])


class CampaignModal(Modal):
    name = TextInput(label=_("Name"), required=True, style=discord.TextStyle.short, min_length=3, max_length=80)
    start = TextInput(label=_("Start (UTC)"), placeholder="yyyy-mm-dd hh24:mi", required=True)
    end = TextInput(label=_("End (UTC)"), placeholder="yyyy-mm-dd hh24:mi", required=False)
    description = TextInput(label=_("Description"), required=False, style=discord.TextStyle.long)

    def __init__(self, eventlistener: Type[TEventListener]):
        super().__init__(title=_("Campaign Info"))
        self.eventlistener = eventlistener
        self.start.default = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            self.start = datetime.strptime(self.start.value, '%Y-%m-%d %H:%M')
        except ValueError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Format for {} needs to be yyyy-mm-dd hh24:mi!").format(self.start.label), ephemeral=True)
            raise
        try:
            self.end = datetime.strptime(self.end.value, '%Y-%m-%d %H:%M') if self.end.value else None
        except ValueError:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Format for {} needs to be yyyy-mm-dd hh24:mi!").format(self.end.label), ephemeral=True)
            raise
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()


class ScriptModal(Modal):
    script = TextInput(label=_("Enter your script here:"), style=discord.TextStyle.long, required=True)

    def __init__(self, server: Server, ephemeral: bool):
        super().__init__(title=_("Lua Script"))
        self.server = server
        self.ephemeral = ephemeral

    async def on_submit(self, interaction: discord.Interaction):
        self.server.send_to_dcs({
            "command": "do_script",
            "script": self.script.value
        })
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Script sent.'), ephemeral=self.ephemeral)
