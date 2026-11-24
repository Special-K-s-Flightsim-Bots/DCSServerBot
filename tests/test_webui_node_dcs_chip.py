"""THE NODE ROW'S DCS CHIP: one label per state, rendered from the cache and from the declaration.

The row states, per node, the installed DCS build and whether an update waits — and tells a node
with NO DCS installation apart from one that is merely up to date. The chip is a LABEL beside the
node's name (the console's own ``.tag`` chip, amber ``.tag.a`` when an update exists), not a column
and not a control.

The four states are pinned one test each, through the REAL app (so the read model, the table
component and the per-request ``say`` interpolation are all exercised):

* ``DCS <version> @ <branch>`` — checked, nothing newer;
* ``DCS <version> → <latest>`` — an update is available, in the amber chip;
* ``no update check yet`` — the cache is unknown (``available is None``), and so is an OFFLINE node,
  whose record carries no version;
* ``no DCS on this node`` — the node declares no ``DCS:`` block.

Plus: a row with no DCS data keeps every pre-existing cell byte-identical apart from the chip, and
the version/branch/latest are operator data interpolated at render (never part of a msgid).
"""
from __future__ import annotations

import re
import types

import pytest

from conftest import create_app, install_shell

from services.webservice import dcs_update, readmodels
from services.webservice import upgrade as upgrade_signal
from services.webservice.pages import nodes as nodes_page
from services.webservice.readmodels import model
from test_webui_settings_cogwheels import (BOT_YAML, build, client, get, sign_in)

#: the nodes this module renders. Every one but ``dcs-absent`` declares a ``DCS:`` block; the
#: declared ``installation`` is never read (the console reads the CACHE, not the file system).
NODES_YAML = """\
dcs-current:
  listen_address: 127.0.0.1
  listen_port: 10042
  DCS:
    installation: /opt/dcs-current
dcs-update:
  listen_address: 127.0.0.1
  listen_port: 10043
  DCS:
    installation: /opt/dcs-update
dcs-unknown:
  listen_address: 127.0.0.1
  listen_port: 10044
  DCS:
    installation: /opt/dcs-unknown
dcs-offline:
  listen_address: 127.0.0.1
  listen_port: 10045
  DCS:
    installation: /opt/dcs-offline
dcs-absent:
  listen_address: 127.0.0.1
  listen_port: 10046
"""

#: the branch/version facts the cache holds for each checked node.
VERSION = "2.9.8.110"
LATEST = "2.9.9.200"
BRANCH = "release"

#: the declared DCS map, as the read model receives it (node name -> its ``nodes.yaml`` block).
DECLARED = {"dcs-current": {"installation": "/opt/dcs-current"},
            "dcs-update": {"installation": "/opt/dcs-update"},
            "dcs-unknown": {"installation": "/opt/dcs-unknown"},
            "dcs-offline": {"installation": "/opt/dcs-offline"}}

#: the row this module asserts on, and the chip inside it (the console's ``.tag`` chip family).
ROW = re.compile(r"<tr><td>.*?</tr>", re.S)
CHIP = re.compile(r' <span class="tag( a)?" data-dcs-chip>([^<]*)</span>')


class Fleet:
    """The cluster the console renders: four live nodes and one the registry cannot resolve."""

    def __init__(self):
        self.names = ("dcs-current", "dcs-update", "dcs-unknown", "dcs-offline", "dcs-absent")
        self.nodes: dict[str, object] = {
            name: types.SimpleNamespace(name=name, master=name == "dcs-current", instances={})
            for name in self.names if name != "dcs-offline"}
        self.nodes["dcs-offline"] = None
        self.node = self.nodes["dcs-current"]

    def source(self):
        return readmodels.LiveSource(nodes=self.nodes, node=self.node, log_path=None,
                                     declared_dcs=DECLARED)


@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """The schema validator reads ``COMMAND_LINE_ARGS.node`` (same fixture the sibling tests use)."""
    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


@pytest.fixture(autouse=True)
def _clean_cache():
    """The DCS cache is PROCESS state: every test starts from nothing checked."""
    dcs_update.reset()
    yield
    dcs_update.reset()


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    return tmp_path


def seed():
    """Stand in for the background poller: one cached answer per node, written before the render."""
    dcs_update.record("dcs-current", has_dcs=True, branch=BRANCH, version=VERSION, latest=VERSION,
                      available=False)
    dcs_update.record("dcs-update", has_dcs=True, branch=BRANCH, version=VERSION, latest=LATEST,
                      available=True)
    # dcs-unknown: nothing cached at all — never checked (or the last check failed)
    # dcs-absent: declared no DCS block; a cache entry must not make a version appear
    dcs_update.record("dcs-absent", has_dcs=True, branch=BRANCH, version=VERSION, latest=LATEST,
                      available=True)
    # dcs-offline: a STALE entry claims it is current — the row must still claim nothing, because
    # this render never asked that node (an unreachable node's row carries the declaration alone)
    dcs_update.record("dcs-offline", has_dcs=True, branch=BRANCH, version=VERSION, latest=VERSION,
                      available=False)


def rows(html: str) -> dict[str, str]:
    """``{node name: its <tr>}`` for every node row on the page."""
    found = {}
    for row in ROW.findall(html):
        name = re.search(r"<b>([^<]+)</b>", row)
        if name:
            found[name.group(1)] = row
    return found


def chip(html: str, name: str) -> tuple[str, str]:
    """``(chip text, chip class)`` for node *name* — the ONE chip in its own cell."""
    row = rows(html)[name]
    matches = CHIP.findall(row.split("</td>")[0])
    assert len(matches) == 1, f"expected exactly one DCS chip in {name}'s cell: {matches}"
    warn, text = matches[0]
    return text, ("tag a" if warn else "tag")


async def render(config_dir, language: str | None = None) -> str:
    app = build(config_dir, Fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        if language:
            http.cookies.set("dcssb_lang", language)
        return await get(http, nodes_page.NODES_PATH)


# ============================================================= state 1: checked, nothing newer

@pytest.mark.asyncio
async def test_a_current_node_states_its_version_and_branch(config_dir):
    seed()
    html = await render(config_dir)
    text, klass = chip(html, "dcs-current")
    assert text == f"DCS {VERSION} @ {BRANCH}", text
    assert klass == "tag", klass


# ============================================================= state 2: an update is available

@pytest.mark.asyncio
async def test_a_node_with_an_update_states_both_versions_in_the_amber_chip(config_dir):
    seed()
    html = await render(config_dir)
    text, klass = chip(html, "dcs-update")
    assert text == f"DCS {VERSION} → {LATEST}", text
    assert klass == "tag a", klass


# ============================================================= state 3: the cache is unknown

@pytest.mark.asyncio
async def test_an_unchecked_node_says_so_and_never_claims_up_to_date(config_dir):
    seed()
    html = await render(config_dir)
    text, klass = chip(html, "dcs-unknown")
    assert text == "no update check yet", text
    assert klass == "tag", klass
    assert "up to date" not in rows(html)["dcs-unknown"]
    assert VERSION not in rows(html)["dcs-unknown"]
    # the SAME wording the node's own upgrade note uses — one state, one sentence
    assert text == upgrade_signal.NOTE_NO_CHECK


# ============================================================= state 4: no DCS declared

@pytest.mark.asyncio
async def test_a_node_without_dcs_says_so_and_never_names_a_version(config_dir):
    seed()
    html = await render(config_dir)
    text, klass = chip(html, "dcs-absent")
    assert text == "no DCS on this node", text
    assert klass == "tag", klass
    # the declaration wins over a cache entry: no version, no claim
    assert VERSION not in rows(html)["dcs-absent"]
    assert LATEST not in rows(html)["dcs-absent"]


# ============================================================= the offline node

@pytest.mark.asyncio
async def test_an_offline_node_states_no_version_and_no_up_to_date_claim(config_dir):
    seed()
    html = await render(config_dir)
    text, klass = chip(html, "dcs-offline")
    assert text == "no update check yet", text
    assert klass == "tag", klass
    row = rows(html)["dcs-offline"]
    assert VERSION not in row and LATEST not in row
    assert "up to date" not in row
    # the existing OFFLINE tag carries the reason
    assert '<span class="tag">offline</span>' in row


# ============================================================= the unchanged row

@pytest.mark.asyncio
async def test_the_existing_row_content_is_unchanged_apart_from_the_chip(config_dir):
    seed()
    html = await render(config_dir)
    row = rows(html)["dcs-current"]
    stripped = CHIP.sub("", row)
    head, acts = stripped.split('<td class="acts">', 1)
    assert head == ('<tr><td><a class="srv-name" href="/nodes/dcs-current"><b>dcs-current</b></a>'
                    ' <span class="tag">master</span>'
                    '<div class="meta">No instances on this node.</div></td>'
                    '<td><span class="dot run" aria-hidden="true"></span> '
                    '<span class="tag">online</span></td>'
                    '<td class="num">0</td><td class="num">0</td><td class="num">0</td>'), head
    assert "/actions/node/restart/confirm" in acts
    assert "/actions/node/shutdown/confirm" in acts


# ============================================================= translated at render

@pytest.mark.asyncio
async def test_the_chip_is_translated_and_keeps_the_versions_verbatim(config_dir):
    """The msgid is the English template; the operator's versions are interpolated AFTER translation."""
    seed()
    text, _klass = chip(await render(config_dir, language="de"), "dcs-update")
    assert text == f"DCS {VERSION} → {LATEST} (Update verfügbar)", text


# ============================================================= the record itself

def test_the_chip_record_carries_placeholders_not_operator_data():
    """The msgids are stable English templates: the version is interpolated at render time."""
    for msgid in (model.DCS_CHIP_CURRENT, model.DCS_CHIP_UPDATE,
                  model.DCS_CHIP_UNCHECKED, model.DCS_CHIP_ABSENT):
        assert VERSION not in msgid and LATEST not in msgid and BRANCH not in msgid, msgid
    assert model.DCS_CHIP_UNCHECKED == upgrade_signal.NOTE_NO_CHECK
    assert model.dcs_chip(None)["msgid"] == model.DCS_CHIP_ABSENT
    # a cache answer with no version is UNKNOWN, never "up to date" with a blank figure
    assert model.dcs_chip(model.DcsView(available=False))["msgid"] == model.DCS_CHIP_UNCHECKED
    assert model.dcs_chip(model.DcsView(available=True, version=VERSION))["msgid"] == \
        model.DCS_CHIP_UNCHECKED
