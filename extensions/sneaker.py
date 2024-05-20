from __future__ import annotations

import asyncio
import atexit
import json
import os
import psutil
import subprocess

from core import Extension, Status, ServiceRegistry, Server, utils
from services import ServiceBus
from threading import Thread
from typing import Optional

process: Optional[psutil.Process] = None
servers: set[str] = set()
lock = asyncio.Lock()


class Sneaker(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.bus = ServiceRegistry.get(ServiceBus)

    def create_config(self):
        cfg = {"servers": []}
        filename = os.path.join(self.node.config_dir, 'sneaker.json')
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

    def _log_output(self, p: subprocess.Popen):
        for line in iter(p.stdout.readline, b''):
            self.log.info(line.decode('utf-8').rstrip())

    def _run_subprocess(self, config: str):
        cmd = os.path.basename(self.config['cmd'])
        out = subprocess.PIPE if self.config.get('debug', False) else subprocess.DEVNULL
        self.log.debug(f"Launching Sneaker server with {cmd} --bind {self.config['bind']} "
                       f"--config {config}")
        p = subprocess.Popen([cmd, "--bind", self.config['bind'], "--config", config],
                                executable=os.path.expandvars(self.config['cmd']),
                                stdout=out, stderr=subprocess.STDOUT)
        if self.config.get('debug', False):
            Thread(target=self._log_output, args=(p,)).start()
        return p

    async def startup(self) -> bool:
        global process, servers, lock

        await super().startup()
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Sneaker needs Tacview to be enabled in your server!')
            return False
        try:
            async with lock:
                if 'config' not in self.config:
                    # we need to lock here, to avoid race conditions on parallel server startups
                    await asyncio.to_thread(utils.terminate_process, process)
                    self.create_config()
                    p = await asyncio.to_thread(self._run_subprocess,
                                                os.path.join(self.node.config_dir, 'sneaker.json'))
                    process = psutil.Process(p.pid)
                elif not process or not process.is_running():
                    p = await asyncio.to_thread(self._run_subprocess, os.path.expandvars(self.config['config']))
                    process = psutil.Process(p.pid)
                    atexit.register(self.shutdown)
            servers.add(self.server.name)
            return True
        except Exception as ex:
            self.log.error(f"Error during launch of {self.config['cmd']}: {str(ex)}")
            return False

    def shutdown(self) -> bool:
        global process, servers

        def terminate() -> bool:
            try:
                utils.terminate_process(process)
                return True
            except Exception as ex:
                self.log.error(f"Error during shutdown of {self.config['cmd']}: {str(ex)}")
                return False

        servers.remove(self.server.name)
        if not servers:
            super().shutdown()
            return terminate()
        elif 'config' not in self.config:
            if terminate():
                self.create_config()
                cmd = os.path.basename(self.config['cmd'])
                self.log.debug(f"Launching Sneaker server with {cmd} --bind {self.config['bind']} "
                               f"--config config/sneaker.json")
                try:
                    p = self._run_subprocess(os.path.join(self.node.config_dir, 'sneaker.json'))
                    process = psutil.Process(p.pid)
                except Exception as ex:
                    self.log.error(f"Error during launch of {self.config['cmd']}: {str(ex)}")
                    return False
            else:
                return False
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
