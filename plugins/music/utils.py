from __future__ import annotations

import discord
import eyed3
import os

from core import utils, ServiceRegistry, Server
from discord import app_commands
from eyed3.id3 import Tag
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def get_tag(file) -> Tag:
    audio = eyed3.load(file)
    if not audio or not audio.tag:
        return Tag()
    return audio.tag


class Playlist:

    def __init__(self):
        from services import MusicService

        self.service = ServiceRegistry.get(MusicService)
        self.log = self.service.log
        self.apool = self.service.apool
        self.playlist = None
        self._items = []

    @classmethod
    async def create(cls, playlist: str):
        self = Playlist()
        self.playlist = playlist
        async with self.apool.connection() as conn:
            cursor = await conn.execute('SELECT song_file FROM music_playlists WHERE name = %s ORDER BY song_id',
                                        (playlist, ))
            self._items = [row[0] async for row in cursor]
        return self

    @property
    def name(self) -> str:
        return self.playlist

    @property
    def items(self) -> list[str]:
        return self._items

    def empty(self) -> bool:
        return len(self._items) == 0

    def size(self) -> int:
        return len(self._items)

    async def add(self, item: str) -> None:
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO music_playlists (name, song_id, song_file) 
                    VALUES (%s, nextval('music_song_id_seq'), %s)
                """, (self.playlist, item))
                self._items.append(item)

    async def remove(self, item: str) -> None:
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute('DELETE FROM music_playlists WHERE name = %s AND song_file = %s',
                                   (self.playlist, item))
                self._items.remove(item)
                # if no item remains, make sure any server mapping to this list is deleted, too
                if not self._items:
                    await conn.execute('DELETE FROM music_radios WHERE playlist_name = %s', (self.playlist, ))

    async def clear(self) -> None:
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute('DELETE FROM music_playlists WHERE name = %s ', (self.playlist,))
                await conn.execute('DELETE FROM music_radios WHERE playlist_name = %s', (self.playlist,))
                self._items.clear()


async def get_all_playlists(interaction: discord.Interaction) -> list[str]:
    async with interaction.client.apool.connection() as conn:
        cursor = await conn.execute('SELECT DISTINCT name FROM music_playlists ORDER BY 1')
        return [x[0] async for x in cursor]


async def playlist_autocomplete(
        interaction: discord.Interaction,
        current: str,
) -> list[app_commands.Choice[str]]:
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


async def all_songs_autocomplete(
        interaction: discord.Interaction,
        current: str,
) -> list[app_commands.Choice[str]]:
    from services import MusicService

    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        ret = []
        service = ServiceRegistry.get(MusicService)
        music_dir = await service.get_music_dir()
        for song in [
            file.name for file in sorted(Path(music_dir).glob('*.mp3'), key=lambda x: x.stat().st_mtime, reverse=True)
        ]:
            title = get_tag(os.path.join(music_dir, song)).title or song
            if current and current.casefold() not in title.casefold():
                continue
            ret.append(app_commands.Choice(name=title[:100], value=song))
        return ret[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def songs_autocomplete(
        interaction: discord.Interaction,
        current: str,
) -> list[app_commands.Choice[str]]:
    from services import MusicService

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
    from services import MusicService

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
