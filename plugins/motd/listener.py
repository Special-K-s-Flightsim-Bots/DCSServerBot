from core import EventListener, utils


class MessageOfTheDayListener(EventListener):

    async def registerDCSServer(self, data: dict):
        server = self.globals[data['server_name']]
        if 'configs' in self.locals:
            specific = default = None
            for element in self.locals['configs']:
                if 'installation' in element or 'server_name' in element:
                    if ('installation' in element and server['installation'] == element['installation']) or \
                            ('server_name' in element and server['server_name'] == element['server_name']):
                        specific = element
                else:
                    default = element
            if specific:
                server[self.plugin_name] = specific
            elif default:
                server[self.plugin_name] = default

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] == 1:
            return
        server = self.globals[data['server_name']]
        config = server[self.plugin_name] if self.plugin_name in server else None
        if config and 'on_join' in config:
            player = utils.get_player(self, data['server_name'], id=data['id'])
            self.bot.sendtoDCS(server, {
                "command": "sendChatMessage",
                "message": utils.format_string(config['on_join']['message'], server=server, player=player),
                "to": data['id']
            })

    async def onMissionEvent(self, data):
        server = self.globals[data['server_name']]
        config = server[self.plugin_name] if self.plugin_name in server else None
        if not config:
            return
        if data['eventName'] == 'S_EVENT_BIRTH' and 'name' in data['initiator'] and 'on_birth' in config:
            player = utils.get_player(self, data['server_name'], name=data['initiator']['name'], active=True)
            message = utils.format_string(config['on_birth']['message'], server=server, player=player, data=data)
            if config['on_birth']['display_type'].lower() == 'chat':
                self.bot.sendtoDCS(server, {
                    "command": "sendChatMessage",
                    "message": message,
                    "to": player['id']
                })
            elif config['on_birth']['display_type'].lower() == 'popup':
                if 'display_time' in config['on_birth']:
                    display_time = config['on_birth']['display_time']
                else:
                    display_time = self.config['BOT']['MESSAGE_TIMEOUT']
                self.bot.sendtoDCS(server, {
                    "command": "sendPopupMessage",
                    "message": message,
                    "time": display_time,
                    "to": player['slot']
                })
