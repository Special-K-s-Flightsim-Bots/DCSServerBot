import subprocess
from core import EventListener


class SchedulerListener(EventListener):

    def run(self, server, method):
        if method.startswith('load:'):
            self.bot.sendtoDCS(server, {
                "command": "do_script_file",
                "file": method[5:].replace('\\', '/')
            })
        elif method.startswith('lua:'):
            self.bot.sendtoDCS(server, {
                "command": "do_script",
                "script": method[4:]
            })
        elif method.startswith('call:'):
            self.bot.sendtoDCS(server, {
                "command": method[5:]
            })
        elif method.startswith('run:'):
            self.log.debug('Launching command: ' + method[4:])
            subprocess.run(method[4:].split(' '), shell=True)

    async def onSimulationStart(self, data):
        server = self.globals[data['server_name']]
        if self.plugin in server:
            config = server[self.plugin]
            if 'onMissionStart' in config:
                self.run(server, config['onMissionStart'])

    async def onMissionEnd(self, data):
        server = self.globals[data['server_name']]
        if self.plugin in server:
            config = server[self.plugin]
            if 'onMissionEnd' in config:
                self.run(server, config['onMissionEnd'])

    async def onShutdown(self, data):
        server = self.globals[data['server_name']]
        if self.plugin in server:
            config = server[self.plugin]
            if 'onShutdown' in config:
                self.run(server, config['onShutdown'])
