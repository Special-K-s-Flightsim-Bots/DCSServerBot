from core import DCSServerBot, Plugin, utils, PluginRequiredError
from discord.ext import commands
from typing import Optional
from .listener import SlotBlockingListener


class SlotBlocking(Plugin):

    @commands.command(description='Campaign management', usage='[start / stop / reset]')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def campaign(self, ctx, command: Optional[str]):
        server = await utils.get_server(self, ctx)
        if server:
            if not command:
                await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}campaign [start / stop / reset]")
            elif command.lower() == 'start':
                self.eventlistener.campaign('start', server)
                await ctx.send(f"Campaign started for server {server['server_name']}")
            elif command.lower() == 'stop':
                if await self.campaign(ctx, 'reset'):
                    self.eventlistener.campaign('stop', server)
                    await ctx.send(f"Campaign stopped for server {server['server_name']}")
            elif command.lower() == 'reset':
                if await utils.yn_question(self, ctx, 'Do you want to delete the old campaign data for server '
                                                      '"{}"?'.format(server['server_name'])) is True:
                    self.eventlistener.campaign('reset', server)
                    await ctx.send(f"Old campaign data wiped for server {server['server_name']}")
                    return True
                else:
                    await ctx.send('Aborted.')
                    return False


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    bot.add_cog(SlotBlocking(bot, SlotBlockingListener))
