import aiohttp
import asyncio
import discord
import os
import sys

from core import Plugin, utils, Server, TEventListener, PluginInstallationError, Status, Group, ServiceRegistry
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from services import DCSServerBot, MusicService
from typing import Type, cast, Optional

from .listener import MusicEventListener
from services.music.sink import Sink
from .utils import playlist_autocomplete, all_songs_autocomplete, songs_autocomplete, get_all_playlists, get_tag, \
    Playlist
from .views import MusicPlayer


class Music(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.service: MusicService = cast(MusicService, ServiceRegistry.get("Music"))

    def get_config(self, server: Optional[Server] = None, *, plugin_name: Optional[str] = None,
                   use_cache: Optional[bool] = True) -> dict:
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        return self.service.get_config(server)

    def get_music_dir(self) -> str:
        music_dir = self.get_config()['music_dir']
        if not os.path.exists(music_dir):
            os.makedirs(music_dir)
        return music_dir

    # New command group "/music"
    music = Group(name="music", description="Commands to manage music in your (DCS) server")

    @music.command(description='Music Player')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def player(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING,
                                                                                            Status.PAUSED])]):
        sink = self.service.sinks.get(server.name)
        if not sink:
            if not self.get_config(server):
                await interaction.response.send_message(
                    f"No entry for server {server.name} configured in your {self.plugin_name}.yaml.", ephemeral=True)
                return
            config = self.get_config(server)['sink']
            sink: Sink = getattr(sys.modules['plugins.music.sink'], config['type'])(
                bot=self.bot, server=server, config=config, music_dir=self.get_config(server)['music_dir'])
            self.service.sinks[server.name] = sink
        playlists = get_all_playlists(self.bot)
        if not playlists:
            await interaction.response.send_message(
                f"You don't have any playlists to play. Please create one with /music add", ephemeral=True)
            return
        view = MusicPlayer(self.bot, music_dir=self.get_music_dir(), sink=sink, playlists=playlists)
        await interaction.response.send_message(embed=view.render(), view=view, ephemeral=True)
        msg = await interaction.original_response()
        try:
            while not view.is_finished():
                await msg.edit(embed=view.render(), view=view)
                await asyncio.sleep(1)
        finally:
            await msg.delete()

    @music.command(description="Play a song from a playlist")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(song=songs_autocomplete)
    async def play(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING,
                                                                                          Status.PAUSED])],
                   playlist: str, song: str):
        sink = self.service.sinks.get(server.name)
        if not sink:
            if not self.get_config(server):
                await interaction.response.send_message(
                    f"No entry for server {server.name} configured in your {self.plugin_name}.yaml.", ephemeral=True)
                return
        song = os.path.join(self.get_music_dir(), song)
        title = get_tag(song).title or os.path.basename(song)
        await interaction.response.send_message(f"Now playing {title} ...")
        await sink.play(song)

    @music.command(description="Add a song to a playlist")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(song=all_songs_autocomplete)
    async def add(self, interaction: discord.Interaction, playlist: str, song: str):
        p = Playlist(self.bot, playlist)
        p.add(song)
        song = os.path.join(self.get_music_dir(), song)
        title = get_tag(song).title or os.path.basename(song)
        await interaction.response.send_message(
            '{} has been added to playlist {}.'.format(utils.escape_string(title), playlist), ephemeral=True)

    @music.command(description="Add all available songs to a playlist")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    async def add_all(self, interaction: discord.Interaction, playlist: str):
        if not await utils.yn_question(interaction, 'Do you really want to add ALL songs to the playlist?'):
            return
        p = Playlist(self.bot, playlist)
        for song in [file.name for file in Path(interaction.command.binding.get_music_dir()).glob('*.mp3')]:
            p.add(song)
            song = os.path.join(self.get_music_dir(), song)
            title = get_tag(song).title or os.path.basename(song)
            await interaction.followup.send(
                '{} has been added to playlist {}.'.format(utils.escape_string(title), playlist), ephemeral=True)

    @music.command(description="Remove a song from a playlist")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(song=songs_autocomplete)
    async def delete(self, interaction: discord.Interaction, playlist: str, song: str):
        p = Playlist(self.bot, playlist)
        try:
            p.remove(song)
            song = os.path.join(self.get_music_dir(), song)
            title = get_tag(song).title or os.path.basename(song)
            await interaction.response.send_message(
                '{} has been removed from playlist {}.'.format(utils.escape_string(title), playlist), ephemeral=True)
        except OSError as ex:
            await interaction.response.send_message(ex)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages or messages that do not contain music attachments
        if message.author.bot or not message.attachments:
            return
        # only DCS Admin role is allowed to upload missions in the servers admin channel
        if not utils.check_roles(self.bot.roles['DCS Admin'], message.author):
            return
        delete = True
        try:
            ctx = await self.bot.get_context(message)
            for att in message.attachments:
                if att.filename[-4:] not in ['.mp3', '.ogg']:
                    delete = False
                    return
                if len(att.filename) > 100:
                    ext = att.filename[-4:]
                    filename = self.get_music_dir() + os.path.sep + (att.filename[:-4])[:96] + ext
                else:
                    filename = self.get_music_dir() + os.path.sep + att.filename
                if os.path.exists(filename):
                    if not await utils.yn_question(ctx, 'File exists. Do you want to overwrite it?'):
                        continue
                async with aiohttp.ClientSession() as session:
                    async with session.get(att.url) as response:
                        if response.status == 200:
                            with open(filename, 'wb') as outfile:
                                outfile.write(await response.read())
                            await message.channel.send('File {} uploaded.'.format(utils.escape_string(att.filename)))
                        else:
                            await message.channel.send(
                                'Error {} while reading file {}!'.format(response.status,
                                                                         utils.escape_string(att.filename)))
        except Exception as ex:
            self.log.exception(ex)
        finally:
            if delete:
                await message.delete()


async def setup(bot: DCSServerBot):
    await bot.add_cog(Music(bot, MusicEventListener))
