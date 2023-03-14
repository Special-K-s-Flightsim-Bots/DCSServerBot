import sys
from core import EventListener, Server
from .sink import Sink


class MusicEventListener(EventListener):
    async def registerDCSServer(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        if not self.plugin.sinks.get(server.name, None):
            config = self.plugin.get_config(server)['sink']
            sink: Sink = getattr(sys.modules['plugins.music.sink'], config['type'])(bot=self.bot, server=server,
                                                                                    config=config)
            self.plugin.sinks[server.name] = sink
