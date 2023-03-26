import asyncio
import logging
import logging.handlers
import math
import platform
import psycopg2
import re
from contextlib import closing
from core import DCSServerBot, Plugin
from datetime import datetime
from discord.ext import tasks
from logging.handlers import QueueHandler, RotatingFileHandler
from queue import Queue
from rich.console import Console, ConsoleOptions, RenderResult, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table


class Header:
    """Display header with clock."""
    def __init__(self, bot: DCSServerBot):
        self.bot = bot
        self.log = bot.log

    def __rich__(self) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right")
        grid.add_row(
            f"[b]DCSServerBot Version {self.bot.version}.{self.bot.sub_version}[/b]",
            datetime.now().ctime().replace(":", "[blink]:[/]"),
        )
        return Panel(grid, style="white on blue")


class Servers:
    """Displaying List of Servers"""
    def __init__(self, bot: DCSServerBot):
        self.bot = bot

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
        msg += "Type:\t\t[bold red]Master[/]\n" if self.bot.master else "Type:\t\tAgent\n"
        if math.isinf(self.bot.latency):
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


class Dashboard(Plugin):
    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.console = Console()
        self.layout = Layout()
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="log", ratio=2, minimum_size=5),
        )
        self.layout['main'].split_row(Layout(name="servers", minimum_size=len(self.bot.servers) + 4, ratio=2),
                                      Layout(name="bot"))
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
        self.update.start()

    async def cog_unload(self):
        self.update.cancel()
        self.log.removeHandler(self.log_handler)
        await super().cog_unload()
        self.log.addHandler(self.old_handler)

    @tasks.loop(reconnect=True)
    async def update(self):
        header = Header(self.bot)
        servers = Servers(self.bot)
        log = Log(self.queue)
        bot = Bot(self.bot)

        def do_update():
            self.layout['header'].update(header)
            self.layout['servers'].update(servers)
            self.layout['bot'].update(bot)
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


async def setup(bot: DCSServerBot):
    await bot.add_cog(Dashboard(bot))
