import discord
from core import report, utils


class Header(report.EmbedElement):
    def render(self, mission_info: dict, server_name: str, message: discord.Message):
        server = self.bot.globals[server_name]
        sides = utils.get_sides(message, server)
        if 'Blue' in sides:
            self.embed.add_field(name='Blue Passwd', value=mission_info['passwords']['Blue'] or 'n/a')
        if 'Red' in sides:
            self.embed.add_field(name='Red Passwd', value=mission_info['passwords']['Red'] or 'n/a')


class Body(report.EmbedElement):
    def render(self, mission_info: dict, server_name: str, message: discord.Message):
        server = self.bot.globals[server_name]
        sides = utils.get_sides(message, server)
        if 'Blue' in sides:
            self.embed.add_field(name='Blue Tasks', value=mission_info['briefing']['descriptionBlueTask'][:1024] or 'n/a', inline=False)
        if 'Red' in sides:
            self.embed.add_field(name='Red Tasks', value=mission_info['briefing']['descriptionRedTask'][:1024] or 'n/a', inline=False)
