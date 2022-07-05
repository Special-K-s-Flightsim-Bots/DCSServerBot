from core import EventListener, Server, Status
from typing import cast
from .player import CreditPlayer


class CreditSystemListener(EventListener):

    async def registerDCSServer(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if config:
            server.sendtoDCS({
                'command': 'loadParams',
                'plugin': self.plugin_name,
                'params': config
            })

    def get_points_per_kill(self, server: Server, data: dict) -> int:
        default = 1
        config = self.plugin.get_config(server)
        if 'points_per_kill' in config:
            for unit in config['points_per_kill']:
                if 'category' in unit and data['victimCategory'] != unit['category']:
                    continue
                if 'unit_type' in unit and unit['unit_type'] != data['arg5']:
                    continue
                if 'type' in unit and ((unit['type'] == 'AI' and data['arg4'] != "-1") or
                                       (unit['type'] == 'Player' and data['arg4'] == "-1")):
                    continue
                if 'category' in unit or 'unit_type' in unit or 'type' in unit:
                    return unit['points']
                elif 'default' in unit:
                    default = unit['default']
        return default

    async def onPlayerStart(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if not config or data['id'] == 1:
            return
        player = server.get_player(id=data['id'])
        if player.points == -1:
            player.points = config['initial_points'] if 'initial_points' in config else 0
        player.sendChatMessage(f"{player.name}, you currently have {player.points} credit points!")

    @staticmethod
    def add_credits(config: dict, player: CreditPlayer, inc: int):
        if (player.points + inc) < 0:
            player.points = 0
        elif 'max_points' in config and (player.points + inc) > config['max_points']:
            player.points = config['max_points']

    async def addUserPoints(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        player: CreditPlayer = cast(CreditPlayer, server.get_player(name=data['name']))
        config = self.plugin.get_config(server)
        self.add_credits(config, player, data['points'])

    async def onGameEvent(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if not config or server.status != Status.RUNNING:
            return
        if data['eventName'] == 'kill':
            # players gain points only, if they don't kill themselves and no teamkills
            if data['arg1'] != -1 and data['arg1'] != data['arg4'] and data['arg3'] != data['arg6']:
                # Multicrew - pilot and all crew members gain points
                for player in server.get_crew_members(server.get_player(id=data['arg1'])):
                    self.add_credits(config, player, self.get_points_per_kill(server, data))

    async def onChatCommand(self, data: dict) -> None:
        if data['subcommand'] == 'credits':
            server: Server = self.bot.servers[data['server_name']]
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['from_id']))
            player.sendChatMessage(f"You currently have {player.points} credit points.")
        elif data['subcommand'] == 'donate':
            server: Server = self.bot.servers[data['server_name']]
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['from_id']))
            if len(data['params']) < 2:
                player.sendChatMessage(f"Usage: {self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}donate player points")
                return
            name = ' '.join(data['params'][:-1])
            donation = int(data['params'][-1])
            if donation > player.points:
                player.sendChatMessage(f"You can't donate {donation} credit points as you only have {player.points}!")
                return
            elif donation <= 0:
                player.sendChatMessage(f"Donation has to be a positive value.")
                return
            receiver: CreditPlayer = cast(CreditPlayer, server.get_player(name=name))
            if not receiver:
                player.sendChatMessage(f"Player {name} not found.")
                return
            config = self.plugin.get_config(server)
            if 'max_points' in config and (receiver.points + donation) > config['max_points']:
                player.sendChatMessage(f"Player {receiver} would overrun the configured maximum points with this "
                                       f"donation. Aborted.")
                return
            player.points -= donation
            receiver.points += donation
            player.sendChatMessage(f"You've donated {donation} credit points to player {name}.")
            receiver.sendChatMessage(f"Player {player.name} donated {donation} credit points to you!")
