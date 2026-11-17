"""An extension block that carries a ``regex;`` SECTION saves from its own page.

THE CARD. On the Olympus extension page (``/nodes/<node>/extensions/Olympus?tab=<instance>``) an
operator added values and pressed Save; the block was never written and the page said nothing useful.
Olympus's schema declares ``regex;(backend|server)`` and ``regex;(frontend|client)`` — two containers
under ONE parent — so the page posts their ``__entries`` / ``__key`` companions against the PARENT
path.

THE ROOT CAUSE. The extension save tree is built from a SCHEMA, so its node and instance steps stay
VARIABLE (``('*', 'instances', '*', 'extensions', 'Olympus')``) while the form posts the concrete
names it rendered. ``_custom_entry_context`` looked a container up by ``field.path == base`` — an exact
tuple comparison that can never match a wildcard template — and REFUSED the whole save as
"a field this page cannot place". Every extension carrying a ``regex;`` key was therefore un-saveable,
create and edit alike.

WHAT IS PINNED HERE, over the REAL app:
* a CREATING save writes the block (its sections, their leaves) and leaves every other block alone;
* a section the operator removes is DELETED — the rendered-entries companion is what says so, and it
  must resolve against the CONCRETE path, not the template;
* a NEW section name outside the schema's own list is still REFUSED, with nothing written.
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

#: Syria carries no Olympus block, so its tab is the CREATE door the report came from.
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
async def test_a_creating_save_with_typed_sections_writes_the_block(olympus_dir, node_data):  # noqa: F811
    """The report's own save: two sections plus a flag, posted from a scope with no block yet."""
    app = _app(olympus_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(_url())
        assert page.status_code == 200, page.text[:200]
        reply = await client.post(_url(), data={
            "_csrf_token": _csrf(page.text), "_revision": _revision(page.text),
            BASE + ".enabled": "0",
            BASE + ".__entries": "", BASE + ".__key": "backend",
            BASE + ".backend.address": "localhost", BASE + ".backend.port": "4512",
            BASE + ".__key": "frontend",
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
    """Olympus's two sections are ``required``, so the page may not drop one — and the refusal has to
    arrive as a refusal, not as the unplaceable-companion error that hid every save before."""
    (olympus_dir / "nodes.yaml").write_text(OLYMPUS_NODES.replace(
        "        Trackfile:\n          enabled: true\n          target: /opt/track\n",
        "        Olympus:\n          enabled: true\n"
        "          backend: {address: localhost, port: 4512}\n"
        "          frontend: {path: C:/Olympus, port: 4513}\n"), encoding="utf-8")
    before = (olympus_dir / "nodes.yaml").read_bytes()
    app = _app(olympus_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(_url())
        reply = await client.post(_url(), data={
            "_csrf_token": _csrf(page.text), "_revision": _revision(page.text),
            BASE + ".__entries": "backend\nfrontend",
            BASE + ".backend.address": "localhost", BASE + ".backend.port": "4512",
        })
    assert reply.status_code == 303, reply.text
    assert (olympus_dir / "nodes.yaml").read_bytes() == before, "a refused save wrote the file"


def test_a_removal_deletes_through_the_CONCRETE_path_not_the_template():
    """The deletion pass reads the entries' parent by the posted base; the tree's own ``field.path`` is
    the schema template (``*`` for node and instance) and addresses nothing in the document."""
    from services.webservice import configgen, scopes

    document = yaml.safe_load(OLYMPUS_NODES.replace(
        "        Trackfile:\n          enabled: true\n          target: /opt/track\n",
        "        Olympus:\n          enabled: true\n"
        "          backend: {address: localhost, port: 4512}\n"
        "          frontend: {path: C:/Olympus, port: 4513}\n"))
    _scopes, tree = scopes.build_extension_scopes(document, "Olympus", ["dcs-a-01"], node="dcs-a-01")
    from starlette.datastructures import FormData

    configgen.apply_form(document, FormData([
        (BASE + ".__entries", "backend\nfrontend"),          # the page rendered both
        (BASE + ".backend.address", "localhost"),             # and the form carries only one
        (BASE + ".backend.port", "4512"),
    ]), tree)
    block = document["dcs-a-01"]["instances"]["Syria"]["extensions"]["Olympus"]
    assert "backend" in block
    assert "frontend" not in block


@pytest.mark.asyncio
async def test_a_section_name_outside_the_schema_list_is_refused(olympus_dir, node_data):  # noqa: F811
    """The fixed-alternative rule survives the wildcard lookup: a forged name writes nothing."""
    before = (olympus_dir / "nodes.yaml").read_bytes()
    app = _app(olympus_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(_url())
        reply = await client.post(_url(), data={
            "_csrf_token": _csrf(page.text), "_revision": _revision(page.text),
            BASE + ".__entries": "", BASE + ".__key": "sideways",
            BASE + ".sideways.path": "C:/Olympus", BASE + ".sideways.port": "4513",
        })
    assert reply.status_code == 303, reply.text
    assert (olympus_dir / "nodes.yaml").read_bytes() == before, "a forged section name was written"


def test_the_template_matcher_accepts_only_matching_shapes():
    from services.webservice.configgen import _path_template_matches

    assert _path_template_matches(("*", "instances", "*", "extensions", "Olympus"),
                                  ("dcs-a-01", "instances", "Syria", "extensions", "Olympus"))
    assert not _path_template_matches(("*", "instances", "*", "extensions", "Olympus"),
                                      ("dcs-a-01", "instances", "Syria", "extensions", "SRS"))
    assert not _path_template_matches(("*", "instances"), ("dcs-a-01", "instances", "Syria"))
