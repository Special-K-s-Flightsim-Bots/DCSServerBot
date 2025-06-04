from __future__ import annotations
import logging
import os
import sys

from core import ServiceRegistry, Service, Server, proxy
from typing import Optional

from .radios import Radio, Mode
from ..servicebus import ServiceBus

# we don't want any irrelevant log entries from eyed3
logging.getLogger(name='eyed3.mp3.headers').setLevel(logging.FATAL)


@ServiceRegistry.register(plugin='music', depends_on=[ServiceBus])
class MusicService(Service):

    def __init__(self, node):
        super().__init__(node=node, name="Music")
        self.bus = ServiceRegistry.get(ServiceBus)
        self.radios: dict[str, dict[str, Radio]] = dict()

    def get_config(self, server: Optional[Server] = None, radio_name: Optional[str] = None) -> dict:
        if not radio_name:
            return super().get_config(server)
        else:
            return super().get_config(server)['radios'][radio_name]

    @property
    def music_dir(self):
        music_dir = os.path.expandvars(self.get_config()['music_dir'])
        if not os.path.exists(music_dir):
            os.makedirs(music_dir)
        return music_dir

    async def get_music_dir(self) -> str:
        return self.music_dir

    async def stop(self):
        for server_name in self.radios.keys():
            for radio in self.radios[server_name].values():
                await radio.stop()
        await super().stop()

    @proxy
    async def init_radios(self, server: Server, radio_name: Optional[str] = None) -> None:
        if not self.get_config(server):
            self.log.debug(
                f"No config/services/music.yaml found or no entry for server {server.name} configured.")
            return
        for name, config in self.get_config(server)['radios'].items():
            if radio_name and name != radio_name:
                continue
            if not self.radios.get(server.name):
                self.radios[server.name] = {}
            if not self.radios[server.name].get(name):
                radio: Radio = getattr(sys.modules['services.music.radios'], config['type'])(name=name, server=server)
                self.radios[server.name][name] = radio

    @proxy
    async def start_radios(self, server: Server, radio_name: Optional[str] = None) -> None:
        if server.name in self.radios:
            for name, radio in self.radios[server.name].items():
                if radio_name and name != radio_name:
                    continue
                if not radio.is_running():
                    await radio.start()

    @proxy
    async def stop_radios(self, server: Server, radio_name: Optional[str] = None) -> None:
        if server.name in self.radios:
            for name, radio in self.radios[server.name].items():
                if radio_name and name != radio_name:
                    continue
                await radio.stop()

    @proxy
    async def play_song(self, server: Server, radio_name: str, song: str) -> None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            await radio.play(song)

    @proxy
    async def skip_song(self, server: Server, radio_name: str) -> None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            await radio.skip()

    @proxy
    async def get_current_song(self, server: Server, radio_name: str) -> Optional[str]:
        radio = self.radios.get(server.name, {}).get(radio_name)
        return radio.current if radio else None

    @proxy
    async def set_playlist(self, server: Server, radio_name: str, playlist: str):
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            radio.playlist = playlist

    @proxy
    async def get_songs(self, server: Server, radio_name: str) -> list[str]:
        radio = self.radios.get(server.name, {}).get(radio_name)
        return radio.songs if radio else []

    @proxy
    async def get_mode(self, server: Server, radio_name: str) -> Optional[Mode]:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            return radio.mode
        return None

    @proxy
    async def set_mode(self, server: Server, radio_name: str, mode: Mode) -> None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            radio.mode = mode
        return None

    @proxy
    async def set_config(self, server: Server, radio_name: str, config: dict) -> None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            radio.config = config
        return None

    @proxy
    async def reset_playlist(self, server: Server, radio_name: str) -> None:
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            radio.reset()
