import discord

from core import Server, get_translation, utils
from datetime import datetime, timezone
from discord import TextStyle, ButtonStyle
from discord.ui import Modal, TextInput, View
from typing import Union, Optional

from .listener import GameMasterEventListener

_ = get_translation(__name__.split('.')[1])


class CampaignModal(Modal):
    # noinspection PyTypeChecker
    name = TextInput(label=_("Name"), required=True, style=TextStyle.short, min_length=3, max_length=80)
    # noinspection PyTypeChecker
    start = TextInput(label=_("Start (UTC)"), placeholder="yyyy-mm-dd hh24:mi", required=True)
    # noinspection PyTypeChecker
    end = TextInput(label=_("End (UTC)"), placeholder="yyyy-mm-dd hh24:mi", required=False)
    # noinspection PyTypeChecker
    description = TextInput(label=_("Description"), required=False, style=TextStyle.long)

    def __init__(self, eventlistener: GameMasterEventListener):
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
    # noinspection PyTypeChecker
    script = TextInput(label=_("Enter your script here:"), style=TextStyle.long, required=True)

    def __init__(self, server: Server, ephemeral: bool):
        super().__init__(title=_("Lua Script"))
        self.server = server
        self.ephemeral = ephemeral

    async def on_submit(self, interaction: discord.Interaction):
        await self.server.send_to_dcs({
            "command": "do_script",
            "script": self.script.value
        })
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Script sent.'), ephemeral=self.ephemeral)


class MessageModal(Modal):
    # noinspection PyTypeChecker
    message = TextInput(label="Message", style=TextStyle.long, required=True)

    def __init__(self, message: Optional[str] = None):
        super().__init__(title="User Message")
        if message:
            self.message.default = message

    async def on_submit(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()


class MessageView(View):

    def __init__(self, messages: list[dict], user: Union[str, discord.Member]):
        super().__init__()
        self.index = 0
        self.messages = messages
        self.user = user

    async def render(self) -> discord.Embed:
        embed = discord.Embed(color=discord.Color.blue())
        embed.description = _("Message for user {}").format(
            self.user.display_name if isinstance(self.user, discord.Member) else self.user)
        embed.add_field(name="Msg. No", value=str(self.index + 1))
        embed.add_field(name="Sender", value=self.messages[self.index]['sender'])
        embed.add_field(name="Time", value=f"<t:{int(self.messages[self.index]['time'].timestamp())}:f>")
        embed.add_field(name=utils.print_ruler(header=_("Message"), ruler_length=27),
                        value=self.messages[self.index]['message'], inline=False)
        return embed

    # noinspection PyTypeChecker
    @discord.ui.button(emoji='◀️', style=ButtonStyle.primary)
    async def on_left(self, interaction: discord.Interaction, _: discord.ui.Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        if self.index > 0:
            self.index -= 1
            await interaction.edit_original_response(embed=await self.render(), view=self)

    # noinspection PyTypeChecker
    @discord.ui.button(emoji='🗒️', style=ButtonStyle.primary)
    async def on_edit(self, interaction: discord.Interaction, _: discord.ui.Button):
        modal = MessageModal(self.messages[self.index]['message'])
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if not await modal.wait():
            async with interaction.client.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        UPDATE messages SET message = %s WHERE id = %s
                    """, (modal.message.value, self.messages[self.index]['id']))
            self.messages[self.index]['message'] = modal.message.value
            await interaction.edit_original_response(embed=await self.render(), view=self)

    # noinspection PyTypeChecker
    @discord.ui.button(emoji='🚮', style=ButtonStyle.primary)
    async def on_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        async with interaction.client.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM messages WHERE id = %s", (self.messages[self.index]['id'],))
        self.messages.pop(self.index)
        if not self.messages:
            await interaction.followup.send(_("No messages left."))
            self.stop()
            return
        if self.index > len(self.messages) - 1:
            self.index -= 1
        await interaction.edit_original_response(embed=await self.render(), view=self)

    # noinspection PyTypeChecker
    @discord.ui.button(emoji='▶️', style=ButtonStyle.primary)
    async def on_right(self, interaction: discord.Interaction, _: discord.ui.Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        if self.index < (len(self.messages) - 1):
            self.index += 1
            await interaction.edit_original_response(embed=await self.render(), view=self)

    # noinspection PyTypeChecker
    @discord.ui.button(label="Quit", style=ButtonStyle.red)
    async def on_cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()
