import asyncio
import time

from contextlib import suppress
from core import EventListener, Server, Player, event, chat_command, get_translation, ChatCommand, Channel, \
    ThreadSafeDict, Coalition
from plugins.competitive.commands import Competitive
from typing import TYPE_CHECKING, cast

from ..mission.commands import Mission
from ..mission.listener import MissionEventListener
from ..userstats.listener import UserStatisticsEventListener

if TYPE_CHECKING:
    from .commands import Punishment

_ = get_translation(__name__.split('.')[1])


class PunishmentEventListener(EventListener["Punishment"]):

    def __init__(self, plugin: "Punishment"):
        super().__init__(plugin)
        self.lock = asyncio.Lock()
        self.active_servers: set[str] = set()
        self.pending_forgiveness: dict[tuple[str, str], list[asyncio.Task]] = {}
        self.pending_kill: dict[str, tuple[int, str | None, str | None, str | None]] = ThreadSafeDict()
        self.disconnected: dict[str, tuple[int, str | None, str | None]] = ThreadSafeDict()

    async def shutdown(self) -> None:
        for tasks in self.pending_forgiveness.values():
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

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
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if self.get_config(server).get('enabled', True):
            self.active_servers.add(server.name)
            # initialize players on bot restarts
            if 'sync' in data['channel']:
                for player in data.get('players', []):
                    if player['id'] == 1:
                        continue
                    if int(player['slot']) > 0:
                        self.pending_kill[player['ucid']] = (-1, None, None, None)
        else:
            self.active_servers.discard(server.name)

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, _: dict) -> None:
        # make sure the config cache is re-read on mission changes
        self.plugin.get_config(server, use_cache=False)

    async def _get_flight_hours(self, player: Player) -> int:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("SELECT COALESCE(SUM(playtime), 0) FROM mv_statistics WHERE player_ucid = %s",
                                        (player.ucid, ))
            return (await cursor.fetchone())[0]

    async def _get_punishment_points(self, player: Player) -> int:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("SELECT COALESCE(SUM(points), 0) FROM pu_events WHERE init_id = %s",
                                        (player.ucid, ))
            return (await cursor.fetchone())[0]

    async def _provide_forgiveness_window(self, data: dict, window: int):
        try:
            # wait for a '-forgive' to happen
            await asyncio.sleep(window)
            # it did not happen -> fulfill the punishment
            asyncio.create_task(self._punish(data))
        except asyncio.CancelledError:
            # it did happen -> do nothing
            pass

    async def _punish(self, data: dict):
        initiator = data['initiator']
        target = data.get('target')
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
                window = config['forgive']
                key = (initiator.ucid, target.ucid)
                inform_victim = False

                async with self.lock:
                    tasks = self.pending_forgiveness.get(key)
                    if not tasks:
                        inform_victim = True
                        tasks = self.pending_forgiveness[key] = []
                    tasks.append(asyncio.create_task(self._provide_forgiveness_window(data, window)))

                if inform_victim:
                    asyncio.create_task(target.sendUserMessage(
                        _("{victim}, you are a victim of a friendly-fire event by player {offender}.\n"
                          "If you send {prefix}forgive in chat within the next {time} seconds, "
                          "you can pardon the other player.").format(
                            victim=target.name, event=data['eventName'], offender=initiator.name,
                            prefix=self.prefix, time=window)))

            else:
                asyncio.create_task(self._punish(data))

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict):
        config = self.get_config(server)

        # only filter FF and TKs
        if not config or not config.get('penalties') or data['eventName'] not in ['friendly_fire', 'kill', 'disconnect']:
            return

        initiator = server.get_player(id=data['arg1'])
        # we don't care about AI kills
        if not initiator:
            return

        # check if we have the Competitive plugin enabled and a match is on
        competitive: Competitive | None = self.bot.cogs.get('Competitive')
        if competitive:
            if competitive.eventlistener.in_match.get(server.name, {}).get(initiator.ucid):
                return

        # generate the event structure
        evt = {
            "server_name": server.name,
            "initiator": initiator
        }

        # check the events
        if data['eventName'] == 'friendly_fire' and data['arg1'] != data['arg3']:
            target = server.get_player(id=data['arg3'])
            if target:
                evt['target'] = target
            # check collision
            if data['arg2'] == initiator.unit_display_name:
                evt['eventName'] = 'collision_hit'
                # TODO: remove when Forrestal is fixed
                if target is None:
                    return
            else:
                evt['eventName'] = 'friendly_fire'
            asyncio.create_task(self._check_punishment(evt))

        elif data['eventName'] == 'kill' and data['arg1'] != data['arg4'] and data['arg3'] == data['arg6']:
            target = server.get_player(id=data['arg4'])
            if target:
                evt['target'] = target
            # check collision
            if data['arg7'] == initiator.unit_display_name:
                evt['eventName'] = 'collision_kill'
            else:
                evt['eventName'] = 'kill'
            asyncio.create_task(self._check_punishment(evt))

        elif data['eventName'] == 'disconnect':
            shot_time, shooter_id, weapon, s_event = self.pending_kill.pop(initiator.ucid, (-1, None, None, None))
            if not shot_time or shot_time == -1:
                return

            delta_time = int(time.time()) - shot_time
            if delta_time < config.get('reslot_window', 60):
                # the kill will be given to the opponent
                asyncio.create_task(self._give_kill(server, shooter_id, initiator.ucid, weapon))
            elif s_event == 'S_EVENT_HIT' and delta_time < config.get('survival_window', 300):
                # reslotting of a damaged plane will be treated as a kill
                asyncio.create_task(self._give_kill(server, shooter_id, initiator.ucid, weapon))
            else:
                return
            # mark the event for a potential penalty
            self.disconnected[initiator.ucid] = (int(time.time()), shooter_id, weapon)

    async def _send_player_points(self, player: Player):
        points = await self._get_punishment_points(player)
        if points > 0:
            asyncio.create_task(player.sendChatMessage(_("{name}, you have {points} punishment points.").format(
                name=player.name, points=points)))

    async def _give_kill(self, server: Server, init_id: str | None, target_id: str, weapon: str) -> None:
        return

        if init_id is None or target_id is None:
            return

        initiator = server.get_player(ucid=init_id)
        victim = server.get_player(ucid=target_id)

        # update the database
        try:
            mission = cast(Mission, self.bot.cogs.get('Mission'))
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    if initiator:
                        # give PvP kill
                        await conn.execute(UserStatisticsEventListener.SQL_EVENT_UPDATES['pvp_planes'],
                                           (server.mission_id, init_id))
                    if victim:
                        # count deaths
                        await conn.execute(UserStatisticsEventListener.SQL_EVENT_UPDATES['deaths_pvp_planes'],
                                           (server.mission_id, target_id))

            # inform players
            message = MissionEventListener.EVENT_TEXTS[initiator.side]['kill'].format(
                ('player ' + initiator.name) if initiator is not None else 'AI',
                initiator.unit_type if initiator else 'unknown',
                victim.side.name,
                'player ' + victim.name,
                victim.unit_type,
                weapon or "the reslot-hammer"
            )
            mission.eventlistener.send_dcs_event(server, initiator.side, message)
            message = "{} {} in {} killed {} {} in {} with {}.".format(
                initiator.side.name,
                ('player ' + initiator.name) if initiator is not None else 'AI',
                initiator.unit_type if initiator else 'unknown',
                victim.side.name,
                'player ' + victim.name,
                victim.unit_type,
                weapon or "the reslot-hammer"
            )
            asyncio.create_task(server.sendChatMessage(Coalition.ALL, message))
        except Exception as ex:
            self.log.exception(ex)

    @event(name="onPlayerConnect")
    async def onPlayerConnect(self, server: Server, data: dict) -> None:
        if data['ucid'] not in self.disconnected:
            return

        self.log.debug("### onPlayerConnect() - disconnected set")
        config = self.get_config(server)
        _time, shooter_id, weapon = self.disconnected.pop(data['ucid'])
        # we do not punish if the disconnect was longer than reslot_window seconds ago
        delta_time = int(time.time()) - _time
        if delta_time > config.get('reslot_window', 60):
            self.log.debug("### onPlayerConnect() - after window, ok")
            return

        self.log.debug("### onPlayerConnect() - inside window, not ok")
        player = server.get_player(ucid=data['ucid'])
        evt = {
            "eventName": "reslot",
            "server_name": server.name,
            "initiator": player
        }
        self.log.debug("### onPlayerConnect() - send punish event")
        asyncio.create_task(self._check_punishment(evt))
        admin = self.bot.get_admin_channel(server)
        if admin:
            self.log.debug("### onPlayerConnect() - inform admins")
            asyncio.create_task(admin.send(
                "```" + _("Player {} ({}) disconnected and reconnected {} seconds after being shot at.").format(
                    player.name, player.ucid, delta_time) + "```"))

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

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        # airstarts or takeoffs reset the reslot timer directly on birth
        if (data['eventName'] == 'S_EVENT_BIRTH' and not data.get('place')) or data['eventName'] == 'S_EVENT_TAKEOFF':
            player = server.get_player(name=data.get('initiator', {}).get('name'))
            if player:
                self.pending_kill[player.ucid] = (-1, None, None, None)

        elif data['eventName'] in ['S_EVENT_SHOT', 'S_EVENT_HIT']:
            initiator = server.get_player(name=data.get('initiator', {}).get('name'))
            victim = server.get_player(name=data.get('target', {}).get('name'))
            # ignore teamkills
            if (
                    data.get('initiator', {}).get('coalition', 0) ==
                    data.get('target', {}).get('coalition', 0)
            ):
                return
            # we only care for real players here
            if victim and victim.ucid in self.pending_kill:
                self.pending_kill[victim.ucid] = (
                    int(time.time()),
                    initiator.ucid if initiator else None,
                    data.get('weapon', {}).get('name', 'Gun'),
                    data['eventName']
                )

        elif data['eventName'] == 'S_EVENT_LAND':
            player = server.get_player(name=data.get('initiator', {}).get('name'))
            if player and player.sub_slot == 0:
                self.pending_kill.pop(player.ucid, None)

        elif data['eventName'] == 'S_EVENT_KILL':
            player = server.get_player(name=data.get('target', {}).get('name'))
            if player:
                self.pending_kill.pop(player.ucid, None)

        elif data['eventName'] in ['S_EVENT_CRASH', 'S_EVENT_EJECTION']:
            self.log.debug(f"### onMissionEvent() - {data['eventName']} received")
            config = self.get_config(server)
            player = server.get_player(name=data.get('initiator', {}).get('name'))
            if not player or player.sub_slot > 0:
                self.log.debug(f"### onMissionEvent() - no player, ignore")
                return

            shot_time, shooter_id, weapon, s_event = self.pending_kill.pop(player.ucid, (-1, None, None, None))
            delta_time = int(time.time()) - shot_time
            # no shot event registered or too old already
            if shot_time is None or shot_time == -1:
                self.log.debug(f"### onMissionEvent() - no shot or hit event detected, ignore")
                return

            self.log.debug(f"### onMissionEvent() - give kill")
            # give the kill to the opponent if we were hit earlier or if the shot was shortly before
            if ((s_event == 'S_EVENT_SHOT' and delta_time < config.get('reslot_window', 60)) or
                    (s_event == 'S_EVENT_HIT' and delta_time < config.get('survival_window', 300))):
                asyncio.create_task(self._give_kill(server, shooter_id, player.ucid, weapon))

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        if 'side' not in data or data['id'] == 1:
            return

        config = self.get_config(server)
        player = server.get_player(id=data['id'])
        if not player or player.slot > 0:
            return

        shot_time, shooter_id, weapon, s_event = self.pending_kill.pop(player.ucid, (None, None, None, None))
        if shot_time is None or shot_time == -1:
            return

        delta_time = int(time.time()) - shot_time
        if delta_time < config.get('reslot_window', 60):
            evt = {
                "eventName": "reslot",
                "server_name": server.name,
                "initiator": player
            }
            # reslotting will be punished
            asyncio.create_task(self._check_punishment(evt))
            # and the kill will be given to the opponent
            asyncio.create_task(self._give_kill(server, shooter_id, player.ucid, weapon))
        elif s_event == 'S_EVENT_HIT' and delta_time < config.get('survival_window', 300):
            # reslotting of a damaged plane will be treated as a kill
            asyncio.create_task(self._give_kill(server, shooter_id, player.ucid, weapon))

    @chat_command(name="forgive", help=_("forgive another user for their infraction"))
    async def forgive(self, server: Server, player: Player, _params: list[str]):
        async with self.lock:
            initiators = []
            all_tasks = []
            # search the initiators and tasks
            for (initiator, target) in list(self.pending_forgiveness.keys()):
                if target == player.ucid:
                    tasks = self.pending_forgiveness.pop((initiator, target))
                    all_tasks.extend(tasks)
                    initiators.append(initiator)

        if not initiators:
            asyncio.create_task(player.sendChatMessage(_('There is nothing to forgive (maybe too late?)')))
            return

        # wait for all tasks to be finished
        for task in all_tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        for initiator in initiators:
            offender = server.get_player(ucid=initiator)
            if offender:
                asyncio.create_task(offender.sendUserMessage(
                    _("{offender}, You have been forgiven by {victim} and you will not be punished for your "
                      "recent actions.").format(offender=offender.name, victim=player.name)))
                asyncio.create_task(player.sendChatMessage(_('You have chosen to forgive {offender} for their actions.').format(
                    offender=offender.name)))
                events_channel = self.bot.get_channel(server.channels.get(Channel.EVENTS, -1))
                if events_channel:
                    asyncio.create_task(events_channel.send(
                        "```" + _("Player {victim} forgave player {offender} for their actions").format(
                            victim=player.display_name, offender=offender.display_name) + "```"
                    ))

    @chat_command(name="penalty", help=_("displays your penalty points"))
    async def penalty(self, _server: Server, player: Player, _params: list[str]):
        asyncio.create_task(self._send_player_points(player))
