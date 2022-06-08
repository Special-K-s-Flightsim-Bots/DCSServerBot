import discord
from discord.ext import commands
from core import DCSServerBot, Plugin
from .listener import HelpListener


class Help(Plugin):

    @commands.command(name='help', description='The help command!')
    async def help(self, ctx):
        help_embed = discord.Embed(color=discord.Color.blue())
        help_embed.title = f'{self.bot.member.name} Commands'
        cmds = []
        descriptions = []
        for plugin in self.bot.plugins:
            # Get a list of all commands for the specified plugin
            for cog in self.bot.cogs.values():
                if f'.{plugin}.' in type(cog).__module__:
                    commands_list = self.bot.get_cog(type(cog).__name__).get_commands()
                    for command in commands_list:
                        if command.hidden:
                            continue
                        check = True
                        for f in command.checks:
                            check &= f(ctx)
                        if not check:
                            continue
                        cmd = f'{ctx.prefix}{command.name}'
                        if command.usage is not None:
                            cmd += ' ' + command.usage
                        cmds.append(cmd)
                        descriptions.append(f'{command.description}')
        name = ''
        value = ''
        for i in range(0, len(cmds)):
            if (len(name + cmds[i]) > 1024) or (len(value + descriptions[i]) > 1024):
                help_embed.add_field(name='Command', value=name)
                help_embed.add_field(name='Description', value=value)
                help_embed.add_field(name='_ _', value='_ _')
                name = ''
                value = ''
            else:
                name += cmds[i] + '\n'
                value += descriptions[i] + '\n'
        if len(name) > 0 or len(value) > 0:
            help_embed.add_field(name='Command', value=name)
            help_embed.add_field(name='Description', value=value)
            help_embed.add_field(name='_ _', value='_ _')
        await ctx.send(embed=help_embed)


def setup(bot: DCSServerBot):
    # help is only available on the master
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(Help(bot, HelpListener))
