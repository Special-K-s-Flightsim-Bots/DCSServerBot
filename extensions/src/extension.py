import asyncio
import atexit
import json
import os
import psutil
import shutil
import subprocess
import sys

from core import Extension, utils, Server
from typing import Optional


class SRC(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.process: Optional[psutil.Process] = None

    @property
    def version(self) -> Optional[str]:
        return '.'.join(utils.get_windows_version(self.get_exe_path()).split('.')[:3])

    def get_exe_path(self) -> str:
        return os.path.join(self.get_inst_path(), 'Server', 'Server.exe')

    def get_inst_path(self) -> str:
        return os.path.expandvars(self.config.get('installation'))

    def get_config_path(self) -> str:
        return self.locals.get('config', os.path.join(self.server.instance.home, 'Config', 'SRC.json'))

    def load_config(self) -> Optional[dict]:
        config_file = self.get_config_path()
        if not os.path.exists(config_file):
            shutil.copy2(os.path.join(self.get_inst_path(), 'Server', 'include', 'config', 'config.json'), config_file)
        with open(config_file, mode='r', encoding='utf-8') as f:
            return json.load(f)

    async def prepare(self) -> bool:
        self.locals['DCS_SAVED_GAMES'] = self.server.instance.home
        self.locals['DISCORD_PRESENCE_SERVER_NAME'] = self.server.display_name[:128]
        self.locals['SERVER_TCP_PORT'] = self.config.get('tcp_port', 7500)
        self.locals['SERVER_UDP_PORT'] = self.config.get('udp_port', 7600)
        self.locals['DCS_UDP_PORT'] = self.config.get('dcs_port', 7700)
        self.locals['PASSWORDS'] = {
            "ADMIN": self.config.get('passwords', {}).get('admin', 'password'),
            "RED": self.config.get('passwords', {}).get('red', 'red'),
            "BLUE": self.config.get('passwords', {}).get('blue', 'blue')
        }
        self.config['MAX_CLIENTS'] = {
            "RED": self.config.get('max_clients', {}).get('red', 5),
            "BLUE": self.config.get('max_clients', {}).get('blue', 5)
        }
        with open(self.get_config_path(), mode='w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2)
        if self.config.get('always_on', False):
            # no_shutdown defaults to True for always_on
            self.config['no_shutdown'] = self.config.get('no_shutdown', True)
            if not await asyncio.to_thread(self.is_running):
                asyncio.create_task(self.startup())
        return await super().prepare()

    def is_installed(self) -> bool:
        try:
            inst_dir = self.get_inst_path()
            if os.path.exists(inst_dir) and os.path.exists(os.path.join(inst_dir, 'Server', 'Server.exe')):
                return True
        except KeyError:
            pass
        return False

    def is_running(self) -> bool:
        return utils.is_open('127.0.0.1', self.locals.get('SERVER_TCP_PORT', 7500))

    async def startup(self) -> bool:
        if self.config.get('autostart', True):
            self.log.debug(f"Launching SRC server with: \"{self.get_exe_path()}\" -cfg=\"{self.config['config']}\"")
            if sys.platform == 'win32' and self.config.get('minimized', True):
                import win32process
                import win32con

                info = subprocess.STARTUPINFO()
                info.dwFlags |= win32process.STARTF_USESHOWWINDOW
                info.wShowWindow = win32con.SW_SHOWMINNOACTIVE
            else:
                info = None

            def run_subprocess():
                return subprocess.Popen([
                    self.get_exe_path(),
                    f"-cfg={os.path.expandvars(self.config['config'])}"
                ], startupinfo=info, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)

            p = await asyncio.to_thread(run_subprocess)
            try:
                self.process = psutil.Process(p.pid)
            except psutil.NoSuchProcess:
                self.log.error(f"Error during launch of {self.get_exe_path()}!")
                return False
            atexit.register(self.terminate)
        return await super().startup()

    def terminate(self) -> bool:
        try:
            utils.terminate_process(self.process)
            self.process = None
            return True
        except Exception as ex:
            self.log.error(f"Error during shutdown of {self.get_exe_path()}: {str(ex)}")
            return False

    def shutdown(self) -> bool:
        if self.config.get('autostart', True) and not self.config.get('no_shutdown', False):
            super().shutdown()
            return self.terminate()
        return True

    async def render(self, param: Optional[dict] = None) -> dict:
        if not self.locals:
            raise NotImplementedError()

        host = self.config.get('host', self.node.public_ip)
        value = f"{host}:{self.locals['SERVER_TCP_PORT']}"
        show_passwords = self.config.get('show_passwords', True)
        if show_passwords:
            blue = self.locals.get('PASSWORDS', {}).get('BLUE')
            red = self.locals.get('PASSWORDS', {}).get('RED')
            if blue or red:
                value += f'\n🔹 Pass: {blue}\n🔸 Pass: {red}'
        rc = {
            "name": self.name,
            "version": self.version,
            "value": value
        }
