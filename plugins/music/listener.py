from core import EventListener, Server, event, ServiceRegistry, Plugin
from typing import cast

from services.music.service import MusicService


class MusicEventListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.service: MusicService = cast(MusicService, ServiceRegistry.get("Music"))

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        try:
            await self.service.init_radios(server=server)
        except asyncio.TimeoutError:
            return
        # if we've just started, we need to start the radios
        if data['channel'].startswith('sync-'):
            await self.service.start_radios(server=server)

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if len(server.get_active_players()) == 1:
            await self.service.start_radios(server=server)

    @event(name="onPlayerStop")
    async def onPlayerStop(self, server: Server, data: dict) -> None:
        if not server.get_active_players():
            await self.service.stop_radios(server=server)

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'disconnect':
            if not server.get_active_players():
                await self.service.stop_radios(server=server)
