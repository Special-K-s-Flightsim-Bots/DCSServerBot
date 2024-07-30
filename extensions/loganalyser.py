import aiofiles
import asyncio
import os
import re

from core import Extension, Server, ServiceRegistry
from services import ServiceBus
from typing import Callable


class LogAnalyser(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.bus = ServiceRegistry.get(ServiceBus)
        self.log_pos = -1
        self.pattern: dict[re.Pattern, Callable] = {}
        self.stop_event = asyncio.Event()
        self.stopped = asyncio.Event()

    def register_callback(self, pattern: str, callback: Callable) -> re.Pattern:
        _pattern = re.compile(pattern)
        self.pattern[_pattern] = callback
        return _pattern

    def unregister_callback(self, pattern: re.Pattern):
        self.pattern.pop(pattern, None)

    async def startup(self) -> bool:
        self.stop_event.clear()
        self.stopped.clear()
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
                                    callback(self.log_pos + idx, line, match)
                    self.log_pos = max_pos
        except Exception as ex:
            self.log.exception(ex)
        finally:
            self.stopped.set()
