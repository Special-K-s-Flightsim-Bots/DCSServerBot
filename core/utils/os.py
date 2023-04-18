import aiohttp
import asyncio
import psutil
import socket
from contextlib import closing, suppress


def is_open(ip, port):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(3)
        return s.connect_ex((ip, int(port))) == 0


async def get_external_ip():
    for i in range(0, 3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api4.ipify.org/') as resp:
                    return await resp.text()
        except aiohttp.ClientError:
            await asyncio.sleep(1)


def find_process(proc, installation):
    for p in psutil.process_iter(['name', 'cmdline']):
        if p.info['name'] == proc:
            with suppress(Exception):
                for c in p.info['cmdline']:
                    if installation in c.replace('\\', '/').split('/'):
                        return p
    return None
