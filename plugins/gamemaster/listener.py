from core import EventListener


class GameMasterEventListener(EventListener):

    async def onChatMessage(self, data):
        chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
        if chat_channel is not None:
            if 'from_id' in data and data['from_id'] != 1 and len(data['message']) > 0:
                return await chat_channel.send(data['from_name'] + ': ' + data['message'])
        return None
