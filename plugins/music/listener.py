import sys
from core import EventListener, Server
from .sink import Sink


class MusicEventListener(EventListener):
    async def registerDCSServer(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        if not self.plugin.sinks.get(server.name, None):
            if not self.plugin.get_config(server):
                self.log.warning(f"No config\\music.json found or no entry for server {server.name} configured.")
                return
            config = self.plugin.get_config(server)['sink']
            sink: Sink = getattr(sys.modules['plugins.music.sink'], config['type'])(bot=self.bot, server=server,
                                                                                    config=config)
            self.plugin.sinks[server.name] = sink
        if server.get_active_players():
            await self.plugin.sinks[server.name].start()

    async def onPlayerStart(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        if len(server.get_active_players()) == 1:
            await self.plugin.sinks[server.name].start()

    async def onPlayerStop(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        if not server.get_active_players():
            await self.plugin.sinks[server.name].stop()

    async def onGameEvent(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        if data['eventName'] == 'disconnect':
            if not server.get_active_players():
                await self.plugin.sinks[server.name].stop()
