import asyncio

from core import Extension, MizFile, Server, UnsupportedMizFileException
from typing_extensions import override

__all__ = [
    "ModManager"
]


class ModManager(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.modules: dict[str, list[str]] = {}

    @override
    async def startup(self, *, quiet: bool = False) -> bool:
        filename = await self.server.get_current_mission_file()
        try:
            mission = await asyncio.to_thread(MizFile, filename)
            self.modules[self.server.name] = mission.requiredModules
        except UnsupportedMizFileException:
            self.log.warning(f"Can't read requiredModules from Mission {filename}, unsupported format.")
        return await super().startup(quiet=True)

    @override
    def shutdown(self, *, quiet: bool = False) -> bool:
        self.modules.pop(self.server.name, None)
        return super().shutdown(quiet=True)

    @override
    async def render(self, param: dict | None = None) -> dict:
        mods = self.modules.get(self.server.name)
        if not mods:
            raise NotImplementedError()

        return {
            "name": "Required Mods",
            "value": '\n'.join([f"- {mod}" for mod in mods])
        }
