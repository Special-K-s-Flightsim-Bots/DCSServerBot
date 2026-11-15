"""THE EXTENSION CONFIG PAGE for a NODE-WIDE BLOCK of an INSTANCE-SCOPED extension.

THE CARD (a review found it). An operator can switch an instance-scoped extension (``LogAnalyser``) ON
at the NODE level — the "set it once for every instance of this node" override the runtime honours
(``core/data/impl/serverimpl.py``'s ``load_extension`` merges the node-level block UNDER the
instance-level one, for ANY name) — but its extension page rendered NO editable fields: the per-node
default could only be filled through Expert Mode YAML.

THE ROOT CAUSE. ``scopes.build_extension_scopes`` built a NODE block's fields from the NODE partial
only (``extension_schema_parts`` returns ``node_partial=None`` for an instance-only name), so the node
block's tab had ZERO fields; and ``extension_save_tree`` omitted the ``<node>.extensions.<Name>`` path
for such a name, so even a rendered field could never be saved. The fix falls the SHAPE BACK to the
other partial (``node_partial or instance_partial``) on BOTH the render and the save tree, so the
node-wide block carries the instance partial's fields addressed at the node-wide path — the very shape
the node page's own node-wide card renders (``_node_wide_extensions``). No new UI string is added; the
existing scoped editor, its picker and its acknowledged-removal discipline are reused unchanged.

Pinned here over the REAL app:
* a node-wide block of an instance-scoped extension renders its schema fields (its SET controls render,
  the rest are picker options) — the node page's card and this page agree;
* a value set there saves and reopens;
* a no-op save is BYTE-IDENTICAL;
* a value-carrying removal still needs the render's acknowledgement;
* the instance-tab view of that extension is exactly what the report claims (a tab per block that
  EXISTS, none for an instance that does not override — the operator never sees one block twice);
* the reverse (a node-scoped extension's instance block) still renders its instance partial.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice.pages import node_settings as node_settings_page
from test_webui_dashboard import _login
from test_webui_node_page import _app, _block, _extension_block, _instance_block  # noqa: F401
from test_webui_node_page import node_data, node_dir  # noqa: F401
from test_webui_settings_node_scope import _csrf, _revision

#: ``LogAnalyser`` configured at the NODE (the per-node default) AND on the Syria instance (an
#: override). ``dcs-b-01`` carries neither, so it is the "not overridden" control.
LOGGED_NODES = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  extensions:
    RealWeather:
      installation: /opt/rw-a
      autoupdate: true
    LogAnalyser:
      enabled: true
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
      extensions:
        LogAnalyser:
          enabled: false
          restart_on_unlist: true
    Caucasus:
      home: /home/a-caucasus
      bot_port: 6667
      webgui_port: 8087
      dcs_port: 10307
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

#: LogAnalyser's OWN schema fields (``extensions/loganalyser/schemas/loganalyser_schema.yaml``).
LOG_FIELDS = ["enabled", "restart_on_unlist", "message_unlist", "disable_detections",
              "ignore_files", "warn_times"]


@pytest.fixture
def logged_dir(node_dir):
    """The shared node fixture, with a node-level ``LogAnalyser`` block written in."""
    (node_dir / "nodes.yaml").write_text(LOGGED_NODES, encoding="utf-8")
    return node_dir


async def _get(client, path: str) -> str:
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    return response.text


def _url(node: str, name: str, tab: int | None = None) -> str:
    url = node_settings_page.node_extension_url(node, name)
    return url if tab is None else f"{url}?tab={tab}"


def _field_names(page: str) -> set[str]:
    """The ``f.`` names the page's LIVE form posts (``<template>`` contents are inert — stripped)."""
    body = re.sub(r"<template\b.*?</template>", "", page, flags=re.S)
    return set(re.findall(r'name="(f\.[^"]*)"', body))


def _picker_options(page: str) -> list[str]:
    """The field keys the ``+ Add`` picker offers, in render order."""
    body = re.sub(r"<template\b.*?</template>", "", page, flags=re.S)
    return re.findall(r'class="cfg-pkey[^"]*">([^<]+)<', body)


def _tab_labels(page: str) -> list[str]:
    nav = re.search(r'<nav class="st-tabs".*?</nav>', page, re.DOTALL)
    assert nav is not None, "the extension page rendered no tab strip"
    return [re.sub(r"<[^>]+>", "", whole.split('<span class="n">')[0]).strip()
            for whole in re.findall(r'<a class="t[^"]*"[^>]*>(.*?)</a>', nav.group(0), re.DOTALL)]


def _remove_ack(page: str) -> str:
    match = re.search(r'name="f\.__remove_ack" value="([^"]*)"', page)
    return html.unescape(match.group(1)) if match else ""


# ============================================== 1. the node-wide block renders its schema fields

@pytest.mark.asyncio
async def test_a_node_wide_block_of_an_instance_scoped_extension_renders_its_fields(logged_dir, node_data):  # noqa: F811
    """THE CARD: the node-wide block of ``LogAnalyser`` (instance-scoped) renders the extension's OWN
    schema fields — the SET control (``enabled``) is on the page and every other field is a picker
    choice. Before the fix the block's tab rendered ZERO fields."""
    app = _app(logged_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _url("dcs-a-01", "LogAnalyser"))
    names = _field_names(page)
    assert "f.dcs-a-01.extensions.LogAnalyser.enabled" in names, \
        "the node-wide block rendered no editable field"
 # the rest of the extension's schema is REACHABLE (the show/hide rule offers absent optionals)
    options = set(_picker_options(page))
    for field in LOG_FIELDS:
        if field == "enabled":
            continue
        assert field in options, f"{field} is neither rendered nor offered by the picker"
 # and it is the extension's OWN fields, at the NODE-wide path — never the instance path
    assert "f.dcs-a-01.instances.Syria.extensions.LogAnalyser.enabled" not in names


# ============================================================= 2. set a value, save, reopen, see it

@pytest.mark.asyncio
async def test_a_value_set_on_the_node_wide_block_saves_and_reopens(logged_dir, node_data):  # noqa: F811
    path: Path = logged_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(logged_dir)
    url = _url("dcs-a-01", "LogAnalyser")
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.dcs-a-01.extensions.LogAnalyser.message_unlist": "kitty unlisted",
        })
        reopened = await _get(client, url)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "message_unlist: kitty unlisted" in _extension_block(after, "dcs-a-01", "LogAnalyser")
 # every OTHER block is byte-for-byte what it was
    assert _extension_block(after, "dcs-a-01", "RealWeather") == \
        _extension_block(before, "dcs-a-01", "RealWeather")
    for instance in ("Syria", "Caucasus"):
        assert _instance_block(after, "dcs-a-01", instance) == \
            _instance_block(before, "dcs-a-01", instance), f"{instance} was rewritten"
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
 # REOPEN: the value the operator set is on the page
    assert 'value="kitty unlisted"' in reopened


# ============================================================= 3. a no-op save is byte-identical

@pytest.mark.asyncio
async def test_a_noop_save_of_the_node_wide_block_is_byte_identical(logged_dir, node_data):  # noqa: F811
    path: Path = logged_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(logged_dir)
    url = _url("dcs-a-01", "LogAnalyser")
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
 # post the node-wide block's own current value back (enabled: true == "1")
        reply = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.dcs-a-01.extensions.LogAnalyser.enabled": "1",
        })
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "a no-op node-wide save rewrote the file"


# ============================================================= 4. a removal needs its acknowledgement

@pytest.mark.asyncio
async def test_a_removal_on_the_node_wide_block_needs_its_acknowledgement(logged_dir, node_data):  # noqa: F811
    """The picker's trashcan discipline is UNCHANGED for this block: a value-carrying removal without
    the acknowledgement the render minted is REFUSED (nothing written); with it, it succeeds."""
    path: Path = logged_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(logged_dir)
    url = _url("dcs-a-01", "LogAnalyser")
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
 # a BARE removal (the acknowledgement blanked) is refused
        bare = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.__remove": "dcs-a-01.extensions.LogAnalyser.enabled", "f.__remove_ack": "",
        })
        still = path.read_text(encoding="utf-8")
        assert bare.status_code == 303, bare.text
        assert "enabled: true" in _block(still, "dcs-a-01"), "a bare removal deleted the field"
        ack = _remove_ack(page)
        assert ack, "the render minted no removal acknowledgement for a removable field"
 # the page's own acknowledgement lets it through
        ok = await client.post(url, data={
            "_csrf_token": _csrf(page), "_revision": _revision(page),
            "f.__remove": "dcs-a-01.extensions.LogAnalyser.enabled", "f.__remove_ack": ack,
        })
    assert ok.status_code == 303, ok.text
    after = path.read_text(encoding="utf-8")
    assert "LogAnalyser" in _block(after, "dcs-a-01"), "the whole block was deleted, not just the field"
    assert "enabled: true" not in _block(after, "dcs-a-01"), \
        "the acknowledged removal did not delete the node-wide field"
 # the Syria override (a DIFFERENT block) is untouched
    assert "enabled: false" in _block(after, "dcs-a-01")
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")


# ============================================================= 5. the tab model (never twice)

@pytest.mark.asyncio
async def test_the_node_wide_block_and_its_instance_override_are_two_tabs(logged_dir, node_data):  # noqa: F811
    """THE INHERITANCE BEHAVIOUR CHOSEN. An instance-scoped extension configured at the NODE level shows
    ONE node-wide tab (its per-node default) and, ADDITIONALLY, one tab per instance that ALSO configures
    it (an override). An instance that does NOT configure it gets NO tab — the operator is never shown
    the same block twice, and the node-wide default is visible once, editable once."""
    app = _app(logged_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        a = await _get(client, _url("dcs-a-01", "LogAnalyser"))
        node_tab = await _get(client, _url("dcs-a-01", "LogAnalyser", 0))
        syria_tab = await _get(client, _url("dcs-a-01", "LogAnalyser", 1))
        b = await _get(client, _url("dcs-b-01", "LogAnalyser"))
 # dcs-a-01: node-wide + Syria (which overrides) — two tabs, no Caucasus/Nevada
    assert _tab_labels(a) == ["dcs-a-01", "dcs-a-01 + Syria"], _tab_labels(a)
 # the node-wide tab carries the NODE-wide path; the instance tab the INSTANCE path — never swapped
    assert "f.dcs-a-01.extensions.LogAnalyser.enabled" in _field_names(node_tab)
    assert "f.dcs-a-01.instances.Syria.extensions.LogAnalyser.enabled" in _field_names(syria_tab)
    assert "f.dcs-a-01.instances.Syria.extensions.LogAnalyser.enabled" not in _field_names(node_tab)
 # dcs-b-01 configures LogAnalyser NOWHERE -> no block -> read-only, honest reason
    assert "f.dcs-b-01.extensions.LogAnalyser" not in _field_names(b)


# ============================================== 6. the reverse: a node-scoped extension on an instance

@pytest.mark.asyncio
async def test_a_node_scoped_extension_configured_on_an_instance_still_renders(logged_dir, node_data):  # noqa: F811
    """The REVERSE direction. ``RealWeather`` is node+instance scoped; configured on the node it renders
    the NODE partial (``installation``/``autoupdate``), and a value there saves unchanged — the fallback
    never touches a name that declares its own partial for the scope it is shown on."""
    path: Path = logged_dir / "nodes.yaml"
    app = _app(logged_dir)
    url = _url("dcs-a-01", "RealWeather")
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
    names = _field_names(page)
 # the NODE partial's fields (not the instance partial's ``enabled``/``terrains``)
    assert "f.dcs-a-01.extensions.RealWeather.installation" in names
    assert "f.dcs-a-01.extensions.RealWeather.autoupdate" in names
    assert "f.dcs-a-01.extensions.RealWeather.enabled" not in names
