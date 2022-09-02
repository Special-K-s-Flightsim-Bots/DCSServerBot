import asyncio
import discord
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from discord.ext import commands
from . import config


async def wait_for_single_reaction(self, ctx, message: discord.Message) -> discord.Reaction:
    def check_press(react: discord.Reaction, user: discord.Member):
        return (react.message.channel == ctx.message.channel) & (user == ctx.message.author) & (react.message.id == message.id)

    tasks = [self.bot.wait_for('reaction_add', check=check_press),
             self.bot.wait_for('reaction_remove', check=check_press)]
    try:
        done, tasks = await asyncio.wait(tasks, timeout=120, return_when=asyncio.FIRST_COMPLETED)
        if len(done) > 0:
            react, _ = done.pop().result()
            return react
        else:
            raise asyncio.TimeoutError
    finally:
        for task in tasks:
            task.cancel()


async def input_value(self, ctx, message: str, delete: Optional[bool] = False, timeout: Optional[float] = 300.0):
    def check(m):
        return (m.channel == ctx.message.channel) & (m.author == ctx.message.author)

    msg = response = None
    try:
        msg = await ctx.send(message)
        response = await self.bot.wait_for('message', check=check, timeout=timeout)
        return response.content if response.content != '.' else None
    finally:
        if delete:
            if msg:
                await msg.delete()
            if response:
                await response.delete()


async def pagination(self, ctx, data, embed_formatter, num=10):
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
                react = await wait_for_single_reaction(self, ctx, message)
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


async def selection_list(self, ctx, data, embed_formatter, num=5, marker=-1, marker_emoji='🔄'):
    message = None
    try:
        j = 0
        while len(data) > 0:
            max_i = (len(data) % num) if (len(data) - j * num) < num else num
            embed = embed_formatter(data[j * num:j * num + max_i], (marker - j * num) if marker in range(j * num, j * num + max_i + 1) else 0, marker_emoji)
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
            react = await wait_for_single_reaction(self, ctx, message)
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


async def multi_selection_list(self, ctx, data, embed_formatter) -> list[int]:
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
        await self.bot.wait_for('reaction_add', check=check_ok, timeout=120.0)
        cache_msg = await ctx.fetch_message(message.id)
        for react in cache_msg.reactions:
            if (react.emoji != '🆗') and (react.count > 1):
                if (len(react.emoji) > 1) and ord(react.emoji[0]) in range(0x30, 0x40):
                    retval.append(ord(react.emoji[0]) - 0x31)
    finally:
        if message:
            await message.delete()
    return retval


async def yn_question(self, ctx, question: str, msg: Optional[str] = None) -> bool:
    yn_embed = discord.Embed(title=question, color=discord.Color.red())
    if msg is not None:
        yn_embed.add_field(name=msg, value='_ _')
    yn_msg = await ctx.send(embed=yn_embed)
    await yn_msg.add_reaction('🇾')
    await yn_msg.add_reaction('🇳')
    try:
        react = await wait_for_single_reaction(self, ctx, yn_msg)
    except asyncio.TimeoutError:
        return False
    finally:
        await yn_msg.delete()
    return react.emoji == '🇾'


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
    def predicate(ctx):
        return check_roles([role], ctx.author)

    return commands.check(predicate)


def has_roles(roles: list[str]):
    def predicate(ctx):
        return check_roles(roles, ctx.author)

    return commands.check(predicate)


def has_not_role(role: str):
    def predicate(ctx):
        return not check_roles([role], ctx.author)

    return commands.check(predicate)


def has_not_roles(roles: list[str]):
    def predicate(ctx):
        return not check_roles(roles, ctx.author)

    return commands.check(predicate)


def coalition_only():
    def predicate(ctx):
        for role in ctx.message.author.roles:
            if role.name in [config['ROLES']['Coalition Blue'], config['ROLES']['Coalition Red']]:
                if ctx.message.channel.overwrites_for(role).send_messages:
                    return True
        return False

    return commands.check(predicate)


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
    if len(embed.title):
        message.append(embed.title.upper())
    if len(embed.description):
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
    if len(embed.title):
        message += embed.title.upper() + '\n' + '=' * len(embed.title) + '\n'
    if len(embed.description):
        message += embed.description + '\n'
    message += '\n'
    for field in embed.fields:
        name = field.name if field.name != '_ _' else ''
        value = field.value if field.value != '_ _' else ''
        if len(name) and len(value):
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
    if len(embed.footer.text):
        message += '\n' + embed.footer.text
    return message


@dataclass
class ContextWrapper:
    message: discord.Message

    async def send(self, content=None, *, tts=False, embed=None, file=None, files=None, delete_after=None, nonce=None,
                   allowed_mentions=None, reference=None, mention_author=None):
        return await self.message.channel.send(content, tts=tts, embed=embed, file=file, files=files,
                                               delete_after=delete_after, nonce=nonce,
                                               allowed_mentions=allowed_mentions, reference=reference,
                                               mention_author=mention_author)
