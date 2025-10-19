import asyncio
import os
import random

from core import EventListener, utils, Server, Player, Status, event, chat_command
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Scheduler


class SchedulerListener(EventListener["Scheduler"]):

    async def get_next_restart(self, server: Server, restart: dict | list) -> tuple[int, dict] | None:
        # do not calculate the restart time, if there is no mission running
        if not server.current_mission:
            return None

        if isinstance(restart, list):
            results: list[tuple[int, dict]] = []
            for r in restart:
                result = await self.get_next_restart(server, r)
                if result:
                    results.append(result)
            return min(results, key=lambda x: x[0]) if results else None
        else:
            if 'method' not in restart:
                self.log.error("Restart structure without 'method' provided: {}".format(repr(restart)))
                return None
            # check no_reload
            if restart['method'] == 'load' and restart.get('no_reload', False):
                mission_id = restart.get('mission_id')
                if not mission_id:
                    mission_id = await self.plugin.get_mission_from_list(server, restart.get('mission_file'))
                if not mission_id or mission_id == server.settings.get('listStartIndex', 1):
                    return None

            if server.is_populated():
                mission_time = restart.get('max_mission_time', restart.get('mission_time'))
            else:
                mission_time = restart.get('mission_time')
            if mission_time:
                delta = mission_time * 60 - int(server.current_mission.mission_time)
                if delta >= 0:
                    return delta, restart
                else:
                    return 0, restart
            elif 'real_time' in restart:
                delta = restart['real_time'] * 60 - int(server.current_mission.real_time)
                if delta >= 0:
                    return delta, restart
                else:
                    return 0, restart
            elif 'idle_time' in restart and server.idle_since:
                delta = restart['idle_time'] * 60 - int((datetime.now(timezone.utc) - server.idle_since).total_seconds())
                if delta >= 0:
                    return delta, restart
                else:
                    return 0, restart
            elif 'local_times' in restart:
                min_time_difference = 86400
                for t in restart['local_times']:
                    restart_time = utils.parse_time(t)
                    check_time = datetime.now().replace(year=restart_time.year, month=restart_time.month,
                                                        day=restart_time.day, second=0, microsecond=0)
                    if restart_time <= check_time:
                        restart_time += timedelta(days=1)
                    time_difference_in_seconds = int((restart_time - check_time).total_seconds())
                    if 0 < time_difference_in_seconds < min_time_difference:
                        min_time_difference = time_difference_in_seconds
                if min_time_difference != 86400:
                    return min_time_difference, restart
                else:
                    return None
            elif 'utc_times' in restart:
                min_time_difference = 86400
                for t in restart['utc_times']:
                    restart_time = utils.parse_time(t, tz=timezone.utc)
                    check_time = datetime.now(tz=timezone.utc).replace(
                        year=restart_time.year, month=restart_time.month, day=restart_time.day, second=0, microsecond=0)
                    if restart_time <= check_time:
                        restart_time += timedelta(days=1)
                    time_difference_in_seconds = int((restart_time - check_time).total_seconds())
                    if 0 < time_difference_in_seconds < min_time_difference:
                        min_time_difference = time_difference_in_seconds
                if min_time_difference != 86400:
                    return min_time_difference, restart
                else:
                    return None
            return None

    async def run(self, server: Server, method: str, **kwargs) -> None:
        if method.startswith('load:'):
            await server.send_to_dcs({
                "command": "do_script_file",
                "file": method[5:].strip().replace('\\', '/')
            })
        elif method.startswith('lua:'):
            await server.send_to_dcs({
                "command": "do_script",
                "script": method[4:].strip()
            })
        elif method.startswith('call:'):
            await server.send_to_dcs({
                "command": method[5:].strip()
            })
        elif method.startswith('run:'):
            cmd = method[4:].strip()
            cmd = utils.format_string(cmd, server=server, **kwargs)
            asyncio.create_task(self.node.shell_command(cmd))

    async def process(self, server: Server, what: dict) -> None:
        if 'shutdown' in what['command'] or what.get('shutdown', False):
            await server.shutdown()
            message = 'shut down DCS server'
            if 'user' not in what:
                message = self.plugin_name.title() + ' ' + message
            asyncio.create_task(self.bot.audit(message, server=server, user=what.get('user')))
        if 'restart' in what['command']:
            run_extensions = what.get('run_extensions', True)
            use_orig = what.get('use_orig', True)
            if server.status == Status.SHUTDOWN:
                await self.plugin.launch_dcs(server, modify_mission=run_extensions, use_orig=use_orig)
            else:
                await server.restart(modify_mission=run_extensions, use_orig=use_orig)
                message = f'restarted mission {server.current_mission.display_name}'
                if 'user' not in what:
                    message = self.plugin_name.title() + ' ' + message
                asyncio.create_task(self.bot.audit(message, server=server, user=what.get('user')))
        elif what['command'] == 'rotate':
            run_extensions = what.get('run_extensions', True)
            use_orig = what.get('use_orig', True)
            await server.loadNextMission(modify_mission=run_extensions, use_orig=use_orig)
            asyncio.create_task(self.bot.audit(f"{self.plugin_name.title()} rotated to mission "
                                               f"{server.current_mission.display_name}", server=server))
        elif what['command'] == 'stop':
            await server.stop()
            asyncio.create_task(self.bot.audit(f"{self.plugin_name.title()} stopped server", server=server))
        elif what['command'] == 'load':
            run_extensions = what.get('run_extensions', True)
            use_orig = what.get('use_orig', True)
            if 'mission_id' in what:
                _mission = what['mission_id']
                if isinstance(_mission, list):
                    _mission = random.choice(_mission)
            elif 'mission_file' in what:
                _mission = what['mission_file']
                if isinstance(_mission, list):
                    _mission = random.choice(_mission)
                if not os.path.isabs(_mission):
                    _mission = os.path.join(await server.get_missions_dir(), _mission)
                if not os.path.exists(_mission):
                    self.log.error(f"Mission file {_mission} not found.")
                    return
            else:
                self.log.error(f"No mission_id or mission_file specified in {what}")
                return
            rc = await server.loadMission(_mission, modify_mission=run_extensions, use_orig=use_orig,
                                          no_reload=what.get('no_reload', False))
            if rc is False:
                self.log.warning(f"Mission {_mission} NOT loaded.")
            elif rc is None:
                self.log.debug(f'Mission {_mission} was already loaded')
            else:
                message = f'loaded mission {server.current_mission.display_name}'
                if 'user' not in what:
                    message = self.plugin_name.title() + ' ' + message
                asyncio.create_task(self.bot.audit(message, server=server, user=what.get('user')))
        elif what['command'] == 'preset':
            if not server.locals.get('mission_rewrite', True):
                await server.stop()
            filename = await server.get_current_mission_file()
            use_orig = what.get('use_orig', True)
            new_filename = await server.modifyMission(
                filename,
                [utils.get_preset(self.node, x) for x in what['preset']],
                use_orig=use_orig
            )
            if new_filename != filename:
                self.log.info(f"  => New mission written: {new_filename}")
                await server.replaceMission(int(server.settings['listStartIndex']), new_filename)
            else:
                self.log.info(f"  => Mission {filename} overwritten.")
            if server.status == Status.STOPPED:
                await server.start()
        server.restart_pending = False

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if data['channel'].startswith('sync-'):
            asyncio.create_task(self.set_restart_time(server))

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        action = self.get_config(server).get('action')
        if action:
            result = await self.get_next_restart(server, action)
            # do not print any chat message when the server is set to restart on populated = False
            if not result or not result[1].get('populated', True):
                return
            restart_time = f"in {utils.format_time(result[0])}"
        elif server.restart_pending:
            restart_time = 'soon!'
        else:
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if player:
            asyncio.create_task(player.sendChatMessage(
                "Server will restart {}".format(restart_time)))

    @event(name="onSimulationResume")
    async def onSimulationResume(self, server: Server, _: dict) -> None:
        asyncio.create_task(self.set_restart_time(server))

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'mission_end':
            if data['arg1'] != 'TODO':
                asyncio.create_task(self.bot.bus.send_to_node({
                    "command": "onMissionEnd",
                    "arg1": data['arg1'],
                    "arg2": data['arg2'],
                    "server_name": server.name
                }))
            else:
                asyncio.create_task(self.bot.bus.send_to_node({
                    "command": "onServerStop",
                    "server_name": server.name
                }))
            if server.on_mission_end:
                self.bot.loop.call_soon(asyncio.create_task, self.process(server, server.on_mission_end.copy()))
                server.on_mission_end.clear()

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, _: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onSimulationStart' in config:
            asyncio.create_task(self.run(server, config['onSimulationStart']))

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, _: dict) -> None:
        # invalidate the config cache
        self.plugin.get_config(server, use_cache=False)
        server.restart_pending = False
        server.on_empty.clear()
        server.on_mission_end.clear()
        asyncio.create_task(self.set_restart_time(server))

    @event(name="onMissionEnd")
    async def onMissionEnd(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onMissionEnd' in config:
            asyncio.create_task(self.run(server, config['onMissionEnd'], winner=data['arg1'], msg=data['arg2']))

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, _: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onSimulationStop' in config:
            asyncio.create_task(self.run(server, config['onSimulationStop']))

    @event(name="onShutdown")
    async def onShutdown(self, server: Server, _: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onShutdown' in config:
            asyncio.create_task(self.run(server, config['onShutdown']))

    async def _restart_server(self, server: Server):
        server.maintenance = True
        await self.plugin.teardown_dcs(server)
        await self.plugin.launch_dcs(server, modify_mission=False, use_orig=False)
        server.maintenance = False

    @event(name="restartServer")
    async def restartServer(self, server: Server, _: dict) -> None:
        asyncio.create_task(self._restart_server(server))

    async def set_restart_time(self, server: Server) -> None:
        config = self.get_config(server)
        if not config or not server.current_mission:
            return
        action = config.get('action')
        if not action:
            return
        result = await self.get_next_restart(server, action)
        if result:
            server.restart_time = datetime.now(tz=timezone.utc) + timedelta(seconds=result[0])

    @event(name="getMissionUpdate")
    async def getMissionUpdate(self, server: Server, _: dict) -> None:
        asyncio.create_task(self.set_restart_time(server))

    @event(name="onServerEmpty")
    async def onServerEmpty(self, server: Server, _: dict) -> None:
        if server.on_empty:
            self.log.debug(f"Scheduler: onServerEmpty: processing on_empty event: {server.on_empty['command']}")
            asyncio.create_task(self.process(server, server.on_empty.copy()))
            server.on_empty.clear()
        else:
            self.log.debug("Scheduler: onServerEmpty: no on_empty event provided - skipping")

    @chat_command(name="maintenance", aliases=["maint"], roles=['DCS Admin'], help="enable maintenance mode")
    async def maintenance(self, server: Server, player: Player, _: list[str]):
        if not server.maintenance:
            server.maintenance = True
            server.restart_pending = False
            server.on_empty.clear()
            server.on_mission_end.clear()
            await player.sendChatMessage('Maintenance mode enabled.')
            await self.bot.audit("set maintenance flag", user=player.member or player.ucid, server=server)
        else:
            await player.sendChatMessage('Maintenance mode is already active.')

    @chat_command(name="clear", roles=['DCS Admin'], help="disable maintenance mode")
    async def clear(self, server: Server, player: Player, _: list[str]):
        if server.maintenance:
            server.maintenance = False
            await player.sendChatMessage('Maintenance mode disabled/cleared.')
            await self.bot.audit("cleared maintenance flag", user=player.member or player.ucid, server=server)
        else:
            await player.sendChatMessage("Maintenance mode wasn't enabled.")

    @chat_command(name="timeleft", help="Time to the next restart")
    async def timeleft(self, server: Server, player: Player, params: list[str]):
        action = self.get_config(server).get('action')
        if not action:
            await player.sendChatMessage("No action configured for this server.")
            return
        elif server.maintenance:
            await player.sendChatMessage("Maintenance mode active, mission will not restart.")
            return
        elif not server.restart_time:
            await player.sendChatMessage("Please try again in a minute.")
            return
        restart_in, rconf = await self.get_next_restart(server, action)
        message = f"The mission will restart in {utils.format_time(restart_in)}"
        if not rconf.get('populated', True) and not rconf.get('max_mission_time'):
            message += ", if all players have left"
        await player.sendChatMessage(message)
