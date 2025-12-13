from core import EventListener, event, Server, get_translation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Profiler

_ = get_translation(__name__.split('.')[1])


class ProfilerListener(EventListener["Profiler"]):

    def __init__(self, plugin: "Profiler") -> None:
        super().__init__(plugin)
        self.max_hung_minutes: dict[str, int] = {}

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
        # profiled servers might take more CPU and respond slower
        self.max_hung_minutes[server.name] = server.instance.locals.get('max_hung_minutes', 3)
        server.instance.locals['max_hung_minutes'] = 99999
        channel = self.bot.get_channel(int(data.get('channel', -1)))
        if channel:
            await channel.send(_("Profiling started."))

    @event(name="onProfilingStop")
    async def onProfilingStop(self, server: Server, data: dict) -> None:
        server.instance.locals['max_hung_minutes'] = self.max_hung_minutes.get(server.name, 3)
        channel = self.bot.get_channel(int(data.get('channel', -1)))
        if channel:
            await channel.send(_("Profiling stopped."))
