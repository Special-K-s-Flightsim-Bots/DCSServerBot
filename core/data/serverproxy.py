from dataclasses import dataclass
from core import Server
from typing import Optional


@dataclass
class ServerProxy(Server):

    @property
    def is_remote(self) -> bool:
        return True

    async def get_missions_dir(self) -> str:
        data = await self.sendtoDCSSync({
            "command": "rpc",
            "object": "Server",
            "method": "missions_dir",
            "server_name": self.name
        })
        return data["missions_dir"]

    @property
    def settings(self) -> dict:
        return {}  # TODO DICT

    @property
    def options(self) -> dict:
        return {}  # TODO DICT

    async def get_current_mission_file(self) -> Optional[str]:
        data = await self.sendtoDCSSync({
            "command": "rpc",
            "object": "Server",
            "method": "get_current_mission_file",
            "server_name": self.name
        })
        return data["current_mission_file"]

    def sendtoDCS(self, message: dict):
        message['server_name'] = self.name
        self.bot.sendtoBot(message, agent=self.host)

    # TODO
    def rename(self, new_name: str, update_settings: bool = False) -> None:
        self.sendtoDCS({
            "command": "rpc",
            "object": "Server",
            "method": "rename",
            "params": {
                "new_name": new_name,
                "update_settings": update_settings
            },
            "server_name": self.name
        })

    async def startup(self) -> None:
        self.sendtoDCS({"command": "rpc", "object": "Server", "method": "startup", "server_name": self.name})

    async def shutdown(self, force: bool = False) -> None:
        self.sendtoDCS({
            "command": "intercom",
            "object": "Server",
            "method": "shutdown",
            "params": {
                "force": force
            }
        })
