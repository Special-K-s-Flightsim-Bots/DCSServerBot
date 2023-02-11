import discord
import string
from core import DCSServerBot, Plugin
from discord import Interaction
from discord.ext import commands
from discord.ui import View, Select, Button
from typing import cast, Optional
from .listener import HelpListener


class Help(Plugin):

    class HelpView(View):
        def __init__(self, bot: DCSServerBot, ctx: commands.Context, options: list[discord.SelectOption]):
            super().__init__()
            self.bot = bot
            self.ctx = ctx
            select: Select = cast(Select, self.children[0])
            select.options = options
            self.result = None

        async def print_command(self, *, command: str) -> discord.Embed:
            command = command.lstrip(self.ctx.prefix)
            cmd = self.bot.all_commands[command]
            predicates = cmd.checks
            if not predicates:
                check = True
            else:
                check = await discord.utils.async_all(predicate(self.ctx) for predicate in predicates)
            if not check:
                raise PermissionError
            help_embed = discord.Embed(color=discord.Color.blue())
            help_embed.title = f'Command: {self.ctx.prefix}{cmd.name}'
            help_embed.description = cmd.description
            usage = f'{self.ctx.prefix}{cmd.name}'
            if cmd.usage:
                usage += f' {cmd.usage}'
            elif cmd.params:
                usage += ' ' + ' '.join(
                    [f'<{name}>' if param.required else f'[{name}]' for name, param in cmd.params.items()])
            help_embed.add_field(name='Usage', value=usage, inline=False)
            if cmd.usage:
                help_embed.set_footer(text='<> mandatory, [] non-mandatory')
            if cmd.aliases:
                help_embed.add_field(name='Aliases', value=','.join([f'{self.ctx.prefix}{x}' for x in cmd.aliases]),
                                     inline=False)
            return help_embed

        async def print_commands(self, *, module: str) -> discord.Embed:
            commands = [x for x in self.bot.commands if x.module == module]
            title = f'{self.bot.user.display_name} Help'
            help_embed = discord.Embed(title=title, color=discord.Color.blue())
            if module != '__main__':
                help_embed.description = '**Module: ' + string.capwords(module.split('.')[1]) + '**\n'
            else:
                help_embed.description = '**Core Commands**\n'
            cmds = []
            descriptions = []
            for command in commands:
                if command.hidden:
                    continue
                predicates = command.checks
                if not predicates:
                    check = True
                else:
                    check = await discord.utils.async_all(predicate(self.ctx) for predicate in predicates)
                if not check:
                    continue
                cmd = f'{self.ctx.prefix}{command.name}'
                if command.usage is not None:
                    cmd += ' ' + command.usage
                cmds.append(cmd)
                descriptions.append(f'{command.brief if command.brief else command.description}')
            if cmds:
                help_embed.add_field(name='Command', value='\n'.join(cmds))
                help_embed.add_field(name='Description', value='\n'.join(descriptions))
                help_embed.add_field(name='_ _', value='_ _')
            else:
                help_embed.add_field(name='There are no commands for your role in this module.', value='_ _')
            help_embed.set_footer(text='Use .help [command] if you want help for a specific command.')
            return help_embed

        @discord.ui.select(placeholder="Select the plugin you want help for")
        async def callback(self, interaction: Interaction, select: Select):
            embed = await self.print_commands(module=select.values[0])
            await interaction.response.edit_message(view=self, embed=embed)

        @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary, emoji='❌')
        async def cancel(self, interaction: Interaction, button: Button):
            self.result = None
            await interaction.response.defer()
            self.stop()

        async def interaction_check(self, interaction: Interaction, /) -> bool:
            if interaction.user != self.ctx.author:
                await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
                return False
            else:
                return True

    @commands.command(name='help', description='The help command')
    async def help(self, ctx, command: Optional[str]):
        options = [discord.SelectOption(label=string.capwords(x), value=f'plugins.{x}.commands') for x in sorted(self.bot.plugins) if x != 'help']
        options.insert(0, discord.SelectOption(label='Core', value='__main__'))
        view = self.HelpView(self.bot, ctx, options)
        if command:
            embed = await view.print_command(command=command)
        else:
            embed = await view.print_commands(module='__main__')
        msg = await ctx.send(embed=embed, view=view)
        try:
            if await view.wait():
                return
            elif not view.result:
                return
        finally:
            await ctx.message.delete()
            await msg.delete()


async def setup(bot: DCSServerBot):
    # help is only available on the master
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(Help(bot, HelpListener))
