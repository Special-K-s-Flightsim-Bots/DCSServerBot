import aiohttp
import asyncio
import discord
import os

import psycopg

from core import Plugin, TEventListener, PluginInstallationError, Status, Group, utils, Server, ServiceRegistry, \
    get_translation, DirectoryPicker
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from services.bot import DCSServerBot
from services.music import MusicService
from typing import Type, Optional

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


async def all_songs_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        ret = []
        service = ServiceRegistry.get(MusicService)
        music_dir = await service.get_music_dir()
        for song in await interaction.client.node.list_directory(music_dir, pattern=['*.mp3', '*.ogg'], traverse=True):
            title = get_tag(song).title or os.path.relpath(song, music_dir)
            if current and current.casefold() not in title.casefold():
                continue
            ret.append(app_commands.Choice(name=title[:100], value=os.path.relpath(song, music_dir)))
        return ret[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


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


class Music(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.service = ServiceRegistry.get(MusicService)
        if not self.service:
            raise PluginInstallationError(plugin=self.plugin_name, reason="MusicService not loaded!")
        if not self.service.locals:
            raise PluginInstallationError(plugin=self.plugin_name, reason=r"No config\services\music.yaml found!")

    def get_config(self, server: Optional[Server] = None, *, plugin_name: Optional[str] = None,
                   use_cache: Optional[bool] = True) -> dict:
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        return self.service.get_config(server)

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: Optional[str] = None) -> None:
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
    @app_commands.autocomplete(song=songs_autocomplete)
    async def play(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING,
                                                                                          Status.PAUSED])],
                   radio_name: str, playlist: Optional[str] = None, song: Optional[str] = None):
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
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    async def add_all(self, interaction: discord.Interaction, playlist: str):
        ephemeral = utils.get_ephemeral(interaction)
        if not await utils.yn_question(interaction, _('Do you really want to add ALL songs to the playlist?'),
                                       ephemeral=ephemeral):
            return
        p = await Playlist.create(playlist)
        for song in [file for file in Path(await self.service.get_music_dir()).glob('*.mp3')]:
            await p.add(song.name)
            title = get_tag(song).title or song.name
            await interaction.followup.send(
                _('{title} has been added to playlist {playlist}.').format(title=utils.escape_string(title),
                                                                           playlist=playlist),
                ephemeral=ephemeral)

    @plgroup.command(description=_("Remove a song from a playlist"))
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(playlist=playlist_autocomplete)
    @app_commands.autocomplete(song=songs_autocomplete)
    async def delete(self, interaction: discord.Interaction, playlist: str, song: Optional[str] = None):
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

        async def get_directory(ctx: commands.Context, directory: str) -> Optional[str]:
            # do we have multiple sub-directories to upload to?
            view = DirectoryPicker(self.node, directory)
            embed = await view.render(init=True)
            if embed:
                msg = await ctx.send(embed=embed, view=view)
                try:
                    if await view.wait():
                        await ctx.send(_('Upload aborted.'))
                        return None
                    directory = view.directory
                    if not directory:
                        await ctx.send(_('Upload aborted.'))
                        return None
                finally:
                    await msg.delete()
            return directory

        # ignore bot messages or messages that do not contain music attachments
        if message.author.bot or not message.attachments:
            return
        # only DCS Admin role is allowed to upload music in the servers admin channel
        if not utils.check_roles(self.bot.roles['DCS Admin'], message.author):
            return
        delete = True
        try:
            ctx = await self.bot.get_context(message)
            music_dir = await get_directory(ctx, await self.service.get_music_dir())
            for att in message.attachments:
                if att.filename[-4:] not in ['.mp3', '.ogg']:
                    delete = False
                    return
                if len(att.filename) > 100:
                    ext = att.filename[-4:]
                    filename = os.path.join(music_dir, (att.filename[:-4])[:96] + ext)
                else:
                    filename = os.path.join(music_dir, att.filename)
                if os.path.exists(filename):
                    if not await utils.yn_question(ctx, _('File exists. Do you want to overwrite it?')):
                        continue
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(att.url) as response:
                            if response.status == 200:
                                with open(filename, mode='wb') as outfile:
                                    outfile.write(await response.read())
                                await message.channel.send(_('Song {} uploaded.').format(
                                    utils.escape_string(att.filename)))
                                await self.bot.audit(f'uploaded song "{utils.escape_string(att.filename)}"',
                                                     user=message.author)

                            else:
                                await message.channel.send(_('Error {status} while reading file {file}!').format(
                                        status=response.status, file=utils.escape_string(att.filename)))
                except (aiohttp.ClientError, OSError) as ex:
                    self.log.error(f"Error while uploading file {att.filename} {ex}")
                    await message.channel.send(_('Error while uploading file {file}! See log for details').format(
                        file=utils.escape_string(att.filename)))
        except Exception as ex:
            self.log.exception(ex)
        finally:
            if delete:
                try:
                    await message.delete()
                except discord.NotFound:
                    pass


async def setup(bot: DCSServerBot):
    await bot.add_cog(Music(bot, MusicEventListener))
