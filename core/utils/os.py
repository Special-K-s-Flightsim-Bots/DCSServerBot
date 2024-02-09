import os
from pathlib import Path

import aiohttp
import ipaddress
import psutil
import socket
import stat
import sys
if sys.platform == 'win32':
    import pywintypes
    import win32api

from contextlib import closing, suppress
from typing import Optional, Union

API_URLS = [
    'https://api4.my-ip.io/ip',
    'https://api4.ipify.org/'
]

__all__ = [
    "is_open",
    "get_public_ip",
    "find_process",
    "get_windows_version",
    "safe_rmtree"
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


def find_process(proc: str, instance: str):
    for p in psutil.process_iter(['cmdline']):
        try:
            if os.path.basename(p.info['cmdline'][0]).casefold() in [proc.casefold() for proc in proc.split("|")]:
                for c in p.info['cmdline']:
                    if instance in c.replace('\\', '/').split('/'):
                        return p
        except Exception:
            continue
    return None


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
