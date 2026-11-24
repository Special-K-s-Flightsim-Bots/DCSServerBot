"""The registrar: registration, unregistration and the reservation of the shell's own paths.

register -> unregister -> 404 -> re-register -> the NEW handler answers; two contributors claiming one
path is refused, loudly; and the registrar is the enumeration source of truth, never ``app.routes``."""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.routing import Mount

from conftest import asgi_client, permissions, simple_router

SRC_DIR = Path(__file__).resolve().parent.parent / "services" / "webservice"


def _register_probe(registrar, owner: str, path: str, body: str):
    return registrar.register_pages(owner, simple_router(path, body),
                                    capabilities={path: permissions.PUBLIC})


# ---------------------------------------------------------------- acceptance 1: stale routes

@pytest.mark.asyncio
async def test_register_unregister_reregister_serves_the_new_handler(shell_app):
    app, registrar = shell_app
    async with asgi_client(app) as client:
        _register_probe(registrar, "probe", "/probe/one", "v1")
        assert (await client.get("/probe/one")).json() == {"body": "v1", "path": "/probe/one"}

        assert registrar.unregister_owner("probe") is True
        assert (await client.get("/probe/one")).status_code == 404

        _register_probe(registrar, "probe", "/probe/one", "v2")
        response = await client.get("/probe/one")
        assert response.status_code == 200
        assert response.json()["body"] == "v2", "a re-registration must serve the new handler"


@pytest.mark.asyncio
async def test_unregister_removes_exactly_the_object_its_own_include_added(shell_app):
    """The mechanism, not just the effect: `include_router` returns None and appends ONE wrapper,
    so removal has to be a snapshot/diff by identity. The tree's old idiom
    (`for route in r.routes: if route in app.routes: app.routes.remove(route)`) removes nothing
    on the pinned FastAPI and the stale handler keeps answering."""
    app, registrar = shell_app
    registration = _register_probe(registrar, "probe", "/probe/one", "v1")
    assert registration.included, "the registrar must remember the object its include appended"
    for route in registration.included:
        assert any(route is r for r in app.routes)

    registrar.unregister_owner("probe")
    for route in registration.included:
        assert not any(route is r for r in app.routes)


@pytest.mark.asyncio
async def test_unregister_unknown_owner_is_a_no_op(shell_app):
    _app, registrar = shell_app
    assert registrar.unregister_owner("never-registered") is False


# ---------------------------------------------------- acceptance 4: collisions are refused

@pytest.mark.asyncio
async def test_two_contributors_claiming_one_path_is_refused(shell_app):
    app, registrar = shell_app
    _register_probe(registrar, "first", "/probe/shared", "first")
    with pytest.raises(Exception) as excinfo:
        _register_probe(registrar, "second", "/probe/shared", "second")
    assert "already claimed by 'first'" in str(excinfo.value)
    async with asgi_client(app) as client:
        assert (await client.get("/probe/shared")).json()["body"] == "first"
    assert "second" not in registrar.owners


def test_registered_capability_collision_is_refused(shell_app):
    _app, registrar = shell_app
    registrar.register_pages("first", simple_router("/probe/a"), capabilities={"/probe/a": permissions.PUBLIC})
    with pytest.raises(Exception) as excinfo:
        registrar.register_pages("second", simple_router("/probe/b"),
                                 capabilities={"/probe/a": permissions.PUBLIC})
    assert "already claimed by 'first'" in str(excinfo.value)


def test_registering_the_same_owner_twice_is_refused(shell_app):
    _app, registrar = shell_app
    _register_probe(registrar, "probe", "/probe/one", "v1")
    with pytest.raises(Exception) as excinfo:
        _register_probe(registrar, "probe", "/probe/two", "v2")
    assert "already registered" in str(excinfo.value)


def test_unknown_capability_is_refused_at_registration_time(shell_app):
    _app, registrar = shell_app
    with pytest.raises(Exception) as excinfo:
        registrar.register_pages("probe", simple_router("/probe/one"),
                                 capabilities={"/probe/one": "not-a-declared-capability"})
    assert "unknown capability" in str(excinfo.value)


@pytest.mark.parametrize("path", ["/", "/login", "/logout", "/static", "/static/shell.css",
                                  "/auth/login", "/auth/callback"])
def test_reserved_paths_cannot_be_claimed_by_a_contributor(shell_app, path):
    _app, registrar = shell_app
    with pytest.raises(Exception) as excinfo:
        registrar.register_pages("probe", simple_router(path), capabilities={path: permissions.PUBLIC})
    assert "reserved" in str(excinfo.value)


def test_nav_item_with_an_unknown_capability_is_refused(shell_app):
    from services.webservice.registry import NavItem
    _app, registrar = shell_app
    with pytest.raises(Exception) as excinfo:
        registrar.register_pages("probe", simple_router("/probe/one"),
                                 nav=[NavItem("Probe", "/probe/one", "nope", "Operations", "grid")],
                                 capabilities={"/probe/one": permissions.PUBLIC})
    assert "unknown capability" in str(excinfo.value)


# --------------------------------------------- acceptance 5: the registrar is the source of truth

@pytest.mark.asyncio
async def test_enumeration_reads_the_registrar_never_app_routes(shell_app):
    """`app.routes` hides the routes of an included router (one `_IncludedRouter` wrapper) and the
    frontend routes (``app.router._frontend_routes``), so a pin that walks it can pass having
    examined nothing. The registrar counts them; `app.routes` cannot see them."""
    app, registrar = shell_app
    registrar.register_pages("probe", simple_router("/probe/one"),
                            capabilities={"/probe/one": permissions.PUBLIC})
    registrar.register_pages("probe2", simple_router("/probe/two"),
                             capabilities={"/probe/two": permissions.PUBLIC})

 # floor: the registrar sees the contributors' routes and the shell's asset route
    assert registrar.route_count >= 3
    assert registrar.capability_count >= 3          # /probe/one, /probe/two, /static*
 # ``actions`` and ``apply`` are the console's two WRITE surfaces (pages/actions.py, config_apply.py),
 # which register their own routes under their own owners: the shell installs them beside its pages so
 # a path claimed twice is refused at registration time. They are real owners of this application,
 # not probes.
    assert set(registrar.owners) == {"shell", "actions", "apply", "probe", "probe2"}
    assert {r.path for r in registrar.routes if getattr(r, "path", None)} >= {
        "/probe/one", "/probe/two", "/static"}

    direct_paths = {getattr(route, "path", None) for route in app.routes}
    assert "/probe/one" not in direct_paths, "app.routes is blind to included routers"
    assert "/probe/two" not in direct_paths, "app.routes is blind to included routers"
    assert "/static" not in direct_paths, "app.routes is blind to frontend routes"


def test_the_shell_owns_assets_through_frontend_and_mounts_nothing(shell_app):
    """One asset owner. A `StaticFiles` mount bypasses the access gate and a second mount on the
    same path is silently dead, so the shell must not create one."""
    app, registrar = shell_app
    assert not [r for r in app.routes if isinstance(r, Mount)], "the shell must not mount anything"
    assert registrar.capability_for(path="/static/shell.css") == permissions.PUBLIC
    assert SRC_DIR.joinpath("static", "shell.css").is_file()


def test_asset_path_without_a_directory_is_refused(shell_app):
    _app, registrar = shell_app
    with pytest.raises(Exception) as excinfo:
        registrar.register_assets("/assets", SRC_DIR / "does-not-exist")
    assert "does not exist" in str(excinfo.value)
