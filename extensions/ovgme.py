import asyncio

from core import Extension, MizFile, Server
from typing import Optional


class OvGME(Extension):
    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.modules: dict[str, list[str]] = {}

    def is_installed(self) -> bool:
        return self.config.get('enabled', True)

    async def startup(self) -> bool:
        await super().startup()
        mission = await asyncio.to_thread(MizFile, self, await self.server.get_current_mission_file())
        self.modules[self.server.name] = mission.requiredModules
        return True

    def shutdown(self) -> bool:
        self.modules.pop(self.server.name, None)
        return super().shutdown()

    async def render(self, param: Optional[dict] = None) -> dict:
        mods = self.modules.get(self.server.name)
        if mods:
            return {
                "name": "Required Mods",
                "value": '\n'.join([f"- {mod}" for mod in mods])
            }
        else:
            raise NotImplementedError()
