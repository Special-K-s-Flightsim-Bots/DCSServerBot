"""THE NODE PAGE's node step — pick the NODE first, then see only its 1 + N tabs and a NODE SLICE in
the Expert Mode editor.

The rule: a scoped file (``nodes.yaml`` / a plugin / a service) is edited ONE NODE at a time. The
node page (``/nodes/<node>``) IS that mechanism for ``nodes.yaml``: its node selector shows only
that node, its tab strip is the node's own DEFAULT plus one tab per instance ("1 + N", never
``nodes x instances``), and the Expert Mode panel shows only that node's SLICE of the file, so the
whole ``nodes.yaml`` is never handed to an operator editing one node.

(The EXTENSION scoped view that used to be pinned here is gone — extensions left Settings; the node
page is now their home. The same selector/slice/save machinery is what this module pins, on the route
that survived.)

THE SLICE, DEFINED. ``nodes.yaml`` is a top-level mapping keyed by NODE. A node's slice is therefore
the sub-mapping holding that top-level key: ``{<node>: …}``. A save MERGES only that key back, so
every node the operator did not edit comes out byte-for-byte as it was. Pinned here over the REAL app:

* the node page offers the node choice and renders 1 + 2 tabs for the selected node;
* the expert editor's slice holds ONLY the selected node;
* switching node changes the slice;
* an expert slice save lands on that node and leaves every OTHER node's block byte-identical;
* the same guard refuses a slice that smuggles another node;
* the SAME mechanism is unchanged on the pure slice helpers (``document_slice`` / ``merge_slice``).
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client
from core.utils import configwrite
from services.webservice import scopes
from services.webservice.pages import node_settings as node_settings_page
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import _build, _hash, _login

BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

#: THREE nodes, each carrying the RealWeather node block AND TWO instance blocks — the 3-node /
#: 2-instance fixture the card names. Every node's own block is what the node selector partitions on.
NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
      extensions:
        RealWeather:
          enabled: true
    Caucasus:
      home: /home/a-caucasus
      bot_port: 6667
      webgui_port: 8087
      dcs_port: 10307
      extensions:
        RealWeather:
          enabled: true
  extensions:
    RealWeather:
      installation: /opt/realweather-a
      autoupdate: true
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  instances:
    Nevada:
      home: /home/b-nevada
      bot_port: 6668
      webgui_port: 8088
      dcs_port: 10308
      extensions:
        RealWeather:
          enabled: true
    PersianGulf:
      home: /home/b-persiangulf
      bot_port: 6669
      webgui_port: 8089
      dcs_port: 10309
      extensions:
        RealWeather:
          enabled: true
  extensions:
    RealWeather:
      installation: /opt/realweather-b
      autoupdate: false
dcs-c-01:
  listen_address: 127.0.0.1
  listen_port: 10044
  instances:
    SinaiMap:
      home: /home/c-sinai
      bot_port: 6670
      webgui_port: 8090
      dcs_port: 10310
      extensions:
        RealWeather:
          enabled: true
    Kola:
      home: /home/c-kola
      bot_port: 6671
      webgui_port: 8091
      dcs_port: 10311
      extensions:
        RealWeather:
          enabled: true
  extensions:
    RealWeather:
      installation: /opt/realweather-c
      autoupdate: true
"""

SERVERS_YAML = """\
SRS-1:
  node: dcs-a-01
"""


@pytest.fixture
def scope_dir(tmp_path):
    (tmp_path / "services").mkdir()
    #: newline="\n" keeps the fixture's bytes equal to the ``\n`` spelling the comparison encodes:
    #: text mode would translate them to CRLF on Windows and the bytes would differ from an
    #: untouched file's own
    for name, text in (("services/bot.yaml", BOT_YAML), ("nodes.yaml", NODES_YAML),
                       ("servers.yaml", SERVERS_YAML)):
        (tmp_path / name).write_text(text, encoding="utf-8", newline="\n")
    return tmp_path


@pytest.fixture
def node_data(scope_dir, monkeypatch):
    """Make the bot's own validator see THIS install's cluster (the same binding the sibling tests use)."""
    import core.commandline as commandline
    from core.utils import validators

    saved_argv = getattr(commandline, "COMMAND_LINE_ARGS", None)
    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(scope_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", saved_argv, raising=False)


def _app(scope_dir):
    app = _build(scope_dir, users=[{"username": "staff", "password_hash": _hash(),
                                    "roles": ["Admin"]}])[0]
    app.state.webui_source_provider = _fleet_source
    return app


def _fleet_source():
    """A scoped source that knows the three nodes, so the node page can resolve them."""
    from test_webui_dashboard import _source, node

    nodes = {name: node(name=name, master=(name == "dcs-a-01")) for name in
             ("dcs-a-01", "dcs-b-01", "dcs-c-01")}
    return _source(nodes=nodes, node_obj=nodes["dcs-a-01"])


async def _get(client, path):
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    return response.text


def _csrf(page: str) -> str:
    return re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)


def _revision(page: str) -> str:
    return re.search(r'name="_revision" value="([^"]*)"', page).group(1)


def _panel(page: str) -> str:
    """The Expert Mode panel's text — exactly the YAML the editor is seeded from."""
    import html

    found = re.search(r'<pre class="st-yaml"[^>]*>(.*?)</pre>', page, re.DOTALL)
    assert found is not None, "the YAML panel was not rendered"
    return html.unescape(found.group(1))


def _chips(page: str) -> list[tuple[str, bool]]:
    """The node selector's chips as ``(name, active)``, in render order."""
    nav = re.search(r'<nav class="st-nodes".*?</nav>', page, re.DOTALL)
    assert nav is not None, "the node selector was not rendered"
    body = nav.group(0)
    return [(re.sub(r"<[^>]+>", "", text).strip(), "on" in cls.split())
            for cls, text in re.findall(r'<a class="n([^"]*)"[^>]*>(.*?)</a>', body, re.DOTALL)]


def _tab_labels(page: str) -> list[str]:
    """The tab strip's labels (the text before the ``<span class="n">`` note)."""
    nav = re.search(r'<nav class="st-tabs".*?</nav>', page, re.DOTALL)
    assert nav is not None, "the tab strip was not rendered"
    body = nav.group(0)
    return [re.sub(r"<[^>]+>", "", whole.split('<span class="n">')[0]).strip() for whole in
            re.findall(r'<a class="t[^"]*"[^>]*>(.*?)</a>', body, re.DOTALL)]


def _block(text: str, node: str) -> str:
    """The raw text of one node's top-level block of a nodes.yaml (its ``node:`` line and indent)."""
    found = re.search(rf"(?m)^{re.escape(node)}:\n(?:[ \t].*\n|\n)*", text)
    assert found is not None, f"no block for {node}"
    return found.group(0)


def _page_url(node: str, tab: int | None = None) -> str:
    url = node_settings_page.node_page_url(node)
    return url if tab is None else f"{url}?tab={tab}"


# ============================================================= the node selector and the 1 + N tabs

@pytest.mark.asyncio
async def test_a_three_node_cluster_offers_the_node_choice(scope_dir, node_data):
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    assert [name for name, _ in _chips(page)] == ["dcs-a-01", "dcs-b-01", "dcs-c-01"], _chips(page)
    # the selector links carry the node, and the FIRST node is selected by default
    assert 'href="/nodes/dcs-a-01"' in page
    assert [active for _, active in _chips(page)] == [True, False, False]


@pytest.mark.asyncio
async def test_the_selected_node_renders_its_own_one_plus_two_tabs(scope_dir, node_data):
    """Choosing a node shows 1 + 2 = THREE tabs — its own block plus its TWO instances, never six."""
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-b-01"))
    assert _tab_labels(page) == ["Node-wide", "Instance Nevada", "Instance PersianGulf"], _tab_labels(page)
    assert [name for name, active in _chips(page) if active] == ["dcs-b-01"]
    # the OTHER nodes are not in the tab strip at all (no nodes x instances explosion)
    assert "dcs-a-01" not in _tab_labels(page) and "dcs-c-01" not in _tab_labels(page)
    # the active tab carries this node's own block's EXTENSION card, with its cogwheel into the config
    assert 'class="st-name">RealWeather<' in page
    assert "/nodes/dcs-b-01/extensions/RealWeather" in page


# ============================================================= the expert editor's SLICE

@pytest.mark.asyncio
async def test_the_expert_slice_holds_only_the_selected_node(scope_dir, node_data):
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    panel = _panel(page)
    assert "dcs-a-01:" in panel
    assert "dcs-b-01" not in panel and "dcs-c-01" not in panel, \
        "the expert editor was handed more than the selected node's slice"
    # the slice note is stated on the panel
    import html as _html

    assert settings_page.SCOPE_SLICE_NOTE in _html.unescape(page)


@pytest.mark.asyncio
async def test_switching_node_changes_the_slice(scope_dir, node_data):
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        a = _panel(await _get(client, _page_url("dcs-a-01")))
        c = _panel(await _get(client, _page_url("dcs-c-01")))
    assert a != c
    assert "dcs-a-01:" in a and "dcs-c-01" not in a
    assert "dcs-c-01:" in c and "dcs-a-01" not in c


# ============================================================= the expert slice SAVE

@pytest.mark.asyncio
async def test_an_expert_slice_save_lands_on_that_node_and_leaves_the_others_byte_identical(
        scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        edited = _panel(page).replace("/opt/realweather-a", "/opt/realweather-a-NEW")
        reply = await client.post(
            _page_url("dcs-a-01"),
            data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                  "_expert": "1", "_yaml": edited})
    assert reply.status_code == 303, reply.text

    after = path.read_text(encoding="utf-8")
    # the changed node is written...
    assert "/opt/realweather-a-NEW" in after
    # ...and EVERY other node's block is byte-for-byte what it was
    for other in ("dcs-b-01", "dcs-c-01"):
        assert _block(after, other) == _block(before, other), f"{other}'s block was rewritten"


@pytest.mark.asyncio
async def test_an_untouched_expert_slice_save_is_byte_identical(scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await client.post(
            _page_url("dcs-a-01"),
            data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                  "_expert": "1", "_yaml": _panel(page)})
        landed = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "an unchanged expert slice rewrote the file"
    assert settings_page.SAVED_NO_CHANGE in landed


@pytest.mark.asyncio
async def test_a_slice_save_that_smuggles_another_node_is_refused(scope_dir, node_data):
    """A slice may carry ONLY the selected node's keys: a document that also carries another node's
    block is REFUSED (nothing written) — never merged and never silently dropped while "Saved" shows.
    Either way the other node's block cannot be rewritten; the guard now says so out loud."""
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        smuggled = _panel(page) + "\ndcs-b-01:\n  listen_address: 1.2.3.4\n  listen_port: 1\n"
        reply = await client.post(
            _page_url("dcs-a-01"),
            data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                  "_expert": "1", "_yaml": smuggled})
        landed = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01"), \
        "a smuggled slice rewrote another node's block"
    assert path.read_bytes() == before.encode("utf-8"), "a smuggled slice wrote the file"
    import html as _html

    assert settings_page.SAVE_REFUSED_FOREIGN_NODE in _html.unescape(landed), \
        "the smuggled slice was not refused"


# ============================================================= the expert slice VALIDATE route

@pytest.mark.asyncio
async def test_the_validate_route_checks_the_slice_as_the_whole_file(scope_dir, node_data):
    """The live check accepts a VALID slice (one that is only valid once merged) and flags a fault.
    A bare slice would fail the schema (nodes.yaml needs its other nodes); the route merges first."""
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        csrf = _csrf(page)
        good = await client.post(
            node_settings_page._node_page_validate_url("dcs-a-01"),
            data={"_csrf_token": csrf, "_yaml": _panel(page)})
        # a slice whose own block carries a schema fault (listen_port is an int)
        broken = _panel(page).replace("listen_port: 10042", "listen_port: not-a-number")
        bad = await client.post(
            node_settings_page._node_page_validate_url("dcs-a-01"),
            data={"_csrf_token": csrf, "_yaml": broken})
    assert good.status_code == 200, good.text
    assert good.json()["ok"] is True, good.json()
    assert bad.status_code == 200 and bad.json()["ok"] is False, bad.json()


# ============================================================= the OLD per-node settings URL

@pytest.mark.asyncio
async def test_the_old_per_node_settings_url_redirects_to_the_node_page(scope_dir, node_data):
    """The retired per-node settings page is a REDIRECT now, never a render: a bookmark lands on the
    node page, and the node page is where the selector plus the slice live."""
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        old = await client.get(node_settings_page.NODE_SETTINGS_PATH.format(node="dcs-a-01"),
                               follow_redirects=False)
        page = await _get(client, _page_url("dcs-a-01"))
    assert old.status_code == 307, old.status_code
    assert old.headers["location"] == "/nodes/dcs-a-01"
    assert [name for name, _ in _chips(page)] == ["dcs-a-01", "dcs-b-01", "dcs-c-01"], _chips(page)
    assert [name for name, active in _chips(page) if active] == ["dcs-a-01"]
    # the panel is that node's SLICE of nodes.yaml — the node key present, the others absent
    panel = _panel(page)
    assert "dcs-a-01:" in panel
    assert "dcs-b-01" not in panel and "dcs-c-01" not in panel


# ============================================================= the mechanism itself (unit)

def test_the_slice_definition_and_merge_are_pure():
    """The slice is the top-level-key sub-mapping ``scope_keys`` names, and ``merge_slice`` writes
    back ONLY those keys — every other key identical, and an unchanged slice is a no-op."""
    document = configwrite.load_config_text(NODES_YAML)
    keys = ["dcs-a-01"]
    slice_ = scopes.document_slice(document, keys)
    assert list(slice_.keys()) == ["dcs-a-01"]
    assert "dcs-b-01" not in slice_ and "dcs-c-01" not in slice_

    edited = configwrite.load_config_text(configwrite.dump_config(slice_))
    edited["dcs-a-01"]["extensions"]["RealWeather"]["installation"] = "/opt/changed"
    scopes.merge_slice(document, scopes.document_slice(edited, keys))
    assert document["dcs-a-01"]["extensions"]["RealWeather"]["installation"] == "/opt/changed"
    assert document["dcs-b-01"]["extensions"]["RealWeather"]["installation"] == "/opt/realweather-b"
    assert document["dcs-c-01"]["extensions"]["RealWeather"]["installation"] == "/opt/realweather-c"
