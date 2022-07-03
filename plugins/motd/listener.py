from core import EventListener, utils, Server


class MessageOfTheDayListener(EventListener):

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] == 1:
            return
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if config and 'on_join' in config:
            player = server.get_player(id=data['id'])
            player.sendChatMessage(utils.format_string(config['on_join']['message']))

    async def onMissionEvent(self, data):
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if not config:
            return
        if data['eventName'] == 'S_EVENT_BIRTH' and 'name' in data['initiator'] and 'on_birth' in config:
            player = server.get_player(name=data['initiator']['name'], active=True)
            message = utils.format_string(config['on_birth']['message'], server=server, player=player, data=data)
            if config['on_birth']['display_type'].lower() == 'chat':
                player.sendChatMessage(message)
            elif config['on_birth']['display_type'].lower() == 'popup':
                player.sendPopupMessage(message, config['on_birth']['display_time'] if 'display_time' in config['on_birth'] else -1)
