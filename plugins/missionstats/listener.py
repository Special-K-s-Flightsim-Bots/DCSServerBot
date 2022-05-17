import asyncio
import psycopg2
from contextlib import closing
from core import utils, EventListener, Plugin, PersistentReport, Status


class MissionStatisticsEventListener(EventListener):

    COALITION = {
        0: 'Neutral',
        1: 'Red',
        2: 'Blue'
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

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        if not self.bot.mission_stats:
            self.bot.mission_stats = dict()
        if 'EVENT_FILTER' in self.config['FILTER']:
            self.filter = [x.strip() for x in self.config['FILTER']['EVENT_FILTER'].split(',')]
        else:
            self.filter = []

    async def toggleMissionStats(self, data):
        server = self.globals[data['server_name']]
        if self.config.getboolean(server['installation'], 'MISSION_STATISTICS'):
            self.bot.sendtoDCS(server, {"command": "enableMissionStats"})
            try:
                response = await self.bot.sendtoDCSSync(server, {"command": "getMissionSituation"}, 60)
            except asyncio.TimeoutError as ex:
                self.log.exception(ex)
                response = {}
            self.bot.mission_stats[data['server_name']] = response
            await self.displayMissionStats(response)
        else:
            self.bot.sendtoDCS(server, {"command": "disableMissionStats"})

    async def registerDCSServer(self, data):
        server = self.globals[data['server_name']]
        if data['channel'].startswith('sync') and server['status'] in [Status.RUNNING, Status.PAUSED]:
            await self.toggleMissionStats(data)

    async def onMissionLoadEnd(self, data):
        await self.toggleMissionStats(data)

    async def displayMissionStats(self, data):
        server = self.globals[data['server_name']]
        # Hide the mission statistics embed, if coalitions are enabled
        if self.config.getboolean(server['installation'], 'DISPLAY_MISSION_STATISTICS') and \
                not self.config.getboolean(server['installation'], 'COALITIONS'):
            stats = self.bot.mission_stats[data['server_name']]
            report = PersistentReport(self.bot, self.plugin_name, 'missionstats.json', server, 'stats_embed')
            await report.render(stats=stats, mission_id=server['mission_id'], sides=['Blue', 'Red'])

    def update_database(self, data):
        if data['eventName'] in self.filter:
            return
        conn = self.pool.getconn()
        try:
            server = self.globals[data['server_name']]
            with closing(conn.cursor()) as cursor:
                def get_value(values: dict, index1, index2):
                    if index1 not in values:
                        return None
                    if index2 not in values[index1]:
                        return None
                    return values[index1][index2]

                player = get_value(data, 'initiator', 'name')
                init_player = utils.get_player(self, server['server_name'], name=player) if player else None
                player = get_value(data, 'target', 'name')
                target_player = utils.get_player(self, server['server_name'], name=player) if player else None
                if self.config.getboolean(server['installation'], 'PERSIST_AI_STATISTICS') or init_player or target_player:
                    dataset = {
                        'mission_id': server['mission_id'],
                        'event': data['eventName'],
                        'init_id': init_player['ucid'] if init_player else -1,
                        'init_side': get_value(data, 'initiator', 'coalition'),
                        'init_type': get_value(data, 'initiator', 'unit_type'),
                        'init_cat': self.UNIT_CATEGORY[get_value(data, 'initiator', 'category')],
                        'target_id': target_player['ucid'] if target_player else -1,
                        'target_side': get_value(data, 'target', 'coalition'),
                        'target_type': get_value(data, 'target', 'unit_type'),
                        'target_cat': self.UNIT_CATEGORY[get_value(data, 'target', 'category')],
                        'weapon': get_value(data, 'weapon', 'name'),
                        'place': get_value(data, 'place', 'name'),
                        'comment': data['comment'] if 'comment' in data else ''
                    }
                    cursor.execute('INSERT INTO missionstats (mission_id, event, init_id, init_side, init_type, '
                                   'init_cat, target_id, target_side, target_type, target_cat, weapon, '
                                   'place, comment) VALUES (%(mission_id)s, %(event)s, %(init_id)s, %(init_side)s, '
                                   '%(init_type)s, %(init_cat)s, %(target_id)s, %(target_side)s, %(target_type)s, '
                                   '%(target_cat)s, %(weapon)s, %(place)s, %(comment)s)', dataset)
                    conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    async def onMissionEvent(self, data):
        server = self.globals[data['server_name']]
        if self.config.getboolean(server['installation'], 'PERSIST_MISSION_STATISTICS'):
            self.update_database(data)
        if data['server_name'] in self.bot.mission_stats:
            stats = self.bot.mission_stats[data['server_name']]
            update = False
            if data['eventName'] == 'S_EVENT_BIRTH':
                initiator = data['initiator']
                if initiator is not None and len(initiator) > 0:
                    category = self.UNIT_CATEGORY[initiator['category']]
                    coalition = self.COALITION[initiator['coalition']]
                    # no stats for Neutral
                    if coalition == 'Neutral':
                        return
                    unit_name = initiator['unit_name']
                    if initiator['type'] == 'UNIT':
                        if category not in stats['coalitions'][coalition]['units']:
                            # lua does initialize the empty dict as an array
                            if len(stats['coalitions'][coalition]['units']) == 0:
                                stats['coalitions'][coalition]['units'] = {}
                            stats['coalitions'][coalition]['units'][category] = []
                        if unit_name not in stats['coalitions'][coalition]['units'][category]:
                            stats['coalitions'][coalition]['units'][category].append(unit_name)
                    elif initiator['type'] == 'STATIC':
                        stats['coalitions'][coalition]['statics'].append(unit_name)
                    update = True
            elif data['eventName'] == 'S_EVENT_KILL' and 'initiator' in data and len(data['initiator']) > 0:
                killer = data['initiator']
                victim = data['target']
                if killer is not None and len(killer) > 0 and len(victim) > 0:
                    coalition = self.COALITION[killer['coalition']]
                    # no stats for Neutral
                    if coalition == 'Neutral':
                        return
                    category = self.UNIT_CATEGORY[victim['category']]
                    if victim['type'] == 'UNIT':
                        if 'kills' not in stats['coalitions'][coalition]:
                            stats['coalitions'][coalition]['kills'] = {}
                        if category not in stats['coalitions'][coalition]['kills']:
                            stats['coalitions'][coalition]['kills'][category] = 1
                        else:
                            stats['coalitions'][coalition]['kills'][category] += 1
                    elif victim['type'] == 'STATIC':
                        if 'kills' not in stats['coalitions'][coalition]:
                            stats['coalitions'][coalition]['kills'] = {}
                        if 'Static' not in stats['coalitions'][coalition]['kills']:
                            stats['coalitions'][coalition]['kills']['Static'] = 1
                        else:
                            stats['coalitions'][coalition]['kills']['Static'] += 1
                    update = True
            elif data['eventName'] in ['S_EVENT_UNIT_LOST', 'S_EVENT_PLAYER_LEAVE_UNIT'] and 'initiator' in data and len(data['initiator']) > 0:
                initiator = data['initiator']
                category = self.UNIT_CATEGORY[initiator['category']]
                coalition = self.COALITION[initiator['coalition']]
                # no stats for Neutral
                if coalition == 'Neutral':
                    return
                unit_name = initiator['unit_name']
                if initiator['type'] == 'UNIT':
                    if unit_name in stats['coalitions'][coalition]['units'][category]:
                        stats['coalitions'][coalition]['units'][category].remove(unit_name)
                elif initiator['type'] == 'STATIC':
                    if unit_name in stats['coalitions'][coalition]['statics']:
                        stats['coalitions'][coalition]['statics'].remove(unit_name)
                update = True
            elif data['eventName'] == 'S_EVENT_BASE_CAPTURED':
                # TODO: rewrite that code, so the initiator is not needed
                if 'initiator' in data:
                    win_coalition = self.COALITION[data['initiator']['coalition']]
                    lose_coalition = self.COALITION[(data['initiator']['coalition'] % 2) + 1]
                    name = data['place']['name']
                    # workaround for DCS base capture bug:
                    if name in stats['coalitions'][win_coalition]['airbases'] or \
                            name not in stats['coalitions'][lose_coalition]['airbases']:
                        return None
                    stats['coalitions'][win_coalition]['airbases'].append(name)
                    if 'captures' not in stats['coalitions'][win_coalition]:
                        stats['coalitions'][win_coalition]['captures'] = 1
                    else:
                        stats['coalitions'][win_coalition]['captures'] += 1
                    message = '{} coalition has captured {}'.format(win_coalition.upper(), name)
                    if name in stats['coalitions'][lose_coalition]['airbases']:
                        stats['coalitions'][lose_coalition]['airbases'].remove(name)
                        message += ' from {} coalition'.format(lose_coalition.upper())
                    update = True
                    chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
                    if chat_channel is not None:
                        await chat_channel.send(message)
            if update:
                return await self.displayMissionStats(data)
            else:
                return None
