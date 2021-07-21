import asyncio
import aiohttp
import os
import re
import xmltodict

SAVED_GAMES = os.path.expandvars('%USERPROFILE%\\Saved Games')
REGEXP = {
    'branch': re.compile(r'"branch": "(?P<branch>.*)"'),
    'version': re.compile(r'"version": "(?P<version>.*)"')
}
PATCHNOTES_URL = 'https://www.digitalcombatsimulator.com/en/news/changelog/rss/'


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
