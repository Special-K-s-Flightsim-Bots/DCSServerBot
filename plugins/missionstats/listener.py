import asyncio
import psycopg2
from contextlib import closing
from core import EventListener, Plugin, PersistentReport, Status, Server, Coalition, Channel


class MissionStatisticsEventListener(EventListener):

    COALITION = {
        0: Coalition.NEUTRAL,
        1: Coalition.RED,
        2: Coalition.BLUE
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
            'capture': '```ansi\n\u001b[0;34mBLUE coalition has captured {}.```',
            'capture_from': '```ansi\n\u001b[0;34mBLUE coalition has captured {} from RED coalition.```'
        },
        Coalition.RED: {
            'capture': '```ansi\n\u001b[0;31mRED coalition has captured {}.```',
            'capture_from': '```ansi\n\u001b[0;31mRED coalition has captured {} from BLUE coalition.```'
        }
    }

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        if not self.bot.mission_stats:
            self.bot.mission_stats = dict()
        if 'EVENT_FILTER' in self.bot.config['FILTER']:
            self.filter = [x.strip() for x in self.bot.config['FILTER']['EVENT_FILTER'].split(',')]
        else:
            self.filter = []

    async def getMissionSituation(self, data):
        self.bot.mission_stats[data['server_name']] = data
        self._display_mission_stats(data)

    def _toggle_mission_stats(self, data):
        server: Server = self.bot.servers[data['server_name']]
        if self.bot.config.getboolean(server.installation, 'MISSION_STATISTICS'):
            server.sendtoDCS({"command": "enableMissionStats"})
            server.sendtoDCS({"command": "getMissionSituation", "channel": server.get_channel(Channel.STATUS).id})
        else:
            server.sendtoDCS({"command": "disableMissionStats"})

    async def registerDCSServer(self, data):
        server: Server = self.bot.servers[data['server_name']]
        if data['channel'].startswith('sync') and server.status in [Status.RUNNING, Status.PAUSED]:
            self._toggle_mission_stats(data)

    async def onMissionLoadEnd(self, data):
        self._toggle_mission_stats(data)

    def _display_mission_stats(self, data):
        server: Server = self.bot.servers[data['server_name']]
        # Hide the mission statistics embed, if coalitions are enabled
        if self.bot.config.getboolean(server.installation, 'DISPLAY_MISSION_STATISTICS') and \
                not self.bot.config.getboolean(server.installation, 'COALITIONS'):
            stats = self.bot.mission_stats[data['server_name']]
            if 'coalitions' in stats:
                report = PersistentReport(self.bot, self.plugin_name, 'missionstats.json', server, 'stats_embed')
                self.bot.loop.call_soon(asyncio.create_task, report.render(stats=stats,
                                                                           mission_id=server.mission_id,
                                                                           sides=[Coalition.BLUE, Coalition.RED]))

    def _update_database(self, data):
        if data['eventName'] in self.filter:
            return
        conn = self.pool.getconn()
        try:
            server: Server = self.bot.servers[data['server_name']]
            with closing(conn.cursor()) as cursor:
                def get_value(values: dict, index1, index2):
                    if index1 not in values:
                        return None
                    if index2 not in values[index1]:
                        return None
                    return values[index1][index2]

                player = get_value(data, 'initiator', 'name')
                init_player = server.get_player(name=player) if player else None
                player = get_value(data, 'target', 'name')
                target_player = server.get_player(name=player) if player else None
                if self.bot.config.getboolean(server.installation, 'PERSIST_AI_STATISTICS') or init_player or \
                        target_player:
                    dataset = {
                        'mission_id': server.mission_id,
                        'event': data['eventName'],
                        'init_id': init_player.ucid if init_player else -1,
                        'init_side': get_value(data, 'initiator', 'coalition'),
                        'init_type': get_value(data, 'initiator', 'unit_type'),
                        'init_cat': self.UNIT_CATEGORY[get_value(data, 'initiator', 'category')],
                        'target_id': target_player.ucid if target_player else -1,
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
        server: Server = self.bot.servers[data['server_name']]
        if self.bot.config.getboolean(server.installation, 'PERSIST_MISSION_STATISTICS'):
            self._update_database(data)
        if data['server_name'] in self.bot.mission_stats:
            stats = self.bot.mission_stats[data['server_name']]
            update = False
            if data['eventName'] == 'S_EVENT_BIRTH':
                initiator = data['initiator']
                if initiator is not None and len(initiator) > 0:
                    category = self.UNIT_CATEGORY[initiator['category']]
                    coalition: Coalition = self.COALITION[initiator['coalition']]
                    # no stats for Neutral
                    if coalition == Coalition.NEUTRAL:
                        return
                    unit_name = initiator['unit_name']
                    if initiator['type'] == 'UNIT':
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
            elif data['eventName'] == 'S_EVENT_KILL' and 'initiator' in data and len(data['initiator']) > 0:
                killer = data['initiator']
                victim = data['target']
                if killer is not None and len(killer) > 0 and len(victim) > 0:
                    coalition: Coalition = self.COALITION[killer['coalition']]
                    # no stats for Neutral
                    if coalition == Coalition.NEUTRAL:
                        return
                    if victim['type'] == 'UNIT':
                        category = self.UNIT_CATEGORY[victim['category']]
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
            elif data['eventName'] in ['S_EVENT_UNIT_LOST', 'S_EVENT_PLAYER_LEAVE_UNIT'] and \
                    'initiator' in data and len(data['initiator']) > 0:
                initiator = data['initiator']
                category = self.UNIT_CATEGORY[initiator['category']]
                coalition: Coalition = self.COALITION[initiator['coalition']]
                # no stats for Neutral
                if coalition == Coalition.NEUTRAL:
                    return
                unit_name = initiator['unit_name']
                if initiator['type'] == 'UNIT':
                    if unit_name in stats['coalitions'][coalition.name]['units'][category]:
                        stats['coalitions'][coalition.name]['units'][category].remove(unit_name)
                elif initiator['type'] == 'STATIC':
                    if unit_name in stats['coalitions'][coalition.name]['statics']:
                        stats['coalitions'][coalition.name]['statics'].remove(unit_name)
                update = True
            elif data['eventName'] == 'S_EVENT_BASE_CAPTURED':
                # TODO: rewrite that code, so the initiator is not needed
                if 'initiator' in data:
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
                    chat_channel = server.get_channel(Channel.CHAT)
                    if chat_channel:
                        self.bot.loop.call_soon(asyncio.create_task, chat_channel.send(message))
            if update:
                self._display_mission_stats(data)
