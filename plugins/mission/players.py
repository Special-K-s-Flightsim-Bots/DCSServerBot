from core import report, Server, Side, Coalition
from plugins.srs.commands import SRS
from typing import cast


class Main(report.EmbedElement):
    async def render(self, server: Server, sides: list[Coalition]):
        players = server.get_active_players()
        sides = {
            Side.NEUTRAL: {"names": [], "units": [], "SRS": []},
            Side.BLUE: {"names": [], "units": [], "SRS": []},
            Side.RED: {"names": [], "units": [], "SRS": []}
        }
        srs_plugin = cast(SRS, self.bot.cogs.get('SRS'))
        if srs_plugin:
            srs_users = srs_plugin.eventlistener.srs_users.get(server.name, {})
        else:
            srs_users = {}
        players_sorted = sorted(players, key=lambda p: p.display_name)
        for player in players_sorted:
            sides[player.side]['names'].append(player.display_name)
            if player.side != Side.NEUTRAL:
                unit = player.unit_type
                if player.sub_slot > 0:
                    unit += ' (crew)'
                sides[player.side]['units'].append(unit)
            else:
                sides[player.side]['units'].append('')
            if srs_users:
                sides[player.side]['SRS'].append(':green_circle:' if player.name in srs_users else ':red_circle:')
        for side in [Side.BLUE, Side.RED, Side.NEUTRAL]:
            if side in sides and len(sides[side]['names']):
                self.add_field(name='▬' * 13 + f' {side.name.title()} ' + '▬' * 13, value='_ _', inline=False)
                self.add_field(name='Name', value='\n'.join(sides[side]['names']) or '_ _')
                self.add_field(name='Unit', value='\n'.join(sides[side]['units']) or '_ _')
                if srs_users:
                    self.add_field(name='SRS', value='\n'.join(sides[side]['SRS']) or '_ _')
