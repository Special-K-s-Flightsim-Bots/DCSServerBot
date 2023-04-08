import discord
import eyed3
import os
import psycopg2
from contextlib import closing
from core import DCSServerBot
from discord import app_commands
from eyed3.id3 import Tag
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def get_tag(file) -> Tag:
    return eyed3.load(file).tag


class Playlist:

    def __init__(self, bot: DCSServerBot, playlist: str):
        self.log = bot.log
        self.pool = bot.pool
        self.playlist = playlist
        # initialize the playlist if there is one stored in the database
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT song_file FROM music_playlists WHERE name = %s ORDER BY song_id',
                               (self.playlist,))
                self._items = [row[0] for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

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
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("INSERT INTO music_playlists (name, song_id, song_file) "
                               "VALUES (%s, nextval('music_song_id_seq'), %s)",
                               (self.playlist, item))
            conn.commit()
            self._items.append(item)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    def remove(self, item: str) -> None:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('DELETE FROM music_playlists WHERE name = %s AND song_file = %s', (self.playlist, item))
            conn.commit()
            self._items.remove(item)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    def clear(self) -> None:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('DELETE FROM music_playlists WHERE name = %s ', (self.playlist,))
            conn.commit()
            self._items.clear()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)


def get_all_playlists(bot: DCSServerBot) -> list[str]:
    conn = bot.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            cursor.execute('SELECT DISTINCT name FROM music_playlists')
            return [x[0] for x in cursor.fetchall()]
    except (Exception, psycopg2.DatabaseError) as error:
        bot.log.exception(error)
    finally:
        bot.pool.putconn(conn)


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
    music_dir = interaction.client.cogs['MusicMasterOnly'].get_music_dir()
    for song in [
        file.name for file in sorted
        (Path(interaction.command.binding.get_music_dir()).glob('*.mp3'),
         key=lambda x: x.stat().st_mtime, reverse=True)]:
        title = get_tag(os.path.join(music_dir, song)).title or song
        if current and current.casefold() not in title.casefold():
            continue
        ret.append(app_commands.Choice(name=title[:100], value=song))
    return ret[:25]


async def songs_autocomplete(
        interaction: discord.Interaction,
        current: str,
) -> list[app_commands.Choice[str]]:
    music_dir = interaction.client.cogs['MusicMasterOnly'].get_music_dir()
    playlist = Playlist(interaction.client, interaction.data['options'][0]['value'])
    ret = []
    for song in playlist.items:
        title = get_tag(os.path.join(music_dir, song)).title or song
        if current and current.casefold() not in title.casefold():
            continue
        ret.append(app_commands.Choice(name=title[:100], value=song))
    return ret
