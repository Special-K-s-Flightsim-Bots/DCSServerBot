import aiohttp
import asyncio
import discord
import os

from core import Plugin, TEventListener, PluginInstallationError, Status, Group, utils, Server, ServiceRegistry
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from services import DCSServerBot, MusicService
from typing import Type, Optional, cast

from .listener import MusicEventListener
from .utils import radios_autocomplete, get_all_playlists, playlist_autocomplete, songs_autocomplete, get_tag, Playlist, \
    all_songs_autocomplete
from .views import MusicPlayer


class Music(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.service: MusicService = cast(MusicService, ServiceRegistry.get("Music"))
        if not self.service.locals:
            raise PluginInstallationError(plugin=self.plugin_name, reason=r"No config\services\music.yaml found!")

    def get_config(self, server: Optional[Server] = None, *, plugin_name: Optional[str] = None,
                   use_cache: Optional[bool] = True) -> dict:
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        return self.service.get_config(server)

    # New command group "/music"
    music = Group(name="music", description="Commands to manage music in your (DCS) server")

    @music.command(description='Music Player')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(_server="server")
    @app_commands.autocomplete(radio_name=radios_autocomplete)
    async def player(self, interaction: discord.Interaction,
                     _server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING,
                                                                                             Status.PAUSED])],
                     radio_name: str):
        playlists = get_all_playlists(interaction)
        if not playlists:
            await interaction.response.send_message(
                f"You don't have any playlists to play. Please create one with /music add", ephemeral=True)
            return
        view = MusicPlayer(server=_server, radio_name=radio_name, playlists=playlists)
        await interaction.response.send_message(embed=await view.render(), view=view, ephemeral=True)
        msg = await interaction.original_response()
        try:
            while not view.is_finished():
                await msg.edit(embed=await view.render(), view=view)
                await asyncio.sleep(1)
        finally:
            await msg.delete()

    @music.command(description="Play a song or a playlist on a specific radio")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(radio_name=radios_autocomplete)
    @app_commands.autocomplete(song=songs_autocomplete)
    async def play(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING,
                                                                                          Status.PAUSED])],
                   radio_name: str, playlist: Optional[str] = None, song: Optional[str] = None):
        if server.status != Status.RUNNING:
            await interaction.response.send_message(f'Server {server.name} is not running.', ephemeral=True)
            return
        if song:
            song = os.path.join(await self.service.get_music_dir(), song)
            title = get_tag(song).title or os.path.basename(song)
            await interaction.response.send_message(f"Now playing {title} ...", ephemeral=True)
            await self.service.play_song(server, radio_name, song)
        else:
            if playlist:
                await interaction.response.send_message(f"Now playing {playlist} ...", ephemeral=True)
                await self.service.stop_radios(server, radio_name)
                await self.service.set_playlist(server, radio_name, playlist)
            else:
                await interaction.response.send_message(f"Radio {radio_name} started.", ephemeral=True)
            await self.service.start_radios(server, radio_name)

    @music.command(description="Stop playing on a specific radio")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(radio_name=radios_autocomplete)
    async def stop(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING,
                                                                                          Status.PAUSED])],
                   radio_name: str):
        if server.status != Status.RUNNING:
            await interaction.response.send_message(f'Server {server.name} is not running.', ephemeral=True)
            return
        await self.service.stop_radios(server, radio_name)
        await interaction.response.send_message(f"Radio {radio_name} stopped.", ephemeral=True)

    @music.command(description="Add a song to a playlist")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(song=all_songs_autocomplete)
    async def add(self, interaction: discord.Interaction, playlist: str, song: str):
        p = Playlist(playlist)
        p.add(song)
        song = os.path.join(await self.service.get_music_dir(), song)
        title = get_tag(song).title or os.path.basename(song)
        await interaction.response.send_message(
            '{} has been added to playlist {}.'.format(utils.escape_string(title), playlist), ephemeral=True)

    @music.command(description="Add all available songs to a playlist")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    async def add_all(self, interaction: discord.Interaction, playlist: str):
        if not await utils.yn_question(interaction, 'Do you really want to add ALL songs to the playlist?'):
            return
        p = Playlist(playlist)
        for song in [file for file in Path(await self.service.get_music_dir()).glob('*.mp3')]:
            p.add(song.name)
            title = get_tag(song).title or song.name
            await interaction.followup.send(
                '{} has been added to playlist {}.'.format(utils.escape_string(title), playlist), ephemeral=True)

    @music.command(description="Remove a song from a playlist")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(song=songs_autocomplete)
    async def delete(self, interaction: discord.Interaction, playlist: str, song: str):
        p = Playlist(playlist)
        try:
            p.remove(song)
            song = os.path.join(await self.service.get_music_dir(), song)
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
                    filename = os.path.join(await self.service.get_music_dir(), (att.filename[:-4])[:96] + ext)
                else:
                    filename = os.path.join(await self.service.get_music_dir(), att.filename)
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
