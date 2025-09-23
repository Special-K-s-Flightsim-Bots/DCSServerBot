import filecmp
import luadata
import math
import os
import re
import shutil
import stat
import sys

# import the correct mgrs library
if sys.version_info < (3, 13):
    import mgrs
else:
    from pymgrs.mgrs import LLtoUTM, encode, UTMtoLL, decode

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
    "dd_to_dmm",
    "dms_to_dd",
    "dd_to_mgrs",
    "mgrs_to_dd",
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


def dd_to_dms(dd: float, precision: int = 3) -> tuple[int, int, int, int]:
    """
    Convert a decimal‑degree value into a DMS tuple.

    Parameters
    ----------
    dd       : float
        Latitude or longitude in decimal degrees.
    precision: int, optional
        Number of decimal places for the seconds fraction.
        The function returns the fraction as an *integer*:
        `precision = 2`  →  hundredths of a second (0‑99)
        `precision = 3`  →  milliseconds   (0‑999) – default.

    Returns
    -------
    tuple[int, int, int, int]
        (degrees, minutes, seconds, fraction)
        All four components are integers.  The sign of the value is
        stored only in the *degrees* component.

    Examples
    --------
    >>> dd_to_dms(49.001234)
    (49, 0, 0, 78)           # 78 × 10⁻³ s = 0.078s
    >>> dd_to_dms(-49.999999, precision=2)
    (-49, 59, 59, 99)        # -49°59'59.99″
    """
    # --- 1️⃣  sign & absolute value ------------------------------------
    sign = 1 if dd >= 0 else -1
    dd_abs = abs(dd)

    # --- 2️⃣  whole degrees & remaining fraction ------------------------
    deg, frac = divmod(dd_abs, 1)          # deg  → float, frac → 0‑1
    deg = int(deg)                         # int degrees

    # --- 3️⃣  minutes ----------------------------------------------------
    minu, frac = divmod(frac * 60, 1)       # minu  → int, frac  → 0‑1
    minu = int(minu)

    # --- 4️⃣  seconds ---------------------------------------------------
    sec, frac = divmod(frac * 60, 1)        # sec   → int, frac  → 0‑1
    sec = int(sec)

    # --- 5️⃣  fractional seconds (as an integer) -----------------------
    frac_units = 10 ** precision          # 100→2decimals, 1000→3decimals…
    frac_int = int(round(frac * frac_units))

    # --- 6️⃣  carry‑over (rounding may produce 60′/60″) -----------------
    if frac_int >= frac_units:          # e.g. 0.999… rounds to 1.0s
        sec += 1
        frac_int = 0

    if sec >= 60:
        minu += 1
        sec = 0

    if minu >= 60:
        deg += 1
        minu = 0

    # --- 7️⃣  apply sign to the degrees component -----------------------
    deg *= sign

    return deg, minu, sec, frac_int


def dd_to_dmm(lat: float, lon: float, prec: int = 2) -> str:
    """
    Convert a lat/lon pair (in decimal degrees) into a DMM string:

        N 38°53.217' E 122°24.300'

    """
    eps = 1e-9                    # tolerance for floating‑point rounding

    def _fmt(val: float, is_lat: bool) -> str:
        sign = 1 if val >= 0 else -1
        dir_ = ('N' if is_lat else 'E') if sign == 1 else ('S' if is_lat else 'W')
        abs_val = abs(val)

        deg  = int(math.floor(abs_val))
        mins_raw = (abs_val - deg) * 60

        # ---- Normalise minutes ----
        mins = round(mins_raw, prec)            # round to desired precision first
        if mins >= 60 - eps:                    # minute overflow → carry to next degree
            deg += 1
            mins = 0.0

        return f'{dir_} {deg}°{mins:0{prec+3}.{prec}f}\''
        # format: e.g. "E 10°00.00'"

    return f'{_fmt(lat, True)} {_fmt(lon, False)}'


_dms_re = re.compile(
    r"""^\s*
    (?P<dir>[NSEW])?                     # optional direction letter
    (?P<deg>\d{2})\s*                    # 2‑digit degrees
    (?P<min>\d{2})\s*                    # 2‑digit minutes
    (?P<sec>\d{2}(?:\.\d+)?)\s*          # 2‑digit seconds (decimal optional)
    $""",
    re.IGNORECASE | re.VERBOSE,
)


def dms_to_dd(dms: str) -> float:
    """
    Convert a compact DMS string (e.g. 'N382623.45') to decimal degrees.

    Parameters
    ----------
    dms : str
        Compact or “classic” DMS representation.

    Returns
    -------
    float
        Positive for North / East, negative for South / West.

    Raises
    ------
    ValueError
        If the string cannot be parsed.
    """
    match = _dms_re.match(dms)
    if not match:
        raise ValueError(f"Invalid compact DMS: {dms!r}")

    # Pull out the numeric parts
    deg   = float(match.group("deg"))
    minu  = float(match.group("min"))
    sec   = float(match.group("sec"))

    # DMS → DD formula
    dd = deg + minu / 60.0 + sec / 3600.0

    # Decide the sign
    direction = match.group("dir")
    if direction:
        direction = direction.upper()
        sign = -1 if direction in ("S", "W") else 1
    else:
        sign = 1
        if dms.lstrip().startswith("-"):
            sign = -1

    return sign * dd


def dd_to_mgrs(lat: float, lon: float, prec: int = 5) -> str:
    if sys.version_info < (3, 13):
        mgrs_converter = mgrs.MGRS()
        return mgrs_converter.toMGRS(lat, lon, MGRSPrecision=prec)
    else:
        ll_coords = LLtoUTM(lat, lon)
        return encode(ll_coords, 5)


def mgrs_to_dd(value: str) -> tuple[float, float]:
    if sys.version_info < (3, 13):
        mgrs_converter = mgrs.MGRS()
        return mgrs_converter.toLatLon(value, inDegrees=True)
    else:
        ll_coords = UTMtoLL(decode(value))
        return ll_coords['lat'], ll_coords['lon']


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
    language = node.config.get('language', 'en')
    wordlist = os.path.join(node.config_dir, 'profanity.txt')
    if not os.path.exists(wordlist):
        shutil.copy2(os.path.join('samples', 'wordlists', f"{language}.txt"), wordlist)
    with open(wordlist, mode='r', encoding='utf-8') as wl:
        words = [x.strip() for x in wl.readlines() if not x.startswith('#')]

    targetfile = os.path.join(os.path.expandvars(node.locals['DCS']['installation']), 'Data', 'censor.lua')
    bakfile = targetfile.replace('.lua', '.bak')
    try:
        if not os.path.exists(bakfile):
            shutil.copy2(targetfile, bakfile)
        with open(targetfile, mode='wb') as outfile:
            # we write with locale EN, because the server runs with EN
            outfile.write(("EN = " + luadata.serialize(
                words, indent='\t', indent_level=0)).encode('utf-8'))
    except PermissionError:
        node.log.warning(
            f"The bot needs write permission on your DCS installation directory to update the profanity filter.")
