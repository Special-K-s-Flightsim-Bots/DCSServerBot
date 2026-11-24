"""The server's own dcs.log in the Log tab (L1, Part B): tail, page back, follow only while open.

the operator's two rulings are the spine: the tab shows the SERVER'S OWN log, and it polls only while the
tab is open. What only a real browser can prove (polling stops when the tab is hidden; a running
server's new lines arrive without a reload) is exercised by ``static/server-log.js`` and is reported
separately — this suite drives the app end to end: the config-driven path, the server-rendered tail,
the no-partial-line rule, follow, page back, the new-log reset, the absent/refused sentences, and the
Admin-only gate (the cluster log panel's own ``logs.view``).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import types
from pathlib import Path

import pytest

from conftest import permissions, run_node_harness
from core import Status
from core.data.impl.nodeimpl import NODE_READ_WINDOW_MAX_BYTES, NodeImpl, file_identity
from services.webservice.pages import logs as logs_page
from services.webservice.pages import server_detail as detail_page
from services.webservice.readmodels import dcslog as dcs_log
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, sign_in,
)

MASTER = "dcs-a-01"
SERVER = "SRS-1"
PAGE = f"/fleet/{SERVER}?tab=log"
WINDOW = f"/fleet/{SERVER}/log/window"

LINES = ("2026-10-04 12:00:00.000 INFO    APP: server started\n"
         "2026-10-04 12:00:01.000 WARNING NET: something happened\n")

#: A POSIX inode above ``2**53`` — the range a network / 64-bit-inode filesystem really hands out, and
#: the value a JavaScript ``JSON.parse`` would round if the identity were shipped as a JSON NUMBER.
BIG_INODE = 2 ** 53 + 3


# --------------------------------------------------------------------------------- test doubles

class WindowNode:
    """The node double: ``read_file_window`` reads a REAL file with the master's own ``seek`` logic.

    It records every read — the path, the length and which cursor was used — so a test can assert the
    window the console asked for. ``error`` (if set) is raised instead, to drive a failure sentence.
    The file's IDENTITY is the REAL :func:`file_identity` of the file it read, so a rotation written
    with :func:`os.replace` (a new inode) changes it exactly as the production path does.
    """

    def __init__(self, name, *, master=True, error=None, plugins=()):
        self.name = name
        self.master = master
        self.instances: dict = {}
        self.error = error
        self.reads: list[dict] = []
        self.listed: list[dict] = []
        #: the node's plugin list — what ``debug_plugin_active`` reads for the optional Events tab.
        self.plugins = list(plugins)

    async def read_file_window(self, path, *, length, offset=None, behind=None):
        self.reads.append({"path": path, "length": length, "offset": offset, "behind": behind})
        if self.error is not None:
            raise self.error
        st = os.stat(path)
        size = st.st_size
        if behind is not None:
            end = max(0, min(int(behind), size))
            start = max(0, end - length)
        else:
            start = min(int(offset), size)
            end = min(size, start + length)
        with open(path, "rb") as handle:
            handle.seek(start)
            return handle.read(max(0, end - start)), size, file_identity(st)

    async def read_file(self, path):
        self.listed.append({"read": path})
        with open(path, "rb") as handle:
            return handle.read()

    async def list_files(self, path, *, pattern="*"):
        """The listing the console's download list reads: real ``glob`` + ``stat``, NEWEST FIRST."""
        self.listed.append({"path": path, "pattern": pattern})
        if self.error is not None:
            raise self.error
        import glob as _glob
        patterns = [pattern] if isinstance(pattern, str) else list(pattern)
        found: dict[str, tuple[str, int, float]] = {}
        for pat in patterns:
            for match in _glob.glob(os.path.join(path, pat)):
                if not os.path.isfile(match):
                    continue
                st = os.stat(match)
                found[match] = (match, int(st.st_size), float(st.st_mtime))
        return sorted(found.values(), key=lambda entry: entry[2], reverse=True)


class FixedIdentityNode(WindowNode):
    """A node whose reads report a FIXED identity — the inode above ``2**53`` a network filesystem can
    hand out, which a JSON NUMBER would lose in the browser's ``JSON.parse`` (L2-fix)."""

    def __init__(self, name, identity, **kwargs):
        super().__init__(name, **kwargs)
        self.fixed_identity = identity

    async def read_file_window(self, path, *, length, offset=None, behind=None):
        raw, size, _ = await super().read_file_window(path, length=length, offset=offset,
                                                      behind=behind)
        return raw, size, self.fixed_identity


class RotatingWalkNode(WindowNode):
    """A node that REPLACES the log after its first window read: a rotation lands MID-WALK, so the
    page-back walk must stop at the change rather than page into the replacement file (L2-fix)."""

    def __init__(self, name, *, incoming, **kwargs):
        super().__init__(name, **kwargs)
        self.incoming = incoming
        self.rotated = False

    async def read_file_window(self, path, *, length, offset=None, behind=None):
        result = await super().read_file_window(path, length=length, offset=offset, behind=behind)
        if not self.rotated:
            os.replace(self.incoming, path)          # the rotation lands DURING the walk
            self.rotated = True
        return result


def log_server(home, *, node_name=MASTER, managed_by=("Host A",)):
    """A server double shaped where the scope, the read models and the log context read it."""
    return types.SimpleNamespace(
        name=SERVER, display_name=SERVER, status=Status.RUNNING, maintenance=False,
        node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name="Host-A-Instance", home=str(home)),
        port=10309, current_mission=None, is_remote=False, players={},
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )


def cluster(home, *, node=None, offline=False, managed_by=("Host A",)):
    """A one-server cluster behind the app's provider seam; ``offline`` makes the node entry ``None``."""
    srv = log_server(home, managed_by=managed_by)
    node_obj = None if offline else (node if node is not None else WindowNode(MASTER))
    return Cluster([srv], nodes={MASTER: node_obj}, node=node_obj, instances=[])


def real_master_node() -> NodeImpl:
    """A REAL ``NodeImpl`` in its master shape — the PRODUCTION read path, not the double."""
    node = object.__new__(NodeImpl)
    node.node = types.SimpleNamespace(master=True)
    return node


def write_log(home, text: str):
    """Write the DCS log at ``{home}/Logs/dcs.log`` — the config-driven default path."""
    path = home / "Logs" / "dcs.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def append_log(home, text: str):
    with open(home / "Logs" / "dcs.log", "a", encoding="utf-8") as handle:
        handle.write(text)


def replace_log(home, text: str):
    """Replace the DCS log with a NEW file — a ROTATION. Written via ``os.replace`` from a sibling, so
    the name points at a DIFFERENT inode: exactly the replacement the identity check is there to catch."""
    path = home / "Logs" / "dcs.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    incoming = path.with_name("dcs.log.incoming")
    incoming.write_text(text, encoding="utf-8")
    os.replace(incoming, path)
    return path


def attr_text(html: str, name: str) -> str:
    """A ``data-*`` attribute VERBATIM, as the browser reads it (the identity is an opaque token)."""
    match = re.search(rf'{name}="([^"]*)"', html)
    assert match, f"the page rendered no {name}"
    return match.group(1)


def attr_value(html: str, name: str) -> int:
    match = re.search(rf'{name}="(\d+)"', html)
    assert match, f"the page rendered no {name}"
    return int(match.group(1))


def admin_app(config_dir, tmp_path, **kwargs):
    return build(config_dir, cluster(tmp_path, **kwargs), plugins=())


# ------------------------------------------------------------------ 1. the capability + the route

def test_the_window_route_is_gated_by_the_log_panels_own_capability(config_dir):
    """No new capability: the route declares ``logs.view``, Admin-only, no scope grant."""
    declared = detail_page.capabilities()
    assert declared[detail_page.LOG_WINDOW_PATH] == logs_page.LOGS_CAPABILITY == "logs.view"
    assert permissions.roles_for_capability("logs.view") == logs_page.LOGS_ROLES == ("Admin",)
    assert permissions.scope_grants("logs.view") is False


def test_the_log_path_defaults_to_the_instance_home(tmp_path):
    srv = types.SimpleNamespace(instance=types.SimpleNamespace(home=str(tmp_path)),
                                node=types.SimpleNamespace(locals={}))
    assert dcs_log.dcs_log_path(srv) == os.path.normpath(os.path.join(str(tmp_path), "Logs", "dcs.log"))


def test_the_log_path_honours_the_loganalyser_log_setting(tmp_path):
    """Config-driven: the SAME ``log`` key ``extensions/loganalyser`` reads, never a hard-coded path."""
    node = types.SimpleNamespace(
        locals={"extensions": {"LogAnalyser": {"log": str(tmp_path / "custom" / "dcs2.log")}}})
    srv = types.SimpleNamespace(instance=types.SimpleNamespace(home=str(tmp_path)), node=node)
    assert dcs_log.dcs_log_path(srv) == str(tmp_path / "custom" / "dcs2.log")


def test_the_log_path_expands_environment_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("DCSLOG_TEST_HOME", str(tmp_path))
    node = types.SimpleNamespace(
        locals={"extensions": {"LogAnalyser": {"log": "$DCSLOG_TEST_HOME/x.log"}}})
    srv = types.SimpleNamespace(instance=types.SimpleNamespace(home=str(tmp_path)), node=node)
    # the setting is expanded, not re-joined: the value keeps the separator it was written with
    assert dcs_log.dcs_log_path(srv) == f"{tmp_path}/x.log"


def test_the_window_route_is_registered_through_the_registrar(config_dir, tmp_path, clean_registry):
    app = admin_app(config_dir, tmp_path)
    registrar = app.state.webui_registrar
    routes = {getattr(route, "path", None): route for route in registrar.routes}
    assert detail_page.LOG_WINDOW_PATH in routes
    # a NON-page: it carries no nav item
    assert detail_page.LOG_WINDOW_PATH not in registrar.page_paths
    assert registrar.capability_for(route=routes[detail_page.LOG_WINDOW_PATH]) == "logs.view"


# ------------------------------------------------------------------- 2. the tab and the tail

@pytest.mark.asyncio
async def test_the_log_tab_is_offered_to_an_admin_only(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")                 # Admin
        page = await http.get(PAGE)
    assert page.status_code == 200
    assert 'id="dcs-log-card"' in page.text

    async with client(app) as http:
        await sign_in(http, "mod1")                     # DCS Admin — no logs.view
        overview = await http.get(f"/fleet/{SERVER}?tab=overview")
        refused = await http.get(PAGE)
    assert "?tab=log" not in overview.text, "the Log tab must not be offered to a DCS Admin"
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_the_tail_is_rendered_server_side_and_has_no_bot_log_pointer(config_dir, tmp_path,
                                                                          clean_registry):
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
    assert 'id="dcs-log-body"' in html
    assert "server started" in html and "something happened" in html
    # the window endpoint and the cursors are on the card, for the follow/page-back script
    assert f'data-window-url="{WINDOW}"' in html
    assert 'data-offset="' in html and 'data-before="' in html and 'data-identity="' in html
    # the node is named, but the bot-log pointer is GONE (L3): the "Open the cluster-wide Logs page"
    # line read as if it were about the DCS log and is not wanted. (The phrase is unique to it — the
    # sidebar's own /logs nav link, which stays, is a different thing entirely.)
    assert "Node <b>" in html and MASTER in html
    assert "cluster-wide Logs page" not in html
    assert "Open the cluster-wide" not in html


@pytest.mark.asyncio
async def test_a_half_written_line_is_never_rendered(config_dir, tmp_path, clean_registry):
    """DCS writes while we read: the final torn line is held back until its newline arrives."""
    write_log(tmp_path, LINES + "2026-10-04 12:00:02.000 INFO    PARTIAL-NO-NEWLINE")
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        assert "PARTIAL-NO-NEWLINE" not in html, "a torn line must not render as if complete"
        offset = attr_value(html, "data-offset")
        append_log(tmp_path, "\n")                       # the newline arrives
        follow = await http.get(WINDOW, params={"offset": str(offset)})
    payload = follow.json()
    assert payload["available"] is True
    assert "PARTIAL-NO-NEWLINE" in payload["lines"]
    assert payload["offset"] > offset


# --------------------------------------------------------------------------------- 3. follow

@pytest.mark.asyncio
async def test_follow_returns_only_the_appended_lines(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        offset = attr_value(html, "data-offset")
        append_log(tmp_path, "2026-10-04 12:00:05.000 ERROR   NEW-LINE-HERE\n")
        reply = await http.get(WINDOW, params={"offset": str(offset)})
    payload = reply.json()
    assert payload["available"] is True
    assert "NEW-LINE-HERE" in payload["lines"]
    assert "server started" not in payload["lines"], "follow must not re-send what was shown"
    assert payload["offset"] > offset and payload["partial"] is False


@pytest.mark.asyncio
async def test_an_unchanged_log_follows_to_nothing(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        offset = attr_value(html, "data-offset")
        reply = await http.get(WINDOW, params={"offset": str(offset)})
    payload = reply.json()
    assert payload["lines"] == "" and payload["offset"] == offset


# ------------------------------------------------------------------------------ 4. page back

@pytest.mark.asyncio
async def test_paging_back_returns_older_lines(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, "".join(f"2026-10-04 12:00:{i:02d}.000 INFO    LINE-{i:02d}\n"
                                for i in range(60)))
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        before = attr_value(html, "data-before")
        reply = await http.get(WINDOW, params={"behind": str(before)})
    payload = reply.json()
    assert payload["available"] is True
    assert "LINE-00" in payload["lines"], "the oldest lines must arrive when paging back"
    assert payload["before"] < before, "the older cursor must move toward the start"


@pytest.mark.asyncio
async def test_a_rotation_DURING_the_page_back_walk_never_splices_two_files(config_dir, tmp_path,
                                                                            clean_registry):
    """The blocker the review proved: a rotation that lands WHILE the walk pages back must end the
    page there. The walk carries the identity it started from; the page holds the PREVIOUS file's
    lines alone and the reset is reported — the replacement's lines are never spliced in."""
    noise = "".join(f"2026-10-04 12:00:{i % 60:02d}.000 DEBUG   NOISE-{i:05d} {'x' * 60}\n"
                    for i in range(2000))
    old = "2026-10-04 11:00:00.000 WARNING OLD-WARN-START\n" + noise
    old += "".join(f"2026-10-04 11:30:{i:02d}.000 WARNING OLD-WARN-{i:02d}\n" for i in range(30))
    path = write_log(tmp_path, old)
    size = os.path.getsize(path)
    # the replacement file, staged beside the log and swapped in AFTER the first window read. It is
    # made LARGER than the page-back cursor (the first window's start), so the ``size < cursor`` check
    # alone cannot see the rotation — only the IDENTITY can, exactly the case the review proved.
    incoming = path.with_name("dcs.log.incoming")
    incoming.write_text("".join(f"2026-10-04 13:00:{i % 60:02d}.000 WARNING NEW-FILE-{i:05d} "
                                f"{'y' * 40}\n" for i in range(4000)), encoding="utf-8")
    cursor_after_first_window = max(0, size - dcs_log.WINDOW_BYTES)
    assert os.path.getsize(incoming) > cursor_after_first_window, \
        "the replacement must be BIGGER than the page-back cursor, or the size check alone stops it"
    node = RotatingWalkNode(MASTER, incoming=incoming)
    app = admin_app(config_dir, tmp_path, node=node)
    async with client(app) as http:
        await sign_in(http, "specialk")
        payload = (await http.get(WINDOW, params={"behind": str(size), "level": "warning"})).json()
    assert payload["available"] is True
    assert payload.get("reset") is True, "a rotation landing mid-walk must be reported"
    assert "OLD-WARN-29" in payload["lines"], "the lines gathered from the OLD file are kept"
    assert "NEW-FILE-" not in payload["lines"], "no page may ever mix lines from two files"
    assert "NOISE-" not in payload["lines"], "the level filter still holds across the walk"


# ------------------------------------------------------------------ 5. a new log (reset)

@pytest.mark.asyncio
async def test_an_offset_past_the_file_resets_to_the_new_log(config_dir, tmp_path, clean_registry):
    """A truncation/rotation is a NEW log: the view resets, never splices two files together."""
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        # the log is replaced by a SMALLER one and the follower's offset is past its end
        write_log(tmp_path, "2026-10-04 12:30:00.000 INFO    NEW-FILE-AFTER-ROTATION\n")
        reply = await http.get(WINDOW, params={"offset": "999999"})
    payload = reply.json()
    assert payload["available"] is True
    assert payload.get("reset") is True
    assert "NEW-FILE-AFTER-ROTATION" in payload["lines"]
    assert "server started" not in payload["lines"]


@pytest.mark.asyncio
async def test_the_card_carries_the_reads_file_identity(config_dir, tmp_path, clean_registry):
    """The browser holds the file's identity and echoes it back, so a rotation can be told from an
    append even when the new file is already bigger than the offset."""
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
    assert 'data-identity="' in html
    assert attr_text(html, "data-identity") not in ("", "0")


@pytest.mark.asyncio
async def test_an_append_does_not_reset_the_view(config_dir, tmp_path, clean_registry):
    """An append keeps the identity: the follow continues and is NOT announced as a rotation."""
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        offset = attr_value(html, "data-offset")
        identity = attr_text(html, "data-identity")
        append_log(tmp_path, "2026-10-04 12:05:00.000 ERROR   APPENDED-LINE\n")
        reply = await http.get(WINDOW, params={"offset": str(offset), "identity": identity})
    payload = reply.json()
    assert payload["available"] is True
    assert payload.get("reset") is not True, "an append must never be announced as a rotation"
    assert "APPENDED-LINE" in payload["lines"]
    assert payload["identity"] == identity, "the identity must not move on an append"


@pytest.mark.asyncio
async def test_an_inode_above_2_53_crosses_json_as_a_string_and_never_fakes_a_rotation(
        config_dir, tmp_path, clean_registry):
    """A POSIX inode can exceed ``2**53`` (network / 64-bit-inode filesystems). Shipped as a JSON
    NUMBER it would lose its low bits in the browser's ``JSON.parse`` and a value that never changed
    would come back CHANGED — a rotation announced where none happened. The identity must therefore
    cross JSON as a STRING token, and an append on such a node must NOT reset the view."""
    write_log(tmp_path, LINES)
    node = FixedIdentityNode(MASTER, BIG_INODE)
    app = admin_app(config_dir, tmp_path, node=node)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        offset = attr_value(html, "data-offset")
        assert attr_text(html, "data-identity") == str(BIG_INODE), \
            "the card must carry the exact inode, not a rounded number"
        append_log(tmp_path, "2026-10-04 12:06:00.000 ERROR   AFTER-BIG-INODE\n")
        reply = await http.get(WINDOW, params={"offset": str(offset), "identity": str(BIG_INODE)})
    payload = reply.json()
    assert isinstance(payload["identity"], str), \
        "the identity must cross JSON as a STRING — a number loses precision above 2**53"
    assert payload["identity"] == str(BIG_INODE)
    assert payload.get("reset") is not True, "an unchanged big inode must never announce a rotation"
    assert "AFTER-BIG-INODE" in payload["lines"]


@pytest.mark.asyncio
async def test_a_rotation_to_a_BIGGER_file_resets_the_view_and_never_splices(config_dir, tmp_path,
                                                                            clean_registry):
    """The gap the review named: the replacement is ALREADY past the offset the follower holds, so a
    ``size < offset`` check cannot see it — the IDENTITY (a new file) must, or two files get spliced."""
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        offset = attr_value(html, "data-offset")
        identity = attr_text(html, "data-identity")
        # a rotation whose replacement file is LONGER than the offset the follower holds
        bigger = LINES + "".join(f"2026-10-04 13:00:{i:02d}.000 INFO    NEW-BIG-{i:02d}\n"
                                 for i in range(40))
        assert len(bigger.encode()) > offset
        replace_log(tmp_path, bigger)
        reply = await http.get(WINDOW, params={"offset": str(offset), "identity": identity})
    payload = reply.json()
    assert payload["available"] is True
    assert payload.get("reset") is True, "a bigger replacement must reset, not splice"
    assert "NEW-BIG-39" in payload["lines"]
    assert "server started" not in payload["lines"], "the OLD file's lines must never be spliced in"
    assert payload["identity"] != identity, "the replacement file's identity must be reported"


@pytest.mark.asyncio
async def test_the_REAL_node_read_path_flows_end_to_end(config_dir, tmp_path, clean_registry):
    """Not a double: the PRODUCTION ``NodeImpl`` read path drives the route — the real inode identity
    is what resets the view when a rotation's replacement is already bigger than the offset."""
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path, node=real_master_node())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        offset = attr_value(html, "data-offset")
        identity = attr_text(html, "data-identity")
        bigger = LINES + "".join(f"2026-10-04 14:00:{i:02d}.000 INFO    REAL-BIG-{i:02d}\n"
                                 for i in range(40))
        assert len(bigger.encode()) > offset
        replace_log(tmp_path, bigger)
        reply = await http.get(WINDOW, params={"offset": str(offset), "identity": identity})
    payload = reply.json()
    assert payload["available"] is True
    assert payload.get("reset") is True
    assert "REAL-BIG-39" in payload["lines"]
    assert payload["identity"] != identity


# ------------------------------------------------------------- 6. the empty and refused states

@pytest.mark.asyncio
async def test_a_server_with_no_log_file_yet_is_its_own_sentence(config_dir, tmp_path,
                                                                 clean_registry):
    """The empty state IS the log body's own page (L3): the body element is present (so the follower
    can start and poll from the empty state), the honest sentence sits BESIDE it, and the window route
    answers the NORMAL state with ``pending: true`` — never the backoff a genuine failure drives."""
    app = admin_app(config_dir, tmp_path)                 # no file written
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        reply = await http.get(WINDOW, params={"offset": "0"})
    assert "No log file yet" in html
    # THE BODY IS RENDERED even in the empty state — the defect was that it was NOT, so the follower
    # bailed out and the sentence could never become the log.
    assert 'id="dcs-log-body"' in html
    assert 'id="dcs-log-note"' in html and 'id="dcs-log-note-text"' in html
    payload = reply.json()
    assert payload["available"] is False and "No log file yet" in payload["message"]
    assert "dcs.log" in payload["message"]
    assert payload.get("pending") is True, "no file yet is a NORMAL state, not a failure"


@pytest.mark.asyncio
async def test_the_empty_state_becomes_the_log_when_the_file_appears(config_dir, tmp_path,
                                                                     clean_registry):
    """the operator's flow, end to end (L3): open the Log tab for a server with no ``dcs.log`` yet, then the
    server starts and writes one. The window route must answer the empty state as ``pending`` (so the
    follower keeps polling), and the SAME follower offset must return the real lines the moment the
    file exists — no reload, no tab switch."""
    app = admin_app(config_dir, tmp_path)                 # no file yet
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        offset = attr_value(html, "data-offset")
        empty = (await http.get(WINDOW, params={"offset": str(offset)})).json()
        write_log(tmp_path, LINES)                        # the server starts and writes the log
        follow = (await http.get(WINDOW, params={"offset": str(offset)})).json()
    assert empty["available"] is False and empty.get("pending") is True
    assert follow["available"] is True, "the file's appearance must render the log on the next tick"
    assert "server started" in follow["lines"]
    assert follow["offset"] > offset


@pytest.mark.asyncio
async def test_the_log_going_away_is_reported_rather_than_a_stale_tail(config_dir, tmp_path,
                                                                       clean_registry):
    """The reverse of the operator's flow (L3): the log is present, then DISAPPEARS (rotated away). The
    window route must answer ``pending`` again — the follower clears the stale tail and says so."""
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        offset = attr_value(html, "data-offset")
        assert "server started" in html, "the tail was shown first"
        (tmp_path / "Logs" / "dcs.log").unlink()          # the log is rotated/removed away
        gone = (await http.get(WINDOW, params={"offset": str(offset)})).json()
    assert gone["available"] is False
    assert gone.get("pending") is True, "the file is gone, so the state is the normal 'not there' one"
    assert "No log file yet" in gone["message"]


@pytest.mark.asyncio
async def test_an_offline_node_is_its_own_sentence(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path, offline=True)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        reply = await http.get(WINDOW, params={"offset": "0"})
    assert "offline or unknown" in html
    assert reply.json()["available"] is False
    assert "offline or unknown" in reply.json()["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error, fragment", [
    (PermissionError("no"), "permission denied"),
    (TimeoutError(), "did not answer in time"),
    (RuntimeError("the muddy wire"), "RuntimeError"),
])
async def test_each_node_failure_gets_its_own_sentence(config_dir, tmp_path, clean_registry,
                                                       error, fragment):
    write_log(tmp_path, LINES)
    node = WindowNode(MASTER, error=error)
    app = admin_app(config_dir, tmp_path, node=node)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        reply = await http.get(WINDOW, params={"offset": "0"})
    assert fragment in html
    payload = reply.json()
    assert payload["available"] is False and fragment in payload["message"]
    assert payload["lines"] == "", "a refused read must never answer with log content"


@pytest.mark.asyncio
async def test_an_empty_window_with_no_cursor_is_refused(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(WINDOW)
    assert reply.status_code == 400
    assert "offset or a behind position is required" in reply.json()["message"]


# --------------------------------------------------------------------------------- 7. the gate

@pytest.mark.asyncio
async def test_a_non_admin_reaches_no_node_through_the_window_route(config_dir, tmp_path,
                                                                    clean_registry):
    write_log(tmp_path, LINES)
    node = WindowNode(MASTER)
    app = admin_app(config_dir, tmp_path, node=node)
    async with client(app) as http:
        await sign_in(http, "mod1")                      # DCS Admin
        reply = await http.get(WINDOW, params={"offset": "0"})
        panel = await http.get(logs_page.LOGS_PATH)
    assert reply.status_code == panel.status_code == 403
    assert node.reads == [], "a refused caller must reach no node"


@pytest.mark.asyncio
async def test_an_anonymous_caller_is_refused(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, LINES)
    node = WindowNode(MASTER)
    app = admin_app(config_dir, tmp_path, node=node)
    async with client(app) as http:
        reply = await http.get(WINDOW, params={"offset": "0"})
    assert reply.status_code == 403
    assert node.reads == []


@pytest.mark.asyncio
async def test_the_window_route_is_a_get_and_never_a_post(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.post(WINDOW)
    assert reply.status_code == 405


@pytest.mark.asyncio
async def test_a_non_admin_is_never_offered_the_tab(config_dir, tmp_path, clean_registry):
    """The strip is built from the SAME predicate the refusal reads, so no role below Admin sees it."""
    write_log(tmp_path, LINES)
    app = admin_app(config_dir, tmp_path)
    for who in ("mod1", "hoster", "plain"):
        async with client(app) as http:
            await sign_in(http, who)
            response = await http.get(f"/fleet/{SERVER}?tab=overview")
        if response.status_code == 403:
            continue
        assert "?tab=log" not in response.text, who


# ------------------------------------------------------- 8. the shipped follower script (node harness)

TESTS = Path(__file__).resolve().parent
SERVER_LOG_JS = TESTS.parent / "services" / "webservice" / "static" / "server-log.js"
HARNESS = TESTS / "server_log_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def follower() -> dict:
    """The shipped follower's own answers, driven by ``server_log_harness.js``."""
    assert HARNESS.is_file(), "the harness that drives the shipped script is missing"
    assert SERVER_LOG_JS.is_file(), "the shipped server-log script is missing"
    done = run_node_harness(["node", str(HARNESS), str(SERVER_LOG_JS)])
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@requires_node
def test_the_follower_polls_while_visible_and_appends_new_lines(follower):
    """A running server's new lines arrive WITHOUT a reload: the tick appends them and moves the offset."""
    assert follower["bootFetches"] == [f"{WINDOW}?offset=10"], "one immediate follow on arrival"
    visible = follower["visible"]
    assert visible["timerCount"] == 1, "a timer is armed while the tab is open"
    assert visible["appended"] and visible["grewFromBoot"], "the appended bytes landed in the body"
    assert visible["status"] == "following while this tab is open"


@requires_node
def test_the_follower_STOPS_the_moment_the_tab_is_hidden(follower):
    """The rule: nothing polls once the tab is hidden, however many ticks would have passed."""
    hidden = follower["hidden"]
    assert hidden["timerCount"] == 0, "the interval must be cleared when the tab is hidden"
    assert hidden["requestsWhileHidden"] == 0, "no request may be made while the tab is hidden"
    assert hidden["status"] == "paused — this tab is hidden"
    # …and it resumes on its own when the tab is shown again
    resumed = follower["resumed"]
    assert resumed["timerCount"] == 1 and resumed["requestOnResume"] is True


@requires_node
def test_the_follower_resets_on_a_new_log_and_never_splices(follower):
    reset = follower["reset"]
    assert reset["showsNotice"], "a rotation is announced, not swallowed"
    assert reset["showsNewLine"], "the fresh file's lines are shown after the reset"


@requires_node
def test_the_follower_pages_back_and_stops_at_the_start(follower):
    older = follower["older"]
    assert older["requestedBehind"], "Load older asks for the window ending at the before cursor"
    assert older["prepended"] and older["orderKept"], "older lines go ABOVE the ones already shown"
    assert older["buttonDisabled"] is True and older["buttonLabel"] == "Start of log"


@requires_node
def test_the_follower_echoes_the_file_identity(follower):
    """The card's data-identity rides on every follow, so the server can tell a rotation from an append."""
    identity = follower["identity"]
    assert identity["echoed"], "the held identity must be sent as ?identity="
    assert identity["bootFetches"] == [f"{WINDOW}?offset=10&identity=4242"]


@requires_node
def test_the_follower_backs_off_on_a_failed_read(follower):
    """L1-fix: a failed read drops the interval and retries on a growing delay, saying so — never the
    once-per-tick hammering a down node would otherwise get from every open tab."""
    backoff = follower["backoff"]
    assert backoff["timerCount"] == 0, "the normal interval is dropped while failing"
    assert backoff["timeoutCount"] == 1, "exactly one backoff retry is pending"
    assert backoff["status"] == "retrying\u2026", "the status line says so"
    assert backoff["retried"], "the backoff fires a fresh request"
    assert backoff["timerCountAfterRetry"] == 1, "and re-arms the normal interval"
    assert backoff["statusAfterRetry"] == "following while this tab is open", "recovery is announced"


@requires_node
def test_the_follower_backs_off_when_the_node_is_down(follower):
    """An ``available: false`` answer (a node that is down) backs off too, keeping the node's sentence."""
    down = follower["downNode"]
    assert "offline or unknown" in down["status"], "the node's own sentence is kept"
    assert "retrying\u2026" in down["status"]
    assert down["timerCount"] == 0 and down["timeoutCount"] == 1


@requires_node
def test_the_follower_stops_cleanly_mid_backoff_when_hidden(follower):
    """The rule holds through a backoff: a hidden tab clears the pending retry, so nothing polls."""
    hidden = follower["backoffHidden"]
    assert hidden["timerCount"] == 0 and hidden["timeoutCount"] == 0, "no retry survives a hidden tab"
    assert hidden["status"] == "paused \u2014 this tab is hidden"


@requires_node
def test_the_follower_sends_the_level_and_the_file_selector(follower):
    """L2: the card's data-level / data-which ride on EVERY request, so the server filters the window
    the way the page rendered it and reads the right file (events.log on the Events tab)."""
    lw = follower["levelWhich"]
    assert lw["hasLevel"], "the chosen level must be sent"
    assert lw["hasWhich"], "the Events tab's file selector must be sent"
    assert "&level=warning" in lw["bootFetches"][0] and "&which=events" in lw["bootFetches"][0]
    assert "&level=warning" in lw["olderFetch"] and "&which=events" in lw["olderFetch"]


@requires_node
def test_the_follower_marks_the_start_of_the_log_on_at_start(follower):
    """L2: a filtered page that reached the start of the file says so rather than offering a page that
    would come back empty."""
    at_start = follower["atStart"]
    assert at_start["buttonDisabled"] is True
    assert at_start["buttonLabel"] == "Start of log"
    assert at_start["status"] == "No log lines match the selected level."


@requires_node
def test_the_follower_resets_the_view_when_a_rotation_lands_during_the_walk(follower):
    """L2-fix: a page-back that crossed a rotation RESETS the view and says the log restarted; the
    page the walk gathered (the previous file's alone) is shown and the button is spent."""
    walk = follower["walkRotation"]
    assert walk["showsNotice"], "a mid-walk rotation is announced, not swallowed"
    assert walk["showsOldPage"], "the page the walk gathered is still shown"
    assert walk["clearedCurrent"], "the view resets (no splice with the replacement)"
    assert walk["buttonDisabled"] is True


@requires_node
def test_the_follower_keeps_its_cadence_when_the_file_is_not_there_yet(follower):
    """L3: a ``pending`` answer ("no log file yet") is a NORMAL state, not a failure — the follower
    KEEPS its normal interval and arms NO backoff retry, and shows the sentence with an empty body."""
    pending = follower["pending"]
    assert pending["timerCount"] == 1, "the normal interval is KEPT for a file that is not there yet"
    assert pending["timeoutCount"] == 0, "no backoff retry is armed for a NORMAL state"
    assert pending["bodyEmpty"] and pending["noteShown"], "the sentence shows, the body stays empty"
    assert pending["status"] == "waiting for the log file to appear"


@requires_node
def test_the_follower_turns_the_empty_state_into_the_log(follower):
    """the operator's flow, in the shipped script (L3): the empty state must NOT stop the follow — the moment
    real lines arrive they render into the body and the empty sentence gives way."""
    case = follower["emptyBecomesLog"]
    assert case["afterPending"]["bodyEmpty"] and case["afterPending"]["noteShown"]
    assert case["afterPending"]["timerCount"] == 1, "the follow survives the empty state"
    assert case["rendered"], "the first real line renders into the body"
    assert case["noteHidden"], "the empty sentence gives way to the log"
    assert case["status"] == "following while this tab is open"

