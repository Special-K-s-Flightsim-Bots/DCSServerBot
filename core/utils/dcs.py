from __future__ import annotations
# noinspection PyPackageRequirements
import aiohttp
import math
import os
import psycopg2
import re
import shutil
import xml
import xmltodict
from contextlib import closing
from core.const import SAVED_GAMES
from typing import Optional, List, Tuple, TYPE_CHECKING
from . import config

if TYPE_CHECKING:
    from core import Server


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


def get_all_servers(self) -> list[str]:
    retval: list[str] = list()
    conn = self.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            cursor.execute(f"SELECT server_name FROM servers WHERE last_seen > (DATE(NOW()) - interval '1 week')")
            for row in cursor.fetchall():
                retval.append(row[0])
        return retval
    except (Exception, psycopg2.DatabaseError) as error:
        self.log.exception(error)
    finally:
        self.pool.putconn(conn)


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


def desanitize(self) -> None:
    # Sanitizing MissionScripting.lua
    filename = os.path.expandvars(config['DCS']['DCS_INSTALLATION']) + r'\Scripts\MissionScripting.lua'
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
            if line.lstrip().startswith('--'):
                output.append(line)
                continue
            if "sanitizeModule('os')" in line or "sanitizeModule('io')" in line or "sanitizeModule('lfs')" in line:
                line = line.replace('sanitizeModule', '--sanitizeModule')
                dirty = True
            elif "_G['require'] = nil" in line or "_G['package'] = nil" in line:
                line = line.replace('_G', '--_G')
                dirty = True
            output.append(line)
        if dirty:
            self.log.info('- Desanitizing MissionScripting')
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


def is_banned(self, ucid: str):
    conn = self.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM bans WHERE ucid = %s", (ucid,))
            return cursor.fetchone()[0] > 0
    except (Exception, psycopg2.DatabaseError) as error:
        self.log.exception(error)
    finally:
        self.pool.putconn(conn)


def get_current_mission_file(server: Server) -> Optional[str]:
    filename: str = None
    if not server.current_mission or not server.current_mission.filename:
        for i in range(int(server.getServerSetting('listStartIndex')), 0, -1):
            filename = server.getServerSetting(i)
            if server.current_mission:
                server.current_mission.filename = filename
            break
    else:
        filename = server.current_mission.filename
    return filename
