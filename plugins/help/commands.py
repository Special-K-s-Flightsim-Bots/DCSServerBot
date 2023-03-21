import discord
import os
import string
from core import DCSServerBot, Plugin, Report, ReportEnv
from discord.ext import commands
from discord.ui import View, Select, Button
from typing import cast, Optional
from .listener import HelpListener


class HelpAgent(Plugin):
    pass


class HelpMaster(HelpAgent):

    class HelpView(View):
        def __init__(self, bot: DCSServerBot, ctx: commands.Context, options: list[discord.SelectOption]):
            super().__init__()
            self.bot = bot
            self.ctx = ctx
            self.options = options
            select: Select = cast(Select, self.children[0])
            select.options = options
            self.index = 0
            self.result = None
            if self.index == 0:
                self.children[1].disabled = True
                self.children[2].disabled = True
            elif self.index == len(self.options) - 1:
                self.children[3].disabled = True
                self.children[4].disabled = True

        async def print_command(self, *, command: str) -> Optional[discord.Embed]:
            command = command.lstrip(self.ctx.prefix)
            if command not in self.bot.all_commands:
                return None
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

        async def print_commands(self, *, plugin: str) -> discord.Embed:
            commands = [x for x in self.bot.commands if x.module == plugin]
            title = f'{self.bot.user.display_name} Help'
            help_embed = discord.Embed(title=title, color=discord.Color.blue())
            if plugin != '__main__':
                help_embed.description = '**Plugin: ' + string.capwords(plugin.split('.')[1]) + '**\n'
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
                help_embed.add_field(name='There are no commands for your role in this plugin.', value='_ _')
            help_embed.set_footer(text='Use .help [command] if you want help for a specific command.')
            return help_embed

        async def paginate(self, plugin: str, interaction: discord.Interaction):
            embed = await self.print_commands(plugin=plugin)
            if self.index == 0:
                self.children[1].disabled = True
                self.children[2].disabled = True
                self.children[3].disabled = False
                self.children[4].disabled = False
            elif self.index == len(self.options) - 1:
                self.children[1].disabled = False
                self.children[2].disabled = False
                self.children[3].disabled = True
                self.children[4].disabled = True
            else:
                self.children[1].disabled = False
                self.children[2].disabled = False
                self.children[3].disabled = False
                self.children[4].disabled = False
            await interaction.response.edit_message(view=self, embed=embed)

        @discord.ui.select(placeholder="Select the plugin you want help for")
        async def callback(self, interaction: discord.Interaction, select: Select):
            self.index = [x.value for x in self.options].index(select.values[0])
            await self.paginate(select.values[0], interaction)

        @discord.ui.button(label="<<", style=discord.ButtonStyle.secondary)
        async def on_start(self, interaction: discord.Interaction, button: Button):
            self.index = 0
            await self.paginate(self.options[self.index].value, interaction)

        @discord.ui.button(label="Back", style=discord.ButtonStyle.primary)
        async def on_left(self, interaction: discord.Interaction, button: Button):
            self.index -= 1
            await self.paginate(self.options[self.index].value, interaction)

        @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
        async def on_right(self, interaction: discord.Interaction, button: Button):
            self.index += 1
            await self.paginate(self.options[self.index].value, interaction)

        @discord.ui.button(label=">>", style=discord.ButtonStyle.secondary)
        async def on_end(self, interaction: discord.Interaction, button: Button):
            self.index = len(self.options) - 1
            await self.paginate(self.options[self.index].value, interaction)

        @discord.ui.button(label="Quit", style=discord.ButtonStyle.red)
        async def on_cancel(self, interaction: discord.Interaction, button: Button):
            await interaction.response.defer()
            self.stop()

        async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
            if interaction.user != self.ctx.author:
                await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
                return False
            else:
                return True

    @commands.command(name='help', description='The help command')
    @commands.guild_only()
    async def help(self, ctx, command: Optional[str]):
        options = [
            discord.SelectOption(label=string.capwords(x),
                                 value=f'plugins.{x}.commands') for x in sorted(self.bot.plugins) if x != 'help'
        ]
        options.insert(0, discord.SelectOption(label='Core', value='__main__'))
        view = self.HelpView(self.bot, ctx, options)
        msg = None
        if command:
            embed = await view.print_command(command=command)
            if embed:
                await ctx.send(embed=embed)
            else:
                await ctx.send(f'Command {command} not found.')
        else:
            try:
                # shall we display a custom report as greeting page?
                if os.path.exists(f'reports/{self.plugin_name}/{self.plugin_name}.json'):
                    report = Report(self.bot, self.plugin_name, filename=f'{self.plugin_name}.json')
                    env: ReportEnv = await report.render(guild=self.bot.guilds[0],
                                                         servers=[
                                                             {
                                                                 "display_name": x.display_name,
                                                                 "password": x.settings['password'],
                                                                 "status": string.capwords(x.status.name),
                                                                 "num_players": len(x.get_active_players())
                                                             } for x in self.bot.servers.values()
                                                         ])
                    embed = env.embed
                    if env.filename:
                        msg = await ctx.send(embed=embed, view=view,
                                             file=discord.File(env.filename, filename=os.path.basename(env.filename)) if env.filename else None)
                    else:
                        msg = await ctx.send(embed=embed, view=view)
                else:
                    embed = await view.print_commands(plugin='__main__')
                    msg = await ctx.send(embed=embed, view=view)
                if await view.wait() or not view.result:
                    return
            except Exception as ex:
                self.log.exception(ex)
            finally:
                await ctx.message.delete()
                if msg:
                    await msg.delete()


async def setup(bot: DCSServerBot):
    # help is only available on the master
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(HelpMaster(bot, HelpListener))
    else:
        await bot.add_cog(HelpAgent(bot, HelpListener))
