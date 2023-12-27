from __future__ import annotations
import json
import os
import subprocess

from typing import Optional, cast
from core import Extension, Status, ServiceRegistry, Server, utils
from services import ServiceBus

process: Optional[subprocess.Popen] = None
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
        with open(filename, 'w') as file:
            json.dump(cfg, file, indent=2)

    async def startup(self) -> bool:
        global process, servers

        await super().startup()
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Sneaker needs Tacview to be enabled in your server!')
            return False
        if 'config' not in self.config:
            out = subprocess.DEVNULL if not self.config.get('debug', False) else None
            if process and process.returncode is None:
                process.kill()
            self.create_config()
            cmd = os.path.basename(self.config['cmd'])
            self.log.debug(
                f"Launching Sneaker server with {cmd} --bind {self.config['bind']} --config config/sneaker.json")
            process = subprocess.Popen([
                cmd, "--bind", self.config['bind'],
                "--config", os.path.join('config', 'sneaker.json')
            ], executable=os.path.expandvars(self.config['cmd']), stdout=out, stderr=out)
        else:
            if not process:
                cmd = os.path.basename(self.config['cmd'])
                self.log.debug(f"Launching Sneaker server with {cmd} --bind {self.config['bind']} "
                               f"--config {self.config['config']}")
                process = subprocess.Popen([cmd, "--bind", self.config['bind'], "--config",
                                            os.path.expandvars(self.config['config'])],
                                           executable=os.path.expandvars(self.config['cmd']),
                                           stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
        servers.add(self.server.name)
        return self.is_running()

    async def shutdown(self) -> bool:
        global process, servers

        servers.remove(self.server.name)
        if not servers and process is not None:
            process.kill()
            process = None
            return await super().shutdown()
        elif 'config' not in self.config:
            if process and process.returncode is None:
                process.kill()
            self.create_config()
            cmd = os.path.basename(self.config['cmd'])
            self.log.debug(f"Launching Sneaker server with {cmd} --bind {self.config['bind']} "
                           f"--config config/sneaker.json")
            process = subprocess.Popen([
                cmd,
                "--bind", self.config['bind'],
                "--config", os.path.join('config', 'sneaker.json')
            ], executable=os.path.expandvars(self.config['cmd']), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True

    def is_running(self) -> bool:
        global process, servers

        if process is not None and process.poll() is None:
            return self.server.name in servers
        else:
            process = None
            return False

    @property
    def version(self) -> Optional[str]:
        return utils.get_windows_version(self.config['cmd'])

    def is_installed(self) -> bool:
        # check if Sneaker is enabled
        if not self.config.get('enabled', True):
            return False
        # check if Sneaker is installed
        if 'cmd' not in self.config or not os.path.exists(os.path.expandvars(self.config['cmd'])):
            self.log.warning("Sneaker executable not found!")
            return False
        return True

    async def render(self, param: Optional[dict] = None) -> dict:
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = 'enabled'
        return {
            "name": "Sneaker",
            "version": self.version or 'n/a',
            "value": value
        }
