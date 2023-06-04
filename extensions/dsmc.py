import os.path
import shutil

from core import Extension, report
from typing import Optional, Union


class DSMC(Extension):
    @property
    def version(self) -> str:
        return "1.0.0"

    def load_config(self) -> Optional[dict]:
        def parse(_value: str) -> Union[int, str, bool]:
            if _value.startswith('"'):
                return _value[1:-1]
            elif _value == 'true':
                return True
            elif _value == 'false':
                return False
            else:
                return eval(_value)

        cfg = dict()
        dcs_home = self.server.instance.home
        with open(dcs_home + os.path.sep + 'DSMC_Dedicated_Server_options.lua') as infile:
            for line in infile.readlines():
                line = line.strip()
                if line.startswith('DSMC'):
                    pos1 = line.find('=')
                    pos2 = line.find('-')
                    if pos2 == -1:
                        pos2 = len(line)
                    key = line[:pos1 - 1].strip()
                    value = parse(line[pos1 + 1:pos2].strip())
                    cfg[key] = value
        return cfg

    async def prepare(self) -> bool:
        if 'DSMC_updateMissionList' not in self.locals:
            self.log.error('  => DSMC_updateMissionList missing in DSMC_Dedicated_Server_options.lua! '
                           'Check your config and / or update DSMC!')
            return False
        if self.locals.get('DSMC_updateMissionList', True) or self.locals.get('DSMC_AutosaveExit_time', 0):
            dcs_home = self.server.instance.home
            shutil.copy2(os.path.join(dcs_home, 'DSMC_Dedicated_Server_options.lua'),
                         os.path.join(dcs_home, 'DSMC_Dedicated_Server_options.lua.bak'))
            with open(dcs_home + os.path.sep + 'DSMC_Dedicated_Server_options.lua.bak') as infile:
                with open(dcs_home + os.path.sep + 'DSMC_Dedicated_Server_options.lua', 'w') as outfile:
                    for line in infile.readlines():
                        if line.strip().startswith('DSMC_updateMissionList'):
                            line = line.replace('true', 'false', 1)
                            self.locals['DSMC_updateMissionList'] = False
                        elif line.strip().startswith('DSMC_AutosaveExit_time'):
                            line = line.replace(str(self.locals['DSMC_AutosaveExit_time']), '0', 1)
                            self.locals['DSMC_AutosaveExit_time'] = 0
                        outfile.write(line)
            self.log.info('  => DSMC configuration changed to be compatible with DCSServerBot.')
        return True

    async def beforeMissionLoad(self) -> bool:
        filename = await self.server.get_current_mission_file()
        if not filename or not os.path.basename(filename).startswith('DSMC'):
            return False
        if not filename[-7:-4].isnumeric():
            filename = filename[:-4] + '_000.miz'
        version = int(filename[-7:-4])
        new_filename = filename[:-7] + f'{version+1:03d}.miz'
        # load the new mission instead, if it exists
        if os.path.exists(new_filename):
            missions = self.server.settings['missionList']
            missions[int(self.server.settings['listStartIndex']) - 1] = new_filename
            self.server.settings['missionList'] = missions
        return True

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        embed.add_field(name='DSMC', value='enabled')

    def is_installed(self) -> bool:
        if 'enabled' not in self.config or not self.config['enabled']:
            return False
        dcs_home = self.server.instance.home
        if not os.path.exists(os.path.join(dcs_home, 'DSMC')) or \
                not os.path.exists(os.path.join(dcs_home, 'Scripts/Hooks/DSMC_hooks.lua')):
            self.log.error(f'DSMC not installed in this server.')
            return False
        return True

    async def shutdown(self) -> bool:
        return True
