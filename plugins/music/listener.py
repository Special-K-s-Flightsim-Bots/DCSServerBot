import sys
from core import EventListener, Server, event
from .sink import Sink


class MusicEventListener(EventListener):

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if not self.plugin.sinks.get(server.name):
            if not self.plugin.get_config(server):
                self.log.warning(f"No config\\music.json found or no entry for server {server.name} configured.")
                return
            config = self.plugin.get_config(server)['sink']
            sink: Sink = getattr(sys.modules['plugins.music.sink'], config['type'])(bot=self.bot, server=server,
                                                                                    config=config)
            self.plugin.sinks[server.name] = sink
        if server.get_active_players():
            await self.plugin.sinks[server.name].start()

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if len(server.get_active_players()) == 1:
            await self.plugin.sinks[server.name].start()

    @event(name="onPlayerStop")
    async def onPlayerStop(self, server: Server, data: dict) -> None:
        if not server.get_active_players():
            await self.plugin.sinks[server.name].stop()

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'disconnect':
            if not server.get_active_players():
                await self.plugin.sinks[server.name].stop()
