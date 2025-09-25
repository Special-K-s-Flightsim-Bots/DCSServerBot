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
        config = self.service.get_config().get('header', {})
        grid = Table.grid(expand=True)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right")
        message = f"[b]"
        if self.service.is_multinode():
            if self.node.master:
                message += "Cluster Master | "
            else:
                message += "Cluster Agent | "
        message += f"DCSServerBot Version {self.node.bot_version}.{self.node.sub_version}"
        if self.node.dcs_version:
            message += f" | DCS Version {self.node.dcs_version}[/]"
        grid.add_row(message, datetime.now().ctime().replace(":", "[blink]:[/]"))
        return Panel(grid, style=config.get("background", "white on navy_blue"),
                     border_style=config.get("border", "white"))


class ServersWidget:
    """Displaying List of Servers"""

    def __init__(self, service: "Dashboard"):
        self.service = service
        self.node = service.node
        self.bus = service.bus

    def __rich__(self) -> Panel:
        config = self.service.get_config().get("servers", {})
        table = Table(expand=True, show_edge=False)
        table.add_column("Status", justify="center", min_width=8)
        table.add_column("Server Name", justify="left", no_wrap=True)
        table.add_column("Mission Name", justify="left", no_wrap=True)
        table.add_column("Players", justify="center", min_width=4)
        if self.service.node.master and self.service.is_multinode():
            table.add_column("Node", justify="left", min_width=8)
        for server_name, server in self.bus.servers.items():
            if config.get('hide_remote_servers', False) and server.is_remote:
                continue
            name = re.sub(self.bus.filter['server_name'], '', server.name).strip()
            mission_name = re.sub(self.bus.filter['mission_name'], '',
                                  server.current_mission.name).strip() if server.current_mission else "n/a"
            num_players = f"{len(server.get_active_players()) + 1}/{server.settings.get('maxPlayers', 16)}" \
                if server.current_mission else "n/a"
            if self.service.node.master and self.service.is_multinode():
                table.add_row(server.status.name.title(), name, mission_name, num_players, server.node.name)
            else:
                table.add_row(server.status.name.title(), name, mission_name, num_players)
        return Panel(table, padding=1,
                     style=config.get("background", "white on dark_blue"),
                     border_style=config.get("border", "white"))


class NodeWidget:
    """Displaying Bot Info"""

    def __init__(self, service: "Dashboard"):
        self.service = service
        self.node = service.node
        self.bus = service.bus
        self.pool = service.pool
        self.log = service.log

    def __rich__(self) -> Panel:
        config = self.service.get_config().get("nodes", {})
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
        return Panel(table, padding=1,
                     style=config.get("background", "white on dark_blue"),
                     border_style=config.get("border", "white"))


class LogWidget:
    """Display log messages"""

    def __init__(self, service: "Dashboard"):
        self.service = service
        self.queue = service.queue
        self.buffer: list[tuple[int, "ConsoleRenderable"]] = []
        self.handler = service.old_handler
        self.console = Console(record=True)
        config = self.service.get_config().get("log", {})
        self.panel = Panel("", height=self.console.options.max_height,
                           style=config.get("background", "white on grey15"),
                           border_style=config.get("border", "white"))
        self.previous_size = self.console.size

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

    def _measure_renderable_lines(self, renderable: "ConsoleRenderable", width: int):
        with self.console.capture() as capture:  # Capture the rendered output for measurement
            self.console.print(renderable, width=width)
        lines = capture.get().splitlines()
        return len(lines)

    def _check_size_change(self) -> bool:
        """Check if the terminal has been resized"""
        current_size = self.console.size
        if current_size != self.previous_size:
            self.previous_size = current_size
            return True
        return False

    def __rich_console__(self, _: Console, options: ConsoleOptions) -> RenderResult:
        if not self.queue.empty() or self._check_size_change():
            config = self.service.get_config()

            max_displayable_height = options.max_height - 2
            available_height = max_displayable_height

            while not self.queue.empty():
                record: logging.LogRecord = self.queue.get()
                log_renderable = self._emit(record)
                renderable_lines = self._measure_renderable_lines(log_renderable, options.max_width)
                self.buffer.append((renderable_lines, log_renderable))
                available_height -= renderable_lines

            # Adjust the buffer to fit into max_displayable_height
            total_height_used = sum(lines for lines, _ in self.buffer)
            while total_height_used > max_displayable_height and self.buffer:
                removed_lines, _ = self.buffer.pop(0)
                total_height_used -= removed_lines

            log_content = Group(*(renderable for _, renderable in self.buffer))
            self.panel = Panel(log_content, height=options.max_height,
                        style=config.get("log", {}).get("background", "white on grey15"),
                        border_style=config.get("log", {}).get("border", "white"))

        yield self.panel


@ServiceRegistry.register(depends_on=[ServiceBus])
class Dashboard(Service):

    def __init__(self, node):
        super().__init__(node)
        self.console = Console()
        self.layout = None
        self.bus = None
        self.queue = None
        self.log_handler = None
        self.old_handler = None
        self.update_task = None
        self.header_widget = None
        self.servers_widget = None
        self.log_widget = None
        self.stop_event = asyncio.Event()

    def is_multinode(self):
        return len(self.node.all_nodes) > 1

    def create_widgets(self):
        self.header_widget = HeaderWidget(self)
        self.servers_widget = ServersWidget(self)
        self.log_widget = LogWidget(self)

    def create_layout(self):
        layout = Layout()
        layout.split(
            Layout(self.header_widget, name="header", size=3),
            Layout(self.servers_widget, name="main", size=len(self.bus.servers) + 6),
            Layout(self.log_widget, name="log", ratio=2, minimum_size=5)
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
        if not self.node.config.get('use_dashboard', True):
            return
        await super().start()
        self.bus = ServiceRegistry.get(ServiceBus)
        self.hook_logging()
        self.create_widgets()
        self.layout = self.create_layout()
        self.stop_event.clear()
        self.update_task = asyncio.create_task(self.update())

    async def stop(self):
        if not self.node.config.get('use_dashboard', True):
            return
        self.stop_event.set()
        if self.update_task:
            await self.update_task
        self.unhook_logging()
        self.console.clear()
        await super().stop()

    async def switch(self):
        await self.stop()
        await self.start()

    async def update(self):
        try:
            previous_server_count = len(self.bus.servers)
            with Live(self.layout, refresh_per_second=1, screen=True) as live:
                while not self.stop_event.is_set():
                    current_server_count = len(self.bus.servers)
                    if current_server_count != previous_server_count:
                        self.layout = self.create_layout()
                        live.update(self.layout)
                        previous_server_count = current_server_count
                    await asyncio.sleep(0.5)
        except Exception as ex:
            self.log.exception(ex)
            await self.stop()
