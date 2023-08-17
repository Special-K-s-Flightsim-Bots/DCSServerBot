from __future__ import annotations
import logging
import sys

from core import ServiceRegistry, Service, Server
from typing import TYPE_CHECKING
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

    async def start(self):
        await super().start()

    async def stop(self):
        for sink in self.sinks.values():
            await sink.stop()

    async def start_sink(self, server: Server):
        if server.is_remote:
            self.bus.send_to_node({
                "command": "rpc",
                "service": "Music",
                "method": "start_sink",
                "params": {
                    "server": server.name
                }
            }, node=server.node)
            return
        if not self.sinks.get(server.name):
            if not self.get_config(server):
                self.log.debug(f"No config\\services\\music.yaml found or no entry for server {server.name} configured.")
                return
            config = self.get_config(server)['sink']
            sink: Sink = getattr(sys.modules['services.music.sink'], config['type'])(
                node=self.node, server=server, config=config, music_dir=self.get_config(server)['music_dir'])
            self.sinks[server.name] = sink
        if server.get_active_players():
            await self.sinks[server.name].start()

    async def stop_sink(self, server: Server):
        if server.is_remote:
            self.bus.send_to_node({
                "command": "rpc",
                "service": "Music",
                "method": "stop_sink",
                "params": {
                    "server": server.name
                }
            }, node=server.node)
            return
        if self.sinks.get(server.name):
            await self.sinks[server.name].stop()
