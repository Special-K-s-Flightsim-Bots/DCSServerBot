import re
from core import EventListener, Plugin, Server, Side, Status
from typing import Union, cast
from plugins.creditsystem.player import CreditPlayer


class SlotBlockingListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)

    async def registerDCSServer(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if config:
            server.sendtoDCS({
                'command': 'loadParams',
                'plugin': self.plugin_name,
                'params': config
            })

    def get_points(self, server: Server, player: CreditPlayer) -> int:
        config = self.plugin.get_config(server)
        if 'restricted' in config:
            for unit in config['restricted']:
                if ('unit_type' in unit and unit['unit_type'] == player.unit_type) or \
                        ('unit_name' in unit and unit['unit_name'] in player.unit_name) or \
                        ('group_name' in unit and unit['group_name'] in player.group_name):
                    if player.sub_slot == 0 and 'points' in unit:
                        return unit['points']
                    elif player.sub_slot > 0 and 'crew' in unit:
                        return unit['crew']
        return 0

    def get_costs(self, server: Server, data: Union[CreditPlayer, dict]) -> int:
        config = self.plugin.get_config(server)
        unit_type = data.unit_type if isinstance(data, CreditPlayer) else data['unit_type']
        unit_name = data.unit_name if isinstance(data, CreditPlayer) else data['unit_name']
        group_name = data.group_name if isinstance(data, CreditPlayer) else data['group_name']
        if 'restricted' in config:
            for unit in config['restricted']:
                if ('unit_type' in unit and re.match(unit['unit_type'], unit_type)) or \
                        ('unit_name' in unit and re.match(unit['unit_name'], unit_name)) or \
                        ('group_name' in unit and re.match(unit['group_name'], group_name)):
                    if 'costs' in unit:
                        return unit['costs']
        return 0

    async def onPlayerChangeSlot(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if not config:
            return
        if 'side' in data and 'use_reservations' in config and config['use_reservations']:
            player: CreditPlayer = cast(CreditPlayer, server.get_player(ucid=data['ucid']))
            if player.deposit > 0:
                player.points -= player.deposit
                player.deposit = 0
            if Side(data['side']) != Side.SPECTATOR:
                # only pilots have to "pay" for their plane
                if int(data['sub_slot']) == 0:
                    # slot change - credit will be taken
                    costs = self.get_costs(server, data)
                    if costs > 0:
                        player.deposit = costs

    async def onGameEvent(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if not config or server.status != Status.RUNNING:
            return
        if data['eventName'] == 'kill':
            # players only lose points if they weren't killed as a teamkill
            if data['arg4'] != -1 and data['arg3'] != data['arg6']:
                # if we don't use reservations, credit will be taken on kill
                player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg4']))
                player.points -= self.get_costs(server, player)
                if player.deposit > 0:
                    player.deposit = 0
                # if the remaining points are not enough to stay in this plane, move them back to spectators
                if player.points < self.get_points(server, player):
                    server.move_to_spectators(player)
        elif data['eventName'] == 'crash':
            # if we don't use reservations, credit will be taken on crash
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg1']))
            player.points -= self.get_costs(server, player)
            if player.deposit > 0:
                player.deposit = 0
            if player.points < self.get_points(server, player):
                server.move_to_spectators(player)
        elif data['eventName'] == 'landing':
            # clear deposit on landing
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg1']))
            if player.deposit > 0:
                player.deposit = 0
        elif data['eventName'] == 'takeoff':
            # take deposit on takeoff
            if 'use_reservations' in config and config['use_reservations']:
                player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg1']))
                if player.deposit == 0 and int(player.sub_slot) == 0:
                    player.deposit = self.get_costs(server, player)
        elif data['eventName'] == 'disconnect':
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg1']))
            if player.deposit > 0:
                player.points -= player.deposit
                player.deposit = 0
        elif data['eventName'] == 'mission_end':
            # give all players their credit back, if the mission ends and they are still airborne
            for player in server.players:
                player.deposit = 0
