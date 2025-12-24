import asyncio
import discord
import math
import os
import psycopg
import random

from contextlib import suppress
from core import (Plugin, PluginRequiredError, utils, Status, Server, Coalition, Channel, Group, Node, Instance,
                  DEFAULT_TAG, get_translation, TRAFFIC_LIGHTS)
from datetime import datetime, timedelta, timezone
from discord import app_commands, TextStyle
from discord.ext import tasks
from discord.ui import Modal, TextInput
from functools import partial
from pathlib import Path
from services.bot import DCSServerBot
from typing import Literal
from zoneinfo import ZoneInfo

from .listener import SchedulerListener
from .views import ConfigView

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])


class Scheduler(Plugin[SchedulerListener]):

    async def cog_load(self) -> None:
        await super().cog_load()
        self.check_state.start()

    async def cog_unload(self):
        self.check_state.cancel()
        await super().cog_unload()

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            config = {
                self.node.name: {name: {} for name in self.bus.node.instances}
            }
            os.makedirs(os.path.join(self.node.config_dir, 'plugins'), exist_ok=True)
            with open(os.path.join(self.node.config_dir, 'plugins', 'scheduler.yaml'), mode='w',
                      encoding='utf-8') as outfile:
                yaml.dump(config, outfile)
        return config

    async def migrate(self, new_version: str, conn: psycopg.AsyncConnection | None = None) -> None:
        def change_instance_3_1(instance: dict):
            if 'restart' in instance:
                instance['action'] = instance.pop('restart')
                if isinstance(instance['action'], list):
                    for action in instance['action']:
                        if action['method'] == 'restart_with_shutdown':
                            action['method'] = 'restart'
                            action['shutdown'] = True
                else:
                    if instance['action']['method'] == 'restart_with_shutdown':
                        instance['action']['method'] = 'restart'
                        instance['action']['shutdown'] = True

        def change_instance_3_2(instance: dict):
            if 'onMissionStart' in instance:
                instance['onSimulationStart'] = instance.pop('onMissionStart')
            if 'onMissionEnd' in instance:
                instance['onSimulationStop'] = instance.pop('onMissionEnd')

        def change_instance_3_3(instance: dict):
            if 'schedule' in instance:
                if 'timezone' in instance['schedule']:
                    instance['timezone'] = instance['schedule'].pop('timezone')
            if isinstance(instance.get('action'), dict):
                actions = [instance['action']]
            elif isinstance(instance.get('action'), list):
                actions = instance['action']
            else:
                return

            for action in actions:
                if 'local_times' in action:
                    action['times'] = action.pop('local_times')
                elif 'utc_times' in action:
                    instance['timezone'] = 'UTC'
                    action['times'] = action.pop('utc_times')

        def change_instance_3_4(instance: dict):
            if 'warn' in instance and 'text' in instance['warn']:
                instance['warn']['message'] = instance['warn'].pop('text')

        if new_version == '3.1':
            change_instance = change_instance_3_1
        elif new_version == '3.2':
            change_instance = change_instance_3_2
        elif new_version == '3.3':
            change_instance = change_instance_3_3
        elif new_version == '3.4':
            change_instance = change_instance_3_4
        else:
            return

        config = os.path.join(self.node.config_dir, 'plugins', f'{self.plugin_name}.yaml')
        data = yaml.load(Path(config).read_text(encoding='utf-8'))
        if self.node.name in data.keys():
            for name, node in data.items():
                if name == DEFAULT_TAG:
                    change_instance(node)
                    continue
                for instance in node.values():
                    change_instance(instance)
        else:
            for instance in data.values():
                change_instance(instance)
        with open(config, mode='w', encoding='utf-8') as outfile:
            yaml.dump(data, outfile)
        self.locals = self.read_locals()

    @staticmethod
    async def check_server_state(server: Server, config: dict) -> Status:
        """
        Return the status that the server should be in at the moment
        based on the schedule supplied in *config*.

        The logic follows the rules expressed in the original code,
        but the flow is now linear and easier to read.
        """
        # No schedule or the server is in maintenance → keep current status
        if not ('schedule' in config and not server.maintenance):
            return server.status

        # ------------------------------------------------------------------
        # 1.  Determine the time (in seconds) until the next restart
        # ------------------------------------------------------------------
        warn_times = (
            Scheduler.get_warn_times(config) if server.is_populated() else [0]
        )
        restart_in = max(warn_times)

        # ------------------------------------------------------------------
        # 2.  Prepare the datetimes that will be compared against the
        #     schedule time‑frames
        # ------------------------------------------------------------------
        now = datetime.now()
        tz = (
            ZoneInfo(config.get('timezone'))
            if 'timezone' in config
            else None
        )

        # Datetime used for the “restart‑in‑the‑future” checks
        now_plus_restart = now + timedelta(seconds=restart_in)

        # Day of week for the *future* moment when the restart would be required
        weekday = now_plus_restart.weekday()


        # ------------------------------------------------------------------
        # 3.  Walk through the schedule periods
        # ------------------------------------------------------------------
        for period, daystate in config['schedule'].items():
            # Validate that the daystate string is 7 characters long
            if len(daystate) != 7:
                server.log.error(
                    f"Error in scheduler.yaml: {daystate} has to be 7 characters long!"
                )
                break

            state = daystate[weekday].upper()  # one letter per weekday

            # --------------------------------------------------------------
            # RUNNING condition
            # --------------------------------------------------------------
            if (
                    utils.is_in_timeframe(now, period, tz)
                    and state == 'Y'
                    and server.status == Status.SHUTDOWN
            ):
                return Status.RUNNING

            # --------------------------------------------------------------
            # SHUTDOWN conditions
            # --------------------------------------------------------------
            if utils.is_in_timeframe(now, period, tz):
                # 1.  A populated‑but‑un‑running server that is marked 'P'
                if (
                        state == 'P'
                        and server.status in {Status.RUNNING, Status.PAUSED, Status.STOPPED}
                        and not server.is_populated()
                ):
                    return Status.SHUTDOWN

                # 2.  A paused or stopped server that is marked 'N'
                if state == 'N' and server.status in {Status.PAUSED, Status.STOPPED}:
                    return Status.SHUTDOWN

            # 3.  Restart has already happened, but the server is still running
            if (
                    utils.is_in_timeframe(now_plus_restart, period, tz)
                    and state == 'N'
                    and server.status == Status.RUNNING
            ):
                return Status.SHUTDOWN

        # ------------------------------------------------------------------
        # No rule matched → keep the current status
        # ------------------------------------------------------------------
        return server.status

    async def launch_dcs(self, server: Server, member: discord.Member | None = None, *,
                         modify_mission: bool | None = True, use_orig: bool | None = True,
                         ignore_exception: bool | None = False):
        self.log.info(f'  => DCS server "{server.name}" starting up ...')
        try:
            await server.startup(modify_mission=modify_mission, use_orig=use_orig)
            if not member:
                self.log.info(f'  => DCS server "{server.name}" started by '
                              f'{self.__cog_name__}.')
                await self.bot.audit(f"{self.__cog_name__} started DCS server", server=server)
            else:
                self.log.info(f'  => DCS server "{server.name}" started by '
                              f'{member.display_name}.')
                await self.bot.audit(f"started DCS server", user=member, server=server)
        except (TimeoutError, asyncio.TimeoutError):
            if server.status == Status.SHUTDOWN:
                self.log.warning(f'  => DCS server "{server.name}" was closed / crashed while launching!')
            else:
                self.log.warning(f'  => DCS server "{server.name}" timeout while launching.')
            if not ignore_exception:
                raise

    @staticmethod
    def get_warn_times(config: dict) -> list[int]:
        times = config.get('warn', {}).get('times', [0])
        if isinstance(times, list):
            return sorted(times, reverse=True)
        elif isinstance(times, dict):
            return sorted(times.keys(), reverse=True)
        return []

    async def warn_users(self, server: Server, config: dict, rconf: dict, max_warn_time: int | None = None):
        warn = config.get('warn', {})
        if not warn:
            return

        times: list | dict = warn.get('times', [0])
        if isinstance(times, list):
            warn_times = sorted(times, reverse=True)
            warn_text = warn.get('message', '!!! {item} will {what} in {when} !!!')
        elif isinstance(times, dict):
            warn_times = sorted(times.keys(), reverse=True)
        else:
            self.log.warning(f"{self.__cog_name__}: warn structure mangled in scheduler.yaml, no user warning!")
            return
        if max_warn_time is None:
            restart_in = max(warn_times)
        else:
            restart_in = max_warn_time
        self.log.debug(f"{self.__cog_name__}: Restart {server.name} in {restart_in} seconds...")

        action = rconf.get("method")
        item = "Server" if action in {"restart", "shutdown"} else "Mission"
        # If a restart is accompanied by a shutdown-flag, we still treat it as a server‑level action.
        if action == "restart" and rconf.get("shutdown", False):
            item = "Server"

        async def do_warn(warn_time: int):
            nonlocal warn_text

            sleep_time = restart_in - warn_time
            if sleep_time > 0 and server.restart_pending:
                await asyncio.sleep(sleep_time)
            if not server.restart_pending:
                return
            if server.status == Status.RUNNING:
                if isinstance(times, dict):
                    warn_text = times[warn_time]
                message = warn_text.format(item=item, what=action, when=utils.format_time(warn_time))
                await server.sendPopupMessage(Coalition.ALL, message, server.locals.get('message_timeout', 10))
                await server.sendChatMessage(Coalition.ALL, message)
                if 'sound' in warn:
                    await server.playSound(Coalition.ALL, utils.format_string(warn['sound'], time=warn_time))
            with suppress(Exception):
                events_channel = self.bot.get_channel(server.channels.get(Channel.EVENTS, -1))
                if events_channel:
                    await events_channel.send(warn_text.format(item=item, what=action,
                                                               when=utils.format_time(warn_time)))
            self.log.debug(f"{self.__cog_name__}: Warning for {server.name} @ {warn_time} fired.")

        tasks = [
            asyncio.create_task(do_warn(i))
            for i in warn_times
            if math.ceil(i/(60 if i >= 60 else 1)) <= math.ceil(restart_in/(60 if i >= 60 else 1))
        ]
        await utils.run_parallel_nofail(*tasks)

        if warn.get('countdown'):
            timer = min(restart_in, min(warn_times))
            countdown = warn['countdown'].get('time', 10)
            warn_text = warn.get('message', warn['countdown'].get('message', 0))
            while timer > countdown:
                await asyncio.sleep(1)
                timer -= 1
            while timer != 0:
                message = warn_text.format(item=item, what=action, when=utils.format_time(timer))
                await server.sendPopupMessage(Coalition.ALL, message, 1)
                await server.sendChatMessage(Coalition.ALL, message)
                await asyncio.sleep(1)
                timer -= 1

            # sleep until the countdown should happen
            await asyncio.sleep(min(restart_in, min(warn_times)))
        else:
            # sleep until the restart should happen
            await asyncio.sleep(min(restart_in, min(warn_times)))

    async def teardown_dcs(self, server: Server, member: discord.Member | None = None):
        await self.bot.bus.send_to_node({"command": "onShutdown", "server_name": server.name})
        await asyncio.sleep(1)
        await server.shutdown()
        if not member:
            self.log.info(
                f"  => DCS server \"{server.name}\" shut down by {self.__cog_name__}.")
            await self.bot.audit(f"{self.__cog_name__} shut down DCS server", server=server)
        else:
            self.log.info(
                f"  => DCS server \"{server.name}\" shut down by {member.display_name}.")
            await self.bot.audit(f"shut down DCS server", server=server, user=member)

    async def teardown(self, server: Server, config: dict):
        # if we should not restart a populated server, wait for it to be unpopulated
        populated = server.is_populated()
        if populated and not config.get('populated', True):
            return
        elif not server.restart_pending:
            server.restart_pending = True
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) else 0
            if restart_in > 0 and populated:
                self.log.info(f"  => DCS server \"{server.name}\" will be shut down "
                              f"by {self.__cog_name__} in {restart_in} seconds ...")
                await self.bot.audit(
                    f"{self.__cog_name__} will shut down DCS server in {utils.format_time(restart_in)}",
                    server=server)
                await self.warn_users(server, config, {"method": "shutdown"})
            # if the shutdown has been cancelled due to maintenance mode
            if not server.restart_pending:
                return
            try:
                await self.teardown_dcs(server)
            except (TimeoutError, asyncio.TimeoutError):
                self.log.warning(f"  => DCS server \"{server.name}\" timeout while shutting down. "
                                 f"Check the status manually.")
            server.restart_pending = False

    async def get_mission_from_list(self, server: Server, mission_file: list | str) -> int:
        if isinstance(mission_file, list):
            mission_file = random.choice(mission_file)
        filename = utils.format_string(mission_file, instance=server.instance, server=server)
        if not filename:
            self.log.error(
                "You need to provide either mission_id or mission_file to your load configuration!")
            return 0
        if not os.path.isabs(filename):
            filename = os.path.join(await server.get_missions_dir(), filename)
        for idx, mission in enumerate(await server.getMissionList()):
            if '.dcssb' in mission:
                secondary = mission
                primary = os.path.join(os.path.dirname(mission).replace('.dcssb', ''),
                                       os.path.basename(mission))
            else:
                primary = mission
                secondary = os.path.join(os.path.dirname(mission), '.dcssb', os.path.basename(mission))
            if os.path.normpath(filename).lower() in [
                os.path.normpath(primary).lower(),
                os.path.normpath(secondary).lower()
            ]:
                return idx + 1
        else:
            return 0

    async def _run_with_presets(self, server: Server, rconf: dict, new_mission: int, is_running_mission: bool):
        # get config
        use_orig = rconf.get('use_orig', True)

        # stop the server if the running mission should be changed
        if (is_running_mission and server.status in [Status.RUNNING, Status.PAUSED] and
                not server.locals.get('mission_rewrite', True)):
            await server.stop()
        filename = (await server.getMissionList())[new_mission - 1]
        preset_files = rconf.get('presets', os.path.join(self.node.config_dir, 'presets.yaml'))
        new_filename = await server.modifyMission(
            filename,
            [utils.get_preset(self.node, x, preset_files) for x in rconf['settings']],
            use_orig=use_orig
        )
        if new_filename != filename:
            self.log.info(f"{self.__cog_name__}: New mission written: {new_filename}")
            await server.replaceMission(new_mission, new_filename)
        else:
            self.log.info(f"{self.__cog_name__}: Mission {filename} overwritten.")

        # start the server again
        if server.status == Status.STOPPED:
            await server.start()
        elif server.status in [Status.SHUTDOWN, Status.LOADING]:
            self.log.debug(f"{self.__cog_name__}: Starting server {server.name}")
            await server.startup(modify_mission=False, use_orig=False)
        else:
            await server.loadMission(
                new_filename,
                modify_mission=False,
                use_orig=False
            )

    async def _run_without_presets(self, server: Server, rconf: dict, new_mission: int):
        # get config
        modify_mission = rconf.get('run_extensions', True)
        use_orig = rconf.get('use_orig', True)
        no_reload = rconf.get('no_reload', False)

        if server.status in [Status.SHUTDOWN, Status.LOADING]:
            self.log.debug(f"{self.__cog_name__}: Starting server {server.name}")
            await server.startup(modify_mission=modify_mission, use_orig=use_orig)
        else:
            rc = await server.loadMission(
                new_mission,
                modify_mission=modify_mission,
                use_orig=use_orig,
                no_reload=no_reload
            )
            if rc is False:
                self.log.error(f"Mission {new_mission} not loaded on server {server.name}")
                return
            elif rc is None:
                self.log.debug(f"Mission {new_mission} already loaded on server {server.name}")
                return

    async def run_action(self, server: Server, rconf: dict):
        method = rconf['method']
        mission_list = await server.getMissionList()

        if method == 'shutdown' or rconf.get('shutdown', False):
            self.log.debug(f"{self.__cog_name__}: Shutting down server {server.name} ...")
            await self.teardown_dcs(server)
            if method == 'shutdown':
                return

        if method == 'stop':
            self.log.debug(f"{self.__cog_name__}: Stopping server {server.name} ...")
            await server.stop()
            await self.bot.audit(f"{self.__cog_name__} stopped DCS Server {server.name}",
                                 server=server)
            return

        # select the new mission id
        if method == 'load':
            new_mission = rconf.get('mission_id')
            if isinstance(new_mission, list):
                new_mission = random.choice(new_mission)
            elif not new_mission:
                new_mission = await self.get_mission_from_list(server, rconf.get('mission_file'))
                if not new_mission:
                    self.log.error(f"{self.__cog_name__}: "
                                   f"Mission {rconf['mission_file']} cannot be loaded on server {server.name}!")
                    return
        else:
            new_mission = int(server.settings.get('listStartIndex', 1))
            if method == 'rotate':
                new_mission += 1
                if new_mission > len(mission_list):
                    new_mission = 1

        # do we change the running mission?
        if server.status in [Status.RUNNING, Status.PAUSED] and server.current_mission:
            new_mission_file = mission_list[new_mission - 1]
            is_running_mission = (new_mission_file == server.current_mission.filename)
        else:
            is_running_mission = False

        # set the new start index if the server is shut down
        if server.status == Status.SHUTDOWN:
            await server.setStartIndex(new_mission)

        # apply presets if configured
        if rconf.get('settings'):
            await self._run_with_presets(server, rconf, new_mission, is_running_mission)
        else:
            await self._run_without_presets(server, rconf, new_mission)

        mission_name = server.current_mission.display_name if server.current_mission else ""
        await self.bot.audit(
            f"{self.__cog_name__} {method.rstrip('e')}ed mission {mission_name}",
            server=server,
            user=rconf.get('user')
        )

    async def handle_action(self, server: Server, config: dict, rconf: dict, max_warn_time: int):
        # a restart is already pending, nothing more to do
        if server.restart_pending:
            return

        self.log.debug(f"{self.__cog_name__}: handle_action(server={server.name}, method={rconf['method']}) triggered.")
        # shall we do something at the mission end only?
        if rconf.get('mission_end', False):
            self.log.debug(f"{self.__cog_name__}: setting mission_end trigger (server={server.name}).")
            server.on_mission_end = rconf.copy()
            server.restart_pending = True
            return

        # check if the server is populated
        if server.is_populated():
            self.log.debug(f"{self.__cog_name__}: Server {server.name} is populated.")
            # max_mission_time overrides "populated: false"
            if not rconf.get('populated', True) and not rconf.get('max_mission_time'):
                if not server.on_empty:
                    self.log.debug(f"{self.__cog_name__}: Setting on_empty trigger in server {server.name}.")
                    server.on_empty = rconf.copy()
                server.restart_pending = True
                return

            server.restart_pending = True
            self.log.debug(f"{self.__cog_name__}: Warning users on server {server.name} ...")
            # we need a minimum of 60 seconds to warn
            max_warn_time = max(max_warn_time, 60)
            await self.warn_users(server, config, rconf, max_warn_time)
            # in the unlikely event that we did restart already in the meantime while warning users or
            # if the restart has been canceled due to maintenance mode
            if not server.restart_pending:
                return
            else:
                server.on_empty.clear()
        else:
            server.restart_pending = True

        try:
            await self.run_action(server, rconf)
        except (TimeoutError, asyncio.TimeoutError):
            await self.bot.audit(f"{self.__cog_name__}: Timeout while starting server", server=server)
        except Exception as ex:
            self.log.error(f"{self.__cog_name__}: Error with action {repr(rconf)} on server {server.name}: {ex}",
                           exc_info=True)
            server.restart_pending = False

    async def check_mission_state(self, server: Server, config: dict) -> None:
        """
        Inspect the mission‑configuration (`config['action']`) and, if the
        server is not already pending a restart, decide whether an action
        (restart / shutdown) has to be triggered.
        """
        # Nothing to do if no action is defined or the server already has a
        # pending restart.
        if not config.get("action") or server.restart_pending:
            return

        actions = (
            config["action"]
            if isinstance(config["action"], list)
            else [config["action"]]
        )

        # Calculate warn-times. If the server is not populated, no warning is necessary.
        warn_times = (
            Scheduler.get_warn_times(config) if server.is_populated() else [0]
        )

        for rconf in actions:
            # The loop over *warn_times* is ordered from small to large so
            # that the first matching rule is always the “closest” one.
            for warn_time in sorted(warn_times):
                restart_in = Scheduler._match_action(
                    server=server,
                    config=config,
                    rconf=rconf,
                    warn_time=warn_time,
                )
                if restart_in is not None:
                    # non-blocking call to the action handler
                    asyncio.create_task(
                        self.handle_action(server, config, rconf, restart_in)
                    )
                    return

    # ------------------------------------------------------------------ #
    # Helper / dispatcher logic
    # ------------------------------------------------------------------ #
    @staticmethod
    def _match_action(
        *,
        server: Server,
        config: dict,
        rconf: dict,
        warn_time: int,
    ) -> int | None:
        """
        Return `restart_in` (seconds) if *rconf* matches a rule,
        otherwise `None`.
        """
        # Dispatch based on the key that is present in the rule.
        if "local_times" in rconf or 'utc_times' in rconf or 'times' in rconf:
            return Scheduler._handle_times(server, config, rconf, warn_time)

        if "mission_time" in rconf:
            return Scheduler._handle_mission_time(server, config, rconf, warn_time)

        if "real_time" in rconf:
            return Scheduler._handle_real_time(server, config, rconf, warn_time)

        if "idle_time" in rconf and server.idle_since:
            return Scheduler._handle_idle_time(server, config, rconf)

        if "cron" in rconf:
            return Scheduler._handle_cron(server, config, rconf, warn_time)

        return None  # no recognised rule

    # ------------------------------------------------------------------ #
    # Individual rule handlers – each returns `restart_in` or `None`
    # ------------------------------------------------------------------ #
    @staticmethod
    def _handle_times(server: Server, config: dict, rconf: dict, warn_time: int) -> int | None:
        """
        *times* – the mission has to restart when the *restart_time*
        falls into any of the supplied time intervals.
        """
        if 'times' in rconf:
            option = 'times'
            tz = config.get('timezone')
        elif 'local_times' in rconf:
            option = 'local_times'
            tz = None
        elif 'utc_times' in rconf:
            option = 'utc_times'
            tz = 'UTC'
        else:
            raise ValueError("Unknown option passed to _handle_times()")

        restart_time = datetime.now() + timedelta(seconds=warn_time)
        tzinfo = ZoneInfo(tz) if tz else None
        for t in rconf[option]:
            if utils.is_in_timeframe(restart_time, t, tz=tzinfo):
                return warn_time
        return None

    @staticmethod
    def _handle_mission_time(server: Server, config: dict, rconf: dict, warn_time: int) -> int | None:
        """
        *mission_time* – check the maximum duration a mission is allowed
        to run.  If the current mission would exceed that duration,
        return the time left until the threshold.
        """
        mission = server.current_mission
        if not mission:
            return None

        if (
            "max_mission_time" in rconf
            and server.is_populated()
            and not rconf.get("populated", True)
        ):
            max_mission_time = rconf["max_mission_time"] * 60
        else:
            max_mission_time = rconf["mission_time"] * 60

        if mission.mission_time + warn_time >= max_mission_time:
            restart_in = int(max_mission_time - mission.mission_time)
            return max(0, restart_in)
        return None

    @staticmethod
    def _handle_real_time(server: Server, config: dict, rconf: dict, warn_time: int) -> int | None:
        """
        *real_time* – similar to *mission_time* but uses the mission’s
        real‑time counter instead of the internal mission timer.
        """
        mission = server.current_mission
        if not mission:
            return None

        real_time = rconf["real_time"] * 60
        if mission.real_time + warn_time >= real_time:
            restart_in = int(real_time - mission.real_time)
            if rconf["method"] == "restart":
                rconf["shutdown"] = True
            return max(0, restart_in)
        return None

    @staticmethod
    def _handle_idle_time(server: Server, config: dict, rconf: dict) -> int | None:
        """
        *idle_time* – if the server has been idle longer than the
        configured threshold, trigger a 0‑second restart.
        """
        idle_diff = (datetime.now(tz=timezone.utc) - server.idle_since).total_seconds() / 60
        if idle_diff >= rconf["idle_time"]:
            return 0
        return None

    @staticmethod
    def _handle_cron(server: Server, config: dict, rconf: dict, warn_time: int) -> int | None:
        """
        *cron* – evaluate the cron expression at the *restart_time* that would occur after *warn_time* seconds.
        """
        tzinfo = (
            ZoneInfo(config.get('timezone'))
            if 'timezone' in config
            else None
        )
        restart_time = datetime.now(tz=tzinfo) + timedelta(seconds=warn_time)
        restart_time = restart_time.replace(second=0, microsecond=0)

        if utils.matches_cron(restart_time, rconf["cron"]):
            return warn_time
        return None

    @tasks.loop(minutes=1.0)
    async def check_state(self):
        # do not change the state if an update is pending
        if self.node.update_pending:
            return

        next_startup = 0
        startup_delay = self.get_config().get('startup_delay', 10)
        for server_name, server in self.bot.servers.items():
            # only care about servers that are not in the startup phase
            if server.status in [Status.UNREGISTERED, Status.LOADING, Status.SHUTTING_DOWN]:
                continue
            config = self.get_config(server)
            if server.maintenance:
                if not config.get('startup', {}).get('clear_maintenance'):
                    continue
                server.maintenance = False
                self.log.warning(f"Maintenance mode cleared on server {server.name}.")

            # if no config is defined for this server, ignore it
            if config:
                try:
                    target_state = await self.check_server_state(server, config)
                    if target_state == Status.RUNNING and server.status == Status.SHUTDOWN:
                        server.status = Status.LOADING
                        if 'startup' in config and ('mission_file' in config['startup'] or 'mission_id' in config['startup']):
                            rconf = config.get('startup').copy()
                            # run "load" if a startup-section was provided
                            rconf['method'] = 'load'
                            self.loop.call_later(
                                delay=next_startup,
                                callback=partial(asyncio.create_task,
                                                 self.run_action(server, rconf))
                            )
                        else:
                            self.loop.call_later(
                                delay=next_startup,
                                callback=partial(asyncio.create_task,
                                                 self.launch_dcs(server, ignore_exception=True))
                            )
                        next_startup += startup_delay
                    elif target_state == Status.SHUTDOWN and server.status in [
                        Status.STOPPED, Status.RUNNING, Status.PAUSED
                    ]:
                        asyncio.create_task(self.teardown(server, config))
                    elif server.status in [Status.RUNNING, Status.PAUSED]:
                        await self.check_mission_state(server, config)
                except Exception as ex:
                    self.log.exception(ex)

    @check_state.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
        while True:
            if all(server.status != Status.UNREGISTERED for server in self.bot.servers.values()):
                break
            await asyncio.sleep(1)

    group = Group(name="server", description="Commands to manage a DCS server")

    @group.command(name='list', description='List of all registered servers')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction):
        if not self.bot.servers:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("No servers registered.", ephemeral=True)
            return
        embed = discord.Embed(title=f"All Servers", color=discord.Color.blue())
        names = []
        status = []
        players = []
        for server in self.bot.servers.values():
            names.append(server.display_name)
            status.append(server.status.name.title())
            if server.status in [Status.RUNNING, Status.PAUSED]:
                players.append(f"{len(server.get_active_players()) + 1}/{server.settings.get('maxPlayers', 0)}")
            else:
                players.append('-')
        if len(names):
            embed.add_field(name='Server', value='\n'.join(names))
            embed.add_field(name='Status', value='\n'.join(status))
            embed.add_field(name='Players', value='\n'.join(players))
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(embed=embed, ephemeral=utils.get_ephemeral(interaction))

    async def _startup(self, interaction: discord.Interaction, embed: discord.Embed, server: Server, *,
                       msg: discord.Message = None, mission_id: int = None, maintenance: bool | None = False,
                       run_extensions: bool = True, use_orig: bool = True, ephemeral: bool = False):

        if maintenance and not server.maintenance:
            server.maintenance = True
            embed.description += f"\n- Maintenance flag set."

        embed.description += f"\n- Starting DCS server, please wait ..."
        embed.set_thumbnail(url=TRAFFIC_LIGHTS['red'])
        if not msg:
            msg = await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await msg.edit(embed=embed)

        try:
            if mission_id is not None:
                mission = (await server.getMissionList())[mission_id]
                await server.setStartIndex(mission_id + 1)
            else:
                mission = await server.get_current_mission_file()
            if mission:
                embed.description += f"\n- Using mission \"{utils.escape_string(os.path.basename(mission)[:-4])}\" ..."
                if run_extensions:
                    embed.description += "\n- Applying extensions"
                    if use_orig:
                        embed.description += " to the original mission file ..."
                    else:
                        embed.description += " to the current mission file ..."
            else:
                embed.description += f"\n- Starting without a mission ..."
            await msg.edit(embed=embed)
            for i in range(1, 4):
                try:
                    task = asyncio.create_task(self.launch_dcs(server, interaction.user, modify_mission=run_extensions,
                                                               use_orig=use_orig))
                    # wait until the server is loading
                    await server.wait_for_status_change(status=[Status.LOADING], timeout=180)
                    embed.description += f"\n- Launching ..."
                    embed.set_thumbnail(url=TRAFFIC_LIGHTS['amber'])
                    await msg.edit(embed=embed)
                    # wait for the startup
                    await task
                    if maintenance:
                        embed = utils.create_warning_embed(
                            title=f"DCS server \"{server.display_name}\" started.",
                            text="Server is in maintenance mode!\n"
                                 "Use {} to reset maintenance mode.".format(
                                (await utils.get_command(self.bot, group=self.scheduler.name,
                                                         name=self.clear.name)).mention
                            )
                        )
                        await msg.edit(embed=embed)
                    else:
                        if maintenance is False and server.maintenance:
                            server.maintenance = False
                            embed.description += f"\n- Maintenance flag cleared."
                        embed.description += f"\n- Server started successfully."
                        embed.set_thumbnail(url=TRAFFIC_LIGHTS['green'])
                        await msg.edit(embed=embed)
                    break
                except (TimeoutError, asyncio.TimeoutError):
                    if server.status == Status.SHUTDOWN:
                        if i < 4:
                            embed.description += f"\n- The server crashed during startup. Trying again ({i}/3) ..."
                            embed.set_thumbnail(url=TRAFFIC_LIGHTS['red'])
                            await msg.edit(embed=embed)
                        else:
                            embed.description += f"\n- The server is crashing during startup. Check the dcs.log for errors."
                            embed.set_thumbnail(url=TRAFFIC_LIGHTS['red'])
                            await msg.edit(embed=embed)
                    else:
                        embed.description += f"\n- Timeout while launching. Please check if the server has started properly."
                        embed.set_thumbnail(url=TRAFFIC_LIGHTS['amber'])
                        await msg.edit(embed=embed)
                        break
        except Exception as ex:
            self.log.exception(ex)
            embed.description += f"\n- Something went wrong. Please check the dcssb*.log."
            embed.description += f"\nException: {str(ex)}"
            embed.set_thumbnail(url=TRAFFIC_LIGHTS['red'])
            await msg.edit(embed=embed)

    @group.command(description='Launches a DCS server')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    @app_commands.rename(mission_id="mission")
    @app_commands.describe(use_orig="Change the mission based on the original uploaded mission file.")
    @app_commands.autocomplete(mission_id=utils.mission_autocomplete)
    async def startup(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                      maintenance: bool | None = False, run_extensions: bool | None = True,
                      use_orig: bool | None = True, mission_id: int | None = None):

        if server.status == Status.STOPPED:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                "DCS server \"{name}\" is stopped.\nPlease use {command} instead.".format(
                    name=server.display_name,
                    command=(await utils.get_command(self.bot, group=self.group.name, name=self.start.name)).mention),
                ephemeral=True)
            return
        elif server.status in [Status.LOADING, Status.SHUTTING_DOWN]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                "DCS server \"{name}\" is {status}.\nPlease wait or use {command} force instead.".format(
                    name=server.display_name, status=server.status.value.lower(),
                    command=(await utils.get_command(self.bot, group=self.group.name, name=self.shutdown.name)).mention
                ),
                ephemeral=True)
            return
        elif server.status in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"DCS server \"{server.display_name}\" is started already.",
                                                    ephemeral=True)
            return

        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        embed = discord.Embed(title=f"Launching DCS Server \"{server.display_name}\"", color=discord.Color.blue())
        embed.description = ""
        await self._startup(interaction, embed=embed, server=server, maintenance=maintenance, mission_id=mission_id,
                            run_extensions=run_extensions, use_orig=use_orig, ephemeral=ephemeral)

    async def _shutdown(self, interaction: discord.Interaction, embed: discord.Embed, server: Server, *,
                        msg: discord.Message = None, maintenance: bool | None = True, force: bool = False,
                        ephemeral: bool = False):

        if maintenance and not server.maintenance:
            server.maintenance = True
            embed.description += f"\n- Maintenance flag set."

        try:
            if force:
                embed.description += "\n- Killing the DCS server, please wait ..."
                embed.set_thumbnail(url=TRAFFIC_LIGHTS['green'])
                if not msg:
                    msg = await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                else:
                    await msg.edit(embed=embed)
                task = asyncio.create_task(server.shutdown(force=True))
            else:
                embed.description += f"\n- Gracefully stopping the DCS server, please wait ..."
                embed.set_thumbnail(url=TRAFFIC_LIGHTS['green'])
                if not msg:
                    msg = await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                else:
                    await msg.edit(embed=embed)
                task = asyncio.create_task(self.teardown_dcs(server, interaction.user))

            await server.wait_for_status_change(status=[Status.SHUTTING_DOWN], timeout=180)
            embed.description += f"\n- Shutdown process initiated ..."
            embed.set_thumbnail(url=TRAFFIC_LIGHTS['amber'])
            await msg.edit(embed=embed)
            await server.wait_for_status_change(status=[Status.STOPPED, Status.SHUTDOWN], timeout=180)
            # wait for the process to vanish
            await task

            if maintenance:
                embed = utils.create_warning_embed(
                    title=f"DCS server \"{server.display_name}\" shut down.",
                    text="Server is in maintenance mode!\n"
                         "Use {} to reset maintenance mode.".format(
                        (await utils.get_command(self.bot, group=self.scheduler.name, name=self.clear.name)).mention
                    )
                )
                await msg.edit(embed=embed)
            else:
                if maintenance is False and server.maintenance:
                    server.maintenance = False
                    embed.description += f"\n- Maintenance flag cleared."
                embed.description += f"\n- Server shut down successfully."
                embed.set_thumbnail(url=TRAFFIC_LIGHTS['red'])
                await msg.edit(embed=embed)

        except (TimeoutError, asyncio.TimeoutError):
            embed.description += f"\n- Timeout while shutting down. Please check if the server has shut down properly."
            embed.set_thumbnail(url=TRAFFIC_LIGHTS['red'])
            await msg.edit(embed=embed)
            raise
        except Exception as ex:
            self.log.exception(ex)
            embed.description += f"\n- Something went wrong. Please check the dcssb*.log."
            embed.description += f"\nException: {str(ex)}"
            embed.set_thumbnail(url=TRAFFIC_LIGHTS['red'])
            await msg.edit(embed=embed)
            raise

    @group.command(description='Shuts a DCS server down')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def shutdown(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(
                           status=[
                               Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.LOADING, Status.SHUTTING_DOWN
                           ])], force: bool | None = False, maintenance: bool | None = True):

        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if server.status == Status.SHUTDOWN:
            await interaction.followup.send(f"DCS server \"{server.display_name}\" is already shut down.",
                                            ephemeral=ephemeral)
            return

        if server.status in [Status.UNREGISTERED, Status.LOADING, Status.SHUTTING_DOWN]:
            if not force and not await utils.yn_question(
                    interaction, f"Server is in state {server.status.name}.\n"
                                 f"Do you want to force a shutdown?", ephemeral=ephemeral):
                return
            force = True

        if not force:
            question = f"Do you want to shut down DCS server \"{server.display_name}\"?"
            if server.is_populated():
                result = await utils.populated_question(interaction, question, ephemeral=ephemeral)
            else:
                result = await utils.yn_question(interaction, question, ephemeral=ephemeral)
            if not result:
                await interaction.followup.send('Aborted.', ephemeral=ephemeral)
                return
            elif result == 'later':
                server.on_empty = {
                    "method": "shutdown",
                    "user": interaction.user
                }
                server.restart_pending = True
                await interaction.followup.send('Shutdown postponed when server is empty.', ephemeral=ephemeral)
                return

        embed = discord.Embed(title=f"Shutting down DCS Server \"{server.display_name}\"",
                              color=discord.Color.blue())
        embed.description = ""
        try:
            await self._shutdown(interaction, embed=embed, server=server, maintenance=maintenance, force=force,
                                 ephemeral=ephemeral)
        except TimeoutError as ex:
            self.log.error(f"Timeout while shutting down server {server.name}.")
        except Exception as ex:
            self.log.error(ex)

    @group.command(description='Restarts a DCS server')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    @app_commands.rename(mission_id="mission")
    @app_commands.describe(use_orig="Change the mission based on the original uploaded mission file.")
    @app_commands.autocomplete(mission_id=utils.mission_autocomplete)
    async def restart(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[
                              Status.RUNNING, Status.PAUSED, Status.STOPPED
                          ])],
                      delay: int | None = 120, force: bool | None = False, run_extensions: bool | None = True,
                      use_orig: bool | None = True, mission_id: int | None = None):

        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        question = f"Do you want to restart DCS server \"{server.display_name}\"?"
        message = ""
        if server.is_populated():
            message += "People are currently flying on this server!"
        if server.restart_pending:
            message += "\nA restart is pending already. If you continue, the restart will be voided."

        if not await utils.yn_question(interaction, question, message=message, ephemeral=ephemeral):
            await interaction.followup.send("Aborted.", ephemeral=ephemeral)
            return

        maintenance = server.maintenance
        server.maintenance = True

        embed = discord.Embed(title=f"Restarting DCS Server \"{server.display_name}\"", color=discord.Color.blue())
        embed.set_thumbnail(url=TRAFFIC_LIGHTS['green'])
        embed.description = "- Maintenance flag set."
        msg = await interaction.followup.send(embed=embed, ephemeral=ephemeral)

        # clear any restart flag
        if server.restart_pending:
            server.restart_pending = False
            server.on_empty.clear()
            server.on_mission_end.clear()
            embed.description += "\n- Already pending restart was cancelled."
            await msg.edit(embed=embed)

        # send warnings (TODO: change to warn structure)
        if server.is_populated():
            if delay > 0:
                message = _("!!! Server will restart in {} !!!").format(utils.format_time(delay))
                await server.sendPopupMessage(Coalition.ALL, message)
                embed.description += '- Restart is delayed for {}. Waiting ...'.format(utils.format_time(delay))
                await msg.edit(embed=embed)
                await asyncio.sleep(delay)
            else:
                message = _("!!! Server will restart NOW !!!")
                await server.sendPopupMessage(Coalition.ALL, message)

        try:
            await self._shutdown(interaction, embed=embed, server=server, msg=msg, maintenance=None, force=force)
            await self._startup(interaction, embed=embed, server=server, msg=msg, maintenance=None,
                                run_extensions=run_extensions, use_orig=use_orig, mission_id=mission_id)
        except TimeoutError as ex:
            self.log.error(f"Timeout while restarting server {server.name}.")
        except Exception as ex:
            self.log.error(ex)

        if server.maintenance != maintenance:
            server.maintenance = maintenance
            embed.description += f"\n- Maintenance flag set." if maintenance else f"\n- Maintenance flag cleared."
            await msg.edit(embed=embed)

    @group.command(description='Starts a stopped DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def start(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.STOPPED])]):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status == Status.STOPPED:
            # noinspection PyUnresolvedReferences
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            try:
                if not await server.start():
                    embed = utils.create_warning_embed(
                        title=f"Error while starting server \"{server.display_name}\"!",
                        text="Please check manually, if the server has started.\n"
                             "If not, check the dcs.log for errors.")
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                    return
            except (TimeoutError, asyncio.TimeoutError):
                embed = utils.create_warning_embed(
                    title=f"Timeout while starting server \"{server.display_name}\"!",
                    text="Please check manually, if the server has started.\n"
                         "If not, check the dcs.log for errors.")
                await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                return
            await interaction.followup.send(f"Server {server.display_name} started.", ephemeral=ephemeral)
            await self.bot.audit('started the server', server=server, user=interaction.user)
        elif server.status == Status.SHUTDOWN:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f"Server {server.display_name} is shut down. Use /server startup to start it up.", ephemeral=ephemeral)
        elif server.status in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Server {server.display_name} is already started.",
                                                    ephemeral=ephemeral)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f"Server {server.display_name} is still {server.status.name}, please wait ...", ephemeral=ephemeral)

    @group.command(description='Stops a running DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def stop(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.RUNNING, Status.PAUSED])]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if server.is_populated() and \
                not await utils.yn_question(interaction, "People are flying on this server atm.\n"
                                                         "Do you really want to stop it?", ephemeral=ephemeral):
            await interaction.followup.send("Aborted.", ephemeral=ephemeral)
            return
        msg = None
        try:
            msg = await interaction.followup.send(f"Stopping server {server.name} ...", ephemeral=ephemeral)
            await server.stop()
        except (TimeoutError, asyncio.TimeoutError):
            embed = utils.create_warning_embed(
                title=f"Timeout while stopping server \"{server.display_name}\"!",
                text="Please check manually, if the server has stopped.\n"
                     "If not, check the dcs.log for errors.")
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
            return
        finally:
            try:
                if msg:
                    await msg.delete()
            except discord.NotFound:
                pass
        await interaction.followup.send(f"Server {server.display_name} stopped.", ephemeral=ephemeral)
        await self.bot.audit('stopped the server', server=server, user=interaction.user)

    @group.command(description='Change the password of a DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def password(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer],
                       coalition: Literal['red', 'blue'] | None = None):
        class PasswordModal(Modal, title="Enter Password"):
            # noinspection PyTypeChecker
            password = TextInput(label="New Password" + (f" for coalition {coalition}:" if coalition else ":"),
                                 style=TextStyle.short, required=False)

            async def on_submit(derived, interaction: discord.Interaction):
                ephemeral = utils.get_ephemeral(interaction)
                # noinspection PyUnresolvedReferences
                await interaction.response.defer(ephemeral=ephemeral)
                if coalition:
                    await server.setCoalitionPassword(Coalition(coalition), derived.password.value)
                    await self.bot.audit(f"changed password for coalition {coalition}",
                                         user=interaction.user, server=server)
                else:
                    await server.setPassword(derived.password.value)
                    await self.bot.audit(f"changed password", user=interaction.user, server=server)
                if server.status in [Status.PAUSED, Status.RUNNING]:
                    message = _("Password will be changed on next server start.")
                else:
                    message = _("Password changed.")

                await interaction.followup.send(message, ephemeral=ephemeral)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(PasswordModal())

    @group.command(description='Change the configuration of a DCS server')
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('DCS Admin')
    async def config(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        if server.status in [Status.RUNNING, Status.PAUSED]:
            if await utils.yn_question(interaction, question='Server has to be stopped to change its configuration.\n'
                                                             'Do you want to stop it?'):
                await server.stop()
            else:
                await interaction.followup.send('Aborted.')
                return

        view = ConfigView(self.bot, server)
        embed = discord.Embed(title=f'Please edit the configuration of server\n"{server.display_name}"')
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        try:
            if not await view.wait() and not view.cancelled:
                if view.channel_update:
                    channels = {
                        "status": server.locals.get('channels', {}).get('status', -1),
                        "chat": server.locals.get('channels', {}).get('chat', -1)
                    }
                    if not self.bot.locals.get('channels', {}).get('admin'):
                        channels['admin'] = server.locals.get('channels', {}).get('admin', -1)
                    await server.update_channels(channels)
                await interaction.followup.send(f'Server configuration for server "{server.display_name}" updated.',
                                                ephemeral=ephemeral)
        finally:
            try:
                await msg.delete()
            except discord.NotFound:
                pass

    @group.command(name='rename', description='Rename a DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def _rename(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.SHUTDOWN])], new_name: str):
        if server.status not in [Status.SHUTDOWN]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Server {server.name} has to be shut down for renaming.",
                                                    ephemeral=True)
            return
        if server.name == new_name:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("The server has this name already. Aborted.", ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        old_name = server.name
        await server.rename(new_name, True)
        await self.bot.audit(f"renamed from {old_name}", server=server, user=interaction.user)
        await interaction.followup.send(f"Server {old_name} renamed to {new_name}.", ephemeral=ephemeral)

    @group.command(name="migrate", description="Migrate a server from one instance to another")
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_role('Admin')
    async def _migrate(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer],
                       node: app_commands.Transform[Node, utils.NodeTransformer],
                       instance: app_commands.Transform[Instance, utils.InstanceTransformer]):
        if server.instance == instance:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f'Server "{server.name}" is already bound to instance "{instance.name}".', ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        if instance.server:
            if not await utils.yn_question(interaction, f"Instance {instance.name} is not empty.\n"
                                                        f"Do you want to unlink (and probably shutdown) server "
                                                        f"{instance.server.name} first?", ephemeral=ephemeral):
                await interaction.followup.send("Aborted.", ephemeral=ephemeral)
                return
        maintenance = server.maintenance
        running = False
        server.maintenance = True
        try:
            if server.status != Status.SHUTDOWN:
                if not await utils.yn_question(interaction,
                                               f"Do you want to shut down server \"{server.name}\" for migration?",
                                               ephemeral=ephemeral):
                    await interaction.followup.send("Aborted", ephemeral=ephemeral)
                running = True
                await server.shutdown()
            # noinspection PyUnresolvedReferences
            if not interaction.response.is_done():
                # noinspection PyUnresolvedReferences
                await interaction.response.defer(ephemeral=ephemeral)
            # prepare server for migration
            await server.persist_settings()
            if instance.server:
                await instance.server.persist_settings()
                if instance.server.status != Status.SHUTDOWN:
                    await instance.server.shutdown()
            await node.migrate_server(server, instance)
            await interaction.followup.send(f"DCS server {server.name} migrated to instance {instance.name}.",
                                            ephemeral=ephemeral)
            await self.bot.audit(f"migrated DCS server to node {node.name} instance {instance.name}",
                                 user=interaction.user, server=server)
            if running:
                msg: discord.Message = await interaction.followup.send("Starting up ...", ephemeral=ephemeral)
                await server.startup()
                if maintenance:
                    await msg.delete()
                    embed = utils.create_warning_embed(
                        title=f"DCS server \"{server.display_name}\" started.",
                        text="Server is in maintenance mode!\n"
                             "Use {} to reset maintenance mode.".format(
                            (await utils.get_command(self.bot, group=self.scheduler.name, name=self.clear.name)).mention
                        )
                    )
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                else:
                    await msg.edit(content=f"DCS server \"{server.display_name}\" started.")
        finally:
            server.maintenance = maintenance

    @group.command(name="timeleft", description="Time until server / mission restart")
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.rename(_server="server")
    async def timeleft(self, interaction: discord.Interaction,
                       _server: app_commands.Transform[Server, utils.ServerTransformer(
                           status=[
                               Status.RUNNING, Status.PAUSED
                           ])
                       ]):
        config = self.get_config(_server).get('action')
        if not config:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("No restart configured for this server.", ephemeral=True)
            return
        elif _server.maintenance:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Server is in maintenance mode, it will not restart.",
                                                    ephemeral=True)
            return
        elif not _server.restart_time:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Please try again in a minute.", ephemeral=True)
            return
        restart_in, rconf = await self.eventlistener.get_next_restart(server=_server, restart=config)
        what = rconf['method']
        if what == 'shutdown' or rconf.get('shutdown', False):
            item = f'Server {_server.name}'
        else:
            item = f'The mission on server {_server.name}'
        message = f"{item} will {what}"
        if (any(key in rconf for key in ['times', 'real_time', 'idle_time', 'cron']) or
                _server.status == Status.RUNNING):
            if _server.restart_time >= datetime.now(tz=timezone.utc):
                message += f" <t:{int(_server.restart_time.timestamp())}:R>"
            else:
                message += " now"
            if not rconf.get('populated', True) and _server.is_populated() and not rconf.get('max_mission_time'):
                message += ", if all players have left."
        else:
            if restart_in:
                message += f" after {utils.format_time(restart_in)}"
            else:
                message += " immediately"
            message += f", if the mission is unpaused again."
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(message, delete_after=60)

    @group.command(name="cleanup", description="Clear the temp directory")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def cleanup(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[Status.SHUTDOWN])]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if server.status != Status.SHUTDOWN:
            if not await utils.yn_question(
                interaction, f"Do you want to shut down server \"{server.display_name}\" for a cleanup?",
                ephemeral=ephemeral):
                return
            await server.shutdown()
        await server.cleanup()
        await interaction.followup.send(f"Server \"{server.display_name}\" cleaned up.", ephemeral=ephemeral)

    @group.command(name="lock", description="Locks a DCS server")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def lock(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.PAUSED, Status.RUNNING])], message: str | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        try:
            await server.lock(message)
            await interaction.followup.send(f"Server \"{server.display_name}\" locked.", ephemeral=ephemeral)
        except (TimeoutError, asyncio.TimeoutError):
            await interaction.followup.send(f"Timeout during locking of server \"{server.display_name}\"",
                                            ephemeral=ephemeral)

    @group.command(name="unlock", description="Unlocks a DCS server")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def unlock(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.PAUSED, Status.RUNNING])]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        try:
            await server.unlock()
            await interaction.followup.send(f"Server \"{server.display_name}\" unlocked.", ephemeral=ephemeral)
        except (TimeoutError, asyncio.TimeoutError):
            await interaction.followup.send(f"Timeout during unlocking of server \"{server.display_name}\"",
                                            ephemeral=ephemeral)

    # /scheduler commands
    scheduler = Group(name="scheduler", description="Commands to manage the Scheduler")

    @scheduler.command(description='Sets the servers maintenance flag')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def maintenance(self, interaction: discord.Interaction,
                          server: app_commands.Transform[Server, utils.ServerTransformer(maintenance=False)]):
        ephemeral = utils.get_ephemeral(interaction)
        if not server.maintenance:
            if (server.restart_pending or server.on_empty or server.on_mission_end) and \
                    not await utils.yn_question(
                        interaction, "Server is configured for a pending restart.\n"
                                     "Setting the maintenance flag will abort this restart.\n"
                                     "Are you sure?", ephemeral=ephemeral):
                await interaction.followup.send("Aborted.", ephemeral=ephemeral)
                return
            server.maintenance = True
            server.restart_pending = False
            server.on_empty.clear()
            server.on_mission_end.clear()
            # noinspection PyUnresolvedReferences
            if interaction.response.is_done():
                await interaction.followup.send(f"Maintenance mode set for server {server.display_name}.",
                                                ephemeral=ephemeral)
            else:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(f"Maintenance mode set for server {server.display_name}.",
                                                        ephemeral=ephemeral)
            await self.bot.audit("set maintenance flag", user=interaction.user, server=server)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Server {server.display_name} is already in maintenance mode.",
                                                    ephemeral=ephemeral)

    @scheduler.command(description='Clears the servers maintenance flag')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(maintenance=True)]):
        ephemeral = utils.get_ephemeral(interaction)
        if server.maintenance:
            server.maintenance = False
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Maintenance mode cleared for server {server.display_name}.",
                                                    ephemeral=ephemeral)
            await self.bot.audit("cleared maintenance flag", user=interaction.user, server=server)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Server {server.display_name} is not in maintenance mode.",
                                                    ephemeral=ephemeral)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(Scheduler(bot, SchedulerListener))
