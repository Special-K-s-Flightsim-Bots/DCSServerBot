"""THE SCHEMA TREE AS HINTS JSON — the Expert Mode editor's completion source.

The Settings pages validate a YAML document against the bot's own schemas but offer no completion. The
schema tree already carries every key, its kind, its description and its enum, so this file pins the
one contract the browser-side client is written against: ``config_hints.payload`` and the two GET
routes that serve it.

Pinned here: the root container and the per-container ``paths`` entries; a sequence element and a
``regex;`` entry named by a ``*`` segment; the SCHEMA catalog's translation of a description against the
webui catalog's kind word; the content-digest memo (two reads parse once, an edit invalidates); an
unresolvable container answering an empty map rather than raising; the read gate (403/200); a
``?node=`` request rooted at that node's block; and the two pages naming the URL they fetch.
"""
from __future__ import annotations

import json
import os

import pytest

from conftest import asgi_client
from services.webservice import config_hints, configgen, i18n, scheduler_plan
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import _build, _hash, _login

#: the schema files ``main.yaml`` is validated against — the same set the page uses.
MAIN_SCHEMA_FILES = configgen.schema_files_for(settings_page.MAIN_SCHEMA_PATH)

#: the kinds that are a BLOCK in a document, so a row for one can offer its own example.
CONTAINER_KINDS = frozenset({configgen.KIND_MAP, configgen.KIND_SEQ, configgen.KIND_REGEX,
                             configgen.KIND_CHOICE})


class FakeRequest:
    """The two request facts the resolvers read: the cookie jar and the headers (no app)."""

    def __init__(self, language: str = "en"):
        self.cookies = {}
        self.headers = {"accept-language": language}
        self.app = None


def _write_schema(tmp_path, text: str):
    path = tmp_path / "s.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ============================================================== 1. the main schema's own payload

# A strict pin on a defect in the PRODUCT, not a weaker assertion: the statements below are the
# ones the design calls for, so each is expected to fail until the product implements them, and
# each fails the suite again (XPASS) the moment the product does.
_XFAIL_NO_SITE_YAML_HINTS = pytest.mark.xfail(
    strict=True,
    reason=(
        "`services/webservice/pages/site_config.py` declares no `/settings/site/yaml/hints` route (its "
        "only site.yaml paths are `SITE_YAML_PATH` and `SITE_YAML_VALIDATE_PATH`), so the request 404s "
        "and the page carries no hints URL"
    ),
)


def test_the_main_schema_roots_at_the_empty_path_and_names_every_container():
    payload = config_hints.payload(MAIN_SCHEMA_FILES)
    assert json.loads(json.dumps(payload)) == payload, "the payload is JSON-native"

    root = payload["paths"][""]
    assert root["desc"] == "", "the root has no schema prose"
    assert root["kind"] == configgen.KIND_MAP
    assert [child["key"] for child in root["children"]][:2] == ["guild_id", "guild_name"]

    containers = [child for child in root["children"] if child["kind"] in CONTAINER_KINDS]
    assert containers, "main.yaml carries blocks"
    for child in containers:
        assert child["path"] in payload["paths"], child["key"]

    database = payload["paths"]["database"]
    assert database["desc"] == "Database connection and pooling configuration."
    assert database["kind"] == configgen.KIND_MAP
    url = next(child for child in database["children"] if child["key"] == "url")
    assert url["path"] == "database.url" and url["kind"] == configgen.KIND_TEXT
    assert url["required"] is True
    assert url["desc"].startswith("PostgreSQL connection URL.")
    assert isinstance(url["example"], str) and url["example"].startswith("postgresql://")
    assert url["label"] == "Text", "the kind word is the webui catalog's, in the request's language"
    assert url["enum"] == []


# ============================================================== 2. the `*` segments

SEQ_OF_ENUM_SCHEMA = """\
type: map
nullable: false
mapping:
  levels:
    type: seq
    nullable: false
    desc: The levels to report.
    sequence:
      - type: str
        enum: ['connect', 'disconnect']
"""

REGEX_SCHEMA = """\
type: map
nullable: false
mapping:
  DEFAULT:
    type: map
    nullable: false
    mapping:
      debug:
        type: bool
        nullable: true
  regex;(.+):
    type: map
    nullable: false
    desc: One block per server.
    mapping:
      home:
        type: str
        nullable: true
"""


def test_a_sequence_of_enums_exposes_its_element_under_a_star_path(tmp_path):
    payload = config_hints.payload([str(_write_schema(tmp_path, SEQ_OF_ENUM_SCHEMA))])
    levels = payload["paths"][""]["children"][0]
    assert levels["key"] == "levels" and levels["kind"] == configgen.KIND_LIST

    inside = payload["paths"]["levels"]["children"]
    assert [child["path"] for child in inside] == ["levels.*"]
    assert inside[0]["kind"] == configgen.KIND_SELECT
    assert inside[0]["enum"] == ["connect", "disconnect"], "the element's OWN enum, in schema order"


def test_a_regex_container_lists_its_children_under_a_star_segment(tmp_path):
    payload = config_hints.payload([str(_write_schema(tmp_path, REGEX_SCHEMA))])
    children = {child["key"]: child for child in payload["paths"][""]["children"]}
    block = children["regex;(.+)"]
    assert block["kind"] == configgen.KIND_REGEX
    assert block["path"] == "regex;(.+)", "the key as written in the schema"

    # the entry inside the container IS one free-key block: its own segment is `*`, and the block's
    # keys hang off that segment
    inside = payload["paths"]["regex;(.+)"]["children"]
    assert [(child["key"], child["path"]) for child in inside] == [("regex", "regex;(.+).*")]
    assert inside[0]["kind"] == configgen.KIND_MAP
    assert [child["path"] for child in payload["paths"]["regex;(.+).*"]["children"]] == \
        ["regex;(.+).*.home"]


# ============================================================== 3. the description's language

DESC_SCHEMA = """\
type: map
nullable: false
mapping:
  port:
    type: int
    nullable: false
    desc: The UDP port the server listens on.
"""


@pytest.fixture
def german_catalog(tmp_path, monkeypatch):
    """A ``de`` install: a webui catalog (so the language is OFFERED) and a schema catalog."""
    pytest.importorskip("babel")
    from babel.messages.catalog import Catalog
    from babel.messages.mofile import write_mo

    locale_dir = tmp_path / "locale"
    directory = locale_dir / "de" / "LC_MESSAGES"
    directory.mkdir(parents=True)
    with (directory / f"{i18n.DOMAIN}.mo").open("wb") as handle:
        write_mo(handle, Catalog(locale="de", domain=i18n.DOMAIN))
    schema_catalog = Catalog(locale="de", domain=i18n.SCHEMA_DOMAIN)
    schema_catalog.add("The UDP port the server listens on.", string="DER UDP-PORT")
    with (directory / f"{i18n.SCHEMA_DOMAIN}.mo").open("wb") as handle:
        write_mo(handle, schema_catalog)

    monkeypatch.setattr(i18n, "LOCALE_DIR", locale_dir)
    monkeypatch.setattr(i18n, "_SCHEMA_TRANSLATORS", {})
    monkeypatch.setattr(i18n, "_CATALOG_CACHE", {})
    return locale_dir


def test_a_description_follows_the_requests_language(tmp_path, german_catalog):
    path = str(_write_schema(tmp_path, DESC_SCHEMA))
    german = config_hints.payload([path], request=FakeRequest("de"))
    assert german["paths"][""]["children"][0]["desc"] == "DER UDP-PORT"

    english = config_hints.payload([path], request=FakeRequest("en"))
    assert english["paths"][""]["children"][0]["desc"] == "The UDP port the server listens on.", \
        "English is the schema's own text"


# ============================================================== 4. the memo

def test_two_payloads_parse_the_schema_once_and_an_edit_changes_the_answer(tmp_path, monkeypatch):
    text = DESC_SCHEMA
    path = _write_schema(tmp_path, text)
    calls = []
    original = configgen.load_schema

    def counting(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(config_hints.configgen, "load_schema", counting)

    first = config_hints.payload([str(path)], request=FakeRequest("en"))
    second = config_hints.payload([str(path)], request=FakeRequest("en"))
    assert first == second
    assert len(calls) == 1, "the second read answers from the memo"

    # a SAME-LENGTH rewrite that leaves the mtime EXACTLY as it was — the clock-tick collision an
    # mtime-keyed memo would miss: only the CONTENT says the schema changed
    edited = text.replace("listens on.", "listens on!")
    assert len(edited) == len(text)
    before = path.stat()
    path.write_text(edited, encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)

    third = config_hints.payload([str(path)], request=FakeRequest("en"))
    assert third["paths"][""]["children"][0]["desc"] == "The UDP port the server listens on!"
    assert len(calls) == 2, "an edited schema file must be parsed again"


# ============================================================== 5. the unresolvable container

def test_an_unresolvable_container_answers_an_empty_path_map():
    assert config_hints.payload(MAIN_SCHEMA_FILES, container=("nope",)) == {"paths": {}}
    assert config_hints.payload(MAIN_SCHEMA_FILES, container=("database", "nope")) == {"paths": {}}


# ============================================================== 6. the routes

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
"""

NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  instances:
    Nevada:
      home: /home/b-nevada
"""

MAIN_YAML = """\
guild_id: 112233445566778899
guild_name: Test
autoupdate: false
use_dashboard: true
chat_command_prefix: "-"
language: en
database:
  url: postgresql://dcssb:secret@localhost:5432/dcssb
"""

MISSION_YAML = """\
DEFAULT:
  messages:
    ban_username: banned
dcs-a-01:
  Syria:
    messages:
      ban_username: syria banned
dcs-b-01:
  Nevada:
    messages:
      ban_username: nevada banned
"""


@pytest.fixture
def hints_dir(tmp_path):
    """A config dir with ``main.yaml``, one schema'd plugin file and one schema-LESS service file."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "services" / "noschema.yaml").write_text("DEFAULT:\n  x: 1\n", encoding="utf-8")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "mission.yaml").write_text(MISSION_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(MAIN_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def hints_app(hints_dir):
    return _build(hints_dir, users=[
        {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]},
        {"username": "mod1", "password_hash": _hash(), "roles": ["DCS Admin"]},
    ])[0]


async def _get(app, path, username="specialk", *, expect=200):
    async with asgi_client(app) as client:
        await _login(client, username)
        response = await client.get(path)
    assert response.status_code == expect, (path, response.status_code, response.text[:300])
    return response


@pytest.mark.asyncio
async def test_the_root_route_serves_the_main_schema(hints_app):
    response = await _get(hints_app, "/actions/settings/hints")
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["paths"][""]["kind"] == configgen.KIND_MAP
    assert any(child["key"] == "database" for child in payload["paths"][""]["children"])


@pytest.mark.asyncio
async def test_a_role_without_the_read_capability_is_refused(hints_app):
    await _get(hints_app, "/actions/settings/hints/plugin/mission", "mod1", expect=403)


@pytest.mark.asyncio
async def test_a_node_request_roots_the_payload_at_that_nodes_block(hints_app):
    whole = (await _get(hints_app, "/actions/settings/hints/plugin/mission")).json()
    assert [child["key"] for child in whole["paths"][""]["children"]] == \
        ["DEFAULT", "commands", "chat_commands", "regex;(.+)"]

    rooted = (await _get(hints_app, "/actions/settings/hints/plugin/mission?node=dcs-a-01")).json()
    assert [child["key"] for child in rooted["paths"][""]["children"]] == \
        ["messages", "event_filter", "uploads", "regex;(.+)"], \
        "the node's block is the keys that block holds, not the file's top-level keys"
    assert rooted != whole


@pytest.mark.asyncio
async def test_an_unknown_node_answers_an_empty_path_map_and_never_a_root_payload(hints_app):
    response = await _get(hints_app, "/actions/settings/hints/main/main?node=nosuchnode")
    assert response.json() == {"paths": {}}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_an_unknown_node_on_a_regex_rooted_schema_still_answers_an_empty_path_map(hints_app):
    """A plugin schema's top-level ``regex;(.+)`` is an invented KEY, never an open door for a name.

    The schema would descend into that container for ANY unknown token, so the name is checked
    against the keys the FILE carries first: a name it does not carry answers the empty map, and a
    name it does carry still answers that block's tree.
    """
    whole = (await _get(hints_app, "/actions/settings/hints/plugin/mission")).json()
    assert INVENTED_KEY in [child["key"] for child in whole["paths"][""]["children"]], \
        "this pin needs a schema that declares the invented-key container"

    unknown = await _get(hints_app, "/actions/settings/hints/plugin/mission?node=garbage-name")
    assert unknown.json() == {"paths": {}}, "an unknown name answered the invented-key block tree"
    assert unknown.headers["cache-control"] == "no-store"

    carried = await _get(hints_app, "/actions/settings/hints/plugin/mission?node=dcs-a-01")
    keys = [child["key"] for child in carried.json()["paths"][""]["children"]]
    assert keys == ["messages", "event_filter", "uploads", INVENTED_KEY], keys


@pytest.mark.asyncio
async def test_a_file_without_a_schema_answers_an_empty_path_map(hints_app):
    response = await _get(hints_app, "/actions/settings/hints/service/noschema")
    assert response.json() == {"paths": {}}


@pytest.mark.asyncio
async def test_the_pages_name_the_hints_url_they_fetch(hints_app):
    main_page = (await _get(hints_app, "/settings/main/main")).text
    assert 'data-hints-url="/actions/settings/hints/main/main"' in main_page

    scoped_page = (await _get(hints_app, "/settings/plugin/mission?node=dcs-a-01")).text
    assert 'data-hints-url="/actions/settings/hints/plugin/mission?node=dcs-a-01"' in scoped_page

    plain_page = (await _get(hints_app, "/settings/service/noschema")).text
    assert 'data-hints-url="/actions/settings/hints/service/noschema"' in plain_page


# ============================================================================ 7. the other surfaces
# THE SAME editor drives five more surfaces, each over its own schema. Every one of them gets ONE hints
# route beside the check it already posts to, and the page names it — no second builder, no second
# client:
#
# * the node page and an extension page edit ONE NODE'S SLICE of ``nodes.yaml``, i.e. a one-key
#   document (``{<node>: block}``), so the payload is rooted at the FILE's root (the container is the
#   empty one) and the block's keys hang under the schema's own ``regex;(.+)`` entry;
# * both preset surfaces edit ONE PRESET's block, also a one-key document — the same rooting, through
#   the preset schema's ``regex;(.+)`` entry;
# * the Site YAML sheet edits the WHOLE ``site.yaml``, so the root entry carries that file's keys
#   (``sites``);
# * the Scheduler review panel is the one exception: its document IS the block's CONTENT, so the
#   payload is rooted at the block's path — the ``DEFAULT`` key on the shared sheet, else the server
#   block the save's own guard resolves.

NODES_SURFACE_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  extensions:
    RealWeather:
      installation: /opt/rw-a
  instances:
    Syria:
      home: /home/a-syria
"""

SERVERS_SURFACE_YAML = """\
SRS-1:
  node: dcs-a-01
"""

PRESETS_SURFACE_YAML = """\
Spring:
  date: '2016-04-21'
  wind:
    atGround: {speed: 3, dir: 20}
"""

SITE_SURFACE_YAML = """\
sites:
  root:
    slug: ""
    enabled: false
    branding:
      title: Root Site
"""

#: the schema's own name for the block a document invents a key for (a node, a preset, a site).
INVENTED_KEY = "regex;(.+)"


@pytest.fixture
def surfaces_dir(tmp_path):
    """One config dir carrying every surface's own file, so each route reads its own schema."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_SURFACE_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_SURFACE_YAML, encoding="utf-8")
    (tmp_path / "presets.yaml").write_text(PRESETS_SURFACE_YAML, encoding="utf-8")
    (tmp_path / "site.yaml").write_text(SITE_SURFACE_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def bot_data(surfaces_dir, monkeypatch):
    """Bind the bot's own validator to THIS install's cluster, as production binds it at startup."""
    import types

    import core.commandline as commandline
    from core.utils import validators

    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(surfaces_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance


def _one_node_source():
    """A source that knows the one node ``surfaces_dir``'s ``nodes.yaml`` declares."""
    from test_webui_dashboard import _source, node

    nodes = {"dcs-a-01": node(name="dcs-a-01", master=True)}
    return _source(nodes=nodes, node_obj=nodes["dcs-a-01"])


def _surface_app(config_dir, users=None):
    app = _build(config_dir, users=users if users is not None else [
        {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]},
        {"username": "mod1", "password_hash": _hash(), "roles": ["DCS Admin"]},
    ])[0]
    app.state.webui_source_provider = _one_node_source
    return app


def _children(payload: dict, path: str) -> list[str]:
    return [row["key"] for row in payload["paths"][path]["children"]]


# ---------------------------------------------------------------------------- 1. the node page

@pytest.mark.asyncio
async def test_the_node_page_hints_root_the_payload_at_the_files_own_keys(surfaces_dir, bot_data):
    """The slice's root key IS the node's name, so the root entry names the schema's invented key.

    The keys of the block are NOT at the root: they hang under ``regex;(.+).*`` — the entry the
    client's own wildcard step resolves a node name through. A payload rooted at the block instead
    would offer block keys where the document carries a node name.
    """
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client)
        response = await client.get("/actions/nodes/dcs-a-01/hints")
    assert response.status_code == 200, response.text[:300]
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert _children(payload, "") == [INVENTED_KEY]
    assert "listen_address" in _children(payload, f"{INVENTED_KEY}.*")
    assert "instances" in _children(payload, f"{INVENTED_KEY}.*")
    assert "listen_address" not in _children(payload, ""), "the block's keys are not the file's keys"


@pytest.mark.asyncio
async def test_the_node_page_hints_refuse_a_role_without_the_page(surfaces_dir, bot_data):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client, "mod1")
        response = await client.get("/actions/nodes/dcs-a-01/hints")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_node_page_names_the_hints_url_it_fetches(surfaces_dir, bot_data):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client)
        page = (await client.get("/nodes/dcs-a-01")).text
    assert 'data-hints-url="/actions/nodes/dcs-a-01/hints"' in page


# ------------------------------------------------------------------- 2. a node extension's page

@pytest.mark.asyncio
async def test_the_extension_page_hints_answer_the_same_tree(surfaces_dir, bot_data):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client)
        response = await client.get("/actions/nodes/dcs-a-01/extensions/RealWeather/hints")
    assert response.status_code == 200, response.text[:300]
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert _children(payload, "") == [INVENTED_KEY]
    assert "listen_address" in _children(payload, f"{INVENTED_KEY}.*")


@pytest.mark.asyncio
async def test_the_extension_page_hints_refuse_a_role_without_the_page(surfaces_dir, bot_data):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client, "mod1")
        response = await client.get("/actions/nodes/dcs-a-01/extensions/RealWeather/hints")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_extension_page_names_the_hints_url_it_fetches(surfaces_dir, bot_data):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client)
        page = (await client.get("/nodes/dcs-a-01/extensions/RealWeather")).text
    assert ('data-hints-url="/actions/nodes/dcs-a-01/extensions/RealWeather/hints"' in page), \
        "the extension page does not name the hints URL its editor fetches"


# ------------------------------------------------------------------------ 3. the preset surfaces

@pytest.mark.asyncio
async def test_the_preset_editor_hints_answer_the_preset_schema(surfaces_dir):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client)
        response = await client.get("/presets/preset/Spring/yaml/hints")
    assert response.status_code == 200, response.text[:300]
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert _children(payload, "") == [INVENTED_KEY]
    block = _children(payload, f"{INVENTED_KEY}.*")
    assert "wind" in block and "date" in block, block


@pytest.mark.asyncio
async def test_the_preset_editor_names_the_hints_url_it_fetches(surfaces_dir):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client)
        page = (await client.get("/presets/preset/Spring/yaml")).text
    assert 'data-hints-url="/presets/preset/Spring/yaml/hints"' in page


@pytest.mark.asyncio
async def test_the_presets_wizard_names_the_hints_url_it_fetches(surfaces_dir):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client)
        page = (await client.get("/presets/preset/Spring/wizard?step=review")).text
        named = await client.get("/presets/preset/Spring/wizard/hints")
        fresh = await client.get("/presets/new/hints")
        fresh_page = (await client.get("/presets/new?step=review")).text
    assert 'data-hints-url="/presets/preset/Spring/wizard/hints"' in page
    assert 'data-hints-url="/presets/new/hints"' in fresh_page
    assert named.status_code == 200 and fresh.status_code == 200
    assert named.json() == fresh.json()


@pytest.mark.asyncio
async def test_the_preset_hints_refuse_a_role_without_the_area(surfaces_dir):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client, "mod1")
        response = await client.get("/presets/preset/Spring/yaml/hints")
    assert response.status_code == 403


# ---------------------------------------------------------------------------- 5. the site sheet

@pytest.mark.asyncio
@_XFAIL_NO_SITE_YAML_HINTS
async def test_the_site_yaml_hints_answer_the_files_own_keys(surfaces_dir):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client)
        response = await client.get("/settings/site/yaml/hints")
    assert response.status_code == 200, response.text[:300]
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert _children(payload, "") == ["sites"], "the document IS site.yaml, so its key is `sites`"
    assert "branding" in _children(payload, f"sites.{INVENTED_KEY}.*")


@pytest.mark.asyncio
@_XFAIL_NO_SITE_YAML_HINTS
async def test_the_site_yaml_hints_refuse_a_role_without_the_expert_sheet(surfaces_dir):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client, "mod1")
        response = await client.get("/settings/site/yaml/hints")
    assert response.status_code == 403


@pytest.mark.asyncio
@_XFAIL_NO_SITE_YAML_HINTS
async def test_the_site_yaml_sheet_names_the_hints_url_it_fetches(surfaces_dir):
    async with asgi_client(_surface_app(surfaces_dir)) as client:
        await _login(client)
        page = (await client.get("/settings/site")).text
    assert 'data-hints-url="/settings/site/yaml/hints"' in page


# ------------------------------------------------------------------------ 4. the Scheduler sheet
# Driven through the scheduler suite's own harness: the real application, the scheduler PLUGIN doubled,
# and a cluster whose two servers carry the file's two blocks. The IMPORTED fixtures are the module's
# (``scope_dir``, ``node_data``, ``client``, ``clean_registry``) so the app under test is the same one
# those suites exercise; only the surface-specific apps are built here.

from test_webui_scheduler_review_yaml import (  # noqa: E402 - imported after the sections above
    PLAN as SCHEDULER_PLAN, review_url as scheduler_review_url)
from test_webui_scheduler_save import (  # noqa: E402,F401 - fixtures imported on purpose
    ADMIN as SCHEDULER_ADMIN, SERVER_A, SERVER_B, app_for as scheduler_app_for, clean_registry,
    client, cluster, sign_in, scope_dir, node_data, build as scheduler_build)


@pytest.mark.asyncio
async def test_the_scheduler_hints_root_the_payload_at_the_servers_own_block(scope_dir, node_data,
                                                                           clean_registry,
                                                                           monkeypatch):
    """The block's CONTENT is the document, so its keys ARE the root — not the file's sections."""
    app = scheduler_app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, SCHEDULER_ADMIN)
        response = await http.get(f"/actions/settings/scheduler/hints?_scope={SERVER_A}")
    assert response.status_code == 200, response.text[:300]
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    keys = _children(payload, "")
    assert "timezone" in keys and "schedule" in keys and "startup" in keys, keys
    assert "DEFAULT" not in keys and "commands" not in keys, \
        "the file's own sections are not keys of the block this sheet edits"


@pytest.mark.asyncio
async def test_the_scheduler_shared_sheet_hints_answer_the_default_block(scope_dir, node_data,
                                                                       clean_registry, monkeypatch):
    """``DEFAULT`` is a literal key of the schema, so the shared sheet's tree has no invented key."""
    app = scheduler_app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, SCHEDULER_ADMIN)
        response = await http.get(f"/actions/settings/scheduler/hints?_scope={SERVER_A}"
                                  f"&{scheduler_plan.SHARED_FIELD}=1")
    assert response.status_code == 200, response.text[:300]
    keys = _children(response.json(), "")
    assert INVENTED_KEY not in keys, "the shared block's keys are the element schema's own keys"
    assert "timezone" in keys and "schedule" in keys, keys


@pytest.mark.asyncio
async def test_the_scheduler_hints_answer_an_empty_map_for_an_unknown_scope(scope_dir, node_data,
                                                                          clean_registry, monkeypatch):
    app = scheduler_app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, SCHEDULER_ADMIN)
        response = await http.get("/actions/settings/scheduler/hints?_scope=nosuchserver")
    assert response.status_code == 200
    assert response.json() == {"paths": {}}


@pytest.mark.asyncio
async def test_the_scheduler_hints_refuse_a_role_without_the_scheduler(scope_dir, node_data,
                                                                     clean_registry):
    app = scheduler_build(scope_dir, cluster(), users=[
        {"username": "member1", "password_hash": _hash(), "roles": ["DCS"]},
    ])
    async with client(app) as http:
        await sign_in(http, "member1")
        response = await http.get(f"/actions/settings/scheduler/hints?_scope={SERVER_A}")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_scheduler_review_panel_names_the_hints_url_it_fetches(scope_dir, node_data,
                                                                        clean_registry, monkeypatch):
    app = scheduler_app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, SCHEDULER_ADMIN)
        response = await http.get(scheduler_review_url(SCHEDULER_PLAN))
    assert response.status_code == 200, response.text[:300]
    assert (f'data-hints-url="/actions/settings/scheduler/hints?_scope={SERVER_A}"'
            in response.text), "the review panel does not name the hints URL its editor fetches"


# ------------------------------------------------- 5. the shared sheet's cross-server boundary
# `scheduler_hints` gates the SHARED sheet on `access.shared` (an installation-wide write, so the
# Admin's alone) and the per-server tree on `access.view` plus a server inside the caller's scope.
# These three pins are the boundary itself: the same request shape, three audiences.

SCOPE_GRANTED_VIEWER = "hosta-mod"


def _scope_granted_viewer_users():
    """A viewer whose SCOPE grants it one server (``Host A``, which hosts ``SRS-1``) and no more.

    Not an Admin: its role carries the per-server read and write, so every refusal below is the
    SCOPE's or the shared-sheet gate's, never the capability's.
    """
    return [{"username": SCOPE_GRANTED_VIEWER, "password_hash": _hash(), "roles": ["DCS Admin"],
             "scope": ["Host A"]}]


@pytest.mark.asyncio
async def test_a_scope_granted_viewer_is_refused_the_shared_sheet(scope_dir, node_data,
                                                                 clean_registry):
    """``access.shared`` is the Admin's: the shared default is every server's starting point."""
    app = scheduler_build(scope_dir, cluster(), users=_scope_granted_viewer_users())
    async with client(app) as http:
        await sign_in(http, SCOPE_GRANTED_VIEWER)
        response = await http.get(f"/actions/settings/scheduler/hints?_scope={SERVER_A}"
                                  f"&{scheduler_plan.SHARED_FIELD}=1")
    assert response.status_code == 200, response.text[:300]
    assert response.json() == {"paths": {}}, "a scope-granted viewer was handed the shared tree"


@pytest.mark.asyncio
async def test_a_scope_granted_viewer_is_refused_a_server_out_of_its_scope(scope_dir, node_data,
                                                                          clean_registry):
    """The server must be one the caller's scope holds — ``SRS-B`` is hosted by ``Host B``."""
    app = scheduler_build(scope_dir, cluster(), users=_scope_granted_viewer_users())
    async with client(app) as http:
        await sign_in(http, SCOPE_GRANTED_VIEWER)
        response = await http.get(f"/actions/settings/scheduler/hints?_scope={SERVER_B}")
    assert response.status_code == 200, response.text[:300]
    assert response.json() == {"paths": {}}, "a server outside the scope answered a tree"


@pytest.mark.asyncio
async def test_an_admin_gets_both_trees_and_they_are_not_the_same_payload(scope_dir, node_data,
                                                                        clean_registry, monkeypatch):
    """The shared sheet roots at ``DEFAULT``, a per-server scope at that server's block."""
    app = scheduler_app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, SCHEDULER_ADMIN)
        shared = (await http.get(f"/actions/settings/scheduler/hints?_scope={SERVER_A}"
                                 f"&{scheduler_plan.SHARED_FIELD}=1")).json()
        own = (await http.get(f"/actions/settings/scheduler/hints?_scope={SERVER_A}")).json()
    shared_keys = _children(shared, "")
    own_keys = _children(own, "")
    assert shared_keys and own_keys, (shared_keys, own_keys)
    assert "timezone" in shared_keys and "timezone" in own_keys
    assert shared != own, "the shared sheet and the server block answered the same tree"
    assert INVENTED_KEY not in shared_keys, "the shared block's keys are the schema's own keys"
    assert set(own_keys) - set(shared_keys), \
        "a server's block carries keys the shared block does not (its instance section)"
