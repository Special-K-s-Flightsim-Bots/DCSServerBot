import shlex
import subprocess
from core import EventListener, utils
from os import path


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
            cmd = method[4:]
            dcs_installation = path.normpath(path.expandvars(self.config['DCS']['DCS_INSTALLATION']))
            dcs_home = path.normpath(path.expandvars(self.config[server['installation']]['DCS_HOME']))
            cmd = utils.format_string(cmd, dcs_installation=dcs_installation, dcs_home=dcs_home, server=server)
            self.log.debug('Launching command: ' + cmd)
            subprocess.run(shlex.split(cmd), shell=True)

    async def onSimulationStart(self, data):
        server = self.globals[data['server_name']]
        if self.plugin_name in server:
            config = server[self.plugin_name]
            if 'onMissionStart' in config:
                self.run(server, config['onMissionStart'])

    async def onMissionLoadEnd(self, data):
        server = self.globals[data['server_name']]
        if 'restart_pending' in server:
            del server['restart_pending']

    async def onMissionEnd(self, data):
        server = self.globals[data['server_name']]
        if self.plugin_name in server:
            config = server[self.plugin_name]
            if 'onMissionEnd' in config:
                self.run(server, config['onMissionEnd'])

    async def onShutdown(self, data):
        server = self.globals[data['server_name']]
        if self.plugin_name in server:
            config = server[self.plugin_name]
            if 'onShutdown' in config:
                self.run(server, config['onShutdown'])
