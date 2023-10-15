import discord
import os

from core import Plugin, Report, ReportEnv, command, Command
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Select, Button
from functools import cache
from services import DCSServerBot
from typing import cast, Optional

from .listener import HelpListener


@cache
async def get_commands(interaction: discord.Interaction) -> dict[str, app_commands.Command]:
    commands: dict[str, app_commands.Command] = dict()
    for command in interaction.client.tree.get_commands(guild=interaction.guild):
        if isinstance(command, app_commands.Group):
            basename = command.name
            for inner in command.commands:
                if await inner._check_can_run(interaction):
                    commands['/' + basename + ' ' + inner.name] = inner
        elif await command._check_can_run(interaction):
            commands['/' + command.name] = command
    return commands


async def commands_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        return [
            app_commands.Choice(name=name, value=name)
            for name, command in sorted((await get_commands(interaction)).items())
            if not current or current.casefold() in name.casefold()
        ][:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def command_picker(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        ctx = await commands.Context.from_interaction(interaction)
        ret = list()
        for command in interaction.client.commands:
            if not command.enabled or command.hidden or (current and current.casefold() not in command.name):
                continue
            if not isinstance(command, discord.ext.commands.core.Command):
                continue
            if await command.can_run(ctx):
                ret.append(app_commands.Choice(name=command.name, value=command.name))
        for command in interaction.client.tree.get_commands():
            if current and current.casefold() not in command.name:
                continue
            if not isinstance(command, discord.ext.commands.hybrid.HybridAppCommand) and \
                    not isinstance(command, Command):
                continue
            if await command._check_can_run(interaction):
                ret.append(app_commands.Choice(name=command.name, value=command.name))
        return sorted(ret, key=lambda x: x.name)[:25]
    except Exception as ex:
        print(ex)


class Help(Plugin):

    class HelpView(View):
        def __init__(self, bot: DCSServerBot, interaction: discord.Interaction, options: list[discord.SelectOption]):
            super().__init__()
            self.bot = bot
            self.interaction = interaction
            self.prefix = self.bot.node.config.get('chat_command_prefix', '-')
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

        @staticmethod
        def get_usage(command: discord.app_commands.Command) -> str:
            return ' '.join([
                f"<{param.name}>" if param.required else f"[{param.name}]"
                for param in command.parameters
            ])

        async def print_command(self, interaction: discord.Interaction, *, name: str) -> Optional[discord.Embed]:
            _name = name.lstrip('/')
            parts = _name.split()
            if len(parts) == 2:
                group = parts[0]
                _name = parts[1]
            else:
                group = None

            for command in interaction.client.tree.get_commands(guild=interaction.guild):
                if group and isinstance(command, app_commands.Group) and command.name == group:
                    for inner in command.commands:
                        if inner.name == _name:
                            command = inner
                            break
                    else:
                        return None
                    break
                elif not group and isinstance(command, app_commands.Command) and command.name == _name:
                    break
            else:
                return None
            if not await command._check_can_run(interaction):
                raise PermissionError()
            help_embed = discord.Embed(color=discord.Color.blue())
            help_embed.title = f"Command: {name}"
            help_embed.description = command.description
            usage = self.get_usage(command)
            help_embed.add_field(name='Usage', value=f"{name} {usage}", inline=False)
            if usage:
                help_embed.set_footer(text='<> mandatory, [] non-mandatory')
            return help_embed

        async def print_commands(self, interaction: discord.Interaction, *, plugin: str) -> discord.Embed:
            title = f'{self.bot.user.display_name} Help'
            help_embed = discord.Embed(title=title, color=discord.Color.blue())
            help_embed.description = '**Plugin: ' + plugin.split('.')[1].title() + '**\n'
            cmds = []
            descriptions = []
            for name, command in (await get_commands(interaction)).items():
                if command.module == plugin:
                    cmds.append(name + ' ' + self.get_usage(command))
                    descriptions.append(command.description)
            if cmds:
                help_embed.add_field(name='Command', value='\n'.join(cmds))
                help_embed.add_field(name='Description', value='\n'.join(descriptions))
                help_embed.add_field(name='_ _', value='_ _')
            else:
                help_embed.add_field(name='There are no commands for your role in this plugin.', value='_ _')
            help_embed.set_footer(text='Use /help [command] if you want help for a specific command.')
            return help_embed

        async def paginate(self, plugin: str, interaction: discord.Interaction):
            embed = await self.print_commands(interaction, plugin=plugin)
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

    @command(description='The help command')
    @app_commands.guild_only()
    @app_commands.autocomplete(command=commands_autocomplete)
    async def help(self, interaction: discord.Interaction, command: Optional[str]):
        options = [
            discord.SelectOption(label=x.title(), value=f'plugins.{x}.commands')
            for x in sorted(self.bot.plugins)
            if x != 'help'
        ]
        view = self.HelpView(self.bot, interaction, options)
        if command:
            try:
                embed = await view.print_command(interaction, name=command)
                if embed:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(f'Command {command} not found.', ephemeral=True)
            except PermissionError:
                await interaction.response.send_message("You don't have the permission to use this command.",
                                                        ephemeral=True)
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
                                                                 "status": x.status.name.title(),
                                                                 "num_players": len(x.get_active_players())
                                                             } for x in self.bot.servers.values()
                                                         ])
                    embed = env.embed
                    if env.filename:
                        await interaction.response.send_message(
                            embed=embed, view=view,
                            file=discord.File(env.filename,
                                              filename=os.path.basename(env.filename)) if env.filename else None,
                            ephemeral=True)
                    else:
                        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                else:
                    embed = await view.print_commands(interaction, plugin='plugins.help.commands')
                    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                if await view.wait() or not view.result:
                    return
            except Exception as ex:
                self.log.exception(ex)
            finally:
                await interaction.delete_original_response()


async def setup(bot: DCSServerBot):
    await bot.add_cog(Help(bot, HelpListener))
