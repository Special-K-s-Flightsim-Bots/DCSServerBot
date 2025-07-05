import asyncio

from core import EventListener, Server, event, ServiceRegistry
from services.music.service import MusicService
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Music


class MusicEventListener(EventListener["Music"]):

    def __init__(self, plugin: "Music"):
        super().__init__(plugin)
        self.service = ServiceRegistry.get(MusicService)

    async def _init_radios(self, server: Server, data: dict) -> None:
        try:
            await self.service.init_radios(server)
        except (TimeoutError, asyncio.TimeoutError):
            return
        # if we've just started, we need to start the radios
        if data['channel'].startswith('sync-') and data.get('players'):
            await self.service.start_radios(server)

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        asyncio.create_task(self._init_radios(server, data))

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, _: dict) -> None:
        if len(server.get_active_players()) == 1:
            asyncio.create_task(self.service.start_radios(server))

    @event(name="onPlayerStop")
    async def onPlayerStop(self, server: Server, _: dict) -> None:
        if self.plugin.get_config().get('pause_without_players', True) and not server.get_active_players():
            asyncio.create_task(self.service.stop_radios(server))

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'disconnect':
            if self.plugin.get_config().get('pause_without_players', True) and not server.get_active_players():
                asyncio.create_task(self.service.stop_radios(server))
