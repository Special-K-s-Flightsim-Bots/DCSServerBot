import discord
import string
from discord.ext import commands
from core import DCSServerBot, Plugin


class Help(Plugin):

    @commands.command(name='help',
                      description='The help command!',
                      usage='<plugin>')
    async def help(self, ctx, plugin='all'):

        help_embed = discord.Embed(color=discord.Color.blue())
        if plugin == 'all':
            help_embed.title = 'DCSServerBot Plugins'
            for p in self.bot.plugins:
                if p.lower() != 'help':
                    help_embed.add_field(name='**' + string.capwords(p) + '**',
                                         value=f'```{ctx.prefix}help {p.lower()}```', inline=True)
            pass
        else:
            help_embed.title = f'{string.capwords(plugin)} Commands'
            if plugin in self.bot.plugins:
                cmds = ''
                descriptions = ''
                # Get a list of all commands for the specified plugin
                for cog in self.bot.cogs.values():
                    if f'.{plugin}.' in type(cog).__module__:
                        commands_list = self.bot.get_cog(type(cog).__name__).get_commands()
                        for command in commands_list:
                            if command.hidden is False:
                                cmds += f'{ctx.prefix}{command.name}'
                                # Also add aliases, if there are any
                                if len(command.aliases) > 0:
                                    cmds += f' / {" / ".join(command.aliases)}'
                                if command.usage is not None:
                                    cmds += ' ' + command.usage
                                cmds += '\n'
                                descriptions += f'{command.description}\n'
                if len(cmds) == 0:
                    cmds = 'No commands.'
                if len(descriptions) == 0:
                    descriptions = '_ _'
                help_embed.add_field(name='Command', value=cmds)
                help_embed.add_field(name='Description', value=descriptions)
            else:
                # Ignore unknown command, as it might have been for other bots
                return
        await ctx.send(embed=help_embed)


def setup(bot: DCSServerBot):
    # help is only available on the master
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(Help('help', bot))
