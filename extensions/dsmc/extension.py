import os
import re
import shutil

from core import Extension, Server
from typing import Optional, Union

__all__ = [
    "DSMC"
]


class DSMC(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        if config.get('enabled', True):
            server.locals['mission_rewrite'] = False
            server.locals['validate_missions'] = False

    @property
    def version(self) -> Optional[str]:
        hook = os.path.join(self.server.instance.home, 'Scripts', 'Hooks', 'DSMC_hooks.lua')
        try:
            version = []
            with open(hook, mode='r', encoding='utf-8') as infile:
                content = infile.read()
            version_parts = ['DSMC_MainVersion', 'DSMC_SubVersion', 'DSMC_SubSubVersion']
            for part in version_parts:
                match = re.search(f'{part}\\s*=\\s*"(\\d*)"', content)
                if match:
                    version.append(match.group(1))
            return ".".join(version)
        except Exception:
            return None

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

        if not self.config.get('enabled', True):
            return {}
        cfg = {}
        dcs_home = self.server.instance.home
        with open(os.path.join(dcs_home, 'DSMC_Dedicated_Server_options.lua'), mode='r', encoding='utf-8') as infile:
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
        if not self.locals.get('DSMC_updateMissionList', True) or self.locals.get('DSMC_AutosaveExit_time', 0):
            dcs_home = self.server.instance.home
            shutil.copy2(os.path.join(dcs_home, 'DSMC_Dedicated_Server_options.lua'),
                         os.path.join(dcs_home, 'DSMC_Dedicated_Server_options.lua.bak'))
            with open(os.path.join(dcs_home, 'DSMC_Dedicated_Server_options.lua.bak'), mode='r',
                      encoding='utf-8') as infile:
                with open(os.path.join(dcs_home, 'DSMC_Dedicated_Server_options.lua'), mode='w',
                          encoding='utf-8') as outfile:
                    for line in infile.readlines():
                        if line.strip().startswith('DSMC_24_7_serverStandardSetup'):
                            line = "DSMC_24_7_serverStandardSetup   = false     -- multiple valid values. This option is a simplified setup for the specific server autosave layout. You can input:"
                            self.locals['DSMC_24_7_serverStandardSetup'] = False
                        elif line.strip().startswith('DSMC_updateMissionList'):
                            line = line.replace('false', 'true', 1)
                            self.locals['DSMC_updateMissionList'] = True
                        elif line.strip().startswith('DSMC_AutosaveExit_time'):
                            line = line.replace(str(self.locals['DSMC_AutosaveExit_time']), '0', 1)
                            self.locals['DSMC_AutosaveExit_time'] = 0
                        outfile.write(line)
            self.log.info('  => DSMC configuration changed to be compatible with DCSServerBot.')
        return await super().prepare()

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        if not os.path.basename(filename).startswith('DSMC'):
            return filename, False
        orig = filename
        if not filename[-7:-4].isnumeric():
            filename = filename[:-4] + '_000.miz'
        version = int(filename[-7:-4])
        new_filename = filename[:-7] + f'{version+1:03d}.miz'
        # load the new mission instead, if it exists
        if os.path.exists(new_filename):
            return new_filename, True
        else:
            return orig, False

    async def render(self, param: Optional[dict] = None) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "value": "enabled"
        }

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        dcs_home = self.server.instance.home
        if not os.path.exists(os.path.join(dcs_home, 'DSMC')) or \
                not os.path.exists(os.path.join(dcs_home, 'Scripts', 'Hooks', 'DSMC_hooks.lua')):
            self.log.error(f"  => {self.server.name}: Can't load extension, {self.name} not correctly installed.")
            return False
        return True

    def shutdown(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
