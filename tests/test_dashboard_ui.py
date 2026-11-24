"""Pins for the console dashboard's widgets.

Every assertion here is driven through a real ``Console(record=True, width=132)`` render of the
actual widgets (stubbed service/node/bus), so the pins describe what an operator sees rather than
what a helper returns.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from pathlib import Path
from queue import Queue
from time import monotonic
from types import SimpleNamespace

import pytest
from rich.console import Console

from core import Port, PortType, Status
from core.utils.helper import ThreadSafeDict
from services.dashboard import service as dashboard_module
from services.dashboard.service import Dashboard, HeaderWidget, LogWidget, NodeWidget, ServersWidget

REPO = Path(__file__).resolve().parent.parent
SERVICE_PY = REPO / "services" / "dashboard" / "service.py"

#: Emoji that must never reach the terminal: they are double-width in Windows Terminal and shift
#: every column to their right.
EMOJI = (
    "🟢🟡🔴🟠⚪📋🖥✈🌐⚡🟣🔵🟤🔶🔷🔸🔹💠🎯📡🚀⬛⬜🟨🟩🟥🧭⏱✔✖❗❓\ufe0f"
)

TACTICAL_VOCABULARY = ("HDG", "RADAR", "LINK-16", "MIL-STD", "TACTICAL")


# --------------------------------------------------------------------------- stubs


class _Mission:
    def __init__(self, name: str):
        self.name = name


class _Instance:
    def __init__(self, dcs_port: int = 10308):
        self.dcs_port = SimpleNamespace(port=dcs_port)
        #: the master's ``nodes.yaml`` copy of the instance block — it carries no SRS port
        self.locals = {"dcs_port": dcs_port}


class _Extension:
    """Records ``run_on_extension`` calls and answers with a canned ``get_ports`` result."""

    def __init__(self, ports: dict | None = None, error: Exception | None = None):
        self.ports = ports
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, extension: str, method: str, **kwargs):
        self.calls.append((extension, method))
        if self.error is not None:
            raise self.error
        return dict(self.ports or {})


class _ServerNode:
    """The node a server reports through: ``name`` for the row, ``master``/``instances`` for NodeWidget."""

    def __init__(self, name: str = "master", master: bool = True):
        self.name = name
        self.master = master
        self.instances = {"SRS-1": object()}


class _Server:
    def __init__(self, name: str, status: Status, *, node_name: str = "master",
                 mission: str | None = None, players: int = 0, max_players: int = 60,
                 last_selected: str | None = None, remote: bool = False,
                 srs: "_Extension | None" = None):
        self.name = name
        self.status = status
        self.node = _ServerNode(node_name, master=node_name == "master")
        self.instance = _Instance()
        #: the SRS extension's ``get_ports`` on the node that owns this server
        self.run_on_extension = srs or _Extension({})
        self.is_remote = remote
        self.num_players = players
        self.settings = {"maxPlayers": max_players}
        if last_selected:
            self.settings["lastSelectedMission"] = last_selected
        self.current_mission = _Mission(mission) if mission else None

    def get_active_players(self) -> list:
        return []

    def __eq__(self, other) -> bool:
        """``ServerImpl`` is unhashable: it defines ``__eq__`` by name and no ``__hash__``."""
        return isinstance(other, _Server) and self.name == other.name

    __hash__ = None  # type: ignore[assignment]


class _Node:
    def __init__(self, config_dir: Path, *, master: bool = True, multinode: bool = False,
                 dcs_version: str | None = "2.9.8.110"):
        self.name = "master" if master else "agent-1"
        self.master = master
        self.bot_version = "3.10"
        self.sub_version = 4
        self.dcs_version = dcs_version
        self.config_dir = str(config_dir)
        self.instances = {"SRS-1": object()}
        self.all_nodes = {"master": None, "agent-1": None} if multinode else {"master": None}


class _Service:
    """The attributes the widgets read off ``Dashboard``."""

    def __init__(self, node: _Node, servers: dict, config: dict | None = None,
                 console: Console | None = None, records=()):
        self.node = node
        self.bus = SimpleNamespace(servers=ThreadSafeDict(servers),
                                   filter={"server_name": "", "mission_name": ""})
        self.pool = None
        self.log = logging.getLogger("dashboard.test")
        self.queue = Queue()
        for record in records:
            self.queue.put_nowait(record)
        self.old_handler = None
        self.console = console or Console(file=io.StringIO(), width=132, height=40)
        self._config = config if config is not None else {}
        #: the SRS port cache the widgets render from, filled by the refresh coroutine
        self.srs_ports: dict[str, str] = {}
        self.srs_status: dict[str, Status] = {}
        self.srs_retry: dict[str, tuple[int, float]] = {}
        self.srs_refreshing = False
        self.stop_event = asyncio.Event()

    def get_config(self, *args, **kwargs) -> dict:
        return self._config

    def is_multinode(self) -> bool:
        return len(self.node.all_nodes) > 1


def _webservice_config(config_dir: Path, url: str = "http://dcs.example.org:9876") -> None:
    path = config_dir / "services"
    path.mkdir(parents=True, exist_ok=True)
    (path / "webservice.yaml").write_text(
        f"DEFAULT:\n  frontend: true\n  port: 9876\n  auth:\n    public_base_url: {url}\n",
        encoding="utf-8")


def _console() -> Console:
    return Console(record=True, file=io.StringIO(), width=132, height=40)


def _render(*renderables) -> tuple[Console, str]:
    """Print the renderables and return the console plus the exact characters it emitted."""
    console = _console()
    for renderable in renderables:
        console.print(renderable)
    return console, "".join(segment.text for segment in console._record_buffer)


def _color_of(console: Console, cell: str) -> set[str]:
    """The foreground colour names of the segments whose stripped text IS ``cell``."""
    return {segment.style.color.name for segment in console._record_buffer
            if segment.text.strip() == cell and segment.style and segment.style.color}


def _style_of(console: Console, cell: str):
    """The style of the first segment whose stripped text IS ``cell``."""
    for segment in console._record_buffer:
        if segment.text.strip() == cell and segment.style is not None:
            return segment.style
    raise AssertionError(f"no segment with the text {cell!r}")


def _record(message: str = "hello", *, level: int = logging.INFO,
            name: str = "plugins.modmanager.commands", filename: str = "nodes.py",
            lineno: int = 194) -> logging.LogRecord:
    return logging.LogRecord(name, level, f"/opt/bot/plugins/{filename}", lineno, message, None, None)


def _servers_service(tmp_path: Path, **kwargs) -> _Service:
    node = _Node(tmp_path, master=True, multinode=True)
    servers = {
        "SRS-1": _Server("SRS-1", Status.RUNNING, mission="Nellis_v2.miz", players=12),
        "SRS-2": _Server("SRS-2", Status.STOPPED, last_selected="/missions/Falklands_v1.miz"),
    }
    return _Service(node, servers, **kwargs)


# --------------------------------------------------------------------------- no decoration


def test_no_emoji_reaches_the_rendered_dashboard(tmp_path):
    _webservice_config(tmp_path)
    service = _servers_service(tmp_path, records=[_record(), _record(level=logging.WARNING)])
    _console_, text = _render(HeaderWidget(service), ServersWidget(service), NodeWidget(service),
                              LogWidget(service))

    present = sorted({char for char in text if char in EMOJI})
    assert present == [], f"emoji reached the terminal: {present}"
    astral = sorted({char for char in text if ord(char) >= 0x1F000})
    assert astral == [], f"astral emoji reached the terminal: {astral}"
    assert "\ufe0f" not in text, "an emoji variation selector reached the terminal"


def test_no_emoji_in_the_source(tmp_path):
    source = SERVICE_PY.read_text(encoding="utf-8")
    present = sorted({char for char in source if char in EMOJI or ord(char) >= 0x1F000})
    assert present == [], f"emoji left in the source: {present}"


def test_tactical_vocabulary_is_gone_from_the_render_and_the_source(tmp_path):
    _webservice_config(tmp_path)
    service = _servers_service(tmp_path, records=[_record()])
    _console_, text = _render(HeaderWidget(service), ServersWidget(service), NodeWidget(service),
                              LogWidget(service))
    source = SERVICE_PY.read_text(encoding="utf-8")
    for token in TACTICAL_VOCABULARY:
        assert token not in text, f"{token!r} is still rendered"
        assert token not in source, f"{token!r} is still in service.py"


def test_the_splash_is_gone(tmp_path):
    assert not hasattr(Dashboard, "_play_intro"), "the splash method still exists"
    assert not hasattr(dashboard_module, "DCS_LOGO"), "the logo banner still exists"


# --------------------------------------------------------------------------- header


def test_header_carries_identity_without_a_splash(tmp_path):
    _webservice_config(tmp_path)
    _console_, text = _render(HeaderWidget(_servers_service(tmp_path)))
    assert "Cluster Master | DCSServerBot 3.10.4" in text
    assert "DCS Version: 2.9.8.110" in text
    assert "Web: http://dcs.example.org:9876" in text


def test_header_clock_is_plain_white(tmp_path):
    _webservice_config(tmp_path)
    console, _text = _render(HeaderWidget(_servers_service(tmp_path)))
    stamp = [segment for segment in console._record_buffer
             if segment.text.strip() and segment.text.strip()[0].isdigit() and ":" in segment.text
             and "." in segment.text]
    assert stamp, "the clock is missing from the header"
    for segment in stamp:
        assert segment.style.color.name == "white", "the clock is not plain white"


# --------------------------------------------------------------------------- servers


def test_server_status_cells(tmp_path):
    _console_, text = _render(ServersWidget(_servers_service(tmp_path)))
    assert "● Running" in text
    assert "○ Stopped" in text


def test_server_status_cell_colours(tmp_path):
    console, _text = _render(ServersWidget(_servers_service(tmp_path)))
    assert _color_of(console, "● Running") == {"green3"}
    assert _color_of(console, "○ Stopped") == {"grey50"}


def test_mission_of_a_stopped_server_is_dim_and_not_bold(tmp_path):
    console, text = _render(ServersWidget(_servers_service(tmp_path)))
    assert "Falklands_v1" in text
    dim = [segment.style for segment in console._record_buffer if "Falklands_v1" in segment.text]
    assert dim, "the stopped server's configured mission is missing"
    for style in dim:
        assert style.color.name == "grey50"
        assert not style.bold


def test_players_only_for_an_active_server(tmp_path):
    console, text = _render(ServersWidget(_servers_service(tmp_path)))
    # maxPlayers 60 leaves 59 slots: the bot's observer holds one of them
    assert "12 / 59" in text
    assert text.count("—") >= 1, "the stopped server shows no placeholder in Players"


def test_players_are_clamped_to_the_adjusted_maximum(tmp_path):
    node = _Node(tmp_path, master=True, multinode=True)
    servers = {"SRS-1": _Server("SRS-1", Status.RUNNING, mission="Nellis_v2.miz",
                                players=61, max_players=61)}
    _console_, text = _render(ServersWidget(_Service(node, servers)))
    assert "60 / 60" in text, "the shown count is not clamped to the 61 -> 60 maximum"


def test_servers_panel_has_no_title_but_keeps_the_counts_subtitle(tmp_path):
    panel = ServersWidget(_servers_service(tmp_path)).__rich__()
    assert panel.title is None
    assert panel.subtitle_align == "right"
    text = panel.subtitle.plain if hasattr(panel.subtitle, "plain") else str(panel.subtitle)
    assert "2 configured" in text
    assert "1 running" in text
    assert "1 offline" in text


def test_servers_table_columns(tmp_path):
    widget = ServersWidget(_servers_service(tmp_path))
    table = widget.__rich__().renderable
    assert [column.header for column in table.columns] == [
        "Status", "Server Name", "Mission Name", "Players", "Port / SRS", "Node"]
    assert [column.width for column in table.columns[:2]] == [11, 22]
    assert table.columns[3].width == 9
    assert table.columns[3].justify == "right"
    assert table.columns[4].width == 15
    assert table.columns[4].justify == "left"
    assert table.columns[5].width == 8


def test_port_cells_line_up_on_every_row(tmp_path):
    """The port cell holds two numbers, so the separator must sit in ONE column.

    A cell rendered as ``f"{dcs_port} │ {srs_port}"`` inside a centred column shifts by the
    length difference of the SRS value, so ``5006`` and ``—`` put their DCS port two columns
    apart. The DCS field is padded to a fixed width and the column left-justified instead.
    """
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension({"SRS Port": Port(5006, PortType.BOTH)})
    with_srs = _Server("SRS-1", Status.RUNNING, mission="Nellis_v2.miz", srs=srs)
    without = _Server("SRS-2", Status.RUNNING, mission="Nellis_v2.miz")
    without.instance = _Instance(8088)
    service = _Service(node, {"SRS-1": with_srs, "SRS-2": without})
    asyncio.run(dashboard_module.refresh_srs_ports(service))

    _console_, text = _render(ServersWidget(service))
    lines = text.splitlines()
    header = next(line for line in lines if "Port / SRS" in line)
    rows = [line for line in lines if "5006" in line or "8088" in line]
    assert len(rows) == 2, f"expected two port rows: {rows!r}"

    starts = [row.index("10308") if "10308" in row else row.index("8088") for row in rows]
    bars = [row.index("│", start) for row, start in zip(rows, starts)]
    assert len(set(bars)) == 1, f"the separator drifts between rows: {rows!r}"
    assert starts[0] + 5 == starts[1] + 4, \
        f"the DCS port is not right-aligned in its field: {rows!r}"
    assert [row[bar + 2] for row, bar in zip(rows, bars)] == ["5", "—"], \
        f"the SRS value does not start right after the separator: {rows!r}"
    assert header.index("Port") == starts[0], \
        f"the header does not start over the DCS port: {header!r} / {rows[0]!r}"


def _srs_server(name: str = "SRS-1", status: Status = Status.RUNNING,
                srs: "_Extension | None" = None) -> _Server:
    return _Server(name, status, mission="Nellis_v2.miz", srs=srs)


def _srs_row(service: _Service, name: str = "SRS-1") -> str:
    _console_, text = _render(ServersWidget(service))
    rows = [line for line in text.splitlines() if name in line]
    assert rows, f"no rendered row for {name}"
    return rows[0]


def test_port_column_shows_dcs_and_srs(tmp_path):
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension({"SRS Port": Port(5002, PortType.BOTH)})
    service = _Service(node, {"SRS-1": _srs_server(srs=srs)})
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    row = _srs_row(service)
    assert "10308 │ 5002" in row, f"the row lost a port: {row!r}"
    assert srs.calls == [("SRS", "get_ports")], "the SRS extension was not asked on the node"


def test_srs_port_renders_when_the_extension_answers(tmp_path):
    """Pin 1: ``get_ports`` returning ``{'SRS Port': Port(5002, BOTH)}`` renders 5002."""
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension({"SRS Port": Port(5002, PortType.BOTH)})
    service = _Service(node, {"SRS-1": _srs_server(srs=srs)})
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert "5002" in _srs_row(service)


def test_an_unchanged_status_issues_one_call_only(tmp_path):
    """Two consecutive frames with the same status issue exactly one RPC."""
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension({"SRS Port": Port(5002, PortType.BOTH)})
    service = _Service(node, {"SRS-1": _srs_server(srs=srs)})
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert srs.calls == [("SRS", "get_ports")], "an unchanged frame issued another RPC"


def test_srs_port_renders_an_em_dash_when_the_extension_reports_nothing(tmp_path):
    """Pin 2: ``{}`` means SRS is not enabled; the DCS port is still shown."""
    node = _Node(tmp_path, master=True, multinode=True)
    service = _Service(node, {"SRS-1": _srs_server(srs=_Extension({}))})
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    row = _srs_row(service)
    assert "10308 │ —" in row, f"the DCS port or the SRS placeholder is missing: {row!r}"


def test_stopped_server_is_not_queried_for_its_srs_port(tmp_path):
    """Pin 3: a stopped server is skipped and renders the em dash."""
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension({"SRS Port": Port(5002, PortType.BOTH)})
    service = _Service(node, {"SRS-2": _Server("SRS-2", Status.STOPPED, srs=srs)})
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    row = _srs_row(service, "SRS-2")
    assert srs.calls == [], "a stopped server's extension was queried"
    assert "5002" not in row
    assert "—" in row


def test_a_failing_server_does_not_stop_the_refresh_for_the_others(tmp_path):
    """Pin 4: one unreachable node leaves its row at the em dash; the refresh does not raise."""
    node = _Node(tmp_path, master=True, multinode=True)
    failing = _Extension(error=RuntimeError("node unreachable"))
    working = _Extension({"SRS Port": Port(5002, PortType.BOTH)})
    servers = {
        "SRS-1": _srs_server("SRS-1", srs=working),
        "SRS-2": _srs_server("SRS-2", srs=failing),
    }
    service = _Service(node, servers)
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert "5002" in _srs_row(service, "SRS-1")
    assert "—" in _srs_row(service, "SRS-2")


def test_the_srs_port_cache_refreshes_on_a_status_change(tmp_path):
    """Pin 5: a status change issues a second call and the new answer is rendered."""
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension({"SRS Port": Port(5002, PortType.BOTH)})
    server = _srs_server(srs=srs)
    service = _Service(node, {"SRS-1": server})
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert "5002" in _srs_row(service)

    server.status = Status.PAUSED
    srs.ports = {"SRS Port": Port(5003, PortType.BOTH)}
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    row = _srs_row(service)
    assert "5003" in row and "5002" not in row, f"the cache did not refresh: {row!r}"
    assert srs.calls == [("SRS", "get_ports")] * 2, "the status change did not refetch"


def test_a_failed_lookup_is_retried_when_the_extension_appears(tmp_path):
    """A lookup that failed at startup is not an answer: it is re-asked and the port lands.

    The extension list arrives a moment after the server starts, so the first lookup of a freshly
    started server answers ``Extension SRS not found``. Caching that as the answer left the em dash
    in place until the server's next status change, which may never come.
    """
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension(error=ValueError("Extension SRS not found."))
    service = _Service(node, {"SRS-1": _srs_server(srs=srs)})
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert srs.calls == [("SRS", "get_ports")], "the first lookup was not attempted"
    assert "—" in _srs_row(service)
    assert "SRS-1" in service.srs_retry, "a failed lookup scheduled no retry"

    srs.error = None
    srs.ports = {"SRS Port": Port(5002, PortType.BOTH)}
    service.srs_retry["SRS-1"] = (1, 0.0)  # the retry delay has run out
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert len(srs.calls) == 2, "the failed lookup was never re-asked"
    assert "5002" in _srs_row(service), "the retried answer did not reach the row"
    assert "SRS-1" not in service.srs_retry, "the retry entry survived a successful answer"


def test_a_failed_lookup_waits_out_its_delay(tmp_path):
    """The render loop must not hammer a server whose port lookup failed."""
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension(error=ValueError("Extension SRS not found."))
    service = _Service(node, {"SRS-1": _srs_server(srs=srs)})
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert srs.calls == [("SRS", "get_ports")], "the failed lookup was repeated inside its delay"


def test_the_retry_stops_and_warns_once_when_it_keeps_failing(tmp_path):
    """A server that never answers is retried a bounded number of times, then left alone."""
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension(error=RuntimeError("node unreachable"))
    service = _Service(node, {"SRS-1": _srs_server(srs=srs)})

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect()
    service.log.addHandler(handler)
    try:
        for _ in range(dashboard_module.SRS_RETRY_ATTEMPTS + 1):
            asyncio.run(dashboard_module.refresh_srs_ports(service))
            if "SRS-1" in service.srs_retry:
                service.srs_retry["SRS-1"] = (service.srs_retry["SRS-1"][0], 0.0)
    finally:
        service.log.removeHandler(handler)

    expected = dashboard_module.SRS_RETRY_ATTEMPTS + 1
    assert len(srs.calls) == expected, f"expected {expected} attempts, saw {len(srs.calls)}"
    warnings = [r for r in records if "SRS port unavailable for SRS-1" in r.getMessage()]
    assert len(warnings) == 1, f"the exhausted retry logged {len(warnings)} warnings"
    assert "SRS-1" not in service.srs_retry, "the retry entry survived its last attempt"

    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert len(srs.calls) == expected, "the server was polled again after its retries ran out"


def test_the_update_loop_rearms_on_a_due_retry(tmp_path):
    """The render loop's other trigger: a failed lookup whose delay has run out."""
    dashboard = object.__new__(Dashboard)
    dashboard.srs_retry = {}
    assert Dashboard._srs_retry_due(dashboard) is False
    dashboard.srs_retry = {"SRS-1": (1, monotonic() + 60)}
    assert Dashboard._srs_retry_due(dashboard) is False, "a retry fired before its delay"
    dashboard.srs_retry = {"SRS-1": (1, 0.0)}
    assert Dashboard._srs_retry_due(dashboard) is True, "a due retry did not rearm the loop"


def test_a_server_that_stops_loses_its_port_without_another_call(tmp_path):
    """RUNNING -> STOPPED clears the cached port and issues no extra RPC."""
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension({"SRS Port": Port(5002, PortType.BOTH)})
    server = _srs_server(srs=srs)
    service = _Service(node, {"SRS-1": server})
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert "5002" in _srs_row(service)

    server.status = Status.STOPPED
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    row = _srs_row(service)
    assert "5002" not in row and "—" in row, f"the stopped row kept a port: {row!r}"
    assert srs.calls == [("SRS", "get_ports")], "a stopping server was queried again"


def test_the_srs_port_is_not_read_from_the_master_copy():
    """Pin 6: the stale ``instance.locals`` SRS read is gone from the source."""
    source = SERVICE_PY.read_text(encoding="utf-8")
    assert "get('extensions', {}).get('SRS'" not in source, \
        "service.py still reads the SRS port from the master's locals copy"
    assert ".get('extensions'" not in source, \
        "service.py still reaches into the extensions block of instance.locals"


def test_the_refresh_survives_the_real_bus_mapping(tmp_path):
    """The bus hands out a ``ThreadSafeDict``, whose ``__iter__`` yields ``(name, server)``.

    Iterating it as if it were a plain dict puts ``ServerImpl`` tuples into a set and raises the
    unhashable-type error that killed every frame.
    """
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension({"SRS Port": Port(5002, PortType.BOTH)})
    service = _Service(node, {"SRS-1": _srs_server(srs=srs)})
    assert isinstance(service.bus.servers, ThreadSafeDict), "the pin is not using the real mapping"
    assert _Server.__hash__ is None, "the stub server is hashable; the real ServerImpl is not"
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert "5002" in _srs_row(service)


def test_a_server_that_left_the_bus_is_pruned_from_the_cache(tmp_path):
    """A name cached for a server that is gone from the bus is dropped, not left to be rendered."""
    node = _Node(tmp_path, master=True, multinode=True)
    srs = _Extension({"SRS Port": Port(5002, PortType.BOTH)})
    service = _Service(node, {"SRS-1": _srs_server(srs=srs)})
    service.srs_status = {"GONE-1": Status.RUNNING}
    service.srs_ports = {"GONE-1": "5009"}
    asyncio.run(dashboard_module.refresh_srs_ports(service))
    assert "GONE-1" not in service.srs_status
    assert "GONE-1" not in service.srs_ports
    assert "5002" in _srs_row(service)


def test_the_render_loop_refreshes_without_logging_a_failure(tmp_path):
    """The shipped caller: the update loop's status check plus its refresh wrapper.

    This is the path the traceback came from — ``_run_srs_refresh`` logged "SRS port refresh
    failed" every frame, so the pin asserts both the port landing in the cache and that the wrapper
    logged nothing.
    """
    dashboard = object.__new__(Dashboard)
    srs = _Extension({"SRS Port": Port(5002, PortType.BOTH)})
    dashboard.bus = SimpleNamespace(servers=ThreadSafeDict({"SRS-1": _srs_server(srs=srs)}))
    dashboard.srs_ports = {}
    dashboard.srs_status = {}
    dashboard.srs_retry = {}
    dashboard.srs_refreshing = False
    dashboard.log = logging.getLogger("dashboard.test")

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect()
    dashboard.log.addHandler(handler)
    try:
        assert Dashboard._srs_status_changed(dashboard) is True, "the first frame missed the change"
        asyncio.run(Dashboard._run_srs_refresh(dashboard))
        assert Dashboard._srs_status_changed(dashboard) is False, "the same frame asked again"
    finally:
        dashboard.log.removeHandler(handler)

    assert dashboard.srs_ports["SRS-1"] == "5002"
    failures = [r.getMessage() for r in records if "SRS port refresh failed" in r.getMessage()]
    assert failures == [], f"the refresh wrapper logged a failure: {failures}"


def test_a_server_without_the_srs_extension_is_not_warned_about(tmp_path):
    """SRS is optional: a missing extension leaves the em dash and must not print a warning."""
    node = _Node(tmp_path, master=True, multinode=True)
    server = _srs_server(srs=_Extension(error=ValueError("Extension SRS not found.")))
    service = _Service(node, {"SRS-1": server})

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect()
    service.log.addHandler(handler)
    try:
        asyncio.run(dashboard_module.refresh_srs_ports(service))
    finally:
        service.log.removeHandler(handler)

    assert service.srs_ports["SRS-1"] == "—"
    assert [r for r in records if r.levelno >= logging.WARNING] == [], \
        f"a missing SRS extension logged: {[r.getMessage() for r in records]}"


# --------------------------------------------------------------------------- nodes


def test_node_panel_uses_the_old_palette_and_no_title(tmp_path):
    widget = NodeWidget(_servers_service(tmp_path))
    panel = widget.__rich__()
    assert panel.title is None
    _console_, text = _render(widget)
    assert "●" in text
    assert text.count("─") > 0


# --------------------------------------------------------------------------- log


def test_log_row_shows_the_file_and_line_right_aligned(tmp_path):
    service = _servers_service(tmp_path, records=[_record()])
    _console_, text = _render(LogWidget(service))
    source_line = [line for line in text.splitlines() if "nodes.py:194" in line]
    assert source_line, "the source column is missing"
    assert source_line[0].rstrip().endswith("nodes.py:194 │"), "the source column is not right-aligned"


def test_log_module_cell_is_the_logger_tail(tmp_path):
    service = _servers_service(tmp_path, records=[_record(name="plugins.modmanager.commands")])
    _console_, text = _render(LogWidget(service))
    assert "commands" in text
    assert "plugins.modmanager" not in text


def test_log_message_markup_is_escaped(tmp_path):
    service = _servers_service(tmp_path, records=[_record("[red]x[/]")])
    _console_, text = _render(LogWidget(service))
    assert "[red]x[/]" in text, "the message was parsed as markup instead of shown literally"


def test_log_level_colour_comes_from_the_real_level(tmp_path):
    """INFO blue, WARNING yellow, ERROR bold red — the palette of the old log renderer."""
    service = _servers_service(tmp_path, records=[_record(level=logging.INFO)])
    console, _text = _render(LogWidget(service))
    assert _color_of(console, "INFO") == {"blue"}

    service = _servers_service(tmp_path, records=[_record(level=logging.WARNING)])
    console, _text = _render(LogWidget(service))
    assert _color_of(console, "WARN") == {"yellow"}

    service = _servers_service(tmp_path, records=[_record(level=logging.ERROR)])
    console, _text = _render(LogWidget(service))
    assert _color_of(console, "ERROR") == {"red"}
    assert _style_of(console, "ERROR").bold is True, "ERROR is not bold"

    # the levelno decides, not the message text: a record that says "error" is still INFO/blue
    service = _servers_service(tmp_path, records=[_record("error", level=logging.INFO)])
    console, _text = _render(LogWidget(service))
    assert _color_of(console, "INFO") == {"blue"}
    assert not _color_of(console, "error") & {"blue", "red"}, "the message text drove the level colour"


def test_log_time_column_is_dim_cyan(tmp_path):
    service = _servers_service(tmp_path, records=[_record()])
    console, _text = _render(LogWidget(service))
    stamps = [segment for segment in console._record_buffer
              if segment.text.strip() and len(segment.text.strip()) == 8
              and segment.text.strip()[2] == ":" and segment.text.strip()[5] == ":"]
    assert stamps, "the time column is missing"
    for segment in stamps:
        style = segment.style
        assert style is not None and style.color is not None, "the time column has no colour"
        assert style.color.name == "cyan", f"the time is {style.color.name!r}, not cyan"
        assert style.dim is True, "the time is not dim"
        assert style.color.name != "white", "the time fell back to plain white"


def test_a_record_with_an_exception_keeps_its_traceback(tmp_path):
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = __import__("sys").exc_info()

    record = _record("failed", level=logging.ERROR)
    record.exc_info = exc_info
    service = _servers_service(tmp_path, records=[record])
    _console_, text = _render(LogWidget(service))
    assert "ValueError: boom" in text


def test_log_panel_has_no_title_and_no_subtitle(tmp_path):
    widget = LogWidget(_servers_service(tmp_path))
    assert widget.panel.title is None
    assert widget.panel.subtitle is None


# --------------------------------------------------------------------------- layout


class _Dashboard(Dashboard):
    """A ``Dashboard`` with its attributes set directly: ``create_layout`` is the subject."""

    log = logging.getLogger("dashboard.test")
    pool = None

    def __init__(self, node: _Node, servers: dict):
        self.node = node
        self.bus = SimpleNamespace(servers=servers,
                                   filter={"server_name": "", "mission_name": ""})
        self.queue = Queue()
        self.old_handler = None
        self.srs_ports: dict[str, str] = {}
        self.console = Console(file=io.StringIO(), width=132, height=40)
        self.header_widget = HeaderWidget(self)
        self.servers_widget = ServersWidget(self)
        self.log_widget = LogWidget(self)

    def get_config(self, *args, **kwargs) -> dict:
        return {}


def test_layout_sizes(tmp_path):
    node = _Node(tmp_path, master=True, multinode=True)
    servers = {"SRS-1": _Server("SRS-1", Status.RUNNING, mission="Nellis_v2.miz")}
    layout = _Dashboard(node, servers).create_layout()

    header, body, log = layout.children
    assert header.size == 3
    assert body.ratio == 1
    assert body.minimum_size == len(servers) + 4
    assert log.ratio == 2
    assert log.minimum_size == 8
    assert len(body.children) == 2, "the master layout lost its servers/nodes split"


def test_layout_sizes_a_long_server_list_against_the_log(tmp_path):
    node = _Node(tmp_path, master=True, multinode=True)
    servers = {f"SRS-{index}": _Server(f"SRS-{index}", Status.STOPPED) for index in range(30)}
    layout = _Dashboard(node, servers).create_layout()
    _header, body, log = layout.children
    assert body.ratio == 1 and log.ratio == 2, "a long server list is still given a fixed size"


#: a mission name that fills the width the mission column has at 132 columns
FITTING_MISSION = "Nellis_v2.miz"

#: the name length the mission column must hold once the node panel stops taking a share
LONG_MISSION = "Operation_Test_Mission_1"


def _layout_render(node: _Node, servers: dict, width: int) -> list[str]:
    """Print the real ``create_layout()`` layout at ``width`` columns and return its lines."""
    layout = _Dashboard(node, servers).create_layout()
    console = Console(record=True, file=io.StringIO(), width=width, height=40)
    console.print(layout)
    text = "".join(segment.text for segment in console._record_buffer)
    return text.splitlines()


def _server_row(lines: list[str]) -> str:
    """The single rendered row that carries server ``SRS-1``."""
    rows = [line for line in lines if "SRS-1" in line and "Running" in line]
    assert len(rows) == 1, f"expected one SRS-1 row, got {rows!r}"
    return rows[0]


def test_the_node_panel_is_fixed_and_the_servers_panel_is_flexible(tmp_path):
    node = _Node(tmp_path, master=True, multinode=True)
    servers = {"SRS-1": _Server("SRS-1", Status.RUNNING, mission=FITTING_MISSION)}
    layout = _Dashboard(node, servers).create_layout()

    servers_list, nodes = layout["servers"].children
    assert servers_list.name == "servers_list"
    assert servers_list.ratio == 2, "the servers panel lost its flexible share"
    assert nodes.name == "nodes"
    assert nodes.size == 34, f"the node panel is not the fixed 34 cells: {nodes.size}"
    assert not nodes.ratio, "a ratio node panel takes the mission column's width"


def test_the_mission_name_is_whole_at_132_columns(tmp_path):
    node = _Node(tmp_path, master=True, multinode=True)
    servers = {"SRS-1": _Server("SRS-1", Status.RUNNING, mission=FITTING_MISSION, players=12)}
    lines = _layout_render(node, servers, 132)

    row = _server_row(lines)
    assert FITTING_MISSION in row, f"the mission is not rendered whole: {row!r}"
    assert "\u2026" not in row, f"the mission is truncated: {row!r}"


def test_a_24_char_mission_name_is_whole_at_150_columns(tmp_path):
    assert len(LONG_MISSION) == 24
    node = _Node(tmp_path, master=True, multinode=True)
    servers = {"SRS-1": _Server("SRS-1", Status.RUNNING, mission=LONG_MISSION, players=12)}
    lines = _layout_render(node, servers, 150)

    row = _server_row(lines)
    assert LONG_MISSION in row, f"the mission is not rendered whole: {row!r}"
    assert "\u2026" not in row, f"the mission is truncated: {row!r}"


def test_the_mission_column_keeps_12_cells_at_132_columns(tmp_path):
    node = _Node(tmp_path, master=True, multinode=True)
    servers = {"SRS-1": _Server("SRS-1", Status.RUNNING, mission=FITTING_MISSION, players=12)}
    lines = _layout_render(node, servers, 132)

    rows = [line for line in lines if "Players" in line and "Status" in line]
    assert len(rows) == 1, f"expected one servers header row, got {rows!r}"
    header = rows[0]
    mission_width = header.index("Players") - header.index("Mis")
    assert mission_width >= 12, f"the mission column is {mission_width} cells wide: {header!r}"


# --------------------------------------------------------------------------- translations


ADDED_MSGIDS = ("configured", "running", "offline")

REMOVED_MSGIDS = (
    "INITIALIZING TACTICAL HUD & TELEMETRY SYSTEMS...",
    "CONNECTING TO POSTGRESQL TACTICAL DATABASE...",
    "CALIBRATING SIMPLE RADIO STANDALONE (SRS)...",
    "SYNCING DCS WORLD DEDICATED SERVERS...",
    "TACTICAL COMMAND & DASHBOARD ONLINE!",
    "HUD LOCK",
    "DEDICATED SERVER CONTROL SYSTEM",
    "RADAR",
    "DATALINK",
    "NODE",
    "TACTICAL HUD",
    "MIL-STD-1553B  •  COCKPIT INITIALIZATION SEQUENCE",
    "MISSION CONTROL CENTER",
    "ACTIVE LANGUAGE",
    "ID",
    "INSTANCE / OPERATION",
    "TARGET SERVERS & PORT",
    "TACTICAL SERVER COMMAND CENTER",
    "MASTER HUD READY",
    "WEB UI",
    "DISCORD",
    "SCHEDULER",
    "SRS RADIO",
    "EXTENSION",
    "SERVER",
    "SYSTEM",
    "ERROR",
    "WARN",
    "DOWN",
    "INIT",
    "ACTIVE",
    "INFO",
    "DCS SERVER STATUS",
    "LIVE SYSTEM LOG",
    "Time   │   Status   │   Module   │   Event Detail",
    "Nodes",
    "Shutting_Down",
    "Shutting Down",
)


def _catalog(language: str = "tr") -> str:
    return (REPO / "locale" / language / "LC_MESSAGES" / "dashboard.po").read_text(encoding="utf-8")


def _re_entry(msgid: str, catalog: str) -> str:
    """The ``msgstr`` of ``msgid`` in a ``.po`` body, or ``''`` when absent."""
    match = re.search(r'(?m)^msgid "%s"\nmsgstr "(.*)"\n' % re.escape(msgid), catalog)
    return match.group(1) if match else ""


@pytest.mark.parametrize("msgid", ADDED_MSGIDS)
def test_added_msgids_are_translated(msgid):
    entry = _re_entry(msgid, _catalog())
    assert entry, f"tr dashboard.po lacks {msgid!r}"
    assert entry.strip() not in ("", msgid), f"tr dashboard.po has no real translation for {msgid!r}"


@pytest.mark.parametrize("msgid", REMOVED_MSGIDS)
def test_removed_msgids_left_the_catalog(msgid):
    assert not _re_entry(msgid, _catalog()), f"tr dashboard.po still carries {msgid!r}"


def test_the_compiled_catalog_serves_the_added_msgids():
    import gettext

    translation = gettext.translation(
        "dashboard", localedir=str(REPO / "locale"), languages=["tr"], fallback=False)
    for msgid in ADDED_MSGIDS:
        assert translation.gettext(msgid) != msgid, f"dashboard.mo serves the English {msgid!r}"


# --------------------------------------------------------------------------- status labels

#: the six server state words of ``STATUS_CELLS``, named by their English msgid
STATUS_MSGIDS = ("Running", "Loading", "Paused", "Stopped", "Shutdown", "Waiting")

#: catalogs that carry all six status words; ``en`` is the msgid itself and has no catalog
STATUS_LANGUAGES = ("de", "es", "fr", "it", "pl", "pt", "ru", "cn", "tr")

#: the status column is 11 cells wide and the cell is ``glyph + space + word``
STATUS_WORD_BUDGET = 9


def _translation(language: str):
    """``gettext`` of the compiled dashboard catalog of ``language``."""
    import gettext

    return gettext.translation("dashboard", localedir=str(REPO / "locale"),
                               languages=[language], fallback=False).gettext


@pytest.mark.parametrize("language", STATUS_LANGUAGES)
def test_every_status_word_fits_the_column(language):
    """At most nine characters, one distinct word per state, never the untranslated msgid."""
    _ = _translation(language)
    words = {msgid: _(msgid) for msgid in STATUS_MSGIDS}

    too_long = {msgid: word for msgid, word in words.items() if len(word) > STATUS_WORD_BUDGET}
    assert not too_long, f"{language} overflows the status column: {too_long}"

    assert len(set(words.values())) == len(STATUS_MSGIDS), \
        f"{language} reuses one status word: {sorted(words.values())}"

    untranslated = {msgid: word for msgid, word in words.items() if word == msgid}
    assert not untranslated, f"{language} left these status words in English: {untranslated}"


def test_the_english_status_words_fit_the_column():
    """The msgids themselves are the English labels, so their own length is the budget."""
    too_long = [msgid for msgid in STATUS_MSGIDS if len(msgid) > STATUS_WORD_BUDGET]
    assert not too_long, f"English status words overflow the column: {too_long}"
    assert len(set(STATUS_MSGIDS)) == len(STATUS_MSGIDS), "two states share an English word"


def test_the_unregistered_state_is_called_waiting():
    """The overflowed ``Unregistered`` label is gone from the source, ``Waiting`` is in."""
    source = SERVICE_PY.read_text(encoding="utf-8")
    assert '_("Unregistered")' not in source, "the long Unregistered label is still in service.py"
    assert '_("Waiting")' in source, "Waiting is not the UNREGISTERED label"
    assert len(STATUS_MSGIDS[-1]) == len("Waiting")


@pytest.mark.parametrize("status", sorted(dashboard_module.STATUS_CELLS, key=lambda s: s.name),
                         ids=lambda s: s.name)
def test_the_status_cell_fits_the_eleven_cell_column(tmp_path, status):
    """Rendered at 132 columns, no status cell is truncated with an ellipsis."""
    node = _Node(tmp_path, master=True, multinode=True)
    servers = {"SRS-1": _Server("SRS-1", status, mission="Nellis_v2.miz", players=4)}
    _console_, text = _render(ServersWidget(_Service(node, servers)))

    glyph, word, _colour = dashboard_module.STATUS_CELLS[status]
    cell = f"{glyph} {word}"
    assert len(word) <= STATUS_WORD_BUDGET, f"{status.name} word {word!r} overflows the column"

    rows = [line for line in text.splitlines() if glyph in line and "SRS-1" in line]
    assert rows, f"no rendered row for {status.name}"
    row = rows[0]
    assert "\u2026" not in row, f"the {status.name} row is truncated: {row!r}"
    assert cell in row, f"the {status.name} cell {cell!r} is not rendered whole: {row!r}"
