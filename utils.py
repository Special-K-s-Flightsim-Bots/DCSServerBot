# utils.py
import asyncio
import aiohttp
import discord
import math
import os
import psutil
import re
import socket
import xmltodict
from configparser import ConfigParser
from contextlib import closing, suppress
from discord.ext import commands

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
        if (os.path.isdir(os.path.join(SAVED_GAMES, dirname))):
            serverSettings = os.path.join(SAVED_GAMES, dirname, 'Config\\serverSettings.lua')
            if (os.path.exists(serverSettings)):
                if (server_name):
                    with open(serverSettings, encoding='utf8') as f:
                        if '["name"] = "{}"'.format(server_name) in f.read():
                            installations.append(dirname)
                else:
                    installations.append(dirname)
    return installations


def changeServerSettings(server_name, name, value):
    assert name in ['listStartIndex', 'password', 'name', 'maxPlayers'], 'Value can\'t be changed.'
    if (isinstance(value, str)):
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
    with open(os.path.join(os.path.expandvars(path), 'autoupdate.cfg'), encoding='utf8') as config:
        lines = config.readlines()
    for line in lines:
        if ('"branch"' in line):
            match = REGEXP['branch'].search(line)
            if (match):
                branch = match.group('branch')
        elif ('"version"' in line):
            match = REGEXP['version'].search(line)
            if (match):
                version = match.group('version')
    return branch, version


async def getLatestVersion(branch):
    async with aiohttp.ClientSession() as session:
        async with session.get(PATCHNOTES_URL) as response:
            xpars = xmltodict.parse(await response.text())
            for item in xpars['rss']['channel']['item']:
                if (branch in item['link']):
                    return item['link'].split('/')[-2]


async def wait_for_single_reaction(self, ctx, message):
    def check_press(react, user):
        return (react.message.channel == ctx.message.channel) & (user == ctx.message.author) & (react.message.id == message.id)

    tasks = [self.bot.wait_for('reaction_add', check=check_press),
             self.bot.wait_for('reaction_remove', check=check_press)]
    try:
        done, tasks = await asyncio.wait(tasks, timeout=300, return_when=asyncio.FIRST_COMPLETED)
        if (len(done) > 0):
            react, _ = done.pop().result()
            return react
        else:
            raise asyncio.TimeoutError
    finally:
        for task in tasks:
            task.cancel()


async def yn_question(self, ctx, question, msg=None):
    yn_embed = discord.Embed(title=question, color=discord.Color.red())
    if (msg is not None):
        yn_embed.add_field(name=msg, value='_ _')
    yn_msg = await ctx.send(embed=yn_embed)
    await yn_msg.add_reaction('🇾')
    await yn_msg.add_reaction('🇳')
    react = await wait_for_single_reaction(self, ctx, yn_msg)
    await yn_msg.delete()
    return (react.emoji == '🇾')


async def get_server(self, ctx):
    server = None
    for key, item in self.bot.DCSServers.items():
        if (item['status'] == 'Unknown'):
            continue
        if ((int(item['status_channel']) == ctx.channel.id) or
            (int(item['chat_channel']) == ctx.channel.id) or
                (int(item['admin_channel']) == ctx.channel.id)):
            server = item
            break
    return server


def has_role(item: str):
    def predicate(ctx):
        if ctx.guild is None:
            raise commands.errors.NoPrivateMessage()

        if ('ROLES' not in config or item not in config['ROLES']):
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
        return (s.connect_ex((ip, int(port))) == 0)


async def get_external_ip():
    async with aiohttp.ClientSession() as session:
        async with session.get('https://api.ipify.org') as resp:
            return await resp.text()


def findProcess(proc, installation):
    for p in psutil.process_iter(['name', 'cmdline']):
        if (p.info['name'] == proc):
            with suppress(Exception):
                if (installation in p.info['cmdline'][1]):
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
        if (diff <= 90):
            retval.append(runway)
    if (len(retval) == 0):
        retval = ['n/a']
    return retval
