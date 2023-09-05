import json
import os
import shutil
import subprocess
import sys
if sys.platform == 'win32':
    import win32api

from core import Extension, report, MizFile
from typing import Optional


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

    async def beforeMissionLoad(self) -> bool:
        rw_home = os.path.expandvars(self.config['installation'])
        with open(os.path.join(rw_home, 'config.json')) as infile:
            cfg = json.load(infile)
            filename = await self.server.get_current_mission_file()
            if not filename:
                self.log.warning("No mission configured, can't apply DCS Real Weather.")
                return False
            # check, if we can write that file
            try:
                with open(filename, 'a'):
                    new_filename = filename
            except PermissionError:
                if '.dcssb' in filename:
                    new_filename = os.path.join(os.path.dirname(filename).replace('.dcssb', ''),
                                                os.path.basename(filename))
                else:
                    dirname = os.path.join(os.path.dirname(filename), '.dcssb')
                    os.makedirs(dirname, exist_ok=True)
                    new_filename = os.path.join(dirname, os.path.basename(filename))

            if not os.path.exists(filename + '.orig'):
                shutil.copy2(filename, filename + '.orig')
            cfg['input-mission-file'] = filename + '.orig'
            cfg['output-mission-file'] = new_filename
            for key in list(cfg.keys()):
                if key in self.config:
                    cfg[key] = self.config[key]
            cwd = await self.server.get_missions_dir()
            with open(os.path.join(cwd, 'config.json'), 'w') as outfile:
                json.dump(cfg, outfile, indent=2)
            subprocess.run(executable=os.path.join(rw_home, 'realweather.exe'), args=[], cwd=cwd, shell=True)
            # check if DCS Real Weather corrupted the miz file
            # (as the original auther does not see any reason to do that on his own)
            try:
                MizFile(self, new_filename)
            except Exception as ex:
                self.log.exception(ex)
                self.log.warning(f"DCS Real Weather corrupted the mission, rolling back...")
                if new_filename == filename:
                    shutil.copy2(filename + '.orig', filename)
                return False
            if new_filename != filename:
                missions: list = self.server.settings['missionList']
                missions.remove(filename)
                missions.append(new_filename)
                self.server.settings['missionList'] = missions
                self.server.settings['listStartIndex'] = missions.index(new_filename) + 1
            self.log.info(f"Real weather applied to the mission.")
            return True

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        embed.add_field(name='RealWeather', value='enabled')

    def is_installed(self) -> bool:
        if 'enabled' not in self.config or not self.config['enabled']:
            return False
        rw_home = os.path.expandvars(self.config['installation'])
        if not os.path.exists(os.path.join(rw_home, 'realweather.exe')):
            self.log.error(f'No realweather.exe found in {rw_home}')
            return False
        if not os.path.exists(os.path.join(rw_home, 'config.json')):
            self.log.error(f'No config.json found in {rw_home}')
            return False
        return True

    async def shutdown(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
