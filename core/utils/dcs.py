import filecmp
import luadata
import math
import os
import re
import shutil
import stat
import sys

from contextlib import suppress
from core.const import SAVED_GAMES
from core.data.node import Node
from core.utils.helper import alternate_parse_settings
from typing import Optional

__all__ = [
    "ParseError",
    "findDCSInstances",
    "desanitize",
    "is_desanitized",
    "dd_to_dms",
    "get_active_runways",
    "create_writable_mission",
    "get_orig_file",
    "lua_pattern_to_python_regex",
    "format_frequency",
    "init_profanity_filter"
]


class ParseError(Exception):
    def __init__(self, filename, original=None):
        super().__init__(f'Parse error in file: {filename}')
        self.filename = filename
        self.original = original


def findDCSInstances(server_name: Optional[str] = None) -> list[tuple[str, str]]:
    if sys.platform != 'win32':
        return []
    instances = []
    for dirname in os.listdir(SAVED_GAMES):
        if os.path.isdir(os.path.join(SAVED_GAMES, dirname)):
            path = os.path.join(SAVED_GAMES, dirname, 'Config', 'serverSettings.lua')
            if os.path.exists(path):
                try:
                    settings = luadata.read(path, encoding='utf-8')
                except Exception:
                    try:
                        # DSMC workaround
                        settings = alternate_parse_settings(path)
                    except Exception as ex:
                        raise ParseError(path, ex)
                if 'name' not in settings:
                    settings['name'] = 'DCS Server'
                if server_name:
                    if settings['name'] == server_name:
                        return [(server_name, dirname)]
                else:
                    instances.append((settings['name'], dirname))
    return instances


def desanitize(self, _filename: str = None) -> None:
    # Sanitizing MissionScripting.lua
    if not _filename:
        filename = os.path.join(self.node.installation, 'Scripts', 'MissionScripting.lua')
    else:
        filename = _filename
    try:
        os.chmod(filename, stat.S_IWUSR)
    except PermissionError:
        self.log.error(f"Can't desanitize {filename}, no write permissions!")
        raise

    backup = filename.replace('.lua', '.bak')
    if os.path.exists(os.path.join(self.node.config_dir, 'MissionScripting.lua')):
        if _filename:
            self.log.error('SLmod is installed, it will overwrite your custom MissionScripting.lua again!')
        self.log.info('- Desanitizing MissionScripting')
        # don't fail if no backup could be created (for whatever reason)
        with suppress(Exception):
            shutil.copyfile(filename, backup)
        shutil.copyfile(os.path.join(self.node.config_dir, 'MissionScripting.lua'), filename)
        return
    try:
        with open(filename, mode='r', encoding='utf-8') as infile:
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
            with suppress(Exception):
                shutil.copyfile(filename, backup)
            with open(filename, mode='w', encoding='utf-8') as outfile:
                outfile.writelines(output)
    except (OSError, IOError) as e:
        self.log.error(f"Can't access {filename}. Make sure, {self.node.installation} is writable.")
        raise e


def is_desanitized(node: Node) -> bool:
    alt_filename = os.path.join(node.config_dir, 'MissionScripting.lua')
    filename = os.path.join(node.installation, 'Scripts', 'MissionScripting.lua')
    if os.path.exists(alt_filename):
        return filecmp.cmp(filename, alt_filename, shallow=False)
    with open(filename, mode='r', encoding='utf-8') as infile:
        for line in infile.readlines():
            if line.lstrip().startswith('--'):
                continue
            if "sanitizeModule('io')" in line or "sanitizeModule('lfs')" in line:
                return False
            elif "_G['require'] = nil" in line or "_G['package'] = nil" in line:
                return False
            elif "require = nil" in line:
                return False
    return True


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


def create_writable_mission(filename: str) -> str:
    if filename.endswith('.orig'):
        filename = filename[:-5]
    try:
        with open(filename, mode='a'):
            return filename
    except PermissionError:
        if '.dcssb' in filename:
            return os.path.join(os.path.dirname(filename).replace('.dcssb', ''),
                                os.path.basename(filename))
        else:
            dirname = os.path.join(os.path.dirname(filename), '.dcssb')
            os.makedirs(dirname, exist_ok=True)
            return os.path.join(dirname, os.path.basename(filename))

def get_orig_file(filename: str, *, create_file: bool = True) -> Optional[str]:
    if filename.endswith('.orig'):
        return filename if os.path.exists(filename) else None
    if '.dcssb' in filename:
        mission_file = os.path.join(os.path.dirname(filename).replace('.dcssb', ''),
                                    os.path.basename(filename))
        if not os.path.exists(filename):
            filename = mission_file
    else:
        mission_file = filename
    if filename.startswith('DSMC'):
        filename = re.sub(r'_\d+(?=\.miz$)', '', filename)
    mission_file = mission_file.replace('.sav', '.miz')
    orig_file = os.path.join(os.path.dirname(mission_file), '.dcssb', os.path.basename(mission_file)) + '.orig'
    if not os.path.exists(orig_file):
        if create_file:
            dirname = os.path.join(os.path.dirname(orig_file))
            os.makedirs(dirname, exist_ok=True)
            # make an initial backup
            shutil.copy2(filename, orig_file)
        else:
            return None
    return orig_file


def lua_pattern_to_python_regex(lua_pattern):
    translation_dict = {
        '%a': '[a-zA-Z]',
        '%c': '[\\x00-\\x1f\\x7f]',
        '%d': '\\d',
        '%l': '[a-z]',
        '%u': '[A-Z]',
        '%w': '\\w',
        '%x': '[a-fA-F0-9]',
        '%p': '[-!\\"#$%&\'()*+,./:;<=>?@[\\\\\\]^_`{|}~]',
        '%s': '\\s',
        '%z': '\\x00',
    }

    python_regex = lua_pattern
    for lua, python in translation_dict.items():
        python_regex = python_regex.replace(lua, python)

    return python_regex


def format_frequency(frequency_hz: int, *, band: bool = True) -> str:
    frequency_mhz = frequency_hz / 1e6
    if 30 <= frequency_mhz < 300:
        _band = "VHF"
    elif 300 <= frequency_mhz < 3000:
        _band = "UHF"
    else:
        _band = None
    if band:
        return f"{frequency_mhz:.1f} MHz ({_band})"
    else:
        return f"{frequency_mhz:.1f} MHz"


def init_profanity_filter(node: Node):
    # Profanity filter
    language = node.config.get('language', 'en')
    wordlist = os.path.join(node.config_dir, 'profanity.txt')
    if not os.path.exists(wordlist):
        shutil.copy2(os.path.join('samples', 'wordlists', f"{language}.txt"), wordlist)
    with open(wordlist, mode='r', encoding='utf-8') as wl:
        words = [x.strip() for x in wl.readlines() if not x.startswith('#')]
    targetfile = os.path.join(os.path.expandvars(node.locals['DCS']['installation']), 'Data', 'censor.lua')
    bakfile = targetfile.replace('.lua', '.bak')
    if not os.path.exists(bakfile):
        shutil.copy2(targetfile, bakfile)
    with open(targetfile, mode='wb') as outfile:
        outfile.write((f"{language.upper()} = " + luadata.serialize(
            words, indent='\t', indent_level=0)).encode('utf-8'))
