from __future__ import annotations

import aiohttp
import asyncio
import atexit
import certifi
import json
import os
import psutil
import ssl
import subprocess

from contextlib import suppress
from core import Status, Server, utils, get_translation, PortType, Port, ProcessManager, InstallableExtension, \
    ServerMaintenanceManager
from threading import Thread
from typing_extensions import override

_ = get_translation(__name__.split('.')[1])

__all__ = [
    "Sneaker"
]

SNEAKER_GITHUB_URL = "https://api.github.com/repos/Special-K-s-Flightsim-Bots/sneaker/releases/latest"
SNEAKER_DOWNLOAD_URL = "https://github.com/Special-K-s-Flightsim-Bots/sneaker/releases/download/v{version}"


class Sneaker(InstallableExtension):
    _process: psutil.Process | None = None
    _servers: set[str] = set()
    _lock = asyncio.Lock()

    NODE_CONFIG_DICT = {
        "cmd": {
            "type": str,
            "label": _("Command"),
            "placeholder": "Path to Sneaker executable",
            "required": True
        },
        "bind": {
            "type": str,
            "label": _("Bind Address"),
            "placeholder": "ip:port",
            "default": "0.0.0.0:8080"
        },
        "url": {
            "type": str,
            "label": _("URL"),
            "required": False
        }
    }

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
        if self.enabled and (not type(self)._process or not type(self)._process.is_running()):
            cmd = self.get_exe_path()
            if not cmd:
                return
            type(self)._process = next(utils.find_process(os.path.basename(cmd), self.config['bind']), None)
            if type(self)._process:
                # Old affinity
                affinity = self.config.get('affinity')
                if isinstance(affinity, str):
                    affinity = [int(x.strip()) for x in affinity.split(',')]
                elif isinstance(affinity, int):
                    affinity = [affinity]

                ProcessManager().assign_process(
                    type(self)._process,
                    affinity=affinity,
                    min_cores=self.config.get('auto_affinity', {}).get('min_cores', 1),
                    max_cores=self.config.get('auto_affinity', {}).get('max_cores', 1),
                    quality=self.config.get('auto_affinity', {}).get('quality', 1),
                    instance=server.instance.name
                )
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

    def get_inst_path(self) -> str:
        return os.path.dirname(os.path.expandvars(self.config['cmd'])) if 'cmd' in self.config else ''

    def get_exe_path(self) -> str | None:
        return os.path.expandvars(self.config['cmd']) if 'cmd' in self.config else None

    def _log_output(self, p: psutil.Popen):
        for line in iter(p.stdout.readline, b''):
            self.log.debug(line.decode('utf-8').rstrip())

    def _run_subprocess(self, config: str) -> psutil.Process:
        cmd = os.path.basename(self.get_exe_path())
        out = subprocess.PIPE if self.config.get('debug', False) else subprocess.DEVNULL
        self.log.debug(f"Launching Sneaker server with {cmd} --bind {self.config['bind']} "
                       f"--config {config}")
        # Old affinity
        affinity = self.config.get('affinity')
        if isinstance(affinity, str):
            affinity = [int(x.strip()) for x in affinity.split(',')]
        elif isinstance(affinity, int):
            affinity = [affinity]
        p = ProcessManager().launch_process(
            [cmd, "--bind", self.config['bind'], "--config", config],
            executable=os.path.expandvars(self.config['cmd']),
            affinity=affinity,
            min_cores=self.config.get('auto_affinity', {}).get('min_cores', 1),
            max_cores=self.config.get('auto_affinity', {}).get('max_cores', 1),
            quality=self.config.get('auto_affinity', {}).get('quality', 1),
            stdout=out,
            stderr=subprocess.STDOUT
        )
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
                    type(self)._process = await asyncio.to_thread(
                        self._run_subprocess,
                        os.path.join(self.node.config_dir, 'sneaker.json')
                    )
                elif not type(self)._process or not type(self)._process.is_running():
                    type(self)._process = await asyncio.to_thread(
                        self._run_subprocess, os.path.expandvars(self.config['config'])
                    )
                    atexit.register(self.terminate)
            type(self)._servers.add(self.server.name)
            return await super().startup()
        except Exception as ex:
            self.log.error(f"Error during launch of {self.get_exe_path()}: {str(ex)}")
            return False

    def terminate(self) -> bool:
        try:
            if type(self)._process:
                utils.terminate_process(type(self)._process)
                type(self)._process = None
            return True
        except Exception as ex:
            self.log.error(f"Error during shutdown of {self.get_exe_path()}: {str(ex)}")
            return False

    @override
    def shutdown(self, *, quiet: bool = False) -> bool:
        try:
            type(self)._servers.remove(self.server.name)
            if not type(self)._servers:
                if self.terminate():
                    return super().shutdown()
                return False
            elif 'config' not in self.config:
                if self.terminate():
                    self.create_config()
                    try:
                        type(self)._process = self._run_subprocess(os.path.join(self.node.config_dir, 'sneaker.json'))
                    except Exception as ex:
                        self.log.error(f"Error during launch of {self.get_exe_path()}: {str(ex)}")
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
        return utils.get_windows_version(self.get_exe_path())

    @override
    async def render(self, param: dict | None = None) -> dict:
        ret = await super().render(param)
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = 'enabled'
        return ret | {
            "value": value
        }

    @override
    def get_ports(self) -> dict[str, Port]:
        return {
            "Sneaker": Port(self.config['bind'].split(':')[1], PortType.TCP, public=True)
        } if self.enabled else {}

    @override
    def is_available(self) -> bool:
        return os.path.exists(self.get_exe_path())

    @override
    async def get_latest_version(self) -> str | None:
        with suppress(aiohttp.ClientError):
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(
                        SNEAKER_GITHUB_URL,
                        proxy=self.node.proxy,
                        proxy_auth=self.node.proxy_auth,
                        raise_for_status=True
                ) as response:
                    data = await response.json()
                    if isinstance(data, list):
                        data = data[0]
                    return data.get('tag_name', '').strip('v')
        return None

    @override
    def is_installed(self) -> bool:
        exe = self.get_exe_path()
        return exe is not None and os.path.exists(exe)

    async def do_install(self, version: str) -> bool:
        def stop_processes():
            for process in utils.find_process(os.path.basename(self.get_exe_path())):
                if process and process.is_running():
                    process.terminate()

        try:
            async with ServerMaintenanceManager(self.node, shutdown=False):
                # stop any existing Sneaker process
                if self.is_installed():
                    await asyncio.to_thread(stop_processes)

                download_url = SNEAKER_DOWNLOAD_URL.format(version=version) + '/sneaker-windows-amd64-v{}.exe'.format(version)
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                            download_url,
                            proxy=self.node.proxy,
                            proxy_auth=self.node.proxy_auth,
                            raise_for_status=True
                    ) as response:
                        os.makedirs(self.get_inst_path(), exist_ok=True)
                        with open(self.get_exe_path(), 'wb') as f:
                            f.write(await response.content.read())
                return True
        except Exception as ex:
            self.log.warning(f"Failed to install {self.name}: {ex}")
            return False

    @override
    async def install(self, version: str | None = None) -> bool:
        if self.is_installed():
            return True
        try:
            if not version:
                version = await self.get_latest_version()
            if await self.do_install(version):
                self.log.info(f"{self.name} version {version} installed.")
                return True
            return False
        except Exception as ex:
            self.log.error(f"Failed to install {self.name}: {ex}")
            return False

    @override
    async def uninstall(self) -> bool:
        # we do not uninstall Sneaker itself
        return True

    @override
    async def repair(self) -> bool:
        if self.is_installed():
            self.log.info(f"Deleting {self.name} installation ...")
            try:
                exe = self.get_exe_path()
                if exe and os.path.exists(exe):
                    os.remove(exe)
                self.log.info(f"{self.name} installation deleted.")
            except Exception as ex:
                self.log.warning(f"Failed to delete {self.name} installation: {ex}\nContinuing anyway ...")
        return await self.install()
