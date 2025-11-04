import discord
import os
import pandas as pd

from core import Plugin, Report, ReportEnv, command, utils, get_translation, Status, async_cache, Command, Port, Node
from discord import app_commands, Interaction, ButtonStyle, TextStyle, SelectOption
from discord.ui import View, Select, Button, Modal, TextInput, Item
from io import BytesIO
from services.bot import DCSServerBot
from typing import cast, Literal, Any

from .listener import HelpListener

_ = get_translation(__name__.split('.')[1])


@async_cache
async def get_commands(interaction: discord.Interaction) -> dict[str, app_commands.Command]:
    cmds: dict[str, app_commands.Command] = dict()
    for cmd in interaction.client.tree.get_commands(guild=interaction.guild):
        if isinstance(cmd, app_commands.Group):
            for inner in cmd.commands:
                if await inner._check_can_run(interaction):
                    cmds[inner.qualified_name] = inner
        elif await cmd._check_can_run(interaction):
            cmds[cmd.name] = cmd
    ctx = await interaction.client.get_context(interaction)
    for name, cmd in interaction.client.all_commands.items():
        # noinspection PyUnresolvedReferences
        if cmd.enabled and await cmd.can_run(ctx):
            cmds[name] = cmd
    return cmds


def get_usage(cmd: discord.app_commands.Command | Command) -> str:
    if isinstance(cmd, Command):
        return ' '.join([
            f"<{param.name.lstrip('_')}>" if param.required else f"[{param.name.lstrip('_')}]"
            for param in cmd.parameters
        ])
    else:
        # noinspection PyUnresolvedReferences
        return cmd.signature

async def commands_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        prefix = interaction.client.locals.get('command_prefix', '.')
        return [
            app_commands.Choice(
                name="{prefix}{name}".format(prefix='/' if isinstance(cmd, Command) else prefix, name=name),
                value=name
            )
            for name, cmd in sorted((await get_commands(interaction)).items())
            if not current or current.casefold() in name.casefold()
        ][:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


class Help(Plugin[HelpListener]):

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
                # noinspection PyUnresolvedReferences
                self.children[1].disabled = True
                # noinspection PyUnresolvedReferences
                self.children[2].disabled = True
            elif self.index == len(self.options) - 1:
                # noinspection PyUnresolvedReferences
                self.children[3].disabled = True
                # noinspection PyUnresolvedReferences
                self.children[4].disabled = True

        async def print_command(self, interaction: discord.Interaction, *, name: str) -> discord.Embed | None:
            cmds = await get_commands(interaction)
            cmd = cmds.get(name)
            if not cmd:
                return None

            prefix = interaction.client.locals.get('command_prefix', '.')
            # noinspection PyUnresolvedReferences
            fqn = cmd.mention if isinstance(cmd, Command) else f"{prefix}{cmd.name}"
            help_embed = discord.Embed(color=discord.Color.blue())
            help_embed.title = _("Command: {}").format(fqn)
            help_embed.description = cmd.description
            usage = get_usage(cmd)
            help_embed.add_field(name=_('Usage'), value=f"{fqn} {usage}", inline=False)
            help_embed.add_field(name=_('Plugin'), value=cmd.binding.__class__.__name__, inline=False)
            if usage:
                help_embed.set_footer(text=_('<> mandatory, [] non-mandatory'))
            return help_embed

        async def print_commands(self, interaction: discord.Interaction, *, plugin: str) -> discord.Embed:
            prefix = self.bot.locals.get('command_prefix', '.')
            module = f'plugins.{plugin.lower()}.commands'
            title = _('{} Help').format(self.bot.user.display_name)
            help_embed = discord.Embed(title=title, color=discord.Color.blue())
            help_embed.description = f'**Plugin: {plugin}**\n'
            cmds = ""
            descriptions = ""
            for name, cmd in (await get_commands(interaction)).items():
                if cmd.module != module:
                    continue
                # noinspection PyUnresolvedReferences
                fqn = cmd.mention if isinstance(cmd, Command) else f"{prefix}{cmd.name}"
                new_cmd = f"{fqn} {get_usage(cmd)}\n"
                new_desc = f"{cmd.description}\n"
                if len(cmds + new_cmd) > 1024 or len(descriptions + new_desc) > 1024:
                    if cmds.strip():  # Only add if there's something besides whitespace
                        help_embed.add_field(name=_('Command'), value=cmds, inline=True)
                        help_embed.add_field(name=_('Description'), value=descriptions, inline=True)
                        help_embed.add_field(name='_ _', value='_ _', inline=True)
                    cmds = new_cmd
                    descriptions = new_desc
                else:
                    cmds += new_cmd
                    descriptions += new_desc

            if cmds.strip():  # Add any remaining commands/descriptions
                help_embed.add_field(name=_('Command'), value=cmds, inline=True)
                help_embed.add_field(name=_('Description'), value=descriptions, inline=True)
                help_embed.add_field(name='_ _', value='_ _', inline=True)
            elif not help_embed.fields:  # If the embed has no fields, there were no commands for the plugin
                help_embed.add_field(name=_('There are no commands for your role in this plugin.'), value='_ _')

            help_embed.set_footer(text=_('Use /help [command] if you want help for a specific command.'))
            return help_embed

        async def paginate(self, plugin: str, interaction: discord.Interaction):
            embed = await self.print_commands(interaction, plugin=plugin)
            target_children = self.children[1:5]
            if self.index == 0:
                new_states = [True, True, False, False]
            elif self.index == len(self.options) - 1:
                new_states = [False, False, True, True]
            else:
                new_states = [False, False, False, False]

            # Set new 'disabled' state
            for child, state in zip(target_children, new_states):
                if hasattr(child, 'disabled'):  # Check if attribute exists
                    # noinspection PyUnresolvedReferences
                    child.disabled = state
            # noinspection PyUnresolvedReferences
            await interaction.response.edit_message(view=self, embed=embed)

        @discord.ui.select(placeholder=_("Select the plugin you want help for"))
        async def callback(self, interaction: discord.Interaction, select: Select):
            self.index = [x.value for x in self.options].index(select.values[0])
            await self.paginate(select.values[0], interaction)

        # noinspection PyTypeChecker
        @discord.ui.button(label="<<", style=ButtonStyle.secondary)
        async def on_start(self, interaction: discord.Interaction, _: Button):
            self.index = 0
            await self.paginate(self.options[self.index].value, interaction)

        # noinspection PyTypeChecker
        @discord.ui.button(label="Back", style=ButtonStyle.primary)
        async def on_left(self, interaction: discord.Interaction, _: Button):
            self.index -= 1
            await self.paginate(self.options[self.index].value, interaction)

        # noinspection PyTypeChecker
        @discord.ui.button(label="Next", style=ButtonStyle.primary)
        async def on_right(self, interaction: discord.Interaction, _: Button):
            self.index += 1
            await self.paginate(self.options[self.index].value, interaction)

        # noinspection PyTypeChecker
        @discord.ui.button(label=">>", style=ButtonStyle.secondary)
        async def on_end(self, interaction: discord.Interaction, _: Button):
            self.index = len(self.options) - 1
            await self.paginate(self.options[self.index].value, interaction)

        # noinspection PyTypeChecker
        @discord.ui.button(label="Quit", style=ButtonStyle.red)
        async def on_cancel(self, interaction: discord.Interaction, _: Button):
            # noinspection PyUnresolvedReferences
            await interaction.response.defer()
            self.stop()

        async def on_error(self, interaction: Interaction, error: Exception, item: Item[Any], /) -> None:
            self.bot.log.exception(error)

    @command(description=_('The help command'))
    @app_commands.guild_only()
    @app_commands.autocomplete(cmd=commands_autocomplete)
    async def help(self, interaction: discord.Interaction, cmd: str | None):
        ephemeral = utils.get_ephemeral(interaction)
        options = [
            discord.SelectOption(label=plugin.__cog_name__, value=plugin.__cog_name__)
            for name, plugin in sorted(self.bot.cogs.items())
            if name != 'Help'
        ][:25]
        view = self.HelpView(self.bot, interaction, options)
        if cmd:
            try:
                embed = await view.print_command(interaction, name=cmd)
                if embed:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
                else:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Command {} not found.').format(cmd), ephemeral=True)
            except PermissionError:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("You don't have the permission to use this command."),
                                                        ephemeral=True)
        else:
            try:
                # shall we display a custom report as a greeting page?
                if os.path.exists(f'reports/{self.plugin_name}/{self.plugin_name}.json'):
                    report = Report(self.bot, self.plugin_name, filename=f'{self.plugin_name}.json')
                    env: ReportEnv = await report.render(
                        guild=self.bot.guilds[0],
                        servers=[
                            {
                                "display_name": x.display_name,
                                "password": x.settings['password'],
                                "status": x.status.name.title(),
                                "num_players": len(x.get_active_players())
                            } for x in self.bot.servers.values()
                        ]
                    )
                    embed = env.embed
                    if env.filename:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(
                            embed=embed, view=view,
                            file=discord.File(env.filename,
                                              filename=os.path.basename(env.filename)) if env.filename else None,
                            ephemeral=ephemeral)
                    else:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                else:
                    embed = await view.print_commands(interaction, plugin='Help')
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
                if await view.wait() or not view.result:
                    return
            except Exception as ex:
                self.log.exception(ex)
            finally:
                await interaction.delete_original_response()

    async def discord_commands_to_df(self, interaction: discord.Interaction, *,
                                     use_mention: bool | None = False) -> pd.DataFrame:
        df = pd.DataFrame(columns=['Plugin', 'Command', 'Parameter', 'Roles', 'Description'])
        for cmd in sorted((await get_commands(interaction)).values(), key=lambda x: x.qualified_name):
            for check in cmd.checks:
                try:
                    if 'has_role.' in check.__qualname__:
                        # noinspection PyUnresolvedReferences
                        roles = [check.role]
                    elif 'has_roles.' in check.__qualname__:
                        # noinspection PyUnresolvedReferences
                        roles = check.roles
                    else:
                        continue
                    plugin = cmd.binding.__cog_name__ if cmd.binding else ''
                    # noinspection PyUnresolvedReferences
                    data_df = pd.DataFrame(
                        [(plugin, f"/{cmd.qualified_name}" if not use_mention else cmd.mention,
                          get_usage(cmd), ','.join(roles), cmd.description.strip('\n'))],
                        columns=df.columns)
                    df = pd.concat([df, data_df], ignore_index=True)
                    break
                except AttributeError as ex:
                    self.log.error("Name: {} has no attribute '{}'".format(cmd.name, ex.name))
            else:
                plugin = cmd.binding.__cog_name__  if cmd.binding else ''
                data_df = pd.DataFrame(
                    [(plugin, '/' + cmd.qualified_name, get_usage(cmd), '', cmd.description.strip('\n'))],
                    columns=df.columns)
                df = pd.concat([df, data_df], ignore_index=True)
        return df

    async def ingame_commands_to_df(self) -> pd.DataFrame:
        df = pd.DataFrame(columns=['Plugin', 'Command', 'Parameter', 'Roles', 'Description'])
        for listener in self.bot.eventListeners:
            for cmd in listener.chat_commands:
                data_df = pd.DataFrame([
                    (listener.plugin.__cog_name__, listener.prefix + cmd.name, cmd.usage, ','.join(cmd.roles), cmd.help)
                ], columns=df.columns)
                df = pd.concat([df, data_df], ignore_index=True)
        return df

    async def generate_commands_doc(self, interaction: discord.Interaction, fmt: Literal['channel', 'xls'],
                                    role: Literal['Admin', 'DCS Admin', 'DCS'] | None = None,
                                    channel: discord.TextChannel | None = None):
        class DocModal(Modal):
            header = TextInput(label="Header", default=_("## DCSServerBot Commands"), style=TextStyle.short,
                               required=True)
            intro = TextInput(label="Intro", style=TextStyle.long, required=True)

            def __init__(derived, role: str | None):
                super().__init__(title=_("Generate Documentation"))
                derived.role = role
                if role:
                    derived.intro.default = _("""
The following bot commands can be used in this discord by members that have the {} role:
_ _ 
                    """).format(derived.role)
                else:
                    derived.intro.default = _("""
The following bot commands can be used in this discord:
_ _ 
                    """)

            async def on_submit(derived, interaction: discord.Interaction):
                # noinspection PyUnresolvedReferences
                await interaction.response.defer()

        if fmt == 'xls':
            # noinspection PyUnresolvedReferences
            await interaction.response.defer()
            discord_commands = (await self.discord_commands_to_df(interaction)).sort_values(['Plugin', 'Command'])
            ingame_commands = (await self.ingame_commands_to_df()).sort_values(['Plugin', 'Command'])
            output = BytesIO()
            with pd.ExcelWriter(output) as writer:
                discord_commands.to_excel(writer, sheet_name='Discord Commands', index=False)
                ingame_commands.to_excel(writer, sheet_name='In-Game Commands', index=False)
                for worksheet in [writer.sheets['Discord Commands'], writer.sheets['In-Game Commands']]:
                    # Apply a filter to all the columns.
                    worksheet.auto_filter.ref = worksheet.calculate_dimension()

                    # Get the max length of content in columns and resize the lengths
                    for col in worksheet.columns:
                        max_length = 0
                        column = col[0]
                        for cell in col:
                            try:
                                if len(str(cell.value)) > max_length:
                                    max_length = len(cell.value)
                            except Exception:
                                pass
                        adjusted_width = max_length + 3  # Add buffer width
                        worksheet.column_dimensions[column.column_letter].width = adjusted_width

            output.seek(0)
            # noinspection PyUnresolvedReferences
            await interaction.followup.send(file=discord.File(fp=output, filename='DCSSB-Commands.xlsx'))
            output.close()
        elif role:
            modal = DocModal(role=role)
            # noinspection PyUnresolvedReferences
            await interaction.response.send_modal(modal)
            await modal.wait()
            if not channel:
                channel = interaction.channel
            await channel.send(modal.header.value + '\n' + modal.intro.value)
            message = ""
            discord_commands = await self.discord_commands_to_df(interaction, use_mention=True)
            for index, row in discord_commands.iterrows():
                if not role or role in row['Roles'].split(','):
                    new_message = f"**{row['Command']}** {row['Parameter']}\n{row['Description']}\n\n"
                    if len(message + new_message) > 2000:
                        await channel.send(message)
                        message = '_ _\n'
                    message += new_message
            if message:
                await channel.send(message)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Please provide a role for channel output."), ephemeral=True)

    async def server_info_to_df(self) -> pd.DataFrame:
        columns = ['Node', 'Instance', 'Name', 'Password', 'Max Players', 'DCS Port', 'Bot Port']
        df = pd.DataFrame(columns=columns)

        for server in self.bot.servers.values():
            server_dict = {
                'Node': server.node.name,
                'Instance': server.instance.name,
                'Name': server.name,
                'Password': server.settings.get('password'),
                'Max Players': server.settings.get('maxPlayers', 16),
                'DCS Port': repr(server.instance.dcs_port),
                'WebGUI Port': repr(server.instance.webgui_port),
                'Bot Port': repr(server.instance.bot_port)
            }

            if server.status == Status.SHUTDOWN:
                await server.init_extensions()
            # all extension ports
            for ext in server.instance.locals.get('extensions', {}).keys():
                try:
                    rc = await server.run_on_extension(ext, 'get_ports')
                    for key, value in rc.items():
                        server_dict[key] = repr(value)
                except ValueError:
                    pass

            data_df = pd.DataFrame([server_dict])
            df = pd.concat([df, data_df], ignore_index=True)

        df = df[columns + [col for col in df.columns if col not in columns]]
        return df

    async def nodes_info_to_df(self) -> pd.DataFrame:
        columns = ['Node', 'Listen Port']
        df = pd.DataFrame(columns=columns)

        for node in self.node.all_nodes.values():
            if not node:
                continue
            node_dict = await node.info()
            for k, v in node_dict.copy().items():
                if isinstance(v, Port):
                    node_dict[k] = repr(v)
            data_df = pd.DataFrame([node_dict])
            df = pd.concat([df, data_df], ignore_index=True)

        return df

    async def generate_server_docs(self, interaction: discord.Interaction):
        def adjust_columns(worksheet):
            # Apply a filter to all the columns.
            worksheet.auto_filter.ref = worksheet.calculate_dimension()

            # Get the max length of content in columns and resize the lengths
            for col in worksheet.columns:
                max_length = 0
                column = col[0]
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(cell.value)
                    except Exception:
                        pass
                adjusted_width = max_length + 3  # Add buffer width
                worksheet.column_dimensions[column.column_letter].width = adjusted_width

        await interaction.followup.send("Generating server documentation... Please wait a moment.", ephemeral=True)
        node_info = (await self.nodes_info_to_df()).sort_values(['Node'])
        server_info = (await self.server_info_to_df()).sort_values(['Node', 'Instance'])
        output = BytesIO()
        with pd.ExcelWriter(output) as writer:
            node_info.to_excel(writer, sheet_name='Node Info', index=False)
            server_info.to_excel(writer, sheet_name='Server Info', index=False)
            for sheet in ['Node Info', 'Server Info']:
                worksheet = writer.sheets[sheet]
                adjust_columns(worksheet)
        output.seek(0)
        await interaction.followup.send(file=discord.File(fp=output, filename='ServerInfo.xlsx'), ephemeral=True)
        output.close()

    async def generate_firewall_rules(self, interaction: discord.Interaction, node: Node) -> str:
        ports: list[Port] = []
        for server in self.bot.servers.values():
            ports.append(server.instance.dcs_port)
            ports.append(server.instance.webgui_port)

            for ext in server.instance.locals.get('extensions', {}).keys():
                try:
                    rc = await server.run_on_extension(ext, 'get_ports')
                    for key, value in rc.items():
                        if value.public:
                            ports.append(value)
                except ValueError:
                    pass
        info = await node.info()
        for k, v in info.items():
            if isinstance(v, Port):
                if v.public:
                    ports.append(v)

        return utils.generate_firewall_rules(ports)

    @command(description=_('Generate Documentation'))
    @app_commands.guild_only()
    @app_commands.rename(fmt='format')
    @utils.app_has_role('Admin')
    async def doc(self, interaction: discord.Interaction,
                  what: Literal['Command Overview', 'Server Config Sheet', 'Firewall Ruleset'],
                  fmt: Literal['channel', 'xls'] | None = None,
                  role: Literal['Admin', 'DCS Admin', 'DCS'] | None = None,
                  channel: discord.TextChannel | None = None):
        if what == 'Command Overview':
            if not fmt:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("Please specify the format (channel or xls)."),
                                                        ephemeral=True)
                return
            await self.generate_commands_doc(interaction, fmt, role, channel)
        elif what == 'Server Config Sheet':
            if not await utils.yn_question(interaction, _("Do you want to generate the server documentation?"),
                                       message=_("The file may contain passwords!")):
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("Aborted."), ephemeral=True)
                return
            await self.generate_server_docs(interaction)
        elif what == 'Firewall Ruleset':
            all_nodes = list(self.node.all_nodes.values())
            idx = await utils.selection(interaction,
                                   title="Select a node",
                                   options=[
                                       SelectOption(label=x.name, value=str(idx))
                                       for idx, x in enumerate(all_nodes)
                                       if x is not None
                                   ])
            if idx:
                rules = await self.generate_firewall_rules(interaction, all_nodes[int(idx)])
                file = discord.File(fp=BytesIO(rules.encode('utf-8')), filename='firewall_rules.ps1')
                # noinspection PyUnresolvedReferences
                if not interaction.response.is_done():
                    # noinspection PyUnresolvedReferences
                    await interaction.response.defer(ephemeral=True)
                await interaction.followup.send(content=_("Your firewall ruleset:"), file=file, ephemeral=True)
            else:
                await interaction.followup.send(_("Aborted."), ephemeral=True)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Unknown option {}!").format(what), ephemeral=True)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Help(bot, HelpListener))
