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
                server[self.plugin] = specific
            elif default:
                server[self.plugin] = default

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] == 1:
            return
        server = self.globals[data['server_name']]
        config = server[self.plugin] if self.plugin in server else None
        if config and config['on_event'].lower() == 'join':
            player = utils.get_player(self, data['server_name'], id=data['id'])
            self.bot.sendtoDCS(server, {
                "command": "sendChatMessage",
                "message": utils.format_string(config['message'], server=server, player=player),
                "to": data['id']
            })

    async def onMissionEvent(self, data):
        server = self.globals[data['server_name']]
        config = server[self.plugin] if self.plugin in server else None
        if not config:
            return
        if config['on_event'].lower() == 'birth' and data['eventName'] == 'S_EVENT_BIRTH':
            # check if it is a player that was "born"
            if 'name' not in data['initiator']:
                return
            player = utils.get_player(self, data['server_name'], name=data['initiator']['name'])
            if config['display_type'].lower() == 'chat':
                self.bot.sendtoDCS(server, {
                    "command": "sendChatMessage",
                    "message": utils.format_string(config['message'], server=server,
                                                   player=player),
                    "to": data['id']
                })
            elif config['display_type'].lower() == 'popup':
                if 'display_time' in config:
                    display_time = config['display_time']
                else:
                    display_time = self.config['BOT']['MESSAGE_TIMEOUT']
                self.bot.sendtoDCS(server, {
                    "command": "sendPopupMessage",
                    "message": utils.format_string(config['message'], server=server,
                                                   player=player, data=data),
                    "time": display_time,
                    "to": player['group_id']
                })
