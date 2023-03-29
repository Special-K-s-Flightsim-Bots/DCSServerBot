from core import EventListener, Plugin, event, Server


class ServerStatsListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.fps = {}

    @event(name="perfmon")
    async def perfmon(self, server: Server, data: dict):
        self.fps[server.name] = data['fps']
