"""The node row's *Download log* control: an agent's own bot log, on demand, Admin-only.

The console's log PANEL stays master-only; the node row hands over THAT node's own bot log file as a
download, guarded by the log panel's own ``logs.view`` capability (no new capability is invented).
Pins: present on every reachable node row on BOTH screens and absent for a non-Admin; the bytes are
that node's own log; the path sent to the node is the relative ``logs/dcssb-<node>.log`` that
``run.py`` writes; a log above ``LOG_DOWNLOAD_MAX_BYTES`` is refused with its own sentence, never
served clipped; and each failure (no file, permission, timeout, a failure code instead of bytes, an
offline node) gets its own sentence, never a zero-byte download. Driven through the real app and a
two-node stub fleet."""
from __future__ import annotations

import re
import types

import pytest

from conftest import asgi_client, permissions
from core import Status
from services.webservice.pages import logs as logs_page
from services.webservice.pages import nodes as nodes_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, sign_in, token_of,
)

NODES = nodes_page.NODES_PATH
DASHBOARD = "/"
MASTER = "dcs-a-01"
AGENT = "dcs-b-01"
LOG_A = b"2026-09-30 10:00:00.000 INFO\tMASTER-LOG-MARKER alpha\n"
LOG_B = b"2026-09-30 10:00:01.000 INFO\tAGENT-B-LOG-MARKER beta\n"


# ------------------------------------------------------------------------------- test doubles

class LogNode:
    """The ``Node`` double: the three lifecycle calls plus ``read_file`` — the download's own seam.

    ``read_file`` is the ONE call the download makes. It records the PATH it was asked for (so a
    test can assert the node resolved a node-side relative path, not a console-local absolute one)
    and answers ``log`` — which may be bytes (content), an ``int`` (a failure CODE, never content),
    or raise ``error`` (a typed transport failure).
    """

    def __init__(self, name, *, master=False, log=b"", error=None):
        self.name = name
        self.master = master
        self.instances = {}
        self.log = log
        self.error = error
        self.reads: list[str] = []
        self.calls: list[str] = []

    async def restart(self):
        self.calls.append("restart")

    async def shutdown(self, rc=-2):
        self.calls.append("shutdown")

    async def upgrade(self):
        self.calls.append("upgrade")

    async def read_file(self, path: str):
        self.reads.append(path)
        if self.error is not None:
            raise self.error
        return self.log


def node_server(name, node_name, managed_by):
    """A server double shaped where the scope, the read models and the route's source read it."""
    srv = types.SimpleNamespace(
        name=name, display_name=name, status=Status.RUNNING, maintenance=False,
        node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=f"{node_name}-Instance"), port=10309,
        current_mission=None, is_remote=False, players={},
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )
    srv.get_active_players = lambda: []
    return srv


def fleet(*, b_online=True, a_log=LOG_A, b_log=LOG_B, a_error=None, b_error=None):
    """Two nodes: ``dcs-a-01`` (the MASTER) and ``dcs-b-01`` (online only on request)."""
    a = node_server("SRS-1", MASTER, ["Host A"])
    b = node_server("SRS-B", AGENT, ["Host B"])
    node_a = LogNode(MASTER, master=True, log=a_log, error=a_error)
    node_b = LogNode(AGENT, log=b_log, error=b_error) if b_online else None
    return Cluster([a, b], nodes={MASTER: node_a, AGENT: node_b}, node=node_a,
                   instances=[instance(f"{MASTER}-Instance", a), instance(f"{AGENT}-Instance", b)])


def agent(cluster):
    """The AGENT node double of a fleet."""
    return cluster.nodes[AGENT]


def master(cluster):
    return cluster.nodes[MASTER]


async def both_screens(http) -> dict[str, str]:
    """The standalone /nodes page and the dashboard's Nodes tab — two screens of one component."""
    return {"nodes": await open_page(http, NODES),
            "dashboard": await open_page(http, DASHBOARD + "?tab=nodes")}


def download_links(html: str) -> set[str]:
    """The download hrefs a page actually RENDERS."""
    return set(re.findall(r'class="act-btn dl" href="([^"]+)"', html))


# ------------------------------------------------------------- 1. the route and the capability

def test_the_download_is_registered_under_the_log_panels_own_capability(config_dir):
    """No new capability: the route declares ``logs.view``, the log panel's own."""
    declared = nodes_page.capabilities()
    assert declared[nodes_page.NODES_LOG_PATH] == logs_page.LOGS_CAPABILITY == "logs.view"
 # …and the capability stays exactly what the log panel declared it as: Admin-only, no scope grant
    assert permissions.roles_for_capability("logs.view") == logs_page.LOGS_ROLES == ("Admin",)
    assert permissions.scope_grants("logs.view") is False


def test_the_download_path_is_one_percent_encoded_segment():
    """A node name becomes ONE path segment — nothing a name can say walks out of the route."""
    assert nodes_page.log_download_path("dcs-b-01") == "/nodes/dcs-b-01/log"
    assert nodes_page.log_download_path("weird/name x") == "/nodes/weird%2Fname%20x/log"


# -------------------------------------------------------------------------- 2. the control

@pytest.mark.asyncio
async def test_the_control_is_offered_on_every_node_row_agent_and_master_alike(config_dir,
                                                                              clean_registry):
    """BOTH nodes render the control, on BOTH screens."""
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = await both_screens(http)
    for where, html in pages.items():
        assert download_links(html) == {f"/nodes/{MASTER}/log", f"/nodes/{AGENT}/log"}, where
        assert f'aria-label="Download the bot log of node {AGENT}"' in html, where
        assert f'aria-label="Download the bot log of node {MASTER}"' in html, where
 # the control names itself, not only a glyph
        assert f'title="Download log — hand over {AGENT}&#39;s own bot log file as a download"' \
            in html or f"title=\"Download log — hand over {AGENT}'s own bot log file as a download\"" \
            in html, where
 # THE GLYPH IS DRAWN — an unlabelled icon with an empty <svg> is the same failure as no
 # accessible name (the pin ``test_webui_node_controls`` keeps for the write controls).
        anchor = re.search(r'class="act-btn dl" href="/nodes/%s/log"[^>]*>.*?</a>' % AGENT,
                           html, re.S)
        assert anchor, where
        assert "<svg" in anchor.group(0) and "<path" in anchor.group(0), where


def test_the_download_control_names_a_glyph_that_exists():
    """A control may not name a glyph the partial does not draw (an empty ``<svg>`` in a button)."""
    from pathlib import Path

    source = Path(nodes_page.__file__).parent.parent.joinpath("templates", "_icons.html")
    glyphs = set(re.findall(r"'([a-z_]+)':\s*'", source.read_text(encoding="utf-8")))
    assert "download" in glyphs, "the download glyph is missing from the set"


@pytest.mark.asyncio
async def test_the_control_needs_no_plugin_because_it_reads_a_core_file(config_dir, clean_registry):
    """The download is a READ of a file the Node API owns: it survives an install with no plugin."""
    app = build(config_dir, fleet(), plugins=())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    assert download_links(html) == {f"/nodes/{MASTER}/log", f"/nodes/{AGENT}/log"}


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["mod1", "hoster", "plain"])
async def test_a_non_admin_is_offered_no_control(config_dir, clean_registry, who):
    """Every role below Admin sees no download link — and no acts cell at all where they read none."""
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, who)
        response = await http.get(NODES)
    if response.status_code == 403:
        return                      # ``plain`` cannot read /nodes at all — nothing to offer
    assert response.status_code == 200, f"{who}: {response.status_code}"
    assert download_links(response.text) == set(), who
    assert 'class="act-btn dl"' not in response.text, who


@pytest.mark.asyncio
async def test_an_offline_node_is_offered_no_control(config_dir, clean_registry):
    """The heartbeat verdict decides: an unreachable node offers nothing — and its download fails."""
    app = build(config_dir, fleet(b_online=False), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
        reply = await http.get(f"/nodes/{AGENT}/log")
 # the online MASTER keeps its control — the assertion above is not vacuous
    assert download_links(html) == {f"/nodes/{MASTER}/log"}
    assert reply.status_code == 404
    assert "offline or unknown" in reply.text
    assert reply.text.strip() != "", "an offline node must not answer with an empty body"


# ------------------------------------------------------------------------------- 3. the bytes

@pytest.mark.asyncio
async def test_the_agent_rows_download_hands_over_that_nodes_own_log(config_dir, clean_registry):
    """THE CARD'S CORE ACCEPTANCE: node B's bytes, a marker line unique to it, on the wire."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/nodes/{AGENT}/log")
    assert reply.status_code == 200, reply.text
    assert reply.content == LOG_B
    assert b"AGENT-B-LOG-MARKER" in reply.content
    assert b"MASTER-LOG-MARKER" not in reply.content
    assert reply.headers["content-type"].startswith("text/plain")
    assert reply.headers["content-disposition"] == 'attachment; filename="dcssb-dcs-b-01.log"'
 # THE PATH THE NODE WAS ASKED FOR: the RELATIVE one run.py writes — resolved ON THE NODE
    assert agent(cluster).reads == ["logs/dcssb-dcs-b-01.log"]


@pytest.mark.asyncio
async def test_the_masters_row_hands_over_the_masters_own_log(config_dir, clean_registry):
    """The MASTER's row works too — and reads the SAME relative path the log panel tails."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/nodes/{MASTER}/log")
    assert reply.status_code == 200, reply.text
    assert reply.content == LOG_A
    assert b"MASTER-LOG-MARKER" in reply.content
    assert master(cluster).reads == ["logs/dcssb-dcs-a-01.log"]
 # the very path the log panel builds for the master (``readmodels.log_path_for``)
    from services.webservice import readmodels
    # the node is addressed with the forward-slashed relative path run.py writes, on every host
    assert readmodels.log_path_for(MASTER).as_posix() == "logs/dcssb-dcs-a-01.log"


@pytest.mark.asyncio
async def test_the_node_is_asked_for_a_relative_path_never_a_console_absolute_one(config_dir,
                                                                                 clean_registry):
    """THE POINT-2 VERDICT, pinned: the path crossing the wire is relative and OS-agnostic.

    A master-local absolute path (``/home/...`` on WSL, ``C:\\...`` on Windows) would be wrong on
    any other machine; the node resolves ``logs/dcssb-<node>.log`` in its OWN working directory,
    exactly as ``run.py`` writes it.
    """
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        await http.get(f"/nodes/{AGENT}/log")
    asked = agent(cluster).reads
    assert asked == ["logs/dcssb-dcs-b-01.log"]
    assert not asked[0].startswith(("/", "\\", "C:", "\\\\")), "a console-local path crossed the wire"


# -------------------------------------------------------------------- 4. the bound and failures

@pytest.mark.asyncio
async def test_an_oversized_log_is_refused_with_its_own_sentence(config_dir, clean_registry):
    """The declared rule: above the limit the file is REFUSED whole, never served clipped."""
    limit = nodes_page.LOG_DOWNLOAD_MAX_BYTES
    cluster = fleet(b_log=b"x" * (limit + 1))
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/nodes/{AGENT}/log")
    assert reply.status_code == 413
    assert str(limit) in reply.text and str(limit + 1) in reply.text
    assert "above the" in reply.text
 # NOT a download at all: no attachment header, and the body is the sentence, not the log
    assert "content-disposition" not in {k.lower() for k in reply.headers}
    assert reply.content != b"x" * (limit + 1)


@pytest.mark.asyncio
async def test_a_log_at_the_limit_is_served(config_dir, clean_registry):
    """The boundary's positive control: AT the limit the file IS served (the rule is `above`)."""
    limit = nodes_page.LOG_DOWNLOAD_MAX_BYTES
    payload = b"y" * limit
    cluster = fleet(b_log=payload)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/nodes/{AGENT}/log")
    assert reply.status_code == 200
    assert reply.content == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("error, status, fragment", [
    (FileNotFoundError("nope"), 404, "No log file on node"),
    (PermissionError("denied"), 403, "permission denied"),
    (TimeoutError(), 504, "did not answer in time"),
    (RuntimeError("the muddy wire"), 502, "RuntimeError"),
])
async def test_each_failure_gets_its_own_sentence_never_a_zero_byte_download(
        config_dir, clean_registry, error, status, fragment):
    """Every failure branch: its own status and its own sentence, and never an attachment."""
    cluster = fleet(b_error=error)
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/nodes/{AGENT}/log")
    assert reply.status_code == status, reply.text
    assert fragment in reply.text, reply.text
    assert AGENT in reply.text
    assert reply.content.strip(), "a failure must never answer with an empty body"
    assert "content-disposition" not in {k.lower() for k in reply.headers}


@pytest.mark.asyncio
async def test_a_failure_code_instead_of_content_is_refused(config_dir, clean_registry):
    """``read_file`` is ``bytes | int`` and an ``int`` is a failure CODE, never content."""
    cluster = fleet()
    agent(cluster).log = 7                   # the double answers an int, as a failing node would
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(f"/nodes/{AGENT}/log")
    assert reply.status_code == 502
    assert "failure code" in reply.text
    assert reply.content != b"7"


@pytest.mark.asyncio
async def test_a_name_that_matches_nothing_is_the_offline_answer(config_dir, clean_registry):
    """One answer for "offline" and "unknown" — a stale URL cannot enumerate the cluster."""
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        missing = await http.get("/nodes/NOPE-9/log")
    assert missing.status_code == 404
    assert "offline or unknown" in missing.text


# --------------------------------------------------------------------------------- 5. the auth

@pytest.mark.asyncio
async def test_a_non_admin_is_refused_with_the_log_panels_own_refusal(config_dir, clean_registry):
    """The refusal is the SAME one ``/logs`` gives — ONE capability, one answer, byte for byte."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "mod1")            # DCS Admin: reads the fleet, not the log
        download = await http.get(f"/nodes/{AGENT}/log")
        panel = await http.get(logs_page.LOGS_PATH)
    assert download.status_code == panel.status_code == 403
    assert download.json()["detail"] == panel.json()["detail"]
    assert "Admin" in download.json()["detail"]
    assert agent(cluster).reads == [], "a refused caller must reach no node"


@pytest.mark.asyncio
async def test_an_anonymous_caller_is_refused(config_dir, clean_registry):
    """The sixth identity: no session at all. The gate refuses before the handler."""
    cluster = fleet()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        reply = await http.get(f"/nodes/{AGENT}/log")
    assert reply.status_code == 403
    assert agent(cluster).reads == []


# --------------------------------------------------------------------------------- 6. the route

@pytest.mark.asyncio
async def test_the_download_is_a_get_and_never_a_post(config_dir, clean_registry):
    """A read is a GET: the route answers no other method."""
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.post(f"/nodes/{AGENT}/log")
    assert reply.status_code == 405


@pytest.mark.asyncio
async def test_the_download_route_is_registered_through_the_registrar(config_dir, clean_registry):
    """It is a real registered route, and a NON-page: it carries no nav item."""
    app = build(config_dir, fleet(), plugins=("mission", "admin"))
    registrar = app.state.webui_registrar
    routes = {getattr(route, "path", None): route for route in registrar.routes}
    assert nodes_page.NODES_LOG_PATH in routes
    assert nodes_page.NODES_LOG_PATH not in registrar.page_paths
 # the capability is declared for the route's TEMPLATE path (the gate passes the matched route)
    assert registrar.capability_for(route=routes[nodes_page.NODES_LOG_PATH]) == "logs.view"
