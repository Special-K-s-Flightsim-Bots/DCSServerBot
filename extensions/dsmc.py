import os.path
import shutil

from core import Extension, report
from typing import Optional, Union


class DSMC(Extension):
    @property
    def version(self) -> str:
        return "1.0.0"

    def load_config(self) -> Optional[dict]:
        def parse(value: str) -> Union[int, str, bool]:
            if value.startswith('"'):
                return value[1:-1]
            elif value == 'true':
                return True
            elif value == 'false':
                return False
            else:
                return int(value)

        cfg = dict()
        dcs_home = os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME'])
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
        # we don't want to have DSMC
        if self.locals['DSMC_updateMissionList'] or self.locals['DSMC_AutosaveExit_time']:
            dcs_home = os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME'])
            shutil.copy2(dcs_home + os.path.sep + 'DSMC_Dedicated_Server_options.lua', dcs_home + os.path.sep + 'DSMC_Dedicated_Server_options.lua.bak')
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
        filename = self.server.get_current_mission_file()
        if not filename.startswith('DSMC'):
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

    def verify(self) -> bool:
        if 'enabled' not in self.config or not self.config['enabled']:
            return False
        dcs_home = os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME'])
        if not os.path.exists(dcs_home + os.path.sep + 'DSMC') or \
                not os.path.exists(dcs_home + '/Scripts/Hooks/DSMC_hooks.lua'):
            self.log.error(f'DSMC not installed in this server.')
            return False
        return True
