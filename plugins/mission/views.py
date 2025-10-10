import asyncio
import discord
import os

from contextlib import suppress
from core import Server, Report, Status, ReportEnv, Player, Member, DataObjectFactory, utils
from discord import SelectOption, ButtonStyle
from discord.ui import View, Select, Button
from io import StringIO
from ruamel.yaml import YAML
from typing import cast

from services.bot import DCSServerBot

WARNING_ICON = "https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/warning.png?raw=true"


class ServerView(View):
    def __init__(self, server: Server):
        super().__init__()
        self.server: Server = server
        self.env: ReportEnv | None = None
        self.modify_mission = True

    async def render(self, interaction: discord.Interaction) -> discord.Embed:
        report = Report(interaction.client, 'mission', 'serverStatus.json')
        self.env = await report.render(server=self.server)
        self.clear_items()
        missions = (await self.server.getMissionList())[:25]
        if len(missions) > 1:
            select: Select = Select(placeholder="Select another mission", options=[
                SelectOption(label=os.path.basename(x)[:-4], value=str(idx))
                for idx, x in enumerate(missions)
                if idx <= 25
            ])
            select.callback = self.load_mission
            self.add_item(select)
#        presets = self.get_presets()
#        if presets:
#            select: Select = Select(placeholder="Select a preset", options=[
#                SelectOption(label=x, value=str(idx))
#                for idx, x in enumerate(self.get_presets())
#                if idx <= 25
#            ])
#            select.callback = self.change_preset
#            self.add_item(select)
        if self.server.status in [Status.PAUSED, Status.STOPPED]:
            # noinspection PyTypeChecker
            button: Button = Button(style=ButtonStyle.primary, emoji='▶️')
            button.callback = self.run
            self.add_item(button)
        elif self.server.status == Status.RUNNING:
            # noinspection PyTypeChecker
            button: Button = Button(style=ButtonStyle.primary, emoji='⏸️')
            button.callback = self.pause
            self.add_item(button)
        if self.server.status in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyTypeChecker
            button: Button = Button(style=ButtonStyle.primary, emoji='⏹️')
            button.callback = self.stop_server
            self.add_item(button)
            # noinspection PyTypeChecker
            button: Button = Button(style=ButtonStyle.primary, emoji='🔁')
            button.callback = self.reload
            self.add_item(button)
        # noinspection PyTypeChecker
        button: Button = Button(style=ButtonStyle.primary if self.modify_mission else ButtonStyle.gray,
                                emoji='⛅' if self.modify_mission else '🚫')
        button.callback = self.toggle_modify
        self.add_item(button)
        # noinspection PyTypeChecker
        button: Button = Button(label='Quit', style=ButtonStyle.red)
        button.callback = self.quit
        self.add_item(button)
        return self.env.embed

    async def load_mission(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.env.embed.set_footer(text="Loading mission, please wait ...")
        await interaction.edit_original_response(embed=self.env.embed)
        if not await self.server.loadMission(int(interaction.data['values'][0]) + 1,
                                             modify_mission=self.modify_mission):
            self.env.embed.set_footer(text="Mission loading failed.", icon_url=WARNING_ICON)
            await interaction.edit_original_response(embed=self.env.embed)
        else:
            with suppress(TimeoutError, asyncio.TimeoutError):
                await self.server.wait_for_status_change([Status.RUNNING], 2)
            await self.render(interaction)
            await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def change_preset(self, interaction: discord.Interaction):
        pass

    async def run(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        if self.server.status == Status.STOPPED:
            self.env.embed.set_footer(text="Starting, please wait ...")
            await interaction.edit_original_response(embed=self.env.embed)
            await self.server.start()
            with suppress(TimeoutError, asyncio.TimeoutError):
                await self.server.wait_for_status_change([Status.RUNNING], 2)
        else:
            await self.server.current_mission.unpause()
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def pause(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        await self.server.current_mission.pause()
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def stop_server(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.env.embed.set_footer(text="Stopping server, please wait ...")
        await interaction.edit_original_response(embed=self.env.embed)
        await self.server.stop()
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def reload(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.env.embed.set_footer(text="Restarting, please wait ...")
        await interaction.edit_original_response(embed=self.env.embed)
        await self.server.current_mission.restart()
        # wait for a possible resume
        with suppress(TimeoutError, asyncio.TimeoutError):
            await self.server.wait_for_status_change([Status.RUNNING], 2)
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def toggle_modify(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.modify_mission = not self.modify_mission
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def quit(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()


class PresetView(View):
    def __init__(self, options: list[discord.SelectOption], multi: bool = True):
        super().__init__()
        select: Select = cast(Select, self.children[0])
        select.options = options
        if multi:
            select.max_values = min(10, len(options))
        else:
            select.max_values = 1
        self.result: list[str] | None = None

    @discord.ui.select(placeholder="Select the preset(s) you want to apply")
    async def callback(self, interaction: discord.Interaction, select: Select):
        self.result = select.values
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()

    # noinspection PyTypeChecker
    @discord.ui.button(label='OK', style=ButtonStyle.green)
    async def ok(self, interaction: discord.Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()

    # noinspection PyTypeChecker
    @discord.ui.button(label='Cancel', style=ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.result = None
        self.stop()


class InfoView(View):

    def __init__(self, member: discord.Member | str, bot: DCSServerBot, ephemeral: bool,
                 player: Player | None = None, server: Server | None = None):
        super().__init__()
        self.member = member
        self.bot = bot
        self.ephemeral = ephemeral
        self.player = player
        self.server = server
        if isinstance(self.member, discord.Member):
            self._member = DataObjectFactory().new(Member, name=self.member.name, node=self.bot.node,
                                                   member=self.member)
            self.ucid = self._member.ucid
        else:
            self._member = None
            self.ucid = self.member

    async def render(self) -> discord.Embed:
        if not self._member or self._member.ucid:
            if isinstance(self.member, discord.Member):
                button = Button(emoji="🔀")
                button.callback = self.on_unlink
                self.add_item(button)
                if not self._member.verified:
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
            watchlist = await self.is_watchlist()
            if watchlist:
                button = Button(emoji="🆓")
                button.callback = self.on_unwatch
                self.add_item(button)
            else:
                button = Button(emoji="🔍")
                button.callback = self.on_watch
                self.add_item(button)
            if self.player:
                button = Button(emoji="⏏️")
                button.callback = self.on_kick
                self.add_item(button)
        else:
            banned = watchlist = False
        # noinspection PyTypeChecker
        button = Button(label="Cancel", style=ButtonStyle.red)
        button.callback = self.on_cancel
        self.add_item(button)
        report = Report(self.bot, 'mission', 'info.json')
        env = await report.render(member=self.member, ucid=self.ucid, player=self.player, banned=banned,
                                  watchlist=watchlist)
        return env.embed

    async def is_banned(self) -> bool:
        return await self.bot.bus.is_banned(self.ucid) is not None

    async def is_watchlist(self) -> bool:
        async with self.bot.apool.connection() as conn:
            cursor = await conn.execute("SELECT True FROM watchlist WHERE player_ucid = %s", (self.ucid,))
            row = await cursor.fetchone()
        return row[0] if row else False

    async def on_cancel(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()

    async def on_ban(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        # TODO: reason modal
        await self.bot.bus.ban(ucid=self.ucid, reason='n/a', banned_by=interaction.user.display_name)
        await interaction.followup.send("User has been banned.", ephemeral=self.ephemeral)
        name = self.player.name if self.player else self.member.display_name if isinstance(self.member, discord.Member) else self.member
        message = f'banned user {name} '
        if not utils.is_ucid(name):
            message += '(ucid={self.ucid}) '
        message += 'permanently'
        await self.bot.audit(message, user=interaction.user)
        self.stop()

    async def on_unban(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        await self.bot.bus.unban(self.ucid)
        await interaction.followup.send("User has been unbanned.", ephemeral=self.ephemeral)
        await self.bot.audit(f'unbanned user {self.ucid}.', user=interaction.user)
        self.stop()

    async def on_kick(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        # TODO: reason modal
        await self.server.kick(player=self.player)
        await interaction.followup.send("User has been kicked.", ephemeral=self.ephemeral)
        await self.bot.audit(f'kicked player {self.player.name} (ucid={self.player.ucid}).', user=interaction.user)
        self.stop()

    async def on_unlink(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        member: discord.Member = self._member.member
        self._member.unlink()
        await self.bot.bus.send_to_node({
            "command": "rpc",
            "service": "ServiceBus",
            "method": "propagate_event",
            "params": {
                "command": "onMemberUnlinked",
                "server": None,
                "data": {
                    "ucid": self.ucid,
                    "discord_id": member.id
                }
            }
        })
        await interaction.followup.send("Member has been unlinked.", ephemeral=self.ephemeral)
        # If autorole is enabled, remove the DCS role from the user:
        autorole = self.bot.locals.get('autorole', {}).get('linked')
        if autorole:
            try:
                await member.remove_roles(self.bot.get_role(autorole))
            except discord.Forbidden:
                await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
        await self.bot.audit(f'unlinked member {member.display_name} from UCID {self.ucid}.', user=interaction.user)
        self.stop()

    async def on_verify(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        member: discord.Member = self._member.member
        self._member.verified = True
        await self.bot.bus.send_to_node({
            "command": "rpc",
            "service": "ServiceBus",
            "method": "propagate_event",
            "params": {
                "command": "onMemberLinked",
                "server": None,
                "data": {
                    "ucid": self.ucid,
                    "discord_id": member.id
                }
            }
        })
        await interaction.followup.send("Member has been verified.", ephemeral=self.ephemeral)
        # If autorole is enabled, give the user the role:
        autorole = self.bot.locals.get('autorole', {}).get('linked')
        if autorole:
            try:
                await member.add_roles(self.bot.get_role(autorole))
            except discord.Forbidden:
                await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)
        await self.bot.audit(f'linked member {member.display_name} to UCID {self.ucid}.', user=interaction.user)
        self.stop()

    async def on_watch(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        async with self.bot.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO watchlist (player_ucid, reason, created_by) VALUES (%s, %s, %s)",
                                   (self.ucid, 'n/a', interaction.user.display_name))
        await interaction.followup.send("User is now on the watchlist.", ephemeral=self.ephemeral)
        self.stop()

    async def on_unwatch(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        async with self.bot.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM watchlist WHERE player_ucid = %s", (self.ucid, ))
        await interaction.followup.send("User removed from the watchlist.", ephemeral=self.ephemeral)
        name = self.player.name if self.player else self.member.display_name if isinstance(self.member, discord.Member) else self.member
        message = f'removed player {name} '
        if not utils.is_ucid(name):
            message += '(ucid={self.ucid}) '
        message += 'from the watchlist'
        await self.bot.audit(message, user=interaction.user)
        self.stop()


class ModifyView(View):
    def __init__(self, presets: dict, mission_change: str, warehouses_change: str, options_change: str):
        super().__init__()
        self.presets = presets
        self.embed = discord.Embed(color=discord.Color.blue())
        self.render()
        self.mission_change = self.cut(mission_change)
        self.warehouses_change = self.cut(warehouses_change)
        self.options_change = self.cut(options_change)

        # noinspection PyTypeChecker
        button = Button(label="Presets", style=ButtonStyle.primary)
        button.callback = self.display_presets
        self.add_item(button)

        if self.mission_change:
            # noinspection PyTypeChecker
            button = Button(label="mission", style=ButtonStyle.secondary)
            button.callback = self.display_mission
            self.add_item(button)

        if self.warehouses_change:
            # noinspection PyTypeChecker
            button = Button(label="warehouses", style=ButtonStyle.secondary)
            button.callback = self.display_warehouses
            self.add_item(button)

        if self.options_change:
            # noinspection PyTypeChecker
            button = Button(label="options", style=ButtonStyle.secondary)
            button.callback = self.display_options
            self.add_item(button)

        # noinspection PyTypeChecker
        button = Button(label="Cancel", style=ButtonStyle.red)
        button.callback = self.cancel
        self.add_item(button)

    @staticmethod
    def cut(message: str | None = None) -> str:
        if not message or len(message) <= 4096:
            return message
        remark = f"``` ... {len(message) - 4096} more"
        return message[:4096 - len(remark)] + remark

    def render(self):
        yaml = YAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        yaml.default_flow_style = False
        yaml.sort_keys = True
        stream = StringIO()

        self.embed.title = 'Presets'
        self.embed.description = 'These modifications will be applied to your mission:\n\n'
        for k, v in self.presets.items():
            yaml.dump(v, stream)
            self.embed.description += "{k}:\n```yaml\n{v}\n```".format(k=k, v=stream.getvalue())

    async def display_presets(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.render()
        await interaction.edit_original_response(embed=self.embed)

    async def display_mission(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.embed.title = 'mission'
        self.embed.description = self.mission_change
        await interaction.edit_original_response(embed=self.embed)

    async def display_warehouses(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.embed.title = 'warehouses'
        self.embed.description = self.warehouses_change
        await interaction.edit_original_response(embed=self.embed)

    async def display_options(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.embed.title = 'options'
        self.embed.description = self.options_change
        await interaction.edit_original_response(embed=self.embed)

    async def cancel(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()
