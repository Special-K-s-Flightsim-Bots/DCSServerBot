from core import DCSServerBot, Plugin, utils
from core.const import Status
from discord.ext import commands
from .listener import GameMasterEventListener


class GameMaster(Plugin):

    @commands.Cog.listener()
    async def on_message(self, message):
        for key, value in self.globals.items():
            if value["chat_channel"] == message.channel.id:
                if message.content.startswith(self.config['BOT']['COMMAND_PREFIX']) is False:
                    message.content = self.config['BOT']['COMMAND_PREFIX'] + 'chat ' + message.content
                    await self.bot.process_commands(message)

    @commands.command(description='Send a chat message to a running DCS instance', usage='<message>', hidden=True)
    @utils.has_role('DCS')
    @commands.guild_only()
    async def chat(self, ctx, *args):
        server = await utils.get_server(self, ctx)
        if server and server['status'] == Status.RUNNING:
            self.bot.sendtoDCS(server, {
                "command": "sendChatMessage",
                "channel": ctx.channel.id,
                "message": ' '.join(args),
                "from": ctx.message.author.display_name
            })

    @commands.command(description='Sends a popup to a coalition', usage='<coalition> <message>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def popup(self, ctx, to, *args):
        server = await utils.get_server(self, ctx)
        if server:
            if to not in ['all', 'red', 'blue']:
                await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}popup all|red|blue <message>")
            elif server['status'] == Status.RUNNING:
                self.bot.sendtoDCS(server, {
                    "command": "sendPopupMessage",
                    "channel": ctx.channel.id,
                    "message": ' '.join(args),
                    "from": ctx.message.author.display_name, "to": to.lower()
                })
                await ctx.send('Message sent.')
            else:
                await ctx.send(f"Mission is {server['status'].lower()}, message discarded.")

    @commands.command(description='Send a chat message to a running DCS instance', usage='<flag> [value]', hidden=True)
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def flag(self, ctx, flag, value=None):
        server = await utils.get_server(self, ctx)
        if server and server['status'] == Status.RUNNING:
            self.bot.sendtoDCS(server, {
                "command": "setFlag",
                "channel": ctx.channel.id,
                "flag": flag,
                "value": value
            })


def setup(bot: DCSServerBot):
    bot.add_cog(GameMaster(bot, GameMasterEventListener))
