from __future__ import annotations
import asyncio
import logging
import re

from core import Service, ServiceRegistry, Status
from datetime import datetime
from logging.handlers import QueueHandler, RotatingFileHandler
from queue import Queue
from rich.console import Console, ConsoleOptions, RenderResult, ConsoleRenderable, Group
from rich.layout import Layout
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.traceback import Traceback
from typing import TYPE_CHECKING, Optional

from ..servicebus import ServiceBus

if TYPE_CHECKING:
    from core import Node

__all__ = [
    "Dashboard"
]


class HeaderWidget:
    """Display header with clock."""
    def __init__(self, service: "Dashboard"):
        self.service = service
        self.node = service.node
        self.log = service.log

    def __rich__(self) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right")
        message = f"[b]"
        if self.service.is_multinode():
            if self.node.master:
                message += "Cluster Master | "
            else:
                message += "Cluster Agent | "
        message += (f"DCSServerBot Version {self.node.bot_version}.{self.node.sub_version} | "
                    f"DCS Version {self.service.dcs_version}[/]")
        grid.add_row(message, datetime.now().ctime().replace(":", "[blink]:[/]"))
        return Panel(grid, style=self.service.get_config().get("header", {}).get("background", "white on navy_blue"),
                     border_style=self.service.get_config().get("header", {}).get("border", "white"))


class ServersWidget:
    """Displaying List of Servers"""
    def __init__(self, service: "Dashboard"):
        self.service = service
        self.bus = service.bus

    def __rich__(self) -> Panel:
        table = Table(expand=True, show_edge=False)
        table.add_column("Status", justify="center", min_width=8)
        table.add_column("Server Name", justify="left", no_wrap=True)
        table.add_column("Mission Name", justify="left", no_wrap=True)
        table.add_column("Players", justify="center", min_width=4)
        if self.service.node.master and self.service.is_multinode():
            table.add_column("Node", justify="left", min_width=8)
        for server_name, server in self.bus.servers.items():
            name = re.sub(self.bus.filter['server_name'], '', server.name).strip()
            mission_name = re.sub(self.bus.filter['mission_name'], '',
                                  server.current_mission.name).strip() if server.current_mission else "n/a"
            num_players = f"{len(server.get_active_players()) + 1}/{server.settings['maxPlayers']}" \
                if server.current_mission else "n/a"
            if self.service.node.master and self.service.is_multinode():
                table.add_row(server.status.name.title(), name, mission_name, num_players, server.node.name)
            else:
                table.add_row(server.status.name.title(), name, mission_name, num_players)
        return Panel(table, title="[b]Servers", padding=1,
                     style=self.service.get_config().get("servers", {}).get("background", "white on dark_blue"),
                     border_style=self.service.get_config().get("servers", {}).get("border", "white"))


class NodeWidget:
    """Displaying Bot Info"""
    def __init__(self, service: "Dashboard"):
        self.service = service
        self.node = service.node
        self.bus = service.bus
        self.pool = service.pool
        self.log = service.log

    def __rich__(self) -> Panel:
        table = Table(expand=True, show_edge=False)
        table.add_column("Node ([green]Master[/])", justify="left")
        table.add_column("Servers", justify="left")
        nodes: dict[str, Optional[Node]] = {name: None for name in self.node.all_nodes.keys()}
        servers: dict[str, int] = dict()
        for server in self.bus.servers.values():
            nodes[server.node.name] = server.node
            if server.node.name not in servers:
                servers[server.node.name] = 0
            if server.status not in [Status.SHUTDOWN, Status.UNREGISTERED]:
                servers[server.node.name] += 1
        for name, node in nodes.items():  # type: Node
            if not node:
                table.add_row(f"[grey54]{name}[/]", "[grey54]inactive[/]")
            elif node.master:
                table.add_row(f"[green]{node.name}[/]", f"{servers[node.name]}/{len(node.instances)}")
            else:
                table.add_row(node.name, f"{servers[node.name]}/{len(node.instances)}")
        return Panel(table, title="[b]Nodes", padding=1,
                     style=self.service.get_config().get("nodes", {}).get("background", "white on dark_blue"),
                     border_style=self.service.get_config().get("nodes", {}).get("border", "white"))


class LogWidget:
    """Display log messages"""
    def __init__(self, service: "Dashboard"):
        self.service = service
        self.queue = service.queue
        self.buffer: list[ConsoleRenderable] = []
        self.handler = service.old_handler

    def _emit(self, record: logging.LogRecord) -> ConsoleRenderable:
        message = self.handler.format(record)
        traceback = None
        if (
            self.handler.rich_tracebacks
            and record.exc_info
            and record.exc_info != (None, None, None)
        ):
            exc_type, exc_value, exc_traceback = record.exc_info
            assert exc_type is not None
            assert exc_value is not None
            traceback = Traceback.from_exception(
                exc_type,
                exc_value,
                exc_traceback,
                width=self.handler.tracebacks_width,
                extra_lines=self.handler.tracebacks_extra_lines,
                theme=self.handler.tracebacks_theme,
                word_wrap=self.handler.tracebacks_word_wrap,
                show_locals=self.handler.tracebacks_show_locals,
                locals_max_length=self.handler.locals_max_length,
                locals_max_string=self.handler.locals_max_string,
                suppress=self.handler.tracebacks_suppress,
            )
            message = record.getMessage()
            if self.handler.formatter:
                record.message = record.getMessage()
                formatter = self.handler.formatter
                if hasattr(formatter, "usesTime") and formatter.usesTime():
                    record.asctime = formatter.formatTime(record, formatter.datefmt)
                message = formatter.formatMessage(record)

        message_renderable = self.handler.render_message(record, message)
        return self.handler.render(
            record=record, traceback=traceback, message_renderable=message_renderable
        )

    def __rich_console__(self, _: Console, options: ConsoleOptions) -> RenderResult:
        while not self.queue.empty():
            record: logging.LogRecord = self.queue.get()
            log_renderable = self._emit(record)
            self.buffer.append(log_renderable)

        height = options.max_height - 2
        if len(self.buffer) > height:
            self.buffer = self.buffer[-height:]

        log_content = Group(*self.buffer)
        yield Panel(log_content, title="[b]Log", height=options.max_height,
                    style=self.service.get_config().get("log", {}).get("background", "white on grey15"),
                    border_style=self.service.get_config().get("log", {}).get("border", "white"))


@ServiceRegistry.register()
class Dashboard(Service):

    def __init__(self, node):
        super().__init__(node)
        self.console = Console()
        self.layout = None
        self.bus = None
        self.queue = None
        self.log_handler = None
        self.old_handler = None
        self.dcs_branch = None
        self.dcs_version = None
        self.update_task = None
        self.stop_event = asyncio.Event()

    def is_multinode(self):
        return len(self.node.all_nodes) > 1

    def create_layout(self):
        header = HeaderWidget(self)
        servers = ServersWidget(self)
        log = LogWidget(self)
        layout = Layout()
        layout.split(
            Layout(header, name="header", size=3),
            Layout(servers, name="main"),
            Layout(log, name="log", ratio=2, minimum_size=5)
        )
        if self.node.master and self.is_multinode():
            servers = ServersWidget(self)
            nodes = NodeWidget(self)
            layout['main'].split_row(Layout(servers, name="servers", ratio=2), Layout(nodes, name="nodes"))
        return layout

    def hook_logging(self):
        self.queue = Queue()
        self.log_handler = QueueHandler(self.queue)
        self.log_handler.setLevel(logging.INFO)
        for handler in self.log.root.handlers:
            if isinstance(handler, RichHandler) and not isinstance(handler, RotatingFileHandler):
                self.old_handler = handler
                self.log_handler.setFormatter(handler.formatter)
                self.log.root.removeHandler(handler)
                self.log.root.addHandler(self.log_handler)

    def unhook_logging(self):
        self.log.root.removeHandler(self.log_handler)
        self.log.root.addHandler(self.old_handler)

    async def start(self):
        await super().start()
        self.bus = ServiceRegistry.get(ServiceBus)
        self.hook_logging()
        self.dcs_branch, self.dcs_version = await self.node.get_dcs_branch_and_version()
        self.layout = self.create_layout()
        self.stop_event.clear()
        self.update_task = asyncio.create_task(self.update())

    async def stop(self):
        self.stop_event.set()
        if self.update_task:
            await self.update_task
        self.unhook_logging()
        self.console.clear()
        await super().stop()

    async def update(self):
        try:
            with Live(self.layout, refresh_per_second=1, screen=False):
                await self.stop_event.wait()
        except Exception as ex:
            self.log.exception(ex)
            await self.stop()
