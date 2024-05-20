import asyncio
import json
import os
import shutil
import subprocess
import tempfile

from core import Extension, MizFile, utils, DEFAULT_TAG, Server
from typing import Optional


class RealWeatherException(Exception):
    pass


class RealWeather(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.lock = asyncio.Lock()

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

    @staticmethod
    def get_icao_code(filename: str) -> Optional[str]:
        index = filename.find('ICAO_')
        if index != -1:
            return filename[index + 5:index + 9]
        else:
            return None

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        rw_home = os.path.expandvars(self.config['installation'])
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        with open(os.path.join(rw_home, 'config.json'), mode='r', encoding='utf-8') as infile:
            cfg = json.load(infile)
        config = self.get_config(filename)
        # create proper configuration
        for name, element in cfg.items():
            if name == 'files':
                element['input-mission'] = filename
                element['output-mission'] = tmpname
            elif name in config:
                if isinstance(config[name], dict):
                    element |= config[name]
                else:
                    cfg[name] = config[name]
        icao = self.get_icao_code(filename)
        if icao and icao != self.config.get('metar', {}).get('icao'):
            cfg |= {
                "metar": {
                    "icao": icao
                }
            }
            self.config['metar'] = {"icao": icao}
        cwd = await self.server.get_missions_dir()
        with open(os.path.join(cwd, 'config.json'), mode='w', encoding='utf-8') as outfile:
            json.dump(cfg, outfile, indent=2)

        def run_subprocess():
            process = subprocess.Popen([os.path.join(rw_home, 'realweather.exe')],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
            stdout, stderr = process.communicate()
            if process.returncode != 0:
                self.log.error(stderr.decode('utf-8'))
            if self.config.get('debug', False):
                self.log.info(stdout.decode('utf-8'))

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
        if icao:
            value = f'Metar: {icao}'
        else:
            value = 'enabled'
        return {
            "name": "RealWeather",
            "version": self.version,
            "value": value
        }

    def is_installed(self) -> bool:
        if not self.config.get('enabled', True):
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
        if not os.path.exists(os.path.join(rw_home, 'config.json')):
            self.log.error(f'No config.json found in {rw_home}')
            return False
        return True

    def shutdown(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
