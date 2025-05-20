import asyncio

from core import EventListener, Server, Player, event, chat_command, get_translation, ChatCommand, Channel
from plugins.competitive.commands import Competitive
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Punishment

_ = get_translation(__name__.split('.')[1])


class PunishmentEventListener(EventListener["Punishment"]):

    def __init__(self, plugin: "Punishment"):
        super().__init__(plugin)
        self.lock = asyncio.Lock()
        self.active_servers: set[str] = set()
        self.pending_forgiveness: dict[tuple[str, str], list[asyncio.Task]] = {}

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

    async def _provide_forgiveness_window(self, data: dict, window: int):
        try:
            await asyncio.wait_for(asyncio.Future(), timeout=window)
        except (TimeoutError, asyncio.TimeoutError):
            asyncio.create_task(self._punish(data))

    async def _punish(self, data: dict):
        initiator = data['initiator']
        target = data.get('target')
        async with self.lock:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO pu_events (init_id, target_id, server_name, event, points) 
                        VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                    """, (initiator.ucid, target.ucid if target else None, data['server_name'], data['eventName'],
                          data['points']))

    async def _check_punishment(self, data: dict):
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)

        # no penalty configured for this event
        penalty = next((item for item in config['penalties'] if item['event'] == data['eventName']), None)
        if not penalty:
            return

        initiator = data['initiator']
        # check if there is an exemption for this user
        if initiator.check_exemptions(config.get('exemptions', {})):
            self.log.debug(f"User {initiator.name} not penalized due to exemption.")
            return

        if 'default' in penalty:
            points = penalty['default']
        else:
            points = penalty.get('human', 0) if 'target' in data else penalty.get('AI', 0)
        # apply flight hours to points
        hours = await self._get_flight_hours(initiator)
        if 'flightHoursWeight' in config:
            weight = 1
            for fhw in config['flightHoursWeight']:
                if fhw['time'] <= hours:
                    weight = fhw['weight']
            points = points * weight

        # check if a punishment has to happen
        if 'action' in penalty or points > 0:
            target = data.get('target')
            data['points'] = points
            if target and 'forgive' in config:
                async with self.lock:
                    window = config['forgive']
                    tasks = self.pending_forgiveness.get((initiator.ucid, target.ucid))
                    if not tasks:
                        await target.sendUserMessage(
                            _("{victim}, you are a victim of a friendly-fire event by player {offender}.\n"
                              "If you send {prefix}forgive in chat within the next {time} seconds, "
                              "you can pardon the other player.").format(
                                victim=target.name, event=data['eventName'], offender=initiator.name,
                                prefix=self.prefix, time=window))
                        tasks = self.pending_forgiveness[(initiator.ucid, target.ucid)] = []
                    tasks.append(asyncio.create_task(self._provide_forgiveness_window(data, window)))
            else:
                asyncio.create_task(self._punish(data))

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict):
        config = self.plugin.get_config(server)
        # only filter FF and TKs
        if not config or not config.get('penalties') or data['eventName'] not in ['friendly_fire', 'kill']:
            return
        initiator = server.get_player(id=data['arg1'])
        # we don't care about AI kills
        if not initiator:
            return

        # check if we have the Competitive plugin enabled and a match is on
        competitive: Optional[Competitive] = self.bot.cogs.get('Competitive')
        if competitive:
            if competitive.eventlistener.in_match.get(server.name, {}).get(initiator.ucid):
                return

        # generate the event structure
        event = {
            "server_name": server.name,
            "initiator": initiator
        }

        # check the events
        if data['eventName'] == 'friendly_fire' and data['arg1'] != data['arg3']:
            target = server.get_player(id=data['arg3'])
            if target:
                event['target'] = target
            # check collision
            if data['arg2'] == initiator.unit_display_name:
                event['eventName'] = 'collision_hit'
                # TODO: remove when Forrestal is fixed
                if target is None:
                    return
            else:
                event['eventName'] = 'friendly_fire'
            asyncio.create_task(self._check_punishment(event))

        elif data['eventName'] == 'kill' and data['arg1'] != data['arg4'] and data['arg3'] == data['arg6']:
            target = server.get_player(id=data['arg4'])
            if target:
                event['target'] = target
            # check collision
            if data['arg7'] == initiator.unit_display_name:
                event['eventName'] = 'collision_kill'
            else:
                event['eventName'] = 'kill'
            asyncio.create_task(self._check_punishment(event))

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
            asyncio.create_task(self._send_player_points(player))

    @event(name="disablePunishments")
    async def disablePunishments(self, server: Server, _: dict) -> None:
        self.active_servers.discard(server.name)

    @chat_command(name="forgive", help=_("forgive another user for their infraction"))
    async def forgive(self, server: Server, player: Player, params: list[str]):
        async with self.lock:
            initiators = []
            all_tasks = []
            # search the initiators and tasks
            for (initiator, target), tasks in self.pending_forgiveness.items():
                if target != player.ucid:
                    continue
                all_tasks.extend(tasks)
                initiators.append(initiator)
            if not initiators:
                await player.sendChatMessage(_('There is nothing to forgive (maybe too late?)'))
                return
            # wait for all tasks to be finished
            for task in all_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            for initiator in initiators:
                self.pending_forgiveness.pop((initiator, player.ucid), None)
                offender = server.get_player(ucid=initiator)
                if offender:
                    await offender.sendUserMessage(
                        _("{offender}, You have been forgiven by {victim} and you will not be punished for your "
                          "recent actions.").format(offender=offender.name, victim=player.name))
                    await player.sendChatMessage(_('You have chosen to forgive {offender} for their actions.').format(
                        offender=offender.name))
                    events_channel = self.bot.get_channel(server.channels.get(Channel.EVENTS, -1))
                    if events_channel:
                        await events_channel.send(
                            "```" + _("Player {victim} forgave player {offender} for their actions").format(
                                victim=player.display_name, offender=offender.display_name) + "```"
                        )

    @chat_command(name="penalty", help=_("displays your penalty points"))
    async def penalty(self, server: Server, player: Player, params: list[str]):
        await self._send_player_points(player)
