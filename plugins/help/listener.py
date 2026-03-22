from __future__ import annotations

import asyncio

from core import EventListener, chat_command, event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server, Player
    from .commands import Help


class HelpListener(EventListener["Help"]):

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if player:
            asyncio.create_task(player.sendChatMessage(f"Use \"{self.prefix}help\" for commands."))

    @chat_command(name="help", help="The help command")
    async def help(self, server: Server, player: Player, _params: list[str]):
        messages = [
            f'You can use the following commands:\n'
        ]
        for listener in self.bot.eventListeners:
            for command in listener.chat_commands:
                if not await listener.can_run(command, server, player):
                    continue
                cmd = f"{self.prefix}{command.name}"
                if command.usage:
                    cmd += f" {command.usage}"
                if command.help:
                    cmd += '\u2000' * (20 - len(cmd)) + f"- {command.help}"
                messages.append(cmd)
        await player.sendUserMessage('\n'.join(messages), 30)
