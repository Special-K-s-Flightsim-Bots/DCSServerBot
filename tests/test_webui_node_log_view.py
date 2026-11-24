"""The node log VIEWER: a remote node's OWN bot log, tailed on the website (follow / page back).

The node row already had a whole-file *Download log*; this adds the VIEWER the operator asked for — "a
similar solution for the node logfiles as for the servers logfiles so that we can look at logfiles
of remote nodes on the website". It mirrors the server's Log tab (40 lines / 64 KiB windows / 3s
follow / "Load older") over the SAME node mechanism (``Node.read_file_window``), and it carries the
DOWNLOAD's own gate (``logs.view``, Admin-only): a caller who cannot download a node's log cannot
view it either.

Pins: both routes registered under ``logs.view``; the two controls offered on every reachable node
row (and nowhere a non-Admin or an offline node reads); a remote node's log follows and pages back;
every failure state — offline/unknown, no log yet, rotated away, permission, timeout, a failure
code, a new-log reset — renders its OWN honest sentence, never an empty viewer; and the list page
renders the documented columns (State included) and omits the two it cannot fill honestly (a
per-node version — the bot version is cluster-wide — and Last seen — a database column the pure read
model does not read).

Driven through the REAL app (the write suites' stub fleet and local login), with a node double
whose ``read_file_window`` reads a real file exactly as the master's own ``seek`` logic does.
"""
from __future__ import annotations

import os
import re
import types

import pytest

from conftest import host_path, permissions
from core import Status
from core.data.impl.nodeimpl import file_identity
from services.webservice.pages import logs as logs_page
from services.webservice.pages import nodes as nodes_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, sign_in,
)

NODES = nodes_page.NODES_PATH
DASHBOARD = "/"
MASTER = "dcs-a-01"
AGENT = "dcs-b-01"
VIEW_A = f"/nodes/{MASTER}/log/view"
VIEW_B = f"/nodes/{AGENT}/log/view"
WINDOW_A = f"/nodes/{MASTER}/log/window"
WINDOW_B = f"/nodes/{AGENT}/log/window"

LINES = ("2026-09-30 10:00:00.000 INFO    APP: server started\n"
         "2026-09-30 10:00:01.000 WARNING NET: something happened\n")


# ------------------------------------------------------------------------------------- doubles

class NodeLogNode:
    """The node double: ``read_file_window`` / ``read_file`` read a REAL file off a base directory.

    A RELATIVE path (the node's own ``logs/dcssb-<node>.log``, the very path ``run.py`` writes) is
    resolved against *base* — exactly what the node's own working directory does — so a test can
    assert the console sent a node-relative path and the node opened it there. ``error`` (if set) is
    raised instead of reading; ``code`` (if set) is RETURNED in place of bytes (a failure code).
    """

    def __init__(self, name, base, *, master=False, error=None, code=None, plugins=()):
        self.name = name
        self.master = master
        self.base = str(base)
        self.instances: dict = {}
        self.error = error
        self.code = code
        self.plugins = list(plugins)
        self.reads: list[dict] = []

    def _resolve(self, path: str) -> str:
        return path if os.path.isabs(path) else os.path.join(self.base, path)

    async def read_file_window(self, path, *, length, offset=None, behind=None):
        self.reads.append({"path": path, "length": length, "offset": offset, "behind": behind})
        if self.error is not None:
            raise self.error
        if self.code is not None:
            return self.code, 0, 0
        real = self._resolve(path)
        st = os.stat(real)
        size = st.st_size
        if behind is not None:
            end = max(0, min(int(behind), size))
            start = max(0, end - length)
        else:
            start = min(int(offset), size)
            end = min(size, start + length)
        with open(real, "rb") as handle:
            handle.seek(start)
            return handle.read(max(0, end - start)), size, file_identity(st)

    async def read_file(self, path):
        real = self._resolve(path)
        with open(real, "rb") as handle:
            return handle.read()


def node_server(name, node_name, managed_by):
    """A server double shaped where the scope, the read models and the routes read it."""
    srv = types.SimpleNamespace(
        name=name, display_name=name, status=Status.RUNNING, maintenance=False,
        node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=f"{node_name}-Instance"), port=10309,
        current_mission=None, is_remote=False, players={},
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )
    srv.get_active_players = lambda: []
    return srv


def fleet(base, *, b_online=True, a_error=None, b_error=None, b_code=None, a_base=None, b_base=None):
    """Two nodes: ``dcs-a-01`` (the MASTER) and ``dcs-b-01`` (online only on request)."""
    a = node_server("SRS-1", MASTER, ["Host A"])
    b = node_server("SRS-B", AGENT, ["Host B"])
    node_a = NodeLogNode(MASTER, a_base or base, master=True, error=a_error)
    node_b = (NodeLogNode(AGENT, b_base or base, error=b_error, code=b_code)
              if b_online else None)
    return Cluster([a, b], nodes={MASTER: node_a, AGENT: node_b}, node=node_a,
                   instances=[instance(f"{MASTER}-Instance", a), instance(f"{AGENT}-Instance", b)])


def write_node_log(base, node, text):
    """Write a node's own bot log at ``{base}/logs/dcssb-<node>.log`` — the path ``run.py`` uses."""
    directory = base / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"dcssb-{node}.log"
    path.write_text(text, encoding="utf-8")
    return path


def append_node_log(base, node, text):
    with open(base / "logs" / f"dcssb-{node}.log", "a", encoding="utf-8") as handle:
        handle.write(text)


def admin_app(config_dir, base, **kwargs):
    return build(config_dir, fleet(base, **kwargs), plugins=("mission", "admin"))


def view_links(html: str) -> set[str]:
    return set(re.findall(r'class="act-btn view" href="([^"]+)"', html))


def attr_value(html: str, name: str) -> int:
    match = re.search(rf'{name}="(\d+)"', html)
    assert match, f"the page rendered no {name}"
    return int(match.group(1))


# ------------------------------------------------------------------ 1. the capability + the routes

def test_the_viewer_is_registered_under_the_log_panels_own_capability(config_dir):
    """No new capability: both viewer routes declare ``logs.view``, the log panel's own."""
    declared = nodes_page.capabilities()
    assert declared[nodes_page.NODE_LOG_VIEW_PATH] == logs_page.LOGS_CAPABILITY == "logs.view"
    assert declared[nodes_page.NODE_LOG_WINDOW_PATH] == logs_page.LOGS_CAPABILITY
    assert declared[nodes_page.NODES_LOG_PATH] == logs_page.LOGS_CAPABILITY
    assert permissions.roles_for_capability("logs.view") == logs_page.LOGS_ROLES == ("Admin",)
    assert permissions.scope_grants("logs.view") is False


def test_the_viewer_paths_are_one_percent_encoded_segment_each():
    assert nodes_page.log_view_path("dcs-b-01") == "/nodes/dcs-b-01/log/view"
    assert nodes_page.log_view_path("weird/name x") == "/nodes/weird%2Fname%20x/log/view"
    assert nodes_page.node_log_window_path("weird/name x") == "/nodes/weird%2Fname%20x/log/window"


@pytest.mark.asyncio
async def test_both_viewer_routes_are_registered_through_the_registrar(config_dir, tmp_path,
                                                                       clean_registry):
    app = admin_app(config_dir, tmp_path)
    registrar = app.state.webui_registrar
    routes = {getattr(route, "path", None): route for route in registrar.routes}
    for path in (nodes_page.NODE_LOG_VIEW_PATH, nodes_page.NODE_LOG_WINDOW_PATH):
        assert path in routes, path
        assert path not in registrar.page_paths, path
        assert registrar.capability_for(route=routes[path]) == "logs.view"


# ---------------------------------------------------------------------------------- 2. the control

@pytest.mark.asyncio
async def test_the_view_control_is_offered_on_every_reachable_node_row(config_dir, tmp_path,
                                                                       clean_registry):
    """BOTH nodes render BOTH log controls, on BOTH screens — the view link is a real LINK."""
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = {"nodes": await open_page(http, NODES),
                 "dashboard": await open_page(http, DASHBOARD + "?tab=nodes")}
    for where, html in pages.items():
        assert view_links(html) == {VIEW_A, VIEW_B}, where
        assert f'aria-label="View the bot log of node {AGENT}"' in html, where
        # the glyph is drawn — an empty <svg> is the same failure as no accessible name
        anchor = re.search(r'class="act-btn view" href="%s"[^>]*>.*?</a>' % re.escape(VIEW_B),
                           html, re.S)
        assert anchor, where
        assert "<svg" in anchor.group(0) and "<path" in anchor.group(0), where


def test_the_view_control_names_a_glyph_that_exists():
    from pathlib import Path
    source = Path(nodes_page.__file__).parent.parent.joinpath("templates", "_icons.html")
    glyphs = set(re.findall(r"'([a-z_]+)':\s*'", source.read_text(encoding="utf-8")))
    assert "view" in glyphs, "the view glyph is missing from the set"


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["mod1", "hoster", "plain"])
async def test_a_non_admin_is_offered_no_view_control(config_dir, tmp_path, clean_registry, who):
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, who)
        response = await http.get(NODES)
    if response.status_code == 403:
        return
    assert view_links(response.text) == set(), who
    assert 'class="act-btn view"' not in response.text, who


@pytest.mark.asyncio
async def test_an_offline_node_is_offered_no_view_control_and_its_page_is_404(config_dir, tmp_path,
                                                                              clean_registry):
    app = admin_app(config_dir, tmp_path, b_online=False)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
        page = await http.get(VIEW_B)
    assert view_links(html) == {VIEW_A}          # the online MASTER keeps its control
    assert page.status_code == 404


# ------------------------------------------------------------------------------- 3. follow the log

@pytest.mark.asyncio
async def test_the_view_page_renders_the_nodes_own_tail(config_dir, tmp_path, clean_registry):
    write_node_log(tmp_path, AGENT, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(VIEW_B)).text
    assert 'id="dcs-log-card"' in html
    assert 'id="dcs-log-body"' in html
    assert "server started" in html and "something happened" in html
    # the follower's own cursors + the window URL are on the card
    assert f'data-window-url="{WINDOW_B}"' in html
    assert 'data-offset="' in html and 'data-before="' in html and 'data-identity="' in html
    # …and the node's own relative log path was sent to the node, never a console-local one
    # (checked below through the double's recorded reads)
    assert AGENT in html


@pytest.mark.asyncio
async def test_the_follow_returns_only_the_appended_lines(config_dir, tmp_path, clean_registry):
    write_node_log(tmp_path, AGENT, LINES)
    node = NodeLogNode(AGENT, tmp_path)
    app = build(config_dir, Cluster([node_server("SRS-B", AGENT, ["Host B"])],
                                    nodes={AGENT: node}, node=node,
                                    instances=[]), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(VIEW_B)).text
        offset = attr_value(html, "data-offset")
        append_node_log(tmp_path, AGENT, "2026-09-30 10:00:05.000 ERROR   NEW-NODE-LINE\n")
        payload = (await http.get(WINDOW_B, params={"offset": str(offset)})).json()
    assert payload["available"] is True
    assert "NEW-NODE-LINE" in payload["lines"]
    assert "server started" not in payload["lines"], "follow must not re-send what was shown"
    assert payload["offset"] > offset
    # the node was asked for the RELATIVE path run.py writes — never a console-absolute one
    assert node.reads and node.reads[0]["path"] == f"logs/dcssb-{AGENT}.log"
    assert not node.reads[0]["path"].startswith(("/", "\\", "C:"))


@pytest.mark.asyncio
async def test_paging_back_returns_older_lines_and_reaches_the_start(config_dir, tmp_path,
                                                                     clean_registry):
    write_node_log(tmp_path, AGENT, "".join(
        f"2026-09-30 10:00:{i:02d}.000 INFO    LINE-{i:02d}\n" for i in range(60)))
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(VIEW_B)).text
        before = attr_value(html, "data-before")
        payload = (await http.get(WINDOW_B, params={"behind": str(before)})).json()
    assert payload["available"] is True
    assert "LINE-00" in payload["lines"], "the oldest lines must arrive when paging back"
    assert payload["at_start"] is True and payload["before"] == 0


# ------------------------------------------------------------------------- 4. the failure states

@pytest.mark.asyncio
async def test_an_offline_or_unknown_node_is_its_own_sentence(config_dir, tmp_path, clean_registry):
    app = admin_app(config_dir, tmp_path, b_online=False)
    async with client(app) as http:
        await sign_in(http, "specialk")
        payload = (await http.get(WINDOW_B, params={"offset": "0"})).json()
        missing = (await http.get("/nodes/NOPE-9/log/window", params={"offset": "0"})).json()
    assert payload["available"] is False and "offline or unknown" in payload["message"]
    assert missing["available"] is False and "offline or unknown" in missing["message"]


@pytest.mark.asyncio
async def test_no_log_file_yet_is_the_normal_pending_state(config_dir, tmp_path, clean_registry):
    app = admin_app(config_dir, tmp_path)                 # no file written
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(VIEW_B)).text
        payload = (await http.get(WINDOW_B, params={"offset": "0"})).json()
    assert "No log file yet for node" in html
    # the BODY is always shipped, so the follower can turn the sentence into the log
    assert 'id="dcs-log-body"' in html and 'id="dcs-log-note"' in html
    assert payload["available"] is False and payload.get("pending") is True
    # the sentence names the SAME spelled-for-the-node path the wire uses, on every platform
    assert f"logs/dcssb-{AGENT}.log" in payload["message"]


@pytest.mark.asyncio
async def test_a_log_rotated_away_is_reported_rather_than_a_stale_tail(config_dir, tmp_path,
                                                                       clean_registry):
    path = write_node_log(tmp_path, AGENT, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(VIEW_B)).text
        offset = attr_value(html, "data-offset")
        assert "server started" in html
        path.unlink()                                     # the log is rotated/removed away
        gone = (await http.get(WINDOW_B, params={"offset": str(offset)})).json()
    assert gone["available"] is False and gone.get("pending") is True
    assert "No log file yet for node" in gone["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error, fragment", [
    (PermissionError("no"), "could not read its own log file"),
    (TimeoutError(), "did not answer in time"),
    (RuntimeError("the muddy wire"), "could not hand over its log"),
])
async def test_each_node_failure_is_its_own_sentence(config_dir, tmp_path, clean_registry,
                                                     error, fragment):
    write_node_log(tmp_path, AGENT, LINES)
    app = admin_app(config_dir, tmp_path, b_error=error)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(VIEW_B)).text
        payload = (await http.get(WINDOW_B, params={"offset": "0"})).json()
    assert fragment in html
    assert payload["available"] is False and fragment in payload["message"]
    assert payload["lines"] == "", "a refused read must never answer with log content"


@pytest.mark.asyncio
async def test_a_failure_code_instead_of_bytes_is_refused(config_dir, tmp_path, clean_registry):
    write_node_log(tmp_path, AGENT, LINES)
    app = admin_app(config_dir, tmp_path, b_code=7)
    async with client(app) as http:
        await sign_in(http, "specialk")
        payload = (await http.get(WINDOW_B, params={"offset": "0"})).json()
    assert payload["available"] is False
    assert "failure code" in payload["message"]


@pytest.mark.asyncio
async def test_a_new_log_resets_the_view_instead_of_splicing(config_dir, tmp_path, clean_registry):
    write_node_log(tmp_path, AGENT, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(VIEW_B)).text
        offset = attr_value(html, "data-offset")
        # a SMALLER replacement: the follower's offset is past its end
        write_node_log(tmp_path, AGENT, "2026-09-30 10:30:00.000 INFO    NEW-FILE-AFTER-ROTATION\n")
        payload = (await http.get(WINDOW_B, params={"offset": "999999"})).json()
    assert payload["available"] is True and payload.get("reset") is True
    assert "NEW-FILE-AFTER-ROTATION" in payload["lines"]
    assert "server started" not in payload["lines"]


@pytest.mark.asyncio
async def test_a_window_request_without_a_cursor_is_refused(config_dir, tmp_path, clean_registry):
    write_node_log(tmp_path, AGENT, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(WINDOW_B)
        bad_offset = await http.get(WINDOW_B + "?offset=abc")
        bad_behind = await http.get(WINDOW_B + "?behind=abc")
    assert reply.status_code == 400 and bad_offset.status_code == 400
    assert bad_behind.status_code == 400
    assert "offset or a behind position is required" in reply.json()["message"]


# ------------------------------------------------------------------------------------ 5. the gate

@pytest.mark.asyncio
async def test_the_gate_matches_the_download_control(config_dir, tmp_path, clean_registry):
    """A caller who cannot DOWNLOAD cannot VIEW either — ONE capability, one refusal, byte for byte."""
    write_node_log(tmp_path, AGENT, LINES)
    node = NodeLogNode(AGENT, tmp_path)
    app = build(config_dir, Cluster([node_server("SRS-B", AGENT, ["Host B"])],
                                    nodes={AGENT: node}, node=node, instances=[]),
                plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "mod1")               # DCS Admin: reads the fleet, not the log
        window = await http.get(WINDOW_B, params={"offset": "0"})
        page = await http.get(VIEW_B)
        download = await http.get(f"/nodes/{AGENT}/log")
        panel = await http.get(logs_page.LOGS_PATH)
    assert window.status_code == page.status_code == download.status_code == panel.status_code == 403
    assert window.json()["detail"] == panel.json()["detail"]
    assert node.reads == [], "a refused caller must reach no node"


@pytest.mark.asyncio
async def test_an_anonymous_caller_is_refused(config_dir, tmp_path, clean_registry):
    write_node_log(tmp_path, AGENT, LINES)
    node = NodeLogNode(AGENT, tmp_path)
    app = build(config_dir, Cluster([node_server("SRS-B", AGENT, ["Host B"])],
                                    nodes={AGENT: node}, node=node, instances=[]),
                plugins=("mission", "admin"))
    async with client(app) as http:
        window = await http.get(WINDOW_B, params={"offset": "0"})
        page = await http.get(VIEW_B)
    assert window.status_code == 403 and page.status_code == 403
    assert node.reads == []


@pytest.mark.asyncio
async def test_the_routes_are_get_only(config_dir, tmp_path, clean_registry):
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        window = await http.post(WINDOW_B)
        page = await http.post(VIEW_B)
    assert window.status_code == 405 and page.status_code == 405


# ---------------------------------------------------------------------------- 6. the list columns

@pytest.mark.asyncio
async def test_the_nodes_list_renders_the_documented_columns_and_omits_the_rest(config_dir,
                                                                                tmp_path,
                                                                                clean_registry):
    """State (and the counters) render; a per-node version and Last seen do NOT — neither is real."""
    write_node_log(tmp_path, AGENT, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    headers = re.findall(r'<th scope="col"[^>]*>.*?</th>', html, re.S)
    labels = [re.sub(r"<[^>]+>", "", h).replace("▲", "").replace("▼", "").replace("↕", "").strip()
              for h in headers]
    assert "Node" in labels and "State" in labels
    assert "Instances" in labels and "Servers" in labels and "Players" in labels
    # the two columns that cannot be filled honestly are ABSENT
    assert "Version" not in " ".join(labels) and "Last seen" not in " ".join(labels)
    assert "version" not in html.lower() and "last seen" not in html.lower()
    # the State cell is a dot + word, as on the Servers row
    assert re.search(r'class="dot (run|dead)"', html)


@pytest.mark.asyncio
async def test_the_state_column_sorts_offline_nodes_together(config_dir, tmp_path, clean_registry):
    """``?sort=state`` is a real sort key (the heartbeat's verdict), not a dead control."""
    app = admin_app(config_dir, tmp_path, b_online=False)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(NODES, params={"sort": "state", "dir": "asc"})).text
    assert 'aria-sort="ascending"' in html
    assert "Sorted by State" in html
