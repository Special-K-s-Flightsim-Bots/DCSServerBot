from __future__ import annotations
from core import Server, Status, utils
from core.data.node import UploadStatus
from dataclasses import dataclass, field
from typing import Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from core import InstanceProxy

__all__ = ["ServerProxy"]


@dataclass
class ServerProxy(Server):
    _instance: InstanceProxy = field(default=None)

    @property
    def is_remote(self) -> bool:
        return True

    async def get_missions_dir(self) -> str:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "get_missions_dir",
            "server_name": self.name
        }, node=self.node.name)
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
        self.bus.send_to_node({
            "command": "rpc",
            "object": "Server",
            "server_name": self.name,
            "params": {
                "maintenance": self.maintenance
            }
        }, node=self.node.name)

    async def get_current_mission_file(self) -> Optional[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "get_current_mission_file",
            "server_name": self.name
        }, node=self.node.name)
        return data["return"]

    def send_to_dcs(self, message: dict):
        message['server_name'] = self.name
        self.bus.send_to_node(message, node=self.node.name)

    async def startup(self) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "startup",
            "server_name": self.name
        }, timeout=300 if self.node.locals.get('slow_system', False) else 180, node=self.node.name)

    async def startup_extensions(self) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "startup_extensions",
            "server_name": self.name
        }, node=self.node.name, timeout=60)

    async def shutdown(self, force: bool = False) -> None:
        await super().shutdown(force)
        if self.status != Status.SHUTDOWN:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "object": "Server",
                "method": "shutdown",
                "server_name": self.name,
                "params": {
                    "force": force
                },
            }, node=self.node.name, timeout=180)
            self.status = Status.SHUTDOWN

    async def init_extensions(self):
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "init_extensions",
            "server_name": self.name
        }, node=self.node.name, timeout=60)

    async def uploadMission(self, filename: str, url: str, force: bool = False) -> UploadStatus:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "uploadMission",
            "params": {
                "filename": filename,
                "url": url,
                "force": force
            },
            "server_name": self.name
        }, timeout=60, node=self.node.name)
        return UploadStatus(data["return"])

    async def listAvailableMissions(self) -> list[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "listAvailableMissions",
            "server_name": self.name
        }, timeout=60, node=self.node.name)
        return data['return']

    async def apply_mission_changes(self) -> bool:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "apply_mission_changes",
            "server_name": self.name
        }, timeout=120, node=self.node.name)
        return data['return']

    async def modifyMission(self, filename: str, preset: Union[list, dict]) -> str:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "modifyMission",
            "server_name": self.name,
            "params": {
                "filename": filename,
                "preset": preset
            }
        }, timeout=120, node=self.node.name)
        return data['return']

    async def persist_settings(self):
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "persist_settings",
            "server_name": self.name
        }, node=self.node.name, timeout=60)
