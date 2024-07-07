import asyncio

from core import EventListener, Plugin, Server, Player, Status, event, chat_command, get_translation, ChatCommand
from plugins.competitive.commands import Competitive
from typing import Optional

_ = get_translation(__name__.split('.')[1])


class PunishmentEventListener(EventListener):

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.lock = asyncio.Lock()
        self.active_servers: set[str] = set()

    async def processEvent(self, name: str, server: Server, data: dict) -> None:
        try:
            if name == 'registerDCSServer' or server.name in self.active_servers:
                await super().processEvent(name, server, data)
        except Exception as ex:
            self.log.exception(ex)

    async def can_run(self, command: ChatCommand, server: Server, player: Player) -> bool:
        if server.name not in self.active_servers:
            return False
        elif command.name == 'forgive':
            return self.plugin.get_config(server).get('forgive') is not None
        return await super().can_run(command, server, player)

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, _: dict) -> None:
        if self.get_config(server).get('enabled', True):
            self.active_servers.add(server.name)
        else:
            self.active_servers.discard(server.name)

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, _: dict) -> None:
        # make sure the config cache is re-read on mission changes
        self.plugin.get_config(server, use_cache=False)

    async def _get_flight_hours(self, player: Player) -> int:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(hop_off, now() AT TIME ZONE 'utc') - hop_on)))) / 3600, 0) 
                       AS playtime 
                FROM statistics WHERE player_ucid = %s
            """, (player.ucid, ))
            return (await cursor.fetchone())[0] if cursor.rowcount > 0 else 0

    async def _get_punishment_points(self, player: Player) -> int:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("SELECT COALESCE(SUM(points), 0) FROM pu_events WHERE init_id = %s",
                                        (player.ucid, ))
            return (await cursor.fetchone())[0]

    async def _punish(self, data: dict):
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if 'penalties' in config:
            penalty = next((item for item in config['penalties'] if item['event'] == data['eventName']), None)
            if penalty:
                initiator = server.get_player(name=data['initiator'])
                # check if there is an exemption for this user
                if initiator.check_exemptions(config.get('exemptions', {})):
                    self.log.debug(f"User {initiator.name} not penalized due to exemption.")
                    return
                if 'default' in penalty:
                    points = penalty['default']
                else:
                    points = penalty['human'] if 'target' in data else penalty.get('AI', 0)
                # apply flight hours to points
                hours = await self._get_flight_hours(initiator)
                if 'flightHoursWeight' in config:
                    weight = 1
                    for fhw in config['flightHoursWeight']:
                        if fhw['time'] <= hours:
                            weight = fhw['weight']
                    points = points * weight
                # check if an action should be run immediately
                if 'action' in penalty:
                    # noinspection PyUnresolvedReferences
                    # noinspection PyAsyncCall
                    asyncio.create_task(self.plugin.punish(server, initiator.ucid, penalty,
                                                           penalty['reason'] if 'reason' in penalty else penalty['event']))
                # ignore events where no punishment points were given
                if points == 0:
                    return
                if 'target' in data and data['target'] != -1:
                    target = server.get_player(name=data['target'])
                    if 'forgive' in config:
                        await target.sendUserMessage(
                            _("{victim}, you are a victim of a {event} event by player {offender}.\n"
                              "If you send {prefix}forgive in chat within the next {time} seconds, "
                              "you can pardon the other player.").format(
                                victim=target.name, event=data['eventName'], offender=data['initiator'],
                                prefix=self.prefix, time=config['forgive']))
                else:
                    target = None
                # add the event to the database
                async with self.lock:
                    async with self.apool.connection() as conn:
                        async with conn.transaction():
                            await conn.execute("""
                                INSERT INTO pu_events (init_id, target_id, server_name, event, points) 
                                VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                            """, (initiator.ucid, target.ucid if target else None, data['server_name'],
                                  data['eventName'], points))

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict):
        if data['eventName'] not in ['friendly_fire', 'kill']:
            return
        # check if we have the Competitive plugin enabled and a match is on
        if data['arg1'] != -1:
            competitive: Optional[Competitive] = self.bot.cogs.get('Competitive')
            if competitive:
                player: Player = server.get_player(id=data['arg1'])
                if not player or competitive.eventlistener.in_match[server.name].get(player.ucid):
                    return
        if self.plugin.get_config(server) and server.status == Status.RUNNING:
            if data['eventName'] == 'friendly_fire':
                if data['arg1'] != -1 and data['arg1'] != data['arg3']:
                    initiator = server.get_player(id=data['arg1'])
                    if not initiator:
                        return
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
                    # noinspection PyAsyncCall
                    asyncio.create_task(self._punish(data))
            elif data['eventName'] == 'kill':
                if data['arg1'] != -1 and data['arg1'] != data['arg4'] and data['arg3'] == data['arg6']:
                    initiator = server.get_player(id=data['arg1'])
                    if not initiator:
                        return
                    target = server.get_player(id=data['arg4']) if data['arg4'] != -1 else None
                    data['initiator'] = initiator.name
                    if target:
                        data['target'] = target.name
                    # check collision
                    if data['arg7'] == initiator.unit_display_name:
                        data['eventName'] = 'collision_kill'
                    # noinspection PyAsyncCall
                    asyncio.create_task(self._punish(data))

    async def _send_player_points(self, player: Player):
        points = await self._get_punishment_points(player)
        if points > 0:
            await player.sendChatMessage(_("{name}, you have {points} punishment points.").format(
                name=player.name, points=points))

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if player:
            # noinspection PyAsyncCall
            asyncio.create_task(self._send_player_points(player))

    @event(name="disablePunishments")
    async def disablePunishments(self, server: Server, _: dict) -> None:
        self.active_servers.discard(server.name)

    @chat_command(name="forgive", help=_("forgive another user for their infraction"))
    async def forgive(self, server: Server, target: Player, params: list[str]):
        config = self.plugin.get_config(server)
        forgive = config.get('forgive', 30)
        async with self.lock:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    # get the punishments
                    cursor = await conn.execute(f"""
                        SELECT DISTINCT init_id 
                        FROM pu_events 
                        WHERE target_id = %s AND time >= (timezone('utc', now()) - interval '{forgive} seconds')
                    """, (target.ucid, ))
                    initiators = [x[0] async for x in cursor]
                    # there were no events, so forgive would not do anything
                    if not initiators:
                        await target.sendChatMessage(_('There is nothing to forgive (maybe too late?)'))
                        return
                    # clean the punishment table from these events
                    await conn.execute(f"""
                        DELETE FROM pu_events 
                        WHERE target_id = %s AND time >= (timezone('utc', now()) - interval '{forgive} seconds')
                    """, (target.ucid, ))
                    # cancel pending punishment tasks
                    await conn.execute(f"""
                        DELETE FROM pu_events_sdw 
                        WHERE target_id = %s AND time >= (timezone('utc', now()) - interval '{forgive} seconds')
                    """, (target.ucid, ))
            names = []
            for initiator in initiators:
                player = server.get_player(ucid=initiator)
                if player:
                    names.append(player.name)
                    await player.sendUserMessage(_("{offender}, You have been forgiven by {victim} and you will not be "
                                                   "punished for your recent actions.").format(offender=player.name,
                                                                                               victim=target.name))
            if not names:
                names = ['another player']
            await target.sendChatMessage(
                _('You have chosen to forgive {} for their actions.').format(', '.join(names)))

    @chat_command(name="penalty", help=_("displays your penalty points"))
    async def penalty(self, server: Server, player: Player, params: list[str]):
        await self._send_player_points(player)
