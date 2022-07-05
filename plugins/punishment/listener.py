import asyncio
import psycopg2
from contextlib import closing
from core import EventListener, Plugin, Server, Player, Status


class PunishmentEventListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.lock = asyncio.Lock()

    def get_flight_hours(self, player: Player) -> int:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(hop_off, NOW()) - hop_on)))) / '
                               '3600, 0) AS playtime FROM statistics WHERE player_ucid = %s', (player.ucid, ))
                return cursor.fetchone()[0] if cursor.rowcount > 0 else 0
        except psycopg2.DatabaseError as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def get_punishment_points(self, player: Player) -> int:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT COALESCE(SUM(points), 0) FROM pu_events WHERE init_id = %s", (player.ucid, ))
                return int(cursor.fetchone()[0])
        except psycopg2.DatabaseError as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    async def punish(self, data: dict):
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if 'penalties' in config:
            penalty = next((item for item in config['penalties'] if item['event'] == data['eventName']), None)
            if penalty:
                initiator = server.get_player(name=data['initiator'])
                # check if there is an exemption for this user
                if 'exemptions' in config:
                    user = self.bot.get_member_by_ucid(initiator.ucid)
                    roles = [x.name for x in user.roles] if user else []
                    for e in config['exemptions']:
                        if ('ucid' in e and e['ucid'] == initiator.ucid) or ('discord' in e and e['discord'] in roles):
                            self.log.debug(f"User {initiator.name} not penalized due to exemption.")
                            return
                if 'default' in penalty:
                    points = penalty['default']
                else:
                    points = penalty['human'] if 'target' in data else penalty['AI']
                if 'target' in data and data['target'] != -1:
                    target = server.get_player(name=data['target'])
                    if 'forgive' in config:
                        target.sendChatMessage(f"{target.name}, you are a victim of a {data['eventName']} event by "
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
                # check if an action should be run immediately
                if 'action' in penalty:
                    await self.plugin.punish(server, initiator, penalty,
                                             penalty['reason'] if 'reason' in penalty else penalty['event'])
                # add the event to the database
                async with self.lock:
                    conn = self.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            cursor.execute('INSERT INTO pu_events (init_id, target_id, server_name, event, points) '
                                           'VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING', (initiator.ucid,
                                                                                                  target.ucid if
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
        server: Server = self.bot.servers[data['server_name']]
        if self.plugin.get_config(server) and server.status == Status.RUNNING:
            if data['eventName'] == 'friendly_fire':
                if data['arg1'] != -1 and data['arg1'] != data['arg3']:
                    initiator = server.get_player(id=data['arg1'])
                    target = server.get_player(id=data['arg3']) if data['arg3'] != -1 else None
                    data['initiator'] = initiator.name
                    if target:
                        data['target'] = target.name
                    # check collision
                    if data['arg2'] == initiator.unit_type:
                        data['eventName'] = 'collision_hit'
                    await self.punish(data)
            elif data['eventName'] == 'kill':
                if data['arg1'] != -1 and data['arg1'] != data['arg4'] and data['arg3'] == data['arg6']:
                    initiator = server.get_player(id=data['arg1'])
                    target = server.get_player(id=data['arg4']) if data['arg4'] != -1 else None
                    data['initiator'] = initiator.name
                    if target:
                        data['target'] = target.name
                    # check collision
                    if data['arg7'] == initiator.unit_type:
                        data['eventName'] = 'collision_kill'
                    await self.punish(data)

    async def onChatCommand(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if data['subcommand'] == 'forgive' and self.plugin.get_config(server):
            target = server.get_player(id=data['from_id'])
            if 'forgive' in config:
                async with self.lock:
                    conn = self.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            # clean the punishment table from these events
                            cursor.execute('DELETE FROM pu_events WHERE target_id = %s AND time >= (NOW() - interval '
                                           '\'%s seconds\')', (target.ucid, config['forgive']))
                            # cancel pending punishment tasks
                            cursor.execute('DELETE FROM pu_events_sdw WHERE target_id = %s AND time >= (NOW() - '
                                           'interval \'%s seconds\')', (target.ucid, config['forgive']))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        conn.rollback()
                        self.log.exception(error)
                    finally:
                        self.pool.putconn(conn)
            else:
                target.sendChatMessage('-forgive is not enabled on this server.')
        elif data['subcommand'] == 'penalty':
            player = server.get_player(id=data['from_id'])
            points = self.get_punishment_points(player)
            player.sendChatMessage(f"{player.name}, you currently have {points} penalty points.")

    async def onPlayerConnect(self, data):
        if data['id'] == 1:
            return
        # check if someone was banned on server A and tries to sneak into server B on another node
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT COUNT(*) FROM bans WHERE ucid = %s', (data['ucid'], ))
                if cursor.fetchone()[0] > 0:
                    # ban them on all servers on this node as it wasn't populated yet
                    for s in self.bot.servers.values():
                        s.sendtoDCS({
                            "command": "ban",
                            "ucid": data['ucid'],
                            "reason": "You are banned on this server."
                        })
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    async def onPlayerStart(self, data):
        if data['id'] == 1:
            return
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['id'])
        points = self.get_punishment_points(player)
        if points > 0:
            player.sendChatMessage(f"{player.name}, you currently have {points} penalty points.")
