from core import EventListener, utils


class HelpListener(EventListener):
    async def onChatMessage(self, data: dict):
        if data['message'].startswith('-help'):
            messages = []
            if 'punishment' in self.bot.plugins:
                messages.append('"-penalty"   display your penalty points')
                messages.append('"- forgive"   forgive another user for teamhits/-kills')
            if 'slotblocking' in self.bot.plugins:
                messages.append('"-credits"   display your credits')
            [utils.sendChatMessage(self, data['server_name'], data['from_id'], message) for message in messages]

    async def onPlayerStart(self, data):
        utils.sendChatMessage(self, data['server_name'], data['id'], 'Use "-help" for commands.')
