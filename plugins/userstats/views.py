import discord
from core import DataObjectFactory, Member, Player, Server, utils, Report
from discord.ui import View, Button
from services import DCSServerBot
from typing import Union, Optional


class InfoView(View):

    def __init__(self, member: Union[discord.Member, str], bot: DCSServerBot, player: Optional[Player] = None,
                 server: Optional[Server] = None):
        super().__init__()
        self.member = member
        self.bot = bot
        self.player = player
        self.server = server

    async def render(self) -> discord.Embed:
        if isinstance(self.member, discord.Member):
            self._member: Member = DataObjectFactory().new('Member', node=self.bot.node, member=self.member)
            self.ucids = self._member.ucids
            if self._member.verified:
                button = Button(emoji="🔀")
                button.callback = self.on_unlink
                self.add_item(button)
            else:
                button = Button(emoji="💯")
                button.callback = self.on_verify
                self.add_item(button)
        else:
            self.ucids = [self.member]
        banned = await self.is_banned()
        if banned:
            button = Button(emoji="✅")
            button.callback = self.on_unban
            self.add_item(button)
        else:
            button = Button(emoji="⛔")
            button.callback = self.on_ban
            self.add_item(button)
        if self.player:
            button = Button(emoji="⏏️")
            button.callback = self.on_kick
            self.add_item(button)
        button = Button(label="Cancel", style=discord.ButtonStyle.red)
        button.callback = self.on_cancel
        self.add_item(button)
        report = Report(self.bot, 'userstats', 'info.json')
        env = await report.render(member=self.member, player=self.player, banned=banned)
        return env.embed

    async def is_banned(self) -> bool:
        for server in self.bot.servers.values():
            for ucid in self.ucids:
                if await server.is_banned(ucid):
                    return True
        return False

    async def on_cancel(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()

    async def on_ban(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # TODO: reason modal
        for server in self.bot.servers.values():
            for ucid in self.ucids:
                server.ban(ucid)
        await interaction.followup.send("User has been banned.")
        self.stop()

    async def on_unban(self, interaction: discord.Interaction):
        await interaction.response.defer()
        for server in self.bot.servers.values():
            for ucid in self.ucids:
                server.unban(ucid)
        await interaction.followup.send("User has been unbanned.")
        self.stop()

    async def on_kick(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # TODO: reason modal
        self.server.kick(player=self.player)
        await interaction.followup.send("User has been kicked.")
        self.stop()

    async def on_unlink(self, interaction: discord.Interaction):
        await interaction.response.defer()
        for ucid in self.ucids:
            self._member.unlink(ucid)
        await interaction.followup.send("Member has been unlinked.")
        self.stop()

    async def on_verify(self, interaction: discord.Interaction):
        await interaction.response.defer()
        for ucid in self.ucids:
            self._member.link(ucid)
        await interaction.followup.send("Member has been verified.")
        self.stop()
