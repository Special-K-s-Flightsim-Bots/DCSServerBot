import json
import psycopg2
from contextlib import closing
from core import utils, EventListener, DCSServerBot
from os import path
from typing import Optional


class SlotBlockingListener(EventListener):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        filename = f'./config/{self.plugin}.json'
        if path.exists(filename):
            with open(filename) as file:
                self.params = json.load(file)
        else:
            self.params = None

    def get_costs(self, server: dict, player: dict) -> int:
        for unit in server[self.plugin]['restricted']:
            if ('unit_type' in unit and unit['unit_type'] == player['unit_type']) or ('unit_name' in unit and unit['unit_name'] in player['unit_name']) or ('group_name' in unit and unit['group_name'] in player['group_name']):
                if 'costs' in unit:
                    return unit['costs']
        return 0

    def get_points(self, server, data):
        for unit in server[self.plugin]['points_per_kill']:
            if 'category' in unit and data['victimCategory'] == unit['category']:
                if 'type' in unit:
                    if (unit['type'] == 'AI' and data['arg4'] == "-1") or (unit['type'] == 'Player' and data['arg4'] != "-1"):
                        return unit['points']
                else:
                    return unit['points']
        return 1

    # Return a player from the internal list
    # TODO: change player data handling!
    def get_player(self, server_name: str, player_id: int):
        df = self.bot.player_data[server_name]
        row = df[df['id'] == player_id]
        if not row.empty:
            return df[df['id'] == player_id].to_dict('records')[0]
        else:
            return None

    def get_player_points(self, server_name: str, player_id: int) -> Optional[dict]:
        player = self.get_player(server_name, player_id)
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

    async def registerDCSServer(self, data):
        server = self.bot.globals[data['server_name']]
        if self.params:
            specific = default = None
            for element in self.params['configs']:
                if ('installation' in element and server['installation'] == element['installation']) or (
                        'server_name' in element and server['server_name'] == element['server_name']):
                    specific = element
                else:
                    default = element
            if default and not specific:
                server[self.plugin] = default
            elif specific and not default:
                server[self.plugin] = specific
            elif default and specific:
                merged = {}
                if 'restricted' in default and 'restricted' not in specific:
                    merged['restricted'] = default['restricted']
                elif 'restricted' not in default and 'restricted' in specific:
                    merged['restricted'] = specific['restricted']
                elif 'restricted' in default and 'restricted' in specific:
                    merged['restricted'] = default['restricted'] + specific['restricted']
                if 'points_per_kill' in default and 'points_per_kill' not in specific:
                    merged['points_per_kill'] = default['points_per_kill']
                elif 'points_per_kill' not in default and 'points_per_kill' in specific:
                    merged['points_per_kill'] = specific['points_per_kill']
                elif 'points_per_kill' in default and 'points_per_kill' in specific:
                    merged['points_per_kill'] = default['points_per_kill'] + specific['points_per_kill']
                server[self.plugin] = merged
            if default or specific:
                self.bot.sendtoDCS(server, {'command': 'loadParams', 'plugin': self.plugin, 'params': server[self.plugin]})

    async def onPlayerStart(self, data):
        if data['id'] == 1:
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                # Initialize the player with a value of 0 if a campaign is active
                cursor.execute('INSERT INTO sb_points (campaign_id, player_ucid, points) SELECT campaign_id, %s, '
                               '0 FROM campaigns WHERE server_name = %s ON CONFLICT DO NOTHING', (data['ucid'],
                                                                                                  data['server_name']))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            conn.rollback()
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        player = self.get_player_points(data['server_name'], data['id'])
        points = player['points'] if player else 0
        user = utils.match_user(self, data)
        roles = [x.name for x in user.roles] if user else []
        self.bot.sendtoDCS(self.bot.globals[data['server_name']],
                           {
                               'command': 'uploadUserInfo',
                               'id': data['id'],
                               'ucid': data['ucid'],
                               'points': points,
                               'roles': roles
                           })

    def update_user_points(self, server_name: str, player: dict):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE sb_points SET points = %s WHERE player_ucid = %s AND '
                               'campaign_id = (SELECT campaign_id FROM campaigns WHERE server_name = %s)',
                               (player['points'], player['ucid'], server_name))
                self.bot.sendtoDCS(self.bot.globals[server_name],
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

    async def onGameEvent(self, data):
        server = self.bot.globals[data['server_name']]
        if data['eventName'] == 'kill':
            # players gain points only, if they don't kill themselves and no teamkills
            if data['arg1'] != -1 and data['arg1'] != data['arg4'] and data['arg3'] != data['arg6']:
                player = self.get_player_points(data['server_name'], data['arg1'])
                player['points'] += self.get_points(server, data)
                self.update_user_points(data['server_name'], player)
            # players only lose points if they weren't killed as a teamkill
            if data['arg4'] != -1 and data['arg3'] != data['arg6']:
                player = self.get_player_points(data['server_name'], data['arg4'])
                player['points'] -= self.get_costs(server, player)
                self.update_user_points(data['server_name'], player)
        elif data['eventName'] == 'crash':
            player = self.get_player_points(data['server_name'], data['arg1'])
            player['points'] -= self.get_costs(server, player)
            self.update_user_points(data['server_name'], player)
