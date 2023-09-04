from __future__ import annotations
import logging
import os
import sys

from core import ServiceRegistry, Service, Server
from typing import TYPE_CHECKING, Optional
from .sink import Sink

if TYPE_CHECKING:
    from .. import ServiceBus


@ServiceRegistry.register("Music", plugin='music')
class MusicService(Service):

    def __init__(self, node, name: str):
        super().__init__(node, name)
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")
        self.sinks: dict[str, Sink] = dict()
        logging.getLogger(name='eyed3.mp3.headers').setLevel(logging.FATAL)

    async def get_music_dir(self) -> str:
        music_dir = self.get_config()['music_dir']
        if not os.path.exists(music_dir):
            os.makedirs(music_dir)
        return music_dir

    async def start(self):
        await super().start()

    async def stop(self):
        for sink in self.sinks.values():
            await sink.stop()

    async def start_sink(self, server: Server) -> None:
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "start_sink",
                "params": {
                    "server": server.name
                }
            }, node=server.node.name)
            return
        if not self.get_config(server):
            self.log.debug(
                f"No config/services/music.yaml found or no entry for server {server.name} configured.")
            return
        config = self.get_config(server)['sink']
        if not self.sinks.get(server.name):
            sink: Sink = getattr(sys.modules['services.music.sink'], config['type'])(
                service=self, server=server, music_dir=self.get_config(server)['music_dir'])
            self.sinks[server.name] = sink
        if server.get_active_players():
            await self.sinks[server.name].start()

    async def stop_sink(self, server: Server) -> None:
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "stop_sink",
                "params": {
                    "server": server.name
                }
            }, node=server.node.name)
            return
        if self.sinks.get(server.name):
            await self.sinks[server.name].stop()

    async def play_music(self, server: Server, song: str):
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "play_music",
                "params": {
                    "server": server.name,
                    "song": song
                }
            }, node=server.node.name)
            return
        sink: Sink = self.sinks[server.name]
        if sink:
            await sink.play(song)

    async def stop_music(self, server: Server):
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "stop_music",
                "params": {
                    "server": server.name
                }
            }, node=server.node.name)
            return
        sink: Sink = self.sinks[server.name]
        if sink:
            await sink.stop()

    async def skip_music(self, server: Server):
        if server.is_remote:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "skip_music",
                "params": {
                    "server": server.name
                }
            }, node=server.node.name)
            return
        sink: Sink = self.sinks[server.name]
        if sink:
            await sink.skip()

    async def get_current_song(self, server: Server) -> Optional[str]:
        if server.is_remote:
            data = await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Music",
                "method": "get_current_song",
                "params": {
                    "server": server.name
                }
            }, node=server.node.name)
            return data["return"]
        sink: Sink = self.sinks[server.name]
        return sink.current if sink else None
