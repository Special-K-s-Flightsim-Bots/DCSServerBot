from core import EventListener, event, Server, get_translation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Profiler

_ = get_translation(__name__.split('.')[1])


class ProfilerListener(EventListener["Profiler"]):

    async def _load_profiler(self, server: Server) -> bool:
        config = self.get_config(server)
        profiler = config.get('profiler', 'chrome').lower()
        if config.get('attach_on_launch'):
            await server.send_to_dcs({
                'command': 'loadProfiler',
                'profiler': profiler
            })
            self.plugin.profilers[server.name] = profiler
            return True
        return False

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if data['channel'].startswith('sync-'):
            await self._load_profiler(server)

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, _: dict) -> None:
        await self._load_profiler(server)

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, _: dict) -> None:
        config = self.get_config(server)
        if config.get('attach_on_launch'):
            await server.send_to_dcs({
                'command': 'startProfiling',
                'verbose': config.get('verbose', False)
            })

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, _: dict) -> None:
        await server.send_to_dcs({
            'command': 'stopProfiling'
        })

    @event(name="onProfilingStart")
    async def onProfilingStart(self, server: Server, data: dict) -> None:
        channel = self.bot.get_channel(int(data.get('channel', -1)))
        if channel:
            await channel.send(_("Profiling started."))

    @event(name="onProfilingStop")
    async def onProfilingStop(self, server: Server, data: dict) -> None:
        channel = self.bot.get_channel(int(data.get('channel', -1)))
        if channel:
            await channel.send(_("Profiling stopped."))
