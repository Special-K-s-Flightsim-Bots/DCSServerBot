"""The public site's password switch: ``servers.yaml``'s ``show_web_passwords`` and the console block.

WHAT THIS SUITE PINS
* the action writes ONE key in ONE server's block and leaves every other byte of ``servers.yaml``
  untouched — comments, key order and un-owned keys included;
* an unknown name, a manager, a non-boolean and a server with no block are REFUSED with a typed
  sentence, and nothing is written;
* the value already in force is a SKIP, not a rewrite;
* the schema refuses a non-boolean value and accepts a ``servers.yaml`` carrying the key;
* the console block renders one row per server of the site, offers the Save only to an Admin, and a
  manager neither sees the block nor can post it;
* the row reads the key as ``locals.get('show_web_passwords', False)`` — absent means OFF;
* the block's POST writes the switches and redirects with one notice.
"""

import asyncio
import os
import re
import types

import pytest
from ruamel.yaml import YAML

import core.actions as actions_module
from conftest import asgi_client
from services.webservice import mount, site_reads
from services.webservice.pages import site_config as site_page
from test_webui_write_pause import (  # noqa: F401 - fixtures used by the tests
    Cluster, build, client, instance, server, sign_in, _hash,
)
from test_webui_site_pages import bus  # noqa: F401 - the public pages' roster stub

PASSWORD = "correct horse battery staple"
ADMIN = "specialk"
MANAGER = "guards-admin"

BOT_YAML = """roles:
  Admin:
    - specialk
  DCS:
    - guards-admin
"""

SERVERS_YAML = """# Operator comment: this file is hand-maintained.
SRS-1:  # the first server
  server_user: Admin        # technical user
  show_passwords: true      # the Discord embed's own key
  managed_by:
    - 5th Guards
SRS-2:
  server_user: Admin
  managed_by:
    - 5th Guards
SRS-3:
  server_user: Admin
"""

SITE_YAML = """sites:
  root:
    slug: ""
    branding:
      title: Root
  guards:
    group_role: 5th Guards
    slug: guards
    branding:
      title: Guards
"""


def _users():
    return [
        {"username": ADMIN, "roles": ["Admin"], "password_hash": _hash()},
        {"username": MANAGER, "roles": ["DCS"], "scope": ["5th Guards"], "password_hash": _hash()},
    ]


def _config():
    return {
        "session": {"secret": "x" * 64},
        "auth": {"local": {"enabled": True, "users": _users()}},
    }


def _server(name, managed_by, *, password="", shown=False, config_dir):
    """A server double the password block and the action read."""
    return types.SimpleNamespace(
        name=name, display_name=name, status="SHUTDOWN",
        settings={"password": password},
        locals={"managed_by": list(managed_by), "show_web_passwords": shown},
        node=types.SimpleNamespace(name="dcs-01", config_dir=str(config_dir)),
    )


def _cluster(config_dir):
    return Cluster([
        _server("SRS-1", ["5th Guards"], password="alpha", shown=False, config_dir=config_dir),
        _server("SRS-2", ["5th Guards"], password="", shown=False, config_dir=config_dir),
        _server("SRS-3", [], password="rootpw", shown=True, config_dir=config_dir),
    ])


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (tmp_path / "site.yaml").write_text(SITE_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(config_dir):
    """The console app with the scheduler's actions registered and the registry restored after."""
    import importlib
    saved = dict(actions_module._ACTIONS)
    importlib.import_module("plugins.scheduler.actions")
    try:
        yield build(config_dir, _cluster(config_dir), plugins=("scheduler",), users=_users())
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)


def _page_path():
    return mount.url(site_page.SITE_CONFIG_PATH)


def _post_path():
    return mount.url(site_page.SITE_PASSWORDS_PATH)


def _token(html):
    match = re.search(r'name="_csrf_token" value="([^"]+)"', html)
    assert match, "the page rendered no CSRF token"
    return match.group(1)


async def _open(http):
    response = await http.get(_page_path())
    assert response.status_code == 200, response.text[:400]
    return response.text


# ------------------------------------------------------------------ the action (bot face)

def _ctx(servers, roles=("Admin",), scope=None):
    from core.actions import ActionContext
    bus = types.SimpleNamespace(servers={s.name: s for s in servers})
    identity = types.SimpleNamespace(roles=set(roles), scope=scope, shown_name="probe",
                                     backend="local", subject="probe")
    return ActionContext.from_web(identity, None, bus)


@pytest.fixture
def action(monkeypatch):
    """The scheduler module with its registry restored afterwards."""
    import importlib
    saved = dict(actions_module._ACTIONS)
    importlib.import_module("plugins.scheduler.actions")
    module = actions_module.action_registry()["set_server_web_passwords"]
    yield module
    actions_module._ACTIONS.clear()
    actions_module._ACTIONS.update(saved)


@pytest.mark.asyncio
async def test_admin_write_changes_only_the_one_key(config_dir, action, monkeypatch):
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: None)
    srv = _server("SRS-1", ["5th Guards"], config_dir=config_dir)
    ctx = _ctx([srv])
    before = (config_dir / "servers.yaml").read_bytes()

    result = await action(ctx, "SRS-1", True)

    assert result.success is True
    after = (config_dir / "servers.yaml").read_text(encoding="utf-8")
    assert "show_web_passwords: true" in after
    # every other line survives byte for byte
    assert "show_passwords: true" in after
    assert "# Operator comment: this file is hand-maintained." in after
    assert "server_user: Admin" in after
    assert "SRS-2:" in after and "SRS-3:" in after
    # the file changed only by the one key's line
    before_lines = before.decode().splitlines()
    after_lines = after.splitlines()
    assert len(after_lines) == len(before_lines) + 1
    assert [line for line in after_lines if "show_web_passwords" not in line] == before_lines


@pytest.mark.asyncio
async def test_unknown_server_is_refused_and_nothing_written(config_dir, action, monkeypatch):
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: None)
    srv = _server("SRS-1", ["5th Guards"], config_dir=config_dir)
    ctx = _ctx([srv])
    before = (config_dir / "servers.yaml").read_bytes()

    result = await action(ctx, "No Such Server", True)

    assert result.success is False
    assert "not found" in result.refused.lower()
    assert (config_dir / "servers.yaml").read_bytes() == before


@pytest.mark.asyncio
async def test_manager_is_refused_with_the_deny_list_sentence(config_dir, action, monkeypatch):
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: None)
    srv = _server("SRS-1", ["5th Guards"], config_dir=config_dir)
    scope = types.SimpleNamespace(allows=lambda managed_by: True, resolved=True, unrestricted=False)
    ctx = _ctx([srv], roles=(), scope=scope)
    before = (config_dir / "servers.yaml").read_bytes()

    result = await action(ctx, "SRS-1", True)

    assert result.success is False
    assert result.refused == "Publishing a server's password on the public site requires the Admin role."
    assert (config_dir / "servers.yaml").read_bytes() == before


@pytest.mark.asyncio
async def test_non_boolean_value_is_refused(config_dir, action, monkeypatch):
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: None)
    srv = _server("SRS-1", ["5th Guards"], config_dir=config_dir)
    ctx = _ctx([srv])
    before = (config_dir / "servers.yaml").read_bytes()

    result = await action(ctx, "SRS-1", "yes")

    assert result.success is False
    assert "true/false" in result.message
    assert (config_dir / "servers.yaml").read_bytes() == before


@pytest.mark.asyncio
async def test_unchanged_value_is_a_skip_not_a_rewrite(config_dir, action, monkeypatch):
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: None)
    srv = _server("SRS-1", ["5th Guards"], shown=True, config_dir=config_dir)
    ctx = _ctx([srv])
    before = (config_dir / "servers.yaml").read_bytes()

    result = await action(ctx, "SRS-1", True)

    assert result.success is False
    assert "nothing changed" in result.message.lower()
    assert (config_dir / "servers.yaml").read_bytes() == before


@pytest.mark.asyncio
async def test_missing_block_is_refused_and_nothing_written(config_dir, action, monkeypatch):
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: None)
    srv = _server("Ghost", [], config_dir=config_dir)
    ctx = _ctx([srv])
    before = (config_dir / "servers.yaml").read_bytes()

    result = await action(ctx, "Ghost", True)

    assert result.success is False
    assert "no entry" in result.refused.lower()
    assert (config_dir / "servers.yaml").read_bytes() == before


# ------------------------------------------------------------------ the schema

def test_schema_refuses_a_non_boolean_value(config_dir):
    from core.utils import configwrite
    path = config_dir / "servers.yaml"

    def mutate(document):
        document["SRS-1"]["show_web_passwords"] = "yes"

    with pytest.raises(configwrite.ConfigWriteError):
        configwrite.patch_config_file(path, mutate, schemas=[str(
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "schemas" / "servers_schema.yaml")])


def test_schema_accepts_a_file_carrying_the_key(config_dir):
    from core.utils import configwrite
    path = config_dir / "servers.yaml"
    text = path.read_text(encoding="utf-8").replace(
        "SRS-1:  # the first server\n", "SRS-1:  # the first server\n  show_web_passwords: false\n")
    path.write_text(text, encoding="utf-8")

    def mutate(document):
        document["SRS-1"]["show_web_passwords"] = True

    configwrite.patch_config_file(path, mutate, schemas=[str(
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "schemas" / "servers_schema.yaml")])
    assert "show_web_passwords: true" in path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ the console block

@pytest.mark.asyncio
async def test_admin_sees_the_block_and_its_rows(app):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await _open(http)
    assert 'id="sc-pass-' in html or "Server passwords" in html
    assert "SRS-1" in html and "SRS-2" in html and "SRS-3" in html
    assert "Save passwords" in html


@pytest.mark.asyncio
async def test_manager_does_not_see_the_block(app):
    async with client(app) as http:
        await sign_in(http, MANAGER)
        html = await _open(http)
    assert "Server passwords" not in html


@pytest.mark.asyncio
async def test_absent_key_reads_off_and_true_reads_on(app):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await _open(http)
    # SRS-1 has no key in the file -> its switch is not checked
    row = re.search(r'name="show_password\.SRS-1"([^>]*)>', html)
    assert row is not None
    assert "checked" not in row.group(1)
    # SRS-3's locals carry true -> its switch is checked
    row = re.search(r'name="show_password\.SRS-3"([^>]*)>', html)
    assert row is not None
    assert "checked" in row.group(1)


@pytest.mark.asyncio
async def test_post_writes_the_switch_and_redirects_with_a_notice(app, config_dir):
    async with client(app) as http:
        await sign_in(http, ADMIN)
        html = await _open(http)
        token = _token(html)
        reply = await http.post(_post_path(), data={
            "_csrf_token": token, "_site": "guards", "show_password.SRS-1": "on",
        })
    assert reply.status_code in (302, 303)
    text = (config_dir / "servers.yaml").read_text(encoding="utf-8")
    assert "show_web_passwords: true" in text


@pytest.mark.asyncio
async def test_manager_post_is_refused_and_writes_nothing(app, config_dir):
    async with client(app) as http:
        await sign_in(http, MANAGER)
        html = await _open(http)
        token = _token(html)
        reply = await http.post(_post_path(), data={
            "_csrf_token": token, "_site": "guards", "show_password.SRS-1": "on",
        })
    # the capability gate refuses the crafted POST before the handler: 403, nothing written
    assert reply.status_code == 403
    text = (config_dir / "servers.yaml").read_text(encoding="utf-8")
    assert "show_web_passwords" not in text


# ------------------------------------------------------------------ the public page

def _public_server(name, password, shown):
    """A live roster double shaped like the public page's own read."""
    return types.SimpleNamespace(
        name=name, display_name=name, status=types.SimpleNamespace(name="RUNNING"),
        players={}, current_mission=None, settings={"password": password},
        maintenance=False, node=types.SimpleNamespace(public_ip="1.2.3.4"),
        locals={"show_web_passwords": shown},
    )


def _public_page(config_dir, bus, name, password, shown):
    """The public server page over the site's own roster and the real template."""
    from core.utils.helper import ThreadSafeDict
    from test_webui_site_pages import build as public_build
    bus.servers = ThreadSafeDict({name: _public_server(name, password, shown)})
    site_reads.forget_live_reads()

    async def _get():
        async with asgi_client(public_build(config_dir)[0]) as http:
            return await http.get("/fleet/" + name)

    return asyncio.run(_get())


def test_public_page_hides_the_password_when_the_key_is_off(config_dir, bus):
    response = _public_page(config_dir, bus, "SRS-3", "alpha", False)
    assert response.status_code == 200, response.text[:400]
    assert "alpha" not in response.text


def test_public_page_prints_the_password_when_the_key_is_on(config_dir, bus):
    response = _public_page(config_dir, bus, "SRS-3", "alpha", True)
    assert response.status_code == 200, response.text[:400]
    assert "alpha" in response.text
