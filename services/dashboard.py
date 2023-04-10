import asyncio
import logging
import logging.handlers
import math
import platform
import psycopg2
import re
from contextlib import closing
from core import Service, ServiceRegistry
from datetime import datetime
from discord.ext import tasks
from logging.handlers import QueueHandler, RotatingFileHandler
from queue import Queue
from rich.console import Console, ConsoleOptions, RenderResult, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from typing import cast, Union

from .listener import EventListenerService
from .bot import DCSServerBot, BotService


class Header:
    """Display header with clock."""
    def __init__(self, main):
        self.main = main
        self.log = main.log

    def __rich__(self) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right")
        grid.add_row(
            f"[b]DCSServerBot Version {self.main.config['BOT']['VERSION']}.{self.main.config['BOT']['SUB_VERSION']}[/b]",
            datetime.now().ctime().replace(":", "[blink]:[/]"),
        )
        return Panel(grid, style="white on blue")


class Servers:
    """Displaying List of Servers"""
    def __init__(self, bot: Union[DCSServerBot, EventListenerService]):
        self.bot = bot
        self.config = bot.config

    def __rich__(self) -> Panel:
        table = Table(expand=True, show_edge=False)
        table.add_column("Status", justify="center", min_width=8)
        table.add_column("Server Name", justify="left", no_wrap=True)
        table.add_column("Mission Name", justify="left", no_wrap=True)
        table.add_column("Players", justify="center", min_width=4)
        for server_name, server in self.bot.servers.items():
            name = re.sub(self.bot.config['FILTER']['SERVER_FILTER'], '', server.name).strip()
            mission_name = re.sub(self.bot.config['FILTER']['MISSION_FILTER'], '', server.current_mission.name).strip() if server.current_mission else "n/a"
            num_players = f"{len(server.get_active_players()) + 1}/{server.settings['maxPlayers']}"
            table.add_row(server.status.name.title(), name, mission_name, num_players)
        return Panel(table, title="Servers", padding=1)


class Bot:
    """Displaying Bot Info"""
    def __init__(self, bot: DCSServerBot):
        self.bot = bot
        self.pool = bot.pool
        self.log = bot.log

    def __rich__(self) -> Panel:

        msg = f"Node:\t\t{platform.node()}\n"
        if math.isinf(self.bot.latency) or math.isnan(self.bot.latency):
            msg += "Heartbeat:\t[bold red]Disconnected![/]"
        else:
            msg += f"Heartbeat:\t{int(self.bot.latency * 1000)} ms"
        if self.bot.is_ws_ratelimited():
            msg += "\t[bold red]Rate limited![/]"

        conn = self.pool.getconn()
        table = None
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT s1.agent_host, COUNT(s1.server_name), COUNT(s2.server_name) FROM "
                               "(SELECT agent_host, server_name FROM servers "
                               "WHERE last_seen > (DATE(NOW()) - interval '1 week')) s1 "
                               "LEFT OUTER JOIN "
                               "(SELECT agent_host, server_name FROM servers "
                               "WHERE last_seen > (NOW() - interval '1 minute')) s2 "
                               "ON (s1.agent_host = s2.agent_host AND s1.server_name = s2.server_name) "
                               "WHERE s1.agent_host <> %s "
                               "GROUP BY 1", (platform.node(), ))
                if cursor.rowcount > 0:
                    table = Table(expand=True, show_edge=False)
                    table.add_column("Node", justify="left")
                    table.add_column("# Servers", justify="center")
                    for row in cursor.fetchall():
                        table.add_row(row[0], f"{row[2]}/{row[1]}")
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

        if table:
            return Panel(Group(Panel(msg), Panel(table)), title="Bot")
        else:
            return Panel(msg, title="Bot", padding=1)


class Log:
    """Display log messages"""
    def __init__(self, queue: Queue):
        self.queue = queue
        self.buffer: list[str] = []

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        while not self.queue.empty():
            rec: logging.LogRecord = self.queue.get()
            for msg in rec.getMessage().splitlines():
                if rec.levelno == logging.INFO:
                    msg = "[green]" + msg + "[/]"
                elif rec.levelno == logging.WARNING:
                    msg = "[yellow]" + msg + "[/]"
                elif rec.levelno == logging.ERROR:
                    msg = "[red]" + msg + "[/]"
                elif rec.levelno == logging.FATAL:
                    msg = "[bold red]" + msg + "[/]"
                self.buffer.append(msg)
        height = options.max_height - 2
        width = options.max_width - 5
        msg = ""
        init = len(self.buffer) + 1 - height if len(self.buffer) > height else 0
        for i in range(init, len(self.buffer)):
            if len(self.buffer[i]) > width:
                msg += self.buffer[i][:width - 4] + '...\n'
            else:
                msg += self.buffer[i] + '\n'
        if len(self.buffer) > 100:
            self.buffer = self.buffer[-100:]
        yield Panel(msg, title="Log")


@ServiceRegistry.register("Dashboard")
class Dashboard(Service):

    def __init__(self, main):
        super().__init__(main)
        self.console = Console()
        self.layout = None
        self.bot = None
        self.queue = None
        self.log_handler = None
        self.old_handler = None

    def create_layout(self):
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="log", ratio=2, minimum_size=5),
        )
        if self.main.is_master():
            layout['main'].split_row(Layout(name="servers", ratio=2), Layout(name="bot"))
        return layout

    def hook_logging(self):
        formatter = logging.Formatter(fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
        self.queue = Queue()
        self.log_handler = QueueHandler(self.queue)
        self.log_handler.setLevel(logging.INFO)
        self.log_handler.setFormatter(formatter)
        self.log.addHandler(self.log_handler)
        for handler in self.log.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, RotatingFileHandler):
                self.old_handler = handler
                self.log.removeHandler(handler)

    def unhook_logging(self):
        self.log.removeHandler(self.log_handler)
        self.log.addHandler(self.old_handler)

    async def start(self):
        await super().start()
        self.layout = self.create_layout()
        if self.main.is_master():
            self.bot: DCSServerBot = cast(BotService, ServiceRegistry.get("Bot")).bot
        else:
            self.bot = cast(EventListenerService, ServiceRegistry.get("EventListener"))
        self.hook_logging()
        self.update.start()

    async def stop(self):
        self.update.cancel()
        self.unhook_logging()
        self.console.clear()
        await super().stop()

    @tasks.loop(reconnect=True)
    async def update(self):
        header = Header(self.main)
        if self.main.is_master():
            servers = Servers(self.bot)
            bot = Bot(self.bot)
        else:
            servers = Servers(self.bot)
        log = Log(self.queue)

        def do_update():
            self.layout['header'].update(header)
            if self.main.is_master():
                self.layout['servers'].update(servers)
                self.layout['bot'].update(bot)
            else:
                self.layout['main'].update(servers)
            self.layout['log'].update(log)

        try:
            do_update()
            with Live(self.layout, refresh_per_second=1, screen=True):
                while not self.update.is_being_cancelled():
                    do_update()
                    await asyncio.sleep(1)
        except Exception as ex:
            await self.cog_unload()
            self.log.exception(ex)
