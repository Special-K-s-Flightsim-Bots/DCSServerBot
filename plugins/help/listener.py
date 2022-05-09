from core import EventListener, utils


class HelpListener(EventListener):
    async def onChatCommand(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        if data['subcommand'] == 'help':
            messages = [
                'You can use the following commands:\n',
                '"-linkme token" link your user to Discord',
                '"-atis airport" display ATIS information'
            ]
            if 'punishment' in self.bot.plugins:
                messages.append('"-penalty"      displays your penalty points')
                messages.append('"-forgive"      forgive another user for teamhits/-kills')
            if 'slotblocking' in self.bot.plugins:
                messages.append('"-credits"      displays your credits')
            if self.config.getboolean(server['installation'], 'COALITIONS'):
                messages.append('"-join coal."   join a coalition')
                messages.append('"-leave"        leave a coalition')
                messages.append('"-password"     shows coalition password')
                messages.append('"-coalition"    shows your current coalition')
            utils.sendUserMessage(self, server, data['from_id'], '\n'.join(messages), 30)

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] != 1:
            utils.sendChatMessage(self, data['server_name'], data['id'], 'Use "-help" for commands.')
