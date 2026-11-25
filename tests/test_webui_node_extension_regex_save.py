"""An extension block saves from its own page, including a ``regex;`` container's entries.

Over the REAL app (routes, schemas, the one write primitive):

* a CREATING save writes the block and switches it OFF, and leaves every other block alone;
* a REQUIRED section the operator drops is refused, and nothing is written;
* a container's ``__entries`` companion is read against the CONCRETE posted path, so a section
  the operator removed is DELETED and one that is still posted survives;
* a section name the schema does not offer is refused, with nothing written.

WHY THE CONTAINER IS ``frontend.mapLayers``. Olympus 2.x-only support dropped the
``regex;(backend|server)`` / ``regex;(frontend|client)`` spellings, so the block's own sections are
literal keys now. ``frontend.mapLayers`` is still a ``regex;(.+)`` container nested one level down,
and the save tree addresses every regex step with a wildcard — the shape that a container lookup
comparing an exact path can never match. Pinning it here keeps that regression covered on the
schema the project ships.
"""
from __future__ import annotations

import yaml
import pytest

from conftest import asgi_client
from services.webservice.pages import node_settings as node_settings_page
from test_webui_dashboard import _login
from test_webui_node_page import _app  # noqa: F401  (fixtures node_dir / node_data)
from test_webui_node_page import node_data, node_dir  # noqa: F401
from test_webui_settings_node_scope import _csrf, _revision

#: Syria carries no Olympus block, so its tab is the CREATE door.
BASE = "f.dcs-a-01.instances.Syria.extensions.Olympus"

OLYMPUS_NODES = """\
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
        Trackfile:
          enabled: true
          target: /opt/track
    Caucasus:
      home: /home/a-caucasus
      bot_port: 6667
      webgui_port: 8087
      dcs_port: 10307
"""


@pytest.fixture
def olympus_dir(node_dir):
    (node_dir / "nodes.yaml").write_text(OLYMPUS_NODES, encoding="utf-8")
    return node_dir


def _url() -> str:
    return f"{node_settings_page.node_extension_url('dcs-a-01', 'Olympus')}?tab=1"


def _written(node_dir):
    return yaml.safe_load((node_dir / "nodes.yaml").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_a_creating_save_writes_the_block_and_switches_it_off(olympus_dir, node_data):  # noqa: F811
    """Both sections plus a flag, posted from a scope with no block yet."""
    app = _app(olympus_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(_url())
        assert page.status_code == 200, page.text[:200]
        reply = await client.post(_url(), data={
            "_csrf_token": _csrf(page.text), "_revision": _revision(page.text),
            BASE + ".enabled": "0",
            BASE + ".backend.address": "localhost", BASE + ".backend.port": "4512",
            BASE + ".frontend.path": "C:/Olympus", BASE + ".frontend.port": "4513",
        })
    assert reply.status_code == 303, reply.text
    document = _written(olympus_dir)
    block = document["dcs-a-01"]["instances"]["Syria"]["extensions"]["Olympus"]
    assert block["backend"] == {"address": "localhost", "port": 4512}
    assert block["frontend"] == {"path": "C:/Olympus", "port": 4513}
    # A CREATED BLOCK IS SWITCHED OFF — an absent flag reads as ON in the runtime.
    assert block["enabled"] is False
    # every other block is untouched
    assert document["dcs-a-01"]["instances"]["Syria"]["extensions"]["Trackfile"] == \
        {"enabled": True, "target": "/opt/track"}


@pytest.mark.asyncio
async def test_a_dropped_REQUIRED_section_is_refused_and_nothing_is_written(olympus_dir, node_data):  # noqa: F811
    """Olympus's two sections are ``required``, so the page may not drop one — and the refusal has
    to arrive as a refusal, with the file untouched."""
    before = (olympus_dir / "nodes.yaml").read_bytes()
    app = _app(olympus_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(_url())
        reply = await client.post(_url(), data={
            "_csrf_token": _csrf(page.text), "_revision": _revision(page.text),
            BASE + ".backend.address": "localhost", BASE + ".backend.port": "4512",
        })
    assert reply.status_code == 303, reply.text
    assert (olympus_dir / "nodes.yaml").read_bytes() == before, "a refused save wrote the file"


def test_a_container_removal_uses_the_CONCRETE_path_not_the_tree_template():
    """The deletion pass reads the entries' parent by the posted base; the tree's own ``field.path``
    is the schema template (``*`` for node, instance and the container's own regex step) and
    addresses nothing in the document."""
    from services.webservice import configgen, scopes

    document = yaml.safe_load(OLYMPUS_NODES.replace(
        "        Trackfile:\n          enabled: true\n          target: /opt/track\n",
        "        Olympus:\n"
        "          frontend:\n"
        "            path: C:/Olympus\n"
        "            port: 4513\n"
        "            mapLayers:\n"
        "              Topo: {urlTemplate: 'https://tiles.example.com/{z}/{x}/{y}.png'}\n"
        "              Graticule: {urlTemplate: 'https://grid.example.com/{z}/{x}/{y}.png'}\n"))
    _scopes, tree = scopes.build_extension_scopes(document, "Olympus", ["dcs-a-01"], node="dcs-a-01")
    from starlette.datastructures import FormData

    layer = BASE + ".frontend.mapLayers"
    configgen.apply_form(document, FormData([
        (layer + ".__entries", "Topo\nGraticule"),      # the page rendered both
        (layer + ".Topo.urlTemplate", "https://tiles.example.com/{z}/{x}/{y}.png"),
        # and the form carries only Topo's leaf: Graticule was removed
    ]), tree)
    block = document["dcs-a-01"]["instances"]["Syria"]["extensions"]["Olympus"]
    assert "Topo" in block["frontend"]["mapLayers"]
    assert "Graticule" not in block["frontend"]["mapLayers"]


@pytest.mark.asyncio
async def test_a_section_name_outside_the_schema_is_refused(olympus_dir, node_data):  # noqa: F811
    """A forged section name writes nothing."""
    before = (olympus_dir / "nodes.yaml").read_bytes()
    app = _app(olympus_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(_url())
        reply = await client.post(_url(), data={
            "_csrf_token": _csrf(page.text), "_revision": _revision(page.text),
            BASE + ".sideways.path": "C:/Olympus", BASE + ".sideways.port": "4513",
        })
    assert reply.status_code == 303, reply.text
    assert (olympus_dir / "nodes.yaml").read_bytes() == before, "a forged section name was written"


def test_the_template_matcher_accepts_only_matching_shapes():
    from services.webservice.configgen import _path_template_matches

    assert _path_template_matches(("*", "instances", "*", "extensions", "Olympus"),
                                  ("dcs-a-01", "instances", "Syria", "extensions", "Olympus"))
    assert _path_template_matches(("*", "instances", "*", "extensions", "Olympus", "*", "mapLayers"),
                                  ("dcs-a-01", "instances", "Syria", "extensions", "Olympus",
                                   "frontend", "mapLayers"))
    assert not _path_template_matches(("*", "instances", "*", "extensions", "Olympus"),
                                      ("dcs-a-01", "instances", "Syria", "extensions", "SRS"))
    assert not _path_template_matches(("*", "instances"), ("dcs-a-01", "instances", "Syria"))
