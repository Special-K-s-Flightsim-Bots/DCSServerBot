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
            cmd = utils.format_string(cmd, dcs_installation=dcs_installation, dcs_home=dcs_home,
                                      server=server, config=self.config)
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

    async def onChatCommand(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        if data['subcommand'] in ['preset', 'presets'] and \
                utils.has_discord_roles(self, server, data['from_id'], ['DCS Admin']):
            config = self.plugin.get_config(server)
            presets = list(config['presets'].keys())
            if len(data['params']) == 0:
                message = 'The following presets are available:\n'
                for i in range(0, len(presets)):
                    preset = presets[i]
                    message += f"{i+1} {preset}\n"
                message += f"\nUse -{data['subcommand']} <number> to load that preset (mission will be restarted!)"
                utils.sendUserMessage(self, server, data['from_id'], message, 30)
            else:
                n = int(data['params'][0]) - 1
                self.bot.sendtoDCS(server, {"command": "stop_server"})
                self.plugin.change_mizfile(server, config, presets[n])
                self.bot.sendtoDCS(server, {"command": "start_server"})
