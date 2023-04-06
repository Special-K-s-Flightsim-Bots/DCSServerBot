import aiohttp
import asyncio
import discord
import logging
import os
import sys
from core import Plugin, DCSServerBot, utils, Server, TEventListener, PluginInstallationError
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from typing import Type

from .listener import MusicEventListener
from .sink import Sink
from .utils import playlist_autocomplete, all_songs_autocomplete, songs_autocomplete, get_all_playlists, get_tag, \
    Playlist
from .views import MusicPlayer, PlaylistEditor


class MusicAgent(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.json file found!", plugin=self.plugin_name)
        self.sinks: dict[str, Sink] = dict()
        logging.getLogger(name='eyed3.mp3.headers').setLevel(logging.FATAL)

    async def cog_unload(self):
        for sink in self.sinks.values():
            await sink.stop()
        await super().cog_unload()

    def get_music_dir(self) -> str:
        music_dir = self.locals['configs'][0]['music_dir']
        if not os.path.exists(music_dir):
            os.makedirs(music_dir)
        return music_dir

    @commands.command(description='Music Player')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def music(self, ctx: commands.Context):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        sink = self.sinks.get(server.name)
        if not sink:
            if not self.get_config(server):
                await ctx.send(f"No entry for server {server.name} configured in your {self.plugin_name}.json.")
                return
            config = self.get_config(server)['sink']
            sink: Sink = getattr(sys.modules['plugins.music.sink'], config['type'])(bot=self.bot,
                                                                                    server=server,
                                                                                    config=config)
            self.sinks[server.name] = sink
        playlists = get_all_playlists(self.bot)
        if not playlists:
            await ctx.send(f"You don't have any playlists to play. Please create them with {ctx.prefix}playlist")
            return
        view = MusicPlayer(self.bot, sink=sink, playlists=playlists)
        msg = await ctx.send(embed=view.render(), view=view)
        try:
            while not view.is_finished():
                await msg.edit(embed=view.render(), view=view)
                await asyncio.sleep(1)
        finally:
            await msg.delete()


class MusicMaster(MusicAgent):

    @commands.command(description='Playlist Editor', aliases=['playlists'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def playlist(self, ctx: commands.Context):
        # list the songs ordered by modified timestamp descending (latest first)
        songs = [file.__str__() for file in sorted(Path(self.get_music_dir()).glob('*.mp3'),
                                                   key=lambda x: x.stat().st_mtime, reverse=True)]
        if not len(songs):
            await ctx.send("No music uploaded on this server. You can just upload mp3 files in here.")
            return
        view = PlaylistEditor(self.bot, songs)
        msg = await ctx.send(embed=view.render(), view=view)
        try:
            await view.wait()
        finally:
            await msg.delete()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages or messages that do not contain music attachments
        if message.author.bot or not message.attachments:
            return
        # only DCS Admin role is allowed to upload missions in the servers admin channel
        if not utils.check_roles([x.strip() for x in self.bot.config['ROLES']['DCS Admin'].split(',')],
                                 message.author):
            return
        try:
            for att in message.attachments:
                if att.filename[-4:] not in ['.mp3', '.ogg']:
                    continue
                filename = self.get_music_dir() + os.path.sep + att.filename
                ctx = utils.ContextWrapper(message)
                if os.path.exists(filename):
                    if await utils.yn_question(ctx, 'File exists. Do you want to overwrite it?') is False:
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
            await message.delete()

    @app_commands.command(description="Add a song to a playlist")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(song=all_songs_autocomplete)
    async def add_song(self, interaction: discord.Interaction, playlist: str, song: str):
        p = Playlist(self.bot, playlist)
        p.add(song)
        title = get_tag(song).title or os.path.basename(song)
        await interaction.response.send_message(
            '{} has been added to playlist {}.'.format(utils.escape_string(title), playlist))

    @app_commands.command(description="Remove a song from a playlist")
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(song=songs_autocomplete)
    async def del_song(self, interaction: discord.Interaction, playlist: str, song: str):
        p = Playlist(self.bot, playlist)
        try:
            p.remove(song)
            title = get_tag(song).title or os.path.basename(song)
            await interaction.response.send_message(
                '{} has been removed from playlist {}.'.format(utils.escape_string(title), playlist))
        except OSError as ex:
            await interaction.response.send_message(ex)


async def setup(bot: DCSServerBot):
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(MusicMaster(bot, MusicEventListener))
    else:
        await bot.add_cog(MusicAgent(bot, MusicEventListener))
