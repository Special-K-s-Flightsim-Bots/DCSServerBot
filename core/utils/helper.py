from __future__ import annotations

import builtins
import importlib
import json
import luadata
import os
import pkgutil
import re
import shutil
import string
import tempfile
import time
import unicodedata

# for eval
import random
import math

from croniter import croniter
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from typing import Optional, Union, TYPE_CHECKING, Generator, Iterable
from urllib.parse import urlparse

# ruamel YAML support
from pykwalify.errors import SchemaError
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()

if TYPE_CHECKING:
    from core import ServerProxy, DataObject, Node

__all__ = [
    "parse_time",
    "is_in_timeframe",
    "is_match_daystate",
    "str_to_class",
    "format_string",
    "sanitize_string",
    "convert_time",
    "format_time",
    "get_utc_offset",
    "format_period",
    "slugify",
    "alternate_parse_settings",
    "get_all_players",
    "is_ucid",
    "get_presets",
    "get_preset",
    "is_valid_url",
    "is_github_repo",
    "matches_cron",
    "dynamic_import",
    "SettingsDict",
    "RemoteSettingsDict",
    "tree_delete",
    "evaluate",
    "for_each",
    "YAMLError"
]


def parse_time(time_str: str) -> datetime:
    fmt, time_str = ('%H:%M', time_str.replace('24:', '00:')) \
        if time_str.find(':') > -1 else ('%H', time_str.replace('24', '00'))
    return datetime.strptime(time_str, fmt)


def is_in_timeframe(time: datetime, timeframe: str) -> bool:
    """
    Check if a given time falls within a specified timeframe.

    :param time: The time to check.
    :type time: datetime
    :param timeframe: The timeframe to check against. Format: 'HH:MM-HH:MM' or 'HH:MM'.
    :type timeframe: str
    :return: True if the time falls within the timeframe, False otherwise.
    :rtype: bool
    """
    pos = timeframe.find('-')
    if pos != -1:
        start_time = parse_time(timeframe[:pos])
        end_time = parse_time(timeframe[pos + 1:])
        if end_time <= start_time:
            end_time += timedelta(days=1)
    else:
        start_time = end_time = parse_time(timeframe)
    check_time = time.replace(year=start_time.year, month=start_time.month, day=start_time.day, second=0, microsecond=0)
    return start_time <= check_time <= end_time


def is_match_daystate(time: datetime, daystate: str) -> bool:
    """
    Check if the given time matches the daystate.

    :param time: The datetime object representing the time to check.
    :param daystate: A string that defines the daystate for each weekday.
    :return: True if the daystate for the given time's weekday is 'Y', False otherwise.

    """
    state = daystate[time.weekday()]
    return state.upper() == 'Y'


def str_to_class(name: str):
    """
    Get a class from a string representation.

    :param name: The string representation of the class, in the format `module_name.ClassName`.
    :return: The class object if found, None otherwise.
    """
    module_name, _, class_name = name.rpartition('.')
    try:
        module = import_module(module_name) if module_name else builtins
        return getattr(module, class_name)
    except AttributeError:
        return None


def format_string(string_: str, default_: Optional[str] = None, **kwargs) -> str:
    """
    Format the given string using the provided keyword arguments.

    :param string_: The string to be formatted.
    :param default_: The default value to be used when a variable is None. If not provided, an empty string will be used.
    :param kwargs: Keyword arguments to be used for formatting.
    :return: The formatted string.
    """
    class NoneFormatter(string.Formatter):
        def format_field(self, value, spec):
            if not isinstance(value, bool) and not value:
                spec = ''
                value = default_ or ''
            elif isinstance(value, list):
                value = '\n'.join(value)
            elif isinstance(value, dict):
                value = json.dumps(value)
            elif isinstance(value, bool):
                value = str(value).lower()
            return super().format_field(value, spec)

    try:
        string_ = NoneFormatter().format(string_, **kwargs)
    except KeyError:
        string_ = ""
    return string_


def sanitize_string(s: str) -> str:
    # Replace single and double quotes, semicolons and backslashes
    s = re.sub(r"[\"';\\]", "", s)

    # Replace comment sequences
    s = re.sub(r"--|/\*|\*/", "", s)

    return s


SECONDS_IN_DAY = 86400
SECONDS_IN_HOUR = 3600
SECONDS_IN_MINUTE = 60
TIME_LABELS = [("d", "day"), ("h", "hour"), ("m", "minute")]


def format_time_units(units, label_single, label_plural=None):
    label = label_single if units == 1 else (label_plural or label_single + "s")
    return f"{units} {label}"


def process_time(seconds, time_unit_seconds, retval, label_symbol, label_single, colon_format=False):
    units, seconds = calculate_time(time_unit_seconds, seconds)
    if units != 0:
        if len(retval):
            if colon_format:
                retval += ":"
            else:
                retval += " "
        formatted_time = format_time_units(units, label_single) if not colon_format else f"{units:02d}{label_symbol}"
        retval += formatted_time
    return seconds, retval


def calculate_time(units_of_time, total_seconds):
    units = int(total_seconds / units_of_time)
    remaining_seconds = total_seconds - units * units_of_time
    return units, remaining_seconds


def convert_time_and_format(seconds: int, colon_format=False):
    retval = ""
    for label_symbol, label_single in TIME_LABELS:
        time_unit_seconds = globals()["SECONDS_IN_" + label_single.upper()]
        seconds, retval = process_time(seconds, time_unit_seconds, retval, label_symbol, label_single, colon_format)
    if colon_format:
        retval = f"{retval}:{seconds:02d}s" if retval else f"{seconds:02d}s"
    else:
        retval += f" {format_time_units(seconds, 'second')}" if seconds > 0 else ""
    return retval


def convert_time(seconds: int):
    """
    Converts the given number of seconds into a formatted string representation of time.

    :param seconds: The number of seconds to be converted into time representation.
    :return: The formatted string representation of time.
    """
    retval = convert_time_and_format(int(seconds), True)
    return retval


def format_time(seconds: int):
    """
    Format the given number of seconds into a human-readable format.

    :param seconds: The number of seconds to be formatted.
    :return: The formatted time string in HH:MM:SS format.
    """
    return convert_time_and_format(int(seconds), False)


def get_utc_offset() -> str:
    """
    Return the UTC offset of the current local time in the format HH:MM.

    :return: The UTC offset in the format HH:MM.
    :rtype: str
    """
    # Get the struct_time objects for the current local time and UTC time
    current_time = time.time()
    localtime = time.localtime(current_time)
    gmtime = time.gmtime(current_time)

    # Convert these to datetime objects
    local_dt = datetime(*localtime[:6], tzinfo=timezone.utc)
    utc_dt = datetime(*gmtime[:6], tzinfo=timezone.utc)

    # Compute the UTC offset
    offset = local_dt - utc_dt

    # Express the offset in hours:minutes
    offset_minutes = int(offset.total_seconds() / 60)
    offset_hours = offset_minutes // 60
    offset_minutes %= 60
    if offset.total_seconds() == 0:
        return ""
    return f"{offset_hours:+03d}:{offset_minutes:02d}"


def format_period(period: str) -> str:
    if period == 'day':
        return 'Daily'
    else:
        return period.capitalize() + 'ly'


def slugify(value, allow_unicode=False):
    """
    Taken from https://github.com/django/django/blob/master/django/utils/text.py
    Convert to ASCII if 'allow_unicode' is False. Convert spaces or repeated
    dashes to single dashes. Remove characters that aren't alphanumerics,
    underscores, or hyphens. Convert to lowercase. Also strip leading and
    trailing whitespace, dashes, and underscores.
    """
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize('NFKC', value)
    else:
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value.lower())
    return re.sub(r'[-\s]+', '-', value).strip('-_')


def alternate_parse_settings(path: str):
    def parse(value: str) -> Union[int, str, bool]:
        if value.startswith('"'):
            return value[1:-1]
        elif value == 'true':
            return True
        elif value == 'false':
            return False
        else:
            return int(value)

    exp1 = re.compile(r'cfg\["(?P<key>.*)"\] = (?P<value>.*)')
    exp2 = re.compile(r'cfg\["(?P<key1>.*)"\]\[(?P<key2>.*)\] = (?P<value>.*)')

    settings = dict()
    with open(path, mode='r', encoding='utf-8') as infile:
        for idx, line in enumerate(infile.readlines()):
            if idx == 0:
                continue
            match = exp2.search(line)
            if match:
                if match.group('key2').isnumeric():
                    settings[match.group('key1')].insert(int(match.group('key2')) - 1, parse(match.group('value')))
                else:
                    settings[match.group('key1')][parse(match.group('key2'))] = parse(match.group('value'))
            else:
                match = exp1.search(line)
                if match:
                    if match.group('value') == "{}":
                        if match.group('key') == 'missionList':
                            settings['missionList'] = list()
                        else:
                            settings[match.group('key')] = dict()
                    else:
                        settings[match.group('key')] = parse(match.group('value'))
    return settings


def get_all_players(self, linked: Optional[bool] = None, watchlist: Optional[bool] = None,
                    vip: Optional[bool] = None) -> list[tuple[str, str]]:
    """
    This method `get_all_players` returns a list of tuples containing the UCID and name of players from the database. Filtering can be optionally applied by providing values for the parameters
    * `linked`, `watchlist`, and `vip`.

    :param self: The object instance of the class.
    :param linked: Optional boolean parameter to filter players based on whether they are linked to a Discord account or not. If set to `True`, only linked players will be returned. If set
    * to `False`, only unlinked players will be returned. If not provided, no filtering based on linking status will be applied.
    :param watchlist: Optional boolean parameter to filter players based on whether they are on the watchlist or not. If set to `True`, only players on the watchlist will be returned. If
    * set to `False`, only players not on the watchlist will be returned. If not provided, no filtering based on watchlist status will be applied.
    :param vip: Optional boolean parameter to filter players based on whether they are VIP players or not. If set to `True`, only VIP players will be returned. If set to `False`, only non
    *-VIP players will be returned. If not provided, no filtering based on VIP status will be applied.
    :return: A list of tuples containing the UCID and name of players from the database.

    """
    sql = "SELECT ucid, name FROM players WHERE length(ucid) = 32"
    if watchlist:
        sql += " AND watchlist IS NOT FALSE"
    if vip:
        sql += " AND vip IS NOT FALSE"
    if linked is not None:
        if linked:
            sql += " AND discord_id != -1 AND manual IS TRUE"
        else:
            sql += " AND manual IS FALSE"
    with self.pool.connection() as conn:
        return [(row[0], row[1]) for row in conn.execute(sql)]


def is_ucid(ucid: Optional[str]) -> bool:
    """
    :param ucid: The UCID (User Client ID) is a unique identifier used in the system.
    :return: Returns True if the UCID is valid, False otherwise.
    """
    return ucid is not None and len(ucid) == 32 and ucid.isalnum() and ucid == ucid.lower()


def get_presets(node: Node) -> Iterable[str]:
    """
    Return the set of non-hidden presets from the YAML files in the 'config' directory.

    :return: A set of non-hidden presets.
    """
    presets = set()
    for file in Path(node.config_dir).glob('presets*.yaml'):
        with open(file, mode='r', encoding='utf-8') as infile:
            presets |= set([
                name for name, value in yaml.load(infile).items()
                if isinstance(value, dict) and not value.get('hidden', False)
            ])
    return presets


def get_preset(node: Node, name: str, filename: Optional[str] = None) -> Optional[dict]:
    """
    :param node: The node where the configuration is stored.
    :param name: The name of the preset to retrieve.
    :param filename: The optional filename of the preset file to search in. If not provided, it will search for preset files in the 'config' directory.
    :return: The dictionary containing the preset data if found, or None if the preset was not found.
    """
    def _read_presets_from_file(filename: Path, name: str) -> Optional[dict]:
        all_presets = yaml.load(filename.read_text(encoding='utf-8'))
        preset = all_presets.get(name)
        if isinstance(preset, list):
            return {k: v for d in preset for k, v in all_presets.get(d, {}).items()}
        return preset

    if filename:
        return _read_presets_from_file(Path(filename), name)
    else:
        for file in Path(node.config_dir).glob('presets*.yaml'):
            preset = _read_presets_from_file(file, name)
            if preset:
                return preset
    return None


def is_valid_url(url: str) -> bool:
    """
    Check if a given URL is valid.

    :param url: The URL to be validated.
    :type url: str
    :return: True if the URL is valid, False otherwise.
    :rtype: bool
    """
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


def is_github_repo(url: str) -> bool:
    """
    :param url: The URL of the repository to check.
    :return: True if the URL is a valid GitHub repository URL, False otherwise.

    The `is_github_repo` function takes in a URL and determines whether it is a valid GitHub repository URL. The function returns `True` if the URL is a valid GitHub repository URL, and
    * `False` otherwise.

    To be considered a valid GitHub repository URL, the URL must meet the following criteria:
    - The URL must pass the `is_valid_url` check, which verifies that the URL is valid.
    - The URL must start with 'https://github.com/'.
    - The URL must not end with '.zip'.

    Example Usage:
    ```
    is_github_repo('https://github.com/example/repo')  # True
    is_github_repo('https://github.com/example/repo.zip')  # False
    ```
    """
    return is_valid_url(url) and url.startswith('https://github.com/') and not url.endswith('.zip')


def matches_cron(datetime_obj: datetime, cron_string: str):
    """
    Check if a given datetime object matches the specified cron string.

    :param datetime_obj: The datetime object to be checked.
    :type datetime_obj: datetime.datetime
    :param cron_string: The cron string to be matched against.
    :type cron_string: str
    :return: True if the datetime object matches the cron string, False otherwise.
    :rtype: bool
    """
    cron_job = croniter(cron_string, datetime_obj)
    next_date = cron_job.get_next(datetime)
    prev_date = cron_job.get_prev(datetime)
    return datetime_obj == prev_date or datetime_obj == next_date


def dynamic_import(package_name: str):
    package = importlib.import_module(package_name)
    for loader, module_name, is_pkg in pkgutil.walk_packages(package.__path__):
        if is_pkg:
            globals()[module_name] = importlib.import_module(f"{package_name}.{module_name}")


class SettingsDict(dict):
    """
    A dictionary subclass that represents settings stored in a file.

    :param obj: The DataObject associated with this SettingsDict.
    :type obj: DataObject
    :param path: The path of the settings file.
    :type path: str
    :param root: The root key of the settings file.
    :type root: str
    """
    def __init__(self, obj: DataObject, path: str, root: str):
        super().__init__()
        self.path = path
        self.root = root
        self.mtime = 0
        self.obj = obj
        self.log = obj.log
        self.read_file()

    def read_file(self):
        if not os.path.exists(self.path):
            self.log.error(f"- File {self.path} does not exist! Creating an empty file.")
            with open(self.path, mode='w', encoding='utf-8') as f:
                f.write(f"{self.root} = {{}}")
            return
        self.mtime = os.path.getmtime(self.path)
        if self.path.lower().endswith('.lua'):
            try:
                data = luadata.read(self.path, encoding='utf-8')
            except Exception as ex:
                self.log.debug(f"Exception while reading {self.path}:\n{ex}")
                data = alternate_parse_settings(self.path)
                if not data:
                    self.log.error("- Error while parsing {}!".format(os.path.basename(self.path)))
                    raise ex
        elif self.path.lower().endswith('.yaml'):
            with open(self.path, mode='r', encoding='utf-8') as file:
                data = yaml.load(file)
        if data:
            self.clear()
            self.update(data)

    def write_file(self):
        # DO NOT write empty config files. This means in general that there is an error.
        if not len(self):
            return
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        if self.path.lower().endswith('.lua'):
            with open(tmpname, mode='wb') as outfile:
                outfile.write((f"{self.root} = " + luadata.serialize(self, indent='\t',
                                                                     indent_level=0)).encode('utf-8'))
        elif self.path.lower().endswith('.json'):
            with open(tmpname, mode="w", encoding='utf-8') as outfile:
                yaml.dump(self, outfile)
        shutil.copy2(tmpname, self.path)
        self.mtime = os.path.getmtime(self.path)

    def __setitem__(self, key, value):
        if self.mtime < os.path.getmtime(self.path):
            self.log.debug(f'{self.path} changed, re-reading from disk.')
            self.read_file()
        super().__setitem__(key, value)
        if len(self):
            self.write_file()
        else:
            self.log.error("- Writing of {} aborted due to empty set.".format(os.path.basename(self.path)))

    def __getitem__(self, item):
        if self.mtime < os.path.getmtime(self.path):
            self.log.debug(f'{self.path} changed, re-reading from disk.')
            self.read_file()
        return super().__getitem__(item)


class RemoteSettingsDict(dict):
    """
    A dictionary subclass that allows remote access to settings on a server.

    Args:
        server (ServerProxy): The server proxy object used to communicate with the server.
        obj (str): The name of the object containing the settings.
        data (dict, optional): The initial data to populate the dictionary with. Defaults to None.

    Attributes:
        server (ServerProxy): The server proxy object used to communicate with the server.
        obj (str): The name of the object containing the settings.

    Raises:
        None

    Returns:
        None

    """
    def __init__(self, server: ServerProxy, obj: str, data: Optional[dict] = None):
        self.server = server
        self.obj = obj
        if data:
            super().__init__(data)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        msg = {
            "command": "rpc",
            "object": "Server",
            "method": "_settings.__setitem__",
            "params": {
                "key": key,
                "value": value
            }
        }
        self.server.send_to_dcs(msg)


def tree_delete(d: dict, key: str, debug: Optional[bool] = False):
    """
    Clears an element from nested structure (a mix of dictionaries and lists)
    given a key in the form "root/element1/element2".
    """
    keys = key.split('/')
    curr_element = d

    try:
        for key in keys[:-1]:
            if isinstance(curr_element, dict):
                curr_element = curr_element[key]
            else:  # if it is a list
                curr_element = curr_element[int(key)]
    except KeyError:
        return

    if debug:
        print("  " * len(keys) + f"|_ Deleting {keys[-1]}")

    if isinstance(curr_element, dict):
        if isinstance(curr_element[keys[-1]], dict):
            curr_element[keys[-1]] = {}
        elif isinstance(curr_element[keys[-1]], list):
            curr_element[keys[-1]] = []
        else:
            del curr_element[keys[-1]]
    else:  # if it's a list
        curr_element.pop(int(keys[-1]))


def evaluate(value: Union[str, int, float, bool], **kwargs) -> Union[str, int, float, bool]:
    """
    Evaluate the given value, replacing placeholders with keyword arguments if necessary.

    :param value: The value to evaluate. Can be a string, integer, float, or boolean.
    :param kwargs: Additional keyword arguments to replace placeholders in the value.
    :return: The evaluated value. Returns the input value if it is not a string or if it does not start with '$'.
             If the input value is a string starting with '$', it will be evaluated with placeholders replaced by keyword arguments.
    """
    if isinstance(value, (int, float, bool)) or not value.startswith('$'):
        return value
    return eval(format_string(value[1:], **kwargs))


def for_each(data: dict, search: list[str], depth: Optional[int] = 0, *,
             debug: Optional[bool] = False, **kwargs) -> Generator[dict]:
    """
    :param data: The data to iterate over.
    :param search: The search pattern to match elements in the data.
    :param depth: The current depth of the recursion. (Optional, default=0)
    :param debug: Flag indicating whether to print debug information. (Optional, default=False)
    :param kwargs: Additional keyword arguments for evaluating search patterns.
    :return: A generator that yields matching elements found in the data.

    This method recursively searches for elements in the given data that match the search pattern. The search pattern is defined as a list of strings, where each string represents a step
    * in the search process. Each element in the data will be evaluated against the search pattern, and if a match is found, it will be yielded by the generator.

    The method supports various search patterns:
    - "*" indicates iterating over elements in a list or dictionary.
    - "[index1, index2, ...]" indicates selecting specific indexes in a list or dictionary.
    - "$expression" indicates evaluating a search pattern with additional keyword arguments.

    If the search pattern is fully matched or the data is empty, the method will yield the data itself. If debug is set to True, debug information will be printed during the search process
    *.
    """
    def process_iteration(_next, data, search, depth, debug):
        if isinstance(data, list):
            for value in data:
                yield from for_each(value, search, depth + 1, debug=debug)
        elif isinstance(data, dict):
            for value in data.values():
                yield from for_each(value, search, depth + 1, debug=debug)

    def process_indexing(_next, data, search, depth, debug):
        if isinstance(data, list):
            indexes = [int(x.strip()) for x in _next[1:-1].split(',')]
            for index in indexes:
                if index <= 0 or len(data) < index:
                    if debug:
                        print("  " * depth + f"|_ {index}. element not found")
                    yield None
                if debug:
                    print("  " * depth + f"|_ Selecting {index}. element")
                yield from for_each(data[index - 1], search, depth + 1, debug=debug)
        elif isinstance(data, dict):
            indexes = [x.strip() for x in _next[1:-1].split(',')]
            for index in indexes:
                if index not in data:
                    if debug:
                        print("  " * depth + f"|_ {index}. element not found")
                    yield None
                if debug:
                    print("  " * depth + f"|_ Selecting element {index}")
                yield from for_each(data[index], search, depth + 1, debug=debug)

    def process_pattern(_next, data, search, depth, debug, **kwargs):
        if isinstance(data, list):
            for idx, value in enumerate(data):
                if evaluate(_next, **(kwargs | value)):
                    if debug:
                        print("  " * depth + f"  - Element {idx + 1} matches.")
                    yield from for_each(value, search, depth + 1, debug=debug)
        else:
            if evaluate(_next, **(kwargs | data)):
                if debug:
                    print("  " * depth + "  - Element matches.")
                yield from for_each(data, search, depth + 1, debug=debug)

    if not data or len(search) == depth:
        if debug:
            print("  " * depth + ("|_ RESULT found => Processing ..." if data else "|_ NO result found, skipping."))
        yield data
    else:
        _next = search[depth]
        if _next == '*':
            if debug:
                print("  " * depth + f"|_ Iterating over {len(data)} {search[depth - 1]} elements")
            yield from process_iteration(_next, data, search, depth, debug)
        elif _next.startswith('['):
            yield from process_indexing(_next, data, search, depth, debug)
        elif _next.startswith('$'):
            if debug:
                print("  " * depth + f"|_ Searching pattern {_next} on {len(data)} {search[depth - 1]} elements")
            yield from process_pattern(_next, data, search, depth, debug, **kwargs)
        elif _next in data:
            if debug:
                print("  " * depth + f"|_ {_next} found.")
            yield from for_each(data.get(_next), search, depth + 1, debug=debug)
        else:
            if debug:
                print("  " * depth + f"|_ {_next} not found.")
            yield None


class YAMLError(Exception):
    """

    The `YAMLError` class is an exception class that is raised when there is an error encountered while parsing or scanning a YAML file.

    **Methods:**

    """
    def __init__(self, file: str, ex: Union[MarkedYAMLError, ValueError, SchemaError]):
        super().__init__(f"Error in {file}, " + ex.__str__().replace('"<unicode string>"', file))
