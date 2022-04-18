from core import report
from discord.ext.commands import Context


class Header(report.EmbedElement):
    def render(self, ctx: Context, mission_info: dict, server_name: str):
        self.embed.add_field(name='Mission', value=mission_info[server_name]['current_mission'])
        for role in ctx.author.roles:
            if role.name == self.bot.config['ROLES']['Coalition Blue'] \
                    and ctx.message.channel.overwrites_for(role).send_messages:
                self.embed.add_field(name='Blue Passwd', value=mission_info[server_name]['passwords']['Blue'] or 'n/a')
            if role.name == self.bot.config['ROLES']['Coalition Red'] \
                    and ctx.message.channel.overwrites_for(role).send_messages:
                self.embed.add_field(name='Red Passwd', value=mission_info[server_name]['passwords']['Red'] or 'n/a')


class Body(report.EmbedElement):
    def render(self, ctx: Context, mission_info: dict, server_name: str):
        briefing = mission_info[server_name]['briefing']
        self.embed.add_field(name='Description', value=briefing['descriptionText'][:2048] or 'n/a', inline=False)
        for role in ctx.author.roles:
            if role.name == self.bot.config['ROLES']['Coalition Blue'] \
                    and ctx.message.channel.overwrites_for(role).send_messages:
                self.embed.add_field(name='Blue Tasks', value=briefing['descriptionBlueTask'][:2048] or 'n/a', inline=False)
            if role.name == self.bot.config['ROLES']['Coalition Red'] \
                    and ctx.message.channel.overwrites_for(role).send_messages:
                self.embed.add_field(name='Red Tasks', value=briefing['descriptionRedTask'][:2048] or 'n/a', inline=False)
