import os
import subprocess
import win32api
from core import Extension, DCSServerBot, utils


class SRS(Extension):
    def __init__(self, bot: DCSServerBot, server: dict):
        super().__init__(bot, server)
        self.process = None

    async def startup(self) -> bool:
        installation = self.server['installation']
        self.log.debug(r'Launching SRS server with: "{}\SR-Server.exe" -cfg="{}"'.format(
            os.path.expandvars(self.config['DCS']['SRS_INSTALLATION']),
            os.path.expandvars(self.config[installation]['SRS_CONFIG'])))
        self.process = subprocess.Popen(['SR-Server.exe', '-cfg={}'.format(
            os.path.expandvars(self.config[installation]['SRS_CONFIG']))],
                                executable=os.path.expandvars(
                                    self.config['DCS']['SRS_INSTALLATION']) + r'\SR-Server.exe')
        return await self.check()

    async def shutdown(self):
        p = self.process or utils.find_process('SR-Server.exe', self.server['installation'])
        if p:
            p.kill()
            self.process = None
            return True
        else:
            return False

    async def check(self) -> bool:
        if self.process:
            return not self.process.poll()
        installation = self.server['installation']
        return utils.is_open(self.config[installation]['SRS_HOST'], self.config[installation]['SRS_PORT'])

    @property
    def version(self) -> str:
        info = win32api.GetFileVersionInfo(
            os.path.expandvars(self.config['DCS']['SRS_INSTALLATION']) + r'\SR-Server.exe', '\\')
        version = "%d.%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                   info['FileVersionMS'] % 65536,
                                   info['FileVersionLS'] / 65536,
                                   info['FileVersionLS'] % 65536)
        return version
