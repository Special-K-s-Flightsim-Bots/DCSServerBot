import asyncio
import psycopg2
from contextlib import closing
from core import utils, EventListener, Plugin
from enum import Enum, auto


class StatsType(Enum):
    USER_STATS = auto(),
    MISSION_STATS = auto()


class PunishmentEventListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.stats_type: StatsType = StatsType.USER_STATS
        self.loop = asyncio.get_event_loop()
        self.tasks = {}

    async def registerDCSServer(self, data):
        server = self.globals[data['server_name']]
        if 'configs' in self.locals:
            specific = default = None
            for element in self.locals['configs']:
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
                merged = default
                # specific settings will always overwrite default settings
                for key, value in specific.items():
                    merged[key] = value
                server[self.plugin] = merged

    def get_flight_hours(self, player: dict) -> int:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT ROUND(SUM(EXTRACT(EPOCH FROM (hop_off - hop_on)))) / 3600 AS playtime FROM '
                               'statistics WHERE player_ucid = %s AND hop_off IS NOT NULL', (player['ucid'], ))
                return cursor.fetchone()[0] if cursor.rowcount > 0 else 0
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def do_action(self, data: dict, initiator: dict, penalty: dict, punishment: dict):
        server = self.globals[data['server_name']]
        reason = penalty['reason'] if 'reason' in penalty else data['eventName']
        if punishment['action'] == 'warn':
            self.bot.sendtoDCS(server, {
                "command": "sendPopupMessage",
                "to": initiator['group_id'],
                "message": f"{initiator['name']}, you have been punished for: {reason}!"
            })
        elif punishment['action'] in ['kick', 'ban']:
            self.bot.sendtoDCS(server, {
                "command": punishment['action'],
                "ucid": initiator['ucid'],
                "reason": reason
            })
            if punishment['action'] == 'ban':
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s)',
                                       (initiator['ucid'], self.plugin, reason))
                        conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    conn.rollback()
                    self.log.exception(error)
                finally:
                    self.pool.putconn(conn)
        elif punishment['action'] == 'move_to_spec':
            self.bot.sendtoDCS(server, {
                "command": "force_player_slot",
                "playerID": initiator['id']
            })

    def sendChatMessage(self, server_name: str, id: int, message):
        self.bot.sendtoDCS(self.globals[server_name], {
            "command": "sendChatMessage",
            "to": id,
            "message": message
        })

    def add_task(self, id: int, task):
        if id not in self.tasks:
            self.tasks[id] = []
        self.tasks[id].append(task)

    def clear_tasks(self, id: int):
        if id in self.tasks:
            del self.tasks[id]

    async def punish(self, data: dict):
        config = self.globals[data['server_name']][self.plugin]
        if 'penalties' in config:
            penalty = next((item for item in config['penalties'] if item['event'] == data['eventName']), None)
            if penalty:
                if 'default' in penalty:
                    points = penalty['default']
                else:
                    points = penalty['human'] if 'target' in data else penalty['AI']
                if 'target' in data and data['target'] != -1:
                    target = utils.get_player(self, data['server_name'], name=data['target'])
                    if 'forgive' in config:
                        self.sendChatMessage(data['server_name'], target['id'],
                                             f"{target['name']}, you are a victim of a {data['eventName']} event by "
                                             f"player {data['initiator']}.\nIf you send -forgive in this chat within "
                                             f"the next {config['forgive']} seconds, you can pardon the other player.")
                else:
                    target = None
                initiator = utils.get_player(self, data['server_name'], name=data['initiator'])
                hours = self.get_flight_hours(initiator)
                if 'flightHoursWeight' in config:
                    weight = 1
                    for fhw in config['flightHoursWeight']:
                        if fhw['time'] <= hours:
                            weight = fhw['weight']
                    points = points * weight
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('INSERT INTO pu_events (init_id, target_id, event, points) VALUES (%s, %s, %s, %s)',
                                       (initiator['ucid'], target['ucid'] if target else None, data['eventName'], points))
                        cursor.execute('SELECT SUM(points) FROM pu_events WHERE init_id = %s', (initiator['ucid'], ))
                        if cursor.rowcount > 0:
                            points = cursor.fetchone()[0]
                        conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    conn.rollback()
                    self.log.exception(error)
                finally:
                    self.pool.putconn(conn)
                if 'punishments' in config:
                    for punishment in config['punishments']:
                        if points >= punishment['points']:
                            task = self.loop.call_later(punishment['delay'] if 'delay' in punishment else 0,
                                                        self.do_action, data, initiator, penalty, punishment)
                            if 'forgive' in config and target:
                                self.add_task(target['id'], task)
                                self.loop.call_later(config['forgive'], self.clear_tasks, target['id'])
                            break

    async def onGameEvent(self, data):
        if self.stats_type == StatsType.USER_STATS:
            server = self.globals[data['server_name']]
            if self.plugin in server:
                if data['eventName'] == 'friendly_fire':
                    if data['arg1'] != -1 and data['arg1'] != data['arg3']:
                        initiator = utils.get_player(self, data['server_name'], id=data['arg1'])
                        target = utils.get_player(self, data['server_name'], id=data['arg3']) if data['arg3'] != -1 else None
                        data['initiator'] = initiator['name']
                        if target:
                            data['target'] = target['name']
                        # check collision
                        if data['arg2'] == initiator['unit_type']:
                            data['eventName'] = 'collision_hit'
                        await self.punish(data)
                elif data['eventName'] == 'kill':
                    # Teamkill
                    if data['arg1'] != -1 and data['arg1'] != data['arg4'] and data['arg3'] == data['arg6']:
                        initiator = utils.get_player(self, data['server_name'], id=data['arg1'])
                        target = utils.get_player(self, data['server_name'], id=data['arg4']) if data['arg4'] != -1 else None
                        data['initiator'] = initiator['name']
                        if target:
                            data['target'] = target['name']
                        # check collision
                        if data['arg7'] == initiator['unit_type']:
                            data['eventName'] = 'collision_kill'
                        await self.punish(data)

    async def onChatMessage(self, data):
        if '-forgive' in data['message']:
            config = self.globals[data['server_name']][self.plugin]
            target = utils.get_player(self, data['server_name'], id=data['from_id'])
            if 'forgive' in config:
                # cancel pending punishment tasks
                if data['from_id'] in self.tasks:
                    for task in self.tasks[data['from_id']]:
                        task.cancel()
                    self.clear_tasks(data['from_id'])
                    self.sendChatMessage(data['server_name'], target['id'], 'Punishment cancelled.')
                # clear relevant punishments from the database
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('DELETE FROM pu_events WHERE target_id = %s AND time >= (NOW() - interval \'%s '
                                       'seconds\')', (target['ucid'], config['forgive']))
                        conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    conn.rollback()
                    self.log.exception(error)
                finally:
                    self.pool.putconn(conn)
            else:
                self.sendChatMessage(data['server_name'], target['id'], '-forgive is not enabled on this server.')
