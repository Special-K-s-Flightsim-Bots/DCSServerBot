import discord

from core import EventListener, Server, Status, utils, event, chat_command, Player
from typing import cast
from .player import CreditPlayer


class CreditSystemListener(EventListener):

    def load_params_into_mission(self, server: Server):
        config = self.plugin.get_config(server, use_cache=False)
        if config:
            server.send_to_dcs({
                'command': 'loadParams',
                'plugin': self.plugin_name,
                'params': config
            })

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        # the server is running already
        if data['channel'].startswith('sync-'):
            self.load_params_into_mission(server)

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        self.load_params_into_mission(server)

    @staticmethod
    def get_points_per_kill(config: dict, data: dict) -> int:
        default = 1
        if 'points_per_kill' in config:
            for unit in config['points_per_kill']:
                if 'category' in unit and data['victimCategory'] != unit['category']:
                    continue
                if 'unit_type' in unit and unit['unit_type'] != data['arg5']:
                    continue
                if 'type' in unit and ((unit['type'] == 'AI' and int(data['arg4']) != -1) or
                                       (unit['type'] == 'Player' and int(data['arg4']) == -1)):
                    continue
                if 'category' in unit or 'unit_type' in unit or 'type' in unit:
                    return unit['points']
                elif 'default' in unit:
                    default = unit['default'] if data['victimCategory'] != 'Structures' else 0
        return default if data['victimCategory'] != 'Structures' else 0

    @staticmethod
    def get_initial_points(player: CreditPlayer, config: dict) -> int:
        if not config or 'initial_points' not in config:
            return 0
        if isinstance(config['initial_points'], int):
            return config['initial_points']
        elif isinstance(config['initial_points'], list):
            roles = [x.name for x in player.member.roles] if player.member else []
            for element in config['initial_points']:
                if 'discord' in element and element['discord'] in roles:
                    return element['points']
                elif 'default' in element:
                    return element['default']
        return 0

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1:
            return
        config = self.plugin.get_config(server)
        player = cast(CreditPlayer, server.get_player(id=data['id']))
        if player.points == -1:
            player.points = self.get_initial_points(player, config)
            player.audit('init', player.points, 'Initial points received')
        else:
            server.send_to_dcs({
                'command': 'updateUserPoints',
                'ucid': player.ucid,
                'points': player.points
            })
        if config:
            player.sendChatMessage(f"{player.name}, you currently have {player.points} credit points.")

    @event(name="addUserPoints")
    async def addUserPoints(self, server: Server, data: dict) -> None:
        if data['points'] != 0:
            player: CreditPlayer = cast(CreditPlayer, server.get_player(name=data['name']))
            old_points = player.points
            player.points += int(data['points'])
            if old_points != player.points:
                player.audit('mission', old_points, 'Unknown mission achievement')

    def get_flighttime(self, ucid: str, campaign_id: int) -> int:
        with self.pool.connection() as conn:
            return int(conn.execute("""
                SELECT COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))), 0) AS playtime 
                FROM statistics s, missions m, campaigns c, campaigns_servers cs 
                WHERE s.player_ucid = %s AND c.id = %s AND s.mission_id = m.id AND cs.campaign_id = c.id 
                AND m.server_name = cs.server_name 
                AND tsrange(s.hop_on, s.hop_off) && tsrange(c.start, c.stop)
            """, (ucid, campaign_id)).fetchone()[0])

    async def process_achievements(self, server: Server, player: CreditPlayer):
        # only members can achieve roles
        member = player.member
        if not member:
            return
        config: dict = self.plugin.get_config(server)
        if 'achievements' not in config:
            return

        campaign_id, _ = utils.get_running_campaign(self.bot, server)
        playtime = self.get_flighttime(player.ucid, campaign_id) / 3600.0
        sorted_achievements = sorted(config['achievements'], key=lambda x: x['credits'], reverse=True)
        role = None
        for achievement in sorted_achievements:
            if 'combined' in achievement and achievement['combined']:
                if ('credits' in achievement and player.points >= achievement['credits']) and \
                        ('playtime' in achievement and playtime >= achievement['playtime']):
                    role = achievement['role']
                    break
            else:
                if ('credits' in achievement and player.points >= achievement['credits']) or \
                        ('playtime' in achievement and playtime >= achievement['playtime']):
                    role = achievement['role']
                    break

        if role:
            for achievement in sorted_achievements:
                # does the member need to get that role?
                if achievement['role'] == role and not utils.check_roles([achievement['role']], member):
                    try:
                        _role = discord.utils.get(member.guild.roles, name=achievement['role'])
                        if _role:
                            await member.add_roles(_role)
                            await self.bot.audit(f"achieved the rank {role}", user=member)
                        else:
                            self.log.error(f"Role {achievement['role']} not found in your Discord!")
                    except discord.Forbidden:
                        self.log.error('The bot needs the Manage Roles permission!')
                # does the member have that role, but they do not deserve it?
                elif achievement['role'] != role and utils.check_roles([achievement['role']], member):
                    try:
                        _role = discord.utils.get(member.guild.roles, name=achievement['role'])
                        await member.remove_roles(_role)
                        await self.bot.audit(f"lost the rank {achievement['role']}", user=member)
                    except discord.Forbidden:
                        self.log.error('The bot needs the Manage Roles permission!')

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config or server.status != Status.RUNNING:
            return
        if data['eventName'] == 'kill':
            # players gain points only, if they don't kill themselves and no teamkills
            if data['arg1'] != -1 and data['arg1'] != data['arg4'] and data['arg3'] != data['arg6']:
                # Multicrew - pilot and all crew members gain points
                for player in server.get_crew_members(server.get_player(id=data['arg1'])):  # type: CreditPlayer
                    ppk = self.get_points_per_kill(config, data)
                    if ppk:
                        old_points = player.points
                        player.points += ppk
                        player.audit('kill', old_points, f"Killed an enemy {data['arg5']}")

        elif data['eventName'] == 'disconnect':
            server: Server = self.bot.servers[data['server_name']]
            player = cast(CreditPlayer, server.get_player(id=data['arg1']))
            if player:
                await self.process_achievements(server, player)

    @chat_command(name="credits", help="displays your credits")
    async def credits(self, server: Server, player: Player, params: list[str]):
        message = f"You currently have {player.points} credit points"
        if player.deposit > 0:
            message += f", {player.deposit} on deposit"
        message += '.'
        player.sendChatMessage(message)

    @chat_command(name="donate", help="donate points to another player")
    async def donate(self, server: Server, player: Player, params: list[str]):
        if len(params) < 2:
            player.sendChatMessage(f"Usage: {self.prefix}donate player points")
            return
        name = ' '.join(params[:-1])
        try:
            donation = int(params[-1])
        except ValueError:
            player.sendChatMessage(f"Usage: {self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}donate player points")
            return
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
        old_points_player = player.points
        old_points_receiver = receiver.points
        player.points -= donation
        player.audit('donation', old_points_player, f"Donation to player {receiver.name}")
        receiver.points += donation
        receiver.audit('donation', old_points_receiver, f"Donation from player {player.name}")
        player.sendChatMessage(f"You've donated {donation} credit points to player {name}.")
        receiver.sendChatMessage(f"Player {player.name} donated {donation} credit points to you!")

    @chat_command(name="tip", help="tip a GCI with points")
    async def tip(self, server: Server, player: Player, params: list[str]):
        player: CreditPlayer = cast(CreditPlayer, player)

        if not params:
            player.sendChatMessage(f"Usage: {self.prefix}tip points [gci_number]")
            return

        donation = int(params[0])
        if len(params) > 1:
            gci_index = int(params[1]) - 1
        else:
            gci_index = -1

        active_gci = list[CreditPlayer]()
        for p in server.get_active_players():
            if player.side == p.side and p.unit_type == "forward_observer":
                active_gci.append(cast(CreditPlayer, p))
        if not len(active_gci):
            player.sendChatMessage(f"There is currently no {player.side.name} GCI active on this server.")
            return
        elif len(active_gci) == 1:
            gci_index = 0

        if gci_index not in range(0, len(active_gci)):
            player.sendChatMessage(
                f"Multiple GCIs found, use \"{self.prefix}tip points GCI-number\".")
            for i, gci in enumerate(active_gci):
                player.sendChatMessage(f"{i + 1}) {gci.name}")
            return
        else:
            receiver = active_gci[gci_index]

        old_points_player = player.points
        old_points_receiver = receiver.points
        player.points -= donation
        player.audit('donation', old_points_player, f"Donation to player {receiver.name}")
        receiver.points += donation
        receiver.audit('donation', old_points_receiver, f"Donation from player {player.name}")
        player.sendChatMessage(f"You've donated {donation} credit points to GCI {receiver.name}.")
        receiver.sendChatMessage(f"Player {player.name} donated {donation} credit points to you!")
