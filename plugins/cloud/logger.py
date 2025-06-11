import aiohttp
import asyncio
import discord
import logging
import psycopg
import traceback
import os
import zipfile

from contextlib import suppress
from core import Node, UnsupportedMizFileException

EXCLUDE_LIST = [
    ConnectionError,
    EOFError,
    MemoryError,
    aiohttp.ClientError,
    discord.errors.HTTPException,
    psycopg.errors.OperationalError,
    zipfile.BadZipFile,
    UnsupportedMizFileException
]


class CloudLoggingHandler(logging.Handler):
    def __init__(self, node: Node, url: str):
        logging.Handler.__init__(self)
        self.node = node
        self.url = url
        self.cwd = os.getcwd()
        self.pending_futures = set()

    def format_traceback(self, trace: traceback) -> tuple[str, int, list[str]]:
        ret = []
        file = None
        line = -1
        while trace is not None:
            filename = trace.tb_frame.f_code.co_filename
            directories_to_exclude = ['\\venv\\', '\\.venv\\']
            if self.cwd in filename and all(directory not in filename for directory in directories_to_exclude):
                filename = os.path.relpath(filename, self.cwd)
                if not file:
                    file = filename
                    line = trace.tb_lineno
                ret.append(f'File "{filename}", line {trace.tb_lineno}, in {trace.tb_frame.f_code.co_name}')
            trace = trace.tb_next
        return file or '<unknown>', line, ret

    async def send_post(self, record: logging.LogRecord):
        exc_info = record.exc_info
        if (isinstance(exc_info, tuple) and len(exc_info) > 1 and
                isinstance(exc_info[1], discord.app_commands.CommandInvokeError)):
            # noinspection PyUnresolvedReferences
            exc = exc_info[1].original
        elif isinstance(exc_info, tuple) and len(exc_info) > 1:
            exc = exc_info[1]
        else:
            exc = None
        # filter events
        if isinstance(exc, tuple(EXCLUDE_LIST)):
            return

        file, line, trace = self.format_traceback(exc.__traceback__) \
            if exc else (record.filename, record.lineno, [record.funcName])
        # ignore errors without a line number
        if line == -1:
            return
        # log the error to the central database
        with suppress(Exception):
            async with aiohttp.ClientSession() as session:
                # noinspection PyUnresolvedReferences
                await session.post(self.url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth, json={
                    "guild_id": self.node.guild_id,
                    "version": f"{self.node.bot_version}.{self.node.sub_version}",
                    "filename": file,
                    "lineno": line,
                    "message": exc.__class__.__name__ + ': ' + record.message,
                    "stacktrace": '\n'.join(trace)
                })

    def emit(self, record: logging.LogRecord):
        if record.levelno in [logging.ERROR, logging.CRITICAL] and record.exc_info is not None:
            with suppress(Exception):
                loop = asyncio.get_event_loop()
                future = asyncio.run_coroutine_threadsafe(self.send_post(record), loop)
                self.pending_futures.add(future)
                future.add_done_callback(lambda f: self.pending_futures.discard(f))

    def close(self):
        for future in list(self.pending_futures):
            try:
                future.result(timeout=1.0)
            except Exception:
                pass
        super().close()
