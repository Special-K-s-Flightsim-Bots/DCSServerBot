from __future__ import annotations
import asyncio
import functools
import discord
import os
import re

from core import Status
from datetime import datetime
from discord import app_commands, Interaction, SelectOption
from discord.app_commands import Choice, TransformerError
from discord.ext import commands
from discord.ui import Button, View, Select
from enum import Enum, auto
from typing import Optional, cast, Union, TYPE_CHECKING, Iterable, Any

from .helper import get_all_players, is_ucid

if TYPE_CHECKING:
    from core import Server, DCSServerBot, Player, ServiceBus, Node, Instance

__all__ = [
    "PlayerType",
    "wait_for_single_reaction",
    "input_value",
    "selection_list",
    "selection",
    "yn_question",
    "populated_question",
    "check_roles",
    "has_role",
    "has_roles",
    "app_has_role",
    "app_has_not_role",
    "app_has_roles",
    "app_has_not_roles",
    "cmd_has_roles",
    "format_embed",
    "embed_to_text",
    "embed_to_simpletext",
    "escape_string",
    "get_interaction_param",
    "get_all_linked_members",
    "NodeTransformer",
    "InstanceTransformer",
    "ServerTransformer",
    "UserTransformer",
    "PlayerTransformer",
    "bans_autocomplete",
    "airbase_autocomplete",
    "mission_autocomplete",
    "mizfile_autocomplete",
    "plugins_autocomplete",
    "available_modules_autocomplete",
    "installed_modules_autocomplete",
    "player_modules_autocomplete",
    "server_selection"
]


class PlayerType(Enum):
    ALL = auto()
    PLAYER = auto()
    MEMBER = auto()
    HISTORY = auto()


async def wait_for_single_reaction(bot: DCSServerBot, interaction: discord.Interaction,
                                   message: discord.Message) -> discord.Reaction:
    def check_press(react: discord.Reaction, user: discord.Member):
        return (react.message.channel == interaction.channel) & (user == member) & (react.message.id == message.id)

    tasks = [
        asyncio.create_task(bot.wait_for('reaction_add', check=check_press)),
        asyncio.create_task(bot.wait_for('reaction_remove', check=check_press))
    ]
    try:
        member = interaction.user
        done, tasks = await asyncio.wait(tasks, timeout=120, return_when=asyncio.FIRST_COMPLETED)
        if len(done) > 0:
            react, _ = done.pop().result()
            return react
        else:
            raise asyncio.TimeoutError
    finally:
        for task in tasks:
            task.cancel()


async def input_value(bot: DCSServerBot, interaction: discord.Interaction, message: Optional[str] = None,
                      delete: Optional[bool] = False, timeout: Optional[float] = 300.0):
    def check(m):
        return (m.channel == interaction.channel) & (m.author == interaction.user)

    msg = response = None
    try:
        if message:
            if interaction.response.is_done():
                msg = await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
                msg = await interaction.original_response()
        response = await bot.wait_for('message', check=check, timeout=timeout)
        return response.content if response.content != '.' else None
    finally:
        if delete:
            if msg:
                await msg.delete()
            if response:
                await response.delete()


async def selection_list(bot: DCSServerBot, interaction: discord.Interaction, data: list, embed_formatter, num: int = 5,
                         marker: int = -1, marker_emoji='🔄'):
    message = None
    try:
        j = 0
        while len(data) > 0:
            max_i = (len(data) % num) if (len(data) - j * num) < num else num
            embed = embed_formatter(data[j * num:j * num + max_i],
                                    (marker - j * num) if marker in range(j * num, j * num + max_i + 1) else 0,
                                    marker_emoji)
            message = await interaction.followup.send(embed=embed)
            if j > 0:
                await message.add_reaction('◀️')
            for i in range(1, max_i + 1):
                if (j * num + i) != marker:
                    await message.add_reaction(chr(0x30 + i) + '\u20E3')
                else:
                    await message.add_reaction(marker_emoji)
            await message.add_reaction('⏹️')
            if ((j + 1) * num) < len(data):
                await message.add_reaction('▶️')
            react = await wait_for_single_reaction(bot, interaction, message)
            await message.delete()
            if react.emoji == '◀️':
                j -= 1
                message = None
            elif react.emoji == '▶️':
                j += 1
                message = None
            elif react.emoji == '⏹️':
                return -1
            elif react.emoji == marker_emoji:
                return marker - 1
            elif (len(react.emoji) > 1) and ord(react.emoji[0]) in range(0x31, 0x39):
                return (ord(react.emoji[0]) - 0x31) + j * num
        return -1
    except asyncio.TimeoutError:
        if message:
            await message.delete()
        return -1


class SelectView(View):
    def __init__(self, *, placeholder: str, options: list[SelectOption], min_values: int, max_values: int):
        super().__init__()
        self.result = None
        select: Select = cast(Select, self.children[0])
        select.placeholder = placeholder
        select.options = options
        select.min_values = min_values
        select.max_values = max_values

    @discord.ui.select()
    async def callback(self, interaction: Interaction, select: Select):
        if not interaction.response.is_done():
            await interaction.response.defer()
        if select.max_values > 1:
            self.result = select.values
        else:
            self.result = select.values[0]
        self.stop()

    @discord.ui.button(label='OK', style=discord.ButtonStyle.green, custom_id='sl_ok')
    async def on_ok(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red, custom_id='sl_cancel')
    async def on_cancel(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        self.result = None
        self.stop()


async def selection(interaction: Union[discord.Interaction, commands.Context], *, title: Optional[str] = None,
                    placeholder: Optional[str] = None, embed: discord.Embed = None,
                    options: list[SelectOption], min_values: Optional[int] = 1,
                    max_values: Optional[int] = 1, ephemeral: bool = False) -> Optional[Union[list, str]]:
    if len(options) == 1:
        return options[0].value
    if not embed and title:
        embed = discord.Embed(description=title, color=discord.Color.blue())
    view = SelectView(placeholder=placeholder, options=options, min_values=min_values, max_values=max_values)
    msg = None
    try:
        if isinstance(interaction, discord.Interaction):
            if interaction.response.is_done():
                msg = await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
                msg = await interaction.original_response()
        else:
            msg = await interaction.send(embed=embed, view=view)
        if await view.wait():
            return None
        return view.result
    finally:
        if msg:
            await msg.delete()


class YNQuestionView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.result = False

    @discord.ui.button(label='Yes', style=discord.ButtonStyle.green, custom_id='yn_yes')
    async def on_yes(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        self.result = True
        self.stop()

    @discord.ui.button(label='No', style=discord.ButtonStyle.red, custom_id='yn_no')
    async def on_no(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        self.result = False
        self.stop()


async def yn_question(ctx: Union[commands.Context, discord.Interaction], question: str,
                      message: Optional[str] = None) -> bool:
    embed = discord.Embed(description=question, color=discord.Color.red())
    if message is not None:
        embed.add_field(name=message, value='_ _')
    if isinstance(ctx, discord.Interaction):
        ctx = await ctx.client.get_context(ctx)
    view = YNQuestionView()
    msg = await ctx.send(embed=embed, view=view, ephemeral=True)
    try:
        if await view.wait():
            return False
        return view.result
    finally:
        await msg.delete()


class PopulatedQuestionView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.result = None

    @discord.ui.button(label='Yes', style=discord.ButtonStyle.green, custom_id='pl_yes')
    async def on_yes(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        self.result = 'yes'
        self.stop()

    @discord.ui.button(label='Later', style=discord.ButtonStyle.primary, custom_id='pl_later', emoji='⏱')
    async def on_later(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        self.result = 'later'
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red, custom_id='pl_cancel')
    async def on_cancel(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        self.stop()


async def populated_question(interaction: discord.Interaction, question: str, message: Optional[str] = None) -> Optional[str]:
    embed = discord.Embed(title='People are flying!', description=question, color=discord.Color.red())
    if message is not None:
        embed.add_field(name=message, value='_ _')
    view = PopulatedQuestionView()
    if interaction.response.is_done():
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        msg = await interaction.original_response()
    try:
        if await view.wait():
            return None
        return view.result
    finally:
        await msg.delete()


def check_roles(roles: Iterable[Union[str, int]], member: discord.Member) -> bool:
    for role in member.roles:
        for valid_role in roles:
            if isinstance(valid_role, str) and role.name == valid_role:
                return True
            elif isinstance(valid_role, int) and role.id == valid_role:
                return True
    return False


def has_role(role: str):
    def predicate(ctx: commands.Context) -> bool:
        return check_roles([role], ctx.author)

    return commands.check(predicate)


def app_has_role(role: str):
    def predicate(interaction: Interaction) -> bool:
        return check_roles(interaction.client.roles[role], interaction.user)

    return app_commands.check(predicate)


def has_roles(roles: list[str]):
    def predicate(ctx: commands.Context) -> bool:
        return check_roles(roles, ctx.author)

    return commands.check(predicate)


def cmd_has_roles(roles: list[str]):
    def predicate(interaction: Interaction) -> bool:
        valid_roles = []
        for role in roles:
            valid_roles.extend(interaction.client.roles[role])
        return check_roles(set(valid_roles), interaction.user)

    @functools.wraps(predicate)
    async def wrapper(interaction: Interaction):
        return predicate(interaction)

    cmd_has_roles.predicate = wrapper

    return cmd_has_roles


def app_has_roles(roles: list[str]):
    def predicate(interaction: Interaction) -> bool:
        valid_roles = []
        for role in roles:
            valid_roles.extend(interaction.client.roles[role])
        return check_roles(set(valid_roles), interaction.user)

    return app_commands.check(predicate)


def app_has_not_role(role: str):
    def predicate(interaction: Interaction) -> bool:
        return not check_roles(interaction.client[role], interaction.user)

    return app_commands.check(predicate)


def app_has_not_roles(roles: list[str]):
    def predicate(interaction: Interaction) -> bool:
        invalid_roles = []
        for role in roles:
            invalid_roles.extend(interaction.client.roles[role])
        return not check_roles(set(invalid_roles), interaction.user)

    return app_commands.check(predicate)


def format_embed(data: dict) -> discord.Embed:
    color = data['color'] if 'color' in data else discord.Color.blue()
    embed = discord.Embed(color=color)
    if 'title' in data:
        embed.title = data['title'] or '_ _'
    if 'description' in data:
        embed.description = data['description'] or '_ _'
    if 'img' in data and isinstance(data['img'], str):
        embed.set_image(url=data['img'])
    if 'image' in data and isinstance(data['image'], dict):
        if 'url' in data['image']:
            embed.set_image(url=data['image']['url'])
    if 'footer' in data:
        if isinstance(data['footer'], str):
            embed.set_footer(text=data['footer'])
        else:
            text = data['footer']['text'] if 'text' in data['footer'] else None
            icon_url = data['footer']['icon_url'] if 'icon_url' in data['footer'] else None
            embed.set_footer(text=text, icon_url=icon_url)
    if 'fields' in data:
        if isinstance(data['fields'], dict):
            for name, value in data['fields'].items():
                embed.add_field(name=name or '_ _', value=value or '_ _')
        elif isinstance(data['fields'], list):
            for field in data['fields']:
                name = field['name'] if 'name' in field else None
                value = field['value'] if 'value' in field else None
                inline = field['inline'] if 'inline' in field else False
                embed.add_field(name=name or '_ _', value=value or '_ _', inline=inline)
    if 'author' in data:
        name = data['author']['name'] if 'name' in data['author'] else None
        url = data['author']['url'] if 'url' in data['author'] else None
        icon_url = data['author']['icon_url'] if 'icon_url' in data['author'] else None
        embed.set_author(name=name, url=url, icon_url=icon_url)
    if 'timestamp' in data:
        embed.timestamp = datetime.strptime(data['timestamp'], '%Y-%m-%dT%H:%M:%S.%fZ')
    return embed


def embed_to_text(embed: discord.Embed) -> str:
    def rows(line: str) -> list[str]:
        return line.splitlines()

    message = []
    if embed.title:
        message.append(embed.title.upper())
    if embed.description:
        message.append(embed.description)
    message.append('')
    row = len(message)
    message.append('')
    col = 0
    pos = [0, 0]
    for field in embed.fields:
        name = field.name if field.name != '_ _' else ''
        if not field.inline:
            if len(message[row]) > 0:
                message.append('')
            message[row] += name
            col = 0
            pos = [0, 0]
            row = len(message)
            message.append('')
            continue
        if col > 0:
            message[row] += ' ' * (pos[col - 1] - len(message[row])) + '| '
        message[row] += name
        if col < 2:
            pos[col] = len(message[row]) + 1
        value = field.value if field.value != '_ _' else ''
        lines = rows(value)
        if len(message) < (row + len(lines) + 1):
            for i in range(len(message), row + len(lines) + 1):
                message.append('')
        for j in range(0, len(lines)):
            if col > 0:
                message[row + 1 + j] += ' ' * (pos[col - 1] - len(message[row + 1 + j])) + '| '
            message[row + 1 + j] += lines[j]
            if col < 2 and (len(message[row + 1 + j]) + 1) > pos[col]:
                pos[col] = len(message[row + 1 + j]) + 1
        if field.inline:
            col += 1
            if col == 3:
                row = len(message)
                col = 0
                pos = [0, 0]
                message.append('')
    return '\n'.join(message)


def embed_to_simpletext(embed: discord.Embed) -> str:
    message = ''
    if embed.title:
        message += embed.title.upper() + '\n' + '=' * len(embed.title) + '\n'
    if embed.description:
        message += embed.description + '\n'
    message += '\n'
    for field in embed.fields:
        name = field.name if field.name != '_ _' else ''
        value = field.value if field.value != '_ _' else ''
        if name and value:
            if field.inline:
                message += name + ': ' + ' | '.join(value.splitlines()) + '\n'
            else:
                message += name + '\n' + value + '\n'
        elif name.startswith('▬'):
            message += name
        else:
            message += name + value + '\n'
        if not field.inline:
            message += '\n'
    if embed.footer and embed.footer.text:
        message += '\n' + embed.footer.text
    return message


def escape_string(msg: str) -> str:
    return re.sub(r"([\*\_~])", r"\\\1", msg)


def get_interaction_param(interaction: discord.Interaction, name: str) -> Optional[Any]:
    def inner(root: Union[dict, list]) -> Optional[Any]:
        if isinstance(root, dict):
            if root.get('name') == name:
                return root.get('value')
        elif isinstance(root, list):
            for param in root:
                if 'options' in param:
                    return inner(param['options'])
                if param['name'] == name:
                    return param['value']
        return None

    return inner(interaction.data['options'])


def get_all_linked_members(bot: DCSServerBot) -> list[discord.Member]:
    members: list[discord.Member] = []
    with bot.pool.connection() as conn:
        for row in conn.execute("SELECT discord_id FROM players WHERE discord_id <> -1"):
            member = bot.guilds[0].get_member(row[0])
            if member:
                members.append(member)
    return members


class ServerTransformer(app_commands.Transformer):

    def __init__(self, *, status: list[Status] = None):
        super().__init__()
        self.status = status

    async def transform(self, interaction: discord.Interaction, value: Optional[str]) -> Server:
        if value:
            server = interaction.client.servers.get(value)
            if not server:
                raise TransformerError(value, self.type, self)
        else:
            server = await interaction.client.get_server(interaction)
        return server

    async def autocomplete(self, interaction: discord.Interaction, current: str) -> list[Choice[str]]:
        try:
            server: Server = await interaction.client.get_server(interaction)
            if server and (not self.status or server.status in self.status):
                return [Choice(name=server.name, value=server.name)]
            choices: list[Choice[str]] = [
                Choice(name=name, value=name)
                for name, value in interaction.client.servers.items()
                if (not self.status or value.status in self.status) and current.casefold() in name.casefold()
            ]
            return choices[:25]
        except Exception as ex:
            interaction.client.log.exception(ex)


class NodeTransformer(app_commands.Transformer):

    async def transform(self, interaction: discord.Interaction, value: Optional[str]) -> Node:
        if value:
            return next(x.node for x in interaction.client.servers.values() if x.node.name == value)
        else:
            return interaction.client.node

    async def autocomplete(self, interaction: discord.Interaction, current: str) -> list[Choice[str]]:
        all_nodes = [interaction.client.node.name]
        all_nodes.extend(interaction.client.node.get_active_nodes())
        return [
            app_commands.Choice(name=x, value=x)
            for x in all_nodes
            if not current or current.casefold() in x.casefold()
        ]


class InstanceTransformer(app_commands.Transformer):

    def __init__(self, *, unused: bool = False):
        super().__init__()
        self.unused = unused

    async def transform(self, interaction: discord.Interaction, value: Optional[str]) -> Optional[Instance]:
        if value:
            node: Node = await NodeTransformer().transform(interaction, get_interaction_param(interaction, 'node'))
            if not node:
                return None
            return next(x for x in node.instances if x.name == value)
        elif len(interaction.client.node.instances) == 1:
            return interaction.client.node.instances[0]
        else:
            return None

    async def autocomplete(self, interaction: discord.Interaction, current: str) -> list[Choice[str]]:
        node: Node = await NodeTransformer().transform(interaction, get_interaction_param(interaction, 'node'))
        if not node:
            return []
        if self.unused:
            all_instances = [instance for server_name, instance in await node.find_all_instances()]
            for instance in node.instances:
                all_instances.remove(instance.name)
            instances = all_instances
        else:
            instances = [x.name for x in node.instances]
        return [
            app_commands.Choice(name=x, value=x)
            for x in instances
            if not current or current.casefold() in x.casefold()
        ]


async def bans_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    choices: list[app_commands.Choice[int]] = [
        app_commands.Choice(name=x['name'] or x['ucid'], value=x['ucid'])
        for x in interaction.client.bus.bans()
        if not current or current.casefold() in x['name'].casefold() or current.casefold() in x['ucid'].casefold()
    ]
    return choices[:25]


async def airbase_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    server: Server = await ServerTransformer().transform(interaction, get_interaction_param(interaction, 'server'))
    if not server:
        return []
    choices: list[app_commands.Choice[int]] = [
        app_commands.Choice(name=x['name'], value=idx)
        for idx, x in enumerate(server.current_mission.airbases)
        if not current or current.casefold() in x['name'].casefold() or current.casefold() in x['code'].casefold()
    ]
    return choices[:25]


async def mission_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    try:
        server: Server = await ServerTransformer().transform(interaction, get_interaction_param(interaction, 'server'))
        if not server:
            return []
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=os.path.basename(x)[:-4], value=idx)
            for idx, x in enumerate(server.settings['missionList'])
            if not current or current.casefold() in x[:-4].casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def mizfile_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    try:
        server: Server = await ServerTransformer().transform(interaction, get_interaction_param(interaction, 'server'))
        if not server:
            return []
        installed_missions = [os.path.expandvars(x) for x in server.settings['missionList']]
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=os.path.basename(x)[:-4], value=idx)
            for idx, x in enumerate(await server.listAvailableMissions())
            if x not in installed_missions and current.casefold() in os.path.basename(x).casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def plugins_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=x, value=x.lower())
        for x in interaction.client.cogs
        if not current or current.casefold() in x.casefold()
    ]


async def available_modules_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    try:
        node = await NodeTransformer().transform(interaction, get_interaction_param(interaction, "node"))
        userid = node.locals['DCS'].get('dcs_user')
        password = node.locals['DCS'].get('dcs_password')
        available_modules = set(await node.get_available_modules(userid, password)) - set(await node.get_installed_modules())
        return [
            app_commands.Choice(name=x, value=x)
            for x in available_modules
            if not current or current.casefold() in x.casefold()
        ]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def installed_modules_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    try:
        node = await NodeTransformer().transform(interaction, get_interaction_param(interaction, "node"))
        available_modules = await node.get_installed_modules()
        return [
            app_commands.Choice(name=x, value=x)
            for x in available_modules
            if not current or current.casefold() in x.casefold()
        ]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def player_modules_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:

    def get_modules(ucid: str) -> list[str]:
        with interaction.client.pool.connection() as conn:
            return [row[0] for row in conn.execute("""
                SELECT DISTINCT slot, COUNT(*) FROM statistics 
                WHERE player_ucid =  %s 
                AND slot NOT IN ('', '?', '''forward_observer', 'instructor', 'observer', 'artillery_commander') 
                GROUP BY 1 ORDER BY 2 DESC
            """, (ucid, )).fetchall()]

    try:
        user = await UserTransformer().transform(interaction, get_interaction_param(interaction, "user"))
        if isinstance(user, str):
            ucid = user
        else:
            ucid = interaction.client.get_ucid_by_member(user)
        if not ucid:
            return []
        return [
            app_commands.Choice(name=x, value=x)
            for x in get_modules(ucid)
            if not current or current.casefold() in x.casefold()
        ]
    except Exception as ex:
        interaction.client.log.exception(ex)


class UserTransformer(app_commands.Transformer):

    def __init__(self, *, sel_type: PlayerType = PlayerType.ALL, linked: Optional[bool] = None):
        super().__init__()
        self.sel_type = sel_type
        self.linked = linked

    async def transform(self, interaction: discord.Interaction, value: str) -> Optional[Union[discord.Member, str]]:
        if value:
            if is_ucid(value):
                return interaction.client.get_member_by_ucid(value) or value
            else:
                return interaction.client.guilds[0].get_member(int(value))
        else:
            return interaction.user

    async def autocomplete(self, interaction: Interaction, current: str) -> list[Choice[str]]:
        ret = []
        if self.sel_type in [PlayerType.ALL, PlayerType.PLAYER]:
            ret.extend([
                app_commands.Choice(name='✈ ' + name + ' (' + ucid + ')', value=ucid)
                for ucid, name in get_all_players(interaction.client, self.linked)
                if not current or current.casefold() in name.casefold() or current.casefold() in ucid
            ])
        if (self.linked is None or self.linked) and self.sel_type in [PlayerType.ALL, PlayerType.MEMBER]:
            ret.extend([
                app_commands.Choice(name='@' + member.display_name, value=str(member.id))
                for member in get_all_linked_members(interaction.client)
                if not current or current.casefold() in member.display_name.casefold()
            ])
        return ret[:25]


class PlayerTransformer(app_commands.Transformer):

    def __init__(self, *, active: bool = False):
        super().__init__()
        self.active = active

    async def transform(self, interaction: discord.Interaction, value: str) -> Player:
        server: Server = await interaction.client.get_server(interaction)
        return server.get_player(ucid=value, active=self.active)

    async def autocomplete(self, interaction: Interaction, current: str) -> list[Choice[str]]:
        try:
            if self.active:
                server: Server = await interaction.client.get_server(interaction)
                choices: list[app_commands.Choice[str]] = [
                    app_commands.Choice(name=x.name, value=x.ucid)
                    for x in server.get_active_players()
                    if current.casefold() in x.name.casefold()
                ]
            else:
                choices = [
                    app_commands.Choice(name=f"{ucid} ({name})", value=ucid)
                    for ucid, name in get_all_players(interaction.client)
                    if not current or current.casefold() in name.casefold() or current.casefold() in ucid
                ]
            return choices[:25]
        except Exception as ex:
            interaction.client.log.exception(ex)


async def server_selection(bus: ServiceBus,
                           interaction: Union[discord.Interaction, commands.Context], *, title: str,
                           multi_select: Optional[bool] = False) -> Optional[Union[Server, list[Server]]]:
    all_servers = list(bus.servers.keys())
    if len(all_servers) == 0:
        return []
    elif len(all_servers) == 1:
        return [bus.servers[all_servers[0]]]
    if multi_select:
        max_values = len(all_servers)
    else:
        max_values = 1
    s = await selection(interaction, title=title,
                        options=[SelectOption(label=x, value=x) for x in all_servers],
                        max_values=max_values, ephemeral=True)
    if multi_select:
        return [bus.servers[x] for x in s]
    else:
        return bus.servers[s]
