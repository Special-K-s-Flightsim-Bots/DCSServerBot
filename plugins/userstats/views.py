import discord

from core import DataObjectFactory, Member, Player, Server, Report
from discord.ui import View, Button
from services import DCSServerBot
from typing import Union, Optional


class InfoView(View):

    def __init__(self, member: Union[discord.Member, str], bot: DCSServerBot, ephemeral: bool,
                 player: Optional[Player] = None, server: Optional[Server] = None):
        super().__init__()
        self.member = member
        self.bot = bot
        self.ephemeral = ephemeral
        self.player = player
        self.server = server
        if isinstance(self.member, discord.Member):
            self._member: Member = DataObjectFactory().new('Member', node=self.bot.node, member=self.member)
            self.ucid = self._member.ucid
        else:
            self.ucid = self.member

    async def render(self) -> discord.Embed:
        if isinstance(self.member, discord.Member):
            if self._member.verified:
                button = Button(emoji="🔀")
                button.callback = self.on_unlink
                self.add_item(button)
            else:
                button = Button(emoji="💯")
                button.callback = self.on_verify
                self.add_item(button)
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
        watchlist = await self.is_watchlist()
        if watchlist:
            button = Button(emoji="🆓")
            button.callback = self.on_unwatch
            self.add_item(button)
        else:
            button = Button(emoji="🔍")
            button.callback = self.on_watch
            self.add_item(button)
        button = Button(label="Cancel", style=discord.ButtonStyle.red)
        button.callback = self.on_cancel
        self.add_item(button)
        report = Report(self.bot, 'userstats', 'info.json')
        env = await report.render(member=self.member, player=self.player, banned=banned, watchlist=watchlist)
        return env.embed

    async def is_banned(self) -> bool:
        return await self.bot.bus.is_banned(self.ucid) is not None

    async def is_watchlist(self) -> bool:
        async with self.bot.apool.connection() as conn:
            cursor = await conn.execute("SELECT watchlist FROM players WHERE ucid = %s", (self.ucid,))
            row = await cursor.fetchone()
        return row[0] if row else False

    async def on_cancel(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()

    async def on_ban(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # TODO: reason modal
        await self.bot.bus.ban(ucid=self.ucid, reason='n/a', banned_by=interaction.user.display_name)
        await interaction.followup.send("User has been banned.", ephemeral=self.ephemeral)
        self.stop()

    async def on_unban(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.bot.bus.unban(self.ucid)
        await interaction.followup.send("User has been unbanned.", ephemeral=self.ephemeral)
        self.stop()

    async def on_kick(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # TODO: reason modal
        self.server.kick(player=self.player)
        await interaction.followup.send("User has been kicked.", ephemeral=self.ephemeral)
        self.stop()

    async def on_unlink(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._member.unlink(self.ucid)
        await interaction.followup.send("Member has been unlinked.", ephemeral=self.ephemeral)
        self.stop()

    async def on_verify(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._member.link(self.ucid)
        await interaction.followup.send("Member has been verified.", ephemeral=self.ephemeral)
        self.stop()

    async def on_watch(self, interaction: discord.Interaction):
        await interaction.response.defer()
        async with self.bot.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE players SET watchlist = TRUE WHERE ucid = %s", (self.ucid, ))
        await interaction.followup.send("User is now on the watchlist.", ephemeral=self.ephemeral)
        self.stop()

    async def on_unwatch(self, interaction: discord.Interaction):
        await interaction.response.defer()
        async with self.bot.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE players SET watchlist = FALSE WHERE ucid = %s", (self.ucid, ))
        await interaction.followup.send("User removed from the watchlist.", ephemeral=self.ephemeral)
        self.stop()
