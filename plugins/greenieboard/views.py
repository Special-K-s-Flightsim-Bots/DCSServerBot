import discord
from core import utils
from datetime import datetime
from discord import TextStyle, SelectOption
from discord.ui import Modal, TextInput, View, Select, Item
from services import DCSServerBot
from typing import Any, Union


class TrapModal(Modal):
    time = TextInput(label='Time (HH24:MI)', style=TextStyle.short, required=True, min_length=5, max_length=5)
    case = TextInput(label='Case', style=TextStyle.short, required=True, min_length=1, max_length=1)
    grade = TextInput(label='Grade', style=TextStyle.short, required=True, min_length=1, max_length=4)
    comment = TextInput(label='LSO Comment', style=TextStyle.long, required=False)
    wire = TextInput(label='Wire', style=TextStyle.short, required=False, min_length=1, max_length=1)

    def __init__(self, bot: DCSServerBot, *, config: dict, user: Union[str, discord.Member], unit_type: str):
        super().__init__(title="Enter the trap details")
        self.bot = bot
        self.log = bot.log
        self.pool = bot.pool
        self.config = config
        self.user = user
        self.unit_type = unit_type
        self.success = False

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await interaction.response.defer()
        time = datetime.strptime(self.time.value, '%H:%M').time()
        night = time.hour >= 20 or time.hour <= 6
        if self.case.value not in ['1', '2', '3']:
            raise TypeError('Case needs to be one of 1, 2 or 3.')
        grade = self.grade.value.upper()
        if grade not in self.config['ratings'].keys():
            raise ValueError(
                "Grade has to be one of " + ', '.join([utils.escape_string(x) for x in self.config['ratings'].keys()]))
        if self.wire.value and self.wire.value not in ['1', '2', '3', '4']:
            raise TypeError('Wire needs to be one of 1 to 4.')

        if isinstance(self.user, discord.Member):
            ucid = self.bot.get_ucid_by_member(self.user)
        else:
            ucid = self.user

        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO greenieboard (mission_id, player_ucid, unit_type, grade, comment, place, night, 
                                              points, wire, trapcase) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (-1, ucid, self.unit_type, self.grade.value, self.comment.value, 'n/a', night,
                      self.config['ratings'][grade], self.wire.value, self.case.value))
            self.success = True

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.followup.send(error)
        self.stop()


class TrapView(View):

    def __init__(self, bot: DCSServerBot, config: dict, user: Union[str, discord.Member]):
        super().__init__()
        self.bot = bot
        self.log = bot.log
        self.config = config
        self.user = user
        self.success = False

    @discord.ui.select(placeholder='Select the plane for the trap',
                       options=[
                           SelectOption(label=x) for x in ['AV8BNA', 'F-14A-135-GR', 'F-14B', 'FA-18C_hornet', 'Su-33']
                       ])
    async def callback(self, interaction: discord.Interaction, select: Select):
        modal = TrapModal(self.bot, config=self.config, user=self.user, unit_type=select.values[0])
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.success = modal.success
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: Item[Any]) -> None:
        await interaction.followup.send(error)
        self.stop()
