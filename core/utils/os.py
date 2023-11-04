import os
import aiohttp
import ipaddress
import psutil
import socket
from contextlib import closing, suppress

API_URLS = [
    'https://api4.my-ip.io/ip',
    'https://api4.ipify.org/'
]

__all__ = [
    "is_open",
    "get_public_ip",
    "find_process"
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
        with suppress(Exception):
            if os.path.basename(p.info['cmdline'][0]).casefold() == proc.casefold():
                for c in p.info['cmdline']:
                    if instance in c.replace('\\', '/').split('/'):
                        return p
    return None
