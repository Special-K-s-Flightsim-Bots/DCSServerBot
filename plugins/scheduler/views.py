import discord

from core import Server
from discord import ButtonStyle, ChannelType, TextStyle
from discord.ui import Modal, TextInput, View, Button, ChannelSelect
from services.bot import DCSServerBot


class ConfigView(View):
    def __init__(self, bot: DCSServerBot, server: Server):
        super().__init__()
        self.bot = bot
        self.server = server
        self.cancelled = False
        self.channel_update = False
        if self.bot.locals.get('channels', {}).get('admin'):
            # noinspection PyUnresolvedReferences
            self.children[0].disabled = True
        # noinspection PyUnresolvedReferences
        self.children[3].disabled = True
        self.toggle_config()

    def toggle_ok(self) -> bool:
        if self.server.name != 'n/a':
            # noinspection PyUnresolvedReferences
            self.children[3].disabled = False
            return True
        else:
            # noinspection PyUnresolvedReferences
            self.children[3].disabled = True
            return False

    def toggle_config(self) -> bool:
        try:
            if (self.server.locals.get('channels', {}).get('admin', self.bot.locals.get('channels', {}).get('admin', -1)) != -1
                    and self.server.locals.get('channels', {}).get('status', -1) != -1):
                # noinspection PyUnresolvedReferences
                self.children[4].disabled = False
                self.toggle_ok()
                return True
            else:
                # noinspection PyUnresolvedReferences
                self.children[4].disabled = True
                return False
        except Exception as ex:
            self.bot.log.exception(ex)
            return False

    # noinspection PyTypeChecker
    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[ChannelType.text],
        placeholder="Select an admin channel",
    )
    async def admin_channel(self, interaction: discord.Interaction,
                            select: ChannelSelect[discord.TextChannel]) -> None:
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        if 'channels' not in self.server.locals:
            self.server.locals['channels'] = {}
        self.server.locals['channels']['admin'] = select.values[0].id
        self.channel_update = True
        if self.toggle_config():
            await interaction.edit_original_response(view=self)

    # noinspection PyTypeChecker
    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[ChannelType.text],
        placeholder="Select a status channel"
    )
    async def status_channel(self, interaction: discord.Interaction,
                             select: ChannelSelect[discord.TextChannel]) -> None:
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        if 'channels' not in self.server.locals:
            self.server.locals['channels'] = {}
        self.server.locals['channels']['status'] = select.values[0].id
        self.channel_update = True
        if self.toggle_config():
            await interaction.edit_original_response(view=self)

    # noinspection PyTypeChecker
    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[ChannelType.text],
        placeholder="Select a chat channel"
    )
    async def chat_channel(self, interaction: discord.Interaction, select: ChannelSelect[discord.TextChannel]) -> None:
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        if 'channels' not in self.server.locals:
            self.server.locals['channels'] = {}
        self.server.locals['channels']['chat'] = select.values[0].id
        self.channel_update = True

    # noinspection PyTypeChecker
    @discord.ui.button(label='OK', style=ButtonStyle.primary)
    async def on_ok(self, interaction: discord.Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()

    # noinspection PyTypeChecker
    @discord.ui.button(label='Config', style=ButtonStyle.secondary)
    async def on_config(self, interaction: discord.Interaction, _: Button):
        class ConfigModal(Modal, title="Server Configuration"):
            # noinspection PyTypeChecker
            name = TextInput(label="Name", default=self.server.name[:80] if self.server.name != 'n/a' else '',
                             min_length=3, max_length=80, required=True)
            # noinspection PyTypeChecker
            description = TextInput(label="Description", style=TextStyle.long,
                                    default=self.server.settings.get('description')[:2000], max_length=2000,
                                    required=False)
            # noinspection PyTypeChecker
            password = TextInput(label="Password", placeholder="n/a",
                                 default=self.server.settings.get('password')[:80], max_length=80, required=False)
            # noinspection PyTypeChecker
            port = TextInput(label="Port", default=str(self.server.settings.get('port', 10308)), max_length=5,
                             required=True)
            # noinspection PyTypeChecker
            max_player = TextInput(label="Max Players", default=str(self.server.settings.get('maxPlayers', 16)),
                                   max_length=3, required=True)

            async def on_submit(derived, interaction: discord.Interaction):
                # noinspection PyUnresolvedReferences
                await interaction.response.defer()
                if derived.name.value != self.server.name:
                    old_name = self.server.name
                    await self.server.rename(new_name=derived.name.value, update_settings=True)
                    interaction.client.servers[derived.name.value] = self.server
                    if old_name in interaction.client.servers:
                        del interaction.client.servers[old_name]
                self.server.settings['description'] = derived.description.value
                self.server.settings['password'] = derived.password.value
                self.server.settings['port'] = int(derived.port.value)
                self.server.settings['maxPlayers'] = int(derived.max_player.value)

        modal = ConfigModal()
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        self.cancelled = await modal.wait()
        self.stop()

    # noinspection PyTypeChecker
    @discord.ui.button(label='Cancel', style=ButtonStyle.red)
    async def on_cancel(self, interaction: discord.Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.cancelled = True
        self.stop()
