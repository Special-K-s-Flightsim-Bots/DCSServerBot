from logging.handlers import RotatingFileHandler

import aiohttp
import ipaddress
import os
import psutil
import socket
import stat
import subprocess
import sys
if sys.platform == 'win32':
    import pywintypes
    import win32api
    import win32console

from contextlib import closing, suppress
from pathlib import Path
from typing import Optional, Union

API_URLS = [
    'https://api4.my-ip.io/ip',
    'https://api4.ipify.org/'
]

ENABLE_QUICK_EDIT_MODE = 0x40
ENABLE_EXTENDED_FLAGS = 0x80

__all__ = [
    "is_open",
    "get_public_ip",
    "find_process",
    "is_process_running",
    "get_windows_version",
    "safe_rmtree",
    "terminate_process",
    "quick_edit_mode",
    "CloudRotatingFileHandler"
]


def is_open(ip, port):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(3)
        return s.connect_ex((ip, int(port))) == 0


async def get_public_ip():
    for url in API_URLS:
        with suppress(aiohttp.ClientError, ValueError):
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    return ipaddress.ip_address(await resp.text()).compressed


def find_process(proc: str, instance: Optional[str] = None):
    for p in psutil.process_iter(['cmdline']):
        try:
            if os.path.basename(p.info['cmdline'][0]).casefold() in [proc.casefold() for proc in proc.split("|")]:
                if instance:
                    for c in p.info['cmdline']:
                        if instance in c.replace('\\', '/').split('/'):
                            return p
                else:
                    return p
        except Exception:
            continue
    return None


def is_process_running(process: Union[subprocess.Popen, psutil.Process]):
    if isinstance(process, subprocess.Popen):
        return process.poll() is None
    elif isinstance(process, psutil.Process):
        return process.is_running()


MS_LSB_MULTIPLIER = 65536


def get_windows_version(cmd: str) -> Optional[str]:
    if sys.platform != 'win32':
        return None
    try:
        info = win32api.GetFileVersionInfo(os.path.expandvars(cmd), '\\')
        version = "%d.%d.%d.%d" % (info['FileVersionMS'] / MS_LSB_MULTIPLIER,
                                   info['FileVersionMS'] % MS_LSB_MULTIPLIER,
                                   info['FileVersionLS'] / MS_LSB_MULTIPLIER,
                                   info['FileVersionLS'] % MS_LSB_MULTIPLIER)
    except pywintypes.error:
        version = None
    return version


def safe_rmtree(path: Union[str, Path]):
    # if path is a single file, delete that
    if os.path.isfile(path):
        os.chmod(path, stat.S_IWUSR)
        os.remove(path)
        return
    # otherwise delete the tree
    elif os.path.isdir(path):
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                filename = os.path.join(root, name)
                os.chmod(filename, stat.S_IWUSR)
                os.remove(filename)
            for name in dirs:
                dirname = os.path.join(root, name)
                os.chmod(dirname, stat.S_IWUSR)
                os.rmdir(dirname)
        os.chmod(path, stat.S_IWUSR)
        os.rmdir(path)


def terminate_process(process: Optional[psutil.Process]):
    if process is not None and process.is_running():
        process.terminate()
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def quick_edit_mode(turn_on=None):
    """ Enable/Disable windows console Quick Edit Mode """
    screen_buffer = win32console.GetStdHandle(-10)
    orig_mode = screen_buffer.GetConsoleMode()
    is_on = (orig_mode & ENABLE_QUICK_EDIT_MODE)
    if is_on != turn_on and turn_on is not None:
        if turn_on:
            new_mode = orig_mode | ENABLE_QUICK_EDIT_MODE
        else:
            new_mode = orig_mode & ~ENABLE_QUICK_EDIT_MODE
        screen_buffer.SetConsoleMode(new_mode | ENABLE_EXTENDED_FLAGS)

    return is_on if turn_on is None else turn_on


class CloudRotatingFileHandler(RotatingFileHandler):
    def shouldRollover(self, record):
        """
        Determine if rollover should occur by comparing the log file size to
        the size specified when the handler was created.
        """
        if self.maxBytes > 0:  # are we rolling over?
            log_file_size = os.path.getsize(self.baseFilename)
            if log_file_size >= self.maxBytes:
                return 1
        return 0
