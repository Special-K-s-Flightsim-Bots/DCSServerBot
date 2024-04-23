from core import EventListener, event, Server, Plugin, Coalition, Player, get_translation, chat_command, ChatCommand, \
    Side, Channel

_ = get_translation(__name__.split('.')[1])


class LotAtcEventListener(EventListener):
    COALITION_MARKUP = {
        "blue": "```ansi\n\u001b[0;34mBLUE {}```",
        "red": "```ansi\n\u001b[0;31mRED {}```"
    }
    EVENT_TEXTS = {
        'on_gci_join': _("GCI {} on station."),
        'on_gci_leave': _("GCI {} offline.")
    }

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.on_station: dict[str, dict[str, []]] = {}

    def can_run(self, command: ChatCommand, server: Server, player: Player) -> bool:
        if command.name == 'gci' and player.side != Side.SPECTATOR:
            return True
        return super().can_run(command, server, player)

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if not player:
            return
        message = ""
        blue = len(self.on_station.get(server.name, {}).get(Coalition.BLUE.value, []))
        if blue:
            message += _("{num} {coalition} GCI{s} on station.").format(num=blue,
                                                                        coalition=Coalition.BLUE.value.title(),
                                                                        s='s' if blue > 1 else '')
        red = len(self.on_station.get(server.name, {}).get(Coalition.RED.value, []))
        if red:
            message += _("{num} {coalition} GCI{s} on station.").format(num=red,
                                                                        coalition=Coalition.RED.value.title(),
                                                                        s='s' if red > 1 else '')
        if message:
            player.sendChatMessage(message)

    @event(name="onGCIJoin")
    async def onGCIJoin(self, server: Server, data: dict) -> None:
        if not self.on_station.get(server.name):
            self.on_station[server.name] = {
                "blue": [],
                "red": []
            }
        if data['name'] not in self.on_station[server.name][data['coalition']]:
            self.on_station[server.name][data['coalition']].append(data['name'])
            server.sendPopupMessage(Coalition(data['coalition']), _("{coalition} GCI \"{name}\" on station.").format(
                coalition=data['coalition'].title(), name=data['name']))
            channel = self.bot.get_channel(server.channels[Channel.EVENTS])
            if channel:
                await channel.send(self.COALITION_MARKUP[data['coalition']].format(
                    self.EVENT_TEXTS['on_gci_join'].format(data['name'])))

    @event(name="onGCILeave")
    async def onGCILeave(self, server: Server, data: dict) -> None:
        if data['name'] in self.on_station.get(server.name, {}).get(data['coalition'], []):
            self.on_station[server.name][data['coalition']].remove(data['name'])
            server.sendPopupMessage(Coalition(data['coalition']), _("{coalition} GCI \"{name}\" offline.").format(
                coalition=data['coalition'].title(), name=data['name']))
            channel = self.bot.get_channel(server.channels[Channel.EVENTS])
            if channel:
                await channel.send(self.COALITION_MARKUP[data['coalition']].format(
                    self.EVENT_TEXTS['on_gci_join'].format(data['name'])))

    @chat_command(name="gci", help=_("Shows active GCIs"))
    async def gci(self, server: Server, player: Player, params: list[str]):
        coalition = {
            Side.RED: "red",
            Side.BLUE: "blue"
        }
        gcis = self.on_station.get(server.name, {}).get(coalition[player.side], [])
        if gcis:
            player.sendUserMessage(_("The following GCIs are active on the {coalition} side:\n{gcis}").format(
                coalition=coalition[player.side], gcis='\n'.join([f"- {x}" for x in gcis])))
        else:
            player.sendUserMessage(_("No GCIs are active on the {} side.").format(coalition[player.side]))
