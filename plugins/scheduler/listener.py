import asyncio
from core import EventListener, utils, Server, Player, Status, event, chat_command
from os import path


class SchedulerListener(EventListener):

    async def run(self, server: Server, method: str) -> None:
        if method.startswith('load:'):
            server.sendtoDCS({
                "command": "do_script_file",
                "file": method[5:].strip().replace('\\', '/')
            })
        elif method.startswith('lua:'):
            server.sendtoDCS({
                "command": "do_script",
                "script": method[4:].strip()
            })
        elif method.startswith('call:'):
            server.sendtoDCS({
                "command": method[5:].strip()
            })
        elif method.startswith('run:'):
            cmd = method[4:].strip()
            dcs_installation = path.normpath(path.expandvars(self.bot.config['DCS']['DCS_INSTALLATION']))
            dcs_home = path.normpath(path.expandvars(self.bot.config[server.installation]['DCS_HOME']))
            cmd = utils.format_string(cmd, dcs_installation=dcs_installation, dcs_home=dcs_home, server=server,
                                      config=self.bot.config)
            self.log.debug('Launching command: ' + cmd)
            await asyncio.create_subprocess_shell(cmd)

    async def process(self, server: Server, what: dict) -> None:
        config = self.plugin.get_config(server)
        if 'shutdown' in what['command']:
            await server.shutdown()
            message = 'shut down DCS server'
            if 'user' not in what:
                message = self.plugin_name.title() + ' ' + message
            await self.bot.audit(message, server=server, user=what['user'] if 'user' in what else None)
        if 'restart' in what['command']:
            if server.status == Status.SHUTDOWN:
                await self.plugin.launch_dcs(server, config)
            elif server.status == Status.STOPPED:
                if self.plugin.is_mission_change(server, config):
                    for ext in server.extensions.values():
                        await ext.beforeMissionLoad()
                    if 'settings' in config:
                        await self.plugin.change_mizfile(server, config)
                    await server.start()
                message = 'started DCS server'
                if 'user' not in what:
                    message = self.plugin_name.title() + ' ' + message
                await self.bot.audit(message, server=server, user=what.get('user'))
            elif server.status in [Status.RUNNING, Status.PAUSED]:
                if self.plugin.is_mission_change(server, config):
                    await server.stop()
                    for ext in server.extensions.values():
                        await ext.beforeMissionLoad()
                    if 'settings' in config:
                        await self.plugin.change_mizfile(server, config)
                    await server.start()
                else:
                    await server.current_mission.restart()
                message = f'restarted mission {server.current_mission.display_name}'
                if 'user' not in what:
                    message = self.plugin_name.title() + ' ' + message
                await self.bot.audit(message, server=server, user=what.get('user'))
        elif what['command'] == 'rotate':
            await server.loadNextMission()
            if self.plugin.is_mission_change(server, config):
                await server.stop()
                for ext in server.extensions.values():
                    await ext.beforeMissionLoad()
                if 'settings' in config:
                    await self.plugin.change_mizfile(server, config)
                await server.start()
            await self.bot.audit(f"{self.plugin_name.title()} rotated to mission "
                                 f"{server.current_mission.display_name}", server=server)
        elif what['command'] == 'load':
            await server.loadMission(what['id'])
            message = f'loaded mission {server.current_mission.display_name}'
            if 'user' not in what:
                message = self.plugin_name.title() + ' ' + message
            await self.bot.audit(message, server=server, user=what['user'] if 'user' in what else None)
        elif what['command'] == 'preset':
            await server.stop()
            for preset in what['preset']:
                await self.plugin.change_mizfile(server, config, preset)
            await server.start()
            await self.bot.audit(f"changed preset to {what['preset']}", server=server, user=what['user'])
        server.restart_pending = False

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        await self.plugin.init_extensions(server, config)
        for ext in server.extensions.values():
            if not ext.is_running():
                await ext.startup()

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        if server.restart_pending:
            player: Player = server.get_player(id=data['id'])
            player.sendChatMessage("*** Mission is about to be restarted soon! ***")

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        if not server.is_populated() and server.on_empty:
            self.bot.loop.call_soon(asyncio.create_task, self.process(server, server.on_empty.copy()))
            server.on_empty.clear()

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'disconnect':
            if not server.is_populated() and server.on_empty:
                self.bot.loop.call_soon(asyncio.create_task, self.process(server, server.on_empty.copy()))
                server.on_empty.clear()
        elif data['eventName'] == 'mission_end':
            self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server.name})
            if server.on_mission_end:
                self.bot.loop.call_soon(asyncio.create_task, self.process(server, server.on_mission_end.copy()))
                server.on_mission_end.clear()

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onMissionStart' in config:
            await self.run(server, config['onMissionStart'])

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        server.restart_pending = False
        server.on_empty.clear()
        server.on_mission_end.clear()
        for ext in server.extensions.values():
            if ext.is_running():
                await ext.onMissionLoadEnd(data)

    @event(name="onMissionEnd")
    async def onMissionEnd(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onMissionEnd' in config:
            await self.run(server, config['onMissionEnd'])

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, data: dict) -> None:
        for ext in server.extensions.values():
            if ext.is_running():
                await ext.shutdown(data)

    @event(name="onShutdown")
    async def onShutdown(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onShutdown' in config:
            await self.run(server, config['onShutdown'])

    @chat_command(name="preset", aliases=["presets"], roles=['DCS Admin'], usage="<preset>",
                  help="load a specific weather preset")
    async def preset(self, server: Server, player: Player, params: list[str]):
        config = self.plugin.get_config(server)
        if config and 'presets' in config:
            presets = list(config['presets'].keys())
            if not params:
                message = 'The following presets are available:\n'
                for i in range(0, len(presets)):
                    preset = presets[i]
                    message += f"{i + 1} {preset}\n"
                message += f"\nUse {self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}preset <number> to load that preset " \
                           f"(mission will be restarted!)"
                player.sendUserMessage(message, 30)
            else:
                n = int(params[0]) - 1
                self.bot.loop.call_soon(asyncio.create_task, self.process(server,
                                                                          {
                                                                               "command": "preset",
                                                                               "preset": [presets[n]],
                                                                               "user": player.member
                                                                           }))
        else:
            player.sendChatMessage(f"There are no presets available to select.")

    @chat_command(name="maintenance", aliases=["maint"], roles=['DCS Admin'], help="enable maintenance mode")
    async def maintenance(self, server: Server, player: Player, params: list[str]):
        if not server.maintenance:
            server.maintenance = True
            server.restart_pending = False
            server.on_empty.clear()
            server.on_mission_end.clear()
            player.sendChatMessage('Maintenance mode enabled.')
            await self.bot.audit("set maintenance flag", user=player.member, server=server)
        else:
            player.sendChatMessage('Maintenance mode is already active.')

    @chat_command(name="clear", roles=['DCS Admin'], help="disable maintenance mode")
    async def clear(self, server: Server, player: Player, params: list[str]):
        if server.maintenance:
            server.maintenance = False
            player.sendChatMessage('Maintenance mode disabled/cleared.')
            await self.bot.audit("cleared maintenance flag", user=player.member, server=server)
        else:
            player.sendChatMessage("Maintenance mode wasn't enabled.")
