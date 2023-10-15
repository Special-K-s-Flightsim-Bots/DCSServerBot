from __future__ import annotations
import logging
import os
import sys

from core import ServiceRegistry, Service, Server
from typing import TYPE_CHECKING, Optional

from .radios import Radio, Mode

if TYPE_CHECKING:
    from .. import ServiceBus

# we don't want any irrelevant log entries from eyed3
logging.getLogger(name='eyed3.mp3.headers').setLevel(logging.FATAL)


@ServiceRegistry.register("Music", plugin='music')
class MusicService(Service):

    def __init__(self, node, name: str):
        super().__init__(node, name)
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")
        self.radios: dict[str, dict[str, Radio]] = dict()

    def get_config(self, server: Optional[Server] = None, radio_name: Optional[str] = None) -> dict:
        if not radio_name:
            return super().get_config(server)
        else:
            return super().get_config(server)['radios'][radio_name]

    async def get_music_dir(self) -> str:
        music_dir = self.get_config()['music_dir']
        if not os.path.exists(music_dir):
            os.makedirs(music_dir)
        return music_dir

    async def stop(self):
        for server_name in self.radios.keys():
            for radio in self.radios[server_name].values():
                await radio.stop()
        await super().stop()

    async def start_radios(self, server: Server, radio_name: Optional[str] = None) -> None:
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "start_radios",
                "params": {
                    "server": server.name,
                    "radio_name": radio_name or ""
                }
            }, node=server.node.name)
            return
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
            if server.get_active_players():
                await self.radios[server.name][name].start()

    async def stop_radios(self, server: Server, radio_name: Optional[str] = None) -> None:
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "stop_radios",
                "params": {
                    "server": server.name,
                    "radio_name": radio_name or ""
                }
            }, node=server.node.name)
            return
        if server.name in self.radios:
            for name, radio in self.radios[server.name].items():
                if radio_name and name != radio_name:
                    continue
                await radio.stop()

    async def play_song(self, server: Server, radio_name: str, song: str) -> None:
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "play_song",
                "params": {
                    "server": server.name,
                    "radio_name": radio_name,
                    "song": song
                }
            }, node=server.node.name)
            return
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            await radio.play(song)

    async def skip_song(self, server: Server, radio_name: str) -> None:
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "skip_song",
                "params": {
                    "server": server.name,
                    "radio_name": radio_name
                }
            }, node=server.node.name)
            return
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            await radio.skip()

    async def get_current_song(self, server: Server, radio_name: str) -> Optional[str]:
        if server.is_remote:
            data = await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "get_current_song",
                "params": {
                    "server": server.name,
                    "radio_name": radio_name
                }
            }, node=server.node.name)
            return data["return"]
        radio = self.radios.get(server.name, {}).get(radio_name)
        return radio.current if radio else None

    async def set_playlist(self, server: Server, radio_name: str, playlist: str):
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "set_playlist",
                "params": {
                    "server": server.name,
                    "radio_name": radio_name,
                    "playlist": playlist
                }
            }, node=server.node.name)
            return
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            radio.playlist = playlist

    async def get_songs(self, server: Server, radio_name: str) -> list[str]:
        if server.is_remote:
            data = await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "get_songs",
                "params": {
                    "server": server.name,
                    "radio_name": radio_name
                }
            }, node=server.node.name)
            return data["return"]
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            return radio.songs

    async def get_mode(self, server: Server, radio_name: str) -> Mode:
        if server.is_remote:
            data = await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "get_mode",
                "params": {
                    "server": server.name,
                    "radio_name": radio_name
                }
            }, node=server.node.name)
            return data["return"]
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            return radio.mode

    async def set_mode(self, server: Server, radio_name: str, mode: Mode) -> None:
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "set_mode",
                "params": {
                    "server": server.name,
                    "radio_name": radio_name,
                    "mode": mode.value
                }
            }, node=server.node.name)
            return
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            radio.mode = mode

    async def set_config(self, server: Server, radio_name: str, config: dict) -> None:
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "set_config",
                "params": {
                    "server": server.name,
                    "radio_name": radio_name,
                    "config": config
                }
            }, node=server.node.name)
            return
        radio = self.radios.get(server.name, {}).get(radio_name)
        if radio:
            radio.config = config
