import asyncio
from typing import Optional

from core import utils, Coalition, Status, Server
from plugins.voting.base import VotableItem


class Preset(VotableItem):

    def __init__(self, server: Server, config: dict, params: Optional[list[str]] = None):
        super().__init__('preset', server, config, params)

    def print(self) -> str:
        return "You can now vote to change the preset of this server."

    def get_choices(self) -> list[str]:
        return ['No Change'] + list(self.config.get('choices', utils.get_presets(self.server.node)))

    async def execute(self, winner: str):
        if winner == 'No Change':
            return
        message = f"The mission will change in 60s."
        self.server.sendChatMessage(Coalition.ALL, message)
        self.server.sendPopupMessage(Coalition.ALL, message)
        await asyncio.sleep(60)
        filename = await self.server.get_current_mission_file()
        if not self.server.node.config.get('mission_rewrite', True):
            await self.server.stop()
        new_filename = await self.server.modifyMission(filename, utils.get_preset(self.server.node, winner))
        if new_filename != filename:
            await self.server.replaceMission(int(self.server.settings['listStartIndex']), new_filename)
        await self.server.restart(modify_mission=False)
        if self.server.status == Status.STOPPED:
            await self.server.start()
