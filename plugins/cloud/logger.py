import aiohttp
import asyncio
import discord
import logging
import traceback
import os

from contextlib import suppress
from core import Node
from typing import Tuple


class CloudLoggingHandler(logging.Handler):
    def __init__(self, node: Node, url: str):
        logging.Handler.__init__(self)
        self.node = node
        self.url = url
        self.cwd = os.getcwd()

    def format_traceback(self, trace: traceback) -> Tuple[str, int, list[str]]:
        ret = []
        file = None
        line = -1
        while trace is not None:
            filename = trace.tb_frame.f_code.co_filename
            if self.cwd in filename and '\\venv\\' not in filename:
                filename = os.path.relpath(filename, self.cwd)
                if not file:
                    file = filename
                    line = trace.tb_lineno
                ret.append(f'File "{filename}", line {trace.tb_lineno}, in {trace.tb_frame.f_code.co_name}')
            trace = trace.tb_next
        return file or '<unknown>', line, ret

    async def send_post(self, record: logging.LogRecord):
        if isinstance(record.exc_info[1], discord.app_commands.CommandInvokeError):
            exc = record.exc_info[1].original
        else:
            exc = record.exc_info[1]
        file, line, trace = self.format_traceback(exc.__traceback__)
        with suppress(Exception):
            async with aiohttp.ClientSession() as session:
                await session.post(self.url, json={
                    "guild_id": self.node.guild_id,
                    "version": f"{self.node.bot_version}.{self.node.sub_version}",
                    "filename": file,
                    "lineno": line,
                    "message": record.message,
                    "stacktrace": '\n'.join(trace)
                })

    def emit(self, record: logging.LogRecord):
        if record.levelname in ['ERROR', 'CRITICAL'] and record.exc_info is not None:
            loop = asyncio.get_event_loop()
            loop.create_task(self.send_post(record))
