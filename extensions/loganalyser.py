import aiofiles
import asyncio
import os
import re

from core import Extension, Server, ServiceRegistry, Status, Coalition, utils, get_translation
from services import ServiceBus
from typing import Callable

_ = get_translation(__name__.split('.')[1])

ERROR_UNLISTED = r"ERROR\s+ASYNCNET\s+\(Main\):\s+Server update failed with code -?\d+\.\s+The server will be unlisted."
ERROR_SCRIPT = r'Mission script error: \[string "(.*)"\]:(\d+): (.*)'


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

    async def startup(self) -> bool:
        self.stop_event.clear()
        self.stopped.clear()
        self.errors.clear()
        self.register_callback(ERROR_UNLISTED, self.unlisted)
        self.register_callback(ERROR_SCRIPT, self.script_error)
        # noinspection PyAsyncCall
        asyncio.create_task(self.check_log())
        return await super().startup()

    async def _shutdown(self):
        await self.stopped.wait()
        self.pattern.clear()

    def shutdown(self) -> bool:
        self.loop.create_task(self._shutdown())
        self.stop_event.set()
        return super().shutdown()

    async def check_log(self):
        try:
            logfile = os.path.expandvars(
                self.config.get('log', os.path.join(self.server.instance.home, 'Logs', 'dcs.log'))
            )
            while not self.stop_event.is_set():
                if not os.path.exists(logfile):
                    self.log_pos = 0
                    await asyncio.sleep(1)
                    continue
                async with aiofiles.open(logfile, mode='r', encoding='utf-8', errors='ignore') as file:
                    max_pos = os.fstat(file.fileno()).st_size
                    # no new data has been added to the log, so continue
                    if max_pos == self.log_pos:
                        await asyncio.sleep(1)
                        continue
                    # if we were started with an existing logfile, seek to the file end, else seek to the last position
                    if self.log_pos == -1:
                        await file.seek(0, 2)
                        self.log_pos = max_pos
                    else:
                        # if the log was rotated, reset the pointer to 0
                        if max_pos < self.log_pos:
                            self.log_pos = 0
                        await file.seek(self.log_pos, 0)
                    lines = await file.readlines()
                    for idx, line in enumerate(lines):
                        if '=== Log closed.' in line:
                            self.log_pos = -1
                            return
                        for pattern, callback in self.pattern.items():
                            match = pattern.search(line)
                            if match:
                                if asyncio.iscoroutinefunction(callback):
                                    # noinspection PyAsyncCall
                                    asyncio.create_task(callback(self.log_pos + idx, line, match))
                                else:
                                    self.loop.run_in_executor(None, callback, self.log_pos + idx, line, match)
                    self.log_pos = max_pos
        except Exception as ex:
            self.log.exception(ex)
        finally:
            self.stopped.set()

    async def _send_warning(self, server: Server, warn_time: int):
        await asyncio.sleep(warn_time)
        await server.sendPopupMessage(
            Coalition.ALL,
            _('Server is going to restart in {}!').format(utils.format_time(warn_time)))

    async def unlisted(self, idx: int, line: str, match: re.Match):
        self.log.error(f"Server {self.server.name} got unlisted from the ED server list. Restarting ...")
        if self.server.status == Status.RUNNING:
            self.log.info("- Warning users before ...")
            warn_times = [120 - t for t in [120, 60, 10]]
            warn_tasks = [self._send_warning(self.server, t) for t in warn_times if t > 0]
            # Gather tasks then wait
            await asyncio.gather(*warn_tasks)
        await self.node.audit("restart due to unlisting from the ED server list", server=self.server)
        await self.server.restart()

    async def _send_audit_msg(self, filename: str, target_line: int, error_message: str, context=5):
        if not os.path.exists(filename):
            return
        async with aiofiles.open(filename, 'r', encoding='utf-8') as file:
            lines = await file.readlines()

        print_lines = lines[target_line - context - 1: target_line + context]
        marked_lines = []
        starting_line_number = target_line - context
        for i, line in enumerate(print_lines, starting_line_number):
            if i == target_line:
                marked_lines.append(f"> {i}: {line.rstrip()}")
            else:
                marked_lines.append(f"{i}: {line.rstrip()}")
        code_content = "\n".join(marked_lines)
        await self.node.audit("A LUA error occurred!", server=self.server, file=filename,
                              error=f"Line {target_line}: {error_message}", code=f"```lua\n{code_content}\n```")

    async def script_error(self, idx: int, line: str, match: re.Match):
        filename, line_number, error_message = match.groups()
        if (filename, int(line_number)) in self.errors:
            return
        await self._send_audit_msg(filename, int(line_number), error_message)
        self.errors.add((filename, int(line_number)))
