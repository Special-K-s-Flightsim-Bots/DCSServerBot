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
        data = await self.send_to_dcs_sync({
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
        self.send_to_dcs({
            "command": "rpc",
            "object": "Server",
            "params": {
                "maintenance": self.maintenance
            }
        })

    async def get_current_mission_file(self) -> Optional[str]:
        data = await self.send_to_dcs_sync({
            "command": "rpc",
            "object": "Server",
            "method": "get_current_mission_file"
        })
        return data["return"]

    def send_to_dcs(self, message: dict):
        message['server_name'] = self.name
        self.bus.send_to_node(message, node=self.node.name)

    def rename(self, new_name: str, update_settings: bool = False) -> None:
        self.send_to_dcs({
            "command": "rpc",
            "object": "Server",
            "method": "rename",
            "params": {
                "new_name": new_name,
                "update_settings": update_settings
            }
        })

    async def startup(self) -> None:
        await self.send_to_dcs_sync({
            "command": "rpc",
            "object": "Server",
            "method": "startup",
            "server_name": self.name
        }, timeout=300 if self.node.locals.get('slow_system', False) else 180)

    async def startup_extensions(self) -> None:
        self.send_to_dcs({
            "command": "rpc",
            "object": "Server",
            "method": "startup_extensions",
            "server_name": self.name
        })

    async def shutdown(self, force: bool = False) -> None:
        await super().shutdown(force)
        if self.status != Status.SHUTDOWN:
            await self.send_to_dcs_sync({
                "command": "rpc",
                "object": "Server",
                "method": "shutdown",
                "server_name": self.name,
                "params": {
                    "force": force
                }
            })
        self.status = Status.SHUTDOWN

    async def modifyMission(self, preset: Union[list, dict]) -> None:
        await self.send_to_dcs_sync({
            "command": "rpc",
            "object": "Server",
            "method": "modifyMission",
            "params": {
                "preset": preset
            }
        })

    async def init_extensions(self):
        await self.send_to_dcs_sync({
            "command": "rpc",
            "object": "Server",
            "method": "init_extensions"
        })

    async def uploadMission(self, filename: str, url: str, force: bool = False) -> UploadStatus:
        data = await self.send_to_dcs_sync({
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

    async def listAvailableMissions(self) -> list[str]:
        data = await self.send_to_dcs_sync({
            "command": "rpc",
            "object": "Server",
            "method": "listAvailableMissions"
        }, timeout=60)
        return data['return']
