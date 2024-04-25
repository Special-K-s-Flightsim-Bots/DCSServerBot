from core import report, Server, Side, Coalition


class Main(report.EmbedElement):
    async def render(self, server: Server, sides: list[Coalition]):
        players = server.get_active_players()
        coalitions = {
            Side.SPECTATOR: {"names": [], "units": []},
            Side.BLUE: {"names": [], "units": []},
            Side.RED: {"names": [], "units": []},
            Side.NEUTRAL: {"names": [], "units": []}
        }
        for player in players:
            coalitions[player.side]['names'].append(player.display_name)
            coalitions[player.side]['units'].append(player.unit_type if player.side != 0 else '')
        extensions = server.instance.locals.get('extensions')
        has_srs = ('SRS' in extensions)
        for coalition in [Coalition.BLUE, Coalition.RED, Coalition.NEUTRAL]:
            side = getattr(Side, coalition.name)
            if coalition in sides and len(coalitions[side]['names']):
                self.add_field(name='▬' * 13 + f' {coalition.name.title()} ' + '▬' * 13, value='_ _', inline=False)
                self.add_field(name='Name', value='\n'.join(coalitions[side]['names']) or '_ _')
                self.add_field(name='Unit', value='\n'.join(coalitions[side]['units']) or '_ _')
                if has_srs:
                    self.add_field(name='SRS',
                                   value='\n'.join([':green_circle:' if x.radios else ':red_circle:' for x in players]))
        # Spectators
        if len(coalitions[Side.SPECTATOR]['names']):
            self.add_field(name='▬' * 13 + ' Spectator ' + '▬' * 13, value='_ _', inline=False)
            self.add_field(name='Name', value='\n'.join(coalitions[Side.SPECTATOR]['names']) or '_ _')
            self.add_field(name='_ _', value='_ _')
            self.add_field(name='_ _', value='_ _')
