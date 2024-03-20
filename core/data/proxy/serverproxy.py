from __future__ import annotations
from core import Server, Status, utils
from core.data.node import UploadStatus
from dataclasses import dataclass
from typing import Optional, Union


__all__ = ["ServerProxy"]


@dataclass
class ServerProxy(Server):

    async def reload(self):
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "reload",
            "server_name": self.name
        }, node=self.node.name)

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

    async def get_current_mission_file(self) -> Optional[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "get_current_mission_file",
            "server_name": self.name
        }, node=self.node.name)
        return data["return"]

    async def get_current_mission_theatre(self) -> Optional[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "get_current_mission_theatre",
            "server_name": self.name
        }, node=self.node.name, timeout=120)
        return data["return"]

    def send_to_dcs(self, message: dict):
        message['server_name'] = self.name
        self.bus.send_to_node(message, node=self.node.name)

    async def startup(self, modify_mission: Optional[bool] = True) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "startup",
            "modify_mission": modify_mission,
            "server_name": self.name
        }, timeout=300 if self.node.locals.get('slow_system', False) else 180, node=self.node.name)

    async def startup_extensions(self) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "startup_extensions",
            "server_name": self.name
        }, node=self.node.name, timeout=180)

    async def shutdown_extensions(self) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "shutdown_extensions",
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
        }, node=self.node.name, timeout=180)

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
        }, timeout=120, node=self.node.name)
        return UploadStatus(data["return"])

    async def listAvailableMissions(self) -> list[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "listAvailableMissions",
            "server_name": self.name
        }, timeout=60, node=self.node.name)
        return data['return']

    async def getMissionList(self) -> list[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "getMissionList",
            "server_name": self.name
        }, timeout=60, node=self.node.name)
        return data['return']

    async def apply_mission_changes(self, filename: Optional[str] = None) -> str:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "apply_mission_changes",
            "server_name": self.name,
            "params": {
                "filename": filename or ""
            }
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

    async def rename(self, new_name: str, update_settings: bool = False) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "rename",
            "server_name": self.name,
            "params": {
                "new_name": new_name,
                "update_settings": update_settings
            }
        }, node=self.node.name, timeout=60)
        self.name = new_name

    async def render_extensions(self) -> list:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "render_extensions",
            "server_name": self.name
        }, timeout=120, node=self.node.name)
        return data['return']

    async def is_running(self) -> bool:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "is_running",
            "server_name": self.name
        }, timeout=60, node=self.node.name)
        return data['return']

    async def restart(self, modify_mission: Optional[bool] = True) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "restart",
            "server_name": self.name,
            "params": {
                "modify_mission": modify_mission
            }
        }, timeout=300, node=self.node.name)

    async def setStartIndex(self, mission_id: int) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "setStartIndex",
            "server_name": self.name,
            "params": {
                "mission_id": mission_id
            }
        }, timeout=60, node=self.node.name)

    async def addMission(self, path: str, *, autostart: Optional[bool] = False) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "addMission",
            "server_name": self.name,
            "params": {
                "path": path,
                "autostart": autostart
            }
        }, timeout=60, node=self.node.name)

    async def deleteMission(self, mission_id: int) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "deleteMission",
            "server_name": self.name,
            "params": {
                "mission_id": mission_id
            }
        }, timeout=60, node=self.node.name)

    async def replaceMission(self, mission_id: int, path: str) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "replaceMission",
            "server_name": self.name,
            "params": {
                "mission_id": mission_id,
                "path": path
            }
        }, timeout=60, node=self.node.name)

    async def loadMission(self, mission: Union[int, str], modify_mission: Optional[bool] = True) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "loadMission",
            "server_name": self.name,
            "params": {
                "mission": mission,
                "modify_mission": modify_mission
            }
        }, timeout=300, node=self.node.name)

    async def loadNextMission(self, modify_mission: Optional[bool] = True) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "loadNextMission",
            "server_name": self.name,
            "params": {
                "modify_mission": modify_mission
            }
        }, timeout=300, node=self.node.name)
