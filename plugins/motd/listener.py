from core import EventListener, utils


class MessageOfTheDayListener(EventListener):

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] == 1:
            return
        if 'configs' in self.locals and self.locals['configs'][0]['on_event'] == 'join':
            server = self.globals[data['server_name']]
            player = utils.get_player(self, data['server_name'], id=data['id'])
            self.bot.sendtoDCS(server, {
                "command": "sendChatMessage",
                "message": utils.format_string(self.locals['configs'][0]['message'], server=server, player=player),
                "to": data['id']
            })

    async def onMissionEvent(self, data):
        if 'configs' in self.locals:
            if self.locals['configs'][0]['on_event'] == 'birth' and data['eventName'] == 'S_EVENT_BIRTH':
                # check if it is a player that was "born"
                if 'name' not in data['initiator']:
                    return
                server = self.globals[data['server_name']]
                player = utils.get_player(self, data['server_name'], name=data['initiator']['name'])
                if self.locals['configs'][0]['display_type'] == 'chat':
                    self.bot.sendtoDCS(server, {
                        "command": "sendChatMessage",
                        "message": utils.format_string(self.locals['configs'][0]['message'], server=server,
                                                       player=player),
                        "to": data['id']
                    })
                elif self.locals['configs'][0]['display_type'] == 'popup':
                    if 'display_time' in self.locals['configs'][0]:
                        display_time = self.locals['configs'][0]['display_time']
                    else:
                        display_time = self.config['BOT']['MESSAGE_TIMEOUT']
                    self.bot.sendtoDCS(server, {
                        "command": "sendPopupMessage",
                        "message": utils.format_string(self.locals['configs'][0]['message'], server=server,
                                                       player=player, data=data),
                        "time": display_time,
                        "to": player['group_id']
                    })
