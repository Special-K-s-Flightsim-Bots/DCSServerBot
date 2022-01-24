# listener.py
from contextlib import closing

import discord
import psycopg2

from core import DCSServerBot, EventListener


class MissionStatisticsEventListener(EventListener):

    COALITION = {
        1: 'Red',
        2: 'Blue'
    }

    UNIT_CATEGORY = {
        0: 'Airplanes',
        1: 'Helicopters',
        2: 'Ground Units',
        3: 'Ships',
        4: 'Structures',
        5: 'Unknown'
    }

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.mission_stats = dict()
        self.mission_ids = dict()

    async def registerDCSServer(self, data):
        if data['statistics']:
            server_name = data['server_name']
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute(
                        'SELECT id FROM missions WHERE server_name = %s AND mission_end IS NULL', (server_name,))
                    if cursor.rowcount > 0:
                        self.mission_ids[server_name] = cursor.fetchone()[0]
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)

    async def enableMissionStats(self, data):
        self.mission_stats[data['server_name']] = data
        return await self.displayMissionStats(data)

    async def disableMissionStats(self, data):
        server = self.bot.DCSServers[data['server_name']]
        self.bot.sendtoDCS(server, {"command": "disableMissionStats"})

    async def displayMissionStats(self, data):
        if data['server_name'] in self.mission_stats:
            stats = self.mission_stats[data['server_name']]
            embed = discord.Embed(title='Mission Statistics', color=discord.Color.blue())
            embed.add_field(name='▬▬▬▬▬▬ Current Situation ▬▬▬▬▬▬', value='_ _', inline=False)
            embed.add_field(
                name='_ _', value='Airbases / FARPs\nPlanes\nHelicopters\nGround Units\nShips\nStructures')
            for coalition in ['Blue', 'Red']:
                coalition_data = stats['coalitions'][coalition]
                value = '{}\n'.format(len(coalition_data['airbases']))
                for unit_type in ['Airplanes', 'Helicopters', 'Ground Units', 'Ships']:
                    value += '{}\n'.format(len(coalition_data['units'][unit_type])
                                           if unit_type in coalition_data['units'] else 0)
                value += '{}\n'.format(len(coalition_data['statics']))
                embed.add_field(name=coalition, value=value)
            embed.add_field(name='▬▬▬▬▬▬ Achievements ▬▬▬▬▬▬▬', value='_ _', inline=False)
            embed.add_field(
                name='_ _',
                value='Base Captures\nKilled Planes\nDowned Helicopters\nGround Shacks\nSunken Ships\nDemolished Structures')
            for coalition in ['Blue', 'Red']:
                value = ''
                coalition_data = stats['coalitions'][coalition]
                value += '{}\n'.format(coalition_data['captures'] if ('captures' in coalition_data) else 0)
                if 'kills' in coalition_data:
                    for unit_type in ['Airplanes', 'Helicopters', 'Ground Units', 'Ships', 'Static']:
                        value += '{}\n'.format(coalition_data['kills'][unit_type]
                                               if unit_type in coalition_data['kills'] else 0)
                else:
                    value += '0\n' * 5
                embed.add_field(name=coalition, value=value)
            return await self.bot.setEmbed(data, 'stats_embed', embed)

    # TODO: this code has to run after the new mission id has been created!
    async def onMissionLoadEnd(self, data):
        conn = self.pool.getconn()
        try:
            server_name = data['server_name']
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT id FROM missions WHERE server_name = %s AND mission_end IS NULL', (server_name,))
                if cursor.rowcount > 0:
                    self.mission_ids[server_name] = cursor.fetchone()[0]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    async def onMissionEvent(self, data):
        # TODO: make this configurable
        conn = self.pool.getconn()
        try:
            server_name = data['server_name']
            with closing(conn.cursor()) as cursor:
                def get_value(values: dict, index1, index2):
                    if index1 not in values:
                        return None
                    if index2 not in values[index1]:
                        return None
                    return values[index1][index2]

                dataset = {
                    'mission_id': self.mission_ids[server_name],
                    'event': data['eventName'],
                    'init_id': get_value(data, 'initiator', 'unit_name'),
                    'init_side': get_value(data, 'initiator', 'coalition'),
                    'init_type': get_value(data, 'initiator', 'unit_type'),
                    'init_cat': get_value(data, 'initiator', 'category'),
                    'target_id': get_value(data, 'target', 'unit_name'),
                    'target_side': get_value(data, 'target', 'coalition'),
                    'target_type': get_value(data, 'target', 'unit_type'),
                    'target_cat': get_value(data, 'target', 'category'),
                    'weapon': get_value(data, 'weapon', 'name')
                }
                cursor.execute('INSERT INTO missionstats (mission_id, event, init_id, init_side, init_type, init_cat, '
                               'target_id, target_side, target_type, target_cat, weapon) VALUES (%(mission_id)s, '
                               '%(event)s, %(init_id)s, %(init_side)s, %(init_type)s, %(init_cat)s, %(target_id)s, '
                               '%(target_side)s, %(target_type)s, %(target_cat)s, %(weapon)s)', dataset)
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

        if data['server_name'] in self.mission_stats:
            stats = self.mission_stats[data['server_name']]
            update = False
            if data['eventName'] == 'birth':
                initiator = data['initiator']
                if initiator is not None and len(initiator) > 0:
                    category = self.UNIT_CATEGORY[initiator['category']]
                    coalition = self.COALITION[initiator['coalition']]
                    unit_name = initiator['unit_name']
                    if initiator['type'] == 'UNIT':
                        if category not in stats['coalitions'][coalition]['units']:
                            stats['coalitions'][coalition]['units'][category] = []
                        if unit_name not in stats['coalitions'][coalition]['units'][category]:
                            stats['coalitions'][coalition]['units'][category].append(unit_name)
                    elif initiator['type'] == 'STATIC':
                        stats['coalitions'][coalition]['statics'].append(unit_name)
                    update = True
            elif data['eventName'] == 'kill' and 'initiator' in data and len(data['initiator']) > 0:
                killer = data['initiator']
                victim = data['target']
                if killer is not None and len(killer) > 0 and len(victim) > 0:
                    coalition = self.COALITION[killer['coalition']]
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
            elif data['eventName'] in ['lost', 'dismiss'] and 'initiator' in data and len(data['initiator']) > 0:
                initiator = data['initiator']
                category = self.UNIT_CATEGORY[initiator['category']]
                coalition = self.COALITION[initiator['coalition']]
                unit_name = initiator['unit_name']
                if initiator['type'] == 'UNIT':
                    if unit_name in stats['coalitions'][coalition]['units'][category]:
                        stats['coalitions'][coalition]['units'][category].remove(unit_name)
                elif initiator['type'] == 'STATIC':
                    if unit_name in stats['coalitions'][coalition]['statics']:
                        stats['coalitions'][coalition]['statics'].remove(unit_name)
                update = True
            elif data['eventName'] == 'capture':
                win_coalition = self.COALITION[data['initiator']['coalition']]
                lose_coalition = self.COALITION[(data['initiator']['coalition'] % 2) + 1]
                name = data['place']['name']
                # workaround for DCS BaseCapture-bug
                if name in stats['coalitions'][win_coalition]['airbases']:
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
