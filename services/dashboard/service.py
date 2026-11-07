from __future__ import annotations

import asyncio
import io
import logging
import os
import re

from core import Service, ServiceRegistry, Status, NodeImpl, get_translation, DEFAULT_TAG
from core.utils.yaml_helper import new_yaml
from datetime import datetime
from logging.handlers import QueueHandler, RotatingFileHandler
from pathlib import Path
from queue import Queue
from rich import box
from rich.console import Console, ConsoleOptions, RenderResult, ConsoleRenderable, Group
from rich.layout import Layout
from rich.live import Live
from rich.logging import RichHandler
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from typing import TYPE_CHECKING

from ..servicebus import ServiceBus

if TYPE_CHECKING:
    from core import Node

__all__ = [
    "Dashboard"
]

_ = get_translation("dashboard")

#: (glyph, state word, colour) per server state. Every glyph is a single cell: an emoji is
#: double-width in Windows Terminal and shifts every column behind it.
STATUS_CELLS: dict[Status, tuple[str, str, str]] = {
    Status.RUNNING: ("●", _("Running"), "green3"),
    Status.LOADING: ("◐", _("Loading"), "gold1"),
    Status.PAUSED: ("◐", _("Paused"), "gold1"),
    Status.STOPPED: ("○", _("Stopped"), "grey50"),
    Status.SHUTDOWN: ("■", _("Shutdown"), "red1"),
    Status.SHUTTING_DOWN: ("■", _("Shutdown"), "red1"),
    Status.UNREGISTERED: ("○", _("Waiting"), "grey50"),
}

#: (label, style) per real level number; every mapped label fits the six-cell column. The styles
#: are the old log renderer's ``logging.level.*`` palette.
LEVEL_CELLS: dict[int, tuple[str, str]] = {
    logging.DEBUG: ("DEBUG", "green"),
    logging.INFO: ("INFO", "blue"),
    logging.WARNING: ("WARN", "yellow"),
    logging.ERROR: ("ERROR", "bold red"),
    logging.CRITICAL: ("CRIT", "bold red"),
}

#: states that show the running mission and a player count
ACTIVE_STATES = (Status.RUNNING, Status.PAUSED, Status.LOADING)

#: placeholder for an unknown port
PORT_PLACEHOLDER = "—"


async def refresh_srs_ports(service: "Dashboard") -> None:
    """Bring ``service.srs_ports`` in step with each server's status.

    Called once per dashboard frame. The extension RPC (``run_on_extension('SRS', 'get_ports')``)
    only runs when a server is seen for the first time or its status changed since the previous
    frame: the SRS port changes only when the configuration or the SRS process changes, so a timer
    would be hundreds of calls for a value that never moves. A server outside ``ACTIVE_STATES`` is
    never queried and loses its cached port (the row shows the em dash). A server without the SRS
    extension keeps the em dash silently, because SRS is optional. A failing server keeps the em
    dash and never stops the refresh for the others; its failure is logged once, because the RPC
    is only attempted on a status change.

    ``bus.servers`` is a ``ThreadSafeDict``, whose ``__iter__`` yields ``(name, server)`` pairs
    instead of keys, so every name list here comes from ``keys()``.
    """
    if service.srs_refreshing:
        return
    service.srs_refreshing = True
    try:
        servers = service.bus.servers
        for name in set(service.srs_status) - set(servers.keys()):
            service.srs_status.pop(name, None)
            service.srs_ports.pop(name, None)
        for server in servers.values():
            if service.srs_status.get(server.name) == server.status:
                continue
            service.srs_status[server.name] = server.status
            if server.status not in ACTIVE_STATES:
                service.srs_ports.pop(server.name, None)
                continue
            try:
                ports = await server.run_on_extension('SRS', 'get_ports')
            except ValueError:
                # the extension, or its get_ports, is not there: the row keeps the em dash
                service.srs_ports[server.name] = PORT_PLACEHOLDER
                continue
            except Exception as ex:
                service.srs_ports[server.name] = PORT_PLACEHOLDER
                service.log.warning("SRS port unavailable for %s: %s", server.name, ex)
                continue
            port = ports.get('SRS Port') if isinstance(ports, dict) else None
            service.srs_ports[server.name] = str(int(port)) if port is not None else PORT_PLACEHOLDER
    finally:
        service.srs_refreshing = False


def _status_cell(status: Status) -> Text:
    glyph, word, colour = STATUS_CELLS.get(
        status, ("○", getattr(status, "name", str(status)).replace("_", " ").title(), "grey50"))
    return Text(f"{glyph} {word}", style=colour)


def _level_cell(record: logging.LogRecord) -> Text:
    label, style = LEVEL_CELLS.get(
        record.levelno, (record.levelname, "dim"))
    return Text(label, style=style)


def _max_players(server) -> int:
    """The server's slot count, with the 61 -> 60 adjustment DCS' off-by-one needs."""
    configured = int(server.settings.get('maxPlayers', 60))
    return max(configured - 1, 1) if configured == 61 else max(configured, 1)


def _mission_cell(server, filter_pattern: str) -> Text:
    """The running mission in bold white, otherwise the last configured one in grey50."""
    if server.status in ACTIVE_STATES and server.current_mission:
        name = re.sub(filter_pattern, '', server.current_mission.name).strip()
        return Text(name, style="bold white")
    selected = server.settings.get('lastSelectedMission') or (
        server.settings.get('missionList', [''])[0] if server.settings.get('missionList') else ''
    )
    name = os.path.splitext(os.path.basename(selected))[0] if selected else '—'
    return Text(name, style="grey50")


def _player_cell(server) -> Text:
    """``<n> / <max>`` for an active server, an em dash otherwise."""
    if server.status not in ACTIVE_STATES:
        return Text("—", style="grey50")
    count = server.num_players if server.num_players is not None else len(server.get_active_players())
    maximum = _max_players(server)
    return Text(f"{min(int(count), maximum):2d} / {maximum}", style="white")


class HeaderWidget:
    """Version, node role, DCS version, console address and clock."""

    def __init__(self, service: Dashboard):
        self.service = service
        self.node = service.node
        self.log = service.log
        #: the console's address, read once — services/webservice.yaml is read at process start, so a
        #: change to it needs a restart anyway and a per-frame file read would be pure cost.
        self.web_url: str | None = self._read_console_url()

    def _read_console_url(self) -> str | None:
        """The console's address as an operator types it, or ``None`` when no console is served.

        Read from ``services/webservice.yaml`` — the file that STARTS the service — so a custom
        port or a configured ``auth.public_base_url`` is what the header shows. The bind address
        (often ``0.0.0.0``) is never a browsable one, so the fallback is loopback plus the port.
        """
        try:
            path = Path(self.node.config_dir) / 'services' / 'webservice.yaml'
            cfg = new_yaml().load(path.read_text(encoding='utf-8')) or {}
            if not cfg or DEFAULT_TAG not in cfg or not cfg[DEFAULT_TAG].get('frontend', False):
                return None
        except Exception:
            return None

        cfg = cfg[DEFAULT_TAG]
        base = str(((cfg.get('auth') or {}).get('public_base_url')) or '').strip()
        if not base:
            base = f"http://localhost:{cfg.get('port', 9876)}"
        return base.rstrip('/')

    def __rich__(self) -> Panel:
        config = self.service.get_config().get('header', {})
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", width=38, no_wrap=True)
        grid.add_column(justify="center", ratio=1, no_wrap=True)
        grid.add_column(justify="right", width=20, no_wrap=True)

        prefix = ""
        if self.service.is_multinode():
            prefix = f"{_('Cluster Master')} | " if self.node.master else f"{_('Cluster Agent')} | "
        left = Text(f"{prefix}DCSServerBot {self.node.bot_version}.{self.node.sub_version}",
                    style="bold white")

        parts = []
        if self.node.dcs_version:
            parts.append(Text(f"{_('DCS Version')}: {self.node.dcs_version}", style="white"))
        if self.web_url:
            parts.append(Text(f"{_('Web')}: {self.web_url}", style="underline white"))
        else:
            parts.append(Text(f"{_('Web')}: —", style="grey50"))
        centre = Text("  │  ", style="grey50").join(parts)

        now = datetime.now()
        right = Text(f"{now.strftime('%d.%m.%Y')} {now.strftime('%H:%M:%S')}", style="white")

        grid.add_row(left, centre, right)
        return Panel(
            grid,
            style=config.get("background", "white on navy_blue"),
            border_style=config.get("border", "white"),
            box=box.ROUNDED,
            padding=(0, 1),
        )


class ServersWidget:
    """Displaying List of Servers"""

    def __init__(self, service: Dashboard):
        self.service = service
        self.node = service.node
        self.bus = service.bus

    def __rich__(self) -> Panel:
        config = self.service.get_config().get("servers", {})
        show_node = bool(self.node.master and self.service.is_multinode())
        table = Table(
            expand=True,
            show_edge=False,
            pad_edge=False,
            box=box.SIMPLE_HEAD,
            header_style="bold white",
        )
        table.add_column(_("Status"), justify="left", width=11, no_wrap=True)
        table.add_column(_("Server Name"), justify="left", width=22, no_wrap=True)
        table.add_column(_("Mission Name"), justify="left", ratio=1, no_wrap=True)
        table.add_column(_("Players"), justify="right", width=9, no_wrap=True)
        table.add_column(_("Port / SRS"), justify="center", width=15, no_wrap=True)
        if show_node:
            table.add_column(_("Node"), justify="center", width=8, no_wrap=True)

        configured = running = offline = 0
        for server in self.bus.servers.values():
            # do not show remote servers on agents or if hide_remote_servers is set
            if (not self.node.master or config.get('hide_remote_servers', False)) and server.is_remote:
                continue
            configured += 1
            if server.status == Status.RUNNING:
                running += 1
            if server.status not in ACTIVE_STATES:
                offline += 1

            name = re.sub(self.bus.filter['server_name'], '', server.name).strip()
            dcs_port = getattr(getattr(server.instance, 'dcs_port', None), 'port', None) \
                or server.instance.locals.get('dcs_port', '-')
            srs_port = self.service.srs_ports.get(server.name, PORT_PLACEHOLDER)
            cells = [
                _status_cell(server.status),
                Text(name, style="bold white" if server.status == Status.RUNNING else "white"),
                _mission_cell(server, self.bus.filter['mission_name']),
                _player_cell(server),
                Text(f"{dcs_port} │ {srs_port}", style="white"),
            ]
            if show_node:
                cells.append(Text(server.node.name, style="white"))
            table.add_row(*cells)

        subtitle = Text(f"{configured} {_('configured')}   {running} {_('running')}   "
                        f"{offline} {_('offline')}", style="grey50")
        return Panel(
            table,
            subtitle=subtitle,
            subtitle_align="right",
            padding=(0, 1),
            style=config.get("background", "white on dark_blue"),
            border_style=config.get("border", "white"),
            box=box.ROUNDED,
        )


class NodeWidget:
    """Displaying Bot Info"""

    def __init__(self, service: Dashboard):
        self.service = service
        self.node = service.node
        self.bus = service.bus
        self.pool = service.pool
        self.log = service.log

    def __rich__(self) -> Panel:
        config = self.service.get_config().get("nodes", {})
        table = Table(expand=True, show_edge=False, pad_edge=False, box=box.SIMPLE_HEAD,
                      header_style="bold white")
        table.add_column(_("Node ([green]Master[/])"), justify="left")
        table.add_column(_("Servers"), justify="center")
        nodes: dict[str, Node | None] = {name: None for name in self.node.all_nodes.keys()}
        servers: dict[str, int] = dict()
        for server in self.bus.servers.values():
            nodes[server.node.name] = server.node
            if server.node.name not in servers:
                servers[server.node.name] = 0
            if server.status not in [Status.SHUTDOWN, Status.UNREGISTERED]:
                servers[server.node.name] += 1
        for name, node in nodes.items():  # type: Node
            if not node:
                table.add_row(Text(f"○ {name}", style="grey50"), Text(_("inactive"), style="grey50"))
            elif node.master:
                table.add_row(Text(f"● {node.name}", style="green3"),
                              Text(f"{servers.get(node.name, 0)}/{len(node.instances)}", style="white"))
            else:
                table.add_row(Text(f"● {node.name}", style="white"),
                              Text(f"{servers.get(node.name, 0)}/{len(node.instances)}", style="white"))
        return Panel(
            table,
            padding=(0, 1),
            style=config.get("background", "white on dark_blue"),
            border_style=config.get("border", "white"),
            box=box.ROUNDED,
        )


class RichQueueHandler(QueueHandler):
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # Keep traceback information for the dashboard widget.
        record = logging.makeLogRecord(record.__dict__.copy())
        message = record.getMessage()
        record.msg = message
        record.message = message
        record.args = None
        return record


class LogWidget:
    """Display log messages"""

    #: column widths shared by a log row and its projected traceback
    COLUMN_WIDTHS = (9, 6, 13, 20)

    def __init__(self, service: Dashboard):
        self.service = service
        self.queue = service.queue
        self.buffer: list[tuple[int, ConsoleRenderable]] = []
        self.handler = service.old_handler
        self.console = Console(record=True, file=io.StringIO())
        self.panel = self._build_panel("")
        self.previous_size = self.service.console.size
        self.cwd = os.getcwd()

    def _build_panel(self, content: ConsoleRenderable) -> Panel:
        config = self.service.get_config()
        return Panel(
            content,
            padding=(0, 1),
            style=config.get("log", {}).get("background", "white on grey15"),
            border_style=config.get("border", "white"),
            box=box.ROUNDED,
            expand=True,
        )

    def _row_grid(self) -> Table:
        time_width, level_width, module_width, source_width = self.COLUMN_WIDTHS
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(width=time_width, no_wrap=True)
        grid.add_column(width=level_width, no_wrap=True)
        grid.add_column(width=module_width, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_column(width=source_width, justify="right", no_wrap=True)
        return grid

    def _fill_initial_buffer(self, height: int) -> None:
        if self.buffer:
            return
        placeholder = self.console.render_str("")
        # Pre-fill with empty rows so the widget occupies its full area immediately.
        self.buffer = [(1, placeholder) for _i in range(max(1, height))]

    def _project_traceback(self, exc_tb) -> tuple[str, int, str, list[str]]:
        frames: list[tuple[str, int, str]] = []
        while exc_tb is not None:
            filename = exc_tb.tb_frame.f_code.co_filename
            if self.cwd in filename and all(x not in filename for x in ('\\venv\\', '\\.venv\\')):
                rel = os.path.relpath(filename, self.cwd)
                frames.append((rel, exc_tb.tb_lineno, exc_tb.tb_frame.f_code.co_name))
            exc_tb = exc_tb.tb_next

        if not frames:
            return "<unknown>", -1, "<unknown>", []

        file, line, func = frames[-1]
        trace = [f'File "{f}", line {ln}, in {fn}' for f, ln, fn in frames[-3:]]
        return file, line, func, trace

    def _normalize_record(self, record: logging.LogRecord) -> logging.LogRecord:
        record = logging.makeLogRecord(record.__dict__.copy())

        message = record.getMessage()

        # Make discord.py task exceptions readable.
        if message.startswith("Unhandled exception in internal background task"):
            coro_name = record.args[0] if isinstance(record.args, tuple) and record.args else None
            if coro_name:
                message = f"Unhandled exception in background task `{coro_name}`"
                record.msg = message
                record.args = None
            elif "%r" in message:
                record.msg = "Unhandled exception in background task"
                record.args = None

        if record.exc_info and record.exc_info != (None, None, None):
            _, _, exc_tb = record.exc_info
            file, line, func, _ = self._project_traceback(exc_tb)
            if line >= 0:
                record.pathname = os.path.join(self.cwd, file)
                record.filename = os.path.basename(file)
                record.lineno = line
                record.funcName = func

        return record

    def _measure_renderable_lines(self, renderable: ConsoleRenderable, width: int) -> int:
        with self.console.capture() as capture:
            self.console.print(renderable, width=width)
        return len(capture.get().splitlines()) or 1

    def _check_size_change(self) -> bool:
        """Check if the terminal has been resized"""
        current_size = self.service.console.size
        if current_size != self.previous_size:
            self.previous_size = current_size
            return True
        return False

    def _emit(self, record: logging.LogRecord) -> ConsoleRenderable:
        record = self._normalize_record(record)
        row = self._row_grid()
        row.add_row(
            Text(datetime.fromtimestamp(record.created).strftime("%H:%M:%S"), style="dim cyan"),
            _level_cell(record),
            Text(record.name.rsplit(".", 1)[-1], style="grey50"),
            Text.from_markup(escape(record.getMessage())),
            Text(f"{record.filename}:{record.lineno}", style="grey50"),
        )

        exc_info = record.exc_info if record.exc_info and record.exc_info != (None, None, None) else None
        if not exc_info:
            return row

        exc_type, exc_value, exc_traceback = exc_info
        file, line, _func, trace = self._project_traceback(exc_traceback)
        lines = [Text(f"{exc_type.__name__}: {exc_value}", style="red1")]
        lines.extend(Text(frame, style="grey50") for frame in trace)
        block = self._row_grid()
        for text in lines:
            block.add_row("", "", "", text, "")
        return Group(row, block)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        try:
            if not self.queue.empty() or self._check_size_change():
                max_displayable_height = max(1, options.max_height - 2)

                while not self.queue.empty():
                    record: logging.LogRecord = self.queue.get_nowait()
                    try:
                        log_renderable = self._emit(record)
                        renderable_lines = self._measure_renderable_lines(log_renderable, options.max_width)
                        self.buffer.append((renderable_lines, log_renderable))
                    except Exception as ex:
                        fallback = self.console.render_str(f"[red]Log render failed[/red]: {ex!r}")
                        fallback_lines = self._measure_renderable_lines(fallback, options.max_width)
                        self.buffer.append((fallback_lines, fallback))

                total_height_used = sum(lines for lines, _r in self.buffer)
                while total_height_used > max_displayable_height and self.buffer:
                    removed_lines, _r = self.buffer.pop(0)
                    total_height_used -= removed_lines

                content = [renderable for _lines, renderable in self.buffer]
                content.extend(self.console.render_str("") for _i in range(max_displayable_height - total_height_used))

                self.panel = self._build_panel(Group(*content))

            yield self.panel
        except Exception as ex:
            yield Panel(
                f"[red]Dashboard log widget error:[/red] {ex!r}",
                style="white on dark_red",
                border_style="red",
                expand=True,
            )


@ServiceRegistry.register(depends_on=[ServiceBus])
class Dashboard(Service):

    def __init__(self, node: NodeImpl):
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
        #: cached SRS port per server name; filled by ``refresh_srs_ports`` over the RPC surface,
        #: because the master's ``instance.locals`` copy is stale or absent for a remote server
        self.srs_ports: dict[str, str] = {}
        #: the status each server had at the previous frame; a change triggers a refetch
        self.srs_status: dict[str, Status] = {}
        #: guards against overlapping refreshes
        self.srs_refreshing = False
        #: the refresh scheduled from the update loop, cancelled when the service stops
        self.srs_refresh_task: asyncio.Task | None = None
        #: set when the ROLE changed; the update loop rebuilds the layout on its next tick
        self._layout_dirty = False

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
            Layout(self.servers_widget, name="servers", ratio=1,
                   minimum_size=len(self.bus.servers) + 4),
            Layout(self.log_widget, name="log", ratio=2, minimum_size=8)
        )
        if self.node.master and self.is_multinode():
            layout['servers'].split_row(
                Layout(self.servers_widget, name="servers_list", ratio=2),
                Layout(NodeWidget(self), name="nodes", size=34, ratio=0)
            )
        return layout

    def hook_logging(self):
        self.queue = Queue()
        self.log_handler = RichQueueHandler(self.queue)
        self.log_handler.setLevel(logging.INFO)
        for handler in self.log.root.handlers:
            if isinstance(handler, RichHandler) and not isinstance(handler, RotatingFileHandler):
                self.old_handler = handler
                for rec in getattr(handler, "early_records", ()):
                    self.queue.put_nowait(rec)
                if hasattr(handler, "early_records"):
                    handler.early_records.clear()
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
        self._layout_dirty = False
        self.stop_event.clear()
        self.update_task = asyncio.create_task(self.update())

    async def stop(self):
        if not self.node.config.get('use_dashboard', True):
            return
        self.stop_event.set()
        if self.update_task and self.update_task is not asyncio.current_task():
            await self.update_task
        task = getattr(self, 'srs_refresh_task', None)
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.unhook_logging()
        self.console.clear()
        await super().stop()

    async def switch(self, master: bool):
        await super().switch(master)
        # The layout is the only role-dependent part (a master adds the node column and the node
        # widgets), so the running update loop rebuilds it. Cycling this service instead would go
        # through the registry cascade: the singleton is replaced, this instance is orphaned and
        # BOTH of them render into the same terminal.
        self._layout_dirty = True
        if self.running and self.update_task and self.update_task.done():
            # The renderer died on an exception and stopped its loop; revive it in place, which is
            # what the old stop/start cycle did as a side effect.
            self.stop_event.clear()
            self.update_task = asyncio.create_task(self.update())

    def _srs_status_changed(self) -> bool:
        """True when any server's status differs from the previous frame's snapshot."""
        bus = getattr(self, 'bus', None)
        if bus is None:
            return False
        return {name: server.status for name, server in bus.servers.items()} \
            != getattr(self, 'srs_status', {})

    async def _run_srs_refresh(self):
        try:
            await refresh_srs_ports(self)
        except Exception:
            self.log.exception("SRS port refresh failed")

    async def update(self):
        try:
            previous_server_count = len(self.bus.servers)
            with Live(self.layout, refresh_per_second=1, screen=True) as live:
                while not self.stop_event.is_set():
                    current_server_count = len(self.bus.servers)
                    if self._layout_dirty or current_server_count != previous_server_count:
                        self.layout = self.create_layout()
                        live.update(self.layout)
                        self._layout_dirty = False
                        previous_server_count = current_server_count
                    # A status change is the only thing that can move the SRS port, so the RPC is
                    # scheduled off the render loop and only when one is seen.
                    if not getattr(self, 'srs_refreshing', False) and self._srs_status_changed():
                        self.srs_refresh_task = asyncio.create_task(self._run_srs_refresh())
                    await asyncio.sleep(0.5)
        except Exception:
            self.log.exception("Dashboard update loop crashed")
            self.stop_event.set()
