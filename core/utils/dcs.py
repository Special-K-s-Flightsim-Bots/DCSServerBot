import aiohttp
import luadata
import math
import os
import re
import shutil
import xml
import xmltodict
from core.const import SAVED_GAMES
from typing import Optional, List, Tuple
from . import config
from .. import utils

REGEXP = {
    'branch': re.compile(r'"branch": "(?P<branch>.*)"'),
    'version': re.compile(r'"version": "(?P<version>.*)"'),
    'server_name': re.compile(r'\["name"\] = "(?P<server_name>.*)"')
}
PATCHNOTES_URL = 'https://www.digitalcombatsimulator.com/en/news/changelog/rss/'


def findDCSInstallations(server_name: Optional[str] = None) -> List[Tuple[str, str]]:
    installations = []
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
                    installations.append((settings['name'], dirname))
    return installations


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


def desanitize(self, _filename: str = None) -> None:
    # Sanitizing MissionScripting.lua
    if not _filename:
        filename = os.path.expandvars(config['DCS']['DCS_INSTALLATION']) + r'\Scripts\MissionScripting.lua'
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
        self.log.error(f"Can't access {filename}. Make sure, {config['DCS']['DCS_INSTALLATION']} is writable.")
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
