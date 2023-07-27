from __future__ import annotations
import asyncio
import json
import os

from typing import Optional, cast
from core import Extension, report, Status, ServiceRegistry, Server
from services import ServiceBus

process: Optional[asyncio.subprocess.Process] = None
servers: set[str] = set()


class Sneaker(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.bus = cast(ServiceBus, ServiceRegistry.get("ServiceBus"))

    def create_config(self):
        cfg = {"servers": []}
        filename = os.path.join('config', 'sneaker.json')
        if os.path.exists(filename):
            with open(filename) as file:
                cfg = json.load(file)
        for s in cfg['servers']:
            if s['name'] == self.server.name:
                s['port'] = int(self.server.options['plugins']['Tacview']['tacviewRealTimeTelemetryPort'])
                break
        else:
            cfg['servers'].append({
                "name": self.server.name,
                "hostname": self.node.listen_address,
                "port": int(self.server.options['plugins']['Tacview']['tacviewRealTimeTelemetryPort']),
                "radar_refresh_rate": 5,
                "enable_friendly_ground_units": True,
                "enable_enemy_ground_units": True
            })
        # filter out servers that are not running
        cfg['servers'] = [
            x for x in cfg['servers'] if x['name'] in [
                y.name for y in self.bus.servers.values()
                if y.status not in [Status.UNREGISTERED, Status.SHUTDOWN]
            ]
        ]
        with open('config\\sneaker.json', 'w') as file:
            json.dump(cfg, file, indent=2)

    async def startup(self) -> bool:
        global process, servers

        await super().startup()
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Sneaker needs Tacview to be enabled in your server!')
            return False
        if 'config' not in self.config:
            if process and process.returncode is None:
                process.kill()
            self.create_config()
            cmd = os.path.basename(self.config['cmd'])
            self.log.debug(
                f"Launching Sneaker server with {cmd} --bind {self.config['bind']} --config config\\sneaker.json")
            process = await asyncio.create_subprocess_exec(
                os.path.expandvars(self.config['cmd']),
                "--bind", self.config['bind'],
                "--config", 'config\\sneaker.json',
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
        else:
            if not process:
                cmd = os.path.basename(self.config['cmd'])
                self.log.debug(f"Launching Sneaker server with {cmd} --bind {self.config['bind']} "
                               f"--config {self.config['config']}")
                process = await asyncio.create_subprocess_exec(
                    os.path.expandvars(self.config['cmd']),
                    "--bind", self.config['bind'],
                    "--config", os.path.expandvars(self.config['config']),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL)
        servers.add(self.server.name)
        return self.is_running()

    async def shutdown(self) -> bool:
        global process, servers

        servers.remove(self.server.name)
        if not servers and process is not None and process.returncode is None:
            process.kill()
            process = None
            return await super().shutdown()
        elif 'config' not in self.config:
            if process and process.returncode is None:
                process.kill()
            self.create_config()
            cmd = os.path.basename(self.config['cmd'])
            self.log.debug(f"Launching Sneaker server with {cmd} --bind {self.config['bind']} "
                           f"--config config\\sneaker.json")
            process = await asyncio.create_subprocess_exec(
                os.path.expandvars(self.config['cmd']),
                "--bind", self.config['bind'],
                "--config", 'config\\sneaker.json',
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
        return True

    def is_running(self) -> bool:
        global process, servers

        if process is not None and process.returncode is None:
            return self.server.name in servers
        else:
            return False

    @property
    def version(self) -> str:
        return "0.0.12"

    def is_installed(self) -> bool:
        # check if Sneaker is enabled
        if 'enabled' not in self.config or not self.config['enabled']:
            return False
        # check if Sneaker is installed
        if 'cmd' not in self.config or not os.path.exists(os.path.expandvars(self.config['cmd'])):
            self.log.warning("Sneaker executable not found!")
            return False
        return True

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = 'enabled'
        embed.add_field(name='Sneaker', value=value)
