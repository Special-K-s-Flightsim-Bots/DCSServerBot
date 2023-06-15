import aiohttp
import certifi
import gzip
import json
import luadata
import math
import os
import re
import shutil
import ssl

from core.const import SAVED_GAMES
from typing import Optional, Tuple
from .. import utils

REGEXP = {
    'branch': re.compile(r'"branch": "(?P<branch>.*)"'),
    'version': re.compile(r'"version": "(?P<version>.*)"'),
    'server_name': re.compile(r'\["name"\] = "(?P<server_name>.*)"')
}
UPDATER_URL = 'https://www.digitalcombatsimulator.com/gameapi/updater/branch/{}/'


def findDCSInstances(server_name: Optional[str] = None) -> list[Tuple[str, str]]:
    instances = []
    for dirname in os.listdir(SAVED_GAMES):
        if os.path.isdir(os.path.join(SAVED_GAMES, dirname)):
            path = os.path.join(SAVED_GAMES, dirname, 'Config\\serverSettings.lua')
            if os.path.exists(path):
                try:
                    settings = luadata.read(path, encoding='utf-8')
                except Exception:
                    # DSMC workaround
                    settings = utils.alternate_parse_settings(path)
                if 'name' not in settings:
                    settings['name'] = 'DCS Server'
                if server_name:
                    if settings['name'] == server_name:
                        return [(server_name, dirname)]
                else:
                    instances.append((settings['name'], dirname))
    return instances


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


async def getLatestVersion(branch: str, *, userid: Optional[str] = None,
                           password: Optional[str] = None) -> Optional[str]:
    if userid:
        auth = aiohttp.BasicAuth(login=userid, password=password)
    else:
        auth = None
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
            ssl=ssl.create_default_context(cafile=certifi.where())), auth=auth) as session:
        async with session.get(UPDATER_URL.format(branch)) as response:
            if response.status == 200:
                return json.loads(gzip.decompress(await response.read()))['versions2'][-1]['version']
    return None


def desanitize(self, _filename: str = None) -> None:
    # Sanitizing MissionScripting.lua
    if not _filename:
        filename = os.path.expandvars(self.node.locals['DCS']['installation']) + r'\Scripts\MissionScripting.lua'
    else:
        filename = _filename
    backup = filename.replace('.lua', '.bak')
    if os.path.exists('./config/MissionScripting.lua'):
        if _filename:
            self.log.error('SLmod is installed, it will overwrite your custom MissionScripting.lua again!')
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
            if line.lstrip().startswith('--'):
                output.append(line)
                continue
            if "sanitizeModule('io')" in line or "sanitizeModule('lfs')" in line:
                line = line.replace('sanitizeModule', '--sanitizeModule')
                dirty = True
            elif "_G['require'] = nil" in line or "_G['package'] = nil" in line:
                line = line.replace('_G', '--_G')
                dirty = True
            elif "require = nil" in line:
                line = line.replace('require', '--require')
                dirty = True
            output.append(line)
        if dirty:
            self.log.info(f'- Desanitizing {filename}')
            # backup original file
            shutil.copyfile(filename, backup)
            with open(filename, 'w') as outfile:
                outfile.writelines(output)
    except (OSError, IOError) as e:
        self.log.error(f"Can't access {filename}. Make sure, {self.node.locals['DCS']['installation']} is writable.")
        raise e


def dd_to_dms(dd):
    frac, degrees = math.modf(dd)
    frac, minutes = math.modf(frac * 60)
    frac, seconds = math.modf(frac * 60)
    return degrees, minutes, seconds, frac


def get_active_runways(runways, wind):
    retval = []
    for runway in runways:
        heading = int(runway[:2]) * 10
        wind_dir = (wind['dir'] + 180) % 360
        diff = abs((wind_dir - heading + 180 + 360) % 360 - 180)
        if diff <= 90:
            retval.append(runway)
    if len(retval) == 0:
        retval = ['n/a']
    return retval
