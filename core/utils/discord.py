from __future__ import annotations

import asyncio
import discord
import functools
import logging
import os
import re

from contextlib import suppress
from core import Status, utils
from core.data.node import SortOrder, UploadStatus
from core.services.registry import ServiceRegistry
from core.translations import get_translation
from datetime import datetime, timedelta
from discord import app_commands, Interaction, SelectOption, ButtonStyle
from discord.ext import commands
from discord.ui import Button, View, Select, Item, Modal, TextInput
from enum import Enum, auto
from fuzzywuzzy import fuzz
from packaging.version import parse, Version
from psycopg.rows import dict_row
from typing import Optional, cast, Union, TYPE_CHECKING, Iterable, Any, Callable

from .helper import get_all_players, is_ucid, format_string

if TYPE_CHECKING:
    from core import Server, Player, Node, Instance, Plugin
    from services.bot import DCSServerBot
    from services.servicebus import ServiceBus


__all__ = [
    "DISCORD_FILE_SIZE_LIMIT",
    "PlayerType",
    "wait_for_single_reaction",
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
    "app_has_dcs_version",
    "cmd_has_roles",
    "get_role_ids",
    "format_embed",
    "embed_to_text",
    "embed_to_simpletext",
    "create_warning_embed",
    "escape_string",
    "print_ruler",
    "match",
    "find_similar_names",
    "get_interaction_param",
    "get_all_linked_members",
    "NodeTransformer",
    "InstanceTransformer",
    "ServerTransformer",
    "UserTransformer",
    "PlayerTransformer",
    "airbase_autocomplete",
    "mission_autocomplete",
    "group_autocomplete",
    "date_autocomplete",
    "server_selection",
    "get_ephemeral",
    "get_command",
    "ConfigModal",
    "DirectoryPicker",
    "NodeUploadHandler",
    "ServerUploadHandler",
    "DatabaseModal"
]

# Internationalisation
_ = get_translation('core')
# Logging
logger = logging.getLogger(__name__)

DISCORD_FILE_SIZE_LIMIT = 10 * 1024 * 1024


class PlayerType(Enum):
    """
    Defines the types of players.

    Attributes
    ----------
    ALL : PlayerType
        Represents Discord members and DCS players.
    PLAYER : PlayerType
        Represents DCS players only.
    MEMBER : PlayerType
        Represents Discord members only.
    HISTORY : PlayerType
        Represents historical DCS players.
    """
    ALL = auto()
    PLAYER = auto()
    MEMBER = auto()
    HISTORY = auto()


async def wait_for_single_reaction(interaction: discord.Interaction, message: discord.Message) -> discord.Reaction:
    def check_press(react: discord.Reaction, user: discord.Member):
        return (react.message.channel == interaction.channel) & (user == member) & (react.message.id == message.id)

    member = interaction.user
    pending_tasks = [
        asyncio.create_task(interaction.client.wait_for('reaction_add', check=check_press)),
        asyncio.create_task(interaction.client.wait_for('reaction_remove', check=check_press))
    ]

    done, pending = await asyncio.wait(pending_tasks, timeout=120, return_when=asyncio.FIRST_COMPLETED)

    # cancel pending tasks
    for task in pending:
        task.cancel()
        await task

    if not done:
        raise TimeoutError

    react, _ = done.pop().result()
    return react


async def selection_list(interaction: discord.Interaction, data: list, embed_formatter, num: int = 5,
                         marker: int = -1, marker_emoji='🔄'):
    """
    :param interaction: A discord.Interaction instance representing the interaction event.
    :param data: A list of data to display in the embeds.
    :param embed_formatter: A function that formats the data into an embed.
    :param num: An integer representing the number of data to display per page, default is 5.
    :param marker: An integer representing the marker index, default is -1.
    :param marker_emoji: A string representing the emoji for the marker, default is '🔄'.
    :return: An integer representing the index of the selected item, or -1 if no item is selected or an error occurs.

    This method is used to display a paginated selection list based on the given data. It sends embeds with reaction buttons for navigation and selection. The user can navigate through the
    * pages and select an item.

    Example usage:
    embeds = ["Embed 1", "Embed 1", "Embed 1", "Embed 1", "Embed 1", "Embed 1", "Embed 1", "Embed 1", "Embed 1"]
    await selection_list(bot, interaction, embeds, custom_embed_formatter, num=3)
    """
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
            react = await wait_for_single_reaction(interaction, message)
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
    except (TimeoutError, asyncio.TimeoutError):
        try:
            if message:
                await message.delete()
        except discord.NotFound:
            pass
        return -1


class SelectView(View):
    def __init__(self, *, placeholder: str, options: list[SelectOption], min_values: int, max_values: int):
        super().__init__()
        self.result = None
        select: Select = cast(Select, self.children[0])
        select.placeholder = placeholder
        select.options = options
        self.result = next((x.value for x in options if x.default), None)
        select.min_values = min_values
        select.max_values = max_values

    @discord.ui.select()
    async def callback(self, interaction: Interaction, select: Select):
        # noinspection PyUnresolvedReferences
        if not interaction.response.is_done():
            # noinspection PyUnresolvedReferences
            await interaction.response.defer()
        if select.max_values > 1:
            self.result = select.values
        else:
            self.result = select.values[0]
        self.stop()

    # noinspection PyTypeChecker
    @discord.ui.button(label='OK', style=ButtonStyle.green, custom_id='sl_ok')
    async def on_ok(self, interaction: Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()

    # noinspection PyTypeChecker
    @discord.ui.button(label='Cancel', style=ButtonStyle.red, custom_id='sl_cancel')
    async def on_cancel(self, interaction: Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.result = None
        self.stop()


async def selection(interaction: Union[discord.Interaction, commands.Context], *, title: Optional[str] = None,
                    placeholder: Optional[str] = None, embed: discord.Embed = None,
                    options: list[SelectOption], min_values: Optional[int] = 1,
                    max_values: Optional[int] = 1, ephemeral: bool = False) -> Optional[Union[list, str, int]]:
    """
    This function generates a selection menu on Discord with provided options.
    If only one option is present, it immediately returns that option's value.
    In the case of multiple options, this generates a selection menu in view form.
    Parameters are an interaction or command context, optional title, optional placeholder,
    optional discord embed, list of option selections, minimum and maximum selectable values,
    and an 'ephemeral' boolean to denote if the message should be user-only visible.
    On user selection confirmation, it returns selected result(s) or None if no confirmation.
    """
    if len(options) == 1:
        return options[0].value
    if not embed and title:
        embed = discord.Embed(description=title, color=discord.Color.blue())
    view = SelectView(placeholder=placeholder, options=options, min_values=min_values, max_values=max_values)
    msg = None
    try:
        if isinstance(interaction, discord.Interaction):
            # noinspection PyUnresolvedReferences
            if interaction.response.is_done():
                msg = await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
            else:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
                msg = await interaction.original_response()
        else:
            msg = await interaction.send(embed=embed, view=view)
        if not await view.wait():
            return view.result
    finally:
        if msg:
            with suppress(discord.NotFound):
                await msg.delete()


class YNQuestionView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.result = None

    # noinspection PyTypeChecker
    @discord.ui.button(label='Yes', style=ButtonStyle.green, custom_id='yn_yes')
    async def on_yes(self, interaction: Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.result = True
        self.stop()

    # noinspection PyTypeChecker
    @discord.ui.button(label='No', style=ButtonStyle.red, custom_id='yn_no')
    async def on_no(self, interaction: Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.result = False
        self.stop()

    async def on_error(self, interaction: Interaction, error: Exception, item: Item[Any], /) -> None:
        interaction.client.log.exception(error)


async def yn_question(ctx: Union[commands.Context, discord.Interaction], question: str, *,
                      message: Optional[str] = None, embed: Optional[discord.Embed] = None,
                      ephemeral: Optional[bool] = True) -> Optional[bool]:
    """
    :param ctx: The context in which the yn_question method is being called. It can be either a discord.py commands.Context object or a discord.Interaction object.
    :param question: The question to be displayed in the embedded message.
    :param message: An optional additional message to be displayed in the embedded message.
    :param embed: An optional embed to be used. If None, then a default embed will be used. Replaces question and message.
    :param ephemeral: An optional boolean value indicating whether the message should be ephemeral (only visible to the user who triggered it). Default is True.
    :return: A boolean value indicating the result of the yn_question. True if the user answered "Yes", False if the user answered "No".

    This method asks a yes/no question using an embedded message, with an optional additional message. It waits for the user to respond and returns their answer as a boolean value.
    If the ctx parameter is a discord.Interaction object, it is converted to a commands.Context object using ctx.client.get_context(ctx).
    The yn_question method uses a custom view called YNQuestionView to handle the interaction. An embedded message is sent with the specified question and optional message, along with two
    * buttons for "Yes" and "No". The view listens for the user's button clicks and returns the corresponding boolean value.
    """
    if not embed:
        embed = discord.Embed(color=discord.Color.red())
        if message is not None:
            embed.description = message
    embed.title = question
    if isinstance(ctx, discord.Interaction):
        ctx = await ctx.client.get_context(ctx)
    view = YNQuestionView()
    msg = await ctx.send(embed=embed, view=view, ephemeral=ephemeral)
    try:
        if not await view.wait():
            return view.result
    finally:
        if msg:
            with suppress(discord.NotFound):
                await msg.delete()


class PopulatedQuestionView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.result = None

    # noinspection PyTypeChecker
    @discord.ui.button(label='Yes', style=ButtonStyle.green, custom_id='pl_yes')
    async def on_yes(self, interaction: Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.result = 'yes'
        self.stop()

    # noinspection PyTypeChecker
    @discord.ui.button(label='Later', style=ButtonStyle.primary, custom_id='pl_later', emoji='⏱')
    async def on_later(self, interaction: Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.result = 'later'
        self.stop()

    # noinspection PyTypeChecker
    @discord.ui.button(label='Cancel', style=ButtonStyle.red, custom_id='pl_cancel')
    async def on_cancel(self, interaction: Interaction, _: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()


async def populated_question(ctx: Union[commands.Context, discord.Interaction], question: str, message: Optional[str] = None,
                             ephemeral: Optional[bool] = True) -> Optional[str]:
    """
    Same as yn_question, but adds an option "Later". The usual use-case of this function would be
    if people are flying atm, and you want to ask to trigger an action that would affect their experience (aka stop
    the server).

    :param ctx: The discord context or interaction object.
    :param question: The question to be displayed in the embed.
    :param message: An optional message to be displayed in the embed.
    :param ephemeral: Whether the interaction response should be ephemeral. Default is True.
    :return: The result of the user's response to the populated question. Returns None if the user does not respond.
    """
    embed = discord.Embed(title='People are flying!', description=question, color=discord.Color.red())
    if message is not None:
        embed.add_field(name=message, value='_ _')
    if isinstance(ctx, discord.Interaction):
        ctx = await ctx.client.get_context(ctx)
    view = PopulatedQuestionView()
    msg = await ctx.send(embed=embed, view=view, ephemeral=ephemeral)
    try:
        if not await view.wait():
            return view.result
    finally:
        if msg:
            with suppress(discord.NotFound):
                await msg.delete()


def check_roles(roles: Iterable[Union[str, int]], member: Optional[discord.Member] = None) -> bool:
    """
    Check if a member has any of the specified roles.

    :param roles: An iterable containing either role names (string) or role IDs (integer).
    :param member: The discord.Member object to check roles for. Defaults to None.
    :return: A boolean value indicating whether the member has any of the specified roles. Returns False if member is None.
    """
    if not member:
        return False
    for role in member.roles:
        for valid_role in roles:
            if isinstance(valid_role, int) and role.id == valid_role:
                return True
            elif isinstance(valid_role, str) and role.name == valid_role:
                return True
    return False


def has_role(role: str):
    """
    Decorator for non-application commands to check if the user has a specific role.

    Example usage:
    @utils.has_role('DCS Admin')

    :param role: The role to check.
    :return: A predicate function that checks if the user has the specified role.
    """
    def predicate(ctx: commands.Context) -> bool:
        return check_roles([role], ctx.author)

    predicate.role = role
    return commands.check(predicate)


def app_has_role(role: str):
    """
    Decorator for application commands to check if the user has a specific role.

    Example usage:
    @utils.app_has_role('DCS Admin')

    :param role: The role to check.
    :return: True if the user has the role, False otherwise.
    """
    def predicate(interaction: Interaction) -> bool:
        return check_roles(interaction.client.roles[role], interaction.user)

    predicate.role = role
    return app_commands.check(predicate)


def has_roles(roles: list[str]):
    """
    Decorator for non-application commands to check if the user has one of the provided roles.

    Example usage:
    @utils.has_roles(['DCS', 'DCS Admin'])

    :param roles: A list of roles that should be checked for membership. Each role should be a string representing the name of the role.
    :return: A decorated function that can be used as a check for membership of the specified roles.
    """
    def predicate(ctx: commands.Context) -> bool:
        return check_roles(roles, ctx.author)

    predicate.roles = roles
    return commands.check(predicate)


def cmd_has_roles(roles: list[str]):
    """
    Check if the user associated with the interaction has any of the specified roles.
    Internal replacement function for command-overwrites, not to be used by API users.

    :param roles: A list of role names to check.
    :type roles: list[str]
    :return: A decorator function that applies the role check to an interaction.
    :rtype: function
    """
    def predicate(interaction: Interaction) -> bool:
        valid_roles = []
        for role in roles:
            mappings = interaction.client.roles.get(role)
            if mappings:
                valid_roles.extend(mappings)
            else:
                valid_roles.append(role)
        return check_roles(set(valid_roles), interaction.user)

    @functools.wraps(predicate)
    async def wrapper(interaction: Interaction):
        return predicate(interaction)

    cmd_has_roles.predicate = wrapper
    wrapper.roles = roles
    return cmd_has_roles


def get_role_ids(plugin: Plugin, role_names) -> list[int]:
    role_ids = []
    if not isinstance(role_names, list):
        role_names = [role_names]

    for role in role_names:
        if isinstance(role, str) and not role.isnumeric():
            role_id = discord.utils.get(plugin.bot.guilds[0].roles, name=role)
            if role_id:
                role_ids.append(role_id.id)
            else:
                plugin.log.warning(f'Role "{role}" from {plugin.plugin_name}.yaml not found in Discord.')
        else:
            role_ids.append(role)
    return role_ids


def app_has_roles(roles: list[str]):
    """
    Decorator for application commands to check if the user has one of the provided roles.

    Example usage:
    @utils.app_has_roles(['DCS', 'DCS Admin'])

    :param roles: A list of roles that should be checked for membership. Each role should be a string representing the name of the role.
    :return: A decorated function that can be used as a check for membership of the specified roles.
    """
    def predicate(interaction: Interaction) -> bool:
        valid_roles = set()
        for role in roles:
            valid_roles |= set(interaction.client.roles[role])
        return check_roles(valid_roles, interaction.user)

    predicate.roles = roles
    return app_commands.check(predicate)


def app_has_not_role(role: str):
    """
    Decorator for application commands to check if the user does not have the provided role.

    Example usage:
    @utils.app_has_not_role('Red')

    :param role: The role to check if the interaction does not have.
    :return: True if the interaction does not have the given role, False otherwise.
    """
    def predicate(interaction: Interaction) -> bool:
        return not check_roles(interaction.client[role], interaction.user)

    predicate.role = role
    return app_commands.check(predicate)


def app_has_not_roles(roles: list[str]):
    """
    Decorator for application commands to check if the user has none one of the provided roles.

    Example usage:
    @utils.app_has_not_roles(['Red'])

    :param roles: A list of role names that the interaction should not have.
    :return: A predicate function that checks if the interaction does not have any of the specified roles.
    """
    def predicate(interaction: Interaction) -> bool:
        invalid_roles = set()
        for role in roles:
            invalid_roles |= set(interaction.client.roles[role])
        return not check_roles(invalid_roles, interaction.user)

    predicate.roles = roles
    return app_commands.check(predicate)


def app_has_dcs_version(version: str):
    def predicate(interaction: Interaction) -> bool:
        if parse(interaction.client.node.dcs_version) < Version(version):
            raise app_commands.AppCommandError(
                _("You need at least DCS version {} to use this command!").format(version))
        return True

    return app_commands.check(predicate)


def format_embed(data: dict, **kwargs) -> discord.Embed:
    """
    :param data: A dictionary containing the data for formatting the embed.
    :param kwargs: Additional keyword arguments to be passed to the format_string function.
    :return: A discord.Embed object.

    This method takes in a dictionary 'data' and optional keyword arguments 'kwargs' to format and construct a discord.Embed object. The function returns the formatted embed.

    The 'data' dictionary contains the following optional keys:
    - 'color': The color of the embed. If not provided, defaults to discord.Color.blue().
    - 'title': The title of the embed.
    - 'description': The description of the embed.
    - 'img': A string representing the URL of an image to be set as the embed's main image.
    - 'image': A dictionary containing the URL of an image to be set as the embed's main image.
    - 'footer': The footer of the embed, which can be either a string or a dictionary with 'text' and 'icon_url' keys.
    - 'fields': A dictionary or a list of dictionaries representing the fields of the embed. Each dictionary can have 'name', 'value', and 'inline' keys.
    - 'author': A dictionary representing the author of the embed. It can have 'name', 'url', and 'icon_url' keys.
    - 'timestamp': A string representing a timestamp in the format '%Y-%m-%dT%H:%M:%S.%fZ'.

    The method constructs a discord.Embed object with the given color. It then sets the title, description, image, footer, fields, author, and timestamp properties based on the provided
    * data.

    Example usage:

    data = {
        'color': 3430907, (#3498DB = blue)
        'title': 'Hello World',
        'description': 'This is an example embed',
        'footer': {
            'text': 'Example Footer',
            'icon_url': 'https://example.com/footer_icon.png',
        },
        'fields': [
            {
                'name': 'Field 1',
                'value': 'Value 1',
                'inline': True,
            },
            {
                'name': 'Field 2',
                'value': 'Value 2',
                'inline': False,
            },
        ],
    }

    embed = format_embed(data)
    """
    color = int(data.get('color', discord.Color.blue()))
    embed = discord.Embed(color=color)
    if 'title' in data:
        embed.title = format_string(data['title'], **kwargs) or '_ _'
    if 'description' in data:
        embed.description = format_string(data['description'], **kwargs) or '_ _'
    if 'img' in data and isinstance(data['img'], str):
        embed.set_image(url=format_string(data['img'], **kwargs))
    if 'image' in data and isinstance(data['image'], dict):
        if 'url' in data['image']:
            embed.set_image(url=format_string(data['image']['url'], **kwargs))
    if 'footer' in data:
        if isinstance(data['footer'], str):
            embed.set_footer(text=format_string(data['footer'], **kwargs))
        else:
            text = format_string(data['footer']['text'], **kwargs) if 'text' in data['footer'] else None
            icon_url = format_string(data['footer']['icon_url'], **kwargs) if 'icon_url' in data['footer'] else None
            embed.set_footer(text=text, icon_url=icon_url)
    if 'fields' in data:
        if isinstance(data['fields'], dict):
            for name, value in data['fields'].items():
                embed.add_field(name=format_string(name, **kwargs) or '_ _',
                                value=format_string(value, **kwargs) or '_ _')
        elif isinstance(data['fields'], list):
            for field in data['fields']:
                name = format_string(field['name'], **kwargs) if 'name' in field else None
                value = format_string(field['value'], **kwargs) if 'value' in field else None
                inline = field['inline'] if 'inline' in field else False
                embed.add_field(name=name or '_ _', value=value or '_ _', inline=inline)
    if 'author' in data:
        name = format_string(data['author']['name'], **kwargs) if 'name' in data['author'] else None
        url = format_string(data['author']['url'], **kwargs) if 'url' in data['author'] else None
        icon_url = format_string(data['author']['icon_url'], **kwargs) if 'icon_url' in data['author'] else None
        embed.set_author(name=name, url=url, icon_url=icon_url)
    if 'timestamp' in data:
        embed.timestamp = datetime.strptime(format_string(data['timestamp'], **kwargs), '%Y-%m-%dT%H:%M:%S.%fZ')
    return embed


def embed_to_text(embed: discord.Embed) -> str:
    """

    :param embed: A discord.Embed object representing the embed content.
    :return: A string containing the formatted text representation of the embed.

    This method takes a discord.Embed object and converts it into a formatted text representation. The resulting string can be used for displaying the embed content in plain text.

    """
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
    """

    :param embed: discord.Embed object containing the content to be converted to simple text format.
    :return: A string representing the content of the embed in simple text format.

    This method takes a discord.Embed object as input and converts its content into a simple text format. The resulting text will include the title (if present), description, fields (including
    * their names and values), and footer (if present) of the embed, formatted as plain text. If a field is marked as inline in the embed, its name and value will be joined by a '|', otherwise
    *, they will be separated by a new line.

    Example usage:
        embed = discord.Embed(title="Example Embed", description="This is an example embed.")
        simple_text = embed_to_simpletext(embed)
        print(simple_text)

    Output:
        EXAMPLE EMBED
        ============
        This is an example embed.

    In addition to handling regular fields, the method also supports special fields that start with '▬'. These fields are treated as separators and are included in the resulting text as
    *-is.
    """
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


def create_warning_embed(title: str, text: Optional[str] = None,
                         fields: Optional[list[tuple[str, str]]] = None) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.yellow())
    if text:
        embed.description = text
    embed.set_thumbnail(url="https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/warning.png?raw=true")
    if fields:
        for name, value in fields:
            embed.add_field(name=name, value=value)
    return embed


def escape_string(msg: str) -> str:
    """
    Escape special characters in a given string to display them in Discord.

    :param msg: The string to escape.
    :return: The escaped string.
    :rtype: str
    """
    return re.sub(r"([\\_*~`|>#+\-={}!.\[\]()])", r"\\\1", msg)


def print_ruler(*, ruler_length: Optional[int] = 34, header: Optional[str] = '') -> str:
    if header:
        header = ' ' + header + ' '
    filler = int((ruler_length - len(header) / 2.5) / 2)
    if filler <= 0:
        filler = 1
    return '▬' * filler + header + '▬' * filler


def normalize_name(name: Optional[str] = None) -> Optional[str]:
    if not name:
        return None
    # removes content surrounded by non-word characters at the beginning or end of string
    name = re.sub(r"^\s*[\W_]+[\w\s\-\.]*[\W_]+\s*|\s*[\W_]+[\w\s\-\.]*[\W_]+\s*$", "", name)
    return name.strip().lower()


def match(name: str, member_list: list[discord.Member], min_score: Optional[int] = 70) -> Optional[discord.Member]:
    """
    Match the given name with members in the member_list based on fuzzy string matching.

    :param name: The name to match.
    :param member_list: The list of discord.Member objects to match against.
    :param min_score: The minimum score required for a match. Defaults to 70.
    :return: The discord.Member object with the best match, or None if no match is found.
    """
    # we do not want to match the DCS standard names
    if name in [
        'Player',
        'Joueur',
        'Spieler',
        'Игрок',
        'Jugador',
        '玩家',
        'Hráč',
        '플레이어'
    ]:
        return None

    name = normalize_name(name)
    weights = [3, 2, 1]
    user_lists = [
        [normalize_name(getattr(member, attr)) for member in member_list]
        for attr in ['display_name', 'global_name', 'name']
    ]
    max_score = 0
    best_match_index = None

    for user_list, weight in zip(user_lists, weights):
        for idx, user in enumerate(user_list):
            score = fuzz.ratio(name, user)
            if score > max_score:
                best_match_index = idx if score >= min_score else None
                max_score = score if score >= min_score else 0

    return member_list[best_match_index] if best_match_index else None


def find_similar_names(list1: list[str], list2: list[str], threshold: int = 90) -> list[tuple[str, str, int]]:
    """
    Compare two lists of usernames and find similar matches using fuzzy string matching.

    Args:
        list1: First list of usernames
        list2: Second list of usernames
        threshold: Minimum similarity score (0-100) to consider names as similar
                  Default is 90 for high confidence matches

    Returns:
        List of tuples containing (name1, name2, similarity_score)
    """
    similar_names = []

    for name1 in list1:
        for name2 in list2:
            # Calculate similarity ratio
            similarity = fuzz.ratio(name1.lower(), name2.lower())

            # If similarity is above threshold, add to results
            if similarity >= threshold:
                similar_names.append((name1, name2, similarity))

    # Sort results by similarity score in descending order
    similar_names.sort(key=lambda x: x[2], reverse=True)
    return similar_names


def get_interaction_param(interaction: discord.Interaction, name: str) -> Optional[Any]:
    """
    Returns the value of a specific parameter in a Discord interaction.

    :param interaction: The Discord interaction object.
    :param name: The name of the parameter to retrieve.
    :return: The value of the parameter, or None if not found.
    """
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

    return inner(interaction.data.get('options', {}))


def get_all_linked_members(interaction: discord.Interaction) -> list[discord.Member]:
    """
    :param interaction: the discord Interaction
    :return: A list of discord.Member objects representing all the members linked to DCS accounts in the bot's guild.
    """
    members: list[discord.Member] = []
    with interaction.client.pool.connection() as conn:
        for row in conn.execute("SELECT DISTINCT discord_id FROM players WHERE discord_id <> -1"):
            member = interaction.guild.get_member(row[0])
            if member:
                members.append(member)
    return members


class ServerTransformer(app_commands.Transformer):
    """

    :class:`ServerTransformer` is a class that is used for transforming and autocompleting servers as a selection for application commands.

    .. attribute:: status

        An optional attribute that specifies the list of status values to filter the servers by.

        :type: list of :class:`Status`
        :default: None

    :param status: An optional parameter that specifies the list of status values to filter the servers by.
    :type status: list of :class:`Status`

    """
    def __init__(self, *, status: list[Status] = None, maintenance: Optional[bool] = None):
        super().__init__()
        self.status: list[Status] = status
        self.maintenance = maintenance

    async def transform(self, interaction: discord.Interaction, value: Optional[str]) -> Server:
        if value:
            server = interaction.client.servers.get(value)
            if not server:
                raise app_commands.TransformerError(value, self.type, self)
        else:
            server = interaction.client.get_server(interaction)
        return server

    async def autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not await interaction.command._check_can_run(interaction):
            return []
        try:
            server: Optional[Server] = interaction.client.get_server(interaction)
            if (not current and server and server.status != Status.UNREGISTERED and
                    (not self.status or server.status in self.status)):
                return [app_commands.Choice(name=server.name, value=server.name)]
            choices: list[app_commands.Choice[str]] = [
                app_commands.Choice(name=name, value=name)
                for name, value in interaction.client.servers.items()
                if (value.status != Status.UNREGISTERED and
                    (not self.status or value.status in self.status) and
                    (not self.maintenance or value.maintenance == self.maintenance) and
                    (not current or current.casefold() in name.casefold()))
            ]
            return choices[:25]
        except Exception as ex:
            interaction.client.log.exception(ex)
            return []


class NodeTransformer(app_commands.Transformer):
    """
    A class for transforming interaction values to Node objects and providing autocomplete choices for Nodes.

    """
    async def transform(self, interaction: discord.Interaction, value: Optional[str]) -> Node:
        if value:
            return interaction.client.node.all_nodes.get(value)
        else:
            return interaction.client.node

    async def autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not await interaction.command._check_can_run(interaction):
            return []
        try:
            all_nodes = [interaction.client.node.name]
            all_nodes.extend(await interaction.client.node.get_active_nodes())
            return [
                app_commands.Choice(name=x, value=x)
                for x in all_nodes
                if not current or current.casefold() in x.casefold()
            ]
        except Exception as ex:
            interaction.client.log.exception(ex)
            return []


class InstanceTransformer(app_commands.Transformer):
    """
    A class for transforming interaction values to Instance objects and providing autocomplete choices for Instances.

    """
    def __init__(self, *, unused: bool = False):
        super().__init__()
        self.unused = unused

    async def transform(self, interaction: discord.Interaction, value: Optional[str]) -> Optional[Instance]:
        if value:
            node: Node = await NodeTransformer().transform(interaction, get_interaction_param(interaction, 'node'))
            if not node:
                return None
            return next((x for x in node.instances if x.name == value), None)
        elif len(interaction.client.node.instances) == 1:
            return interaction.client.node.instances[0]
        else:
            return None

    async def autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not await interaction.command._check_can_run(interaction):
            return []
        try:
            node: Node = await NodeTransformer().transform(interaction, get_interaction_param(interaction, 'node'))
            if not node:
                return []
            if self.unused:
                instances = [
                    instance for server_name, instance in await node.find_all_instances()
                    if not any(instance == x.name for x in node.instances)
                ]
            else:
                instances = [x.name for x in node.instances]
            return [
                app_commands.Choice(name=x, value=x)
                for x in instances
                if not current or current.casefold() in x.casefold()
            ]
        except Exception as ex:
            interaction.client.log.exception(ex)
            return []


async def airbase_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """
    Autocompletion for airbases that are in your current mission.
    """
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await ServerTransformer().transform(interaction, get_interaction_param(interaction, 'server'))
        if not server or not server.current_mission:
            return []
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=x['name'], value=idx)
            for idx, x in enumerate(server.current_mission.airbases)
            if not current or current.casefold() in x['name'].casefold() or current.casefold() in x['code'].casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def mission_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """
    Autocompletion of mission names from the current mission list of a server that has to be provided as an earlier
    parameter to the application command. The mission list can only be obtained by people with the DCS Admin role.
    """
    def get_name(base_dir: str, path: str):
        try:
            name = os.path.relpath(path, base_dir).replace('.dcssb' + os.path.sep, '')[:-4]
            if len(name) > 100:
                raise ValueError("Mission name exceeds maximum length")
            return name
        except ValueError:
            return (os.path.basename(path)[:-4])[:100]

    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await ServerTransformer().transform(interaction, get_interaction_param(interaction, 'server'))
        if not server:
            return []
        base_dir = await server.get_missions_dir()
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=get_name(base_dir, x), value=idx)
            for idx, x in enumerate(await server.getMissionList())
            if not current or current.casefold() in get_name(base_dir, x).casefold()
        ]
        return sorted(choices, key=lambda choice: choice.name)[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def group_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    # is a user is not allowed to run the interaction, they are not allowed to see the autocompletions also
    if not await interaction.command._check_can_run(interaction):
        return []
    server: Server = await ServerTransformer().transform(interaction,
                                                         get_interaction_param(interaction, 'server'))
    return [
        app_commands.Choice(name=group_name, value=group_name)
        for group_name in set(player.group_name for player in server.get_active_players() if player.group_id != 0)
        if not current or current.casefold() in group_name
    ][:25]


async def date_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    def get_date_range(date: str):
        try:
            end_date = datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            end_date = datetime.now()
        for days_back in range(25):
            yield end_date - timedelta(days=days_back)

    return [
        app_commands.Choice(name=x.strftime('%Y-%m-%d'), value=x.strftime('%Y-%m-%d'))
        for x in get_date_range(current)
    ][:25]


class UserTransformer(app_commands.Transformer):
    """
    A class for transforming interaction values to either discord.Member or ucid (str) objects and providing autocomplete choices for users.

    Parameters:
    - sel_type: The type of user to select. Default is PlayerType.ALL.
    - linked: Optional boolean value to specify whether to select only linked users. Default is None.
    """
    def __init__(self, *, sel_type: PlayerType = PlayerType.ALL, linked: Optional[bool] = None,
                 watchlist: Optional[bool] = None):
        super().__init__()
        self.sel_type = sel_type
        self.linked = linked
        self.watchlist = watchlist

    async def transform(self, interaction: discord.Interaction, value: str) -> Optional[Union[discord.Member, str]]:
        if value:
            if is_ucid(value):
                return interaction.client.get_member_by_ucid(value) or value
            elif value.isnumeric():
                return interaction.guild.get_member(int(value))
            else:
                return None
        else:
            return interaction.user

    async def autocomplete(self, interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        # is a user is not allowed to run the interaction, they are not allowed to see the autocompletions also
        if not await interaction.command._check_can_run(interaction):
            return []
        # only Admin and DCS Admin should be allowed to see ucids at all
        show_ucid = utils.check_roles(interaction.client.roles['DCS Admin'], interaction.user)
        ret = []
        if self.sel_type in [PlayerType.ALL, PlayerType.PLAYER]:
            ret.extend([
                app_commands.Choice(name='✈ ' + name + (' (' + ucid + ')' if show_ucid else ''),
                                    value=ucid)
                for ucid, name in get_all_players(interaction.client, self.linked, self.watchlist)
                if not current or current.casefold() in name.casefold() or current.casefold() in ucid
            ])
        if (self.linked is None or self.linked) and self.sel_type in [PlayerType.ALL, PlayerType.MEMBER]:
            ret.extend([
                app_commands.Choice(name='@' + member.display_name, value=str(member.id))
                for member in get_all_linked_members(interaction)
                if not current or current.casefold() in member.display_name.casefold()
            ])
        return ret[:25]


class PlayerTransformer(app_commands.Transformer):
    """

    """
    def __init__(self, *, active: Optional[bool] = None, watchlist: Optional[bool] = None, vip: Optional[bool] = None):
        super().__init__()
        self.active = active
        self.watchlist = watchlist
        self.vip = vip

    async def transform(self, interaction: discord.Interaction, value: str) -> Player:
        server: Server = await ServerTransformer().transform(interaction, get_interaction_param(interaction, 'server'))
        return server.get_player(ucid=value, active=self.active)

    async def autocomplete(self, interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not await interaction.command._check_can_run(interaction):
            return []
        try:
            if self.active:
                server: Server = await ServerTransformer().transform(interaction,
                                                                     get_interaction_param(interaction, 'server'))
                if not server:
                    return []
                choices: list[app_commands.Choice[str]] = [
                    app_commands.Choice(name=x.name, value=x.ucid)
                    for x in server.get_active_players()
                    if ((not self.watchlist or x.watchlist == self.watchlist) and (not self.vip or x.vip == self.vip)
                        and (not current or current.casefold() in x.name.casefold() or current.casefold() in x.ucid))
                ]
            else:
                choices = [
                    app_commands.Choice(name=f"{ucid} ({name})", value=ucid)
                    for ucid, name in get_all_players(interaction.client, self.watchlist, self.vip)
                    if not current or current.casefold() in name.casefold() or current.casefold() in ucid
                ]
            return choices[:25]
        except Exception as ex:
            interaction.client.log.exception(ex)
            return []


def _server_filter(server: Server) -> bool:
    return True


async def server_selection(bus: ServiceBus,
                           interaction: Union[discord.Interaction, commands.Context], *, title: str,
                           multi_select: Optional[bool] = False,
                           ephemeral: Optional[bool] = True,
                           filter_func: Callable[[Server], bool] = _server_filter
                           ) -> Optional[Union[Server, list[Server]]]:
    """

    """
    all_servers = list(bus.servers.values())
    if len(all_servers) == 0:
        return []
    elif len(all_servers) == 1:
        return [all_servers[0]]
    if multi_select:
        max_values = len(all_servers)
    else:
        max_values = 1
    server: Optional[Server] = None
    if isinstance(interaction, discord.Interaction):
        server = interaction.client.get_server(interaction)
    s = await selection(interaction, title=title,
                        options=[
                            SelectOption(label=x.name, value=str(idx), default=(
                                True if server and server == x else
                                True if not server and idx == 0 else
                                False
                            )) for idx, x in enumerate(all_servers) if filter_func(x)
                        ],
                        max_values=max_values, ephemeral=ephemeral)
    if isinstance(s, list):
        return [all_servers[int(x)] for x in s]
    elif s:
        return all_servers[int(s)]
    return None


def get_ephemeral(interaction: discord.Interaction) -> bool:
    """
    Can be used to determine whether a message should be hidden in the current context or not.
    Usually, you want to hide admin messages in a public context.

    Sample:
        await interaction.response.send_message("This message should be hidden in public.", ephemeral=utils.get_ephemeral(interaction))


    :param interaction: The discord Interaction object representing the interaction event.
    :return: A boolean value indicating whether the message will be sent as ephemeral or not.

    """
    bot: DCSServerBot = interaction.client
    server: Server = bot.get_server(interaction)
    # we will be ephemeral when we are called in public
    if not server:
        return True
    admin_channel = bot.get_admin_channel(server)
    return not admin_channel == interaction.channel


async def get_command(bot: DCSServerBot, *, name: str,
                      group: Optional[str] = None) -> Union[app_commands.AppCommand, app_commands.AppCommandGroup]:
    for cmd in await bot.tree.fetch_commands(guild=bot.guilds[0]):
        if cmd.options and isinstance(cmd.options[0], app_commands.AppCommandGroup):
            if group != cmd.name:
                continue
            for inner in cmd.options:
                if inner.name == name:
                    return inner
        elif cmd.name == name:
            return cmd
    raise app_commands.CommandNotFound(name, [group] if group else [])


class ConfigModal(Modal):
    def __init__(self, title: str, config: dict, old_values: Optional[dict] = None, ephemeral: Optional[bool] = False):
        super().__init__(title=title)
        self.ephemeral = ephemeral
        self.value = None
        self.config = config
        if not old_values:
            old_values = {}
        for k, v in self.config.items():
            self.add_item(TextInput(
                custom_id=k,
                label=v.get('label'),
                style=discord.TextStyle(v.get('style', 1)),
                placeholder=v.get('placeholder'),
                default=self.parse(old_values.get(k)) if old_values.get(k) is not None else self.parse(v.get('default', '')),
                required=v.get('required', False),
                min_length=v.get('min_length'),
                max_length=v.get('max_length')))

    @staticmethod
    def parse(value: Any) -> str:
        if isinstance(value, bool):
            return 'true' if value else 'false'
        elif isinstance(value, datetime):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return str(value)

    @staticmethod
    def unparse(value: str, t: str = None) -> Any:
        if not t or t == str:
            return value
        elif not value:
            return None
        elif t == int:
            return int(value)
        elif t == float:
            return float(value)
        elif t == bool:
            if value.lower() == 'true':
                return True
            elif value.lower() == 'false':
                return False
            else:
                raise ValueError(f"{value} is not a boolean!")
        return value

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=self.ephemeral)
        # noinspection PyUnresolvedReferences
        self.value = {
            v.custom_id: self.unparse(v.value, self.config[v.custom_id].get('type'))
            for v in self.children
        }
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(f"An error occurred: {error}")
        self.stop()


class DirectoryPicker(discord.ui.View):

    def __init__(self, node: Node, base_dir: str, ignore: Optional[list[str]] = None):
        super().__init__()
        self.node = node
        self.base_dir = base_dir
        self.dir = None
        self.ignore = ignore or []

    @property
    def directory(self) -> str:
        if self.dir:
            return os.path.join(self.base_dir, self.dir)
        else:
            return self.base_dir

    @property
    def rel_path(self) -> str:
        rel_dir = os.path.basename(self.base_dir)
        if self.dir:
            rel_dir = os.path.join(rel_dir, self.dir)
        return rel_dir

    async def render(self, init=False) -> Optional[discord.Embed]:
        embed = discord.Embed(color=discord.Color.blue())
        embed.title = f"Current Directory: {self.rel_path}"
        if await self.create_select():
            options = cast(Select, self.children[0]).options
            embed.description = (
                "Your directory contains sub-directories:\n"
                "```"
                f"\n{os.path.basename(self.rel_path)}"
                "\n{}"
                "\n```"
            ).format(
                '\n'.join(
                    [
                        f"{'├' if i < len(options) - 1 else '└'} {x.label}"
                        for i, x in enumerate(options)
                    ]
                )
            )
            # noinspection PyUnresolvedReferences
            self.children[0].disabled = False
        elif not init:
            # noinspection PyUnresolvedReferences
            self.children[0].disabled = True
        else:
            # noinspection PyUnresolvedReferences
            self.children[0].disabled = True
            embed = None
        # noinspection PyUnresolvedReferences
        self.children[2].disabled = not self.dir
        return embed

    async def create_select(self) -> bool:
        _, sub_dirs = await self.node.list_directory(self.directory, is_dir=True, ignore=self.ignore,
                                                     order=SortOrder.NAME)
        select = cast(Select, self.children[0])
        if sub_dirs:
            select.options = [
                SelectOption(label=os.path.basename(x), value=x)
                for x in sub_dirs if os.path.basename(x)
            ]
            return True
        else:
            select.options = [SelectOption(label="None", value="None")]
            return False

    async def refresh(self, interaction: discord.Interaction) -> None:
        embed = await self.render()
        if interaction.message:
            await interaction.message.edit(view=self, embed=embed)
        else:
            await interaction.edit_original_response(view=self, embed=embed)

    @discord.ui.select(min_values=0, max_values=1, placeholder="Pick a directory ...")
    async def on_select(self, interaction: discord.Interaction, select: Select):
        try:
            # noinspection PyUnresolvedReferences
            await interaction.response.defer()
            self.dir = os.path.relpath(select.values[0], self.base_dir)
            await self.refresh(interaction)
        except Exception as ex:
            interaction.client.log.exception(ex)

    # noinspection PyTypeChecker
    @discord.ui.button(label="Upload", style=ButtonStyle.green, row=2)
    async def on_upload(self, interaction: discord.Interaction, button: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.stop()

    # noinspection PyTypeChecker
    @discord.ui.button(label="Up", style=ButtonStyle.secondary)
    async def on_up(self, interaction: discord.Interaction, button: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        if self.dir:
            self.dir = os.path.dirname(self.dir)
            await self.refresh(interaction)

    # noinspection PyTypeChecker
    @discord.ui.button(label="Create", style=ButtonStyle.primary)
    async def on_create(self, interaction: discord.Interaction, button: Button):
        class TextModal(Modal, title="Create Directory"):
            name = TextInput(label="Name", max_length=80, required=True)

            async def on_submit(derived, interaction: discord.Interaction) -> None:
                # noinspection PyUnresolvedReferences
                await interaction.response.defer()
                derived.stop()

        modal = TextModal()
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if not await modal.wait():
            new_name = re.sub(r'[\\/\.]', '', modal.name.value)
            await self.node.create_directory(os.path.join(self.directory, new_name))
            if self.dir:
                self.dir = os.path.join(self.dir, new_name)
            else:
                self.dir = modal.name.value
            await self.refresh(interaction)

    # noinspection PyTypeChecker
    @discord.ui.button(label="Cancel", style=ButtonStyle.red, row=2)
    async def on_cancel(self, interaction: discord.Interaction, button: Button):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.base_dir = self.dir = None
        self.stop()


class UploadView(DirectoryPicker):

    def __init__(self, node: Node, base_dir: str, ignore: Optional[list[str]] = None):
        super().__init__(node, base_dir, ignore)
        self.overwrite = False

    async def render(self, init=False) -> Optional[discord.Embed]:
        embed = await super().render(init)
        if 'Overwrite' not in cast(Button, self.children[-1]).label:
            button = Button(label="❌ Overwrite")
            button.callback = self.on_overwrite
            self.add_item(button)
        return embed

    async def on_overwrite(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        self.overwrite = not self.overwrite
        cast(Button, self.children[-1]).label = '✔️ Overwrite' if self.overwrite else '❌ Overwrite'
        await interaction.edit_original_response(view=self)


class NodeUploadHandler:

    def __init__(self, node: Node, message: discord.Message, pattern: list[str]):
        from services.bot import BotService

        self.node = node
        self.message = message
        self.channel = message.channel
        self.log = node.log
        self.bot = ServiceRegistry.get(BotService).bot
        self.pattern = pattern
        self.overwrite = False

    @staticmethod
    def is_valid(message: discord.Message, pattern: list[str], roles: list[Union[int, str]]) -> bool:
        # ignore bot messages or messages that do not contain miz attachments
        if (message.author.bot or not message.attachments or
                not any(att.filename.lower().endswith(ext) for ext in pattern for att in message.attachments)):
            return False
        # check if the user has the correct role to upload, defaults to DCS Admin
        if not utils.check_roles(roles, message.author):
            return False
        return True

    async def render(self, directory: str, ignore_list: Optional[list[str]] = None) -> Optional[str]:
        # do we have multiple subdirectories to upload to?
        view = UploadView(self.node, directory, ignore=ignore_list)
        embed = await view.render(init=True) or discord.utils.MISSING
        try:
            msg = await self.channel.send(embed=embed, view=view)
        except Exception:
            return None
        try:
            if await view.wait():
                await self.channel.send(_('Upload aborted.'))
                return None
            directory = view.directory
            self.overwrite = view.overwrite
            if not directory:
                await self.channel.send(_('Upload aborted.'))
                return None

            return directory

        finally:
            await msg.delete()

    async def upload_file(self, directory: str, att: discord.Attachment) -> UploadStatus:
        self.log.debug(f"Uploading {att.filename} to {self.node.name}:{directory} ...")
        filename = os.path.join(directory, att.filename)
        if not self.overwrite:
            rc = await self.node.write_file(filename, att.url, overwrite=False)
            if rc == UploadStatus.FILE_EXISTS:
                self.log.debug("File exists, asking for overwrite.")
                ctx = await self.bot.get_context(self.message)
                if not await utils.yn_question(ctx, _('File exists. Do you want to overwrite it?')):
                    await self.channel.send(_('Upload aborted.'))
                    return rc
                rc = await self.node.write_file(filename, att.url, overwrite=True)
        else:
            rc = await self.node.write_file(filename, att.url, overwrite=True)
        if rc != UploadStatus.OK:
            self.log.debug(f"Error while uploading: {rc}")
            await self.channel.send(_('Error while uploading: {}').format(rc.name))
        return rc

    async def handle_attachment(self, directory: str, att: discord.Attachment) -> UploadStatus:
        rc = await self.upload_file(directory, att)
        if rc == UploadStatus.OK:
            await self.channel.send(_("File {} uploaded.").format(utils.escape_string(att.filename)))
            await self.bot.audit(f'uploaded file "{utils.escape_string(att.filename)}"',
                                 node=self.node, user=self.message.author)
        return rc

    async def post_upload(self, uploaded: list[discord.Attachment]):
        ...

    async def upload(self, base_dir: str, ignore_list: Optional[list[str]] = None):
        directory = await self.render(base_dir, ignore_list)
        if not directory:
            return

        attachments = [
            att for att in self.message.attachments
            if any(att.filename.lower().endswith(ext) for ext in self.pattern)
        ]
        # run all uploads in parallel
        tasks = [self.handle_attachment(directory, att) for att in attachments]
        ret_vals = await asyncio.gather(*tasks, return_exceptions=True)

        uploaded = []
        for idx, ret in enumerate(ret_vals):
            if isinstance(ret, Exception):
                self.log.error(f"Error during upload of {attachments[idx].filename}: {ret}")
            elif ret == UploadStatus.OK:
                uploaded.append(attachments[idx])

        # handle aftermath
        await self.post_upload(uploaded)


class ServerUploadHandler(NodeUploadHandler):

    def __init__(self, server: Server, message: discord.Message, pattern: list[str]):
        super().__init__(server.node, message, pattern)
        self.server = server

    @staticmethod
    async def get_server(message: discord.Message, channel_id: Optional[int] = None) -> Optional[Server]:
        from services.bot import BotService
        from services.servicebus import ServiceBus

        bot = ServiceRegistry.get(BotService).bot
        server = bot.get_server(message, admin_only=True)
        if not channel_id:
            channel_id = bot.locals.get('channels', {}).get('admin')

        if not server and message.channel.id == channel_id:
            bus = ServiceRegistry.get(ServiceBus)
            ctx = await bot.get_context(message)
            server = await utils.server_selection(bus, ctx, title=_("To which server do you want to upload?"))
            if not server:
                await ctx.send(_('Upload aborted.'))
                return None
        return server


class DatabaseModal(Modal):
    def __init__(
            self,
            node: Node,
            table_name: str,
            columns: list[str],
            title: str = "Data Entry Form"
    ):
        super().__init__(title=title)
        self.node = node
        self.table_name = table_name
        self.columns = columns
        self.column_types = {}
        self.response = {}

    async def get_column_info(self) -> dict[str, dict[str, Any]]:
        """
        Fetch column information from the database schema using async psycopg3.
        Returns a dictionary of column information including type, constraints, etc.
        """
        query = """
                SELECT column_name,
                       data_type,
                       is_nullable,
                       column_default,
                       character_maximum_length,
                       numeric_precision,
                       numeric_scale
                FROM information_schema.columns
                WHERE table_name = %s
                  AND column_name = ANY (%s::text[])
                """

        columns_info = {}
        async with self.node.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                async for row in await cur.execute(query, (self.table_name, self.columns)):
                    columns_info[row['column_name']] = {
                        'data_type': row['data_type'],
                        'is_nullable': row['is_nullable'] == 'YES',
                        'default': row['column_default'],
                        'max_length': row['character_maximum_length'],
                        'numeric_precision': row['numeric_precision'],
                        'numeric_scale': row['numeric_scale']
                    }

        return columns_info

    async def setup_fields(self):
        """
        Set up TextInput fields based on column information
        """
        columns_info = await self.get_column_info()

        for column_name in self.columns:
            info = columns_info[column_name]

            # Store column type for validation
            self.column_types[column_name] = info['data_type']

            # Configure TextInput based on data type
            field_params: dict[str, Any] = {
                'label': column_name.replace('_', ' ').title(),
                'required': not info['is_nullable'],
                'placeholder': f"Enter {column_name.replace('_', ' ')}..."
            }

            # Add max_length for text fields
            if info['max_length']:
                field_params['max_length'] = min(info['max_length'], 4000)  # Discord's limit

            # Configure field based on data type
            if info['data_type'] in ('integer', 'bigint', 'smallint'):
                field_params['placeholder'] = 'Enter a number...'
                field_params['max_length'] = 20
            elif info['data_type'] in ('timestamp', 'timestamp without time zone'):
                field_params['placeholder'] = 'YYYY-MM-DD HH:MM'
            elif info['data_type'] in ('numeric', 'decimal'):
                field_params['placeholder'] = f'Enter a decimal number...'
                if info['numeric_precision']:
                    max_digits = info['numeric_precision']
                    if info['numeric_scale']:
                        field_params['placeholder'] += f' (max {info["numeric_scale"]} decimal places)'
                    field_params['max_length'] = max_digits + 1  # +1 for decimal point

            # Create and add the TextInput field
            text_input = TextInput(**field_params)
            self.add_item(text_input)
            setattr(self, column_name, text_input)

    @staticmethod
    def validate_integer(value: str) -> int:
        """Validate and convert integer input"""
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"'{value}' is not a valid integer")

    @staticmethod
    def validate_numeric(value: str, scale: int = None) -> float:
        """Validate and convert numeric input"""
        try:
            num = float(value)
            if scale is not None:
                decimal_str = str(num).split('.')
                if len(decimal_str) > 1 and len(decimal_str[1]) > scale:
                    raise ValueError(f"Number can't have more than {scale} decimal places")
            return num
        except ValueError as e:
            raise ValueError(f"'{value}' is not a valid number: {str(e)}")

    @staticmethod
    def validate_timestamp(value: str) -> datetime:
        """Validate and convert timestamp input"""
        try:
            return datetime.strptime(value, '%Y-%m-%d %H:%M')
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD HH:MM")

    async def on_submit(self, interaction: discord.Interaction):
        # Validate and convert all inputs
        validated_data = {}
        columns_info = await self.get_column_info()

        for column_name, column_info in columns_info.items():
            value = getattr(self, column_name).value

            # Skip empty optional fields
            if not value and not getattr(self, column_name).required:
                continue

            # Validate based on column type
            if column_info['data_type'] in ('integer', 'bigint', 'smallint'):
                validated_data[column_name] = self.validate_integer(value)
            elif column_info['data_type'] in ('numeric', 'decimal'):
                validated_data[column_name] = self.validate_numeric(
                    value,
                    scale=column_info['numeric_scale']
                )
            elif column_info['data_type'] in ('timestamp', 'timestamp without time zone'):
                validated_data[column_name] = self.validate_timestamp(value)
            else:  # text, varchar, etc.
                validated_data[column_name] = value

        # Generate INSERT query
        columns = ', '.join(validated_data.keys())
        placeholders = ', '.join(['%s'] * len(validated_data))
        query = f"""
            INSERT INTO {self.table_name} ({columns})
            VALUES ({placeholders})
            RETURNING *
        """

        # Execute query
        async with self.node.apool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(query, list(validated_data.values()))
                    self.response = await cur.fetchone()

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(
            f"Data successfully inserted into {self.table_name}!",
            ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Handle any errors that occur during modal submission"""
        if isinstance(error, ValueError):
            # Handle validation errors
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f"Validation error: {str(error)}",
                ephemeral=True
            )
        else:
            # Handle other errors
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f"An error occurred: {str(error)}",
                ephemeral=True
            )
            logger.error(f"Error while inserting a new row in {self.table_name}: {error}")
