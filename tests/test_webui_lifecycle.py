"""Lifecycle: idempotency, the rebuilt application, and the wiring in the service.

``install_shell()``/``start()`` twice and ``stop()``/``start()`` once must leave the registrar's route
and capability counts unchanged. ``WebService`` creates the app in ``__init__`` and again in
``start()`` whenever ``self.app`` is falsy, and ``stop()`` sets it to ``None``; a shell installed once
in ``__init__`` would be gone after a master/agent takeover while anything registered per plugin load
came back -- so the shell is installed from ``start()`` through ``WebService._create_app()``.

The test interpreter has no psycopg/lupa, so ``services.webservice.service`` cannot be imported here;
the service's two call sites are pinned structurally and the app factory it uses is driven for real."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import asgi_client, create_app, install_shell, permissions, simple_router

SERVICE_PY = Path(__file__).resolve().parent.parent / "services" / "webservice" / "service.py"
SHELL_PY = Path(__file__).resolve().parent.parent / "services" / "webservice" / "shell.py"


def _counts(registrar):
    return (registrar.route_count, registrar.capability_count, len(registrar.nav),
            tuple(registrar.owners))


def test_install_shell_is_idempotent_on_the_same_app():
    app = create_app()
    first = install_shell(app, node=None, config={})
    second = install_shell(app, node=None, config={})
    assert first is second, "a second install must reuse the shell, not layer a second one"
    assert _counts(first) == _counts(second)


@pytest.mark.asyncio
async def test_rebuilding_the_app_keeps_the_counts_unchanged():
    """`stop()` drops the app, `start()` builds a new one (what a master/agent switch does).
    Replaying the same contributor registration — what a plugin's webui.py does on the new app —
    must land on the same numbers, with no duplicate and nothing lost."""
    first_app = create_app()
    first = install_shell(first_app, node=None, config={})
    first.register_pages("probe", simple_router("/probe/one"),
                        capabilities={"/probe/one": permissions.PUBLIC})
    baseline = _counts(first)

 # stop: self.app = None; start: a brand new app object with a brand new registrar
    first_app = None
    second_app = create_app()
    second = install_shell(second_app, node=None, config={})
    second.register_pages("probe", simple_router("/probe/one"),
                          capabilities={"/probe/one": permissions.PUBLIC})

    assert _counts(second) == baseline
    assert baseline[0] >= 2 and baseline[1] >= 2, "floor: the counts must not be trivially zero"
    async with asgi_client(second_app) as client:
        assert (await client.get("/probe/one")).status_code == 200


def test_registrar_state_is_per_app_not_global():
    """Two applications must not share registrations: a module-level registry would leak a
    contributor (and its capability declarations) from one app object into the next."""
    first = install_shell(create_app(), node=None, config={})
    first.register_pages("probe", simple_router("/probe/one"),
                        capabilities={"/probe/one": permissions.PUBLIC})
    second = install_shell(create_app(), node=None, config={})
    assert "probe" not in second.owners
    assert second.capability_for(path="/probe/one") is None


# ------------------------------------------------------------------- structural pins

def _class_functions(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "WebService":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[item.name] = item
    return out


def _called_names(function: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                names.add(target.attr)
            elif isinstance(target, ast.Name):
                names.add(target.id)
    return names


def test_webui_service_wiring_pin():
    """`WebService` builds the app through ONE factory, and both app-creation paths use it.

    Pinned structurally because `services.webservice.service` cannot be imported in this
    environment (see the module docstring). If this fails, re-read `service.py` by hand before
    changing the pin: the failure mode it guards is a shell that is installed in `__init__` only
    and is therefore missing after the first master/agent switch.
    """
    functions = _class_functions(SERVICE_PY)
    assert {"__init__", "start", "stop", "_create_app"} <= set(functions), \
        "WebService no longer has the expected methods - re-check the lifecycle by hand"
    assert "install_shell" in _called_names(functions["_create_app"]), \
        "_create_app() must install the shell (that is the seam after a rebuild)"
    assert "_create_app" in _called_names(functions["__init__"]), \
        "__init__ must build the app through _create_app()"
    assert "_create_app" in _called_names(functions["start"]), \
        "start() must build the app through _create_app() after stop() dropped it"
    drops_app = any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Attribute) and target.attr == "app"
                for target in node.targets)
        and isinstance(node.value, ast.Constant) and node.value.value is None
        for node in ast.walk(functions["stop"])
    )
    assert drops_app, \
        "stop() must still set self.app = None (that is what makes the rebuild necessary)"

    source = SERVICE_PY.read_text(encoding="utf-8")
    assert 'on_event("startup")' not in source and "on_event('startup')" not in source, \
        'lifespan="off" means FastAPI startup events never fire - no init may live in one'
    assert 'lifespan="off"' in source, "the pin above assumes lifespan is still off"


def test_shell_app_factory_attaches_the_gate_at_construction():
    source = SHELL_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    factory = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef) and node.name == "create_app")
    fastapi_call = next(node for node in ast.walk(factory)
                        if isinstance(node, ast.Call) and
                        getattr(node.func, "id", None) == "FastAPI")
    keywords = {kw.arg for kw in fastapi_call.keywords}
    assert "dependencies" in keywords, \
        "the access gate must be a constructor-level dependency (routes added before it would " \
        "never see a later one)"
