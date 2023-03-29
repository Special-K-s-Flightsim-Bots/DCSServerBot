from core import EventListener, utils, Server, Report, Player, event


class MessageOfTheDayListener(EventListener):

    @staticmethod
    def on_join(config: dict) -> str:
        return utils.format_string(config['on_join']['message'])

    async def on_birth(self, config: dict, server: Server, player: Player) -> str:
        message = None
        if 'message' in config['on_birth']:
            message = utils.format_string(config['on_birth']['message'], server=server, player=player)
        elif 'report' in config['on_birth']:
            report = Report(self.bot, self.plugin_name, config['on_birth']['report'])
            env = await report.render(server=server, player=player, guild=self.bot.guilds[0])
            message = utils.embed_to_simpletext(env.embed)
        return message

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1:
            return
        config = self.plugin.get_config(server)
        if config and 'on_join' in config:
            player: Player = server.get_player(id=data['id'])
            player.sendChatMessage(self.on_join(config))

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config:
            return
        if data['eventName'] == 'S_EVENT_BIRTH' and 'name' in data['initiator'] and 'on_birth' in config:
            player: Player = server.get_player(name=data['initiator']['name'], active=True)
            message = await self.on_birth(config, server, player)
            if message:
                self.plugin.send_message(message, server, config['on_birth'], player)
