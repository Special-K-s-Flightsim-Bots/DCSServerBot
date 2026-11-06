from __future__ import annotations

import asyncio
import io
import logging
import os
import re

from core import Service, ServiceRegistry, Status, NodeImpl, get_translation
from datetime import datetime
from logging.handlers import QueueHandler, RotatingFileHandler
from queue import Queue
from rich import box
from rich.align import Align
from rich.console import Console, ConsoleOptions, RenderResult, ConsoleRenderable, Group
from rich.layout import Layout
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from typing import TYPE_CHECKING

from ..servicebus import ServiceBus

if TYPE_CHECKING:
    from core import Node

__all__ = [
    "Dashboard"
]

_ = get_translation("dashboard")

DCS_LOGO = [
    "██████╗  ██████╗███████╗    ██╗    ██╗ ██████╗ ██████╗ ██╗     ██████╗ ",
    "██╔══██╗██╔════╝██╔════╝    ██║    ██║██╔═══██╗██╔══██╗██║     ██╔══██╗",
    "██║  ██║██║     ███████╗    ██║ █╗ ██║██║   ██║██████╔╝██║     ██║  ██║",
    "██║  ██║██║     ╚════██║    ██║███╗██║██║   ██║██╔══██╗██║     ██║  ██║",
    "██████╔╝╚██████╗███████║    ╚███╔███╔╝╚██████╔╝██║  ██║███████╗██████╔╝",
    "╚═════╝  ╚═════╝╚══════╝     ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═════╝ ",
]

STATUS_ICONS = {
    Status.RUNNING: ("🟢", "bold bright_green"),
    Status.LOADING: ("🟡", "bold bright_yellow"),
    Status.PAUSED: ("🟡", "bold gold1"),
    Status.STOPPED: ("🟠", "bold orange3"),
    Status.SHUTDOWN: ("🔴", "bold bright_red"),
    Status.SHUTTING_DOWN: ("🟡", "bold yellow"),
    Status.UNREGISTERED: ("⚪", "dim grey62"),
}


def _clean_log_prefix(msg: str) -> str:
    msg = re.sub(r"^\s*(?:=>|->|-)\s*", "", msg.strip())
    for emoji in ("🟢", "🟡", "🔴", "🟠", "⚪"):
        msg = msg.replace(emoji, "")
    return re.sub(r"\s{2,}", " ", msg).strip()


def _log_badge_and_module(record: logging.LogRecord, raw_msg: str, clean_msg: str) -> tuple[str, str, str]:
    """Return (status_badge, module_label, styled_message) for symmetrical 4-column log rows."""
    fn = (record.filename or "").lower()
    low = raw_msg.lower()

    if "webservice" in fn or "local.py" in fn or "routes.py" in fn or "web " in low or "signed in" in low or "signed out" in low:
        mod = _("WEB UI")
    elif "dcsserverbot" in fn or "discord" in low:
        mod = _("DISCORD")
    elif "commands.py" in fn or "scheduler" in fn:
        mod = _("SCHEDULER")
    elif "srs" in low or "srs" in fn:
        mod = _("SRS RADIO")
    elif "extension" in fn or "loganalyser" in low:
        mod = _("EXTENSION")
    elif "server" in fn or "service.py" in fn:
        mod = _("SERVER")
    else:
        mod = _("SYSTEM")

    if record.levelno >= logging.ERROR:
        badge = f"🔴 [bold bright_red]{_('ERROR'):<6}[/]"
        msg_styled = f"[bright_red]{clean_msg}[/]"
    elif record.levelno >= logging.WARNING:
        badge = f"🟡 [bold bright_yellow]{_('WARN'):<6}[/]"
        msg_styled = f"[bright_yellow]{clean_msg}[/]"
    elif "registered as down" in low or "shut down" in low or "stopped" in low:
        badge = f"🔴 [bold bright_red]{_('DOWN'):<6}[/]"
        msg_styled = f"[white]{clean_msg}[/]"
    elif "starting up" in low or "searching for" in low or "registering" in low or "preparing" in low or "loading" in low:
        badge = f"🟡 [bold gold1]{_('INIT'):<6}[/]"
        msg_styled = f"[bright_white]{clean_msg}[/]"
    elif "registered as up" in low or "started" in low or "signed in" in low or "loaded" in low:
        badge = f"🟢 [bold bright_green]{_('ACTIVE'):<6}[/]"
        msg_styled = f"[bold bright_white]{clean_msg}[/]"
    else:
        badge = f"🟢 [bold bright_cyan]{_('INFO'):<6}[/]"
        msg_styled = f"[white]{clean_msg}[/]"

    msg_styled = re.sub(r'"([^"]+)"', r'[bold gold1]"\1"[/]', msg_styled)
    return badge, mod, msg_styled


class HeaderWidget:
    """Display header with clock."""

    def __init__(self, service: Dashboard):
        self.service = service
        self.node = service.node
        self.log = service.log

    def __rich__(self) -> Panel:
        config = self.service.get_config().get('header', {})
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", width=34, no_wrap=True)
        grid.add_column(justify="center", ratio=1, no_wrap=True)
        grid.add_column(justify="right", width=30, no_wrap=True)

        prefix = ""
        if self.service.is_multinode():
            if self.node.master:
                prefix = f"{_('Cluster Master')} | "
            else:
                prefix = f"{_('Cluster Agent')} | "
        left_msg = f"🟢 [bold bright_white]{prefix}DCSServerBot v{self.node.bot_version}.{self.node.sub_version}[/]"

        center_msg = f"[bold bright_cyan]{_('Web')}:[/] [underline bright_white]localhost:9876[/]"
        if self.node.dcs_version:
            center_msg = f"[bold bright_cyan]{_('DCS Version')}:[/] [bright_white]{self.node.dcs_version}[/]  [dim cyan]│[/]  " + center_msg

        now = datetime.now()
        time_str = now.strftime("%H:%M:%S").replace(":", "[blink]:[/]")
        right_msg = f"[bold bright_white]{now.strftime('%d.%m.%Y')} • {time_str}[/]"

        grid.add_row(left_msg, center_msg, right_msg)
        return Panel(
            grid,
            style=config.get("background", "white on deep_sky_blue4"),
            border_style=config.get("border", "bright_cyan"),
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
        table = Table(
            expand=True,
            show_edge=False,
            pad_edge=False,
            header_style="bold gold1",
            border_style="bright_blue",
        )
        table.add_column(_("Status"), justify="left", width=15, no_wrap=True)
        table.add_column(_("Server Name"), justify="left", width=29, no_wrap=True)
        table.add_column(_("Mission Name"), justify="left", ratio=1, no_wrap=True)
        table.add_column(_("Players"), justify="center", width=10, no_wrap=True)
        table.add_column(_("Port / SRS"), justify="center", width=18, no_wrap=True)
        if self.service.node.master and self.service.is_multinode():
            table.add_column(_("Node"), justify="center", width=12, no_wrap=True)

        for server_name, server in self.bus.servers.items():
            # do not show remote servers on agents or if hide_remote_servers is set
            if (not self.node.master or config.get('hide_remote_servers', False)) and server.is_remote:
                continue
            name = re.sub(self.bus.filter['server_name'], '', server.name).strip()
            icon, style = STATUS_ICONS.get(server.status, ("⚪", "white"))
            status_label = f"{icon} [{style}]{_(server.status.name.title())}[/]"

            if server.current_mission:
                mission_raw = re.sub(self.bus.filter['mission_name'], '', server.current_mission.name).strip()
                mission_name = f"[bold bright_white]{mission_raw}[/]"
            else:
                _m = server.settings.get('lastSelectedMission') or (
                    server.settings.get('missionList', [''])[0] if server.settings.get('missionList') else ''
                )
                mission_raw = os.path.splitext(os.path.basename(_m))[0] if _m else '—'
                mission_name = f"[dim white]{mission_raw}[/]"

            if server.status in (Status.RUNNING, Status.PAUSED, Status.LOADING):
                n = server.num_players if server.num_players is not None else len(server.get_active_players())
                max_p = max(int(server.settings.get('maxPlayers', 60)) - 1, 1) if int(server.settings.get('maxPlayers', 60)) == 61 else int(server.settings.get('maxPlayers', 60))
                if n > 0:
                    num_players = f"[bold bright_green]{n:2d} / {max_p}[/]"
                else:
                    num_players = f"[bright_white]{n:2d} / {max_p}[/]"
            else:
                num_players = "[dim]  —   [/]"

            dcs_port = getattr(getattr(server.instance, 'dcs_port', None), 'port', None) or server.instance.locals.get('dcs_port', '-')
            srs_port = server.instance.locals.get('extensions', {}).get('SRS', {}).get('port', '-')
            ports_str = f"[bright_cyan]{dcs_port}[/] [dim]│[/] [gold1]SRS:{srs_port}[/]"

            name_styled = f"[bold bright_white]{name}[/]" if server.status == Status.RUNNING else f"[white]{name}[/]"

            if self.service.node.master and self.service.is_multinode():
                table.add_row(status_label, name_styled, mission_name, num_players, ports_str, server.node.name)
            else:
                table.add_row(status_label, name_styled, mission_name, num_players, ports_str)

        return Panel(
            table,
            title=f"[bold bright_cyan] 🖥️  {_('DCS SERVER STATUS')} [/]",
            title_align="left",
            subtitle=f"[dim white]🟢 {_('Running')}   🟡 {_('Loading')}   🔴 {_('Shutdown')}[/]",
            subtitle_align="right",
            padding=(0, 1),
            style=config.get("background", "white on navy_blue"),
            border_style=config.get("border", "bright_cyan"),
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
        table = Table(expand=True, show_edge=False)
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
                table.add_row(f"🔴 [grey54]{name}[/]", f"[grey54]{_('inactive')}[/]")
            elif node.master:
                table.add_row(f"🟢 [green]{node.name}[/]", f"{servers[node.name]}/{len(node.instances)}")
            else:
                table.add_row(f"🟢 {node.name}", f"{servers[node.name]}/{len(node.instances)}")
        return Panel(
            table,
            title=f"[bold bright_cyan] {_('Nodes')} [/]",
            padding=1,
            style=config.get("background", "white on navy_blue"),
            border_style=config.get("border", "bright_cyan"),
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

    def __init__(self, service: Dashboard):
        self.service = service
        self.queue = service.queue
        self.buffer: list[tuple[int, ConsoleRenderable]] = []
        self.handler = service.old_handler
        self.console = Console(record=True, file=io.StringIO())
        config = self.service.get_config().get("log", {})
        self.panel = Panel(
            "",
            title=f"[bold bright_cyan] 📋  {_('LIVE SYSTEM LOG')} [/]",
            title_align="left",
            subtitle=f"[dim white]{_('Time   │   Status   │   Module   │   Event Detail')}[/]",
            subtitle_align="right",
            padding=(0, 1),
            style=config.get("background", "white on grey11"),
            border_style=config.get("border", "bright_cyan"),
            box=box.ROUNDED,
            expand=True,
        )
        self.previous_size = self.service.console.size
        self.cwd = os.getcwd()

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

    def _emit(self, record: logging.LogRecord) -> ConsoleRenderable | None:
        record = self._normalize_record(record)
        raw_msg = getattr(record, "message", None) or record.getMessage()
        if any(s in raw_msg for s in (
            "signed in via the local backend",
            "Registrar: '",
            "Admin web shell installed:",
            "Admin web UI auth:",
            "PluginManager:",
            "Python version ",
            "Web UI session: session.https_only",
        )):
            return None

        clean_msg = _clean_log_prefix(raw_msg)
        badge, mod, msg_styled = _log_badge_and_module(record, raw_msg, clean_msg)
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        row = Table.grid(expand=True)
        row.add_column(width=8, no_wrap=True)
        row.add_column(width=3, justify="center", no_wrap=True)
        row.add_column(width=9, no_wrap=True)
        row.add_column(width=3, justify="center", no_wrap=True)
        row.add_column(width=11, no_wrap=True)
        row.add_column(width=3, justify="center", no_wrap=True)
        row.add_column(ratio=1, no_wrap=True)

        row.add_row(
            f"[dim bright_cyan]{ts}[/]",
            "[dim blue]│[/]",
            badge,
            "[dim blue]│[/]",
            f"[bold cyan]{mod:<11}[/]",
            "[dim blue]│[/]",
            msg_styled,
        )

        exc_info = record.exc_info if record.exc_info and record.exc_info != (None, None, None) else None
        if exc_info:
            exc_type, exc_value, exc_traceback = exc_info
            file, line, _func, trace = self._project_traceback(exc_traceback)
            return Group(
                row,
                self.console.render_str(f"           [red]{exc_type.__name__}[/red]: {exc_value} [dim](at {file}:{line})[/dim]"),
                *( [self.console.render_str("[dim]" + "\n".join(trace) + "[/dim]")] if trace else [] ),
            )

        return row

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        try:
            if not self.queue.empty() or self._check_size_change():
                config = self.service.get_config()
                max_displayable_height = max(1, options.max_height - 2)

                while not self.queue.empty():
                    record: logging.LogRecord = self.queue.get_nowait()
                    try:
                        log_renderable = self._emit(record)
                        if log_renderable is None:
                            continue
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

                self.panel = Panel(
                    Group(*content),
                    title=f"[bold bright_cyan] 📋  {_('LIVE SYSTEM LOG')} [/]",
                    title_align="left",
                    subtitle=f"[dim white]{_('Time   │   Status   │   Module   │   Event Detail')}[/]",
                    subtitle_align="right",
                    padding=(0, 1),
                    style=config.get("log", {}).get("background", "white on grey11"),
                    border_style=config.get("border", "bright_cyan"),
                    box=box.ROUNDED,
                    expand=True,
                )

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
            Layout(self.servers_widget, name="main", size=len(self.bus.servers) + 5),
            Layout(self.log_widget, name="log", ratio=2, minimum_size=5)
        )
        if self.node.master and self.is_multinode():
            servers = ServersWidget(self)
            nodes = NodeWidget(self)
            layout['main'].split_row(Layout(servers, name="servers", ratio=2), Layout(nodes, name="nodes"))
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

    async def _play_intro(self) -> None:
        if not self.get_config().get("show_intro", True):
            return
        try:
            steps = [
                _("INITIALIZING TACTICAL HUD & TELEMETRY SYSTEMS..."),
                _("CONNECTING TO POSTGRESQL TACTICAL DATABASE..."),
                _("CALIBRATING SIMPLE RADIO STANDALONE (SRS)..."),
                _("SYNCING DCS WORLD DEDICATED SERVERS..."),
                _("TACTICAL COMMAND & DASHBOARD ONLINE!"),
            ]
            radar_frames = ["◜", "◝", "◞", "◟"]
            compass_tapes = [
                "◄── 330 ─── 345 ─── [ HDG 000° N ] ─── 015 ─── 030 ──►",
                "◄── 335 ─── 350 ─── [ HDG 005° N ] ─── 020 ─── 035 ──►",
                "◄── 340 ─── 355 ─── [ HDG 010° N ] ─── 025 ─── 040 ──►",
                "◄── 335 ─── 350 ─── [ HDG 005° N ] ─── 020 ─── 035 ──►",
            ]
            jet_art = [
                r"                      __|__                      ",
                r"               --o--o--(_)--o--o--               ",
                f"         ───────═══╡  {_('HUD LOCK')}  ╞═══───────      ",
            ]
            subtitle_banner = "  ".join(_("DEDICATED SERVER CONTROL SYSTEM"))
            bot_ver = f"DCSServerBot v{self.node.bot_version}.{self.node.sub_version}"
            node_info = f"{self.node.name} ({len(self.node.instances)})"

            with Live(console=self.console, refresh_per_second=20, transient=True) as live:
                for i in range(16):
                    pct = min(100, int((i + 1) * (100 / 16)))
                    status_txt = steps[min(len(steps) - 1, i // 3)]
                    radar = radar_frames[i % len(radar_frames)]
                    compass = compass_tapes[i % len(compass_tapes)]

                    logo_colors = ["bright_cyan", "cyan", "deep_sky_blue1", "dodger_blue1", "gold1", "bright_yellow"]
                    logo_lines = [
                        f"[bold {logo_colors[(idx + i) % len(logo_colors)]}]{line}[/]"
                        for idx, line in enumerate(DCS_LOGO)
                    ]

                    jet_offset = " " * ((i % 9) - 4 + 4)
                    bar_width = 44
                    filled = int(bar_width * pct / 100)
                    bar = "[bold bright_green]" + "█" * filled + "[/][dim cyan]" + "░" * (bar_width - filled) + "[/]"

                    telem = (
                        f"[bold bright_cyan]{radar} {_('RADAR')}:[/] [bright_green]{_('ACTIVE')}[/]   │   "
                        f"[bold bright_cyan]{_('DATALINK')}:[/] [bright_green]LINK-16 OK[/]   │   "
                        f"[bold bright_cyan]{_('NODE')}:[/] [underline bright_white]{node_info}[/]"
                    )

                    hud_block = [
                        f"[bold bright_green]{compass}[/]",
                        "",
                        *logo_lines,
                        f"[bold gold1]━━━━━━  {subtitle_banner}  ━━━━━━[/]",
                        "",
                        f"[bold bright_cyan]{jet_offset}{jet_art[0]}[/]",
                        f"[bold bright_white]{jet_offset}{jet_art[1]}[/]",
                        f"[bold bright_green]{jet_offset}{jet_art[2]}[/]",
                        "",
                        telem,
                        "",
                        f"{bar}  [bold gold1]{pct:3d}%[/]",
                        f"[bold bright_yellow]⚡ {status_txt}[/]",
                    ]

                    panel = Panel(
                        Align.center("\n".join(hud_block)),
                        title=f"[bold bright_cyan] ✈️  DCS WORLD  •  {bot_ver}  •  {_('TACTICAL HUD')} [/]",
                        subtitle=f"[dim bright_cyan]{_('MIL-STD-1553B  •  COCKPIT INITIALIZATION SEQUENCE')}[/]",
                        style="white on #050d1a",
                        border_style="bright_cyan",
                        box=box.DOUBLE_EDGE,
                        padding=(1, 2),
                    )
                    live.update(panel)
                    await asyncio.sleep(0.04)

                # Phase 2: Dynamic Tactical Server Command Center overview from default bot instances/servers
                lang_code = str(self.node.config.get("language", "en")).upper()
                header_logo = [f"[bold bright_cyan]{line}[/]" for line in DCS_LOGO[:3]] + [
                    f"[bold gold1]{line}[/]" for line in DCS_LOGO[3:]
                ]
                cmd_header = [
                    "[bold bright_green]◄── 330 ─── 345 ─── [ HDG 000° N  •  ALT 25,000 FT  •  MACH 0.92 ] ─── 015 ─── 030 ──►[/]",
                    "",
                    *header_logo,
                    f"[bold gold1]━━━━━━  {_('MISSION CONTROL CENTER')}  •  {_('ACTIVE LANGUAGE')}: {lang_code} [🟢]  ━━━━━━[/]",
                    "",
                ]

                tbl = Table(
                    show_header=True,
                    header_style="bold gold1",
                    border_style="bright_cyan",
                    box=box.ROUNDED,
                    style="on #09172e",
                    padding=(0, 2),
                )
                tbl.add_column(_("ID"), justify="center", width=8)
                tbl.add_column(_("INSTANCE / OPERATION"), justify="left", width=34)
                tbl.add_column(_("TARGET SERVERS & PORT"), justify="left", width=38)

                if self.bus and self.bus.servers:
                    for idx, (srv_name, srv) in enumerate(self.bus.servers.items(), start=1):
                        inst_name = getattr(srv.instance, "name", srv_name)
                        dcs_port = getattr(getattr(srv.instance, "dcs_port", None), "port", None) or srv.instance.locals.get("dcs_port", "-")
                        srs_port = srv.instance.locals.get("extensions", {}).get("SRS", {}).get("port", "-")
                        tbl.add_row(
                            f"🟢 [bold bright_green][{idx}][/]",
                            f"[bold bright_white]{inst_name}[/]",
                            f"[bright_cyan]{srv_name}  (:{dcs_port} │ SRS:{srs_port})[/]",
                        )
                else:
                    for idx, inst in enumerate(self.node.instances.values(), start=1):
                        dcs_port = inst.locals.get("dcs_port", "-")
                        srs_port = inst.locals.get("extensions", {}).get("SRS", {}).get("port", "-")
                        tbl.add_row(
                            f"🟢 [bold bright_green][{idx}][/]",
                            f"[bold bright_white]{inst.name}[/]",
                            f"[bright_cyan]{inst.name}  (:{dcs_port} │ SRS:{srs_port})[/]",
                        )

                cmd_group = Group(
                    Align.center("\n".join(cmd_header)),
                    Align.center(tbl),
                )
                cmd_panel = Panel(
                    cmd_group,
                    title=f"[bold bright_cyan] ✈️  DCS WORLD  •  {_('TACTICAL SERVER COMMAND CENTER')} [/]",
                    subtitle=f"[dim bright_green]🟢 {_('MASTER HUD READY')}   │   🌐 {_('NODE')}: {node_info}[/]",
                    style="white on #050d1a",
                    border_style="bright_cyan",
                    box=box.DOUBLE_EDGE,
                    padding=(1, 2),
                )
                live.update(cmd_panel)
                await asyncio.sleep(0.8)
        except Exception:
            pass

    async def start(self):
        if not self.node.config.get('use_dashboard', True):
            return
        await super().start()
        self.bus = ServiceRegistry.get(ServiceBus)
        self.hook_logging()
        await self._play_intro()
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
                    await asyncio.sleep(0.5)
        except Exception:
            self.log.exception("Dashboard update loop crashed")
            self.stop_event.set()
