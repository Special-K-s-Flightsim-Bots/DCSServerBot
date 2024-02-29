from __future__ import annotations

import asyncio
import atexit
import json
import os
import psutil
import subprocess

from contextlib import suppress
from core import Extension, Status, ServiceRegistry, Server, utils
from typing import Optional

process: Optional[psutil.Process] = None
servers: set[str] = set()


class Sneaker(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.bus = ServiceRegistry.get("ServiceBus")

    def create_config(self):
        cfg = {"servers": []}
        filename = os.path.join('config', 'sneaker.json')
        if os.path.exists(filename):
            with open(filename, mode='r', encoding='utf-8') as file:
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
        with open(filename, mode='w', encoding='utf-8') as file:
            json.dump(cfg, file, indent=2)

    def _run_subprocess(self, config: str):
        cmd = os.path.basename(self.config['cmd'])
        out = subprocess.DEVNULL if not self.config.get('debug', False) else None
        self.log.debug(f"Launching Sneaker server with {cmd} --bind {self.config['bind']} "
                       f"--config {config}")
        return subprocess.Popen([
            cmd, "--bind", self.config['bind'], "--config", config
        ], executable=os.path.expandvars(self.config['cmd']), stdout=out, stderr=out)

    def _terminate_process(self):
        global process

        if process is not None and process.is_running():
            process.terminate()
            if process.is_running():
                with suppress(psutil.NoSuchProcess):
                    process.kill()
        process = None

    async def startup(self) -> bool:
        global process, servers

        await super().startup()
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Sneaker needs Tacview to be enabled in your server!')
            return False
        if 'config' not in self.config:
            self._terminate_process()
            self.create_config()
            p = await asyncio.to_thread(self._run_subprocess, os.path.join('config', 'sneaker.json'))
            process = psutil.Process(p.pid)
        elif not process or not process.is_running():
            p = await asyncio.to_thread(self._run_subprocess, os.path.expandvars(self.config['config']))
            process = psutil.Process(p.pid)
            atexit.register(self.shutdown)
        servers.add(self.server.name)
        return self.is_running()

    def shutdown(self) -> bool:
        global process, servers

        servers.remove(self.server.name)
        if not servers:
            super().shutdown()
            self._terminate_process()
        elif 'config' not in self.config:
            self._terminate_process()
            self.create_config()
            cmd = os.path.basename(self.config['cmd'])
            self.log.debug(f"Launching Sneaker server with {cmd} --bind {self.config['bind']} "
                           f"--config config/sneaker.json")
            p = self._run_subprocess(os.path.join('config', 'sneaker.json'))
            process = psutil.Process(p.pid)
        return True

    def is_running(self) -> bool:
        global process, servers

        if not process or not process.is_running():
            cmd = os.path.basename(self.config['cmd'])
            process = utils.find_process(cmd)
        return process is not None and self.server.name in servers

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
