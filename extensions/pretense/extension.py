import asyncio
import glob
import os
import re

from core import Extension, Server
from typing import Optional

__all__ = [
    "Pretense"
]


class Pretense(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.missions_dir = None
        self._version = None
        asyncio.create_task(self._set_missions_dir())

    async def _set_missions_dir(self):
        self.missions_dir = await self.server.get_missions_dir()

    async def prepare(self) -> bool:
        if self.locals.get('randomize', False):
            path = os.path.join(self.missions_dir, 'Saves')
            os.makedirs(path)
            open(os.path.join(path, 'randomize.lua'), 'w').close()
        return True

    @property
    def version(self) -> Optional[str]:
        if not self._version:
            if not self.missions_dir:
                return None
            path = os.path.join(self.missions_dir, 'Saves')
            files = glob.glob(os.path.join(path, "pretense_*.json"))
            if not files:
                return None
            latest_file = max(files, key=os.path.getmtime)
            _version = re.findall(r'pretense_([\d.]+)\.json', latest_file)
            self._version = _version[0] if _version else None
        return self._version

    async def render(self, param: Optional[dict] = None) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "value": "enabled"
        }
