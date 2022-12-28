from __future__ import annotations
import asyncio
import discord
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, cast, Sequence, Union, TYPE_CHECKING
from discord import Interaction, app_commands, SelectOption
from discord.ext import commands
from discord.ui import Button, View, Select

from . import config

if TYPE_CHECKING:
    from .. import Server, DCSServerBot


async def wait_for_single_reaction(bot: DCSServerBot, ctx: Union[commands.Context, discord.DMChannel],
                                   message: discord.Message) -> discord.Reaction:
    def check_press(react: discord.Reaction, user: discord.Member):
        return (react.message.channel == message.channel) & (user == member) & (react.message.id == message.id)

    tasks = [bot.wait_for('reaction_add', check=check_press),
             bot.wait_for('reaction_remove', check=check_press)]
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


async def input_value(bot: DCSServerBot, ctx: commands.Context, message: Optional[str] = None,
                      delete: Optional[bool] = False, timeout: Optional[float] = 300.0):
    def check(m):
        return (m.channel == ctx.message.channel) & (m.author == ctx.message.author)

    msg = response = None
    try:
        if message:
            msg = await ctx.send(message)
        response = await bot.wait_for('message', check=check, timeout=timeout)
        return response.content if response.content != '.' else None
    finally:
        if delete:
            if msg:
                await msg.delete()
            if response:
                await response.delete()


async def pagination(bot: DCSServerBot, ctx: commands.Context, data: list, embed_formatter, num: int = 10):
    message = None
    try:
        j = 0
        while len(data) > 0:
            max_i = (len(data) % num) if (len(data) - j * num) < num else num
            embed = embed_formatter(data[j * num:j * num + max_i])
            message = await ctx.send(embed=embed)
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
    def __init__(self, ctx: commands.Context, *, placeholder: str, options: list[SelectOption], min_values: int,
                 max_values: int):
        super().__init__()
        self.ctx = ctx
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

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary, custom_id='sl_cancel', emoji='❌')
    async def on_cancel(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        self.stop()

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        if interaction.user != self.ctx.author:
            await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
            return False
        else:
            return True


async def selection(ctx, *, title: Optional[str] = None, placeholder: Optional[str] = None, embed: discord.Embed = None,
                    options: list[SelectOption], min_values: Optional[int] = 1, max_values: Optional[int] = 1,
                    ephemeral: bool = False) -> Optional[str]:
    if len(options) == 1:
        return options[0].value
    if not embed and title:
        embed = discord.Embed(description=title, color=discord.Color.blue())
    view = SelectView(ctx, placeholder=placeholder, options=options, min_values=min_values, max_values=max_values)
    msg = None
    try:
        msg = await ctx.send(embed=embed, view=view, ephemeral=ephemeral)
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
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.result = False

    @discord.ui.button(label='Yes', style=discord.ButtonStyle.green, custom_id='yn_yes', emoji='✅')
    async def on_yes(self, interaction: Interaction, button: Button):
        self.result = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label='No', style=discord.ButtonStyle.secondary, custom_id='yn_no', emoji='❌')
    async def on_no(self, interaction: Interaction, button: Button):
        self.result = False
        await interaction.response.defer()
        self.stop()

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        if interaction.user != self.ctx.author:
            await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
            return False
        else:
            return True


async def yn_question(ctx: commands.Context, question: str, message: Optional[str] = None) -> bool:
    embed = discord.Embed(description=question, color=discord.Color.red())
    if message is not None:
        embed.add_field(name=message, value='_ _')
    view = YNQuestionView(ctx)
    msg = None
    try:
        msg = await ctx.send(embed=embed, view=view)
        if await view.wait():
            return False
        return view.result
    finally:
        await msg.delete()


class PopulatedQuestionView(View):
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.result = None

    @discord.ui.button(label='Yes', style=discord.ButtonStyle.red, custom_id='pl_yes', emoji='⚠')
    async def on_yes(self, interaction: Interaction, button: Button):
        self.result = 'yes'
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label='Later', style=discord.ButtonStyle.primary, custom_id='pl_later', emoji='⏱')
    async def on_later(self, interaction: Interaction, button: Button):
        self.result = 'later'
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary, custom_id='pl_cancel', emoji='❌')
    async def on_cancel(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        self.stop()

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        if interaction.user != self.ctx.author:
            await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
            return False
        else:
            return True


async def populated_question(ctx: commands.Context, question: str, message: Optional[str] = None) -> Optional[str]:
    embed = discord.Embed(title='People are flying!', description=question, color=discord.Color.red())
    if message is not None:
        embed.add_field(name=message, value='_ _')
    view = PopulatedQuestionView(ctx)
    msg = None
    try:
        msg = await ctx.send(embed=embed, view=view)
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


async def servers_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices: list[app_commands.Choice[str]] = [
        app_commands.Choice(name=x, value=x) for x in interaction.client.servers.keys() if current.casefold() in x.casefold()
    ]
    return choices[:25]


async def players_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    server: Server = interaction.client.get_server(interaction)
    choices: list[app_commands.Choice[str]] = [
        app_commands.Choice(name=x.name, value=x.ucid) for x in server.get_active_players() if current.casefold() in x.name.casefold()
    ]
    return choices[:25]


@dataclass
class ContextWrapper(commands.Context):
    message: discord.Message

    async def send(
        self,
        content: Optional[str] = None,
        *,
        tts: bool = False,
        embed: Optional[discord.Embed] = None,
        embeds: Optional[Sequence[discord.Embed]] = None,
        file: Optional[discord.File] = None,
        files: Optional[Sequence[discord.File]] = None,
        stickers: Optional[Sequence[Union[discord.GuildSticker, discord.StickerItem]]] = None,
        delete_after: Optional[float] = None,
        nonce: Optional[Union[str, int]] = None,
        allowed_mentions: Optional[discord.AllowedMentions] = None,
        reference: Optional[Union[discord.Message, discord.MessageReference, discord.PartialMessage]] = None,
        mention_author: Optional[bool] = None,
        view: Optional[View] = None,
        suppress_embeds: bool = False,
        ephemeral: bool = False
    ) -> discord.Message:
        return await self.message.channel.send(content, tts=tts, embed=embed, embeds=embeds, file=file, files=files,
                                               stickers=stickers, delete_after=delete_after, nonce=nonce,
                                               allowed_mentions=allowed_mentions, reference=reference,
                                               mention_author=mention_author, view=view,
                                               suppress_embeds=suppress_embeds)
