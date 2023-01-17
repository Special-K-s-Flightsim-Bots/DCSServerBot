import asyncio
import shlex
import string
import subprocess
from core import EventListener, utils, Server, Player, Status
from os import path
from typing import cast, TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Scheduler


class SchedulerListener(EventListener):

    def _run(self, server: Server, method: str) -> None:
        if method.startswith('load:'):
            server.sendtoDCS({
                "command": "do_script_file",
                "file": method[5:].replace('\\', '/')
            })
        elif method.startswith('lua:'):
            server.sendtoDCS({
                "command": "do_script",
                "script": method[4:]
            })
        elif method.startswith('call:'):
            server.sendtoDCS({
                "command": method[5:]
            })
        elif method.startswith('run:'):
            cmd = method[4:]
            dcs_installation = path.normpath(path.expandvars(self.bot.config['DCS']['DCS_INSTALLATION']))
            dcs_home = path.normpath(path.expandvars(self.bot.config[server.installation]['DCS_HOME']))
            cmd = utils.format_string(cmd, dcs_installation=dcs_installation, dcs_home=dcs_home,
                                      server=server, config=self.bot.config)
            self.log.debug('Launching command: ' + cmd)
            subprocess.run(shlex.split(cmd), shell=True)

    async def registerDCSServer(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        self.plugin.init_extensions(server, config)
        for ext in server.extensions.values():
            if not await ext.is_running() and await ext.startup():
                self.log.info(f"  - {ext.name} v{ext.version} launched for \"{server.name}\".")
                await self.bot.audit(f"{ext.name} started", server=server)

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        server: Server = self.bot.servers[data['server_name']]
        if server.restart_pending:
            player: Player = server.get_player(id=data['id'])
            player.sendChatMessage("*** Mission is about to be restarted soon! ***")

    async def onGameEvent(self, data: dict) -> None:
        async def _process(server: Server, what: dict) -> None:
            config = self.plugin.get_config(server)
            if 'shutdown' in what['command']:
                await server.shutdown()
                message = 'shut down DCS server'
                if 'user' not in what:
                    message = string.capwords(self.plugin_name) + ' ' + message
                await self.bot.audit(message, server=server, user=what['user'] if 'user' in what else None)
            if 'restart' in what['command']:
                if server.status == Status.SHUTDOWN:
                    await self.plugin.launch_dcs(server, config)
                elif server.status == Status.STOPPED:
                    if self.plugin.is_mission_change(server, config):
                        if 'RealWeather' in server.extensions.keys():
                            await server.extensions['RealWeather'].beforeMissionLoad()
                        if 'settings' in config['restart']:
                            self.plugin.change_mizfile(server, config)
                        await server.start()
                    message = 'started DCS server'
                    if 'user' not in what:
                        message = string.capwords(self.plugin_name) + ' ' + message
                    await self.bot.audit(message, server=server, user=what['user'] if 'user' in what else None)
                elif server.status in [Status.RUNNING, Status.PAUSED]:
                    if self.plugin.is_mission_change(server, config):
                        await server.stop()
                        if 'RealWeather' in server.extensions.keys():
                            await server.extensions['RealWeather'].beforeMissionLoad()
                        if 'settings' in config['restart']:
                            self.plugin.change_mizfile(server, config)
                        await server.start()
                    else:
                        await server.current_mission.restart()
                    message = 'restarted mission'
                    if 'user' not in what:
                        message = string.capwords(self.plugin_name) + ' ' + message
                    await self.bot.audit(message, server=server, user=what['user'] if 'user' in what else None)
            elif what['command'] == 'rotate':
                await server.loadNextMission()
                if self.plugin.is_mission_change(server, config):
                    await server.stop()
                    if 'RealWeather' in server.extensions.keys():
                        await server.extensions['RealWeather'].beforeMissionLoad()
                    if 'settings' in config['restart']:
                        self.plugin.change_mizfile(server, config)
                    await server.start()
                await self.bot.audit(f"{string.capwords(self.plugin_name)} rotated mission", server=server)
            elif what['command'] == 'load':
                await server.loadMission(what['id'])
                message = 'loaded mission'
                if 'user' not in what:
                    message = string.capwords(self.plugin_name) + ' ' + message
                await self.bot.audit(message, server=server, user=what['user'] if 'user' in what else None)
            elif what['command'] == 'preset':
                await server.stop()
                for preset in what['preset']:
                    self.plugin.change_mizfile(server, config, preset)
                await server.start()
                await self.bot.audit("changed preset", server=server, user=what['user'])
            server.restart_pending = False

        server: Server = self.bot.servers[data['server_name']]
        if data['eventName'] == 'disconnect':
            if not server.is_populated() and server.on_empty:
                await _process(server, server.on_empty)
                server.on_empty = dict()
        elif data['eventName'] == 'mission_end':
            self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server.name})
            if server.on_mission_end:
                await _process(server, server.on_mission_end)
                server.on_mission_end = dict()

    async def onSimulationStart(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if config and 'onMissionStart' in config:
            self._run(server, config['onMissionStart'])

    async def onMissionLoadEnd(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        server.restart_pending = False
        for ext in server.extensions.values():
            if await ext.is_running():
                self.bot.loop.call_soon(asyncio.create_task, ext.onMissionLoadEnd(data))

    async def onMissionEnd(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if config and 'onMissionEnd' in config:
            self._run(server, config['onMissionEnd'])

    async def onSimulationStop(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        for ext in server.extensions.values():
            if await ext.is_running():
                self.bot.loop.call_soon(asyncio.create_task, ext.onSimulationStop(data))

    async def onShutdown(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if config and 'onShutdown' in config:
            self._run(server, config['onShutdown'])

    async def onChatCommand(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        player = server.get_player(id=data['from_id'])
        if not player or not player.has_discord_roles(['DCS Admin']):
            return
        if data['subcommand'] in ['preset', 'presets']:
            config = self.plugin.get_config(server)
            if config and 'presets' in config:
                presets = list(config['presets'].keys())
                if len(data['params']) == 0:
                    message = 'The following presets are available:\n'
                    for i in range(0, len(presets)):
                        preset = presets[i]
                        message += f"{i+1} {preset}\n"
                    message += f"\nUse -{data['subcommand']} <number> to load that preset (mission will be restarted!)"
                    player.sendUserMessage(message, 30)
                else:
                    n = int(data['params'][0]) - 1
                    await server.stop()
                    cast(Scheduler, self.plugin).change_mizfile(server, config, presets[n])
                    await server.start()
            else:
                player.sendChatMessage(f"There are no presets available to select.")
        elif data['subcommand'] in ['maintenance', 'maint']:
            if not server.maintenance:
                server.maintenance = True
                server.restart_pending = False
                server.on_empty = dict()
                server.on_mission_end = dict()
                player.sendChatMessage('Maintenance mode enabled.')
                await self.bot.audit("set maintenance flag", user=player.member, server=server)
            else:
                player.sendChatMessage('Maintenance mode is already active.')
        elif data['subcommand'] == 'clear':
            if server.maintenance:
                server.maintenance = False
                player.sendChatMessage('Maintenance mode disabled/cleared.')
                await self.bot.audit("cleared maintenance flag", user=player.member, server=server)
            else:
                player.sendChatMessage("Maintenance mode wasn't enabled.")
