import asyncio
import logging
import logging.handlers
from core import DCSServerBot, Plugin
from datetime import datetime
from discord.ext import tasks
from logging.handlers import QueueHandler, RotatingFileHandler
from queue import Queue
from rich.console import Console, ConsoleOptions, RenderResult
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
        table.add_column("Status", justify="center")
        table.add_column("Server Name", justify="left")
        table.add_column("Mission Name", justify="left")
        table.add_column("# Players", justify="center")
        for server_name, server in self.bot.servers.items():
            mission_name = server.current_mission.name if server.current_mission else "n/a"
            num_players = f"{len(server.get_active_players()) or 1}/{server.settings['maxPlayers']}"
            table.add_row(str.capitalize(server.status.name), server_name, mission_name, num_players)
        return Panel(table)


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
        yield Panel(msg)


class Dashboard(Plugin):
    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.console = Console()
        self.layout = Layout()
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="main", minimum_size=len(self.bot.servers) + 4),
            Layout(name="log", ratio=2, minimum_size=5),
        )
        self.log.info("- Launching Dashboard ...")
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

        def update_header():
            self.layout['header'].update(header)

        def update_main():
            self.layout['main'].update(servers)
            self.layout['log'].update(log)

        with Live(update_header(), refresh_per_second=1, screen=True) as live:
            while not self.update.is_being_cancelled():
                update_main()
                live.update(self.layout)
                await asyncio.sleep(1)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Dashboard(bot))
