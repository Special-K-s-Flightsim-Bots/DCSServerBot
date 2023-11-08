import asyncio
from contextlib import closing
from typing import Optional

from core import EventListener, Plugin, Server, Player, Status, event, chat_command
from plugins.competitive.commands import Competitive


class PunishmentEventListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.lock = asyncio.Lock()

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        # make sure the config cache is re-read on mission changes
        self.plugin.get_config(server, use_cache=False)

    def _get_flight_hours(self, player: Player) -> int:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(hop_off, NOW()) - hop_on)))) / 3600, 0) 
                           AS playtime 
                    FROM statistics WHERE player_ucid = %s
                """, (player.ucid, ))
                return cursor.fetchone()[0] if cursor.rowcount > 0 else 0

    def _get_punishment_points(self, player: Player) -> int:
        with self.pool.connection() as conn:
            return conn.execute("SELECT COALESCE(SUM(points), 0) FROM pu_events WHERE init_id = %s",
                                (player.ucid, )).fetchone()[0]

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
                # apply flight hours to points
                hours = self._get_flight_hours(initiator)
                if 'flightHoursWeight' in config:
                    weight = 1
                    for fhw in config['flightHoursWeight']:
                        if fhw['time'] <= hours:
                            weight = fhw['weight']
                    points = points * weight
                # check if an action should be run immediately
                if 'action' in penalty:
                    await self.plugin.punish(server, initiator.ucid, penalty,
                                             penalty['reason'] if 'reason' in penalty else penalty['event'])
                # ignore events where no punishment points were given
                if points == 0:
                    return
                if 'target' in data and data['target'] != -1:
                    target = server.get_player(name=data['target'])
                    if 'forgive' in config:
                        target.sendChatMessage(f"{target.name}, you are a victim of a {data['eventName']} event by "
                                               f"player {data['initiator']}.\nIf you send {self.prefix}forgive in this "
                                               f"chat within the next {config['forgive']} seconds, you can pardon the "
                                               f"other player.")
                else:
                    target = None
                # add the event to the database
                async with self.lock:
                    with self.pool.connection() as conn:
                        with conn.transaction():
                            conn.execute("""
                                INSERT INTO pu_events (init_id, target_id, server_name, event, points) 
                                VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                            """, (initiator.ucid, target.ucid if target else None, data['server_name'],
                                  data['eventName'], points))

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict):
        # check if we have the Competitive plugin enabled and a match is on
        competitive: Optional[Competitive] = self.bot.cogs.get('Competitive')
        if competitive:
            player: Player = server.get_player(id=data['arg1'])
            if competitive.eventlistener.in_match[server].get(player.ucid):
                return
        if self.plugin.get_config(server) and server.status == Status.RUNNING:
            if data['eventName'] == 'friendly_fire':
                if data['arg1'] != -1 and data['arg1'] != data['arg3']:
                    initiator = server.get_player(id=data['arg1'])
                    target = server.get_player(id=data['arg3']) if data['arg3'] != -1 else None
                    data['initiator'] = initiator.name
                    if target:
                        data['target'] = target.name
                    # check collision
                    if data['arg2'] == initiator.unit_display_name:
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
                    if data['arg7'] == initiator.unit_display_name:
                        data['eventName'] = 'collision_kill'
                    await self._punish(data)

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
            target.sendChatMessage(f'{self.prefix}forgive is not enabled on this server.')
            return

        async with self.lock:
            with self.pool.connection() as conn:
                with conn.transaction():
                    with closing(conn.cursor()) as cursor:
                        # get the punishments
                        initiators = [
                            x[0] for x in cursor.execute("""
                                SELECT DISTINCT init_id 
                                FROM pu_events 
                                WHERE target_id = %s AND time >= (timezone('utc', now())  - interval '%s seconds')
                            """, (target.ucid, config['forgive'])).fetchall()
                        ]
                        # there were no events, so forgive would not do anything
                        if not initiators:
                            target.sendChatMessage('There is nothing to forgive (anymore).')
                            return
                        # clean the punishment table from these events
                        cursor.execute("""
                            DELETE FROM pu_events 
                            WHERE target_id = %s AND time >= (timezone('utc', now()) - interval '%s seconds')
                        """, (target.ucid, config['forgive']))
                        # cancel pending punishment tasks
                        cursor.execute("""
                            DELETE FROM pu_events_sdw 
                            WHERE target_id = %s AND time >= (timezone('utc', now()) - interval '%s seconds')
                        """, (target.ucid, config['forgive']))
                        names = []
                        for initiator in initiators:
                            player = self.bot.get_player_by_ucid(initiator)
                            if player:
                                names.append(player.name)
                                player.sendChatMessage(
                                    f'You have been forgiven by {target.name} and will not be punished '
                                    f'for your recent actions.')
                        if not names:
                            names = ['another player']
                        target.sendChatMessage(
                            'You have chosen to forgive {} for their actions.'.format(', '.join(names)))

    @chat_command(name="penalty", help="displays your penalty points")
    async def penalty(self, server: Server, player: Player, params: list[str]):
        points = self._get_punishment_points(player)
        player.sendChatMessage(f"{player.name}, you currently have {points} penalty points.")
