import discord
from discord.ui import Modal, TextInput, View, Button

from core import Server


class ConfigView(View):
    def __init__(self, server: Server):
        super().__init__()
        self.server = server

    @discord.ui.button(label='Yes', style=discord.ButtonStyle.green, custom_id='cfg_yes')
    async def on_yes(self, interaction: discord.Interaction, button: Button):
        class ConfigModal(Modal, title="Server Configuration"):
            name = TextInput(label="Name", default=self.server.name, max_length=80, required=True)
            description = TextInput(label="Description", style=discord.TextStyle.long,
                                    default=self.server.settings['description'], max_length=2000, required=False)
            password = TextInput(label="Password", placeholder="n/a", default=self.server.settings['password'],
                                 max_length=20, required=False)
            max_player = TextInput(label="Max Players", default=self.server.settings['maxPlayers'], max_length=3,
                                   required=True)

            async def on_submit(derived, interaction: discord.Interaction):
                if derived.name.value != self.server.name:
                    old_name = self.server.name
                    self.server.rename(new_name=derived.name.value, update_settings=True)
                    interaction.client.servers[derived.name.value] = self.server
                    del interaction.client.servers[old_name]
                self.server.settings['description'] = derived.description.value
                self.server.settings['password'] = derived.password.value
                self.server.settings['maxPlayers'] = int(derived.max_player.value)
                await interaction.response.send_message(
                    f'Server configuration for server "{self.server.display_name}" updated.', ephemeral=True)

        modal = ConfigModal()
        await interaction.response.send_modal(modal)
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red, custom_id='cfg_cancel')
    async def on_cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message('Aborted.', ephemeral=True)
        self.stop()
