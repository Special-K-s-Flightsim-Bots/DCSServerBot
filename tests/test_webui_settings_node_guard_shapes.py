"""THE NODE GUARD's NO-OP RE-PROOF across the scoped file shapes.

The card that closed the form door's allow-list (BLOCKER 3) required the no-op BYTE-IDENTITY to be
re-proven for every shape a scoped file takes — a plugin, a service, the node page over ``nodes.yaml``
and a single-node file (a scoped file whose view is the WHOLE file, no node selector). Posting back,
unchanged, exactly the content the editor was seeded with must leave the file byte-for-byte as it was —
the guard's allow-list must never turn a genuine no-op into a rewrite.

The extension shape that used to be here moved with the feature: extensions are no longer a Settings
kind, and the ``nodes.yaml`` Expert door is now the NODE PAGE (``/nodes/<node>``) — so THAT route is
the nodes.yaml shape proved here.

Pinned over the REAL app here:
* the plugin and service shapes (each with a node SELECTOR) post their own slice back and the file is
  byte-identical;
* the node page over ``nodes.yaml`` posts the selected node's slice back and the file is byte-identical;
* the single-node shape (no selector) posts the WHOLE file back and it is byte-identical.
"""
from __future__ import annotations

import html
import re
import types

import pytest

from conftest import asgi_client
from services.webservice.pages import node_settings as node_settings_page
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import _build, _hash, _login

BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

#: Two nodes, each with an instance — enough for a service/plugin file to carry TWO node blocks (and so
#: offer the node selector), plus a RealWeather extension so the extension view reaches its own scope.
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
  extensions:
    RealWeather:
      installation: /opt/realweather-b
      autoupdate: false
"""

SERVERS_YAML = """\
SRS-1:
  node: dcs-a-01
"""

#: A plugin file with a DEFAULT block AND TWO node blocks — the multi-node shape (node selector).
MISSION_YAML = """\
DEFAULT:
  messages:
    ban_username: "banned"
    ban_evasion: "evade"
  event_filter:
    - connect
dcs-a-01:
  Syria:
    messages:
      ban_username: "syria banned"
dcs-b-01:
  Nevada:
    messages:
      ban_username: "nevada banned"
"""

#: A service file with a DEFAULT block AND TWO node blocks — the multi-node shape (node selector).
MUSIC_YAML = """\
DEFAULT:
  music_dir: /etc
  pause_without_players: true
dcs-a-01:
  Syria:
    music_dir: /etc
dcs-b-01:
  Nevada:
    music_dir: /etc
"""

#: A service file with a DEFAULT block and ONE node block — the SINGLE-NODE reading: the whole file is
#: the operator's unit (no node selector, the Expert panel shows every key).
MUSIC_YAML_SINGLE = """\
DEFAULT:
  music_dir: /etc
  pause_without_players: true
dcs-a-01:
  Syria:
    music_dir: /etc
"""


def _write_base(tmp):
    (tmp / "services").mkdir()
    (tmp / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (tmp / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")


@pytest.fixture
def multi_dir(tmp_path):
    """A config dir whose plugin/service files carry TWO node blocks (node selector)."""
    _write_base(tmp_path)
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "mission.yaml").write_text(MISSION_YAML, encoding="utf-8")
    (tmp_path / "services" / "music.yaml").write_text(MUSIC_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def single_dir(tmp_path):
    """A config dir whose service file carries ONE node block (single-node reading)."""
    _write_base(tmp_path)
    (tmp_path / "services" / "music.yaml").write_text(MUSIC_YAML_SINGLE, encoding="utf-8")
    return tmp_path


def _bind_validator(config_dir, monkeypatch):
    import core.commandline as commandline
    from core.utils import validators

    saved_argv = getattr(commandline, "COMMAND_LINE_ARGS", None)
    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(config_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", saved_argv, raising=False)


@pytest.fixture
def node_data(multi_dir, monkeypatch):
    yield from _bind_validator(multi_dir, monkeypatch)


def _app(config_dir):
    app = _build(config_dir, users=[{"username": "staff", "password_hash": _hash(),
                                     "roles": ["Admin"]}])[0]
    app.state.webui_source_provider = _fleet_source
    return app


def _fleet_source():
    """A scoped source that knows both nodes, so the node page can resolve them."""
    from test_webui_dashboard import _source, node

    nodes = {"dcs-a-01": node(name="dcs-a-01", master=True),
             "dcs-b-01": node(name="dcs-b-01", master=False)}
    return _source(nodes=nodes, node_obj=nodes["dcs-a-01"])


def _csrf(page: str) -> str:
    return re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)


def _revision(page: str) -> str:
    return re.search(r'name="_revision" value="([^"]*)"', page).group(1)


def _panel(page: str) -> str:
    found = re.search(r'<pre class="st-yaml"[^>]*>(.*?)</pre>', page, re.DOTALL)
    assert found is not None, "the YAML panel was not rendered"
    return html.unescape(found.group(1))


async def _repost(client, page_url, save_url, node):
    page = await client.get(page_url)
    assert page.status_code == 200, (page_url, page.status_code, page.text[:300])
    text = page.text
    reply = await client.post(
        save_url, data={"_csrf_token": _csrf(text), "_revision": _revision(text),
                        "_expert": "1", "_yaml": _panel(text)})
    assert reply.status_code == 303, reply.text


@pytest.mark.asyncio
async def test_no_op_byte_identity_across_the_scoped_file_shapes(multi_dir, node_data):
    """Plugin, service AND the node page over nodes.yaml — an unchanged slice save is byte-identical."""
    app = _app(multi_dir)
    plugin_path = multi_dir / "plugins" / "mission.yaml"
    service_path = multi_dir / "services" / "music.yaml"
    nodes_path = multi_dir / "nodes.yaml"

    before_plugin = plugin_path.read_bytes()
    before_service = service_path.read_bytes()
    before_nodes = nodes_path.read_bytes()

    async with asgi_client(app) as client:
        await _login(client, "staff")
 # 1. plugin (multi-node: node selector)
        await _repost(client, "/settings/plugin/mission?node=dcs-a-01",
                      "/actions/settings/config/plugin/mission?node=dcs-a-01", "dcs-a-01")
 # 2. service (multi-node: node selector)
        await _repost(client, "/settings/service/music?node=dcs-a-01",
                      "/actions/settings/config/service/music?node=dcs-a-01", "dcs-a-01")
 # 3. the NODE page over nodes.yaml (the extension door's new home)
        await _repost(client, node_settings_page.node_page_url("dcs-a-01"),
                      node_settings_page.node_page_url("dcs-a-01"), "dcs-a-01")

    assert plugin_path.read_bytes() == before_plugin, "the plugin no-op rewrote the file"
    assert service_path.read_bytes() == before_service, "the service no-op rewrote the file"
    assert nodes_path.read_bytes() == before_nodes, "the node-page no-op rewrote nodes.yaml"


@pytest.mark.asyncio
async def test_no_op_byte_identity_for_a_single_node_file(single_dir, monkeypatch):
    """The single-node shape: no node selector, the Expert panel is the WHOLE file; posting it back
    unchanged leaves the file byte-for-byte as it was."""
    list(_bind_validator(single_dir, monkeypatch))
    service_path = single_dir / "services" / "music.yaml"
    before = service_path.read_bytes()
    app = _app(single_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get("/settings/service/music")
        assert page.status_code == 200, page.text[:300]
 # no node selector is offered for a single-node reading
        assert 'class="st-nodes"' not in page.text, "a single-node file offered a node selector"
        reply = await client.post(
            "/actions/settings/config/service/music",
            data={"_csrf_token": _csrf(page.text), "_revision": _revision(page.text),
                  "_expert": "1", "_yaml": _panel(page.text)})
    assert reply.status_code == 303, reply.text
    assert service_path.read_bytes() == before, "the single-node no-op rewrote the file"