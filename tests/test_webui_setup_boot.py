"""The boot window, and the proof that the normal console is untouched by it.

Two subjects:

* WHICH boot may start the setup console — ``main.yaml`` absent at process start, the refusal for
  every other boot, and the interactive/headless choice between the console and the command line
  installer.
* WHAT THAT COSTS THE NORMAL CONSOLE — the identity backend set, the anonymous refusal and a real
  sign-in on an ordinary boot, plus the fact that a configured install has no setup route at all.

WHAT THEY DO NOT COVER: ``run.py`` itself is not imported here (its module import pulls the bot's
runtime environment, so a rule that lived there would be untested) — which is why the two launcher
rules live in the console's own module and only the wiring stays in the runner. Nothing here proves
a browser is opened, and nothing here proves the process's ``PidFile`` keeps a second process out.
"""
from __future__ import annotations

import asyncio
import re
import socket
import sys
import types
from pathlib import Path

import httpx
import pytest

from conftest import asgi_client, create_app, install_shell, permissions
from services.webservice import session
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.setup import boot as setup_boot
from services.webservice.setup import service as setup_service
from services.webservice.setup.app import SETUP_PATH

CODE = "ABCD2345"
PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"

BOT_YAML = """\
roles:
  Admin:
    - specialk
"""


# ----------------------------------------------------------------------- the two launcher rules

def test_the_window_is_the_presence_of_main_yaml_at_process_start(tmp_path):
    assert setup_boot.should_run_setup(tmp_path) is True
    assert setup_boot.main_config_path(tmp_path) == str(tmp_path / "main.yaml")
    (tmp_path / "main.yaml").write_text("node: TestNode\n", encoding="utf-8")
    assert setup_boot.should_run_setup(tmp_path) is False


# ------------------------------------------------------- the joining half of the same rule

def test_the_window_also_opens_for_a_node_absent_from_nodes_yaml(tmp_path):
    """``main.yaml`` PRESENT and this node has no block: the joining state, and only for that node."""
    (tmp_path / "main.yaml").write_text("guild_id: 7\n", encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text("NodeOne:\n  listen_port: 10042\n", encoding="utf-8")
    assert setup_boot.setup_mode(tmp_path, "JoinNode") == setup_boot.JOIN
    assert setup_boot.should_run_setup(tmp_path, "JoinNode") is True
    # the node that DOES have a block stays closed, in the same directory
    assert setup_boot.setup_mode(tmp_path, "NodeOne") is None
    assert setup_boot.should_run_setup(tmp_path, "NodeOne") is False
    # and a caller that names no node never gets the joining answer
    assert setup_boot.should_run_setup(tmp_path) is False
    assert setup_boot.nodes_config_path(tmp_path) == str(tmp_path / "nodes.yaml")


def test_the_window_rule_reads_nodes_yaml_with_the_project_s_own_parser(tmp_path, monkeypatch):
    """An ``import yaml`` here works on any machine that happens to carry PyYAML and dies with
    ModuleNotFoundError on an install built from requirements.txt, which declares only
    ruamel-yaml. Blocking PyYAML is the state a clean install is in."""
    (tmp_path / "main.yaml").write_text("guild_id: 7\n", encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text("NodeOne:\n  listen_port: 10042\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "yaml", None)  # a later `import yaml` now raises ImportError
    assert setup_boot.setup_mode(tmp_path, "JoinNode") == setup_boot.JOIN
    # the control: the file really was parsed, by the node it does carry
    assert setup_boot.setup_mode(tmp_path, "NodeOne") is None


def test_main_yaml_alone_is_not_a_joining_state(tmp_path):
    """The rule needs BOTH files: a configured install whose nodes.yaml is absent stays closed."""
    (tmp_path / "main.yaml").write_text("guild_id: 7\n", encoding="utf-8")
    assert setup_boot.setup_mode(tmp_path, "JoinNode") is None
    assert setup_boot.should_run_setup(tmp_path, "JoinNode") is False


def test_the_joining_rule_fails_closed_on_a_document_it_cannot_read(tmp_path):
    """A malformed, empty or non-mapping nodes.yaml is NEVER read as "this node is missing"."""
    (tmp_path / "main.yaml").write_text("guild_id: 7\n", encoding="utf-8")
    nodes = tmp_path / "nodes.yaml"
    for content in ("NodeOne: [unclosed\n", "", "# only a comment\n",
                    "- NodeOne\n- NodeTwo\n", "just a scalar\n"):
        nodes.write_text(content, encoding="utf-8")
        assert setup_boot.setup_mode(tmp_path, "JoinNode") is None, content
        assert setup_boot.should_run_setup(tmp_path, "JoinNode") is False, content


def test_a_first_run_dir_is_the_install_mode_whatever_node_is_named(tmp_path):
    assert setup_boot.setup_mode(tmp_path, "JoinNode") == setup_boot.INSTALL
    assert setup_boot.setup_mode(tmp_path, "") == setup_boot.INSTALL


def test_web_setup_enabled_honours_the_two_flags():
    forced_off = types.SimpleNamespace(no_web_setup=True, setup=False)
    forced_on = types.SimpleNamespace(no_web_setup=True, setup=True)
    default = types.SimpleNamespace(no_web_setup=False, setup=False)
    assert setup_boot.web_setup_enabled(forced_off) is False
    # both flags at once: "--no-web-setup" is the stronger instruction, so it wins
    assert setup_boot.web_setup_enabled(forced_on) is False
    assert setup_boot.web_setup_enabled(types.SimpleNamespace(no_web_setup=False, setup=True)) is True
    assert setup_boot.web_setup_enabled(default) is setup_boot._interactive_stdout()


def test_a_redirected_or_absent_terminal_takes_the_installer_path():
    class NotATerminal:
        def isatty(self):
            return False

    class ATerminal:
        def isatty(self):
            return True

    class Broken:
        def isatty(self):
            raise OSError("no isatty here")

    assert setup_boot._interactive_stdout(NotATerminal()) is False
    assert setup_boot._interactive_stdout(ATerminal()) is True
    assert setup_boot._interactive_stdout(Broken()) is False


# -------------------------------------------------- no boot may build the console from a file

@pytest.mark.asyncio
async def test_a_configured_install_never_reaches_the_application_factory(monkeypatch, tmp_path):
    """The by-construction guarantee: with main.yaml present, nothing is minted and nothing built."""
    (tmp_path / "main.yaml").write_text("node: TestNode\n", encoding="utf-8")
    reached = []

    def factory(*args, **kwargs):
        reached.append(True)
        raise AssertionError("the setup application factory must not be reached")

    from services.webservice.setup import app as setup_app
    monkeypatch.setattr(setup_app, "create_setup_app", factory)
    monkeypatch.setattr(setup_service, "create_setup_app", factory)

    with pytest.raises(setup_boot.SetupWindowClosed) as refused:
        await setup_boot.start_setup_console(config_dir=tmp_path)
    assert "main.yaml" in str(refused.value)
    assert reached == []

    with pytest.raises(setup_boot.SetupWindowClosed):
        await setup_boot.run_setup_console(config_dir=tmp_path)
    assert reached == []


@pytest.mark.asyncio
async def test_an_unconfigured_install_does_build_the_console(tmp_path):
    """The positive control for the test above: the same call, one file different, builds it."""
    service = await setup_boot.start_setup_console(node="TestNode", config_dir=tmp_path,
                                                   open_browser=False, announce=False,
                                                   port=_free_port())
    try:
        assert service.window.spent is False
        assert service.listen == "127.0.0.1"
        assert service.bound is True
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_the_console_refuses_the_joining_state_of_a_NODE_THAT_HAS_A_BLOCK(monkeypatch,
                                                                               tmp_path):
    """The SECOND refusal, widened with the launcher: this node is configured, so nothing is built."""
    (tmp_path / "main.yaml").write_text("guild_id: 7\n", encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text("TestNode:\n  listen_port: 10042\n", encoding="utf-8")
    reached = []

    def factory(*args, **kwargs):
        reached.append(True)
        raise AssertionError("the setup application factory must not be reached")

    from services.webservice.setup import app as setup_app
    monkeypatch.setattr(setup_app, "create_setup_app", factory)
    monkeypatch.setattr(setup_service, "create_setup_app", factory)

    with pytest.raises(setup_boot.SetupWindowClosed) as refused:
        await setup_boot.start_setup_console(node="TestNode", config_dir=tmp_path,
                                             open_browser=False, announce=False)
    assert "nodes.yaml" in str(refused.value)
    assert reached == []


@pytest.mark.asyncio
async def test_the_console_does_build_for_a_node_absent_from_nodes_yaml(tmp_path):
    """The positive control for the refusal above: the same directory, a node with no block."""
    (tmp_path / "main.yaml").write_text("guild_id: 7\n", encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text("NodeOne:\n  listen_port: 10042\n", encoding="utf-8")
    service = await setup_boot.start_setup_console(node="JoinNode", config_dir=tmp_path,
                                                   open_browser=False, announce=False,
                                                   port=_free_port())
    try:
        assert service.mode == setup_boot.JOIN
        assert service.bound is True
        assert service.app.state.webui_setup_mode == setup_boot.JOIN
        assert service.app.state.webui_setup_node == "JoinNode"
    finally:
        await service.stop()


def test_the_factory_refuses_a_joining_mode_that_names_no_node(tmp_path):
    """``mode=JOIN`` names ONE node: without a name the factory refuses rather than render Done."""
    from services.webservice.setup.app import SetupModeRefused, create_setup_app
    from services.webservice.setup.window import SetupWindow

    for empty in ("", "   "):
        with pytest.raises(SetupModeRefused) as refused:
            create_setup_app(SetupWindow(code=CODE, wrong_delay=0.0), config_dir=tmp_path,
                             node=empty, mode=setup_boot.JOIN)
        assert "no node name" in str(refused.value)

    # the positive control: a NAMED joining mode builds, and INSTALL needs no name at all
    app = create_setup_app(SetupWindow(code=CODE, wrong_delay=0.0), config_dir=tmp_path,
                           node="JoinNode", mode=setup_boot.JOIN)
    assert app.state.webui_setup_mode == setup_boot.JOIN
    create_setup_app(SetupWindow(code=CODE, wrong_delay=0.0), config_dir=tmp_path,
                     node="", mode=setup_boot.INSTALL)


# ------------------------------------------------------------- the console on a real socket

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.asyncio
async def test_the_console_binds_a_real_socket_and_answers_over_it(tmp_path):
    """A real uvicorn server on a real loopback port: the shipped path, not an in-process double."""
    port = _free_port()
    service = await setup_boot.start_setup_console(node="TestNode", config_dir=tmp_path,
                                                   open_browser=False, announce=False, port=port)
    try:
        assert service.bound is True, "the readiness check must see the bound port"
        base = f"http://{service.listen}:{port}"
        async with httpx.AsyncClient() as client:
            health = await client.get(f"{base}/healthz")
            assert health.status_code == 200 and health.json() == {"status": "ok"}
            gate = await client.get(f"{base}{SETUP_PATH}")
            assert gate.status_code == 200
            # the code in the URL is exchanged, and the console keeps serving afterwards
            accepted = await client.get(f"{base}{SETUP_PATH}", params={"code": service.window.code})
            assert accepted.status_code == 303
            hello = await client.get(f"{base}{SETUP_PATH}")
            assert hello.status_code == 200
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_the_console_refuses_a_non_loopback_bind_before_it_builds_anything(tmp_path):
    with pytest.raises(Exception) as refused:
        setup_service.SetupService(setup_service.SetupWindow(code=CODE, wrong_delay=0.0),
                                   config_dir=tmp_path, listen="0.0.0.0")
    assert "loopback" in str(refused.value)


@pytest.mark.asyncio
async def test_finishing_the_tour_releases_the_console(tmp_path):
    """The finish path the tour will use: the console stops and the launcher may continue."""
    port = _free_port()
    service = await setup_boot.start_setup_console(node="TestNode", config_dir=tmp_path,
                                                   open_browser=False, announce=False, port=port)
    running = asyncio.ensure_future(service.run())
    await asyncio.sleep(0.05)
    service.finish()
    await asyncio.wait_for(running, timeout=5)
    assert service._task is None, "the server task must be dropped once the console has stopped"


@pytest.mark.asyncio
async def test_a_taken_port_is_reported_and_does_not_raise(tmp_path):
    """A bind failure is a log line and a False, never a SystemExit out of the boot path."""
    port = _free_port()
    first = await setup_boot.start_setup_console(node="TestNode", config_dir=tmp_path,
                                                 open_browser=False, announce=False, port=port)
    try:
        second = await setup_boot.start_setup_console(node="TestNode", config_dir=tmp_path,
                                                      open_browser=False, announce=False, port=port)
        try:
            assert second.bound is False
        finally:
            await second.stop()
    finally:
        await first.stop()


# ------------------------------------------------------- the normal console is untouched

def _node(config_dir) -> types.SimpleNamespace:
    return types.SimpleNamespace(master=True, config_dir=str(config_dir))


@pytest.fixture
def normal_install(tmp_path: Path):
    """A configured install: bot.yaml with one role and a webservice.yaml config for the shell."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    config = {
        "listen": "127.0.0.1",
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {
            "public_base_url": "http://localhost:9876",
            "local": {"enabled": True, "users": [
                {"username": "specialk",
                 "password_hash": local_auth.hash_password(PASSWORD,
                                                          iterations=local_auth.MIN_ITERATIONS),
                 "roles": ["Admin"]},
            ]},
        },
    }
    return tmp_path, config


@pytest.mark.asyncio
async def test_a_normal_boot_installs_the_identity_backend_and_refuses_anonymous(normal_install):
    config_dir, config = normal_install
    app = create_app()
    install_shell(app, node=_node(config_dir), config=config)

    manager = app.state.webui_auth
    assert manager is not None, "the identity layer must be installed on a normal boot"
    assert manager.backend("local") is not None, "auth.local from webservice.yaml must be installed"
    assert session_identity_resolver_installed()

    async with asgi_client(app) as client:
        # a data caller keeps the JSON refusal it has always had
        refused = await client.get("/")
        assert refused.status_code == 403
        assert "Not authorized" in refused.json()["detail"]
        # a browser is sent to the door, which is exactly what the setup app does NOT serve
        browser = await client.get("/", headers={"accept": "text/html"})
        assert browser.status_code == 303
        assert browser.headers["location"].startswith(auth_routes.LOGIN_PATH)


@pytest.mark.asyncio
async def test_a_normal_boot_still_signs_a_user_in(normal_install):
    config_dir, config = normal_install
    app = create_app()
    install_shell(app, node=_node(config_dir), config=config)
    async with asgi_client(app) as client:
        page = await client.get(auth_routes.LOGIN_PATH, headers={"accept": "text/html"})
        assert page.status_code == 200
        token = re.search(r'name="_csrf_token" value="([^"]+)"', page.text)
        assert token, "the login form must carry the session's CSRF token"
        signed_in = await client.post(auth_routes.LOGIN_PATH,
                                      data={"username": "specialk", "password": PASSWORD,
                                            session.CSRF_FIELD: token.group(1)})
        assert signed_in.status_code == 303
        assert (await client.get("/")).status_code == 200


@pytest.mark.asyncio
async def test_a_configured_install_serves_no_setup_route_at_all(normal_install):
    """The last code, the right code, any code: /setup does not exist on a configured install."""
    config_dir, config = normal_install
    app = create_app()
    install_shell(app, node=_node(config_dir), config=config)
    async with asgi_client(app) as client:
        for params in ({}, {"code": CODE}, {"code": "ZZZZ-9999"}):
            assert (await client.get(SETUP_PATH, params=params)).status_code == 404


@pytest.mark.asyncio
async def test_the_setup_console_install_does_not_touch_the_normal_identity_layer(tmp_path):
    """Building the setup app leaves the three process-global resolvers exactly where they were."""
    app = setup_service.create_setup_app(
        setup_service.SetupWindow(code=CODE, wrong_delay=0.0),
        config_dir=tmp_path, listen="127.0.0.1")
    assert getattr(app.state, "webui_auth", None) is None
    assert not session_identity_resolver_installed()


def session_identity_resolver_installed() -> bool:
    """Whether an identity backend has installed the process-global resolvers.

    ``install_auth`` is the only caller of the three ``set_*_resolver`` functions, so this answers
    "did an identity layer install on this application" without reading the app's state.
    """
    return permissions._ROLE_RESOLVER is not None


def test_the_readiness_check_gives_up_instead_of_hanging():
    """A server that never reports its port bound must not block the boot forever."""
    class Never:
        started = False

    async def run() -> bool:
        service = setup_service.SetupService.__new__(setup_service.SetupService)
        service.server = Never()  # type: ignore[assignment] - a server double that never binds
        service._task = asyncio.create_task(asyncio.sleep(3600))
        service._ready_timeout = 0.05
        try:
            return await service._await_ready()
        finally:
            service._task.cancel()

    assert asyncio.run(run()) is False
