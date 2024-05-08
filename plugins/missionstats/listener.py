import asyncio

from core import EventListener, Plugin, PersistentReport, Status, Server, Coalition, Channel, event, Report, \
    get_translation
from discord.ext import tasks

_ = get_translation(__name__.split('.')[1])


class MissionStatisticsEventListener(EventListener):

    COALITION = {
        0: Coalition.NEUTRAL,
        1: Coalition.RED,
        2: Coalition.BLUE,
        3: Coalition.NEUTRAL
    }

    UNIT_CATEGORY = {
        None: None,
        0: 'Airplanes',
        1: 'Helicopters',
        2: 'Ground Units',
        3: 'Ships',
        4: 'Structures',
        5: 'Unknown'
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

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        if not self.bot.mission_stats:
            self.bot.mission_stats = dict()
        self.update: dict[str, bool] = dict()
        self.do_update.start()

    async def shutdown(self):
        self.do_update.cancel()

    @event(name="getMissionSituation")
    async def getMissionSituation(self, server: Server, data: dict) -> None:
        self.bot.mission_stats[server.name] = data

    def _toggle_mission_stats(self, server: Server):
        if self.plugin.get_config(server).get('enabled', True):
            server.send_to_dcs({"command": "enableMissionStats"})
            server.send_to_dcs({"command": "getMissionSituation", "channel": server.channels.get(Channel.STATUS, -1)})
        else:
            server.send_to_dcs({"command": "disableMissionStats"})

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if data['channel'].startswith('sync') and server.status in [Status.RUNNING, Status.PAUSED]:
            self._toggle_mission_stats(server)

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, _: dict) -> None:
        self._toggle_mission_stats(server)

    async def _update_database(self, server: Server, config: dict, data: dict):
        def get_value(values: dict, index1, index2):
            if index1 not in values:
                return None
            if index2 not in values[index1]:
                return None
            return values[index1][index2]

        if not config.get('persistence', True) or data['eventName'] in config.get('event_filter', []):
            return
        player = get_value(data, 'initiator', 'name')
        init_player = server.get_player(name=player) if player else None
        init_type = get_value(data, 'initiator', 'type')
        player = get_value(data, 'target', 'name')
        target_player = server.get_player(name=player) if player else None
        target_type = get_value(data, 'target', 'type')
        if (config.get('persist_ai_statistics', False) or (init_player and init_type == 'UNIT') or
                (target_player and target_type == 'UNIT')):
            dataset = {
                'mission_id': server.mission_id,
                'event': data['eventName'],
                'init_id': init_player.ucid if init_player else -1,
                'init_side': get_value(data, 'initiator', 'coalition'),
                'init_type': get_value(data, 'initiator', 'unit_type'),
                'init_cat': self.UNIT_CATEGORY.get(get_value(data, 'initiator', 'category'), 'Unknown'),
                'target_id': target_player.ucid if target_player else -1,
                'target_side': get_value(data, 'target', 'coalition'),
                'target_type': get_value(data, 'target', 'unit_type'),
                'target_cat': self.UNIT_CATEGORY.get(get_value(data, 'target', 'category'), 'Unknown'),
                'weapon': get_value(data, 'weapon', 'name'),
                'place': get_value(data, 'place', 'name'),
                'comment': data['comment'] if 'comment' in data else ''
            }
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

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if config.get('persistence', True):
            # noinspection PyAsyncCall
            asyncio.create_task(self._update_database(server, config, data))
        if not data['server_name'] in self.bot.mission_stats or not data.get('initiator'):
            return
        stats = self.bot.mission_stats[data['server_name']]
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
            if initiator['type'] == 'UNIT':
                category = self.UNIT_CATEGORY.get(initiator['category'], 'Unknown')
                if category not in stats['coalitions'][coalition.name]['units']:
                    # lua does initialize the empty dict as an array
                    if len(stats['coalitions'][coalition.name]['units']) == 0:
                        stats['coalitions'][coalition.name]['units'] = {}
                    stats['coalitions'][coalition.name]['units'][category] = []
                if unit_name not in stats['coalitions'][coalition.name]['units'][category]:
                    stats['coalitions'][coalition.name]['units'][category].append(unit_name)
            elif initiator['type'] == 'STATIC':
                stats['coalitions'][coalition.name]['statics'].append(unit_name)
            update = True
        elif data['eventName'] == 'S_EVENT_KILL':
            killer = data['initiator']
            victim = data['target']
            if killer and victim:
                coalition: Coalition = self.COALITION[killer['coalition']]
                # no stats for Neutral
                if coalition == Coalition.NEUTRAL:
                    return
                if victim['type'] == 'UNIT':
                    category = self.UNIT_CATEGORY.get(victim['category'], 'Unknown')
                    if 'kills' not in stats['coalitions'][coalition.name]:
                        stats['coalitions'][coalition.name]['kills'] = {}
                    if category not in stats['coalitions'][coalition.name]['kills']:
                        stats['coalitions'][coalition.name]['kills'][category] = 1
                    else:
                        stats['coalitions'][coalition.name]['kills'][category] += 1
                elif victim['type'] == 'STATIC':
                    if 'kills' not in stats['coalitions'][coalition.name]:
                        stats['coalitions'][coalition.name]['kills'] = {}
                    if 'Static' not in stats['coalitions'][coalition.name]['kills']:
                        stats['coalitions'][coalition.name]['kills']['Static'] = 1
                    else:
                        stats['coalitions'][coalition.name]['kills']['Static'] += 1
                update = True
        elif data['eventName'] in ['S_EVENT_UNIT_LOST', 'S_EVENT_PLAYER_LEAVE_UNIT']:
            initiator = data['initiator']
            # no stats for Neutral
            coalition: Coalition = self.COALITION[initiator['coalition']]
            if coalition == Coalition.NEUTRAL:
                return
            unit_name = initiator['unit_name']
            if initiator['type'] == 'UNIT':
                category = self.UNIT_CATEGORY.get(initiator['category'], 'Unknown')
                if category == 'Structures':
                    if unit_name in stats['coalitions'][coalition.name]['statics']:
                        stats['coalitions'][coalition.name]['statics'].remove(unit_name)
                elif unit_name in stats['coalitions'][coalition.name]['units'][category]:
                    stats['coalitions'][coalition.name]['units'][category].remove(unit_name)
            elif initiator['type'] == 'STATIC':
                if unit_name in stats['coalitions'][coalition.name]['statics']:
                    stats['coalitions'][coalition.name]['statics'].remove(unit_name)
            update = True
        elif data['eventName'] == 'S_EVENT_BASE_CAPTURED':
            # TODO: rewrite that code, so the initiator is not needed
            win_coalition = self.COALITION[data['initiator']['coalition']]
            lose_coalition = self.COALITION[(data['initiator']['coalition'] % 2) + 1]
            name = data['place']['name']
            # workaround for DCS base capture bug:
            if name in stats['coalitions'][win_coalition.name]['airbases'] or \
                    name not in stats['coalitions'][lose_coalition.name]['airbases']:
                return None
            stats['coalitions'][win_coalition.name]['airbases'].append(name)
            if 'captures' not in stats['coalitions'][win_coalition.name]:
                stats['coalitions'][win_coalition.name]['captures'] = 1
            else:
                stats['coalitions'][win_coalition.name]['captures'] += 1
            if name in stats['coalitions'][lose_coalition.name]['airbases']:
                stats['coalitions'][lose_coalition.name]['airbases'].remove(name)
                message = self.EVENT_TEXTS[win_coalition]['capture_from'].format(name)
            else:
                message = self.EVENT_TEXTS[win_coalition]['capture'].format(name)
            update = True
            events_channel = self.bot.get_channel(server.channels[Channel.EVENTS])
            if events_channel:
                # noinspection PyAsyncCall
                asyncio.create_task(events_channel.send(message))
        if update:
            self.update[server.name] = True

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'mission_end':
            config = self.get_config(server)
            if 'mission_end' in config:
                title = config['mission_end'].get('title', 'Mission Result')
                stats = self.bot.mission_stats.get(server.name)
                if not stats:
                    return
                if config['mission_end'].get('persistent', False):
                    report = PersistentReport(self.bot, self.plugin_name, 'missionstats.json',
                                              embed_name='stats_embed_me', server=server,
                                              channel_id=int(config['mission_end'].get('channel')))
                    # noinspection PyAsyncCall
                    asyncio.create_task(report.render(stats=stats, mission_id=server.mission_id,
                                                      sides=[Coalition.BLUE, Coalition.RED], title=title))
                else:
                    channel = self.bot.get_channel(config['mission_end'].get('channel'))
                    report = Report(self.bot, self.plugin_name, 'missionstats.json')
                    env = await report.render(stats=stats, mission_id=server.mission_id,
                                              sides=[Coalition.BLUE, Coalition.RED], title=title)
                    # noinspection PyAsyncCall
                    asyncio.create_task(channel.send(embed=env.embed))

    @tasks.loop(seconds=30)
    async def do_update(self):
        for server_name, update in self.update.items():
            if not update:
                continue
            server: Server = self.bot.servers[server_name]
            # Hide the mission statistics embed, if coalitions are enabled
            if self.plugin.get_config(server).get('display', True) and \
                    not server.locals.get('coalitions'):
                stats = self.bot.mission_stats[server_name]
                if 'coalitions' in stats:
                    report = PersistentReport(self.bot, self.plugin_name, 'missionstats.json',
                                              embed_name='stats_embed', server=server)
                    # noinspection PyAsyncCall
                    asyncio.create_task(report.render(
                        stats=stats, mission_id=server.mission_id, sides=[Coalition.BLUE, Coalition.RED],
                        title='Mission Statistics'))
            self.update[server_name] = False
