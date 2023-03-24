from __future__ import annotations
import importlib
import json
import luadata
import os
import re
import string
from datetime import datetime, timedelta
from typing import Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server


def is_in_timeframe(time: datetime, timeframe: str) -> bool:
    def parse_time(time_str: str) -> datetime:
        fmt, time_str = ('%H:%M', time_str.replace('24:', '00:')) \
            if time_str.find(':') > -1 else ('%H', time_str.replace('24', '00'))
        return datetime.strptime(time_str, fmt)

    pos = timeframe.find('-')
    if pos != -1:
        start_time = parse_time(timeframe[:pos])
        end_time = parse_time(timeframe[pos+1:])
        if end_time <= start_time:
            end_time += timedelta(days=1)
    else:
        start_time = end_time = parse_time(timeframe)
    check_time = time.replace(year=start_time.year, month=start_time.month, day=start_time.day, second=0, microsecond=0)
    return start_time <= check_time <= end_time


def str_to_class(name):
    try:
        module_name, class_name = name.rsplit('.', 1)
        return getattr(importlib.import_module(module_name), class_name)
    except AttributeError:
        return None


def format_string(string_: str, default_: Optional[str] = None, **kwargs) -> str:
    class NoneFormatter(string.Formatter):
        def format_field(self, value, spec):
            if value is None:
                spec = ''
                if default_:
                    value = default_
                else:
                    value = ""
            elif isinstance(value, list):
                value = '\n'.join(value)
            elif isinstance(value, dict):
                value = json.dumps(value)
            return super().format_field(value, spec)
    try:
        string_ = NoneFormatter().format(string_, **kwargs)
    except KeyError:
        string_ = ""
    return string_


def convert_time(seconds: int):
    retval = ""
    days = int(seconds / 86400)
    if days != 0:
        retval += f"{days}d"
    seconds = seconds - days * 86400
    hours = int(seconds / 3600)
    if hours != 0:
        if len(retval):
            retval += ":"
        retval += f"{hours:02d}h"
    seconds = seconds - hours * 3600
    minutes = int(seconds / 60)
    if len(retval):
        retval += ":"
    retval += f"{minutes:02d}m"
    return retval


def format_time(seconds: int):
    retval = ""
    days = int(seconds / 86400)
    if days != 0:
        retval += f"{days} day"
        if days > 1:
            retval += "s"
        seconds -= days * 86400
    hours = int(seconds / 3600)
    if hours != 0:
        if len(retval):
            retval += " "
        retval += f"{hours} hour"
        if hours > 1:
            retval += "s"
        seconds -= hours * 3600
    minutes = int(seconds / 60)
    if minutes != 0:
        if len(retval):
            retval += " "
        retval += f"{minutes} minute"
        if minutes > 1:
            retval += "s"
        seconds -= minutes * 60
    if seconds != 0:
        if len(retval):
            retval += " "
        retval += f"{seconds} second"
        if seconds > 1:
            retval += "s"
    return retval


def format_period(period: str) -> str:
    if period == 'day':
        return 'Daily'
    else:
        return period.capitalize() + 'ly'


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

    exp1 = re.compile('cfg\["(?P<key>.*)"\] = (?P<value>.*)')
    exp2 = re.compile('cfg\["(?P<key1>.*)"\]\[(?P<key2>.*)\] = (?P<value>.*)')

    settings = dict()
    with open(path, encoding='utf-8') as infile:
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


class SettingsDict(dict):
    def __init__(self, server: Server, path: str, root: str):
        super().__init__()
        self.path = path
        self.root = root
        self.mtime = 0
        self.server = server
        self.bot = server.bot
        self.log = server.log
        self.read_file()

    def read_file(self):
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
        elif self.path.lower().endswith('.json'):
            with open(self.path, encoding='utf-8') as file:
                data = json.load(file)
        if data:
            self.clear()
            self.update(data)

    def write_file(self):
        if self.path.lower().endswith('.lua'):
            with open(self.path, 'wb') as outfile:
                self.mtime = os.path.getmtime(self.path)
                outfile.write((f"{self.root} = " + luadata.serialize(self, indent='\t', indent_level=0)).encode('utf-8'))
        elif self.path.lower().endswith('.json'):
            with open(self.path, "w", encoding='utf-8') as outfile:
                json.dump(self, outfile)

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
