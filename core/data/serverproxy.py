from dataclasses import dataclass
from core import Server, Status, utils
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
            "method": "missions_dir"
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

    async def get_current_mission_file(self) -> Optional[str]:
        data = await self.sendtoDCSSync({
            "command": "rpc",
            "object": "Server",
            "method": "get_current_mission_file"
        })
        return data["return"]

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
            }
        })

    async def startup(self) -> None:
        self.sendtoDCS({
            "command": "rpc",
            "object": "Server",
            "method": "do_startup",
            "server_name": self.name
        })
        timeout = 300 if self.bot.config.getboolean('BOT', 'SLOW_SYSTEM') else 180
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
