from core.services.base import Service
from core.services.registry import ServiceRegistry


@ServiceRegistry.register("Monitoring")
class MonitoringService(Service):

    async def start(self):
        await super().start()
        self.log.info("Monitoring started.")

    async def stop(self):
        await super().stop()
        self.log.info("Monitoring stopped.")
