# listener.py
import discord
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
        self.mission_stats = {}

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

    async def onMissionEvent(self, data):
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
            elif data['eventName'] == 'BaseCaptured':
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
