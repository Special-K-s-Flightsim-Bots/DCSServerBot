from core import EventListener, utils


class HelpListener(EventListener):
    async def onChatCommand(self, data: dict):
        if data['message'].startswith('-help'):
            messages = ['"-linkme token" link your user to Discord']
            if 'punishment' in self.bot.plugins:
                messages.append('"-penalty"      display your penalty points')
                messages.append('"-forgive"      forgive another user for teamhits/-kills')
            if 'slotblocking' in self.bot.plugins:
                messages.append('"-credits"      display your credits')
            [utils.sendChatMessage(self, data['server_name'], data['from_id'], message) for message in messages]

    async def onPlayerStart(self, data):
        if data['id'] != 1:
            utils.sendChatMessage(self, data['server_name'], data['id'], 'Use "-help" for commands.')
