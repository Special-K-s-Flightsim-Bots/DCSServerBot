import importlib
import string
from datetime import datetime, timedelta
from typing import Optional


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
        return string.capwords(period) + 'ly'
