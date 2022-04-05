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
        self.lock = asyncio.Lock()

    async def registerDCSServer(self, data: dict):
        server = self.globals[data['server_name']]
        if 'configs' in self.locals:
            specific = default = None
            for element in self.locals['configs']:
                if 'installation' in element or 'server_name' in element:
                    if ('installation' in element and server['installation'] == element['installation']) or \
                            ('server_name' in element and server['server_name'] == element['server_name']):
                        specific = element
                else:
                    default = element
            if default and not specific:
                server[self.plugin] = default
            elif specific and not default:
                server[self.plugin] = specific
            elif default and specific:
                merged = default.copy()
                # specific settings will always overwrite default settings
                for key, value in specific.items():
                    merged[key] = value
                server[self.plugin] = merged

    def get_flight_hours(self, player: dict) -> int:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(hop_off, NOW()) - hop_on)))) / '
                               '3600, 0) AS playtime FROM statistics WHERE player_ucid = %s', (player['ucid'], ))
                return cursor.fetchone()[0] if cursor.rowcount > 0 else 0
        except psycopg2.DatabaseError as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def get_punishment_points(self, player: dict) -> int:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT COALESCE(SUM(points), 0) FROM pu_events WHERE init_id = %s", (player['ucid'], ))
                return int(cursor.fetchone()[0])
        except psycopg2.DatabaseError as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    async def punish(self, data: dict):
        config = self.globals[data['server_name']][self.plugin]
        if 'penalties' in config:
            penalty = next((item for item in config['penalties'] if item['event'] == data['eventName']), None)
            if penalty:
                initiator = utils.get_player(self, data['server_name'], name=data['initiator'])
                # check if there is an exemption for this user
                if 'exemptions' in config:
                    user = utils.match_user(self, initiator)
                    roles = [x.name for x in user.roles] if user else []
                    for e in config['exemptions']:
                        if ('ucid' in e and e['ucid'] == initiator['ucid']) or ('discord' in e and e['discord'] in roles):
                            self.log.debug(f"User {initiator['name']} not penalized due to exemption.")
                            return
                if 'default' in penalty:
                    points = penalty['default']
                else:
                    points = penalty['human'] if 'target' in data else penalty['AI']
                if 'target' in data and data['target'] != -1:
                    target = utils.get_player(self, data['server_name'], name=data['target'])
                    if 'forgive' in config:
                        utils.sendChatMessage(self, data['server_name'], target['id'],
                                             f"{target['name']}, you are a victim of a {data['eventName']} event by "
                                             f"player {data['initiator']}.\nIf you send -forgive in this chat within "
                                             f"the next {config['forgive']} seconds, you can pardon the other player.")
                else:
                    target = None
                hours = self.get_flight_hours(initiator)
                if 'flightHoursWeight' in config:
                    weight = 1
                    for fhw in config['flightHoursWeight']:
                        if fhw['time'] <= hours:
                            weight = fhw['weight']
                    points = points * weight
                async with self.lock:
                    conn = self.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            cursor.execute('INSERT INTO pu_events (init_id, target_id, server_name, event, points) '
                                           'VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING', (initiator['ucid'],
                                                                                                  target['ucid'] if
                                                                                                  target else None,
                                                                                                  data['server_name'],
                                                                                                  data['eventName'],
                                                                                                  points))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        conn.rollback()
                        self.log.exception(error)
                    finally:
                        self.pool.putconn(conn)

    async def onGameEvent(self, data: dict):
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

    async def onChatCommand(self, data: dict):
        if data['message'].startswith('-forgive') and self.plugin in self.globals[data['server_name']]:
            config = self.globals[data['server_name']][self.plugin]
            target = utils.get_player(self, data['server_name'], id=data['from_id'])
            if 'forgive' in config:
                async with self.lock:
                    conn = self.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            # clean the punishment table from these events
                            cursor.execute('DELETE FROM pu_events WHERE target_id = %s AND time >= (NOW() - interval '
                                           '\'%s seconds\')', (target['ucid'], config['forgive']))
                            # cancel pending punishment tasks
                            cursor.execute('DELETE FROM pu_events_sdw WHERE target_id = %s AND time >= (NOW() - '
                                           'interval \'%s seconds\')', (target['ucid'], config['forgive']))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        conn.rollback()
                        self.log.exception(error)
                    finally:
                        self.pool.putconn(conn)
            else:
                utils.sendChatMessage(self, data['server_name'], target['id'], '-forgive is not enabled on this server.')
        elif data['message'].startswith('-penalty'):
            player = utils.get_player(self, data['server_name'], id=data['from_id'])
            points = self.get_punishment_points(player)
            utils.sendChatMessage(self, data['server_name'], data['from_id'],
                                  f"{player['name']}, you currently have {points} penalty points.")

    async def onPlayerConnect(self, data):
        # check if someone was banned on server A and tries to sneak into server B on another node
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT COUNT(*) FROM bans WHERE ucid = %s', (data['ucid'], ))
                if cursor.fetchone()[0] > 0:
                    # ban them on all servers on this node as it wasn't populated yet
                    for s in self.globals.values():
                        self.bot.sendtoDCS(s, {
                            "command": "ban",
                            "ucid": data['ucid'],
                            "reason": "You are banned on this server."
                        })
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    async def onPlayerStart(self, data):
        # the server owner don't need to get an update of their stats
        if data['id'] == 1:
            return
        points = self.get_punishment_points(data)
        if points > 0:
            utils.sendChatMessage(self, data['server_name'], data['id'], f"{data['name']}, you currently have {points} penalty points.")
