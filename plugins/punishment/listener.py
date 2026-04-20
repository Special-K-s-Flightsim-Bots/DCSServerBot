import asyncio
import time

from contextlib import suppress
from core import EventListener, Server, Player, event, chat_command, get_translation, ChatCommand, Channel, \
    ThreadSafeDict, Coalition, Side
from plugins.competitive.commands import Competitive
from psycopg.types.json import Json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .commands import Punishment

_ = get_translation(__name__.split('.')[1])

MAX_MISSILE_LIFETIME = 100  # max lifetime is 100s (Fox 3)
AVG_MISSILE_SPEED = 1000    # avg speed is 1000 m/s

class PunishmentEventListener(EventListener["Punishment"]):

    def __init__(self, plugin: "Punishment"):
        super().__init__(plugin)
        self.lock = asyncio.Lock()
        self.active_servers: set[str] = set()
        self.pending_forgiveness: dict[tuple[str, str], list[asyncio.Task]] = {}
        self.pending_kill: dict[str, tuple[int, dict | None]] = ThreadSafeDict()
        self.disconnected: dict[str, tuple[int, dict | None]] = ThreadSafeDict()
        self.awaiting_task: dict[str, asyncio.TimerHandle] = ThreadSafeDict()

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
                        self.pending_kill[player['ucid']] = (-1, None)
        else:
            self.active_servers.discard(server.name)

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, _data: dict) -> None:
        # make sure the config cache is re-read on mission changes
        self.plugin.get_config(server, use_cache=False)

    @event(name="onSimulationStop")
    async def onSimulationStop(self, _server: Server, _data: dict) -> None:
        self.pending_kill.clear()
        self.disconnected.clear()

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

    async def _provide_forgiveness_window(self, data: dict, window: int, key: tuple[str, str]) -> None:
        try:
            # wait for a '-forgive' to happen
            await asyncio.sleep(window)
            # it did not happen -> fulfill the punishment
            asyncio.create_task(self._punish(data))
        except asyncio.CancelledError:
            # it did happen -> do nothing
            pass
        finally:
            current = asyncio.current_task()
            if current:
                async with self.lock:
                    tasks = self.pending_forgiveness.get(key)
                    if tasks and current in tasks:
                        tasks.remove(current)
                        if not tasks:
                            self.pending_forgiveness.pop(key, None)

    @event(name="punish")
    async def punish(self, server: Server, data: dict):
        initiator = self.bot.servers[server.name].get_player(name=data['initiator'])
        if not initiator:
            return
        data['server_name'] = server.name
        data['initiator'] = initiator
        await self._check_punishment(data)

    async def _punish(self, data: dict):
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)

        initiator = data['initiator']
        target = data.get('target')
        penalty = next(item for item in config['penalties'] if item['event'] == data['eventName'])

        # do we have to fire an immediate action?
        if 'action' in penalty:
            asyncio.create_task(
                self.plugin.punish(server, initiator.ucid, penalty, penalty['reason']))

        if data.get('points', 0) > 0:
            async with self.apool.connection() as conn:
                await conn.execute("""
                    INSERT INTO pu_events (init_id, target_id, server_name, event, points) 
                    VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                """, (initiator.ucid, target.ucid if target else None, data['server_name'], data['eventName'],
                      data['points']))
            await self.plugin.trigger.publish({
                "guild_id": self.node.guild_id,
                "node": "Master",
                "data": Json({
                    'init_id': initiator.ucid,
                    'target_id': target.ucid if target else None,
                    'server_name': data['server_name'],
                    'event': data['eventName'],
                    'points': data['points']
                })
            })

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
                    tasks.append(asyncio.create_task(self._provide_forgiveness_window(data.copy(), window, key)))

                if inform_victim:
                    asyncio.create_task(target.sendUserMessage(
                        _("{victim}, you are a victim of a friendly-fire event by player {offender}.\n"
                          "If you send {prefix}forgive in chat within the next {time} seconds, "
                          "you can pardon the other player.").format(
                            victim=target.name, event=data['eventName'], offender=initiator.name,
                            prefix=self.prefix, time=window)))

            else:
                asyncio.create_task(self._punish(data.copy()))

    def _recreate_events(self, server: Server, data: dict, evt: dict):
        initiator = server.get_player(id=data['arg1'])
        target = evt['target']
        if not initiator:
            self.log.debug("Punishment: failed to recreate S_EVENT_HIT/S_EVENT_KILL for target {}".format(target.name))
            return

        # preserve the old event (to avoid modifying the original event)
        s_event = evt.copy()

        # check if we have the correct initiator
        if initiator != evt['initiator']:
            # replace the initiator with the correct one
            s_event['initiator'] = {
                "name": initiator.name,
                "coalition": initiator.side.value,
                "type": "UNIT",
                "category": 0, # TODO: we can only guess it was an airplane at this stage
                "unit_type": initiator.unit_type,
                "unit_name": initiator.unit_name,
                "group_name": initiator.group_name
            }

        if initiator == evt['initiator']:
            # clear fields that are no longer relevant
            s_event['initiator'].pop('position', None)
            s_event['target'].pop('position', None)
            s_event.pop('distance', None)

            # generate S_EVENT_HIT
            if s_event['eventName'] == 'S_EVENT_SHOT':
                self.log.debug("Punishment: autocreating missing S_EVENT_HIT for player {} vs {}".format(
                    initiator.name, target.name)
                )
                s_event |= {
                    "eventName": "S_EVENT_HIT",
                    "id": 28,
                    "comment": "auto-generated"
                }
                asyncio.create_task(self.bus.send_to_node(s_event))

            # generate S_EVENT_KILL
            if s_event['eventName'] == 'S_EVENT_HIT':
                self.log.debug("Punishment: autocreating missing S_EVENT_KILL for player {} vs {}".format(
                    initiator.name, target.name)
                )
                s_event |= {
                    "eventName": "S_EVENT_KILL",
                    "id": 28,
                    "comment": "auto-generated"
                }
                asyncio.create_task(self.bus.send_to_node(s_event))

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
        competitive: Competitive | None = cast(Competitive, self.bot.cogs.get('Competitive'))
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

        elif data['eventName'] == 'kill':
            # check team-kills
            target = server.get_player(id=data['arg4'])
            if data['arg1'] != data['arg4'] and data['arg3'] == data['arg6']:
                if target:
                    evt['target'] = target
                # check collision
                if data['arg7'] == initiator.unit_display_name:
                    evt['eventName'] = 'collision_kill'
                else:
                    evt['eventName'] = 'kill'
                asyncio.create_task(self._check_punishment(evt))

            # remove pending kills
            if target:
                shot_time, evt = self.pending_kill.pop(target.ucid, (-1, None))
                task = self.awaiting_task.pop(target.ucid, None)
                if task:
                    # Workaround for DCS bug with missing S_EVENT_KILL events
                    task.cancel()
                    if evt:
                        self._recreate_events(server, data, evt)
                    else:
                        # there was not even a recent shot onto this player
                        self.log.debug(f"Missing S_EVENT_KILL for player {initiator.name} vs {target.name}, ignoring")

        elif data['eventName'] == 'disconnect':
            shot_time, evt = self.pending_kill.pop(initiator.ucid, (-1, None))
            if shot_time == -1 or not evt:
                return

            delta_time = int(time.time()) - shot_time
            if delta_time < config.get('reslot_window', 60):
                # the kill will be given to the opponent
                asyncio.create_task(self._give_kill(server, evt))
            elif evt['eventName'] == 'S_EVENT_HIT' and delta_time < config.get('survival_window', 300):
                # reslotting of a damaged plane will be treated as a kill
                asyncio.create_task(self._give_kill(server, evt))
            else:
                return
            # mark the event for a potential penalty
            self.disconnected[initiator.ucid] = (int(time.time()), evt)

    async def _send_player_points(self, player: Player):
        points = await self._get_punishment_points(player)
        if points > 0:
            asyncio.create_task(player.sendChatMessage(_("{name}, you have {points} punishment points.").format(
                name=player.name, points=points)))

    def _schedule_give_kill(self, server: Server, victim_ucid: str, s_event: dict, delay: int = 10) -> None:
        def fire() -> None:
            self.awaiting_task.pop(victim_ucid, None)
            asyncio.create_task(self._give_kill(server, s_event))

        old = self.awaiting_task.pop(victim_ucid, None)
        if old:
            old.cancel()

        self.awaiting_task[victim_ucid] = self.loop.call_later(delay, fire)

    async def _cancel_give_kill(self, victim_ucid: str) -> None:
        handle = self.awaiting_task.pop(victim_ucid, None)
        if handle:
            handle.cancel()

    async def _give_kill(self, server: Server, s_event: dict) -> None:
        # clear fields that are no longer relevant
        s_event['initiator'].pop('position', None)
        s_event['target'].pop('position', None)
        s_event.pop('distance', None)
        # create the pseudo-event "S_EVENT_KILL"
        s_event |= {
            "eventName": "S_EVENT_KILL",
            "id": 28,
            "comment": "auto-generated"
        }
        asyncio.create_task(self.bus.send_to_node(s_event))

        initiator = server.get_player(name=s_event.get('initiator', {}).get('name'))
        target = server.get_player(name=s_event.get('target', {}).get('name'))

        # remove the pending task if there is one
        if target:
            self.awaiting_task.pop(target.ucid, None)

        categories = {
            0: "Planes",
            1: "Helicopters",
            2: "Air Defence"
        }

        init_type = s_event.get('initiator', {}).get('unit_type')
        init_side = Side(s_event.get('initiator', {}).get('coalition'))
        init_cat = categories[s_event.get('initiator', {}).get('category', 0)]
        target_type = s_event.get('target', {}).get('unit_type')
        target_side = Side(s_event.get('target', {}).get('coalition'))
        target_cat = categories[s_event.get('target', {}).get('category', 0)]
        weapon = s_event.get('weapon', {}).get('name', "the reslot hammer")

        # create the pseudo-event "kill" (for stats and to credit the player)
        asyncio.create_task(self.bus.send_to_node(
            {
                "command": "onGameEvent",
                "eventName": "kill",
                "arg1": initiator.id if initiator else -1,
                "arg2": init_type,
                "arg3": init_side.value,
                "arg4": target.id if target else -1,
                "arg5": target_type,
                "arg6": target_side.value,
                "arg7": weapon,
                "killerCategory": init_cat,
                "victimCategory": target_cat,
                "channel": "-1",
                "server_name": server.name,
                "comment": "auto-generated"
            }
        ))

        # inform the players
        message = "{} {} in {} killed {} {} in {} with {}.".format(
            init_side.name,
            ('player ' + initiator.name) if initiator is not None else 'AI',
            init_type,
            target_side.name,
            ('player ' + target.name) if target is not None else 'AI',
            target_type,
            weapon
        )
        asyncio.create_task(server.sendChatMessage(Coalition.ALL, message))

    @event(name="onPlayerConnect")
    async def onPlayerConnect(self, server: Server, data: dict) -> None:
        if data['ucid'] not in self.disconnected:
            return

        config = self.get_config(server)
        tm, _evt = self.disconnected.pop(data['ucid'])
        # we do not punish if the disconnect was longer than reslot_window seconds ago
        delta_time = int(time.time()) - tm
        if delta_time > config.get('reslot_window', 60):
            return

        player = server.get_player(ucid=data['ucid'])
        if not player:
            self.log.warning("Player UCID is in the disconnect list but not in the playerlist anymore!")
            return

        evt = {
            "eventName": "reslot",
            "server_name": server.name,
            "initiator": player
        }
        asyncio.create_task(self._check_punishment(evt))
        admin = self.bot.get_admin_channel(server)
        if admin:
            asyncio.create_task(admin.send(
                "```" + _("Player {} (ucid={}) disconnected and reconnected {} seconds after being shot at.").format(
                    player.name, player.ucid, delta_time) + "```"))

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        player = server.get_player(ucid=data['ucid'])
        if player:
            asyncio.create_task(self._send_player_points(player))

    @event(name="disablePunishments")
    async def disablePunishments(self, server: Server, _: dict) -> None:
        self.active_servers.discard(server.name)

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        config = self.get_config(server)

        # airstarts or takeoffs reset the reslot timer directly on birth
        if (data['eventName'] == 'S_EVENT_BIRTH' and not data.get('place')) or data['eventName'] == 'S_EVENT_TAKEOFF':
            initiator = server.get_player(name=data.get('initiator', {}).get('name'))
            if initiator:
                self.pending_kill[initiator.ucid] = (-1, None)

        elif data['eventName'] in ['S_EVENT_SHOT', 'S_EVENT_HIT']:
            initiator = server.get_player(name=data.get('initiator', {}).get('name'))
            target = server.get_player(name=data.get('target', {}).get('name'))
            # ignore teamkills
            if (
                    data.get('initiator', {}).get('coalition', 0) ==
                    data.get('target', {}).get('coalition', 0)
            ):
                return

            # we only care for real players here
            if not target or target.ucid not in self.pending_kill:
                return

            shot_time, s_event = self.pending_kill.get(target.ucid, (-1, None))
            if data['eventName'] == 'S_EVENT_SHOT':
                # if there is an older shot ...
                if shot_time > 0 and s_event:
                    delta_time = int(time.time()) - shot_time
                    if s_event.get('eventName') == 'S_EVENT_HIT':
                        # we do not overwrite hit events with shot events if they are still hot
                        if delta_time < config.get('survival_window', 300):
                            return
                        self.log.debug(f"Punishment: Replacing old hit event as delta_time was {delta_time}.")
                    # ... check the PBK for both shots
                    else:
                        # we consider missiles that were in the air for more than MAX_MISSILE_LIFETIME as dead
                        if delta_time < MAX_MISSILE_LIFETIME:
                            distance_old = s_event.get('distance', 0)
                            distance_new = data.get('distance', 0)
                            # calculate the traveled distance
                            distance_old -= delta_time * AVG_MISSILE_SPEED # assume 1000 m/s as avg speed
                            # ignore the new shot event if the old missile is still hot and has a higher PBK (closer)
                            if 0 < distance_old < distance_new:
                                return
                            self.log.debug(f"Punishment: Replacing old shot event as distance was {distance_old}.")
                        else:
                            self.log.debug(f"Punishment: Replacing old shot event as delta_time was {delta_time}.")

            # we got hit by a player
            elif initiator:
                # check how good our prediction was
                if s_event['eventName'] == 'S_EVENT_SHOT' and s_event['initiator'] == initiator:
                    self.log.debug("Punishment: Good prediction, shot hit the player.")
                else:
                    self.log.debug("Punishment: Bad prediction, another shot hit the player.")

            # store the shot with the highest PBK or the latest hit event
            self.pending_kill[target.ucid] = (int(time.time()), data)

        elif data['eventName'] == 'S_EVENT_LAND':
            initiator = server.get_player(name=data.get('initiator', {}).get('name'))
            if initiator and initiator.sub_slot == 0:
                self.pending_kill.pop(initiator.ucid, None)

        elif data['eventName'] == 'S_EVENT_KILL':
            target = server.get_player(name=data.get('target', {}).get('name'))
            if target:
                self.pending_kill.pop(target.ucid, None)

        elif data['eventName'] in ['S_EVENT_CRASH', 'S_EVENT_EJECTION']:
            initiator = server.get_player(name=data.get('initiator', {}).get('name'))
            if not initiator or initiator.sub_slot > 0:
                return

            shot_time, s_event = self.pending_kill.pop(initiator.ucid, (-1, None))
            if shot_time == -1 or not s_event:
                return

            delta_time = int(time.time()) - shot_time
            # give the kill to the opponent if we were hit earlier or if the shot was shortly before
            if ((s_event['eventName'] == 'S_EVENT_SHOT' and delta_time < config.get('reslot_window', 60)) or
                    (s_event['eventName'] == 'S_EVENT_HIT' and delta_time < config.get('survival_window', 300))):
                # TODO: DCS Bug, change this to immediate, when S_EVENT_KILL is fixed
                self._schedule_give_kill(server, initiator.ucid, s_event)

        elif data['eventName'] == 'S_EVENT_TAXIWAY_TAKEOFF':
            player = server.get_player(name=data.get('initiator', {}).get('name'))
            if not player or player.sub_slot > 0:
                return

            evt = {
                "server_name": server.name,
                "initiator": player,
                "eventName": "taxiway_takeoff"
            }
            asyncio.create_task(self._check_punishment(evt))

    async def _change_slot(self, server: Server, data: dict) -> None:
        if 'side' not in data or data['id'] == 1:
            return

        config = self.get_config(server)
        player = server.get_player(id=data['id'])
        if not player:
            return

        shot_time, s_event = self.pending_kill.pop(player.ucid, (-1, None))
        if shot_time == -1 or not s_event:
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
            asyncio.create_task(self._give_kill(server, s_event))
        elif s_event['eventName'] == 'S_EVENT_HIT' and delta_time < config.get('survival_window', 300):
            # reslotting of a damaged plane will be treated as a kill
            asyncio.create_task(self._give_kill(server, s_event))

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        await self._change_slot(server, data)

    @event(name="onPlayerChangeCoalition")
    async def onPlayerChangeCoalition(self, server: Server, data: dict) -> None:
        await self._change_slot(server, data)

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
