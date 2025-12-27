from __future__ import annotations

import asyncio
import base64
import builtins
import functools
import hashlib
import importlib
import inspect
import json
import keyword
import logging
import luadata
import os
import pkgutil
import re
import secrets
import shutil
import string
import tempfile
import threading
import time
import traceback
import unicodedata

# for eval
import random
import math

from collections.abc import Mapping
from copy import deepcopy
from croniter import croniter
from datetime import datetime, timedelta, timezone, tzinfo
from difflib import unified_diff
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Iterable, Callable, Any
from urllib.parse import urlparse

# ruamel YAML support
from pykwalify.errors import SchemaError
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()

if TYPE_CHECKING:
    from core import ServerProxy, DataObject, Node
    from services.servicebus import ServiceBus

__all__ = [
    "parse_time",
    "is_in_timeframe",
    "is_match_daystate",
    "str_to_class",
    "format_string",
    "sanitize_string",
    "convert_time",
    "format_time",
    "format_period",
    "slugify",
    "alternate_parse_settings",
    "exception_to_dict",
    "rebuild_exception",
    "ReprException",
    "get_all_players",
    "is_ucid",
    "get_presets",
    "get_preset",
    "is_valid_url",
    "is_github_repo",
    "matches_cron",
    "dynamic_import",
    "async_cache",
    "cache_with_expiration",
    "ThreadSafeDict",
    "SettingsDict",
    "RemoteSettingsDict",
    "tree_delete",
    "deep_merge",
    "hash_password",
    "run_parallel_nofail",
    "safe_set_result",
    "evaluate",
    "for_each",
    "YAMLError",
    "DictWrapper",
    "format_dict_pretty",
    "show_dict_diff",
    "to_valid_pyfunc_name",
    "pg_interval_to_seconds"
]

logger = logging.getLogger(__name__)


def parse_time(time_str: str, tz: tzinfo = None) -> datetime:
    fmt, time_str = ('%H:%M', time_str.replace('24:', '00:')) \
        if time_str.find(':') > -1 else ('%H', time_str.replace('24', '00'))
    ret = datetime.strptime(time_str, fmt)
    if tz is not None:
        ret = ret.replace(tzinfo=tz)
    return ret


def is_in_timeframe(time: datetime, timeframe: str, tz: tzinfo = None) -> bool:
    """
    Check if a given time falls within a specified timeframe.

    :param time: The time to check.
    :type time: datetime
    :param timeframe: The timeframe to check against. Format: 'HH:MM-HH:MM' or 'HH:MM'.
    :type timeframe: str
    :param tz: timezone to be used
    :type tz: datetime.tzinfo
    :return: True if the time falls within the timeframe, False otherwise.
    :rtype: bool
    """
    pos = timeframe.find('-')
    if pos != -1:
        start_time = parse_time(timeframe[:pos], tz).replace(year=time.year, month=time.month, day=time.day,
                                                             second=0, microsecond=0)
        end_time = parse_time(timeframe[pos + 1:], tz).replace(year=time.year, month=time.month, day=time.day,
                                                               second=0, microsecond=0)
        if end_time <= start_time:
            end_time += timedelta(days=1)
    else:
        start_time = end_time = parse_time(timeframe, tz).replace(year=time.year, month=time.month, day=time.day,
                                                                  second=0, microsecond=0)
    check_time = time.replace(second=0, microsecond=0)
    if tz:
        check_time = check_time.astimezone(tz=tz)
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


def format_string(string_: str, default_: str | None = None, **kwargs) -> str:
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
                value = repr(value)
            elif isinstance(value, dict):
                value = json.dumps(value)
            elif isinstance(value, bool):
                value = str(value).lower()
            elif isinstance(value, datetime) and value.tzinfo:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            return super().format_field(value, spec)

        def get_value(self, key, args, kwargs):
            if isinstance(key, int):
                return args[key]
            elif key in kwargs:
                return kwargs[key]
            else:
                return "{" + key + "}"

    try:
        string_ = NoneFormatter().format(string_, **kwargs)
    except (KeyError, TypeError):
        string_ = ""
    except IndexError as ex:
        logger.exception(ex)
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
    def parse(value: str) -> int | str | bool:
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


def exception_to_dict(e: BaseException) -> dict[str, Any]:
    """Return a plain‑dict representation of any exception."""
    exc_dict = {
        'class': f'{e.__class__.__module__}.{e.__class__.__name__}',
        'message': str(e),
        'traceback': traceback.format_exception_only(type(e), e), 'args': [repr(a) for a in e.args]
    }

    # Pull out useful OSError / socket attributes
    # (only those that are JSON‑friendly)
    for key in ('errno', 'strerror', 'filename', 'filename2'):
        if hasattr(e, key):
            exc_dict[key] = getattr(e, key)

    # If the exception has a kwargs dict (rare), sanitize it
    kwargs = getattr(e, 'kwargs', None)
    if isinstance(kwargs, dict):
        exc_dict['kwargs'] = {k: repr(v) for k, v in kwargs.items()}

    return exc_dict


class ReprException(Exception):
    """Wrapper that keeps the original payload if we can’t rebuild it."""
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        super().__init__(f'Unable to reconstruct exception from {payload!r}')


def rebuild_exception(payload: dict[str, Any]) -> BaseException:
    """
    Recreate a BaseException from the serialized payload.
    If the payload cannot be used to instantiate the original type,
    we return a lightweight wrapper that stores the payload.
    """
    cls = str_to_class(payload['class'])
    if not cls:
        return ReprException(payload)

    args = tuple(payload.get('args', ()))          # ensures a tuple
    kwargs = dict(payload.get('kwargs', {}))      # ensures a dict

    try:
        return cls(*args, **kwargs)
    except Exception:
        # Constructor raised an unexpected error – fall back.
        return ReprException(payload)


def get_all_players(self, linked: bool | None = None, watchlist: bool | None = None,
                    vip: bool | None = None) -> list[tuple[str, str]]:
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
    sql = "SELECT p.ucid, p.name FROM players p{} WHERE length(p.ucid) = 32"
    sub_sql = ""
    if watchlist:
        sub_sql = " JOIN watchlist w ON p.ucid = w.player_ucid"
    elif watchlist is False:
        sql += " AND p.ucid NOT IN (SELECT player_ucid FROM watchlist)"
    if vip:
        sql += " AND p.vip IS NOT FALSE"
    if linked is not None:
        if linked:
            sql += " AND p.discord_id != -1 AND p.manual IS TRUE"
        else:
            sql += " AND p.manual IS FALSE"
    sql = sql.format(sub_sql)
    with self.pool.connection() as conn:
        return [(row[0], row[1]) for row in conn.execute(sql)]


def is_ucid(ucid: str | None) -> bool:
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


def get_preset(node: Node, name: str, filename: str | list[str] | None = None) -> dict | None:
    """
    :param node: The node where the configuration is stored.
    :param name: The name of the preset to retrieve.
    :param filename: The optional filename of the preset file to search in. If not provided, it will search for preset files in the 'config' directory.
    :return: The dictionary containing the preset data if found, or None if the preset was not found.
    """
    @cache_with_expiration(120)
    def load_all_presets(filename: Path) -> dict:
        return yaml.load(filename.read_text(encoding='utf-8'))

    def _read_presets_from_file(filename: Path, name: str) -> dict | list | None:
        all_presets = load_all_presets(filename)
        preset = all_presets.get(name)
        if isinstance(preset, list):
            return [_read_presets_from_file(filename, x) for x in preset]
        return preset

    if isinstance(filename, str):
        preset_files = [filename]
    elif isinstance(filename, list):
        preset_files = filename
    else:
        preset_files = Path(node.config_dir).glob('presets*.yaml')

    for file in preset_files:
        preset = _read_presets_from_file(Path(file), name)
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
            try:
                globals()[module_name] = importlib.import_module(f"{package_name}.{module_name}")
            except Exception as ex:
                logger.error(f"Failed to import {module_name} due to {ex}, skipping.")

def async_cache(func: Callable):
    cache: dict[Any, Any] = {}
    pending: dict[Any, asyncio.Future] = {}
    locks: dict[Any, asyncio.Lock] = {}
    _SENTINEL = object()

    def get_cache_key(*args, **kwargs):
        signature = inspect.signature(func)
        bound_args = signature.bind(*args, **kwargs)
        bound_args.apply_defaults()

        # Convert unhashable types to hashable forms
        hashable_args = []
        for k, v in bound_args.arguments.items():
            if k not in ["interaction"]:  # Removed "self" from the exclusion list
                # For the self-parameter, use its id as part of the key
                if k == "self":
                    hashable_args.append(id(v))
                # if we have a .name element, use this as key instead
                elif hasattr(v, "name") and not isinstance(v, (str, bytes)):
                    hashable_args.append(("name", getattr(v, "name", None)))
                # Convert lists to tuples and handle nested lists
                elif isinstance(v, list):
                    hashable_args.append(tuple(tuple(x) if isinstance(x, list) else x for x in v))
                else:
                    hashable_args.append(v)

        # Use tuple instead of frozenset to preserve order and handle nested structures
        return func.__name__, tuple(hashable_args)

    async def _get_lock(key) -> asyncio.Lock:
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        key = get_cache_key(*args, **kwargs)

        cached = cache.get(key, _SENTINEL)
        if cached is not _SENTINEL:
            return cached

        lock = await _get_lock(key)
        async with lock:
            cached = cache.get(key, _SENTINEL)
            if cached is not _SENTINEL:
                return cached

            fut = pending.get(key)
            if fut is None:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                pending[key] = fut

                async def run():
                    try:
                        result = await func(*args, **kwargs)
                        cache[key] = result
                        fut.set_result(result)
                    except Exception as e:
                        fut.set_exception(e)
                    finally:
                        _ = pending.pop(key, None)

                loop.create_task(run())

        return await fut

    return wrapper


def cache_with_expiration(expiration: int):
    """
    Decorator to cache function results for a specific duration.
    Works with both sync and async functions.
    Adds concurrency safety (per-key in-flight coalescing).
    """
    def decorator(func: Callable) -> Callable:
        cache: dict[Any, Any] = {}
        cache_expiry: dict[Any, float] = {}
        pending: dict[Any, asyncio.Future] = {}
        locks: dict[Any, asyncio.Lock] = {}
        _SENTINEL = object()

        def get_cache_key(*args, **kwargs):
            signature = inspect.signature(func)
            bound_args = signature.bind(*args, **kwargs)
            bound_args.apply_defaults()

            # Convert unhashable types to hashable forms
            hashable_args = []
            for k, v in bound_args.arguments.items():
                # For the self-parameter, use its id as part of the key
                if k == "self":
                    hashable_args.append(id(v))
                # if we have a .name element, use this as key instead
                elif hasattr(v, "name") and not isinstance(v, (str, bytes)):
                    hashable_args.append(("name", getattr(v, "name", None)))
                # Convert lists to tuples and handle nested lists
                elif isinstance(v, list):
                    hashable_args.append(tuple(tuple(x) if isinstance(x, list) else x for x in v))
                else:
                    hashable_args.append(v)
            return func.__name__, tuple(hashable_args)

        def check_cache(cache_key):
            ts = cache_expiry.get(cache_key)
            if ts is not None and time.time() < ts:
                return cache.get(cache_key, _SENTINEL)
            return _SENTINEL

        def update_cache(cache_key, result):
            cache[cache_key] = result
            cache_expiry[cache_key] = time.time() + expiration
            return result

        async def _get_lock(cache_key) -> asyncio.Lock:
            # Fast path
            lock = locks.get(cache_key)
            if lock is None:
                # Create lazily; a small race is fine (we only need mutual exclusion, not singletons)
                lock = asyncio.Lock()
                locks[cache_key] = lock
            return lock

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            cache_key = get_cache_key(*args, **kwargs)

            cached_result = check_cache(cache_key)
            if cached_result is not _SENTINEL:
                return cached_result

            lock = await _get_lock(cache_key)
            async with lock:
                cached_result = check_cache(cache_key)
                if cached_result is not _SENTINEL:
                    return cached_result

                fut = pending.get(cache_key)
                if fut is None:
                    loop = asyncio.get_running_loop()
                    fut = loop.create_future()
                    pending[cache_key] = fut

                    async def producer():
                        try:
                            result = await func(*args, **kwargs)
                            update_cache(cache_key, result)
                            fut.set_result(result)
                        except Exception as e:
                            fut.set_exception(e)
                        finally:
                            _ = pending.pop(cache_key, None)

                    loop.create_task(producer())

            return await fut

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            cache_key = get_cache_key(*args, **kwargs)

            cached_result = check_cache(cache_key)
            if cached_result is not _SENTINEL:
                return cached_result

            result = func(*args, **kwargs)
            return update_cache(cache_key, result)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


class ThreadSafeDict(dict):
    def __init__(self, *args, **kwargs):
        self.lock = threading.Lock()
        super(ThreadSafeDict, self).__init__(*args, **kwargs)

    def __getitem__(self, key):
        with self.lock:
            return super(ThreadSafeDict, self).__getitem__(key)

    def __setitem__(self, key, value):
        with self.lock:
            return super(ThreadSafeDict, self).__setitem__(key, value)

    def __delitem__(self, key):
        with self.lock:
            return super(ThreadSafeDict, self).__delitem__(key)

    def __iter__(self):
        with self.lock:
            for key in dict.keys(self):
                yield key, dict.__getitem__(self, key)

    def items(self):
        with self.lock:
            return list(super().items())

    def values(self):
        with self.lock:
            return list(super().values())

    def keys(self):
        with self.lock:
            return list(super().keys())

    def get(self, key, default=None):
        with self.lock:
            return super().get(key, default)

    def pop(self, key, *default):
        with self.lock:
            return super().pop(key, *default)

    def update(self, *args, **kwargs):
        with self.lock:
            return super().update(*args, **kwargs)

    def clear(self):
        with self.lock:
            super().clear()


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
        self._bus = None
        self.read_file()

    def read_file(self):
        if not os.path.exists(self.path):
            return
        self.mtime = os.path.getmtime(self.path)
        data = None
        if self.path.lower().endswith('.lua'):
            content = None
            try:
                #data = luadata.read(self.path, encoding='utf-8')
                with open(self.path, mode='r', encoding='utf-8') as infile:
                    content = infile.read()
                data = luadata.unserialize(content, encoding='utf-8')
            except Exception as ex:
                self.log.debug(f"Exception while reading {self.path}:\n{ex}")
                if content:
                    self.log.debug("Content:\n{}".format(content))
                data = alternate_parse_settings(self.path)
                if not data:
                    self.log.error("- Error while parsing {}:\n{}".format(os.path.basename(self.path), ex))
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
            self.log.error("- Writing of {} aborted due to empty set.".format(os.path.basename(self.path)))
            return
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        if self.path.lower().endswith('.lua'):
            with open(tmpname, mode='wb') as outfile:
                outfile.write((f"{self.root} = " + luadata.serialize(self, indent='\t',
                                                                     indent_level=0)).encode('utf-8'))
        elif self.path.lower().endswith('.yaml'):
            with open(tmpname, mode="w", encoding='utf-8') as outfile:
                yaml.dump(self, outfile)
        if not os.path.exists(self.path):
            self.log.info(f"- Creating {self.path} as it did not exist yet.")
        try:
            shutil.copy2(tmpname, self.path)
            self.mtime = os.path.getmtime(self.path)
        except Exception as ex:
            self.log.exception(ex)
        finally:
            os.remove(tmpname)

    @property
    def bus(self) -> "ServiceBus":
        if self._bus is None:
            from core.services.registry import ServiceRegistry
            from services.servicebus import ServiceBus

            self._bus = ServiceRegistry.get(ServiceBus)
        return self._bus

    def update_master(self, key, value = None, *, method: str):
        if self.root == 'cfg':
            obj = '_settings'
        else:
            obj = '_options'
        params = {
            "key": key,
            "sync": False
        }
        if value:
            params['value'] = value
        msg = {
            "command": "rpc",
            "object": "Server",
            "server_name": self.obj.name,
            "method": f"{obj}.{method}",
            "params": {
                "key": key,
                "value": value,
                "sync": False
            }
        }
        if self.bus:
            self.bus.loop.create_task(self.bus.send_to_node(msg))

    def __setitem__(self, key, value, *, sync: bool = False):
        if os.path.exists(self.path) and self.mtime < os.path.getmtime(self.path):
            self.log.debug(f'{self.path} changed, re-reading from disk.')
            self.read_file()
        super().__setitem__(key, value)
        self.write_file()
        if not self.obj.node.master:
            self.update_master(key, value, method='__setitem__')

    def __getitem__(self, item):
        if os.path.exists(self.path) and self.mtime < os.path.getmtime(self.path):
            self.log.debug(f'{self.path} changed, re-reading from disk.')
            self.read_file()
        return super().__getitem__(item)

    def __delitem__(self, key, *, sync: bool = False):
        if os.path.exists(self.path) and self.mtime < os.path.getmtime(self.path):
            self.log.debug(f'{self.path} changed, re-reading from disk.')
            self.read_file()
        if self.get(key) is not None:
            super().__delitem__(key)
            self.write_file()
            if not self.obj.node.master:
                self.update_master(key, method='__delitem__')

    def get(self, key, default=None):
        try:
            return self.__getitem__(key)
        except KeyError:
            return default

    def pop(self, key, *default):
        try:
            value = self.__getitem__(key)
            self.__delitem__(key)
        except KeyError:
            if default:
                return default[0]
            else:
                raise
        return value


class RemoteSettingsDict(dict):
    """A dictionary-like class for managing remote settings.

    This class inherits from the built-in dict class and provides additional functionality for managing settings on a remote server.

    Args:
        server (ServerProxy): The server proxy object that handles communication with the remote server.
        obj (str): The name of the object on the remote server that the settings belong to.
        data (dict | None): Optional initial data for the settings dictionary.

    Attributes:
        server (ServerProxy): The server proxy object that handles communication with the remote server.
        obj (str): The name of the object on the remote server that the settings belong to.

    """
    def __init__(self, server: ServerProxy, obj: str, data: dict | None = None):
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        self.server = server
        self.obj = obj
        self.bus = ServiceRegistry.get(ServiceBus)
        if data:
            super().__init__(data)

    def __setitem__(self, key, value, *, sync: bool = True):
        super().__setitem__(key, value)
        if sync:
            msg = {
                "command": "rpc",
                "object": "Server",
                "server_name": self.server.name,
                "method": f"{self.obj}.__setitem__",
                "params": {
                    "key": key,
                    "value": value
                }
            }
            self.bus.loop.create_task(self.bus.send_to_node(msg, node=self.server.node))

    def __delitem__(self, key, *, sync: bool = True):
        super().__delitem__(key)
        if sync:
            msg = {
                "command": "rpc",
                "object": "Server",
                "server_name": self.server.name,
                "method": f"{self.obj}.__delitem__",
                "params": {
                    "key": key
                }
            }
            self.bus.loop.create_task(self.bus.send_to_node(msg, node=self.server.node))


def tree_delete(d: dict, key: str, debug: bool | None = False):
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
        logger.debug("  " * len(keys) + f"|_ Deleting {keys[-1]}")

    if isinstance(curr_element, dict):
        if isinstance(curr_element[keys[-1]], dict):
            curr_element[keys[-1]] = {}
        elif isinstance(curr_element[keys[-1]], list):
            curr_element[keys[-1]] = []
        else:
            del curr_element[keys[-1]]
    else:  # if it's a list
        curr_element.pop(int(keys[-1]))


def deep_merge(d1: Mapping[str, Any], d2: Mapping[str, Any]) -> Mapping[str, Any]:
    """
       Merge two dictionaries recursively.  Non‑mapping values are overwritten.

       Parameters
       ----------
       d1, d2 : Mapping
           Input mappings to merge.  They are *not* modified.

       Returns
       -------
       dict
           A new dictionary containing the deep merge of `d1` and `d2`.
       """
    if not isinstance(d1, Mapping):
        raise TypeError(f"d1 must be a Mapping, got {type(d1).__name__}")
    if not isinstance(d2, Mapping):
        raise TypeError(f"d2 must be a Mapping, got {type(d2).__name__}")

    result: dict = dict(d1)  # shallow copy of d1

    for key, value in d2.items():
        # If both sides are mappings, merge recursively
        if (
                key in result
                and isinstance(result[key], Mapping)
                and isinstance(value, Mapping)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            # Overwrite or add the new key/value pair
            result[key] = value

    return result


def hash_password(password: str) -> str:
    # Generate an 11-character alphanumeric string
    key = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(11))

    # Create a 32-byte-digest using the "Blake2b" hash algorithm
    # with the password as the input and the key as the key
    password_bytes = password.encode('utf-8')
    key_bytes = key.encode('utf-8')
    digest = hashlib.blake2b(password_bytes, key=key_bytes, digest_size=32).digest()

    # Base64URL encode the resulting 32 byte digest
    encoded_digest = base64.urlsafe_b64encode(digest).replace(b'=', b'').decode()

    # Create a string with the salt and the Base64URL encoded digest separated by a ":"
    hashed_password = key + ':' + encoded_digest

    return hashed_password


async def run_parallel_nofail(*tasks):
    """Run tasks in parallel, ignoring any failures."""
    await asyncio.gather(*tasks, return_exceptions=True)


def safe_set_result(fut: asyncio.Future, payload: dict) -> None:
    if not fut.done():
        fut.set_result(payload)


def evaluate(value: str | int | float | bool | list | dict, **kwargs) -> str | int | float | bool | list | dict:
    """
    Evaluate the given value, replacing placeholders with keyword arguments if necessary.

    :param value: The value to evaluate. Can be a string, integer, float, or boolean.
    :param kwargs: Additional keyword arguments to replace placeholders in the value.
    :return: The evaluated value. Returns the input value if it is not a string or if it does not start with '$'.
             If the input value is a string starting with '$', it will be evaluated with placeholders replaced by keyword arguments.
    """
    def _evaluate(value, **kwargs):
        if isinstance(value, (int, float, bool)) or not value.startswith('$'):
            return value
        value = format_string(value[1:], **kwargs)
        namespace = {k: v for k, v in globals().items() if not k.startswith("__")}
        try:
            return eval(value, namespace, kwargs) if value else False
        except Exception:
            logger.error(f"Error evaluating: {value} using kwargs={repr(kwargs)}")
            raise

    if isinstance(value, list):
        for i in range(len(value)):
            value[i] = _evaluate(value[i], **kwargs)
        return value
    elif isinstance(value, dict):
        return {_evaluate(k, **kwargs): evaluate(v, **kwargs) for k, v in value.items()}
    else:
        return _evaluate(value, **kwargs)


def for_each(data: dict, search: list[str], depth: int | None = 0, *,
             debug: bool | None = False, **kwargs) -> Generator[dict | None]:
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
    def process_iteration(_next, data, search, depth, debug, **kwargs):
        if isinstance(data, list):
            for value in data:
                yield from for_each(value, search, depth + 1, debug=debug, **kwargs)
        elif isinstance(data, dict):
            for value in data.values():
                yield from for_each(value, search, depth + 1, debug=debug, **kwargs)

    def process_indexing(_next, data, search, depth, debug, **kwargs):
        if isinstance(data, list):
            indexes = [int(x.strip()) for x in _next[1:-1].split(',')]
            for index in indexes:
                if index <= 0 or len(data) < index:
                    if debug:
                        logger.debug("  " * depth + f"|_ {index}. element not found")
                    yield None
                if debug:
                    logger.debug("  " * depth + f"|_ Selecting {index}. element")
                yield from for_each(data[index - 1], search, depth + 1, debug=debug, **kwargs)
        elif isinstance(data, dict):
            indexes = [x.strip() for x in _next[1:-1].split(',')]
            for index in indexes:
                if index not in data:
                    if debug:
                        logger.debug("  " * depth + f"|_ {index}. element not found")
                    yield None
                if debug:
                    logger.debug("  " * depth + f"|_ Selecting element {index}")
                yield from for_each(data[index], search, depth + 1, debug=debug, **kwargs)

    def process_pattern(_next, data, search, depth, debug, **kwargs):
        if isinstance(data, list):
            for idx, value in enumerate(data):
                if evaluate(_next, **(kwargs | value)):
                    if debug:
                        logger.debug("  " * depth + f"  - Element {idx + 1} matches.")
                    yield from for_each(value, search, depth + 1, debug=debug, **kwargs)
        elif isinstance(data, dict):
            if any(x for x in data.keys() if isinstance(x, int)):
                for idx, value in data.items():
                    if evaluate(_next, **(kwargs | value)):
                        if debug:
                            logger.debug("  " * depth + f"  - Element {idx} matches.")
                        yield from for_each(value, search, depth + 1, debug=debug, **kwargs)
            elif evaluate(_next, **(kwargs | data)):
                if debug:
                    logger.debug("  " * depth + f"  - Element {format_string(_next[1:], **kwargs)} matches.")
                yield from for_each(data, search, depth + 1, debug=debug, **kwargs)

    if not data or len(search) == depth:
        if len(search) == depth:
            if debug:
                logger.debug("  " * depth + "|_ RESULT found => Processing ...")
            yield data
        else:
            logger.debug("  " * depth +  "|_ NO result found, skipping.")
            yield None
    else:
        _next = search[depth]
        if _next == '*':
            if debug:
                logger.debug("  " * depth + f"|_ Iterating over {len(data)} {search[depth - 1]} elements")
            yield from process_iteration(_next, data, search, depth, debug, **kwargs)
        elif _next.startswith('['):
            yield from process_indexing(_next, data, search, depth, debug, **kwargs)
        elif _next.startswith('$'):
            if debug:
                pattern = format_string(_next[1:], **kwargs)
                logger.debug("  " * depth + f"|_ Searching pattern {pattern} on {len(data)} {search[depth - 1]} elements")
            yield from process_pattern(_next, data, search, depth, debug, **kwargs)
        elif _next in data:
            if debug:
                logger.debug("  " * depth + f"|_ {_next} found.")
            yield from for_each(data.get(_next), search, depth + 1, debug=debug, **kwargs)
        else:
            if debug:
                logger.debug("  " * depth + f"|_ {_next} not found.")
            yield None


class YAMLError(Exception):
    """

    The `YAMLError` class is an exception class raised when there is an error encountered while parsing or scanning a YAML file.

    **Methods:**

    """
    def __init__(self, file: str, ex: MarkedYAMLError | ValueError | SchemaError):
        super().__init__(f"Error in {file}, " + ex.__str__().replace('"<unicode string>"', file))


class DictWrapper:
    """A wrapper for dictionaries enabling both attribute and key-based access."""

    def __init__(self, data):
        """Initialize with a dictionary or a list."""
        if isinstance(data, dict):
            self._data = {k: self._wrap(v) for k, v in data.items()}
        elif isinstance(data, list):
            self._data = [self._wrap(v) for v in data]
        else:
            self._data = data  # Handle non-dict types (e.g., primitive values)

    @staticmethod
    def _wrap(value):
        """Wrap nested dictionaries or lists inside DictWrapper."""
        if isinstance(value, dict):
            return DictWrapper(value)
        elif isinstance(value, list):
            return [DictWrapper._wrap(v) for v in value]
        return value

    def __getattr__(self, name):
        """Access dictionary keys as attributes."""
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"Attribute '{name}' not found")

    def __setattr__(self, name, value):
        """Set dictionary keys as attributes."""
        if name == "_data":
            super().__setattr__(name, value)
        else:
            self._data[name] = self._wrap(value)

    def __delattr__(self, name):
        """Delete keys like attributes."""
        try:
            del self._data[name]
        except KeyError:
            raise AttributeError(f"Attribute '{name}' not found")

    def __getitem__(self, key):
        """Support list-style or dictionary-style key/item access."""
        return self._data[key]

    def __setitem__(self, key, value):
        """Set items using key."""
        self._data[key] = self._wrap(value)

    def __delitem__(self, key):
        """Support item deletion."""
        del self._data[key]

    def __iter__(self):
        """Allow iteration."""
        return iter(self._data)

    def __repr__(self):
        """Pretty representation."""
        return repr(self._data)

    def to_dict(self):
        def _unwrap_list(value):
            if isinstance(value, list):
                return [(v.to_dict() if isinstance(v, DictWrapper) else _unwrap_list(v)) for v in value]
            return value

        if isinstance(self._data, dict):
            return {
                k: (v.to_dict() if isinstance(v, DictWrapper) else _unwrap_list(v)) for k, v in self._data.items()
            }
        elif isinstance(self._data, list):
            return _unwrap_list(self._data)
        return self._data

    def clone(self):
        """Deeply clone the DictWrapper object."""
        return DictWrapper(deepcopy(self.to_dict()))


def format_dict_pretty(d: dict) -> str:
    """Convert dictionary to pretty-printed JSON string with indentation."""

    def default_serializer(obj):
        return str(obj)

    # Convert to string keys and sort manually
    items = sorted(d.items(), key=lambda x: str(x[0]))
    sorted_dict = dict(items)

    return json.dumps(sorted_dict, indent=4, sort_keys=False, default=default_serializer)


def show_dict_diff(old_dict: dict[str, Any], new_dict: dict[str, Any], context_lines: int = 3) -> str:
    """
    Generate a Discord-friendly diff between two dictionaries with context lines.

    Args:
        old_dict: Original dictionary
        new_dict: Modified dictionary
        context_lines: Number of context lines to show before and after changes

    Returns:
        String formatted for Discord with diff syntax highlighting
    """
    # Convert both dictionaries to a pretty-printed format
    old_str = format_dict_pretty(old_dict).splitlines()
    new_str = format_dict_pretty(new_dict).splitlines()

    # Generate a unified diff with specified context
    diff = list(unified_diff(old_str, new_str, lineterm='', n=context_lines))

    # Build the formatted string for Discord
    result = ["```diff"]
    for line in diff:
        # Skip the header lines that show file names
        if line.startswith('---') or line.startswith('+++'):
            continue
        result.append(line)
    result.append("```")

    return '\n'.join(result)


def to_valid_pyfunc_name(raw_name: str) -> str:
    """
    Convert an arbitrary name (e.g. 'test-1') into a legal Python identifier.

    Rules applied (in order):

    1. Replace every character that is **not** `[A-Za-z0-9_]` with an underscore.
    2. If the resulting string starts with a digit, prepend an underscore.
    3. If the result is a Python keyword (`def`, `class`, …), prefix it with an underscore as well.

    The function returns the sanitized name; the original name is kept unchanged.
    """
    # 1️⃣  Replace everything that is not a word character
    cleaned = re.sub(r'\W', '_', raw_name)

    # 2️⃣  If it starts with a digit, add a leading underscore
    if re.match(r'^\d', cleaned):
        cleaned = '_' + cleaned

    # 3️⃣  Avoid Python keywords
    if keyword.iskeyword(cleaned):
        cleaned = '_' + cleaned

    return cleaned


# -------------------------------------------------------------
# 1️⃣  Unit → seconds mapping (only integer units allowed)
# -------------------------------------------------------------
_UNIT_TO_SECONDS = {
    # days
    "day": 24 * 3600,
    "days": 24 * 3600,
    "d": 24 * 3600,
    # weeks
    "week": 7 * 24 * 3600,
    "weeks": 7 * 24 * 3600,
    "w": 7 * 24 * 3600,
    # months → 30 days (approx)
    "month": 30 * 24 * 3600,
    "months": 30 * 24 * 3600,
    "mon": 30 * 24 * 3600,
    # years → 365 days (approx)
    "year": 365 * 24 * 3600,
    "years": 365 * 24 * 3600,
    # hours
    "hour": 3600,
    "hours": 3600,
    "h": 3600,
    "hrs": 3600,
    # minutes
    "minute": 60,
    "minutes": 60,
    "min": 60,
    "mins": 60,
    # seconds (no fractions allowed)
    "second": 1,
    "seconds": 1,
    "sec": 1,
    "secs": 1,
}

# -------------------------------------------------------------
# 2️⃣  Regex: captures "number + unit" pairs
# -------------------------------------------------------------
#   - number must be an integer (optional sign, no decimal point)
#   - unit must be one of the keys in _UNIT_TO_SECONDS
_INTERVAL_RE = re.compile(r'([+-]?\d+)\s*(\w+)', re.IGNORECASE)


def _is_valid_unit(unit: str) -> bool:
    return unit.lower() in _UNIT_TO_SECONDS


def pg_interval_to_seconds(interval: str) -> int:
    """
    Convert a PostgreSQL‑style interval literal to whole seconds.

    Rules
    -----
    * Only integer values are accepted for all units.
      If a value contains a decimal point, a ValueError is raised.
    * Units below “seconds” (milliseconds, microseconds, …) are rejected.
    * The function returns an *integer* number of seconds (no fractional part).

    Parameters
    ----------
    interval : str
        Example: "1 day 2 hours", "3 weeks", "-2 days 15 minutes 30 seconds"

    Returns
    -------
    int
        Total number of seconds.

    Raises
    ------
    ValueError
        If the string contains a fractional value, an unknown unit, or
        a sub‑second unit.
    """
    total_seconds = 0

    # Scan the string for all "number unit" pairs
    for num_str, unit in _INTERVAL_RE.findall(interval):
        # 1️⃣  Unit check
        if not _is_valid_unit(unit):
            raise ValueError(f"Unsupported or sub‑second unit: '{unit}'")

        # 2️⃣  Value check – must be an integer
        #      (num_str comes from the regex that only accepts plain integers)
        #      The regex already guarantees no decimal point,
        #      so we can safely convert to int.
        try:
            value = int(num_str)
        except ValueError:  # pragma: no cover – defensive
            raise ValueError(f"Invalid number: {num_str!r}")

        # 3️⃣  Convert to seconds and accumulate
        total_seconds += value * _UNIT_TO_SECONDS[unit.lower()]

    return total_seconds
