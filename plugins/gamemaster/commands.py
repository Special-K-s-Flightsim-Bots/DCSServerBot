import discord
from core import DCSServerBot, Plugin, utils
from core.const import Status
from discord.ext import commands
from .listener import GameMasterEventListener


class GameMasterAgent(Plugin):

    @commands.Cog.listener()
    async def on_message(self, message):
        for server in self.globals.values():
            if 'chat_channel' in server and server["chat_channel"] == str(message.channel.id):
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

    @commands.command(description='Sends a popup to a coalition', usage='<all|red|blue> [time] <message>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def popup(self, ctx, to, *args):
        server = await utils.get_server(self, ctx)
        if server:
            if to not in ['all', 'red', 'blue']:
                await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}popup all|red|blue [time] <message>")
            elif server['status'] == Status.RUNNING:
                if len(args) > 0:
                    if args[0].isnumeric():
                        time = int(args[0])
                        i = 1
                    else:
                        time = self.config['BOT']['MESSAGE_TIMEOUT']
                        i = 0
                    self.bot.sendtoDCS(server, {
                        "command": "sendPopupMessage",
                        "channel": ctx.channel.id,
                        "message": ' '.join(args[i:]),
                        "time": time,
                        "from": ctx.message.author.display_name, "to": to.lower()
                    })
                    await ctx.send('Message sent.')
                else:
                    await ctx.send(f"Usage: {self.config['BOT']['COMMAND_PREFIX']}popup all|red|blue [time] <message>")
            else:
                await ctx.send(f"Mission is {server['status'].name.lower()}, message discarded.")

    @commands.command(description='Set or clear a flag inside the mission environment', usage='<flag> [value]', hidden=True)
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def flag(self, ctx, flag, value=None):
        server = await utils.get_server(self, ctx)
        if server and server['status'] in [Status.RUNNING, Status.PAUSED]:
            self.bot.sendtoDCS(server, {
                "command": "setFlag",
                "channel": ctx.channel.id,
                "flag": flag,
                "value": value
            })
            await ctx.send('Flag set.')
        else:
            await ctx.send(f"Mission is {server['status'].name.lower()}, can't set flag.")

    @commands.command(description='Calls any function inside the mission environment', usage='<script>', hidden=True)
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def do_script(self, ctx, *script):
        server = await utils.get_server(self, ctx)
        if server and server['status'] in [Status.RUNNING, Status.PAUSED]:
            self.bot.sendtoDCS(server, {
                "command": "do_script",
                "script": ' '.join(script)
            })
            await ctx.send('Command sent.')
        else:
            await ctx.send(f"Mission is {server['status'].name.lower()}, command discarded.")

    @commands.command(description='Loads a lua file into the mission environment', usage='<file>', hidden=True)
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def do_script_file(self, ctx, filename):
        server = await utils.get_server(self, ctx)
        if server and server['status'] in [Status.RUNNING, Status.PAUSED]:
            self.bot.sendtoDCS(server, {
                "command": "do_script_file",
                "file": filename.replace('\\', '/')
            })
            await ctx.send('Command sent.')
        else:
            await ctx.send(f"Mission is {server['status'].name.lower()}, command discarded.")


class GameMasterMaster(GameMasterAgent):

    @commands.command(description='Join a coalition (red / blue)', usage='[red | blue]')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def join(self, ctx, coalition: str):
        member = ctx.message.author
        roles = {
            "red": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Red']),
            "blue": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Blue'])
        }
        if coalition.lower() not in roles.keys():
            await ctx.send('Usage: {}join [{}]'.format(self.config['BOT']['COMMAND_PREFIX'], '|'.join(roles.keys())))
            return
        for key, value in roles.items():
            if value in member.roles:
                await ctx.send(f'You are a member of coalition {key} already. Aborted.')
                return
        try:
            await member.add_roles(roles[coalition.lower()])
            await ctx.send(f'Welcome to the {coalition} side!')
        except discord.Forbidden:
            await ctx.send("I can't add you to this coalition. Please contact an Admin.")
            await self.bot.audit(f'Permission "Manage Roles" missing for {self.bot.member.name}.')

    @commands.command(description='Leave a coalition (red / blue)', usage='[red | blue]')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def leave(self, ctx):
        member = ctx.message.author
        roles = {
            "red": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Red']),
            "blue": discord.utils.get(member.guild.roles, name=self.config['ROLES']['Coalition Blue'])
        }
        for key, value in roles.items():
            if value in member.roles:
                try:
                    await member.remove_roles(value)
                    await ctx.send(f"You've left the {key} coalition!")
                    return
                except discord.Forbidden:
                    await ctx.send("I can't remove you from this coalition. Please contact an Admin.")
                    await self.bot.audit(f'Permission "Manage Roles" missing for {self.bot.member.name}.')


def setup(bot: DCSServerBot):
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(GameMasterMaster(bot, GameMasterEventListener))
    else:
        bot.add_cog(GameMasterAgent(bot, GameMasterEventListener))
