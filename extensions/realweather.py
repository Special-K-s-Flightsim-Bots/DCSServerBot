import json
import os
import tempfile
import subprocess
import sys
if sys.platform == 'win32':
    import win32api

from core import Extension, report, MizFile, utils
from typing import Optional, Tuple


class RealWeather(Extension):
    @property
    def version(self) -> Optional[str]:
        if sys.platform == 'win32':
            info = win32api.GetFileVersionInfo(
                os.path.join(os.path.expandvars(self.config['installation']), 'realweather.exe'), '\\')
            version = "%d.%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                       info['FileVersionMS'] % 65536,
                                       info['FileVersionLS'] / 65536,
                                       info['FileVersionLS'] % 65536)
        else:
            version = None
        return version

    async def beforeMissionLoad(self, filename: str) -> Tuple[str, bool]:
        rw_home = os.path.expandvars(self.config['installation'])
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        with open(os.path.join(rw_home, 'config.json')) as infile:
            cfg = json.load(infile)
        # create proper configuration
        for name, element in cfg.items():
            if name == 'files':
                element['input-mission'] = filename
                element['output-mission'] = tmpname
            elif name in self.config:
                if isinstance(self.config[name], dict):
                    element |= self.config[name]
                else:
                    element = self.config[name]
        cwd = await self.server.get_missions_dir()
        with open(os.path.join(cwd, 'config.json'), 'w') as outfile:
            json.dump(cfg, outfile, indent=2)
        subprocess.run(executable=os.path.join(rw_home, 'realweather.exe'), args=[], cwd=cwd, shell=True)
        # check if DCS Real Weather corrupted the miz file
        # (as the original author does not see any reason to do that on his own)
        MizFile(self, tmpname)
        # mission is good, take it
        new_filename = utils.create_writable_mission(filename)
        if os.path.exists(new_filename):
            os.remove(new_filename)
        os.rename(tmpname, new_filename)
        self.log.info(f"Real weather applied to the mission.")
        return new_filename, True

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        embed.add_field(name='RealWeather', value='enabled')

    def is_installed(self) -> bool:
        if not self.config.get('enabled', True):
            return False
        rw_home = os.path.expandvars(self.config['installation'])
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

    async def shutdown(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
