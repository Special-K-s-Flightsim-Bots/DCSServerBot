import discord

from core import EventListener, event, Server, Plugin, Coalition, Player, get_translation, chat_command, ChatCommand, \
    Side, Channel, utils
from dataclasses import dataclass, field

_ = get_translation(__name__.split('.')[1])


@dataclass
class GCI:
    name: str = field()
    coalition: str = field()
    radios: list[int] = field(compare=False, default_factory=list, init=False)
    lotatc: bool = field(default=False)


class LotAtcEventListener(EventListener):
    COALITION_MARKUP = {
        "blue": "```ansi\n\u001b[0;34mBLUE {}```",
        "red": "```ansi\n\u001b[0;31mRED {}```"
    }
    EVENT_TEXTS = {
        'on_gci_join': _("GCI {} on station."),
        'on_gci_leave': _("GCI {} offline.")
    }
    COALITION = {
        Side.BLUE: "blue",
        Side.RED: "red"
    }

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.on_station: dict[str, dict[str, dict[str, GCI]]] = {}

    def can_run(self, command: ChatCommand, server: Server, player: Player) -> bool:
        if command.name in ['gci', 'gcis'] and player.side != Side.SPECTATOR:
            return True
        return super().can_run(command, server, player)

    def _generate_message(self, server: Server, coalition: Coalition) -> str:
        count = len(self.on_station.get(server.name, {}).get(coalition.value, {}))
        if count:
            return _("{num} {coalition} GCI{s} on station.").format(
                num=count,
                coalition=coalition.value.title(),
                s='s' if count > 1 else ''
            )
        return ''

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if not player:
            return
        message = ""
        message += self._generate_message(server, Coalition.BLUE)
        message += self._generate_message(server, Coalition.RED)
        if message:
            player.sendChatMessage(message)

    async def add_gci(self, server: Server, gci: GCI) -> GCI:
        if not self.on_station.get(server.name):
            self.on_station[server.name] = {
                "blue": {},
                "red": {}
            }
        self.on_station[server.name][gci.coalition][gci.name] = gci
        server.sendPopupMessage(
            Coalition(gci.coalition), _("{coalition} GCI \"{name}\" on station.").format(
                coalition=gci.coalition.title(), name=gci.name))
        channel = self.bot.get_channel(server.channels[Channel.EVENTS])
        if channel:
            await channel.send(self.COALITION_MARKUP[gci.coalition].format(
                self.EVENT_TEXTS['on_gci_join'].format(gci.name)))
        return gci

    async def del_gci(self, server: Server, gci: GCI) -> None:
        server.sendPopupMessage(Coalition(gci.coalition), _("{coalition} GCI \"{name}\" offline.").format(
            coalition=gci.coalition.title(), name=gci.name))
        channel = self.bot.get_channel(server.channels[Channel.EVENTS])
        if channel:
            await channel.send(self.COALITION_MARKUP[gci.coalition].format(
                self.EVENT_TEXTS['on_gci_leave'].format(gci.name)))
        del self.on_station[server.name][gci.coalition][gci.name]

    @event(name="onSRSConnect")
    async def onSRSConnect(self, server: Server, data: dict) -> None:
        if data['unit'] != 'EAM':
            return
        coalition = self.COALITION[Side(data['side'])]
        gci = self.on_station.get(server.name, {}).get(coalition, {}).get(data['player_name'])
        if not gci:
            gci = await self.add_gci(server, GCI(data['player_name'], coalition))
        gci.radios = data['radios']

    @event(name="onSRSUpdate")
    async def onSRSUpdate(self, server: Server, data: dict) -> None:
        if data['unit'] != 'EAM':
            return
        gci = self.on_station.get(server.name, {}).get(self.COALITION[Side(data['side'])], {}).get(data['player_name'])
        if not gci:
            return
        gci.radios = data['radios']

    @event(name="onSRSDisconnect")
    async def onSRSDisconnect(self, server: Server, data: dict) -> None:
        for side in [Side.BLUE, Side.RED]:
            gci = self.on_station.get(server.name, {}).get(self.COALITION[side], {}).get(data['player_name'])
            if gci:
                gci.radios = []
                if not gci.lotatc:
                    await self.del_gci(server, gci)
                break

    @event(name="onGCIJoin")
    async def onGCIJoin(self, server: Server, data: dict) -> None:
        gci = self.on_station.get(server.name, {}).get(data['coalition'], {}).get(data['name'])
        if not gci:
            gci = await self.add_gci(server, GCI(data['name'], data['coalition']))
        gci.lotatc = True

    @event(name="onGCILeave")
    async def onGCILeave(self, server: Server, data: dict) -> None:
        gci = self.on_station.get(server.name, {}).get(data['coalition'], {}).get(data['name'])
        if not gci:
            return
        gci.lotatc = False
        if not gci.radios:
            await self.del_gci(server, gci)

    @chat_command(name="gcis", help=_("Shows active GCIs"))
    async def gcis(self, server: Server, player: Player, params: list[str]):
        coalition = self.COALITION[player.side]
        gcis = self.on_station.get(server.name, {}).get(coalition, {})
        if gcis:
            player.sendUserMessage(_("The following GCIs are active on the {coalition} side:\n{gcis}").format(
                coalition=coalition, gcis='\n'.join([f"- {x}" for x in gcis.keys()])))
        else:
            player.sendUserMessage(_("No GCIs are active on the {} side.").format(coalition))

    @staticmethod
    def create_gci_embed(gci: GCI) -> discord.Embed:
        embed = discord.Embed(color=discord.Color.blue())
        embed.title = _("Information about {coalition} GCI \"{name}\"").format(coalition=gci.coalition, name=gci.name)
        embed.add_field(name="LotAtc", value=_("active") if gci.lotatc else _("inactive"), inline=False)
        if gci.radios:
            embed.add_field(name="SRS", value=', '.join([utils.format_frequency(x) for x in gci.radios]), inline=False)
        return embed

    @chat_command(name="gci", help=_("Info about a GCI"))
    async def gci(self, server: Server, player: Player, params: list[str]):
        if not params:
            player.sendChatMessage(_("Usage: {}gci <name>").format(self.prefix))
            return
        name = ' '.join(params)
        gci = self.on_station.get(server.name, {}).get(self.COALITION[player.side], {}).get(name)
        if not gci:
            player.sendChatMessage(_("GCI {} not found.").format(name))
            return
        player.sendPopupMessage(utils.embed_to_simpletext(self.create_gci_embed(gci)))
