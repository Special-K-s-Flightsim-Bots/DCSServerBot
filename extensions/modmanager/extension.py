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
        if not config.get('name'):
            self._name = 'Required Mods'

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
        ret = await super().render(param)
        mods = self.modules.get(self.server.name)
        if not mods:
            raise NotImplementedError()

        return ret | {
            "value": '\n'.join([f"- {mod}" for mod in mods])
        }
