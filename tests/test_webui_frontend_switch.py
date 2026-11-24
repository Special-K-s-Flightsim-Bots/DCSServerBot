"""The ``webservice.yaml`` ``frontend`` switch: run the service as a REST API only.

With ``frontend: false`` (and with the key absent -- the default) the service builds its app WITHOUT
the shell: no page route, no ``/auth/*``, no session middleware, no shell assets, no registrar, and
every UI path answers 404 (not 403 -- there is nothing to authorise). With ``frontend: true`` the
shell is installed exactly as before. The same REST request set is compared across both boots
(statuses and payloads identical); an enabled-but-unconfigured Discord login refuses the shell install
only with the frontend on; the UI background work does not run in the off mode; and the config
plumbing (schema, boolean, a loud refusal of a non-boolean, the sample) is pinned."""
from __future__ import annotations

import asyncio
import logging
import types
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter, Depends, HTTPException

from services.webservice import upgrade as upgrade_signal
from services.webservice.shell import frontend_enabled

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "services" / "webservice" / "schemas" / "webservice_schema.yaml"
SAMPLE_PATH = REPO_ROOT / "samples" / "services" / "webservice.yaml"

#: A REST surface shaped like ``plugins/restapi``: each route carries its OWN guard (a route-level
#: dependency), which is what the access gate recognises as the legacy, self-guarded surface.
GUARDED = "/instance/list"
SECURED = "/secured"
UNDECLARED = "/undeclared"


# ---------------------------------------------------------------------------------------- harness

@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """The schema validator's ``unique_port`` reads ``COMMAND_LINE_ARGS.node``.

    The test interpreter's program is not one of the bot's own entry points, so the bot's parser
    takes its defaults and the ``-n/--node`` attribute never exists. pykwalify loads
    ``core/utils/validators.py`` as a FRESH extension module, so the only patch that reaches it is
    the one on ``core.commandline`` itself.
    """
    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


def service_config(config_dir: Path, body: str, *, frontend=None) -> None:
    """Write a real ``config/services/webservice.yaml`` the service will read through ``read_locals``.

    ``body`` is appended as-is (already indented under ``DEFAULT``); ``frontend`` writes the switch.
    """
    lines = ["DEFAULT:", "  listen: 127.0.0.1", "  port: 9876"]
    if frontend is not None:
        lines.append(f"  frontend: {frontend}")
    if body:
        lines.append(body.rstrip("\n"))
    directory = config_dir / "services"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "webservice.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def node_for(config_dir: Path, *, validation: str = "lazy", plugins=()):
    #: A node double shaped like ``NodeImpl`` where a SERVICE reads it — including the two DCS-update
    #: hook dicts and their accessors, so a service that registers on them is exercised rather than
    #: dying on an AttributeError the real node would never raise.
    node = types.SimpleNamespace(
        config_dir=str(config_dir), config={"validation": validation},
        pool=None, apool=None, bot_version="3", sub_version="0",
        name="master", plugins=list(plugins), master=True,
        before_update={}, after_update={})

    def register_callback(what: str, name: str, func):
        (node.before_update if what == "before_dcs_update" else node.after_update)[name] = func

    def unregister_callback(what: str, name: str):
        (node.before_update if what == "before_dcs_update" else node.after_update).pop(name, None)

    node.register_callback = register_callback
    node.unregister_callback = unregister_callback
    return node



def boot(config_dir: Path, body: str = "", *, frontend=None, validation: str = "lazy"):
    """Build the REAL ``WebService`` from a real ``webservice.yaml`` on disk."""
    service_config(config_dir, body, frontend=frontend)
    from services.webservice.service import WebService
    return WebService(node_for(config_dir, validation=validation))


def rest_router() -> APIRouter:
    router = APIRouter()

    def local_only():
        return True

    async def instance_list():
        return {"servers": ["SRS-1"], "count": 1}

    async def secured():
 # a self-guarded route that REFUSES: the auth behaviour must be identical in both modes
        raise HTTPException(status_code=401, detail="Missing API key.")

    async def undeclared():
        return {"ok": True}

    router.add_api_route(GUARDED, instance_list, methods=["GET"],
                         dependencies=[Depends(local_only)])
    router.add_api_route(SECURED, secured, methods=["GET"],
                         dependencies=[Depends(local_only)])
    router.add_api_route(UNDECLARED, undeclared, methods=["GET"])
    return router


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver")


def route_paths(app) -> list[str]:
    from services.webservice import permissions
    return [getattr(route, "path", "") for route in permissions._walk_routes(app)]


def middleware_names(app) -> list[str]:
    return [m.cls.__name__ for m in app.user_middleware]


class FakeServer:
    """A uvicorn stand-in: ``start()`` must not bind a port, only build the app and its tasks."""
    started = False
    servers: list = []
    should_exit = False

    async def serve(self):
 # never returns on its own: the test cancels the task it lives in
        await asyncio.sleep(3600)


async def run_start(svc):
    """Call the REAL ``start()`` without the registry cascade (``Service.start``'s fast path)."""
    svc._in_registry_cascade = True
    svc.server = FakeServer()
    await svc.start()


async def cancel_tasks(svc):
    for name in ("task", "upgrade_task", "dcs_task", "warm_task", "scope_task"):
        task = getattr(svc, name, None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            setattr(svc, name, None)
    # the post-update checks the node's hook schedules: a set, not a single slot
    extra_tasks = getattr(svc, "dcs_tasks", None)
    if extra_tasks:
        for extra in list(extra_tasks):
            extra.cancel()
            with suppress(asyncio.CancelledError):
                await extra
        extra_tasks.clear()


# ------------------------------------------------------------------------ 1. the switch: no shell

@pytest.mark.asyncio
async def test_frontend_off_installs_no_shell_at_all(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="services.webservice.service.WebService"):
        svc = boot(tmp_path, frontend="false")

 # the shell is not installed: no registrar, no session middleware, no page/auth routes
    assert svc.ui is None
    assert getattr(svc.app.state, "webui_registrar", None) is None
    assert middleware_names(svc.app) == [], "the session middleware belongs to the shell"
    paths = route_paths(svc.app)
    assert "/" not in paths, "the dashboard route must not exist"
    assert not any(p.startswith("/auth/") for p in paths), "no /auth/* route may exist"
    assert "/static/shell.css" not in paths

 # every former UI path answers 404 — NOT 403: there is no route to authorise
    async with client_for(svc.app) as http:
        for path in ("/", "/logs", "/fleet", "/auth/login", "/auth/logout", "/auth/discord"):
            assert (await http.get(path)).status_code == 404, f"{path} must be a 404"

 # and the one log line names the switch and the skipped auth block
    messages = [r.getMessage() for r in caplog.records]
    assert any("frontend is disabled" in m and "auth block was skipped" in m for m in messages), \
        f"the service must log the switch: {messages}"


@pytest.mark.asyncio
async def test_frontend_on_still_installs_the_shell(tmp_path):
    """The control: an explicit ``frontend: true`` installs the shell exactly as before."""
    svc = boot(tmp_path, frontend="true")
    assert svc.ui is not None
    assert getattr(svc.app.state, "webui_registrar", None) is not None
    assert "SessionMiddleware" in middleware_names(svc.app)
    paths = route_paths(svc.app)
    assert "/" in paths and "/auth/login" in paths

    async with client_for(svc.app) as http:
        assert (await http.get("/auth/login")).status_code == 200
 # the dashboard route exists (this deployment enabled no backend, so it is gated, not 404)
        assert (await http.get("/")).status_code == 403


@pytest.mark.asyncio
async def test_the_switch_defaults_to_no_frontend_when_the_key_is_absent(tmp_path):
    """The default is ``false`` (the frontend is opt-in while it is WIP): an absent key is no shell.

    An existing configuration that never wrote ``frontend:`` gets the REST API only — no page, no
    ``/auth/*``, no session middleware, no upgrade poller — and the REST API answers normally.
    """
    svc = boot(tmp_path)  # no `frontend:` key at all
    assert svc.ui is None
    assert getattr(svc.app.state, "webui_registrar", None) is None
    assert middleware_names(svc.app) == [], "the session middleware belongs to the shell"
    paths = route_paths(svc.app)
    assert "/" not in paths, "the dashboard route must not exist"
    assert not any(p.startswith("/auth/") for p in paths), "no /auth/* route may exist"

 # the UI background work must not run either: start may not create the upgrade poller
    await run_start(svc)
    assert svc.upgrade_task is None, "an absent key must not start the UI's upgrade poller"
    assert svc.scope_task is None, "an absent key must not start the ready-time scope audit"
    await cancel_tasks(svc)

 # and the REST API keeps serving
    svc.app.include_router(rest_router())
    async with client_for(svc.app) as http:
        response = await http.get(GUARDED)
    assert response.status_code == 200
    assert response.json() == {"servers": ["SRS-1"], "count": 1}


def test_frontend_enabled_reads_the_one_key_with_a_false_default():
    assert frontend_enabled(None) is False
    assert frontend_enabled({}) is False
    assert frontend_enabled({"frontend": True}) is True
    assert frontend_enabled({"frontend": False}) is False


def _schema_default(key: str):
    """The ``default:`` the schema declares for *key* — read from the YAML, whatever the rule's layout.

    Reading the file's TEXT with a regex would tie this test to one formatting style (``frontend: {…}``
    written on a single line), so reformatting a rule — exactly what adding a ``desc:`` does — would
    break a test about DEFAULTS. The schema is YAML: read it as YAML and let the author lay it out freely.
    """
    from ruamel.yaml import YAML

    document = YAML(typ="safe").load(SCHEMA_PATH.read_text(encoding="utf-8"))

    def walk(node):
        if isinstance(node, dict):
            rule = node.get(key)
            if isinstance(rule, dict) and isinstance(rule.get("default"), bool):
                return rule["default"]           # a RULE's default, never the ``example:`` block's value
            for value in node.values():
                found = walk(value)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found is not None:
                    return found
        return None

    return walk(document)


def test_the_code_default_and_the_schema_default_agree():
    """The one rule cannot drift: the predicate on an empty block must equal the schema's default.

    The schema's ``default:`` is the single source of truth; the code default is a second copy of it.
    Read BOTH — the schema file and ``frontend_enabled({})`` — and assert they agree, so a
    later change to either one alone fails here rather than silently reverting the switch.
    """
    schema_default = _schema_default("frontend")
    assert schema_default is not None, "the schema declares no `frontend` default to compare against"
    code_default = frontend_enabled({})
    assert code_default is schema_default, (
        f"the code default ({code_default}) and the schema default ({schema_default}) disagree — "
        "the schema is the source of truth and the predicate must match it")


# --------------------------------------------------- 2. the REST API is identical in both modes

@pytest.mark.asyncio
async def test_the_rest_surface_is_identical_whether_the_frontend_is_on_or_off(tmp_path):
    """Drive the SAME request set against both boots and compare statuses and payloads.

    The REST surface is the self-guarded routes (like ``plugins/restapi``): a served route and a
    refusing one, so both the success and the auth-refusal answers are compared. ``frontend`` is
    written as a real YAML boolean — ``on``/``off`` are STRINGS in YAML 1.2 and would leave the
    frontend enabled in both boots (a comparison that then proves nothing).
    """

    async def observe(name, frontend):
        svc = boot(tmp_path / name, frontend=frontend)
        svc.app.include_router(rest_router())
        observed = {}
        async with client_for(svc.app) as http:
            for path in (GUARDED, SECURED, UNDECLARED, "/nothing/here"):
                response = await http.get(path)
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                observed[path] = (response.status_code, body)
        return observed

    on = await observe("on", "true")
    off = await observe("off", "false")
 # the REST API is the self-guarded surface: same statuses AND same payloads in both modes
    for path in (GUARDED, SECURED):
        assert on[path] == off[path], f"the REST API changed for {path}: {on[path]} != {off[path]}"
 # floor: the surface really answered (an all-404 comparison would prove nothing)
    assert on[GUARDED] == (200, {"servers": ["SRS-1"], "count": 1})
    assert on[SECURED][0] == 401 and off[SECURED][0] == 401
 # an undeclared route stays refused in both modes (the STATUS is the contract; the detail text
 # names the reason and legitimately differs: no shell vs. deny-by-default)
    assert on[UNDECLARED][0] == 403 and off[UNDECLARED][0] == 403
    assert on["/nothing/here"][0] == 404 and off["/nothing/here"][0] == 404


# ------------------------------------------- 3. a broken auth block is skipped when the frontend is off

BROKEN_AUTH = """\
  auth:
    discord:
      enabled: true
"""


@pytest.mark.asyncio
async def test_a_broken_auth_block_blocks_the_frontend_on_boot(tmp_path):
    """With the frontend on, an enabled-but-unconfigured Discord login refuses the install as before."""
    from core.services.base import ServiceInstallationError
    service_config(tmp_path, BROKEN_AUTH, frontend="true")
    from services.webservice.service import WebService
    with pytest.raises((ValueError, ServiceInstallationError)):
        WebService(node_for(tmp_path))


@pytest.mark.asyncio
async def test_a_broken_auth_block_does_not_block_the_rest_api_when_frontend_off(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="services.webservice.service.WebService"):
        svc = boot(tmp_path, BROKEN_AUTH, frontend="false")
    assert svc.ui is None

    svc.app.include_router(rest_router())
    async with client_for(svc.app) as http:
        response = await http.get(GUARDED)
    assert response.status_code == 200
    assert response.json() == {"servers": ["SRS-1"], "count": 1}

    messages = [r.getMessage() for r in caplog.records]
    assert any("frontend is disabled" in m and "auth block was skipped" in m for m in messages), \
        "the skipped auth block must be logged, or an operator will hunt a refusal that never ran"


# ------------------------------------------------------------ 4. no UI background work in off mode

@pytest.mark.asyncio
async def test_the_upgrade_poller_makes_no_node_call_when_frontend_off(tmp_path, monkeypatch):
    ticks = {"count": 0}

    async def spy_tick(self, nodes):
        ticks["count"] += 1
        return ()

    monkeypatch.setattr(upgrade_signal.UpgradePoller, "tick", spy_tick)

    off = boot(tmp_path / "off", frontend="false")
    await run_start(off)
    assert off.upgrade_task is None, "frontend: false must not start the UI's upgrade poller"
    assert off.scope_task is None, "frontend: false must not start the ready-time scope audit"
    await asyncio.sleep(0.05)
    assert ticks["count"] == 0, "no node may be polled for a console nobody serves"
    await cancel_tasks(off)

 # the control: with the frontend on the poller IS the background task
    on = boot(tmp_path / "on", frontend="true")
    await run_start(on)
    assert on.upgrade_task is not None, "the frontend still starts its upgrade poller"
    for _ in range(20):
        if ticks["count"]:
            break
        await asyncio.sleep(0.01)
    assert ticks["count"] >= 1
    await cancel_tasks(on)


# ------------------------------------------------------------------- 5. the config plumbing

def _validate(text: str, tmp_path) -> None:
    """Validate *text* against the service schema with a stand-in for the one port rule."""
    from pykwalify.core import Core
    extension = tmp_path / "ext.py"
    extension.write_text("def unique_port(value, rule_obj, path):\n    return True\n",
                         encoding="utf-8")
    source = tmp_path / "webservice.yaml"
    source.write_text(text, encoding="utf-8")
    Core(source_file=str(source), schema_files=[str(SCHEMA_PATH)],
         extensions=[str(extension)]).validate()


def test_the_sample_documents_the_switch_and_validates(tmp_path):
    text = SAMPLE_PATH.read_text(encoding="utf-8")
    assert "frontend:" in text
    _validate(text, tmp_path)


@pytest.mark.parametrize("value", ['"yes"', '"false"', "1", "[]"])
def test_the_schema_refuses_a_non_boolean_frontend(value, tmp_path):
    from pykwalify.errors import SchemaError
    body = f"DEFAULT:\n  listen: 127.0.0.1\n  port: 9876\n  frontend: {value}\n"
    with pytest.raises(SchemaError):
        _validate(body, tmp_path)


def test_the_schema_accepts_a_boolean_frontend(tmp_path):
    _validate("DEFAULT:\n  listen: 127.0.0.1\n  port: 9876\n  frontend: false\n", tmp_path)


@pytest.mark.asyncio
async def test_an_invalid_frontend_value_is_refused_at_startup(tmp_path):
    """Strict validation: a string for a boolean key stops the service from starting."""
    from core.services.base import ServiceInstallationError
    service_config(tmp_path, "", frontend='"yes"')
    from services.webservice.service import WebService
    with pytest.raises(ServiceInstallationError):
        WebService(node_for(tmp_path, validation="strict"))


# --------------------------------------------------------------------- 6. the wiring pin

def test_the_service_reads_the_one_predicate_at_both_call_sites():
    """Pinned structurally: the app factory and the background-work gate read ``frontend_enabled``.

    ``services.webservice.service`` imports ``core`` (psycopg, lupa, the bot's argparse), so this
    is a source pin rather than an import — the two call sites are what make the switch a switch.
    """
    import ast
    source = (REPO_ROOT / "services" / "webservice" / "service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    service = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef) and n.name == "WebService")
    functions = {item.name: item for item in service.body
                 if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def called(function):
        names = set()
        for n in ast.walk(function):
            if isinstance(n, ast.Call):
                target = n.func
                names.add(target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", ""))
        return names

    assert "frontend_enabled" in called(functions["_create_app"]), \
        "the app factory must gate the shell install on the switch"
    assert "frontend_enabled" in called(functions["start"]), \
        "start() must gate the UI background work on the switch"
    assert "install_shell" in called(functions["_create_app"])
