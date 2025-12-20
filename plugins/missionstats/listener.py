import asyncio

from core import EventListener, PersistentReport, Server, Coalition, Channel, event, Report, get_translation, \
    ThreadSafeDict
from discord.ext import tasks
from typing import TYPE_CHECKING, Counter

if TYPE_CHECKING:
    from .commands import MissionStatistics

_ = get_translation(__name__.split('.')[1])


class MissionStatisticsEventListener(EventListener["MissionStatistics"]):

    COALITION = {
        0: Coalition.NEUTRAL,
        1: Coalition.RED,
        2: Coalition.BLUE,
        3: Coalition.NEUTRAL
    }

    CATEGORY = {
        "UNIT": {
            None: None,
            0: 'Airplanes',
            1: 'Helicopters',
            2: 'Ground Units',
            3: 'Ships',
            4: 'Structures'
        },
        "WEAPON": {
            None: None,
            0: 'Shell',
            1: 'Missile',
            2: 'Rocket',
            3: 'Bomb'
        },
        "STATIC": {
            None: None,
            0: 'Static Object'
        },
        "SCENERY": {
            None: None,
            0: 'Scenery Object'
        }
    }

    EVENT_TEXTS = {
        Coalition.BLUE: {
            'capture': '```ansi\n\u001b[0;34m{}```'.format(_('BLUE coalition has captured {}.')),
            'capture_from': '```ansi\n\u001b[0;34m{}```'.format(_('BLUE coalition has captured {} from RED coalition.'))
        },
        Coalition.RED: {
            'capture': '```ansi\n\u001b[0;31m{}```'.format(_('RED coalition has captured {}.')),
            'capture_from': '```ansi\n\u001b[0;31m{}```'.format(_('RED coalition has captured {} from BLUE coalition.'))
        }
    }

    def __init__(self, plugin: "MissionStatistics"):
        super().__init__(plugin)
        self.mission_stats = {}
        self.update: dict[str, bool] = ThreadSafeDict()
        self.do_update.start()

    async def shutdown(self):
        self.do_update.cancel()
        self.mission_stats.clear()

    @event(name="getMissionSituation")
    async def getMissionSituation(self, server: Server, data: dict) -> None:
        self.mission_stats[server.name] = data
        self.update[server.name] = True

    async def _toggle_mission_stats(self, server: Server):
        if self.plugin.get_config(server).get('enabled', True):
            await server.send_to_dcs({
                "command": "enableMissionStats",
                "filter": self.plugin.get_config(server).get('event_filter', [
                    "S_EVENT_MARK_ADDED",
                    "S_EVENT_MARK_CHANGE",
                    "S_EVENT_MARK_REMOVED",
                    "S_EVENT_DISCARD_CHAIR_AFTER_EJECTION",
                    "S_EVENT_AI_ABORT_MISSION",
                    "S_EVENT_SHOOTING_START",
                    "S_EVENT_SHOOTING_END"
                ])
            })
            await server.send_to_dcs({"command": "getMissionSituation",
                                      "channel": server.channels.get(Channel.STATUS, -1)})
        else:
            await server.send_to_dcs({"command": "disableMissionStats"})

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if data['channel'].startswith('sync') and data.get('players'):
            asyncio.create_task(self._toggle_mission_stats(server))

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, _: dict) -> None:
        asyncio.create_task(self._toggle_mission_stats(server))

    async def _update_database(self, server: Server, config: dict, data: dict):
        def get_value(values: dict, index1, index2):
            if index1 not in values:
                return None
            if index2 not in values[index1]:
                return None
            return values[index1][index2]

        if not config.get('persistence', True) or server.mission_id == -1:
            return
        player = get_value(data, 'initiator', 'name')
        init_player = server.get_player(name=player) if player else None
        init_type = get_value(data, 'initiator', 'type')
        player = get_value(data, 'target', 'name')
        target_player = server.get_player(name=player) if player else None
        target_type = get_value(data, 'target', 'type')
        if (config.get('persist_ai_statistics', False) or (init_player and init_type == 'UNIT') or
                (target_player and target_type == 'UNIT')):
            init_type = get_value(data, 'initiator', 'type')
            if init_type:
                init_cat = self.CATEGORY[init_type].get(
                    get_value(data, 'initiator', 'category'), 'Unknown')
            else:
                init_cat = None
            target_type = get_value(data, 'target', 'type')
            if target_type:
                target_cat = self.CATEGORY[target_type].get(
                    get_value(data, 'target', 'category'), 'Unknown')
            else:
                target_cat = None
            dataset = {
                'mission_id': server.mission_id,
                'event': data['eventName'],
                'init_id': init_player.ucid if init_player else None,
                'init_side': get_value(data, 'initiator', 'coalition'),
                'init_type': get_value(data, 'initiator', 'unit_type'),
                'init_cat': init_cat,
                'target_id': target_player.ucid if target_player else None,
                'target_side': get_value(data, 'target', 'coalition'),
                'target_type': get_value(data, 'target', 'unit_type'),
                'target_cat': target_cat,
                'weapon': get_value(data, 'weapon', 'name'),
                'place': get_value(data, 'place', 'name'),
                'comment': data['comment'] if 'comment' in data else ''
            }
            try:
                async with self.apool.connection() as conn:
                    async with conn.transaction():
                        await conn.execute("""
                            INSERT INTO missionstats (mission_id, event, init_id, init_side, init_type, init_cat, 
                                                      target_id, target_side, target_type, target_cat, weapon, place, 
                                                      comment) 
                            VALUES (%(mission_id)s, %(event)s, %(init_id)s, %(init_side)s, %(init_type)s, %(init_cat)s, 
                                    %(target_id)s, %(target_side)s, %(target_type)s, %(target_cat)s, %(weapon)s, 
                                    %(place)s, %(comment)s)
                        """, dataset)
            except Exception as ex:
                self.log.warning(str(ex) + ' / ignoring event')

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if config.get('persistence', True):
            asyncio.create_task(self._update_database(server, config, data))
        if not data['server_name'] in self.mission_stats or not data.get('initiator'):
            return

        stats = self.mission_stats[data['server_name']]
        update = False

        if data['eventName'] == 'S_EVENT_BIRTH':
            initiator = data['initiator']
            # set the real unit id in the player
            player_name = initiator.get('name')
            init_player = server.get_player(name=player_name) if player_name else None
            if init_player:
                init_player.unit_id = initiator['unit']['id_']

            coalition: Coalition = self.COALITION[initiator['coalition']]
            # no stats for Neutral
            if coalition == Coalition.NEUTRAL:
                return

            unit_name = initiator['unit_name']
            coalition_stats = stats['coalitions'][coalition.name]
            if initiator['type'] == 'UNIT':
                category = self.CATEGORY['UNIT'].get(initiator['category'], 'Unknown')
                if not coalition_stats['units'].get(category):
                    # lua does initialize the empty dict as an array
                    if len(coalition_stats['units']) == 0:
                        coalition_stats['units'] = {}
                    coalition_stats['units'][category] = []
                units = coalition_stats['units'][category]
                if unit_name not in units:
                    units.append(unit_name)
            elif initiator['type'] == 'STATIC':
                if len(coalition_stats['statics']) == 0:
                    coalition_stats['statics'] = []
                units = coalition_stats['statics']
                if unit_name not in units:
                    units.append(unit_name)
            update = True

        elif data['eventName'] == 'S_EVENT_KILL':
            killer = data['initiator']
            victim = data.get('target')
            if killer and victim:
                coalition: Coalition = self.COALITION[killer['coalition']]
                # no stats for Neutral
                if coalition == Coalition.NEUTRAL:
                    return

                coalition_stats = stats['coalitions'][coalition.name]
                if victim['type'] == 'UNIT':
                    category = self.CATEGORY['UNIT'].get(victim['category'], 'Unknown')
                    kill_counter = coalition_stats.setdefault('kills', Counter())
                    kill_counter[category] += 1
                elif victim['type'] == 'STATIC':
                    kill_counter = coalition_stats.setdefault('kills', Counter())
                    kill_counter['Static'] += 1
                update = True

        elif data['eventName'] in ['S_EVENT_UNIT_LOST', 'S_EVENT_PLAYER_LEAVE_UNIT']:
            initiator = data['initiator']
            # no stats for Neutral
            coalition: Coalition = self.COALITION[initiator['coalition']]
            if coalition == Coalition.NEUTRAL:
                return

            coalition_stats = stats['coalitions'][coalition.name]
            unit_name = initiator['unit_name']
            if initiator['type'] == 'UNIT':
                category = self.CATEGORY['UNIT'].get(initiator['category'], 'Unknown')
                if category == 'Structures':
                    units = coalition_stats['statics']
                else:
                    units = coalition_stats['units'][category]
                if unit_name in units:
                    units.remove(unit_name)
            elif initiator['type'] == 'STATIC':
                units = coalition_stats['statics']
                if unit_name in units:
                    units.remove(unit_name)
            update = True

        elif data['eventName'] == 'S_EVENT_BASE_CAPTURED':
            # TODO: rewrite that code, so the initiator is not needed
            win_coalition = self.COALITION[data['initiator']['coalition']]
            lose_coalition = self.COALITION[(data['initiator']['coalition'] % 2) + 1]
            name = data['place']['name']
            # workaround for DCS base capture bug:
            if name in stats['coalitions'][win_coalition.name]['airbases'] or \
                    name not in stats['coalitions'][lose_coalition.name]['airbases']:
                return

            wc = stats['coalitions'][win_coalition.name]
            wc.setdefault('airbases', []).append(name)
            wc['captures'] = wc.get('captures', 0) + 1
            lc = stats['coalitions'][lose_coalition.name]
            if name in lc['airbases']:
                lc['airbases'].remove(name)
                message = self.EVENT_TEXTS[win_coalition]['capture_from'].format(name)
            else:
                message = self.EVENT_TEXTS[win_coalition]['capture'].format(name)
            update = True
            events_channel = self.bot.get_channel(server.channels.get(Channel.EVENTS, -1))
            if events_channel:
                asyncio.create_task(events_channel.send(message))

        # is an embed update necessary?
        self.update[server.name] = update

    async def _process_event(self, server: Server) -> None:
        try:
            config = self.get_config(server)
            if 'mission_end' not in config:
                return
            title = config['mission_end'].get('title', 'Mission Result')
            stats = self.mission_stats.get(server.name)
            if not stats:
                return
            if config['mission_end'].get('persistent', False):
                report = PersistentReport(self.bot, self.plugin_name, 'missionstats.json',
                                          embed_name='stats_embed_me', server=server,
                                          channel_id=int(config['mission_end'].get('channel')))
                await report.render(stats=stats, mission_id=server.mission_id, sides=[Coalition.BLUE, Coalition.RED],
                                    title=title)
            else:
                channel = self.bot.get_channel(config['mission_end'].get('channel'))
                if not channel:
                    self.log.warning("Missionstats: you have no valid mission_end channel configured "
                                     "in your missionstats.yaml")
                    return
                report = Report(self.bot, self.plugin_name, 'missionstats.json')
                env = await report.render(stats=stats, mission_id=server.mission_id,
                                          sides=[Coalition.BLUE, Coalition.RED], title=title)
                await channel.send(embed=env.embed)
        except Exception as ex:
            self.log.exception(ex)

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'mission_end':
            asyncio.create_task(self._process_event(server))

    @tasks.loop(minutes=1)
    async def do_update(self):
        for server_name, update in self.update.items():
            server: Server = self.bot.servers.get(server_name)
            if not update or not server:
                continue
            if self.plugin.get_config(server).get('display', True):
                stats = self.mission_stats[server_name]
                if 'coalitions' in stats:
                    report = PersistentReport(self.bot, self.plugin_name, 'missionstats.json',
                                              embed_name='stats_embed', server=server)
                    await report.render(stats=stats, mission_id=server.mission_id, title='Mission Statistics',
                                        sides=[Coalition.BLUE, Coalition.RED])
            self.update[server_name] = False
