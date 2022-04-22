from core import EventListener, utils


class HelpListener(EventListener):
    async def onChatCommand(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        if data['subcommand'] == 'help':
            messages = ['"-linkme token" link your user to Discord']
            if 'punishment' in self.bot.plugins:
                messages.append('"-penalty"      display your penalty points')
                messages.append('"-forgive"      forgive another user for teamhits/-kills')
            if 'slotblocking' in self.bot.plugins:
                messages.append('"-credits"      display your credits')
            if self.config.getboolean(server['installation'], 'COALITIONS'):
                messages.append('"-join"         join a coalition')
                messages.append('"-leave"        leave a coalition')
                messages.append('"-password"     show coalition password')
                messages.append('"-coalition"    show your current coalition')
            [utils.sendChatMessage(self, data['server_name'], data['from_id'], message) for message in messages]

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] != 1:
            utils.sendChatMessage(self, data['server_name'], data['id'], 'Use "-help" for commands.')
