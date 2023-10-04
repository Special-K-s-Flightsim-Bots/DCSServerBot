import discord
from core import report, utils, Server, Coalition


class Header(report.EmbedElement):
    def render(self, mission_info: dict, server_name: str, interaction: discord.Interaction):
        server: Server = self.bot.servers[server_name]
        sides = utils.get_sides(interaction.client, interaction, server)
        if Coalition.BLUE in sides:
            self.add_field(name='Blue Passwd', value=mission_info['passwords']['Blue'] or 'n/a')
        if Coalition.RED in sides:
            self.add_field(name='Red Passwd', value=mission_info['passwords']['Red'] or 'n/a')


class Body(report.EmbedElement):
    def render(self, mission_info: dict, server_name: str, interaction: discord.Interaction):
        server: Server = self.bot.servers[server_name]
        sides = utils.get_sides(interaction.client, interaction, server)
        if Coalition.BLUE in sides:
            self.add_field(name='Blue Tasks', value=mission_info['briefing']['descriptionBlueTask'][:1024].strip('\n') or 'n/a', inline=False)
        if Coalition.RED in sides:
            self.add_field(name='Red Tasks', value=mission_info['briefing']['descriptionRedTask'][:1024].strip('\n') or 'n/a', inline=False)
