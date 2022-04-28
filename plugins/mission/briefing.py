import discord
from core import report, utils


class Header(report.EmbedElement):
    def render(self, mission_info: dict, server_name: str, message: discord.Message):
        server = self.bot.globals[server_name]
        sides = utils.get_sides(message, server)
        self.embed.add_field(name='Mission', value=mission_info[server_name]['current_mission'])
        if 'Blue' in sides:
            self.embed.add_field(name='Blue Passwd', value=mission_info[server_name]['passwords']['Blue'] or 'n/a')
        if 'Red' in sides:
            self.embed.add_field(name='Red Passwd', value=mission_info[server_name]['passwords']['Red'] or 'n/a')


class Body(report.EmbedElement):
    def render(self, mission_info: dict, server_name: str, message: discord.Message):
        server = self.bot.globals[server_name]
        sides = utils.get_sides(message, server)
        briefing = mission_info[server_name]['briefing']
        self.embed.add_field(name='Description', value=briefing['descriptionText'][:1024] or 'n/a', inline=False)
        if 'Blue' in sides:
            self.embed.add_field(name='Blue Tasks', value=briefing['descriptionBlueTask'][:1024] or 'n/a', inline=False)
        if 'Red' in sides:
            self.embed.add_field(name='Red Tasks', value=briefing['descriptionRedTask'][:1024] or 'n/a', inline=False)
