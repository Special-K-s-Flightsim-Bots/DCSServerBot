"""A plugin's web UI pages must survive a WebService restart.

``PluginManager`` registered plugin pages once, when ``load_plugins()`` ran, but ``WebService.stop()``
drops ``self.app``/``self.ui`` and ``start()`` builds a FRESH, empty registrar -- so a WebService that
starts after the plugin load or restarts serves no plugin page, silently. The fix is a module-level
contributor registry (``services.webservice.contributors``): ``PluginManager`` records the
contributors before it looks for a registrar, and ``install_shell()`` replays them onto whatever
registrar it just built. ``core.plugin_manager`` cannot be imported here; the registry/replay seam is
driven for real and the two call sites are pinned structurally."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import asgi_client, contributors, create_app, install_shell, permissions, simple_router

PLUGIN_MANAGER_PY = Path(__file__).resolve().parent.parent / "core" / "plugin_manager.py"


def a_plugin_page(registrar):
    """What `plugins/<name>/webui.py::register_pages(registrar)` does (one page, one nav item)."""
    registrar.register_pages("mission", simple_router("/mission/overview", "overview"),
                             nav=[], capabilities={"/mission/overview": permissions.PUBLIC})


@pytest.mark.asyncio
async def test_a_plugin_page_is_reachable_again_after_the_app_is_rebuilt():
    """The restart path: register while plugins are loaded, then `stop()`/`start()` (a fresh app +
    a fresh registrar), and the page must be reachable again."""
    first_app = create_app()
    first = install_shell(first_app, node=None, config={})
    contributors.set_contributors({"mission": a_plugin_page})
    contributors.replay(first)
    async with asgi_client(first_app) as client:
        assert (await client.get("/mission/overview")).status_code == 200

 # stop: self.app = None; start: a brand new application object
    second_app = create_app()
    install_shell(second_app, node=None, config={})
    async with asgi_client(second_app) as client:
        response = await client.get("/mission/overview")
    assert response.status_code == 200, \
        "the plugin page is silently missing after the WebService restarted"


@pytest.mark.asyncio
async def test_pages_registered_before_the_web_service_exists_appear_when_it_starts():
    """On a fresh install the service config (and therefore the WebService) can appear AFTER
    ``load_plugins()`` ran. The contributors are recorded without a registrar and replayed by the first
    shell installed."""
    contributors.set_contributors({"mission": a_plugin_page})   # no WebService yet
    app = create_app()
    install_shell(app, node=None, config={})
    async with asgi_client(app) as client:
        response = await client.get("/mission/overview")
    assert response.status_code == 200, \
        "a plugin page recorded while no WebService existed never became reachable"


@pytest.mark.asyncio
async def test_an_unloaded_plugin_is_not_replayed_back():
    """`unload_plugins()` removes the pages of the plugins it unloads; a later rebuild must not
    resurrect them from the registry."""
    contributors.set_contributors({"mission": a_plugin_page})
    install_shell(create_app(), node=None, config={})

    assert contributors.unregister_contributor("mission") is True    # unload_plugins()

    app = create_app()
    registrar = install_shell(app, node=None, config={})
    assert "mission" not in registrar.owners
    async with asgi_client(app) as client:
        assert (await client.get("/mission/overview")).status_code == 404


def test_replaying_twice_on_one_registrar_does_not_register_twice():
    """The replay is idempotent: an owner that is already registered is skipped, so reinstalling a
    shell (or replaying by hand) cannot hit the registrar's "already registered" refusal."""
    app = create_app()
    registrar = install_shell(app, node=None, config={})
    contributors.set_contributors({"mission": a_plugin_page})
    assert contributors.replay(registrar) == ["mission"]
    assert contributors.replay(registrar) == []
    assert registrar.owners.count("mission") == 1


def test_a_failing_contributor_does_not_stop_the_shell():
    """One broken plugin must not take the shell (and therefore the bot) down with it."""
    def broken(registrar):
        raise RuntimeError("broken plugin")

    app = create_app()
    contributors.set_contributors({"broken": broken, "mission": a_plugin_page})
    registrar = install_shell(app, node=None, config={})
    assert "mission" in registrar.owners
    assert "broken" not in registrar.owners


# structural pins for the registry/replay seam

def _functions(path: Path, class_name: str) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {item.name: item
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == class_name
            for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}


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


def test_plugin_manager_records_the_contributors_and_drops_them_on_unload():
    """`core/plugin_manager.py` cannot be imported in this environment (see the module docstring),
    so the two call sites that make the registry live are pinned structurally. If this fails,
    re-read `_load_webui_pages()`/`_unload_webui_pages()` by hand: the failure mode it guards is
    plugin pages that are registered into a live registrar only, and therefore lost on restart."""
    functions = _functions(PLUGIN_MANAGER_PY, "PluginManager")
    assert {"_load_webui_pages", "_unload_webui_pages"} <= set(functions), \
        "PluginManager no longer has the expected web UI methods - re-check by hand"
    assert "set_contributors" in _called_names(functions["_load_webui_pages"]), \
        "_load_webui_pages() must record the contributors (that is what a fresh registrar replays)"
    assert "unregister_contributor" in _called_names(functions["_unload_webui_pages"]), \
        "_unload_webui_pages() must drop them, or a rebuilt app resurrects an unloaded plugin"
