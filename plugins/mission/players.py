from core import report, Server, Side, Coalition


class Main(report.EmbedElement):
    def render(self, server: Server, sides: list[Coalition]):
        players = server.get_active_players()
        coalitions = {
            Side.SPECTATOR: {"names": [], "units": []},
            Side.BLUE: {"names": [], "units": []},
            Side.RED: {"names": [], "units": []}
        }
        for player in players:
            coalitions[player.side]['names'].append(player.name)
            coalitions[player.side]['units'].append(player.unit_type if player.side != 0 else '')
        if Coalition.BLUE in sides and len(coalitions[Side.BLUE]['names']):
            self.embed.add_field(name='Blue', value='_ _')
            self.embed.add_field(name='Name', value='\n'.join(coalitions[Side.BLUE]['names']) or '_ _')
            self.embed.add_field(name='Unit', value='\n'.join(coalitions[Side.BLUE]['units']) or '_ _')
        if Coalition.RED in sides and len(coalitions[Side.RED]['names']):
            self.embed.add_field(name='Red', value='_ _')
            self.embed.add_field(name='Name', value='\n'.join(coalitions[Side.RED]['names']) or '_ _')
            self.embed.add_field(name='Unit', value='\n'.join(coalitions[Side.RED]['units']) or '_ _')
        # Neutral
        if len(coalitions[Side.SPECTATOR]['names']):
            self.embed.add_field(name='Spectator', value='_ _')
            self.embed.add_field(name='Name', value='\n'.join(coalitions[Side.SPECTATOR]['names']) or '_ _')
            self.embed.add_field(name='_ _', value='_ _')
