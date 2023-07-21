import asyncio
import psycopg2
from contextlib import closing
from core import EventListener, Plugin, Server, Player, Status, event, chat_command, Channel


class PunishmentEventListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.lock = asyncio.Lock()

    def _get_flight_hours(self, player: Player) -> int:
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

    def _get_punishment_points(self, player: Player) -> int:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT COALESCE(SUM(points), 0) FROM pu_events WHERE init_id = %s", (player.ucid, ))
                return int(cursor.fetchone()[0])
        except psycopg2.DatabaseError as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    async def _punish(self, data: dict):
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
                        target.sendChatMessage(
                            f"{target.name}, you are a victim of a {data['eventName']} event by player "
                            f"{data['initiator']}.\nIf you send {self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}forgive "
                            f"in this chat within the next {config['forgive']} seconds, you can pardon the other "
                            f"player.")
                else:
                    target = None
                hours = self._get_flight_hours(initiator)
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

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict):
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
                        # TODO: remove when Forrestal is fixed
                        if target is None:
                            return
                    await self._punish(data)
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
                    await self._punish(data)

    @event(name="onPlayerConnect")
    async def onPlayerConnect(self, server: Server, data: dict) -> None:
        if data['id'] == 1:
            return
        # check if someone was banned on server A and tries to sneak into server B on another node
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT reason FROM bans WHERE ucid = %s', (data['ucid'], ))
                if cursor.rowcount > 0:
                    reason = cursor.fetchone()[0]
                    # ban them on all servers on this node as it wasn't populated yet
                    for s in self.bot.servers.values():
                        s.sendtoDCS({
                            "command": "ban",
                            "ucid": data['ucid'],
                            "reason": reason
                        })
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1:
            return
        player: Player = server.get_player(id=data['id'])
        points = self._get_punishment_points(player)
        if points > 0:
            player.sendChatMessage(f"{player.name}, you currently have {points} penalty points.")

    @chat_command(name="forgive", help="forgive another user for teamhits/-kills")
    async def forgive(self, server: Server, target: Player, params: list[str]):
        config = self.plugin.get_config(server)
        if 'forgive' not in config:
            target.sendChatMessage(
                f"{self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}forgive is not enabled on this server.")
            return

        async with self.lock:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    # get the punishments
                    cursor.execute('SELECT DISTINCT init_id FROM pu_events WHERE target_id = %s '
                                   'AND time >= (NOW() - interval \'%s seconds\')',
                                   (target.ucid, config['forgive']))
                    initiators = [x[0] for x in cursor.fetchall()]
                    # there were no events, so forgive would not do anything
                    if not initiators:
                        target.sendChatMessage('There is nothing to forgive (anymore).')
                        return
                    # clean the punishment table from these events
                    cursor.execute('DELETE FROM pu_events WHERE target_id = %s AND time >= (NOW() - interval '
                                   '\'%s seconds\')', (target.ucid, config['forgive']))
                    # cancel pending punishment tasks
                    cursor.execute('DELETE FROM pu_events_sdw WHERE target_id = %s AND time >= (NOW() - '
                                   'interval \'%s seconds\')', (target.ucid, config['forgive']))
                    conn.commit()
                    names = []
                    for initiator in initiators:
                        player = self.bot.get_player_by_ucid(initiator)
                        if player:
                            names.append(player.name)
                            player.sendChatMessage(
                                f'You have been forgiven by {target.name} and will not be punished '
                                f'for your recent actions.')
                            await server.get_channel(Channel.ADMIN).send(
                                f"Player {target.name} forgave player {player.name} (ucid={player.ucid}) for his "
                                f"recent actions. Punishment points cleared.")
                    if not names:
                        names = ['another player']
                    target.sendChatMessage(
                        'You have chosen to forgive {} for their actions.'.format(', '.join(names)))
            except (Exception, psycopg2.DatabaseError) as error:
                conn.rollback()
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)

    @chat_command(name="penalty", help="displays your penalty points")
    async def penalty(self, server: Server, player: Player, params: list[str]):
        points = self._get_punishment_points(player)
        player.sendChatMessage(f"{player.name}, you currently have {points} penalty points.")
