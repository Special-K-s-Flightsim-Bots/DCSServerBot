from __future__ import annotations
from core import EventListener, chat_command, event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server, Player


class HelpListener(EventListener):

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if player:
            player.sendChatMessage(f"Use \"{self.prefix}help\" for commands.")

    @chat_command(name="help", help="The help command")
    async def help(self, _: Server, player: Player, __: list[str]):
        messages = [
            f'You can use the following commands:\n'
        ]
        for listener in self.bot.eventListeners:
            for command in listener.chat_commands:
                if command.roles and not player.has_discord_roles(command.roles):
                    continue
                cmd = f"{self.prefix}{command.name}"
                if command.usage:
                    cmd += f" {command.usage}"
                if command.help:
                    cmd += '\u2000' * (20 - len(cmd)) + f"- {command.help}"
                messages.append(cmd)
        player.sendUserMessage('\n'.join(messages), 30)
