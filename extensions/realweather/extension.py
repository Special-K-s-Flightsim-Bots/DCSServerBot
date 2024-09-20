import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys

from core import Extension, MizFile, utils, DEFAULT_TAG, Server
from typing import Optional

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


class RealWeatherException(Exception):
    pass


class RealWeather(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.lock = asyncio.Lock()
        self.metar = None

    @property
    def version(self) -> Optional[str]:
        return utils.get_windows_version(os.path.join(os.path.expandvars(self.config['installation']),
                                                      'realweather.exe'))

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
    def get_icao_code(filename: str) -> Optional[str]:
        index = filename.find('ICAO_')
        if index != -1:
            return filename[index + 5:index + 9]
        else:
            return None

    async def generate_config_1_0(self, input_mission: str, output_mission: str, cwd: str):
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
            cfg |= {
                "metar": {
                    "icao": icao
                }
            }
            self.config['metar'] = {"icao": icao}
        with open(os.path.join(cwd, 'config.json'), mode='w', encoding='utf-8') as outfile:
            json.dump(cfg, outfile, indent=2)

    async def generate_config_2_0(self, input_mission: str, output_mission: str, cwd: str):
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
                element['mission'] |= config.get('mission', {}) | {
                    "input": input_mission,
                    "output": output_mission
                }
            elif name in config:
                if isinstance(config[name], dict):
                    element |= config[name]
                else:
                    cfg[name] = config[name]
        icao = self.get_icao_code(input_mission)
        if icao and icao != self.config.get('weather', {}).get('icao'):
            cfg |= {
                "weather": {
                    "icao": icao
                }
            }
            self.config['weather'] = {"icao": icao}
        with open(os.path.join(cwd, 'config.toml'), mode='wb') as outfile:
            tomli_w.dump(cfg, outfile)

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        cwd = await self.server.get_missions_dir()

        if self.version.split('.')[0] == '1':
            await self.generate_config_1_0(filename, tmpname, cwd)
        else:
            await self.generate_config_2_0(filename, tmpname, cwd)
        rw_home = os.path.expandvars(self.config['installation'])

        def run_subprocess():
            process = subprocess.Popen([os.path.join(rw_home, 'realweather.exe')],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
            stdout, stderr = process.communicate()
            if process.returncode != 0:
                self.log.error(stderr.decode('utf-8'))
            output = stdout.decode('utf-8')
            metar = next((x for x in output.split('\n') if 'METAR:' in x), "")
            matches = re.search(r"(?<=METAR: )(.*)(?= Generated by)", metar)
            if matches:
                self.metar = matches.group(0)
            if self.config.get('debug', False):
                self.log.debug(output)

        async with self.lock:
            await asyncio.to_thread(run_subprocess)

        # check if DCS Real Weather corrupted the miz file
        # (as the original author does not see any reason to do that on his own)
        await asyncio.to_thread(MizFile, tmpname)
        # mission is good, take it
        new_filename = utils.create_writable_mission(filename)
        shutil.copy2(tmpname, new_filename)
        os.remove(tmpname)
        return new_filename, True

    async def render(self, param: Optional[dict] = None) -> dict:
        icao = self.config.get('metar', {}).get('icao')
        if self.metar:
            value = f'METAR: {self.metar}'
        elif icao:
            value = f'ICAO: {icao}'
        else:
            value = 'enabled'
        return {
            "name": "RealWeather",
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

    def shutdown(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
