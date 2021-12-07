import discord
from discord.ext import commands


class Help(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='help',
                      description='The help command!',
                      usage='<cog>')
    async def help(self, ctx, cog='all'):
        help_embed = discord.Embed(title="DCSServerBot Commands", color=discord.Color.blue())
        # help_embed.set_thumbnail(url=self.bot.user.avatar_url)
        # Get a list of all cogs
        cogs = [c for c in self.bot.cogs.keys()]

        if cog == 'all':
            for cog in cogs:
                if cog.lower() != 'help':
                    help_embed.add_field(name='**' + cog + '**',
                                         value=f'```{ctx.prefix}help {cog.lower()}```', inline=True)
            pass
        else:
            # If the cog was specified
            lower_cogs = [c.lower() for c in cogs]
            # If the cog actually exists.
            if cog.lower() in lower_cogs:
                cmds = ''
                descriptions = ''
                # Get a list of all commands in the specified cog
                commands_list = self.bot.get_cog(cogs[lower_cogs.index(cog.lower())]).get_commands()
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
                help_embed.add_field(name='Command', value=cmds)
                help_embed.add_field(name='Description', value=descriptions)
            else:
                # Ignore unknown command, as it might have been for other bots
                return
        help_embed.set_footer(text='Support Discord: https://discord.gg/zjRateN')
        await ctx.send(embed=help_embed)


def setup(bot):
    bot.add_cog(Help(bot))
