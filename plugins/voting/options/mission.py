import asyncio
import os

from core import Coalition, Server
from plugins.voting.base import VotableItem


class Mission(VotableItem):

    def __init__(self, server: Server, config: dict, params: list[str] | None = None):
        super().__init__('mission', server, config, params)

    def __repr__(self) -> str:
        return f"Vote to change mission"

    async def print(self) -> str:
        return ("You can now vote to change the mission of this server.\n"
                "If you vote for the current mission, the mission will be restarted!\n"
                "If you do not want any change, vote for \"No Change\".")

    async def get_choices(self) -> list[str]:
        return ['No Change'] + self.config.get('choices', [
            os.path.basename(x) for x in await self.server.getMissionList()
        ])

    async def execute(self, winner: str):
        if winner == 'No Change':
            return
        if self.server.is_populated():
            message = f"The mission will change in 60s."
            await self.server.sendChatMessage(Coalition.ALL, message)
            await self.server.sendPopupMessage(Coalition.ALL, message)
            await asyncio.sleep(60)

        for idx, mission in enumerate(await self.server.getMissionList()):
            if winner in mission:
                asyncio.create_task(self.server.loadMission(mission=idx + 1, modify_mission=False))
                break
        else:
            mission = os.path.join(await self.server.get_missions_dir(), winner)
            asyncio.create_task(self.server.loadMission(mission=mission, modify_mission=False))
