import discord
import os
import psycopg2
from contextlib import closing
from core import utils, DCSServerBot
from discord import SelectOption, TextStyle
from discord.ui import View, Select, Button, TextInput, Modal
from typing import Optional

from .sink import Sink, Mode
from .utils import get_tag, Playlist


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
        embed.add_field(name='_ _', value='\n'.join(playlist) or '- empty -')
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


class PlaylistEditor(PlayerBase):

    def __init__(self, bot: DCSServerBot, music_dir: str, songs: list[str], playlist: Optional[str] = None):
        super().__init__(bot, music_dir)
        self.playlist = Playlist(bot, playlist) if playlist else None
        self.all_songs = songs
        self.all_titles = self.get_titles(self.all_songs)

    def get_all_playlists(self) -> list[str]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT DISTINCT name FROM music_playlists ORDER BY 1')
                return [row[0] for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def render(self) -> discord.Embed:
        embed = discord.Embed(title="Playlist Editor", colour=discord.Colour.blue())
        self.clear_items()
        row = 0
        if self.playlist:
            embed.description = f'Playlist "{self.playlist.name}"'
            playlist = []
            options = []
            for idx, song in enumerate(self.playlist.items):
                if idx == 25:
                    break
                title = self.all_titles[self.all_songs.index(song)] or song
                playlist.append(
                    f"{idx + 1}. - {utils.escape_string(title)}")
                options.append(SelectOption(label=title[:25], value=str(idx)))
            if playlist:
                embed.add_field(name='_ _', value='\n'.join(playlist))
                select = Select(placeholder="Remove a song from the playlist", options=options, row=row)
                select.callback = self.remove
                self.add_item(select)
                row += 1
            else:
                embed.add_field(name='_ _', value='- empty -')
            select = Select(placeholder="Add a song to the playlist",
                            options=[SelectOption(label=x,
                                                  value=str(idx)) for idx, x in enumerate(self.all_titles) if idx < 25],
                            row=row)
            select.callback = self.add
            self.add_item(select)
            row += 1
            button = Button(label="Delete", style=discord.ButtonStyle.secondary, row=row)
            button.callback = self.del_playlist
            self.add_item(button)
        else:
            all_playlists = self.get_all_playlists()
            if all_playlists:
                select = Select(placeholder="Select a playlist to edit",
                                options=[SelectOption(label=x) for x in all_playlists], row=row)
                select.callback = self.load_playlist
                self.add_item(select)
                row += 1
            if len(all_playlists) < 25:
                button = Button(label="Add", style=discord.ButtonStyle.secondary, row=row)
                button.callback = self.add_playlist
                self.add_item(button)
        button = Button(label="Quit", style=discord.ButtonStyle.red, row=row)
        button.callback = self.cancel
        self.add_item(button)
        return embed

    async def add(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.playlist.add(self.all_songs[int(interaction.data['values'][0])])
        embed = self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def remove(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.playlist.remove(self.playlist.items[int(interaction.data['values'][0])])
        embed = self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def load_playlist(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.playlist = Playlist(self.bot, interaction.data['values'][0])
        embed = self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def add_playlist(self, interaction: discord.Interaction):
        class AddPlaylistModal(Modal, title="Enter a name for the playlist"):
            name = TextInput(label="Playlist", style=TextStyle.short, required=True, min_length=3, max_length=25)

            async def on_submit(_, interaction: discord.Interaction) -> None:
                await interaction.response.defer()

        modal = AddPlaylistModal()
        await interaction.response.send_modal(modal)
        if not await modal.wait():
            self.playlist = Playlist(self.bot, modal.name.value)
        embed = self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def del_playlist(self, interaction: discord.Interaction):
        class DelPlaylistModal(Modal, title="Are you sure?"):
            name = TextInput(label="Enter YES, if you want to delete the playlist",
                             style=TextStyle.short, required=True, min_length=3, max_length=3)

            async def on_submit(_, interaction: discord.Interaction) -> None:
                await interaction.response.defer()

        modal = DelPlaylistModal()
        await interaction.response.send_modal(modal)
        if not await modal.wait():
            if str(modal.name.value).casefold() == 'yes':
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('DELETE FROM music_playlists WHERE name = %s', (self.playlist.name, ))
                        cursor.execute('DELETE FROM music_servers WHERE playlist_name = %s', (self.playlist.name,))
                    conn.commit()
                    self.playlist = None
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)
        embed = self.render()
        await interaction.edit_original_response(embed=embed, view=self)

    async def cancel(self, interaction: discord.Interaction):
        self.stop()
