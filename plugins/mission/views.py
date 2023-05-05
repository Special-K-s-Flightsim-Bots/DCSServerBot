import asyncio
import discord
import os
from contextlib import suppress
from core import Server, Report, Status, ReportEnv
from discord import SelectOption
from discord.ui import View, Select, Button
from typing import cast, Optional


class ServerView(View):
    def __init__(self, server: Server):
        super().__init__()
        self.server: Server = server
        self.env: Optional[ReportEnv] = None

    async def render(self, interaction: discord.Interaction) -> discord.Embed:
        report = Report(interaction.client, 'mission', 'serverStatus.json')
        self.env = await report.render(server=self.server)
        self.clear_items()
        missions = self.server.settings['missionList']
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
            button: Button = Button(style=discord.ButtonStyle.primary, emoji='▶️')
            button.callback = self.run
            self.add_item(button)
        elif self.server.status == Status.RUNNING:
            button: Button = Button(style=discord.ButtonStyle.primary, emoji='⏸️')
            button.callback = self.pause
            self.add_item(button)
        if self.server.status in [Status.RUNNING, Status.PAUSED]:
            button: Button = Button(style=discord.ButtonStyle.primary, emoji='⏹️')
            button.callback = self.stop_server
            self.add_item(button)
            button: Button = Button(style=discord.ButtonStyle.primary, emoji='🔁')
            button.callback = self.reload
            self.add_item(button)
        button: Button = Button(label='Quit', style=discord.ButtonStyle.red)
        button.callback = self.quit
        self.add_item(button)
        return self.env.embed

    async def load_mission(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.env.embed.set_footer(text="Loading mission, please wait ...")
        await interaction.edit_original_response(embed=self.env.embed)
        await self.server.loadMission(int(interaction.data['values'][0]) + 1)
        with suppress(asyncio.TimeoutError):
            await self.server.wait_for_status_change([Status.RUNNING], 2)
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def change_preset(self, interaction: discord.Interaction):
        pass

    async def run(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.server.status == Status.STOPPED:
            self.env.embed.set_footer(text="Starting, please wait ...")
            await interaction.edit_original_response(embed=self.env.embed)
            await self.server.start()
            with suppress(asyncio.TimeoutError):
                await self.server.wait_for_status_change([Status.RUNNING], 2)
        else:
            await self.server.current_mission.unpause()
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def pause(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.server.current_mission.pause()
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def stop_server(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.env.embed.set_footer(text="Stopping server, please wait ...")
        await interaction.edit_original_response(embed=self.env.embed)
        await self.server.stop()
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def reload(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.env.embed.set_footer(text="Restarting, please wait ...")
        await interaction.edit_original_response(embed=self.env.embed)
        await self.server.current_mission.restart()
        # wait for a possible resume
        with suppress(asyncio.TimeoutError):
            await self.server.wait_for_status_change([Status.RUNNING], 2)
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def quit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()


class PresetView(View):
    def __init__(self, options: list[discord.SelectOption]):
        super().__init__()
        select: Select = cast(Select, self.children[0])
        select.options = options
        select.max_values = min(10, len(options))
        self.result = None

    @discord.ui.select(placeholder="Select the preset(s) you want to apply")
    async def callback(self, interaction: discord.Interaction, select: Select):
        self.result = select.values
        await interaction.response.defer()

    @discord.ui.button(label='OK', style=discord.ButtonStyle.green)
    async def ok(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        self.result = None
        self.stop()
