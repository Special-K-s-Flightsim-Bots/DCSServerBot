import asyncio

from core import EventListener, event, Server, Player, get_translation, Side
from typing import cast, TYPE_CHECKING

from plugins.mission.commands import Mission

if TYPE_CHECKING:
    from .commands import SRS

_ = get_translation(__name__.split('.')[1])


class SRSEventListener(EventListener["SRS"]):

    def __init__(self, plugin: "SRS"):
        super().__init__(plugin)
        self.mission: Mission = cast(Mission, self.bot.cogs['Mission'])
        self.srs_users: dict[str, dict[str, dict]] = {}

    def _add_or_update_srs_user(self, server: Server, data: dict) -> None:
        if server.name not in self.srs_users:
            self.srs_users[server.name] = {}
        self.srs_users[server.name][data['player_name']] = data

    def _del_srs_user(self, server: Server, data: dict) -> None:
        if server.name not in self.srs_users:
            return
        self.srs_users[server.name].pop(data['player_name'], None)

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, _: dict) -> None:
        config = self.get_config(server) or {
            "message_no_srs": "You need to use SRS to play on this server!"
        }
        asyncio.create_task(server.send_to_dcs({
            'command': 'loadParams',
            'plugin': self.plugin_name,
            'params': config
        }))

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        if self.get_config(server).get('enforce_srs', False):
            player: Player = server.get_player(ucid=data['ucid'], active=True)
            if player.name not in self.srs_users.get(server.name, {}):
                asyncio.create_task(server.send_to_dcs({"command": "disableSRS", "name": player.name}))

    @event(name="onSRSConnect")
    async def onSRSConnect(self, server: Server, data: dict) -> None:
        if data['player_name'] == '"LotAtc"' or data['unit'] == 'EAM':
            return
        self._add_or_update_srs_user(server, data)
        if self.get_config(server).get('enforce_srs', False):
            asyncio.create_task(server.send_to_dcs({"command": "enableSRS", "name": data['player_name']}))
        self.mission.eventlistener.display_player_embed(server)

    @event(name="onSRSUpdate")
    async def onSRSUpdate(self, server: Server, data: dict) -> None:
        if data['player_name'] == '"LotAtc"' or data['unit'] == 'EAM':
            return
        self._add_or_update_srs_user(server, data)

    @event(name="onSRSDisconnect")
    async def onSRSDisconnect(self, server: Server, data: dict) -> None:
        if data['player_name'] == '"LotAtc"':
            return
        self._del_srs_user(server, data)
        if self.get_config(server).get('enforce_srs', False):
            asyncio.create_task(server.send_to_dcs({"command": "disableSRS", "name": data['player_name']}))
            if self.get_config(server).get('move_to_spec', False):
                player = server.get_player(name=data['player_name'])
                if player and player.side != Side.NEUTRAL:
                    asyncio.create_task(server.move_to_spectators(player, reason=self.get_config(server).get(
                        'message_no_srs', 'You need to use SRS to play on this server!')))
        self.mission.eventlistener.display_player_embed(server)
