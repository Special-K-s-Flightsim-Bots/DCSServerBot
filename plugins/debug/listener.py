import asyncio
import json

from core import EventListener, chat_command, event, Server, Player, Coalition
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Debug


class DebugListener(EventListener["Debug"]):
    def __init__(self, plugin: "Debug"):
        super().__init__(plugin)
        self.debug = False

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        if self.debug:
            initiator = server.get_player(name=data.get('initiator', {}).get('name'))
            target = server.get_player(name=data.get('target', {}).get('name'))
            weapon = data.get('weapon', {}).get('name') or 'n/a'
            message = "{eventName}: {initiator} | {target} | {weapon}".format(
                eventName=data['eventName'],
                initiator=initiator.name if initiator else 'n/a',
                target=target.name if target else 'n/a',
                weapon=weapon
            )
            asyncio.create_task(server.sendPopupMessage(recipient=Coalition.ALL, message=message))

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if self.debug:
            message = json.dumps(data)
            asyncio.create_task(server.sendPopupMessage(recipient=Coalition.ALL, message=message))

    @chat_command(name="debug", help="Enable debugging", roles=['Admin'])
    async def debug(self, _server: Server, player: Player, _params: list[str]):
        self.debug = not self.debug
        if self.debug:
            await player.sendChatMessage("Debug enabled")
        else:
            await player.sendChatMessage("Debug disabled")
