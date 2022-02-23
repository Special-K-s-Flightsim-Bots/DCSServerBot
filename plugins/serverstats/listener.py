from core import EventListener, Plugin


class ServerStatsListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.fps = {}

    async def perfmon(self, data):
        self.fps[data['server_name']] = data['fps']
