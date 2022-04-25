import aiohttp
import asyncio
import discord
import importlib
import math
import os
import psutil
import re
import shutil
import socket
import string
import subprocess
import psycopg2
import xml
import xmltodict
from core.const import Status
from configparser import ConfigParser
from contextlib import closing, suppress
from datetime import datetime, timedelta
from discord.ext import commands
from typing import Union, Optional, Tuple

SAVED_GAMES = os.path.expandvars('%USERPROFILE%\\Saved Games')
REGEXP = {
    'branch': re.compile(r'"branch": "(?P<branch>.*)"'),
    'version': re.compile(r'"version": "(?P<version>.*)"'),
    'server_name': re.compile(r'\["name"\] = "(?P<server_name>.*)"')
}
PATCHNOTES_URL = 'https://www.digitalcombatsimulator.com/en/news/changelog/rss/'

config = ConfigParser()
config.read('config/default.ini')
config.read('config/dcsserverbot.ini')


def findDCSInstallations(server_name=None):
    installations = []
    for dirname in os.listdir(SAVED_GAMES):
        if os.path.isdir(os.path.join(SAVED_GAMES, dirname)):
            settings = os.path.join(SAVED_GAMES, dirname, 'Config\\serverSettings.lua')
            if os.path.exists(settings):
                if server_name:
                    with open(settings, encoding='utf8') as f:
                        if '["name"] = "{}"'.format(server_name) in f.read():
                            installations.append((server_name, dirname))
                else:
                    with open(settings, encoding='utf8') as f:
                        match = REGEXP['server_name'].search(f.read())
                        if match:
                            installations.append((match.group('server_name'), dirname))
    return installations


def changeServerSettings(server_name, name: str, value: Union[str, int, bool]):
    assert name in ['listStartIndex', 'password', 'name', 'maxPlayers', 'listLoop'], "Value can't be changed."
    if isinstance(value, str):
        value = '"' + value + '"'
    elif isinstance(value, bool):
        value = value.__repr__().lower()
    _, installation = findDCSInstallations(server_name)[0]
    server_settings = os.path.join(SAVED_GAMES, installation, 'Config\\serverSettings.lua')
    tmp_settings = os.path.join(SAVED_GAMES, installation, 'Config\\serverSettings.tmp')
    with open(server_settings, encoding='utf8') as infile:
        inlines = infile.readlines()
    outlines = []
    for line in inlines:
        if '["{}"]'.format(name) in line:
            outlines.append(re.sub(' = ([^,]*)', ' = {}'.format(value), line))
            if line.startswith('cfg'):
                outlines.append('\n')
        else:
            outlines.append(line)
    with open(tmp_settings, 'w', encoding='utf8') as outfile:
        outfile.writelines(outlines)
    os.remove(server_settings)
    os.rename(tmp_settings, server_settings)


def getInstalledVersion(path: str) -> Tuple[Optional[str], Optional[str]]:
    branch = version = None
    with open(os.path.join(os.path.expandvars(path), 'autoupdate.cfg'), encoding='utf8') as cfg:
        lines = cfg.readlines()
    for line in lines:
        if '"branch"' in line:
            match = REGEXP['branch'].search(line)
            if match:
                branch = match.group('branch')
        elif '"version"' in line:
            match = REGEXP['version'].search(line)
            if match:
                version = match.group('version')
    return branch, version


async def getLatestVersion(branch: str) -> Optional[str]:
    async with aiohttp.ClientSession() as session:
        async with session.get(PATCHNOTES_URL) as response:
            try:
                xpars = xmltodict.parse(await response.text())
                for item in xpars['rss']['channel']['item']:
                    if branch in item['link']:
                        return item['link'].split('/')[-2]
            except xml.parsers.expat.ExpatError:
                pass
    return None


def match(name1: str, name2: str) -> int:
    def compare_words(n1: str, n2: str) -> int:
        n1 = re.sub('|', '', n1)
        n1 = re.sub('[._-]', ' ', n1)
        n2 = re.sub('|', '', n2)
        n2 = re.sub('[._-]', ' ', n2)
        n1_words = n1.split()
        n2_words = n2.split()
        length = 0
        for w in n1_words:
            if w in n2_words:
                if len(w) > 3 or length > 0:
                    length += len(w)
        return length

    if name1 == name2:
        return len(name1)
    # remove any tags
    n1 = re.sub('^[\[\<\(=-].*[-=\)\>\]]', '', name1).strip().casefold()
    if len(n1) == 0:
        n1 = name1.casefold()
    n2 = re.sub('^[\[\<\(=-].*[-=\)\>\]]', '', name2).strip().casefold()
    if len(n2) == 0:
        n2 = name2.casefold()
    # if the names are too short, return
    if (len(n1) <= 3 or len(n2) <= 3) and (n1 != n2):
        return 0
    length = max(compare_words(n1, n2), compare_words(n2, n1))
    if length > 0:
        return length
    # remove any special characters
    n1 = re.sub('[^a-zA-Z0-9 ]', '', n1).strip()
    n2 = re.sub('[^a-zA-Z0-9 ]', '', n2).strip()
    if (len(n1) == 0) or (len(n2) == 0):
        return 0
    # if the names are too short, return
    if len(n1) <= 3 or len(n2) <= 3:
        return 0
    length = max(compare_words(n1, n2), compare_words(n2, n1))
    if length > 0:
        return length
    # remove any numbers
    n1 = re.sub('[0-9 ]', '', n1).strip()
    n2 = re.sub('[0-9 ]', '', n2).strip()
    if (len(n1) == 0) or (len(n2) == 0):
        return 0
    # if the names are too short, return
    if (len(n1) <= 3 or len(n2) <= 3) and (n1 != n2):
        return 0
    return max(compare_words(n1, n2), compare_words(n2, n1))


def match_user(self, data: Union[dict, discord.Member], rematch=False) -> Optional[discord.Member]:
    # try to match a DCS user with a Discord member
    tag_filter = self.config['FILTER']['TAG_FILTER'] if 'TAG_FILTER' in self.config['FILTER'] else None
    if isinstance(data, dict):
        if not rematch:
            member = get_member_by_ucid(self, data['ucid'])
            if member:
                return member
        # we could not find the user, so try to match them
        dcs_name = re.sub(tag_filter, '', data['name']).strip() if tag_filter else data['name']
        # we do not match the default names
        if dcs_name in ['Player', 'Spieler', 'Jugador', 'Joueur']:
            return None
        # a minimum of 3 characters have to match
        max_weight = 3
        best_fit = []
        for member in self.bot.get_all_members():
            name = re.sub(tag_filter, '', member.name).strip() if tag_filter else member.name
            if member.nick:
                nickname = re.sub(tag_filter, '', member.nick).strip() if tag_filter else member.nick
                weight = max(match(dcs_name, nickname), match(dcs_name, name))
            else:
                weight = match(dcs_name, name)
            if weight > max_weight:
                max_weight = weight
                best_fit = [member]
            elif weight == max_weight:
                best_fit.append(member)
        if len(best_fit) == 1:
            return best_fit[0]
        # ambiguous matches
        elif len(best_fit) > 1 and not rematch:
            online_match = []
            gaming_match = []
            # check for online users
            for m in best_fit:
                if m.status != discord.Status.offline:
                    online_match.append(m)
                    if isinstance(m.activiy, discord.Game) and 'DCS' in m.activity.name:
                        gaming_match.append(m)
            if len(gaming_match) == 1:
                return gaming_match[0]
            elif len(online_match) == 1:
                return online_match[0]
        return None
    # try to match a Discord member with a DCS user that played on the servers
    else:
        max_weight = 0
        best_fit = None
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                sql = 'SELECT ucid, name from players'
                if rematch is False:
                    sql += ' WHERE discord_id = -1 AND name IS NOT NULL'
                cursor.execute(sql)
                for row in cursor.fetchall():
                    name = re.sub(tag_filter, '', data.name).strip() if tag_filter else data.name
                    if data.nick:
                        nickname = re.sub(tag_filter, '', data.nick).strip() if tag_filter else data.nick
                        weight = max(match(nickname, row['name']), match(name, row['name']))
                    else:
                        weight = match(name, row[1])
                    if weight > max_weight:
                        max_weight = weight
                        best_fit = row[0]
                return best_fit
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


def get_ucid_by_name(self, name: str) -> Optional[str]:
    conn = self.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            search = f'%{name}%'
            cursor.execute('SELECT ucid FROM players WHERE LOWER(name) like LOWER(%s) ORDER BY last_seen DESC LIMIT 1',
                           (search, ))
            if cursor.rowcount == 1:
                return cursor.fetchone()[0]
            else:
                return None
    except (Exception, psycopg2.DatabaseError) as error:
        self.log.exception(error)
    finally:
        self.pool.putconn(conn)


def get_member_by_ucid(self, ucid: str) -> Optional[discord.Member]:
    conn = self.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            cursor.execute('SELECT discord_id FROM players WHERE ucid = %s AND discord_id <> -1', (ucid, ))
            if cursor.rowcount == 1:
                return self.bot.guilds[0].get_member(cursor.fetchone()[0])
            else:
                return None
    except (Exception, psycopg2.DatabaseError) as error:
        self.log.exception(error)
    finally:
        self.pool.putconn(conn)


async def wait_for_single_reaction(self, ctx, message):
    def check_press(react, user):
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
            if j > 0:
                await message.add_reaction('◀️')
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
                return marker - j * num - 1
            elif (len(react.emoji) > 1) and ord(react.emoji[0]) in range(0x31, 0x39):
                return (ord(react.emoji[0]) - 0x31) + j * num
        return -1
    except asyncio.TimeoutError:
        if message:
            await message.delete()
        return -1


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


async def get_server(self, ctx: Union[discord.ext.commands.context.Context, str]):
    for server_name, server in self.globals.items():
        if isinstance(ctx, discord.ext.commands.context.Context):
            if server['status'] == Status.UNREGISTERED:
                continue
            channels = ['status_channel', 'chat_channel', 'admin_channel']
            if 'COALITION_BLUE_CHANNEL' in config[server['installation']]:
                channels.append('coalition_blue_channel')
            if 'COALITION_RED_CHANNEL' in config[server['installation']]:
                channels.append('coalition_red_channel')
            for channel in channels:
                if int(server[channel]) == ctx.channel.id:
                    return server
        else:
            if server_name == ctx:
                return server
    return None


def has_roles(roles: list[str]):
    def predicate(ctx):
        valid_roles = []
        for role in roles:
            if 'ROLES' not in config or role not in config['ROLES']:
                valid_roles.append(role)
            else:
                valid_roles.extend([x.strip() for x in config['ROLES'][role].split(',')])
        for role in ctx.author.roles:
            if role.name in valid_roles:
                return True
        return False

    return commands.check(predicate)


def has_not_roles(roles: list[str]):
    def predicate(ctx):
        valid_roles = []
        for role in roles:
            if 'ROLES' not in config or role not in config['ROLES']:
                valid_roles.append(role)
            else:
                valid_roles.extend([x.strip() for x in config['ROLES'][role].split(',')])
        for role in ctx.author.roles:
            if role.name in valid_roles:
                return False
        return True

    return commands.check(predicate)


def has_role(item: str):
    def predicate(ctx):
        if 'ROLES' not in config or item not in config['ROLES']:
            valid_roles = [item]
        else:
            valid_roles = [x.strip() for x in config['ROLES'][item].split(',')]
        for role in ctx.author.roles:
            if role.name in valid_roles:
                return True
        return False

    return commands.check(predicate)


def has_not_role(item: str):
    def predicate(ctx):
        if 'ROLES' not in config or item not in config['ROLES']:
            valid_roles = [item]
        else:
            valid_roles = [x.strip() for x in config['ROLES'][item].split(',')]
        for role in ctx.author.roles:
            if role.name in valid_roles:
                return False
        return True

    return commands.check(predicate)


def coalition_only():
    def predicate(ctx):
        for role in ctx.message.author.roles:
            if role.name in [config['ROLES']['Coalition Blue'], config['ROLES']['Coalition Red']]:
                if ctx.message.channel.overwrites_for(role).send_messages:
                    return True
        return False
    return commands.check(predicate)


def is_open(ip, port):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(3)
        return s.connect_ex((ip, int(port))) == 0


async def get_external_ip():
    async with aiohttp.ClientSession() as session:
        async with session.get('https://api4.ipify.org/') as resp:
            return await resp.text()


def find_process(proc, installation):
    for p in psutil.process_iter(['name', 'cmdline']):
        if p.info['name'] == proc:
            with suppress(Exception):
                for c in p.info['cmdline']:
                    if installation in c:
                        return p
    return None


def dd_to_dms(dd):
    frac, degrees = math.modf(dd)
    frac, minutes = math.modf(frac * 60)
    frac, seconds = math.modf(frac * 60)
    return degrees, minutes, seconds, frac


def get_active_runways(runways, wind):
    retval = []
    for runway in runways:
        heading = int(runway[:2]) * 10
        winddir = (wind['dir'] + 180) % 360
        diff = abs((winddir - heading + 180 + 360) % 360 - 180)
        if diff <= 90:
            retval.append(runway)
    if len(retval) == 0:
        retval = ['n/a']
    return retval


def is_in_timeframe(time: datetime, timeframe: str) -> bool:
    def parse_time(timestr: str) -> datetime:
        format, timestr = ('%H:%M', timestr.replace('24:', '00:')) \
            if timestr.find(':') > -1 else ('%H', timestr.replace('24', '00'))
        return datetime.strptime(timestr, format)

    pos = timeframe.find('-')
    if pos != -1:
        starttime = parse_time(timeframe[:pos])
        endtime = parse_time(timeframe[pos+1:])
        if endtime <= starttime:
            endtime += timedelta(days=1)
    else:
        starttime = endtime = parse_time(timeframe)
    checktime = time.replace(year=starttime.year, month=starttime.month, day=starttime.day, second=0, microsecond=0)
    return starttime <= checktime <= endtime


def start_dcs(self, server: dict):
    self.log.debug(r'Launching DCS server with: "{}\bin\dcs.exe" --server --norender -w {}'.format(
        os.path.expandvars(self.config['DCS']['DCS_INSTALLATION']), server['installation']))
    p = subprocess.Popen(['dcs.exe', '--server', '--norender', '-w', server['installation']],
                         executable=os.path.expandvars(self.config['DCS']['DCS_INSTALLATION']) + r'\bin\dcs.exe')
    server['PID'] = p.pid
    server['status'] = Status.LOADING
    return p


def stop_dcs(self, server: dict):
    self.bot.sendtoDCS(server, {"command": "shutdown"})


def start_srs(self, server: dict):
    self.log.debug(r'Launching SRS server with: "{}\SR-Server.exe" -cfg="{}"'.format(
        os.path.expandvars(self.config['DCS']['SRS_INSTALLATION']),
        os.path.expandvars(self.config[server['installation']]['SRS_CONFIG'])))
    return subprocess.Popen(['SR-Server.exe', '-cfg={}'.format(
        os.path.expandvars(self.config[server['installation']]['SRS_CONFIG']))],
                           executable=os.path.expandvars(self.config['DCS']['SRS_INSTALLATION']) + r'\SR-Server.exe')


def check_srs(self, server: dict) -> bool:
    return is_open(self.config[server['installation']]['SRS_HOST'], self.config[server['installation']]['SRS_PORT'])


def stop_srs(self, server: dict) -> bool:
    p = find_process('SR-Server.exe', server['installation'])
    if p:
        p.kill()
        return True
    else:
        return False


def str_to_class(name):
    try:
        module_name, class_name = name.rsplit('.', 1)
        return getattr(importlib.import_module(module_name), class_name)
    except AttributeError:
        return None


# Return a player from the internal list
def get_player(self, server_name: str, **kwargs):
    if server_name in self.bot.player_data:
        df = self.bot.player_data[server_name]
        if 'id' in kwargs:
            row = df[df['id'] == kwargs['id']]
        elif 'name' in kwargs:
            row = df[df['name'] == kwargs['name']]
        elif 'ucid' in kwargs:
            row = df[df['ucid'] == kwargs['ucid']]
        else:
            return None
        if not row.empty:
            return row.to_dict('records')[0]
    return None


def get_crew_members(self, server_name: str, player_id: int):
    # get the pilot
    pilot = get_player(self, server_name, id=player_id)
    if pilot:
        # now find players that have the same slot
        df = self.bot.player_data[server_name]
        rows = df[df['slot'] == pilot['slot']]
        if not rows.empty:
            return rows.to_dict('records')
    return None


def is_populated(self, server: dict) -> bool:
    if server['server_name'] not in self.bot.player_data:
        return True
    players = self.bot.player_data[server['server_name']]
    return len(players[players['active'] == True]) > 0


def sanitize(self) -> None:
    # Sanitizing MissionScripting.lua
    filename = os.path.expandvars(self.config['DCS']['DCS_INSTALLATION']) + r'\Scripts\MissionScripting.lua'
    backup = filename.replace('.lua', '.bak')
    if os.path.exists('./config/MissionScripting.lua'):
        self.log.info('- Sanitizing MissionScripting')
        shutil.copyfile(filename, backup)
        shutil.copyfile('./config/MissionScripting.lua', filename)
        return
    try:
        with open(filename, 'r') as infile:
            orig = infile.readlines()
        output = []
        dirty = False
        for line in orig:
            if ("sanitizeModule('io')" in line or "sanitizeModule('lfs')" in line) and not line.lstrip().startswith(
                    '--'):
                line = line.replace('sanitizeModule', '--sanitizeModule')
                dirty = True
            # old sanitization (pre 2.7.9)
            elif 'require = nil' in line and not line.lstrip().startswith('--'):
                line = line.replace('require', '--require')
            # new sanitization (2.7.9 and above)
            elif ("_G['require'] = nil" in line or "_G['package'] = nil" in line) and not line.lstrip().startswith('--'):
                line = line.replace('_G', '--_G')
                dirty = True
            output.append(line)
        if dirty:
            self.log.info('- Sanitizing MissionScripting')
            # backup original file
            shutil.copyfile(filename, backup)
            with open(filename, 'w') as outfile:
                outfile.writelines(output)
    except (OSError, IOError) as e:
        self.log.error(f"Can't access {filename}. Make sure, {self.config['DCS']['DCS_INSTALLATION']} is writable.")
        raise e


def format_string(string_: str, default_: Optional[str] = None, **kwargs) -> str:
    class NoneFormatter(string.Formatter):
        def format_field(self, value, spec):
            if value is None:
                if default_:
                    value = default_
                else:
                    raise KeyError
            return super().format_field(value, spec)
    try:
        string_ = NoneFormatter().format(string_, **kwargs)
    except KeyError:
        string_ = ''
    return string_


def sendChatMessage(self, server_name: str, player: int, message: str):
    self.bot.sendtoDCS(self.globals[server_name], {
        "command": "sendChatMessage",
        "to": player,
        "message": message
    })


def convert_time(seconds: int):
    retval = ""
    days = int(seconds / 86400)
    if days != 0:
        retval += f"{days}d"
    seconds = seconds - days * 86400
    hours = int(seconds / 3600)
    if hours != 0:
        if len(retval):
            retval += ":"
        retval += f"{hours:02d}h"
    seconds = seconds - hours * 3600
    minutes = int(seconds / 60)
    if len(retval):
        retval += ":"
    retval += f"{minutes:02d}m"
    return retval

def get_sides(message: discord.Message, server: dict) -> list[str]:
    sides = []
    if config.getboolean(server['installation'], 'COALITIONS'):
        # TODO: cache that
        roles = {
            "All Blue": set(),
            "All Red": set(),
            "everyone": discord.Role,
            "DCS": discord.Role,
            "Blue": discord.Role,
            "Red": discord.Role,
        }
        da_roles = [x.strip() for x in config['ROLES']['DCS Admin'].split(',')]
        gm_roles = [x.strip() for x in config['ROLES']['GameMaster'].split(',')]
        # find all roles that are allowed to see red and blue
        for role in message.channel.guild.roles:
            if role.name == config['ROLES']['Coalition Blue']:
                roles['Blue'] = role
                roles['All Blue'].add(role.name)
            elif role.name == config['ROLES']['Coalition Red']:
                roles['Red'] = role
                roles['All Red'].add(role.name)
            elif role.name == config['ROLES']['DCS']:
                roles['DCS'] = role
            elif role.name == '@everyone':
                roles['everyone'] = role
            elif role.name in da_roles:
                roles['All Blue'].add(role.name)
                roles['All Red'].add(role.name)
            elif role.name in gm_roles:
                roles['All Blue'].add(role.name)
                roles['All Red'].add(role.name)
        # check, which coalition specific data can be displayed in the questioned channel by that user
        for role in message.author.roles:
            if (role.name in gm_roles or role.name in da_roles) and \
                    not message.channel.overwrites_for(roles['everyone']).read_messages and \
                    not message.channel.overwrites_for(roles['DCS']).read_messages and \
                    not message.channel.overwrites_for(roles['Blue']).read_messages and \
                    not message.channel.overwrites_for(roles['Red']).read_messages:
                sides = ['Blue', 'Red']
                break
            elif role.name in roles['All Blue'] \
                    and message.channel.overwrites_for(roles['Blue']).send_messages and \
                    not message.channel.overwrites_for(roles['Red']).read_messages:
                sides = ['Blue']
                break
            elif role.name in roles['All Red'] \
                    and message.channel.overwrites_for(roles['Red']).send_messages and \
                    not message.channel.overwrites_for(roles['Blue']).read_messages:
                sides = ['Red']
                break
    else:
        sides = ['Blue', 'Red']
    return sides


def format_embed(data):
    embed = discord.Embed(color=discord.Color.blue())
    if 'title' in data and len(data['title']) > 0:
        embed.title = data['title']
    if 'description' in data and len(data['description']) > 0:
        embed.description = data['description']
    if 'img' in data and len(data['img']) > 0:
        embed.set_image(url=data['img'])
    if 'footer' in data and len(data['footer']) > 0:
        embed.set_footer(text=data['footer'])
    if 'fields' in data:
        for name, value in data['fields'].items():
            embed.add_field(name=name, value=value)
    return embed
