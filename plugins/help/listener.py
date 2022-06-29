from __future__ import annotations
from core import EventListener
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server, Player


class HelpListener(EventListener):
    async def onChatCommand(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        if data['subcommand'] == 'help':
            messages = [
                'You can use the following commands:\n',
                '"-linkme token" link your user to Discord',
                '"-atis airport" display ATIS information'
            ]
            player = server.get_player(id=data['from_id'])
            dcs_admin = player.has_discord_roles(['DCS Admin'])
            if dcs_admin:
                messages.append('"-kick \'name\'"  kick a user')
                messages.append('"-restart time" restart the running mission')
                messages.append('"-list"         list available missions')
                messages.append('"-load number"  load a specific mission')
                messages.append('"-preset"       load a specific weather preset')
            game_master = player.has_discord_roles(['GameMaster'])
            if dcs_admin or game_master:
                messages.append('"-flag"         reads or sets a flag')
            if 'punishment' in self.bot.plugins:
                messages.append('"-penalty"      displays your penalty points')
                messages.append('"-forgive"      forgive another user for teamhits/-kills')
            if 'slotblocking' in self.bot.plugins:
                messages.append('"-credits"      displays your credits')
            if self.bot.config.getboolean(server.installation, 'COALITIONS'):
                messages.append('"-join coal."   join a coalition')
                messages.append('"-leave"        leave a coalition')
                messages.append('"-password"     shows coalition password')
                messages.append('"-coalition"    shows your current coalition')
            player.sendUserMessage('\n'.join(messages), 30)

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] == 1:
            return
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['id'])
        player.sendChatMessage('Use "-help" for commands.')
