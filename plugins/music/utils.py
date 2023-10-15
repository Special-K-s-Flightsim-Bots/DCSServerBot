from __future__ import annotations

import discord
import eyed3
import os

from core import utils, ServiceRegistry, Server
from discord import app_commands
from eyed3.id3 import Tag
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services import MusicService


@lru_cache(maxsize=None)
def get_tag(file) -> Tag:
    audio = eyed3.load(file)
    return audio.tag if audio else Tag()


class Playlist:

    def __init__(self, playlist: str):
        self.service = ServiceRegistry.get("Music")
        self.log = self.service.log
        self.pool = self.service.pool
        self.playlist = playlist
        # initialize the playlist if there is one stored in the database
        with self.pool.connection() as conn:
            self._items = [
                row[0] for row in conn.execute(
                    'SELECT song_file FROM music_playlists WHERE name = %s ORDER BY song_id',
                    (self.playlist,)).fetchall()
            ]

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

    def add(self, item: str) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("INSERT INTO music_playlists (name, song_id, song_file) "
                             "VALUES (%s, nextval('music_song_id_seq'), %s)",
                             (self.playlist, item))
                self._items.append(item)

    def remove(self, item: str) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('DELETE FROM music_playlists WHERE name = %s AND song_file = %s', (self.playlist, item))
                self._items.remove(item)
                # if no item remains, make sure any server mapping to this list is deleted, too
                if not self._items:
                    conn.execute('DELETE FROM music_servers WHERE playlist_name = %s', (self.playlist, ))

    def clear(self) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('DELETE FROM music_playlists WHERE name = %s ', (self.playlist,))
                conn.execute('DELETE FROM music_servers WHERE playlist_name = %s', (self.playlist,))
                self._items.clear()


def get_all_playlists(interaction: discord.Interaction) -> list[str]:
    with interaction.client.pool.connection() as conn:
        return [x[0] for x in conn.execute('SELECT DISTINCT name FROM music_playlists ORDER BY 1').fetchall()]


async def playlist_autocomplete(
        interaction: discord.Interaction,
        current: str,
) -> list[app_commands.Choice[str]]:
    playlists = get_all_playlists(interaction.client)
    return [
        app_commands.Choice(name=playlist, value=playlist)
        for playlist in playlists if not current or current.casefold() in playlist.casefold()
    ]


async def all_songs_autocomplete(
        interaction: discord.Interaction,
        current: str,
) -> list[app_commands.Choice[str]]:
    ret = []
    service: MusicService = ServiceRegistry.get("Music")
    music_dir = await service.get_music_dir()
    for song in [
        file.name for file in sorted(Path(music_dir).glob('*.mp3'), key=lambda x: x.stat().st_mtime, reverse=True)]:
        title = get_tag(os.path.join(music_dir, song)).title or song
        if current and current.casefold() not in title.casefold():
            continue
        ret.append(app_commands.Choice(name=title[:100], value=song))
    return ret[:25]


async def songs_autocomplete(
        interaction: discord.Interaction,
        current: str,
) -> list[app_commands.Choice[str]]:
    service: MusicService = ServiceRegistry.get("Music")
    music_dir = await service.get_music_dir()
    playlist = Playlist(utils.get_interaction_param(interaction, 'playlist'))
    ret = []
    for song in playlist.items:
        title = get_tag(os.path.join(music_dir, song)).title or song
        if current and current.casefold() not in title.casefold():
            continue
        ret.append(app_commands.Choice(name=title[:100], value=song))
    return ret[:25]


async def radios_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        server: Server = await utils.ServerTransformer().transform(
            interaction, utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        service: MusicService = ServiceRegistry.get("Music")
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x, value=x) for x in service.get_config(server)['radios'].keys()
            if not current or current.casefold() in x.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
