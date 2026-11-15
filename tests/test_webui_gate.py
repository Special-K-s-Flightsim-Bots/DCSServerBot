"""The access gate: one app-level, deny-by-default dependency, and every attempt to bypass it.

An undeclared route is refused (403), a floor count over the registrar, and a positive control that
proves the gate runs (declaring the route turns the same request from 403 into 200). The gate is
attached in :func:`services.webservice.shell.create_app` AT CONSTRUCTION, because FastAPI copies
application-level dependencies into every ``APIRoute`` created afterwards -- a route added before the
app-level dependency never sees it, and a per-route dependency cannot express "no declaration =
refuse" at all."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.staticfiles import StaticFiles

from conftest import stub_identity, asgi_client, create_app, install_shell, permissions, simple_router

from fastapi import APIRouter, Request


# ------------------------------------------------------------- acceptance 2: deny by default

@pytest.mark.asyncio
async def test_undeclared_route_is_refused_and_declaring_it_passes(shell_app):
    """The positive control: the same route and the same request change answer purely because of
    the capability declaration, so the 403 is the gate and not a missing route."""
    app, registrar = shell_app
    async with asgi_client(app) as client:
        registrar.register_pages("probe", simple_router("/probe/undeclared"))
        response = await client.get("/probe/undeclared")
        assert response.status_code == 403
        assert "deny by default" in response.json()["detail"]

        registrar.unregister_owner("probe")
        registrar.register_pages("probe", simple_router("/probe/undeclared"),
                                 capabilities={"/probe/undeclared": permissions.PUBLIC})
        assert (await client.get("/probe/undeclared")).status_code == 200


@pytest.mark.asyncio
async def test_floor_over_the_registrar_and_the_gate_running(shell_app):
    """A floor count over the registrar, plus the two directions of the gate: three contributing
    routes, two allowed (declared public) and one refused (undeclared)."""
    app, registrar = shell_app
    registrar.register_pages("probe", simple_router("/probe/a"),
                            capabilities={"/probe/a": permissions.PUBLIC})
    registrar.register_pages("probe2", simple_router("/probe/b"),
                             capabilities={"/probe/b": permissions.PUBLIC})
    registrar.register_pages("probe3", simple_router("/probe/c"))
    assert registrar.route_count >= 3, "floor: the registrar owns at least the probe routes"
    assert registrar.capability_count >= 2

    async with asgi_client(app) as client:
        assert (await client.get("/probe/a")).status_code == 200
        assert (await client.get("/probe/b")).status_code == 200
        assert (await client.get("/probe/c")).status_code == 403


@pytest.mark.asyncio
async def test_gate_is_attached_at_construction_not_retrofitted(shell_app):
    """A route added to the app *before* the shell is installed still carries the gate, because
    the gate is a constructor-level dependency. Retrofitting (`app.router.dependencies.append`)
    would leave that route unguarded."""
    app = create_app()
    app.add_api_route("/early", lambda: {"ok": True}, methods=["GET"])
    install_shell(app, node=None, config={})
    async with asgi_client(app) as client:
        assert (await client.get("/early")).status_code == 403


@pytest.mark.asyncio
async def test_gate_fails_closed_when_the_shell_is_not_installed():
    app = create_app()
    app.add_api_route("/undeclared", lambda: {"ok": True}, methods=["GET"])
    async with asgi_client(app) as client:
        response = await client.get("/undeclared")
    assert response.status_code == 403
    assert "not installed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unmatched_path_is_a_404_not_a_403(shell_app):
    """No route matches -> the dependency never runs -> 404. Recorded so the 403s above are
    understood as authorisation decisions, not as a router that answers everything."""
    app, _registrar = shell_app
    async with asgi_client(app) as client:
        assert (await client.get("/nothing/here")).status_code == 404


# -------------------------------------------------------- role capabilities use ONE resolver

@pytest.mark.asyncio
async def test_declared_role_capability_is_refused_without_a_matching_identity(shell_app):
    app, registrar = shell_app
    permissions.declare_capability("probe.read", ("DCS Admin",))
    registrar.register_pages("probe", simple_router("/probe/read"),
                             capabilities={"/probe/read": "probe.read"})
    async with asgi_client(app) as client:
 # no auth backend installed yet: the default resolver grants nothing
        assert (await client.get("/probe/read")).status_code == 403
        stub_identity(["DCS"])
        assert (await client.get("/probe/read")).status_code == 403
        stub_identity(["GameMaster", "DCS Admin"])
        assert (await client.get("/probe/read")).status_code == 200


@pytest.mark.asyncio
async def test_a_failing_role_resolver_denies_instead_of_raising(shell_app):
    app, registrar = shell_app
    permissions.declare_capability("probe.read", ("Admin",))
    registrar.register_pages("probe", simple_router("/probe/read"),
                             capabilities={"/probe/read": "probe.read"})

    def broken(request):
        raise RuntimeError("identity backend is down")

    permissions.set_role_resolver(broken)
    async with asgi_client(app) as client:
        assert (await client.get("/probe/read")).status_code == 403


# ------------------------------------------------------------------- assets are gated too

@pytest.mark.asyncio
async def test_assets_do_not_bypass_the_gate(shell_app):
    """The shell serves its own assets (declared public) — and an asset path nobody declared is
    refused. This is exactly where a ``StaticFiles`` mount leaks."""
    app, registrar = shell_app
    async with asgi_client(app) as client:
        response = await client.get("/static/shell.css")
        assert response.status_code == 200
        assert ".shell-bar" in response.text

    directory = tempfile.mkdtemp()
    Path(directory, "x.txt").write_text("x", encoding="utf-8")
    app.frontend("/undeclared-assets", directory=directory)
    async with asgi_client(app) as client:
        assert (await client.get("/undeclared-assets/x.txt")).status_code == 403


@pytest.mark.asyncio
async def test_a_staticfiles_mount_bypasses_the_gate_the_shell_must_not_use_one(shell_app):
    """The documented bypass, kept as evidence for why the shell uses ``app.frontend``: a mount is
    invisible to the dependency chain. Asserted so the reason survives a future refactor."""
    app, _registrar = shell_app
    directory = tempfile.mkdtemp()
    Path(directory, "m.txt").write_text("m", encoding="utf-8")
    app.mount("/mnt", StaticFiles(directory=directory), name="mnt")
    async with asgi_client(app) as client:
        assert (await client.get("/mnt/m.txt")).status_code == 200, \
            "a Mount is not a gated surface - which is why the shell owns assets via frontend()"


# ------------------------------------------------------- the legacy surface (self-guarded)

@pytest.mark.asyncio
async def test_a_self_guarded_route_is_allowed_and_reported_as_undeclared(shell_app):
    """`plugins/restapi` (and the WebService's own debug endpoints) install their routes with
    their own auth/local-network dependency and must keep answering unchanged. Such a route is
    allowed by the gate, logged, and listed by the audit helper — so the exemption is a visible
    set instead of an assumption."""
    app, registrar = shell_app

    def local_only(request: Request):
        return True

    router = APIRouter()

    @router.get("/legacy/one")
    async def legacy_one():
        return {"ok": True}

    app.include_router(router, dependencies=[Depends(local_only)])
    async with asgi_client(app) as client:
        assert (await client.get("/legacy/one")).status_code == 200

    assert "/legacy/one" in permissions.undeclared_routes(app, registrar)


@pytest.mark.asyncio
async def test_a_bare_route_outside_the_shell_is_still_refused(shell_app):
    """The exemption is only for routes that guard themselves: a route added straight to the app
    with nothing but the gate is refused, whoever added it."""
    app, _registrar = shell_app
    app.add_api_route("/bare", lambda: {"ok": True}, methods=["GET"])
    async with asgi_client(app) as client:
        assert (await client.get("/bare")).status_code == 403


def test_undeclared_routes_audit_is_empty_for_the_shell_itself(shell_app):
    """The shell's own surface declares everything it serves: no legacy hole of its own."""
    app, registrar = shell_app
    assert permissions.undeclared_routes(app, registrar) == []
