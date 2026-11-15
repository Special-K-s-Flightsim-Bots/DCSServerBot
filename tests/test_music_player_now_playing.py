"""The Discord player's "Now Playing" reads the TRACK FILE's tags, never its own display title.

The radio's ``current`` is the title it shows ("Paint It, Black"); the file it plays is
``current_file`` ("jungle/b.mp3"). Passing the former to the tag reader opens a file named after the
song, logs "the tags of Paint It, Black could not be read", and then renders the title's first
characters minus four as a fallback — so the card shows a truncated title and the log carries a
warning on every track.
"""
import os

import discord
import pytest

from plugins.music import views as music_views
from plugins.music.views import MusicPlayer
from services.music.radios import Mode

TITLE = "Paint It, Black"
FILE = "jungle/b.mp3"


class _Tag:
    title = TITLE
    artist = "The Rolling Stones"
    album = "Aftermath"


class _Service:
    log = None

    def __init__(self, music_dir):
        self.music_dir = music_dir
        self.read = []

    async def get_music_dir(self):
        return self.music_dir

    async def get_songs(self, server, radio_name):
        return [FILE]

    async def get_current_song(self, server, radio_name):
        return TITLE

    async def get_current_file(self, server, radio_name):
        return FILE

    async def get_mode(self, server, radio_name):
        return Mode.REPEAT

    def get_config(self, server, radio_name):
        return {"frequency": "30.0", "modulation": "FM", "coalition": 2,
                "volume": "0.5", "display_name": "Jungle Beats", "mode": 1}


def _player(service):
    player = object.__new__(MusicPlayer)
    discord.ui.View.__init__(player)
    player.radio_name = "Radio 1"
    player.service = service
    player.server = object()
    player.playlists = []
    player.songs = player.titles = None
    player.config = None
    return player


@pytest.mark.asyncio
async def test_now_playing_reads_the_track_file_and_not_the_display_title(tmp_path):
    service = _Service(str(tmp_path))
    player = _player(service)

    def fake_tag(path):
        service.read.append(path)
        return _Tag()

    original = music_views.get_tag
    music_views.get_tag = fake_tag
    try:
        embed = await player.render()
    finally:
        music_views.get_tag = original

    assert service.read, "the card reads tags"
    assert set(service.read) == {os.path.join(str(tmp_path), FILE)}, (
        "the tag reader is given the track's path, never the display title")
    assert TITLE not in service.read
    values = [field.value for field in embed.fields]
    assert TITLE in values, "the card shows the tag's title"
