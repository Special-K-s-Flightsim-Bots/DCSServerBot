# utils.py
import asyncio
import aiohttp
import discord
import math
import os
import psutil
import re
import socket
import subprocess
import psycopg2
import xmltodict
from core import const
from datetime import datetime, timedelta
from configparser import ConfigParser
from contextlib import closing, suppress
from discord.ext import commands
from typing import Union

SAVED_GAMES = os.path.expandvars('%USERPROFILE%\\Saved Games')
REGEXP = {
    'branch': re.compile(r'"branch": "(?P<branch>.*)"'),
    'version': re.compile(r'"version": "(?P<version>.*)"')
}
PATCHNOTES_URL = 'https://www.digitalcombatsimulator.com/en/news/changelog/rss/'

config = ConfigParser()
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
                            installations.append(dirname)
                else:
                    installations.append(dirname)
    return installations


def changeServerSettings(server_name, name, value):
    assert name in ['listStartIndex', 'password', 'name', 'maxPlayers'], 'Value can\'t be changed.'
    if isinstance(value, str):
        value = '"' + value + '"'
    installation = findDCSInstallations(server_name)[0]
    serverSettings = os.path.join(SAVED_GAMES, installation, 'Config\\serverSettings.lua')
    tmpSettings = os.path.join(SAVED_GAMES, installation, 'Config\\serverSettings.tmp')
    with open(serverSettings, encoding='utf8') as infile:
        inlines = infile.readlines()
    outlines = []
    for line in inlines:
        if '["{}"]'.format(name) in line:
            #    outlines.append('["{}"] = {}\n'.format(name, value))
            outlines.append(re.sub(' = ([^,]*)', ' = {}'.format(value), line))
        else:
            outlines.append(line)
    with open(tmpSettings, 'w', encoding='utf8') as outfile:
        outfile.writelines(outlines)
    os.remove(serverSettings)
    os.rename(tmpSettings, serverSettings)


def getInstalledVersion(path):
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


async def getLatestVersion(branch):
    async with aiohttp.ClientSession() as session:
        async with session.get(PATCHNOTES_URL) as response:
            xpars = xmltodict.parse(await response.text())
            for item in xpars['rss']['channel']['item']:
                if branch in item['link']:
                    return item['link'].split('/')[-2]


def match(name1, name2):
    if name1 == name2:
        return len(name1)
    # remove any tags
    n1 = re.sub('^[\[\<\(=].*[=\)\>\]]', '', name1).strip()
    if len(n1) == 0:
        n1 = name1
    n2 = re.sub('^[\[\<\(=].*[=\)\>\]]', '', name2).strip()
    if len(n2) == 0:
        n2 = name2
    if n1 in n2:
        return len(n1)
    elif n2 in n1:
        return len(n2)
    # remove any special characters
    n1 = re.sub('[^a-zA-Z0-9 ]', '', n1).lower()
    n2 = re.sub('[^a-zA-Z0-9 ]', '', n2).lower()
    if (len(n1) == 0) or (len(n2) == 0):
        return 0
    if n1 in n2:
        return len(n1)
    elif n2 in n1:
        return len(n2)
    else:
        return 0


def match_user(self, data: Union[dict, discord.Member], rematch=False):
    conn = self.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            # try to match a DCS user with a Discord member
            if isinstance(data, dict):
                if not rematch:
                    sql = 'SELECT discord_id FROM players WHERE ucid = %s AND discord_id != -1'
                    cursor.execute(sql, (data['ucid'], ))
                    result = cursor.fetchone()
                    if result and result[0] != -1:
                        return self.bot.guilds[0].get_member(result[0])
                # we could not find the user, so try to match them
                dcs_name = data['name']
                max_weight = 0
                best_fit = None
                for member in self.bot.get_all_members():
                    weight = match(dcs_name, member.display_name)
                    if weight > max_weight:
                        max_weight = weight
                        best_fit = member
                return best_fit
            # try to match a Discord member with a DCS user that played on the servers
            else:
                max_weight = 0
                best_fit = None
                sql = 'SELECT ucid, name from players'
                if rematch is False:
                    sql += ' WHERE discord_id = -1'
                cursor.execute(sql)
                for row in cursor.fetchall():
                    weight = max(match(data.nick, row['name']), match(data.name, row['name']))
                    if weight > max_weight:
                        max_weight = weight
                        best_fit = row['ucid']
                return best_fit
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


async def selection_list(self, ctx, data, embed_formatter, num=5, marker=-1):
    message = None
    try:
        j = 0
        while len(data) > 0:
            max_i = (len(data) % num) if (len(data) - j * num) < num else num
            embed = embed_formatter(data[j * num:j * num + max_i], (marker - j * num) if marker in range(j * num, j * num + max_i + 1) else 0)
            message = await ctx.send(embed=embed)
            if j > 0:
                await message.add_reaction('◀️')
            for i in range(1, max_i + 1):
                if (j * num + i) != marker:
                    await message.add_reaction(chr(0x30 + i) + '\u20E3')
                else:
                    await message.add_reaction('🔄')
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
            elif react.emoji == '🔄':
                return marker - j * num - 1
            elif (len(react.emoji) > 1) and ord(react.emoji[0]) in range(0x31, 0x39):
                return (ord(react.emoji[0]) - 0x31) + j * num
    except asyncio.TimeoutError:
        if message:
            await message.delete()
            return -1


async def yn_question(self, ctx, question, msg=None):
    yn_embed = discord.Embed(title=question, color=discord.Color.red())
    if msg is not None:
        yn_embed.add_field(name=msg, value='_ _')
    yn_msg = await ctx.send(embed=yn_embed)
    await yn_msg.add_reaction('🇾')
    await yn_msg.add_reaction('🇳')
    react = await wait_for_single_reaction(self, ctx, yn_msg)
    await yn_msg.delete()
    return react.emoji == '🇾'


async def get_server(self, ctx: Union[discord.ext.commands.context.Context, str]):
    for server_name, server in self.bot.DCSServers.items():
        if isinstance(ctx, discord.ext.commands.context.Context):
            if server['status'] == 'Unknown':
                continue
            if (int(server['status_channel']) == ctx.channel.id) or (int(server['chat_channel']) == ctx.channel.id) or (int(server['admin_channel']) == ctx.channel.id):
                return server
        else:
            if server_name == ctx:
                return server
    return None


def has_role(item: str):
    def predicate(ctx):
        if ctx.guild is None:
            raise commands.errors.NoPrivateMessage()

        if 'ROLES' not in config or item not in config['ROLES']:
            valid_roles = [item]
        else:
            valid_roles = [x.strip() for x in config['ROLES'][item].split(',')]
        for role in ctx.author.roles:
            if role.name in valid_roles:
                return True
        raise commands.errors.MissingRole(item)

    return commands.check(predicate)


def isOpen(ip, port):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(3)
        return s.connect_ex((ip, int(port))) == 0


async def get_external_ip():
    async with aiohttp.ClientSession() as session:
        async with session.get('https://api.ipify.org') as resp:
            return await resp.text()


def findProcess(proc, installation):
    for p in psutil.process_iter(['name', 'cmdline']):
        if p.info['name'] == proc:
            with suppress(Exception):
                if installation in p.info['cmdline'][1]:
                    return p
    return None


def DDtoDMS(dd):
    frac, degrees = math.modf(dd)
    frac, minutes = math.modf(frac * 60)
    frac, seconds = math.modf(frac * 60)
    return degrees, minutes, seconds, frac


def getActiveRunways(runways, wind):
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


def start_dcs(self, installation):
    self.log.debug('Launching DCS server with: "{}\\bin\\dcs.exe" --server --norender -w {}'.format(
        os.path.expandvars(self.config['DCS']['DCS_INSTALLATION']), installation))
    return subprocess.Popen(['dcs.exe', '--server', '--norender', '-w', installation], executable=os.path.expandvars(self.config['DCS']['DCS_INSTALLATION']) + '\\bin\\dcs.exe')


def start_srs(self, installation):
    self.log.debug('Launching SRS server with: "{}\\SR-Server.exe" -cfg="{}"'.format(
        os.path.expandvars(self.config['DCS']['SRS_INSTALLATION']), os.path.expandvars(self.config[installation]['SRS_CONFIG'])))
    return subprocess.Popen(['SR-Server.exe', '-cfg={}'.format(os.path.expandvars(self.config[installation]['SRS_CONFIG']))], executable=os.path.expandvars(self.config['DCS']['SRS_INSTALLATION']) + '\\SR-Server.exe')


def format_mission_embed(self, mission):
    server = self.bot.DCSServers[mission['server_name']]
    if 'serverSettings' not in server:
        self.bot.log.error('Can\'t format mission embed due to incomplete server data.')
        return None
    plugins = []
    embed = discord.Embed(title='{} [{}/{}]\n{}'.format(mission['server_name'],
                                                        mission['num_players'], server['serverSettings']['maxPlayers'],
                                                        ('"' + mission['current_mission'] + '"') if server['status'] in ['Running', 'Paused'] else ('_' + server['status'] + '_')),
                          color=discord.Color.blue())

    embed.set_thumbnail(url=self.STATUS_IMG[server['status']])
    embed.add_field(name='Map', value=mission['current_map'])
    embed.add_field(name='Server-IP / Port', value=self.bot.external_ip + ':' + str(server['serverSettings']['port']))
    if len(server['serverSettings']['password']) > 0:
        embed.add_field(name='Password', value=server['serverSettings']['password'])
    else:
        embed.add_field(name='Password', value='_ _')
    uptime = int(mission['mission_time'])
    embed.add_field(name='Runtime', value=str(timedelta(seconds=uptime)))
    if 'start_time' in mission:
        if mission['date']['Year'] >= 1970:
            date = datetime(mission['date']['Year'], mission['date']['Month'],
                            mission['date']['Day'], 0, 0).timestamp()
            real_time = date + mission['start_time'] + uptime
            value = str(datetime.fromtimestamp(real_time))
        else:
            value = '{}-{:02d}-{:02d} {}'.format(mission['date']['Year'], mission['date']['Month'],
                                                 mission['date']['Day'], timedelta(seconds=mission['start_time'] + uptime))
    else:
        value = '-'
    embed.add_field(name='Date/Time in Mission', value=value)
    embed.add_field(name='Avail. Slots',
                    value='🔹 {}  |  {} 🔸'.format(mission['num_slots_blue'] if 'num_slots_blue' in mission else '-', mission['num_slots_red'] if 'num_slots_red' in mission else '-'))
    embed.add_field(name='▬' * 25, value='_ _', inline=False)
    if 'weather' in mission:
        if 'clouds' in mission and 'preset' in mission['clouds']:
            embed.add_field(name='Preset', value=mission['clouds']['preset']['readableNameShort'])
        else:
            embed.add_field(name='Weather', value='Dynamic')
        weather = mission['weather']
        embed.add_field(name='Temperature', value=str(int(weather['season']['temperature'])) + ' °C')
        embed.add_field(name='QNH', value='{:.2f} inHg'.format(weather['qnh'] * const.MMHG_IN_INHG))
        embed.add_field(name='Wind', value='\u2002Ground: {}° / {} kts\n\u20026600 ft: {}° / {} kts\n26000 ft: {}° / {} kts'.format(
            int(weather['wind']['atGround']['dir'] + 180) % 360, int(weather['wind']['atGround']['speed']),
            int(weather['wind']['at2000']['dir'] + 180) % 360, int(weather['wind']['at2000']['speed']),
            int(weather['wind']['at8000']['dir'] + 180) % 360, int(weather['wind']['at8000']['speed'])))
        if 'clouds' in mission:
            if 'preset' in mission['clouds']:
                embed.add_field(name='Cloudbase',
                                value=f'{int(mission["clouds"]["base"] * const.METER_IN_FEET):,} ft')
            else:
                embed.add_field(name='Clouds', value='Base:\u2002\u2002\u2002\u2002 {:,} ft\nDensity:\u2002\u2002 {}/10\nThickness: {:,} ft'.format(
                    int(mission['clouds']['base'] * const.METER_IN_FEET), mission['clouds']['density'], int(mission['clouds']['thickness'] * const.METER_IN_FEET)))
        else:
            embed.add_field(name='Clouds', value='n/a')
        visibility = weather['visibility']['distance']
        if weather['enable_fog'] is True:
            visibility = weather['fog']['visibility'] * const.METER_IN_FEET
        embed.add_field(name='Visibility', value=f'{int(visibility):,} ft')
        embed.add_field(name='▬' * 25, value='_ _', inline=False)
    if 'SRSSettings' in server:
        plugins.append('SRS')
        if 'EXTERNAL_AWACS_MODE' in server['SRSSettings'] and server['SRSSettings']['EXTERNAL_AWACS_MODE'] is True:
            value = '🔹 Pass: {}\n🔸 Pass: {}'.format(
                server['SRSSettings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD'], server['SRSSettings']['EXTERNAL_AWACS_MODE_RED_PASSWORD'])
        else:
            value = '_ _'
        embed.add_field(name='SRS [{}]'.format(
            server['SRSSettings']['SERVER_SRS_PORT']), value=value)
    if 'lotAtcSettings' in server:
        plugins.append('LotAtc')
        embed.add_field(name='LotAtc [{}]'.format(server['lotAtcSettings']['port']), value='🔹 Pass: {}\n🔸 Pass: {}'.format(
            server['lotAtcSettings']['blue_password'], server['lotAtcSettings']['red_password']))
    if 'Tacview' in server['options']['plugins']:
        name = 'Tacview'
        if ('tacviewModuleEnabled' in server['options']['plugins']['Tacview'] and server['options']['plugins']['Tacview']['tacviewModuleEnabled'] is False) or ('tacviewFlightDataRecordingEnabled' in server['options']['plugins']['Tacview'] and server['options']['plugins']['Tacview']['tacviewFlightDataRecordingEnabled'] is False):
            value = 'disabled'
        else:
            plugins.append('Tacview')
            value = ''
            tacview = server['options']['plugins']['Tacview']
            if 'tacviewRealTimeTelemetryEnabled' in tacview and tacview['tacviewRealTimeTelemetryEnabled'] is True:
                name += ' RT'
                if 'tacviewRealTimeTelemetryPassword' in tacview and len(tacview['tacviewRealTimeTelemetryPassword']) > 0:
                    value += 'Password: {}\n'.format(tacview['tacviewRealTimeTelemetryPassword'])
            elif 'tacviewHostTelemetryPassword' in tacview and len(tacview['tacviewHostTelemetryPassword']) > 0:
                value += 'Password: "{}"\n'.format(tacview['tacviewHostTelemetryPassword'])
            if 'tacviewRealTimeTelemetryPort' in tacview and len(tacview['tacviewRealTimeTelemetryPort']) > 0:
                name += ' [{}]'.format(tacview['tacviewRealTimeTelemetryPort'])
            if 'tacviewRemoteControlEnabled' in tacview and tacview['tacviewRemoteControlEnabled'] is True:
                value += '**Remote Ctrl [{}]**\n'.format(tacview['tacviewRemoteControlPort'])
                if 'tacviewRemoteControlPassword' in tacview and len(tacview['tacviewRemoteControlPassword']) > 0:
                    value += 'Password: {}'.format(tacview['tacviewRemoteControlPassword'])
            if len(value) == 0:
                value = 'enabled'
        embed.add_field(name=name, value=value)
    footer = '- Server is running DCS {}\n'.format(server['dcs_version'])
    if len(plugins) > 0:
        footer += '- The IP address of '
        if len(plugins) == 1:
            footer += plugins[0]
        else:
            footer += ', '.join(plugins[0:len(plugins) - 1]) + ' and ' + plugins[len(plugins) - 1]
        footer += ' is the same as the server.\n'
    for listener in self.bot.eventListeners:
        if (type(listener).__name__ == 'UserStatisticsEventListener') and \
                (mission['server_name'] in listener.statistics):
            footer += '- User statistics are enabled for this server.'
    embed.set_footer(text=footer)
    return embed
