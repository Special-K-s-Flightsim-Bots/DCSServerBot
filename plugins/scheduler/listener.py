import asyncio
from core import EventListener, utils, Server, Player, Status, event, chat_command
from os import path


class SchedulerListener(EventListener):

    async def run(self, server: Server, method: str) -> None:
        if method.startswith('load:'):
            server.send_to_dcs({
                "command": "do_script_file",
                "file": method[5:].strip().replace('\\', '/')
            })
        elif method.startswith('lua:'):
            server.send_to_dcs({
                "command": "do_script",
                "script": method[4:].strip()
            })
        elif method.startswith('call:'):
            server.send_to_dcs({
                "command": method[5:].strip()
            })
        elif method.startswith('run:'):
            cmd = method[4:].strip()
            dcs_installation = path.normpath(path.expandvars(self.bot.node.locals['DCS']['installation']))
            dcs_home = path.normpath(server.instance.home)
            cmd = utils.format_string(cmd, dcs_installation=dcs_installation, dcs_home=dcs_home, server=server)
            if server.is_remote:
                self.bot.bus.send_to_node({
                    "command": "rpc",
                    "object": "Node",
                    "method": "shell_command",
                    "params": {
                        "cmd": cmd
                    }
                }, node=server.node.name)
            else:
                self.log.debug('Running shell-command: ' + cmd)
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
                await server.apply_mission_changes()
                await server.start()
                message = 'started DCS server'
                if 'user' not in what:
                    message = self.plugin_name.title() + ' ' + message
                await self.bot.audit(message, server=server, user=what.get('user'))
            elif server.status in [Status.RUNNING, Status.PAUSED]:
                await server.restart(smooth=await server.apply_mission_changes())
                message = f'restarted mission {server.current_mission.display_name}'
                if 'user' not in what:
                    message = self.plugin_name.title() + ' ' + message
                await self.bot.audit(message, server=server, user=what.get('user'))
        elif what['command'] == 'rotate':
            await server.loadNextMission()
            if await server.apply_mission_changes():
                await server.restart(smooth=True)
            await self.bot.audit(f"{self.plugin_name.title()} rotated to mission "
                                 f"{server.current_mission.display_name}", server=server)
        elif what['command'] == 'load':
            await server.loadMission(what['id'])
            message = f'loaded mission {server.current_mission.display_name}'
            if 'user' not in what:
                message = self.plugin_name.title() + ' ' + message
            await self.bot.audit(message, server=server, user=what['user'] if 'user' in what else None)
        server.restart_pending = False

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        # init and start extensions if necessary
        await server.init_extensions()
        await server.startup_extensions()

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        if server.restart_pending:
            player: Player = server.get_player(id=data['id'])
            player.sendChatMessage("*** Mission is about to be restarted soon! ***")

#    @event(name="onPlayerChangeSlot")
#    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
#        if not server.is_populated() and server.on_empty:
#           self.bot.loop.call_soon(asyncio.create_task, self.process(server, server.on_empty.copy()))
#            server.on_empty.clear()

    @event(name="onSimulationPause")
    async def onSimulationPause(self, server: Server, data: dict) -> None:
        if server.on_empty:
            self.bot.loop.call_soon(asyncio.create_task, self.process(server, server.on_empty.copy()))
            server.on_empty.clear()

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'disconnect':
            if not server.is_populated() and server.on_empty:
                self.bot.loop.call_soon(asyncio.create_task, self.process(server, server.on_empty.copy()))
                server.on_empty.clear()
        elif data['eventName'] == 'mission_end':
            self.bot.bus.send_to_node({"command": "onMissionEnd", "server_name": server.name})
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
        # invalidate the config cache
        self.plugin.get_config(server, use_cache=False)
        server.restart_pending = False
        server.on_empty.clear()
        server.on_mission_end.clear()

    @event(name="onMissionEnd")
    async def onMissionEnd(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onMissionEnd' in config:
            await self.run(server, config['onMissionEnd'])

    @event(name="onShutdown")
    async def onShutdown(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onShutdown' in config:
            await self.run(server, config['onShutdown'])

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
