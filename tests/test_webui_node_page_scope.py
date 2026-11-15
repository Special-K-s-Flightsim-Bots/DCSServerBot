"""THE NODE PAGE's SAVE SCOPE — the active tab is CARRIED, never inferred, and the save is scoped.

The review proved four blockers on the node page's save, all one root cause: the save's
scope was the WHOLE NODE (``node_tree``'s ``instances`` element is the schema's regex wildcard, so
``configgen.find_field`` placed ANY instance token) and the POST carried no tab, so the server could
not tell which scope a save came from. Pinned here over the REAL app:

* B1 — a POST from one instance tab may not rewrite a SIBLING instance (byte-identical, refused);
* B2 — a save may not CREATE an extension block the scope does not configure (byte-identical, refused);
* B3 — cross-tab fields apply in NEITHER direction (a DEFAULT field from an instance tab, an instance
  field from the DEFAULT tab — both refused);
* B4 — a node with NO block in ``nodes.yaml`` refuses its save instead of creating the block.

The scope is carried on the form (a hidden ``_scope``) and — for a bookmark or a stale form — read
from the ``?tab=`` index on the POST URL. A field naming anything outside the selected scope is
REFUSED (nothing written, the file byte-identical), never silently dropped.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice.pages import node_settings as node_settings_page
from test_webui_dashboard import _build, _hash, _login
from test_webui_settings_node_scope import _block, _csrf, _revision

BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

#: ``dcs-a-01`` declares THREE instances (Syria configures Trackfile, Caucasus and PersianGulf do not);
#: ``dcs-b-01`` ONE. Enough to tell a scope's own fields from a sibling's.
NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  extensions:
    RealWeather:
      installation: /opt/rw-a
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
      extensions:
        Trackfile:
          enabled: true
          target: /opt/track
    Caucasus:
      home: /home/a-caucasus
      bot_port: 6667
      webgui_port: 8087
      dcs_port: 10307
    PersianGulf:
      home: /home/a-pg
      bot_port: 6668
      webgui_port: 8088
      dcs_port: 10308
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  instances:
    Nevada:
      home: /home/b-nevada
      bot_port: 6669
      webgui_port: 8089
      dcs_port: 10309
"""

SERVERS_YAML = "SRS-1:\n  node: dcs-a-01\n"


@pytest.fixture
def scope_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def node_data(scope_dir, monkeypatch):
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
    from test_webui_dashboard import _source, node

    nodes = {"dcs-a-01": node(name="dcs-a-01", master=True),
             "dcs-b-01": node(name="dcs-b-01", master=False)}
    return _source(nodes=nodes, node_obj=nodes["dcs-a-01"])


def _url(node: str, tab: int | None = None) -> str:
    url = node_settings_page.node_page_url(node)
    return url if tab is None else f"{url}?tab={tab}"


async def _get(client, path):
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    return response.text


def _scope_value(page: str) -> str:
    return re.search(r'name="_scope" value="([^"]*)"', page).group(1)


async def _post(client, node, data, tab=None):
    """POST to the node page's save URL, carrying the CSRF/revision of the rendered tab."""
    url = _url(node, tab)
    page = await _get(client, url)
    body = {"_csrf_token": _csrf(page), "_revision": _revision(page), **data}
    return await client.post(url, data=body)


def _notice(page: str) -> str:
    found = re.search(r'notice-msg">([^<]*)<', page)
    assert found is not None, "no notice was rendered"
    import html as _html

    return _html.unescape(found.group(1))


# ============================================================ the scope CARRIER on the form

@pytest.mark.asyncio
async def test_the_form_carries_the_active_tabs_scope(scope_dir, node_data):
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        default = await _get(client, _url("dcs-a-01"))
        syria = await _get(client, _url("dcs-a-01", 1))
    assert _scope_value(default) == "default"
    assert _scope_value(syria) == "instance:Syria"


# ============================================================ B3 — cross-tab, both directions

@pytest.mark.asyncio
async def test_a_default_field_from_an_instance_tab_is_refused(scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-a-01", {"_scope": "instance:Syria",
                                                 "f.dcs-a-01.autoupdate": "true"}, tab=1)
        landed = await _get(client, _url("dcs-a-01", 1))
    assert reply.status_code == 303
    assert path.read_bytes() == before, "a DEFAULT field from an instance tab wrote the file"
    assert _notice(landed) == node_settings_page.SAVE_REFUSED_FOREIGN_SCOPE


@pytest.mark.asyncio
async def test_an_instance_field_from_the_default_tab_is_refused(scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-a-01",
                            {"_scope": "default",
                             "f.dcs-a-01.instances.Syria.bot_port": "7000"})
        landed = await _get(client, _url("dcs-a-01"))
    assert reply.status_code == 303
    assert path.read_bytes() == before, "an instance field from the DEFAULT tab wrote the file"
    assert _notice(landed) == node_settings_page.SAVE_REFUSED_FOREIGN_SCOPE


# ============================================================ B1 — a sibling instance

@pytest.mark.asyncio
async def test_a_sibling_instances_field_from_another_instance_tab_is_refused(scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-a-01",
                            {"_scope": "instance:Syria",
                             "f.dcs-a-01.instances.Caucasus.bot_port": "1"}, tab=1)
        landed = await _get(client, _url("dcs-a-01", 1))
    assert reply.status_code == 303
    assert path.read_bytes() == before, "a sibling instance's field wrote the file"
    assert _notice(landed) == node_settings_page.SAVE_REFUSED_FOREIGN_SCOPE


@pytest.mark.asyncio
async def test_a_foreign_node_field_is_refused(scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-a-01",
                            {"_scope": "default", "f.dcs-b-01.listen_address": "9.9.9.9"})
        landed = await _get(client, _url("dcs-a-01"))
    assert reply.status_code == 303
    assert path.read_bytes() == before, "a foreign node's field wrote the file"
    assert _notice(landed) == node_settings_page.SAVE_REFUSED_FOREIGN_SCOPE


# ============================================================ B2 — no implicit extension block

@pytest.mark.asyncio
async def test_an_extension_the_scope_does_not_configure_cannot_be_created(scope_dir, node_data):
    """Caucasus's tab posting a fully VALID Trackfile block (which Caucasus never configures) must be
    REFUSED — the field is placed no longer, so pykwalify is not what stops it; the scope does."""
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-a-01",
                            {"_scope": "instance:Caucasus",
                             "f.dcs-a-01.instances.Caucasus.extensions.Trackfile.enabled": "true",
                             "f.dcs-a-01.instances.Caucasus.extensions.Trackfile.target": "/tmp/x"},
                            tab=2)
        landed = await _get(client, _url("dcs-a-01", 2))
    assert reply.status_code == 303
    assert path.read_bytes() == before, "an extension block the scope does not configure was created"
    assert _notice(landed) == node_settings_page.SAVE_REFUSED_FOREIGN_SCOPE


@pytest.mark.asyncio
async def test_an_instance_only_extension_at_node_scope_is_refused(scope_dir, node_data):
    """Trackfile is an INSTANCE extension — the node (DEFAULT) scope's schema does not declare it."""
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-a-01",
                            {"_scope": "default",
                             "f.dcs-a-01.extensions.Trackfile.enabled": "true"})
        landed = await _get(client, _url("dcs-a-01"))
    assert reply.status_code == 303
    assert path.read_bytes() == before, "an instance-only extension was written at node scope"
    assert _notice(landed) == node_settings_page.SAVE_REFUSED_FOREIGN_SCOPE


# ============================================================ a LEGITIMATE save writes its own scope

@pytest.mark.asyncio
async def test_a_default_scoped_save_writes_the_nodes_own_setting(scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-a-01",
                            {"_scope": "default", "f.dcs-a-01.listen_port": "10099"})
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "listen_port: 10099" in _block(after, "dcs-a-01")
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")


@pytest.mark.asyncio
async def test_an_instance_scoped_save_writes_that_instances_field(scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-a-01",
                            {"_scope": "instance:Syria",
                             "f.dcs-a-01.instances.Syria.bot_port": "7000"}, tab=1)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "bot_port: 7000" in _instance_block(after, "dcs-a-01", "Syria")


@pytest.mark.asyncio
async def test_a_configured_extension_can_be_edited(scope_dir, node_data):
    """The control: Syria CONFIGURES Trackfile, so editing it from Syria's tab lands."""
    path: Path = scope_dir / "nodes.yaml"
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-a-01",
                            {"_scope": "instance:Syria",
                             "f.dcs-a-01.instances.Syria.extensions.Trackfile.enabled": "0"}, tab=1)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "enabled: false" in _instance_block(after, "dcs-a-01", "Syria")


# ============================================================ the ?tab= fallback + a bogus tab

@pytest.mark.asyncio
async def test_the_tab_query_is_read_when_no_scope_carrier_is_posted(scope_dir, node_data):
    """A bookmark/stale form posts no ``_scope`` — the ``?tab=`` index still scopes the save."""
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
 # ?tab=1 is Syria; a DEFAULT field must be refused
        reply = await _post(client, "dcs-a-01", {"f.dcs-a-01.autoupdate": "true"}, tab=1)
        landed = await _get(client, _url("dcs-a-01", 1))
    assert reply.status_code == 303
    assert path.read_bytes() == before
    assert _notice(landed) == node_settings_page.SAVE_REFUSED_FOREIGN_SCOPE


@pytest.mark.asyncio
async def test_a_tab_beyond_the_instances_is_refused(scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-a-01",
                            {"f.dcs-a-01.listen_address": "9.9.9.9"}, tab=99)
        landed = await _get(client, _url("dcs-a-01"))
    assert reply.status_code == 303
    assert path.read_bytes() == before, "a bogus tab index wrote the file"
    assert _notice(landed) == node_settings_page.SAVE_REFUSED_UNKNOWN_SCOPE


# ============================================================ B4 — a node with NO block

@pytest.mark.asyncio
async def test_a_node_with_no_block_refuses_its_save_and_creates_nothing(scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
 # a node the caller can see but which has no block in nodes.yaml
    from test_webui_dashboard import _source, node

    nodes = {"dcs-a-01": node(name="dcs-a-01", master=True),
             "dcs-z-01": node(name="dcs-z-01", master=False)}
    app.state.webui_source_provider = lambda: _source(nodes=nodes, node_obj=nodes["dcs-a-01"])
    async with asgi_client(app) as client:
        await _login(client, "staff")
        reply = await _post(client, "dcs-z-01",
                            {"_scope": "default", "f.dcs-z-01.listen_address": "9.9.9.9"})
        landed = await _get(client, _url("dcs-z-01"))
    assert reply.status_code == 303
    assert path.read_bytes() == before, "a block-less node's save created the block"
    assert _notice(landed) == node_settings_page.SAVE_REFUSED_NO_NODE_BLOCK


# ============================================================ byte-identity for a no-op, all shapes

@pytest.mark.asyncio
async def test_a_no_op_scoped_save_is_byte_identical(scope_dir, node_data):
    path: Path = scope_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        for node, tab, scope in (("dcs-a-01", None, "default"),
                                 ("dcs-a-01", 1, "instance:Syria"),
                                 ("dcs-b-01", None, "default"),
                                 ("dcs-b-01", 1, "instance:Nevada")):
            reply = await _post(client, node, {"_scope": scope}, tab=tab)
            assert reply.status_code == 303
            assert path.read_bytes() == before, f"a no-op {node}/{scope} rewrote the file"


# ============================================================ the LOCKED section on the page

@pytest.mark.asyncio
async def test_an_unconfigured_extension_is_still_offered_as_an_off_card(scope_dir, node_data):
    """Every schema-known extension is still OFFERED. One the scope does not configure is a CHOICE of
    the ONE ``+ Add`` tile whose control turns it ON (the ``add`` action) — its configuration is opened
    from the node page once it is configured, never as a locked inline form."""
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        caucasus = await _get(client, _url("dcs-a-01", 2))
 # still offered: as a choice inside the "+ Add" tile (never a dimmed off card any more)
    assert '<article class="st-tile ext add">' in caucasus
    assert re.search(r'class="st-add-name"[^>]*>Trackfile<', caucasus)
    assert '<article class="st-tile ext off' not in caucasus
 # its control is the OFF (add) form — not a locked inline fieldset
    assert 'value="add"' in caucasus
    assert "<fieldset class=\"st-locked\" disabled>" not in caucasus


def _instance_block(text: str, node: str, instance: str) -> str:
    body = _block(text, node)
    found = re.search(rf"(?m)^[ ]{{4}}{re.escape(instance)}:\n(?:[ ]{{6,}}.*\n|\n)*", body)
    assert found is not None, f"no block for {node}.{instance}"
    return found.group(0)
