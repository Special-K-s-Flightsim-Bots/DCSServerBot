import asyncio
import discord
import os
import psycopg

from core import (Plugin, PluginInstallationError, Status, Group, utils, Server, ServiceRegistry, get_translation,
                  NodeUploadHandler, Channel)
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from services.bot import DCSServerBot
from services.music import MusicService
from typing import Type

from .listener import MusicEventListener
from .utils import get_tag, Playlist
from .views import MusicPlayer

_ = get_translation(__name__.split('.')[1])

async def get_all_playlists(interaction: discord.Interaction) -> list[str]:
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute('SELECT DISTINCT name FROM music_playlists ORDER BY 1')
        return [x[0] async for x in cursor]


async def playlist_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        playlists = await get_all_playlists(interaction)
        return [
            app_commands.Choice(name=playlist, value=playlist)
            for playlist in playlists if not current or current.casefold() in playlist.casefold()
        ]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def all_songs_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        ret = []
        service = ServiceRegistry.get(MusicService)
        music_dir = await service.get_music_dir()
        _, file_list = await interaction.client.node.list_directory(music_dir, pattern=['*.mp3', '*.ogg'], traverse=True)
        for song in file_list:
            if os.path.isdir(song):
                continue
            song_path = os.path.relpath(song, music_dir)
            title = os.path.join(os.path.dirname(song_path), get_tag(song).title or os.path.basename(song))
            if current and current.casefold() not in title.casefold():
                continue
            ret.append(app_commands.Choice(name=title[:100], value=song_path))
        return ret[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def songs_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        service = ServiceRegistry.get(MusicService)
        music_dir = await service.get_music_dir()
        playlist = await Playlist.create(utils.get_interaction_param(interaction, 'playlist'))
        ret = []
        for song in playlist.items:
            title = get_tag(os.path.join(music_dir, song)).title or song
            if current and current.casefold() not in title.casefold():
                continue
            ret.append(app_commands.Choice(name=title[:100], value=song))
        return ret[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def radios_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(
            interaction, utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        service = ServiceRegistry.get(MusicService)
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x, value=x) for x in service.get_config(server)['radios'].keys()
            if not current or current.casefold() in x.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def subfolder_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        service = ServiceRegistry.get(MusicService)
        music_dir = await service.get_music_dir()
        _, file_list = await interaction.client.node.list_directory(music_dir, is_dir=True, traverse=True)
        ret: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=os.path.relpath(folder, music_dir), value=folder)
            for folder in file_list
        ]
        return ret[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


class Music(Plugin[MusicEventListener]):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[MusicEventListener] = None):
        super().__init__(bot, eventlistener)
        self.service = ServiceRegistry.get(MusicService)
        if not self.service:
            raise PluginInstallationError(plugin=self.plugin_name, reason="MusicService not loaded!")
        if not self.service.locals:
            raise PluginInstallationError(plugin=self.plugin_name, reason=r"No config\services\music.yaml found!")

    def get_config(self, server: Server | None = None, *, plugin_name: str | None = None,
                   use_cache: bool | None = True) -> dict:
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        return self.service.get_config(server)

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: str | None = None) -> None:
        self.log.debug('Pruning Music ...')
        if server:
            await conn.execute("DELETE FROM music_radios WHERE server_name = %s", (server, ))
        self.log.debug('Music pruned.')

    # New command group "/music"
    music = Group(name="music", description=_("Commands to manage music in your (DCS) server"))

    @music.command(description=_('Music Player'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(_server="server")
    @app_commands.autocomplete(radio_name=radios_autocomplete)
    async def player(self, interaction: discord.Interaction,
                     _server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING,
                                                                                             Status.PAUSED])],
                     radio_name: str):
        playlists = await get_all_playlists(interaction)
        if not playlists:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("You don't have any playlists to play. Please create one with {}.").format(
                    (await utils.get_command(self.bot, group='playlist', name='add')).mention
                ), ephemeral=True)
            return
        view = MusicPlayer(server=_server, radio_name=radio_name, playlists=playlists)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=await view.render(), view=view,
                                                ephemeral=utils.get_ephemeral(interaction))
        msg = await interaction.original_response()
        try:
            while not view.is_finished():
                await msg.edit(embed=await view.render(), view=view)
                await asyncio.sleep(5)
        finally:
            try:
                await msg.delete()
            except discord.NotFound:
                pass

    @music.command(description=_("Play a song or a playlist\n"))
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(radio_name=radios_autocomplete)
    @app_commands.autocomplete(song=all_songs_autocomplete)
    async def play(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.RUNNING, Status.PAUSED])],
                   radio_name: str, playlist: str | None = None, song: str | None = None):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Server {} is not running.').format(server.name), ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        if song:
            song = os.path.join(await self.service.get_music_dir(), song)
            title = get_tag(song).title or os.path.basename(song)
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Now playing {} ...").format(title), ephemeral=ephemeral)
            await self.service.play_song(server, radio_name, song)
        else:
            if playlist:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("Now playing {} ...").format(playlist), ephemeral=ephemeral)
                await self.service.stop_radios(server, radio_name)
                await self.service.set_playlist(server, radio_name, playlist)
            else:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("Radio {} started.").format(radio_name), ephemeral=ephemeral)
            await self.service.start_radios(server, radio_name)

    @music.command(description=_("Stop playing"))
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(radio_name=radios_autocomplete)
    async def stop(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING,
                                                                                          Status.PAUSED])],
                   radio_name: str):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Server {} is not running.').format(server.name), ephemeral=True)
            return
        await self.service.stop_radios(server, radio_name)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Radio {} stopped.").format(radio_name),
                                                ephemeral=utils.get_ephemeral(interaction))

    # New command group "/playlist"
    plgroup = Group(name="playlist", description=_("Commands to manage music playlists"))

    @plgroup.command(description=_("Add a song to a playlist"))
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(song=all_songs_autocomplete)
    async def add(self, interaction: discord.Interaction, playlist: str, song: str):
        p = await Playlist.create(playlist)
        await p.add(song)
        song = os.path.join(await self.service.get_music_dir(), song)
        title = get_tag(song).title or os.path.basename(song)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(
            _('{title} has been added to playlist {playlist}.').format(title=utils.escape_string(title),
                                                                       playlist=playlist),
            ephemeral=utils.get_ephemeral(interaction))

    @plgroup.command(description=_("Add all available songs to a playlist"))
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(subfolder='Add all songs within a subfolder of the main music directory.')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(subfolder=subfolder_autocomplete)
    async def add_all(self, interaction: discord.Interaction, playlist: str, subfolder: str | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        music_dir = await self.service.get_music_dir()
        if subfolder:
            message = _('Do you really want to add ALL songs from the `{}` folder to the playlist?').format(
                os.path.relpath(subfolder, music_dir))
            final_path = Path(await self.service.get_music_dir()) / subfolder
        else:
            message = _('Do you really want to add ALL songs to the playlist?')
            final_path = Path(await self.service.get_music_dir())

        if not await utils.yn_question(interaction, message, ephemeral=ephemeral):
            return

        p = await Playlist.create(playlist)
        
        x, file_list = await interaction.client.node.list_directory(final_path, pattern=['*.mp3', '*.ogg'])
        for song in file_list:
            await p.add(os.path.relpath(song, music_dir))
            title = get_tag(song).title or os.path.basename(song)
            await interaction.followup.send(
                _('{title} has been added to playlist {playlist}.').format(title=utils.escape_string(title),
                                                                           playlist=playlist),
                ephemeral=ephemeral)

        await interaction.followup.send(_('Playlist {} updated.').format(playlist), ephemeral=ephemeral)

    @plgroup.command(description=_("Remove a song from a playlist"))
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(song=songs_autocomplete)
    async def delete(self, interaction: discord.Interaction, playlist: str, song: str | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        p = await Playlist.create(playlist)
        try:
            if song:
                await p.remove(song)
                song = os.path.join(await self.service.get_music_dir(), song)
                title = get_tag(song).title or os.path.basename(song)
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(
                    _('{title} has been removed from playlist {playlist}.').format(title=utils.escape_string(title),
                                                                                   playlist=playlist),
                    ephemeral=ephemeral)
            else:
                await p.clear()
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_('Playlist {} deleted.').format(playlist), ephemeral=ephemeral)
        except OSError as ex:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(ex, ephemeral=ephemeral)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        pattern =  ['.mp3', '.ogg']
        if not NodeUploadHandler.is_valid(message, pattern, self.bot.roles['DCS Admin']):
            return
        admin_channels = []
        if self.bot.locals.get('channels', {}).get('admin'):
            admin_channels.append(self.bot.locals.get('channels', {}).get('admin'))
        else:
            for server in self.bot.servers.values():
                admin_channels.append(server.channels[Channel.ADMIN])
        if message.channel.id not in admin_channels:
            return
        try:
            handler = NodeUploadHandler(self.node, message, pattern)
            base_dir = await self.service.get_music_dir()
            await handler.upload(base_dir)
        except Exception as ex:
            self.log.exception(ex)
        finally:
            await message.delete()


async def setup(bot: DCSServerBot):
    await bot.add_cog(Music(bot, MusicEventListener))
