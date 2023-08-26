import asyncio
import json
import os
import shutil

from core import Extension, report
from typing import Optional


class RealWeather(Extension):
    @property
    def version(self) -> str:
        return "1.5.0"

    async def beforeMissionLoad(self) -> bool:
        filename = None
        rw_home = os.path.expandvars(self.config['installation'])
        try:
            with open(os.path.join(rw_home, 'config.json')) as infile:
                cfg = json.load(infile)
                filename = await self.server.get_current_mission_file()
                if not filename:
                    self.log.warning("No mission configured, can't apply real weather.")
                    return False
                if not os.path.exists(filename + '.orig'):
                    shutil.copy2(filename, filename + '.orig')
                cfg['input-mission-file'] = filename + '.orig'
                cfg['output-mission-file'] = filename
                for key in list(cfg.keys()):
                    if key in self.config:
                        cfg[key] = self.config[key]
                cwd = await self.server.get_missions_dir()
                with open(os.path.join(cwd, 'config.json'), 'w') as outfile:
                    json.dump(cfg, outfile, indent=2)
                proc = await asyncio.create_subprocess_exec(os.path.join(rw_home, 'realweather.exe'), cwd=cwd)
                await proc.communicate()
                self.log.info(f"Real weather applied to mission.")
                return True
        except Exception as ex:
            self.log.exception(ex)
            shutil.copy2(filename + '.orig', filename)
            return False

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
