import discord
import os

from core import Plugin, Report, ReportEnv, command, Command, utils
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Select, Button, Modal, TextInput
from functools import cache
from services import DCSServerBot
from typing import cast, Optional, Literal

from .listener import HelpListener


@cache
async def get_commands(interaction: discord.Interaction) -> dict[str, app_commands.Command]:
    cmds: dict[str, app_commands.Command] = dict()
    for cmd in interaction.client.tree.get_commands(guild=interaction.guild):
        if isinstance(cmd, app_commands.Group):
            basename = cmd.name
            for inner in cmd.commands:
                if await inner._check_can_run(interaction):
                    cmds['/' + basename + ' ' + inner.name] = inner
        elif await cmd._check_can_run(interaction):
            cmds['/' + cmd.name] = cmd
    return cmds


def get_usage(command: discord.app_commands.Command) -> str:
    return ' '.join([
        f"<{param.name}>" if param.required else f"[{param.name}]"
        for param in command.parameters
    ])

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
        for cmd in interaction.client.commands:
            if not cmd.enabled or cmd.hidden or (current and current.casefold() not in cmd.name):
                continue
            if not isinstance(cmd, discord.ext.commands.core.Command):
                continue
            if await cmd.can_run(ctx):
                ret.append(app_commands.Choice(name=cmd.name, value=cmd.name))
        for cmd in interaction.client.tree.get_commands():
            if current and current.casefold() not in cmd.name:
                continue
            if not isinstance(cmd, discord.ext.commands.hybrid.HybridAppCommand) and \
                    not isinstance(cmd, Command):
                continue
            if await cmd._check_can_run(interaction):
                ret.append(app_commands.Choice(name=cmd.name, value=cmd.name))
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

        async def print_command(self, interaction: discord.Interaction, *, name: str) -> Optional[discord.Embed]:
            _name = name.lstrip('/')
            parts = _name.split()
            if len(parts) == 2:
                group = parts[0]
                _name = parts[1]
            else:
                group = None

            for cmd in interaction.client.tree.get_commands(guild=interaction.guild):
                if group and isinstance(cmd, app_commands.Group) and cmd.name == group:
                    for inner in cmd.commands:
                        if inner.name == _name:
                            cmd = inner
                            break
                    else:
                        return None
                    break
                elif not group and isinstance(cmd, app_commands.Command) and cmd.name == _name:
                    break
            else:
                return None
            if not await cmd._check_can_run(interaction):
                raise PermissionError()
            help_embed = discord.Embed(color=discord.Color.blue())
            help_embed.title = f"Command: {name}"
            help_embed.description = cmd.description
            usage = get_usage(cmd)
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
            for name, cmd in (await get_commands(interaction)).items():
                if cmd.module == plugin:
                    cmds.append(name + ' ' + get_usage(cmd))
                    descriptions.append(cmd.description)
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
        ephemeral = utils.get_ephemeral(interaction)
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
                    await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
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
                            ephemeral=ephemeral)
                    else:
                        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                else:
                    embed = await view.print_commands(interaction, plugin='plugins.help.commands')
                    await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
                if await view.wait() or not view.result:
                    return
            except Exception as ex:
                self.log.exception(ex)
            finally:
                await interaction.delete_original_response()

    @command(description='Generate Documentation')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def doc(self, interaction: discord.Interaction, role: Literal['Admin', 'DCS Admin', 'DCS'],
                  channel: Optional[discord.TextChannel] = None):

        class DocModal(Modal):
            header = TextInput(label="Header", default="## DCSServerBot Commands", style=discord.TextStyle.short,
                              required=True)
            intro = TextInput(label="Intro", style=discord.TextStyle.long, required=True)

            def __init__(derived, role: str):
                super().__init__(title="Generate Documentation")
                derived.role = role
                derived.intro.default = f"""
The following bot commands can be used in this discord by members that have the {derived.role} role:
_ _ 
            """

            async def on_submit(derived, interaction: discord.Interaction):
                await interaction.response.defer()

        modal = DocModal(role=role)
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not channel:
            channel = interaction.channel
        await channel.send(modal.header.value + '\n' + modal.intro.value)
        message = ""
        for cmd in sorted((await get_commands(interaction)).values(), key=lambda x: x.qualified_name):
            for check in cmd.checks:
                try:
                    if (('has_role.' in check.__qualname__ and check.role == role) or
                            ('has_roles.' in check.__qualname__ and role in check.roles)):
                        message += f'**/{cmd.qualified_name}** {get_usage(cmd)}\n' + cmd.description.strip('\n') + '\n\n'
                        if len(message) > 1900:
                            await channel.send(message)
                            message = ""
                    break
                except AttributeError as ex:
                    self.log.error("Name: {} has no attribute '{}'".format(cmd.name, ex.name))
            else:
                message += f'**/{cmd.qualified_name}** {get_usage(cmd)}\n' + cmd.description + '\n\n'
        if message:
            await channel.send(message)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Help(bot, HelpListener))
