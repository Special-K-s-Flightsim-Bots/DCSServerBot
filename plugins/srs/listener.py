from core import EventListener, event, Server, Player, Plugin, get_translation
from typing import Optional

from plugins.mission.commands import Mission

_ = get_translation(__name__.split('.')[1])


class SRSEventListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.mission: Mission = self.bot.cogs.get('Mission')

    def get_player(self, server: Server, data: dict) -> Optional[Player]:
        if data['unit_id'] in range(100000000, 100000099):
            player = server.get_player(id=data['unit_id'] - 100000000 + 1)
        else:
            player = server.get_player(unit_id=data['unit_id'])
        if not player:
            player = server.get_player(name=data['player_name'])
        return player

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        if self.get_config(server).get('enforce_srs', False):
            player: Player = server.get_player(ucid=data['ucid'])
            if not player.srs:
                server.send_to_dcs({"command": "disableSRS", "ucid": player.ucid})

    @event(name="onSRSConnect")
    async def onSRSConnect(self, server: Server, data: dict) -> None:
        if data['player_name'] == '"LotAtc"' or data['unit'] == 'EAM':
            return
        player = self.get_player(server, data)
        if not player:
            self.log.info(f"SRS client {data['player_name']} not found on the server.")
            return
        elif player.name != data['player_name']:
            player.sendChatMessage("Please use the same name on SRS as you do in DCS!")
        player.srs = True
        player.radios = data['radios']
        if self.get_config(server).get('enforce_srs', False):
            server.send_to_dcs({"command": "enableSRS", "ucid": player.ucid})
        self.mission.eventlistener.display_player_embed(server)

    @event(name="onSRSUpdate")
    async def onSRSUpdate(self, server: Server, data: dict) -> None:
        if data['player_name'] == '"LotAtc"' or data['unit'] == 'EAM':
            return
        player = self.get_player(server, data)
        if player:
            player.radios = data['radios']

    @event(name="onSRSDisconnect")
    async def onSRSDisconnect(self, server: Server, data: dict) -> None:
        if data['player_name'] == '"LotAtc"':
            return
        player = server.get_player(name=data['player_name'])
        if not player:
            return
        player.srs = False
        player.radios = []
        if self.get_config(server).get('enforce_srs', False):
            server.send_to_dcs({"command": "disableSRS", "ucid": player.ucid})
            if self.get_config(server).get('move_to_spec', False):
                server.move_to_spectators(player,
                                          reason=self.get_config(server).get(
                                              'message_no_srs',
                                              'You need to enable SRS to use any slot on this server!'))
        self.mission.eventlistener.display_player_embed(server)
