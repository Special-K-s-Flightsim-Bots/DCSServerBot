from __future__ import annotations
from core import Server, Status, utils, UploadStatus
from dataclasses import dataclass, field
from typing import Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from core import InstanceProxy


@dataclass
class ServerProxy(Server):
    _instance: InstanceProxy = field(default=None)

    @property
    def is_remote(self) -> bool:
        return True

    async def get_missions_dir(self) -> str:
        data = await self.sendtoDCSSync({
            "command": "rpc",
            "object": "Server",
            "method": "get_missions_dir"
        })
        return data["return"]

    @property
    def settings(self) -> dict:
        return self._settings

    @settings.setter
    def settings(self, s: dict):
        self._settings = utils.RemoteSettingsDict(self, "settings", s)

    @property
    def options(self) -> dict:
        return self._options

    @options.setter
    def options(self, o: dict):
        self._options = utils.RemoteSettingsDict(self, "options", o)

    @property
    def instance(self) -> InstanceProxy:
        return self._instance

    @instance.setter
    def instance(self, instance: InstanceProxy):
        self._instance = instance
        self._instance.server = self

    @property
    def maintenance(self) -> bool:
        return self._maintenance

    @maintenance.setter
    def maintenance(self, maintenance: bool):
        self._maintenance = maintenance
        self.sendtoDCS({
            "command": "rpc",
            "object": "Server",
            "params": {
                "maintenance": self.maintenance
            }
        })

    async def get_current_mission_file(self) -> Optional[str]:
        data = await self.sendtoDCSSync({
            "command": "rpc",
            "object": "Server",
            "method": "get_current_mission_file"
        })
        return data["return"]

    def sendtoDCS(self, message: dict):
        message['server_name'] = self.name
        self.bus.sendtoBot(message, node=self.node.name)

    def rename(self, new_name: str, update_settings: bool = False) -> None:
        self.sendtoDCS({
            "command": "rpc",
            "object": "Server",
            "method": "rename",
            "params": {
                "new_name": new_name,
                "update_settings": update_settings
            }
        })

    async def startup(self) -> None:
        self.sendtoDCS({
            "command": "rpc",
            "object": "Server",
            "method": "do_startup",
            "server_name": self.name
        })
        timeout = 300 if self.node.locals.get('slow_system', False) else 180
        self.status = Status.LOADING
        await self.wait_for_status_change([Status.STOPPED, Status.PAUSED, Status.RUNNING], timeout)

    async def shutdown(self, force: bool = False) -> None:
        await super().shutdown(force)
        if self.status != Status.SHUTDOWN:
            self.sendtoDCS({
                "command": "rpc",
                "object": "Server",
                "method": "terminate",
                "server_name": self.name
            })
        self.status = Status.SHUTDOWN

    def ban(self, ucid: str, reason: str = 'n/a', period: int = 30*86400):
        self.sendtoDCS({
            "command": "rpc",
            "object": "Server",
            "method": "ban",
            "params": {
                "ucid": ucid,
                "reason": reason,
                "period": period
            }
        })

    def unban(self, ucid: str):
        self.sendtoDCS({
            "command": "rpc",
            "object": "Server",
            "method": "unban",
            "params": {
                "ucid": ucid
            }
        })

    async def bans(self) -> list[str]:
        data = await self.sendtoDCSSync({
            "command": "rpc",
            "object": "Server",
            "method": "bans"
        })
        return data["return"]

    async def is_banned(self, ucid: str) -> bool:
        data = await self.sendtoDCSSync({
            "command": "rpc",
            "object": "Server",
            "method": "is_banned",
            "params": {
                "ucid": ucid
            }
        })
        return data["return"] == "True"

    async def modifyMission(self, preset: Union[list, dict]) -> None:
        await self.sendtoDCSSync({
            "command": "rpc",
            "object": "Server",
            "method": "modifyMission",
            "params": {
                "preset": preset
            }
        })

    async def init_extensions(self):
        await self.sendtoDCSSync({
            "command": "rpc",
            "object": "Server",
            "method": "init_extensions"
        })

    async def uploadMission(self, filename: str, url: str, force: bool = False) -> UploadStatus:
        data = await self.sendtoDCSSync({
            "command": "rpc",
            "object": "Server",
            "method": "uploadMission",
            "params": {
                "filename": filename,
                "url": url,
                "force": force
            }
        }, timeout=60)
        return UploadStatus(data["return"])
