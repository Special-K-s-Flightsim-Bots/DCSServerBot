import aiofiles
import asyncio
import os
import re

from contextlib import suppress
from core import Extension, Server, ServiceRegistry, Status, Coalition, utils, get_translation, Autoexec, InstanceImpl
from datetime import datetime
from services.bot import BotService
from services.servicebus import ServiceBus
from typing import Callable, cast

_ = get_translation(__name__.split('.')[1])

ERROR_UNLISTED = r"ERROR\s+ASYNCNET\s+\(Main\):\s+Server update failed with code -?\d+\.\s+The server will be unlisted."
ERROR_SCRIPT = r'SCRIPTING.*\[string "(.*)"\]:(\d+): (.*)'
MOOSE_COMMIT_LOG = r"\*\*\* MOOSE GITHUB Commit Hash ID: (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+\d{2}:\d{2})-\w+ \*\*\*"
NO_UPNP = r"\s+\(Main\):\s+No UPNP devices found."
NO_TERRAIN = r"INFO\s+Dispatcher\s+\(Main\):\s+Terrain theatre\s*$"
REGMAP_STORAGE_FULL = "RegMapStorage has no more IDs"

__all__ = [
    "LogAnalyser"
]


class LogAnalyser(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.bus = ServiceRegistry.get(ServiceBus)
        self.log_pos = -1
        self.pattern: dict[re.Pattern, Callable] = {}
        self.stop_event = asyncio.Event()
        self.stopped = asyncio.Event()
        self.errors: set[tuple[str, int]] = set()

    def register_callback(self, pattern: str, callback: Callable) -> re.Pattern:
        _pattern = re.compile(pattern)
        self.pattern[_pattern] = callback
        return _pattern

    def unregister_callback(self, pattern: re.Pattern):
        self.pattern.pop(pattern, None)

    async def do_startup(self):
        self.stop_event.clear()
        self.stopped.clear()
        self.errors.clear()
        #self.register_callback(ERROR_UNLISTED, self.unlisted)
        self.register_callback(ERROR_SCRIPT, self.script_error)
        self.register_callback(MOOSE_COMMIT_LOG, self.moose_log)
        self.register_callback(NO_UPNP, self.disable_upnp)
        self.register_callback(NO_TERRAIN, self.terrain_missing)
        #self.register_callback(REGMAP_STORAGE_FULL, self.restart_server)
        asyncio.create_task(self.check_log())

    async def prepare(self) -> bool:
        with suppress(Exception):
            if os.path.exists(self.logfile):
                os.remove(self.logfile)
        await self.do_startup()
        self.running = True
        return await super().startup()

    async def startup(self) -> bool:
        await self.do_startup()
        return await super().startup()

    async def _shutdown(self):
        await self.stopped.wait()
        self.pattern.clear()

    def shutdown(self) -> bool:
        self.loop.create_task(self._shutdown())
        self.stop_event.set()
        return super().shutdown()

    @property
    def logfile(self) -> str:
        return os.path.expandvars(
            self.config.get('log', os.path.join(self.server.instance.home, 'Logs', 'dcs.log'))
        )

    async def process_lines(self, lines: list[str]):
        for idx, line in enumerate(lines):
            if '=== Log closed.' in line:
                self.log_pos = -1
                return

            for pattern, callback in self.pattern.items():
                match = pattern.search(line)
                if not match:
                    continue

                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(
                        callback(self.log_pos + idx, line, match),
                        name=f"callback_{callback.__name__}_{self.log_pos + idx}"
                    )
                else:
                    asyncio.create_task(
                        asyncio.to_thread(callback, self.log_pos + idx, line, match),
                        name=f"executor_{callback.__name__}_{self.log_pos + idx}"
                    )

    async def check_log(self):
        try:
            logfile = os.path.expandvars(
                self.config.get('log', os.path.join(self.server.instance.home, 'Logs', 'dcs.log'))
            )

            while not self.stop_event.is_set():
                try:
                    if not os.path.exists(logfile):
                        self.log_pos = 0
                        continue

                    async with aiofiles.open(logfile, mode='r', encoding='utf-8', errors='ignore') as file:
                        max_pos = os.fstat(file.fileno()).st_size
                        if self.log_pos == -1 or max_pos == self.log_pos:
                            self.log_pos = max_pos
                            continue
                        # if the logfile was rotated, seek to the beginning of the file
                        elif max_pos < self.log_pos:
                            self.log_pos = 0

                        self.log_pos = await file.seek(self.log_pos, 0)
                        lines = await file.readlines()
                        await self.process_lines(lines)
                        self.log_pos = await file.tell()
                except FileNotFoundError:
                    pass
                finally:
                    await asyncio.sleep(1)
        except Exception as ex:
            self.log.exception(ex)
        finally:
            self.stopped.set()

    async def _send_warning(self, server: Server, warn_time: int):
        await asyncio.sleep(warn_time)
        await server.sendPopupMessage(
            Coalition.ALL, self.config.get('message_unlist', 'Server is going to restart in {}!').format(
                utils.format_time(warn_time)))

    async def send_alert(self, title: str, message: str):
        params = {
            "title": title,
            "message": message,
            'server': self.server.name
        }
        await self.bus.send_to_node({
            "command": "rpc",
            "service": BotService.__name__,
            "method": "alert",
            "params": params
        })

    async def unlisted(self, idx: int, line: str, match: re.Match):
        if not self.config.get('restart_on_unlist', False):
            return
        self.log.error(f"Server {self.server.name} got unlisted from the ED server list. Restarting ...")
        if self.server.status == Status.RUNNING:
            self.log.info("- Warning users before ...")
            warn_times = self.config.get('warn_times', [600, 300, 120, 60, 10])
            wait_times = [max(warn_times) - t for t in warn_times]
            warn_tasks = [self._send_warning(self.server, t) for t in wait_times if t > 0]
            # Gather tasks then wait
            await utils.run_parallel_nofail(*warn_tasks)
        await self.node.audit("restart due to unlisting from the ED server list", server=self.server)
        await self.server.restart(modify_mission=False)

    async def _send_audit_msg(self, filename: str, target_line: int, error_message: str, context: int = 5):
        if not filename.strip('.') or not os.path.exists(filename):
            kwargs = {
                "code": filename
            }
        else:
            kwargs = {
                "file": filename
            }
            marked_lines = []
            try:
                async with aiofiles.open(filename, 'r', encoding='utf-8') as file:
                    lines = await file.readlines()

                print_lines = lines[target_line - context - 1: target_line + context]
                starting_line_number = target_line - context
                for i, line in enumerate(print_lines, starting_line_number):
                    if i == target_line:
                        marked_lines.append(f"> {i}: {line.rstrip()}")
                    else:
                        marked_lines.append(f"{i}: {line.rstrip()}")
                code_content = "\n".join(marked_lines)
                kwargs['code'] = f"```lua\n{code_content}\n```"
            except PermissionError:
                self.log.debug(f"Can't open file {filename} for reading!")
        kwargs['error'] = f"Line {target_line}: {error_message}"
        await self.node.audit("A LUA error occurred!", server=self.server, **kwargs)

    async def script_error(self, idx: int, line: str, match: re.Match):
        filename, line_number, error_message = match.groups()
        basename = os.path.basename(filename)

        # Get the ignore-pattern from config. Defaults to an empty list if not present.
        ignore_patterns = self.config.get('ignore_files', [])

        # Check if the filename matches any of the regex patterns
        if (any(re.match(pattern, basename) for pattern in ignore_patterns) or
                (filename, int(line_number)) in self.errors):
            return

        await self._send_audit_msg(filename, int(line_number), error_message)
        self.errors.add((filename, int(line_number)))

    async def moose_log(self, idx: int, line: str, match: re.Match):
        timestamp_str = match.group(1)
        timestamp = datetime.fromisoformat(timestamp_str)
        if timestamp < datetime.fromisoformat('2024-09-03T16:47:17+02:00'):
            mission_name = self.server.current_mission.name if self.server.current_mission else f"on server {self.server.name}"
            embed = utils.create_warning_embed(
                title='Outdated Moose version found!',
                text=f"Mission {mission_name} is using an old Moose version. "
                     f"You will probably see performance issues!")
            try:
                await self.bus.send_to_node_sync({
                    "command": "rpc",
                    "service": BotService.__name__,
                    "method": "send_message",
                    "params": {
                        "embed": embed.to_dict()
                    }
                })
            except Exception as ex:
                self.log.exception(ex)

    async def disable_upnp(self, idx: int, line: str, match: re.Match):
        autoexec = Autoexec(cast(InstanceImpl, self.server.instance))
        net = autoexec.net or {}
        net |= {
            "use_upnp": False
        }
        autoexec.net = net

    async def terrain_missing(self, idx: int, line: str, match: re.Match):
        filename = await self.server.get_current_mission_file()
        theatre = await self.server.get_current_mission_theatre()
        if theatre:
            await self.send_alert(title="Terrain Missing!",
                                  message=f"Terrain {theatre} is not installed on this server!\n"
                                          f"You can't run mission {filename}.")

    async def restart_server(self, idx: int, line: str, match: re.Match):
        self.log.warning(f"Server restarting due to critical error: {line.rstrip()}")
        asyncio.create_task(self.server.restart(modify_mission=False))
