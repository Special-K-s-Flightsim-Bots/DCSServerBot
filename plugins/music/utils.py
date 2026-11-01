from __future__ import annotations

from core import ServiceRegistry
from services.music.tags import get_tag

__all__ = ["get_tag", "Playlist"]


class Playlist:

    def __init__(self):
        from services.music import MusicService

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

    async def add(self, item: str) -> bool:
        """Add *item* unless the playlist already holds it; ``True`` when a row was written.

        The table's ``(name, song_file)`` constraint is what decides: ``ON CONFLICT DO NOTHING``
        makes a second add of one file a no-op the caller can report in words, not a driver error.
        """
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                INSERT INTO music_playlists (name, song_id, song_file) 
                VALUES (%s, nextval('music_song_id_seq'), %s)
                ON CONFLICT (name, song_file) DO NOTHING
            """, (self.playlist, item))
            if not cursor.rowcount:
                return False
            self._items.append(item)
        return True

    async def remove(self, item: str) -> None:
        async with self.apool.connection() as conn:
            await conn.execute('DELETE FROM music_playlists WHERE name = %s AND song_file = %s',
                               (self.playlist, item))
            self._items.remove(item)
            # if no item remains, make sure any server mapping to this list is deleted, too
            if not self._items:
                await conn.execute('DELETE FROM music_radios WHERE playlist_name = %s', (self.playlist, ))

    async def clear(self) -> None:
        async with self.apool.connection() as conn:
            await conn.execute('DELETE FROM music_playlists WHERE name = %s ', (self.playlist,))
            await conn.execute('DELETE FROM music_radios WHERE playlist_name = %s', (self.playlist,))
            self._items.clear()
