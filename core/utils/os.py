import os
import aiohttp
import asyncio
import ipaddress
import psutil
import socket
from contextlib import closing, suppress

# API_URL = 'https://api4.ipify.org/'
API_URL = 'https://api4.my-ip.io/ip'

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
    for i in range(0, 2):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(API_URL) as resp:
                    return ipaddress.ip_address(await resp.text()).compressed
        except (aiohttp.ClientError, ValueError):
            await asyncio.sleep(1)


def find_process(proc, instance: str):
    for p in psutil.process_iter(['cmdline']):
        with suppress(Exception):
            if os.path.basename(p.info['cmdline'][0]) == proc:
                for c in p.info['cmdline']:
                    if instance in c.replace('\\', '/').split('/'):
                        return p
    return None
