import discord
import os

from core import utils
from discord import SelectOption
from discord.ui import View, Select, Button
from services import DCSServerBot

from .sink import Sink, Mode
from .utils import get_tag


class PlayerBase(View):

    def __init__(self, bot: DCSServerBot, music_dir: str):
        super().__init__()
        self.bot = bot
        self.log = bot.log
        self.pool = bot.pool
        self.music_dir = music_dir

    def get_titles(self, songs: list[str]) -> list[str]:
        return [get_tag(os.path.join(self.music_dir, x)).title or x[:-4] for x in songs]


class MusicPlayer(PlayerBase):

    def __init__(self, bot: DCSServerBot, music_dir: str, sink: Sink, playlists: list[str]):
        super().__init__(bot, music_dir)
        self.sink = sink
        self.playlists = playlists
        self.titles = self.get_titles(self.sink.songs)

    def render(self) -> discord.Embed:
        embed = self.sink.render()
        embed.title = "Music Player"
        if self.sink.current:
            tag = get_tag(self.sink.current)
            title = utils.escape_string(tag.title[:255] if tag.title else os.path.basename(self.sink.current)[:-4])
            artist = utils.escape_string(tag.artist[:255] if tag.artist else 'n/a')
            album = utils.escape_string(tag.album[:255] if tag.album else 'n/a')
            embed.add_field(name='▬' * 13 + " Now Playing " + '▬' * 13, value='_ _', inline=False)
            embed.add_field(name="Title", value=title)
            embed.add_field(name='Artist', value=artist)
            embed.add_field(name='Album', value=album)
        embed.add_field(name='▬' * 14 + " Playlist " + '▬' * 14, value='_ _', inline=False)
        playlist = []
        for idx, title in enumerate(self.titles):
            playlist.append(
                f"{idx + 1}. - {utils.escape_string(title)}")
        all_songs = '\n'.join(playlist) or '- empty -'
        embed.add_field(name='_ _', value=all_songs[:1024])
        footer = "▬" * 37 + "\n"
        self.clear_items()
        # Select Song
        if self.titles:
            select = Select(placeholder="Pick a song from the list")
            select.options = [
                SelectOption(label=x[:25], value=str(idx)) for idx, x in enumerate(self.titles)
            ]
            select.callback = self.play
            self.add_item(select)
        # Select Playlists
        if self.playlists:
            select = Select(placeholder="Pick a playlist to play")
            select.options = [SelectOption(label=x) for x in self.playlists]
            select.callback = self.playlist
            self.add_item(select)
        # Play/Stop Button
        button = Button(style=discord.ButtonStyle.primary, emoji="⏹️" if self.sink.queue_worker.is_running() else "▶️")
        button.callback = self.on_play_stop
        self.add_item(button)
        # Skip Button
        button = Button(style=discord.ButtonStyle.primary, emoji="⏩")
        button.callback = self.on_skip
        self.add_item(button)
        # Repeat Button
        button = Button(style=discord.ButtonStyle.primary, emoji="🔁" if self.sink.mode == Mode.REPEAT else "🔂")
        button.callback = self.on_skip
        self.add_item(button)
        # Edit Button
        button = Button(label="Edit", style=discord.ButtonStyle.secondary)
        button.callback = self.on_edit
        self.add_item(button)
        # Quit Button
        button = Button(label="Quit", style=discord.ButtonStyle.red)
        button.callback = self.on_cancel
        self.add_item(button)
        if self.sink.queue_worker.is_running():
            footer += "⏹️ Stop"
        else:
            footer += "▶️ Play"
        footer += " | ⏩ Skip | "
        if self.sink.mode == Mode.SHUFFLE:
            footer += "🔁 Repeat"
        else:
            footer += "🔂 Shuffle"
        embed.set_footer(text=footer)
        return embed

    async def play(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.sink.stop()
        self.sink.idx = int(interaction.data['values'][0])
        await self.sink.start()
        await interaction.edit_original_response(view=self, embed=self.render())

    async def playlist(self, interaction: discord.Interaction):
        await interaction.response.defer()
        running = self.sink.is_running()
        if running:
            await self.sink.stop()
        self.sink.playlist = interaction.data['values'][0]
        self.titles = self.get_titles(self.sink.songs)
        if running:
            await self.sink.start()
        await interaction.edit_original_response(view=self, embed=self.render())

    async def on_play_stop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.sink.queue_worker.is_running():
            await self.sink.stop()
        else:
            await self.sink.start()
        embed = self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_skip(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.sink.skip()
        embed = self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_repeat(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.sink.stop()
        if self.sink.mode == Mode.SHUFFLE:
            self.sink.mode = Mode.REPEAT
        else:
            self.sink.mode = Mode.SHUFFLE
        await self.sink.start()
        embed = self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_edit(self, interaction: discord.Interaction):
        modal = self.sink.edit()
        await interaction.response.send_modal(modal)
        if not await modal.wait():
            embed = self.render()
            await interaction.edit_original_response(embed=embed, view=self)

    async def on_cancel(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()
