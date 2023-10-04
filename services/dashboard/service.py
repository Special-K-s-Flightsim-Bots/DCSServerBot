from __future__ import annotations
import asyncio
import logging
import logging.handlers
import psycopg
import re

from core import Service, ServiceRegistry, Status
from datetime import datetime
from discord.ext import tasks
from logging.handlers import QueueHandler, RotatingFileHandler
from queue import Queue
from rich.console import Console, ConsoleOptions, RenderResult
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from services import ServiceBus
from typing import cast, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Node

__all__ = [
    "Dashboard"
]


class HeaderWidget:
    """Display header with clock."""
    def __init__(self, service: Service):
        self.service = service
        self.node = service.node
        self.log = service.log

    def __rich__(self) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right")
        grid.add_row(
            f"[b]DCSServerBot {'Master' if self.node.master else 'Agent'} Version {self.node.bot_version}.{self.node.sub_version} | DCS Version {self.service.dcs_version}[/]",
            datetime.now().ctime().replace(":", "[blink]:[/]"),
        )
        return Panel(grid, style="white on blue")


class ServersWidget:
    """Displaying List of Servers"""
    def __init__(self, service: Service):
        self.service = service
        self.bus = service.bus

    def __rich__(self) -> Panel:
        table = Table(expand=True, show_edge=False)
        table.add_column("Status", justify="center", min_width=8)
        table.add_column("Server Name", justify="left", no_wrap=True)
        table.add_column("Mission Name", justify="left", no_wrap=True)
        table.add_column("Players", justify="center", min_width=4)
        if self.service.node.master:
            table.add_column("Node", justify="left", min_width=8)
        for server_name, server in self.bus.servers.items():
            name = re.sub(self.bus.filter['server_name'], '', server.name).strip()
            mission_name = re.sub(self.bus.filter['mission_name'], '',
                                  server.current_mission.name).strip() if server.current_mission else "n/a"
            num_players = f"{len(server.get_active_players()) + 1}/{server.settings['maxPlayers']}" \
                if server.current_mission else "n/a"
            if self.service.node.master:
                table.add_row(server.status.name.title(), name, mission_name, num_players, server.node.name)
            else:
                table.add_row(server.status.name.title(), name, mission_name, num_players)
        return Panel(table, title="Servers", padding=1)


class NodeWidget:
    """Displaying Bot Info"""
    def __init__(self, service: Service):
        self.service = service
        self.bus = service.bus
        self.pool = service.pool
        self.log = service.log

    def __rich__(self) -> Panel:
        table = Table(expand=True, show_edge=False)
        table.add_column("Node ([green]Master[/])", justify="left")
        table.add_column("Servers", justify="left")
        nodes: dict[str, Node] = dict()
        servers: dict[str, int] = dict()
        for server in self.bus.servers.values():
            nodes[server.node.name] = server.node
            if server.node.name not in servers:
                servers[server.node.name] = 0
            if server.status not in [Status.SHUTDOWN, Status.UNREGISTERED]:
                servers[server.node.name] += 1
        for node in nodes.values():  # type: Node
            if node.master:
                table.add_row(f"[green]{node.name}[/]", f"{servers[node.name]}/{len(node.instances)}")
            else:
                table.add_row(node.name, f"{servers[node.name]}/{len(node.instances)}")
        return Panel(table, title="Nodes", padding=1)


class LogWidget:
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

    def __init__(self, node, name: str):
        super().__init__(node, name)
        self.console = Console()
        self.layout = None
        self.bus = None
        self.queue = None
        self.log_handler = None
        self.old_handler = None
        self.dcs_branch = None
        self.dcs_version = None

    def create_layout(self):
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="log", ratio=2, minimum_size=5),
        )
        if self.node.master:
            layout['main'].split_row(Layout(name="servers", ratio=2), Layout(name="nodes"))
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
        self.bus = cast(ServiceBus, ServiceRegistry.get("ServiceBus"))
        self.dcs_branch, self.dcs_version = await self.node.get_dcs_branch_and_version()
        self.hook_logging()
        self.update.add_exception_type(psycopg.DatabaseError)
        self.update.start()

    async def stop(self):
        self.update.cancel()
        self.unhook_logging()
        self.console.clear()
        await super().stop()

    @tasks.loop(reconnect=True)
    async def update(self):
        header = HeaderWidget(self)
        servers = ServersWidget(self)
        nodes = NodeWidget(self)
        log = LogWidget(self.queue)

        def do_update():
            self.layout['header'].update(header)
            if self.node.master:
                self.layout['servers'].update(servers)
                self.layout['nodes'].update(nodes)
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
            self.log.exception(ex)
            await self.stop()
