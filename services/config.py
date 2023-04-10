from core import ServiceRegistry, Service
from typing import Any


@ServiceRegistry.register("Configuration")
class ConfigService(Service):

    async def start(self):
        await super().start()
        self.log.info("ConfigService started.")

    async def stop(self):
        await super().stop()
        self.log.info("ConfigService stopped.")

    def get(self, name: str) -> Any:
        return "any"
