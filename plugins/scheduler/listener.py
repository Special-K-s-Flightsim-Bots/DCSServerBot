import asyncio

from core import EventListener, utils, Server, Player, event, chat_command, Status, get_translation
from croniter import croniter
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .commands import Scheduler

_ = get_translation(__name__.split('.')[1])


class SchedulerListener(EventListener["Scheduler"]):

    async def get_next_restart(self, server: Server, restart: dict | list) -> tuple[int, dict] | None:
        # do not calculate the restart time if there is no mission running
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
                if 'mission_id' in restart:
                    mission_id = restart['mission_id']
                elif 'mission_file' in restart:
                    mission_id = await self.plugin.get_mission_from_list(server, restart['mission_file'])
                else:
                    return None
                if not mission_id:
                    return None

                mission_list = await server.getMissionList()
                if server.status in [Status.RUNNING, Status.PAUSED] and server.current_mission:
                    new_mission_file = mission_list[mission_id - 1]
                    if new_mission_file == server.current_mission.filename:
                        return None

            if server.is_populated():
                mission_time: int | None = restart.get('max_mission_time', restart.get('mission_time'))
            else:
                mission_time: int | None = restart.get('mission_time')
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
            elif 'times' in restart:
                config = self.get_config(server)
                tz = (
                    ZoneInfo(config['timezone'])
                    if 'timezone' in config
                    else None
                )
                min_time_difference = 86400
                for t in restart['times']:
                    restart_time = utils.parse_time(t, tz=tz)
                    check_time = datetime.now(tz=tz).replace(year=restart_time.year, month=restart_time.month,
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
            elif 'cron' in restart:
                now = datetime.now().replace(second=0, microsecond=0)
                tz: str | None = self.get_config(server).get('timezone')
                if tz:
                    tzinfo = ZoneInfo(tz)
                    now = now.replace(tzinfo=tzinfo)

                cron_job = croniter(restart['cron'], now)
                next_date = cron_job.get_next(datetime)
                return int((next_date - now).total_seconds()), restart

            return None

    async def run(self, server: Server, method: dict | str, **kwargs) -> None:
        if isinstance(method, dict):
            if 'sleep' in method:
                await asyncio.sleep(method['sleep'])
            method = method['command']

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

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if data['channel'].startswith('sync-'):
            asyncio.create_task(self.set_restart_time(server))

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        action: dict | None = self.get_config(server).get('action')
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
        player: Player | None = server.get_player(ucid=data['ucid'])
        if player:
            asyncio.create_task(player.sendChatMessage(
                "Server will restart {}".format(restart_time)))

    @event(name="onSimulationResume")
    async def onSimulationResume(self, server: Server, _data: dict) -> None:
        asyncio.create_task(self.set_restart_time(server))

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        if data['eventName'] == 'mission_end':
            if data['arg1'] != 'TODO':
                asyncio.create_task(self.bus.send_to_node({
                    "command": "onMissionEnd",
                    "arg1": data['arg1'],
                    "arg2": data['arg2'],
                    "server_name": server.name
                }))
                if server.on_coalition_win:
                    self.bot.loop.call_soon(asyncio.create_task,
                                            self.plugin.run_action(server, server.on_coalition_win.copy()))
                    server.on_coalition_win.clear()
            else:
                asyncio.create_task(self.bus.send_to_node({
                    "command": "onServerStop",
                    "server_name": server.name
                }))
            if server.on_mission_end:
                self.bot.loop.call_soon(asyncio.create_task, self.plugin.run_action(server, server.on_mission_end.copy()))
                server.on_mission_end.clear()

    @event(name="onMissionLoadBegin")
    async def onMissionLoadBegin(self, server: Server, _data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config or 'onSimulationStart' not in config:
            return

        rconf = config['onSimulationStart']
        sleep = rconf.get('sleep', 0) if isinstance(rconf, dict) else 0

        # if the server is going to run, we do not need to smooth pause
        if server.settings.get('advanced', {}).get('resume_mode', 0) == 1:
            return

        # make sure that smooth_pause is enabled and set high enough to allow our script to be loaded
        smooth_pause = server.locals.get('smooth_pause', 0)
        if smooth_pause < (sleep + 10):
            server.locals['smooth_pause'] = sleep + 10
            self.log.info(f"Scheduler: Adjusted smooth_pause to {sleep + 10}s to allow the command.")

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, _data: dict) -> None:
        # invalidate the config cache
        self.plugin.get_config(server, use_cache=False)
        server.restart_pending = False
        server.on_empty.clear()
        server.on_mission_end.clear()
        server.on_coalition_win.clear()
        asyncio.create_task(self.set_restart_time(server))

    @event(name="onMissionEnd")
    async def onMissionEnd(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onMissionEnd' in config:
            asyncio.create_task(self.run(server, config['onMissionEnd'], winner=data['arg1'], msg=data['arg2']))

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, _data: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onSimulationStart' in config:
            asyncio.create_task(self.run(server, config['onSimulationStart']))

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, _data: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onSimulationStop' in config:
            asyncio.create_task(self.run(server, config['onSimulationStop']))

    @event(name="onShutdown")
    async def onShutdown(self, server: Server, _data: dict) -> None:
        config = self.plugin.get_config(server)
        if config and 'onShutdown' in config:
            asyncio.create_task(self.run(server, config['onShutdown']))

    async def _restart_server(self, server: Server):
        server.maintenance = True
        await self.plugin.teardown_dcs(server)
        await self.plugin.launch_dcs(server, modify_mission=False, use_orig=False)
        server.maintenance = False

    @event(name="restartServer")
    async def restartServer(self, server: Server, _data: dict) -> None:
        asyncio.create_task(self._restart_server(server))

    async def set_restart_time(self, server: Server) -> None:
        config = self.get_config(server)
        if not config or not server.current_mission:
            return
        action: dict | None = config.get('action')
        if not action:
            return
        result = await self.get_next_restart(server, action)
        if result:
            server.restart_time = datetime.now(tz=timezone.utc) + timedelta(seconds=result[0])

    @event(name="getMissionUpdate")
    async def getMissionUpdate(self, server: Server, _data: dict) -> None:
        asyncio.create_task(self.set_restart_time(server))

    @event(name="onServerEmpty")
    async def onServerEmpty(self, server: Server, _data: dict) -> None:
        if server.on_empty:
            self.log.debug(f"Scheduler: onServerEmpty: processing on_empty event: {server.on_empty['method']}")
            asyncio.create_task(self.plugin.run_action(server, server.on_empty.copy()))
            server.on_empty.clear()
        else:
            self.log.debug("Scheduler: onServerEmpty: no on_empty event provided - skipping")

    @chat_command(name="maintenance", aliases=["maint"], roles=['DCS Admin'], help=_("enable maintenance mode"))
    async def maintenance(self, server: Server, player: Player, _params: list[str]):
        if not server.maintenance:
            server.maintenance = True
            server.restart_pending = False
            server.on_empty.clear()
            server.on_mission_end.clear()
            server.on_coalition_win.clear()
            await player.sendChatMessage(_('Maintenance mode enabled.'))
            await self.bot.audit("set maintenance flag", user=player.member or player.ucid, server=server)
        else:
            await player.sendChatMessage(_('Maintenance mode is already active.'))

    @chat_command(name="clear", roles=['DCS Admin'], help=_("disable maintenance mode"))
    async def clear(self, server: Server, player: Player, _params: list[str]):
        if server.maintenance:
            server.maintenance = False
            await player.sendChatMessage(_('Maintenance mode disabled.'))
            await self.bot.audit("cleared maintenance flag", user=player.member or player.ucid, server=server)
        else:
            await player.sendChatMessage(_("Maintenance mode wasn't enabled."))

    @chat_command(name="timeleft", help=_("Time to the next restart"))
    async def timeleft(self, server: Server, player: Player, _params: list[str]):
        action: dict | None = self.get_config(server).get('action')
        if not action:
            await player.sendChatMessage(_("No restart configured for this server."))
            return
        elif server.maintenance:
            await player.sendChatMessage(_("Maintenance mode active, mission will not restart."))
            return
        elif not server.restart_time:
            await player.sendChatMessage(_("Please try again in a minute."))
            return
        restart_in, rconf = await self.get_next_restart(server, action)
        what = rconf['method']
        if what == 'shutdown' or rconf.get('shutdown', False):
            item = _('The server')
        else:
            item = _('The mission')
        when1 = _("in {}").format(utils.format_time(restart_in))
        if not rconf.get('populated', True) and not rconf.get('max_mission_time'):
            when2 = _(", if all players have left")
        else:
            when2 = ""

        message = _("{item} will {what} {when1}{when2}").format(item=item, what=_(what), when1=when1, when2=when2)
        await player.sendChatMessage(message)
