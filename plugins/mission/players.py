from core import report, const
from discord.ext.commands import Context
from typing import Optional


class Main(report.EmbedElement):
    def render(self, server: dict, sides: list[str]):
        players = self.bot.player_data[server['server_name']]
        players = players[players['active'] == True]
        coalitions = {
            const.SIDE_SPECTATOR: {"names": [], "units": []},
            const.SIDE_BLUE: {"names": [], "units": []},
            const.SIDE_RED: {"names": [], "units": []}
        }
        for idx, player in players.iterrows():
            coalitions[player['side']]['names'].append(player['name'])
            coalitions[player['side']]['units'].append(player['unit_type'] if player['side'] != 0 else '')
        if 'Blue' in sides and len(coalitions[const.SIDE_BLUE]['names']):
            self.embed.add_field(name='Blue', value='_ _')
            self.embed.add_field(name='Name', value='\n'.join(coalitions[const.SIDE_BLUE]['names']) or '_ _')
            self.embed.add_field(name='Unit', value='\n'.join(coalitions[const.SIDE_BLUE]['units']) or '_ _')
        if 'Red' in sides and len(coalitions[const.SIDE_RED]['names']):
            self.embed.add_field(name='Red', value='_ _')
            self.embed.add_field(name='Name', value='\n'.join(coalitions[const.SIDE_RED]['names']) or '_ _')
            self.embed.add_field(name='Unit', value='\n'.join(coalitions[const.SIDE_RED]['units']) or '_ _')
        # Neutral
        if len(coalitions[const.SIDE_SPECTATOR]['names']):
            self.embed.add_field(name='Spectator', value='_ _')
            self.embed.add_field(name='Name', value='\n'.join(coalitions[const.SIDE_SPECTATOR]['names']) or '_ _')
            self.embed.add_field(name='_ _', value='_ _')
