import discord
from discord.ext import commands
from core import DCSServerBot, Plugin
from typing import Optional
from .listener import HelpListener


class Help(Plugin):

    async def print_all(self, ctx) -> discord.Embed:
        help_embed = discord.Embed(color=discord.Color.blue())
        help_embed.title = f'{self.bot.member.name} Commands'
        cmds = []
        descriptions = []
        for command in self.bot.commands:
            if command.hidden:
                continue
            predicates = command.checks
            if not predicates:
                check = True
            else:
                check = await discord.utils.async_all(predicate(ctx) for predicate in predicates)
            if not check:
                continue
            cmd = f'{ctx.prefix}{command.name}'
            if command.usage is not None:
                cmd += ' ' + command.usage
            cmds.append(cmd)
            descriptions.append(f'{command.brief if command.brief else command.description}')
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
        return help_embed

    async def print_command(self, ctx, cmd: str) -> discord.Embed:
        cmd = cmd.lstrip(ctx.prefix)
        command = self.bot.all_commands[cmd]
        predicates = command.checks
        if not predicates:
            check = True
        else:
            check = await discord.utils.async_all(predicate(ctx) for predicate in predicates)
        if not check:
            raise PermissionError
        help_embed = discord.Embed(color=discord.Color.blue())
        help_embed.title = f'Command: {ctx.prefix}{command.name}'
        help_embed.description = command.description
        usage = f'{ctx.prefix}{cmd}'
        if command.usage:
            usage += f' {command.usage}'
        elif command.params:
            usage += ' ' + ' '.join([f'<{name}>' if param.required else f'[{name}]' for name, param in command.params.items()])
        help_embed.add_field(name='Usage', value=usage, inline=False)
        if command.usage:
            help_embed.set_footer(text='<> mandatory, [] non-mandatory')
        if command.aliases:
            help_embed.add_field(name='Aliases', value=','.join([f'{ctx.prefix}{x}' for x in command.aliases]), inline=False)
        return help_embed

    @commands.command(name='help', description='The help command!')
    async def help(self, ctx, cmd: Optional[str]):
        if not cmd:
            help_embed = await self.print_all(ctx)
        else:
            try:
                help_embed = await self.print_command(ctx, cmd)
            except PermissionError:
                await ctx.send("You don't have the permission to use this command.")
                return
        await ctx.send(embed=help_embed)


async def setup(bot: DCSServerBot):
    # help is only available on the master
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(Help(bot, HelpListener))
