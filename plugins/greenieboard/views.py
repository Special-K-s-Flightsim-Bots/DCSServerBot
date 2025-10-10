import discord

from core import utils, get_translation
from datetime import datetime
from discord import TextStyle, SelectOption
from discord.ui import Modal, TextInput, View, Select, Item
from services.bot import DCSServerBot
from typing import Any

_ = get_translation(__name__.split('.')[1])


class TrapModal(Modal):
    # noinspection PyTypeChecker
    time = TextInput(label=_('Time (HH24:MI)'), style=TextStyle.short, required=True, min_length=5, max_length=5)
    # noinspection PyTypeChecker
    case = TextInput(label=_('Case'), style=TextStyle.short, required=True, min_length=1, max_length=1)
    # noinspection PyTypeChecker
    grade = TextInput(label=_('Grade'), style=TextStyle.short, required=True, min_length=1, max_length=4)
    # noinspection PyTypeChecker
    comment = TextInput(label=_('LSO Comment'), style=TextStyle.long, required=False)
    # noinspection PyTypeChecker
    wire = TextInput(label=_('Wire'), style=TextStyle.short, required=False, min_length=1, max_length=1)

    def __init__(self, bot: DCSServerBot, *, config: dict, user: str | discord.Member, unit_type: str):
        super().__init__(title=_("Enter the trap details"))
        self.bot = bot
        self.log = bot.log
        self.apool = bot.apool
        self.config = config
        self.user = user
        self.unit_type = unit_type
        self.success = False

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        time = datetime.strptime(self.time.value, '%H:%M').time()
        night = time.hour >= 20 or time.hour <= 6
        if self.case.value not in ['1', '2', '3']:
            raise TypeError(_('Case needs to be one of 1, 2 or 3.'))
        grade = self.grade.value.upper()
        if grade not in self.config['grades'].keys():
            raise ValueError(_("Grade has to be one of {}.").format(
                ', '.join([utils.escape_string(x) for x in self.config['ratings'].keys()])))
        if self.wire.value and self.wire.value not in ['1', '2', '3', '4']:
            raise TypeError(_('Wire needs to be one of 1 to 4.'))

        if isinstance(self.user, discord.Member):
            ucid = await self.bot.get_ucid_by_member(self.user)
        else:
            ucid = self.user

        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO traps (mission_id, player_ucid, unit_type, grade, comment, place, night, points, wire, 
                                       trapcase) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (-1, ucid, self.unit_type, self.grade.value, self.comment.value, 'n/a', night,
                      self.config['grades'][grade]['rating'], self.wire.value, self.case.value))
            self.success = True

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.followup.send(error, ephemeral=True)
        self.stop()


class TrapView(View):

    def __init__(self, bot: DCSServerBot, config: dict, user: str | discord.Member):
        super().__init__()
        self.bot = bot
        self.log = bot.log
        self.config = config
        self.user = user
        self.success = False

    @discord.ui.select(placeholder=_('Select the plane for the trap'),
                       options=[
                           SelectOption(label=x, default=(idx == 0))
                           for idx, x in enumerate([
                               'AV8BNA', 'F-14A-135-GR', 'F-14B', 'FA-18C_hornet', 'Su-33', 'F-4E-45MC'
                           ])
                       ])
    async def callback(self, interaction: discord.Interaction, select: Select):
        modal = TrapModal(self.bot, config=self.config, user=self.user, unit_type=select.values[0])
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.success = modal.success
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: Item[Any]) -> None:
        await interaction.followup.send(error, ephemeral=True)
        self.stop()
