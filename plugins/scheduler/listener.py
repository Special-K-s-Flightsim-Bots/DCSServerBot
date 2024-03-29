import asyncio
import os

from core import EventListener, utils, Server, Player, Status, event, chat_command


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
            dcs_installation = os.path.normpath(os.path.expandvars(self.node.locals['DCS']['installation']))
            dcs_home = os.path.normpath(server.instance.home)
            cmd = utils.format_string(cmd, dcs_installation=dcs_installation, dcs_home=dcs_home, server=server)
            # noinspection PyAsyncCall
            asyncio.create_task(self.node.shell_command(cmd))

    async def process(self, server: Server, what: dict) -> None:
        if 'shutdown' in what['command']:
            await server.shutdown()
            message = 'shut down DCS server'
            if 'user' not in what:
                message = self.plugin_name.title() + ' ' + message
            # noinspection PyAsyncCall
            asyncio.create_task(self.bot.audit(message, server=server, user=what.get('user')))
        if 'restart' in what['command']:
            if server.status == Status.SHUTDOWN:
                # noinspection PyUnresolvedReferences
                await self.plugin.launch_dcs(server)
            else:
                await server.restart()
                message = f'restarted mission {server.current_mission.display_name}'
                if 'user' not in what:
                    message = self.plugin_name.title() + ' ' + message
                # noinspection PyAsyncCall
                asyncio.create_task(self.bot.audit(message, server=server, user=what.get('user')))
        elif what['command'] == 'rotate':
            await server.loadNextMission()
            # noinspection PyAsyncCall
            asyncio.create_task(self.bot.audit(f"{self.plugin_name.title()} rotated to mission "
                                               f"{server.current_mission.display_name}", server=server))
        elif what['command'] == 'load':
            await server.loadMission(what['id'])
            message = f'loaded mission {server.current_mission.display_name}'
            if 'user' not in what:
                message = self.plugin_name.title() + ' ' + message
            # noinspection PyAsyncCall
            asyncio.create_task(self.bot.audit(message, server=server, user=what.get('user')))
        elif what['command'] == 'preset':
            if not server.node.config.get('mission_rewrite', True):
                await server.stop()
            filename = await server.get_current_mission_file()
            new_filename = await server.modifyMission(filename,
                                                      [utils.get_preset(self.node, x) for x in what['preset']])
            if new_filename != filename:
                self.log.info(f"  => New mission written: {new_filename}")
                await server.replaceMission(int(server.settings['listStartIndex']), new_filename)
            else:
                self.log.info(f"  => Mission {filename} overwritten.")
            if server.status == Status.STOPPED:
                await server.start()
        server.restart_pending = False

    async def _init_extensions(self, server: Server, data: dict) -> None:
        try:
            # init and start extensions if necessary
            if data['channel'].startswith('sync-'):
                await server.init_extensions()
            await server.startup_extensions()
        except (TimeoutError, asyncio.TimeoutError):
            self.log.error(f"Timeout while loading extensions for server {server.name}!")

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        # noinspection PyAsyncCall
        asyncio.create_task(self._init_extensions(server, data))

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        if server.restart_pending:
            player: Player = server.get_player(ucid=data['ucid'])
            player.sendChatMessage("*** Mission is about to be restarted soon! ***")

#    @event(name="onPlayerChangeSlot")
#    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
#        if not server.is_populated() and server.on_empty:
#           self.bot.loop.call_soon(asyncio.create_task, self.process(server, server.on_empty.copy()))
#            server.on_empty.clear()

    @event(name="onSimulationPause")
    async def onSimulationPause(self, server: Server, _: dict) -> None:
        if server.on_empty:
            # noinspection PyAsyncCall
            asyncio.create_task(self.process(server, server.on_empty.copy()))
            server.on_empty.clear()

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'disconnect':
            if not server.is_populated() and server.on_empty:
                # noinspection PyAsyncCall
                asyncio.create_task(self.process(server, server.on_empty.copy()))
                server.on_empty.clear()
        elif data['eventName'] == 'mission_end':
            self.bot.bus.send_to_node({"command": "onMissionEnd", "server_name": server.name})
            if server.on_mission_end:
                self.bot.loop.call_soon(asyncio.create_task, self.process(server, server.on_mission_end.copy()))
                server.on_mission_end.clear()

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, _: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onMissionStart' in config:
            # noinspection PyAsyncCall
            asyncio.create_task(self.run(server, config['onMissionStart']))

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, _: dict) -> None:
        # invalidate the config cache
        self.plugin.get_config(server, use_cache=False)
        server.restart_pending = False
        server.on_empty.clear()
        server.on_mission_end.clear()

    @event(name="onMissionEnd")
    async def onMissionEnd(self, server: Server, _: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onMissionEnd' in config:
            # noinspection PyAsyncCall
            asyncio.create_task(self.run(server, config['onMissionEnd']))

    async def _shutdown_extensions(self, server: Server) -> None:
        try:
            await server.shutdown_extensions()
        except (TimeoutError, asyncio.TimeoutError):
            self.log.error(f"Timeout while shutting down extensions for server {server.name}!")

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, _: dict) -> None:
        # noinspection PyAsyncCall
        asyncio.create_task(self._shutdown_extensions(server))

    @event(name="onShutdown")
    async def onShutdown(self, server: Server, _: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onShutdown' in config:
            # noinspection PyAsyncCall
            asyncio.create_task(self.run(server, config['onShutdown']))

    @chat_command(name="maintenance", aliases=["maint"], roles=['DCS Admin'], help="enable maintenance mode")
    async def maintenance(self, server: Server, player: Player, _: list[str]):
        if not server.maintenance:
            server.maintenance = True
            server.restart_pending = False
            server.on_empty.clear()
            server.on_mission_end.clear()
            player.sendChatMessage('Maintenance mode enabled.')
            # noinspection PyAsyncCall
            asyncio.create_task(self.bot.audit("set maintenance flag", user=player.member, server=server))
        else:
            player.sendChatMessage('Maintenance mode is already active.')

    @chat_command(name="clear", roles=['DCS Admin'], help="disable maintenance mode")
    async def clear(self, server: Server, player: Player, _: list[str]):
        if server.maintenance:
            server.maintenance = False
            player.sendChatMessage('Maintenance mode disabled/cleared.')
            # noinspection PyAsyncCall
            asyncio.create_task(self.bot.audit("cleared maintenance flag", user=player.member, server=server))
        else:
            player.sendChatMessage("Maintenance mode wasn't enabled.")
