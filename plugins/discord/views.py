import asyncio
import discord

from discord import ButtonStyle, Interaction
from discord._types import ClientT
from discord.ui import View, Button, Modal, TextDisplay

__all__ = [
    "HealthcheckView"
]

class HealthCheckModal(Modal):
    text = TextDisplay(content="This will remove all critical permissions from your roles.")

    def __init__(self, view: "HealthcheckView", title: str = 'Are you sure?'):
        super().__init__(title=title)
        self.view = view

    async def on_submit(self, interaction: Interaction[ClientT], /) -> None:
        await interaction.response.defer()
        tasks = []
        for role in set(self.view.everyone_ping) | set(self.view.external_apps):
            x = role.permissions
            x.update(mention_everyone=False, use_external_apps=False)
            tasks.append(role.edit(permissions=x))
        await asyncio.gather(*tasks)
        await interaction.followup.send(content="Permissions fixed successfully!\n"
                                                "Do not forget to remove the administrative permissions from the bot.",
                                        ephemeral=True)


class HealthcheckView(View):

    def __init__(self, everyone_ping: list[discord.Role], external_apps: list[discord.Role]) -> None:
        super().__init__()
        self.everyone_ping = everyone_ping
        self.external_apps = external_apps

    @discord.ui.button(label='Fix Permissions', style=ButtonStyle.green)
    async def fix(self, interaction: discord.Interaction, _: Button):
        modal = HealthCheckModal(self)
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.stop()

    @discord.ui.button(label='Cancel', style=ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _: Button):
        await interaction.response.defer()
        self.stop()
