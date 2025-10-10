import aiohttp
import asyncio
import certifi
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import sys
import zipfile

from contextlib import suppress
from core import Extension, MizFile, utils, DEFAULT_TAG, Server, ServiceRegistry
from io import BytesIO
from packaging.version import parse
from services.bot import BotService
from services.servicebus import ServiceBus

# TOML
if sys.version_info >= (3, 11):
    import tomllib as tomli
else:
    import tomli
import tomli_w

__all__ = [
    "RealWeather",
    "RealWeatherException"
]

RW_GITHUB_URL = "https://github.com/evogelsa/dcs-real-weather/releases/latest"
RW_DOWNLOAD_URL = "https://github.com/evogelsa/dcs-real-weather/releases/download/{version}/realweather_{version}.zip"


class RealWeatherException(Exception):
    pass


class RealWeather(Extension):
    _lock = asyncio.Lock()

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.metar = None

    @property
    def version(self) -> str | None:
        return utils.get_windows_version(os.path.join(os.path.expandvars(self.config['installation']),
                                                      'realweather.exe'))

    def load_config(self) -> dict | None:
        try:
            if self.version.split('.')[0] == '1':
                with open(self.config_path, mode='r', encoding='utf-8') as infile:
                    return json.load(infile)
            else:
                with open(self.config_path, mode='rb') as infile:
                    return tomli.load(infile)
        except Exception as ex:
            raise RealWeatherException(f"Error while reading {self.config_path}: {ex}")

    def get_config(self, filename: str) -> dict:
        if 'terrains' in self.config:
            miz = MizFile(filename)
            return self.config['terrains'].get(miz.theatre, self.config['terrains'].get(DEFAULT_TAG, {}))
        else:
            return self.config

    @property
    def config_path(self) -> str:
        rw_home = os.path.expandvars(self.config['installation'])
        if self.version.split('.')[0] == '1':
            return os.path.join(rw_home, 'config.json')
        else:
            return os.path.join(rw_home, 'config.toml')

    @staticmethod
    def get_icao_code(filename: str) -> str | None:
        index = filename.find('ICAO_')
        if index != -1:
            return filename[index + 5:index + 9]
        else:
            return None

    async def _autoupdate(self):
        try:
            version = await self.check_for_updates()
            if version:
                self.log.info(f"A new DCS Real Weather update is available. Updating to version {version} ...")
                await self.do_update(version)
                self._version = version.lstrip('v')
                self.log.info("DCS Real Weather updated.")
                bus = ServiceRegistry.get(ServiceBus)
                await bus.send_to_node({
                    "command": "rpc",
                    "service": BotService.__name__,
                    "method": "audit",
                    "params": {
                        "message": f"DCS Real Weather updated to version {version} on node {self.node.name}."
                    }
                })
        except Exception as ex:
            self.log.exception(ex)

    async def prepare(self) -> bool:
        if self.config.get('autoupdate', False):
            await self._autoupdate()
        return await super().prepare()

    async def generate_config_1_0(self, input_mission: str, output_mission: str, override: dict | None = None):
        try:
            with open(self.config_path, mode='r', encoding='utf-8') as infile:
                cfg = json.load(infile)
        except json.JSONDecodeError as ex:
            raise RealWeatherException(f"Error while reading {self.config_path}: {ex}")
        config = await asyncio.to_thread(self.get_config, input_mission)
        # create proper configuration
        for name, element in cfg.items():
            if name == 'files':
                element['input-mission'] = input_mission
                element['output-mission'] = output_mission
                element['log'] = config.get('files', {}).get('log', 'logfile.log')
            elif name in config:
                if isinstance(config[name], dict):
                    element |= config[name]
                else:
                    cfg[name] = config[name]
        icao = self.get_icao_code(input_mission)
        if icao and icao != self.config.get('metar', {}).get('icao'):
            if 'metar' not in cfg:
                cfg['metar'] = {}
            cfg['metar']['icao'] = icao
        self.locals = utils.deep_merge(cfg, override or {})
        await self.write_config()

    async def generate_config_2_0(self, input_mission: str, output_mission: str, override: dict | None = None):
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        try:
            with open(self.config_path, mode='rb') as infile:
                cfg = tomli.load(infile)
        except tomli.TOMLDecodeError as ex:
            raise RealWeatherException(f"Error while reading {self.config_path}: {ex}")
        config = await asyncio.to_thread(self.get_config, input_mission)
        # create proper configuration
        for name, element in cfg.items():
            if name == 'realweather':
                element |= config.get('realweather', {"mission": {}})
                element['mission'] |= config.get('realweather', {}).get('mission', {}) | {
                    "input": input_mission,
                    "output": output_mission
                }
            elif name in config:
                if isinstance(config[name], dict):
                    element |= config[name]
                else:
                    cfg[name] = config[name]
        icao = self.get_icao_code(input_mission)
        if icao and icao != self.config.get('options', {}).get('weather', {}).get('icao'):
            if 'options' not in cfg:
                cfg['options'] = {}
            if 'weather' not in cfg['options']:
                cfg['options']['weather'] = {"enable": True}
            cfg['options']['weather']['icao'] = icao
        # make sure we only have icao or icao-list
        if cfg.get('options', {}).get('weather', {}).get('icao'):
            cfg['options']['weather'].pop('icao-list', None)
        else:
            cfg['options']['weather']['icao'] = ""
        self.locals = utils.deep_merge(cfg, override or {})
        await self.write_config()

    async def write_config(self):
        cwd = await self.server.get_missions_dir()
        if self.version.split('.')[0] == '1':
            with open(os.path.join(cwd, 'config.json'), mode='w', encoding='utf-8') as outfile:
                json.dump(self.locals, outfile, indent=2)
        else:
            with open(os.path.join(cwd, 'config.toml'), mode='wb') as outfile:
                tomli_w.dump(self.locals, outfile)

    async def generate_config(self, filename: str, tmpname: str, config: dict | None = None):
        if self.version.split('.')[0] == '1':
            await self.generate_config_1_0(filename, tmpname, config)
        else:
            await self.generate_config_2_0(filename, tmpname, config)

    async def run_realweather(self, filename: str, tmpname: str) -> tuple[str, bool]:
        try:
            cwd = await self.server.get_missions_dir()
            rw_home = os.path.expandvars(self.config['installation'])

            def cleanup(cwd: str):
                # delete the mission_unpacked directory which might still be there from former RW runs
                mission_unpacked_dir = os.path.join(cwd, 'mission_unpacked')
                if os.path.exists(mission_unpacked_dir):
                    utils.safe_rmtree(mission_unpacked_dir)

            def run_subprocess():
                # double-check that no mission_unpacked dir is there
                cleanup(cwd)
                # run RW
                process = subprocess.Popen([os.path.join(rw_home, 'realweather.exe')],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
                stdout, stderr = process.communicate()
                if process.returncode != 0:
                    error = stdout.decode('utf-8')
                    self.log.error(error)
                    raise RealWeatherException(f"Error during {self.name}: {process.returncode} - {error}")
                output = stdout.decode('utf-8')
                metar = next((x for x in output.split('\n') if 'METAR:' in x), "")
                remarks = self.locals.get('realweather', {}).get('mission', {}).get('brief', {}).get('remarks', '')
                matches = re.search(rf"(?<=METAR: )(.*)(?= {remarks})", metar)
                if matches:
                    self.metar = matches.group(0)
                if self.config.get('debug', False):
                    self.log.debug(output)

            async with type(self)._lock:
                try:
                    await asyncio.to_thread(run_subprocess)
                finally:
                    cleanup(cwd)

            # check if DCS Real Weather corrupted the miz file
            await asyncio.to_thread(MizFile, tmpname)

            # mission is good, take it
            new_filename = utils.create_writable_mission(filename)
            shutil.copy2(tmpname, new_filename)
            return new_filename, True
        finally:
            os.remove(tmpname)

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        await self.generate_config(filename, tmpname)
        return await self.run_realweather(filename, tmpname)

    async def apply_realweather(self, filename: str, config: dict, use_orig: bool = True) -> str:
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        if use_orig:
            filename = utils.get_orig_file(filename)
        await self.generate_config(filename, tmpname, config)
        return (await self.run_realweather(filename, tmpname))[0]

    async def render(self, param: dict | None = None) -> dict:
        if self.version.split('.')[0] == '1':
            icao = self.config.get('metar', {}).get('icao')
        else:
            icao = self.config.get('options', {}).get('weather', {}).get('icao')
        if self.metar:
            value = f'METAR: {self.metar}'
        elif icao:
            value = f'ICAO: {icao}'
        else:
            value = 'enabled'
        return {
            "name": self.name,
            "version": self.version,
            "value": value
        }

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        installation = self.config.get('installation')
        if not installation:
            self.log.error("No 'installation' specified for RealWeather in your nodes.yaml!")
            return False
        rw_home = os.path.expandvars(installation)
        if not os.path.exists(os.path.join(rw_home, 'realweather.exe')):
            self.log.error(f'No realweather.exe found in {rw_home}')
            return False
        if self.version:
            ver = [int(x) for x in self.version.split('.')]
            if ver[0] == 1 and ver[1] < 9:
                self.log.error("DCS Realweather < 1.9.x not supported, please upgrade!")
                return False
        else:
            self.log.error("DCS Realweather < 1.9.x not supported, please upgrade!")
            return False
        if not os.path.exists(self.config_path):
            self.log.error(f'No {os.path.basename(self.config_path)} found in {rw_home}')
            return False
        return True

    async def startup(self, *, quiet: bool = False) -> bool:
        return await super().startup(quiet=True)

    def shutdown(self, *, quiet: bool = False) -> bool:
        return super().shutdown(quiet=True)

    async def check_for_updates(self) -> str | None:
        with suppress(aiohttp.ClientConnectionError):
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(RW_GITHUB_URL, proxy=self.node.proxy,
                                       proxy_auth=self.node.proxy_auth) as response:
                    if response.status in [200, 302]:
                        version = response.url.raw_parts[-1]
                        if parse(version) > parse(self.version):
                            return version
        return None

    async def do_update(self, version: str):
        installation_dir = os.path.expandvars(self.config['installation'])
        async with aiohttp.ClientSession() as session:
            async with session.get(RW_DOWNLOAD_URL.format(version=version), raise_for_status=True, proxy=self.node.proxy,
                                   proxy_auth=self.node.proxy_auth) as response:
                with zipfile.ZipFile(BytesIO(await response.content.read())) as z:
                    for member in z.namelist():
                        destination_path = os.path.join(installation_dir, member)
                        with open(destination_path, 'wb') as output_file:
                            output_file.write(z.read(member))
