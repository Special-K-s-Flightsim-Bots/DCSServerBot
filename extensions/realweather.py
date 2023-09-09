import json
import os
import shutil
import subprocess
import win32api

from core import Extension, report
from typing import Optional


class RealWeather(Extension):
    @property
    def version(self) -> str:
        try:
            info = win32api.GetFileVersionInfo(
                os.path.join(os.path.expandvars(self.config['installation']), 'realweather.exe'), '\\')
            version = "%d.%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                       info['FileVersionMS'] % 65536,
                                       info['FileVersionLS'] / 65536,
                                       info['FileVersionLS'] % 65536)
        except Exception:
            version = "1.5.0"
        return version

    async def beforeMissionLoad(self) -> bool:
        filename = None
        rw_home = os.path.expandvars(self.config['installation'])
        dcs_home = os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME'])
        try:
            with open(os.path.join(rw_home, 'config.json')) as infile:
                cfg = json.load(infile)
                filename = self.server.get_current_mission_file()
                if not filename:
                    self.log.warning("No mission configured, can't apply real weather.")
                    return False
                if not os.path.exists(filename + '.orig'):
                    shutil.copy2(filename, filename + '.orig')
                # create proper configuration
                for name, element in cfg.items():
                    if name == 'files':
                        element['input-mission'] = filename + '.orig'
                        element['output-mission'] = filename
                    elif name in self.config:
                        element |= self.config[name]
                cwd = os.path.join(dcs_home, 'Missions')
                with open(os.path.join(cwd, 'config.json'), 'w') as outfile:
                    json.dump(cfg, outfile, indent=2)
                subprocess.run(executable=os.path.join(rw_home, 'realweather.exe'), args=[], cwd=cwd, shell=True)
                self.log.info(f"Real weather applied to mission.")
                return True
        except Exception as ex:
            self.log.exception(ex)
            shutil.copy2(filename + '.orig', filename)
            return False

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        embed.add_field(name='RealWeather', value='enabled')

    def is_installed(self) -> bool:
        if not self.config.get('enabled', True):
            return False
        rw_home = os.path.expandvars(self.config['installation'])
        if not os.path.exists(os.path.join(rw_home, 'realweather.exe')):
            self.log.error(f'No realweather.exe found in {rw_home}')
            return False
        ver = [int(x) for x in self.version.split('.')]
        if ver[0] == 1 and ver[1] < 9:
            self.log.error("DCS Realweather < 1.9.x not supported, please upgrade!")
            return False
        if not os.path.exists(os.path.join(rw_home, 'config.json')):
            self.log.error(f'No config.json found in {rw_home}')
            return False
        return True

    async def shutdown(self, data: dict) -> bool:
        return True
