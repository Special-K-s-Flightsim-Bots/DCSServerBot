"""The node row's DCS update: ONE control for update, rollback and branch switch.

Pins the whole door, from the declaration to the node: the ACTION's four pre-check refusals and the
four values it hands to ``Node.dcs_update`` unchanged; its three exit-code sentences and the two audit
lines ``/dcs update`` writes; the control's capability (Admin only, no scope grant), its dialog (two
picks, a warning time, an announce box) and the confirm token a crafted POST cannot skip; the
ON-DEMAND version list (``GET /nodes/{node}/dcs/versions``) with every answer it can give; and the
declaration tables that have to move with them (``capabilities()``, the route table).

The engine is never run and no node is asked outside the routes: the ``DcsNode`` double records every
call, so "the updater was never called" is a countable fact rather than an intention.

RED PROOF. Every pin here needs a symbol this feature ADDS, read once at import: with the surface
missing the module still collects and each test fails with the module's own reason
(``_SURFACE_ERROR``), so a red run names the pin instead of dying on an import line.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import types
from pathlib import Path
from urllib.parse import quote

import pytest

from conftest import permissions
from core.actions import ActionContext, action_available, call_action
from services.webservice import readmodels
from services.webservice.pages import actions as actions_page
from services.webservice.pages import nodes as nodes_page
from services.webservice.scope import Scope
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    BOT_YAML, Cluster, build, clean_registry, client, config_dir, instance, open_page,
    recording_bot, server, sign_in, token_of,
)

NODES = nodes_page.NODES_PATH
NODE = "dcs-a-01"
OTHER = "dcs-b-01"
CONFIRM = actions_page.CONFIRM_SUFFIX

#: THE SURFACE THIS MODULE PINS, read once so that a missing symbol is a per-test failure rather than
#: a collection error.
_SURFACE_ERROR: str = ""
try:
    DCS_PATH = actions_page.NODE_DCS_UPDATE_PATH
    DCS_CAPABILITY = actions_page.NODE_DCS_UPDATE_CAPABILITY
    DCS_VERSIONS_PATH = actions_page.DCS_VERSIONS_PATH
    DCS_BRANCH_FIELD = actions_page.DCS_BRANCH_FIELD
    DCS_VERSION_FIELD = actions_page.DCS_VERSION_FIELD
    DCS_WARN_TIME_FIELD = actions_page.DCS_WARN_TIME_FIELD
    versions_url = actions_page.dcs_versions_path
except AttributeError as _missing:  # pragma: no cover - the RED run's own path
    _SURFACE_ERROR = f"the DCS update surface is missing from pages/actions: {_missing}"
    DCS_PATH = "/actions/node/dcs_update"
    DCS_CAPABILITY = "nodes.dcs_update"
    DCS_VERSIONS_PATH = "/nodes/{node}/dcs/versions"
    DCS_BRANCH_FIELD = "branch"
    DCS_VERSION_FIELD = "version"
    DCS_WARN_TIME_FIELD = "warn_time"
    versions_url = lambda node_name: ""    # noqa: E731 - the RED run fails before it is ever used


@pytest.fixture(autouse=True)
def surface_present():
    """RED PROOF: every pin below needs the declared surface, so its absence fails each one."""
    if _SURFACE_ERROR:
        pytest.fail(_SURFACE_ERROR)


# ---------------------------------------------------------------------------------- test doubles

class DcsNode:
    """The ``Node`` double: the DCS API the action and the routes read, every call recorded.

    ``dcs_update`` FLIPS the installed facts when it answers ``0`` — the action reads the branch and
    the version back off the node afterwards, so a double that left them alone could not tell "the
    console names the version it installed" from "the console repeats the request".
    """

    def __init__(self, name, *, master=False, branch="release", version="2.9.0.1",
                 latest="2.9.9.9", versions=("2.9.0.0", "2.9.0.1", "2.9.9.9"), rc=0,
                 versions_error=None):
        self.name = name
        self.master = master
        self.instances = {}
        self.branch = branch
        self.version = version
        self.latest = latest
        self.versions = list(versions) if versions is not None else []
        self.versions_error = versions_error
        self.rc = rc
        self.calls: list[dict] = []
        self.version_lists: list[str] = []

    async def get_dcs_branch_and_version(self):
        return self.branch, self.version

    async def get_latest_version(self, branch):
        return self.latest

    async def get_available_dcs_versions(self, branch):
        self.version_lists.append(branch)
        if self.versions_error is not None:
            raise self.versions_error
        return list(self.versions)

    async def dcs_update(self, *, branch=None, version=None, warn_times=None, announce=True):
        self.calls.append({"branch": branch, "version": version, "warn_times": warn_times,
                           "announce": announce})
        if self.rc == 0:
            self.branch = branch or self.branch
            self.version = version or self.version
        return self.rc


class DcsCluster(Cluster):
    """The stub cluster, plus the DECLARED DCS map the node rows and the dialog read."""

    def __init__(self, servers=(), *, nodes=None, node=None, instances=(), declared_dcs=None):
        super().__init__(servers, nodes=nodes, node=node, instances=instances)
        self.declared_dcs = dict(declared_dcs or {})

    def source(self):
        return readmodels.LiveSource(servers=tuple(self.servers), instances=tuple(self.instances),
                                     nodes=self.nodes, node=self.node, log_path=None,
                                     declared_dcs=self.declared_dcs)


class Fleet:
    """``nodes.yaml``'s DCS declarations, for BOTH readers of that fact.

    The console reads it from the source (``declared_dcs``: the row's control, the dialog) and the
    ACTION reads it from ``get_node_data().dcs`` (its own "this node declares no DCS" refusal). This
    keeps both on ONE dict, so a test cannot declare a node on one side only.
    """

    def __init__(self, monkeypatch):
        self.blocks: dict[str, dict] = {}
        monkeypatch.setattr("core.utils.validators.get_node_data",
                            lambda: types.SimpleNamespace(dcs=self.blocks))

    def declare(self, *names: str, announce: bool = True) -> "Fleet":
        """Give *names* a DCS block, with credentials so every branch is offered.

        ``announce`` adds the ``DCS.announce`` block the dialog's announce box is drawn from; pass
        ``False`` for a node that configures no announcement.
        """
        self.blocks.clear()
        for name in names:
            block: dict[str, object] = {"installation": "C:/DCS World", "user": "pilot"}
            if announce:
                block["announce"] = {"channel": 11223344556677}
            self.blocks[name] = block
        return self

    def cluster(self, *, node=None, other=None) -> DcsCluster:
        """A one- (or two-) node cluster whose rows carry exactly the declared map."""
        srv = server("SRS-1", None, node_name=NODE, instance="Host-A-Instance")
        first = node or DcsNode(NODE, master=True)
        nodes = {first.name: first}
        if other is not None:
            nodes[other.name] = other
        return DcsCluster([srv], nodes=nodes, node=first,
                          instances=[instance("Host-A-Instance", srv)],
                          declared_dcs=self.blocks)


@pytest.fixture
def fleet(monkeypatch):
    """The declarations: ``fleet.declare(NODE).cluster()``."""
    return Fleet(monkeypatch)


def app_with(config_dir, cluster, plugins=("mission", "admin")):
    """A real application: the production factory, the shell, the local backend and the stub source.

    Named so another module can render THIS control's dialog without rebuilding the declaration
    doubles (``tests/test_webui_dialog_script_parses.py`` parses its inline script).
    """
    return build(config_dir, cluster, plugins=plugins)


def action_context(node):
    """The seam's context for a DIRECT action call — the Discord/MCP shape, with no request."""
    bus = types.SimpleNamespace(servers={}, nodes={node.name: node} if node is not None else {})
    identity = types.SimpleNamespace(roles={"Admin"}, scope=Scope.everything(),
                                     subject="specialk", shown_name="specialk")
    return ActionContext.from_web(identity, None, bus)


async def _dialog_for(http, node_name=NODE):
    """``(page token, dialog markup)`` — the nodes page's CSRF token and the control's own dialog."""
    page = await open_page(http, NODES)
    token = token_of(page)
    dialog = await http.post(DCS_PATH + CONFIRM, data={"node": node_name, "_csrf_token": token})
    return token, dialog


# ------------------------------------------------------------------ 1. the wiring and the control

@pytest.mark.asyncio
async def test_the_web_shell_discovers_the_dcs_update_action(config_dir, fleet):
    """``update_dcs`` reaches the console through the shell's own discovery, and only with ``admin``."""
    assert action_available("update_dcs") is False, "the registry must start EMPTY for this test"
    build(config_dir, fleet.declare(NODE).cluster(), plugins=("mission",))
    assert action_available("update_dcs") is False, "a shell without the admin plugin found it"
    build(config_dir, fleet.declare(NODE).cluster(), plugins=("mission", "admin"))
    assert action_available("update_dcs") is True


@pytest.mark.asyncio
async def test_the_row_offers_the_control_on_a_node_that_declares_dcs_and_not_on_one_that_does_not(
        config_dir, fleet):
    """OFFERED ALWAYS on a node that HAS DCS — no state gate, no availability check."""
    with_dcs = fleet.declare(NODE).cluster()
    app = build(config_dir, with_dcs, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    assert f'action="{DCS_PATH}{CONFIRM}"' in html

    without = fleet.declare(OTHER).cluster()
    app = build(config_dir, without, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await open_page(http, NODES)
    assert f'action="{DCS_PATH}{CONFIRM}"' not in html, \
        "a node that declares no DCS was offered a DCS update"


@pytest.mark.asyncio
async def test_the_control_is_admin_only_and_a_valid_csrf_token_does_not_help(config_dir, fleet):
    """A crafted POST without the capability is refused by the GATE, token and all."""
    cluster = fleet.declare(NODE).cluster()
    node = cluster.nodes[NODE]
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, "/fleet")
        for path in (DCS_PATH, DCS_PATH + CONFIRM):
            reply = await http.post(path, data={"node": NODE, "_csrf_token": token_of(page)})
            assert reply.status_code == 403, path
            assert "Not authorized" in reply.json()["detail"]
    assert node.calls == [], "an unauthorized POST reached the updater"


@pytest.mark.asyncio
async def test_a_post_that_skipped_the_dialog_is_refused(config_dir, fleet):
    """The confirmation is a control, not a screen: ``curl`` cannot skip it."""
    cluster = fleet.declare(NODE).cluster()
    node = cluster.nodes[NODE]
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, NODES)
        reply = await http.post(DCS_PATH, data={"node": NODE, "_csrf_token": token_of(page)})
    assert reply.status_code == 403
    assert "confirmed through its dialog" in reply.json()["detail"]
    assert node.calls == []


# --------------------------------------------------------------------- 2. the action's refusals

@pytest.mark.asyncio
async def test_a_node_that_declares_no_dcs_is_refused_and_nothing_is_called(config_dir, fleet):
    """No installation declared is its OWN sentence, and the engine is never reached."""
    cluster = fleet.declare(OTHER).cluster()
    node = cluster.nodes[NODE]
    build(config_dir, cluster, plugins=("mission", "admin"))
    result = await call_action("update_dcs", action_context(node), node_name=NODE,
                               branch="release", version="2.9.9.9")
    assert result.success is False
    assert result.message == (f"Node '{NODE}' declares no DCS installation, so there is nothing to "
                              f"update.")
    assert node.calls == []


@pytest.mark.asyncio
async def test_the_installed_version_on_the_installed_branch_is_refused_with_its_own_sentence(
        config_dir, fleet):
    """Equal target is a refusal that names BOTH facts — never the Discord "latest on branch" line."""
    cluster = fleet.declare(NODE).cluster()
    node = cluster.nodes[NODE]
    build(config_dir, cluster, plugins=("mission", "admin"))
    result = await call_action("update_dcs", action_context(node), node_name=NODE,
                               branch="release", version=node.version)
    assert result.success is False
    assert result.message == (f"Version {node.version} of branch 'release' is already installed on "
                              f"node '{NODE}'.")
    assert "is the latest on branch" not in result.message, "the Discord sentence was reused"
    assert node.calls == []


@pytest.mark.asyncio
async def test_an_empty_version_answer_from_ed_is_refused_and_nothing_is_called(config_dir, fleet):
    """ED answering nothing is a refusal naming its causes — never an update with an empty version."""
    for latest in (None, ""):
        cluster = fleet.declare(NODE).cluster(node=DcsNode(NODE, master=True, latest=latest))
        node = cluster.nodes[NODE]
        build(config_dir, cluster, plugins=("mission", "admin"))
        result = await call_action("update_dcs", action_context(node), node_name=NODE,
                                   branch="release")
        assert result.success is False
        assert result.message == (
            f"No version information for branch 'release' on node '{NODE}' — ED may be unreachable, "
            f"or this node has no DCS credentials.")
        assert node.calls == []


# ---------------------------------------------------------------- 3. the call and its three codes

@pytest.mark.asyncio
async def test_the_four_values_reach_the_node_unchanged(config_dir, fleet, recording_bot):
    """``warn_times=[warn_time]``, the branch, the version and ``announce`` — asserted as the CALL."""
    cluster = fleet.declare(NODE).cluster()
    node = cluster.nodes[NODE]
    build(config_dir, cluster, plugins=("mission", "admin"))
    result = await call_action("update_dcs", action_context(node), node_name=NODE,
                               branch="testing", version="2.9.9.9", warn_time=15, announce=False)
    assert node.calls == [{"branch": "testing", "version": "2.9.9.9", "warn_times": [15],
                           "announce": False}]
    assert result.success is True
    # the 0 case names the version READ BACK off the node, not merely the one the caller asked for
    assert result.message == (f"DCS World on node '{NODE}' was updated to version 2.9.9.9 on "
                              f"branch 'testing'.")


@pytest.mark.asyncio
async def test_the_three_exit_codes_read_as_three_different_sentences(config_dir, fleet,
                                                                    recording_bot):
    """``rc`` 0, 2 and anything else are three sentences, and only 0 is a success."""
    sentences: dict[int, str] = {}
    outcomes: dict[int, bool] = {}
    for rc in (0, 2, 7):
        cluster = fleet.declare(NODE).cluster(node=DcsNode(NODE, master=True, rc=rc))
        node = cluster.nodes[NODE]
        build(config_dir, cluster, plugins=("mission", "admin"))
        result = await call_action("update_dcs", action_context(node), node_name=NODE,
                                   branch="testing", version="2.9.9.9", warn_time=60)
        sentences[rc] = result.message
        outcomes[rc] = result.success
        assert len(node.calls) == 1, f"rc {rc}: the updater was not called exactly once"
    assert outcomes == {0: True, 2: False, 7: False}
    assert len(set(sentences.values())) == 3, sentences
    assert "was updated to version 2.9.9.9 on branch 'testing'" in sentences[0]
    assert "aborted (check disk space)" in sentences[2]
    assert "code=7" in sentences[7]


@pytest.mark.asyncio
async def test_the_two_audit_lines_mirror_the_discord_command(config_dir, fleet, recording_bot):
    """``/dcs update`` writes "started …" then "updated DCS from X to Y …"; so does the console."""
    cluster = fleet.declare(NODE).cluster()
    node = cluster.nodes[NODE]
    installed = node.version      # read BEFORE the update moves the double's own facts
    build(config_dir, cluster, plugins=("mission", "admin"))
    await call_action("update_dcs", action_context(node), node_name=NODE, branch="testing",
                      version="2.9.9.9", warn_time=60)
    written = [message for message, _kwargs in recording_bot.calls]
    # each entry leads with the ACTOR (``AuditContext.audit_actor``); the sentence is the rest
    assert any(entry.endswith(f"started an update of all DCS servers on node {NODE}.")
               for entry in written), written
    assert any(entry.endswith(f"updated DCS from {installed} to 2.9.9.9 on node {NODE}.")
               for entry in written), written


# --------------------------------------------------------------------------- 4. the dialog

@pytest.mark.asyncio
async def test_the_dialog_draws_the_two_picks_the_warning_time_and_the_announce_box(config_dir,
                                                                                   fleet):
    """The branch allow-list, the version pick, the warning time and the announce option."""
    cluster = fleet.declare(NODE).cluster()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        _token, dialog = await _dialog_for(http)
    assert dialog.status_code == 200, dialog.text
    markup = dialog.text
    assert f'name="{DCS_BRANCH_FIELD}"' in markup
    assert f'name="{DCS_VERSION_FIELD}"' in markup
    assert f'name="{DCS_WARN_TIME_FIELD}"' in markup
    assert 'type="number"' in markup and 'value="60"' in markup
    assert 'name="announce"' in markup and "checked" in markup
    for branch in (">Release<", ">Testing<", ">Nightly<"):
        assert branch in markup, f"the branch pick does not offer {branch}"
    # the version pick is filled ON DEMAND: the dialog carries the route it reads
    assert versions_url(NODE) in markup


@pytest.mark.asyncio
async def test_a_free_text_branch_is_refused_by_the_route(config_dir, fleet):
    """A crafted branch never reaches the node: the picker's allow-list is the gate."""
    cluster = fleet.declare(NODE).cluster()
    node = cluster.nodes[NODE]
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token, dialog = await _dialog_for(http)
        confirm = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text).group(1)
        reply = await http.post(DCS_PATH, data={"node": NODE, "_csrf_token": token,
                                                "_confirm_token": confirm,
                                                DCS_BRANCH_FIELD: "anything-goes"})
    assert reply.status_code == 400
    assert "not a branch this console offers" in reply.json()["detail"]
    assert node.calls == [], "a free-text branch was forwarded to the node"


# ------------------------------------------------------------------- 5. the version list route

async def _versions(http, node_name=NODE, branch="release"):
    return await http.get(f"/nodes/{quote(node_name, safe='')}/dcs/versions?branch={branch}")


@pytest.mark.asyncio
async def test_the_versions_route_answers_the_node_s_list_newest_first(config_dir, fleet):
    cluster = fleet.declare(NODE).cluster()
    node = cluster.nodes[NODE]
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await _versions(http)
    assert reply.status_code == 200, reply.text
    assert reply.json() == {"versions": ["2.9.9.9", "2.9.0.1", "2.9.0.0"]}
    assert node.version_lists == ["release"], "the route did not ask the node about that branch"


@pytest.mark.asyncio
async def test_the_versions_route_refuses_a_free_text_branch_and_never_asks_the_node(config_dir,
                                                                                    fleet):
    cluster = fleet.declare(NODE).cluster()
    node = cluster.nodes[NODE]
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await _versions(http, branch="../../etc")
    assert reply.status_code == 400
    body = reply.json()
    assert body["versions"] == []
    assert body["reason"] == "'../../etc' is not a branch this node offers."
    assert node.version_lists == [], "a free-text branch was forwarded to the node"


@pytest.mark.asyncio
async def test_the_versions_route_refuses_an_unknown_node_and_one_outside_the_scope(config_dir,
                                                                                   fleet):
    """Two causes, two sentences, and neither is a 500."""
    cluster = fleet.declare(NODE).cluster()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        unknown = await _versions(http, node_name="no-such-node")
        assert unknown.status_code == 404
        assert unknown.json() == {"versions": [],
                                  "reason": ("Node 'no-such-node' is offline or unknown, so its "
                                             "versions cannot be read.")}
        # a caller whose view does not carry the name gets the console's own out-of-scope answer —
        # the SAME answer a name that does not exist gets
        permissions.set_scope_resolver(lambda request: Scope.restricted({"Host A"}))
        outside = await _versions(http, node_name="somebody-elses-node")
    assert outside.status_code == 403
    assert outside.json() == {"versions": [],
                              "reason": "Not authorized (this target is outside your scope)."}


@pytest.mark.asyncio
async def test_the_versions_route_never_invents_a_list(config_dir, fleet):
    """Every way of not answering is an EMPTY list with its own sentence."""
    cases = (
        (DcsNode(NODE, master=True, versions=None),
         "answered with no version for branch 'release'"),
        (DcsNode(NODE, master=True, versions_error=RuntimeError("rpc dropped")),
         "could not answer with its version list (RuntimeError)"),
    )
    for node, fragment in cases:
        cluster = fleet.declare(NODE).cluster(node=node)
        app = build(config_dir, cluster, plugins=("mission", "admin"))
        async with client(app) as http:
            await sign_in(http, "specialk")
            reply = await _versions(http)
        assert reply.status_code == 200, fragment
        body = reply.json()
        assert body["versions"] == [], fragment
        assert fragment in body["reason"], body
    # a node that declares NO DCS says so, with its own sentence
    cluster = fleet.declare(OTHER).cluster()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await _versions(http)
    assert reply.status_code == 200
    assert reply.json() == {"versions": [],
                            "reason": (f"Node '{NODE}' declares no DCS installation, so it has no "
                                       f"versions to offer.")}


@pytest.mark.asyncio
async def test_the_node_name_travels_as_one_percent_encoded_segment(config_dir, fleet):
    """A name carrying ``/`` and a space is ONE path segment, and the route resolves it back."""
    odd = "weird/name x"
    assert versions_url(odd) == "/nodes/weird%2Fname%20x/dcs/versions"
    cluster = fleet.declare(odd).cluster(node=DcsNode(odd, master=True))
    node = cluster.nodes[odd]
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(versions_url(odd) + "?branch=release")
    assert reply.status_code == 200, reply.text
    assert reply.json() == {"versions": ["2.9.9.9", "2.9.0.1", "2.9.0.0"]}
    assert node.version_lists == ["release"], "the decoded name did not reach the node"


# ------------------------------------------------------- 6. the dialog's read, against a fake DOM

HARNESS = Path(__file__).resolve().parent / "dcs_versions_harness.js"
requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: no DOM to drive the script with")


def _inline_scripts(markup: str) -> list[str]:
    """The inline ``<script>`` blocks of a rendered page, in document order (``src`` ones skipped)."""
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", markup, re.S | re.I)


def _rendered_entries(markup: str, field: str) -> list[dict]:
    """The ``(value, label)`` pairs the SERVER drew in *field*'s pick."""
    block = re.search(rf'<select id="{field}-dialog".*?</select>', markup, re.S)
    assert block, f"the dialog drew no {field} pick"
    return [{"value": value, "label": label}
            for value, label in re.findall(r'<option value="([^"]*)">([^<]*)</option>', block.group(0))]


async def _drive(http, tmp_path, answer: dict, markup: str) -> dict:
    """Run the dialog's own script against the fake DOM, answering the read with *answer*."""
    scripts = [source for source in _inline_scripts(markup) if "installed_branch" in source]
    assert scripts, "the rendered dialog carries no version-pick script"
    script = tmp_path / "dcs_versions_inline.js"
    script.write_text(scripts[-1], encoding="utf-8")
    answer_file = tmp_path / "answer.json"
    answer_file.write_text(json.dumps(answer), encoding="utf-8")
    initial_file = tmp_path / "initial.json"
    initial_file.write_text(json.dumps(_rendered_entries(markup, DCS_VERSION_FIELD)), encoding="utf-8")
    done = subprocess.run(["node", str(HARNESS), str(script), str(answer_file), str(initial_file)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.asyncio
@requires_node
async def test_the_version_pick_reads_the_route_on_open_and_again_on_a_branch_change(config_dir,
                                                                                    fleet,
                                                                                    tmp_path):
    """The pick is filled from the NODE: on open for the installed branch, on a change for the new one.

    It draws the newest build first, keeps the installed one marked, and offers the OLDER builds
    below it — which is what makes a rollback reachable from this dialog.
    """
    from services.webservice import dcs_update
    dcs_update.record(NODE, has_dcs=True, branch="release", version="2.9.0.1", latest="2.9.9.9",
                      available=True)
    cluster = fleet.declare(NODE).cluster()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        _token, dialog = await _dialog_for(http)
    seen = await _drive(http, tmp_path, {"versions": ["2.9.9.9", "2.9.0.1", "2.9.0.0"]}, dialog.text)
    assert seen["onOpen"]["asked"] == [versions_url(NODE) + "?branch=release"]
    labels = [option["label"] for option in seen["onOpen"]["options"]]
    assert labels == ["Newest build on the branch", "2.9.9.9 — newest on this branch",
                      "2.9.0.1 — installed now", "2.9.0.0"], labels
    assert seen["onOpen"]["value"] == "", "the pick must default to the newest build"
    # A BRANCH CHANGE is the same read, for the chosen branch — and the installed marker goes, since
    # that version is not what the new branch has installed
    assert seen["onBranchChange"]["asked"] == [versions_url(NODE) + "?branch=release",
                                              versions_url(NODE) + "?branch=testing"]
    assert [option["label"] for option in seen["onBranchChange"]["options"]][1:] == [
        "2.9.9.9 — newest on this branch", "2.9.0.1", "2.9.0.0"]


@pytest.mark.asyncio
@requires_node
async def test_a_read_that_cannot_answer_states_the_reason_and_keeps_the_fallback(config_dir, fleet,
                                                                                 tmp_path):
    """A route that answers no list puts its REASON first — and the server-drawn entries stay."""
    from services.webservice import dcs_update
    # the pick asks about a branch it KNOWS: the cached check's installed one (a cold cache offers
    # the fallback entries and asks nothing, since there is no branch to ask about)
    dcs_update.record(NODE, has_dcs=True, branch="release", version="2.9.0.1", latest="2.9.9.9",
                      available=True)
    cluster = fleet.declare(NODE).cluster()
    app = build(config_dir, cluster, plugins=("mission", "admin"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        _token, dialog = await _dialog_for(http)
    drawn = _rendered_entries(dialog.text, DCS_VERSION_FIELD)
    seen = await _drive(http, tmp_path, {"versions": [], "reason": "ED is unreachable"}, dialog.text)
    options = seen["onOpen"]["options"]
    assert options[0]["disabled"] is True and options[0]["selected"] is True, options[0]
    assert options[0]["label"] == "The version list could not be read: ED is unreachable"
    assert [option["value"] for option in options[1:]] == [entry["value"] for entry in drawn]


# ---------------------------------------------------------------------- 7. the declaration table

def test_the_branch_allow_list_is_the_discord_rule(fleet):
    """``/dcs update``'s own branch rule, computed with NO node call.

    Three branches plus the ``dcs_server.`` prefix for an installation that names its branches that
    way, and Testing/Nightly ONLY for a node configured with DCS credentials — the rule the ACTION
    then receives a value from, since the route refuses anything outside this list.
    """
    from services.webservice import dcs_update

    branches = actions_page.dcs_branch_values
    fleet.declare(NODE)                        # declared, with credentials
    assert branches(NODE) == ("release", "testing", "nightly")
    fleet.blocks[NODE] = {"installation": "C:/DCS World"}   # declared, NO credentials
    assert branches(NODE) == ("release",)
    dcs_update.record(NODE, has_dcs=True, branch="dcs_server.release", version="2.9.0.1")
    assert branches(NODE) == ("dcs_server.release",)
    fleet.blocks[NODE] = {"installation": "C:/DCS World", "user": "pilot"}
    assert branches(NODE) == ("dcs_server.release", "dcs_server.testing", "dcs_server.nightly")
    # a node that declares nothing has no credentials to read either — the plain default, which the
    # action resolves to the INSTALLED branch when the picker states none (and which the row never
    # offers anyway: ``needs_dcs`` withholds the control there)
    assert branches("no-such-node") == ("release",)


def test_the_capability_and_the_route_tables_carry_the_new_surface():
    """The declaration and the route table are read from the ONE map, so both must name both paths."""
    declared = actions_page.capabilities()
    assert declared[DCS_PATH] == DCS_CAPABILITY
    assert declared[DCS_PATH + CONFIRM] == DCS_CAPABILITY
    assert declared[DCS_VERSIONS_PATH] == DCS_CAPABILITY
    # Admin only, and NO scope grant: a manager's scope must never widen into a whole node
    assert permissions.roles_for_capability(DCS_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(DCS_CAPABILITY) is False
    action = next(item for item in actions_page.NODE_ACTIONS if item.key == "dcs_update")
    assert action.qualname == "update_dcs"
    assert action.needs_dcs is True
    assert action.states == () and action.check is False, "the control must not be gated"
    assert action.confirm is True
