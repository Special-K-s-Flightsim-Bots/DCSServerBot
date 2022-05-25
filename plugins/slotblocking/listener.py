import psycopg2
import re
from contextlib import closing
from core import utils, EventListener, const, Plugin
from typing import Optional, Union


class SlotBlockingListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.credits = {}

    def get_points(self, server: dict, player: dict) -> int:
        if 'restricted' in server[self.plugin_name]:
            for unit in server[self.plugin_name]['restricted']:
                if ('unit_type' in unit and unit['unit_type'] == player['unit_type']) or \
                        ('unit_name' in unit and unit['unit_name'] in player['unit_name']) or \
                        ('group_name' in unit and unit['group_name'] in player['group_name']):
                    if player['sub_slot'] == 0 and 'points' in unit:
                        return unit['points']
                    elif player['sub_slot'] > 0 and 'crew' in unit:
                        return unit['crew']
        return 0

    def get_costs(self, server: dict, player: dict) -> int:
        if 'restricted' in server[self.plugin_name]:
            for unit in server[self.plugin_name]['restricted']:
                if ('unit_type' in unit and re.match(unit['unit_type'], player['unit_type'])) or \
                        ('unit_name' in unit and re.match(unit['unit_name'], player['unit_name'])) or \
                        ('group_name' in unit and re.match(unit['group_name'], player['group_name'])):
                    if 'costs' in unit:
                        return unit['costs']
        return 0

    def get_points_per_kill(self, server: dict, data: dict) -> int:
        default = 1
        if 'points_per_kill' in server[self.plugin_name]:
            for unit in server[self.plugin_name]['points_per_kill']:
                if 'category' in unit and data['victimCategory'] == unit['category']:
                    if 'unit_type' in unit and unit['unit_type'] != data['arg5']:
                        continue
                    if 'type' in unit and ((unit['type'] == 'AI' and data['arg4'] != "-1") or (unit['type'] == 'Player' and data['arg4'] == "-1")):
                        continue
                    return unit['points']
                elif 'default' in unit:
                    default = unit['default']
        return default

    def get_player_points(self, server_name: str, player: Union[dict, int]) -> Optional[dict]:
        if isinstance(player, int):
            player = utils.get_player(self, server_name, id=player)
        if player:
            if 'points' not in player:
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('SELECT points AS points FROM sb_points WHERE campaign_id = (SELECT '
                                       'campaign_id FROM campaigns WHERE server_name = %s) AND player_ucid = %s',
                                       (server_name, player['ucid']))
                        if cursor.rowcount > 0:
                            player['points'] = cursor.fetchone()[0]
                        else:
                            player['points'] = 0
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                finally:
                    self.pool.putconn(conn)
            return player
        return None

    async def registerDCSServer(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        update = True
        if self.plugin_name not in server:
            update = False
        config = self.plugin.get_config(server)
        # only send to DCS, if not already sent by get_config()
        if update:
            self.bot.sendtoDCS(server, {'command': 'loadParams', 'plugin': self.plugin_name, 'params': config})

    async def onPlayerStart(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        config = self.plugin.get_config(server)
        if not config or data['id'] == 1 or 'ucid' not in data:
            return
        initial_points = config['initial_points'] if 'initial_points' in config else 0
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # Initialize the player with a value of 0 if a campaign is active
                cursor.execute('INSERT INTO sb_points (campaign_id, player_ucid, points) SELECT campaign_id, %s, '
                               '%s FROM campaigns WHERE server_name = %s ON CONFLICT DO NOTHING',
                               (data['ucid'], initial_points, data['server_name']))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        player = self.get_player_points(data['server_name'], data['id'])
        points = player['points'] if player else 0
        user = utils.get_member_by_ucid(self, player['ucid'])
        roles = [x.name for x in user.roles] if user else []
        self.bot.sendtoDCS(self.globals[data['server_name']],
                           {
                               'command': 'uploadUserInfo',
                               'id': data['id'],
                               'ucid': data['ucid'],
                               'points': points,
                               'roles': roles
                           })

    def update_user_points(self, server_name: str, player: dict) -> None:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE sb_points SET points = %s WHERE player_ucid = %s AND '
                               'campaign_id = (SELECT campaign_id FROM campaigns WHERE server_name = %s)',
                               (player['points'], player['ucid'], server_name))
                self.bot.sendtoDCS(self.globals[server_name],
                                   {
                                       'command': 'updateUserPoints',
                                       'ucid': player['ucid'],
                                       'points': player['points']
                                   })
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def move_to_spectators(self, server: dict, player: dict) -> None:
        self.bot.sendtoDCS(server, {
            "command": "force_player_slot",
            "playerID": player['id'],
            "sideID": 0,
            "slotID": ""
        })

    async def onPlayerChangeSlot(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        config = self.plugin.get_config(server)
        if not config:
            return
        if 'side' in data and 'use_reservations' in config and config['use_reservations']:
            player = self.get_player_points(data['server_name'],
                                            utils.get_player(self, data['server_name'], ucid=data['ucid']))
            if data['side'] != const.SIDE_SPECTATOR:
                # only pilots have to "pay" for their plane
                if int(data['sub_slot']) == 0:
                    # slot change - credit will be taken
                    costs = self.get_costs(server, data)
                    if costs > 0:
                        self.credits[player['ucid']] = costs
                        player['points'] -= costs
                        self.update_user_points(data['server_name'], player)
            elif player['ucid'] in self.credits:
                # back to spectator removes any credit
                del self.credits[player['ucid']]

    async def onGameEvent(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        config = self.plugin.get_config(server)
        if not config:
            return
        if data['eventName'] == 'kill':
            # players gain points only, if they don't kill themselves and no teamkills
            if data['arg1'] != -1 and data['arg1'] != data['arg4'] and data['arg3'] != data['arg6']:
                # Multicrew - pilot and all crew members gain points
                for player in utils.get_crew_members(self, data['server_name'], data['arg1']):
                    player = self.get_player_points(data['server_name'], player)
                    player['points'] += self.get_points_per_kill(server, data)
                    self.update_user_points(data['server_name'], player)
            # players only lose points if they weren't killed as a teamkill
            if data['arg4'] != -1 and data['arg3'] != data['arg6']:
                # if we don't use reservations, credit will be taken on kill
                player = self.get_player_points(data['server_name'], data['arg4'])
                if 'use_reservations' not in config or not config['use_reservations']:
                    player['points'] -= self.get_costs(server, player)
                    if player['points'] < 0:
                        player['points'] = 0
                    self.update_user_points(data['server_name'], player)
                elif player['ucid'] in self.credits:
                    # back to spectator removes any credit
                    del self.credits[player['ucid']]
                # if the remaining points are not enough to stay in this plane, move them back to spectators
                if player['points'] < self.get_points(server, player):
                    self.move_to_spectators(server, player)
        elif data['eventName'] == 'crash':
            # if we don't use reservations, credit will be taken on crash
            player = self.get_player_points(data['server_name'], data['arg1'])
            if 'use_reservations' not in config or not config['use_reservations']:
                player['points'] -= self.get_costs(server, player)
                if player['points'] < 0:
                    player['points'] = 0
                self.update_user_points(data['server_name'], player)
                # if the remaining points are not enough to stay in this plane, move them back to spectators
            elif player['ucid'] in self.credits:
                # back to spectator removes any credit
                del self.credits[player['ucid']]
            if player['points'] < self.get_points(server, player):
                self.move_to_spectators(server, player)
        elif data['eventName'] == 'landing':
            # pay back on landing
            player = self.get_player_points(data['server_name'], data['arg1'])
            if player['ucid'] in self.credits:
                player['points'] += self.credits[player['ucid']]
                self.update_user_points(data['server_name'], player)
                del self.credits[player['ucid']]
        elif data['eventName'] == 'takeoff':
            # credit on takeoff but don't move back to spectators
            if 'use_reservations' in config and config['use_reservations']:
                player = self.get_player_points(data['server_name'],
                                                utils.get_player(self, data['server_name'], id=data['arg1']))
                if player['ucid'] not in self.credits and int(player['sub_slot']) == 0:
                    costs = self.get_costs(server, player)
                    if costs > 0:
                        self.credits[player['ucid']] = costs
                        player['points'] -= costs
                        self.update_user_points(data['server_name'], player)
        elif data['eventName'] == 'disconnect':
            player = self.get_player_points(data['server_name'],
                                            utils.get_player(self, data['server_name'], id=data['arg1']))
            if player['ucid'] in self.credits:
                del self.credits[player['ucid']]
        elif data['eventName'] == 'mission_end':
            # give all players their credit back, if the mission ends and they are still airborne
            for ucid, points in self.credits.items():
                player = utils.get_player(self, data['server_name'], ucid=ucid)
                if player:
                    player = self.get_player_points(data['server_name'], player)
                    player['points'] += points
                    self.update_user_points(data['server_name'], player)
            self.credits = {}

    def campaign(self, command: str, server: dict) -> None:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if command == 'start':
                    cursor.execute('INSERT INTO campaigns (server_name) VALUES (%s) ON CONFLICT DO NOTHING',
                                   (server['server_name'],))
                elif command == 'stop' or command == 'reset':
                    cursor.execute('DELETE FROM sb_points WHERE campaign_id = (SELECT campaign_id FROM '
                                   'campaigns WHERE server_name = %s)', (server['server_name'],))
                    if command == 'stop':
                        cursor.execute('DELETE FROM campaigns WHERE server_name = %s', (server['server_name'],))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    async def startCampaign(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        self.campaign('start', server)

    async def stopCampaign(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        self.campaign('stop', server)

    async def resetCampaign(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        self.campaign('reset', server)

    async def addUserPoints(self, data: dict) -> None:
        player = self.get_player_points(data['server_name'],
                                        utils.get_player(self, data['server_name'], name=data['name']))
        player['points'] += data['points']
        if player['points'] < 0:
            player['points'] = 0
        self.update_user_points(data['server_name'], player)

    async def onChatCommand(self, data: dict) -> None:
        if data['subcommand'] == 'credits':
            server_name = data['server_name']
            player_id = data['from_id']
            player = self.get_player_points(server_name, player_id)
            points = player['points']
            if player['ucid'] in self.credits:
                points += self.credits[player['ucid']]
            utils.sendChatMessage(self, server_name, player_id, f"You currently have {points} credit points.")
