from __future__ import annotations
from core import EventListener, chat_command, event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server, Player


class HelpListener(EventListener):

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1:
            return
        player: Player = server.get_player(id=data['id'])
        if player:
            player.sendChatMessage(f"Use \"{self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}help\" for commands.")

    @chat_command(name="help", help="The help command")
    async def help(self, server: Server, player: Player, params: list[str]):
        prefix = self.bot.config['BOT']['CHAT_COMMAND_PREFIX']
        messages = [
            f'You can use the following commands:\n'
        ]
        for listener in self.bot.eventListeners:
            for chat_command in listener.chat_commands:
                if chat_command.roles and not player.has_discord_roles(chat_command.roles):
                    continue
                cmd = f"{prefix}{chat_command.name}"
                if chat_command.usage:
                    cmd += f" {chat_command.usage}"
                if chat_command.help:
                    cmd += '\u2000' * (20 - len(cmd)) + f"- {chat_command.help}"
                messages.append(cmd)
        player.sendUserMessage('\n'.join(messages), 30)
