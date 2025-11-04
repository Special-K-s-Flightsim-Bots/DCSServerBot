from __future__ import annotations

import asyncio
import atexit
import json
import os
import psutil
import subprocess

from core import Extension, Status, ServiceRegistry, Server, utils, get_translation, PortType, Port
from services.servicebus import ServiceBus
from threading import Thread
from typing_extensions import override

_ = get_translation(__name__.split('.')[1])

__all__ = [
    "Sneaker"
]


class Sneaker(Extension):
    _process: psutil.Process | None = None
    _servers: set[str] = set()
    _lock = asyncio.Lock()

    CONFIG_DICT = {
        "bind": {
            "type": str,
            "label": _("Bind Address"),
            "placeholder": "ip:port",
            "required": True
        },
        "url": {
            "type": str,
            "label": _("URL")
        }
    }

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.bus = ServiceRegistry.get(ServiceBus)
        if self.enabled and (not type(self)._process or not type(self)._process.is_running()):
            cmd = self.config.get('cmd')
            if not cmd:
                return
            type(self)._process = next(utils.find_process(os.path.basename(cmd), self.config['bind']), None)
            if type(self)._process:
                self.log.debug("- Running Sneaker process found.")

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
                if y.name == self.server.name or y.status not in [Status.UNREGISTERED, Status.SHUTDOWN]
            ]
        ]
        with open(filename, mode='w', encoding='utf-8') as file:
            json.dump(cfg, file, indent=2)
        self.log.debug(f"Created / updated Sneaker config file: {filename}")

    def _log_output(self, p: subprocess.Popen):
        for line in iter(p.stdout.readline, b''):
            self.log.debug(line.decode('utf-8').rstrip())

    def _run_subprocess(self, config: str):
        cmd = os.path.basename(self.config['cmd'])
        out = subprocess.PIPE if self.config.get('debug', False) else subprocess.DEVNULL
        self.log.debug(f"Launching Sneaker server with {cmd} --bind {self.config['bind']} "
                       f"--config {config}")
        p = subprocess.Popen([cmd, "--bind", self.config['bind'], "--config", config],
                             executable=os.path.expandvars(self.config['cmd']),
                             stdout=out, stderr=subprocess.STDOUT)
        if self.config.get('debug', False):
            Thread(target=self._log_output, args=(p,), daemon=True).start()
        return p

    @override
    async def startup(self, *, quiet: bool = False) -> bool:
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Sneaker needs Tacview to be enabled in your server!')
            return False
        try:
            async with type(self)._lock:
                if 'config' not in self.config:
                    # we need to lock here to avoid race conditions on parallel server startups
                    await asyncio.to_thread(utils.terminate_process, type(self)._process)
                    self.create_config()
                    p = await asyncio.to_thread(self._run_subprocess,
                                                os.path.join(self.node.config_dir, 'sneaker.json'))
                    type(self)._process = psutil.Process(p.pid)
                elif not type(self)._process or not type(self)._process.is_running():
                    p = await asyncio.to_thread(self._run_subprocess, os.path.expandvars(self.config['config']))
                    type(self)._process = psutil.Process(p.pid)
                    atexit.register(self.terminate)
            type(self)._servers.add(self.server.name)
            return await super().startup()
        except Exception as ex:
            self.log.error(f"Error during launch of {self.config['cmd']}: {str(ex)}")
            return False

    def terminate(self) -> bool:
        try:
            if type(self)._process:
                utils.terminate_process(type(self)._process)
                type(self)._process = None
            return True
        except Exception as ex:
            self.log.error(f"Error during shutdown of {self.config['cmd']}: {str(ex)}")
            return False

    @override
    def shutdown(self, *, quiet: bool = False) -> bool:
        try:
            type(self)._servers.remove(self.server.name)
            if not type(self)._servers:
                super().shutdown()
                return self.terminate()
            elif 'config' not in self.config:
                if self.terminate():
                    self.create_config()
                    try:
                        p = self._run_subprocess(os.path.join(self.node.config_dir, 'sneaker.json'))
                        type(self)._process = psutil.Process(p.pid)
                    except Exception as ex:
                        self.log.error(f"Error during launch of {self.config['cmd']}: {str(ex)}")
                        return False
                else:
                    return False
            super().shutdown(quiet=True)
            return True
        except Exception as ex:
            self.log.exception(ex)
            return False

    @override
    def is_running(self) -> bool:
        return type(self)._process and type(self)._process.is_running() and self.server.name in type(self)._servers

    @override
    @property
    def version(self) -> str | None:
        return utils.get_windows_version(self.config['cmd'])

    @override
    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        # check if Sneaker is installed
        if 'cmd' not in self.config or not os.path.exists(os.path.expandvars(self.config['cmd'])):
            self.log.warning("  => Sneaker: can't run extension, executable not found!")
            return False
        return True

    @override
    async def render(self, param: dict | None = None) -> dict:
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = 'enabled'
        return {
            "name": self.name,
            "version": self.version or 'n/a',
            "value": value
        }

    @override
    def get_ports(self) -> dict[str, Port]:
        return {
            "Sneaker": Port(self.config['bind'].split(':')[1], PortType.TCP, public=True)
        } if self.enabled else {}
