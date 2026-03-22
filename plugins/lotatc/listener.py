import asyncio
import discord

from core import (EventListener, event, Server, Coalition, Player, get_translation, chat_command, ChatCommand, Side,
                  Channel, utils)
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import LotAtc

_ = get_translation(__name__.split('.')[1])


@dataclass
class GCI:
    name: str = field()
    coalition: Coalition = field()
    ipaddr: str | None = field(init=False, default=None)
    radios: list[int] = field(compare=False, default_factory=list, init=False)
    lotatc: bool = field(default=False)


class LotAtcEventListener(EventListener["LotAtc"]):
    COALITION_MARKUP = {
        Coalition.BLUE: "```ansi\n\u001b[0;34mBLUE {}```",
        Coalition.RED: "```ansi\n\u001b[0;31mRED {}```"
    }
    EVENT_TEXTS = {
        'on_gci_join': _("GCI {} on station."),
        'on_gci_leave': _("GCI {} offline.")
    }

    def __init__(self, plugin: "LotAtc"):
        super().__init__(plugin)
        self.on_station: dict[str, dict[Coalition, dict[str, GCI]]] = {}

    async def can_run(self, command: ChatCommand, server: Server, player: Player) -> bool:
        if command.name in ['gci', 'gcis'] and player.side != Side.NEUTRAL:
            return True
        return await super().can_run(command, server, player)

    def _generate_message(self, server: Server, coalition: Coalition) -> str:
        count = len(self.on_station.get(server.name, {}).get(coalition, {}))
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
        all_gcis = self.on_station.get(server.name, {})
        gci = None
        for coalition in [Coalition.BLUE, Coalition.RED]:
            for _gci in all_gcis.get(coalition, {}).values():
                if _gci.ipaddr == player.ipaddr:
                    gci = _gci
                    break
            if gci:
                break
        if (gci and self.get_config(server).get('kick_gci', False) and
                not player.check_exemptions(self.get_config(server).get('exemptions', {}))):
            asyncio.create_task(server.kick(player, reason=_("You are not allowed to play when being a GCI.")))
            admin_channel = self.bot.get_admin_channel(server)
            if admin_channel:
                asyncio.create_task(
                    admin_channel.send(_("GCI {} tried to join as player {}!").format(gci.name, player.name)))
            return
        message = ""
        message += self._generate_message(server, Coalition.BLUE)
        message += self._generate_message(server, Coalition.RED)
        if message:
            asyncio.create_task(player.sendChatMessage(message))

    async def add_gci(self, server: Server, gci: GCI) -> GCI:
        if not self.on_station.get(server.name):
            self.on_station[server.name] = {
                Coalition.BLUE: {},
                Coalition.RED: {}
            }
        self.on_station[server.name][gci.coalition][gci.name] = gci
        await server.sendPopupMessage(
            gci.coalition, _("{coalition} GCI \"{name}\" on station.").format(
                coalition=gci.coalition.value.title(), name=gci.name))
        channel = self.bot.get_channel(server.channels.get(Channel.EVENTS, -1))
        if channel:
            await channel.send(self.COALITION_MARKUP[gci.coalition].format(
                self.EVENT_TEXTS['on_gci_join'].format(gci.name)))
        return gci

    async def del_gci(self, server: Server, gci: GCI) -> None:
        await server.sendPopupMessage(gci.coalition, _("{coalition} GCI \"{name}\" offline.").format(
            coalition=gci.coalition.value.title(), name=gci.name))
        channel = self.bot.get_channel(server.channels.get(Channel.EVENTS, -1))
        if channel:
            await channel.send(self.COALITION_MARKUP[gci.coalition].format(
                self.EVENT_TEXTS['on_gci_leave'].format(gci.name)))
        del self.on_station[server.name][gci.coalition][gci.name]

    @event(name="onSRSConnect")
    async def onSRSConnect(self, server: Server, data: dict) -> None:
        if data['unit'] != 'EAM':
            return
        coalition = Coalition.BLUE if data['side'] == 2 else Coalition.RED
        gci = self.on_station.get(server.name, {}).get(coalition, {}).get(data['player_name'])
        if not gci:
            gci = await self.add_gci(server, GCI(data['player_name'], coalition))
        gci.radios = data['radios']

    @event(name="onSRSUpdate")
    async def onSRSUpdate(self, server: Server, data: dict) -> None:
        if data['unit'] != 'EAM':
            return
        coalition = Coalition.BLUE if data['side'] == 2 else Coalition.RED
        gci = self.on_station.get(server.name, {}).get(coalition, {}).get(data['player_name'])
        if not gci:
            return
        gci.radios = data['radios']

    @event(name="onSRSDisconnect")
    async def onSRSDisconnect(self, server: Server, data: dict) -> None:
        for coalition in [Coalition.BLUE, Coalition.RED]:
            gci = self.on_station.get(server.name, {}).get(coalition, {}).get(data['player_name'])
            if gci:
                gci.radios = []
                if not gci.lotatc:
                    await self.del_gci(server, gci)
                break

    @event(name="onGCIJoin")
    async def onGCIJoin(self, server: Server, data: dict) -> None:
        gci = self.on_station.get(server.name, {}).get(Coalition(data['coalition']), {}).get(data['name'])
        if not gci:
            gci = await self.add_gci(server, GCI(name=data['name'], coalition=Coalition(data['coalition'])))
        gci.ipaddr = data['ipaddr']
        gci.lotatc = True
        player = server.get_player(ipaddr=gci.ipaddr)
        if (not player or not self.get_config(server).get('kick_gci', False) or
                player.check_exemptions(self.get_config(server).get('exemptions', {}))):
            return
        asyncio.create_task(server.kick(player, reason=_("You are not allowed to play when being a GCI.")))
        admin_channel = self.bot.get_admin_channel(server)
        if admin_channel:
            asyncio.create_task(
                admin_channel.send(_("GCI {} tried to join as player {}!").format(gci.name, player.name)))

    @event(name="onGCILeave")
    async def onGCILeave(self, server: Server, data: dict) -> None:
        gci = self.on_station.get(server.name, {}).get(Coalition(data['coalition']), {}).get(data['name'])
        if not gci:
            return
        gci.lotatc = False
        gci.ipaddr = None
        if not gci.radios:
            asyncio.create_task(self.del_gci(server, gci))

    @chat_command(name="gcis", help=_("Shows active GCIs"))
    async def gcis(self, server: Server, player: Player, _params: list[str]):
        if player.side == Side.NEUTRAL:
            await player.sendChatMessage(_("You need to join a side to show their GCIs"))
            return
        coalition = Coalition.BLUE if player.side == Side.BLUE else Coalition.RED
        gcis = self.on_station.get(server.name, {}).get(coalition, {})
        if gcis:
            await player.sendUserMessage(_("The following GCIs are active on the {coalition} side:\n{gcis}").format(
                coalition=coalition.value, gcis='\n'.join([f"- {x}" for x in gcis.keys()])))
        else:
            await player.sendUserMessage(_("No GCIs are active on the {} side.").format(coalition.value))

    @staticmethod
    def create_gci_embed(gci: GCI) -> discord.Embed:
        embed = discord.Embed(color=discord.Color.blue())
        embed.title = _("Information about {coalition} GCI \"{name}\"").format(coalition=gci.coalition.value,
                                                                               name=gci.name)
        embed.add_field(name="LotAtc", value=_("active") if gci.lotatc else _("inactive"), inline=False)
        if gci.radios:
            embed.add_field(name="SRS", value=', '.join([utils.format_frequency(x) for x in gci.radios]), inline=False)
        return embed

    @chat_command(name="gci", help=_("Info about a GCI"))
    async def gci(self, server: Server, player: Player, params: list[str]):
        if not params:
            await player.sendChatMessage(_("Usage: {prefix}{command} <name>").format(
                prefix=self.prefix, command=self.gci.name))
            return
        name = ' '.join(params)
        coalition = Coalition.BLUE if player.side == Side.BLUE else Coalition.RED
        gci = self.on_station.get(server.name, {}).get(coalition, {}).get(name)
        if not gci:
            await player.sendChatMessage(_("GCI {} not found.").format(name))
            return
        await player.sendPopupMessage(utils.embed_to_simpletext(self.create_gci_embed(gci)))
