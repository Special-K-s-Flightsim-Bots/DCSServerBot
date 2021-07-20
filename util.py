import os
import re

SAVED_GAMES = os.path.expandvars('%USERPROFILE%\\Saved Games')


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
