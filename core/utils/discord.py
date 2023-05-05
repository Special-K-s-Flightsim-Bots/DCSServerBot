from __future__ import annotations
import asyncio
import discord
import os
import re
from core import Status
from dataclasses import dataclass
from datetime import datetime
from discord import Interaction, app_commands, SelectOption
from discord.app_commands import Choice, TransformerError
from discord.ext import commands
from discord.ui import Button, View, Select
from pathlib import Path, PurePath
from typing import Optional, cast, Union, TYPE_CHECKING, List
from .helper import get_all_players, is_ucid

from . import config

if TYPE_CHECKING:
    from .. import Server, DCSServerBot, Player


async def wait_for_single_reaction(bot: DCSServerBot, ctx: Union[commands.Context, discord.DMChannel],
                                   message: discord.Message) -> discord.Reaction:
    def check_press(react: discord.Reaction, user: discord.Member):
        return (react.message.channel == message.channel) & (user == member) & (react.message.id == message.id)

    tasks = [
        asyncio.create_task(bot.wait_for('reaction_add', check=check_press)),
        asyncio.create_task(bot.wait_for('reaction_remove', check=check_press))
    ]
    try:
        member = ctx.message.author if isinstance(ctx, commands.Context) else ctx.recipient
        done, tasks = await asyncio.wait(tasks, timeout=120, return_when=asyncio.FIRST_COMPLETED)
        if len(done) > 0:
            react, _ = done.pop().result()
            return react
        else:
            raise asyncio.TimeoutError
    finally:
        for task in tasks:
            task.cancel()


async def input_multiline(bot: DCSServerBot, ctx: commands.Context, message: Optional[str] = None,
                          delete: Optional[bool] = False, timeout: Optional[float] = 300.0) -> str:
    def check(m):
        return (m.channel == ctx.message.channel) & (m.author == ctx.message.author)

    msgs: list[discord.Message] = list()
    try:
        if message:
            msgs.append(await ctx.send(message))
        response = await bot.wait_for('message', check=check, timeout=timeout)
        if input:
            msgs.append(response)
        retval = ""
        while response.content != '.':
            retval += response.content + '\n'
            msgs.append(response)
            response = await bot.wait_for('message', check=check, timeout=timeout)
        return retval
    finally:
        if delete:
            for msg in msgs:
                await msg.delete()


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


async def pagination(bot: DCSServerBot, interaction: discord.Interaction, data: list, embed_formatter, num: int = 10):
    if not interaction.response.is_done():
        await interaction.response.defer()
    message = None
    try:
        j = 0
        while len(data) > 0:
            max_i = (len(data) % num) if (len(data) - j * num) < num else num
            embed = embed_formatter(data[j * num:j * num + max_i])
            message = await interaction.followup.send(embed=embed)
            wait = False
            if j > 0:
                await message.add_reaction('◀️')
                wait = True
            if j > 0 or ((j + 1) * num) < len(data):
                await message.add_reaction('⏹️')
            if ((j + 1) * num) < len(data):
                await message.add_reaction('▶️')
                wait = True
            if wait:
                react = await wait_for_single_reaction(bot, ctx, message)
                await message.delete()
                if react.emoji == '◀️':
                    j -= 1
                    message = None
                elif react.emoji == '▶️':
                    j += 1
                    message = None
                elif react.emoji == '⏹️':
                    return -1
            else:
                return
    except asyncio.TimeoutError:
        if message:
            await message.delete()
            return -1


async def selection_list(bot: DCSServerBot, ctx: commands.Context, data: list, embed_formatter, num: int = 5,
                         marker: int = -1, marker_emoji='🔄'):
    message = None
    try:
        j = 0
        while len(data) > 0:
            max_i = (len(data) % num) if (len(data) - j * num) < num else num
            embed = embed_formatter(data[j * num:j * num + max_i],
                                    (marker - j * num) if marker in range(j * num, j * num + max_i + 1) else 0,
                                    marker_emoji)
            message = await ctx.send(embed=embed)
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
            react = await wait_for_single_reaction(bot, ctx, message)
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

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        if interaction.user != (await interaction.original_response()).user:
            await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
            return False
        else:
            return True


async def selection(interaction: discord.Interaction, *, title: Optional[str] = None, placeholder: Optional[str] = None,
                    embed: discord.Embed = None, options: list[SelectOption], min_values: Optional[int] = 1,
                    max_values: Optional[int] = 1, ephemeral: bool = False) -> Optional[str]:
    if len(options) == 1:
        return options[0].value
    if not embed and title:
        embed = discord.Embed(description=title, color=discord.Color.blue())
    view = SelectView(placeholder=placeholder, options=options, min_values=min_values, max_values=max_values)
    msg = None
    try:
        if interaction.response.is_done():
            msg = interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
            msg = await interaction.original_response()
        if await view.wait():
            return None
        return view.result
    finally:
        if msg:
            await msg.delete()


async def multi_selection_list(bot: DCSServerBot, ctx: commands.Context, data: list, embed_formatter) -> list[int]:
    def check_ok(react: discord.Reaction, user: discord.Member):
        return (react.message.channel == ctx.message.channel) & (user == ctx.message.author) & (react.emoji == '🆗')

    retval = list[int]()
    message = None
    try:
        embed = embed_formatter(data)
        message = await ctx.send(embed=embed)
        for i in range(0, len(data)):
            await message.add_reaction(chr(0x31 + i) + '\u20E3')
        await message.add_reaction('🆗')
        await bot.wait_for('reaction_add', check=check_ok, timeout=120.0)
        cache_msg = await ctx.fetch_message(message.id)
        for react in cache_msg.reactions:
            if (react.emoji != '🆗') and (react.count > 1):
                if (len(react.emoji) > 1) and ord(react.emoji[0]) in range(0x30, 0x40):
                    retval.append(ord(react.emoji[0]) - 0x31)
    finally:
        if message:
            await message.delete()
    return retval


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


async def yn_question(interaction: discord.Interaction, question: str, message: Optional[str] = None) -> bool:
    embed = discord.Embed(description=question, color=discord.Color.red())
    if message is not None:
        embed.add_field(name=message, value='_ _')
    view = YNQuestionView()
    if interaction.response.is_done():
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        msg = await interaction.original_response()
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
    msg = None
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


def check_roles(roles: list[str], member: discord.Member) -> bool:
    valid_roles = set()
    for role in roles:
        if 'ROLES' not in config or role not in config['ROLES']:
            valid_roles.add(role)
        else:
            valid_roles |= set([x.strip() for x in config['ROLES'][role].split(',')])
    for role in member.roles:
        if role.name in valid_roles:
            return True
    return False


def has_role(role: str):
    def predicate(ctx: commands.Context) -> bool:
        return check_roles([role], ctx.author)

    return commands.check(predicate)


def app_has_role(role: str):
    def predicate(interaction: Interaction) -> bool:
        return check_roles([role], interaction.user)

    return app_commands.check(predicate)


def has_roles(roles: list[str]):
    def predicate(ctx):
        return check_roles(roles, ctx.author)

    return commands.check(predicate)


def app_has_roles(roles: list[str]):
    def predicate(interaction: Interaction) -> bool:
        return check_roles(roles, interaction.user)

    return app_commands.check(predicate)


def has_not_role(role: str):
    def predicate(ctx):
        return not check_roles([role], ctx.author)

    return commands.check(predicate)


def app_has_not_role(role: str):
    def predicate(interaction: Interaction) -> bool:
        return not check_roles([role], interaction.user)

    return app_commands.check(predicate)


def has_not_roles(roles: list[str]):
    def predicate(ctx):
        return not check_roles(roles, ctx.author)

    return commands.check(predicate)


def app_has_not_roles(roles: list[str]):
    def predicate(interaction: Interaction) -> bool:
        return not check_roles(roles, interaction.user)

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


def get_interaction_param(interaction: discord.Interaction, name: str):
    root = interaction.data['options'][0]
    if root.get('options'):
        root = root['options']
    for parameter in root:
        if parameter['name'] == name:
            return parameter['value']


def get_all_linked_members(bot: DCSServerBot) -> list[discord.Member]:
    members: list[discord.Member] = []
    with bot.pool.connection() as conn:
        for row in conn.execute("SELECT discord_id FROM players WHERE discord_id <> -1"):
            members.append(bot.guilds[0].get_member(row[0]))
    return members


class ServerTransformer(app_commands.Transformer):

    def __init__(self, *, status: list[Status] = None):
        super().__init__()
        self.status = status

    async def transform(self, interaction: discord.Interaction, value: str) -> Server:
        server = interaction.client.servers.get(value)
        if not server:
            raise TransformerError(value, self.type, self)
        return server

    async def autocomplete(self, interaction: Interaction, current: str) -> List[Choice[str]]:
        server: Server = await interaction.client.get_server(interaction)
        if server and (not self.status or server.status in self.status):
            return [app_commands.Choice(name=server.name, value=server.name)]
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x, value=x)
            for x in interaction.client.servers.keys()
            if (not self.status or x.status in self.status) and current.casefold() in x.casefold()
        ]
        return choices[:25]


async def airbase_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    server: Server = await ServerTransformer().transform(interaction, get_interaction_param(interaction, "server"))
    if not server:
        return []
    choices: list[app_commands.Choice[int]] = [
        app_commands.Choice(name=x['name'], value=idx)
        for idx, x in enumerate(server.current_mission.airbases)
        if current.casefold() in x['name'].casefold() or current.casefold() in x['code'].casefold()
    ]
    return choices[:25]


async def mission_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    server: Server = await ServerTransformer().transform(interaction, get_interaction_param(interaction, "server"))
    if not server:
        return []
    choices: list[app_commands.Choice[int]] = [
        app_commands.Choice(name=os.path.basename(x)[:-4], value=idx)
        for idx, x in enumerate(server.settings['missionList'])
        if current.casefold() in x.casefold()
    ]
    return choices[:25]


async def mizfile_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    server: Server = await ServerTransformer().transform(interaction, get_interaction_param(interaction, "server"))
    installed_missions = [os.path.expandvars(x) for x in server.settings['missionList']]
    choices: list[app_commands.Choice[str]] = [
        app_commands.Choice(name=x.name[:-4], value=str(x))
        for x in sorted(Path(PurePath(os.path.expandvars(interaction.client.config[server.installation]['DCS_HOME']),
                                      "Missions")).glob("*.miz"))
        if str(x) not in installed_missions and current.casefold() in x.name.casefold()
    ]
    return choices[:25]


class UserTransformer(app_commands.Transformer):
    async def transform(self, interaction: discord.Interaction, value: str) -> Union[discord.Member, str]:
        if value and is_ucid(value):
            return interaction.client.get_member_by_ucid(value) or value
        else:
            return interaction.client.guilds[0].get_member(int(value))

    async def autocomplete(self, interaction: Interaction, current: str) -> List[Choice[str]]:
        players = [
            app_commands.Choice(name='✈ ' + name, value=ucid)
            for idx, ucid, name in enumerate(get_all_players(interaction.client, name=current))
            if idx < 25 and (not current or current.casefold() in name.casefold() or current.casefold() in ucid)
        ]
        members = [
            app_commands.Choice(name='@' + member.display_name, value=str(member.id))
            for idx, member in enumerate(get_all_linked_members(interaction.client))
            if idx < 25 and (not current or current.casefold() in member.display_name.casefold())
        ]
        return players + members


class PlayerTransformer(app_commands.Transformer):

    def __init__(self, *, active: bool = False):
        super().__init__()
        self.active = active

    async def transform(self, interaction: discord.Interaction, value: str) -> Player:
        server: Server = interaction.client.servers[interaction.data['options'][0]['value']]
        return server.get_player(ucid=value, active=True)

    async def autocomplete(self, interaction: Interaction, current: str) -> List[Choice[str]]:
        if self.active:
            server: Server = await ServerTransformer().transform(interaction,
                                                                 get_interaction_param(interaction, "server"))
            choices: list[app_commands.Choice[str]] = [
                app_commands.Choice(name=x.name, value=x.ucid)
                for x in server.get_active_players()
                if current.casefold() in x.name.casefold()
            ]
            return choices[:25]
        else:
            return [
                app_commands.Choice(name=f"{ucid} ({name})", value=ucid)
                for idx, ucid, name in enumerate(get_all_players(interaction.client, name=current))
                if idx < 25 and (not current or current.casefold() in name.casefold() or current.casefold() in ucid)
            ]


@dataclass
class ContextWrapper(commands.Context):
    message: discord.Message

    async def send(self, *args, **kwargs) -> discord.Message:
        return await self.message.channel.send(*args, **kwargs)
